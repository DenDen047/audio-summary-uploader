"""キャラ立ち絵素材を用意する。

優先: `assets/characters/{zunda,metan}.png` のカスタム立ち絵 (透過 PNG、
AI 生成のオリジナルキャラ等)。`{name}_open.png` (口開き差分) があれば
口パク用の口元パッチを自動抽出する。`{name}__{pose}.png` は台詞ごとに
切り替える表情・ポーズ差分として読み込み、同名の `_open.png` から
ポーズ固有の自然な口元パッチを抽出する。
フォールバック: VOICEVOX 公式ポートレートの上半身クロップ。
"""

from dataclasses import dataclass
from pathlib import Path

import httpx
from loguru import logger
from PIL import Image, ImageChops

from lecture.tts import ENGINE_URL, SPEAKER_MAP

BUST_HEIGHT = 500        # VOICEVOX ポートレート由来のバストアップの高さ (px)
BUST_CROP_FRACTION = 0.56  # 公式全身ポートレートの上から何割を使うか
# PowerPoint で手動調整した 1920x1080 の基準配置を再現する表示サイズ。
# 素材自体をこの比率向けに描き分けているため、骨格を引き伸ばしてはいけない。
CUSTOM_HEIGHTS = {"metan": 1232, "zunda": 1019}
CUSTOM_BLEEDS = {"metan": 357, "zunda": 170}
ALPHA_THRESHOLD = 24     # 背景除去の残りアルファをノイズとして落とす閾値
MOUTH_DIFF_THRESHOLD = 50  # 口開き差分とみなす画素差
MOUTH_PAD = 14           # 口元パッチ bbox の余白 (px)
MOUTH_GRID = 8           # ブロブ検出の格子サイズ (px)。輪郭の細線ノイズを除く
ALIGN_SEARCH = 6         # 口開き画像の位置合わせ探索範囲 (±px)
# 正規化後の口開き差分がこの範囲を超えたら、顔・髪の再生成混入として拒否する。
MAX_OPEN_DIFF_SIZE = (96, 72)

_VOICEVOX_NAMES = {"zunda": "満別花丸", "metan": "九州そら"}


@dataclass
class CharacterAssets:
    image: Path
    width: int
    height: int
    bleed: int                    # 下端を画面外へ落とす量
    mouth_patch: Path | None = None  # 口開き時に口元へ重ねるパッチ
    mouth_x: int = 0              # パッチの立ち絵内オフセット
    mouth_y: int = 0


def prepare_characters(
    assets_dir: Path,
    custom_dir: Path | None = None,
    preserve_custom_canvas: bool = False,
) -> dict[str, CharacterAssets]:
    assets_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, CharacterAssets] = {}
    portraits = None
    for speaker in SPEAKER_MAP:
        custom_src = (custom_dir / f"{speaker}.png") if custom_dir else None
        if custom_src is not None and custom_src.exists():
            result[speaker] = _prepare_custom(
                custom_src,
                assets_dir,
                speaker,
                preserve_canvas=preserve_custom_canvas,
            )
            for pose_src in sorted(custom_dir.glob(f"{speaker}__*.png")):
                if pose_src.stem.endswith("_open"):
                    continue
                pose = pose_src.stem.removeprefix(f"{speaker}__")
                if not pose:
                    raise RuntimeError(f"ポーズ名が空です: {pose_src}")
                pose_assets = _prepare_custom(
                    pose_src,
                    assets_dir,
                    speaker,
                    preserve_canvas=preserve_custom_canvas,
                    asset_name=f"{speaker}__{pose}",
                )
                result[f"{speaker}:{pose}"] = pose_assets
            continue
        image = assets_dir / f"{speaker}_bust{BUST_HEIGHT}.png"
        if not image.exists():
            if portraits is None:
                portraits = _fetch_portraits(assets_dir)
            _build_bust(portraits[speaker], image)
        with Image.open(image) as im:
            width, height = im.size
        result[speaker] = CharacterAssets(
            image=image, width=width, height=height, bleed=8
        )
    return result


