"""サムネイル生成 (Pillow)."""

import asyncio
import colorsys
import random
import re
import unicodedata
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import budoux
import httpx
from loguru import logger
from PIL import Image, ImageDraw, ImageFont

from podcast.config import ThumbnailConfig

_FONT_DIR = Path(__file__).resolve().parent.parent.parent / "fonts"

# 日本語の自然な改行位置（文節境界）判定用。Chrome の日本語改行と同じ budoux を使う
_BUDOUX_PARSER = budoux.load_default_japanese_parser()


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


def _join_units(units: list[str], max_len: int, separator: str) -> str:
    """max_len を超えない範囲で units を先頭から連結する."""
    joined = ""
    for unit in units:
        candidate = f"{joined}{separator}{unit}" if joined else unit
        if len(candidate) > max_len:
            break
        joined = candidate
    return joined


def truncate_at_phrase(text: str, max_len: int) -> str:
    """max_len 文字以内へ、語中で切らずに丸める.

    素朴な `text[:max_len]` は「AIが勝手に3組織をハ」「Opus 5 on V」のように
    語中で切れて意味が壊れる。日本語は budoux の文節境界、英語は単語境界で切る。
    どちらでも1語も入らない場合だけ、最後の手段として文字数で切る。
    """
    if len(text) <= max_len:
        return text
    fitted = _join_units(_BUDOUX_PARSER.parse(text), max_len, "")
    if not fitted and " " in text:
        fitted = _join_units(text.split(), max_len, " ")
    return fitted or text[:max_len]


def _wrap_text_phrases(
    text: str, font: ImageFont.FreeTypeFont, max_width: int
) -> list[str]:
    """budoux の文節境界で自然に折り返す.

    日本語として不自然な語中改行（「オー/プンソース」等）を避ける。
    文節単体が幅を超える場合のみ、その文節を文字単位で分割する。
    """
    lines: list[str] = []
    current = ""
    for phrase in _BUDOUX_PARSER.parse(text):
        test = current + phrase
        if font.getbbox(test)[2] - font.getbbox(test)[0] <= max_width:
            current = test
            continue
        if current:
            lines.append(current)
            current = ""
        if font.getbbox(phrase)[2] - font.getbbox(phrase)[0] <= max_width:
            current = phrase
        else:
            chunks = _wrap_text(phrase, font, max_width)
            lines.extend(chunks[:-1])
            current = chunks[-1]
    if current:
        lines.append(current)
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


# 数字＋助数詞は1かたまりで扱い、「10選」を「1」「0選」に割らない
_NUM_UNIT = re.compile(
    r"[0-9０-９]+(?:選|つ|個|位|名|倍|%|％|件|人|年|日|秒|分|冠|化)?"
)

_YELLOW = "#FFE24A"  # 下段ベネフィットの基調色
# 文字詰め（トラッキング）: 全角(CJK/かな)の送り幅をわずかに詰める。
# 強く詰めると字が重なって読みづらいので控えめに（詰めるのは主に行間側）。
# Pillow の draw.text は詰めに非対応なので1文字ずつ手動配置する（PIL Issue #3977）。
_TRACK = 0.02


@dataclass(frozen=True)
class ThumbCopy:
    """サムネの3層テキスト（上=製品名/導入・中=説明・下=ベネフィット）.

    highlight は下段内で別色強調する1語（省略時は下段全体を黄色1色）。
    伸びているAI解説チャンネル（mikimiki / 本気AI 等）の「型」に合わせた構成。
    """

    top: str = ""
    mid: str = ""
    bottom: str = ""
    highlight: str = ""


def _num_safe_atoms(text: str) -> list[str]:
    """budoux 文節に分けつつ、数字＋助数詞グループはまたがせない."""
    units: list[str] = []

    def _stash(m: re.Match) -> str:
        units.append(m.group(0))
        return f"\x00{len(units) - 1}\x00"

    masked = _NUM_UNIT.sub(_stash, text)
    return [
        re.sub(r"\x00(\d+)\x00", lambda m: units[int(m.group(1))], phrase)
        for phrase in _BUDOUX_PARSER.parse(masked)
    ]


