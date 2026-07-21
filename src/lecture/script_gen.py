"""claude -p (headless) で台本 JSON を生成する。定額サブスク内で完結させる。"""

import json
import shutil
import subprocess
from pathlib import Path

from loguru import logger

from lecture.fetch import SourceContent
from lecture.reveal import REVEAL_TEMPLATES, total_units

PROMPT_PATH = Path(__file__).parent / "prompts" / "lecture_script.md"
TEMPLATES = {"title", "bullets", "compare", "code", "quote", "outro"}
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


def generate_script(source: SourceContent) -> dict:
    if shutil.which("claude") is None:
        raise RuntimeError(
            "claude CLI が見つからない。Claude Code をインストールすること"
        )

    prompt = (
        PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{{TITLE}}", source.title)
        .replace("{{URL}}", source.url)
        .replace("{{TEXT}}", source.text)
    )

    script = _try_generate(prompt)
    errors = _validate(script)
    if errors:
        logger.warning("台本の検証エラー、1 回だけ再生成する: {}", errors)
        retry_prompt = (
            prompt
            + "\n\n# 前回の出力の問題点（修正すること）\n\n"
            + "\n".join(f"- {e}" for e in errors)
        )
        script = _try_generate(retry_prompt)
        errors = _validate(script)
        if errors:
            raise RuntimeError(f"再生成後も台本が不正: {errors}")

    _finalize(script, source)
    return script


def _try_generate(prompt: str) -> dict:
    logger.info("claude -p で台本を生成中 (プロンプト {} 文字)...", len(prompt))
    result = subprocess.run(
        ["claude", "-p", "--output-format", "text"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=GENERATION_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude -p が失敗 (exit {result.returncode}): {result.stderr[:500]}"
        )
    return _parse_json(result.stdout)


def _parse_json(raw: str) -> dict:
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end <= start:
        raise RuntimeError(f"出力に JSON が含まれない: {raw[:300]}")
    return json.loads(raw[start : end + 1])


def _validate(script: dict) -> list[str]:
    errors = []
    for key in ("title", "description", "scenes"):
        if key not in script:
            errors.append(f"必須キー {key} がない")
    scenes = script.get("scenes", [])
    if not isinstance(scenes, list) or len(scenes) < 3:
        errors.append(f"scenes が少なすぎる ({len(scenes)} 個)")
        return errors

    for i, scene in enumerate(scenes, 1):
        slide = scene.get("slide", {})
        template = slide.get("template")
        if template not in TEMPLATES:
            errors.append(f"scene {i}: 不明な template '{template}'")
        background_mood = slide.get("background_mood")
        if background_mood not in {"explain", "safety", "warm"}:
            errors.append(
                f"scene {i}: background_mood '{background_mood}' が不正"
            )
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
        if template in REVEAL_TEMPLATES:
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
    """出典 URL と音声素材クレジットを description に強制付与する。"""
    description = script["description"].rstrip()
    if source.url not in description:
        description += f"\n\n出典: {source.url}"
    description += "\n\n" + " / ".join(VOICEVOX_CREDITS)
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
