"""Claude Maxで台本を作り、ChatGPT Codexで品質審査する。"""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from loguru import logger

from automator.citation import sanitize_public_text
from lecture.fetch import SourceContent
from lecture.reveal import REVEAL_TEMPLATES, total_units
from lecture.thumbnail_backdrop import THUMBNAIL_VISUAL_PROMPT_MAX_CHARS

PROMPT_PATH = Path(__file__).parent / "prompts" / "lecture_script.md"
REVIEW_PROMPT_PATH = Path(__file__).parent / "prompts" / "lecture_script_review.md"
SCHEMA_PATH = Path(__file__).parent / "prompts" / "lecture_script.schema.json"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CLAUDE_MODEL = "opus"
DEFAULT_CLAUDE_EFFORT = "xhigh"
DEFAULT_CODEX_MODEL = "gpt-5.6-sol"
DEFAULT_CODEX_EFFORT = "xhigh"
TEMPLATES = {"title", "bullets", "compare", "code", "quote", "outro"}
TEMPLATE_FIELDS = {
    "title": ("subheading", "source_label"),
    "bullets": ("items",),
    "compare": ("left_title", "left_items", "right_title", "right_items"),
    "code": ("code", "caption"),
    "quote": ("quote", "attribution"),
    "outro": ("items",),
}
SPEAKERS = {"zunda", "metan"}
VOICEVOX_CREDITS = ["VOICEVOX:満別花丸", "VOICEVOX:もち子さん"]
EYECATCH_AUDIO_CREDIT = "OtoLogic (https://otologic.jp/)"
POSES = {
    "metan": {
        "default",
        "explain",
        "point",
        "praise",
        "tease",
        "caution",
        "surprised",
        "wink",
        "viewer",
    },
    "zunda": {
        "default",
        "listen",
        "understand",
        "confused",
        "flustered",
        "shy",
        "delighted",
        "praised",
    },
}
GENERATION_TIMEOUT_SECONDS = 900
_PUBLIC_SOURCE_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


def generate_script(
    source: SourceContent,
    *,
    model: str = DEFAULT_CLAUDE_MODEL,
    effort: str = DEFAULT_CLAUDE_EFFORT,
    review_model: str = DEFAULT_CODEX_MODEL,
    review_effort: str = DEFAULT_CODEX_EFFORT,
) -> dict:
    _assert_subscription_auth()

    prompt = (
        PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{{TITLE}}", source.title)
        .replace("{{TEXT}}", source.text)
    )

    script, primary_metadata = _generate_with_claude(prompt, model, effort)
    errors = _validate(script)
    if errors:
        logger.warning("Claude初稿の検証エラー、1回だけ再生成する: {}", errors)
        retry_prompt = (
            prompt
            + "\n\n# 前回の出力の問題点（修正すること）\n\n"
            + "\n".join(f"- {e}" for e in errors)
        )
        script, primary_metadata = _generate_with_claude(
            retry_prompt, model, effort
        )
        errors = _validate(script)
        if errors:
            raise RuntimeError(f"Claude再生成後も台本が不正: {errors}")

    script, review_metadata = _review_with_codex(
        source,
        script,
        review_model,
        review_effort,
    )
    errors = _validate(script)
    if errors:
        logger.warning("Codex審査後の検証エラー、1回だけ再審査する: {}", errors)
        script, review_metadata = _review_with_codex(
            source,
            script,
            review_model,
            review_effort,
            errors=errors,
        )
        errors = _validate(script)
        if errors:
            raise RuntimeError(f"Codex再審査後も台本が不正: {errors}")

    script["generation"] = _generation_metadata(
        primary_metadata, review_metadata
    )
    _finalize(script, source)
    return script


def _generate_with_claude(
    prompt: str,
    model: str,
    effort: str,
) -> tuple[dict, dict]:
    logger.info(
        "Claude Maxで台本初稿を生成中 (model={}, effort={}, {}文字)...",
        model,
        effort,
        len(prompt),
    )
    result = subprocess.run(
        [
            "claude",
            "-p",
            "--safe-mode",
            "--model",
            model,
            "--effort",
            effort,
            "--tools",
            "",
            "--output-format",
            "json",
            "--no-session-persistence",
            "--json-schema",
            _claude_schema(),
        ],
        input=prompt,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=GENERATION_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Claude Codeが失敗 "
            f"(exit {result.returncode}): {result.stderr[-1000:]}"
        )
    return _parse_claude_output(result.stdout, model, effort)


