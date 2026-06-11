"""ルーティング + API ハンドラ."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from loguru import logger

from automator.config import Settings
from automator.pipeline import _find_or_create_job, _load_state, _save_state
from automator.url_parser import UrlEntry
from automator.web.app import enqueue_urls, templates

router = APIRouter()

# 追加・再投入を受け付けないステータス (failed のみ再投入可能)
_ACTIVE_STATUSES = ("queued", "generating", "video_ready", "uploading", "uploaded")


@router.get("/health")
async def health():
    return {"status": "ok"}


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


def _badge_counts(jobs: list[dict]) -> tuple[int, int]:
    """ヘッダーバッジ用の (processing, queued) 件数を state.json から数える.

    インメモリのキューカウンタではなく state を単一の情報源にすることで、
    リスト表示とバッジの食い違いを防ぐ。
    """
    processing = sum(
        1
        for j in jobs
        if j.get("status") in ("generating", "video_ready", "uploading")
    )
    queued = sum(1 for j in jobs if j.get("status") == "queued")
    return processing, queued


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
    processing = _processing_jobs(jobs)
    completed = _completed_jobs(jobs)
    processing_count, queued_count = _badge_counts(jobs)
    presets = list(settings.notebooklm.prompt_presets.keys())

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        _template_ctx(
            processing_jobs=processing,
            completed_jobs=completed,
            processing_count=processing_count,
            queued_count=queued_count,
            presets=presets,
        ),
    )


# --- htmx パーシャル ---


@router.get("/partials/header-badge", response_class=HTMLResponse)
async def header_badge(request: Request) -> HTMLResponse:
    settings = _get_settings(request)
    jobs = _get_jobs(settings)
    processing_count, queued_count = _badge_counts(jobs)
    return templates.TemplateResponse(
        request,
        "partials/header_badge.html",
        {"processing_count": processing_count, "queued_count": queued_count},
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
    # 重複行を除去 (同一バッチ内で同じ URL を二重 submit しないため)
    urls = list(dict.fromkeys(urls))

    # state.json に即座に "queued" ジョブを書き込み → UI に即反映。
    # 処理中・アップロード済みの URL はスキップし、重複実行ガードを保つ。
    state_path = Path(settings.general.state_file)
    state = _load_state(state_path)
    to_queue: list[UrlEntry] = []
    for url in urls:
        existing = next(
            (j for j in state["jobs"] if j["url"] == url), None
        )
        if existing and existing["status"] in _ACTIVE_STATUSES:
            logger.info(
                "Skipping already active URL: {} (status={})",
                url,
                existing["status"],
            )
            continue
        job = _find_or_create_job(state, url, audio_length, prompt)
        job["status"] = "queued"
        job["error"] = None
        to_queue.append(
            UrlEntry(url=url, audio_length=audio_length, prompt=prompt)
        )
    _save_state(state_path, state)

    logger.info(
        "Adding {} URLs to queue (prompt={}, audio_length={})",
        len(to_queue),
        prompt,
        audio_length,
    )

    if to_queue:
        await enqueue_urls(to_queue)

    jobs = _get_jobs(settings)
    processing = _processing_jobs(jobs)
    return templates.TemplateResponse(
        request,
        "partials/processing.html",
        _template_ctx(processing_jobs=processing),
        headers={"HX-Trigger": "refreshAll"},
    )


def _reset_failed_job(job: dict) -> UrlEntry | None:
    """failed ジョブを再実行用にリセットする (state への保存は呼び出し元が行う).

    ジョブを削除せず queued に戻して state.json に残すことで、ワーカーが
    処理を始めるまでの間も UI に表示され、サーバー再起動でもリトライが
    失われない。動画変換まで完了済みなら video_ready に戻し、音声の
    再生成をスキップしてアップロードのみ再試行する (動画の重複防止)。
    """
    job["error"] = None
    video_path = job.get("video_path")
    thumbnail_path = job.get("thumbnail_path")
    # youtube_url が残っているジョブ (CLI --force 再実行の失敗等) は
    # アップロード済みのため、video_ready 再開すると同じ動画が重複する
    if (
        job.get("youtube_url") is None
        and video_path
        and Path(video_path).exists()
        and thumbnail_path
        and Path(thumbnail_path).exists()
    ):
        job["status"] = "video_ready"
        return None
    job["status"] = "queued"
    return UrlEntry(
        url=job["url"],
        audio_length=job.get("audio_length", "default"),
        prompt=job.get("prompt", "default"),
    )


@router.post("/api/retry/{slug}", response_class=HTMLResponse)
async def retry_job(slug: str, request: Request) -> HTMLResponse:
    settings = _get_settings(request)
    state_path = Path(settings.general.state_file)
    state = _load_state(state_path)

    for job in state.get("jobs", []):
        if job["slug"] == slug and job["status"] == "failed":
            entry = _reset_failed_job(job)
            _save_state(state_path, state)
            # entry が None なら video_ready 再開 → 空バッチで upload スイープを起動
            await enqueue_urls([entry] if entry else [])
            logger.info(
                "Retrying job: {} (slug={}, resume={})",
                job["url"],
                slug,
                "upload" if entry is None else "submit",
            )
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
    entries: list[UrlEntry] = []
    for job in failed_jobs:
        entry = _reset_failed_job(job)
        if entry:
            entries.append(entry)

    if failed_jobs:
        _save_state(state_path, state)
        # entries が空でも video_ready 再開分の upload スイープとして投入する
        await enqueue_urls(entries)
        logger.info("Retrying {} failed jobs", len(failed_jobs))

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

    # GUI_SPEC: uploaded のみ削除する。failed はエラー内容とリトライ機会を
    # 残すため対象外 (個別の削除ボタンで消せる)。
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
