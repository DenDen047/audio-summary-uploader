"""機能3: カテゴリ判定・サムネスタイル・プレイリスト解決のテスト."""
from __future__ import annotations

import pytest

from automator.category import (
    BUSINESS,
    DEFAULT,
    ENGINEERING,
    NEWS,
    PAPER,
    classify_category,
    resolve_playlist_id,
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