def _wrap_digit_safe(
    text: str, font: ImageFont.FreeTypeFont, max_width: int
) -> list[str]:
    """数字グループを割らずに幅で greedy 折り返しする."""
    lines: list[str] = []
    current = ""
    for atom in _num_safe_atoms(text):
        test = current + atom
        if not current or font.getbbox(test)[2] - font.getbbox(test)[0] <= max_width:
            current = test
        else:
            lines.append(current)
            current = atom
    if current:
        lines.append(current)
    return lines


# フォント実測用のスクラッチ描画（実インク下端を測り、見切れを防ぐ）
_SCRATCH = ImageDraw.Draw(Image.new("RGB", (4, 4)))


def _cell_h(font: ImageFont.FreeTypeFont) -> int:
    """フォントの実行高（ascent+descent。NotoSansJP-Bold は約1.45×サイズ）."""
    asc, desc = font.getmetrics()
    return asc + desc


def _block_ink(font, lines, stroke) -> tuple[int, int]:
    """block を draw-y=0 に描いたときの (先頭行インク上端, 最終行インク下端) を返す."""
    it = _SCRATCH.textbbox((0, 0), lines[0], font=font, stroke_width=stroke)[1]
    ib = (len(lines) - 1) * _cell_h(font) + _SCRATCH.textbbox(
        (0, 0), lines[-1], font=font, stroke_width=stroke
    )[3]
    return it, ib


def _fit_zone(
    font_name: str, text: str, zone_h: int, max_w: int,
    max_lines: int, cap: int, floor: int, *, digit_safe: bool = False,
) -> tuple[ImageFont.FreeTypeFont, int, list[str]]:
    """幅とゾーン高さの両方を埋める最大フォントを選ぶ（＝密度を上げる）."""
    wrap = _wrap_digit_safe if digit_safe else _wrap_text_phrases
    for size in range(cap, floor - 1, -3):
        font = _load_font(font_name, size)
        lines = wrap(text, font, max_w)
        # 実行高（ascent+descent）でゾーンに収まるか判定する（見切れ防止）
        if len(lines) <= max_lines and _cell_h(font) * len(lines) <= zone_h:
            return font, size, lines
    font = _load_font(font_name, floor)
    return font, floor, wrap(text, font, max_w)


def _fit_bottom(
    font_name: str, text: str, zone_h: int, wide_w: int
) -> tuple[ImageFont.FreeTypeFont, int, list[str]]:
    """下段: まず1行で最大化、無理なら数字を割らず2行にする."""
    font, size, lines = _fit_zone(
        font_name, text, zone_h, wide_w, 1, 216, 108, digit_safe=True
    )
    if len(lines) <= 1:
        return font, size, lines
    return _fit_zone(font_name, text, zone_h, wide_w, 2, 132, 84, digit_safe=True)


def _advance(font: ImageFont.FreeTypeFont, ch: str) -> float:
    """1文字の送り幅。全角(CJK/かな)は _TRACK 分詰める（ASCIIは自然幅のまま）."""
    adv = font.getlength(ch)
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        adv *= 1 - _TRACK
    return adv


def _char_xs(font, text, x) -> list[int]:
    """各文字の描画 x 座標（文字詰め適用）を返す."""
    xs, cx = [], float(x)
    for ch in text:
        xs.append(round(cx))
        cx += _advance(font, ch)
    return xs


