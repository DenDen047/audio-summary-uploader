---
name: lecture-plan-teaching
description: lectureモードの資料理解JSONから、理解目標・用語導入・場面接続を持つ教える順番を設計する。「講義構成を作って」「教える順番を直して」など、台本前の構成設計を求められた時に使う。
---

# 講義の教える順番

1. `source-understanding.json`を読む。元資料へ戻って構成を作り直さない。
2. `src/lecture/prompts/lecture_teaching_outline.md`を全文読む。このMDがプロンプト本文の
   単一ソースである。
3. `{{UNDERSTANDING}}`と`{{FIGURES}}`を置換する。図はURLではなく番号とキャプションだけを渡す。
4. AIを呼ぶ場合は`claude -p`のサブスクリプション経路だけを使い、
   `src/lecture/prompts/lecture_teaching_outline.schema.json`でJSONを拘束する。
5. 結果を`teaching-outline.json`として保存し、主張ID、用語導入、接続理由が全場面に
   あることを確認する。
