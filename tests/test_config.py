"""config モジュールのテスト."""

from pathlib import Path

import pytest

from podcast.config import PodcastConfig, load_settings


def test_load_settings_from_default() -> None:
    settings = load_settings(Path("config/settings.yaml"))
    assert settings.podcast.backend == "notebooklm-py"
    assert settings.podcast.audio_language == "ja"
    assert settings.youtube.category_id == "27"
    assert settings.thumbnail.width == 1280
    assert settings.thumbnail.background_mode == "codex-svg"
    assert settings.lecture.script_model == "opus"
    assert settings.lecture.script_effort == "xhigh"
    assert settings.lecture.review_model == "gpt-5.6-sol"
    assert settings.lecture.review_effort == "xhigh"
    assert settings.lecture.generation_timeout_seconds == 3600
    assert settings.general.max_retries == 3


def test_invalid_backend() -> None:
    with pytest.raises(ValueError, match="Invalid backend"):
        PodcastConfig(backend="invalid")


def test_invalid_audio_length() -> None:
    with pytest.raises(ValueError, match="Invalid audio_length"):
        PodcastConfig(audio_length="invalid")


def test_settings_file_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        load_settings(Path("/nonexistent/settings.yaml"))
