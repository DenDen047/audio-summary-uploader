# 講義動画パイプライン（クロノIT方式）仕様書

## 1. 背景と目的

### 1.1 参照元

クロノITチャンネルの動画「【祝チャンネル開設半年】ぶっちゃけマークダウンって読みづらくない？」
（https://youtu.be/348DdatDa4A 、2026-07-08 公開）で紹介された動画制作方針に従う。
動画内で語られた方式の要点:

1. **映像生成 AI は使わない**。遅い・高い・毎回結果が変わり差分修正できないため。
   「決まったテンプレのパターンを組み合わせるだけで講義動画としては十分成立する」。
2. **コード生成で動画を作る**。Claude Maxで台本初稿、ChatGPT ProのCodexで
   技術／編集審査を行い、どちらも既存サブスクリプション内で実行する。
3. 中身は「丁寧に噛み砕いたマークダウン」。それを **2 キャラクターの掛け合い**
   （音声＋字幕）とスライド映像で同時に提示する（視覚＋聴覚の 2 チャンネル）。
4. TTS はクロノITも当初 VOICEVOX（ずんだもん）で開始し、後に規約フリー・多言語の
   独自キャラ＋独自 TTS に移行した。

### 1.2 現行システムとの関係

現行 audio-summary-uploader（`specs/SPEC.md`）は NotebookLM の Audio Overview
（音声のみ＋静止背景）を YouTube に上げる。本仕様はそれを置き換えるのではなく、
**別系統の動画生成パイプライン**として追加する。利用者から見た使い方は現行と同じ:

> URL リストを YAML に書いて 1 コマンド実行すると、数分〜十数分後に動画ができている。

### 1.3 本運用統合の範囲（本ブランチ）

- Web UI は `lecture` を既定とし、入力 URL 1 本につき澪・透の講義動画を 1 本生成する。
- 1 ジョブで MP4、サムネイル、台本、YouTube 投稿情報をまとめて出力し、既存の
  `video_ready` → YouTube upload 経路へ合流する。
- 従来の NotebookLM 音声要約は `notebooklm` モードとして残し、既存ジョブと
  YAML の後方互換性を保つ。
- `src/lecture/` は動画生成の詳細を隠す独立モジュールのままとし、
  `src/summary/pipeline.py` からは `generate_lecture()` の成果物契約だけを利用する。
- BudouX / Pygments は本番依存関係へ収載し、Web ワーカーから追加引数なしで使える。
- **AI費用は既存サブスクリプション内に限定する。** OpenAI API、Anthropic API、
  Gemini APIなどの従量課金APIを本番経路から呼ばない。将来この制約を変える場合は、
  設定変更ではなく仕様変更として明示的に合意する。

---

## 2. パイプライン全体像

```
URL（記事 / 論文 / GitHub / Spark メール共有）またはローカル PDF
    │
    ▼
1. fetch.py         URL → 本文テキスト＋キャプション付き図の抽出
                   （HTML/Spark: httpx+bs4 / PDF: pymupdf）
    │
    ▼
2. script_gen.py    Claude Maxで初稿 → ChatGPT Pro Codexで最終審査
    │                 - タイトル / 説明文 / タグ
    │                 - scenes[]: 一次資料図または意味別図解＋掛け合いセリフ
    │                 - サムネイルコピー＋動画固有の背景美術案
    │
    ▼
3. reveal.py        show_items から段階表示の計画を組み立てる
    │                 (シーンごとの表示状態列と、各セリフがどの状態かの対応)
    │
    ▼
4. slides.py        Jinja2 HTML テンプレ → Playwright スクリーンショット
    │                 → 表示状態ごとに 3840x2160 PNG（2x 解像度）
    │
    ▼
5. characters.py    立ち絵素材を用意 (カスタム/AI生成 優先、口パクパッチ抽出)
    │
    ▼
6. tts.py           VOICEVOX ENGINE (http://127.0.0.1:50021)
    │                 セリフごとに reading を audio_query → synthesis → wav
    │
    ▼
7. thumbnail_backdrop.py
    │               審査済み意味モチーフを決定論的なローカルSVGへ変換し
    │               文字・人物なしの動画固有サムネイル背景を生成
    │
    ▼
8. assemble.py      タイムライン計算 → ffmpeg 1 パス合成
    │                 - スライド静止画列 (セリフ同期でシーン内も切替) の concat
    │                 - キャラ立ち絵オーバーレイ (座標固定、口パクのみ局所更新)
    │                 - 章境界でアイキャッチ画像＋短い独自チャイムを挿入
    │                 - ASS 字幕焼き込み (文単位分割) + 音声 concat
    │
    ▼
output: tmp/lecture/<job_id>/
        video.mp4 + thumbnail.png + upload_metadata.json
        thumbnail-background.svg + thumbnail-background.png
        + thumbnail-background-prompt.txt
        （＋ source.txt, source_figures/, script.json, slides/, audio/）
```

