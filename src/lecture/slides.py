"""台本のスライド定義を Jinja2 + Playwright で PNG に描画する。

zoompan のジッタ回避のため 2 倍解像度 (3840x2160) で撮り、合成時に縮小する。
"""

from pathlib import Path

import budoux
from jinja2 import Environment, FileSystemLoader
from loguru import logger
from markupsafe import Markup, escape
from playwright.sync_api import sync_playwright
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name

from lecture.reveal import ScenePlan

TEMPLATE_DIR = Path(__file__).parent / "templates"
BACKGROUND_DIR = (
    Path(__file__).resolve().parents[2] / "assets" / "lecture" / "backgrounds"
)
BACKGROUND_MOODS = {"explain", "safety", "warm"}
BACKGROUND_FILE = "infirmary_bed_row_pink_v1.png"
WIDTH, HEIGHT = 1920, 1080
SCALE = 2

_budoux_parser = budoux.load_default_japanese_parser()


def _budoux_filter(text: str) -> Markup:
    """日本語テキストに <wbr> を挿入して自然な位置で改行させる。"""
    return Markup(_budoux_parser.translate_html_string(str(escape(text))))


def _highlight_filter(code: str) -> Markup:
    """コードを Pygments でハイライトする (インラインスタイル)。"""
    stripped = code.lstrip()
    if stripped.startswith("$") or "\n$" in code:
        lexer_name = "console"
    elif stripped.startswith(("curl", "irm", "pip ", "uv ", "#")):
        lexer_name = "console"
    elif "import " in code or "def " in code:
        lexer_name = "python"
    else:
        lexer_name = "bash"
    lexer = get_lexer_by_name(lexer_name)
    formatter = HtmlFormatter(nowrap=True, noclasses=True, style="monokai")
    return Markup(highlight(code, lexer, formatter))


def _background_for(slide: dict) -> tuple[str, str]:
    """互換用の雰囲気指定を検証し、全シーン共通の背景を返す。"""
    mood = slide.get("background_mood", "explain")
    if mood not in BACKGROUND_MOODS:
        choices = ", ".join(sorted(BACKGROUND_MOODS))
        raise RuntimeError(f"不明な background_mood '{mood}' (選択肢: {choices})")
    path = BACKGROUND_DIR / BACKGROUND_FILE
    if not path.exists():
        raise RuntimeError(f"講義動画の背景が見つかりません: {path}")
    return mood, path.resolve().as_uri()


def _source_figure_url(slide: dict, job_dir: Path) -> str | None:
    if slide.get("template") != "figure":
        return None
    index = slide.get("figure_index")
    if type(index) is not int or index < 1:
        raise RuntimeError(f"figure_index が不正です: {index}")
    matches = tuple((job_dir / "source_figures").glob(f"figure_{index:02d}.*"))
    if len(matches) != 1:
        raise RuntimeError(
            f"一次資料の図 {index} が一意に見つかりません: {matches}"
        )
    return matches[0].resolve().as_uri()


def render_slides(
    script: dict,
    plans: list[ScenePlan],
    slides_dir: Path,
    heading_font: Path,
    body_font: Path,
) -> list[list[Path]]:
    """シーンごとに、段階表示の状態数ぶんの PNG を描画して返す。"""
    for font in (heading_font, body_font):
        if not font.exists():
            raise RuntimeError(f"フォントが見つからない: {font}")
    slides_dir.mkdir(parents=True, exist_ok=True)

    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=True)
    env.filters["budoux"] = _budoux_filter
    env.filters["highlight_code"] = _highlight_filter
    template = env.get_template("slide.html.j2")

    scenes = script["scenes"]
    scene_pngs: list[list[Path]] = []
    count = 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=SCALE
        )
        for i, (scene, plan) in enumerate(zip(scenes, plans), 1):
            pngs = []
            background_mood, background_url = _background_for(scene["slide"])
            source_figure_url = _source_figure_url(
                scene["slide"],
                slides_dir.parent,
            )
            for k, visible in enumerate(plan.states, 1):
                html = template.render(
                    slide=scene["slide"],
                    visible=visible if len(plan.states) > 1 or visible > 0 else None,
                    scene_no=i,
                    total=len(scenes),
                    heading_font_url=heading_font.resolve().as_uri(),
                    body_font_url=body_font.resolve().as_uri(),
                    background_mood=background_mood,
                    background_url=background_url,
                    source_figure_url=source_figure_url,
                )
                html_path = slides_dir / f"scene_{i:02d}_s{k}.html"
                html_path.write_text(html, encoding="utf-8")
                png_path = slides_dir / f"scene_{i:02d}_s{k}.png"
                page.goto(html_path.resolve().as_uri())
                page.screenshot(path=png_path)
                pngs.append(png_path)
                count += 1
            scene_pngs.append(pngs)
        browser.close()
    logger.info("スライド {} 枚を描画 ({}x 解像度): {}", count, SCALE, slides_dir)
    return scene_pngs
