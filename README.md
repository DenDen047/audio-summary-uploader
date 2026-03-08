# NotebookLM → YouTube 自動化パイプライン

URL リストから NotebookLM で音声要約（Audio Overview）を生成し、YouTube にアップロードする CLI ツール。

## 必要なもの

| 項目 | 備考 |
|---|---|
| Python 3.11 | `.python-version` で指定済み |
| [uv](https://docs.astral.sh/uv/) | パッケージ管理・実行 |
| [FFmpeg](https://ffmpeg.org/) 6.x+ | 動画変換に使用 |
| Google Workspace アカウント | NotebookLM 用 |
| YouTube チャンネル（個人 Google アカウント） | アップロード先 |

## セットアップ

```bash
# 1. uv のインストール（未インストールの場合）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. FFmpeg のインストール
# macOS
brew install ffmpeg
# Ubuntu/Debian
sudo apt install ffmpeg

# 3. 依存関係のインストール（.venv は uv が自動作成）
uv sync

# 4. 日本語フォントの配置
# Noto Sans JP を https://fonts.google.com/noto/specimen/Noto+Sans+JP からDL
# fonts/ ディレクトリに NotoSansJP-Bold.ttf を配置

# 5. 環境変数の設定
cp .env.example .env
```

### 認証セットアップ

**NotebookLM:**

```bash
uv run automator auth notebooklm
```

**YouTube API:**

1. [Google Cloud Console](https://console.cloud.google.com) でプロジェクトを作成
2. YouTube Data API v3 を有効化
3. OAuth 2.0 クライアント ID を作成（デスクトップアプリ）
4. JSON をダウンロードして配置:
   ```bash
   cp ~/Downloads/client_secret_xxxxx.json ./credentials/youtube_client_secret.json
   ```
5. 認証フローを実行:
   ```bash
   uv run automator auth youtube
   ```

## 使い方

### URL リストの作成

`urls.yaml` に処理したい URL を記載する。URL だけ書けばデフォルト設定で動作する。

```yaml
# urls.yaml
- url: https://arxiv.org/abs/2401.12345

- url: https://example.com/article
  audio_length: short
  prompt: deep_dive

- url: https://newsletter.example.com/issue-42
  audio_length: long
```

| フィールド | 必須 | 値 | デフォルト |
|---|---|---|---|
| `url` | Yes | URL 文字列 | — |
| `audio_length` | No | `"short"` / `"long"` | `settings.yaml` の `notebooklm.audio_length` |
| `prompt` | No | `"default"` / `"deep_dive"` | `"default"` |

### 実行

```bash
# 基本実行
uv run automator run urls.yaml

# ドライラン（NotebookLM/YouTube 操作を実行しない）
uv run automator run urls.yaml --dry-run

# 特定の URL だけ処理
uv run automator run-single "https://example.com/article"

# 処理状況の確認
uv run automator status
```

### YouTube アップロード

アップロードされた動画には以下が自動設定される:

- **タイトル:** `🎧 {記事タイトル}`
- **概要欄:** 元記事 URL、ソースサイト名、生成条件（音声の長さ・プロンプトプリセット）
- **プレイリスト:** `settings.yaml` の `youtube.playlist_id` で指定（任意）

## 設定

`config/settings.yaml` で各種設定を変更できる。

```yaml
notebooklm:
  backend: "notebooklm-py"
  audio_language: "ja"
  audio_length: "default"       # "short" | "long" | "default"

  prompt_presets:
    default: >
      この内容を日本語で要約してポッドキャスト形式で説明してください。
      専門用語は必要に応じて英語のまま使ってください。
    deep_dive: >
      この内容を日本語で深く掘り下げて解説してください。
      背景知識や関連する概念も含めて、詳細に議論してください。
      専門用語は必要に応じて英語のまま使ってください。

youtube:
  privacy_status: "public"
  playlist_id: null             # プレイリスト ID（null = 追加しない）
  daily_upload_limit: 5
```

## 開発

```bash
# Python ファイルの実行
uv run python <file>

# リント
uv run ruff check .

# テスト
uv run pytest
```

### ディレクトリ構成

```
├── pyproject.toml
├── config/
│   └── settings.yaml             # アプリ設定
├── credentials/                  # OAuth トークン等（.gitignore 対象）
├── src/automator/                # メインパッケージ
├── specs/                        # 仕様書
├── fonts/                        # サムネイル用フォント
├── templates/                    # サムネイルテンプレート
├── tests/
└── tmp/                          # 一時ファイル（.gitignore 対象）
```

### Git Workflow

git-flow に従う。`develop` ブランチから作業ブランチを作成し、完了後に `develop` へマージ。

詳細仕様は `specs/SPEC.md` を参照。
