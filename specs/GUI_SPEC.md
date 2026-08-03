# GUI 仕様書: Web ダッシュボード

## 概要

URL を入力するだけで、澪・透の掛け合い解説動画の生成から YouTubeアップロードまで自動で行う Web UI。従来のポッドキャスト音声要約（Gemini Notebook、旧 NotebookLM）も選択できる。内部の 3 フェーズ（submit → collect → upload）はユーザーに見せず、MeTube のようなシンプルな体験を提供する。

**設計思想（MeTube に倣う）:**

- URL を入れて「動画を作成」を押すだけ（既定は「澪と透の解説動画」）
- 処理中と完了済みの 2 セクションで進捗を把握
- オプションは最小限、デフォルトで動く
- ダークテーマ、1 画面完結

## 技術スタック


| 項目       | 選定                   | 理由                               |
| -------- | -------------------- | -------------------------------- |
| バックエンド   | FastAPI              | 既存の async パイプラインと直接呼べる           |
| テンプレート   | Jinja2               | SSR で十分。SPA フレームワーク不要            |
| インタラクション | htmx                 | ボタン操作 + 5 秒ポーリングで自動更新            |
| CSS      | Pico CSS (dark mode) | classless でダークテーマ対応。カスタム CSS 最小限 |


htmx・Pico CSS は CDN から読み込み。

## 画面レイアウト

1 画面構成。上から順に配置する。

```
┌─────────────────────────────────────────────────────────────────┐
│  動画解説スタジオ                           ● 2 processing     │  ← ヘッダー
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────────────────────────────────┐  ┌──────────┐ │
│  │ 解説したいURL（1行に1つ）                    │  │動画を作成│ │  ← URL 入力
│  └─────────────────────────────────────────────┘  └──────────┘ │
│                                                                 │
│  動画タイプ: [ポッドキャスト音声要約 ▼]  公開範囲: [一般公開 ▼] │  ← オプション
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│  Processing                                                     │
│  ─────────────────────────────────────────────────────────────  │
│                                                                 │
│  │ 🟡  論文タイトルA                   Generating audio...    │ │  ← 処理中ジョブ
│  │ 🔵  記事タイトルB                   Uploading to YouTube...│ │
│  │                                                             │ │
├─────────────────────────────────────────────────────────────────┤
│  Completed                                                      │
│  ✅ Clear completed  🔄 Retry failed                            │
│  ─────────────────────────────────────────────────────────────  │
│                                                                 │
│  │ ✅  論文タイトルC                              🔗  🗑     │ │  ← アップロード済み
│  │ ❌  記事タイトルD                              🔄  🗑     │ │  ← 失敗
│  │     ERROR: NotebookLM timeout after 1200s                   │ │
│  │                                                             │ │
└─────────────────────────────────────────────────────────────────┘
```

### ヘッダー

- アプリ名「動画解説スタジオ」
- 処理中ジョブ数のバッジ（例: `● 2 processing`）。0 件なら非表示
- ダークモード切替は不要（常にダーク）

### URL 入力エリア

- 改行対応テキスト入力 + 「動画を作成」ボタン
- 複数 URL は改行区切りで入力可能
- Enter キーでも送信可能
- 動画タイプ: `podcast`（ポッドキャスト音声要約、既定）/ `lecture`（澪と透の解説動画）
- 公開範囲: `public`（一般公開、既定）/ `unlisted`（限定公開）
  - ドロップダウンの初期選択は `settings.youtube.privacy_status` に従う（`config/settings.yaml` の既定値は `public`）
  - 選択値はジョブごとに state.json へ保存し、再起動や再試行を挟んでも維持する
  - 旧ジョブなど値がない場合は `settings.youtube.privacy_status` を使う
- NotebookLM 方式の詳細設定: Prompt プリセット選択 + Audio Length 選択
  - `settings.yaml` の `prompt_presets` からドロップダウンを動的生成
  - Audio Length: `short` / `default`
  - デフォルト値で動くので、触らなくて OK
- 既に処理中・アップロード済み (`queued` / `generating` / `video_ready` / `uploading` / `uploaded`) の URL はスキップされる（重複実行ガード）。`failed` の URL は再追加で `queued` に戻る
- 同一バッチ内の重複 URL は 1 件に集約される

