"""url_parser モジュールのテスト."""

from pathlib import Path
from textwrap import dedent

from automator.url_parser import UrlEntry, parse_url_file


def _write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "urls.yaml"
    p.write_text(dedent(content), encoding="utf-8")
    return p


def test_parse_basic(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, """\
        - url: https://example.com/article1
        - url: https://example.com/article2
    """)
    entries = parse_url_file(path)
    assert len(entries) == 2
    assert entries[0].url == "https://example.com/article1"
    assert entries[1].url == "https://example.com/article2"


def test_parse_with_options(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, """\
        - url: https://example.com/article
          audio_length: short
          prompt: deep_dive
    """)
    presets = {"default", "deep_dive"}
    entries = parse_url_file(path, valid_prompt_presets=presets)
    assert len(entries) == 1
    assert entries[0] == UrlEntry(
        url="https://example.com/article",
        audio_length="short",
        prompt="deep_dive",
    )


def test_skip_invalid_url(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, """\
        - url: not-a-url
        - url: https://valid.com/page
    """)
    entries = parse_url_file(path)
    assert len(entries) == 1
    assert entries[0].url == "https://valid.com/page"


def test_skip_duplicate(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, """\
        - url: https://example.com/same
        - url: https://example.com/same
    """)
    entries = parse_url_file(path)
    assert len(entries) == 1


def test_skip_invalid_audio_length(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, """\
        - url: https://example.com/page
          audio_length: invalid
    """)
    entries = parse_url_file(path)
    assert len(entries) == 0


def test_skip_unknown_prompt(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, """\
        - url: https://example.com/page
          prompt: nonexistent
    """)
    presets = {"default", "deep_dive"}
    entries = parse_url_file(path, valid_prompt_presets=presets)
    assert len(entries) == 0


def test_file_not_found() -> None:
    import pytest

    with pytest.raises(FileNotFoundError):
        parse_url_file(Path("/nonexistent/urls.yaml"))
