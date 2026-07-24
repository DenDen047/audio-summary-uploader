"""AI台本を動画・投稿工程へ渡す前の契約テスト。"""

import json
from subprocess import CompletedProcess
from unittest.mock import patch

from lecture.script_gen import (
    _assert_subscription_auth,
    _autonomous_task_prompt,
    _finalize,
    _generation_metadata,
    _is_polite_utterance,
    _run_autonomous_claude,
    _validate,
    _validate_diagram,
    _validate_world,
    _write_autonomous_inputs,
    generate_script,
)
from sources.fetch import SourceContent, SourceFigure


def test_subscription_auth_accepts_claude_max() -> None:
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
    with (
        patch("lecture.script_gen.shutil.which", return_value="/bin/tool"),
        patch(
            "lecture.script_gen.subprocess.run",
            return_value=claude_status,
        ),
    ):
        _assert_subscription_auth()


def test_autonomous_generation_enables_project_skill_and_fixed_tools(
    tmp_path,
) -> None:
    work_dir = tmp_path
    response = CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps(
            {"modelUsage": {"claude-opus": {}}},
            ensure_ascii=False,
        ),
        stderr="",
    )

    with patch(
        "lecture.script_gen.subprocess.run",
        return_value=response,
    ) as run:
        metadata = _run_autonomous_claude(
            work_dir,
            "opus",
            "xhigh",
            timeout_seconds=3600,
        )

    command = run.call_args.args[0]
    prompt = run.call_args.kwargs["input"]
    assert "--safe-mode" not in command
    assert command[command.index("--setting-sources") + 1] == "project"
    assert "Read,Write,Edit,Grep,Bash" in command
    assert "validate_workdir.py" in command[command.index("--allowedTools") + 1]
    assert command[command.index("--mcp-config") + 1] == '{"mcpServers": {}}'
    assert prompt.startswith("/lecture-generate-autonomously")
    assert metadata["models_used"] == ["claude-opus"]


def test_autonomous_input_contract_excludes_source_url(tmp_path) -> None:
    source = SourceContent(
        url="https://example.com/private?access_token=secret",
        title="Source https://example.com/private?access_token=secret",
        text="本文",
        kind="html",
        figures=[
            SourceFigure(
                caption=(
                    "Figure 1 https://example.com/private?access_token=secret"
                ),
                url="https://example.com/figure.png",
            )
        ],
    )

    _write_autonomous_inputs(source, tmp_path)

    run_input = (tmp_path / "run-input.json").read_text(encoding="utf-8")
    assert source.url not in run_input
    assert "https://" not in run_input
    assert (tmp_path / "source.txt").read_text(encoding="utf-8") == "本文"
    assert "run-input.jsonの契約" in _autonomous_task_prompt(tmp_path)
    assert "Source" not in _autonomous_task_prompt(tmp_path)


def test_generate_script_collects_autonomous_stage_outputs() -> None:
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
        "role": "autonomous-four-stage-generation",
    }
    stage_outputs = {}

    def run_autonomously(work_dir, model, effort, *, timeout_seconds):
        del model, effort, timeout_seconds
        payloads = {
            "source-understanding.json": {"stage": "understanding"},
            "teaching-outline.json": {"stage": "outline"},
            "scene-draft.json": draft,
            "script.json": draft,
            "run-status.json": {
                "status": "passed",
                "attempts": {
                    "understanding": 1,
                    "outline": 1,
                    "draft": 1,
                    "final": 1,
                },
                "validation": {
                    "stage": "final",
                    "file": "script.json",
                    "passed": True,
                    "errors": [],
                },
            },
        }
        for filename, payload in payloads.items():
            (work_dir / filename).write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
        return metadata

    with (
        patch("lecture.script_gen._assert_subscription_auth"),
        patch(
            "lecture.script_gen._run_autonomous_claude",
            side_effect=run_autonomously,
        ),
        patch("lecture.script_gen._validate", return_value=[]),
        patch("lecture.script_gen._finalize"),
    ):
        script = generate_script(source, stage_outputs=stage_outputs)

    assert script["generation"]["script_agent"] == "claude-code-cli"
    assert stage_outputs["source-understanding.json"]["stage"] == (
        "understanding"
    )
    assert stage_outputs["teaching-outline.json"]["stage"] == "outline"
    assert stage_outputs["scene-draft.json"] == {"scenes": []}
    assert stage_outputs["run-status.json"]["status"] == "passed"


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


def test_polite_validation_accepts_past_tense_and_trailing_vocative() -> None:
    assert _is_polite_utterance(
        "いい着眼です、透くん。中央値は45.2ミリでした。"
    )
    assert _is_polite_utterance(
        "いい着眼です、とおるくん。中央値はよんじゅうごてんにミリでした。"
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


def test_autonomous_metadata_records_subscription_policy() -> None:
    primary = {
        "agent": "claude-code-cli",
        "model_requested": "opus",
        "models_used": ["claude-opus-4-8"],
        "effort": "xhigh",
        "authentication": "claude-max-subscription",
        "role": "scene-writing",
    }
    generation = _generation_metadata(
        primary,
        {
            **primary,
            "effort": "xhigh",
            "role": "teaching-review-and-repair",
        },
    )

    assert generation["script_agent"] == "claude-code-cli"
    assert generation["script_models_used"] == ["claude-opus-4-8"]
    assert generation["quality_mode"] == "xhigh"
    assert generation["metered_api"] is False
    assert generation["primary"]["authentication"] == (
        "claude-max-subscription"
    )
    assert generation["review"]["authentication"] == (
        "claude-max-subscription"
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
    metan_text = (
        "ここでは入力と変換と出力のつながりを一つずつ確かめ、どの段階で意味が変わるのかを"
        "具体例と一緒に整理し、根拠と限界も分けて説明します。"
    )
    zunda_text = (
        "ぼくは図の矢印をたどりながら、前の段階で分かったことを次の例へ当てはめ、"
        "どこで判断が変わるかを自分の言葉で確かめてみます。"
    )
    for scene in script["scenes"]:
        while len(scene["lines"]) < 6:
            speaker = "metan" if len(scene["lines"]) % 2 == 0 else "zunda"
            scene["lines"].append(
                {
                    "speaker": speaker,
                    "text": metan_text if speaker == "metan" else zunda_text,
                    "reading": metan_text if speaker == "metan" else zunda_text,
                    "metan_pose": "explain" if speaker == "metan" else "default",
                    "zunda_pose": "listen" if speaker == "metan" else "understand",
                }
            )
        for line in scene["lines"]:
            text = metan_text if line["speaker"] == "metan" else zunda_text
            line["text"] = text
            line["reading"] = text
            if scene["slide"]["template"] in {"diagram", "outro"}:
                line["show_items"] = len(scene["slide"]["items"])
    script["scenes"][0]["lines"][0]["text"] = (
        "澪先生、ぼくは入力から出力までの矢印をたどりましたが、どの段階で意味が変わるのか"
        "判断できず、具体例で確かめたいです。"
    )
    script["scenes"][0]["lines"][0]["reading"] = (
        "みおせんせい、ぼくは入力から出力までの矢印をたどりましたが、どの段階で意味が"
        "変わるのか判断できず、具体例で確かめたいです。"
    )

    assert _validate(script, available_figure_count=1) == []
    script["scenes"][2]["slide"]["figure_index"] = 2
    assert "scene 3: figure_index 2 は利用可能な図 1 件の範囲外" in _validate(
        script,
        available_figure_count=1,
    )