### Processing セクション

`queued`・`generating`・`video_ready`・`uploading` 状態のジョブを表示。

各ジョブの表示内容:

- ステータスアイコン（色付き丸）
- 記事タイトル（クリックで元 URL を新しいタブで開く）
- 現在のフェーズを日本語で表示:
  - `queued` → 「準備中...」
  - `generating` → 「動画を生成中...」
  - `video_ready` → 「動画変換完了、アップロード待ち」
  - `uploading` → 「YouTube にアップロード中...」

htmx で 5 秒ごとにセクション全体を更新。ジョブが完了すると自動的に Completed に移動。

### Completed セクション

`uploaded`・`failed` 状態のジョブを表示。新しい順。

一括操作ボタン（MeTube と同じ）:

- **Clear completed**: uploaded ジョブを一覧から削除（failed はエラー内容とリトライ機会を残すため対象外。個別の 🗑 で削除する）
- **Retry failed**: 全 failed ジョブを再実行

各ジョブの表示内容:

- ステータスアイコン（✅ or ❌）
- 記事タイトル（クリックで元 URL を新しいタブで開く）
- 操作アイコン:
  - uploaded: 🔗（YouTube を開く）、🗑（一覧から削除）
  - failed: 🔄（リトライ）、🗑（一覧から削除）
- failed の場合: タイトルの下にエラーメッセージを小さく表示（MeTube と同じ）
- `lecture` はサムネイルプレビュー、動画、サムネイル、AI背景、背景生成プロンプト、投稿情報JSONへのリンクと、title / description / tags の確認欄を表示する。
- 投稿情報に記録された台本AIの実使用モデルと、背景のprovider / modelも表示する。

htmx で 5 秒ごとに更新。

## 内部処理フロー

ユーザーが「動画を作成」を押すと、以下がバックグラウンドで自動実行される:

```
動画を作成ボタン
  → submit_urls() ... mode に応じて生成ジョブを開始
  → collect_audio(poll=True)
      lecture: 台本 + 音声 + スライド + 動画 + サムネ + 投稿情報を生成
      notebooklm: 完了待ち + DL + 画像認証選択 + AIサムネ/複数背景 + 動画変換
  → upload_videos() ... YouTube アップロード
```

これは既存の `run_pipeline()` をそのまま呼ぶ。ユーザーから見ると、ジョブのステータスが「動画を生成中...」→「YouTube にアップロード中...」→ ✅ と遷移するだけ。NotebookLM の画像専用プロファイルが失効している場合は、全 NotebookLM 操作を終えた後に通常プロファイルへ自動退避する。使用プロファイルと動画へ渡した背景一覧は`image_profile_used` / `background_paths` として state に保存する。

### 並行実行の制御

- パイプラインが実行中に新しい URL が追加された場合: **キューに入れて順次実行**
- 理由: state.json の同時書き込みと NotebookLM / YouTube の並行操作を避けるため
- **パイプラインの実行は単一のワーカータスクに一本化する**。起動時リカバリ等の他の経路が直接 collect / upload を呼ぶと、同一ジョブの並行処理（二重 YouTube アップロード等）が起きるため、すべてキュー経由でワーカーに渡す
- キュー投入前に必ず state.json へ `queued` ステータスを書き込む。これによりワーカーが処理を始める前から UI に表示され、サーバー再起動でもジョブが失われない
- ヘッダーバッジの processing / queued 件数は state.json のジョブステータスから数える（インメモリのカウンタは持たない）。例: `● 2 processing, 3 queued`
- 空バッチ（`entries=[]`）の投入は collect / upload スイープの起動として機能する（`run_pipeline` は entries に関わらず state.json 上の全 `generating` / `video_ready` ジョブを処理するため）
- バッチ全体が例外で失敗した場合、`queued` のまま残ったジョブは failed に遷移させてエラーを可視化する（submit まで進んだジョブは復旧可能性があるため触らない）

