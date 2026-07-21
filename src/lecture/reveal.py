"""セリフ同期のスライド段階表示 (show_items) の計画を組み立てる。

slides.py (状態ごとの PNG 描画) と assemble.py (状態切替のタイミング) が共有する。
"""

from dataclasses import dataclass

REVEAL_TEMPLATES = {"bullets", "outro", "compare"}


@dataclass
class ScenePlan:
    states: list[int]          # 描画すべき表示状態 (見えている項目数) の列
    line_state_idx: list[int]  # 各セリフがどの状態か (states のインデックス)


def total_units(slide: dict) -> int:
    """スライドの表示ステップ総数。bullets/outro は項目数、compare はカラム数。"""
    if slide["template"] == "compare":
        return 2
    return len(slide["items"])


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
