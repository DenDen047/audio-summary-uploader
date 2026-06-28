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
from notebooklm.exceptions import NetworkError as NotebookLMNetworkError

from automator.citation import (
    EmailCitation,
    format_source_line,
    is_spark_share_url,
    parse_email_metadata,
    sanitize_public_text,
)
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

# Spark メール等、OGP が無く title=URL になりがちなソース用の仮タイトル
_SPARK_TITLE_PLACEHOLDER = "メール要約（タイトル取得中）"
# メール系ソースの出典抽出に使う chat 質問（受信者情報は出させない）
_EMAIL_META_QUESTION = (
    "以下のソースはメール（ニュースレター）です。"
    "メールの『件名・送信者の表示名・送信元ドメイン・送信日(YYYY-MM-DD)』だけを"
    "JSONで返してください。キー: title, sender, domain, date。分からない項目はnull。"
    "受信者(宛先)の名前やアドレスは絶対に含めないでください。JSONのみ出力。"
)


def _is_notebooklm_auth_error(exc: Exception) -> bool:
    """NotebookLM の認証エラーかどうかを判定する."""
    if isinstance(exc, NotebookLMAuthError):
        return True
    msg = str(exc).lower()
    return sum(1 for kw in _AUTH_ERROR_KEYWORDS if kw in msg) >= 2


async def _cleanup_notebook(
    backend: NotebookLMBackend, notebook_id: str
) -> None:
    """部分的失敗時に作成済みノートブックを best-effort で削除する.

    削除自体が失敗しても呼び出し元の元エラーを優先したいので、例外は WARN ログのみ。
    NotebookLM のアカウント上限消費を防ぐためのリーク対策。
    """
    try:
        await backend.delete_notebook(notebook_id)
        logger.info("Cleaned up orphaned notebook: {}", notebook_id)
    except Exception as exc:
        logger.warning(
            "Failed to cleanup orphaned notebook {}: {}", notebook_id, exc
        )


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
    citation: EmailCitation | None = None,
) -> str:
    """YouTube 説明文を生成する（個人情報はサニタイズ）.

    メール系ソース（Spark 共有等）は生 URL を公開面に出さない。出典が取れていれば
    「出典: 送信元 - 日付」を、取れていなければ汎用ラベルを表示する。
    """
    if citation is not None:
        source_block = format_source_line(citation)
    elif is_spark_share_url(metadata.url):
        source_block = "📰 ソース: メールニュースレター"
    else:
        lines = [f"📄 元記事: {metadata.url}"]
        if metadata.site_name:
            lines.append(f"📰 ソース: {metadata.site_name}")
        source_block = "\n".join(lines)

    description = f"""NotebookLM の Audio Overview で自動生成された音声要約です。

{source_block}

🔧 生成条件
  音声の長さ: {audio_length}
  プロンプト: {prompt_preset_name}

---
この動画は audio-summary-uploader で自動生成されました。""".strip()
    # 最後の砦: メールアドレス・Spark 共有 URL を除去する
    return sanitize_public_text(description)


def _sanitize_youtube_title(title: str) -> str:
    """YouTube API が拒否する文字を置換する."""
    replacements = {
        "<": "＜",
        ">": "＞",
    }
    for old, new in replacements.items():
        title = title.replace(old, new)
    return title


def _build_title(metadata: PageMetadata, settings: Settings) -> str:
    """YouTube タイトルを生成する."""
    prefix = settings.youtube.title_prefix
    max_len = settings.youtube.title_max_length
    title = sanitize_public_text(_sanitize_youtube_title(metadata.title))
    if len(title) > max_len:
        title = title[: max_len - 1] + "…"
    return f"{prefix} {title}"


def _apply_spark_safety(metadata: PageMetadata) -> None:
    """Spark 共有ソースの生 URL がタイトル・サムネに漏れないようにする（in-place）.

    Spark の共有ページは OGP を持たず title=URL になりがち。実際の件名は collect
    フェーズで NotebookLM chat から取得するため、それまでは仮タイトルにしておく。
    """
    if is_spark_share_url(metadata.url):
        metadata.title = _SPARK_TITLE_PLACEHOLDER
        metadata.site_name = metadata.site_name or "メールニュースレター"
        metadata.og_image_url = None


async def _extract_email_citation(
    backend: NotebookLMBackend, notebook_id: str, url: str
) -> EmailCitation | None:
    """メール系ソースから出典(件名/送信元/日付)を chat で抽出する."""
    try:
        answer = await backend.ask(notebook_id, _EMAIL_META_QUESTION)
    except Exception as exc:
        logger.warning("出典抽出に失敗 ({}): {}", url, exc)
        return None
    citation = parse_email_metadata(answer)
    if citation is None:
        logger.warning("出典の解析に失敗 ({})", url)
    return citation


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


