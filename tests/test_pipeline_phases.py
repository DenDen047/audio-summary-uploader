"""パイプラインのフェーズ遷移 (submit → collect → upload) の統合テスト."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from podcast.config import (
    CredentialsConfig,
    GeneralConfig,
    PodcastConfig,
    Settings,
    ThumbnailConfig,
    YouTubeConfig,
)
from podcast.pipeline import (
    _load_state,
    collect_audio,
    run_pipeline,
    submit_urls,
    upload_videos,
)
from podcast.url_parser import UrlEntry
from podcast.youtube import UploadResult
from sources.fetch import ExtractedSource


@pytest.fixture()
def tmp_state(tmp_path: Path) -> Path:
    state_path = tmp_path / "data" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    return state_path


@pytest.fixture()
def settings(tmp_state: Path, tmp_path: Path) -> Settings:
    return Settings(
        podcast=PodcastConfig(
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


def _mock_generation_status(status: str = "COMPLETED"):
    """GenerationStatus のモックを作成（実装が読むのは .status のみ）."""
    gs = MagicMock()
    gs.status = status
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


@pytest.fixture(autouse=True)
def mock_image_profile_resolution():
    """フェーズテストでは Gemini の実セッション確認を行わない."""
    default_storage = (
        Path.home()
        / ".notebooklm"
        / "profiles"
        / "default"
        / "storage_state.json"
    )
    with patch(
        "podcast.pipeline.resolve_google_storage_state",
        new=AsyncMock(return_value=default_storage),
    ):
        yield


@pytest.mark.asyncio()
async def test_submit_sets_generating(
    settings: Settings, mock_backend: AsyncMock
) -> None:
    """submit_urls がジョブを generating にセットすることを確認."""
    entries = [UrlEntry(url="https://example.com/article1")]

    with (
        patch("podcast.pipeline._create_backend", return_value=mock_backend),
        patch("podcast.pipeline.fetch_metadata") as mock_meta,
    ):
        mock_meta.return_value = MagicMock(
            title="Test Article",
            description="desc",
            og_image_url=None,
            site_name="Example",
            language="ja",
            favicon_url=None,
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
async def test_submit_spark_share_adds_text_source(
    settings: Settings, mock_backend: AsyncMock
) -> None:
    """Spark 共有 URL は抽出済み本文をテキストソースとして投入することを確認.

    NotebookLM に URL を直接取得させると、JS レンダリング前のシェル
    （アプリ宣伝文）だけを掴んで空音声を静かに量産することがあるため。
    """
    spark_url = "https://app.sparkmailapp.com/web-share/abc"
    entries = [UrlEntry(url=spark_url)]

    with (
        patch("podcast.pipeline._create_backend", return_value=mock_backend),
        patch("podcast.pipeline.fetch_metadata") as mock_meta,
        patch(
            "podcast.pipeline.resolve_source",
            return_value=ExtractedSource(
                url=spark_url, title="今週のAIニュース", text="本文" * 200
            ),
        ),
    ):
        results = await submit_urls(entries, settings)

    assert results[0].status == "generating"
    # OGP は取得しない（Spark はシェルしか返さず title=宣伝文になるため）
    mock_meta.assert_not_called()
    mock_backend.add_source.assert_not_called()
    mock_backend.add_text_source.assert_awaited_once_with(
        "notebook-id-abc", "今週のAIニュース", "本文" * 200
    )

    state = _load_state(Path(settings.general.state_file))
    assert state["jobs"][0]["metadata"]["title"] == "今週のAIニュース"


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
        patch("podcast.pipeline._create_backend", return_value=mock_backend),
        patch(
            "podcast.pipeline._generate_backgrounds",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "podcast.pipeline._generate_thumb_base",
            new=AsyncMock(return_value=None),
        ),
        patch("podcast.pipeline.generate_thumbnail") as mock_thumb,
        patch("podcast.pipeline.convert_to_video") as mock_video,
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
async def test_collect_falls_back_to_active_notebook_profile_for_visuals(
    settings: Settings, mock_backend: AsyncMock, tmp_path: Path
) -> None:
    """Web UI の NotebookLM ジョブでも生成背景を動画変換へ渡す."""
    settings.podcast.image_profile = "imagegen"
    state_path = Path(settings.general.state_file)
    _write_state(state_path, [_make_job()])

    audio_path = tmp_path / "audio.mp3"
    audio_path.write_bytes(b"fake audio")
    mock_backend.download_audio.return_value = audio_path

    default_storage = (
        Path.home()
        / ".notebooklm"
        / "profiles"
        / "default"
        / "storage_state.json"
    )
    thumb_path = tmp_path / "thumb.png"
    background_path = tmp_path / "background.png"
    video_path = tmp_path / "video.mp4"

    with (
        patch("podcast.pipeline._create_backend", return_value=mock_backend),
        patch(
            "podcast.pipeline.resolve_google_storage_state",
            new=AsyncMock(return_value=default_storage),
        ) as mock_resolve,
        patch(
            "podcast.pipeline._compose_topic_thumbnail",
            new=AsyncMock(return_value=thumb_path),
        ) as mock_thumbnail,
        patch(
            "podcast.pipeline._generate_backgrounds",
            new=AsyncMock(return_value=[background_path]),
        ) as mock_backgrounds,
        patch(
            "podcast.pipeline.convert_to_video",
            new=AsyncMock(return_value=video_path),
        ) as mock_video,
    ):
        results = await collect_audio(settings, poll=True)

    assert results[0].status == "video_ready"
    candidate_paths = mock_resolve.await_args.args[0]
    assert [path.parent.name for path in candidate_paths] == [
        "imagegen",
        "default",
    ]
    assert mock_thumbnail.await_args.kwargs["storage_state_path"] == default_storage
    assert mock_backgrounds.await_args.kwargs["storage_state_path"] == default_storage
    assert mock_video.await_args.kwargs["background_paths"] == [background_path]

    job = _load_state(state_path)["jobs"][0]
    assert job["image_profile_used"] == "default"
    assert job["background_paths"] == [str(background_path)]


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
        patch("podcast.pipeline._create_backend", return_value=mock_backend),
        patch(
            "podcast.pipeline._generate_backgrounds",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "podcast.pipeline._generate_thumb_base",
            new=AsyncMock(return_value=None),
        ),
        patch("podcast.pipeline.generate_thumbnail") as mock_thumb,
        patch("podcast.pipeline.convert_to_video") as mock_video,
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
        patch("podcast.pipeline._create_backend", return_value=mock_backend),
        patch("podcast.pipeline.fetch_metadata") as mock_meta,
        patch(
            "podcast.pipeline._generate_backgrounds",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "podcast.pipeline._generate_thumb_base",
            new=AsyncMock(return_value=None),
        ),
        patch("podcast.pipeline.generate_thumbnail") as mock_thumb_fn,
        patch("podcast.pipeline.convert_to_video") as mock_video_fn,
        patch("podcast.pipeline.authenticate") as mock_auth,
        patch("podcast.pipeline.upload_video") as mock_upload,
    ):
        mock_meta.return_value = MagicMock(
            title="Test Article",
            description="desc",
            og_image_url=None,
            site_name="Example",
            language="ja",
            favicon_url=None,
            url="https://example.com/article1",
        )
        mock_thumb_fn.return_value = thumb_path
        mock_video_fn.return_value = video_path
        mock_auth.return_value = MagicMock()
        mock_upload.return_value = UploadResult(
            youtube_url="https://youtube.com/watch?v=test123", thumbnail_set=True
        )

        await run_pipeline(entries, settings)

    # 最終状態を確認
    state = _load_state(state_path)
    jobs = state["jobs"]
    assert len(jobs) == 1
    job = jobs[0]
    assert job["status"] == "uploaded"
    assert job["youtube_url"] == "https://youtube.com/watch?v=test123"
    # collect 完了時にノートブックは削除され、参照もクリアされる
    assert job["notebook_id"] is None
    assert job["submitted_at"] is not None
    assert job["collected_at"] is not None
    assert job["uploaded_at"] is not None


@pytest.mark.asyncio()
async def test_queued_job_not_skipped_by_submit(
    settings: Settings, mock_backend: AsyncMock
) -> None:
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
        patch("podcast.pipeline._create_backend", return_value=mock_backend),
        patch("podcast.pipeline.fetch_metadata") as mock_meta,
    ):
        mock_meta.return_value = MagicMock(
            title="Test Article",
            description="desc",
            og_image_url=None,
            site_name="Example",
            language="ja",
            favicon_url=None,
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
        patch("podcast.pipeline._create_backend", return_value=mock_backend),
        patch(
            "podcast.pipeline._generate_backgrounds",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "podcast.pipeline._generate_thumb_base",
            new=AsyncMock(return_value=None),
        ),
        patch("podcast.pipeline.generate_thumbnail") as mock_thumb,
        patch("podcast.pipeline.convert_to_video") as mock_video,
    ):
        mock_thumb.return_value = Path("/tmp/thumb.png")
        mock_video.return_value = Path("/tmp/video.mp4")

        results = await collect_audio(settings, poll=True)

    assert len(results) == 1
    assert results[0].status == "video_ready"
    # wait_for_audio は呼ばれない（check_audio_status で completed 検知済み）
    mock_backend.wait_for_audio.assert_not_called()


# --- レビュー修正で追加された挙動のテスト ---


def _make_job(**overrides) -> dict:
    """テスト用ジョブ dict を生成する."""
    job = {
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
    job.update(overrides)
    return job


def _write_state(state_path: Path, jobs: list[dict]) -> None:
    state_path.write_text(
        json.dumps({"last_run": None, "jobs": jobs}), encoding="utf-8"
    )


@pytest.mark.asyncio()
async def test_collect_terminal_failed_without_poll(
    settings: Settings, mock_backend: AsyncMock
) -> None:
    """poll なしでも terminal な FAILED は failed に遷移しノートブックを掃除する."""
    state_path = Path(settings.general.state_file)
    _write_state(state_path, [_make_job()])

    mock_backend.check_audio_status.return_value = _mock_generation_status("FAILED")

    with patch("podcast.pipeline._create_backend", return_value=mock_backend):
        results = await collect_audio(settings, poll=False)

    assert results[0].status == "failed"
    assert "FAILED" in results[0].error
    mock_backend.delete_notebook.assert_called_once_with("notebook-id-abc")

    state = _load_state(state_path)
    assert state["jobs"][0]["status"] == "failed"
    assert state["jobs"][0]["notebook_id"] is None


@pytest.mark.asyncio()
async def test_collect_timeout_keeps_generating(
    settings: Settings, mock_backend: AsyncMock
) -> None:
    """wait_for_audio のタイムアウトでは failed にせず generating を維持する."""
    state_path = Path(settings.general.state_file)
    _write_state(state_path, [_make_job()])

    mock_backend.check_audio_status.return_value = _mock_generation_status("PROCESSING")
    mock_backend.wait_for_audio.side_effect = TimeoutError("timed out")

    with patch("podcast.pipeline._create_backend", return_value=mock_backend):
        results = await collect_audio(settings, poll=True)

    assert results[0].status == "generating"
    assert results[0].error is None
    mock_backend.delete_notebook.assert_not_called()

    state = _load_state(state_path)
    assert state["jobs"][0]["status"] == "generating"
    assert state["jobs"][0]["notebook_id"] == "notebook-id-abc"


@pytest.mark.asyncio()
async def test_collect_missing_task_id_fails_with_clear_error(
    settings: Settings, mock_backend: AsyncMock
) -> None:
    """submit が中断されたジョブ (task_id なし) は明確なエラーで failed になる."""
    state_path = Path(settings.general.state_file)
    _write_state(state_path, [_make_job(task_id=None)])

    with patch("podcast.pipeline._create_backend", return_value=mock_backend):
        results = await collect_audio(settings, poll=True)

    assert results[0].status == "failed"
    assert "submit が完了していない" in results[0].error
    # 残っているノートブックは掃除される
    mock_backend.delete_notebook.assert_called_once_with("notebook-id-abc")
    mock_backend.check_audio_status.assert_not_called()

    state = _load_state(state_path)
    assert state["jobs"][0]["status"] == "failed"


@pytest.mark.asyncio()
async def test_dry_run_does_not_write_state(
    settings: Settings, mock_backend: AsyncMock
) -> None:
    """dry-run は state.json を汚染しない (以後の本実行を壊さない)."""
    state_path = Path(settings.general.state_file)
    entries = [UrlEntry(url="https://example.com/article1")]

    with (
        patch("podcast.pipeline._create_backend", return_value=mock_backend),
        patch("podcast.pipeline.fetch_metadata") as mock_meta,
    ):
        mock_meta.return_value = MagicMock(
            title="Test Article",
            description="desc",
            og_image_url=None,
            site_name="Example",
            language="ja",
            favicon_url=None,
            url="https://example.com/article1",
        )
        results = await submit_urls(entries, settings, dry_run=True)

    assert results[0].status == "generating (dry-run)"
    assert not state_path.exists()
    mock_backend.create_notebook.assert_not_called()


@pytest.mark.asyncio()
async def test_submit_failure_does_not_clobber_other_jobs(
    settings: Settings, mock_backend: AsyncMock
) -> None:
    """submit 中の state 書き込みが、並行して書き込まれた他ジョブを巻き戻さない."""
    state_path = Path(settings.general.state_file)
    other_job = _make_job(
        url="https://example.com/other", slug="other1", status="uploaded"
    )
    _write_state(state_path, [other_job])

    async def _add_source_with_concurrent_edit(notebook_id: str, url: str) -> None:
        # submit 実行中に Web ハンドラが other ジョブを削除した状況を再現
        state = _load_state(state_path)
        state["jobs"] = [j for j in state["jobs"] if j["slug"] != "other1"]
        from podcast.pipeline import _save_state

        _save_state(state_path, state)
        raise RuntimeError("source add failed")

    mock_backend.add_source.side_effect = _add_source_with_concurrent_edit

    entries = [UrlEntry(url="https://example.com/article1")]
    with (
        patch("podcast.pipeline._create_backend", return_value=mock_backend),
        patch("podcast.pipeline.fetch_metadata") as mock_meta,
    ):
        mock_meta.return_value = MagicMock(
            title="Test Article",
            description="desc",
            og_image_url=None,
            site_name="Example",
            language="ja",
            favicon_url=None,
            url="https://example.com/article1",
        )
        results = await submit_urls(entries, settings)

    assert results[0].status == "failed"
    state = _load_state(state_path)
    # 削除された other ジョブは復活しない
    urls = [j["url"] for j in state["jobs"]]
    assert "https://example.com/other" not in urls
    assert urls == ["https://example.com/article1"]
    assert state["jobs"][0]["status"] == "failed"


@pytest.mark.asyncio()
async def test_submit_cleans_up_old_notebook(
    settings: Settings, mock_backend: AsyncMock
) -> None:
    """failed ジョブの再 submit 時、残存ノートブックを best-effort で削除する."""
    state_path = Path(settings.general.state_file)
    _write_state(
        state_path,
        [_make_job(status="failed", notebook_id="nb-old", error="boom")],
    )

    entries = [UrlEntry(url="https://example.com/article1")]
    with (
        patch("podcast.pipeline._create_backend", return_value=mock_backend),
        patch("podcast.pipeline.fetch_metadata") as mock_meta,
    ):
        mock_meta.return_value = MagicMock(
            title="Test Article",
            description="desc",
            og_image_url=None,
            site_name="Example",
            language="ja",
            favicon_url=None,
            url="https://example.com/article1",
        )
        results = await submit_urls(entries, settings)

    assert results[0].status == "generating"
    mock_backend.delete_notebook.assert_called_once_with("nb-old")

    state = _load_state(state_path)
    assert state["jobs"][0]["notebook_id"] == "notebook-id-abc"


@pytest.mark.asyncio()
async def test_upload_auth_failure_non_interactive_marks_failed(
    settings: Settings,
) -> None:
    """Web コンテキストでの認証失敗はジョブを failed にして可視化する."""
    state_path = Path(settings.general.state_file)
    _write_state(
        state_path,
        [_make_job(status="video_ready", video_path="/tmp/v.mp4",
                   thumbnail_path="/tmp/t.png")],
    )

    with patch(
        "podcast.pipeline.authenticate",
        side_effect=RuntimeError("YouTube の認証が必要です"),
    ) as mock_auth:
        results = await upload_videos(settings, allow_interactive_auth=False)

    # 非対話フラグが authenticate まで配線されている (対話 OAuth 復活の防止)
    assert mock_auth.call_args.kwargs["allow_interactive"] is False
    assert results[0].status == "failed"
    assert "認証" in results[0].error

    state = _load_state(state_path)
    job = state["jobs"][0]
    assert job["status"] == "failed"
    # 動画は残るため、リトライで video_ready に戻してアップロードのみ再試行できる
    assert job["video_path"] == "/tmp/v.mp4"


@pytest.mark.asyncio()
async def test_upload_auth_failure_interactive_raises(
    settings: Settings,
) -> None:
    """CLI コンテキストでの認証失敗は Fail Fast でそのまま例外にする."""
    state_path = Path(settings.general.state_file)
    _write_state(state_path, [_make_job(status="video_ready")])

    with (
        patch(
            "podcast.pipeline.authenticate",
            side_effect=RuntimeError("auth failed"),
        ),
        pytest.raises(RuntimeError, match="auth failed"),
    ):
        await upload_videos(settings, allow_interactive_auth=True)


@pytest.mark.asyncio()
async def test_upload_reapplies_pending_thumbnail(
    settings: Settings, tmp_path: Path
) -> None:
    """サムネ未適用(thumbnail_pending)の既存動画に再適用し pending を下ろす."""
    state_path = Path(settings.general.state_file)
    thumb = tmp_path / "t.png"
    thumb.write_bytes(b"png")
    _write_state(state_path, [_make_job(
        status="uploaded", thumbnail_pending=True,
        youtube_url="https://youtu.be/vid123", thumbnail_path=str(thumb),
    )])

    with (
        patch("podcast.pipeline.authenticate", return_value=MagicMock()),
        patch(
            "podcast.pipeline.set_thumbnail", new=AsyncMock(return_value="ok")
        ) as mock_set,
    ):
        await upload_videos(settings, allow_interactive_auth=False)

    # 動画IDが youtu.be URL から正しく抽出され再適用される
    assert mock_set.await_args.args[1] == "vid123"
    job = _load_state(state_path)["jobs"][0]
    assert job["thumbnail_pending"] is False


@pytest.mark.asyncio()
async def test_upload_pending_thumbnail_quota_stays_pending(
    settings: Settings, tmp_path: Path
) -> None:
    """再適用が 429(quota) なら pending を維持し次回に持ち越す."""
    state_path = Path(settings.general.state_file)
    thumb = tmp_path / "t.png"
    thumb.write_bytes(b"png")
    _write_state(state_path, [_make_job(
        status="uploaded", thumbnail_pending=True,
        youtube_url="https://youtu.be/vid123", thumbnail_path=str(thumb),
    )])

    with (
        patch("podcast.pipeline.authenticate", return_value=MagicMock()),
        patch(
            "podcast.pipeline.set_thumbnail", new=AsyncMock(return_value="quota")
        ),
    ):
        await upload_videos(settings, allow_interactive_auth=False)

    job = _load_state(state_path)["jobs"][0]
    assert job["thumbnail_pending"] is True


@pytest.mark.asyncio()
async def test_simple_mode_collect_skips_ai_backgrounds(
    settings: Settings, mock_backend: AsyncMock, tmp_path: Path
) -> None:
    """簡易動画モードは collect で AI背景生成を呼ばない（サムネはマスコット合成）."""
    settings.general.simple_video_mode = True
    state_path = Path(settings.general.state_file)
    _write_state(state_path, [_make_job()])
    mock_backend.download_audio.return_value = tmp_path / "a.mp3"

    with (
        patch("podcast.pipeline._create_backend", return_value=mock_backend),
        patch("podcast.pipeline._generate_backgrounds") as mock_bg,
        patch(
            "podcast.pipeline.compose_thumbnail",
            return_value=tmp_path / "t.png",
        ) as mock_compose,
        patch(
            "podcast.pipeline.convert_to_video",
            new=AsyncMock(return_value=tmp_path / "v.mp4"),
        ),
    ):
        results = await collect_audio(settings, poll=True)

    assert results[0].status == "video_ready"
    mock_bg.assert_not_called()
    mock_compose.assert_called_once()


@pytest.mark.asyncio()
async def test_simple_mode_upload_skips_custom_thumbnail(
    settings: Settings,
) -> None:
    """簡易動画モードは upload でカスタムサムネ(thumbnail_path)を渡さない(429回避)."""
    settings.general.simple_video_mode = True
    state_path = Path(settings.general.state_file)
    _write_state(state_path, [_make_job(
        status="video_ready", video_path="/tmp/v.mp4",
        thumbnail_path="/tmp/t.png",
    )])

    with (
        patch("podcast.pipeline.authenticate", return_value=MagicMock()),
        patch(
            "podcast.pipeline.upload_video",
            new=AsyncMock(return_value=UploadResult(
                youtube_url="https://youtu.be/x", thumbnail_set=True
            )),
        ) as mock_upload,
    ):
        await upload_videos(settings, allow_interactive_auth=False)

    params = mock_upload.await_args.args[1]
    assert params.thumbnail_path is None


@pytest.mark.asyncio()
async def test_collect_single_not_found_not_terminal(
    settings: Settings, mock_backend: AsyncMock
) -> None:
    """単発の not_found は一時的 lag の可能性があるため terminal 扱いしない."""
    state_path = Path(settings.general.state_file)
    _write_state(state_path, [_make_job()])

    mock_backend.check_audio_status.return_value = _mock_generation_status("not_found")

    with patch("podcast.pipeline._create_backend", return_value=mock_backend):
        results = await collect_audio(settings, poll=False)

    assert results[0].status == "generating"
    assert results[0].error is None
    # 生成中の可能性があるノートブックを削除しない
    mock_backend.delete_notebook.assert_not_called()

    state = _load_state(state_path)
    assert state["jobs"][0]["status"] == "generating"


@pytest.mark.asyncio()
async def test_collect_network_error_keeps_generating(
    settings: Settings, mock_backend: AsyncMock
) -> None:
    """ポーリング中の一時的なネットワークエラーでは failed にしない."""
    from notebooklm.exceptions import NetworkError

    state_path = Path(settings.general.state_file)
    _write_state(state_path, [_make_job()])

    mock_backend.check_audio_status.return_value = _mock_generation_status("PROCESSING")
    mock_backend.wait_for_audio.side_effect = NetworkError("connection reset")

    with patch("podcast.pipeline._create_backend", return_value=mock_backend):
        results = await collect_audio(settings, poll=True)

    assert results[0].status == "generating"
    mock_backend.delete_notebook.assert_not_called()

    state = _load_state(state_path)
    assert state["jobs"][0]["status"] == "generating"
    assert state["jobs"][0]["notebook_id"] == "notebook-id-abc"


@pytest.mark.asyncio()
async def test_resubmit_clears_stale_task_id(
    settings: Settings, mock_backend: AsyncMock
) -> None:
    """再 submit の最初の永続化で旧 task_id がクリアされる.

    クリアしないと、submit 中断時に「新 notebook_id + 旧 task_id」の
    不整合ペアが残り、collect の中断検出ガードをすり抜ける。
    """
    state_path = Path(settings.general.state_file)
    _write_state(
        state_path,
        [_make_job(status="failed", notebook_id=None, task_id="task-old",
                   error="boom")],
    )

    # start_audio_generation で失敗させ、1回目の upsert 内容を観測する
    mock_backend.start_audio_generation.side_effect = RuntimeError("gen failed")

    entries = [UrlEntry(url="https://example.com/article1")]
    with (
        patch("podcast.pipeline._create_backend", return_value=mock_backend),
        patch("podcast.pipeline.fetch_metadata") as mock_meta,
    ):
        mock_meta.return_value = MagicMock(
            title="Test Article",
            description="desc",
            og_image_url=None,
            site_name="Example",
            language="ja",
            favicon_url=None,
            url="https://example.com/article1",
        )
        results = await submit_urls(entries, settings)

    assert results[0].status == "failed"
    state = _load_state(state_path)
    assert state["jobs"][0]["task_id"] is None


@pytest.mark.asyncio()
async def test_compose_topic_thumbnail_uses_ai_base(
    settings: Settings, tmp_path: Path
) -> None:
    """AIベース生成が成功したら、そのベース画像で compose する."""
    from podcast.category import style_for_category
    from podcast.pipeline import _compose_topic_thumbnail
    from podcast.thumbnail import ThumbCopy

    ai_base = tmp_path / "base.png"
    with (
        patch(
            "podcast.pipeline._generate_thumb_base",
            new=AsyncMock(return_value=ai_base),
        ),
        patch(
            "podcast.pipeline.compose_thumbnail",
            return_value=tmp_path / "out.png",
        ) as mock_compose,
    ):
        out = await _compose_topic_thumbnail(
            "slug", tmp_path, settings, "見出し",
            style_for_category("default"), ThumbCopy(bottom="x"),
            tmp_path / "out.png", "example.com",
        )

    assert out == tmp_path / "out.png"
    assert mock_compose.call_args.args[0] == ai_base


@pytest.mark.asyncio()
async def test_compose_topic_thumbnail_falls_back_to_mascot(
    settings: Settings, tmp_path: Path
) -> None:
    """AIベース生成が失敗(None)したら固定マスコットで compose する."""
    from podcast.category import style_for_category
    from podcast.pipeline import _MASCOT_BASE, _compose_topic_thumbnail
    from podcast.thumbnail import ThumbCopy

    with (
        patch(
            "podcast.pipeline._generate_thumb_base",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "podcast.pipeline.compose_thumbnail",
            return_value=tmp_path / "out.png",
        ) as mock_compose,
    ):
        await _compose_topic_thumbnail(
            "slug", tmp_path, settings, "見出し",
            style_for_category("default"), ThumbCopy(bottom="x"),
            tmp_path / "out.png", "example.com",
        )

    assert mock_compose.call_args.args[0] == _MASCOT_BASE


@pytest.mark.asyncio()
async def test_compose_topic_thumbnail_simple_mode_skips_ai(
    settings: Settings, tmp_path: Path
) -> None:
    """簡易動画モードは AIベース生成を呼ばず固定マスコットで compose する."""
    from podcast.category import style_for_category
    from podcast.pipeline import _MASCOT_BASE, _compose_topic_thumbnail
    from podcast.thumbnail import ThumbCopy

    settings.general.simple_video_mode = True
    with (
        patch("podcast.pipeline._generate_thumb_base") as mock_base,
        patch(
            "podcast.pipeline.compose_thumbnail",
            return_value=tmp_path / "out.png",
        ) as mock_compose,
    ):
        await _compose_topic_thumbnail(
            "slug", tmp_path, settings, "見出し",
            style_for_category("default"), ThumbCopy(bottom="x"),
            tmp_path / "out.png", "example.com",
        )

    mock_base.assert_not_called()
    assert mock_compose.call_args.args[0] == _MASCOT_BASE


@pytest.mark.asyncio()
async def test_collect_resumes_from_checkpoint(
    settings: Settings, mock_backend: AsyncMock
) -> None:
    """ノートブック削除後に落ちたジョブは、音声生成せず動画化から再開する."""
    state_path = Path(settings.general.state_file)
    audio_path = Path(settings.general.tmp_dir) / "audio" / "abc123.mp3"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"fake audio")

    state = {
        "last_run": None,
        "jobs": [
            {
                "url": "https://example.com/article1",
                "slug": "abc123",
                "mode": "podcast",
                "audio_length": "default",
                "prompt": "default",
                "status": "generating",
                "notebook_id": None,          # NotebookLM の作業は完了済み
                "task_id": "task-1",
                "metadata": {
                    "title": "日本語タイトル",
                    "description": "",
                    "og_image_url": None,
                    "site_name": "example.com",
                    "language": "ja",
                    "favicon_url": None,
                },
                "category": "news",
                "thumb_copy": {
                    "top": "AIニュース",
                    "mid": "",
                    "bottom": "つづきは動画で",
                    "highlight": "",
                },
                "audio_path": str(audio_path),
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

    with (
        patch("podcast.pipeline._create_backend", return_value=mock_backend),
        patch(
            "podcast.pipeline._generate_backgrounds",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "podcast.pipeline._compose_topic_thumbnail",
            new=AsyncMock(return_value=Path("/tmp/thumb.png")),
        ),
        patch("podcast.pipeline.convert_to_video") as mock_video,
    ):
        mock_video.return_value = Path("/tmp/video.mp4")
        results = await collect_audio(settings, poll=True)

    assert len(results) == 1
    assert results[0].status == "video_ready"
    # 音声生成・DL・chat は一切呼ばれない
    mock_backend.check_audio_status.assert_not_called()
    mock_backend.wait_for_audio.assert_not_called()
    mock_backend.download_audio.assert_not_called()
    mock_backend.ask.assert_not_called()

    job = _load_state(state_path)["jobs"][0]
    assert job["status"] == "video_ready"
    assert job["metadata"]["title"] == "日本語タイトル"


@pytest.mark.asyncio()
async def test_collect_cannot_resume_when_audio_file_is_gone(
    settings: Settings, mock_backend: AsyncMock
) -> None:
    """チェックポイントの音声ファイルが消えていれば再開せず failed にする."""
    state_path = Path(settings.general.state_file)
    state = {
        "last_run": None,
        "jobs": [
            {
                "url": "https://example.com/article1",
                "slug": "abc123",
                "mode": "podcast",
                "audio_length": "default",
                "prompt": "default",
                "status": "generating",
                "notebook_id": None,
                "task_id": None,
                "metadata": {"title": "T", "description": "", "og_image_url": None,
                             "site_name": None, "language": None,
                             "favicon_url": None},
                "category": "news",
                "thumb_copy": {"top": "AI", "mid": "", "bottom": "B",
                               "highlight": ""},
                "audio_path": str(
                    Path(settings.general.tmp_dir) / "audio" / "gone.mp3"
                ),
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

    with patch("podcast.pipeline._create_backend", return_value=mock_backend):
        results = await collect_audio(settings, poll=True)

    assert results[0].status == "failed"