## 3. モジュール仕様

### 3.1 fetch.py — 本文抽出

- `fetch_content(url) -> SourceContent(title, text, kind, figures)`
- HTML は `httpx` + `BeautifulSoup` で `<article>` 優先・なければ `<main>`・`<body>` の
  テキストを抽出する。同時に、本文中の`figure > img`と`figcaption`の組を最大12件抽出し、
  台本にはURLを渡さず、番号とキャプションだけを候補として渡す。PDF
  （Content-Type または拡張子判定）は`pymupdf`で本文を抽出し、v1では図候補を持たない。
- Spark の `/web-share/` URL は、SSR 応答中の React Router 初期データから件名と
  `threadRaw.messages[].webMessage.parts[]` のメール本文を復元する。ブラウザ描画済みDOMには
  依存しない。Sparkは完全なブラウザUAではSSR本文を省くため、共有ページ取得時だけ
  `Mozilla/5.0`を送り、入れ子を含む非表示プリヘッダー、メールアドレス、共有URLを本文から除去する。
- ローカル情報源は PDF のみ受け付け、HTTP を経由せず直接抽出する。
- GitHub リポジトリ URL は README を raw.githubusercontent.com から取得。
- 台本が`figure_index`で選んだ図だけを、元ページと同一ホストから最大15MBで取得し、
  `source_figures/figure_NN.<ext>`へ保存する。PNG / JPEG / WebP / SVGに限定し、後から
  `lecture render`だけを再実行できるよう外部URLへ依存しない。
- 上限 40,000 文字で切り詰め（台本生成プロンプトの入力上限対策）。
- 取得失敗は Fail Fast（例外で即停止）。

### 3.2 script_gen.py — 台本生成（Claude初稿＋Codex審査）

- 定額サブスク内で完結させるため、APIではなくClaude Maxでログイン済みの
  **Claude Code CLI** と、ChatGPT Proでログイン済みの **Codex CLI** を呼ぶ。
- 初稿は`claude -p --safe-mode --model opus --effort xhigh --tools ""`で生成する。
  `--json-schema`で構造を強制し、外部ツール・カスタマイズ・セッション保存を無効にする。
- Claude/Codexの各CLI呼び出しは`lecture.generation_timeout_seconds`で上限を設定する。
  40,000文字級の入力を最高品質で処理できるよう既定値は3,600秒とする。
- 初稿を固定コードで検証後、`codex exec --ephemeral --ignore-user-config --ignore-rules
  --sandbox read-only --model gpt-5.6-sol`へ、元情報・初稿・審査基準を渡す。
  `model_reasoning_effort=xhigh`、`approval_policy="never"`、`--output-schema`で、技術的断定、
  危険なコマンド、説明順、澪・透の口調、字幕と読み、スライド同期を最終審査する。
- Claude Max認証とChatGPT認証を起動前に確認し、APIキー経路へフォールバックしない。
- 監査情報には要求モデル、実際に使われた全モデル、各役割、effort、認証方式、
  `metered_api: false`、実際のeffortを表す`quality_mode`を保存する。
- 入力: 本文テキスト＋プロンプトテンプレート（`prompts/lecture_script.md`）。
- 出力: 下記スキーマの JSON。セリフの`text`は共通JSON Schemaの`maxLength: 80`で
  Claude/Codexの両方へ強制し、コードフェンス除去→`json.loads`→固定コードでも再検証する。
  投稿タグを含む必須キー・テンプレ型・話者名・セリフ長が不正なら、前回の台本JSONと
  行番号付きエラーを渡してClaudeで1回だけ修正する。残ったエラーは初稿ごとCodex審査へ
  引き継ぎ、Codexでも1回だけ再審査する。それでも不正ならFail Fastする。

