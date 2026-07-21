"""講義動画パイプライン CLI。

使い方 (リポジトリルートで):
    PYTHONPATH=src uv run python -m lecture.cli generate <URL>
    PYTHONPATH=src uv run python -m lecture.cli generate <URL> --script <path>
    PYTHONPATH=src uv run python -m lecture.cli render <job_dir>
"""

import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path

import click
from loguru import logger

REPO_ROOT = Path(__file__).resolve().parents[2]
FONTS_DIR = REPO_ROOT / "fonts"
HEADING_FONT = FONTS_DIR / "MPLUSRounded1c-Black.ttf"
BODY_FONT = FONTS_DIR / "NotoSansJP-Bold.ttf"
DEFAULT_OUT_DIR = REPO_ROOT / "tmp" / "lecture"
VIDEO_CHARACTER_DIR = REPO_ROOT / "assets" / "characters" / "video_v3"
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
        REPO_ROOT
        / "assets"
        / "lecture"
        / "eyecatch_recap_otologic_glocken02-4.wav",
    ),
)


@click.group()
def main() -> None:
    """講義動画パイプライン（クロノIT方式）。specs/LECTURE_SPEC.md 参照。"""


@main.command()
@click.argument("url")
@click.option("--out-dir", type=click.Path(path_type=Path), default=DEFAULT_OUT_DIR)
@click.option(
    "--script", "script_path", type=click.Path(exists=True, path_type=Path),
    default=None, help="既存の台本 JSON を使う (claude -p をスキップ)",
)
def generate(url: str, out_dir: Path, script_path: Path | None) -> None:
    """URL から講義動画 mp4 を生成する。"""
    from lecture.fetch import fetch_content
    from lecture.script_gen import generate_script

    source = fetch_content(url)

    if script_path is not None:
        script = json.loads(script_path.read_text(encoding="utf-8"))
        logger.info("既存台本を使用: {}", script_path)
    else:
        script = generate_script(source)

    job_id = f"{datetime.now():%Y%m%d-%H%M%S}-{_slugify(script['title'])}"
    job_dir = out_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "source.txt").write_text(source.text, encoding="utf-8")
    (job_dir / "script.json").write_text(
        json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("ジョブディレクトリ: {}", job_dir)
    _render(script, job_dir)


@main.command()
@click.argument("job_dir", type=click.Path(exists=True, path_type=Path))
def render(job_dir: Path) -> None:
    """既存ジョブの script.json から再レンダリングする (台本手直し用)。"""
    script = json.loads((job_dir / "script.json").read_text(encoding="utf-8"))
    _render(script, job_dir)


def _render(script: dict, job_dir: Path) -> None:
    from lecture.assemble import EyeCatch, assemble
    from lecture.characters import prepare_characters
    from lecture.reveal import build_reveal_plan
    from lecture.slides import render_slides
    from lecture.tts import synthesize_all

    plans = build_reveal_plan(script)
    characters = prepare_characters(
        DEFAULT_OUT_DIR / "_assets_v3",
        custom_dir=VIDEO_CHARACTER_DIR,
        preserve_custom_canvas=True,
    )
    scene_state_pngs = render_slides(
        script, plans, job_dir / "slides", HEADING_FONT, BODY_FONT
    )
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
    video = assemble(
        script,
        scene_wavs,
        scene_state_pngs,
        plans,
        job_dir,
        FONTS_DIR,
        characters,
        eyecatches=eyecatches,
    )
    click.echo(f"\n完成: {video}")
    click.echo(f"タイトル: {script['title']}")


def _slugify(title: str) -> str:
    ascii_part = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_part).strip("-").lower()
    return slug[:40] if slug else "lecture"


if __name__ == "__main__":
    main()
