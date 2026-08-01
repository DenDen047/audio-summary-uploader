# CLAUDE.md

本ファイルがエージェント向け指示の実体。`AGENTS.md` は本ファイルへのシンボリックリンク（Codex 等はそちらを読む）。編集は必ず本ファイルに対して行う。

## Project Overview

Gemini Notebook（旧 NotebookLM）→ YouTube 自動化パイプライン。URL リストからポッドキャスト風の音声要約を生成し、YouTube にアップロードする CLI ツール。講義動画（澪・透＝紫ノ宮澪・麦野透の掛け合い）生成パイプラインも同居する。詳細仕様: `specs/PODCAST_SPEC.md`（音声要約）, `specs/LECTURE_SPEC.md`（講義動画）, `specs/GUI_SPEC.md`（Web UI）

### 現在の運用方針（2026-07-24 時点）

- **既定モードは podcast**。WebUI ダッシュボードの「動画タイプ」既定を podcast にしている（`src/webui/templates/dashboard.html`）。
- **lecture モードの本格生成は当面保留**。クロノITチャンネルが解説動画の自作システムを OSS 化／サービス化する続報を出す見込みで、1 から自作せずそれを待つ方針（目安: 2026-08 下旬に続報を確認して再開判断）。参考動画: https://youtu.be/Jul3isnP5qQ
- **当面の注力先は澪・透（紫ノ宮澪・麦野透）の TTS**。現状は VOICEVOX（style 66=澪 / 69=透）。

## Commands

```bash
# Package management (UV, not pip)
uv add <package>            # Install a new dependency
uv sync                     # Install all dependencies from lockfile

# Python commands
uv run python <file>      # Run a Python file

# Lint
ruff check .
```

### notebooklm-py の更新

