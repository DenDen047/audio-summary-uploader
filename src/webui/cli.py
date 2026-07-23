"""Web ダッシュボード CLI エントリポイント (Click)."""

from pathlib import Path

import click
from loguru import logger

from summary.config import load_settings


@click.command()
@click.option(
    "--host", default="127.0.0.1", help="ホストアドレス (デフォルト: 127.0.0.1)"
)
@click.option("--port", default=8080, help="ポート番号 (デフォルト: 8080)")
@click.option("--no-browser", is_flag=True, help="ブラウザを自動で開かない")
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, path_type=Path),
    default=None,
    help="設定ファイルパス (デフォルト: config/settings.yaml)",
)
def main(host: str, port: int, no_browser: bool, config_path: Path | None) -> None:
    """Web ダッシュボードを起動する（音声要約・講義動画 共通）."""
    import uvicorn

    from webui.app import create_app

    settings = load_settings(config_path)
    app = create_app(settings)

    url = f"http://{host}:{port}"
    logger.info("Starting web dashboard at {}", url)
    if not no_browser:
        import webbrowser

        webbrowser.open(url)
    uvicorn.run(app, host=host, port=port, log_level="warning")
