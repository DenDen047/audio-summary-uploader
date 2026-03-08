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
        assert "Audio Summary" in resp.text
        assert "No jobs in progress" in resp.text
        assert "No completed jobs" in resp.text

    def test_dashboard_with_jobs(
        self, client: TestClient, state_with_jobs: None
    ) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "Article One" in resp.text
        assert "Article Two" in resp.text
        assert "Article Three" in resp.text
        assert "音声を生成中" in resp.text
        assert "NotebookLM timeout" in resp.text

    def test_presets_in_form(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        assert '<option value="default"' in resp.text
        assert '<option value="paper"' in resp.text


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
                },
            )
        assert resp.status_code == 200
        mock_enqueue.assert_called_once()
        entries = mock_enqueue.call_args[0][0]
        assert len(entries) == 1
        assert entries[0].url == "https://example.com/new"
        assert entries[0].audio_length == "short"
        assert entries[0].prompt == "default"

        # ジョブが即座に state.json に "queued" で書き込まれている
        state = json.loads(tmp_state.read_text())
        queued = [j for j in state["jobs"] if j["status"] == "queued"]
        assert len(queued) == 1
        assert queued[0]["url"] == "https://example.com/new"

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

        # state からは削除されている
        state = json.loads(tmp_state.read_text())
        slugs = [j["slug"] for j in state["jobs"]]
        assert "eee555fff666" not in slugs

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
