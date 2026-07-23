"""図解を含むスライドの段階表示テスト。"""

from lecture.reveal import build_reveal_plan, normalize_reveal_counts, total_units


def test_diagram_nodes_appear_in_dialogue_order() -> None:
    slide = {
        "template": "diagram",
        "diagram_type": "flow",
        "items": ["入力", "処理", "出力"],
    }
    script = {
        "scenes": [
            {
                "slide": slide,
                "lines": [
                    {"text": "まず入力です。", "show_items": 1},
                    {"text": "次に処理します。", "show_items": 2},
                    {"text": "最後に出力します。", "show_items": 3},
                ],
            }
        ]
    }

    plans = build_reveal_plan(script)

    assert total_units(slide) == 3
    assert plans[0].states == [1, 2, 3]
    assert plans[0].line_state_idx == [0, 1, 2]


def test_reveal_counts_are_repaired_without_rewriting_the_script() -> None:
    script = {
        "scenes": [
            {
                "slide": {
                    "template": "compare",
                    "left_items": ["A"],
                    "right_items": ["B"],
                },
                "lines": [
                    {"text": "左", "show_items": 1},
                    {"text": "右", "show_items": 3},
                ],
            },
            {
                "slide": {
                    "template": "diagram",
                    "diagram_type": "flow",
                    "items": ["入力", "処理", "確認", "出力"],
                },
                "lines": [
                    {"text": "処理", "show_items": 3},
                    {"text": "逆行", "show_items": 2},
                    {"text": "超過", "show_items": 7},
                ],
            },
        ]
    }

    repaired = normalize_reveal_counts(script)

    assert repaired == 3
    assert [line["show_items"] for line in script["scenes"][0]["lines"]] == [
        1,
        2,
    ]
    assert [line["show_items"] for line in script["scenes"][1]["lines"]] == [
        3,
        3,
        4,
    ]
