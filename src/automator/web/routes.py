"""ルーティング + API ハンドラ."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from loguru import logger

from automator.config import Settings
from automator.pipeline import _find_or_create_job, _load_state, _save_state
from automator.url_parser import UrlEntry
from automator.web.app import enqueue_urls, get_queue_status, templates

router = APIRouter()


def _get_settings(request: Request) -> Settings:
    return request.app.state.settings


def _get_jobs(settings: Settings) -> list[dict]:
    state_path = Path(settings.general.state_file)
    state = _load_state(state_path)
    return state.get("jobs", [])


def _processing_jobs(jobs: list[dict]) -> list[dict]:
    return [
        j
        for j in jobs
        if j.get("status")
        in ("queued", "generating", "video_ready", "uploading")
    ]


def _completed_jobs(jobs: list[dict]) -> list[dict]:
    return [
        j for j in jobs if j.get("status") in ("uploaded", "failed")
    ]


def _job_title(job: dict) -> str:
    meta = job.get("metadata")
    if meta and meta.get("title"):
        return meta["title"]
    return job.get("url", "Unknown")


def _status_display(status: str) -> dict[str, str]:
    mapping = {
        "queued": {"icon": "🕐", "text": "準備中..."},
        "generating": {"icon": "⏳", "text": "音声を生成中..."},
        "video_ready": {
            "icon": "🎬",
            "text": "動画変換完了、アップロード待ち",
        },
        "uploading": {
            "icon": "⬆️",
            "text": "YouTube にアップロード中...",
        },
        "uploaded": {"icon": "✅", "text": ""},
        "failed": {"icon": "❌", "text": ""},
    }
    return mapping.get(status, {"icon": "❓", "text": status})


def _template_ctx(**kwargs: object) -> dict[str, object]:
    """テンプレートコンテキストに共通ヘルパーを注入する."""
    kwargs.setdefault("job_title", _job_title)
    kwargs.setdefault("status_display", _status_display)
    return kwargs


# --- ページ ---


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    settings = _get_settings(request)
    jobs = _get_jobs(settings)
    _is_running, queued = get_queue_status()
    processing = _processing_jobs(jobs)
    completed = _completed_jobs(jobs)
    presets = list(settings.notebooklm.prompt_presets.keys())

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        _template_ctx(
            processing_jobs=processing,
            completed_jobs=completed,
            processing_count=len(processing),
            queued_count=queued,
            presets=presets,
        ),
    )


# --- htmx パーシャル ---


@router.get("/partials/header-badge", response_class=HTMLResponse)
async def header_badge(request: Request) -> HTMLResponse:
    settings = _get_settings(request)
    jobs = _get_jobs(settings)
    _is_running, queued = get_queue_status()
    processing = _processing_jobs(jobs)
    return templates.TemplateResponse(
        request,
        "partials/header_badge.html",
        {"processing_count": len(processing), "queued_count": queued},
    )


@router.get("/partials/processing", response_class=HTMLResponse)
async def processing_partial(request: Request) -> HTMLResponse:
    settings = _get_settings(request)
    jobs = _get_jobs(settings)
    processing = _processing_jobs(jobs)
    return templates.TemplateResponse(
        request,
        "partials/processing.html",
        _template_ctx(processing_jobs=processing),
    )


@router.get("/partials/completed", response_class=HTMLResponse)
async def completed_partial(request: Request) -> HTMLResponse:
    settings = _get_settings(request)
    jobs = _get_jobs(settings)
    completed = _completed_jobs(jobs)
    return templates.TemplateResponse(
        request,
        "partials/completed.html",
        _template_ctx(completed_jobs=completed),
    )


# --- アクション API ---


@router.post("/api/add", response_class=HTMLResponse)
async def add_urls(request: Request) -> HTMLResponse:
    settings = _get_settings(request)
    form = await request.form()
    urls_text = str(form.get("urls", "")).strip()
    prompt = str(form.get("prompt", "default")).strip() or "default"
    audio_length = (
        str(form.get("audio_length", "default")).strip() or "default"
    )

    if not urls_text:
        return HTMLResponse(
            '<div class="error">URL を入力してください</div>',
            status_code=400,
        )

    urls = [u.strip() for u in urls_text.splitlines() if u.strip()]
    entries = [
        UrlEntry(url=u, audio_length=audio_length, prompt=prompt)
        for u in urls
    ]

    logger.info(
        "Adding {} URLs to queue (prompt={}, audio_length={})",
        len(entries),
        prompt,
        audio_length,
    )

    # state.json に即座に "queued" ジョブを書き込み → UI に即反映
    state_path = Path(settings.general.state_file)
    state = _load_state(state_path)
    for entry in entries:
        job = _find_or_create_job(
            state,
            entry.url,
            entry.audio_length or "default",
            entry.prompt or "default",
        )
        job["status"] = "queued"
    _save_state(state_path, state)

    await enqueue_urls(entries)

    jobs = _get_jobs(settings)
    processing = _processing_jobs(jobs)
    return templates.TemplateResponse(
        request,
        "partials/processing.html",
        _template_ctx(processing_jobs=processing),
        headers={"HX-Trigger": "refreshAll"},
    )


@router.post("/api/retry/{slug}", response_class=HTMLResponse)
async def retry_job(slug: str, request: Request) -> HTMLResponse:
    settings = _get_settings(request)
    state_path = Path(settings.general.state_file)
    state = _load_state(state_path)

    for job in state.get("jobs", []):
        if job["slug"] == slug and job["status"] == "failed":
            url = job["url"]
            audio_length = job.get("audio_length", "default")
            prompt = job.get("prompt", "default")

            state["jobs"] = [
                j for j in state["jobs"] if j["slug"] != slug
            ]
            _save_state(state_path, state)

            entry = UrlEntry(
                url=url, audio_length=audio_length, prompt=prompt
            )
            await enqueue_urls([entry])
            logger.info("Retrying job: {} (slug={})", url, slug)
            break

    jobs = _get_jobs(settings)
    completed = _completed_jobs(jobs)
    return templates.TemplateResponse(
        request,
        "partials/completed.html",
        _template_ctx(completed_jobs=completed),
        headers={"HX-Trigger": "refreshAll"},
    )


@router.post("/api/retry-all-failed", response_class=HTMLResponse)
async def retry_all_failed(request: Request) -> HTMLResponse:
    settings = _get_settings(request)
    state_path = Path(settings.general.state_file)
    state = _load_state(state_path)

    failed_jobs = [
        j for j in state.get("jobs", []) if j["status"] == "failed"
    ]
    entries = [
        UrlEntry(
            url=job["url"],
            audio_length=job.get("audio_length", "default"),
            prompt=job.get("prompt", "default"),
        )
        for job in failed_jobs
    ]

    state["jobs"] = [
        j for j in state["jobs"] if j["status"] != "failed"
    ]
    _save_state(state_path, state)

    if entries:
        await enqueue_urls(entries)
        logger.info("Retrying {} failed jobs", len(entries))

    jobs = _get_jobs(settings)
    completed = _completed_jobs(jobs)
    return templates.TemplateResponse(
        request,
        "partials/completed.html",
        _template_ctx(completed_jobs=completed),
        headers={"HX-Trigger": "refreshAll"},
    )


@router.delete("/api/jobs/{slug}", response_class=HTMLResponse)
async def delete_job(slug: str, request: Request) -> HTMLResponse:
    settings = _get_settings(request)
    state_path = Path(settings.general.state_file)
    state = _load_state(state_path)

    state["jobs"] = [
        j for j in state.get("jobs", []) if j["slug"] != slug
    ]
    _save_state(state_path, state)
    logger.info("Deleted job: slug={}", slug)

    return HTMLResponse("", headers={"HX-Trigger": "refreshAll"})


@router.post("/api/clear-completed", response_class=HTMLResponse)
async def clear_completed(request: Request) -> HTMLResponse:
    settings = _get_settings(request)
    state_path = Path(settings.general.state_file)
    state = _load_state(state_path)

    before = len(state.get("jobs", []))
    state["jobs"] = [
        j for j in state.get("jobs", []) if j["status"] != "uploaded"
    ]
    after = len(state["jobs"])
    _save_state(state_path, state)
    logger.info("Cleared {} completed jobs", before - after)

    jobs = _get_jobs(settings)
    completed = _completed_jobs(jobs)
    return templates.TemplateResponse(
        request,
        "partials/completed.html",
        _template_ctx(completed_jobs=completed),
    )