def _claude_schema() -> str:
    """Claude CLI非対応のメタ宣言だけ除き、検証規則は維持する。"""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    schema.pop("$schema", None)
    return json.dumps(schema, ensure_ascii=False)


def _review_with_codex(
    source: SourceContent,
    script: dict,
    model: str,
    effort: str,
    *,
    errors: list[str] | None = None,
) -> tuple[dict, dict]:
    prompt = (
        REVIEW_PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{{TITLE}}", source.title)
        .replace("{{TEXT}}", source.text)
        .replace(
            "{{SCRIPT}}",
            json.dumps(script, ensure_ascii=False, indent=2),
        )
    )
    if errors:
        prompt += "\n\n# 必ず解消する機械検証エラー\n" + "\n".join(
            f"- {error}" for error in errors
        )
    logger.info(
        "Codexで台本を最終審査中 (model={}, effort={}, {}文字)...",
        model,
        effort,
        len(prompt),
    )
    with tempfile.TemporaryDirectory(prefix="lecture-script-") as tmp_dir:
        output_path = Path(tmp_dir) / "script.json"
        command = [
            "codex",
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--config",
            'approval_policy="never"',
            "--config",
            f'model_reasoning_effort="{effort}"',
            "--model",
            model,
            "--output-schema",
            str(SCHEMA_PATH),
            "--output-last-message",
            str(output_path),
        ]
        command.append("-")
        result = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=GENERATION_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "codex execが失敗 "
                f"(exit {result.returncode}): {result.stderr[-1000:]}"
            )
        if not output_path.is_file():
            raise RuntimeError("Codexが審査済み台本JSONを出力しませんでした")
        reviewed = _parse_codex_output(output_path.read_text(encoding="utf-8"))
        actual_model = _codex_model_from_stderr(result.stderr) or model
        return reviewed, {
            "agent": "codex-cli",
            "model_requested": model,
            "models_used": [actual_model],
            "effort": effort,
            "authentication": "chatgpt-subscription",
            "role": "technical-and-editorial-review",
        }


def _parse_claude_output(
    raw: str,
    requested_model: str,
    effort: str,
) -> tuple[dict, dict]:
    envelope = json.loads(raw)
    structured = envelope.get("structured_output")
    script = structured if isinstance(structured, dict) else _parse_json(
        str(envelope.get("result", ""))
    )
    model_usage = envelope.get("modelUsage", {})
    used_models = (
        sorted(str(name) for name in model_usage)
        if isinstance(model_usage, dict)
        else []
    )
    return script, {
        "agent": "claude-code-cli",
        "model_requested": requested_model,
        "models_used": used_models,
        "effort": effort,
        "authentication": "claude-max-subscription",
        "role": "draft-and-character-writing",
    }


def _parse_codex_output(raw: str) -> dict:
    """Codexの厳密スキーマ出力を台本辞書へ戻す。"""
    return _parse_json(raw)


def _codex_model_from_stderr(stderr: str) -> str | None:
    for line in stderr.splitlines():
        if line.startswith("model: "):
            return line.removeprefix("model: ").strip() or None
    return None


def _generation_metadata(primary: dict, review: dict) -> dict:
    models = list(dict.fromkeys(primary["models_used"] + review["models_used"]))
    quality_mode = primary["effort"]
    if primary["effort"] != review["effort"]:
        quality_mode = f"{primary['effort']}+{review['effort']}"
    return {
        "script_agent": "claude-code-cli+codex-cli",
        "script_model_requested": (
            f"{primary['model_requested']} + {review['model_requested']}"
        ),
        "script_models_used": models,
        "primary": primary,
        "review": review,
        "metered_api": False,
        "quality_mode": quality_mode,
    }


