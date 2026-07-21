# 講義動画パイプライン（クロノIT方式）仕様書

## 1. 背景と目的

### 1.1 参照元

クロノITチャンネルの動画「【祝チャンネル開設半年】ぶっちゃけマークダウンって読みづらくない？」
（https://youtu.be/348DdatDa4A 、2026-07-08 公開）で紹介された動画制作方針に従う。
動画内で語られた方式の要点:

1. **映像生成 AI は使わない**。遅い・高い・毎回結果が変わり差分修正できないため。
   「決まったテンプレのパターンを組み合わせるだけで講義動画としては十分成立する」。
2. **コード生成で動画を作る**。Claude Code / Codex の定額サブスクリプション内で
   生成するため、動画制作の追加コストがほぼゼロ。
3. 中身は「丁寧に噛み砕いたマークダウン」。それを **2 キャラクターの掛け合い**
   （音声＋字幕）とスライド映像で同時に提示する（視覚＋聴覚の 2 チャンネル）。
4. TTS はクロノITも当初 VOICEVOX（ずんだもん）で開始し、後に規約フリー・多言語の
   独自キャラ＋独自 TTS に移行した。

### 1.2 現行システムとの関係

現行 audio-summary-uploader（`specs/SPEC.md`）は NotebookLM の Audio Overview
（音声のみ＋静止背景）を YouTube に上げる。本仕様はそれを置き換えるのではなく、
**別系統の動画生成パイプライン**として追加する。利用者から見た使い方は現行と同じ:

> URL リストを YAML に書いて 1 コマンド実行すると、数分〜十数分後に動画ができている。

### 1.3 プロトタイプの範囲（本ブランチ）

- 入力 URL 1 本 → 講義動画 mp4 の生成まで（YouTube アップロードはしない）。
- 既存 `src/automator/` は一切変更しない。新規パッケージ `src/lecture/` に隔離。
- pyproject.toml も変更しない。起動は
  `PYTHONPATH=src uv run --frozen --with budoux --with pygments python -m lecture.cli`
  （budoux / pygments は main の pyproject に未収載のため `--with` で注入する）。
- 本運用に昇格する際に §7 の統合方針に従って CLI サブコマンド化する。

---

## 2. パイプライン全体像

```
URL（記事 / 論文 / GitHub リポジトリ）
    │
    ▼
1. fetch.py         URL → 本文テキスト抽出（HTML: httpx+bs4 / PDF: pymupdf）
    │
    ▼
2. script_gen.py    claude -p（headless, サブスク内）で台本 JSON を生成
    │                 - タイトル / 説明文 / タグ
    │                 - scenes[]: スライド内容（テンプレ型に正規化）＋掛け合いセリフ
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
7. assemble.py      タイムライン計算 → ffmpeg 1 パス合成
    │                 - スライド静止画列 (セリフ同期でシーン内も切替) の concat
    │                 - キャラ立ち絵オーバーレイ (座標固定、口パクのみ局所更新)
    │                 - 章境界でアイキャッチ画像＋短い独自チャイムを挿入
    │                 - ASS 字幕焼き込み (文単位分割) + 音声 concat
    │
    ▼
output: tmp/lecture/<job_id>/video.mp4 （＋ script.json, slides/, audio/）
```

## 3. モジュール仕様

### 3.1 fetch.py — 本文抽出

- `fetch_content(url) -> SourceContent(title, text, kind)`
- HTML は `httpx` + `BeautifulSoup` で `<article>` 優先・なければ `<main>`・`<body>` の
  テキストを抽出。PDF（Content-Type または拡張子判定）は `pymupdf`。
- GitHub リポジトリ URL は README を raw.githubusercontent.com から取得。
- 上限 40,000 文字で切り詰め（台本生成プロンプトの入力上限対策）。
- 取得失敗は Fail Fast（例外で即停止）。

### 3.2 script_gen.py — 台本生成（claude -p）

- 定額サブスク内で完結させるため、API ではなく **Claude Code headless
  (`claude -p`)** を subprocess で呼ぶ。プロンプトで「ツールは一切使わず JSON のみを
  出力」と指示し、純テキスト生成にする。
- 入力: 本文テキスト＋プロンプトテンプレート（`prompts/lecture_script.md`）。
- 出力: 下記スキーマの JSON。コードフェンス除去→`json.loads`→スキーマ検証
  （必須キー・テンプレ型・話者名・セリフ長）を行い、不正なら 1 回だけ再生成、
  それでも駄目なら Fail Fast。

