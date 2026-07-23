---
name: lecture-write-scenes
description: lectureモードの資料理解と教える順番から、図解優先の澪・透の場面台本JSONを生成する。「講義台本を書いて」「構成から場面を作って」など、場面生成を求められた時に使う。
---

# 講義の場面生成

1. `source-understanding.json`と`teaching-outline.json`を読む。
2. `src/lecture/prompts/lecture_script.md`を全文読む。このMDがキャラクター、図解、
   セリフ、採点下限を含むプロンプト本文の単一ソースである。
3. 全プレースホルダーを置換する。元URLは渡さず、一次資料の図は番号とキャプションだけを渡す。
4. AIを呼ぶ場合は`claude -p`のサブスクリプション経路だけを使い、
   `src/lecture/prompts/lecture_script.schema.json`でJSONを拘束する。
5. 結果を`scene-draft.json`として保存し、`scripts/score_lecture.py`の機械指標を確認する。
   不正JSONや固定検証エラーだけを直し、同一箇所の再試行は3回までにする。
