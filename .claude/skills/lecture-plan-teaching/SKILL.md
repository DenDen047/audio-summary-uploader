---
name: lecture-plan-teaching
description: lectureモードの資料理解JSONから、理解目標・用語導入・場面接続を持つ教える順番を設計する。「講義構成を作って」「教える順番を直して」など、台本前の構成設計を求められた時に使う。
---

# 講義の教える順番

1. パイプライン実行では、呼び出し元がこのSKILL.md、`src/lecture/prompts/lecture_teaching_outline.md`、schema、合格済み資料理解JSON、URLを除いた図候補を1メッセージへ展開し、ファイル・シェル・Webツールなしで構造化JSONを受け取る。展開済みの工程プロンプトを単一ソースとして使い、ファイルを読もうとせずJSONだけを返す。
2. 対話から単独実行する場合は、指定された`work_dir`の合格済み`source-understanding.json`と`run-input.json`を読み、次に`src/lecture/prompts/lecture_teaching_outline.md`を全文読む。工程本文をこのスキルや実行指示へ複製しない。
3. 資料理解と図候補をMDの入力へ適用する。元資料や外部検索へ戻って主張や限界を作り直さない。
4. 現在のClaude Codeセッション自身が`lecture_teaching_outline.schema.json`に一致するJSONを作る。パイプラインでは構造化出力として返し、単独実行では`work_dir/teaching-outline.json`へ保存する。別のAI CLIを起動しない。
5. パイプラインでは固定検証と再試行を呼び出し元へ任せる。単独実行では初回生成を1試行目として`uv run python scripts/validate_lecture_stage.py <work_dir> --stage outline`で固定検証し、指摘箇所だけを最大2回修正する（合計最大3試行）。
