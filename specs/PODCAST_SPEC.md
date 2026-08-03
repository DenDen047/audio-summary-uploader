# ポッドキャスト音声要約パイプライン 仕様書

## 1. プロジェクト概要

### 1.1 目的

ユーザーが URL リストを YAML ファイルまたは Web UI に入力すると、Gemini Notebook（旧 NotebookLM。2026-07 改名、以下 NotebookLM と表記）の音声要約から、ポッドキャスト風の動画一式を作成して YouTube へ投稿する（`mode: podcast`）。

1. NotebookLM でノートブックを作成し、URL をソースとして追加
2. 日本語の Audio Overview（ポッドキャスト形式の音声要約）を生成
3. 生成された音声を YouTube に公開動画としてアップロード

### 1.2 本書の範囲

本書は `podcast` モードの生成内容と、両モード共通のオーケストレーション（URL 入力・3フェーズ・state.json・YouTube アップロード）を定義する。同じ基盤の上に澪・透の掛け合い講義動画を生成する `lecture` モードが同居するが、その生成内容は `specs/LECTURE_SPEC.md`、Web UI は `specs/GUI_SPEC.md` を正とする。

### 1.3 ユーザーストーリー

> 英語の論文やニュース記事の URL を YAML ファイルに記載して CLI コマンドを実行すると、数分後に YouTube の自分のチャンネルに日本語の音声要約がアップロードされている。URL ごとに音声の長さや解説スタイルを変えることもできる。移動中やスキマ時間に YouTube アプリで聴ける。

### 1.4 前提条件

| 項目 | 内容 |
|---|---|
| Gemini Notebook（旧 NotebookLM）アカウント | Google Workspace（会社契約）のアカウント |
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
│     ├─ chat で日本語タイトル/メール出典/論文略称  │
│     ├─ 音声ファイル (.mp3) ダウンロード           │
│     └─ 全 chat 完了後、画像生成前にノートブック削除│
│                                                 │
│  4. カテゴリ判定＋サムネイル生成                  │
│     └─ 話題連動AIベース画像＋3層テキストを        │
│        Pillow 合成（AI失敗時はマスコットに縮退）  │
│        （サムネベース・本編背景とも AI 生成）     │
│                                                 │
│  5. 動画変換                                     │
│     └─ FFmpeg: 画像 + mp3 → mp4                 │
│        （FFT EQ・背景ローテーション・メタ除去）  │
│                                                 │
│  6. YouTube アップロード                          │
│     ├─ YouTube Data API v3 (videos.insert)       │
│     ├─ サムネイル設定 (thumbnails.set)            │
│     ├─ カテゴリ→プレイリスト振り分け             │
│     └─ 公開ステータス: 既定 public                │
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
│   ├── sources/                  # 情報源の取得層（podcast / lecture 両モード共通）
│   │   ├── fetch.py              # URL→本文・図の抽出 + 投入形式の解決 (resolve_source)
│   │   └── sanitize.py           # 公開テキストのサニタイズ・Spark URL 判定
│   ├── summary/                  # 音声要約パイプライン（本仕様の対象）
│   │   ├── __init__.py
│   │   ├── cli.py                # CLI エントリポイント (Click)
│   │   ├── config.py             # 設定読み込み
│   │   ├── pipeline.py           # パイプライン全体のオーケストレーション
│   │   ├── url_parser.py         # URL リスト読み込み・バリデーション（複数ソース対応）
│   │   ├── metadata.py           # OGP メタデータ取得
│   │   ├── notebooklm.py         # NotebookLM 操作（抽象層）
│   │   ├── notebooklm_py_backend.py  # notebooklm-py による実装
│   │   ├── citation.py           # NotebookLM chat 回答からの出典・略称抽出
│   │   ├── category.py           # カテゴリ判定→背景配色/プレイリスト解決
│   │   ├── image_gen.py          # AIサムネベース/背景生成 (gemini-webapi / Nano Banana)
│   │   ├── thumbnail.py          # サムネ合成 3層テキスト装飾 (Pillow)
│   │   ├── video.py              # FFmpeg による動画変換
│   │   ├── youtube.py            # YouTube API 操作
│   │   ├── report.py             # 結果レポート生成
│   │   └── prompts/              # NotebookLM chat・画像生成の定型プロンプト (md)
│   ├── lecture/                  # 講義動画パイプライン（specs/LECTURE_SPEC.md）
│   └── webui/                    # 共通 Web ダッシュボード（specs/GUI_SPEC.md）
│       ├── cli.py                # webui エントリポイント
│       ├── app.py                # FastAPI アプリ + バックグラウンドワーカー
│       ├── routes.py             # ルーティング + API ハンドラ
│       └── templates/            # Jinja2 テンプレート
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

