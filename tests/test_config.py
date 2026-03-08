"""config モジュールのテスト."""

from pathlib import Path

import pytest

from automator.config import NotebookLMConfig, load_settings


def test_load_settings_from_default() -> None:
    settings = load_settings(Path("config/settings.yaml"))
    assert settings.notebooklm.backend == "notebooklm-py"
    assert settings.notebooklm.audio_language == "ja"
    assert settings.youtube.category_id == "27"
    assert settings.thumbnail.width == 1280
    assert settings.general.max_retries == 3


def test_invalid_backend() -> None:
    with pytest.raises(ValueError, match="Invalid backend"):
        NotebookLMConfig(backend="invalid")


def test_invalid_audio_length() -> None:
    with pytest.raises(ValueError, match="Invalid audio_length"):
        NotebookLMConfig(audio_length="invalid")


def test_settings_file_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        load_settings(Path("/nonexistent/settings.yaml"))
