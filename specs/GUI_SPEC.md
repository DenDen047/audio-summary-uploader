# GUI 仕様書: Web ダッシュボード

## 概要

URL を入力するだけで、音声要約の生成から YouTube アップロードまで自動で行う Web UI。
内部の 3 フェーズ（submit → collect → upload）はユーザーに見せず、MeTube のようなシンプルな体験を提供する。

**設計思想（MeTube に倣う）:**

- URL を入れてボタンを押すだけ
- 処理中と完了済みの 2 セクションで進捗を把握
- オプションは最小限、デフォルトで動く
- ダークテーマ、1 画面完結n

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
│  🎧 Audio Summary                           ● 2 processing     │  ← ヘッダー
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────────────────────────────────┐  ┌──────────┐ │
│  │ Enter URL                                    │  │   Add    │ │  ← URL 入力
│  └─────────────────────────────────────────────┘  └──────────┘ │
│                                                                 │
│  Prompt: [default ▼]    Audio Length: [default ▼]               │  ← オプション
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
│  🗑 Clear selected  ✅ Clear completed  🔄 Retry failed         │
│  ─────────────────────────────────────────────────────────────  │
│                                                                 │
│  │ ✅  論文タイトルC                              🔗  🗑     │ │  ← アップロード済み
│  │ ❌  記事タイトルD                              🔄  🗑     │ │  ← 失敗
│  │     ERROR: NotebookLM timeout after 1200s                   │ │
│  │                                                             │ │
└─────────────────────────────────────────────────────────────────┘
```

### ヘッダー

- アプリ名「Audio Summary」
- 処理中ジョブ数のバッジ（例: `● 2 processing`）。0 件なら非表示
- ダークモード切替は不要（常にダーク）

### URL 入力エリア

- テキスト入力 + 「Add」ボタン（MeTube と同じ配置）
- 複数 URL は改行区切りで入力可能（textarea に切替 or 1 行ずつ追加）
- Enter キーでも送信可能
- オプション行: Prompt プリセット選択 + Audio Length 選択
  - `settings.yaml` の `prompt_presets` からドロップダウンを動的生成
  - Audio Length: `default` / `short` / `long`
  - デフォルト値で動くので、触らなくて OK

### Processing セクション

`generating`・`video_ready`・`uploading` 状態のジョブを表示。

各ジョブの表示内容:

- ステータスアイコン（色付き丸）
- 記事タイトル（クリックで元 URL を新しいタブで開く）
- 現在のフェーズを日本語で表示:
  - `generating` → 「音声を生成中...」
  - `video_ready` → 「動画変換完了」
  - `uploading` → 「YouTube にアップロード中...」

htmx で 5 秒ごとにセクション全体を更新。ジョブが完了すると自動的に Completed に移動。

### Completed セクション

`uploaded`・`failed` 状態のジョブを表示。新しい順。

一括操作ボタン（MeTube と同じ）:

- **Clear completed**: uploaded ジョブを一覧から削除
- **Retry failed**: 全 failed ジョブを再実行
- **Clear selected**: チェックボックスで選択したジョブを削除

各ジョブの表示内容:

- ステータスアイコン（✅ or ❌）
- 記事タイトル（クリックで元 URL を新しいタブで開く）
- 操作アイコン:
  - uploaded: 🔗（YouTube を開く）、🗑（一覧から削除）
  - failed: 🔄（リトライ）、🗑（一覧から削除）
- failed の場合: タイトルの下にエラーメッセージを小さく表示（MeTube と同じ）

htmx で 5 秒ごとに更新。

## 内部処理フロー

ユーザーが「Add」を押すと、以下がバックグラウンドで自動実行される:

```
Add ボタン
  → submit_urls() ... ノートブック作成 + 音声生成開始
  → collect_audio(poll=True) ... 完了待ち + DL + 動画変換
  → upload_videos() ... YouTube アップロード
```

これは既存の `run_pipeline()` をそのまま呼ぶ。ユーザーから見ると、ジョブのステータスが
「音声を生成中...」→「YouTube にアップロード中...」→ ✅ と遷移するだけ。

### 並行実行の制御

- パイプラインが実行中に新しい URL が追加された場合: **キューに入れて順次実行**
- 理由: state.json の同時書き込みを避けるため
- 実行中は「Add」ボタンのテキストを「Add (queued)」に変えてフィードバック
- キューが詰まっている場合はヘッダーに表示（例: `● 2 processing, 3 queued`）

```python
import asyncio

_task_queue: asyncio.Queue[list[UrlEntry]] = asyncio.Queue()
_is_running: bool = False

async def pipeline_worker(settings: Settings):
    """バックグラウンドワーカー: キューからジョブを取り出して実行."""
    global _is_running
    while True:
        entries = await _task_queue.get()
        _is_running = True
        try:
            await run_pipeline(entries, settings, force=False)
        except Exception as exc:
            logger.error("Pipeline error: {}", exc)
        finally:
            _is_running = False
            _task_queue.task_done()
```

## ステータスマッピング

state.json のステータスと UI 表示の対応:


| state.json    | セクション      | アイコン | 表示テキスト              |
| ------------- | ---------- | ---- | ------------------- |
| `generating`  | Processing | ⏳    | 音声を生成中...           |
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


### API 詳細

#### `POST /api/add`

```
Form Data:
  urls: str              # 改行区切りの URL リスト（1行1URL）
  prompt: str            # プリセット名 (default: "default")
  audio_length: str      # "short" | "default"
```

- URL を `UrlEntry` に変換してキューに追加
- 即座に `202 Accepted` を返す
- htmx: レスポンスでURL入力欄をクリア + Processing セクションをリフレッシュ

#### `POST /api/retry/{slug}`

- 該当ジョブを `failed` → リセットしてキューに再投入
- htmx: Completed セクションをリフレッシュ

#### `DELETE /api/jobs/{slug}`

- state.json からジョブエントリを削除
- htmx: 該当セクションをリフレッシュ

#### `POST /api/clear-completed`

- `uploaded` ステータスの全ジョブを state.json から削除

## ファイル構成

```
src/automator/
├── web/
│   ├── __init__.py
│   ├── app.py              # FastAPI アプリ + ワーカー起動
│   ├── routes.py            # ルーティング + API ハンドラ
│   └── templates/
│       ├── base.html        # ベーステンプレート (Pico CSS dark + htmx)
│       ├── dashboard.html   # メイン画面
│       └── partials/
│           ├── header_badge.html
│           ├── processing.html
│           └── completed.html
```

## CLI コマンド

```bash
uv run automator web [--port 8080] [--config PATH]
```

- デフォルト: `http://127.0.0.1:8080`（localhost のみ。LAN 公開しない）
- 起動時にブラウザを自動で開く（`webbrowser.open()`）
- Ctrl+C で停止。実行中のタスクがあっても中断して OK（次回起動時に `generating` ジョブは collect で回収可能）

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
- ユーザー認証（ローカル専用のため不要）
- urls.yaml のファイル選択・編集
- サムネイルのプレビュー

