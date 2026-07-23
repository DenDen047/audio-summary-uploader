---
name: lecture-understand-source
description: lectureモードの元資料から主要主張・根拠・限界・前提用語をJSONへ整理する。「資料理解を作って」「講義の根拠を整理して」など、台本より前の資料分析を求められた時に使う。
---

# 講義の資料理解

1. `src/lecture/prompts/lecture_source_understanding.md`を全文読む。このMDがプロンプト本文の
   単一ソースであり、指示をこのスキルへ複製しない。
2. `{{TITLE}}`、`{{SOURCE_KIND}}`、`{{TEXT}}`を対象資料で置換する。元URLは埋め込まない。
3. AIを呼ぶ場合は`claude -p`のサブスクリプション経路だけを使い、
   `src/lecture/prompts/lecture_source_understanding.schema.json`でJSONを拘束する。
4. 結果を`source-understanding.json`として保存し、URL・メールアドレス・アクセストークンが
   含まれないことを確認する。
5. 後続の`lecture-plan-teaching`へ、このJSONだけを資料理解成果物として渡す。
