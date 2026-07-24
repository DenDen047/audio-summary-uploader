# CLAUDE.md

本ファイルがエージェント向け指示の実体。`AGENTS.md` は本ファイルへのシンボリックリンク（Codex 等はそちらを読む）。編集は必ず本ファイルに対して行う。

## Project Overview

Gemini Notebook（旧 NotebookLM）→ YouTube 自動化パイプライン。URL リストからポッドキャスト風の音声要約を生成し、YouTube にアップロードする CLI ツール。講義動画（ずんだもん・四国めたん掛け合い）生成パイプラインも同居する。詳細仕様: `specs/PODCAST_SPEC.md`（音声要約）, `specs/LECTURE_SPEC.md`（講義動画）, `specs/GUI_SPEC.md`（Web UI）

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