```json
{
  "title": "動画タイトル（35字以内）",
  "description": "概要欄テキスト（参照元 URL を含めない）",
  "tags": ["..."],
  "thumbnail_text": ["疑問・意外性", "具体的な便益"],
  "thumbnail_visual_prompt": "motif=packages; 人物や文字を含めない動画固有の背景美術案",
  "generation": {
    "script_agent": "claude-code-cli+codex-cli",
    "script_model_requested": "opus + gpt-5.6-sol",
    "script_models_used": ["claude-opus-4-8", "gpt-5.6-sol"],
    "primary": {
      "authentication": "claude-max-subscription",
      "effort": "xhigh",
      "role": "draft-and-character-writing"
    },
    "review": {
      "authentication": "chatgpt-subscription",
      "effort": "xhigh",
      "role": "technical-and-editorial-review"
    },
    "metered_api": false,
    "quality_mode": "xhigh"
  },
  "scenes": [
    {
      "slide": {
        "template": "title | bullets | compare | code | quote | diagram | figure | outro",
        "background_mood": "explain | safety | warm",
        "heading": "スライド見出し",
        "...": "テンプレ型ごとの追加フィールド（§3.3）"
      },
      "lines": [
        {"speaker": "zunda",
         "text": "字幕表示用（英語用語は自然な英語表記のまま。80字以内）",
         "reading": "音声合成用の読み（英語をカタカナ化、記号を読み下す）",
         "metan_pose": "viewer", "zunda_pose": "listen",
         "show_items": 1},
        {"speaker": "metan", "text": "...", "reading": "...",
         "metan_pose": "default", "zunda_pose": "understand", "show_items": 2}
      ]
    }
  ]
}
```

- 台本の設計指針（プロンプトに明記）:
  - 想定視聴者は AI・ソフトウェア技術に関心のあるエンジニア。
    リスナー個人の属性には言及しない（公開面の規約は現行 prompt_presets と同じ）。
  - 1 シーン 1 論点。スライドは要点の再掲ではなくセリフの「板書」。
  - **図解優先**: 一次資料に論点を直接支える図があれば`figure`、なければ意味に合う
    `diagram`、関係性を視覚化できない残余だけを`bullets` / `quote`にする。
  - 図型は、対立・2軸=`matrix`、派生・分類=`tree`、因果・処理順=`flow`、
    積層依存=`layers`、時間変化=`timeline`、フィードバック=`cycle`、定量比較=`table`とする。
    数値表をmatrixへ入れず、全説明を一つの図型へ押し込めない
    （ultrasurveyの意味関係別マッピングに準拠）。
  - `tree.items[0]`は親・根、残りは子とする。複数要素をまとめる主体は根へ置き、
    枝と説明の向きが逆転しないようにする。
  - 一次資料図の表示キャプションはAIの転記値を使わず、抽出時のキャプションへ戻す。
    出典資料名とFigure番号を併記し、図の意味を変える加工はしない。
  - 掛け合いは 解説役（metan）× 聞き役・ツッコミ役（zunda）の役割分担。
  - 透は独り言・驚き・ツッコミを含めて常に敬語で話す。澪も通常は敬語で話し、
    `metan_pose: tease` の短いからかい一言だけ非敬語にする。からかい以外の口調崩れと、
    敬語のままの`tease`は固定コードで検証し、不正なら再生成する。
  - 冒頭は、困っている透が「澪先生」と相談する具体的な場面から始める。透が試したことと
    腑に落ちない点を示し、澪が問題の正体と今回の問いを定める。説明順は原則として
    `困りごと → 問い → 一文の答え → 全体図 → 代表例 → 原理 → 一次資料・実測 →
    失敗例・限界 → 判断`とし、末尾で冒頭の困りごとを回収する。既存チャンネルからは
    問題起点の構成だけを参考にし、台詞・人物像・世界観は模倣しない。
  - 8〜14 シーン、各シーン2〜6セリフ。セリフ合計の目安は3,000〜4,500字
    （≒ 5〜8 分）。Python検証と構造化出力schemaの両方で上下限を強制する。
- **セリフ同期の段階表示** (`show_items`): bullets / outro / diagram は「そのセリフの間に
  見えている項目数」(単調非減少、最後は総数)、compare は 1=左のみ / 2=両方。
  title / code / quote / figure には付けない。検証は script_gen、計画の組み立ては reveal.py。
  AIが整数を返した場合の範囲超過・逆行・最終項目不足は、内容を再生成せず有効範囲へ
  決定論的に正規化する。欠損や非整数は正規化せず検証エラーとして再生成へ戻す。
