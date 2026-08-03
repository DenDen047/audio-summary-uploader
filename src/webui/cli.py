"""Web ダッシュボード CLI エントリポイント (Click)."""

import logging
from pathlib import Path

import click
from loguru import logger

from podcast.config import load_settings

_LOG_PATH = Path("logs/webui.log")
_LOGURU_LEVEL_NAMES = frozenset(
    {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}
)


class _InterceptHandler(logging.Handler):
    """stdlib logging を loguru へ転送する.

    notebooklm-py は stdlib logging を使うため、これがないと RPC エラー
    (`RPC GET_NOTEBOOK failed ... rpc_code=5` 等) がログファイルに残らない。
    失敗診断に必要な行なので取りこぼさない。
    """

    def emit(self, record: logging.LogRecord) -> None:
        level: str | int = (
            record.levelname
            if record.levelname in _LOGURU_LEVEL_NAMES
            else record.levelno
        )
        logger.opt(exception=record.exc_info).log(
            level, "[{}] {}", record.name, record.getMessage()
        )


def _setup_file_logging() -> Path:
    """端末を閉じても残るログファイルを用意する（標準出力だけでは追えないため）."""
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        _LOG_PATH, rotation="10 MB", retention=5, level="DEBUG", encoding="utf-8"
    )

    root = logging.getLogger()
    root.handlers = [_InterceptHandler()]
    # WARNING 以上だけ拾う。INFO だと httpx が全リクエストを吐いてログが埋まり、
    # 診断に必要な notebooklm-py の RPC エラーが埋没する。
    root.setLevel(logging.WARNING)
    return _LOG_PATH


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

    log_path = _setup_file_logging()
    settings = load_settings(config_path)
    app = create_app(settings)

    url = f"http://{host}:{port}"
    logger.info("Starting web dashboard at {} (log: {})", url, log_path)
    if not no_browser:
        import webbrowser

        webbrowser.open(url)
    uvicorn.run(app, host=host, port=port, log_level="warning")
