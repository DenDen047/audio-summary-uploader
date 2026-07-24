"""講義台本の採点スクリプト（Layer 1 機械指標 + Layer 2 審査プロンプト生成）.

docs/lecture-scoring-rubric-2026-07-23.html の4層スコアカードのうち、
コードで決定論的に測れる Layer 1 (M1〜M8) を計測する。M3 は「〜のですね」型の
代理指標、M7 (未定義語) は機械化できないため LLM 審査 (Layer 2) へ委ねる。

使い方:
    uv run python scripts/score_lecture.py tmp/lecture/<job_id>
    uv run python scripts/score_lecture.py tmp/lecture/<job_id>/script.json --json
    uv run python scripts/score_lecture.py tmp/lecture/<job_id> \
        --emit-judge-prompt tmp/judge_prompt.md

--emit-judge-prompt は scripts/prompts/lecture_score_judge.md に台本・機械指標・
元資料 (job ディレクトリの source.txt があれば) を埋め込んだ Layer 2 審査
プロンプトを書き出す。Claude Max サブスク内で実行する例:
    claude -p --safe-mode --model opus --tools "" < tmp/judge_prompt.md
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 暫定しきい値 (丸め値)。3〜5本の実測後にここを調整する。
# 根拠: docs/lecture-scoring-rubric-2026-07-23.html §3
# ---------------------------------------------------------------------------
TOTAL_CHARS_RANGE = (3000, 4500)  # M1: specs/LECTURE_SPEC.md §3.2
ZUNDA_LINE_RATIO_RANGE = (0.35, 0.50)  # M2
PARAPHRASE_RATIO_MAX = 0.25  # M3 (代理指標)
TEASE_COUNT_RANGE = (1, 3)  # M4
EMOTION_MARKER_MIN = 8  # M5
EMOTION_SCENE_COVERAGE_MIN = 0.5  # M5: マーカーを含むシーンの割合
UNDERSTAND_RATIO_MAX = 0.50  # M6
POSE_VARIETY_MIN = 4  # M6: 自分の発話中に使うポーズ種類数
REPEATED_PHRASE_MAX = 1  # M8
REPEATED_PHRASE_MIN_LEN = 7  # M8: この長さ以上の一致だけ反復とみなす
# M8: 話題に必然の用語反復 (漢字・英数・カナ主体) と読点跨ぎの偶然一致を除き、
# 口癖的な反復に絞る
REPEATED_PHRASE_HIRAGANA_MIN = 0.5
REPEATED_PHRASE_EXCLUDE_RE = re.compile(r"[A-Za-z0-9ァ-ヶ、]")

# M3: 澪の説明の言い換え確認とみなす透の文末パターン (代理指標)
PARAPHRASE_RE = re.compile(r"のですね|ですね。?\s*$")
# M5: 感情マーカー (感嘆符・間投詞・言い淀み・声に出る笑い)
EMOTION_MARKER_RE = re.compile(
    r"[！!]|えっ|ええっ|うわ|わあ|おお、|あっ、|うっ、|そ、そ|ふふ|あはは|えへへ"
)
# M8: キャラの決まり文句・呼びかけ・通常の丁寧語尾は反復として数えない
REPEAT_ALLOWLIST = (
    "澪先生",
    "透くん",
    "とおるくん",
    "ではありません",
    "でしょうか",
    "ありがとうございます",
)

JUDGE_PROMPT_PATH = Path(__file__).parent / "prompts" / "lecture_score_judge.md"


def load_script(target: Path) -> dict:
    path = target / "script.json" if target.is_dir() else target
    if not path.is_file():
        raise FileNotFoundError(f"script.json が見つからない: {path}")
    return json.loads(path.read_text())


def flatten_lines(script: dict) -> list[dict]:
    lines = []
    for scene_no, scene in enumerate(script["scenes"], 1):
        for line in scene["lines"]:
            lines.append({**line, "scene": scene_no})
    return lines


def longest_common_substring(a: str, b: str) -> str:
    best = ""
    table = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                table[i][j] = table[i - 1][j - 1] + 1
                if table[i][j] > len(best):
                    best = a[i - table[i][j] : i]
    return best


def is_verbal_tic(phrase: str) -> bool:
    """話題由来の用語反復を除き、口癖的な言い回しの反復だけを残す."""
    if len(phrase) < REPEATED_PHRASE_MIN_LEN:
        return False
    if any(word in phrase for word in REPEAT_ALLOWLIST):
        return False
    if REPEATED_PHRASE_EXCLUDE_RE.search(phrase):
        return False
    hiragana = sum(1 for ch in phrase if "ぁ" <= ch <= "ん")
    return hiragana / len(phrase) > REPEATED_PHRASE_HIRAGANA_MIN


def find_repeated_phrases(lines: list[dict]) -> list[str]:
    found: dict[str, int] = {}
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            if lines[i]["speaker"] != lines[j]["speaker"]:
                continue
            phrase = longest_common_substring(
                lines[i]["text"], lines[j]["text"]
            )
            phrase = phrase.strip("、。！？!? 　")
            if not is_verbal_tic(phrase):
                continue
            found[phrase] = found.get(phrase, 0) + 1
    # 部分文字列として他の検出結果に含まれるものは除き、最長の反復だけ残す
    phrases = sorted(found, key=len, reverse=True)
    kept: list[str] = []
    for phrase in phrases:
        if not any(phrase in longer for longer in kept):
            kept.append(phrase)
    return kept


def measure(script: dict) -> dict:
    lines = flatten_lines(script)
    scenes = script["scenes"]
    texts = [line["text"] for line in lines]
    zunda = [line for line in lines if line["speaker"] == "zunda"]
    metan = [line for line in lines if line["speaker"] == "metan"]

    total_chars = sum(len(t) for t in texts)

    paraphrase = [line for line in zunda if PARAPHRASE_RE.search(line["text"])]

    tease = [line for line in metan if line.get("metan_pose") == "tease"]
    praise_pairs = []
    for scene_no, scene in enumerate(scenes, 1):
        scene_lines = scene["lines"]
        for a, b in zip(scene_lines, scene_lines[1:]):
            if (
                a["speaker"] == "metan"
                and a.get("metan_pose") == "praise"
                and b["speaker"] == "zunda"
                and b.get("zunda_pose") in {"shy", "praised"}
            ):
                praise_pairs.append(scene_no)
    calls_to_mio = sum(line["text"].count("澪先生") for line in zunda)
    calls_to_toru = sum(line["text"].count("透くん") for line in metan)
    world_events = len(tease) + len(praise_pairs)
    world_events_min = math.ceil(len(scenes) / 3)

    marker_hits = [
        line for line in lines if EMOTION_MARKER_RE.search(line["text"])
    ]
    marker_count = sum(
        len(EMOTION_MARKER_RE.findall(line["text"])) for line in lines
    )
    marker_scenes = {line["scene"] for line in marker_hits}

    understand = [
        line for line in zunda if line.get("zunda_pose") == "understand"
    ]
    zunda_poses = {line.get("zunda_pose") for line in zunda} - {None}
    metan_poses = {line.get("metan_pose") for line in metan} - {None}

    repeated = find_repeated_phrases(lines)

    return {
        "title": script.get("title", ""),
        "scene_count": len(scenes),
        "line_count": len(lines),
        "m1_total_chars": total_chars,
        "m2_zunda_lines": len(zunda),
        "m2_zunda_ratio": len(zunda) / len(lines),
        "m3_paraphrase_lines": len(paraphrase),
        "m3_paraphrase_ratio": len(paraphrase) / len(zunda) if zunda else 0.0,
        "m4_tease_count": len(tease),
        "m4_praise_pair_count": len(praise_pairs),
        "m4_world_events": world_events,
        "m4_world_events_min": world_events_min,
        "m4_calls_to_mio": calls_to_mio,
        "m4_calls_to_toru": calls_to_toru,
        "m5_marker_count": marker_count,
        "m5_marker_scene_count": len(marker_scenes),
        "m5_marker_scene_ratio": len(marker_scenes) / len(scenes),
        "m6_understand_ratio": len(understand) / len(zunda) if zunda else 0.0,
        "m6_zunda_pose_variety": len(zunda_poses),
        "m6_metan_pose_variety": len(metan_poses),
        "m8_repeated_phrases": repeated,
    }


def evaluate(m: dict) -> list[dict]:
    def row(mid: str, label: str, value: str, target: str, passed: bool | None):
        mark = {True: "○", False: "✗", None: "—"}[passed]
        return {
            "id": mid,
            "label": label,
            "value": value,
            "target": target,
            "mark": mark,
        }

    lo, hi = TOTAL_CHARS_RANGE
    rlo, rhi = ZUNDA_LINE_RATIO_RANGE
    tlo, thi = TEASE_COUNT_RANGE
    bidirectional = m["m4_calls_to_mio"] >= 1 and m["m4_calls_to_toru"] >= 1
    return [
        row(
            "M1",
            "セリフ総文字数",
            f"{m['m1_total_chars']:,}",
            f"{lo:,}〜{hi:,}",
            lo <= m["m1_total_chars"] <= hi,
        ),
        row(
            "M2",
            "透のセリフ比率",
            f"{m['m2_zunda_ratio']:.0%} ({m['m2_zunda_lines']}/{m['line_count']})",
            f"{rlo:.0%}〜{rhi:.0%}",
            rlo <= m["m2_zunda_ratio"] <= rhi,
        ),
        row(
            "M3",
            "透の言い換え確認率 (代理指標)",
            f"{m['m3_paraphrase_ratio']:.0%} "
            f"({m['m3_paraphrase_lines']}/{m['m2_zunda_lines']})",
            f"{PARAPHRASE_RATIO_MAX:.0%}以下",
            m["m3_paraphrase_ratio"] <= PARAPHRASE_RATIO_MAX,
        ),
        row(
            "M4",
            "世界観イベント数",
            f"{m['m4_world_events']}回 (tease {m['m4_tease_count']}・"
            f"褒め照れ {m['m4_praise_pair_count']})・"
            f"名前呼び 澪先生{m['m4_calls_to_mio']}/透くん{m['m4_calls_to_toru']}",
            f"{m['m4_world_events_min']}回以上・tease {tlo}〜{thi}・名前呼び双方向",
            m["m4_world_events"] >= m["m4_world_events_min"]
            and tlo <= m["m4_tease_count"] <= thi
            and bidirectional,
        ),
        row(
            "M5",
            "感情マーカー数",
            f"{m['m5_marker_count']}個・"
            f"シーン分布 {m['m5_marker_scene_ratio']:.0%}",
            f"{EMOTION_MARKER_MIN}個以上・"
            f"分布 {EMOTION_SCENE_COVERAGE_MIN:.0%}以上",
            m["m5_marker_count"] >= EMOTION_MARKER_MIN
            and m["m5_marker_scene_ratio"] >= EMOTION_SCENE_COVERAGE_MIN,
        ),
        row(
            "M6",
            "ポーズ多様性",
            f"understand {m['m6_understand_ratio']:.0%}・"
            f"透{m['m6_zunda_pose_variety']}種 澪{m['m6_metan_pose_variety']}種",
            f"understand {UNDERSTAND_RATIO_MAX:.0%}以下・各{POSE_VARIETY_MIN}種以上",
            m["m6_understand_ratio"] <= UNDERSTAND_RATIO_MAX
            and m["m6_zunda_pose_variety"] >= POSE_VARIETY_MIN
            and m["m6_metan_pose_variety"] >= POSE_VARIETY_MIN,
        ),
        row(
            "M7",
            "未定義語数",
            "要LLM審査",
            "0件",
            None,
        ),
        row(
            "M8",
            "定型フレーズ反復",
            f"{len(m['m8_repeated_phrases'])}件 "
            + "・".join(f"「{p}」" for p in m["m8_repeated_phrases"][:5]),
            f"{REPEATED_PHRASE_MAX}件以下",
            len(m["m8_repeated_phrases"]) <= REPEATED_PHRASE_MAX,
        ),
    ]


def render_report(m: dict, rows: list[dict]) -> str:
    lines = [
        f"台本: {m['title']}",
        f"シーン {m['scene_count']}・セリフ {m['line_count']}",
        "",
        f"{'':2} {'#':3} {'指標':<18} 実測 / 目標",
        "-" * 72,
    ]
    for r in rows:
        lines.append(f"{r['mark']:2} {r['id']:3} {r['label']}")
        lines.append(f"{'':6}実測: {r['value']}")
        lines.append(f"{'':6}目標: {r['target']}")
    failed = [r["id"] for r in rows if r["mark"] == "✗"]
    lines.append("-" * 72)
    lines.append(f"未達: {', '.join(failed) if failed else 'なし'}")
    lines.append(
        "M7 (未定義語) と Layer 2 の7軸採点は --emit-judge-prompt で審査する。"
    )
    return "\n".join(lines)


def emit_judge_prompt(target: Path, script: dict, m: dict, out: Path) -> None:
    if not JUDGE_PROMPT_PATH.is_file():
        raise FileNotFoundError(f"審査プロンプトが無い: {JUDGE_PROMPT_PATH}")
    source_path = (target if target.is_dir() else target.parent) / "source.txt"
    if source_path.is_file():
        source = source_path.read_text()[:40000]
    else:
        source = "（元資料なし — R1 は参考判定とし、fail にはしない）"
    prompt = (
        JUDGE_PROMPT_PATH.read_text()
        .replace("{{SCRIPT}}", json.dumps(script, ensure_ascii=False, indent=1))
        .replace("{{METRICS}}", json.dumps(m, ensure_ascii=False, indent=1))
        .replace("{{SOURCE}}", source)
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(prompt)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path, help="job ディレクトリか script.json")
    parser.add_argument("--json", action="store_true", help="機械指標をJSONで出力")
    parser.add_argument(
        "--emit-judge-prompt",
        type=Path,
        metavar="OUT_MD",
        help="Layer 2 審査プロンプトを書き出す",
    )
    args = parser.parse_args()

    script = load_script(args.target)
    m = measure(script)
    rows = evaluate(m)

    if args.json:
        print(json.dumps({**m, "results": rows}, ensure_ascii=False, indent=1))
    else:
        print(render_report(m, rows))

    if args.emit_judge_prompt:
        emit_judge_prompt(args.target, script, m, args.emit_judge_prompt)
        print(f"\n審査プロンプトを書き出した: {args.emit_judge_prompt}")
        print("実行例: claude -p --safe-mode --model opus --tools \"\" "
              f"< {args.emit_judge_prompt}")


if __name__ == "__main__":
    sys.exit(main())