def _draw_tracked(draw, text, font, x, y, fill, stroke_w) -> None:
    """上段/中段: 1文字ずつ文字詰めして袋文字＋ドロップシャドウで描く."""
    xs = _char_xs(font, text, x)
    sh = max(2, stroke_w // 2)
    for cx, ch in zip(xs, text):  # 影
        draw.text((cx + sh, y + sh), ch, font=font, fill=(0, 0, 0),
                  stroke_width=stroke_w, stroke_fill=(0, 0, 0))
    for cx, ch in zip(xs, text):  # 白文字＋黒縁
        draw.text((cx, y), ch, font=font, fill=fill,
                  stroke_width=stroke_w, stroke_fill="#000000")


def _v_gradient(w: int, h: int, top_rgb, bot_rgb) -> Image.Image:
    """縦グラデーション画像（上=top_rgb・下=bot_rgb）."""
    grad = Image.new("RGB", (max(1, w), max(1, h)))
    gd = ImageDraw.Draw(grad)
    for yy in range(grad.height):
        t = yy / max(1, grad.height - 1)
        gd.line([(0, yy), (grad.width, yy)], fill=tuple(
            round(top_rgb[k] + (bot_rgb[k] - top_rgb[k]) * t) for k in range(3)
        ))
    return grad


# 上端を白の中縁と同化させないため、明るくても彩度の高い色にする（青みB/Rを落とす）。
_GOLD = ((255, 206, 40), (247, 138, 10))  # 下段本体: 濃い金→オレンジ金
_BLUE = ((70, 190, 255), (25, 115, 245))  # 強調キーワード: はっきりした水色→青


def _draw_bottom_line(base, draw, text, font, x, y, hl, black_w, white_w) -> None:
    """下段1行を「3重袋文字＋グラデ塗り＋影」で派手に描く（参考チャンネル風）.

    重ね順: 影 → 黒の外縁 → 白の中縁 → 金グラデ塗り（強調語だけ青グラデ）。
    """
    w, h = base.size
    xs = _char_xs(font, text, x)
    total = black_w + white_w
    sh = max(3, total // 2)
    for cx, ch in zip(xs, text):  # 影
        draw.text((cx + sh, y + sh), ch, font=font, fill=(0, 0, 0),
                  stroke_width=total, stroke_fill=(0, 0, 0))
    for cx, ch in zip(xs, text):  # 黒の外縁
        draw.text((cx, y), ch, font=font, fill=(0, 0, 0),
                  stroke_width=total, stroke_fill=(0, 0, 0))
    for cx, ch in zip(xs, text):  # 白の中縁
        draw.text((cx, y), ch, font=font, fill=(255, 255, 255),
                  stroke_width=white_w, stroke_fill=(255, 255, 255))
    # 塗り部分のマスクを作り、金/青グラデを流し込む
    hs = text.index(hl) if (hl and hl in text) else -1
    he = hs + len(hl) if hs >= 0 else -1
    mask_gold = Image.new("L", (w, h), 0)
    mask_blue = Image.new("L", (w, h), 0)
    dg, db = ImageDraw.Draw(mask_gold), ImageDraw.Draw(mask_blue)
    for i, (cx, ch) in enumerate(zip(xs, text)):
        (db if hs <= i < he else dg).text((cx, y), ch, font=font, fill=255)
    bb = _SCRATCH.textbbox((0, 0), text, font=font)
    gy, gh = y + bb[1], max(1, bb[3] - bb[1])
    region = (0, gy, w, gy + gh)
    base.paste(_v_gradient(w, gh, *_GOLD), (0, gy), mask_gold.crop(region))
    base.paste(_v_gradient(w, gh, *_BLUE), (0, gy), mask_blue.crop(region))


def compose_thumbnail(
    base_image_path: Path,
    copy: ThumbCopy,
    output_path: Path,
    config: ThumbnailConfig,
) -> Path:
    """固定マスコット等のベース画像に高密度3層テキストを合成する（同分野TTP準拠）.

    伸びているAI解説チャンネルの「型」に合わせた構成:
    - 文字で画面を埋め、余白を潰す（縮小しても読める）
    - 上段=製品名/導入／中段=説明／下段=ベネフィット（金グラデの3重袋文字＋数字）
    - 下段のキーワード1語を青グラデで2トーン強調、影・多重縁取りで派手に
    文字はすべて Pillow 描画のため AI 画像特有の文字化けが起きない。
    """
    width, height = config.width, config.height
    font_name = config.font_name
    margin = 30
    # 上段（製品名）はマスコットの目を避けて左に収める。
    text_w = int(width * 0.47) - margin
    # 中段（説明）は目より下に来るので少し広く使い、縮小しても読める大きさにする。
    mid_w = int(width * 0.60) - margin
    # 下段の黄色袋文字は被写体上でも読めるので広く使う。
    bottom_w = int(width * 0.80) - margin

    with Image.open(base_image_path) as base_src:
        base = base_src.convert("RGB")
        if base.size != (width, height):
            base = base.resize((width, height), Image.LANCZOS)

    # 左側をしっかり暗くして文字可読性を確保（右のマスコットは明るいまま残す）
    scrim = Image.new("L", (width, 1), 0)
    fade_end = int(width * 0.70)
    for x in range(fade_end):
        scrim.putpixel((x, 0), round(225 * (1 - x / fade_end)))
    scrim = scrim.resize((width, height))
    base = Image.composite(
        Image.new("RGB", (width, height), (0, 0, 0)), base, scrim
    ).convert("RGB")

    pad = 16
    usable = height - 2 * pad
    top_zone = int(usable * 0.26)
    mid_zone = int(usable * 0.24)
    bottom_zone = int(usable * 0.42)

    top_font = top_lines = top_sw = None
    if copy.top:
        top_font, ts, top_lines = _fit_zone(
            font_name, copy.top, top_zone, text_w, 1, 150, 54
        )
        top_sw = max(4, round(ts * 0.07))
    m_font = m_lines = m_sw = None
    if copy.mid:
        # 中段は1行で最大化（縮小しても読める）。長い時だけ2行に落とす。
        m_font, ms, m_lines = _fit_zone(
            font_name, copy.mid, mid_zone, mid_w, 1, 104, 44
        )
        if len(m_lines) > 1:
            m_font, ms, m_lines = _fit_zone(
                font_name, copy.mid, mid_zone, mid_w, 2, 76, 40
            )
        m_sw = max(3, round(ms * 0.085))
    b_font = b_lines = None
    b_black = b_white = b_reserve = 0
    if copy.bottom:
        b_font, bs, b_lines = _fit_bottom(font_name, copy.bottom, bottom_zone, bottom_w)
        b_black = max(6, round(bs * 0.08))   # 黒の外縁
        b_white = max(4, round(bs * 0.05))   # 白の中縁
        # 見切れ防止: 縁取り総幅＋影オフセットを下端の予約に含める
        b_reserve = b_black + b_white + max(3, (b_black + b_white) // 2)

    # 3行を「行間を詰めて」下から積む。下段フックを下端に接地し（マスコットの目に
    # かからず胴体側に重なる）、その上に中段・上段を小さいギャップで積む。
    line_gap = round(height * 0.030)
    ty0 = my0 = by0 = 0
    cursor = None  # 直下ブロックのインク上端
    if copy.bottom:
        it, ib = _block_ink(b_font, b_lines, b_reserve)
        by0 = (height - pad) - ib
        cursor = by0 + it
    if copy.mid:
        it, ib = _block_ink(m_font, m_lines, m_sw)
        anchor = (cursor - line_gap) if cursor is not None else height - pad
        my0 = anchor - ib
        cursor = my0 + it
    if copy.top:
        it, ib = _block_ink(top_font, top_lines, top_sw)
        anchor = (cursor - line_gap) if cursor is not None else height - pad
        ty0 = anchor - ib
        cursor = ty0 + it
    if cursor is not None and cursor < pad:  # 上にはみ出したら全体を下げる
        shift = pad - cursor
        ty0 += shift
        my0 += shift
        by0 += shift

    draw = ImageDraw.Draw(base)
    if copy.top:
        _draw_tracked(draw, top_lines[0], top_font, margin, ty0, "#FFFFFF", top_sw)
    if copy.mid:
        lh = _cell_h(m_font)
        for i, line in enumerate(m_lines):
            _draw_tracked(draw, line, m_font, margin, my0 + i * lh, "#FFFFFF", m_sw)
    if copy.bottom:
        lh = _cell_h(b_font)
        kw = copy.highlight
        for i, line in enumerate(b_lines):
            hl_here = kw if (kw and kw in line) else ""
            _draw_bottom_line(
                base, draw, line, b_font, margin, by0 + i * lh,
                hl_here, b_black, b_white,
            )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    base.save(str(output_path), "PNG")
    logger.info("Thumbnail composed: {}", output_path)
    return output_path
