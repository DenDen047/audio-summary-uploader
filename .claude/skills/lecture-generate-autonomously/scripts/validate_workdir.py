"""lecture自律生成の段階成果物を決定論的に検証する。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from score_lecture import evaluate, measure  # noqa: E402

from lecture.script_gen import _validate  # noqa: E402

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
PUBLIC_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
SECRET_RE = re.compile(
    r"(?:access[_-]?token|authorization|bearer)\s*[:=]\s*\S+",
    re.IGNORECASE,
)


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
    errors = []
    if PUBLIC_URL_RE.search(serialized):
        errors.append(f"{filename}: URLを含めない")
    if EMAIL_RE.search(serialized):
        errors.append(f"{filename}: メールアドレスを含めない")
    if SECRET_RE.search(serialized):
        errors.append(f"{filename}: アクセストークンらしき値を含めない")
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
        unknown = set(scene.get("claim_ids", [])) - claim_ids
        if unknown:
            errors.append(
                f"teaching-outline.json scene {index}: "
                f"未知のclaim_ids {sorted(unknown)}"
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
    metrics = measure(value) if not errors else {}
    if metrics:
        failed = [
            row["id"]
            for row in evaluate(metrics)
            if row["id"] != "M7" and row["mark"] != "○"
        ]
        if failed:
            errors.append(
                f"{FILES[stage]}: 機械指標が未達 ({', '.join(failed)})"
            )
    return errors, metrics


def validate_stage(work_dir: Path, stage: str) -> dict:
    filename = FILES[stage]
    value, errors = _load_json(work_dir / filename)
    metrics: dict = {}
    if value is not None:
        schema = json.loads(SCHEMAS[stage].read_text(encoding="utf-8"))
        errors.extend(_schema_errors(value, schema))
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