```bash
# 3フェーズ一括実行（従来の run コマンド）
$ podcast run urls.yaml
$ podcast run urls.yaml --dry-run
$ podcast run urls.yaml --force
$ podcast run urls.yaml --retry-failed

# Phase 1: ノートブック作成＋音声生成開始（並列）
$ podcast submit urls.yaml
$ podcast submit urls.yaml --dry-run
$ podcast submit urls.yaml --force

# Phase 2: 生成完了した音声をDL→サムネイル→動画変換
$ podcast collect              # 完了チェックのみ
$ podcast collect --poll       # 完了までポーリング待機
$ podcast collect --timeout 900

# Phase 3: video_ready のジョブを YouTube にアップロード
$ podcast upload

# 特定のURLだけ処理
$ podcast run-single "https://example.com/article"

# YouTube 認証セットアップ
$ podcast auth youtube

# NotebookLM 認証セットアップ
$ podcast auth notebooklm

# 処理状況の確認（各ステータスのカウント表示）
$ podcast status

# Web ダッシュボードを起動
$ webui [--port 8080] [--config PATH]
```

### 3.2 設定読み込み (`config.py`)

**実装方針:**
- `settings.yaml` を `PyYAML` で読み込み、`dataclass` にマッピング
- 設定値のバリデーションは `dataclass` の `__post_init__` で実施
- 環境変数による上書きは行わない（`settings.yaml` を Single Source of Truth とする）

