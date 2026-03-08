# CLAUDE.md

## Project Overview

## Commands

```bash
# Package management (UV, not pip)
uv add <package>            # Install a new dependency
uv sync                     # Install all dependencies from lockfile

# Lint
ruff check .
```

## Environment Strategy

3つの環境を使い分ける。Docker とホスト venv は同等構成を目指すが、二重管理コストは許容する。

| 環境 | 用途 | ネイティブ拡張 |
|------|------|---------------|
| **ホスト uv venv** | データ生成（Step 1-4）、小規模学習・推論、BlenderProc | 段階的に追加 |
| **Docker** (`denden047/synchuman`) | 推論・学習の動作確認（フル構成） | flash-attn, spconv 等を含む |
| **レンタル GPU サーバー** | 本格学習（H200 等）| Docker イメージと同等構成 |


```bash
# Docker
docker compose up -d --build
docker exec -it [container_name] bash
```

## Code Style

- **Fail Fast**: Crash immediately on errors for debugging — no silent failures.
- **Explicit checks**: Use `if` statements instead of `try-except`.
- **Logging**: Use `loguru` (`from loguru import logger`), not `print()` or stdlib `logging`.
- **Paths**: Use `pathlib.Path`, not raw strings.
- **Python**: 3.11 (`.python-version`), type hints throughout.
- **Spec-Code Consistency**: Specs (`specs/`) and code must always match. When implementing from a spec, follow it exactly. When modifying code that has a corresponding spec, update the spec in the same change. When modifying a spec, update the code in the same change. If a conflict is found between spec and code, stop and ask the user which is correct before proceeding.

### Data Directory Convention

```
data/
├── 01_raw/          # Input images and raw 3D meshes ({subject_id}.glb)
├── 02_intermediate/ # Normalized GLB files
├── 03_primary/      # Rendered multiview images & masks (8 views + face crop)
├── 04_feature/      # Voxel occupancy grids (voxels.npz / voxels.ply)
├── 05_model_input/  # metadata.json, train_list.txt, val_list.txt
├── 07_model_output/ # Pipeline inference outputs
└── 08_reporting/    # Timing/performance logs (JSON)
```

Training data pipeline: `01_raw → 02_intermediate → 03_primary → 04_feature → 05_model_input`

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
