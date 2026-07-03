"""機能3: カテゴリ判定・サムネスタイル・プレイリスト解決のテスト."""
from __future__ import annotations

import pytest

from automator.category import (
    AMBIGUOUS_CATEGORIES,
    BUSINESS,
    DEFAULT,
    ENGINEERING,
    NEWS,
    PAPER,
    classify_category,
    parse_category,
    resolve_playlist_id,
    resolve_playlist_ids,
    style_for_category,
)
from automator.image_gen import DEFAULT_STYLE


class TestClassify:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("https://arxiv.org/abs/2511.15059", PAPER),
            ("/Users/me/papers/foo.pdf", PAPER),
            ("https://example.com/report.PDF", PAPER),
            ("https://app.sparkmailapp.com/web-share/abc", NEWS),
            ("https://blog.substack.com/p/x", NEWS),
            ("https://github.com/foo/bar", ENGINEERING),
            ("https://zenn.dev/foo/articles/bar", ENGINEERING),
            ("https://www.youtube.com/watch?v=abc", BUSINESS),
            ("https://youtu.be/abc", BUSINESS),
            ("https://some-random-blog.example/post", DEFAULT),
        ],
    )
    def test_classify(self, url: str, expected: str) -> None:
        assert classify_category(url) == expected


class TestParseCategory:
    def test_exact_key(self) -> None:
        assert parse_category("engineering") == ENGINEERING

    def test_with_noise_and_marker(self) -> None:
        assert parse_category("Category: paper [1]") == PAPER

    def test_unparseable_returns_none(self) -> None:
        assert parse_category("よくわかりません") is None

    def test_business_and_default(self) -> None:
        assert parse_category("business") == BUSINESS
        assert parse_category("default") == DEFAULT

    def test_only_business_and_default_are_ambiguous(self) -> None:
        assert AMBIGUOUS_CATEGORIES == {BUSINESS, DEFAULT}


class TestStyle:
    def test_each_category_has_distinct_style(self) -> None:
        styles = {
            style_for_category(c).name
            for c in (PAPER, NEWS, ENGINEERING, BUSINESS)
        }
        assert styles == {PAPER, NEWS, ENGINEERING, BUSINESS}

    def test_unknown_falls_back_to_default(self) -> None:
        assert style_for_category("nonexistent") is DEFAULT_STYLE
        assert style_for_category(None) is DEFAULT_STYLE


class TestResolvePlaylist:
    def test_category_specific_wins(self) -> None:
        playlists = {"paper": "PL_paper", "news": "PL_news"}
        assert resolve_playlist_id("paper", playlists, "PL_default") == "PL_paper"

    def test_falls_back_when_category_absent(self) -> None:
        assert resolve_playlist_id("news", {}, "PL_default") == "PL_default"

    def test_falls_back_when_category_none(self) -> None:
        assert resolve_playlist_id(None, {"paper": "x"}, "PL_default") == "PL_default"

    def test_returns_none_when_no_default(self) -> None:
        assert resolve_playlist_id("paper", {}, None) is None


class TestResolvePlaylistIds:
    def test_category_plus_all(self) -> None:
        playlists = {"paper": "PL_paper"}
        assert resolve_playlist_ids("paper", playlists, "PL_default", "PL_all") == [
            "PL_paper",
            "PL_all",
        ]

    def test_all_only_when_no_category_nor_default(self) -> None:
        assert resolve_playlist_ids("news", {}, None, "PL_all") == ["PL_all"]

    def test_category_only_when_no_all(self) -> None:
        assert resolve_playlist_ids("paper", {"paper": "PL_paper"}, None, None) == [
            "PL_paper"
        ]

    def test_dedupes_same_id(self) -> None:
        assert resolve_playlist_ids(None, {}, "PL_all", "PL_all") == ["PL_all"]

    def test_empty_when_nothing_configured(self) -> None:
        assert resolve_playlist_ids(None, {}, None, None) == []
