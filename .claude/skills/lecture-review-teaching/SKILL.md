---
name: lecture-review-teaching
description: lectureモードの場面台本を、資料理解と教える順番に照らして根拠・平易さ・掛け合い・世界観・感情の観点で修正する。「教え方をレビューして」「台本を仕上げて」などに使う。
---

# 講義の教え方レビュー

1. `source-understanding.json`、`teaching-outline.json`、`scene-draft.json`を読む。
2. `src/lecture/prompts/lecture_teaching_review.md`を全文読む。このMDが教え方レビューの
   プロンプト本文の単一ソースである。
3. 全プレースホルダーを置換し、機械検証エラーがなければ`なし`を入れる。元URLは渡さない。
4. AIを呼ぶ場合は`codex exec --ephemeral`のChatGPTサブスクリプション経路だけを使い、
   `src/lecture/prompts/lecture_script.schema.json`で完全な台本JSONを拘束する。
5. 結果を`script.json`として保存し、固定検証と`lecture-scoring`を実行する。
6. Layer 2の正式採点は、このレビュー担当自身では行わない。`lecture-scoring`の手順で
   履歴を持たない独立審査へ渡す。
