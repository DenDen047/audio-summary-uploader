"""サムネのタイポグラフィ合成 (compose_thumbnail) のオフライン単体テスト."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from summary.config import ThumbnailConfig
from summary.thumbnail import ThumbCopy, _wrap_text_phrases, compose_thumbnail


def _make_base(path: Path, size: tuple[int, int] = (1280, 720)) -> Path:
    Image.new("RGB", size, (30, 40, 60)).save(path)
    return path


class TestComposeThumbnail:
    def test_produces_png_with_config_dimensions(self, tmp_path: Path) -> None:
        base = _make_base(tmp_path / "base.png")
        out = tmp_path / "thumb.png"
        result = compose_thumbnail(
            base,
            ThumbCopy(
                top="AIニュース", mid="継続的学習で自己進化",
                bottom="神ツール5選", highlight="5選",
            ),
            out,
            ThumbnailConfig(),
        )
        assert result == out
        with Image.open(out) as img:
            assert img.size == (1280, 720)

    def test_resizes_mismatched_base(self, tmp_path: Path) -> None:
        base = _make_base(tmp_path / "base.png", size=(640, 360))
        out = tmp_path / "thumb.png"
        compose_thumbnail(
            base, ThumbCopy(top="論文解説", bottom="短い結論"), out, ThumbnailConfig()
        )
        with Image.open(out) as img:
            assert img.size == (1280, 720)

    def test_text_is_drawn(self, tmp_path: Path) -> None:
        """合成後はベタ塗りベースに比べ色数が大きく増える（文字・帯の描画確認）."""
        base = _make_base(tmp_path / "base.png")
        out = tmp_path / "thumb.png"
        compose_thumbnail(
            base, ThumbCopy(top="速報", mid="業界が一変", bottom="何が変わる"),
            out, ThumbnailConfig(),
        )
        with Image.open(out) as img:
            colors = img.getcolors(maxcolors=100000)
        assert colors is None or len(colors) > 50


class TestWrapTextPhrases:
    def test_breaks_at_phrase_boundaries(self, tmp_path: Path) -> None:
        from summary.thumbnail import _load_font

        font = _load_font("NotoSansJP-Bold", 60)
        text = "米政府のオープンソースAI移行とPalantirの戦略"
        lines = _wrap_text_phrases(text, font, 700)
        assert "".join(lines) == text
        # 語中改行しない: カタカナ語・英単語が行をまたがない
        assert any("オープンソース" in line for line in lines)
        assert any("Palantir" in line for line in lines)

    def test_oversized_phrase_falls_back_to_char_split(self) -> None:
        from summary.thumbnail import _load_font

        font = _load_font("NotoSansJP-Bold", 60)
        text = "スーパーウルトラハイパーミラクルロマンティック"
        lines = _wrap_text_phrases(text, font, 300)
        assert "".join(lines) == text
        assert len(lines) > 1
