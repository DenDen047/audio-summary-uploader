"""サムネイル生成 (Pillow)."""

import asyncio
from io import BytesIO
from pathlib import Path

import httpx
from loguru import logger
from PIL import Image, ImageDraw, ImageFont

from automator.config import ThumbnailConfig

_FONT_DIR = Path(__file__).resolve().parent.parent.parent / "fonts"


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return (int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16))


def _resolve_font_path(font_name: str) -> str | None:
    """フォントファイルのパスを解決する（見つからなければ None）."""
    font_path = _FONT_DIR / f"{font_name}.ttf"
    if font_path.exists():
        return str(font_path)
    return None


def _load_font(
    font_name: str, size: int, *, resolved_path: str | None = None,
) -> ImageFont.FreeTypeFont:
    path = resolved_path or _resolve_font_path(font_name)
    if path:
        return ImageFont.truetype(path, size)
    # システムフォントにフォールバック
    try:
        return ImageFont.truetype(font_name, size)
    except OSError:
        logger.warning("Font {!r} not found, using default", font_name)
        return ImageFont.load_default()


def _create_gradient_background(
    width: int, height: int, start_color: str, end_color: str
) -> Image.Image:
    """グラデーション背景を生成."""
    img = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(img)
    start = _hex_to_rgb(start_color)
    end = _hex_to_rgb(end_color)
    for y in range(height):
        ratio = y / height
        r = int(start[0] + (end[0] - start[0]) * ratio)
        g = int(start[1] + (end[1] - start[1]) * ratio)
        b = int(start[2] + (end[2] - start[2]) * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))
    return img


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """テキストを指定幅で折り返す."""
    lines: list[str] = []
    current_line = ""
    for char in text:
        test_line = current_line + char
        bbox = font.getbbox(test_line)
        if bbox[2] - bbox[0] > max_width:
            if current_line:
                lines.append(current_line)
            current_line = char
        else:
            current_line = test_line
    if current_line:
        lines.append(current_line)
    return lines


def _determine_font_size(
    text: str, font_name: str, max_width: int, size_max: int, size_min: int
) -> int:
    """タイトル長に応じてフォントサイズを自動調整."""
    resolved_path = _resolve_font_path(font_name)
    for size in range(size_max, size_min - 1, -2):
        font = _load_font(font_name, size, resolved_path=resolved_path)
        lines = _wrap_text(text, font, max_width)
        if len(lines) <= 4:
            return size
    return size_min


def _draw_text_with_shadow(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    x: int,
    y: int,
    fill: str,
    shadow_offset: int = 2,
    shadow_color: str = "#000000",
) -> None:
    """影付きテキストを描画する."""
    draw.text(
        (x + shadow_offset, y + shadow_offset),
        text, fill=shadow_color, font=font,
    )
    draw.text((x, y), text, fill=fill, font=font)


def generate_thumbnail_sync(
    title: str,
    site_name: str | None,
    og_image_url: str | None,
    output_path: Path,
    config: ThumbnailConfig,
) -> Path:
    """サムネイル画像を生成する（同期版）."""
    width, height = config.width, config.height
    text_color = config.text_color

    # 背景画像の取得
    bg: Image.Image | None = None
    if og_image_url:
        try:
            resp = httpx.get(og_image_url, timeout=10.0, follow_redirects=True)
            resp.raise_for_status()
            bg = Image.open(BytesIO(resp.content)).convert("RGB")
            # アスペクト比維持でリサイズ＆クロップ
            bg_ratio = max(width / bg.width, height / bg.height)
            new_size = (int(bg.width * bg_ratio), int(bg.height * bg_ratio))
            bg = bg.resize(new_size, Image.LANCZOS)
            left = (bg.width - width) // 2
            top = (bg.height - height) // 2
            bg = bg.crop((left, top, left + width, top + height))
        except Exception:
            logger.warning("Failed to fetch OG image, using gradient fallback")
            bg = None

    if bg is None:
        bg = _create_gradient_background(
            width, height,
            config.fallback_gradient["start"],
            config.fallback_gradient["end"],
        )

    # 暗めオーバーレイ
    alpha = int(255 * config.overlay_opacity)
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, alpha))
    bg = bg.convert("RGBA")
    bg = Image.alpha_composite(bg, overlay)
    bg = bg.convert("RGB")

    draw = ImageDraw.Draw(bg)

    # タイトルテキスト
    text_margin = 80
    max_text_width = width - text_margin * 2
    font_size = _determine_font_size(
        title, config.font_name, max_text_width,
        config.title_font_size_max, config.title_font_size_min,
    )
    title_font = _load_font(config.font_name, font_size)
    lines = _wrap_text(title, title_font, max_text_width)

    # テキスト全体の高さを計算し中央配置
    line_height = font_size + 8
    total_text_height = len(lines) * line_height
    y_start = (height - total_text_height) // 2 - 20

    for i, line in enumerate(lines):
        bbox = title_font.getbbox(line)
        line_width = bbox[2] - bbox[0]
        x = (width - line_width) // 2
        y = y_start + i * line_height
        _draw_text_with_shadow(
            draw, line, title_font, x, y, text_color, shadow_offset=2,
        )

    # サイト名（下部）
    if site_name:
        sub_font = _load_font(config.font_name, config.subtitle_font_size)
        bbox = sub_font.getbbox(site_name)
        sw = bbox[2] - bbox[0]
        sx = (width - sw) // 2
        sy = height - 60
        _draw_text_with_shadow(
            draw, site_name, sub_font, sx, sy, "#CCCCCC",
            shadow_offset=1, shadow_color="#999999",
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    bg.save(str(output_path), "PNG")
    logger.info("Thumbnail saved: {}", output_path)
    return output_path


async def generate_thumbnail(
    title: str,
    site_name: str | None,
    og_image_url: str | None,
    output_path: Path,
    config: ThumbnailConfig,
) -> Path:
    """サムネイル画像を生成する（async ラッパー）."""
    return await asyncio.to_thread(
        generate_thumbnail_sync,
        title, site_name, og_image_url, output_path, config,
    )