def _upsert_job_state(
    state_path: Path,
    url: str,
    audio_length: str,
    prompt: str,
    updates: dict[str, Any],
) -> None:
    """state.json を読み直してジョブを find-or-create し、更新して保存する.

    submit フェーズ用。メモリ上の古い state 全体を書き戻すと、並行する
    Web 操作 (delete / clear / retry / add) を巻き戻してしまうため、
    必ずディスク上の最新 state に対して更新する。処理中のジョブが
    並行操作で削除されていた場合は再作成する (オーファン化を防ぐ)。
    """
    state = _load_state(state_path)
    job = _find_or_create_job(state, url, audio_length, prompt)
    job.update(updates)
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
            # 再投入時の audio_length / prompt 指定変更を反映する
            job["audio_length"] = audio_length
            job["prompt"] = prompt
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
        _apply_spark_safety(metadata)

    if dry_run:
        # state.json には書き込まない。generating を書くと以後の本実行が
        # スキップされ、collect が notebook_id なしのジョブで失敗するため。
        logger.info("[DRY RUN] Would submit: {!r}", metadata.title)
        return ProcessResult(
            url=url,
            title=metadata.title,
            status="generating (dry-run)",
            phase="submit",
        )

    # 旧ノートブックが残っていれば掃除 (force 再実行・リトライ経路でのリーク防止)
    state = _load_state(state_path)
    existing = next((j for j in state["jobs"] if j["url"] == url), None)
    if existing and existing.get("notebook_id"):
        await _cleanup_notebook(backend, existing["notebook_id"])

    # ノートブック作成 → ID を即座に永続化 (クラッシュ時のオーファン追跡用)。
    # 旧 task_id を残すと collect の submit 中断検出ガードをすり抜けるためクリアする。
    notebook_id = await backend.create_notebook(f"Summary: {metadata.title}")
    _upsert_job_state(state_path, url, audio_length, prompt_preset_name, {
        "notebook_id": notebook_id,
        "task_id": None,
        "status": "generating",
    })

    try:
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
    except Exception:
        # create_notebook は通ったが後段が失敗 → 作成済みノートブックを掃除
        await _cleanup_notebook(backend, notebook_id)
        raise

    # state 更新
    _upsert_job_state(state_path, url, audio_length, prompt_preset_name, {
        "status": "generating",
        "task_id": task_id,
        "metadata": _metadata_to_dict(metadata),
        "submitted_at": _now_iso(),
        "error": None,
    })

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
        to_submit.append(entry)

    if not to_submit:
        logger.info("No new URLs to submit")
        return []

    async def _safe_submit(entry: UrlEntry) -> ProcessResult:
        try:
            return await _submit_single(
                entry, settings, backend, state_path, dry_run
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
            # state にエラーを記録。notebook_id は破棄しない:
            # 掃除に失敗したノートブックが残っていても、リトライ時の
            # _submit_single 冒頭で best-effort 削除されるため。
            audio_length = entry.audio_length or settings.notebooklm.audio_length
            prompt_preset_name = entry.prompt or "default"
            _upsert_job_state(
                state_path, entry.url, audio_length, prompt_preset_name, {
                    "status": "failed",
                    "error": error_msg,
                    "submitted_at": _now_iso(),
                },
            )
            return ProcessResult(
                url=entry.url,
                status="failed",
                error=error_msg,
                phase="submit",
            )

    results = await asyncio.gather(*[_safe_submit(e) for e in to_submit])
    return list(results)


# --- Phase 2: collect ---


async def _fail_collect_job(
    backend: NotebookLMBackend,
    state_path: Path,
    url: str,
    title: str | None,
    notebook_id: str | None,
    error_msg: str,
) -> ProcessResult:
    """collect 失敗を確定させる: ノートブック掃除 + failed 遷移."""
    if notebook_id:
        await _cleanup_notebook(backend, notebook_id)
    _update_job_state(state_path, url, {
        "status": "failed",
        "error": error_msg,
        "notebook_id": None,
    })
    return ProcessResult(
        url=url,
        title=title,
        status="failed",
        error=error_msg,
        phase="collect",
    )


async def _collect_single(
    job: dict,
    settings: Settings,
    backend: NotebookLMBackend,
    tmp_dir: Path,
    poll: bool,
    state_path: Path,
) -> ProcessResult:
    """1つのジョブに対して collect 処理を実行する."""
    url = job["url"]
    slug = job["slug"]
    notebook_id = job["notebook_id"]
    task_id = job["task_id"]
    title = job["metadata"]["title"] if job.get("metadata") else None

    logger.info("Collecting: {} (slug={})", url, slug)

    # submit が中断されたジョブは回収できない → 明示的に failed にする
    if not notebook_id or not task_id:
        return await _fail_collect_job(
            backend, state_path, url, title, notebook_id,
            "submit が完了していないため音声を回収できません。リトライしてください。",
        )

    # ステータスチェック
    gen_status = await backend.check_audio_status(notebook_id, task_id)

    if gen_status.status.upper() == "FAILED":
        # 生成失敗が確定 → ノートブックは再利用できない。
        # NOT_FOUND は作成直後の一時的な lag の可能性があるため (notebooklm-py の
        # is_not_found docstring 参照) 単発では terminal とみなさず、
        # wait_for_audio 側の連続判定 (5回連続 + 10秒) に委ねる。
        return await _fail_collect_job(
            backend, state_path, url, title, notebook_id,
            f"Audio generation failed: {gen_status.status}",
        )

    if gen_status.status.upper() != "COMPLETED":
        if not poll:
            logger.info("Audio still generating for {}, skipping (use --poll)", url)
            return ProcessResult(
                url=url,
                title=title,
                status="generating",
                phase="collect",
            )

        logger.info("Audio still generating, polling until completion...")
        try:
            gen_status = await backend.wait_for_audio(notebook_id, task_id)
        except (TimeoutError, NotebookLMNetworkError) as exc:
            # タイムアウトや一時的なネットワーク断は terminal ではない:
            # 生成は継続中の可能性があるため generating のまま残し、
            # 次回の collect で再試行できるようにする (ノートブックも残す)
            logger.warning(
                "Audio polling interrupted for {} (notebook={}): {};"
                " keeping status=generating for next collect",
                url, notebook_id, exc,
            )
            return ProcessResult(
                url=url,
                title=title,
                status="generating",
                phase="collect",
            )
        if gen_status.status.upper() != "COMPLETED":
            # 音声生成が terminal FAILED で確定 → ノートブックは再利用できない
            return await _fail_collect_job(
                backend, state_path, url, title, notebook_id,
                f"Audio generation failed: {gen_status.status}",
            )

    # 音声ダウンロード (以降の例外時の掃除と failed 遷移は _safe_collect が担う)
    audio_path = await backend.download_audio(
        notebook_id, output_path=tmp_dir / "audio" / f"{slug}.mp3"
    )

    # メタデータ復元
    metadata = _dict_to_metadata(url, job["metadata"])

    # メール系ソース: ノートブック削除前に chat で出典(件名/送信元/日付)を抽出し、
    # 件名をタイトルに反映する（生 URL を公開面に出さないため）。
    citation_dict = job.get("citation")
    if is_spark_share_url(url):
        citation = await _extract_email_citation(backend, notebook_id, url)
        if citation is not None:
            if citation.title:
                metadata.title = citation.title
            citation_dict = {
                "sender": citation.sender,
                "date": citation.date,
                "domain": citation.domain,
            }

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
        "notebook_id": None,
        "metadata": _metadata_to_dict(metadata),
        "citation": citation_dict,
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
                job, settings, backend, tmp_dir, poll, state_path
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
            return await _fail_collect_job(
                backend,
                state_path,
                job["url"],
                job["metadata"]["title"] if job.get("metadata") else None,
                job.get("notebook_id"),
                error_msg,
            )

    results = await asyncio.gather(*[_safe_collect(j) for j in generating_jobs])
    return list(results)


# --- Phase 3: upload ---


async def upload_videos(
    settings: Settings,
    allow_interactive_auth: bool = True,
) -> list[ProcessResult]:
    """Phase 3: video_ready のジョブを YouTube にアップロードする.

    allow_interactive_auth=False (Web サーバー等) では、対話 OAuth フローを
    開始せずに認証失敗を failed として記録する。同期的な認証処理で
    イベントループをブロックしないよう to_thread でラップする。
    """
    state_path = Path(settings.general.state_file)
    state = _load_state(state_path)

    ready_jobs = [
        job for job in state.get("jobs", []) if job.get("status") == "video_ready"
    ]

    if not ready_jobs:
        logger.info("No video_ready jobs to upload")
        return []

    # YouTube 認証（1回）
    try:
        creds = await asyncio.to_thread(
            authenticate,
            client_secret_path=Path(settings.credentials.youtube_client_secret),
            token_path=Path(settings.credentials.youtube_token),
            allow_interactive=allow_interactive_auth,
        )
    except Exception as exc:
        if allow_interactive_auth:
            # CLI コンテキストでは Fail Fast でそのままクラッシュさせる
            raise
        # Web コンテキストではジョブを failed にして UI にエラーを可視化する。
        # 動画ファイルは video_path に残るため、リトライでアップロードのみ再試行できる。
        logger.error("YouTube authentication failed: {}", exc)
        results = []
        for job in ready_jobs:
            _update_job_state(state_path, job["url"], {
                "status": "failed",
                "error": str(exc),
            })
            results.append(
                ProcessResult(
                    url=job["url"],
                    title=job["metadata"]["title"] if job.get("metadata") else None,
                    status="failed",
                    error=str(exc),
                    phase="upload",
                )
            )
        return results

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

            citation_data = job.get("citation")
            citation = EmailCitation(**citation_data) if citation_data else None
            description = _build_description(
                metadata, audio_length, prompt_preset_name, citation=citation
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
        _apply_spark_safety(metadata)

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
    allow_interactive_auth: bool = True,
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
    upload_results = await upload_videos(
        settings, allow_interactive_auth=allow_interactive_auth
    )
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
