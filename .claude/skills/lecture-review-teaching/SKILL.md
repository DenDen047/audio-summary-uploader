---
name: lecture-review-teaching
description: lectureモードの場面台本を、資料理解と教える順番に照らして根拠・平易さ・掛け合い・世界観・感情の観点で修正する。「教え方をレビューして」「台本を仕上げて」などに使う。
---

# 講義の教え方レビュー

1. パイプライン実行では、呼び出し元がこのSKILL.md、`src/lecture/prompts/lecture_teaching_review.md`、schema、3つの合格済み前段JSON、URLを除いたタイトルと図候補、直前の検証エラーを1メッセージへ展開し、ファイル・シェル・Webツールなしで構造化JSONを受け取る。展開済みの工程プロンプトを単一ソースとして使い、ファイルを読もうとせずJSONだけを返す。
2. 対話から単独実行する場合は、指定された`work_dir`の合格済み`source-understanding.json`、`teaching-outline.json`、`scene-draft.json`、`run-input.json`を読み、次に`src/lecture/prompts/lecture_teaching_review.md`を全文読む。工程本文をこのスキルや実行指示へ複製しない。
3. タイトル、前段JSON、図候補、直前の固定検証エラーをMDの入力へ適用する。元資料や外部検索へ戻らず、第1段階が保存した情報だけを使う。
4. 現在のClaude Codeセッション自身が照合・修正し、完全な台本JSONを作る。パイプラインでは構造化出力として返し、単独実行では`work_dir/script.json`へ保存する。別のAI CLIを起動しない。
5. パイプラインでは固定検証と再試行を呼び出し元へ任せる。単独実行では初回生成を1試行目として`uv run python scripts/validate_lecture_stage.py <work_dir> --stage final`で固定検証し、問題のない箇所を保ったまま指摘箇所だけを最大2回修正する（合計最大3試行）。
6. Layer 2の正式採点はこのレビュー担当自身では行わない。`lecture-scoring`の手順で履歴を持たない独立審査へ渡す。
