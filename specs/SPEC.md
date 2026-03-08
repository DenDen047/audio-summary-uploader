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
│     └─ 各 URL から OGP 情報を取得                 │
│        (タイトル、説明、OGP画像URL)               │
│                                                 │
│  3. NotebookLM 操作 (notebooklm-py)              │
│     ├─ ノートブック作成                           │
│     ├─ URL をソースとして追加                     │
│     ├─ Audio Overview 生成（日本語指定）          │
│     ├─ 音声ファイル (.mp3) ダウンロード           │
│     └─ アップロード完了後にノートブック削除       │
│                                                 │
│  4. サムネイル生成                                │
│     └─ OGP画像 + タイトルテキスト合成             │
│                                                 │
│  5. 動画変換                                     │
│     └─ FFmpeg: 静止画 + mp3 → mp4               │
│                                                 │
│  6. YouTube アップロード                          │
│     ├─ YouTube Data API v3 (videos.insert)       │
│     ├─ サムネイル設定 (thumbnails.set)            │
│     └─ 公開ステータス: public                     │
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
│       ├── url_parser.py         # URL リスト読み込み・バリデーション
│       ├── metadata.py           # OGP メタデータ取得
│       ├── notebooklm.py         # NotebookLM 操作（抽象層）
│       ├── notebooklm_py_backend.py  # notebooklm-py による実装
│       ├── notebooklm_playwright_backend.py  # Playwright による実装（Phase 2）
│       ├── thumbnail.py          # サムネイル生成 (Pillow)
│       ├── video.py              # FFmpeg による動画変換
│       ├── youtube.py            # YouTube API 操作
│       └── report.py             # 結果レポート生成
├── fonts/
│   └── NotoSansJP-Bold.ttf       # 日本語フォント（サムネイル用）
├── tests/
│   ├── test_url_parser.py
│   ├── test_metadata.py
│   ├── test_thumbnail.py
│   ├── test_video.py
│   └── test_youtube.py
└── tmp/                          # 一時ファイル（.gitignore 対象）
    ├── audio/
    ├── thumbnails/
    └── videos/
```

---

## 3. モジュール仕様

### 3.1 CLI (`cli.py`)

Click ベースの CLI インターフェース。

```
# 基本実行
$ automator run urls.yaml

# ドライラン（NotebookLM/YouTube操作を実行しない）
$ automator run urls.yaml --dry-run

# 特定のURLだけ処理
$ automator run-single "https://example.com/article"

# YouTube 認証セットアップ
$ automator auth youtube

# NotebookLM 認証セットアップ
$ automator auth notebooklm

# 処理状況の確認
$ automator status
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
    audio_length: str = "default"
    generation_timeout_seconds: int = 600
    generation_poll_interval_seconds: int = 10
    prompt_presets: dict[str, str] = field(default_factory=dict)

@dataclass
class YouTubeConfig:
    privacy_status: str = "public"
    category_id: str = "27"
    playlist_id: str | None = None
    title_prefix: str = "🎧"
    title_max_length: int = 95
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
  audio_length: long

# ローカル PDF ファイル
- url: ~/Documents/papers/interesting-paper.pdf
  prompt: paper_summary

# フォルダ指定（中の全 PDF を処理）
- url: ~/Documents/papers/
  prompt: paper_summary
```

**データモデル:**

```python
@dataclass
class UrlEntry:
    url: str                          # URL またはローカルファイルパス
    audio_length: str | None = None   # "short" or "long", None = settings.yaml のデフォルトを使用
    prompt: str | None = None         # プリセット名 ("default", "paper_summary"), None = "default" プリセットを使用
```

**処理内容:**
- YAML ファイルを読み込み、各エントリをパース
- URL のバリデーション（`urllib.parse` で基本チェック）
- ローカルパスのバリデーション（ファイル存在確認、PDF 拡張子チェック）
- フォルダが指定された場合、中の `*.pdf` ファイルを個別エントリに展開
- `audio_length` の値バリデーション（`"short"` / `"long"` / `None` のみ許可）
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
```

**実装方針:**
- `httpx` でページを取得し、`BeautifulSoup` で OGP タグをパース
- OGP が取得できない場合は `<title>` タグにフォールバック
- タイムアウト: 10秒
- User-Agent: 一般的なブラウザの User-Agent を使用（403 回避のため）
- ローカルファイルの場合: ファイル名からタイトルを生成（OGP取得なし）

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
    async def generate_audio(
        self,
        notebook_id: str,
        language: str = "ja",
        instructions: str = "",
        audio_length: str | None = None,
    ) -> str:
        """Audio Overview を生成し、audio_id を返す

        Args:
            notebook_id: ノートブック ID
            language: 音声の言語
            instructions: プリセットから解決されたプロンプト文字列
            audio_length: "short" or "long", None の場合は settings.yaml のデフォルトを使用
        """
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

