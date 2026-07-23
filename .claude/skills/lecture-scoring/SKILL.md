---
name: lecture-scoring
description: 講義動画(lectureモード)の台本を4層スコアカードで採点する。「台本を採点して」「品質チェックして」「スコアを出して」「ベースラインを測って」など、講義台本・講義動画の品質評価を求められたら必ずこのスキルを使う。
---

# 講義台本の採点 (lecture-scoring)

採点基準の全体設計は `docs/lecture-scoring-rubric-2026-07-23.html`（4層スコアカード）。
このスキルは Layer 1（機械指標）と Layer 2（LLM審査7軸）を実行する。
**定義の単一ソース**は次の2ファイルで、採点基準をここや他の文書へ複製しない:

- Layer 1 のしきい値: `scripts/score_lecture.py` 冒頭の定数ブロック
- Layer 2 の7軸アンカー: `scripts/prompts/lecture_score_judge.md`

## 手順

1. **対象の特定**: 引数やユーザー指定から job ディレクトリ（`tmp/lecture/<job_id>/`）
   または `script.json` を特定する。指定が無ければ `tmp/lecture/` の最新 job を使い、
   どれを採点したかを明示する。
2. **Layer 1（機械指標）**: 必ずスクリプトで計測する。LLM が自分で数え直さない
   （決定論的な再現性がこの層の目的）。

   ```bash
   uv run python scripts/score_lecture.py <job_dir>
   ```

3. **Layer 2（LLM審査7軸）**: 審査は必ず**会話履歴を持たないクリーンな文脈**で
   実行する。台本の生成・改善に関わったセッションが自分で採点すると、経緯や期待が
   混入して点が歪むため、**セッション内の自分では採点しない**。まず審査プロンプトを
   書き出す:

   ```bash
   uv run python scripts/score_lecture.py <job_dir> --emit-judge-prompt tmp/judge.md
   ```

   実行単位はクライアントに応じて選ぶ（どれも審査者には judge プロンプト以外を
   渡さない）:

   - **Claude Code セッション内**: Agent ツールでサブエージェントを1つ起動し、
     「`tmp/judge.md` を読み、その指示だけに従って採点し、結果の JSON のみを
     返す」とだけ指示する。台本の経緯・要約・期待などの追加文脈を書かない。
   - **headless / パイプライン**: `claude -p --safe-mode --model opus --tools "" < tmp/judge.md`
   - **Codex**: `codex exec --ephemeral --sandbox read-only - < tmp/judge.md`
     （新規プロセスのため履歴なし）

4. **報告**: 次を1つの表にまとめて報告する。
   - Layer 1: M1〜M8 の実測と○✗（スクリプト出力をそのまま使う）
   - Layer 2: R1 の pass/fail と R2〜R7 の点数・weighted_total（100点満点）
   - top_fixes 3件（シーン番号付き）
   - ベースラインとの比較: 2026-07-23 の `20260722-234425-ai1939` は 52/100
5. **保存**: 求められた場合のみ、審査JSONを `<job_dir>/scorecard.json` へ保存する。

## 注意

- **審査者の独立性**: Layer 2 の審査者には judge プロンプトの内容以外を一切
  入力しない。生成経緯・過去の採点結果・改善の意図を渡すと、独立した審査に
  ならない。
- しきい値・配点を変えるときは `scripts/score_lecture.py` の定数と
  `scripts/prompts/lecture_score_judge.md` を同時に直し、
  `docs/lecture-scoring-rubric-2026-07-23.html` も更新する。
- M3（言い換え確認率）は「〜のですね」型の代理指標、M8（定型反復）は
  ひらがな主体の同一話者反復に絞ったヒューリスティック。境界例は Layer 2 の
  分析で上書きしてよいが、その旨を報告に書く。
- Layer 3（視聴審査）・Layer 4（視聴者実測）は人が行う。このスキルでは実行しない。
