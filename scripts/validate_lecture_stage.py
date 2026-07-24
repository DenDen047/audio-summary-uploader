"""lectureの段階成果物を決定論的に検証する。"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from score_lecture import (  # noqa: E402
    EMOTION_MARKER_MIN,
    EMOTION_SCENE_COVERAGE_MIN,
    REPEATED_PHRASE_MAX,
    TEASE_COUNT_RANGE,
    evaluate,
    measure,
)

from lecture.script_gen import (  # noqa: E402
    _PUBLIC_BARE_LINK_RE,
    _PUBLIC_PEM_RE,
    _PUBLIC_SECRET_RE,
    _PUBLIC_URI_RE,
    EYECATCH_AUDIO_CREDIT,
    _validate,
)

PROMPTS_DIR = REPO_ROOT / "src" / "lecture" / "prompts"
SCHEMAS = {
    "understanding": PROMPTS_DIR / "lecture_source_understanding.schema.json",
    "outline": PROMPTS_DIR / "lecture_teaching_outline.schema.json",
    "draft": PROMPTS_DIR / "lecture_script.schema.json",
    "final": PROMPTS_DIR / "lecture_script.schema.json",
}
FILES = {
    "understanding": "source-understanding.json",
    "outline": "teaching-outline.json",
    "draft": "scene-draft.json",
    "final": "script.json",
}
CONTEXT_SOURCE_MARKERS = {
    "official": ("公式", "開発元"),
    "primary_research": ("研究", "論文", "査読"),
    "technical_analysis": ("技術解説", "技術記事", "分析"),
    "community_discussion": (
        "Hacker News",
        "コミュニティ",
        "掲示板",
        "投稿",
    ),
}
COMMUNITY_LIMITATION_PATTERNS = (
    re.compile(
        r"(?:全体|一般|世論|利用者|読者|視聴者).{0,12}"
        r"(?:代表(?:しない(?!わけ|とは)|しません|していない(?!わけ|とは)"
        r"|していません|ではない(?!わけ|とは)|ではありません)"
        r"|示すものではない|示すものではありません|とは限らない|とは限りません)"
    ),
    re.compile(
        r"(?:一部|少数|限られた)(?:の)?(?:投稿|意見|利用者|反応|人)"
        r"(?:だけ|です|に限られ|にとどまり)"
    ),
    re.compile(
        r"(?:個人|投稿者)(?:の)?(?:意見|感想|見解)"
        r"(?:です|に限られ|にとどまり)"
    ),
    re.compile(r"(?:偏り|バイアス)(?:が|を)?(?:ある|あります|含む|含みます)"),
)
COMMUNITY_LIMITATION_REVERSALS = (
    "しないとは限",
    "しないわけでは",
    "していないとは限",
    "していないわけでは",
    "ではないとは限",
    "ではないわけでは",
    "という主張は誤",
    "という見方は誤",
)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")


def _states_community_limitation(text: str) -> bool:
    """コミュニティ反応の非代表性を肯定的に限定する文だけを認める。"""
    if any(reversal in text for reversal in COMMUNITY_LIMITATION_REVERSALS):
        return False
    return any(pattern.search(text) for pattern in COMMUNITY_LIMITATION_PATTERNS)


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return type(value) is int
    if expected == "number":
        return type(value) in {int, float}
    if expected == "boolean":
        return type(value) is bool
    return True


def _schema_errors(value: Any, schema: dict, path: str = "$") -> list[str]:
    errors: list[str] = []
    expected = schema.get("type")
    expected_types = expected if isinstance(expected, list) else [expected]
    if expected and not any(_matches_type(value, item) for item in expected_types):
        return [f"{path}: 型が{expected}ではない"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: 許可値ではない")
    if isinstance(value, str) and len(value) > schema.get("maxLength", len(value)):
        errors.append(f"{path}: {schema['maxLength']}文字を超えている")
    if type(value) is int:
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {schema['minimum']}未満")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {schema['maximum']}を超えている")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", len(value)):
            errors.append(f"{path}: 要素数が{schema['minItems']}未満")
        if len(value) > schema.get("maxItems", len(value)):
            errors.append(f"{path}: 要素数が{schema['maxItems']}を超えている")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(_schema_errors(item, item_schema, f"{path}[{index}]"))
    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}.{key}: 必須キーがない")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value.keys() - properties.keys():
                errors.append(f"{path}.{key}: 未定義キー")
        for key, child_schema in properties.items():
            if key in value:
                errors.extend(
                    _schema_errors(value[key], child_schema, f"{path}.{key}")
                )
    return errors


def _load_json(path: Path) -> tuple[dict | None, list[str]]:
    if not path.is_file():
        return None, [f"{path.name}: ファイルがない"]
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        return None, [f"{path.name}: JSON不正 ({error})"]
    if not isinstance(value, dict):
        return None, [f"{path.name}: トップレベルはobjectにする"]
    return value, []


def _safety_errors(value: dict, filename: str) -> list[str]:
    serialized = json.dumps(value, ensure_ascii=False)
    if filename == FILES["final"]:
        serialized = serialized.replace(EYECATCH_AUDIO_CREDIT, "OtoLogic")
    errors = []
    if _PUBLIC_URI_RE.search(serialized) or _PUBLIC_BARE_LINK_RE.search(serialized):
        errors.append(f"{filename}: URLを含めない")
    if EMAIL_RE.search(serialized):
        errors.append(f"{filename}: メールアドレスを含めない")
    if _PUBLIC_SECRET_RE.search(serialized) or _PUBLIC_PEM_RE.search(serialized):
        errors.append(f"{filename}: アクセストークンらしき値を含めない")
    return errors


def _generation_metadata_errors(value: Any) -> list[str]:
    """Pythonが最終化時に付ける非公開実行履歴の固定条件を検証する。"""
    if not isinstance(value, dict):
        return ["script.json: generationはobjectにする"]
    required = {
        "script_agent",
        "script_model_requested",
        "script_models_used",
        "primary",
        "review",
        "stages",
        "metered_api",
        "quality_mode",
    }
    errors = []
    if value.keys() != required:
        errors.append("script.json: generationのキーが固定仕様と一致しない")
    if value.get("metered_api") is not False:
        errors.append("script.json: generation.metered_apiはfalseにする")
    stages = value.get("stages")
    if not isinstance(stages, list) or len(stages) != 4:
        errors.append("script.json: generation.stagesは4工程にする")
        return errors
    expected_roles = (
        "source-understanding-and-research",
        "teaching-order-planning",
        "scene-writing",
        "teaching-review-and-repair",
    )
    for index, (stage, role) in enumerate(zip(stages, expected_roles), 1):
        if not isinstance(stage, dict):
            errors.append(f"script.json: generation.stages[{index}]はobjectにする")
            continue
        if stage.get("agent") != "claude-code-cli":
            errors.append(
                f"script.json: generation.stages[{index}].agentが不正"
            )
        if stage.get("authentication") != "claude-max-subscription":
            errors.append(
                f"script.json: generation.stages[{index}].authenticationが不正"
            )
        if stage.get("role") != role:
            errors.append(
                f"script.json: generation.stages[{index}].roleが不正"
            )
    return errors


def _understanding_errors(value: dict) -> list[str]:
    claims = value.get("major_claims", [])
    ids = [claim.get("id") for claim in claims if isinstance(claim, dict)]
    errors = []
    if len(ids) != len(set(ids)):
        errors.append("source-understanding.json: major_claimsのidが重複")
    if any(
        not isinstance(claim_id, str) or not re.fullmatch(r"C\d+", claim_id)
        for claim_id in ids
    ):
        errors.append("source-understanding.json: major_claimsのidはC1形式にする")
    research = value.get("research", {})
    materials = (
        research.get("related_materials", [])
        if isinstance(research, dict)
        else []
    )
    context_ids = [
        material.get("id")
        for material in materials
        if isinstance(material, dict)
    ]
    if len(context_ids) != len(set(context_ids)):
        errors.append("source-understanding.json: related_materialsのidが重複")
    if any(
        not isinstance(context_id, str)
        or not re.fullmatch(r"R\d+", context_id)
        for context_id in context_ids
    ):
        errors.append(
            "source-understanding.json: related_materialsのidはR1形式にする"
        )
    if (
        isinstance(research, dict)
        and research.get("status") == "completed"
        and not context_ids
    ):
        errors.append(
            "source-understanding.json: research.status=completedなら"
            "related_materialsを1件以上にする"
        )
    if isinstance(research, dict) and research.get("status") == "completed":
        search_queries = research.get("search_queries", [])
        if not isinstance(search_queries, list) or not 2 <= len(search_queries) <= 4:
            errors.append(
                "source-understanding.json: research.status=completedなら"
                "search_queriesを2〜4件にする"
            )
    if (
        isinstance(research, dict)
        and research.get("status") == "unavailable"
        and not research.get("notes")
    ):
        errors.append(
            "source-understanding.json: research.status=unavailableなら"
            "notesへ理由を書く"
        )
    for index, material in enumerate(materials, 1):
        if not isinstance(material, dict):
            continue
        if material.get("material_type") != "community_discussion":
            continue
        if material.get("evidential_role") != "reception":
            errors.append(
                "source-understanding.json: community_discussion "
                f"{index}はevidential_role=receptionにする"
            )
        caveat = material.get("caveat")
        if not isinstance(caveat, str) or not caveat.strip():
            errors.append(
                "source-understanding.json: community_discussion "
                f"{index}は代表性の限界をcaveatへ書く"
            )
        elif not _states_community_limitation(caveat):
            errors.append(
                "source-understanding.json: community_discussion "
                f"{index}のcaveatで代表性を限定する"
            )
    return errors


def _outline_errors(work_dir: Path, value: dict) -> list[str]:
    understanding, load_errors = _load_json(work_dir / FILES["understanding"])
    if load_errors or understanding is None:
        return load_errors
    claim_ids = {
        claim["id"]
        for claim in understanding.get("major_claims", [])
        if isinstance(claim, dict) and isinstance(claim.get("id"), str)
    }
    research = understanding.get("research", {})
    related_materials = (
        research.get("related_materials", [])
        if isinstance(research, dict)
        else []
    )
    context_ids = {
        material["id"]
        for material in related_materials
        if isinstance(material, dict) and isinstance(material.get("id"), str)
    }
    scenes = value.get("scenes", [])
    errors = []
    numbers = [
        scene.get("scene_number") for scene in scenes if isinstance(scene, dict)
    ]
    if numbers != list(range(1, len(scenes) + 1)):
        errors.append("teaching-outline.json: scene_numberを1から連番にする")
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            continue
        scene_claim_ids = scene.get("claim_ids", [])
        scene_context_ids = scene.get("context_ids", [])
        if not isinstance(scene_claim_ids, list) or not all(
            isinstance(item, str) for item in scene_claim_ids
        ):
            continue
        if not isinstance(scene_context_ids, list) or not all(
            isinstance(item, str) for item in scene_context_ids
        ):
            continue
        if len(scene_claim_ids) != len(set(scene_claim_ids)):
            errors.append(
                f"teaching-outline.json scene {index}: claim_idsが重複"
            )
        if len(scene_context_ids) != len(set(scene_context_ids)):
            errors.append(
                f"teaching-outline.json scene {index}: context_idsが重複"
            )
        unknown = set(scene_claim_ids) - claim_ids
        if unknown:
            errors.append(
                f"teaching-outline.json scene {index}: "
                f"未知のclaim_ids {sorted(unknown)}"
            )
        unknown_context = set(scene_context_ids) - context_ids
        if unknown_context:
            errors.append(
                f"teaching-outline.json scene {index}: "
                f"未知のcontext_ids {sorted(unknown_context)}"
            )
    return errors


def _script_provenance_errors(work_dir: Path, value: dict) -> list[str]:
    understanding, understanding_errors = _load_json(
        work_dir / FILES["understanding"]
    )
    outline, outline_errors = _load_json(work_dir / FILES["outline"])
    if understanding_errors or understanding is None:
        return understanding_errors
    if outline_errors or outline is None:
        return outline_errors

    known_claims = {
        claim["id"]
        for claim in understanding.get("major_claims", [])
        if isinstance(claim, dict) and isinstance(claim.get("id"), str)
    }
    research = understanding.get("research", {})
    context_by_id = {
        material["id"]: material
        for material in (
            research.get("related_materials", [])
            if isinstance(research, dict)
            else []
        )
        if isinstance(material, dict) and isinstance(material.get("id"), str)
    }
    known_context = set(context_by_id)
    scenes = value.get("scenes", [])
    outline_scenes = outline.get("scenes", [])
    if not isinstance(scenes, list) or not isinstance(outline_scenes, list):
        return []

    errors = []
    if len(scenes) != len(outline_scenes):
        errors.append(
            f"台本のシーン数{len(scenes)}を教える順番の"
            f"{len(outline_scenes)}シーンと一致させる"
        )
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict):
            continue
        claim_ids = scene.get("claim_ids", [])
        context_ids = scene.get("context_ids", [])
        if not isinstance(claim_ids, list) or not all(
            isinstance(item, str) for item in claim_ids
        ):
            continue
        if not isinstance(context_ids, list) or not all(
            isinstance(item, str) for item in context_ids
        ):
            continue
        if len(claim_ids) != len(set(claim_ids)):
            errors.append(f"scene {index}: claim_idsが重複")
        if len(context_ids) != len(set(context_ids)):
            errors.append(f"scene {index}: context_idsが重複")
        unknown_claims = set(claim_ids) - known_claims
        unknown_context = set(context_ids) - known_context
        if unknown_claims:
            errors.append(
                f"scene {index}: 未知のclaim_ids {sorted(unknown_claims)}"
            )
        if unknown_context:
            errors.append(
                f"scene {index}: 未知のcontext_ids {sorted(unknown_context)}"
            )
        if index > len(outline_scenes):
            continue
        outline_scene = outline_scenes[index - 1]
        if not isinstance(outline_scene, dict):
            continue
        if claim_ids != outline_scene.get("claim_ids", []):
            errors.append(
                f"scene {index}: claim_idsを教える順番から変えない"
            )
        if context_ids != outline_scene.get("context_ids", []):
            errors.append(
                f"scene {index}: context_idsを教える順番から変えない"
            )
        disclosures = scene.get("context_disclosures", [])
        if not isinstance(disclosures, list) or not all(
            isinstance(item, dict) for item in disclosures
        ):
            continue
        disclosure_ids = [
            disclosure.get("context_id") for disclosure in disclosures
        ]
        if disclosure_ids != context_ids:
            errors.append(
                f"scene {index}: context_disclosuresをcontext_idsと同順にする"
            )
        spoken_lines = [
            line.get("text", "")
            for line in scene.get("lines", [])
            if isinstance(line, dict) and isinstance(line.get("text"), str)
        ]
        for disclosure in disclosures:
            context_id = disclosure.get("context_id")
            material = context_by_id.get(context_id)
            if material is None:
                continue
            material_type = disclosure.get("material_type")
            if material_type != material.get("material_type"):
                errors.append(
                    f"scene {index}: {context_id}のmaterial_typeを"
                    "資料理解と一致させる"
                )
                continue
            source_text = disclosure.get("source_text")
            if not isinstance(source_text, str) or not source_text.strip():
                errors.append(
                    f"scene {index}: {context_id}のsource_textを書く"
                )
            elif not any(source_text in line for line in spoken_lines):
                errors.append(
                    f"scene {index}: {context_id}のsource_textを実際に発話する"
                )
            elif not any(
                marker in source_text
                for marker in CONTEXT_SOURCE_MARKERS.get(material_type, ())
            ):
                errors.append(
                    f"scene {index}: {context_id}のsource_textで"
                    "情報種別を明示する"
                )
            limitation_text = disclosure.get("limitation_text")
            if material_type == "community_discussion":
                if (
                    not isinstance(limitation_text, str)
                    or not limitation_text.strip()
                ):
                    errors.append(
                        f"scene {index}: {context_id}の代表性の限界を書く"
                    )
                    continue
                if not any(limitation_text in line for line in spoken_lines):
                    errors.append(
                        f"scene {index}: {context_id}の代表性の限界を"
                        "実際に発話する"
                    )
                if not _states_community_limitation(limitation_text):
                    errors.append(
                        f"scene {index}: {context_id}のlimitation_textで"
                        "代表性の限界を明示する"
                    )
            elif (
                isinstance(limitation_text, str)
                and limitation_text
                and not any(limitation_text in line for line in spoken_lines)
            ):
                errors.append(
                    f"scene {index}: {context_id}のlimitation_textを"
                    "実際に発話する"
                )
    return errors


def _script_errors(
    work_dir: Path,
    value: dict,
    stage: str,
) -> tuple[list[str], dict]:
    run_input, load_errors = _load_json(work_dir / "run-input.json")
    if load_errors or run_input is None:
        return load_errors, {}
    figures = run_input.get("figures", [])
    figure_count = len(figures) if isinstance(figures, list) else 0
    errors = _validate(value, available_figure_count=figure_count)
    errors.extend(_script_provenance_errors(work_dir, value))
    metrics = measure(value) if not errors else {}
    if metrics:
        failed_rows = [
            row
            for row in evaluate(metrics)
            if row["id"] != "M7" and row["mark"] != "○"
        ]
        errors.extend(
            _machine_feedback_errors(FILES[stage], failed_rows, metrics)
        )
    return errors, metrics


def _machine_feedback_errors(
    filename: str,
    failed_rows: list[dict],
    metrics: dict,
) -> list[str]:
    """修正セッションへ実測・目標・採点規則に沿う最小修正を返す。"""
    errors = []
    for row in failed_rows:
        feedback = (
            f"{filename}: {row['id']} {row['label']}が未達 "
            f"(現在: {row['value']}; 目標: {row['target']})"
        )
        if row["id"] == "M4":
            actions = _m4_repair_actions(metrics)
        elif row["id"] == "M5":
            actions = _m5_repair_actions(metrics)
        elif row["id"] == "M8":
            excess = max(
                0,
                len(metrics.get("m8_repeated_phrases", []))
                - REPEATED_PHRASE_MAX,
            )
            phrases = "、".join(
                f"「{phrase}」"
                for phrase in metrics.get("m8_repeated_phrases", [])
            )
            actions = (
                f"列挙された反復を別表現にして検出件数を最低{excess}件減らす。"
                f"対象: {phrases}"
            )
        else:
            actions = "現在値と目標の差だけを直し、合格済みの指標を維持する。"
        errors.append(f"{feedback}。修正: {actions}")
    return errors


def _m4_repair_actions(metrics: dict) -> str:
    """M4の複合条件から、未達の下位条件だけを修正指示にする。"""
    actions = []
    world_deficit = max(
        0,
        metrics["m4_world_events_min"] - metrics["m4_world_events"],
    )
    tease_min, tease_max = TEASE_COUNT_RANGE
    tease_count = metrics["m4_tease_count"]
    if world_deficit:
        if tease_count < tease_min:
            actions.append(
                f"内容に結びつくtease行を最低{tease_min - tease_count}回追加する"
            )
        else:
            actions.append(
                "採点対象の褒め照れを最低"
                f"{world_deficit}組追加する。同じ場面でmetan_pose=praiseの"
                "発話直後にzunda_pose=praisedまたはshyの発話を置く"
            )
    if tease_count > tease_max:
        actions.append(
            f"tease行を最低{tease_count - tease_max}回、別の自然な反応へ直す"
        )
    if metrics["m4_calls_to_mio"] < 1:
        actions.append("透の発話へ「澪先生」を最低1回入れる")
    if metrics["m4_calls_to_toru"] < 1:
        actions.append("澪の発話へ「透くん」を最低1回入れる")
    actions.append("合格済みの世界観イベントと名前呼びは削らない")
    return "。".join(actions)


def _m5_repair_actions(metrics: dict) -> str:
    """M5の個数と場面分布を、同じ自然な反応で満たせる不足数へ分解する。"""
    marker_deficit = max(
        0,
        EMOTION_MARKER_MIN - metrics["m5_marker_count"],
    )
    required_scenes = math.ceil(
        metrics["scene_count"] * EMOTION_SCENE_COVERAGE_MIN
    )
    scene_deficit = max(
        0,
        required_scenes - metrics["m5_marker_scene_count"],
    )
    actions = []
    if marker_deficit:
        actions.append(
            "説明内容に沿う驚き・言い淀み・笑いなどの感情マーカーを最低"
            f"{marker_deficit}個増やす"
        )
    if scene_deficit:
        actions.append(
            f"そのうち最低{scene_deficit}個はマーカー未配置の別場面へ置く"
        )
    else:
        actions.append("半数以上の場面への分布は達成済みなので維持する")
    actions.append("同じ間投詞を機械的に反復しない")
    return "。".join(actions)


def validate_stage(work_dir: Path, stage: str) -> dict:
    filename = FILES[stage]
    value, errors = _load_json(work_dir / filename)
    metrics: dict = {}
    if value is not None:
        schema = json.loads(SCHEMAS[stage].read_text(encoding="utf-8"))
        schema_value = value
        if stage == "final" and "generation" in value:
            schema_value = {
                key: item for key, item in value.items() if key != "generation"
            }
            errors.extend(_generation_metadata_errors(value["generation"]))
        errors.extend(_schema_errors(schema_value, schema))
        errors.extend(_safety_errors(value, filename))
        if stage == "understanding":
            errors.extend(_understanding_errors(value))
        elif stage == "outline":
            errors.extend(_outline_errors(work_dir, value))
        else:
            script_errors, metrics = _script_errors(work_dir, value, stage)
            errors.extend(script_errors)
    return {
        "stage": stage,
        "file": filename,
        "passed": not errors,
        "errors": errors,
        "metrics": metrics,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("work_dir", type=Path)
    parser.add_argument(
        "--stage",
        choices=tuple(FILES),
        required=True,
    )
    args = parser.parse_args()
    result = validate_stage(args.work_dir.resolve(), args.stage)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