`UrlEntry.audio_length` が指定されている場合はその値を、未指定の場合は `settings.yaml` の `notebooklm.audio_length` の値を `generate_audio` の `audio_length` パラメータに渡す。`"default"` の場合は NotebookLM のデフォルト動作に委ねる。

**音声生成の待機:**
- 生成完了までポーリング（10秒間隔、最大タイムアウト10分）
- 生成ステータスが「完了」になったらダウンロード

### 3.6 サムネイル生成 (`thumbnail.py`)

YouTube のサムネイル画像（1280×720px）を生成する。

**生成ロジック:**

```
┌──────────────────────────────────────────┐
│                                          │
│   ┌──────────────────────────────────┐   │
│   │                                  │   │
│   │     OGP 画像（暗めフィルター）     │   │
│   │                                  │   │
│   │  ┌────────────────────────────┐  │   │
│   │  │                            │  │   │
│   │  │    記事タイトル（日本語）     │  │   │
│   │  │    白文字・影つき            │  │   │
│   │  │                            │  │   │
│   │  └────────────────────────────┘  │   │
│   │                                  │   │
│   │           サイト名               │   │
│   └──────────────────────────────────┘   │
│                                          │
└──────────────────────────────────────────┘
```

**処理フロー:**
1. OGP 画像を URL からダウンロード
2. 1280×720 にリサイズ（アスペクト比維持、クロップ）
3. 半透明の暗いオーバーレイを適用（rgba(0,0,0,0.5)）
4. タイトルテキストを中央に白文字でレンダリング
   - フォント: Noto Sans JP Bold
   - フォントサイズ: 自動調整（タイトル長に応じて）
   - テキスト影: 黒い影をつけて視認性確保
5. サイト名を下部にサブテキストとして配置
6. OGP 画像が取得できない場合はグラデーション背景にフォールバック

**実装:** `Pillow` (PIL)

### 3.7 動画変換 (`video.py`)

YouTube は音声のみのアップロードに対応していないため、静止画+音声で動画ファイルを作成する。

**FFmpeg コマンド:**
```bash
ffmpeg -loop 1 -i thumbnail.png -i audio.mp3 \
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
    title: str                    # "[Audio Summary] {記事タイトル}"
    description: str              # 元記事の説明 + URL + 生成条件
    tags: list[str]               # ["NotebookLM", "Audio Summary", "AI", ...]
    category_id: str = "27"       # Education カテゴリ
    privacy_status: str = "public"
    default_language: str = "ja"
    thumbnail_path: Path | None = None
    playlist_id: str | None = None  # 追加先プレイリスト ID
    made_for_kids: bool = False       # "No, it's not made for kids"
```

**YouTube タイトルの形式:**
```
{settings.youtube.title_prefix} {記事タイトル（settings.youtube.title_max_length 文字に切り詰め）}
```

**YouTube 説明文テンプレート:**
```
NotebookLM の Audio Overview で自動生成された音声要約です。

📄 元記事: {URL}
📰 ソース: {サイト名}

🔧 生成条件
  音声の長さ: {audio_length}（"short" / "long" / "default"）
  プロンプト: {prompt_preset_name}（"default" / "paper_summary"）

---
この動画は audio-summary-uploader で自動生成されました。
```

- `audio_length` / `prompt_preset_name` には実際に使用された値（per-URL 指定 or settings.yaml デフォルト）を記載する

**アップロード手順:**
1. `videos.insert` で動画をアップロード（resumable upload）
2. `thumbnails.set` でカスタムサムネイルを設定
3. `playlist_id` が指定されている場合、`playlistItems.insert` で動画をプレイリストに追加
4. `selfDeclaredMadeForKids: false` を常に設定（子供向けではない）
5. アップロード後の YouTube URL を返却
6. パイプライン側でアップロード完了後に NotebookLM のノートブックを削除

**クォータ管理:**
- `videos.insert` = 1,600 ユニット
- `thumbnails.set` = 50 ユニット
- `playlistItems.insert` = 50 ユニット
- 1URLあたり合計 ≈ 1,700 ユニット
- デフォルトクォータ 10,000/日 → 1日あたり最大5本
- クォータ残量チェックを実装（超過時は翌日に持ち越し）

### 3.9 パイプラインオーケストレーション (`pipeline.py`)

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

**処理フロー（1 URL あたり）:**

