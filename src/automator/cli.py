"""CLI エントリポイント (Click)."""

import asyncio
from pathlib import Path

import click
from loguru import logger

from automator.config import load_settings
from automator.pipeline import (
    collect_audio,
    get_status_counts,
    run_pipeline,
    submit_urls,
    upload_videos,
)
from automator.report import print_report
from automator.url_parser import parse_url_file
from automator.youtube import authenticate


@click.group()
def main() -> None:
    """audio-summary-uploader: NotebookLM → YouTube automation pipeline."""


@main.command()
@click.argument("url_file", type=click.Path(exists=True, path_type=Path))
@click.option("--dry-run", is_flag=True, help="NotebookLM/YouTube操作を実行しない")
@click.option("--force", is_flag=True, help="処理済み URL も再処理する")
@click.option("--retry-failed", is_flag=True, help="失敗した URL のみ再処理する")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="設定ファイルパス (デフォルト: config/settings.yaml)",
)
def run(
    url_file: Path,
    dry_run: bool,
    force: bool,
    retry_failed: bool,
    config_path: Path | None,
) -> None:
    """URL リストを処理してYouTubeにアップロードする（3フェーズ一括実行）."""
    settings = load_settings(config_path)

    valid_presets = set(settings.notebooklm.prompt_presets.keys())
    entries = parse_url_file(url_file, valid_prompt_presets=valid_presets)

    if not entries:
        logger.warning("No valid URL entries found in {}", url_file)
        return

    results = asyncio.run(
        run_pipeline(
            entries,
            settings,
            dry_run=dry_run,
            force=force,
            retry_failed=retry_failed,
        )
    )

    print_report(results)


@main.command()
@click.argument("url_file", type=click.Path(exists=True, path_type=Path))
@click.option("--dry-run", is_flag=True, help="NotebookLM操作を実行しない")
@click.option("--force", is_flag=True, help="生成中/処理済み URL も再処理する")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="設定ファイルパス (デフォルト: config/settings.yaml)",
)
def submit(
    url_file: Path,
    dry_run: bool,
    force: bool,
    config_path: Path | None,
) -> None:
    """Phase 1: ノートブック作成＋音声生成を開始する."""
    settings = load_settings(config_path)

    valid_presets = set(settings.notebooklm.prompt_presets.keys())
    entries = parse_url_file(url_file, valid_prompt_presets=valid_presets)

    if not entries:
        logger.warning("No valid URL entries found in {}", url_file)
        return

    results = asyncio.run(
        submit_urls(entries, settings, force=force, dry_run=dry_run)
    )

    print_report(results)


@main.command()
@click.option("--poll", is_flag=True, help="生成完了までポーリングで待機する")
@click.option(
    "--timeout",
    type=int,
    default=None,
    help="ポーリングタイムアウト（秒）",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="設定ファイルパス (デフォルト: config/settings.yaml)",
)
def collect(
    poll: bool,
    timeout: int | None,
    config_path: Path | None,
) -> None:
    """Phase 2: 生成完了した音声をDL→サムネイル→動画変換する."""
    settings = load_settings(config_path)

    results = asyncio.run(
        collect_audio(settings, poll=poll, timeout=timeout)
    )

    print_report(results)


@main.command()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="設定ファイルパス (デフォルト: config/settings.yaml)",
)
def upload(config_path: Path | None) -> None:
    """Phase 3: video_ready のジョブを YouTube にアップロードする."""
    settings = load_settings(config_path)

    results = asyncio.run(upload_videos(settings))

    print_report(results)


@main.command()
@click.argument("url")
@click.option("--dry-run", is_flag=True)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
)
def run_single(url: str, dry_run: bool, config_path: Path | None) -> None:
    """単一の URL を処理する."""
    from automator.url_parser import UrlEntry

    settings = load_settings(config_path)
    entry = UrlEntry(url=url)

    results = asyncio.run(
        run_pipeline([entry], settings, dry_run=dry_run, force=True)
    )
    print_report(results)


@main.group()
def auth() -> None:
    """認証セットアップ."""


@auth.command()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
)
def youtube(config_path: Path | None) -> None:
    """YouTube API の OAuth 認証をセットアップする."""
    settings = load_settings(config_path)
    creds = authenticate(
        client_secret_path=Path(settings.credentials.youtube_client_secret),
        token_path=Path(settings.credentials.youtube_token),
    )
    if creds and creds.valid:
        logger.info("YouTube authentication successful!")
    else:
        logger.error("YouTube authentication failed")


@auth.command()
def notebooklm() -> None:
    """NotebookLM の認証をセットアップする (notebooklm login を実行)."""
    import shutil
    import subprocess

    notebooklm_bin = shutil.which("notebooklm")
    if notebooklm_bin is None:
        logger.error("'notebooklm' command not found. Run 'uv sync' first.")
        return

    logger.info("Running 'notebooklm login' ...")
    result = subprocess.run([notebooklm_bin, "login"], check=False)
    if result.returncode == 0:
        logger.info("NotebookLM authentication successful!")
    else:
        logger.error(
            "NotebookLM authentication failed (exit code {})",
            result.returncode,
        )


@main.command()
@click.option("--port", default=8080, help="ポート番号 (デフォルト: 8080)")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="設定ファイルパス (デフォルト: config/settings.yaml)",
)
def web(port: int, config_path: Path | None) -> None:
    """Web ダッシュボードを起動する."""
    import webbrowser

    import uvicorn

    settings = load_settings(config_path)

    from automator.web.app import create_app

    app = create_app(settings)

    url = f"http://127.0.0.1:{port}"
    logger.info("Starting web dashboard at {}", url)
    webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


@main.command()
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
)
def status(config_path: Path | None) -> None:
    """処理状況を確認する."""
    settings = load_settings(config_path)
    state_path = Path(settings.general.state_file)

    if not state_path.exists():
        logger.info("No state file found. No processing history.")
        return

    counts = get_status_counts(settings)
    total = sum(counts.values())

    logger.info("Last run: check state.json for details")
    logger.info("Total jobs: {}", total)
    for status_name in ("generating", "video_ready", "uploaded", "failed"):
        count = counts.get(status_name, 0)
        if count > 0:
            logger.info("  {}: {}", status_name.replace("_", " ").title(), count)
