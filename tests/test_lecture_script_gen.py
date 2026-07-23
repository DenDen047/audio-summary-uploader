"""AI台本を動画・投稿工程へ渡す前の契約テスト。"""

import json
from subprocess import CompletedProcess
from unittest.mock import patch

from lecture.fetch import SourceContent, SourceFigure
from lecture.script_gen import (
    _assert_subscription_auth,
    _build_claude_retry_prompt,
    _claude_schema,
    _finalize,
    _generate_with_claude,
    _generation_metadata,
    _is_polite_utterance,
    _parse_claude_output,
    _parse_codex_output,
    _source_figures_prompt,
    _validate,
    _validate_diagram,
    _validate_world,
    generate_script,
)


def test_subscription_auth_accepts_codex_status_on_stderr() -> None:
    claude_status = CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps(
            {
                "loggedIn": True,
                "authMethod": "claude.ai",
                "subscriptionType": "max",
            }
        ),
        stderr="",
    )
    codex_status = CompletedProcess(
        args=[],
        returncode=0,
        stdout="",
        stderr="Logged in using ChatGPT\n",
    )

    with (
        patch("lecture.script_gen.shutil.which", return_value="/bin/tool"),
        patch(
            "lecture.script_gen.subprocess.run",
            side_effect=[claude_status, codex_status],
        ),
    ):
        _assert_subscription_auth()


def test_claude_schema_removes_unsupported_draft_declaration() -> None:
    schema = json.loads(_claude_schema())
    line_properties = schema["properties"]["scenes"]["items"]["properties"][
        "lines"
    ]["items"]["properties"]

    assert "$schema" not in schema
    assert schema["additionalProperties"] is False
    assert line_properties["text"]["maxLength"] == 80
    slide = schema["properties"]["scenes"]["items"]["properties"]["slide"]
    assert {"diagram", "figure"} <= set(
        slide["properties"]["template"]["enum"]
    )
    assert {"diagram_type", "figure_index"} <= set(slide["required"])
    assert schema["properties"]["scenes"]["minItems"] == 8
    assert schema["properties"]["scenes"]["maxItems"] == 14
    lines = schema["properties"]["scenes"]["items"]["properties"]["lines"]
    assert lines["minItems"] == 2
    assert lines["maxItems"] == 6
    assert "table" in slide["properties"]["diagram_type"]["enum"]


def test_claude_generation_uses_configured_timeout() -> None:
    response = CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps({"structured_output": {}}),
        stderr="",
    )

    with patch(
        "lecture.script_gen.subprocess.run", return_value=response
    ) as run:
        _generate_with_claude(
            "prompt",
            "opus",
            "xhigh",
            timeout_seconds=3600,
        )

    assert run.call_args.kwargs["timeout"] == 3600


def test_claude_retry_prompt_includes_previous_script() -> None:
    previous = {"title": "前回", "scenes": [{"lines": []}]}

    retry_prompt = _build_claude_retry_prompt(
        "元の指示",
        previous,
        ["scene 2 line 1: 透は常に敬語で話す"],
    )

    assert "元の指示" in retry_prompt
    assert json.dumps(previous, ensure_ascii=False, indent=2) in retry_prompt
    assert "scene 2 line 1: 透は常に敬語で話す" in retry_prompt


def test_generate_script_passes_remaining_claude_errors_to_codex() -> None:
    source = SourceContent(
        url="https://example.com/source",
        title="Source",
        text="本文",
        kind="html",
    )
    draft = {"scenes": []}
    metadata = {
        "agent": "claude-code-cli",
        "model_requested": "opus",
        "models_used": ["claude-opus"],
        "effort": "xhigh",
        "authentication": "claude-max-subscription",
        "role": "draft-and-character-writing",
    }
    review_metadata = {
        "agent": "codex-cli",
        "model_requested": "gpt-5.6-sol",
        "models_used": ["gpt-5.6-sol"],
        "effort": "xhigh",
        "authentication": "chatgpt-subscription",
        "role": "technical-and-editorial-review",
    }
    remaining_errors = ["scene 2 line 1: 透は常に敬語で話す"]

    with (
        patch("lecture.script_gen._assert_subscription_auth"),
        patch(
            "lecture.script_gen._generate_with_claude",
            side_effect=[(draft, metadata), (draft, metadata)],
        ),
        patch(
            "lecture.script_gen._validate",
            side_effect=[remaining_errors, remaining_errors, []],
        ),
        patch(
            "lecture.script_gen._review_with_codex",
            return_value=(draft, review_metadata),
        ) as review,
        patch("lecture.script_gen._finalize"),
    ):
        generate_script(source)

    assert review.call_args.kwargs["errors"] == remaining_errors


