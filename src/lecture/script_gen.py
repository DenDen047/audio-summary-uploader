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
from lecture.reveal import (
    REVEAL_TEMPLATES,
    normalize_reveal_counts,
    total_units,
)
from lecture.thumbnail_backdrop import THUMBNAIL_VISUAL_PROMPT_MAX_CHARS

PROMPT_PATH = Path(__file__).parent / "prompts" / "lecture_script.md"
REVIEW_PROMPT_PATH = Path(__file__).parent / "prompts" / "lecture_script_review.md"
SCHEMA_PATH = Path(__file__).parent / "prompts" / "lecture_script.schema.json"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CLAUDE_MODEL = "opus"
DEFAULT_CLAUDE_EFFORT = "xhigh"
DEFAULT_CODEX_MODEL = "gpt-5.6-sol"
DEFAULT_CODEX_EFFORT = "xhigh"
TEMPLATES = {
    "title",
    "bullets",
    "compare",
    "code",
    "quote",
    "diagram",
    "figure",
    "outro",
}
DIAGRAM_TYPES = {
    "flow",
    "tree",
    "layers",
    "timeline",
    "cycle",
    "matrix",
    "table",
}
TEMPLATE_FIELDS = {
    "title": ("subheading", "source_label"),
    "bullets": ("items",),
    "compare": ("left_title", "left_items", "right_title", "right_items"),
    "code": ("code", "caption"),
    "quote": ("quote", "attribution"),
    "diagram": ("diagram_type", "items"),
    "figure": ("figure_index", "caption", "attribution"),
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
GENERATION_TIMEOUT_SECONDS = 3600
_PUBLIC_SOURCE_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_POLITE_SENTENCE_END_RE = re.compile(
    r"(?:です|ます|ません|ました|ましょう|でしょう|ください|ございます|"
    r"おります|いたします)(?:か|ね|よ|よね|かね)?$"
)
_POLITE_INTERJECTIONS = {"はい", "ええ", "ありがとうございます", "すみません"}


def generate_script(
    source: SourceContent,
    *,
    model: str = DEFAULT_CLAUDE_MODEL,
    effort: str = DEFAULT_CLAUDE_EFFORT,
    review_model: str = DEFAULT_CODEX_MODEL,
    review_effort: str = DEFAULT_CODEX_EFFORT,
    generation_timeout_seconds: int = GENERATION_TIMEOUT_SECONDS,
) -> dict:
    _assert_subscription_auth()

    prompt = (
        PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{{TITLE}}", source.title)
        .replace("{{TEXT}}", source.text)
        .replace("{{FIGURES}}", _source_figures_prompt(source))
    )

    script, primary_metadata = _generate_with_claude(
        prompt,
        model,
        effort,
        timeout_seconds=generation_timeout_seconds,
    )
    _normalize_generated_reveals(script, "Claude初稿")
    errors = _validate(script, available_figure_count=len(source.figures))
    if errors:
        logger.warning("Claude初稿の検証エラー、1回だけ再生成する: {}", errors)
        retry_prompt = _build_claude_retry_prompt(prompt, script, errors)
        script, primary_metadata = _generate_with_claude(
            retry_prompt,
            model,
            effort,
            timeout_seconds=generation_timeout_seconds,
        )
        _normalize_generated_reveals(script, "Claude再生成")
        errors = _validate(script, available_figure_count=len(source.figures))
        if errors:
            logger.warning(
                "Claude再生成後に残った検証エラーをCodex審査へ引き継ぐ: {}",
                errors,
            )

    script, review_metadata = _review_with_codex(
        source,
        script,
        review_model,
        review_effort,
        timeout_seconds=generation_timeout_seconds,
        errors=errors or None,
    )
    _normalize_generated_reveals(script, "Codex審査")
    errors = _validate(script, available_figure_count=len(source.figures))
    if errors:
        logger.warning("Codex審査後の検証エラー、1回だけ再審査する: {}", errors)
        script, review_metadata = _review_with_codex(
            source,
            script,
            review_model,
            review_effort,
            timeout_seconds=generation_timeout_seconds,
            errors=errors,
        )
        _normalize_generated_reveals(script, "Codex再審査")
        errors = _validate(script, available_figure_count=len(source.figures))
        if errors:
            raise RuntimeError(f"Codex再審査後も台本が不正: {errors}")

    script["generation"] = _generation_metadata(
        primary_metadata, review_metadata
    )
    _finalize(script, source)
    return script


def _build_claude_retry_prompt(
    prompt: str,
    script: dict,
    errors: list[str],
) -> str:
    """失敗台本を保持したまま、指摘箇所だけ直す再生成プロンプトを作る。"""
    return (
        prompt
        + "\n\n# 前回の出力の問題点（すべて修正すること）\n\n"
        + "\n".join(f"- {error}" for error in errors)
        + "\n\n# 前回の台本JSON\n\n"
        + json.dumps(script, ensure_ascii=False, indent=2)
        + "\n\n問題のない箇所は維持し、修正後の完全なJSONを返してください。"
    )


def _normalize_generated_reveals(script: dict, stage: str) -> None:
    """表示番号は機械的状態なので、全文再生成より限定修復を優先する。"""
    repaired = normalize_reveal_counts(script)
    if repaired:
        logger.warning("{}のshow_itemsを機械修復: {}件", stage, repaired)


def _generate_with_claude(
    prompt: str,
    model: str,
    effort: str,
    *,
    timeout_seconds: int = GENERATION_TIMEOUT_SECONDS,
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
        timeout=timeout_seconds,
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
    timeout_seconds: int = GENERATION_TIMEOUT_SECONDS,
    errors: list[str] | None = None,
) -> tuple[dict, dict]:
    prompt = (
        REVIEW_PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{{TITLE}}", source.title)
        .replace("{{TEXT}}", source.text)
        .replace("{{FIGURES}}", _source_figures_prompt(source))
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
            timeout=timeout_seconds,
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


def _source_figures_prompt(source: SourceContent) -> str:
    """画像URLを公開せず、AIが一次資料の図を番号で選べる一覧を作る。"""
    if not source.figures:
        return "利用可能な図はありません。"
    return "\n".join(
        f"{index}. {figure.caption}"
        for index, figure in enumerate(source.figures, 1)
    )


def _validate(
    script: dict,
    *,
    available_figure_count: int | None = None,
) -> list[str]:
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
    if not isinstance(scenes, list):
        errors.append("scenes は8〜14個の配列にする")
        return errors
    if not scenes:
        errors.append("scenes は8〜14個にする (現在0個)")
        return errors
    if not 8 <= len(scenes) <= 14:
        errors.append(f"scenes は8〜14個にする (現在{len(scenes)}個)")

    for i, scene in enumerate(scenes, 1):
        slide = scene.get("slide", {})
        template = slide.get("template")
        if template not in TEMPLATES:
            errors.append(f"scene {i}: 不明な template '{template}'")
        else:
            errors.extend(_validate_slide_fields(i, slide, template))
            if template == "diagram":
                errors.extend(_validate_diagram(i, slide))
            if template == "figure" and available_figure_count is not None:
                figure_index = slide.get("figure_index")
                if (
                    type(figure_index) is int
                    and figure_index > available_figure_count
                ):
                    errors.append(
                        f"scene {i}: figure_index {figure_index} は利用可能な図 "
                        f"{available_figure_count} 件の範囲外"
                    )
        background_mood = slide.get("background_mood")
        if background_mood not in {"explain", "safety", "warm"}:
            errors.append(f"scene {i}: background_mood '{background_mood}' が不正")
        if not slide.get("heading"):
            errors.append(f"scene {i}: heading がない")
        lines = scene.get("lines", [])
        if not isinstance(lines, list):
            errors.append(f"scene {i}: lines は2〜6個の配列にする")
            lines = []
        elif not 2 <= len(lines) <= 6:
            errors.append(
                f"scene {i}: lines は2〜6個にする (現在{len(lines)}個)"
            )
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

    errors.extend(_validate_world(scenes))

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


def _validate_world(scenes: list[dict]) -> list[str]:
    """澪と透の固定世界観を、生成モデルの解釈だけに委ねず検証する。"""
    errors = []
    first_lines = scenes[0].get("lines", []) if scenes else []
    if first_lines:
        first = first_lines[0]
        if (
            first.get("speaker") != "zunda"
            or first.get("zunda_pose") not in {"confused", "flustered"}
        ):
            errors.append(
                "scene 1 line 1: 導入は困っている透の相談から始める"
            )
        elif "澪先生" not in first.get("text", ""):
            errors.append(
                "scene 1 line 1: 透は『澪先生』と呼びかけて相談を始める"
            )
        if not any(
            line.get("speaker") == "metan" for line in first_lines[1:]
        ):
            errors.append("scene 1: 透の相談を受け止める澪の返答が必要")

    for i, scene in enumerate(scenes, 1):
        for j, line in enumerate(scene.get("lines", []), 1):
            speaker = line.get("speaker")
            text = line.get("text", "")
            if not isinstance(text, str) or not text.strip():
                continue
            reading = line.get("reading")
            utterances = [text]
            if isinstance(reading, str) and reading.strip():
                utterances.append(reading)
            polite = all(_is_polite_utterance(value) for value in utterances)
            if speaker == "zunda" and not polite:
                errors.append(f"scene {i} line {j}: 透は常に敬語で話す")
            if speaker != "metan":
                continue
            teasing = line.get("metan_pose") == "tease"
            if teasing and polite:
                errors.append(
                    f"scene {i} line {j}: 澪がからかう時は敬語を使わない"
                )
            elif not teasing and not polite:
                errors.append(
                    f"scene {i} line {j}: 澪の非敬語はからかう時だけにする"
                )
    return errors


def _is_polite_utterance(text: str) -> bool:
    """各文末が敬語かを保守的に判定し、明白な口調崩れだけを弾く。"""
    # 引用内の常体は話者本人の口調ではないため、文末判定から外す。
    spoken = re.sub(r"「[^」]*」|『[^』]*』", "引用", text)
    sentences = [
        sentence.strip(" \t\n\r」』）)]…")
        for sentence in re.split(r"[。！？!?]+", spoken)
        if sentence.strip(" \t\n\r」』）)]…")
    ]
    return bool(sentences) and all(
        sentence in _POLITE_INTERJECTIONS
        or _POLITE_SENTENCE_END_RE.search(sentence) is not None
        for sentence in sentences
    )


def _validate_slide_fields(i: int, slide: dict, template: str) -> list[str]:
    """描画で例外になる欠損を、AI再生成可能な検証エラーへ変換する。"""
    errors = []
    for field in TEMPLATE_FIELDS[template]:
        value = slide.get(field)
        if field == "figure_index":
            valid = type(value) is int and value >= 1
        elif isinstance(value, list):
            valid = bool(value) and all(
                isinstance(item, str) and item.strip() for item in value
            )
        else:
            valid = isinstance(value, str) and bool(value.strip())
        if not valid:
            errors.append(f"scene {i}: slide.{field} が空または不正")
    return errors


def _validate_diagram(i: int, slide: dict) -> list[str]:
    """図型とノード数を固定し、HTMLテンプレートの意味を曖昧にしない。"""
    errors = []
    diagram_type = slide.get("diagram_type")
    items = slide.get("items")
    if diagram_type not in DIAGRAM_TYPES:
        errors.append(f"scene {i}: diagram_type '{diagram_type}' が不正")
    if not isinstance(items, list):
        return errors
    if not 2 <= len(items) <= 6:
        errors.append(f"scene {i}: diagram の items は2〜6個にする")
    if diagram_type == "matrix" and len(items) != 4:
        errors.append(f"scene {i}: matrix の items は4個にする")
    if diagram_type == "matrix":
        for field in ("left_title", "right_title"):
            value = slide.get(field)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"scene {i}: matrix では slide.{field} が必要")
        if any(isinstance(item, str) and "|" in item for item in items):
            errors.append(
                f"scene {i}: 定量比較は matrix ではなく table を使う"
            )
        if any(
            isinstance(item, str)
            and item.strip().startswith(("横軸", "縦軸"))
            for item in items
        ):
            errors.append(f"scene {i}: matrix の items に軸名を含めない")
    if diagram_type == "table":
        rows = [
            [cell.strip() for cell in item.split("|")]
            for item in items
            if isinstance(item, str)
        ]
        column_counts = {len(row) for row in rows}
        if (
            len(rows) != len(items)
            or len(column_counts) != 1
            or not 2 <= next(iter(column_counts), 0) <= 4
            or any(not cell for row in rows for cell in row)
        ):
            errors.append(
                f"scene {i}: table は同じ2〜4列を | で区切った行にする"
            )
    return errors


def _validate_reveal(i: int, slide: dict, lines: list[dict]) -> list[str]:
    """項目型スライドの show_items (段階表示) を検証する。"""
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