`notebooklm-py` は PyPI リリースではなく upstream の `main` ブランチを追う（`pyproject.toml` の `[tool.uv.sources]`）。Gemini Notebook（旧 NotebookLM）は Google 側の仕様変更が頻繁で、リリース間隔より修正の到着が速いため。実例として 2026-07 の `notebooklm.google.com` → `notebook.google.com` ドメイン移行では、ログイン完了判定の修正（[PR #2015](https://github.com/teng-lin/notebooklm-py/pull/2015)）が `main` にのみ入り、PyPI のどのリリースにも載らなかった。

常に最新を取り込む。`uv.lock` にはその時点のコミットが記録されるので、再現性は保たれる。

```bash
uv lock --upgrade-package notebooklm-py && uv sync
```

`main` を追う以上、上流の変更で壊れることがある。認証やパイプラインが急に失敗し始めたら、まず `uv run notebooklm auth check --test` でサーバー側まで含めた認証状態を確認する（`notebooklm doctor` はローカルに cookie があるかしか見ないので、失効セッションでも pass になる）。それでも切り分かない場合は `uv.lock` を直前のコミットへ戻して再現するか確かめる。

## Code Style

- **Fail Fast**: Crash immediately on errors for debugging — no silent failures.
- **Explicit checks**: Use `if` statements instead of `try-except`.
- **Logging**: Use `loguru` (`from loguru import logger`), not `print()` or stdlib `logging`.
- **Paths**: Use `pathlib.Path`, not raw strings.
- **Python**: 3.11 (`.python-version`), type hints throughout.
- **Prompts**: AI に送る定型プロンプトは Python コードに埋め込まず、各パッケージの `prompts/` 配下の md ファイルで管理する（`{{PLACEHOLDER}}` を `.replace()` で埋める方式）。
- **Writing Style（文書全般）**: 仕様書・README・レポート等の散文では段落内で手動改行しない。1段落＝1行で書き、見た目の折り返しはエディタに任せる（表・コードブロック・図解は除く）。
- **Spec-Code Consistency**: Specs (`specs/`) and code must always match. When implementing from a spec, follow it exactly. When modifying code that has a corresponding spec, update the spec in the same change. When modifying a spec, update the code in the same change. If a conflict is found between spec and code, stop and ask the user which is correct before proceeding.

### Directory Structure

```
src/sources/         # 情報源の取得層（podcast/lecture 両モード共通。本文抽出・サニタイズ）
src/podcast/         # ポッドキャスト音声要約パイプライン（Gemini Notebook → YouTube）
src/lecture/         # 講義動画パイプライン
src/webui/           # 共通 Web ダッシュボード（FastAPI、podcast/lecture 両モードを扱う）
specs/               # 仕様書
tmp/                 # 一時ファイル（audio, thumbnails, videos）
credentials/         # OAuth トークン等（.gitignore 対象）
config/              # settings.yaml
```

## Claude Code Skills

プロジェクト固有のスキルは `.claude/skills/` に配置する（グローバルの `~/.claude/` は使わない）。Claude Code の運用テスト中のため、設定・スキルはすべてプロジェクト内で完結させること。

**メタルール**: ユーザーから開発スタイル・ワークフロー・ツール利用方法に関する指示があった場合、その内容をその場で `CLAUDE.md` または対応する `.claude/skills/*/SKILL.md` に反映すること。口頭で確認するだけでなく、必ずファイルに書き残す。

- lecture台本のパイプライン生成は、`lecture-understand-source`、`lecture-plan-teaching`、`lecture-write-scenes`、`lecture-review-teaching`を別々のClaude Codeセッションで順番に実行する。呼び出し元が該当SKILL.md、`src/lecture/prompts/`の工程MD、schema、許可済み入力だけを1メッセージへ展開し、ファイル・シェルツールなしの構造化出力で受け取る。資料理解工程だけは`WebSearch` / `WebFetch`で関連する公式資料・一次研究・技術分析・必要に応じてHacker News等のコミュニティ反応を調査し、元資料の主張と補助文脈をJSON内で分離する。後段へ元資料を渡さず、合格済みJSONを確定入力として外部検索しない。工程プロンプト本文をPythonや別スキルへ複製しない。
- 各lecture段階の固定検証はPython側が管理し、失敗した段階だけを初回を含め最大3セッション実行する。試行回数を生成セッションの自己申告へ委ねない。
- `src/lecture/prompts/`のMarkdownは、段落や同一リスト項目を手動折り返ししない。見出し、段落間、独立したリスト項目、コード・JSON例などMarkdownの構造に必要な改行だけを残す。

## Lecture Visual Explanation Policy

- **Diagram first**: 講義動画では、関係・構造・手順・時間変化を図で表せる場合、箇条書きより図解を優先する。
- **Primary-source figures first**: 論文・記事の一次資料に論点を直接支える図がある場合は、出典とキャプションを保ったまま、その図を優先して使う。図の意味を変える加工や、本文から確認できない説明は加えない。
- **Match the visual form to the relation**: 対立・2軸はマトリクス、派生・分類はツリー、因果・処理順はフロー、積層依存はレイヤー、時間変化はタイムライン、フィードバックは循環図、定量比較は表を使う。全説明を同じ図型へ押し込めない。
- **Diagram direction must match the explanation**: ツリーは`items[0]`を親・根、残りを子にする。複数要素をまとめる主体を枝へ置かない。
- **Text is the fallback**: 一次資料の図、意味に合う構造図の順で検討し、関係性を視覚化できない論点だけを箇条書きや引用で説明する。
- 生成・編集した図解スライドは、完了前に実際のスクリーンショットを目視確認する。

## Modern CLI Tools

When running shell commands via the Bash tool, always prefer modern alternatives over legacy commands:

| Legacy | Modern | Notes |
|--------|--------|-------|
| `find` | `fd` | simpler, faster and user-friendly |
| `grep` | `rg` (ripgrep) | ripgrep is a line-oriented search tool that recursively searches the current directory for a regex pattern. By default, ripgrep will respect gitignore rules and automatically skip hidden files/directories and binary files.  |
