# NotebookLM → YouTube 自動化パイプライン 仕様書

## 1. プロジェクト概要

### 1.1 目的

ユーザーが URL リストを YAML ファイルに記載するだけで、以下が自動実行される CLI ツールを構築する。各 URL に音声の長さやプロンプトプリセットを個別指定することも可能。

1. NotebookLM でノートブックを作成し、URL をソースとして追加
2. 日本語の Audio Overview（ポッドキャスト形式の音声要約）を生成
3. 生成された音声を YouTube に公開動画としてアップロード

### 1.2 ユーザーストーリー

> 英語の論文やニュース記事の URL を YAML ファイルに記載して CLI コマンドを実行すると、数分後に YouTube の自分のチャンネルに日本語の音声要約がアップロードされている。URL ごとに音声の長さや解説スタイルを変えることもできる。移動中やスキマ時間に YouTube アプリで聴ける。

### 1.3 前提条件

| 項目 | 内容 |
|---|---|
| NotebookLM アカウント | Google Workspace（会社契約）のアカウント |
| YouTube アカウント | 個人の Google アカウント（YouTube チャンネル） |
| 実行環境 | macOS または Linux（Python 3.11+） |
| NotebookLM 操作方法 | Phase 1: `notebooklm-py`（非公式 CLI）、Phase 2: Playwright |

---

## 2. システムアーキテクチャ

### 2.1 全体フロー

```
urls.yaml                 (入力: URL + per-URL 設定)
    │
    ▼
┌─────────────────────────────────────────────────┐
│  audio-summary-uploader (Python CLI)             │
│                                                 │
│  1. URL パーサー                                 │
│     └─ urls.yaml を読み込み、UrlEntry リストを生成 │
│                                                 │
│  2. メタデータ取得                                │
│     └─ 各 URL からタイトル/サイト名を取得         │
│        (複数ソース・メール系は collect で補完)     │
│                                                 │
│  3. NotebookLM 操作 (notebooklm-py)              │
│     ├─ ノートブック作成                           │
│     ├─ 全ソース(1つ以上)を追加                    │
│     ├─ Audio Overview 生成（日本語指定）          │
│     ├─ chat で日本語タイトル/メール出典を抽出     │
│     ├─ 音声ファイル (.mp3) ダウンロード           │
│     └─ 動画変換完了後にノートブック削除           │
│                                                 │
│  4. カテゴリ判定＋サムネイル生成                  │
│     └─ AI画像生成(Nano Banana)で見出し入り        │
│        サムネ生成（OGP流用は廃止/失敗時は         │
│        グラデーションへフォールバック）           │
│                                                 │
│  5. 動画変換                                     │
│     └─ FFmpeg: 静止画 + mp3 → mp4（メタ除去）   │
│                                                 │
│  6. YouTube アップロード                          │
│     ├─ YouTube Data API v3 (videos.insert)       │
│     ├─ サムネイル設定 (thumbnails.set)            │
│     ├─ カテゴリ→プレイリスト振り分け             │
│     └─ 公開ステータス: 既定 unlisted              │
│                                                 │
│  7. 結果レポート                                  │
│     └─ 処理結果 + YouTube URL を出力             │
└─────────────────────────────────────────────────┘
```

### 2.2 ディレクトリ構成

```
audio-summary-uploader/
├── pyproject.toml
├── README.md
├── config/
│   └── settings.yaml             # アプリ設定
├── credentials/
│   ├── .gitkeep
│   ├── youtube_client_secret.json  # YouTube OAuth クライアント
│   └── youtube_token.json          # リフレッシュトークン（自動生成）
├── src/
│   └── automator/
│       ├── __init__.py
│       ├── cli.py                # CLI エントリポイント (Click)
│       ├── config.py             # 設定読み込み
│       ├── pipeline.py           # パイプライン全体のオーケストレーション
│       ├── url_parser.py         # URL リスト読み込み・バリデーション（複数ソース対応）
│       ├── metadata.py           # OGP メタデータ取得
│       ├── notebooklm.py         # NotebookLM 操作（抽象層）
│       ├── notebooklm_py_backend.py  # notebooklm-py による実装
│       ├── notebooklm_playwright_backend.py  # Playwright による実装（Phase 2）
│       ├── citation.py           # 出典抽出・公開テキストのサニタイズ
│       ├── category.py           # カテゴリ判定→サムネ配色/プレイリスト解決
│       ├── image_gen.py          # AIサムネ生成 (gemini-webapi / Nano Banana)
│       ├── thumbnail.py          # サムネイル生成 フォールバック (Pillow)
│       ├── video.py              # FFmpeg による動画変換
│       ├── youtube.py            # YouTube API 操作
│       ├── report.py             # 結果レポート生成
│       └── web/                  # Web ダッシュボード
│           ├── app.py            # FastAPI アプリ + バックグラウンドワーカー
│           ├── routes.py         # ルーティング + API ハンドラ
│           └── templates/        # Jinja2 テンプレート
├── fonts/
│   └── NotoSansJP-Bold.ttf       # 日本語フォント（サムネイル用）
├── tests/
│   ├── test_config.py
│   ├── test_url_parser.py
│   ├── test_pipeline_phases.py
│   └── test_web.py
└── tmp/                          # 一時ファイル（.gitignore 対象）
    ├── audio/
    ├── thumbnails/
    └── videos/
```

