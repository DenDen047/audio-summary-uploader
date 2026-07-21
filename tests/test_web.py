"""Web ダッシュボードのテスト."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from automator.config import (
    CredentialsConfig,
    GeneralConfig,
    NotebookLMConfig,
    Settings,
    ThumbnailConfig,
    YouTubeConfig,
)
from automator.web.app import create_app


@pytest.fixture()
def tmp_state(tmp_path: Path) -> Path:
    """一時的な state.json を返す."""
    return tmp_path / "state.json"


@pytest.fixture()
def settings(tmp_state: Path) -> Settings:
    """テスト用 Settings."""
    return Settings(
        notebooklm=NotebookLMConfig(
            prompt_presets={"default": "Summarize", "paper": "Summarize paper"},
        ),
        youtube=YouTubeConfig(),
        thumbnail=ThumbnailConfig(),
        credentials=CredentialsConfig(),
        general=GeneralConfig(
            state_file=str(tmp_state),
        ),
    )


@pytest.fixture()
def client(settings: Settings) -> TestClient:
    """FastAPI TestClient."""
    app = create_app(settings)
    return TestClient(app)


@pytest.fixture()
def state_with_jobs(tmp_state: Path) -> None:
    """テスト用の state.json を作成."""
    state = {
        "last_run": "2026-03-08T00:00:00+00:00",
        "jobs": [
            {
                "url": "https://example.com/article1",
                "slug": "aaa111bbb222",
                "audio_length": "default",
                "prompt": "default",
                "status": "generating",
                "notebook_id": "nb-1",
                "task_id": "task-1",
                "metadata": {"title": "Article One", "description": "", "og_image_url": None, "site_name": "Example", "language": "ja"},
                "audio_path": None,
                "thumbnail_path": None,
                "video_path": None,
                "youtube_url": None,
                "error": None,
                "submitted_at": "2026-03-08T00:00:00+00:00",
                "collected_at": None,
                "uploaded_at": None,
            },
            {
                "url": "https://example.com/article2",
                "slug": "ccc333ddd444",
                "audio_length": "short",
                "prompt": "paper",
                "status": "uploaded",
                "notebook_id": "nb-2",
                "task_id": "task-2",
                "metadata": {"title": "Article Two", "description": "", "og_image_url": None, "site_name": "Example", "language": "ja"},
                "audio_path": "/tmp/audio.mp3",
                "thumbnail_path": "/tmp/thumb.png",
                "video_path": "/tmp/video.mp4",
                "youtube_url": "https://youtu.be/test123",
                "error": None,
                "submitted_at": "2026-03-08T00:00:00+00:00",
                "collected_at": "2026-03-08T01:00:00+00:00",
                "uploaded_at": "2026-03-08T02:00:00+00:00",
            },
            {
                "url": "https://example.com/article3",
                "slug": "eee555fff666",
                "audio_length": "default",
                "prompt": "default",
                "status": "failed",
                "notebook_id": "nb-3",
                "task_id": "task-3",
                "metadata": {"title": "Article Three", "description": "", "og_image_url": None, "site_name": None, "language": None},
                "audio_path": None,
                "thumbnail_path": None,
                "video_path": None,
                "youtube_url": None,
                "error": "NotebookLM timeout after 600s",
                "submitted_at": "2026-03-08T00:00:00+00:00",
                "collected_at": None,
                "uploaded_at": None,
            },
        ],
    }
    tmp_state.write_text(json.dumps(state, ensure_ascii=False))


class TestDashboard:
    """ダッシュボードページのテスト."""

    def test_empty_dashboard(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "動画解説スタジオ" in resp.text
        assert "作成中の動画はありません" in resp.text
        assert "完了した動画はありません" in resp.text

    def test_dashboard_with_jobs(
        self, client: TestClient, state_with_jobs: None
    ) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Article One" in resp.text
        assert "Article Two" in resp.text
        assert "Article Three" in resp.text
        assert "動画を生成中" in resp.text
        assert "NotebookLM timeout" in resp.text

    def test_presets_in_form(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert '<option value="default"' in resp.text
        assert '<option value="paper"' in resp.text
        assert '<option value="lecture" selected' in resp.text
        assert "澪と透の解説動画" in resp.text
        assert 'name="privacy_status"' in resp.text
        assert '<option value="unlisted" selected' in resp.text
        assert "限定公開" in resp.text
        assert "一般公開" in resp.text


class TestPartials:
    """htmx パーシャルのテスト."""

    def test_header_badge_empty(self, client: TestClient) -> None:
        resp = client.get("/partials/header-badge")
        assert resp.status_code == 200
        assert "processing" not in resp.text

    def test_header_badge_with_jobs(
        self, client: TestClient, state_with_jobs: None
    ) -> None:
        resp = client.get("/partials/header-badge")
        assert resp.status_code == 200
        assert "1 processing" in resp.text

    def test_processing_partial(
        self, client: TestClient, state_with_jobs: None
    ) -> None:
        resp = client.get("/partials/processing")
        assert resp.status_code == 200
        assert "Article One" in resp.text
        assert "Article Two" not in resp.text  # uploaded は表示しない

    def test_completed_partial(
        self, client: TestClient, state_with_jobs: None
    ) -> None:
        resp = client.get("/partials/completed")
        assert resp.status_code == 200
        assert "Article Two" in resp.text
        assert "Article Three" in resp.text
        assert "Article One" not in resp.text  # generating は表示しない


class TestAPI:
    """API エンドポイントのテスト."""

    def test_add_urls_empty(self, client: TestClient) -> None:
        resp = client.post("/api/add", data={"urls": "", "prompt": "default", "audio_length": "default"})
        assert resp.status_code == 400

    def test_add_urls(self, client: TestClient, tmp_state: Path) -> None:
        with patch("automator.web.routes.enqueue_urls") as mock_enqueue:
            resp = client.post(
                "/api/add",
                data={
                    "urls": "https://example.com/new",
                    "prompt": "default",
                    "audio_length": "short",
                    "privacy_status": "public",
                },
            )
        assert resp.status_code == 200
        mock_enqueue.assert_called_once()
        entries = mock_enqueue.call_args[0][0]
        assert len(entries) == 1
        assert entries[0].url == "https://example.com/new"
        assert entries[0].audio_length == "short"
        assert entries[0].prompt == "default"
        assert entries[0].privacy_status == "public"

        # ジョブが即座に state.json に "queued" で書き込まれている
        state = json.loads(tmp_state.read_text())
        queued = [j for j in state["jobs"] if j["status"] == "queued"]
        assert len(queued) == 1
        assert queued[0]["url"] == "https://example.com/new"
        assert queued[0]["privacy_status"] == "public"

    def test_add_urls_shows_immediately(
        self, client: TestClient, tmp_state: Path
    ) -> None:
        """Add 直後のレスポンスに queued ジョブが含まれる."""
        with patch("automator.web.routes.enqueue_urls"):
            resp = client.post(
                "/api/add",
                data={
                    "urls": "https://example.com/instant",
                    "prompt": "default",
                    "audio_length": "default",
                },
            )
        assert resp.status_code == 200
        assert "準備中" in resp.text

    def test_add_multiple_urls(self, client: TestClient) -> None:
        with patch("automator.web.routes.enqueue_urls") as mock_enqueue:
            resp = client.post(
                "/api/add",
                data={
                    "urls": "https://example.com/a\nhttps://example.com/b",
                    "prompt": "default",
                    "audio_length": "default",
                },
            )
        assert resp.status_code == 200
        entries = mock_enqueue.call_args[0][0]
        assert len(entries) == 2

    def test_delete_job(
        self, client: TestClient, state_with_jobs: None, tmp_state: Path
    ) -> None:
        resp = client.delete("/api/jobs/ccc333ddd444")
        assert resp.status_code == 200

        state = json.loads(tmp_state.read_text())
        slugs = [j["slug"] for j in state["jobs"]]
        assert "ccc333ddd444" not in slugs
        assert "aaa111bbb222" in slugs

    def test_clear_completed(
        self, client: TestClient, state_with_jobs: None, tmp_state: Path
    ) -> None:
        resp = client.post("/api/clear-completed")
        assert resp.status_code == 200

        state = json.loads(tmp_state.read_text())
        statuses = [j["status"] for j in state["jobs"]]
        assert "uploaded" not in statuses
        # failed と generating は残る
        assert "failed" in statuses
        assert "generating" in statuses

    def test_retry_job(
        self, client: TestClient, state_with_jobs: None, tmp_state: Path
    ) -> None:
        with patch("automator.web.routes.enqueue_urls") as mock_enqueue:
            resp = client.post("/api/retry/eee555fff666")
        assert resp.status_code == 200
        mock_enqueue.assert_called_once()
        entries = mock_enqueue.call_args[0][0]
        assert entries[0].url == "https://example.com/article3"

        # ジョブは削除されず queued に戻る (再起動してもリトライが残る)
        state = json.loads(tmp_state.read_text())
        job = next(j for j in state["jobs"] if j["slug"] == "eee555fff666")
        assert job["status"] == "queued"
        assert job["error"] is None
        # メタデータは保持される (Processing リストにタイトルが出る)
        assert job["metadata"]["title"] == "Article Three"

    def test_retry_job_with_video_resumes_upload(
        self, client: TestClient, tmp_state: Path, tmp_path: Path
    ) -> None:
        """動画変換済みの failed ジョブはアップロードのみ再試行する."""
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake video")
        thumb = tmp_path / "thumb.png"
        thumb.write_bytes(b"fake thumb")
        state = {
            "last_run": None,
            "jobs": [
                {
                    "url": "https://example.com/upload-failed",
                    "slug": "deadbeef0001",
                    "audio_length": "default",
                    "prompt": "default",
                    "status": "failed",
                    "notebook_id": None,
                    "task_id": "task-1",
                    "metadata": {"title": "Upload Failed"},
                    "audio_path": None,
                    "thumbnail_path": str(thumb),
                    "video_path": str(video),
                    "youtube_url": None,
                    "error": "quota exceeded",
                    "submitted_at": None,
                    "collected_at": None,
                    "uploaded_at": None,
                }
            ],
        }
        tmp_state.write_text(json.dumps(state, ensure_ascii=False))

        with patch("automator.web.routes.enqueue_urls") as mock_enqueue:
            resp = client.post("/api/retry/deadbeef0001")
        assert resp.status_code == 200
        # 空バッチ → run_pipeline の upload スイープで再試行される
        mock_enqueue.assert_called_once_with([])

        state = json.loads(tmp_state.read_text())
        job = state["jobs"][0]
        assert job["status"] == "video_ready"
        assert job["error"] is None

    def test_retry_job_with_youtube_url_does_full_regen(
        self, client: TestClient, tmp_state: Path, tmp_path: Path
    ) -> None:
        """アップロード済み (youtube_url あり) のジョブは video_ready に短絡しない.

        CLI --force 再実行の失敗等で古い video_path と youtube_url が残った
        failed ジョブをリトライした場合、旧動画を再アップロードすると
        重複するため、queued に戻して最初から再生成する。
        """
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake video")
        thumb = tmp_path / "thumb.png"
        thumb.write_bytes(b"fake thumb")
        state = {
            "last_run": None,
            "jobs": [
                {
                    "url": "https://example.com/force-refailed",
                    "slug": "deadbeef0002",
                    "audio_length": "default",
                    "prompt": "default",
                    "status": "failed",
                    "notebook_id": None,
                    "task_id": None,
                    "metadata": {"title": "Force Refailed"},
                    "audio_path": None,
                    "thumbnail_path": str(thumb),
                    "video_path": str(video),
                    "youtube_url": "https://youtu.be/already-up",
                    "error": "submit failed",
                    "submitted_at": None,
                    "collected_at": None,
                    "uploaded_at": None,
                }
            ],
        }
        tmp_state.write_text(json.dumps(state, ensure_ascii=False))

        with patch("automator.web.routes.enqueue_urls") as mock_enqueue:
            resp = client.post("/api/retry/deadbeef0002")
        assert resp.status_code == 200
        entries = mock_enqueue.call_args[0][0]
        assert len(entries) == 1

        state = json.loads(tmp_state.read_text())
        assert state["jobs"][0]["status"] == "queued"

    def test_retry_all_failed(
        self, client: TestClient, state_with_jobs: None, tmp_state: Path
    ) -> None:
        with patch("automator.web.routes.enqueue_urls") as mock_enqueue:
            resp = client.post("/api/retry-all-failed")
        assert resp.status_code == 200
        mock_enqueue.assert_called_once()
        entries = mock_enqueue.call_args[0][0]
        assert len(entries) == 1
        assert entries[0].url == "https://example.com/article3"

        state = json.loads(tmp_state.read_text())
        statuses = [j["status"] for j in state["jobs"]]
        assert "failed" not in statuses
        assert "queued" in statuses

    def test_add_skips_active_urls(
        self, client: TestClient, state_with_jobs: None, tmp_state: Path
    ) -> None:
        """処理中・アップロード済みの URL は再追加されない."""
        with patch("automator.web.routes.enqueue_urls") as mock_enqueue:
            resp = client.post(
                "/api/add",
                data={
                    "urls": "https://example.com/article1\n"  # generating
                    "https://example.com/article2",  # uploaded
                    "prompt": "default",
                    "audio_length": "default",
                },
            )
        assert resp.status_code == 200
        mock_enqueue.assert_not_called()

        state = json.loads(tmp_state.read_text())
        statuses = {j["url"]: j["status"] for j in state["jobs"]}
        assert statuses["https://example.com/article1"] == "generating"
        assert statuses["https://example.com/article2"] == "uploaded"

    def test_add_requeues_failed_url(
        self, client: TestClient, state_with_jobs: None, tmp_state: Path
    ) -> None:
        """failed の URL は再追加で queued に戻る."""
        with patch("automator.web.routes.enqueue_urls") as mock_enqueue:
            resp = client.post(
                "/api/add",
                data={
                    "urls": "https://example.com/article3",
                    "prompt": "default",
                    "audio_length": "default",
                },
            )
        assert resp.status_code == 200
        mock_enqueue.assert_called_once()

        state = json.loads(tmp_state.read_text())
        job = next(
            j
            for j in state["jobs"]
            if j["url"] == "https://example.com/article3"
        )
        assert job["status"] == "queued"
        assert job["error"] is None

    def test_add_dedups_urls_in_batch(self, client: TestClient) -> None:
        """同一バッチ内の重複 URL は 1 件に集約される."""
        with patch("automator.web.routes.enqueue_urls") as mock_enqueue:
            client.post(
                "/api/add",
                data={
                    "urls": "https://example.com/dup\nhttps://example.com/dup",
                    "prompt": "default",
                    "audio_length": "default",
                },
            )
        entries = mock_enqueue.call_args[0][0]
        assert len(entries) == 1


class TestHeaderBadge:
    """ヘッダーバッジのカウントは state.json を単一の情報源とする."""

    def test_queued_jobs_counted_from_state(
        self, client: TestClient, tmp_state: Path
    ) -> None:
        state = {
            "last_run": None,
            "jobs": [
                {"url": "u1", "slug": "s1", "status": "queued"},
                {"url": "u2", "slug": "s2", "status": "generating"},
            ],
        }
        tmp_state.write_text(json.dumps(state))

        resp = client.get("/partials/header-badge")
        assert resp.status_code == 200
        assert "1 processing" in resp.text
        assert "1 queued" in resp.text


class TestRecovery:
    """起動時リカバリはワーカーキュー経由でのみ復旧する."""

    @pytest.mark.asyncio()
    async def test_recover_queued_jobs_enqueues_batch(
        self, settings: Settings, tmp_state: Path
    ) -> None:
        from automator.web.app import _recover_orphaned_jobs

        state = {
            "last_run": None,
            "jobs": [
                {"url": "u1", "slug": "s1", "status": "queued",
                 "audio_length": "default", "prompt": "default",
                 "privacy_status": "public"},
                {"url": "u2", "slug": "s2", "status": "generating"},
            ],
        }
        tmp_state.write_text(json.dumps(state))

        with patch("automator.web.app.enqueue_urls") as mock_enqueue:
            await _recover_orphaned_jobs(settings)

        # queued バッチの run_pipeline が generating のスイープも行うため 1 回のみ
        mock_enqueue.assert_called_once()
        entries = mock_enqueue.call_args[0][0]
        assert [e.url for e in entries] == ["u1"]
        assert entries[0].privacy_status == "public"

    @pytest.mark.asyncio()
    async def test_recover_in_flight_jobs_enqueues_sweep(
        self, settings: Settings, tmp_state: Path
    ) -> None:
        from automator.web.app import _recover_orphaned_jobs

        state = {
            "last_run": None,
            "jobs": [
                {"url": "u1", "slug": "s1", "status": "video_ready"},
            ],
        }
        tmp_state.write_text(json.dumps(state))

        with patch("automator.web.app.enqueue_urls") as mock_enqueue:
            await _recover_orphaned_jobs(settings)

        mock_enqueue.assert_called_once_with([])

    def test_mark_queued_jobs_failed(self, tmp_state: Path) -> None:
        from automator.web.app import _mark_queued_jobs_failed

        state = {
            "last_run": None,
            "jobs": [
                {"url": "u1", "slug": "s1", "status": "queued", "error": None},
                {"url": "u2", "slug": "s2", "status": "generating",
                 "error": None},
            ],
        }
        tmp_state.write_text(json.dumps(state))

        _mark_queued_jobs_failed(tmp_state, ["u1", "u2"], "boom")

        state = json.loads(tmp_state.read_text())
        jobs = {j["url"]: j for j in state["jobs"]}
        # queued のみ failed になり、generating (復旧可能) は触らない
        assert jobs["u1"]["status"] == "failed"
        assert jobs["u1"]["error"] == "boom"
        assert jobs["u2"]["status"] == "generating"

    @pytest.mark.asyncio()
    async def test_worker_marks_queued_failed_on_batch_error(
        self, settings: Settings, tmp_state: Path
    ) -> None:
        """run_pipeline がバッチごと失敗したら queued ジョブを failed にする配線."""
        import asyncio

        from automator.url_parser import UrlEntry
        from automator.web import app as web_app

        state = {
            "last_run": None,
            "jobs": [
                {"url": "https://example.com/u1", "slug": "s1",
                 "status": "queued", "error": None},
            ],
        }
        tmp_state.write_text(json.dumps(state))

        with patch(
            "automator.web.app.run_pipeline",
            side_effect=RuntimeError("boom"),
        ):
            worker = asyncio.create_task(web_app.pipeline_worker(settings))
            await web_app.enqueue_urls(
                [UrlEntry(url="https://example.com/u1")]
            )
            await asyncio.wait_for(web_app._task_queue.join(), timeout=2)
            worker.cancel()

        state = json.loads(tmp_state.read_text())
        job = state["jobs"][0]
        assert job["status"] == "failed"
        assert "boom" in job["error"]