def _assert_subscription_auth() -> None:
    if shutil.which("claude") is None:
        raise RuntimeError("claude CLIが見つかりません")
    if shutil.which("codex") is None:
        raise RuntimeError("codex CLIが見つかりません")

    claude_status = subprocess.run(
        ["claude", "auth", "status", "--json"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=30,
    )
    if claude_status.returncode != 0:
        raise RuntimeError("Claude Codeの認証状態を確認できません")
    claude_auth = json.loads(claude_status.stdout)
    if not (
        claude_auth.get("loggedIn") is True
        and claude_auth.get("authMethod") == "claude.ai"
        and claude_auth.get("subscriptionType") == "max"
    ):
        raise RuntimeError(
            "Claude Maxのサブスクリプション認証が必要です。"
            "APIキー経路は費用方針により使用しません"
        )

    codex_status = subprocess.run(
        ["codex", "login", "status"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=30,
    )
    codex_auth_message = codex_status.stdout + codex_status.stderr
    if (
        codex_status.returncode != 0
        or "Logged in using ChatGPT" not in codex_auth_message
    ):
        raise RuntimeError(
            "CodexはChatGPTサブスクリプションでログインしてください。"
            "OPENAI_API_KEY経路は使用しません"
        )


def _parse_json(raw: str) -> dict:
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        raise RuntimeError(f"出力に JSON が含まれない: {raw[:300]}")
    return json.loads(raw[start : end + 1])


def _validate(script: dict) -> list[str]:
    errors = []
    for key in (
        "title",
        "description",
        "tags",
        "thumbnail_text",
        "thumbnail_visual_prompt",
        "scenes",
    ):
        if key not in script:
            errors.append(f"必須キー {key} がない")
    tags = script.get("tags")
    if not isinstance(tags, list) or not any(
        isinstance(tag, str) and tag.strip() for tag in tags
    ):
        errors.append("tags は1件以上の文字列配列にする")
    thumbnail_text = script.get("thumbnail_text")
    if (
        not isinstance(thumbnail_text, list)
        or len(thumbnail_text) != 2
        or any(
            not isinstance(line, str) or not line.strip() or len(line.strip()) > 14
            for line in thumbnail_text
        )
    ):
        errors.append("thumbnail_text は各14文字以内の空でない2行にする")
    visual_prompt = script.get("thumbnail_visual_prompt")
    if (
        not isinstance(visual_prompt, str)
        or not visual_prompt.strip()
        or len(visual_prompt.strip()) > THUMBNAIL_VISUAL_PROMPT_MAX_CHARS
    ):
        errors.append(
            "thumbnail_visual_prompt は"
            f"{THUMBNAIL_VISUAL_PROMPT_MAX_CHARS}文字以内の文字列にする"
        )
    scenes = script.get("scenes", [])
    if not isinstance(scenes, list) or len(scenes) < 3:
        errors.append(f"scenes が少なすぎる ({len(scenes)} 個)")
        return errors

    for i, scene in enumerate(scenes, 1):
        slide = scene.get("slide", {})
        template = slide.get("template")
        if template not in TEMPLATES:
            errors.append(f"scene {i}: 不明な template '{template}'")
        else:
            errors.extend(_validate_slide_fields(i, slide, template))
        background_mood = slide.get("background_mood")
        if background_mood not in {"explain", "safety", "warm"}:
            errors.append(f"scene {i}: background_mood '{background_mood}' が不正")
        if not slide.get("heading"):
            errors.append(f"scene {i}: heading がない")
        lines = scene.get("lines", [])
        if not lines:
            errors.append(f"scene {i}: lines が空")
        for j, line in enumerate(lines, 1):
            if line.get("speaker") not in SPEAKERS:
                errors.append(
                    f"scene {i} line {j}: 不明な speaker '{line.get('speaker')}'"
                )
            if not line.get("text", "").strip():
                errors.append(f"scene {i} line {j}: text が空")
            elif len(line["text"]) > 80:
                errors.append(
                    f"scene {i} line {j}: text が80文字を超えている"
                )
            if not line.get("reading", "").strip():
                errors.append(f"scene {i} line {j}: reading が空")
            for speaker in SPEAKERS:
                pose = line.get(f"{speaker}_pose")
                if pose not in POSES[speaker]:
                    choices = ", ".join(sorted(POSES[speaker]))
                    errors.append(
                        f"scene {i} line {j}: {speaker}_pose '{pose}' が不正"
                        f" (選択肢: {choices})"
                    )
        reveal_fields_valid = not any(
            error.startswith(f"scene {i}: slide.") for error in errors
        )
        if template in REVEAL_TEMPLATES and reveal_fields_valid:
            errors.extend(_validate_reveal(i, slide, lines))

    if scenes[0].get("slide", {}).get("template") != "title":
        errors.append("最初のシーンが title でない")
    if scenes[-1].get("slide", {}).get("template") != "outro":
        errors.append("最後のシーンが outro でない")
    eyecatches = script.get("eyecatch_before_scenes")
    if (
        not isinstance(eyecatches, list)
        or not 1 <= len(eyecatches) <= 2
        or any(type(scene) is not int for scene in eyecatches)
        or eyecatches != sorted(set(eyecatches))
        or any(scene <= 1 or scene > len(scenes) for scene in eyecatches)
    ):
        errors.append(
            "eyecatch_before_scenes は話題転換前のシーン番号を昇順で1〜2個指定する"
        )
    return errors


def _validate_slide_fields(i: int, slide: dict, template: str) -> list[str]:
    """描画で例外になる欠損を、AI再生成可能な検証エラーへ変換する。"""
    errors = []
    for field in TEMPLATE_FIELDS[template]:
        value = slide.get(field)
        if isinstance(value, list):
            valid = bool(value) and all(
                isinstance(item, str) and item.strip() for item in value
            )
        else:
            valid = isinstance(value, str) and bool(value.strip())
        if not valid:
            errors.append(f"scene {i}: slide.{field} が空または不正")
    return errors


def _validate_reveal(i: int, slide: dict, lines: list[dict]) -> list[str]:
    """bullets/outro/compare の show_items (段階表示) を検証する。"""
    errors = []
    units = total_units(slide)
    previous = 0
    for j, line in enumerate(lines, 1):
        value = line.get("show_items")
        if not isinstance(value, int) or not 1 <= value <= units:
            errors.append(f"scene {i} line {j}: show_items が 1〜{units} の整数でない")
            return errors
        if value < previous:
            errors.append(f"scene {i} line {j}: show_items が減少している")
        previous = value
    if lines and previous != units:
        errors.append(f"scene {i}: 最後のセリフの show_items が総数 {units} でない")
    return errors


def _finalize(script: dict, source: SourceContent) -> None:
    """公開不要な情報を落とし、音声素材クレジットを強制付与する。"""
    _sanitize_generated_content(script, source.url)
    voice_credit = " / ".join(VOICEVOX_CREDITS)
    description = "\n".join(
        line
        for line in script["description"].splitlines()
        if line.strip() not in {voice_credit, f"効果音: {EYECATCH_AUDIO_CREDIT}"}
        and not _PUBLIC_SOURCE_URL_RE.search(line)
    ).rstrip()
    description = sanitize_public_text(description)
    description += f"\n\n{voice_credit}"
    description += f"\n効果音: {EYECATCH_AUDIO_CREDIT}"
    script["description"] = description
    total_chars = sum(
        len(line["text"]) for scene in script["scenes"] for line in scene["lines"]
    )
    logger.info(
        "台本生成完了: {} シーン, セリフ合計 {} 字, タイトル「{}」",
        len(script["scenes"]),
        total_chars,
        script["title"],
    )


def _sanitize_generated_content(value: object, source_url: str) -> object:
    """AIへの禁止指示だけに頼らず、公開用台本から入力元と個人情報を除く。"""
    if isinstance(value, str):
        return sanitize_public_text(value.replace(source_url, ""))
    if isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = _sanitize_generated_content(item, source_url)
        return value
    if isinstance(value, dict):
        for key, item in value.items():
            value[key] = _sanitize_generated_content(item, source_url)
        return value
    return value
