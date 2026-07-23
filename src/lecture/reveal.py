"""セリフ同期のスライド段階表示 (show_items) の計画を組み立てる。

slides.py (状態ごとの PNG 描画) と assemble.py (状態切替のタイミング) が共有する。
"""

from dataclasses import dataclass

REVEAL_TEMPLATES = {"bullets", "outro", "compare", "diagram"}


@dataclass
class ScenePlan:
    states: list[int]          # 描画すべき表示状態 (見えている項目数) の列
    line_state_idx: list[int]  # 各セリフがどの状態か (states のインデックス)


def total_units(slide: dict) -> int:
    """スライドの表示ステップ総数。項目型は要素数、compare はカラム数。"""
    if slide["template"] == "compare":
        return 2
    return len(slide["items"])


def normalize_reveal_counts(script: dict) -> int:
    """意味内容を変えず、整数の段階表示だけを描画可能な範囲へ直す。"""
    repaired = 0
    for scene in script.get("scenes", []):
        slide = scene.get("slide", {})
        if slide.get("template") not in REVEAL_TEMPLATES:
            continue
        lines = scene.get("lines")
        if not isinstance(lines, list) or not lines:
            continue
        values = [line.get("show_items") for line in lines]
        if not all(isinstance(value, int) for value in values):
            continue
        units = total_units(slide)
        if units < 1:
            continue
        previous = 1
        normalized = []
        for value in values:
            current = min(units, max(1, previous, value))
            normalized.append(current)
            previous = current
        normalized[-1] = units
        for line, old, new in zip(lines, values, normalized, strict=True):
            if old != new:
                line["show_items"] = new
                repaired += 1
    return repaired


def build_reveal_plan(script: dict) -> list[ScenePlan]:
    plans = []
    for scene in script["scenes"]:
        slide = scene["slide"]
        lines = scene["lines"]
        if slide["template"] not in REVEAL_TEMPLATES:
            plans.append(ScenePlan(states=[0], line_state_idx=[0] * len(lines)))
            continue
        states: list[int] = []
        idx = []
        for line in lines:
            if "show_items" not in line:
                raise RuntimeError(
                    f"{slide['template']} シーンのセリフに show_items がない: "
                    f"{line['text'][:30]}"
                )
            value = line["show_items"]
            if not states or value != states[-1]:
                states.append(value)
            idx.append(len(states) - 1)
        plans.append(ScenePlan(states=states, line_state_idx=idx))
    return plans
