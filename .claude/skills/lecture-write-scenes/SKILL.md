---
name: lecture-write-scenes
description: lectureモードの資料理解と教える順番から、図解優先の澪・透の場面台本JSONを生成する。「講義台本を書いて」「構成から場面を作って」など、場面生成を求められた時に使う。
---

# 講義の場面生成

1. 合格済みの`source-understanding.json`と`teaching-outline.json`を確定入力として読む。
2. `src/lecture/prompts/lecture_script.md`を全文読む。このMDがキャラクター、図解、セリフ、採点下限を含む工程プロンプト本文の単一ソースであり、本文をこのスキルや実行指示へ複製しない。
3. タイトル、前段JSON、URLを除いた図番号・キャプションを各プレースホルダーの入力として適用する。
4. 現在のClaude Codeセッション自身が`src/lecture/prompts/lecture_script.schema.json`に一致する完全な台本JSONを書き、指定された`scene-draft.json`へ保存する。別のAI CLIを起動しない。
5. 自律生成スキルの固定検証を`draft`段階で実行し、M1〜M6・M8と構造制約を確認する。不正JSONや報告された固定検証エラーだけを直し、同一工程は初回を含め最大3回までとする。