---

## 3. モジュール仕様

### 3.1 CLI (`cli.py`)

Click ベースの CLI インターフェース。3フェーズ分離アーキテクチャに対応。

```
# 3フェーズ一括実行（従来の run コマンド）
$ automator run urls.yaml
$ automator run urls.yaml --dry-run
$ automator run urls.yaml --force
$ automator run urls.yaml --retry-failed

# Phase 1: ノートブック作成＋音声生成開始（並列）
$ automator submit urls.yaml
$ automator submit urls.yaml --dry-run
$ automator submit urls.yaml --force

# Phase 2: 生成完了した音声をDL→サムネイル→動画変換
$ automator collect              # 完了チェックのみ
$ automator collect --poll       # 完了までポーリング待機
$ automator collect --timeout 900

# Phase 3: video_ready のジョブを YouTube にアップロード
$ automator upload

# 特定のURLだけ処理
$ automator run-single "https://example.com/article"

# YouTube 認証セットアップ
$ automator auth youtube

# NotebookLM 認証セットアップ
$ automator auth notebooklm

# 処理状況の確認（各ステータスのカウント表示）
$ automator status

# Web ダッシュボードを起動
$ automator web [--port 8080] [--config PATH]
```

### 3.2 設定読み込み (`config.py`)

**実装方針:**
- `settings.yaml` を `PyYAML` で読み込み、`dataclass` にマッピング
- 設定値のバリデーションは `dataclass` の `__post_init__` で実施
- 環境変数による上書きは行わない（`settings.yaml` を Single Source of Truth とする）

```python
@dataclass
class NotebookLMConfig:
    backend: str = "notebooklm-py"
    audio_language: str = "ja"
    audio_length: str = "short"
    generation_timeout_seconds: int = 1200
    generation_poll_interval_seconds: int = 10
    prompt_presets: dict[str, str] = field(default_factory=dict)

@dataclass
class YouTubeConfig:
    privacy_status: str = "unlisted"
    category_id: str = "27"
    playlist_id: str | None = None          # 既定（カテゴリ未設定時のフォールバック）
    playlists: dict[str, str] = field(default_factory=dict)  # カテゴリ→playlist_id
    all_playlist_id: str | None = None      # 全動画横断プレイリスト（常に追加）
    title_prefix: str = "🎧"
    title_max_length: int = 95
    generated_title_max_length: int = 35    # ② chat 生成タイトルの全角字数上限
    default_tags: list[str] = field(default_factory=list)
    daily_upload_limit: int = 5

@dataclass
class CredentialsConfig:
    youtube_client_secret: str = "./credentials/youtube_client_secret.json"
    youtube_token: str = "./credentials/youtube_token.json"

@dataclass
class Settings:
    notebooklm: NotebookLMConfig
    youtube: YouTubeConfig
    credentials: CredentialsConfig
    thumbnail: ThumbnailConfig
    general: GeneralConfig
```

### 3.3 URL パーサー (`url_parser.py`)

**入力形式:** YAML ファイル（URL リスト + per-URL 設定）

```yaml
# urls.yaml — URL だけ書けばデフォルト設定で動作
- url: https://arxiv.org/abs/2401.12345

- url: https://example.com/article
  audio_length: short
  prompt: paper_summary

- url: https://newsletter.example.com/issue-42
  audio_length: default

# ローカル PDF ファイル
- url: ~/Documents/papers/interesting-paper.pdf
  prompt: paper_summary

# フォルダ指定（中の全 PDF を処理）
- url: ~/Documents/papers/
  prompt: paper_summary

# 複数ソース→1音声（⑦）。任意 title、無ければ chat 生成。代表URLは先頭。
- title: 今週のAIニュースまとめ
  urls:
    - https://app.sparkmailapp.com/web-share/xxxx
    - https://www.theinformation.com/articles/yyyy
  prompt: paper_summary
```

**データモデル:**

```python
@dataclass
class UrlEntry:
    url: str                          # 代表URL（複数ソース時は先頭）またはローカルパス
    audio_length: str | None = None   # "short" or "default", None = settings.yaml のデフォルト
    prompt: str | None = None         # プリセット名, None = "default"
    title: str | None = None          # 複数ソース時の任意タイトル
    extra_urls: list[str] = field(default_factory=list)  # 2番目以降のソース

    @property
    def sources(self) -> list[str]:   # [url, *extra_urls]
        ...
```

**処理内容:**
- YAML ファイルを読み込み、各エントリをパース
- URL のバリデーション（`urllib.parse` で基本チェック）
- ローカルパスのバリデーション（ファイル存在確認、PDF 拡張子チェック）
- フォルダが指定された場合、中の `*.pdf` ファイルを個別エントリに展開
- `audio_length` の値バリデーション（`"short"` / `"default"` / `None` のみ許可）
- `prompt` の値バリデーション（`settings.yaml` の `prompt_presets` に定義されたキーのみ許可）
- 重複 URL の除去
- 処理済み URL のスキップ（状態ファイルとの照合）

