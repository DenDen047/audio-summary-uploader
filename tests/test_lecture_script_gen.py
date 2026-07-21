"""AI台本を動画・投稿工程へ渡す前の契約テスト。"""

import json
from subprocess import CompletedProcess
from unittest.mock import patch

from lecture.fetch import SourceContent
from lecture.script_gen import (
    _assert_subscription_auth,
    _claude_schema,
    _finalize,
    _generation_metadata,
    _parse_claude_output,
    _parse_codex_output,
    _validate,
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

    assert "$schema" not in schema
    assert schema["additionalProperties"] is False


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
