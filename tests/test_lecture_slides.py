from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import sync_playwright

from lecture.reveal import ScenePlan
from lecture.slides import _background_for, render_slides

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_background_mood_resolves_to_existing_asset() -> None:
    mood, url = _background_for({"background_mood": "warm"})

    assert mood == "warm"
    assert Path(url.removeprefix("file://")).exists()


def test_background_moods_use_same_fixed_asset() -> None:
    urls = {
        _background_for({"background_mood": mood})[1]
        for mood in ("explain", "safety", "warm")
    }

    assert len(urls) == 1


def test_unknown_background_mood_fails_fast() -> None:
    with pytest.raises(RuntimeError, match="不明な background_mood"):
        _background_for({"background_mood": "nightmare"})


def test_long_title_source_stays_inside_character_safe_area(
    tmp_path: Path,
) -> None:
    script = {
        "scenes": [
            {
                "slide": {
                    "template": "title",
                    "background_mood": "warm",
                    "heading": "プロンプトが効かない3D生成AIの罠と回避法",
                    "subheading": "反転は空欄、編集は言葉",
                    "source_label": (
                        "Beyond Prompts: Unconditional 3D Inversion for "
                        "Out-of-Distribution Shapes"
                    ),
                },
                "lines": [{"text": "導入です。"}],
            }
        ]
    }
    render_slides(
        script,
        [ScenePlan(states=[0], line_state_idx=[0])],
        tmp_path,
        REPO_ROOT / "fonts" / "MPLUSRounded1c-Black.ttf",
        REPO_ROOT / "fonts" / "NotoSansJP-Bold.ttf",
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto((tmp_path / "scene_01_s1.html").resolve().as_uri())
        box = page.locator(".source").bounding_box()
        browser.close()

    assert box is not None
    assert box["x"] >= 520
    assert box["x"] + box["width"] <= 1400


@pytest.mark.parametrize(
    "diagram_type,items",
    [
        ("flow", ["入力", "変換", "出力"]),
        ("tree", ["全体", "分類A", "分類B", "分類C"]),
        ("layers", ["利用者", "サービス", "基盤"]),
        ("timeline", ["発見", "検証", "導入", "改善"]),
        ("cycle", ["観測", "判断", "実行", "学習"]),
        ("matrix", ["低・小", "高・小", "低・大", "高・大"]),
        (
            "table",
            [
                "指標 | TRELLIS | 提案法",
                "SigLIP↑ | 0.0797 | 0.1469",
                "処理時間↓ | 52分 | 8分",
            ],
        ),
    ],
)
def test_semantic_diagrams_render_inside_safe_area(
    tmp_path: Path,
    diagram_type: str,
    items: list[str],
) -> None:
    slide = {
        "template": "diagram",
        "background_mood": "explain",
        "heading": f"{diagram_type} 図",
        "diagram_type": diagram_type,
        "items": items,
        "left_title": "効果",
        "right_title": "負荷",
    }
    script = {"scenes": [{"slide": slide, "lines": [{"text": "説明"}]}]}
    render_slides(
        script,
        [ScenePlan(states=[len(items)], line_state_idx=[0])],
        tmp_path,
        REPO_ROOT / "fonts" / "MPLUSRounded1c-Black.ttf",
        REPO_ROOT / "fonts" / "NotoSansJP-Bold.ttf",
    )

    assert (tmp_path / "scene_01_s1.png").is_file()
    html = (tmp_path / "scene_01_s1.html").read_text(encoding="utf-8")
    assert f'diagram-{diagram_type}' in html


def test_source_figure_renders_from_job_assets(tmp_path: Path) -> None:
    figure_dir = tmp_path / "source_figures"
    figure_dir.mkdir()
    Image.new("RGB", (1200, 700), "white").save(figure_dir / "figure_01.png")
    slides_dir = tmp_path / "slides"
    slide = {
        "template": "figure",
        "background_mood": "explain",
        "heading": "論文の概念図",
        "figure_index": 1,
        "caption": "Figure 1: 入力から出力までの構造",
        "attribution": "Paper — Figure 1",
    }
    script = {"scenes": [{"slide": slide, "lines": [{"text": "説明"}]}]}

    render_slides(
        script,
        [ScenePlan(states=[0], line_state_idx=[0])],
        slides_dir,
        REPO_ROOT / "fonts" / "MPLUSRounded1c-Black.ttf",
        REPO_ROOT / "fonts" / "NotoSansJP-Bold.ttf",
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        page.goto((slides_dir / "scene_01_s1.html").resolve().as_uri())
        box = page.locator(".source-figure-card").bounding_box()
        browser.close()

    assert box is not None
    assert box["x"] >= 400
    assert box["x"] + box["width"] <= 1520
