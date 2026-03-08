"""CLI エントリポイント (Click)."""

import asyncio
from pathlib import Path

import click
from loguru import logger

from automator.config import load_settings
from automator.pipeline import run_pipeline
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
    """URL リストを処理してYouTubeにアップロードする."""
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
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
)
def status(config_path: Path | None) -> None:
    """処理状況を確認する."""
    import json

    settings = load_settings(config_path)
    state_path = Path(settings.general.state_file)

    if not state_path.exists():
        logger.info("No state file found. No processing history.")
        return

    state = json.loads(state_path.read_text(encoding="utf-8"))
    processed = state.get("processed", [])
    success = sum(1 for e in processed if e.get("status") == "success")
    failed = sum(1 for e in processed if e.get("status") == "failed")

    logger.info("Last run: {}", state.get("last_run", "N/A"))
    logger.info("Total processed: {}", len(processed))
    logger.info("  Success: {}", success)
    logger.info("  Failed: {}", failed)
