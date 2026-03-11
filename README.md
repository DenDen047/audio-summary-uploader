# NotebookLM → YouTube 自動化パイプライン

URL リストから NotebookLM で音声要約（Audio Overview）を生成し、YouTube にアップロードする CLI + Web UI ツール。

## 必要なもの

| 項目 | 備考 |
|---|---|
| Python 3.11+ | `.python-version` で指定済み |
| [uv](https://docs.astral.sh/uv/) | パッケージ管理・実行 |
| [FFmpeg](https://ffmpeg.org/) 6.x+ | 動画変換に使用 |
| Google Workspace アカウント | NotebookLM 用（会社契約アカウント） |
| YouTube チャンネル（個人 Google アカウント） | アップロード先 |

> **注意:** NotebookLM と YouTube で**異なる Google アカウント**を使用します。認証情報は別々に管理されます。

## セットアップ

### 1. 前提ソフトウェアのインストール

```bash
# uv のインストール（未インストールの場合）
curl -LsSf https://astral.sh/uv/install.sh | sh

# FFmpeg のインストール
# macOS
brew install ffmpeg
# Ubuntu/Debian
sudo apt install ffmpeg
```

### 2. 依存関係のインストール

```bash
cd audio-summary-uploader
uv sync
```

`.venv` は uv が自動作成します。

### 3. 日本語フォントの配置

サムネイル生成に日本語フォントが必要です。

1. [Noto Sans JP](https://fonts.google.com/noto/specimen/Noto+Sans+JP) をダウンロード
2. `fonts/NotoSansJP-Bold.ttf` として配置

```bash
# ダウンロードした ZIP を展開後
cp NotoSansJP-Bold.ttf ./fonts/
```

### 4. NotebookLM 認証セットアップ

NotebookLM の操作には [notebooklm-py](https://github.com/nichochar/notebooklm-py) を使用します。**Google Workspace アカウント**で認証が必要です。

#### 4-1. Playwright のインストール

`notebooklm-py` はブラウザ認証に Playwright を使用します。初回のみブラウザバイナリのダウンロードが必要です。

```bash
# Playwright のブラウザをダウンロード（Chromium のみで OK）
uv run playwright install chromium
```

#### 4-2. ログイン

```bash
uv run notebooklm login
```

ブラウザが開くので、**Google Workspace アカウント**でログインしてください。NotebookLM のホーム画面が表示されたら、ターミナルで **Enter** を押して認証を保存します。

認証情報は `~/.notebooklm/storage_state.json` に保存されます。

### 5. YouTube API 認証セットアップ

YouTube へのアップロードには **個人の Google アカウント** で OAuth 2.0 認証が必要です。

#### 5-1. Google Cloud Console でプロジェクトを作成

1. [Google Cloud Console](https://console.cloud.google.com) にアクセス
2. 新しいプロジェクトを作成（例: `audio-summary-uploader`）

#### 5-2. YouTube Data API v3 を有効化

1. 左メニュー「APIとサービス」→「ライブラリ」
2. 「YouTube Data API v3」を検索して有効化

#### 5-3. OAuth 同意画面の設定

1. 「APIとサービス」→「OAuth 同意画面」
2. User Type: 「外部」を選択
3. アプリ名・メールアドレスを入力して保存
4. スコープは設定不要（CLI 実行時に自動で要求します）
5. テストユーザーに**自分の個人 Google アカウント**を追加
   - 本番公開申請をしない限り、テストユーザーのみ利用可能です

#### 5-4. OAuth 2.0 クライアント ID を作成

1. 「APIとサービス」→「認証情報」→「認証情報を作成」
2. 「OAuth クライアント ID」を選択
3. アプリケーションの種類: **デスクトップアプリ**
4. 作成後、JSON をダウンロード

#### 5-5. クライアントシークレットを配置

```bash
cp ~/Downloads/client_secret_xxxxx.json ./credentials/youtube_client_secret.json
```

#### 5-6. 認証フローを実行

```bash
uv run automator auth youtube
```

ブラウザが開き、個人の YouTube アカウントで OAuth 認証を行います。認証が完了するとリフレッシュトークンが `credentials/youtube_token.json` に自動保存されます。以降は自動的にトークンがリフレッシュされます。

> **カスタムサムネイルについて:** YouTube でカスタムサムネイルを設定するには、チャンネルの電話番号認証が必要です。[YouTube Studio](https://studio.youtube.com) → 設定 → チャンネル → 機能の利用資格 から確認できます。

## Docker で使う（推奨）

Docker を使えば Python や FFmpeg のインストールなしで、すぐに利用できます。

### クイックスタート

```bash
git clone https://github.com/<your-username>/audio-summary-uploader.git
cd audio-summary-uploader
```

#### 1. 認証情報を準備

```bash
# YouTube API のクライアントシークレット（取得方法は「YouTube API 認証セットアップ」を参照）
cp ~/Downloads/client_secret_xxxxx.json ./credentials/youtube_client_secret.json

# YouTube OAuth トークン（ホスト側で事前に取得）
uv run automator auth youtube

# NotebookLM の認証（ホスト側で事前に取得）
uv run notebooklm login
```

> `make setup` で必要なファイルの存在を確認できます。

#### 2. 起動

```bash
make setup   # 認証情報・設定ファイルの存在チェック
make up      # docker compose up -d
```

http://localhost:8080 で Web ダッシュボードにアクセスできます。

#### 3. 停止

```bash
make down
```

### Make コマンド一覧

| コマンド | 説明 |
|----------|------|
| `make setup` | 認証情報・設定ファイルの存在チェック |
| `make build` | Docker イメージをビルド |
| `make up` | コンテナをバックグラウンドで起動 |
| `make down` | コンテナを停止・削除 |
| `make logs` | ログをリアルタイム表示 |
| `make restart` | コンテナを再起動 |
| `make status` | コンテナの状態を表示 |

### ボリュームの説明

| パス | 用途 |
|------|------|
| `./config` | `settings.yaml`（アプリ設定） |
| `./credentials` | YouTube OAuth トークン |
| `./tmp` | 生成された音声・サムネイル・動画ファイル |
| `./data` | `state.json`（処理状態の永続化） |
| `~/.notebooklm` | NotebookLM 認証情報（読み取り専用でマウント） |

### トラブルシューティング

#### ログの確認

```bash
make logs
# または特定の行数だけ表示
docker compose logs --tail 100
```

#### ヘルスチェックの確認

```bash
# コンテナの STATUS が "healthy" であることを確認
make status

# ヘルスエンドポイントに直接アクセス
curl http://localhost:8080/health
# => {"status":"ok"}
```

#### NotebookLM の認証期限切れ

NotebookLM のセッションは一定期間で期限切れになります。再ログインしてください:

```bash
# ホスト側で再認証
uv run notebooklm login

# コンテナを再起動（ボリュームが再マウントされる）
make restart
```

#### YouTube トークンの期限切れ

リフレッシュトークンは通常自動更新されますが、長期間使用しなかった場合は再認証が必要です:

```bash
uv run automator auth youtube
make restart
```

## ローカルで使う

Docker を使わずにローカル環境で直接実行する場合は、以下の手順でセットアップしてください。

## 使い方

### Web ダッシュボード（推奨）

ブラウザベースの GUI で操作できます。URL を入力してボタンを押すだけで、音声生成から YouTube アップロードまで自動実行されます。

```bash
# Web ダッシュボードを起動（ブラウザが自動で開きます）
uv run automator web

# ポートを指定する場合
uv run automator web --port 3000
```

- ダークテーマの MeTube 風 UI
- URL 入力 → 自動で 3 フェーズ実行（submit → collect → upload）
- 5 秒ごとに自動更新で進捗を確認
- 失敗したジョブのリトライ、完了済みジョブの一括削除
- サーバー再起動時に未完了ジョブを自動復旧

### CLI で実行

#### URL リストの作成

`urls.yaml` に処理したい URL を記載します（`urls.yaml.example` を参考にしてください）。URL だけ書けばデフォルト設定で動作します。

```yaml
# urls.yaml
- url: https://arxiv.org/abs/2401.12345

- url: https://example.com/article
  audio_length: short
  prompt: paper_summary

- url: https://newsletter.example.com/issue-42
  audio_length: default

# ローカル PDF ファイルも指定可能
- url: ~/Documents/papers/interesting-paper.pdf
  prompt: paper_summary

# フォルダを指定すると、中の全 PDF を処理
- url: ~/Documents/papers/
  prompt: paper_summary
```

| フィールド | 必須 | 値 | デフォルト |
|---|---|---|---|
| `url` | Yes | URL 文字列、ローカル PDF パス、または PDF を含むフォルダパス | — |
| `audio_length` | No | `"short"` / `"default"` | `settings.yaml` の `notebooklm.audio_length` |
| `prompt` | No | `settings.yaml` の `prompt_presets` で定義されたキー（`"default"`, `"paper_summary"` 等） | `"default"` |

#### 実行

パイプラインは3つのフェーズに分離されており、個別にも一括でも実行できます。

##### 一括実行

```bash
# 基本実行（submit → collect → upload を順に実行）
uv run automator run urls.yaml

# ドライラン（メタデータ取得のみ、NotebookLM/YouTube 操作なし）
uv run automator run urls.yaml --dry-run

# 処理済み URL も強制的に再処理
uv run automator run urls.yaml --force

# 前回失敗した URL だけ再処理
uv run automator run urls.yaml --retry-failed
```

##### 3フェーズ分離実行

5件以上処理する場合は、フェーズを分けて実行すると高速です。音声生成を全URLで並列に開始し、完了後にまとめて回収するため、5件でも約10分で処理できます（一括実行の場合は最大50分）。

```bash
# Phase 1: ノートブック作成＋音声生成を並列に開始
uv run automator submit urls.yaml
uv run automator submit urls.yaml --dry-run   # API呼び出しなし
uv run automator submit urls.yaml --force      # 生成中/処理済みも再処理

# Phase 2: 生成完了した音声をDL→サムネイル→動画変換
uv run automator collect              # 完了チェックのみ（未完了はスキップ）
uv run automator collect --poll       # 全ジョブの完了までポーリング待機
uv run automator collect --timeout 900  # タイムアウト指定（秒）

# Phase 3: 動画を YouTube にアップロード
uv run automator upload
```

##### その他のコマンド

```bash
# 特定の URL だけ処理（一括実行）
uv run automator run-single "https://example.com/article"

# 処理状況の確認（各ステータスのカウント表示）
uv run automator status
```

### 処理の流れ

パイプラインは以下の3フェーズで構成されます:

**Phase 1: submit** — 各URLに対して並列で実行
1. OGP メタデータ取得（タイトル、説明、画像 URL）
2. NotebookLM でノートブック作成・URL をソース追加
3. Audio Overview の生成を開始（完了を待たない）

**Phase 2: collect** — 生成完了したジョブに対して並列で実行
4. 音声ファイル (.mp3) をダウンロード
5. OGP 画像 + タイトルでサムネイル画像を生成
6. FFmpeg で静止画 + MP3 → MP4 動画に変換
7. NotebookLM のノートブックを削除

**Phase 3: upload** — 順次実行（quota制限あり）
8. YouTube Data API v3 で動画をアップロード（プレイリストに自動追加）
9. 結果レポートを出力

### YouTube アップロード内容

アップロードされた動画には以下が自動設定されます:

- **タイトル:** `🎧 {記事タイトル}`（最大95文字）
- **概要欄:** 元記事 URL、ソースサイト名、生成条件
- **カテゴリ:** Education (27)
- **タグ:** NotebookLM, Audio Summary, AI, 音声要約
- **プレイリスト:** `settings.yaml` の `youtube.playlist_id` で指定（任意）

### 状態管理

処理状態は `state.json` に自動保存されます。各ジョブは以下のステータスで管理されます:

| ステータス | 意味 |
|---|---|
| `queued` | キューに追加済み、処理待ち（Web GUI 使用時） |
| `generating` | submit 完了、音声生成中 |
| `video_ready` | collect 完了、MP4 ファイル準備済み |
| `uploaded` | upload 完了（最終成功状態） |
| `failed` | いずれかのフェーズでエラー |

生成中・処理済みの URL はデフォルトでスキップされます。

- `--force`: 生成中・処理済みも含めて全 URL を再処理
- `--retry-failed`: 前回失敗した URL のみ再処理

### クォータ制限

YouTube Data API のデフォルトクォータは 10,000 ユニット/日です。1本の動画アップロードで約 1,700 ユニットを消費するため、**1日あたり最大5本**が安全な上限です（`settings.yaml` の `daily_upload_limit` で制御）。

## 設定

`config/settings.yaml` で各種設定を変更できます。

```yaml
notebooklm:
  backend: "notebooklm-py"       # "notebooklm-py" or "playwright"
  audio_language: "ja"
  audio_length: "short"           # "short" | "default"
  generation_timeout_seconds: 600
  prompt_presets:
    default: >
      この内容を日本語で要約してポッドキャスト形式で説明してください。
      専門用語は必要に応じて英語のまま使ってください。
    paper_summary: >
      (論文解説用プリセット)

youtube:
  privacy_status: "public"       # "public" | "unlisted" | "private"
  category_id: "27"              # Education
  playlist_id: "PLB9Pwo4Wnh7UuI9jWgxN9Jy2oflIICABO"  # My AI-Podcast
  title_prefix: "🎧"
  title_max_length: 95
  daily_upload_limit: 5

thumbnail:
  width: 1280
  height: 720
  overlay_opacity: 0.5           # 背景画像の暗さ (0.0〜1.0)

credentials:
  youtube_client_secret: "./credentials/youtube_client_secret.json"
  youtube_token: "./credentials/youtube_token.json"
```

## 開発

```bash
# リント
uv run ruff check .

# テスト
uv run pytest

# Python ファイルの実行
uv run python <file>
```

### ディレクトリ構成

```
├── pyproject.toml
├── config/
│   └── settings.yaml             # アプリ設定
├── credentials/                  # OAuth トークン等（.gitignore 対象）
├── src/automator/                # メインパッケージ
│   ├── cli.py                    # CLI エントリポイント (Click)
│   ├── config.py                 # 設定読み込み
│   ├── pipeline.py               # パイプラインオーケストレーション
│   ├── url_parser.py             # URL リスト解析
│   ├── metadata.py               # OGP メタデータ取得
│   ├── notebooklm.py             # NotebookLM 抽象インターフェース
│   ├── notebooklm_py_backend.py  # notebooklm-py 実装
│   ├── thumbnail.py              # サムネイル生成 (Pillow)
│   ├── video.py                  # FFmpeg 動画変換
│   ├── youtube.py                # YouTube API 操作
│   ├── report.py                 # 結果レポート
│   └── web/                      # Web ダッシュボード
│       ├── app.py                # FastAPI アプリ + バックグラウンドワーカー
│       ├── routes.py             # ルーティング + API ハンドラ
│       └── templates/            # Jinja2 テンプレート (htmx + Pico CSS)
├── specs/                        # 仕様書
├── fonts/                        # サムネイル用日本語フォント
├── tests/
└── tmp/                          # 一時ファイル（.gitignore 対象）
```

### Git Workflow

`main` ブランチから feature ブランチを作成し、完了後に `main` へマージします。

詳細仕様は `specs/SPEC.md` を参照してください。
