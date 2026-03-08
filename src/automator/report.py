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


def print_report(results: list[ProcessResult]) -> None:
    """処理結果をターミナルに出力する."""
    success_count = sum(1 for r in results if r.status.startswith("success"))
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
            if result.status.startswith("success"):
                display = result.title or result.url
                logger.info("  {}. ✅ {}", i, display)
                if result.youtube_url:
                    logger.info("     📺 {}", result.youtube_url)
            else:
                logger.info("  {}. ❌ {}", i, result.url)
                if result.error:
                    logger.info("     ⚠️  Error: {}", result.error)

    logger.info("")
    logger.info(separator)
