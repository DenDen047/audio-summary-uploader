# NotebookLM → YouTube 自動化パイプライン 仕様書

## 1. プロジェクト概要

### 1.1 目的

ユーザーが URL リストをテキストファイルに記載するだけで、以下が自動実行される CLI ツールを構築する。

1. NotebookLM でノートブックを作成し、URL をソースとして追加
2. 日本語の Audio Overview（ポッドキャスト形式の音声要約）を生成
3. 生成された音声を YouTube に公開動画としてアップロード

### 1.2 ユーザーストーリー

> 英語の論文やニュース記事の URL をテキストファイルに貼り付けて CLI コマンドを実行すると、数分後に YouTube の自分のチャンネルに日本語の音声要約がアップロードされている。移動中やスキマ時間に YouTube アプリで聴ける。

### 1.3 前提条件

| 項目 | 内容 |
|---|---|
| NotebookLM アカウント | Google Workspace（会社契約）のアカウント |
| YouTube アカウント | 個人の Google アカウント（YouTube チャンネル） |
| 実行環境 | macOS または Linux（Python 3.10+） |
| NotebookLM 操作方法 | Phase 1: `notebooklm-py`（非公式 CLI）、Phase 2: Playwright |

---

## 2. システムアーキテクチャ

### 2.1 全体フロー

```
urls.txt                  (入力: 1行1URL)
    │
    ▼
┌─────────────────────────────────────────────────┐
│  notebooklm-youtube-automator (Python CLI)      │
│                                                 │
│  1. URL パーサー                                 │
│     └─ urls.txt を読み込み、URL リストを生成       │
│                                                 │
│  2. メタデータ取得                                │
│     └─ 各 URL から OGP 情報を取得                 │
│        (タイトル、説明、OGP画像URL)               │
│                                                 │
│  3. NotebookLM 操作 (notebooklm-py)              │
│     ├─ ノートブック作成                           │
│     ├─ URL をソースとして追加                     │
│     ├─ Audio Overview 生成（日本語指定）          │
│     └─ 音声ファイル (.mp3) ダウンロード           │
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
notebooklm-youtube-automator/
├── pyproject.toml
├── README.md
├── .env.example                  # 環境変数テンプレート
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
├── templates/
│   └── thumbnail_base.png        # サムネイルベーステンプレート
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
$ automator run urls.txt

# ドライラン（NotebookLM/YouTube操作を実行しない）
$ automator run urls.txt --dry-run

# 特定のURLだけ処理
$ automator run-single "https://example.com/article"

# YouTube 認証セットアップ
$ automator auth youtube

# NotebookLM 認証セットアップ
$ automator auth notebooklm

# 処理状況の確認
$ automator status
```

### 3.2 URL パーサー (`url_parser.py`)

**入力形式:** テキストファイル（1行に1URL）

```
# urls.txt の例
# コメント行（#始まり）はスキップ
# 空行もスキップ

https://arxiv.org/abs/2401.12345
https://www.example.com/news/article-1
https://newsletter.example.com/issue-42
```

**処理内容:**
- ファイルを読み込み、行ごとにパース
- `#` で始まる行と空行をスキップ
- URL のバリデーション（`urllib.parse` で基本チェック）
- 重複 URL の除去
- 処理済み URL のスキップ（状態ファイルとの照合）

**出力:** `list[str]` — 有効な URL のリスト

### 3.3 メタデータ取得 (`metadata.py`)

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
- User-Agent を適切に設定

### 3.4 NotebookLM 操作 (`notebooklm.py` + バックエンド)

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
    async def generate_audio(
        self, notebook_id: str, language: str = "ja", instructions: str = ""
    ) -> str:
        """Audio Overview を生成し、audio_id を返す"""
        ...

    @abstractmethod
    async def download_audio(self, notebook_id: str, output_path: Path) -> Path:
        """生成された音声をダウンロードする"""
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
```
この内容を日本語で要約してポッドキャスト形式で説明してください。
専門用語は必要に応じて英語のまま使ってください。
```

**音声生成の待機:**
- 生成完了までポーリング（10秒間隔、最大タイムアウト10分）
- 生成ステータスが「完了」になったらダウンロード

### 3.5 サムネイル生成 (`thumbnail.py`)

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

### 3.6 動画変換 (`video.py`)

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

### 3.7 YouTube アップロード (`youtube.py`)

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
    description: str              # 元記事の説明 + URL
    tags: list[str]               # ["NotebookLM", "Audio Summary", "AI", ...]
    category_id: str = "27"       # Education カテゴリ
    privacy_status: str = "public"
    default_language: str = "ja"
    thumbnail_path: Path | None = None
```

**YouTube タイトルの形式:**
```
🎧 {記事タイトル（最大90文字に切り詰め）}
```

**YouTube 説明文テンプレート:**
```
NotebookLM の Audio Overview で自動生成された音声要約です。

📄 元記事: {URL}
📰 ソース: {サイト名}

