"""FastAPI アプリケーション + バックグラウンドワーカー."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from loguru import logger

from automator.config import Settings
from automator.pipeline import _load_state, collect_audio, run_pipeline, upload_videos
from automator.url_parser import UrlEntry

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_task_queue: asyncio.Queue[list[UrlEntry]] = asyncio.Queue()
_is_running: bool = False
_queued_count: int = 0


def get_queue_status() -> tuple[bool, int]:
    """現在の実行状態とキュー内のジョブ数を返す."""
    return _is_running, _queued_count


async def enqueue_urls(entries: list[UrlEntry]) -> None:
    """URL をキューに追加する."""
    global _queued_count
    _queued_count += 1
    await _task_queue.put(entries)


async def _recover_orphaned_jobs(settings: Settings) -> None:
    """サーバー起動時: state.json 内の未完了ジョブを復旧する.

    - "queued" ジョブ → キューに再投入（submit からやり直す）
    - "generating" ジョブ → collect + upload を直接実行（submit 済みのため）
    - "video_ready" ジョブ → upload を直接実行（動画変換済みのため）
    """
    state_path = Path(settings.general.state_file)
    if not state_path.exists():
        return

    state = _load_state(state_path)
    jobs = state.get("jobs", [])

    # queued ジョブを再投入
    queued = [j for j in jobs if j.get("status") == "queued"]
    if queued:
        entries = [
            UrlEntry(
                url=j["url"],
                audio_length=j.get("audio_length"),
                prompt=j.get("prompt"),
            )
            for j in queued
        ]
        logger.info(
            "Recovering {} queued jobs from previous session", len(queued)
        )
        await enqueue_urls(entries)

    # generating ジョブは submit 済み → collect + upload で復旧
    generating = [
        j for j in jobs if j.get("status") == "generating" and j.get("notebook_id")
    ]
    if generating:
        logger.info(
            "Recovering {} generating jobs (collect + upload)",
            len(generating),
        )
        try:
            collect_results = await collect_audio(settings, poll=True)
            for r in collect_results:
                logger.info(
                    "Recovery collect: url={} status={} error={}",
                    r.url, r.status, r.error,
                )
            upload_results = await upload_videos(settings)
            for r in upload_results:
                logger.info(
                    "Recovery upload: url={} status={} error={}",
                    r.url, r.status, r.error,
                )
        except Exception as exc:
            logger.error("Recovery failed for generating jobs: {}", exc)

    # video_ready ジョブは動画変換済み → upload のみで復旧
    # (generating 復旧内の upload_videos が成功済みなら state 上は uploaded になっており
    #  upload_videos が再読込するため空振りになる。失敗時はここで再試行される。)
    try:
        upload_results = await upload_videos(settings)
        for r in upload_results:
            logger.info(
                "Recovery upload: url={} status={} error={}",
                r.url, r.status, r.error,
            )
    except Exception as exc:
        logger.error("Recovery failed for video_ready jobs: {}", exc)


async def pipeline_worker(settings: Settings) -> None:
    """バックグラウンドワーカー: キューからジョブを取り出して実行."""
    global _is_running, _queued_count
    while True:
        entries = await _task_queue.get()
        _is_running = True
        _queued_count = max(0, _queued_count - 1)
        urls = [e.url for e in entries]
        logger.info("Pipeline worker: processing {} URLs: {}", len(entries), urls)
        try:
            results = await run_pipeline(entries, settings, force=False)
            for r in results:
                logger.info(
                    "Pipeline result: url={} status={} phase={} error={}",
                    r.url, r.status, r.phase, r.error,
                )
        except Exception as exc:
            logger.error("Pipeline error: {}", exc)
            import traceback
            logger.error("Traceback:\n{}", traceback.format_exc())
        finally:
            _is_running = False
            _task_queue.task_done()
            logger.info("Pipeline worker: done processing")


def create_app(settings: Settings) -> FastAPI:
    """FastAPI アプリを構築する."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(pipeline_worker(settings))
        logger.info("Pipeline worker started")
        recovery_task = asyncio.create_task(_recover_orphaned_jobs(settings))
        yield
        recovery_task.cancel()
        task.cancel()

    app = FastAPI(title="Audio Summary", lifespan=lifespan)
    app.state.settings = settings

    from automator.web.routes import router

    app.include_router(router)

    return app