```json
{
  "title": "動画タイトル（35字以内）",
  "description": "概要欄テキスト（出典 URL を含む）",
  "tags": ["..."],
  "scenes": [
    {
      "slide": {
        "template": "title | bullets | compare | code | quote | outro",
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
  - 掛け合いは 解説役（metan）× 聞き役・ツッコミ役（zunda）の役割分担。
  - 冒頭に導入（なぜ今この話題か）、末尾に「結局何が重要か」のまとめ。
  - 目安: 8〜14 シーン、セリフ合計 3,000〜4,500 字（≒ 5〜8 分）。
- **セリフ同期の段階表示** (`show_items`): bullets / outro は「そのセリフの間に
  見えている項目数」(単調非減少、最後は総数)、compare は 1=左のみ / 2=両方。
  title / code / quote には付けない。検証は script_gen、計画の組み立ては reveal.py。
- **表情・ポーズ**: 全セリフに `metan_pose` / `zunda_pose` を持たせる。セリフ開始時に
  切り替え、次のセリフ開始まで維持する。澪は視聴者向け説明、注意、軽い微笑みを、
  透は傾聴、疑問、照れ、理解、喜びを内容に応じて使い分ける。

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
  フォントは `fonts/` の ttf を `@font-face` で読む（環境非依存）。
  日本語見出しの改行は `budoux` で分節する。
- `code` テンプレは Pygments (monokai, インラインスタイル) でハイライトし、
  mac 風ウィンドウ枠に載せる。lexer はコード内容から判定（console / python / bash）。
- テンプレ型（固定パターン。クロノIT方式の「決まったテンプレの組み合わせ」）:

| template | 用途 | フィールド |
|---|---|---|
| `title`   | 表紙 | heading, subheading, source_label |
| `bullets` | 箇条書き板書 | heading, items[] (≤5, 各≤40字) |
| `compare` | 2 カラム対比 | heading, left_title, left_items[], right_title, right_items[] |
| `code`    | コード/コマンド例 | heading, code, caption |
| `quote`   | 原文引用 | heading, quote, attribution |
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
  - 音声: 無音 wav を挟んだ concat demuxer → AAC 192k
  - 最終フレームの duration 解釈で映像が総尺より延びるため `-t 総尺` で打ち切る
  - 字幕の分割・改行: 長いセリフは句点で分割して順に表示（1 表示 約 40 字まで、
    表示時間は文字数比で配分）。行の折り返しは libass がスペース無しの日本語を
    扱えないため、budoux の分節を 1 行 20 字以内に詰めて `\N` を自前で挿入する
  - 字幕テキストは `text`（表示用）を使う。読みテキスト (`reading`) は使わない
- BGM は v1 では入れない（ライセンス確認済みの音源を導入してから）。

### 3.6 cli.py — エントリポイント

```
PYTHONPATH=src uv run --frozen --with budoux --with pygments python -m lecture.cli generate <URL> [--out-dir tmp/lecture]
PYTHONPATH=src uv run --frozen --with budoux --with pygments python -m lecture.cli generate <URL> --script <path>  # 台本再利用
PYTHONPATH=src uv run --frozen --with budoux --with pygments python -m lecture.cli render <job_dir>  # script.json 以降だけ再実行
```

- 中間生成物（script.json / slides / audio）はジョブディレクトリに全て残し、
  **台本だけ手直しして再レンダリング**できるようにする（コード生成方式の利点 =
  差分修正可能性を確保する）。

## 4. 出力ディレクトリ

```
tmp/lecture/<job_id>/        # job_id = YYYYMMDD-HHMMSS-<slug>
├── source.txt               # 抽出本文（デバッグ用）
├── script.json              # 台本（編集して render で再合成可能）
├── slides/scene_01_s1.png ...  # 表示状態ごとの静止画
├── audio/scene_01_line_01.wav ...
├── subs.ass
└── video.mp4
```

## 5. 失敗時の方針

- 全ステージ Fail Fast（CLAUDE.md 方針どおり）。リトライは script_gen の
  JSON 不正時 1 回のみ。
- VOICEVOX エンジン未起動・claude CLI 不在は前提条件エラーとして
  起動方法を添えて即終了。

## 6. 現行システムから再利用するもの / しないもの

| 資産 | v1 プロトタイプ | 本運用統合時 |
|---|---|---|
| YouTube アップロード (`automator/youtube.py`) | 使わない | そのまま再利用 |
| サムネイル生成 (`thumbnail.py`, `image_gen.py`) | 使わない | そのまま再利用 |
| urls.yaml パーサ (`url_parser.py`) | 使わない（URL 直指定） | 再利用（`type: lecture` を追加） |
| NotebookLM 系 (`notebooklm*.py`) | 使わない | 使わない（音声要約系統専用） |
| フォント `fonts/NotoSansJP-Bold.ttf` | 使う | 使う |
| フォント `fonts/MPLUSRounded1c-{Bold,Black}.ttf` | 使う（Google Fonts, OFL。リポジトリ未収載なら DL） | 使う |

## 7. 本運用への統合方針（プロトタイプ検証後）

1. urls.yaml のエントリに `mode: lecture` を追加し、`automator run` から分岐する。
2. Phase 3（upload）は既存の video_ready ステートに合流させ、既存の
   プレイリスト振り分け・日次上限・サムネイル生成をそのまま通す。
3. VOICEVOX エンジンの自動起動/停止をパイプラインに組み込む。
4. キャラクター立ち絵・BGM・多言語化は本仕様の範囲外（クロノITの発展系として
   別途検討）。
