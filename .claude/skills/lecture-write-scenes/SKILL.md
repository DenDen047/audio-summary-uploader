---
name: lecture-write-scenes
description: lectureモードの資料理解と教える順番から、図解優先の澪・透の場面台本JSONを生成する。「講義台本を書いて」「構成から場面を作って」など、場面生成を求められた時に使う。
---

# 講義の場面生成

1. パイプライン実行では、呼び出し元がこのSKILL.md、`src/lecture/prompts/lecture_script.md`、schema、合格済み前段JSON、URLを除いたタイトルと図候補を1メッセージへ展開し、ファイル・シェル・Webツールなしで構造化JSONを受け取る。展開済みの工程プロンプトを単一ソースとして使い、ファイルを読もうとせずJSONだけを返す。
2. 対話から単独実行する場合は、指定された`work_dir`の合格済み`source-understanding.json`、`teaching-outline.json`、`run-input.json`を読み、次に`src/lecture/prompts/lecture_script.md`を全文読む。工程本文をこのスキルや実行指示へ複製しない。
3. タイトル、図候補、前段JSONをMDの入力へ適用する。元資料や外部検索へ戻って内容を作り直さない。
4. 現在のClaude Codeセッション自身が`lecture_script.schema.json`に一致する完全な台本JSONを作る。パイプラインでは構造化出力として返し、単独実行では`work_dir/scene-draft.json`へ保存する。別のAI CLIを起動しない。
5. パイプラインでは固定検証と再試行を呼び出し元へ任せる。単独実行では初回生成を1試行目として`uv run python scripts/validate_lecture_stage.py <work_dir> --stage draft`で固定検証し、M1〜M6・M8と構造制約の指摘箇所だけを最大2回修正する（合計最大3試行）。