---
この動画は notebooklm-youtube-automator で自動生成されました。
```

**アップロード手順:**
1. `videos.insert` で動画をアップロード（resumable upload）
2. `thumbnails.set` でカスタムサムネイルを設定
3. アップロード後の YouTube URL を返却

**クォータ管理:**
- `videos.insert` = 1,600 ユニット
- `thumbnails.set` = 50 ユニット
- 1URLあたり合計 ≈ 1,650 ユニット
- デフォルトクォータ 10,000/日 → 1日あたり最大6本
- クォータ残量チェックを実装（超過時は翌日に持ち越し）

### 3.8 パイプラインオーケストレーション (`pipeline.py`)

**処理フロー（1 URL あたり）:**

```python
async def process_single_url(url: str) -> ProcessResult:
    # 1. メタデータ取得
    metadata = await fetch_metadata(url)

    # 2. NotebookLM でノートブック作成
    notebook_id = await notebooklm.create_notebook(
        title=f"Summary: {metadata.title}"
    )

    # 3. ソース追加
    await notebooklm.add_source(notebook_id, url)

    # 4. Audio Overview 生成（日本語）
    await notebooklm.generate_audio(
        notebook_id,
        language="ja",
        instructions="この内容を日本語で要約してポッドキャスト形式で説明してください。"
    )

    # 5. 音声ダウンロード
    audio_path = await notebooklm.download_audio(
        notebook_id,
        output_path=tmp_dir / f"{slug}.mp3"
    )

    # 6. サムネイル生成
    thumbnail_path = await generate_thumbnail(
        metadata=metadata,
        output_path=tmp_dir / f"{slug}_thumb.png"
    )

    # 7. 動画変換
    video_path = await convert_to_video(
        audio_path=audio_path,
        thumbnail_path=thumbnail_path,
        output_path=tmp_dir / f"{slug}.mp4"
    )

    # 8. YouTube アップロード
    youtube_url = await upload_to_youtube(
        video_path=video_path,
        metadata=metadata,
        thumbnail_path=thumbnail_path,
    )

    return ProcessResult(url=url, youtube_url=youtube_url, status="success")
```

**エラーハンドリング:**
- 各ステップで例外をキャッチし、ログに記録
- 1つの URL が失敗しても他の URL の処理は継続
- リトライ: 最大3回（指数バックオフ）
- 最終的な結果レポートに成功/失敗を記録

**並列処理:**
- NotebookLM の Audio Overview 生成は時間がかかるため、基本的には直列処理
- ただし、メタデータ取得やサムネイル生成は並列化可能
- YouTube のクォータ制限を考慮し、1日6本を超えないよう制御

### 3.9 状態管理

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
      "status": "success",
      "processed_at": "2026-03-08T12:05:00Z"
    },
    {
      "url": "https://example.com/article-2",
      "notebook_id": "def456",
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

### 3.10 結果レポート (`report.py`)

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
  audio_instructions: >
    この内容を日本語で要約してポッドキャスト形式で説明してください。
    専門用語は必要に応じて英語のまま使ってください。
  generation_timeout_seconds: 600    # Audio Overview 生成のタイムアウト
  generation_poll_interval_seconds: 10

# YouTube 設定
youtube:
  privacy_status: "public"
  category_id: "27"              # Education
  title_prefix: "🎧"
  title_max_length: 95
  default_tags:
    - "NotebookLM"
    - "Audio Summary"
    - "AI"
    - "音声要約"
  daily_upload_limit: 6          # クォータ制限に基づく安全マージン

# サムネイル設定
thumbnail:
  width: 1280
  height: 720
  overlay_opacity: 0.5           # 暗めフィルターの不透明度
  font_name: "NotoSansJP-Bold"
  title_font_size_max: 60
  title_font_size_min: 32
  subtitle_font_size: 24
  text_color: "#FFFFFF"
  fallback_gradient:             # OGP画像がない場合のグラデーション
    start: "#1a1a2e"
    end: "#16213e"

# 一般設定
general:
  tmp_dir: "./tmp"
  state_file: "./state.json"
  max_retries: 3
  retry_backoff_base: 2          # 指数バックオフの底（秒）
```

### 4.2 `.env.example`

```bash
# NotebookLM (notebooklm-py) 認証
# notebooklm-py の認証方式に従って設定（詳細は README 参照）

# YouTube API
YOUTUBE_CLIENT_SECRET_PATH=./credentials/youtube_client_secret.json
YOUTUBE_TOKEN_PATH=./credentials/youtube_token.json
```

---

## 5. 技術スタック

| カテゴリ | 技術 | バージョン | 用途 |
|---|---|---|---|
| 言語 | Python | 3.10+ | メイン言語 |
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
cd notebooklm-youtube-automator

# 仮想環境の作成と依存関係インストール
uv venv
uv pip install -e .

# 環境変数の設定
cp .env.example .env
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
- テキストファイルから URL を読み込み
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

- デフォルトのアップロードクォータは 1日10,000ユニット（約6動画/日）
- クォータ増加申請には Google の審査が必要（数日〜数週間）
- カスタムサムネイルの設定にはチャンネルの電話番号認証が必要
- 著作権のある素材をそのまま使う場合は注意が必要

### 8.3 アカウント分離

- NotebookLM: 会社の Google Workspace アカウント
- YouTube: 個人の Google アカウント
- 2つのアカウントの認証情報を別々に管理する必要がある
- YouTube の OAuth トークンは個人アカウント側で取得すること
