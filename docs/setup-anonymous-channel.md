# ⑧ 匿名 YouTube チャンネル準備 runbook（D1=b）

公開先を匿名チャンネルに確定するための**手動セットアップ手順**。ここが済めば、以降の自動化
（日本語タイトル②・AIサムネ④・カテゴリ→プレイリスト③）はこのチャンネルに対して動く。

> アカウントの作成・本人確認・電話番号認証・OAuth 同意などは**人手が必須**（私＝Claude では実行不可）。
> 本書では「**あなたにしかできない手動作業**」と「**私が用意済み／私ができること**」を分けて示す。

> 用途分離: NotebookLM と Gemini 画像生成（Nano Banana）は**会社 Workspace アカウント**のまま使う。
> **YouTube チャンネルだけ別の匿名アカウント**にする（チャンネル公開面に個人情報を出さないため）。

---

## ✅ 私が用意済み（コード/設定/素材）

- **AIサムネ/日本語タイトル/カテゴリ/概要欄サニタイズ**：実装・テスト済み（`feat/tech-verification`）。匿名チャンネルでもそのまま動く。
- **ブランド素材ジェネレータ**：`scripts/gen_branding.py`（アイコン800×800・バナー2048×1152を AI 生成）。
  - 実行（cookie 更新後）: `uv run notebooklm login` → `uv run python scripts/gen_branding.py`
  - 名前を変える: `uv run python scripts/gen_branding.py --name "<チャンネル名>" --tagline "<一言>"`
  - 出力: `tmp/branding/icon.png`, `tmp/branding/banner.png`
- **プレイリスト設定枠**：`config/settings.yaml` の `youtube.playlists`（ID を貼るだけの状態）。
- **チャンネル名/ハンドル/概要文の候補**（下記）。

---

## 0. 匿名ブランドの決定（候補・推奨）

| 候補 | 読み/意図 | ハンドル例 |
|---|---|---|
| **ながらAI**（推奨） | 「ながら聞き」×AI。通勤・作業中に聴く用途にフィット | `@nagara-ai` |
| AIみみ / AI耳 | 耳から入れるAI | `@ai-mimi` |
| 10分AI | 10分で要点 | `@10pun-ai` |
| 耳から最前線 | 最新を耳で | `@mimikara-ai` |

- **推奨**: `ながらAI` / ハンドル `@nagara-ai`（空いていれば）。
- **概要文（匿名・個人情報なし）例**:
  > AIの最新ニュースや論文を、日本語のポッドキャスト風音声でコンパクトにお届け。通勤・作業の「ながら聞き」にどうぞ。音声はAIによる自動生成です。正確な内容は各出典をご確認ください。
- 名前は最終的にあなたの判断で。決めたら `gen_branding.py --name` で素材を作り直せる。

---

## 1.（手動）専用 Google アカウント／ブランドアカウントを用意

- 身バレ防止のため、本名と紐づかない**新規 Google アカウント**を推奨（または既存アカウント配下に
  **ブランドアカウント**を作成: YouTube → 設定 → チャンネルを追加 → ブランドアカウント）。
- 2段階認証を設定。リカバリ情報に個人特定情報を入れすぎない。

## 2.（手動）YouTube チャンネルを作成・装飾

- youtube.com → チャンネル作成（ブランドアカウント推奨）。
- **チャンネル名/ハンドル**: 上記の匿名ブランドで設定。
- **アイコン**: `tmp/branding/icon.png`（800×800、円形クロップされる前提の中央配置）。
- **バナー**: `tmp/branding/banner.png`（2048×1152。全デバイス共通の**セーフエリアは中央 1235×338**。
  文字はその帯に収めてある）。最大 6MB。
- **概要（About）**: 上記の概要文。リンク欄に個人 SNS 等は載せない。
- **電話番号認証**を済ませる（**カスタムサムネのアップロードに必須** = ④ が効くために必要）。

## 3.（手動）カテゴリ別プレイリストを作成し ID を控える

