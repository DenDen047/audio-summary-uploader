"""情報源から投稿可能な澪・透の講義動画一式を生成する。"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loguru import logger
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from lecture.assemble import EyeCatch, assemble
from lecture.characters import CharacterAssets, prepare_characters
from lecture.fetch import (
    SourceContent,
    fetch_content,
    materialize_source_figures,
)
from lecture.reveal import build_reveal_plan
from lecture.script_gen import generate_script
from lecture.slides import render_slides
from lecture.thumbnail_backdrop import (
    ThumbnailBackdropOptions,
    ThumbnailBackdropResult,
    generate_thumbnail_backdrop,
)
from lecture.tts import managed_voicevox_engine, synthesize_all
from summary.citation import sanitize_public_text

REPO_ROOT = Path(__file__).resolve().parents[2]
FONTS_DIR = REPO_ROOT / "fonts"
HEADING_FONT = FONTS_DIR / "MPLUSRounded1c-Black.ttf"
BODY_FONT = FONTS_DIR / "NotoSansJP-Bold.ttf"
DEFAULT_OUT_DIR = REPO_ROOT / "tmp" / "lecture"
VIDEO_CHARACTER_DIR = REPO_ROOT / "assets" / "characters" / "video_v3"
THUMBNAIL_BACKDROP = REPO_ROOT / "assets" / "lecture" / "thumbnail_backdrop_v1.png"
THUMBNAIL_COPY_MAX_CHARS = 14
EYECATCH_ASSETS = (
    (
        REPO_ROOT / "assets" / "lecture" / "eyecatch_practice.png",
        REPO_ROOT
        / "assets"
        / "lecture"
        / "eyecatch_practice_otologic_xylophone06-1.wav",
    ),
    (
        REPO_ROOT / "assets" / "lecture" / "eyecatch_recap.png",
        REPO_ROOT / "assets" / "lecture" / "eyecatch_recap_otologic_glocken02-4.wav",
    ),
)


@dataclass(frozen=True)
class RenderedLecture:
    video_path: Path
    first_slide_path: Path
    characters: dict[str, CharacterAssets]


@dataclass(frozen=True)
class LectureArtifacts:
    source_url: str
    job_dir: Path
    source_path: Path
    script_path: Path
    video_path: Path
    thumbnail_path: Path
    upload_metadata_path: Path
    title: str
    description: str
    tags: tuple[str, ...]
    thumbnail_text: tuple[str, str]
    thumbnail_backdrop: ThumbnailBackdropResult
    script_generation: dict


def generate_lecture(
    url: str,
    out_dir: Path = DEFAULT_OUT_DIR,
    *,
    script_path: Path | None = None,
    thumbnail_size: tuple[int, int] = (1280, 720),
    thumbnail_backdrop_options: ThumbnailBackdropOptions | None = None,
    script_model: str = "opus",
    script_effort: str = "xhigh",
    review_model: str = "gpt-5.6-sol",
    review_effort: str = "xhigh",
    generation_timeout_seconds: int = 3600,
) -> LectureArtifacts:
    """URL から動画・サムネイル・投稿情報を同じジョブフォルダへ出力する。"""
    source = fetch_content(url)
    stage_outputs: dict[str, dict] = {}
    if script_path is None:
        script = generate_script(
            source,
            model=script_model,
            effort=script_effort,
            review_model=review_model,
            review_effort=review_effort,
            generation_timeout_seconds=generation_timeout_seconds,
            stage_outputs=stage_outputs,
        )
    else:
        script = json.loads(script_path.read_text(encoding="utf-8"))
        logger.info("既存台本を使用: {}", script_path)

    title = _required_text(script, "title")
    description = _required_text(script, "description")
    tags = _tags(script)
    thumbnail_text = _thumbnail_text(script, title)
    script_generation = _script_generation(script, script_model)
    job_dir = _new_job_dir(out_dir, title)
    source_output = job_dir / "source.txt"
    script_output = job_dir / "script.json"
    source_output.write_text(source.text, encoding="utf-8")
    for filename, payload in stage_outputs.items():
        (job_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    selected_figures = _selected_source_figure_indices(script)
    materialize_source_figures(
        source,
        job_dir / "source_figures",
        selected_figures,
    )
    _bind_source_figure_metadata(script, source)
    script_output.write_text(
        json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    backdrop = generate_thumbnail_backdrop(
        script,
        job_dir / "thumbnail-background.png",
        THUMBNAIL_BACKDROP,
        thumbnail_backdrop_options or ThumbnailBackdropOptions(),
    )

    logger.info("講義動画ジョブを開始: {}", job_dir)
    rendered = render_lecture(script, job_dir)
    thumbnail_path = generate_lecture_thumbnail(
        rendered,
        job_dir / "thumbnail.png",
        thumbnail_size,
        script,
        backdrop_path=backdrop.path,
    )
    upload_metadata_path = job_dir / "upload_metadata.json"
    upload_metadata_path.write_text(
        json.dumps(
            {
                "source_url": source.url,
                "title": title,
                "description": description,
                "tags": list(tags),
                "thumbnail_text": list(thumbnail_text),
                "thumbnail_background": backdrop.as_metadata(),
                "script_generation": script_generation,
                "video_path": str(rendered.video_path),
                "thumbnail_path": str(thumbnail_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("投稿可能な成果物一式を生成: {}", job_dir)
    return LectureArtifacts(
        source_url=source.url,
        job_dir=job_dir,
        source_path=source_output,
        script_path=script_output,
        video_path=rendered.video_path,
        thumbnail_path=thumbnail_path,
        upload_metadata_path=upload_metadata_path,
        title=title,
        description=description,
        tags=tags,
        thumbnail_text=thumbnail_text,
        thumbnail_backdrop=backdrop,
        script_generation=script_generation,
    )


def _selected_source_figure_indices(script: dict) -> set[int]:
    selected: set[int] = set()
    for scene in script.get("scenes", []):
        slide = scene.get("slide", {})
        if slide.get("template") != "figure":
            continue
        index = slide.get("figure_index")
        if type(index) is not int or index < 1:
            raise RuntimeError(f"figure_index が不正です: {index}")
        selected.add(index)
    return selected


def _bind_source_figure_metadata(script: dict, source: SourceContent) -> None:
    """AIの転記揺れを捨て、表示キャプションを一次資料の値へ戻す。"""
    source_title = re.sub(
        r"https?://\S+",
        "",
        sanitize_public_text(source.title),
    ).strip()
    if not source_title:
        source_title = "一次資料"
    for scene in script.get("scenes", []):
        slide = scene.get("slide", {})
        if slide.get("template") != "figure":
            continue
        index = slide.get("figure_index")
        if type(index) is not int or not 1 <= index <= len(source.figures):
            raise RuntimeError(
                f"figure_index {index} は利用可能な図 {len(source.figures)} 件の範囲外"
            )
        figure = source.figures[index - 1]
        slide["caption"] = sanitize_public_text(figure.caption)
        slide["attribution"] = f"{source_title} — Figure {index}"


def render_lecture(script: dict, job_dir: Path) -> RenderedLecture:
    """検証済み台本をスライド・音声・立ち絵付き MP4 に描画する。"""
    plans = build_reveal_plan(script)
    characters = prepare_characters(
        job_dir.parent / "_assets_v3",
        custom_dir=VIDEO_CHARACTER_DIR,
        preserve_custom_canvas=True,
    )
    scene_state_pngs = render_slides(
        script, plans, job_dir / "slides", HEADING_FONT, BODY_FONT
    )
    with managed_voicevox_engine(job_dir / "voicevox-engine.log"):
        scene_wavs = synthesize_all(script, job_dir / "audio")
    eyecatch_scenes = tuple(script.get("eyecatch_before_scenes", []))
    if len(eyecatch_scenes) > len(EYECATCH_ASSETS):
        raise RuntimeError(
            f"アイキャッチ素材が不足しています: {len(eyecatch_scenes)}箇所"
        )
    eyecatches = tuple(
        EyeCatch(scene, image, audio)
        for scene, (image, audio) in zip(eyecatch_scenes, EYECATCH_ASSETS)
    )
    video_path = assemble(
        script,
        scene_wavs,
        scene_state_pngs,
        plans,
        job_dir,
        FONTS_DIR,
        characters,
        eyecatches=eyecatches,
    )
    return RenderedLecture(
        video_path=video_path,
        first_slide_path=scene_state_pngs[0][0],
        characters=characters,
    )


def generate_lecture_thumbnail(
    rendered: RenderedLecture,
    output_path: Path,
    size: tuple[int, int],
    script: dict,
    *,
    backdrop_path: Path | None = None,
) -> Path:
    """確定立ち絵と短い訴求コピーから高視認性のサムネイルを作る。"""
    width, height = size
    if width <= 0 or height <= 0:
        raise ValueError(f"サムネイル寸法が不正です: {size}")
    selected_backdrop = backdrop_path or THUMBNAIL_BACKDROP
    if not selected_backdrop.is_file():
        raise RuntimeError(f"サムネイル背景がありません: {selected_backdrop}")
    with Image.open(selected_backdrop) as backdrop:
        canvas = backdrop.convert("RGBA").resize(size, Image.LANCZOS)

    scale = width / 1280
    panel = Image.new("RGBA", size, (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel)
    panel_draw.rounded_rectangle(
        (
            round(330 * scale),
            round(106 * scale),
            round(1034 * scale),
            round(616 * scale),
        ),
        radius=round(34 * scale),
        fill=(53, 25, 55, 232),
        outline=(255, 255, 255, 220),
        width=max(2, round(4 * scale)),
    )
    canvas = Image.alpha_composite(canvas, panel)

    # 顔を大きく見せるため、動画用の全身比率ではなく胸上中心に切り出す。
    mio = _thumbnail_character(
        rendered.characters.get("metan:point", rendered.characters["metan"]),
        target_height=round(754 * height / 720),
        crop_fraction=0.68,
    )
    toru = _thumbnail_character(
        rendered.characters.get("zunda:confused", rendered.characters["zunda"]),
        target_height=round(575 * height / 720),
        crop_fraction=0.62,
    )
    _composite_with_outline(
        canvas,
        mio,
        (round(-38 * width / 1280), round(10 * height / 720)),
        round(9 * scale),
    )
    _composite_with_outline(
        canvas,
        toru,
        (round(1040 * width / 1280), round(138 * height / 720)),
        round(8 * scale),
    )

    draw = ImageDraw.Draw(canvas)
    brand_font = ImageFont.truetype(BODY_FONT, max(14, round(28 * scale)))
    chip_font = ImageFont.truetype(BODY_FONT, max(13, round(24 * scale)))
    copy = _thumbnail_text(script, _required_text(script, "title"))
    center_x = round(682 * width / 1280)
    _draw_centered_pill(
        draw,
        (center_x, round(74 * height / 720)),
        "澪先生と透のIT講座",
        brand_font,
        fill="#FFFFFF",
        background="#D64274",
        scale=scale,
    )
    _draw_thumbnail_copy(
        draw,
        copy[0],
        center_x,
        round(246 * height / 720),
        round(620 * width / 1280),
        fill="#FFFFFF",
        scale=scale,
    )
    _draw_thumbnail_copy(
        draw,
        copy[1],
        center_x,
        round(382 * height / 720),
        round(620 * width / 1280),
        fill="#FFD84D",
        scale=scale,
    )
    _draw_centered_pill(
        draw,
        (center_x, round(548 * height / 720)),
        "初心者でも大丈夫",
        chip_font,
        fill="#351A37",
        background="#FFFFFF",
        scale=scale,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(output_path, "PNG", optimize=True)
    logger.info("講義動画サムネイルを生成: {}", output_path)
    return output_path


def _thumbnail_character(
    asset: CharacterAssets,
    *,
    target_height: int,
    crop_fraction: float,
) -> Image.Image:
    with Image.open(asset.image) as source:
        character = source.convert("RGBA")
    alpha_box = character.getchannel("A").getbbox()
    if alpha_box is None:
        raise RuntimeError(f"立ち絵が完全に透明です: {asset.image}")
    character = character.crop(alpha_box)
    crop_bottom = max(1, round(character.height * crop_fraction))
    character = character.crop((0, 0, character.width, crop_bottom))
    resize_scale = target_height / character.height
    return character.resize(
        (round(character.width * resize_scale), target_height), Image.LANCZOS
    )


def _composite_with_outline(
    canvas: Image.Image,
    character: Image.Image,
    position: tuple[int, int],
    outline_width: int,
) -> None:
    mask = character.getchannel("A")
    filter_size = max(3, outline_width * 2 + 1)
    outline_mask = mask.filter(ImageFilter.MaxFilter(filter_size))
    shadow_mask = outline_mask.filter(ImageFilter.GaussianBlur(max(2, outline_width)))
    shadow = Image.new("RGBA", character.size, (45, 18, 48, 0))
    shadow.putalpha(shadow_mask.point(lambda alpha: round(alpha * 0.5)))
    canvas.alpha_composite(
        shadow, (position[0] + outline_width, position[1] + outline_width)
    )
    outline = Image.new("RGBA", character.size, (255, 255, 255, 0))
    outline.putalpha(outline_mask)
    canvas.alpha_composite(outline, position)
    canvas.alpha_composite(character, position)


def _draw_thumbnail_copy(
    draw: ImageDraw.ImageDraw,
    text: str,
    center_x: int,
    center_y: int,
    max_width: int,
    *,
    fill: str,
    scale: float,
) -> None:
    font_size = round(88 * scale)
    minimum_size = max(28, round(52 * scale))
    while font_size > minimum_size:
        font = ImageFont.truetype(HEADING_FONT, font_size)
        if draw.textbbox((0, 0), text, font=font, stroke_width=0)[2] <= max_width:
            break
        font_size -= 2
    font = ImageFont.truetype(HEADING_FONT, font_size)
    draw.text(
        (center_x, center_y),
        text,
        font=font,
        fill=fill,
        stroke_width=max(4, round(9 * scale)),
        stroke_fill="#2F1834",
        anchor="mm",
    )


def _draw_centered_pill(
    draw: ImageDraw.ImageDraw,
    center: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    *,
    fill: str,
    background: str,
    scale: float,
) -> None:
    box = draw.textbbox(center, text, font=font, anchor="mm")
    pad_x = round(20 * scale)
    pad_y = round(10 * scale)
    pill = (box[0] - pad_x, box[1] - pad_y, box[2] + pad_x, box[3] + pad_y)
    draw.rounded_rectangle(pill, radius=round(18 * scale), fill=background)
    draw.text(center, text, font=font, fill=fill, anchor="mm")


def _new_job_dir(out_dir: Path, title: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{datetime.now():%Y%m%d-%H%M%S}-{_slugify(title)}"
    candidate = out_dir / stem
    suffix = 2
    while candidate.exists():
        candidate = out_dir / f"{stem}-{suffix:02d}"
        suffix += 1
    candidate.mkdir()
    return candidate


def _required_text(script: dict, key: str) -> str:
    value = script.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"台本の {key} が空です")
    return value.strip()


def _tags(script: dict) -> tuple[str, ...]:
    raw = script.get("tags")
    if not isinstance(raw, list) or not raw:
        raise RuntimeError("台本の tags は1件以上の配列である必要があります")
    tags = tuple(str(tag).strip() for tag in raw if str(tag).strip())
    if not tags:
        raise RuntimeError("台本の tags が空です")
    return tags


def _script_generation(script: dict, requested_model: str) -> dict:
    value = script.get("generation")
    if isinstance(value, dict):
        return value
    return {
        "script_agent": "manual-script",
        "script_model_requested": requested_model,
        "script_models_used": [],
        "metered_api": False,
    }


def _thumbnail_text(script: dict, title: str) -> tuple[str, str]:
    raw = script.get("thumbnail_text")
    if raw is None:
        compact = title.replace("澪先生と学ぶ", "").strip()
        if len(compact) <= THUMBNAIL_COPY_MAX_CHARS:
            return compact, "やさしく解説"
        return (
            compact[:THUMBNAIL_COPY_MAX_CHARS],
            compact[THUMBNAIL_COPY_MAX_CHARS : 2 * THUMBNAIL_COPY_MAX_CHARS]
            or "やさしく解説",
        )
    if (
        not isinstance(raw, list)
        or len(raw) != 2
        or any(not isinstance(line, str) or not line.strip() for line in raw)
    ):
        raise RuntimeError("台本の thumbnail_text は空でない2行の文字列配列にする")
    lines = tuple(line.strip() for line in raw)
    if any(len(line) > THUMBNAIL_COPY_MAX_CHARS for line in lines):
        raise RuntimeError(
            f"thumbnail_text は各{THUMBNAIL_COPY_MAX_CHARS}文字以内にする"
        )
    return lines[0], lines[1]


def _slugify(title: str) -> str:
    ascii_part = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_part).strip("-").lower()
    return slug[:40] if slug else "lecture"
