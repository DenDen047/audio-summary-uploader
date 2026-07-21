"""従量課金なしのサムネイル背景生成契約テスト。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from PIL import Image

from lecture.thumbnail_backdrop import (
    ThumbnailBackdropOptions,
    _select_motif,
    build_thumbnail_backdrop_prompt,
    generate_thumbnail_backdrop,
)


def _script() -> dict:
    return {
        "title": "Python開発ツールuv",
        "thumbnail_text": ["pipより速い？", "uvなら全部できる"],
        "thumbnail_visual_prompt": (
            "motif=packages; 依存関係が一本の光る経路へ整理される"
        ),
    }


def _fake_rasterize(_source: Path, output: Path) -> None:
    Image.new("RGB", (1600, 900), "#23182f").save(output)


def test_codex_directed_svg_is_saved_without_external_api(tmp_path: Path) -> None:
    fallback = tmp_path / "fallback.png"
    Image.new("RGB", (1280, 720), "white").save(fallback)

    with patch(
        "lecture.thumbnail_backdrop._rasterize_svg", side_effect=_fake_rasterize
    ) as rasterize:
        result = generate_thumbnail_backdrop(
            _script(),
            tmp_path / "job" / "thumbnail-background.png",
            fallback,
            ThumbnailBackdropOptions(),
        )

    assert result.provider == "codex-directed-local-svg"
    assert result.model is None
    assert result.fallback_reason is None
    assert result.source_path is not None and result.source_path.is_file()
    assert "metered image APIs are prohibited" in result.prompt
    assert "<svg" in result.source_path.read_text(encoding="utf-8")
    assert result.as_metadata()["metered_api"] is False
    rasterize.assert_called_once()


def test_same_script_produces_identical_svg(tmp_path: Path) -> None:
    fallback = tmp_path / "fallback.png"
    Image.new("RGB", (1280, 720), "white").save(fallback)
    with patch(
        "lecture.thumbnail_backdrop._rasterize_svg", side_effect=_fake_rasterize
    ):
        first = generate_thumbnail_backdrop(
            _script(), tmp_path / "a" / "background.png", fallback,
            ThumbnailBackdropOptions(),
        )
        second = generate_thumbnail_backdrop(
            _script(), tmp_path / "b" / "background.png", fallback,
            ThumbnailBackdropOptions(),
        )

    assert first.source_path is not None
    assert second.source_path is not None
    assert first.source_path.read_bytes() == second.source_path.read_bytes()


def test_static_mode_copies_fixed_background(tmp_path: Path) -> None:
    fallback = tmp_path / "fallback.png"
    Image.new("RGB", (1280, 720), "#FFE2EA").save(fallback)

    result = generate_thumbnail_backdrop(
        _script(),
        tmp_path / "job" / "thumbnail-background.png",
        fallback,
        ThumbnailBackdropOptions(mode="static"),
    )

    assert result.provider == "static"
    assert result.path.read_bytes() == fallback.read_bytes()
    assert result.source_path is None


def test_motif_prefers_explicit_contract_and_can_infer_legacy_prompt() -> None:
    assert _select_motif("motif=security; 認証の仕組み", "") == "security"
    assert _select_motif("依存関係を箱で表現", "Python uv") == "packages"


def test_prompt_records_layout_and_cost_policy() -> None:
    prompt = build_thumbnail_backdrop_prompt(_script())

    assert "Codex art direction" in prompt
    assert "Selected motif: packages" in prompt
    assert "calm high-contrast center" in prompt
    assert "no people, text, logos" in prompt