- **表情・ポーズ**: 全セリフに `metan_pose` / `zunda_pose` を持たせる。セリフ開始時に
  切り替え、次のセリフ開始まで維持する。澪は視聴者向け説明、注意、軽い微笑みを、
  透は傾聴、疑問、照れ、理解、喜びを内容に応じて使い分ける。

### 3.2.1 thumbnail_backdrop.py — 動画固有のローカルSVG背景

- 審査済み台本は`thumbnail_visual_prompt`の先頭で、`packages | network | code | security |
  database | cloud | research | speed | comparison | generic`から意味モチーフを選ぶ。
- Pythonはモチーフ、タイトル、美術案から決定論的seedを作り、チャンネル共通の色、余白、
  コントラストを持つ16:9 SVGをローカル生成する。Playwright Chromiumで1600x900 PNGへ
  ラスタライズするため、外部画像API、APIキー、従量課金は発生しない。
- 人物、文字、ロゴは背景へ入れず、澪・透の確定立ち絵、見出し、配置を後段のPillowで
  合成する。同じ台本からは同じSVGが得られ、差分確認と手修正もできる。
- `thumbnail.background_mode`は`codex-svg`（既定）または`static`。後者だけ固定背景を使う。
- ジョブには`thumbnail-background.svg`、`thumbnail-background.png`、再現・監査用の
  `thumbnail-background-prompt.txt`を残し、providerと`metered_api: false`をメタデータへ記録する。
- Codexアプリの`$imagegen`は`gpt-image-2`をサブスクリプション枠で使えるため、
  キャラクター原本や、人が比較して選ぶ専用サムネイル背景には利用する。ただし現環境の
  `codex exec`非対話試験では画像を保存できず、公式のプログラム実行手段であるOpenAI Image
  APIはAPIキーと従量課金を伴う。そのため自動Web経路の必須背景はローカルSVGとする。

### 3.2.2 AIと決定論的処理の責任境界

| 領域 | 実行主体 | 理由 |
|---|---|---|
| 要点抽出、会話構成、図型選択、スライド内容、投稿文、背景美術案 | Claude Opus | 長い一次情報から自然な初稿を組み立てる必要がある |
| 技術的断定、危険なコマンド、説明順、会話、構造の最終審査 | Codex Sol | 初稿を元情報と独立に照合し、公開前の第二視点が必要である |
| サムネイル背景 | Python / SVG / Playwright | Codexの意味モチーフを、追加費用なしで再現可能な図形へ変換する |
| 声の波形生成 | VOICEVOX | 確定した読みを話者スタイルごとの音声へ変換する専用TTS |
| 図の取得とキャプション固定、JSON検証、スライド配置、キャラ、字幕、口パク、時間同期、動画合成 | Python / Playwright / ffmpeg | 同じ入力なら同じ位置・同じ時間で再現し、微小な見た目の揺れを防ぐ |

生成AIは意味や美術のように「正解が一つでない箇所」に限定し、キャラクター同一性、文字の
正確さ、幾何学、時間同期のように「毎回一致すべき箇所」は固定コードへ閉じ込める。AI出力と
固定処理の境界にはJSONスキーマ検証とモチーフ契約を置く。

### 3.3 slides.py — スライド描画

- Jinja2 テンプレート（`src/lecture/templates/`）を HTML に展開し、Playwright
  (chromium, headless, device_scale_factor=2) で 3840x2160 スクリーンショット。
  段階表示のあるシーンは表示状態ごとに 1 枚ずつ描画する（未到達の項目は
  `visibility: hidden` で隠し、レイアウトを固定したまま出現させる）。
- デザインは現代の「保健室兼IT相談室」テーマ。手前のベッド区画を開放し、奥の
  薄ピンクのカーテン区画にもベッドが続く、漫画的な保健室背景を全シーンで固定して
  使う。`background_mood` は台本互換のため `explain` / `safety` / `warm` を保持するが、
  背景画像は切り替えない。背景だけを約 8% 暗くしてキャラを前景へ分離し、中央には
  読みやすい半透明の板書面を置く。見出しは丸ゴシック
  (M PLUS Rounded 1c) ＋ずんだ緑×めたんピンクの2色グラデーションマーカー
  （署名要素）。本文は Noto Sans JP。