```python
async def process_single_url(entry: UrlEntry) -> ProcessResult:
    # 1. メタデータ取得
    metadata = await fetch_metadata(entry.url)

    # 2. NotebookLM でノートブック作成
    notebook_id = await notebooklm.create_notebook(
        title=f"Summary: {metadata.title}"
    )

    # 3. ソース追加（URL またはローカルファイル）
    if is_local_path(entry.url):
        await notebooklm.add_file_source(notebook_id, Path(entry.url))
    else:
        await notebooklm.add_source(notebook_id, entry.url)

    # 4. プロンプトプリセットを解決
    prompt_text = resolve_prompt_preset(entry.prompt)  # None → "default" プリセット

    # 5. audio_length を解決（per-URL 指定 > settings.yaml デフォルト）
    audio_length = entry.audio_length or settings.notebooklm.audio_length

    # 6. Audio Overview 生成（日本語）
    await notebooklm.generate_audio(
        notebook_id,
        language="ja",
        instructions=prompt_text,
        audio_length=audio_length,
    )

    # 7. 音声ダウンロード
    audio_path = await notebooklm.download_audio(
        notebook_id,
        output_path=tmp_dir / f"{slug}.mp3"
    )

    # 8. サムネイル生成
    thumbnail_path = await generate_thumbnail(
        metadata=metadata,
        output_path=tmp_dir / f"{slug}_thumb.png"
    )

    # 9. 動画変換
    video_path = await convert_to_video(
        audio_path=audio_path,
        thumbnail_path=thumbnail_path,
        output_path=tmp_dir / f"{slug}.mp4"
    )

    # 10. YouTube アップロード
    youtube_url = await upload_to_youtube(
        video_path=video_path,
        metadata=metadata,
        thumbnail_path=thumbnail_path,
        playlist_id=settings.youtube.playlist_id,
        audio_length=audio_length,
        prompt_preset_name=entry.prompt or "default",
    )

    return ProcessResult(url=entry.url, youtube_url=youtube_url, status="success")
```

**エラーハンドリング:**

CLAUDE.md の Fail Fast 原則に基づき、以下のように粒度を分ける:

- **URL 間**: 1つの URL が失敗しても他の URL の処理は継続（catch & continue）
- **URL 内の各ステップ**: Fail Fast。予期しないエラーは即座にその URL の処理を中断し、次の URL へ進む
- **リトライ対象**: ネットワークエラーなど一時的な障害のみ（最大3回、指数バックオフ）。ロジックエラーやバリデーションエラーはリトライしない
- 最終的な結果レポートに成功/失敗を記録

**並列処理:**
- NotebookLM の Audio Overview 生成は時間がかかるため、基本的には直列処理
- ただし、メタデータ取得やサムネイル生成は並列化可能
- YouTube のクォータ制限を考慮し、1日5本を超えないよう制御（`settings.yaml` の `daily_upload_limit` に従う）

### 3.10 状態管理

処理の再開やスキップのために、状態ファイルを管理する。

**状態ファイル（`state.json`）:**
```json
{
  "last_run": "2026-03-08T12:00:00Z",
  "processed": [
    {
      "url": "https://example.com/article-1",
      "notebook_id": "abc123",
      "youtube_url": "https://youtu.be/xyz789",
      "audio_length": "default",
      "prompt": "default",
      "status": "success",
      "processed_at": "2026-03-08T12:05:00Z"
    },
    {
      "url": "https://example.com/article-2",
      "notebook_id": "def456",
      "audio_length": "short",
      "prompt": "deep_dive",
      "status": "failed",
      "error": "Audio generation timeout",
      "processed_at": "2026-03-08T12:10:00Z"
    }
  ]
}
```

**ポイント:**
- 処理済み URL はスキップ（`--force` で上書き可能）
- 失敗した URL は `--retry-failed` で再処理可能

### 3.11 結果レポート (`report.py`)

処理完了後にターミナルに結果を出力する。

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
  audio_length: "default"   # グローバルデフォルト: "short" | "long" | "default"
  generation_timeout_seconds: 600    # Audio Overview 生成のタイムアウト
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
  privacy_status: "public"
  category_id: "27"              # Education
  playlist_id: "PLB9Pwo4Wnh7UuI9jWgxN9Jy2oflIICABO"  # My AI-Podcast プレイリスト
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
  fallback_gradient:             # OGP画像がない場合のグラデーション
    start: "#1a1a2e"
    end: "#16213e"

# 認証情報パス
credentials:
  youtube_client_secret: "./credentials/youtube_client_secret.json"
  youtube_token: "./credentials/youtube_token.json"

# 一般設定
general:
  tmp_dir: "./tmp"
  state_file: "./state.json"
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
- 処理キューイング（Redis / SQLite）
- Web UI ダッシュボード

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