コード内カテゴリ（`paper`/`news`/`engineering`/`business`）に対応する 4 つを作成（`default` は既定にフォールバック）:

| コードのカテゴリ | プレイリスト名（例） |
|---|---|
| `paper` | 📄 論文ななめ聴き |
| `news` | 🗞 AIニュース |
| `engineering` | 🛠 AI開発メモ |
| `business` | 💰 ビジネス・キャリア |

- 各プレイリスト URL の `...list=PLxxxx` から **playlist ID を控える**。
- `config/settings.yaml` の `youtube.playlists` に貼る（**キー名はカテゴリ名と完全一致**）:
  ```yaml
  youtube:
    playlist_id: "PL_default_既定"   # default カテゴリ用のフォールバック
    playlists:
      paper: "PLxxxx"
      news: "PLxxxx"
      engineering: "PLxxxx"
      business: "PLxxxx"
    all_playlist_id: "PLxxxx"        # 全動画横断プレイリスト（カテゴリ別に加えて常に追加）
  ```

## 4.（手動）Google Cloud で OAuth クライアントを作成

- console.cloud.google.com → 新規プロジェクト → **YouTube Data API v3** を有効化。
- **OAuth 同意画面**:
  - User type = 外部。
  - **公開ステータスを「本番（In production）」に設定**することを推奨（テスト中だと
    リフレッシュトークンが7日で失効し、無人/Web実行で詰まる）。機微スコープのため初回認証時に
    「未確認アプリ」警告が出るが、**自分だけで使う分は「詳細→続行」で通せる**。
    （テスト中のまま運用するなら、7日ごとに `uv run automator auth youtube` で再認証でも可）
- 認証情報 → OAuth クライアント ID（**デスクトップアプリ**）→ JSON をダウンロード →
  `credentials/youtube_client_secret.json` に配置。

## 5.（手動）匿名アカウントで認証＝公開先の切り替え

```bash
uv run automator auth youtube
```
- ブラウザが開いたら、**必ず手順1で作った匿名チャンネルのアカウント**で承認する
  （既存の自分のアカウントと取り違えない）。
- `credentials/youtube_token.json` が**匿名アカウントのトークンに置き換わる**＝以降のアップロード先が匿名チャンネルになる。
- ※元のアカウントに戻したい時は、再度 `auth youtube` で元アカウントを承認すればよい（トークンを差し替えるだけ）。

## 6.（確認）テスト1本

```bash
uv run notebooklm login         # ④AIサムネ用に cookie を新鮮化
uv run automator run urls.yaml --force
```
- アップロード先が**新しい匿名チャンネル**であることを確認（動画 URL のチャンネル）。
- 日本語タイトル・AIサムネ・カテゴリのプレイリスト振り分け・概要欄に個人情報が無いことを確認。

---

## あなたにしかできない作業（チェックリスト）

- [ ] 匿名 Google アカウント／ブランドアカウントの作成・2段階認証
- [ ] チャンネル作成、名前/ハンドル設定、**電話番号認証**
- [ ] アイコン/バナー/概要の設定（素材は `tmp/branding/` を使用）
- [ ] カテゴリ別プレイリスト 4 つ作成 → ID を `settings.yaml` に記入
- [ ] Google Cloud で OAuth クライアント作成（同意画面=本番推奨）→ `youtube_client_secret.json` 配置
- [ ] `uv run automator auth youtube` を**匿名アカウント**で承認

## 補足

- 既存の自分のチャンネルの動画は、混在を避けたいなら**限定公開化**を推奨。
- **AI機能の cookie**: ④AIサムネ・②タイトル・③カテゴリ判定は NotebookLM の cookie を使う。
  数十分〜で失効するので、**まとめて作る前に `uv run notebooklm login`** を一度実行する。
  失効していてもパイプラインは止まらず、サムネはクリーンなグラデにフォールバックする。
- 画像生成アカウントは**会社 Workspace のまま**（YouTube アカウントとは分離）。チャンネル公開面には露出しない。
</content>
