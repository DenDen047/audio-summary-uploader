"""澪・透の講義動画を既存パイプラインへ接続するテスト。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from automator.config import (
    CredentialsConfig,
    GeneralConfig,
    NotebookLMConfig,
    Settings,
    ThumbnailConfig,
    YouTubeConfig,
)
from automator.pipeline import (
    _find_or_create_job,
    _update_job_state,
    collect_audio,
    upload_videos,
)
from automator.youtube import UploadResult
from lecture.characters import CharacterAssets
from lecture.fetch import SourceContent
from lecture.pipeline import (
    LectureArtifacts,
    RenderedLecture,
    generate_lecture,
    generate_lecture_thumbnail,
)
from lecture.thumbnail_backdrop import ThumbnailBackdropResult


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        notebooklm=NotebookLMConfig(prompt_presets={"default": "Summarize"}),
        youtube=YouTubeConfig(default_tags=["fallback"]),
        thumbnail=ThumbnailConfig(),
        credentials=CredentialsConfig(),
        general=GeneralConfig(
            tmp_dir=str(tmp_path / "tmp"),
            state_file=str(tmp_path / "state.json"),
        ),
    )


def _artifacts(job_dir: Path) -> LectureArtifacts:
    job_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "source_path": job_dir / "source.txt",
        "script_path": job_dir / "script.json",
        "video_path": job_dir / "video.mp4",
        "thumbnail_path": job_dir / "thumbnail.png",
        "upload_metadata_path": job_dir / "upload_metadata.json",
    }
    for path in paths.values():
        path.write_bytes(b"artifact")
    backdrop_path = job_dir / "thumbnail-background.png"
    prompt_path = job_dir / "thumbnail-background-prompt.txt"
    backdrop_path.write_bytes(b"background")
    prompt_path.write_text("prompt", encoding="utf-8")
    backdrop = ThumbnailBackdropResult(
        path=backdrop_path,
        prompt_path=prompt_path,
        provider="codex-directed-local-svg",
        model=None,
        prompt="prompt",
        fallback_reason=None,
    )
    return LectureArtifacts(
        source_url="https://example.com/source",
        job_dir=job_dir,
        title="澪先生と学ぶテスト",
        description="概要\n\n出典: https://example.com/source",
        tags=("Python", "解説"),
        thumbnail_text=("Pythonの悩み", "これで解決"),
        thumbnail_backdrop=backdrop,
        script_generation={
            "script_agent": "codex-cli",
            "script_model_requested": "codex-config-default",
            "script_models_used": [],
            "authentication": "chatgpt-subscription",
            "metered_api": False,
        },
        **paths,
    )


def test_same_url_can_keep_notebooklm_and_lecture_jobs(tmp_path: Path) -> None:
    state = {"last_run": None, "jobs": []}
    notebook = _find_or_create_job(
        state,
        "https://example.com/source",
        "default",
        "default",
        "notebooklm",
    )
    lecture = _find_or_create_job(
        state,
        "https://example.com/source",
        "default",
        "default",
        "lecture",
    )

    assert notebook is not lecture
    assert notebook["slug"] != lecture["slug"]

    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    _update_job_state(
        state_path,
        lecture["url"],
        {"status": "video_ready"},
        mode="lecture",
    )
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    statuses = {job["mode"]: job["status"] for job in saved["jobs"]}
    assert statuses == {"notebooklm": "generating", "lecture": "video_ready"}


def test_generate_lecture_writes_upload_ready_artifacts(tmp_path: Path) -> None:
    source = SourceContent(
        url="https://example.com/source",
        title="Source title",
        text="本文" * 200,
        kind="html",
    )
    script = {
        "title": "澪先生と学ぶテスト",
        "description": "概要\n\n出典: https://example.com/source",
        "tags": ["Python", "解説"],
        "thumbnail_text": ["Pythonの悩み", "これで解決"],
        "thumbnail_visual_prompt": "Pythonの依存関係を光る経路で表現する",
        "generation": {
            "script_agent": "codex-cli",
            "script_model_requested": "codex-config-default",
            "script_models_used": [],
            "authentication": "chatgpt-subscription",
            "metered_api": False,
        },
        "eyecatch_before_scenes": [2],
        "scenes": [{"slide": {}, "lines": []}],
    }

    def fake_render(_script: dict, job_dir: Path) -> RenderedLecture:
        video = job_dir / "video.mp4"
        first_slide = job_dir / "slides" / "scene_01_s1.png"
        first_slide.parent.mkdir(parents=True)
        video.write_bytes(b"video")
        first_slide.write_bytes(b"slide")
        return RenderedLecture(
            video_path=video,
            first_slide_path=first_slide,
            characters={},
        )

    def fake_thumbnail(
        _rendered: RenderedLecture,
        output_path: Path,
        _size: tuple[int, int],
        _script: dict,
        *,
        backdrop_path: Path | None = None,
    ) -> Path:
        assert backdrop_path == backdrop.path
        output_path.write_bytes(b"thumbnail")
        return output_path

    backdrop_path = tmp_path / "thumbnail-background.png"
    prompt_path = tmp_path / "thumbnail-background-prompt.txt"
    backdrop = ThumbnailBackdropResult(
        path=backdrop_path,
        prompt_path=prompt_path,
        provider="static",
        model=None,
        prompt="prompt",
        fallback_reason="test",
    )

    with (
        patch("lecture.pipeline.fetch_content", return_value=source),
        patch("lecture.pipeline.generate_script", return_value=script),
        patch("lecture.pipeline.generate_thumbnail_backdrop", return_value=backdrop),
        patch("lecture.pipeline.render_lecture", side_effect=fake_render),
        patch(
            "lecture.pipeline.generate_lecture_thumbnail",
            side_effect=fake_thumbnail,
        ),
    ):
        artifacts = generate_lecture(
            source.url,
            tmp_path / "lecture",
            thumbnail_size=(1280, 720),
        )

    assert artifacts.video_path.read_bytes() == b"video"
    assert artifacts.thumbnail_path.read_bytes() == b"thumbnail"
    assert artifacts.source_path.read_text(encoding="utf-8") == source.text
    assert json.loads(artifacts.script_path.read_text(encoding="utf-8")) == script
    upload = json.loads(artifacts.upload_metadata_path.read_text(encoding="utf-8"))
    assert upload == {
        "source_url": source.url,
        "title": script["title"],
        "description": script["description"],
        "tags": script["tags"],
        "thumbnail_text": script["thumbnail_text"],
        "thumbnail_background": backdrop.as_metadata(),
        "script_generation": script["generation"],
        "video_path": str(artifacts.video_path),
        "thumbnail_path": str(artifacts.thumbnail_path),
    }


def test_generate_lecture_thumbnail_uses_clickable_copy(tmp_path: Path) -> None:
    backdrop = tmp_path / "backdrop.png"
    Image.new("RGB", (1280, 720), "#FFD7DF").save(backdrop)
    characters: dict[str, CharacterAssets] = {}
    for speaker, color in (("metan", "#A53C72"), ("zunda", "#F5B942")):
        path = tmp_path / f"{speaker}.png"
        image = Image.new("RGBA", (280, 700), (0, 0, 0, 0))
        image.paste(color, (30, 20, 250, 700))
        image.save(path)
        characters[speaker] = CharacterAssets(
            image=path,
            width=image.width,
            height=image.height,
            bleed=0,
        )
    rendered = RenderedLecture(
        video_path=tmp_path / "video.mp4",
        first_slide_path=tmp_path / "unused.png",
        characters=characters,
    )
    script = {
        "title": "Python開発ツールuv",
        "thumbnail_text": ["pipより速い？", "uvなら全部できる"],
        "thumbnail_visual_prompt": "依存関係が一本の光る経路へ整理される",
    }
    output = tmp_path / "thumbnail.png"

    with patch("lecture.pipeline.THUMBNAIL_BACKDROP", backdrop):
        generate_lecture_thumbnail(rendered, output, (1280, 720), script)

    with Image.open(output) as thumbnail:
        assert thumbnail.size == (1280, 720)
        assert thumbnail.mode == "RGB"


@pytest.mark.asyncio()
async def test_collect_generates_lecture_without_notebooklm(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    state_path = Path(settings.general.state_file)
    state_path.write_text(
        json.dumps(
            {
                "last_run": None,
                "jobs": [
                    {
                        "url": "https://example.com/source",
                        "slug": "lecture123",
                        "mode": "lecture",
                        "audio_length": "default",
                        "prompt": "default",
                        "status": "generating",
                        "notebook_id": None,
                        "task_id": None,
                        "metadata": None,
                        "audio_path": None,
                        "thumbnail_path": None,
                        "video_path": None,
                        "youtube_url": None,
                        "error": None,
                        "submitted_at": None,
                        "collected_at": None,
                        "uploaded_at": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    artifacts = _artifacts(tmp_path / "lecture-job")

    with (
        patch("automator.pipeline.generate_lecture", return_value=artifacts),
        patch(
            "automator.pipeline._create_backend",
            side_effect=AssertionError("NotebookLM must not be created"),
        ),
    ):
        results = await collect_audio(settings, poll=True)

    assert [result.status for result in results] == ["video_ready"]
    job = json.loads(state_path.read_text(encoding="utf-8"))["jobs"][0]
    assert job["status"] == "video_ready"
    assert job["metadata"]["title"] == artifacts.title
    assert job["upload_metadata"]["description"] == artifacts.description
    assert job["upload_metadata"]["tags"] == list(artifacts.tags)
    assert job["upload_metadata"]["thumbnail_text"] == list(artifacts.thumbnail_text)
    assert job["upload_metadata"]["thumbnail_background"] == (
        artifacts.thumbnail_backdrop.as_metadata()
    )
    assert job["upload_metadata"]["script_generation"] == (artifacts.script_generation)
    assert job["video_path"] == str(artifacts.video_path)
    assert job["thumbnail_path"] == str(artifacts.thumbnail_path)
    assert job["upload_metadata_path"] == str(artifacts.upload_metadata_path)


@pytest.mark.asyncio()
async def test_upload_uses_lecture_title_description_and_tags(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    state_path = Path(settings.general.state_file)
    artifacts = _artifacts(tmp_path / "lecture-job")
    state_path.write_text(
        json.dumps(
            {
                "last_run": None,
                "jobs": [
                    {
                        "url": artifacts.source_url,
                        "slug": "lecture123",
                        "mode": "lecture",
                        "audio_length": "default",
                        "prompt": "default",
                        "privacy_status": "public",
                        "status": "video_ready",
                        "metadata": {"title": artifacts.title},
                        "upload_metadata": {
                            "title": artifacts.title,
                            "description": artifacts.description,
                            "tags": list(artifacts.tags),
                        },
                        "video_path": str(artifacts.video_path),
                        "thumbnail_path": str(artifacts.thumbnail_path),
                        "youtube_url": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    mock_upload = AsyncMock(
        return_value=UploadResult(
            youtube_url="https://youtu.be/lecture",
            thumbnail_set=True,
        )
    )
    with (
        patch("automator.pipeline.authenticate", return_value=object()),
        patch("automator.pipeline.upload_video", mock_upload),
    ):
        results = await upload_videos(settings)

    assert [result.status for result in results] == ["uploaded"]
    params = mock_upload.call_args.args[1]
    assert params.title == artifacts.title
    assert params.description == artifacts.description
    assert params.tags == list(artifacts.tags)
    assert params.file_path == artifacts.video_path
    assert params.thumbnail_path == artifacts.thumbnail_path
    assert params.privacy_status == "public"