def _prepare_custom(
    src: Path,
    assets_dir: Path,
    speaker: str,
    preserve_canvas: bool = False,
    asset_name: str | None = None,
) -> CharacterAssets:
    """カスタム立ち絵を正規化し、口開き差分があれば口元パッチを抽出する。"""
    closed_raw = _load_rgba(src)
    crop_box = (
        (0, 0, closed_raw.width, closed_raw.height)
        if preserve_canvas
        else _horizontal_crop_box(closed_raw)
    )
    closed = closed_raw.crop(crop_box)
    target_height = CUSTOM_HEIGHTS[speaker]
    scale = target_height / closed.height
    closed_n = closed.resize(
        (round(closed.width * scale), target_height), Image.LANCZOS
    )
    output_stem = asset_name or speaker
    image = assets_dir / f"{output_stem}_custom{target_height}.png"
    closed_n.save(image)
    assets = CharacterAssets(
        image=image,
        width=closed_n.width,
        height=target_height,
        bleed=CUSTOM_BLEEDS[speaker],
    )
    logger.info("カスタム立ち絵を使用: {} ({}x{})", src, assets.width, assets.height)

    open_src = src.with_name(f"{src.stem}_open{src.suffix}")
    if not open_src.exists():
        return assets

    open_raw = _load_rgba(open_src)
    if open_raw.size == closed_raw.size:
        open_ = open_raw.crop(crop_box)
    else:
        open_ = open_raw.crop(_horizontal_crop_box(open_raw))
    open_n = open_.resize(
        (round(open_.width * scale), round(open_.height * scale)), Image.LANCZOS
    )
    dx, dy = _best_offset(closed_n, open_n)
    open_canvas = Image.new("RGBA", closed_n.size, (0, 0, 0, 0))
    open_canvas.paste(open_n, (dx, dy))

    full_diff = ImageChops.difference(
        _flatten_gray(closed_n), _flatten_gray(open_canvas)
    ).getbbox()
    if full_diff is not None:
        diff_width = full_diff[2] - full_diff[0]
        diff_height = full_diff[3] - full_diff[1]
        max_width, max_height = MAX_OPEN_DIFF_SIZE
        if diff_width > max_width or diff_height > max_height:
            raise RuntimeError(
                "口開き差分が広すぎます。口以外を元画像と一致させてください: "
                f"{open_src} diff={diff_width}x{diff_height}"
            )

    box = _diff_bbox(closed_n, open_canvas)
    if box is None:
        logger.warning("口開き差分が検出できない: {} (口パク無効)", open_src)
        return assets
    patch = open_canvas.crop(box)
    patch_path = assets_dir / f"{output_stem}_mouth.png"
    patch.save(patch_path)
    assets.mouth_patch = patch_path
    assets.mouth_x, assets.mouth_y = box[0], box[1]
    logger.info(
        "口パクパッチ抽出: {} box={} offset=({}, {})", patch_path.name, box, dx, dy
    )
    return assets


def _load_rgba(path: Path) -> Image.Image:
    im = Image.open(path).convert("RGBA")
    mask = im.getchannel("A").point(lambda a: 255 if a > ALPHA_THRESHOLD else 0)
    return Image.composite(im, Image.new("RGBA", im.size, (0, 0, 0, 0)), mask)


def _trim(im: Image.Image) -> Image.Image:
    bbox = im.getchannel("A").getbbox()
    if bbox is None:
        raise RuntimeError("立ち絵画像が完全に透明")
    return im.crop(bbox)


def _horizontal_crop_box(im: Image.Image) -> tuple[int, int, int, int]:
    """左右の透明余白だけを除き、同一カメラ尺度を表す縦余白は保持する。"""
    bbox = im.getchannel("A").getbbox()
    if bbox is None:
        raise RuntimeError("立ち絵画像が完全に透明")
    return (bbox[0], 0, bbox[2], im.height)


def _flatten_gray(im: Image.Image) -> Image.Image:
    canvas = Image.new("RGBA", im.size, (255, 255, 255, 255))
    return Image.alpha_composite(canvas, im).convert("L")


def _mean_abs_diff(a: Image.Image, b: Image.Image) -> float:
    hist = ImageChops.difference(a, b).histogram()
    total = sum(hist)
    return sum(i * c for i, c in enumerate(hist)) / total