def test_world_validation_requires_tooru_problem_first() -> None:
    scenes = [
        {
            "lines": [
                {
                    "speaker": "metan",
                    "text": "今日は新しい技術を説明します。",
                    "metan_pose": "viewer",
                    "zunda_pose": "listen",
                },
                {
                    "speaker": "zunda",
                    "text": "よろしくお願いします。",
                    "metan_pose": "default",
                    "zunda_pose": "default",
                },
            ]
        }
    ]

    errors = _validate_world(scenes)

    assert "scene 1 line 1: 導入は困っている透の相談から始める" in errors


def test_world_validation_enforces_character_speech_styles() -> None:
    scenes = [
        {
            "lines": [
                {
                    "speaker": "zunda",
                    "text": "澪先生、この結果の意味が分からず困っています。",
                    "metan_pose": "default",
                    "zunda_pose": "confused",
                },
                {
                    "speaker": "metan",
                    "text": "まず、どこで予想と違ったのかを整理しましょう。",
                    "metan_pose": "explain",
                    "zunda_pose": "listen",
                },
                {
                    "speaker": "zunda",
                    "text": "この数字だけ見ればいいの？",
                    "metan_pose": "default",
                    "zunda_pose": "confused",
                },
                {
                    "speaker": "metan",
                    "text": "数字だけでは足りないよ。",
                    "metan_pose": "explain",
                    "zunda_pose": "listen",
                },
                {
                    "speaker": "metan",
                    "text": "透くん、少し慌てすぎですよ。",
                    "metan_pose": "tease",
                    "zunda_pose": "flustered",
                },
            ]
        }
    ]

    errors = _validate_world(scenes)

    assert "scene 1 line 3: 透は常に敬語で話す" in errors
    assert "scene 1 line 4: 澪の非敬語はからかう時だけにする" in errors
    assert "scene 1 line 5: 澪がからかう時は敬語を使わない" in errors


def test_world_validation_accepts_polite_tooru_and_casual_teasing_mio() -> None:
    scenes = [
        {
            "lines": [
                {
                    "speaker": "zunda",
                    "text": "澪先生、この結果の意味が分からず困っています。",
                    "metan_pose": "default",
                    "zunda_pose": "confused",
                },
                {
                    "speaker": "metan",
                    "text": "まず、どこで予想と違ったのかを整理しましょう。",
                    "metan_pose": "explain",
                    "zunda_pose": "listen",
                },
                {
                    "speaker": "metan",
                    "text": "透くん、そんなに慌てなくてもいいのに。",
                    "metan_pose": "tease",
                    "zunda_pose": "flustered",
                },
                {
                    "speaker": "zunda",
                    "text": "すみません。順番に確認してみます。",
                    "metan_pose": "default",
                    "zunda_pose": "shy",
                },
            ]
        }
    ]

    assert _validate_world(scenes) == []


def test_polite_validation_ignores_plain_style_inside_a_quote() -> None:
    assert _is_polite_utterance(
        "論文には『この方法が速い。』と書かれています。"
    )


def test_world_validation_checks_spoken_reading_style_too() -> None:
    scenes = [
        {
            "lines": [
                {
                    "speaker": "zunda",
                    "text": "澪先生、この結果が分からず困っています。",
                    "reading": "みおせんせい、この結果が分からず困ってる。",
                    "metan_pose": "default",
                    "zunda_pose": "confused",
                },
                {
                    "speaker": "metan",
                    "text": "では、順番に確認しましょう。",
                    "reading": "では、順番に確認しましょう。",
                    "metan_pose": "explain",
                    "zunda_pose": "listen",
                },
            ]
        }
    ]

    assert "scene 1 line 1: 透は常に敬語で話す" in _validate_world(scenes)


def test_finalize_is_idempotent_for_required_credits() -> None:
    script = {
        "description": (
            "概要です。\n\n"
            "出典: https://example.com/source\n"
            "連絡先: reader@example.com"
        ),
        "scenes": [
            {
                "slide": {
                    "source_label": "Source: https://example.com/source"
                },
                "lines": [
                    {
                        "text": "連絡先はreader@example.comです。",
                    }
                ],
            }
        ],
        "title": "テスト",
    }
    source = SourceContent(
        url="https://example.com/source",
        title="Source",
        text="本文",
        kind="html",
    )

    _finalize(script, source)
    _finalize(script, source)

    assert script["description"].count("VOICEVOX:満別花丸") == 1
    assert script["description"].count("効果音: OtoLogic") == 1
    public_script = json.dumps(script, ensure_ascii=False)
    assert "https://example.com/source" not in public_script
    assert "reader@example.com" not in public_script


