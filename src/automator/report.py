"""結果レポート生成."""

from dataclasses import dataclass

from loguru import logger


@dataclass
class ProcessResult:
    url: str
    title: str | None = None
    youtube_url: str | None = None
    status: str = "success"
    error: str | None = None
    phase: str | None = None


def _is_success(result: ProcessResult) -> bool:
    """フェーズ成功かどうかを判定する.

    各フェーズの正常ステータス (generating / video_ready / uploaded / success*)
    はすべて error が None になるため、error の有無で判定する。
    """
    return result.error is None


def print_report(results: list[ProcessResult]) -> None:
    """処理結果をターミナルに出力する."""
    success_count = sum(1 for r in results if _is_success(r))
    total = len(results)

    separator = "═" * 52
    logger.info("")
    logger.info(separator)
    logger.info(" NotebookLM → YouTube Automator  処理結果")
    logger.info(separator)
    logger.info("")

    if total == 0:
        logger.info("  処理対象の URL がありません。")
    else:
        logger.info("  ✅ 成功: {}/{}", success_count, total)
        logger.info("")

        for i, result in enumerate(results, 1):
            display = result.title or result.url
            if _is_success(result):
                logger.info("  {}. ✅ {}", i, display)
                if result.youtube_url:
                    logger.info("     📺 {}", result.youtube_url)
                elif result.status != "uploaded":
                    logger.info("     ({})", result.status)
            else:
                logger.info("  {}. ❌ {}", i, display)
                if result.error:
                    logger.info("     ⚠️  Error: {}", result.error)

    logger.info("")
    logger.info(separator)
