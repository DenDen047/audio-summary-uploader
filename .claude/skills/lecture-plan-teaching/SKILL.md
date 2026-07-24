---
name: lecture-plan-teaching
description: lectureモードの資料理解JSONから、理解目標・用語導入・場面接続を持つ教える順番を設計する。「講義構成を作って」「教える順番を直して」など、台本前の構成設計を求められた時に使う。
---

# 講義の教える順番

1. 合格済みの`source-understanding.json`を確定入力として読む。元資料へ戻って主張や限界を作り直さない。
2. `src/lecture/prompts/lecture_teaching_outline.md`を全文読む。このMDが工程プロンプト本文の単一ソースであり、本文をこのスキルや実行指示へ複製しない。
3. 資料理解と、URLを除いた図番号・キャプションを`{{UNDERSTANDING}}`と`{{FIGURES}}`の入力として適用する。
4. 現在のClaude Codeセッション自身が`src/lecture/prompts/lecture_teaching_outline.schema.json`に一致するJSONを書き、指定された`teaching-outline.json`へ保存する。別のAI CLIを起動しない。
5. 固定検証で主張ID、用語導入、接続理由、場面番号を確認する。エラーがあれば指摘箇所だけを修正し、同一工程は初回を含め最大3回までとする。