def test_claude_and_codex_metadata_records_subscription_policy() -> None:
    script_payload = {
        "title": "テスト",
        "description": "説明",
        "tags": ["AI"],
        "thumbnail_text": ["なぜ？", "解決"],
        "thumbnail_visual_prompt": "motif=network; 光る経路",
        "eyecatch_before_scenes": [2],
        "scenes": [],
    }
    envelope = json.dumps(
        {
            "structured_output": script_payload,
            "modelUsage": {"claude-opus-4-8": {}},
        },
        ensure_ascii=False,
    )

    script, primary = _parse_claude_output(envelope, "opus", "xhigh")
    reviewed = _parse_codex_output(
        json.dumps(script_payload, ensure_ascii=False)
    )
    generation = _generation_metadata(
        primary,
        {
            "agent": "codex-cli",
            "model_requested": "gpt-5.6-sol",
            "models_used": ["gpt-5.6-sol"],
            "effort": "xhigh",
            "authentication": "chatgpt-subscription",
            "role": "technical-and-editorial-review",
        },
    )

    assert script == script_payload
    assert reviewed == script_payload
    assert generation["script_agent"] == "claude-code-cli+codex-cli"
    assert generation["script_models_used"] == [
        "claude-opus-4-8",
        "gpt-5.6-sol",
    ]
    assert generation["quality_mode"] == "xhigh"
    assert generation["metered_api"] is False
    assert generation["primary"]["authentication"] == (
        "claude-max-subscription"
    )
    assert generation["review"]["authentication"] == (
        "chatgpt-subscription"
    )


def test_validate_requires_nonempty_tags_and_short_dialogue() -> None:
    script = {
        "title": "テスト動画",
        "description": "説明",
        "tags": [],
        "thumbnail_text": ["疑問", "解決"],
        "thumbnail_visual_prompt": "疑問が一本の光る経路で解決する",
        "eyecatch_before_scenes": [2],
        "scenes": [
            {
                "slide": slide,
                "lines": [
                    {
                        "speaker": "metan",
                        "text": "説明します。",
                        "reading": "説明します。",
                        "metan_pose": "default",
                        "zunda_pose": "listen",
                        **({"show_items": 1} if slide["template"] == "outro" else {}),
                    }
                ],
            }
            for slide in (
                {
                    "template": "title",
                    "background_mood": "warm",
                    "heading": "scene 1",
                    "subheading": "導入",
                    "source_label": "Source: example.com",
                },
                {
                    "template": "quote",
                    "background_mood": "warm",
                    "heading": "scene 2",
                    "quote": "引用",
                    "attribution": "出典",
                },
                {
                    "template": "outro",
                    "background_mood": "warm",
                    "heading": "scene 3",
                    "items": ["まとめ"],
                },
            )
        ],
    }
    script["scenes"][0]["lines"][0]["text"] = "あ" * 81

    errors = _validate(script)

    assert "tags は1件以上の文字列配列にする" in errors
    assert "scene 1 line 1: text が80文字を超えている" in errors
    assert "scenes は8〜14個にする (現在3個)" in errors
    assert "scene 1: lines は2〜6個にする (現在1個)" in errors

    script["scenes"] *= 5
    assert "scenes は8〜14個にする (現在15個)" in _validate(script)


def test_validate_reports_missing_template_fields_without_raising() -> None:
    errors = _validate(
        {
            "title": "テスト動画",
            "description": "説明",
            "tags": ["テスト"],
            "thumbnail_text": ["疑問", "解決"],
            "thumbnail_visual_prompt": "疑問が一本の光る経路で解決する",
            "eyecatch_before_scenes": [2],
            "scenes": [
                {
                    "slide": {
                        "template": template,
                        "background_mood": "warm",
                        "heading": "見出し",
                    },
                    "lines": [
                        {
                            "speaker": "metan",
                            "text": "説明します。",
                            "reading": "説明します。",
                            "metan_pose": "default",
                            "zunda_pose": "listen",
                        }
                    ],
                }
                for template in ("title", "quote", "outro")
            ],
        }
    )

    assert "scene 3: slide.items が空または不正" in errors


