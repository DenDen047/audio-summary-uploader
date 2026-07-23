"""機能2: 日本語タイトル生成(NotebookLM chat)の整形・フォールバックのテスト."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from summary.pipeline import (
    _clean_generated_title,
    _generate_japanese_title,
    _refine_category,
)


class TestCleanGeneratedTitle:
    def test_strips_citation_markers(self) -> None:
        got = _clean_generated_title("拡散モデルの高速化 [1]", 35)
        assert got == "拡散モデルの高速化"

    def test_strips_wrapping_corner_brackets(self) -> None:
        assert _clean_generated_title("「AIの未来」", 35) == "AIの未来"

    def test_strips_wrapping_double_quotes(self) -> None:
        got = _clean_generated_title('"今週のAIニュース"', 35)
        assert got == "今週のAIニュース"

    def test_keeps_internal_brackets(self) -> None:
        # 全体を囲っていない括弧は残す（【論文解説】等の体裁を壊さない）
        got = _clean_generated_title("蒸留の謎【論文解説】", 35)
        assert got == "蒸留の謎【論文解説】"

    def test_strips_leading_emoji(self) -> None:
        got = _clean_generated_title("🎧 AIニュースまとめ", 35)
        assert got == "AIニュースまとめ"

    def test_takes_first_nonempty_line(self) -> None:
        got = _clean_generated_title("\n\n本命タイトル\n補足説明", 35)
        assert got == "本命タイトル"

    def test_length_cap_adds_ellipsis(self) -> None:
        long = "あ" * 50
        result = _clean_generated_title(long, 35)
        assert result is not None
        assert len(result) == 35
        assert result.endswith("…")

    def test_empty_returns_none(self) -> None:
        assert _clean_generated_title("", 35) is None
        assert _clean_generated_title("   \n  ", 35) is None


@pytest.mark.asyncio()
async def test_generate_title_success() -> None:
    backend = AsyncMock()
    backend.ask = AsyncMock(return_value="拡散モデルはなぜ爆速化したのか")
    title = await _generate_japanese_title(backend, "nb-1", 35)
    assert title == "拡散モデルはなぜ爆速化したのか"
    backend.ask.assert_awaited_once()


@pytest.mark.asyncio()
async def test_generate_title_chat_failure_returns_none() -> None:
    backend = AsyncMock()
    backend.ask = AsyncMock(side_effect=RuntimeError("chat down"))
    assert await _generate_japanese_title(backend, "nb-1", 35) is None


@pytest.mark.asyncio()
async def test_generate_title_non_str_answer_returns_none() -> None:
    # backend.ask 未設定の AsyncMock は str 以外を返す → None で安全に縮退
    backend = AsyncMock()
    assert await _generate_japanese_title(backend, "nb-1", 35) is None


@pytest.mark.asyncio()
async def test_refine_category_skips_confident() -> None:
    # 確定カテゴリ(paper)は chat を呼ばずそのまま返す
    backend = AsyncMock()
    assert await _refine_category(backend, "nb-1", "paper") == "paper"
    backend.ask.assert_not_called()


@pytest.mark.asyncio()
async def test_refine_category_uses_chat_for_ambiguous() -> None:
    backend = AsyncMock()
    backend.ask = AsyncMock(return_value="engineering")
    assert await _refine_category(backend, "nb-1", "business") == "engineering"


@pytest.mark.asyncio()
async def test_refine_category_falls_back_on_failure() -> None:
    backend = AsyncMock()
    backend.ask = AsyncMock(side_effect=RuntimeError("down"))
    assert await _refine_category(backend, "nb-1", "default") == "default"


@pytest.mark.asyncio()
async def test_refine_category_falls_back_on_unparseable() -> None:
    backend = AsyncMock()
    backend.ask = AsyncMock(return_value="???")
    assert await _refine_category(backend, "nb-1", "business") == "business"