def _best_offset(closed: Image.Image, open_: Image.Image) -> tuple[int, int]:
    """口開き画像を閉じ画像へ最も重なるオフセットに合わせる (総当たり探索)。"""
    base = _flatten_gray(closed)
    best = (0, 0)
    best_score = float("inf")
    for dy in range(-ALIGN_SEARCH, ALIGN_SEARCH + 1):
        for dx in range(-ALIGN_SEARCH, ALIGN_SEARCH + 1):
            canvas = Image.new("RGBA", closed.size, (0, 0, 0, 0))
            canvas.paste(open_, (dx, dy))
            score = _mean_abs_diff(base, _flatten_gray(canvas))
            if score < best_score:
                best_score = score
                best = (dx, dy)
    return best


def _diff_bbox(
    closed: Image.Image, open_canvas: Image.Image
) -> tuple[int, int, int, int] | None:
    """差分の最も濃い連結ブロブ (=口) の bbox を返す。

    再生成による輪郭の細線ノイズは格子密度が低いので、格子化して
    最大密度セルから連結領域を広げることで口の塊だけを拾う。
    """
    diff = ImageChops.difference(_flatten_gray(closed), _flatten_gray(open_canvas))
    mask = diff.point(lambda v: 255 if v > MOUTH_DIFF_THRESHOLD else 0)
    gw = max(1, closed.width // MOUTH_GRID)
    gh = max(1, closed.height // MOUTH_GRID)
    grid = list(mask.resize((gw, gh), Image.BOX).getdata())

    peak = max(grid)
    if peak < 32:  # 小さな顔でも、口の差分と呼べる密度があるかを確認する
        return None
    peak_idx = grid.index(peak)
    cutoff = peak * 0.35
    # 最大密度セルから 4 近傍で連結領域を広げる
    seen = {peak_idx}
    frontier = [peak_idx]
    while frontier:
        idx = frontier.pop()
        cx, cy = idx % gw, idx // gw
        for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
            if not (0 <= nx < gw and 0 <= ny < gh):
                continue
            nidx = ny * gw + nx
            if nidx not in seen and grid[nidx] >= cutoff:
                seen.add(nidx)
                frontier.append(nidx)

    xs = [idx % gw for idx in seen]
    ys = [idx // gw for idx in seen]
    left = max(0, min(xs) * MOUTH_GRID - MOUTH_PAD)
    top = max(0, min(ys) * MOUTH_GRID - MOUTH_PAD)
    right = min(closed.width, (max(xs) + 1) * MOUTH_GRID + MOUTH_PAD)
    bottom = min(closed.height, (max(ys) + 1) * MOUTH_GRID + MOUTH_PAD)
    return (left, top, right, bottom)


def _fetch_portraits(assets_dir: Path) -> dict[str, Path]:
    import base64

    client = httpx.Client(base_url=ENGINE_URL, timeout=60)
    resp = client.get("/speakers")
    if resp.status_code != 200:
        raise RuntimeError("VOICEVOX ENGINE から話者一覧を取得できない")
    uuid_by_name = {s["name"]: s["speaker_uuid"] for s in resp.json()}

    paths = {}
    for speaker, vv_name in _VOICEVOX_NAMES.items():
        if vv_name not in uuid_by_name:
            raise RuntimeError(f"VOICEVOX に話者 {vv_name} が見つからない")
        info = client.get(
            "/speaker_info", params={"speaker_uuid": uuid_by_name[vv_name]}
        )
        if info.status_code != 200:
            raise RuntimeError(f"speaker_info の取得に失敗: {vv_name}")
        raw = assets_dir / f"{speaker}_portrait.png"
        raw.write_bytes(base64.b64decode(info.json()["portrait"]))
        paths[speaker] = raw
        logger.info("ポートレート取得: {} → {}", vv_name, raw.name)
    return paths


def _build_bust(portrait: Path, out: Path) -> None:
    """全身ポートレート → 上半身クロップ (切断線は画面下端に隠れる前提)。"""
    im = Image.open(portrait).convert("RGBA")
    bust = im.crop((0, 0, im.width, int(im.height * BUST_CROP_FRACTION)))
    trimmed = _trim(bust)
    scale = BUST_HEIGHT / trimmed.height
    resized = trimmed.resize(
        (round(trimmed.width * scale), BUST_HEIGHT), Image.LANCZOS
    )
    resized.save(out)