```python
@dataclass
class PodcastConfig:
    backend: str = "notebooklm-py"
    audio_language: str = "ja"
    audio_length: str = "short"
    generation_timeout_seconds: int = 1200
    generation_poll_interval_seconds: int = 10
    collect_concurrency: int = 2       # 同時に動かす ffmpeg エンコード数
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
    podcast: PodcastConfig
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
    mode: str = "podcast"          # "lecture" | "podcast"
    audio_length: str | None = None   # "short" or "default", None = settings.yaml のデフォルト
    prompt: str | None = None         # プリセット名, None = "default"
    title: str | None = None          # 複数ソース時の任意タイトル
    extra_urls: list[str] = field(default_factory=list)  # 2番目以降のソース
    privacy_status: str | None = None # Webで選んだ公開範囲。NoneはYouTube設定値

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
- `mode` の値バリデーション（`"lecture"` / `"podcast"` のみ許可）
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
- 抽出済みソース（Spark 共有等、`sources.fetch.resolve_source` が `ExtractedSource` を返すもの）の場合: OGP は取得しない（共有ページは宣伝シェルしか返さない）。SSR から抽出した件名を `title` に、`site_name="メールニュースレター"` を設定する

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
    async def add_text_source(
        self, notebook_id: str, title: str, content: str,
    ) -> None:
        """抽出済みテキストをソースとして追加する（Spark 共有ページ等）"""
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

`UrlEntry.audio_length` が指定されている場合はその値を、未指定の場合は `settings.yaml` の `podcast.audio_length` の値を `generate_audio` の `audio_length` パラメータに渡す。`"default"` は NotebookLM の「デフォルト」（長め）に対応する。

**音声生成の待機:**
- 生成完了までポーリング（`generation_poll_interval_seconds` 間隔、最大 `generation_timeout_seconds` 秒 = デフォルト 1200 秒）
- 生成ステータスが「完了」になったらダウンロード
- タイムアウトや一時的なネットワークエラーの場合は terminal 扱いにせず `generating` を維持し、次回の collect で再試行する（生成自体は継続中の可能性があるため。ノートブックも残す）
- ステータスが `failed`（terminal）の場合は `failed` に遷移し、ノートブックを削除する。`not_found` は作成直後の一時的な lag の可能性があるため単発では terminal とみなさず、ポーリング側の連続判定（notebooklm-py: 5 回連続 + 10 秒）に委ねる

### 3.6 サムネイル生成（`thumbnail.py` 主 / `category.py` / AI背景は `image_gen.py`）

YouTube サムネ（1280×720px）は **固定マスコットを参照画像として毎回 AI 生成にかけ、キャラの同一性（大きな驚き顔）は保ちつつ、ポーズ・前景の大きな小道具・配色を話題ごとに変えたベース画像を作り、その上に高密度3層テキストを Pillow で合成**する（④）。キャラは同じでブランドを保ちながら、ポーズ・小物・色が話題ごとに変わるので**縮小時（一覧表示）でも各動画が見分けられる**。AI 生成が失敗（cookie 失効・地域制限等）した場合は**固定マスコット素材（静止）**に縮退し、それも無ければグラデーションに縮退する。マスコット縮退のおかげで、失敗時も「退屈なグラデ量産」にはならずブランド統一の見た目を保つ。伸びている日本語 AI 解説チャンネル（mikimiki / 本気AI 等）の「型」を TTP した設計。

**カテゴリ判定（`category.py`, ③）:**
- まず URL のドメイン/拡張子のルールで判定（arxiv/PDF→`paper`、Spark/ニュースレター→`news`、github 等→`engineering`、youtube→`business`、その他→`default`）。
- **曖昧なカテゴリ（`business`/`default`、＝ドメインだけでは内容が判らないもの）のみ、collect でNotebookLM chat に内容を 5 カテゴリから1語で選ばせて再判定**（C）。例: AI 研究の YouTube 動画は`business` ではなく `paper`/`engineering` に補正され、ハッシュタグの的外れ（#副業 等）を防ぐ。確定カテゴリ（arxiv/spark/github 等）は chat を呼ばない。chat 失敗/解析不可ならルール結果を使う。
- カテゴリは (1) サムネベース画像／動画本編背景の配色スタイル、(2) プレイリスト振り分け（`youtube.playlists`）、(3) サムネ3層コピーの `top` フォールバックラベルに使う。

**サムネ用ベース画像の AI 生成（`image_gen.generate_thumbnail_image`, 方式A2, best-effort）:**
- **固定マスコットを参照画像として毎回 Nano Banana に渡し**（`generate_content(files=[mascot])`）、キャラの同一性と「**大きな驚き顔**」を保ったまま、**ポーズ・前景の大きな小道具・配色を話題ごとに変える**（`pipeline._generate_thumb_base` → `generate_thumbnail_image(reference_image=mascot, pose=…)`）。ポーズを固定して背景だけ変えると縮小時に見分けが付かないため、ポーズ自体を散らすのが要点。
- ポーズは `pipeline._THUMB_POSE_VARIATIONS`（両手を上げる／指さす／小物を抱える等）から **slug のハッシュで決定的に選ぶ**（動画ごとに絵柄が散り、リトライでは同じ絵になる）。
- プロンプトの型は `image_gen.build_thumbnail_base_prompt`：マスコットと小物を右側 ~65% に大きく・驚き顔で置き、話題を象徴する**大きな前景オブジェクト**（縮小しても分かる大きさ）を必ず入れ、配色は話題に合わせて鮮やかに（カテゴリ配色はアクセント）、**左1/3は暗く空けて**テキスト用に確保（顔を左1/3に置かせない）。文字は描かせず compose_thumbnail が Pillow で合成する（参照画像が無くても記述だけで近いキャラを描ける）。
- cookie 源・鮮度の制約・自動延命は本編背景の AI 生成（下記）と共通。失敗時は None を返し、呼び出し側が固定マスコット（静止）に縮退する。**簡易動画モード（`general.simple_video_mode`）では AI ベース生成を行わず、固定マスコット（無ければグラデ）を使う**（429／cookie 失効の影響を受けない）。

**固定マスコット素材（参照画像の元＋AI 生成失敗時のフォールバック／ブランド共通）:**
- `assets/thumbnails/mascot_default.png`（1280×720、文字なし）。毎回の生成で**参照画像**として渡すキャラの元であり、AI 生成が失敗したときは**そのまま静止サムネ**として使う。差し替えれば全動画のキャラが変わる。

**3層テキスト合成（`thumbnail.compose_thumbnail`, `ThumbCopy`）:**
- レイアウト（勝ちサムネの型）: 左に可読性スクリム。ベース画像は被写体を右側に置き左を暗く空ける型なので、上段=製品名・中段=説明はその左に収め（`text_w`/`mid_w`）、下段=ベネフィットは広く使う（`bottom_w`）。3行は行間を詰めて下端から積み、フォントは幅とゾーン高さの両方を埋める最大サイズを自動選択（縮小しても読める）。中段は原則1行で大きく表示する。
- 派手な装飾（参考チャンネル準拠）: 下段は**3重袋文字＋グラデ塗り＋ドロップシャドウ**（影→黒の外縁→白の中縁→金グラデ本体）。強調キーワード1語だけ青グラデにして2トーン。上段/中段は白の袋文字＋影。数字＋助数詞（例「10選」）は改行で割らない。改行は budoux の文節境界で行い語中改行を避ける。文字はすべて Pillow 描画のため文字化けが起きない。フォント実行高を実測して見切れを防ぐ。
- **3層コピーは NotebookLM chat で生成**（`_THUMB_COPY_QUESTION` → `_generate_thumb_copy`）: JSON で`top`（主役ワード、全角7字以内・一般語）、`mid`（補足、全角9字以内＝縮小しても読める）、`bottom`（ベネフィット、全角8字以内・数字可）、`highlight`（bottom 内の強調1語）を1回で生成。失敗・不正時は top→カテゴリラベル（論文解説/AIニュース/AI開発/ビジネス、既定 AI要約）、bottom→動画タイトル（先頭を切り詰め）にフォールバック。合成前に `sanitize_public_text` で個人情報を除去する。
- **論文の通称を主役ワードに固定**: 論文カテゴリで略称（SAM/YOLO 等、`_THUMB_TOP_MAX_LEN` 字以内）が抽出できた場合、`top` を chat 生成値ではなくその略称で上書きする。縮小時も一目で「どの論文か」を判別できる。

**動画本編背景の AI 生成（`image_gen.py`, best-effort。サムネとは独立）:**
- 動画（サムネではなく本編の背景ローテーション）用に、話題連動の文字なし背景を Nano Banana で生成する（`generate_background_image`）。失敗時は静止背景に縮退。サムネのベース生成とは独立に試みる。簡易動画モード（`general.simple_video_mode`）では背景 AI 生成をスキップする。
- cookie 源は **画像生成専用の notebooklm プロファイル**（`podcast.image_profile`、既定 `imagegen`）を第一候補とし、利用可否を画像生成前に `account_status` で確認する。専用プロファイルが失効していれば、直前の NotebookLM ジョブで認証済みの `default` プロファイルへ自動フォールバックする。通常プロファイルへの退避は NotebookLM chat とノートブック削除を全て終えた後に限り、Gemini の cookie ローテーション後にNotebookLM RPC が残らない順序を守る。使用したプロファイル名と背景一覧は state の`image_profile_used` / `background_paths` に記録する。
- 各プロファイルの cookie は`~/.notebooklm/profiles/<profile>/storage_state.json` の `__Secure-1PSID` / `__Secure-1PSIDTS`（`.google.com`）。値はログに出さない。通常時は本体（default）とセッションを分離する（同一セッション共有は cookie チェーンを壊し得る）。初回: `uv run notebooklm -p imagegen login`（NotebookLM と同アカウント。`-p/--profile` はサブコマンドの前に置くグローバルオプション）。
- **cookie 鮮度の制約と自動延命**: `__Secure-1PSIDTS` は短時間でローテーションする。init 後に`account_status` を確認し、認証済みなら 1PSIDTS を即時ローテートして永続キャッシュ（`credentials/gemini_cookie_cache/`、`GEMINI_COOKIE_PATH` で上書き可）へ保存。以降 storage_state.json が失効してもキャッシュ側で認証できる。長期未実行で専用プロファイルもキャッシュも失効した場合は`UNAUTHENTICATED` を検知し、`default` が利用可能なら継続する。全候補が利用不可のときだけ WARN（`uv run notebooklm login` を案内）→静止背景に縮退。

**フォールバック（`thumbnail.generate_thumbnail`, 方式B）:**
- AI ベース画像もマスコット素材も無い場合のみ、ランダムなグラデーション背景＋日本語見出し（NotoSansJP-Bold、長さに応じた自動サイズ・影つき）を中央に描画。中央アイコン（favicon）・OGP は使わない。

**実装:** `Pillow`（サムネ合成＋フォールバック）/ `gemini-webapi`（本編背景の AI 生成）

### 3.7 動画変換 (`video.py`)

YouTube は音声のみのアップロードに対応していないため、画像+音声で動画ファイルを作成する。静止画のままでは動きが無く視聴維持に不利なため、以下の2つの演出を付ける:

1. **FFT イコライザ（常時・VU メーター風）**: `showfreqs` を白・低解像度（48×12, mode=bar, fscale=log, ascale=sqrt, 可視化専用に +14dB）で描画し、音声帯域が集中する下半分（24列）へクロップ → neighbor 拡大 → 透明グリッド（`drawgrid` replace）で LED ブロック風に分割。その白バーのアルファを `alphaextract`/`alphamerge` で縦グラデーション画像（下=緑 `0x2BFF88` / 中=ゴールド `0xFFD24A` / 上=赤 `0xFF5E5E`、Pillow で動的生成）にマスク合成し、**音量が大きいほどバー先端が赤くなる**。α0.85 で画面下部（1280×216）にオーバーレイする。
2. **背景ローテーション（AI背景が生成できた場合のみ）**: タイトル入りサムネ(20s)を先頭に1回だけ表示し、残り時間を各AI背景で等分する ffconcat スライドショー。**同じ画像は動画を通して1回しか出さない**。背景は `generate_background_image`（`{slug}_bg{i}.png`）で**動画の話題（日本語タイトル）に関連した内容**をテキストなしで生成し、構図ヒントを1枚ごとに変えて絵の重複を避ける。枚数は音声長から自動決定（45秒/枚目安、上限6枚）。1枚も生成できなければ従来どおり静止背景に縮退する。

**FFmpeg 構成（概略）:**
```bash
# 背景あり: -f concat -safe 0 -i slides.txt / 背景なし: -loop 1 -i thumbnail.png
# 入力2 = EQ 用縦グラデーション PNG（Pillow で一時生成、変換後に削除）
ffmpeg <背景入力> -i audio.mp3 -loop 1 -i eqgrad.png \
  -map_metadata -1 \
  -filter_complex "[0:v]fps=24,scale=1280:720,setsar=1[bg];\
    [1:a]volume=14dB,showfreqs=...,alphaextract[mask];\
    [2:v]format=rgba[grad];[grad][mask]alphamerge,...[eq];\
    [bg][eq]overlay=0:H-h[v]" \
  -map "[v]" -map 1:a \
  -c:v libx264 -preset veryfast -crf 23 -r 24 -c:a aac -b:a 192k \
  -pix_fmt yuv420p -shortest -movflags +faststart output.mp4
