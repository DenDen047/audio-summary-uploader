# PyTorch-based Project Template

## Setup

### Docker
```bash
$ docker compose up -d --build
$ docker compose down && docker compose up -d --build && docker exec -it [container_name] bash
```

### uv
```bash
$ uv python pin 3.11
$ uv venv --python 3.11
$ source .venv/bin/activate
$ uv init

## uv add ...
$ uv add ruff

## run python program
$ uv run python main.py

$ uv lock
## uv sync    # installs everything into .venv
$ uv export --format=requirements.txt > requirements.txt
$ deactivate
```

or

```bash
## copy pyproject.toml and uv.lock
$ uv sync
```

ref:
- [Pythonパッケージ管理 [uv] 完全入門](https://speakerdeck.com/mickey_kubo/pythonpatukeziguan-li-uv-wan-quan-ru-men)
- [uvでパッケージ管理をしよう！〜初心者でも分かる！〜仮想環境を簡単に構築](https://youtu.be/VgH1GKSCXJQ?si=B-o0UPSoZjrfkHTY)

## GPU Cloud Setup

### Lambda Cloud

ref: https://lambda.ai/blog/set-up-a-tensorflow-gpu-docker-container-using-lambda-stack-dockerfile

```bash
ssh ubuntu@IP_ADDRESS -i ~/.ssh/lambda_cloud
```

```bash
curl -fsSL https://raw.githubusercontent.com/DenDen047/dotfiles/refs/heads/master/setup_scripts/lambda_cloud1.sh | bash
# if failed in the last step
sudo apt-get update && sudo apt-get install -y lambda-stack-cuda && sudo reboot

# after reboot, run the following command
curl -fsSL https://raw.githubusercontent.com/DenDen047/dotfiles/refs/heads/master/setup_scripts/lambda_cloud2.sh | bash
```

You can easily upload files to the cloud using the [FTP/SFTP/SSH Sync Tool](https://marketplace.visualstudio.com/items?itemName=oorzc.ssh-tools) extension.

### Modal

ref: https://modal.com/docs/guide

```bash
modal setup
modal run src/modal_sample.py
```

## Usage

## Project Configuration

```bash
.
├── README.md            # プロジェクト概要と使い方を記述
├── conf/                # 実験設定ファイル (例: parameters.yml, secrets.yml)
├── data/                # データや中間成果物の一時保存場所
├── notebooks/           # JupyterLabでの実験ノート
├── pyproject.toml       # Pythonプロジェクトの主要設定ファイル (PEP 518準拠)
├── setup.cfg            # pyproject.toml未対応の設定を補完
├── specs/               # 仕様書やドキュメント
└── src/                 # Pythonパッケージコード (共通処理の関数やクラスなど)
```

## Data directory

Please see the details [here](https://docs.kedro.org/en/stable/faq/faq.html#what-is-data-engineering-convention).

```bash
data/
├── 01_raw/              # Original, immutable data from source systems
├── 02_intermediate/     # Partially processed (cleaned/transformed) data
├── 03_primary/          # Canonical datasets for feature engineering
├── 04_feature/          # Engineered features ready for modeling
├── 05_model_input/      # Data prepared specifically for model training
├── 06_models/           # Trained models (e.g., .pkl, .h5 files)
├── 07_model_output/     # Model outputs like predictions or embeddings
└── 08_reporting/        # Reports, visualizations, dashboards, final outputs
```

## Development

### Git Workflow

This project follows `git-flow`.

1.  **Starting Work:** Begin new development or experiments by creating a branch off of the `develop` branch.
2.  **Merging to `develop`:** Once work intended for `develop` is complete, merge it into the `develop` branch (creating a Pull Request on GitHub is recommended if applicable) and delete the original branch.
3.  **Keeping History (Archiving):** If you wish to keep the code and history of a branch *without* merging it into `develop` (e.g., failed experiments, pure explorations), rename the branch to `archive/<original-branch-name>`.
    * Example: To archive a branch named `exp/try-hyperparams-v1`, rename it to `archive/exp/try-hyperparams-v1`.
4.  **Archived Branches:** Branches prefixed with `archive/` are kept for reference only and must *not* be merged into active branches like `develop` or `main`.

### Commit Message

Following commitlint rule:
- https://github.com/conventional-changelog/commitlint
- https://www.conventionalcommits.org/en/v1.0.0/

### Jupyter Notebook on Cursor Editor

<iframe width="560" height="315" src="https://www.youtube.com/embed/eOSfeBIBzr0?si=MFjxL47thNJGC1SN" title="YouTube video player" frameborder="0" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe>

### Visualization

- colormap: `turbo`

### Naming Conventions for Rotation / Transformation Matrices
ref: https://en.wikipedia.org/wiki/Active_and_passive_transformation

To avoid confusion, we distinguish between **active** and **passive** interpretations:

- **Active rotation / transformation**
  - Variables: `R_active`, `R_apply`, `R_obj`, `T_active`
  - Meaning: Actively rotating points or vectors (e.g., applying to a point cloud).

- **Passive rotation / transformation**
  - Variables: `R_world_to_cam`, `R_frame`, `R_pose`, `T_world_to_cam`
  - Meaning: Changing the coordinate frame (e.g., camera extrinsics).

- **Other common conventions**
  - `R_ext`, `T_ext`: Extrinsic parameters (world → camera transformation).
  - `R_int`, `K`: Intrinsic parameters (camera matrix).
  - `R_wc`, `R_cw`: Shorthand for `R_world_to_cam`, `R_cam_to_world`.

### Conversion between Active and Passive

- **Rotation matrices**
  - `R_passive = R_active.T`
  - `R_active = R_passive.T`

- **Transformation matrices (SE(3))**
  - `T_passive = T_active^-1`
  - `T_active = T_passive^-1`

This ensures consistent handling of both interpretations.

## Useful Tools

- [SAM2 Colab Notebook](https://colab.research.google.com/drive/1q-_LLIBZ-WW64VRzJ9fSVYDBOvADvWkW?usp=sharing)
- [MoGe Colab Notebook](https://colab.research.google.com/drive/1reb8Hn_0N7N3i1LgXbMhm7LkaDcA4CKj?usp=sharing&authuser=1#scrollTo=tTDZf8kR7_nV)
- [utils3d](https://github.com/EasternJournalist/utils3d)

## Reference

- [【Pythonの仮想環境を比較】〜オススメを紹介 〜](https://youtu.be/r4SkIhQThe0?si=kziY5m9s05gCk9Hx)
- [モダンなPyTorchのテンプレ](https://zenn.dev/dena/articles/6f04641801b387)
- [timm – PyTorch Image Models](https://huggingface.co/timm)
- [Best Practices for Python Coding](https://cyberagentailab.github.io/BestPracticesForPythonCoding/)

### Sample Projects

- https://github.com/Delgan/loguru
- https://github.com/kotaro-kinoshita/yomitoku

### Claude Code

- [Claude Code どこまでも](https://speakerdeck.com/nwiizo/claude-everywhere)
- [Claude Code の settings.json は設定した方がいい](https://syu-m-5151.hatenablog.com/entry/2025/06/05/134147)
- [Claude Code の CLAUDE.mdは設定した方がいい](https://syu-m-5151.hatenablog.com/entry/2025/06/06/190847)
