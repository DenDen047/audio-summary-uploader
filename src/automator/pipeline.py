"""パイプライン全体のオーケストレーション（3フェーズ: submit / collect / upload）."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from google.oauth2.credentials import Credentials

from notebooklm.exceptions import AuthError as NotebookLMAuthError

from automator.config import Settings
from automator.metadata import PageMetadata, fetch_metadata, metadata_for_local_file
from automator.notebooklm import NotebookLMBackend
from automator.notebooklm_py_backend import NotebookLMPyBackend
from automator.report import ProcessResult
from automator.thumbnail import generate_thumbnail
from automator.url_parser import UrlEntry, is_local_path
from automator.video import convert_to_video
from automator.youtube import YouTubeUploadParams, authenticate, upload_video

_NOTEBOOKLM_AUTH_ERROR_MSG = (
    "NotebookLM の認証が期限切れです。"
    "ターミナルで 'uv run notebooklm login' を実行して再認証してください。"
    "再認証後、Web UI からリトライできます。"
)

_AUTH_ERROR_KEYWORDS = ("authentication", "expired", "re-authenticate", "login")


def _is_notebooklm_auth_error(exc: Exception) -> bool:
    """NotebookLM の認証エラーかどうかを判定する."""
    if isinstance(exc, NotebookLMAuthError):
        return True
    msg = str(exc).lower()
    return sum(1 for kw in _AUTH_ERROR_KEYWORDS if kw in msg) >= 2


def _make_slug(url: str) -> str:
    """URL から一意な slug を生成する (SHA-256 先頭 12 文字)."""
    return hashlib.sha256(url.encode()).hexdigest()[:12]


def _resolve_prompt_preset(preset_name: str | None, settings: Settings) -> str:
    """プロンプトプリセット名を実際のテキストに解決する."""
    name = preset_name or "default"
    presets = settings.notebooklm.prompt_presets
    if name not in presets:
        msg = f"Unknown prompt preset: {name!r}"
        raise ValueError(msg)
    return presets[name]


def _build_description(
    metadata: PageMetadata,
    audio_length: str,
    prompt_preset_name: str,
) -> str:
    """YouTube 説明文を生成する."""
    source_line = f"📰 ソース: {metadata.site_name}" if metadata.site_name else ""
    return f"""NotebookLM の Audio Overview で自動生成された音声要約です。

📄 元記事: {metadata.url}
{source_line}

🔧 生成条件
  音声の長さ: {audio_length}
  プロンプト: {prompt_preset_name}

