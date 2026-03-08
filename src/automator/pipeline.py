"""パイプライン全体のオーケストレーション."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from google.oauth2.credentials import Credentials

from automator.config import Settings
from automator.metadata import PageMetadata, fetch_metadata, metadata_for_local_file
from automator.notebooklm import NotebookLMBackend
from automator.notebooklm_py_backend import NotebookLMPyBackend
from automator.report import ProcessResult
from automator.thumbnail import generate_thumbnail
from automator.url_parser import UrlEntry, is_local_path
from automator.video import convert_to_video
from automator.youtube import YouTubeUploadParams, authenticate, upload_video


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


def _load_state(state_path: Path) -> dict:
    """状態ファイルを読み込む."""
    if state_path.exists():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return {"last_run": None, "processed": []}


def _save_state(state_path: Path, state: dict) -> None:
    """状態ファイルを保存する."""
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _get_processed_urls(state: dict) -> set[str]:
    """処理済み URL のセットを返す."""
    return {
        entry["url"]
        for entry in state.get("processed", [])
        if entry.get("status") == "success"
    }


def _create_backend(settings: Settings) -> NotebookLMBackend:
    """設定に応じた NotebookLM バックエンドを生成する."""
    if settings.notebooklm.backend == "notebooklm-py":
        return NotebookLMPyBackend(
            poll_interval=settings.notebooklm.generation_poll_interval_seconds,
            timeout=settings.notebooklm.generation_timeout_seconds,
        )
    msg = f"Backend {settings.notebooklm.backend!r} is not yet implemented"
    raise NotImplementedError(msg)


async def process_single_url(
    entry: UrlEntry,
    settings: Settings,
    backend: NotebookLMBackend,
    tmp_dir: Path,
    creds: Credentials | None,
    dry_run: bool = False,
) -> ProcessResult:
    """1 URL の処理パイプラインを実行する."""
    slug = _make_slug(entry.url)
    logger.info("Processing: {} (slug={})", entry.url, slug)

    # 1. メタデータ取得
    is_local = is_local_path(entry.url)
    if is_local:
        metadata = metadata_for_local_file(Path(entry.url))
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
    """パイプライン全体を実行する."""
    tmp_dir = Path(settings.general.tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    state_path = Path(settings.general.state_file)
    state = _load_state(state_path)
    processed_urls = _get_processed_urls(state)

    backend = _create_backend(settings)

    # YouTube 認証（1回だけ）
    creds = authenticate(
        client_secret_path=Path(settings.credentials.youtube_client_secret),
        token_path=Path(settings.credentials.youtube_token),
    ) if not dry_run else None

    results: list[ProcessResult] = []

    # クォータ制限チェック
    upload_count = 0
    daily_limit = settings.youtube.daily_upload_limit

    for entry in entries:
        # 処理済みスキップ
        if not force and entry.url in processed_urls:
            logger.info("Skipping already processed: {}", entry.url)
            continue

        # retry_failed: 失敗した URL のみ再処理
        if retry_failed:
            failed_urls = {
                e["url"]
                for e in state.get("processed", [])
                if e.get("status") == "failed"
            }
            if entry.url not in failed_urls:
                continue

        # クォータチェック
        if not dry_run and upload_count >= daily_limit:
            logger.warning(
                "Daily upload limit ({}) reached, stopping", daily_limit
            )
            break

        try:
            result = await process_single_url(
                entry, settings, backend, tmp_dir, creds, dry_run=dry_run
            )
            results.append(result)
            if not dry_run:
                upload_count += 1
        except Exception as exc:
            logger.error("Failed to process {}: {}", entry.url, exc)
            results.append(
                ProcessResult(url=entry.url, status="failed", error=str(exc))
            )

        # 状態保存（各 URL 処理後）
        if not dry_run:
            now = datetime.now(tz=timezone.utc).isoformat()
            state_entry = {
                "url": entry.url,
                "audio_length": entry.audio_length or settings.notebooklm.audio_length,
                "prompt": entry.prompt or "default",
                "status": results[-1].status,
                "processed_at": now,
            }
            if results[-1].youtube_url:
                state_entry["youtube_url"] = results[-1].youtube_url
            if results[-1].error:
                state_entry["error"] = results[-1].error
            state["processed"].append(state_entry)
            state["last_run"] = now
            _save_state(state_path, state)

    return results
