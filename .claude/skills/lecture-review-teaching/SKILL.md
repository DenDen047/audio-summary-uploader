---
name: lecture-review-teaching
description: lectureモードの場面台本を、資料理解と教える順番に照らして根拠・平易さ・掛け合い・世界観・感情の観点で修正する。「教え方をレビューして」「台本を仕上げて」などに使う。
---

# 講義の教え方レビュー

1. 元資料、合格済みの`source-understanding.json`と`teaching-outline.json`、`scene-draft.json`を照合する。
2. `src/lecture/prompts/lecture_teaching_review.md`を全文読む。このMDが教え方レビューの工程プロンプト本文の単一ソースであり、本文をこのスキルや実行指示へ複製しない。
3. タイトル、元資料、前段JSON、URLを除いた図番号・キャプション、直前の固定検証エラーを各プレースホルダーの入力として適用する。
4. 現在のClaude Codeセッション自身が調査・修正し、`src/lecture/prompts/lecture_script.schema.json`に一致する完全な台本JSONを指定された`script.json`へ保存する。別のAI CLIを起動しない。
5. 自律生成スキルの固定検証を`final`段階で実行し、問題のない箇所を保ったまま報告されたエラーだけを直す。同一工程は初回を含め最大3回までとする。
6. Layer 2の正式採点はこのレビュー担当自身では行わない。`lecture-scoring`の手順で履歴を持たない独立審査へ渡す。