```python
import asyncio

_task_queue: asyncio.Queue[list[UrlEntry]] = asyncio.Queue()

async def pipeline_worker(settings: Settings):
    """バックグラウンドワーカー: キューからバッチを取り出して直列実行."""
    while True:
        entries = await _task_queue.get()
        try:
            await run_pipeline(
                entries, settings, force=False, allow_interactive_auth=False
            )
        except Exception as exc:
            logger.exception("Pipeline error: {}", exc)
            # queued のまま残ったジョブを failed にする
        finally:
            _task_queue.task_done()
```

### 起動時リカバリ

サーバー起動時に state.json 内の未完了ジョブをワーカーキュー経由で復旧する:

- `queued` ジョブ → バッチとして再投入（submit からやり直す）
- `generating` / `video_ready` ジョブ → 空バッチを投入し、run_pipeline の collect / upload スイープに回収させる（queued の再投入がある場合はそのバッチのスイープで回収されるため追加投入しない）

**既知の制限**: 音声生成がポーリングタイムアウト（`generation_timeout_seconds`）を超えた `generating` ジョブは UI 上「音声を生成中...」のまま残る。定期スイープは存在しないため、再回収のトリガーは「別 URL の Add（そのバッチの collect スイープ）」または「サーバー再起動（起動時リカバリ）」のいずれかになる。

### 認証エラーの扱い

Web サーバーは非対話コンテキストのため、YouTube トークンが無効な場合にブラウザ OAuth フローを開始しない（イベントループがブロックされ UI 全体がフリーズするため）。代わりに該当ジョブを failed にし、`uv run podcast auth youtube` での再認証を促すエラーメッセージを表示する。動画ファイルは`video_path` に残るため、再認証後のリトライではアップロードのみ再試行される。

## ステータスマッピング

state.json のステータスと UI 表示の対応:


| state.json    | セクション      | アイコン | 表示テキスト              |
| ------------- | ---------- | ---- | ------------------- |
| `queued`      | Processing | 🕐   | 準備中...              |
| `generating`  | Processing | ⏳    | 動画を生成中...           |
| `video_ready` | Processing | 🎬   | 動画変換完了、アップロード待ち     |
| `uploading`   | Processing | ⬆️   | YouTube にアップロード中... |
| `uploaded`    | Completed  | ✅    | （なし、アイコンのみ）         |
| `failed`      | Completed  | ❌    | エラーメッセージ            |


> **Note:** `uploading` は現在の state.json にない新ステータス。upload 開始時に
> `video_ready` → `uploading` に更新する変更が必要（pipeline.py の `upload_videos` 内）。
> 対応しない場合は `video_ready` を「アップロード待ち」として Processing に表示する。

## API エンドポイント

### ページ


| メソッド | パス  | 説明      |
| ---- | --- | ------- |
| GET  | `/` | ダッシュボード |


### htmx パーシャル（HTML フラグメント）


| メソッド | パス                       | 説明               |
| ---- | ------------------------ | ---------------- |
| GET  | `/partials/header-badge` | ヘッダーのステータスバッジ    |
| GET  | `/partials/processing`   | Processing セクション |
| GET  | `/partials/completed`    | Completed セクション  |


各パーシャルは `hx-trigger="every 5s"` で自動ポーリング。

### アクション API


| メソッド   | パス                      | 説明                |
| ------ | ----------------------- | ----------------- |
| POST   | `/api/add`              | URL を追加してパイプライン実行 |
| POST   | `/api/retry/{slug}`     | 失敗ジョブを再実行         |
| POST   | `/api/retry-all-failed` | 全失敗ジョブを再実行        |
| DELETE | `/api/jobs/{slug}`      | ジョブを一覧から削除        |
| POST   | `/api/clear-completed`  | 完了済みジョブを一括削除      |
| GET    | `/api/jobs/{slug}/artifacts/{kind}` | 生成成果物を取得 |


### API 詳細

#### `POST /api/add`

```
Form Data:
  urls: str              # 改行区切りの URL リスト（1行1URL）
  mode: str              # "podcast" (default) | "lecture"
  privacy_status: str    # "public" (default) | "unlisted"
  prompt: str            # プリセット名 (default: "default")
  audio_length: str      # "short" | "default"
```

