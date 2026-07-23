"""機能4: AIサムネ生成 (image_gen) のオフライン単体テスト.

実際の gemini-webapi 呼び出しはライブ検証で確認済み。ここでは cookie 解析・
プロンプト生成・整形・cookie 無し時の縮退をネットワークなしで担保する。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

from summary.image_gen import (
    DEFAULT_STYLE,
    ThumbnailStyle,
    _resize_cover,
    build_background_prompt,
    build_thumbnail_base_prompt,
    generate_thumbnail_image,
    load_google_cookies,
)


def _write_storage_state(path: Path, cookies: list[dict]) -> None:
    path.write_text(json.dumps({"cookies": cookies}), encoding="utf-8")


class TestLoadGoogleCookies:
    def test_reads_psid_and_psidts(self, tmp_path: Path) -> None:
        ss = tmp_path / "storage_state.json"
        _write_storage_state(ss, [
            {"domain": ".google.com", "name": "__Secure-1PSID", "value": "PSID"},
            {"domain": ".google.com", "name": "__Secure-1PSIDTS", "value": "PSIDTS"},
        ])
        assert load_google_cookies(ss) == ("PSID", "PSIDTS")

    def test_ignores_other_domains(self, tmp_path: Path) -> None:
        ss = tmp_path / "storage_state.json"
        _write_storage_state(ss, [
            {"domain": ".example.com", "name": "__Secure-1PSID", "value": "X"},
        ])
        assert load_google_cookies(ss) == (None, None)

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert load_google_cookies(tmp_path / "nope.json") == (None, None)


class TestBuildBasePrompt:
    def test_contains_topic_and_style(self) -> None:
        prompt = build_thumbnail_base_prompt("拡散モデルの謎", DEFAULT_STYLE)
        assert "拡散モデルの謎" in prompt
        assert DEFAULT_STYLE.palette in prompt
        assert "16:9" in prompt

    def test_forbids_text_and_keeps_mascot(self) -> None:
        prompt = build_thumbnail_base_prompt("速報", DEFAULT_STYLE)
        assert "NO text" in prompt
        # 固定マスコットを参照して同一キャラ＋驚き顔を維持する指示が入る
        assert "mascot" in prompt.lower()
        assert "SURPRISED" in prompt

    def test_uses_custom_style(self) -> None:
        style = ThumbnailStyle(name="news", palette="warm red", motif="newspaper")
        prompt = build_thumbnail_base_prompt("速報", style)
        assert "warm red" in prompt

    def test_pose_hint_varies_pose(self) -> None:
        prompt = build_thumbnail_base_prompt(
            "速報", DEFAULT_STYLE, pose="throwing both arms up"
        )
        assert "throwing both arms up" in prompt
        # 参照画像のポーズを踏襲しない指示（縮小時に絵柄が同じにならないため）
        assert "do NOT copy the reference pose" in prompt


class TestBuildBackgroundPrompt:
    def test_topic_and_variation_included(self) -> None:
        prompt = build_background_prompt(
            DEFAULT_STYLE,
            topic="米政府のオープンソースAI移行",
            variation="wide cinematic establishing shot",
        )
        assert "米政府のオープンソースAI移行" in prompt
        assert "wide cinematic establishing shot" in prompt
        assert "NO text" in prompt

    def test_works_without_topic(self) -> None:
        prompt = build_background_prompt(DEFAULT_STYLE)
        assert "NO text" in prompt
        assert DEFAULT_STYLE.palette in prompt


class TestResizeCover:
    def test_produces_exact_dimensions(self) -> None:
        src = Image.new("RGB", (2752, 1536), (10, 20, 30))
        out = _resize_cover(src, 1280, 720)
        assert out.size == (1280, 720)

    def test_handles_rgba_input(self) -> None:
        src = Image.new("RGBA", (2000, 2000), (1, 2, 3, 255))
        out = _resize_cover(src, 1280, 720)
        assert out.size == (1280, 720)
        assert out.mode == "RGB"


@pytest.mark.asyncio()
async def test_generate_returns_none_without_cookies(tmp_path: Path) -> None:
    """cookie が無ければ gemini を一切呼ばず None を返す."""
    with patch(
        "summary.image_gen.load_google_cookies", return_value=(None, None)
    ):
        result = await generate_thumbnail_image(
            "見出し", tmp_path / "t.png", width=1280, height=720
        )
    assert result is None
