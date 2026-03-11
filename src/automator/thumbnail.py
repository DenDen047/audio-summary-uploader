"""サムネイル生成 (Pillow)."""

import asyncio
import colorsys
import random
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


def _generate_random_gradient_colors() -> tuple[str, str]:
    """ランダムなグラデーション色のペアを生成する.

    HSL色空間を使い、彩度・明度を制御して視認性の良い色を生成する。
    テキストの可読性を保つため、中程度の明度に抑える。
    """
    # ランダムな色相を選択
    hue = random.random()
    # 2色目は色相を少しずらして調和のとれたグラデーションにする
    hue_shift = random.uniform(0.05, 0.15)
    hue2 = (hue + hue_shift) % 1.0

    # 彩度は高め、明度は中程度（テキスト可読性のため暗すぎず明るすぎず）
    saturation = random.uniform(0.5, 0.8)
    lightness_start = random.uniform(0.25, 0.40)
    lightness_end = random.uniform(0.15, 0.30)

    r1, g1, b1 = colorsys.hls_to_rgb(hue, lightness_start, saturation)
    r2, g2, b2 = colorsys.hls_to_rgb(hue2, lightness_end, saturation)

    start = f"#{int(r1*255):02x}{int(g1*255):02x}{int(b1*255):02x}"
    end = f"#{int(r2*255):02x}{int(g2*255):02x}{int(b2*255):02x}"
    return start, end


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


_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def _fetch_image(url_or_path: str) -> Image.Image | None:
    """URL またはローカルパスから画像を読み込む."""
    path = Path(url_or_path)
    if path.exists():
        try:
            return Image.open(path).convert("RGBA")
        except Exception:
            logger.warning("Failed to open local image: {}", url_or_path)
            return None

    try:
        resp = httpx.get(
            url_or_path, timeout=10.0, follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
        return Image.open(BytesIO(resp.content)).convert("RGBA")
    except Exception:
        logger.warning("Failed to fetch image: {}", url_or_path)
        return None


def _place_icon_on_gradient(
    gradient: Image.Image, icon: Image.Image, icon_size: int = 280,
) -> Image.Image:
    """グラデーション背景の中央にアイコンを配置する (RGBA を返す)."""
    # アスペクト比維持でリサイズ
    ratio = min(icon_size / icon.width, icon_size / icon.height)
    new_w = int(icon.width * ratio)
    new_h = int(icon.height * ratio)
    icon = icon.resize((new_w, new_h), Image.LANCZOS)

    # 中央に配置（テキスト領域を考慮して少し上寄り）
    bg = gradient.convert("RGBA")
    x = (bg.width - new_w) // 2
    y = (bg.height - new_h) // 2 - 40
    bg.paste(icon, (x, y), icon)
    return bg


def generate_thumbnail_sync(
    title: str,
    site_name: str | None,
    og_image_url: str | None,
    output_path: Path,
    config: ThumbnailConfig,
    *,
    favicon_url: str | None = None,
) -> Path:
    """サムネイル画像を生成する（同期版）."""
    width, height = config.width, config.height
    text_color = config.text_color

    # 背景画像の取得
    bg: Image.Image | None = None
    if og_image_url:
        og_img = _fetch_image(og_image_url)
        if og_img:
            bg = og_img.convert("RGB")
            # アスペクト比維持でリサイズ＆クロップ
            bg_ratio = max(width / bg.width, height / bg.height)
            new_size = (int(bg.width * bg_ratio), int(bg.height * bg_ratio))
            bg = bg.resize(new_size, Image.LANCZOS)
            left = (bg.width - width) // 2
            top = (bg.height - height) // 2
            bg = bg.crop((left, top, left + width, top + height))

    if bg is None:
        start_color, end_color = _generate_random_gradient_colors()
        logger.info("Using random gradient: {} -> {}", start_color, end_color)
        gradient = _create_gradient_background(width, height, start_color, end_color)

        # ファビコン/アイコンをグラデーション上に配置
        if favicon_url:
            icon = _fetch_image(favicon_url)
            if icon:
                bg = _place_icon_on_gradient(gradient, icon)
                logger.info("Placed icon on gradient background")

        if bg is None:
            bg = gradient

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
    *,
    favicon_url: str | None = None,
) -> Path:
    """サムネイル画像を生成する（async ラッパー）."""
    return await asyncio.to_thread(
        generate_thumbnail_sync,
        title, site_name, og_image_url, output_path, config,
        favicon_url=favicon_url,
    )
