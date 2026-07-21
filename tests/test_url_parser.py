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


def test_parse_lecture_mode(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, """\
        - url: https://example.com/article
          mode: lecture
    """)
    entries = parse_url_file(path)
    assert entries == [
        UrlEntry(url="https://example.com/article", mode="lecture")
    ]


def test_skip_unknown_mode(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, """\
        - url: https://example.com/article
          mode: podcast
    """)
    assert parse_url_file(path) == []


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


def test_parse_multi_source(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, """\
        - title: 今週のAIニュースまとめ
          mode: lecture
          urls:
            - https://example.com/a
            - https://example.com/b
          prompt: deep_dive
    """)
    entries = parse_url_file(path, valid_prompt_presets={"default", "deep_dive"})
    assert len(entries) == 1
    e = entries[0]
    assert e.url == "https://example.com/a"
    assert e.extra_urls == ["https://example.com/b"]
    assert e.sources == ["https://example.com/a", "https://example.com/b"]
    assert e.title == "今週のAIニュースまとめ"
    assert e.mode == "lecture"
    assert e.prompt == "deep_dive"


def test_multi_source_filters_invalid_urls(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, """\
        - urls:
            - not-a-url
            - https://example.com/ok
    """)
    entries = parse_url_file(path)
    assert len(entries) == 1
    assert entries[0].url == "https://example.com/ok"
    assert entries[0].extra_urls == []


def test_multi_source_all_invalid_skipped(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, """\
        - urls:
            - not-a-url
            - also-bad
    """)
    assert parse_url_file(path) == []


def test_single_url_has_empty_extra_urls(tmp_path: Path) -> None:
    path = _write_yaml(tmp_path, """\
        - url: https://example.com/article
    """)
    entries = parse_url_file(path)
    assert entries[0].extra_urls == []
    assert entries[0].sources == ["https://example.com/article"]
