# CLAUDE.md

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

## Claude Code Skills

プロジェクト固有のスキルは `.claude/skills/` に配置する（グローバルの `~/.claude/` は使わない）。
Claude Code の運用テスト中のため、設定・スキルはすべてプロジェクト内で完結させること。

**メタルール**: ユーザーから開発スタイル・ワークフロー・ツール利用方法に関する指示があった場合、
その内容をその場で `CLAUDE.md` または対応する `.claude/skills/*/SKILL.md` に反映すること。
口頭で確認するだけでなく、必ずファイルに書き残す。

## Modern CLI Tools

When running shell commands via the Bash tool, always prefer modern alternatives over legacy commands:

| Legacy | Modern | Notes |
|--------|--------|-------|
| `find` | `fd` | simpler, faster and user-friendly |
| `grep` | `rg` (ripgrep) | ripgrep is a line-oriented search tool that recursively searches the current directory for a regex pattern. By default, ripgrep will respect gitignore rules and automatically skip hidden files/directories and binary files.  |
