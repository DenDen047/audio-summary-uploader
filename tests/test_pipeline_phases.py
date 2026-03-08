"""パイプラインのフェーズ遷移 (submit → collect → upload) の統合テスト."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automator.config import (
    CredentialsConfig,
    GeneralConfig,
    NotebookLMConfig,
    Settings,
    ThumbnailConfig,
    YouTubeConfig,
)
from automator.pipeline import (
    _load_state,
    collect_audio,
    run_pipeline,
    submit_urls,
    upload_videos,
)
from automator.url_parser import UrlEntry


@pytest.fixture()
def tmp_state(tmp_path: Path) -> Path:
    state_path = tmp_path / "data" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    return state_path


@pytest.fixture()
def settings(tmp_state: Path, tmp_path: Path) -> Settings:
    return Settings(
        notebooklm=NotebookLMConfig(
            backend="notebooklm-py",
            audio_language="ja",
            audio_length="default",
            generation_timeout_seconds=10,
            generation_poll_interval_seconds=1,
            prompt_presets={"default": "テスト用プロンプト"},
        ),
        youtube=YouTubeConfig(daily_upload_limit=5),
        thumbnail=ThumbnailConfig(),
        credentials=CredentialsConfig(),
        general=GeneralConfig(
            tmp_dir=str(tmp_path / "tmp"),
            state_file=str(tmp_state),
        ),
    )


def _mock_generation_status(status: str = "COMPLETED", task_id: str = "test-task-123"):
    """GenerationStatus のモックを作成."""
    gs = MagicMock()
    gs.status = status
    gs.task_id = task_id
    gs.is_complete = status == "COMPLETED"
    gs.is_failed = status == "FAILED"
    return gs


@pytest.fixture()
def mock_backend():
    """NotebookLMPyBackend のモック."""
    backend = AsyncMock()
    backend.create_notebook = AsyncMock(return_value="notebook-id-abc")
    backend.add_source = AsyncMock()
    backend.add_file_source = AsyncMock()
    backend.start_audio_generation = AsyncMock(return_value="test-task-123")
    backend.check_audio_status = AsyncMock(
        return_value=_mock_generation_status("COMPLETED")
    )
    backend.wait_for_audio = AsyncMock(
        return_value=_mock_generation_status("COMPLETED")
    )
    backend.download_audio = AsyncMock()
    backend.delete_notebook = AsyncMock()
    return backend


@pytest.mark.asyncio()
async def test_submit_sets_generating(settings: Settings, mock_backend: AsyncMock) -> None:
    """submit_urls がジョブを generating にセットすることを確認."""
    entries = [UrlEntry(url="https://example.com/article1")]

    with (
        patch("automator.pipeline._create_backend", return_value=mock_backend),
        patch("automator.pipeline.fetch_metadata") as mock_meta,
    ):
        mock_meta.return_value = MagicMock(
            title="Test Article",
            description="desc",
            og_image_url=None,
            site_name="Example",
            language="ja",
            url="https://example.com/article1",
        )

        results = await submit_urls(entries, settings)

    assert len(results) == 1
    assert results[0].status == "generating"

    state = _load_state(Path(settings.general.state_file))
    jobs = state["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["status"] == "generating"
    assert jobs[0]["notebook_id"] == "notebook-id-abc"
    assert jobs[0]["task_id"] == "test-task-123"


@pytest.mark.asyncio()
async def test_collect_transitions_to_video_ready(
    settings: Settings, mock_backend: AsyncMock, tmp_path: Path
) -> None:
    """collect_audio が generating → video_ready に遷移させることを確認."""
    # まず state に generating ジョブを作成
    state_path = Path(settings.general.state_file)
    state = {
        "last_run": None,
        "jobs": [
            {
                "url": "https://example.com/article1",
                "slug": "abc123",
                "audio_length": "default",
                "prompt": "default",
                "status": "generating",
                "notebook_id": "notebook-id-abc",
                "task_id": "test-task-123",
                "metadata": {
                    "title": "Test Article",
                    "description": "desc",
                    "og_image_url": None,
                    "site_name": "Example",
                    "language": "ja",
                },
                "audio_path": None,
                "thumbnail_path": None,
                "video_path": None,
                "youtube_url": None,
                "error": None,
                "submitted_at": "2026-01-01T00:00:00+00:00",
                "collected_at": None,
                "uploaded_at": None,
            }
        ],
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    audio_path = tmp_path / "tmp" / "audio" / "abc123.mp3"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"fake audio")
    mock_backend.download_audio.return_value = audio_path

    thumb_path = tmp_path / "tmp" / "thumbnails" / "abc123_thumb.png"
    video_path = tmp_path / "tmp" / "videos" / "abc123.mp4"

    with (
        patch("automator.pipeline._create_backend", return_value=mock_backend),
        patch("automator.pipeline.generate_thumbnail") as mock_thumb,
        patch("automator.pipeline.convert_to_video") as mock_video,
    ):
        mock_thumb.return_value = thumb_path
        mock_video.return_value = video_path

        results = await collect_audio(settings, poll=True)

    assert len(results) == 1
    assert results[0].status == "video_ready"

    state = _load_state(state_path)
    jobs = state["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["status"] == "video_ready"
    assert jobs[0]["collected_at"] is not None


@pytest.mark.asyncio()
async def test_collect_still_generating(
    settings: Settings, mock_backend: AsyncMock
) -> None:
    """collect_audio で音声がまだ生成中の場合、poll=True なら待機する."""
    state_path = Path(settings.general.state_file)
    state = {
        "last_run": None,
        "jobs": [
            {
                "url": "https://example.com/article1",
                "slug": "abc123",
                "audio_length": "default",
                "prompt": "default",
                "status": "generating",
                "notebook_id": "notebook-id-abc",
                "task_id": "test-task-123",
                "metadata": {
                    "title": "Test Article",
                    "description": "desc",
                    "og_image_url": None,
                    "site_name": "Example",
                    "language": "ja",
                },
                "audio_path": None,
                "thumbnail_path": None,
                "video_path": None,
                "youtube_url": None,
                "error": None,
                "submitted_at": "2026-01-01T00:00:00+00:00",
                "collected_at": None,
                "uploaded_at": None,
            }
        ],
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    # check_audio_status が PROCESSING を返す → wait_for_audio で COMPLETED
    mock_backend.check_audio_status.return_value = _mock_generation_status("PROCESSING")
    mock_backend.wait_for_audio.return_value = _mock_generation_status("COMPLETED")

    audio_path = Path(settings.general.tmp_dir) / "audio" / "abc123.mp3"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"fake audio")
    mock_backend.download_audio.return_value = audio_path

    with (
        patch("automator.pipeline._create_backend", return_value=mock_backend),
        patch("automator.pipeline.generate_thumbnail") as mock_thumb,
        patch("automator.pipeline.convert_to_video") as mock_video,
    ):
        mock_thumb.return_value = Path("/tmp/thumb.png")
        mock_video.return_value = Path("/tmp/video.mp4")

        results = await collect_audio(settings, poll=True)

    assert len(results) == 1
    assert results[0].status == "video_ready"
    mock_backend.wait_for_audio.assert_called_once()


@pytest.mark.asyncio()
async def test_full_pipeline_phase_transitions(
    settings: Settings, mock_backend: AsyncMock, tmp_path: Path
) -> None:
    """run_pipeline で queued → generating → video_ready → uploaded の遷移を確認."""
    # state に queued ジョブを事前作成 (Web GUI の /api/add と同等)
    state_path = Path(settings.general.state_file)
    state = {
        "last_run": None,
        "jobs": [
            {
                "url": "https://example.com/article1",
                "slug": "abc123",
                "audio_length": "default",
                "prompt": "default",
                "status": "queued",
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
        ],
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    entries = [UrlEntry(url="https://example.com/article1")]

    audio_path = tmp_path / "tmp" / "audio" / "abc123.mp3"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"fake audio")
    mock_backend.download_audio.return_value = audio_path

    thumb_path = tmp_path / "tmp" / "thumbnails" / "abc123_thumb.png"
    video_path = tmp_path / "tmp" / "videos" / "abc123.mp4"
    video_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.write_bytes(b"fake video")

    with (
        patch("automator.pipeline._create_backend", return_value=mock_backend),
        patch("automator.pipeline.fetch_metadata") as mock_meta,
        patch("automator.pipeline.generate_thumbnail") as mock_thumb_fn,
        patch("automator.pipeline.convert_to_video") as mock_video_fn,
        patch("automator.pipeline.authenticate") as mock_auth,
        patch("automator.pipeline.upload_video") as mock_upload,
    ):
        mock_meta.return_value = MagicMock(
            title="Test Article",
            description="desc",
            og_image_url=None,
            site_name="Example",
            language="ja",
            url="https://example.com/article1",
        )
        mock_thumb_fn.return_value = thumb_path
        mock_video_fn.return_value = video_path
        mock_auth.return_value = MagicMock()
        mock_upload.return_value = "https://youtube.com/watch?v=test123"

        results = await run_pipeline(entries, settings)

    # 最終状態を確認
    state = _load_state(state_path)
    jobs = state["jobs"]
    assert len(jobs) == 1
    job = jobs[0]
    assert job["status"] == "uploaded"
    assert job["youtube_url"] == "https://youtube.com/watch?v=test123"
    assert job["notebook_id"] == "notebook-id-abc"
    assert job["submitted_at"] is not None
    assert job["collected_at"] is not None
    assert job["uploaded_at"] is not None


@pytest.mark.asyncio()
async def test_queued_job_not_skipped_by_submit(settings: Settings, mock_backend: AsyncMock) -> None:
    """queued ステータスのジョブが submit_urls でスキップされないことを確認."""
    state_path = Path(settings.general.state_file)
    state = {
        "last_run": None,
        "jobs": [
            {
                "url": "https://example.com/article1",
                "slug": "abc123",
                "audio_length": "default",
                "prompt": "default",
                "status": "queued",
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
        ],
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    entries = [UrlEntry(url="https://example.com/article1")]

    with (
        patch("automator.pipeline._create_backend", return_value=mock_backend),
        patch("automator.pipeline.fetch_metadata") as mock_meta,
    ):
        mock_meta.return_value = MagicMock(
            title="Test Article",
            description="desc",
            og_image_url=None,
            site_name="Example",
            language="ja",
            url="https://example.com/article1",
        )

        results = await submit_urls(entries, settings)

    # queued ジョブはスキップされず、generating に遷移
    assert len(results) == 1
    assert results[0].status == "generating"

    state = _load_state(state_path)
    assert state["jobs"][0]["status"] == "generating"
    assert state["jobs"][0]["notebook_id"] == "notebook-id-abc"


@pytest.mark.asyncio()
async def test_collect_handles_lowercase_completed(
    settings: Settings, mock_backend: AsyncMock, tmp_path: Path
) -> None:
    """notebooklm-py が小文字 "completed" を返しても正しく処理されることを確認."""
    state_path = Path(settings.general.state_file)
    state = {
        "last_run": None,
        "jobs": [
            {
                "url": "https://example.com/article1",
                "slug": "abc123",
                "audio_length": "default",
                "prompt": "default",
                "status": "generating",
                "notebook_id": "notebook-id-abc",
                "task_id": "test-task-123",
                "metadata": {
                    "title": "Test Article",
                    "description": "desc",
                    "og_image_url": None,
                    "site_name": "Example",
                    "language": "ja",
                },
                "audio_path": None,
                "thumbnail_path": None,
                "video_path": None,
                "youtube_url": None,
                "error": None,
                "submitted_at": "2026-01-01T00:00:00+00:00",
                "collected_at": None,
                "uploaded_at": None,
            }
        ],
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")

    # ライブラリが小文字 "completed" を返すケース（実際の挙動）
    mock_backend.check_audio_status.return_value = _mock_generation_status("completed")

    audio_path = tmp_path / "tmp" / "audio" / "abc123.mp3"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"fake audio")
    mock_backend.download_audio.return_value = audio_path

    with (
        patch("automator.pipeline._create_backend", return_value=mock_backend),
        patch("automator.pipeline.generate_thumbnail") as mock_thumb,
        patch("automator.pipeline.convert_to_video") as mock_video,
    ):
        mock_thumb.return_value = Path("/tmp/thumb.png")
        mock_video.return_value = Path("/tmp/video.mp4")

        results = await collect_audio(settings, poll=True)

    assert len(results) == 1
    assert results[0].status == "video_ready"
    # wait_for_audio は呼ばれない（check_audio_status で completed 検知済み）
    mock_backend.wait_for_audio.assert_not_called()
