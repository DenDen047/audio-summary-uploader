# AGENTS.md

## Project Overview

NotebookLM → YouTube 自動化パイプライン。URL リストから NotebookLM で音声要約を生成し、YouTube にアップロードする CLI ツール。
詳細仕様: `specs/SPEC.md`

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
- **Spec-Code Consistency**: Specs (`specs/`) and code must always match. When implementing from a spec, follow it exactly. When modifying code that has a corresponding spec, update the spec in the same change. When modifying a spec, update the code in the same change. If a conflict is found between spec and code, stop and ask the user which is correct before proceeding.

### Directory Structure

```
src/automator/       # メインパッケージ
specs/               # 仕様書
tmp/                 # 一時ファイル（audio, thumbnails, videos）
credentials/         # OAuth トークン等（.gitignore 対象）
config/              # settings.yaml
```

## Codex Skills

プロジェクト固有のスキルは `.Codex/skills/` に配置する（グローバルの `~/.Codex/` は使わない）。
Codex の運用テスト中のため、設定・スキルはすべてプロジェクト内で完結させること。

**メタルール**: ユーザーから開発スタイル・ワークフロー・ツール利用方法に関する指示があった場合、
その内容をその場で `AGENTS.md` または対応する `.Codex/skills/*/SKILL.md` に反映すること。
口頭で確認するだけでなく、必ずファイルに書き残す。

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