- titleテンプレートの出典ラベルは左右のキャラクター安全領域へ入らない最大幅に制限し、
  長い論文名・記事名は末尾を省略表示する。
  フォントは `fonts/` の ttf を `@font-face` で読む（環境非依存）。
  日本語見出しの改行は `budoux` で分節する。
- `code` テンプレは Pygments (monokai, インラインスタイル) でハイライトし、
  mac 風ウィンドウ枠に載せる。lexer はコード内容から判定（console / python / bash）。
- `figure`はジョブ内に保存した一次資料の図を白いカード内へ`object-fit: contain`で表示し、
  抽出時のキャプションと出典を併記する。`diagram`は意味別の7図型を決定論的HTML/CSSで
  描画し、ノードをセリフに同期して順に表示する。
- テンプレ型（固定パターン。クロノIT方式の「決まったテンプレの組み合わせ」）:

| template | 用途 | フィールド |
|---|---|---|
| `title`   | 表紙 | heading, subheading, source_label |
| `bullets` | 箇条書き板書 | heading, items[] (≤5, 各≤40字) |
| `compare` | 2 カラム対比 | heading, left_title, left_items[], right_title, right_items[] |
| `code`    | コード/コマンド例 | heading, code, caption |
| `quote`   | 原文引用 | heading, quote, attribution |
| `diagram` | 関係性の図解 | heading, diagram_type (`flow/tree/layers/timeline/cycle/matrix/table`), items[] (2〜6、matrixは4)。matrixのみleft_title=横軸・right_title=縦軸。tableは先頭を見出しとし、同じ2〜4列を ` | ` で区切る |
| `figure`  | 一次資料の図 | heading, figure_index, caption, attribution |
| `outro`   | まとめ | heading, items[] (≤4)。チェックマーク付き箇条書き |

### 3.4 tts.py — 音声合成（VOICEVOX）

- VOICEVOX ENGINE の REST API（`POST /audio_query` → `POST /synthesis`）。
  エンジンは `/Applications/VOICEVOX.app/Contents/Resources/vv-engine/run` を
  起動しておく（未起動なら Fail Fast でエラーメッセージに起動コマンドを出す）。
- 話者マッピング（v1 固定）:

| 台本上の名前 | キャラクター | style_id | 字幕色 |
|---|---|---|---|
| `zunda` | 麦野透（VOICEVOX:満別花丸 ノーマル） | 69 | #8A641E |
| `metan` | 紫ノ宮澪（VOICEVOX:もち子さん セクシー／あん子） | 66 | #B43A73 |

- 出力: セリフごとの wav（24kHz mono）。読み上げテキストは `reading` を使う。
  同一文の実測尺を揃えるため、`speedScale` は澪=1.18、透=0.90とする。
- **立ち絵** (`characters.py`): 優先は `assets/characters/video_v3/{zunda,metan}.png` の
  カスタム立ち絵（画像生成で作成した規約フリーのオリジナルキャラ。
  ローズ色の解説役＝metan 声、ハニーイエローの聞き役＝zunda 声。単色クロマキーで
  生成して透過化）。PowerPointで手動調整した配置を基準に、澪は1232px高の素材
  キャンバスを`x=0, y=205`、透は1019px高の素材キャンバスを`x=1641, y=231`へ置く。
  画面内で見える基準寸法は澪505×875px、透186×677px。透明余白は配置情報を兼ねる
  ため左右もトリムしない。透は大きな頭、短い胴と手足、狭いなで肩を持つ約5頭身の
  少年体型を画像生成段階で確定し、動画合成時に骨格を引き伸ばさない。
  フォールバックは VOICEVOX 公式ポートレートの
  上半身クロップ (高さ 500px)。減光版は作らない (非話者を暗くすると不自然な
  ため、話者だけを動かして目立たせる)。
- **口パク** (`characters.py`): `{name}_open.png` がある立ち絵は、閉じ画像へ位置合わせ
  （±6px総当たり）→ 差分の最大密度ブロブ（格子 8px、輪郭の細線ノイズを除外）から
  口元パッチを自動抽出する。表情・ポーズ差分には必ず同じ名前の専用口開き画像
  (`{name}__{pose}_open.png`) を用意し、別ポーズの口やプログラム描画の口は流用しない。
  正規化後の全差分が96×72pxを超える素材は、顔・髪などの再生成が混入したものとして
  動画生成前にエラーにする。
  合成時、発話区間だけパッチを点滅させて口パクにする。澪は
  0.56秒周期のうち0.14秒だけ開くことで、落ち着いた話速に合う控えめな動きにする。