**出力:** `list[UrlEntry]` — 有効な URL エントリのリスト

### 3.4 メタデータ取得 (`metadata.py`)

各 URL から OGP (Open Graph Protocol) メタデータを取得する。

**取得項目:**

```python
@dataclass
class PageMetadata:
    url: str
    title: str              # og:title or <title>
    description: str        # og:description or meta description
    og_image_url: str | None  # og:image
    site_name: str | None   # og:site_name
    language: str | None    # html lang attribute
    favicon_url: str | None  # <link rel="icon"> → /favicon.ico フォールバック
```

**実装方針:**
- `httpx` でページを取得し、`BeautifulSoup` で OGP タグをパース
- OGP が取得できない場合は `<title>` タグにフォールバック
- タイムアウト: 10秒
- User-Agent: 一般的なブラウザの User-Agent を使用（403 回避のため）
- ファビコン: `<link rel="icon">` を抽出、無ければ `/favicon.ico` にフォールバック（サムネイルのアイコン表示に使用）
- ローカルファイルの場合: ファイル名からタイトルを生成（OGP取得なし）。PDF は先頭 5 ページを走査して最大の埋め込み画像（25MP 以下）を `og_image_url` 相当として抽出を試み、失敗時は PDF アイコンを `favicon_url` に設定

### 3.5 NotebookLM 操作 (`notebooklm.py` + バックエンド)

**抽象インターフェース（Strategy パターン）:**

```python
from abc import ABC, abstractmethod

class NotebookLMBackend(ABC):
    @abstractmethod
    async def create_notebook(self, title: str) -> str:
        """ノートブックを作成し、notebook_id を返す"""
        ...

    @abstractmethod
    async def add_source(self, notebook_id: str, url: str) -> None:
        """ノートブックに URL ソースを追加する"""
        ...

    @abstractmethod
    async def add_file_source(self, notebook_id: str, file_path: Path) -> None:
        """ノートブックにローカルファイルをソースとして追加する"""
        ...

    @abstractmethod
    async def start_audio_generation(
        self, notebook_id: str, language: str = "ja",
        instructions: str = "", audio_length: str | None = None,
    ) -> str:
        """音声生成を開始し task_id を返す（完了を待たない）"""
        ...

    @abstractmethod
    async def check_audio_status(
        self, notebook_id: str, task_id: str,
    ) -> GenerationStatus:
        """生成ステータスを1回チェックする"""
        ...

    @abstractmethod
    async def wait_for_audio(
        self, notebook_id: str, task_id: str,
    ) -> GenerationStatus:
        """音声生成の完了をポーリングで待機する"""
        ...

    @abstractmethod
    async def generate_audio(
        self, notebook_id: str, language: str = "ja",
        instructions: str = "", audio_length: str | None = None,
    ) -> str:
        """Audio Overview を生成し、audio_id を返す（start + wait の組み合わせ）"""
        ...

    @abstractmethod
    async def ask(
        self, notebook_id: str, question: str,
        source_ids: list[str] | None = None,
    ) -> str:
        """ノートブックのソースにチャットで質問し回答を返す（出典/タイトル抽出用）"""
        ...

    @abstractmethod
    async def download_audio(self, notebook_id: str, output_path: Path) -> Path:
        """生成された音声をダウンロードする"""
        ...

    @abstractmethod
    async def delete_notebook(self, notebook_id: str) -> None:
        """ノートブックを削除する"""
        ...
```

**Phase 1 実装 (`notebooklm_py_backend.py`):**
- `notebooklm-py` CLI をサブプロセスとして呼び出す
- または `notebooklm-py` の Python API を直接利用

**Phase 2 実装 (`notebooklm_playwright_backend.py`):**
- Playwright で Chrome を操作
- Chrome DevTools Protocol (CDP) 経由で既存の Chrome セッションに接続
- NotebookLM の Web UI を操作してノートブック作成・音声生成

**Audio Overview 生成時の指示テキスト:**

`settings.yaml` の `prompt_presets` から、`UrlEntry.prompt`（デフォルト: `"default"`）に対応するプリセットを解決して `instructions` に渡す。

```
# prompt_presets.default の場合:
この内容を日本語で要約してポッドキャスト形式で説明してください。
専門用語は必要に応じて英語のまま使ってください。

# prompt_presets.paper_summary の場合:
論文の詳細な解説をポッドキャスト形式で行ってください。
リスナーの専門分野や知識レベルに合わせた解説を行います。
```

**音声の長さ:**

`UrlEntry.audio_length` が指定されている場合はその値を、未指定の場合は `settings.yaml` の `notebooklm.audio_length` の値を `generate_audio` の `audio_length` パラメータに渡す。`"default"` は NotebookLM の「デフォルト」（長め）に対応する。

