"""FastAPI アプリケーション + バックグラウンドワーカー."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from loguru import logger

from podcast.config import Settings
from podcast.locking import PipelineBusyError
from podcast.pipeline import _load_state, _save_state, run_pipeline
from podcast.url_parser import UrlEntry

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_task_queue: asyncio.Queue[list[UrlEntry]] = asyncio.Queue()


async def enqueue_urls(entries: list[UrlEntry]) -> None:
    """URL バッチをワーカーキューに追加する.

    空バッチは collect / upload スイープの起動として機能する
    (run_pipeline は entries に関わらず state.json 上の全 generating /
    video_ready ジョブを処理するため)。
    """
    await _task_queue.put(entries)


def _mark_queued_jobs_failed(state_path: Path, urls: list[str], error: str) -> None:
    """バッチ全体が失敗したとき、queued のまま残ったジョブを failed にする.

    submit まで進んだジョブ (generating 以降) は復旧可能性があるため触らない。
    """
    state = _load_state(state_path)
    changed = False
    for job in state.get("jobs", []):
        if job["url"] in urls and job.get("status") == "queued":
            job["status"] = "failed"
            job["error"] = error
            changed = True
    if changed:
        _save_state(state_path, state)


async def _recover_orphaned_jobs(settings: Settings) -> None:
    """サーバー起動時: state.json 内の未完了ジョブをワーカーキュー経由で復旧する.

    - "queued" ジョブ → バッチとして再投入（submit からやり直す）
    - "generating" / "video_ready" ジョブ → 空バッチを投入し、run_pipeline の
      collect / upload スイープに回収させる

    パイプラインの実行は必ずワーカーに一本化する。リカバリが直接
    collect / upload を呼ぶと、ワーカーと同一ジョブを並行処理して
    二重 YouTube アップロード等の競合が起きるため。
    """
    state_path = Path(settings.general.state_file)
    if not state_path.exists():
        return

    state = _load_state(state_path)
    jobs = state.get("jobs", [])

    queued = [j for j in jobs if j.get("status") == "queued"]
    in_flight = [j for j in jobs if j.get("status") in ("generating", "video_ready")]

    if queued:
        entries = [
            UrlEntry(
                url=j["url"],
                mode=j.get("mode", "podcast"),
                audio_length=j.get("audio_length"),
                prompt=j.get("prompt"),
                privacy_status=j.get("privacy_status"),
            )
            for j in queued
        ]
        logger.info("Recovering {} queued jobs from previous session", len(queued))
        # このバッチの run_pipeline が collect / upload スイープも行うため、
        # in_flight ジョブも一緒に回収される
        await enqueue_urls(entries)
    elif in_flight:
        logger.info(
            "Recovering {} in-flight jobs (collect/upload sweep)",
            len(in_flight),
        )
        await enqueue_urls([])


async def pipeline_worker(settings: Settings) -> None:
    """バックグラウンドワーカー: キューからバッチを取り出して直列実行する."""
    state_path = Path(settings.general.state_file)
    while True:
        entries = await _task_queue.get()
        urls = [e.url for e in entries]
        if entries:
            logger.info("Pipeline worker: processing {} URLs: {}", len(entries), urls)
        else:
            logger.info("Pipeline worker: running collect/upload sweep")
        try:
            results = await run_pipeline(
                entries, settings, force=False, allow_interactive_auth=False
            )
            for r in results:
                logger.info(
                    "Pipeline result: url={} status={} phase={} error={}",
                    r.url,
                    r.status,
                    r.phase,
                    r.error,
                )
        except PipelineBusyError as exc:
            # CLI 等が同じジョブを処理中。ジョブは相手が進めるので failed にしない。
            logger.warning("Pipeline busy, skipped this sweep: {}", exc)
        except Exception as exc:
            logger.exception("Pipeline error: {}", exc)
            # queued のまま放置すると UI で永遠に「準備中...」になるため failed にする
            _mark_queued_jobs_failed(
                state_path, urls, f"パイプライン実行に失敗しました: {exc}"
            )
        finally:
            _task_queue.task_done()
            logger.info("Pipeline worker: done processing")


def create_app(settings: Settings) -> FastAPI:
    """FastAPI アプリを構築する."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        worker = asyncio.create_task(pipeline_worker(settings))
        logger.info("Pipeline worker started")
        # キューに積むだけなので await で完了させる (fire-and-forget にしない)
        await _recover_orphaned_jobs(settings)
        yield
        worker.cancel()

    app = FastAPI(title="動画解説スタジオ", lifespan=lifespan)
    app.state.settings = settings

    from webui.routes import router

    app.include_router(router)

    return app