- **クレジット表記**: VOICEVOX 利用規約に従い、概要欄 description に
  `VOICEVOX:満別花丸` `VOICEVOX:もち子さん` を必ず含める（script_gen の
  description 生成後にコードで強制付与する）。アイキャッチ効果音は
  OtoLogic の CC BY 4.0 素材を使い、`OtoLogic` のクレジットも強制付与する。
- **公開情報の安全化**: 入力元 URL は Claude / Codex の台本プロンプトへ渡さず、
  台本全体（タイトル、スライド、字幕、サムネ文言、概要欄）から入力元 URL と
  メールアドレスをコードで除去する。URL は state と `upload_metadata.json` の
  内部追跡用 `source_url` にだけ保持する。OtoLogic のライセンス表記 URL は維持する。

### 3.5 assemble.py — 合成

- タイムライン計算（Python）: 各 wav の実測長（ffprobe）から
  - セリフ間ギャップ 0.25s、シーン間ギャップ 0.7s、末尾 1.0s
  - シーン長 = セリフ長合計＋ギャップ → スライド表示時間
  - 各セリフの開始/終了時刻 → ASS 字幕のタイミング
- ffmpeg 1 パス:
  - 映像: スライド静止画列の concat demuxer（段階表示の状態切替はセリフ開始時刻で
    行う）→ fps=30, scale 1920x1080。ズーム等のカメラ的な動きは入れない
    （zoompan は座標丸めでサブピクセルの揺れ・カクつきが出るため不採用）
  - キャラ立ち絵 overlay: 常時表示し、`metan_pose` / `zunda_pose` に応じて台詞単位で
    表情・ポーズを切り替える。髪や輪郭全体の1px移動が口パク切替と重なるのを防ぐため、
    発話中も座標は固定し、動きは口パクと意味のあるポーズ差分だけで表現する。
  - `eyecatch_before_scenes` で指定した章境界には、短いアイキャッチを挿入する。
    画像と効果音は挿入ごとに別のものを使う。1回目は実践開始にOtoLogic
    「木琴06-1（上昇・短）」、2回目は総復習への転換に「グロッケン02-4
    （高・短）」を使う。素材は24kHz mono WAVへ変換し、末尾に0.7秒以上の
    完全な無音を保持して次の台詞と聴覚的に分離する。人声の効果音は使わない。
    この区間は通常立ち絵と字幕を表示しない。
    解説役(metan 声)が左端・聞き役(zunda 声)が右端から95px内側。位置と表示寸法は
    PowerPointの手動配置実測値に従う。人物の骨格は素材生成時に調整済みで、合成時は
    アスペクト比を変えない。口パクパッチは
    話者別の開閉周期で重ねる（澪は `lt(mod(t,0.56),0.14)`）
  - 字幕: `.ass` を `subtitles=` フィルタで焼き込み
    （`fontsdir=fonts/`、丸ゴシック 50px、話者色＋白フチ。話者名プレフィックスは
    立ち絵で判別できるため付けない）
  - 冒頭0〜6秒は、各立ち絵の腰付近に話者色の名札を重ねて
    `紫ノ宮 澪` / `麦野 透` を表示する。短いフェードを付け、6秒以降は消して
    通常字幕とスライドの視認性を優先する
  - 音声: 無音 wav を挟んだ concat demuxer → AAC 192k
  - 最終フレームの duration 解釈で映像が総尺より延びるため `-t 総尺` で打ち切る
  - 字幕の分割・改行: 長いセリフは句点で分割して順に表示（1 表示 約 40 字まで、
    表示時間は文字数比で配分）。行の折り返しは libass がスペース無しの日本語を
    扱えないため、budoux の分節を 1 行 20 字以内に詰めて `\N` を自前で挿入する
  - 字幕テキストは `text`（表示用）を使う。読みテキスト (`reading`) は使わない
- BGM は v1 では入れない（ライセンス確認済みの音源を導入してから）。

### 3.6 pipeline.py / cli.py — 成果物契約とエントリポイント

```
uv run lecture generate <URL> [--out-dir tmp/lecture]
uv run lecture generate <URL> --script <path>  # 台本再利用
uv run lecture render <job_dir>                 # script.json 以降だけ再実行
```