```

**実装:** `subprocess` で FFmpeg を呼び出し。音声長は `ffprobe` で取得し、スライド列（`build_slideshow_entries`）を音声長分だけ並べる。

**要件:**
- 入力: サムネイル画像 (PNG) + 音声ファイル (MP3) + 任意のAI背景画像 (PNG×N)
- 出力: MP4 (H.264 + AAC, 1280×720, 24fps)
- 音声ビットレート: 192kbps（EQ 用の volume ブーストは映像のみで出力音声に影響しない）
- FFmpeg がインストールされていない場合はエラーメッセージを表示
- **メタデータ除去（⑥）**: `-map_metadata -1` で入力（NotebookLM の mp3 等）のメタデータを引き継がず、出力 mp4 にローカルパス・個人情報・元タイトル等を残さない（自動テストで担保）

### 3.8 YouTube アップロード (`youtube.py`)

**認証フロー（初回セットアップ）:**
1. Google Cloud Console で OAuth 2.0 クライアント ID を作成
2. `youtube_client_secret.json` を `credentials/` に配置
3. `podcast auth youtube` を実行
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

Web UI から投入したジョブは `privacy_status` を state.json に保存し、アップロード時はジョブの値を優先する。未指定の CLI/YAML ジョブと旧 state は`settings.youtube.privacy_status` にフォールバックする。

**YouTube タイトルの形式:**
```
{settings.youtube.title_prefix} {日本語タイトル（generated_title_max_length 全角字以内）}
```

- タイトルは **② で collect フェーズに NotebookLM チャット（`ask`）から日本語生成**する。引用マーカー（`[1]`）・全体を囲う引用符・先頭絵文字を除去し、全角 `generated_title_max_length` 字に丸める。chat 失敗時は元タイトル（メール系は抽出した件名）にフォールバックする。
- **タイトルポリシー**（`_JP_TITLE_QUESTION` の生成条件に反映。タイトルとサムネが再生数のほぼ全てを決めるため、伸びているチャンネルのタイトル術に合わせる。出典: ゆる言語学ラジオのタイトル講座回 https://youtu.be/4PzlDRz5v4Q ）:
  - **先頭 約20字に引きを集約**: スマホ表示ではタイトルが手前で切り詰められ、クリックを迷う視聴者は最後まで読まない。最も引きのある情報（意外性・数字・視聴者の得）を先頭約20字以内に置く。
  - **一般視聴者基準**: 「内容を何も知らない人がタップするか」を毎回の判断基準にする。学術用語・研究分野名は再生にマイナスなので一般に通じる言葉へ言い換える（広く知られた製品名・サービス名は可）。専門家向けの正確な情報（原題・URL）はタイトルに入れず概要欄が担う（概要欄を読むのは既存ファンのみ）。
  - **短さ優先・重複禁止**: YouTube タイトルに字数を埋める価値は無く、長いほど評価が下がる。同義語があれば1文字でも短い方を選び（例:「〜より多い」→「〜以上」）、同じ意味の語を繰り返さない。
  - **弱い定型で締めない**: 「〜を解説」「〜について」は元々関心がある人しか押さないため避け、問いかけ・言い切りで好奇心を引く。問いの形にする場合、前提の置き方で反応する層が変わるため（例:「正しいのか」は肯定・否定の両側を集め、「間違いなのか」は否定側しか集めない）、より広い層が反応する前提を選ぶ。
  - **忠実性の下限**: 引きを優先しつつも、ソースに無い誇張はしない（内容への忠実さは維持）。
- **論文カテゴリの通称付与**: カテゴリが `paper` の場合、collect で NotebookLM チャット（`_PAPER_SHORTNAME_QUESTION` → `_extract_paper_shortname`）から論文の通称・略称（SAM/YOLO/3DGS 等）を抽出し、日本語タイトル先頭に `【略称】` を付与する（例: `【SAM】あらゆる物体を一発で切り抜く基盤モデル`）。有名論文の解説を検索する学生・研究者に見つけてもらいやすくするため。略称は `clean_paper_shortname`で検証（英数字始まり・英字を含む 1〜16 字、`none`/年号/フレーズは棄却）し、抽出できない論文はそのまま。既にタイトルに略称が含まれている場合は二重付与しない（YouTube 検索は本文全体を索引するため、含まれていれば発見性は満たされる。先頭【】は略称が欠落しているときに付ける形式）。ユーザーがタイトルを明示指定した場合（`user_title`）は尊重し、付与しない。
- YouTube API が拒否する文字（`<`, `>`）は全角（`＜`, `＞`）に自動置換し、出力前に`sanitize_public_text` で個人情報を除去する。

**YouTube 説明文テンプレート（⑤）:**
```
AIが元情報をもとに自動生成した、ポッドキャスト風の音声要約です。
通勤や作業のお供にどうぞ。