def test_source_figures_prompt_exposes_captions_but_not_urls() -> None:
    source = SourceContent(
        url="https://papers.example/article",
        title="Paper",
        text="本文",
        kind="html",
        figures=(
            SourceFigure(
                url="https://papers.example/private-path/figure.png",
                caption="Figure 1: 学習ループ",
            ),
        ),
    )

    prompt = _source_figures_prompt(source)

    assert "1. Figure 1: 学習ループ" in prompt
    assert "https://" not in prompt


def test_validate_distinguishes_quantitative_table_from_matrix() -> None:
    table = {
        "diagram_type": "table",
        "items": [
            "指標 | TRELLIS | 提案法",
            "SigLIP↑ | 0.0797 | 0.1469",
            "処理時間↓ | 52分 | 8分",
        ],
    }
    matrix = {
        "diagram_type": "matrix",
        "items": [
            "指標 | TRELLIS | 提案法",
            "SigLIP↑ | 0.0797 | 0.1469",
            "横軸：品質",
            "縦軸：処理時間",
        ],
        "left_title": "品質",
        "right_title": "処理時間",
    }

    assert _validate_diagram(2, table) == []
    malformed_table = {"diagram_type": "table", "items": ["指標 | A", "値 | 1 | 2"]}
    assert (
        "scene 2: table は同じ2〜4列を | で区切った行にする"
        in _validate_diagram(2, malformed_table)
    )
    errors = _validate_diagram(3, matrix)
    assert "scene 3: 定量比較は matrix ではなく table を使う" in errors
    assert "scene 3: matrix の items に軸名を含めない" in errors


def test_validate_accepts_semantic_diagram_and_available_source_figure() -> None:
    slides = [
        {
            "template": "title",
            "background_mood": "warm",
            "heading": "導入",
            "subheading": "図で理解する",
            "source_label": "Paper",
        },
        {
            "template": "diagram",
            "background_mood": "explain",
            "heading": "処理の流れ",
            "diagram_type": "flow",
            "items": ["入力", "変換", "出力"],
        },
        {
            "template": "figure",
            "background_mood": "explain",
            "heading": "論文の結果",
            "figure_index": 1,
            "caption": "Figure 1: 実験結果",
            "attribution": "Paper — Figure 1",
        },
        *[
            {
                "template": "quote",
                "background_mood": "explain",
                "heading": f"補足 {index}",
                "quote": "図の読み方を補足します",
                "attribution": "Paper",
            }
            for index in range(1, 5)
        ],
        {
            "template": "outro",
            "background_mood": "warm",
            "heading": "まとめ",
            "items": ["図で関係を見る"],
        },
    ]
    script = {
        "title": "図解テスト",
        "description": "説明",
        "tags": ["図解"],
        "thumbnail_text": ["関係が見える", "図で理解する"],
        "thumbnail_visual_prompt": "motif=research; 関係を示す線",
        "eyecatch_before_scenes": [3],
        "scenes": [
            {
                "slide": slide,
                "lines": [
                    {
                        "speaker": "metan",
                        "text": "まず要点を説明します。",
                        "reading": "まず要点を説明します。",
                        "metan_pose": "explain",
                        "zunda_pose": "listen",
                        **(
                            {"show_items": 1}
                            if slide["template"] in {"diagram", "outro"}
                            else {}
                        ),
                    },
                    {
                        "speaker": "zunda",
                        "text": "関係が見えると理解しやすいです。",
                        "reading": "関係が見えると理解しやすいです。",
                        "metan_pose": "default",
                        "zunda_pose": "understand",
                        **(
                            {"show_items": len(slide.get("items", []))}
                            if slide["template"] in {"diagram", "outro"}
                            else {}
                        ),
                    },
                ],
            }
            for slide in slides
        ],
    }
    script["scenes"][0]["lines"] = [
        {
            "speaker": "zunda",
            "text": "澪先生、処理の全体像が分からず困っています。",
            "reading": "みおせんせい、処理の全体像が分からず困っています。",
            "metan_pose": "default",
            "zunda_pose": "confused",
        },
        {
            "speaker": "metan",
            "text": "では、図を使って入力から出力まで整理しましょう。",
            "reading": "では、図を使って入力から出力まで整理しましょう。",
            "metan_pose": "explain",
            "zunda_pose": "listen",
        },
    ]

    assert _validate(script, available_figure_count=1) == []
    script["scenes"][2]["slide"]["figure_index"] = 2
    assert "scene 3: figure_index 2 は利用可能な図 1 件の範囲外" in _validate(
        script,
        available_figure_count=1,
    )