- `generate_lecture()` は `LectureArtifacts` を返し、動画、サムネイル、台本、投稿情報の
  パスと title / description / tags / thumbnail_text を一括して呼び出し側へ渡す。
- VOICEVOX ENGINE が停止中ならローカルアプリ同梱エンジンを自動起動し、生成後に
  自動終了する。既に起動済みならそのプロセスを再利用して終了させない。
- 中間生成物（script.json / slides / audio）はジョブディレクトリに全て残し、
  **台本だけ手直しして再レンダリング**できるようにする（コード生成方式の利点 =
  差分修正可能性を確保する）。

## 4. 出力ディレクトリ

```
tmp/lecture/<job_id>/        # job_id = YYYYMMDD-HHMMSS-<slug>
├── source.txt               # 抽出本文（デバッグ用）
├── source_figures/          # 台本が選んだ一次資料図（外部URLなしで再描画可能）
│   └── figure_01.png ...
├── script.json              # 台本（編集して render で再合成可能）
├── thumbnail-background.svg # 審査済み美術案から作る編集可能なベクター原本
├── thumbnail-background.png # SVGをローカルでラスタライズした背景
├── thumbnail-background-prompt.txt # 美術案・モチーフ・seed・費用方針
├── thumbnail.png            # 専用背景・大きな表情・短い訴求コピーの16:9サムネイル
├── upload_metadata.json     # 投稿情報 / AIモデル / 背景provider / 成果物パス
├── slides/scene_01_s1.png ...  # 表示状態ごとの静止画
├── audio/scene_01_line_01.wav ...
├── voicevox-engine.log      # 自動起動した場合の ENGINE ログ
├── subs.ass
└── video.mp4
```

## 5. 失敗時の方針

- 全ステージ Fail Fast（AGENTS.md 方針どおり）。リトライは script_gen の
  JSON 不正時 1 回のみ。固定背景が必要な運用では`background_mode: static`を明示する。
- VOICEVOXエンジン未起動、Claude Code / Codex CLI不在、Claude Max / ChatGPT Pro
  未ログインは前提条件エラーとして起動方法を添えて即終了。APIキー経路へは切り替えない。

## 6. 現行システムから再利用するもの / しないもの

| 資産 | 統合後の扱い |
|---|---|
| YouTube アップロード (`summary/youtube.py`) | `video_ready` 以降を共用 |
| サムネイル | 審査済み美術案のローカルSVG背景＋澪・透の確定立ち絵＋`thumbnail_text` |
| urls.yaml パーサ (`url_parser.py`) | `mode: lecture` / `mode: notebooklm` を解釈 |
| NotebookLM 系 (`notebooklm*.py`) | `notebooklm` モードだけで使用 |
| フォント `fonts/NotoSansJP-Bold.ttf` | 共用 |
| フォント `fonts/MPLUSRounded1c-{Bold,Black}.ttf` | 講義動画で使用 |

## 7. 本運用への統合（実装済み）

1. Web の `/api/add` は `mode` と `privacy_status` を state と `UrlEntry` に保存する。
   Web の既定値は `lecture` / `unlisted` で、公開範囲は `unlisted`（限定公開）と
   `public`（一般公開）から選択できる。YAML で mode を省略した場合は後方互換のため
   `notebooklm` とする。
2. submit は講義ジョブを `generating` にし、collect は `generate_lecture()` を
   ワーカースレッドで実行する。Playwright / VOICEVOX / ffmpeg の競合を避けるため
   講義ジョブは単一ワーカー内で直列生成する。
3. 完成時に動画・サムネイル・台本・投稿情報を state へ記録して `video_ready` にし、
   upload は入力元 URL と個人情報を除去済みの title / description / tags を YouTube へ渡す。
4. サーバー再起動時の `generating` 講義ジョブは同じ URL から安全に再生成する。
   upload だけ失敗したジョブは既存成果物を保持し、投稿だけ再試行する。
5. Web UI は完成・投稿失敗のどちらでも動画、サムネイル、AI背景、背景プロンプト、
   投稿情報JSONを取得でき、実使用モデルと背景providerも表示する。
6. `thumbnail_text` は各14文字以内の2行とし、内容に即した疑問と便益を大きく表示する。
   確定済みの澪・透立ち絵を再利用し、AI生成は文字のない抽象背景に限定することで、
   キャラクター同一性と動画間のチャンネル統一感を維持する。