📄 元記事: {URL}            # 複数ソースは全URLを列挙
📄 元資料: {ファイル名}      # ローカルPDF は絶対パスを出さずファイル名(stem)のみ
📰 ソース: {サイト名}        # 単一・非メール・非ローカル時のみ

#AI #論文解説 #機械学習      # カテゴリ別ハッシュタグ（3〜5）

---
※ Gemini Notebook（旧 NotebookLM）の音声概要で自動生成。要点把握用です。正確な内容は元情報をご確認ください。
```

- **内部設定（プロンプト名・音声長）は公開面に出さない**（旧テンプレの「🔧 生成条件」は廃止）。
- 説明文・タイトル・サムネ見出しは出力前に**個人情報をサニタイズ**する（メールアドレス・Spark 共有 URL・ローカル絶対パスを除去。`sanitize_public_text` が最後の砦）。
- **ローカルファイルソース**: 絶対パス（ユーザー名・ディレクトリ構造）は公開面に出さない。出典はディレクトリと拡張子を落としたファイル名（stem）のみを「📄 元資料: {ファイル名}」として表示する（Zotero 等の stem は「著者 - 年 - タイトル」形式で公開してよい論文メタデータ）。
- **複数ソース（⑦）**: `extra_urls` を含む全ソースを列挙する。メール系（Spark 共有）が混じる場合は当該 URL を出さず「📰 ソース: メールニュースレター」を表示する。
- **メール系ソース（Spark 共有リンク）**: 生の共有 URL は公開面に出さない。submit で `sources.fetch` が SSR 初期データから件名と本文を抽出し、URL ではなく**テキストソース**（`add_text_source`）として NotebookLM に投入する（NotebookLM に URL を直接取得させると、JS レンダリング前の宣伝シェルだけを掴んで本文なしの音声を静かに生成することがあるため。抽出失敗は Fail Fast で `failed`）。件名はこの時点でタイトルに反映する。collect では従来どおり NotebookLM チャット（`ask`）から送信元・日付を抽出し、説明文に「出典: {送信元}（{ドメイン}）- {日付}」を表示する（生 URL は state.json のみに保持）。抽出失敗時も生 URL は出さない。
- **プレイリスト振り分け（③）**: カテゴリ→`youtube.playlists` で解決し、無ければ `playlist_id` にフォールバックする。さらに `all_playlist_id` が設定されていれば全動画横断プレイリストとして常に追加する（重複 ID は除外）。

**アップロード手順:**
1. `videos.insert` で動画をアップロード（resumable upload）
2. `thumbnails.set` でカスタムサムネイルを設定
3. `playlist_ids` の各 ID について `playlistItems.insert` で動画をプレイリストに追加
4. `selfDeclaredMadeForKids: false` を常に設定（子供向けではない）
5. `containsSyntheticMedia: true` を常に設定（AI生成コンテンツの開示）
6. アップロード後、`UploadResult(youtube_url, thumbnail_set)` を返却（`thumbnail_set=false` は要再適用）
7. NotebookLM のノートブックは collect / `run-single` とも、全 chat と音声ダウンロードの完了後、画像生成へ入る前に削除済み

手順 2〜3（サムネイル設定・プレイリスト追加）の失敗は WARN ログに留め、ジョブは`uploaded` として扱う。動画本体は `videos.insert` で既にアップロード済みのため、ここで failed にするとリトライで同じ動画が重複アップロードされてしまう。

**サムネ未適用の自己修復（`thumbnail_pending`）:** `thumbnails.set` は新規チャンネルで `429 uploadRateLimitExceeded`（一時的なサムネアップロード上限）を返すことがある。この場合サムネが貼られず、YouTube が動画フレームから自動サムネを選んでしまう。`upload_video` は `UploadResult(youtube_url, thumbnail_set)` を返し、サムネ未適用時はstate の当該ジョブに `thumbnail_pending: true` を記録する。次回 `upload_videos` 実行時、先頭で`_reapply_pending_thumbnails` が `thumbnail_pending` のアップロード済みジョブへ `set_thumbnail`（`thumbnails.set` 単体）で再適用を試みる。成功で pending を下ろし、再び 429（クォータ）を受けたら残りを打ち切って次回に持ち越す（冪等）。これによりクォータ回復後に無人でサムネが自己修復される。

**認証コンテキスト:**
- CLI（対話可能）: トークンが無効な場合はブラウザ OAuth フローを開始する
- Web サーバー（非対話、`allow_interactive_auth=False`）: OAuth フローを開始せず即座にエラーにし、該当ジョブを failed として記録する（`auth youtube` での再認証を促す）

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

1. **submit**: ノートブック作成＋音声生成開始（`mode="lecture"` のジョブは生成待ちへ遷移させるだけ）
2. **collect**: 音声回収と後処理（chat・サムネ・動画変換。`mode="lecture"` はここで講義動画生成へディスパッチ）
3. **upload**: YouTube アップロード（順次実行、quota制限あり。両モード共通）

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
1. state.json をロード、生成中/処理済みの `(URL, mode)` をスキップ（`--force`で上書き）
2. `mode="lecture"` は外部AIをまだ起動せず `generating` に遷移し、Webワーカーへ制御を返す
3. `mode="podcast"` は各URLに対して `asyncio.gather` で並列実行:
   - 既存ジョブに `notebook_id` が残っていれば best-effort で旧ノートブックを削除（リトライ・force 再実行時のリーク防止）
   - ソースの投入形式を解決（`sources.fetch.resolve_source`。Spark 共有はここで SSR 本文を抽出、失敗は Fail Fast）→ メタデータ取得 → ノートブック作成（作成直後に `notebook_id` を永続化）→ ソース追加（`RemoteSource` は `add_source`、`ExtractedSource` は `add_text_source`）→ `start_audio_generation()`
   - state に `status="generating"` + `task_id` + `metadata` を保存
4. 各URLのエラーは個別にキャッチして `failed` として記録
5. `--dry-run` は state.json に一切書き込まない（本実行のスキップ判定や collect を汚染しないため）

**state 書き込みの原則（全フェーズ共通）:**ジョブ更新は必ずディスク上の最新 state を読み直してから該当ジョブのみ更新して保存する（`_update_job_state` / `_upsert_job_state`）。メモリ上の古い stateスナップショット全体を書き戻すと、並行する Web 操作（削除・クリア・リトライ・追加）を巻き戻してしまうため。

**プロセス間の排他（全フェーズ共通）:**各フェーズは `state.json` と同じ場所の `state.lock` を `flock` で排他ロックしてから実行する（`podcast.locking.pipeline_lock`）。ロックを取れない実行は待たずに `PipelineBusyError` で中止し、Web UI のワーカーはそのスイープを skip する（ジョブは相手が進めるので `failed` にしない）。同一プロセス内の入れ子（`run_pipeline` → 各フェーズ）は再入可能。CLI と Web UI が同じジョブを同時に collect すると、片方がノートブックを削除した時点でもう片方のアーティファクトが一覧から消え（`removed`）、chat も not found で拒否されて互いの成果を壊すため。

**Phase 2: collect_audio(settings, poll, timeout)**
1. state.json から `status="generating"` のジョブを取得
2. `mode="lecture"` は `lecture.generate_lecture()` をワーカースレッドで直列実行し、動画・サムネイル・台本・投稿JSONを出力して `video_ready` に合流させる（生成内容の仕様は `specs/LECTURE_SPEC.md` を正とする）
3. `mode="podcast"` で `notebook_id` / `task_id` が無いジョブは明示的なエラーで `failed` に遷移
4. NotebookLMジョブに対して並列で `check_audio_status()` を呼び出し
5. terminal な `failed` ステータス: `failed` に遷移 + ノートブック削除（`--poll` の有無に関わらず）。`not_found` は一時的 lag の可能性があるため単発では terminal 扱いしない（§3.5 参照）
6. 完了したジョブ: 音声DL → chat 後処理 → ノートブック削除 → 利用可能な画像プロファイルを選択→ AIサムネイル・複数背景 → 動画変換 → `status="video_ready"`（`notebook_id` をクリア）。専用画像プロファイルが失効していれば `default` へ自動退避し、生成背景を動画変換へ渡す
7. 未完了ジョブ: `--poll` あり → `wait_for_audio` で待機（タイムアウト時は `generating` 維持で次回再試行）/ なし → ステータス報告のみ
8. 例外で `failed` に遷移する際は、残存ノートブックを best-effort で削除する

動画変換（ffmpeg）だけは `podcast.collect_concurrency` 件までに絞る。ジョブ全体を絞ると音声生成の待機まで直列化して総時間が伸びるため、エンコードの並走だけを抑える（4本並走で ffmpeg が SIGKILL された）。

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
      "mode": "podcast",
      "audio_length": "default",
      "prompt": "default",
      "privacy_status": "unlisted",
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
      "image_profile_used": "default",
      "background_paths": [
        "./tmp/thumbnails/a1b2c3d4e5f6_bg0.png",
        "./tmp/thumbnails/a1b2c3d4e5f6_bg1.png"
      ],
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
- Web UI で選んだ公開範囲はジョブ単位で保持し、再起動・再試行後も同じ値でアップロードする
- 状態ファイルはアトミック書き込み（一時ファイル→rename）
- パイプライン実行は `state.lock` の `flock` で1プロセスに直列化する（二重実行防止。上記 §3.9 参照）

### 3.11 結果レポート (`report.py`)

処理完了後にターミナルに結果を出力する。成功判定は `error` の有無で行う（`submit` / `collect` 単体実行時の `generating` / `video_ready` などのフェーズ途中ステータスも成功として扱い、YouTube URL が無い場合はステータスを括弧書きで表示する）。

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
# ポッドキャスト（podcast モード）設定
podcast:
  backend: "notebooklm-py"  # "notebooklm-py" or "playwright"
  audio_language: "ja"
  audio_length: "short"     # グローバルデフォルト: "short" | "default"
  generation_timeout_seconds: 1200   # Audio Overview 生成のタイムアウト (default長の音声は10分以上かかる場合がある)
  generation_poll_interval_seconds: 10
  collect_concurrency: 2             # 同時に走らせる ffmpeg エンコード数（増やすとメモリ不足で SIGKILL される）

  prompt_presets:
    default: >
      この内容を日本語で要約してポッドキャスト形式で説明してください。
      専門用語は必要に応じて英語のまま使ってください。
    paper_summary: >
      論文の詳細な解説をポッドキャスト形式で行います。
      リスナーの専門分野や知識レベルに合わせた解説を行います。

# YouTube 設定
youtube:
  privacy_status: "public"
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
  background_mode: "codex-svg"  # lecture モード用: "codex-svg" | "static"（specs/LECTURE_SPEC.md）
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
  simple_video_mode: false       # 簡易動画モード（下記）

# lecture モード用（内容は specs/LECTURE_SPEC.md を正とする）
lecture:
  script_model: "opus"
  script_effort: "xhigh"
  review_model: "gpt-5.6-sol"
  review_effort: "xhigh"
```

**簡易動画モード（`general.simple_video_mode`）:** `true` にすると、AIサムネ・AI背景の生成をスキップしてグラデーション静止背景の動画を高速に作り、さらにアップロード時に `thumbnails.set`（カスタムサムネ設定）を行わない（`thumbnail_path=None`）。サムネアップロード上限（429）の回復中に 429 を叩いて24hローリングをリセットしないための一時モード。自分用に素早く動画を作りたいとき用。通常運用に戻すときは `false`。

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
podcast auth notebooklm
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
podcast auth youtube
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
- ~~Web UI ダッシュボード~~ → **実装済み**（`webui`、詳細は `specs/GUI_SPEC.md`）

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