- 各 URL のジョブを `queued` ステータスで state.json に書き込んでから、`UrlEntry` に変換してキューに追加。`privacy_status` も同時に保存する
- アクティブな URL（`queued` / `generating` / `video_ready` / `uploading` / `uploaded`）はスキップ
- `200 OK` + Processing セクションの HTML パーシャルを返す（`HX-Trigger: refreshAll` で全セクションをリフレッシュ）
- htmx: レスポンスでURL入力欄をクリア + Processing セクションをリフレッシュ

#### `POST /api/retry/{slug}`

- 該当ジョブ（`failed` のみ）をリセットしてキューに再投入。ジョブは**削除せず** `queued` に戻して state.json に残す（ワーカー処理開始までの間も UI に表示され、再起動でもリトライが失われない）
- 動画変換まで完了済み（`video_path` と `thumbnail_path` のファイルが存在）なら `video_ready` に戻し、音声の再生成をスキップしてアップロードのみ再試行する（動画の重複アップロード防止）
- htmx: Completed セクションをリフレッシュ（`HX-Trigger: refreshAll`）

#### `POST /api/retry-all-failed`

- 全 `failed` ジョブに `/api/retry/{slug}` と同じリセットを適用し、1 バッチで再投入

#### `DELETE /api/jobs/{slug}`

- state.json からジョブエントリを削除
- htmx: 該当セクションをリフレッシュ

#### `POST /api/clear-completed`

- `uploaded` ステータスの全ジョブを state.json から削除

#### `GET /api/jobs/{slug}/artifacts/{kind}`

- `kind`: `video` / `thumbnail` / `thumbnail-background` / `thumbnail-prompt` / `upload-metadata`
- state に記録された当該ジョブの成果物だけを返す。未完成・削除済みは 404。

## ファイル構成

```
src/webui/
├── __init__.py
├── cli.py               # webui エントリポイント (Click)
├── app.py               # FastAPI アプリ + ワーカー起動
├── routes.py            # ルーティング + API ハンドラ
└── templates/
    ├── base.html        # ベーステンプレート (Pico CSS dark + htmx)
    ├── dashboard.html   # メイン画面
    └── partials/
        ├── header_badge.html
        ├── processing.html
        └── completed.html
```

## CLI コマンド

```bash
./webui.sh [--config PATH]       # 既定ポート3000、PORT環境変数で変更可
uv run webui [--port 8080] [--config PATH]
```

- デフォルト: `http://127.0.0.1:8080`（localhost のみ。LAN 公開しない）
- 起動時にブラウザを自動で開く（`webbrowser.open()`）
- Ctrl+C で停止。実行中のタスクがあっても中断して OK（次回起動時に `generating` ジョブは collect で回収可能）

### ログ

`logs/webui.log` に DEBUG 以上を出力する（10 MB でローテーション、5 世代保持）。端末の標準出力だけでは端末を閉じた時点で失敗の記録が失われ、後から原因を追えないため。notebooklm-py は stdlib logging を使うので、root logger を loguru へ転送してから記録する（これがないと `RPC ... rpc_code=5` のような診断に必要な行が残らない）。転送する stdlib 側のレベルは WARNING 以上に絞る（INFO だと httpx が全リクエストを吐いてログが埋まる）。

## 依存パッケージ追加

```toml
# pyproject.toml の dependencies に追加
"fastapi>=0.115",
"uvicorn>=0.32",
"jinja2>=3.1",
"python-multipart>=0.0.9",
```

## 実装の優先順位

1. **MVP**: ダッシュボード表示（Processing + Completed）— state.json 読み取り + ポーリング
2. **v0.2**: 「Add」ボタンで `run_pipeline` をバックグラウンド実行
3. **v0.3**: Retry / Clear / Delete 操作
4. **v0.4**: キュー管理 + ヘッダーバッジ

## 非スコープ（将来検討）

- settings.yaml の GUI 編集
- ログのリアルタイムストリーミング
- ユーザー認証（ローカル専用のため不要。**そのため LAN に公開しないこと** — docker-compose は `127.0.0.1` バインド）
- urls.yaml のファイル選択・編集
- サムネイルのプレビュー
- Clear selected（チェックボックスで選択したジョブの一括削除）
- 実行中の「Add (queued)」ボタンテキスト切替フィードバック