**音声生成の待機:**
- 生成完了までポーリング（`generation_poll_interval_seconds` 間隔、最大 `generation_timeout_seconds` 秒 = デフォルト 1200 秒）
- 生成ステータスが「完了」になったらダウンロード
- タイムアウトや一時的なネットワークエラーの場合は terminal 扱いにせず `generating` を維持し、次回の collect で再試行する（生成自体は継続中の可能性があるため。ノートブックも残す）
- ステータスが `failed`（terminal）の場合は `failed` に遷移し、ノートブックを削除する。`not_found` は作成直後の一時的な lag の可能性があるため単発では terminal とみなさず、ポーリング側の連続判定（notebooklm-py: 5 回連続 + 10 秒）に委ねる

### 3.6 サムネイル生成（`image_gen.py` 主 / `thumbnail.py` フォールバック / `category.py`）

YouTube サムネ（1280×720px）は **AI 画像生成（Nano Banana）で日本語見出し入りを毎回生成**する（④）。
OGP 画像の流用は廃止（他者サムネの著作権・体裁の問題を回避）。AI 生成に失敗した場合のみ Pillow の
グラデーション背景＋日本語見出しにフォールバックする。

**カテゴリ判定（`category.py`, ③）:**
- まず URL のドメイン/拡張子のルールで判定（arxiv/PDF→`paper`、Spark/ニュースレター→`news`、
  github 等→`engineering`、youtube→`business`、その他→`default`）。
- **曖昧なカテゴリ（`business`/`default`、＝ドメインだけでは内容が判らないもの）のみ、collect で
  NotebookLM chat に内容を 5 カテゴリから1語で選ばせて再判定**（C）。例: AI 研究の YouTube 動画は
  `business` ではなく `paper`/`engineering` に補正され、ハッシュタグの的外れ（#副業 等）を防ぐ。
  確定カテゴリ（arxiv/spark/github 等）は chat を呼ばない。chat 失敗/解析不可ならルール結果を使う。
- カテゴリは (1) サムネの配色スタイル、(2) プレイリスト振り分け（`youtube.playlists`）の両方に使う。

**AIサムネ生成（`image_gen.py`, 方式A）:**
- cookie 源 = NotebookLM と同一アカウントの `~/.notebooklm/profiles/default/storage_state.json` の
  `__Secure-1PSID` / `__Secure-1PSIDTS`（`.google.com`）。値はログに出さない。
- `gemini-webapi`（非公式）で「16:9・カテゴリ別配色＋日本語見出しをそのまま描画」を指示して生成し、
  生成画像（約 2752×1536）を 1280×720 へ中央クロップして PNG 保存。見出しは焼き込み前に
  `sanitize_public_text` で個人情報を除去する（画像は公開面で唯一の非サニタイズ面のため）。
- **失敗時は `None` を返しフォールバックへ**（cookie 失効・地域/アカウント制限・画像未返却など）。
  非公式 API のため例外は握りつぶし、パイプラインは止めない。
- **cookie 鮮度の制約**: `__Secure-1PSIDTS` は短時間で値がローテーションし、storage_state.json の
  コピーは `notebooklm login`（ブラウザ）でしか更新されない。古くなると `UNAUTHENTICATED` となり
  画像が返らずフォールバックする（理由は応答テキストとして WARN ログに残す）。確実に AI サムネを
  出すには直前に `uv run notebooklm login` で cookie を更新する（将来 browser-cookie3 等での自動
  鮮度確保を検討）。

**フォールバック（`thumbnail.py`, 方式B）:**
- ランダムなグラデーション背景＋日本語見出し（NotoSansJP-Bold、長さに応じた自動サイズ・影つき）を
  中央に描画。中央アイコン（favicon）は見出しと重なるため使わない。OGP も使わない。

**実装:** `gemini-webapi`（AI生成）/ `Pillow`（フォールバック）

### 3.7 動画変換 (`video.py`)

YouTube は音声のみのアップロードに対応していないため、静止画+音声で動画ファイルを作成する。

**FFmpeg コマンド:**
```bash
ffmpeg -loop 1 -i thumbnail.png -i audio.mp3 \
  -map_metadata -1 \
  -c:v libx264 -tune stillimage -c:a aac -b:a 192k \
  -pix_fmt yuv420p -shortest -movflags +faststart \
  output.mp4
```

**実装:** `subprocess` で FFmpeg を呼び出し

**要件:**
- 入力: サムネイル画像 (PNG) + 音声ファイル (MP3)
- 出力: MP4 (H.264 + AAC)
- 音声ビットレート: 192kbps
- FFmpeg がインストールされていない場合はエラーメッセージを表示
- **メタデータ除去（⑥）**: `-map_metadata -1` で入力（NotebookLM の mp3 等）のメタデータを引き継がず、
  出力 mp4 にローカルパス・個人情報・元タイトル等を残さない（自動テストで担保）

### 3.8 YouTube アップロード (`youtube.py`)

**認証フロー（初回セットアップ）:**
1. Google Cloud Console で OAuth 2.0 クライアント ID を作成
2. `youtube_client_secret.json` を `credentials/` に配置
3. `automator auth youtube` を実行
4. ブラウザでOAuth同意画面が開き、YouTube アカウントで認証
5. リフレッシュトークンが `credentials/youtube_token.json` に保存
6. 以降は自動的にトークンリフレッシュ

**アップロード時のメタデータ:**

```python
@dataclass
class YouTubeUploadParams:
    file_path: Path               # mp4 ファイルパス
    title: str                    # "{title_prefix} {日本語タイトル}"（②）
    description: str              # 冒頭 + 出典 + ハッシュタグ（⑤、サニタイズ済み）
    tags: list[str]               # ["NotebookLM", "Audio Summary", "AI", ...]
    category_id: str = "27"       # Education カテゴリ
    privacy_status: str = "unlisted"
    default_language: str = "ja"
    thumbnail_path: Path | None = None
    playlist_ids: list[str] = field(default_factory=list)  # 追加先プレイリスト ID 一覧（③カテゴリ解決＋全動画横断）
    made_for_kids: bool = False       # "No, it's not made for kids"
    contains_synthetic_media: bool = True  # AI生成コンテンツラベル
```

**YouTube タイトルの形式:**
```
{settings.youtube.title_prefix} {日本語タイトル（generated_title_max_length 全角字以内）}
```

- タイトルは **② で collect フェーズに NotebookLM チャット（`ask`）から日本語生成**する。引用マーカー
  （`[1]`）・全体を囲う引用符・先頭絵文字を除去し、全角 `generated_title_max_length` 字に丸める。
  chat 失敗時は元タイトル（メール系は抽出した件名）にフォールバックする。
- YouTube API が拒否する文字（`<`, `>`）は全角（`＜`, `＞`）に自動置換し、出力前に
  `sanitize_public_text` で個人情報を除去する。

**YouTube 説明文テンプレート（⑤）:**
```
AIが元情報をもとに自動生成した、ポッドキャスト風の音声要約です。
通勤や作業のお供にどうぞ。

📄 元記事: {URL}            # 複数ソースは全URLを列挙
📰 ソース: {サイト名}        # 単一・非メール時のみ

#AI #論文解説 #機械学習      # カテゴリ別ハッシュタグ（3〜5）

---
※ NotebookLM の Audio Overview で自動生成。要点把握用です。正確な内容は元情報をご確認ください。
```

- **内部設定（プロンプト名・音声長）は公開面に出さない**（旧テンプレの「🔧 生成条件」は廃止）。
- 説明文・タイトル・サムネ見出しは出力前に**個人情報をサニタイズ**する（メールアドレス・Spark 共有 URL を除去）。
- **複数ソース（⑦）**: `extra_urls` を含む全ソースを列挙する。メール系（Spark 共有）が混じる場合は
  当該 URL を出さず「📰 ソース: メールニュースレター」を表示する。
- **メール系ソース（Spark 共有リンク）**: 生の共有 URL は公開面に出さない。collect で NotebookLM
  チャット（`ask`）から件名・送信元・日付を抽出し、説明文に「出典: {送信元}（{ドメイン}）- {日付}」を
  表示する（生 URL は state.json のみに保持）。抽出失敗時も生 URL は出さない。
- **プレイリスト振り分け（③）**: カテゴリ→`youtube.playlists` で解決し、無ければ `playlist_id` にフォールバックする。

**アップロード手順:**
1. `videos.insert` で動画をアップロード（resumable upload）
2. `thumbnails.set` でカスタムサムネイルを設定
3. `playlist_id` が指定されている場合、`playlistItems.insert` で動画をプレイリストに追加
4. `selfDeclaredMadeForKids: false` を常に設定（子供向けではない）
5. `containsSyntheticMedia: true` を常に設定（AI生成コンテンツの開示）
6. アップロード後の YouTube URL を返却
7. NotebookLM のノートブックは collect フェーズの動画変換完了時点で削除済み（`run-single` のみアップロード完了後に削除）

手順 2〜3（サムネイル設定・プレイリスト追加）の失敗は WARN ログに留め、ジョブは
`uploaded` として扱う。動画本体は `videos.insert` で既にアップロード済みのため、
ここで failed にするとリトライで同じ動画が重複アップロードされてしまう。

**認証コンテキスト:**
- CLI（対話可能）: トークンが無効な場合はブラウザ OAuth フローを開始する
- Web サーバー（非対話、`allow_interactive_auth=False`）: OAuth フローを開始せず
  即座にエラーにし、該当ジョブを failed として記録する（`auth youtube` での再認証を促す）

**クォータ管理:**
- `videos.insert` = 1,600 ユニット
- `thumbnails.set` = 50 ユニット
- `playlistItems.insert` = 50 ユニット
- 1URLあたり合計 ≈ 1,700 ユニット
- デフォルトクォータ 10,000/日 → 1日あたり最大5本
- 1回の upload 実行あたり `daily_upload_limit` 件で停止する（残りは `video_ready` のまま次回実行に持ち越し。API のクォータ残量チェックは行わない）

### 3.9 パイプラインオーケストレーション (`pipeline.py`)

**3フェーズアーキテクチャ:**

パイプラインは3つの独立したフェーズに分離されている:

1. **submit**: ノートブック作成＋音声生成開始（並列実行、完了を待たない）
2. **collect**: 生成完了チェック＋音声DL＋サムネイル＋動画変換（並列実行）
3. **upload**: YouTube アップロード（順次実行、quota制限あり）

```
submit_urls()     → status: "generating"
collect_audio()   → status: "video_ready"
upload_videos()   → status: "uploaded"
run_pipeline()    → 3フェーズを順に実行（従来互換）
```

**async の扱い:**
- NotebookLM バックエンドの操作（ネットワーク I/O）: `async` ネイティブ
- メタデータ取得（httpx）: `async` ネイティブ
- サムネイル生成（Pillow、CPU バウンド）: `asyncio.to_thread` でラップ
- 動画変換（FFmpeg サブプロセス）: `asyncio.create_subprocess_exec` で非同期実行
- YouTube アップロード（google-api-python-client、同期ライブラリ）: `asyncio.to_thread` でラップ

**slug 生成ルール:**
- URL の SHA-256 ハッシュの先頭 12 文字を使用
- 例: `https://arxiv.org/abs/2401.12345` → `a1b2c3d4e5f6`
- 一意性を担保しつつ、ファイル名として安全な文字列を生成

**Phase 1: submit_urls(entries, settings, force, dry_run)**
1. state.json をロード、生成中/処理済みのURLをスキップ（`--force`で上書き）
2. 各URLに対して `asyncio.gather` で並列実行:
   - 既存ジョブに `notebook_id` が残っていれば best-effort で旧ノートブックを削除（リトライ・force 再実行時のリーク防止）
   - メタデータ取得 → ノートブック作成（作成直後に `notebook_id` を永続化）→ ソース追加 → `start_audio_generation()`
   - state に `status="generating"` + `task_id` + `metadata` を保存
3. 各URLのエラーは個別にキャッチして `failed` として記録
4. `--dry-run` は state.json に一切書き込まない（本実行のスキップ判定や collect を汚染しないため）

**state 書き込みの原則（全フェーズ共通）:**
ジョブ更新は必ずディスク上の最新 state を読み直してから該当ジョブのみ更新して
保存する（`_update_job_state` / `_upsert_job_state`）。メモリ上の古い state
スナップショット全体を書き戻すと、並行する Web 操作（削除・クリア・リトライ・追加）を
巻き戻してしまうため。

**Phase 2: collect_audio(settings, poll, timeout)**
1. state.json から `status="generating"` のジョブを取得
2. `notebook_id` / `task_id` が無いジョブ（submit 中断）は明示的なエラーで `failed` に遷移
3. 各ジョブに対して並列で `check_audio_status()` を呼び出し
4. terminal な `failed` ステータス: `failed` に遷移 + ノートブック削除（`--poll` の有無に関わらず）。`not_found` は一時的 lag の可能性があるため単発では terminal 扱いしない（§3.5 参照）
5. 完了したジョブ: 音声DL → サムネイル → 動画変換 → ノートブック削除 → `status="video_ready"`（`notebook_id` をクリア）
6. 未完了ジョブ: `--poll` あり → `wait_for_audio` で待機（タイムアウト時は `generating` 維持で次回再試行）/ なし → ステータス報告のみ
7. 例外で `failed` に遷移する際は、残存ノートブックを best-effort で削除する

**Phase 3: upload_videos(settings, allow_interactive_auth)**
1. state.json から `status="video_ready"` のジョブを取得
2. YouTube認証（1回、`asyncio.to_thread` でラップ）→ 各ジョブを順次アップロード（`daily_upload_limit` 件で停止）
3. `status="uploaded"` + `youtube_url` を記録

**エラーハンドリング:**

CLAUDE.md の Fail Fast 原則に基づき、以下のように粒度を分ける:

- **URL 間**: 1つの URL が失敗しても他の URL の処理は継続（catch & continue）
- **URL 内の各ステップ**: Fail Fast。予期しないエラーは即座にその URL の処理を中断し、`failed` として記録
- 最終的な結果レポートに成功/失敗を記録

**並列処理:**
- submit フェーズ: 全URLの音声生成を `asyncio.gather` で並列に開始
- collect フェーズ: 全ジョブのステータスチェック＋後処理を並列実行
- upload フェーズ: YouTube のクォータ制限を考慮し順次実行（`daily_upload_limit` に従う）

### 3.10 状態管理

処理の再開やスキップのために、状態ファイルを管理する。3フェーズ分離に対応した jobs スキーマを使用。

**状態ファイル（`state.json`）:**
```json
{
  "last_run": "2026-03-08T12:00:00Z",
  "jobs": [
    {
      "url": "https://example.com/article-1",
      "slug": "a1b2c3d4e5f6",
      "audio_length": "default",
      "prompt": "default",
      "status": "uploaded",
      "notebook_id": "abc123",
      "task_id": "task_xyz",
      "metadata": {
        "title": "Article Title",
        "description": "...",
        "og_image_url": "https://...",
        "site_name": "Example",
        "language": "en"
      },
      "audio_path": "./tmp/audio/a1b2c3d4e5f6.mp3",
      "thumbnail_path": "./tmp/thumbnails/a1b2c3d4e5f6_thumb.png",
      "video_path": "./tmp/videos/a1b2c3d4e5f6.mp4",
      "youtube_url": "https://youtu.be/xyz789",
      "error": null,
      "submitted_at": "2026-03-08T12:00:00Z",
      "collected_at": "2026-03-08T12:05:00Z",
      "uploaded_at": "2026-03-08T12:06:00Z"
    }
  ]
}
```

**ステータスライフサイクル:**
- `queued` — キューに追加済み、処理待ち（Web GUI 使用時）
- `generating` — submit完了、音声生成中
- `video_ready` — collect完了、MP4ファイル準備済み
- `uploaded` — upload完了（最終成功状態）
- `failed` — いずれかのフェーズでエラー

**ポイント:**
- 生成中・処理済み URL はスキップ（`--force` で上書き可能）
- 失敗した URL は `--retry-failed` で再処理可能
- 旧 `state.json`（`processed` キー）は初回ロード時に自動マイグレーション
- 状態ファイルはアトミック書き込み（一時ファイル→rename）

### 3.11 結果レポート (`report.py`)

処理完了後にターミナルに結果を出力する。成功判定は `error` の有無で行う
（`submit` / `collect` 単体実行時の `generating` / `video_ready` などの
フェーズ途中ステータスも成功として扱い、YouTube URL が無い場合は
ステータスを括弧書きで表示する）。

```
════════════════════════════════════════════════════
 NotebookLM → YouTube Automator  処理結果
════════════════════════════════════════════════════

✅ 成功: 3/4

  1. ✅ Understanding Transformer Architecture
     📺 https://youtu.be/abc123

  2. ✅ The Future of AI Regulation
     📺 https://youtu.be/def456

  3. ✅ Weekly Tech Newsletter #42
     📺 https://youtu.be/ghi789

  4. ❌ https://example.com/paywalled-article
     ⚠️  Error: Source could not be added (paywall detected)

════════════════════════════════════════════════════
```

---

## 4. 設定ファイル

### 4.1 `config/settings.yaml`

```yaml
# NotebookLM 設定
notebooklm:
  backend: "notebooklm-py"  # "notebooklm-py" or "playwright"
  audio_language: "ja"
  audio_length: "short"     # グローバルデフォルト: "short" | "default"
  generation_timeout_seconds: 1200   # Audio Overview 生成のタイムアウト (default長の音声は10分以上かかる場合がある)
  generation_poll_interval_seconds: 10

  prompt_presets:
    default: >
      この内容を日本語で要約してポッドキャスト形式で説明してください。
      専門用語は必要に応じて英語のまま使ってください。
    paper_summary: >
      論文の詳細な解説をポッドキャスト形式で行います。
      リスナーの専門分野や知識レベルに合わせた解説を行います。

# YouTube 設定
youtube:
  privacy_status: "unlisted"
  category_id: "27"              # Education
  playlist_id: null                # 既定（カテゴリ未設定時のフォールバック）
  playlists:                       # カテゴリ→playlist_id（③）
    paper: "PLxxxx"
    news: "PLxxxx"
    engineering: "PLxxxx"
    business: "PLxxxx"
  all_playlist_id: "PLxxxx"        # 全動画横断プレイリスト（常に追加）
  title_prefix: "🎧"
  title_max_length: 95
  default_tags:
    - "NotebookLM"
    - "Audio Summary"
    - "AI"
    - "音声要約"
  daily_upload_limit: 5          # クォータ制限に基づく安全マージン（プレイリスト追加含む）

# サムネイル設定
thumbnail:
  width: 1280
  height: 720
  overlay_opacity: 0.5           # 暗めフィルターの不透明度
  font_name: "NotoSansJP-Bold"
  title_font_size_max: 80
  title_font_size_min: 44
  subtitle_font_size: 24
  text_color: "#FFFFFF"
  # OGP画像がない場合のフォールバックはランダム生成グラデーション (設定不要)

# 認証情報パス
credentials:
  youtube_client_secret: "./credentials/youtube_client_secret.json"
  youtube_token: "./credentials/youtube_token.json"

# 一般設定
general:
  tmp_dir: "./tmp"
  state_file: "./data/state.json"
  max_retries: 3
  retry_backoff_base: 2          # 指数バックオフの底（秒）
```

### 4.2 認証情報パス

認証情報のパスは `settings.yaml` の `credentials` セクションで一元管理する。`.env` ファイルは使用しない。

---

## 5. 技術スタック

| カテゴリ | 技術 | バージョン | 用途 |
|---|---|---|---|
| 言語 | Python | 3.11+ | メイン言語 |
| CLI フレームワーク | Click | 8.x | コマンドライン |
| NotebookLM 操作 (Phase 1) | notebooklm-py | latest | 非公式 CLI/SDK |
| NotebookLM 操作 (Phase 2) | Playwright | latest | ブラウザ自動化 |
| HTTP クライアント | httpx | 0.27+ | メタデータ取得 |
| HTML パーサー | beautifulsoup4 | 4.x | OGP 解析 |
| 画像処理 | Pillow | 10.x | サムネイル生成 |
| 動画変換 | FFmpeg | 6.x+ | mp3 → mp4 |
| YouTube API | google-api-python-client | 2.x | アップロード |
| 認証 | google-auth-oauthlib | 1.x | OAuth 2.0 |
| 設定 | PyYAML | 6.x | YAML 設定読み込み |
| 非同期処理 | asyncio | stdlib | パイプライン制御 |
| Web フレームワーク | FastAPI | 0.115+ | Web ダッシュボード |
| ASGI サーバー | uvicorn | 0.32+ | Web サーバー |
| テンプレート | Jinja2 + htmx + Pico CSS | — | SSR + インタラクション |
| テスト | pytest + pytest-asyncio | — | ユニットテスト |
| パッケージ管理 | uv | latest | 依存関係管理 |

---

## 6. 初期セットアップ手順

### 6.1 前提ソフトウェア

```bash
# 1. uv のインストール（未インストールの場合）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. FFmpeg のインストール
# macOS
brew install ffmpeg
# Ubuntu/Debian
sudo apt install ffmpeg

# 3. 日本語フォントの配置
# Noto Sans JP を https://fonts.google.com/noto/specimen/Noto+Sans+JP からDL
# fonts/ ディレクトリに NotoSansJP-Bold.ttf を配置
```

### 6.2 プロジェクトセットアップ

```bash
# リポジトリクローン後
cd audio-summary-uploader

# 依存関係のインストール（.venv は uv が自動作成）
uv sync
```

### 6.3 NotebookLM 認証（notebooklm-py）

```bash
# notebooklm-py のセットアップに従う
# Google Workspace アカウントでログイン済みの状態が必要
automator auth notebooklm
```

### 6.4 YouTube API 認証

```bash
# 1. Google Cloud Console (https://console.cloud.google.com) で:
#    - 新しいプロジェクトを作成
#    - YouTube Data API v3 を有効化
#    - OAuth 2.0 クライアント ID を作成（デスクトップアプリ）
#    - JSON をダウンロード

# 2. クライアントシークレットを配置
cp ~/Downloads/client_secret_xxxxx.json ./credentials/youtube_client_secret.json

# 3. 認証フローを実行（ブラウザが開く）
automator auth youtube
# → 個人の YouTube アカウントで認証
```

---

## 7. Phase 計画

### Phase 1: MVP（notebooklm-py ベース）

**スコープ:**
- YAML ファイルから URL + per-URL 設定を読み込み
- notebooklm-py でノートブック作成 → Audio Overview 生成 → ダウンロード
- OGP 画像 + タイトルでサムネイル生成
- FFmpeg で MP4 変換
- YouTube Data API v3 でアップロード
- 状態管理（処理済みスキップ）
- 結果レポート出力

**リスク:**
- notebooklm-py は Google の内部 API に依存しており、突然動作しなくなる可能性がある

### Phase 2: Playwright 移行

**スコープ:**
- NotebookLM のバックエンドを Playwright ベースに切り替え
- Chrome DevTools Protocol (CDP) で既存 Chrome セッションに接続
- UI 操作による安定したノートブック作成・音声生成
- notebooklm-py と Playwright を設定で切り替え可能

**トリガー:**
- notebooklm-py が動作しなくなった場合
- より安定した運用が必要になった場合

### Phase 3: 機能拡張（将来）

- CSV/スプレッドシート入力対応
- YouTube プレイリスト自動整理
- 定期実行（cron / スケジューラ連携）
- ~~Web UI ダッシュボード~~ → **実装済み**（`automator web`、詳細は `specs/GUI_SPEC.md`）

---

## 8. 制約事項・注意点

### 8.1 NotebookLM 関連

- `notebooklm-py` は非公式ツールであり、Google の内部 API 変更で動作しなくなるリスクがある
- Audio Overview の生成時間は内容量やサーバー負荷により変動する（通常2〜8分）
- Google Workspace アカウントの NotebookLM 利用規約に準拠すること
- 大量のノートブック作成はレート制限に引っかかる可能性がある

### 8.2 YouTube 関連

- デフォルトのアップロードクォータは 1日10,000ユニット（安全マージン込みで最大5動画/日）
- クォータ増加申請には Google の審査が必要（数日〜数週間）
- カスタムサムネイルの設定にはチャンネルの電話番号認証が必要
- 著作権のある素材をそのまま使う場合は注意が必要

### 8.3 アカウント分離

- NotebookLM: 会社の Google Workspace アカウント
- YouTube: 個人の Google アカウント
- 2つのアカウントの認証情報を別々に管理する必要がある
- YouTube の OAuth トークンは個人アカウント側で取得すること