---
この動画は audio-summary-uploader で自動生成されました。""".strip()


def _build_title(metadata: PageMetadata, settings: Settings) -> str:
    """YouTube タイトルを生成する."""
    prefix = settings.youtube.title_prefix
    max_len = settings.youtube.title_max_length
    title = metadata.title
    if len(title) > max_len:
        title = title[: max_len - 1] + "…"
    return f"{prefix} {title}"


# --- 状態管理 ---


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _migrate_state(state: dict) -> dict:
    """旧 state.json (processed キー) を新 jobs スキーマにマイグレーションする."""
    if "jobs" in state:
        return state
    old_processed = state.get("processed", [])
    jobs: list[dict[str, Any]] = []
    for entry in old_processed:
        status = entry.get("status", "failed")
        if status == "success":
            status = "uploaded"
        job: dict[str, Any] = {
            "url": entry["url"],
            "slug": _make_slug(entry["url"]),
            "audio_length": entry.get("audio_length", "default"),
            "prompt": entry.get("prompt", "default"),
            "status": status,
            "notebook_id": entry.get("notebook_id"),
            "task_id": None,
            "metadata": None,
            "audio_path": None,
            "thumbnail_path": None,
            "video_path": None,
            "youtube_url": entry.get("youtube_url"),
            "error": entry.get("error"),
            "submitted_at": entry.get("processed_at"),
            "collected_at": entry.get("processed_at") if status == "uploaded" else None,
            "uploaded_at": entry.get("processed_at") if status == "uploaded" else None,
        }
        jobs.append(job)
    logger.info("Migrated {} old entries to new jobs schema", len(jobs))
    return {"last_run": state.get("last_run"), "jobs": jobs}


def _load_state(state_path: Path) -> dict:
    """状態ファイルを読み込む（必要に応じてマイグレーション）."""
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        return _migrate_state(state)
    return {"last_run": None, "jobs": []}


def _save_state(state_path: Path, state: dict) -> None:
    """状態ファイルをアトミックに保存する."""
    content = json.dumps(state, ensure_ascii=False, indent=2)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=state_path.parent, suffix=".tmp", prefix=".state_"
    )
    try:
        with open(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
        Path(tmp_path).replace(state_path)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def _update_job_state(
    state_path: Path, url: str, updates: dict[str, Any]
) -> None:
    """state.json からジョブを検索し、指定フィールドのみ更新して保存する.

    ディスク上の最新 state を読み直すことで、他の操作 (clear, delete 等) との
    競合によるデータ復活を防ぐ。ジョブが既に削除されていた場合は何もしない。
    """
    state = _load_state(state_path)
    for job in state["jobs"]:
        if job["url"] == url:
            job.update(updates)
            break
    state["last_run"] = _now_iso()
    _save_state(state_path, state)


def _get_active_urls(state: dict) -> set[str]:
    """生成中・video_ready・uploaded の URL セットを返す."""
    return {
        job["url"]
        for job in state.get("jobs", [])
        if job.get("status") in ("generating", "video_ready", "uploaded")
    }


def _find_or_create_job(
    state: dict, url: str, audio_length: str, prompt: str
) -> dict:
    """既存ジョブを探すか新規作成する."""
    for job in state["jobs"]:
        if job["url"] == url:
            return job
    job: dict[str, Any] = {
        "url": url,
        "slug": _make_slug(url),
        "audio_length": audio_length,
        "prompt": prompt,
        "status": "generating",
        "notebook_id": None,
        "task_id": None,
        "metadata": None,
        "audio_path": None,
        "thumbnail_path": None,
        "video_path": None,
        "youtube_url": None,
        "error": None,
        "submitted_at": None,
        "collected_at": None,
        "uploaded_at": None,
    }
    state["jobs"].append(job)
    return job


def _metadata_to_dict(metadata: PageMetadata) -> dict:
    """PageMetadata を dict に変換する."""
    return {
        "title": metadata.title,
        "description": metadata.description,
        "og_image_url": metadata.og_image_url,
        "site_name": metadata.site_name,
        "language": metadata.language,
        "favicon_url": metadata.favicon_url,
    }


def _dict_to_metadata(url: str, d: dict) -> PageMetadata:
    """dict から PageMetadata を復元する."""
    return PageMetadata(
        url=url,
        title=d["title"],
        description=d.get("description", ""),
        og_image_url=d.get("og_image_url"),
        site_name=d.get("site_name"),
        language=d.get("language"),
        favicon_url=d.get("favicon_url"),
    )


def _create_backend(settings: Settings) -> NotebookLMBackend:
    """設定に応じた NotebookLM バックエンドを生成する."""
    if settings.notebooklm.backend == "notebooklm-py":
        return NotebookLMPyBackend(
            poll_interval=settings.notebooklm.generation_poll_interval_seconds,
            timeout=settings.notebooklm.generation_timeout_seconds,
        )
    msg = f"Backend {settings.notebooklm.backend!r} is not yet implemented"
    raise NotImplementedError(msg)


# --- Phase 1: submit ---


async def _submit_single(
    entry: UrlEntry,
    settings: Settings,
    backend: NotebookLMBackend,
    state: dict,
    state_path: Path,
    dry_run: bool,
) -> ProcessResult:
    """1つの URL に対して submit 処理を実行する."""
    url = entry.url
    slug = _make_slug(url)
    audio_length = entry.audio_length or settings.notebooklm.audio_length
    prompt_preset_name = entry.prompt or "default"

    logger.info("Submitting: {} (slug={})", url, slug)

    # メタデータ取得
    is_local = is_local_path(url)
    if is_local:
        tmp_dir = Path(settings.general.tmp_dir)
        metadata = metadata_for_local_file(Path(url), tmp_dir=tmp_dir)
    else:
        metadata = await fetch_metadata(url)

    if dry_run:
        logger.info("[DRY RUN] Would submit: {!r}", metadata.title)
        job = _find_or_create_job(state, url, audio_length, prompt_preset_name)
        job["status"] = "generating"
        job["metadata"] = _metadata_to_dict(metadata)
        job["submitted_at"] = _now_iso()
        state["last_run"] = _now_iso()
        _save_state(state_path, state)
        return ProcessResult(
            url=url,
            title=metadata.title,
            status="generating (dry-run)",
            phase="submit",
        )

    # ノートブック作成
    notebook_id = await backend.create_notebook(f"Summary: {metadata.title}")

    job = _find_or_create_job(state, url, audio_length, prompt_preset_name)
    job["notebook_id"] = notebook_id

    # ソース追加
    if is_local:
        await backend.add_file_source(notebook_id, Path(url))
    else:
        await backend.add_source(notebook_id, url)

    # プロンプト解決
    prompt_text = _resolve_prompt_preset(entry.prompt, settings)

    # 音声生成開始（完了を待たない）
    task_id = await backend.start_audio_generation(
        notebook_id,
        language=settings.notebooklm.audio_language,
        instructions=prompt_text,
        audio_length=audio_length,
    )

    # state 更新
    job["status"] = "generating"
    job["task_id"] = task_id
    job["metadata"] = _metadata_to_dict(metadata)
    job["submitted_at"] = _now_iso()
    job["error"] = None
    state["last_run"] = _now_iso()
    _save_state(state_path, state)

    return ProcessResult(
        url=url,
        title=metadata.title,
        status="generating",
        phase="submit",
    )


async def submit_urls(
    entries: list[UrlEntry],
    settings: Settings,
    force: bool = False,
    dry_run: bool = False,
) -> list[ProcessResult]:
    """Phase 1: 各URLに対してノートブック作成＋音声生成開始を並列実行する."""
    state_path = Path(settings.general.state_file)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = _load_state(state_path)
    active_urls = _get_active_urls(state)

    backend = _create_backend(settings)

    to_submit: list[UrlEntry] = []
    for entry in entries:
        if not force and entry.url in active_urls:
            logger.info("Skipping already active: {}", entry.url)
            continue
        if force:
            # force の場合は既存ジョブをリセット
            for job in state["jobs"]:
                if job["url"] == entry.url:
                    job["status"] = "failed"
                    break
        to_submit.append(entry)

    if not to_submit:
        logger.info("No new URLs to submit")
        return []

    async def _safe_submit(entry: UrlEntry) -> ProcessResult:
        try:
            return await _submit_single(
                entry, settings, backend, state, state_path, dry_run
            )
        except Exception as exc:
            if _is_notebooklm_auth_error(exc):
                logger.error(
                    "NotebookLM 認証エラー (url={}): {}",
                    entry.url,
                    _NOTEBOOKLM_AUTH_ERROR_MSG,
                )
                error_msg = _NOTEBOOKLM_AUTH_ERROR_MSG
            else:
                logger.error("Failed to submit {}: {}", entry.url, exc)
                error_msg = str(exc)
            # state にエラーを記録
            audio_length = entry.audio_length or settings.notebooklm.audio_length
            prompt_preset_name = entry.prompt or "default"
            job = _find_or_create_job(
                state, entry.url, audio_length, prompt_preset_name
            )
            job["status"] = "failed"
            job["error"] = error_msg
            job["submitted_at"] = _now_iso()
            state["last_run"] = _now_iso()
            _save_state(state_path, state)
            return ProcessResult(
                url=entry.url,
                status="failed",
                error=error_msg,
                phase="submit",
            )

    results = await asyncio.gather(*[_safe_submit(e) for e in to_submit])
    return list(results)


# --- Phase 2: collect ---


async def _collect_single(
    job: dict,
    settings: Settings,
    backend: NotebookLMBackend,
    tmp_dir: Path,
    poll: bool,
    state: dict,
    state_path: Path,
) -> ProcessResult:
    """1つのジョブに対して collect 処理を実行する."""
    url = job["url"]
    slug = job["slug"]
    notebook_id = job["notebook_id"]
    task_id = job["task_id"]

    logger.info("Collecting: {} (slug={})", url, slug)

    # ステータスチェック
    gen_status = await backend.check_audio_status(notebook_id, task_id)

    if gen_status.status.upper() != "COMPLETED":
        if poll:
            logger.info("Audio still generating, polling until completion...")
            gen_status = await backend.wait_for_audio(notebook_id, task_id)
            if gen_status.status.upper() != "COMPLETED":
                error_msg = f"Audio generation failed: {gen_status.status}"
                _update_job_state(state_path, url, {
                    "status": "failed",
                    "error": error_msg,
                })
                return ProcessResult(
                    url=url,
                    title=job["metadata"]["title"] if job["metadata"] else None,
                    status="failed",
                    error=error_msg,
                    phase="collect",
                )
        else:
            logger.info("Audio still generating for {}, skipping (use --poll)", url)
            return ProcessResult(
                url=url,
                title=job["metadata"]["title"] if job["metadata"] else None,
                status="generating",
                phase="collect",
            )

    # 音声ダウンロード
    audio_path = await backend.download_audio(
        notebook_id, output_path=tmp_dir / "audio" / f"{slug}.mp3"
    )

    # メタデータ復元
    metadata = _dict_to_metadata(url, job["metadata"])

    # サムネイル生成
    thumbnail_path = await generate_thumbnail(
        title=metadata.title,
        site_name=metadata.site_name,
        og_image_url=metadata.og_image_url,
        output_path=tmp_dir / "thumbnails" / f"{slug}_thumb.png",
        config=settings.thumbnail,
        favicon_url=metadata.favicon_url,
    )

    # 動画変換
    video_path = await convert_to_video(
        audio_path=audio_path,
        thumbnail_path=thumbnail_path,
        output_path=tmp_dir / "videos" / f"{slug}.mp4",
    )

    # ノートブック削除
    await backend.delete_notebook(notebook_id)

    # state 更新 (ディスクから再読込して競合を防ぐ)
    _update_job_state(state_path, url, {
        "status": "video_ready",
        "audio_path": str(audio_path),
        "thumbnail_path": str(thumbnail_path),
        "video_path": str(video_path),
        "collected_at": _now_iso(),
    })

    return ProcessResult(
        url=url,
        title=metadata.title,
        status="video_ready",
        phase="collect",
    )


async def collect_audio(
    settings: Settings,
    poll: bool = False,
    timeout: int | None = None,
) -> list[ProcessResult]:
    """Phase 2: generating のジョブから音声をDL→サムネイル→動画変換する."""
    tmp_dir = Path(settings.general.tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    state_path = Path(settings.general.state_file)
    state = _load_state(state_path)

    generating_jobs = [
        job for job in state.get("jobs", []) if job.get("status") == "generating"
    ]

    if not generating_jobs:
        logger.info("No generating jobs to collect")
        return []

    if timeout is not None:
        settings.notebooklm.generation_timeout_seconds = timeout

    backend = _create_backend(settings)

    async def _safe_collect(job: dict) -> ProcessResult:
        try:
            return await _collect_single(
                job, settings, backend, tmp_dir, poll, state, state_path
            )
        except Exception as exc:
            if _is_notebooklm_auth_error(exc):
                logger.error(
                    "NotebookLM 認証エラー (url={}): {}",
                    job["url"],
                    _NOTEBOOKLM_AUTH_ERROR_MSG,
                )
                error_msg = _NOTEBOOKLM_AUTH_ERROR_MSG
            else:
                logger.error("Failed to collect {}: {}", job["url"], exc)
                error_msg = str(exc)
            _update_job_state(state_path, job["url"], {
                "status": "failed",
                "error": error_msg,
            })
            return ProcessResult(
                url=job["url"],
                title=job["metadata"]["title"] if job.get("metadata") else None,
                status="failed",
                error=error_msg,
                phase="collect",
            )

    results = await asyncio.gather(*[_safe_collect(j) for j in generating_jobs])
    return list(results)


# --- Phase 3: upload ---


async def upload_videos(settings: Settings) -> list[ProcessResult]:
    """Phase 3: video_ready のジョブを YouTube にアップロードする."""
    state_path = Path(settings.general.state_file)
    state = _load_state(state_path)

    ready_jobs = [
        job for job in state.get("jobs", []) if job.get("status") == "video_ready"
    ]

    if not ready_jobs:
        logger.info("No video_ready jobs to upload")
        return []

    # YouTube 認証（1回）
    creds = authenticate(
        client_secret_path=Path(settings.credentials.youtube_client_secret),
        token_path=Path(settings.credentials.youtube_token),
    )

    results: list[ProcessResult] = []
    daily_limit = settings.youtube.daily_upload_limit

    for i, job in enumerate(ready_jobs):
        if i >= daily_limit:
            logger.warning(
                "Daily upload limit ({}) reached, stopping", daily_limit
            )
            break

        url = job["url"]
        try:
            metadata = _dict_to_metadata(url, job["metadata"])
            audio_length = job.get("audio_length", "default")
            prompt_preset_name = job.get("prompt", "default")

            description = _build_description(
                metadata, audio_length, prompt_preset_name
            )
            title = _build_title(metadata, settings)

            params = YouTubeUploadParams(
                file_path=Path(job["video_path"]),
                title=title,
                description=description,
                tags=settings.youtube.default_tags,
                category_id=settings.youtube.category_id,
                privacy_status=settings.youtube.privacy_status,
                thumbnail_path=Path(job["thumbnail_path"]),
                playlist_id=settings.youtube.playlist_id,
            )

            youtube_url = await upload_video(creds, params)

            _update_job_state(state_path, url, {
                "status": "uploaded",
                "youtube_url": youtube_url,
                "uploaded_at": _now_iso(),
            })

            results.append(
                ProcessResult(
                    url=url,
                    title=metadata.title,
                    youtube_url=youtube_url,
                    status="uploaded",
                    phase="upload",
                )
            )
        except Exception as exc:
            logger.error("Failed to upload {}: {}", url, exc)
            _update_job_state(state_path, url, {
                "status": "failed",
                "error": str(exc),
            })
            results.append(
                ProcessResult(
                    url=url,
                    title=job["metadata"]["title"] if job.get("metadata") else None,
                    status="failed",
                    error=str(exc),
                    phase="upload",
                )
            )

    return results


# --- 既存互換: process_single_url + run_pipeline ---


async def process_single_url(
    entry: UrlEntry,
    settings: Settings,
    backend: NotebookLMBackend,
    tmp_dir: Path,
    creds: Credentials | None,
    dry_run: bool = False,
) -> ProcessResult:
    """1 URL の処理パイプラインを実行する（後方互換: run-single 用）."""
    slug = _make_slug(entry.url)
    logger.info("Processing: {} (slug={})", entry.url, slug)

    # 1. メタデータ取得
    is_local = is_local_path(entry.url)
    if is_local:
        metadata = metadata_for_local_file(Path(entry.url), tmp_dir=tmp_dir)
    else:
        metadata = await fetch_metadata(entry.url)

    if dry_run:
        logger.info("[DRY RUN] Would process: {!r}", metadata.title)
        return ProcessResult(
            url=entry.url, title=metadata.title, status="success (dry-run)"
        )

    # 2. NotebookLM でノートブック作成
    notebook_id = await backend.create_notebook(f"Summary: {metadata.title}")

    # 3. ソース追加
    if is_local:
        await backend.add_file_source(notebook_id, Path(entry.url))
    else:
        await backend.add_source(notebook_id, entry.url)

    # 4. プロンプト解決
    prompt_text = _resolve_prompt_preset(entry.prompt, settings)
    prompt_preset_name = entry.prompt or "default"

    # 5. audio_length 解決
    audio_length = entry.audio_length or settings.notebooklm.audio_length

    # 6. Audio Overview 生成
    await backend.generate_audio(
        notebook_id,
        language=settings.notebooklm.audio_language,
        instructions=prompt_text,
        audio_length=audio_length,
    )

    # 7. 音声ダウンロード
    audio_path = await backend.download_audio(
        notebook_id, output_path=tmp_dir / "audio" / f"{slug}.mp3"
    )

    # 8. サムネイル生成
    thumbnail_path = await generate_thumbnail(
        title=metadata.title,
        site_name=metadata.site_name,
        og_image_url=metadata.og_image_url,
        output_path=tmp_dir / "thumbnails" / f"{slug}_thumb.png",
        config=settings.thumbnail,
        favicon_url=metadata.favicon_url,
    )

    # 9. 動画変換
    video_path = await convert_to_video(
        audio_path=audio_path,
        thumbnail_path=thumbnail_path,
        output_path=tmp_dir / "videos" / f"{slug}.mp4",
    )

    # 10. YouTube アップロード
    description = _build_description(metadata, audio_length, prompt_preset_name)
    title = _build_title(metadata, settings)

    params = YouTubeUploadParams(
        file_path=video_path,
        title=title,
        description=description,
        tags=settings.youtube.default_tags,
        category_id=settings.youtube.category_id,
        privacy_status=settings.youtube.privacy_status,
        thumbnail_path=thumbnail_path,
        playlist_id=settings.youtube.playlist_id,
    )

    youtube_url = await upload_video(creds, params)

    # 11. NotebookLM ノートブック削除
    await backend.delete_notebook(notebook_id)

    return ProcessResult(
        url=entry.url,
        title=metadata.title,
        youtube_url=youtube_url,
        status="success",
    )


async def run_pipeline(
    entries: list[UrlEntry],
    settings: Settings,
    dry_run: bool = False,
    force: bool = False,
    retry_failed: bool = False,
) -> list[ProcessResult]:
    """パイプライン全体を実行する（3フェーズ統合）."""
    if retry_failed:
        # retry_failed は旧互換: failed のジョブを generating にリセットして再実行
        state_path = Path(settings.general.state_file)
        state = _load_state(state_path)
        failed_urls = {
            job["url"]
            for job in state.get("jobs", [])
            if job.get("status") == "failed"
        }
        entries = [e for e in entries if e.url in failed_urls]
        if not entries:
            logger.info("No failed URLs to retry")
            return []
        force = True

    all_results: list[ProcessResult] = []

    # Phase 1: submit
    submit_results = await submit_urls(
        entries, settings, force=force, dry_run=dry_run
    )
    all_results.extend(submit_results)

    if dry_run:
        return all_results

    # Phase 2: collect (poll=True で完了まで待機)
    collect_results = await collect_audio(settings, poll=True)
    all_results.extend(collect_results)

    # Phase 3: upload
    upload_results = await upload_videos(settings)
    all_results.extend(upload_results)

    return all_results


def get_status_counts(settings: Settings) -> dict[str, int]:
    """各ステータスのジョブ数を返す."""
    state_path = Path(settings.general.state_file)
    state = _load_state(state_path)
    counts: dict[str, int] = {}
    for job in state.get("jobs", []):
        status = job.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts
