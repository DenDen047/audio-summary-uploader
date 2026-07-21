"""機能1: Spark メール出典の安全化（説明文）と collect での chat 抽出のテスト."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from automator.citation import EmailCitation
from automator.config import (
    CredentialsConfig,
    GeneralConfig,
    NotebookLMConfig,
    Settings,
    ThumbnailConfig,
    YouTubeConfig,
)
from automator.metadata import PageMetadata
from automator.pipeline import _build_description, _load_state, collect_audio

SPARK_URL = "https://app.sparkmailapp.com/web-share/AbC123xyz"


def _meta(url: str, title: str = "T") -> PageMetadata:
    return PageMetadata(
        url=url, title=title, description="", og_image_url=None,
        site_name=None, language=None, favicon_url=None,
    )


def _completed() -> MagicMock:
    gs = MagicMock()
    gs.status = "COMPLETED"
    return gs


class TestBuildDescription:
    def test_non_spark_keeps_url(self) -> None:
        m = _meta("https://arxiv.org/abs/2511.15059", "Paper")
        desc = _build_description(m, category="paper")
        assert "https://arxiv.org/abs/2511.15059" in desc
        # ⑤ カテゴリ別ハッシュタグが入る
        assert "#論文解説" in desc
        # 内部設定（プロンプト名）は公開面に出さない
        assert "プロンプト" not in desc

    def test_spark_with_citation_hides_url_shows_source(self) -> None:
        m = _meta(SPARK_URL, "Are Anthropic Bills Accurate?")
        c = EmailCitation(sender="Applied AI", date="2026-06-25", domain=None)
        desc = _build_description(m, citation=c, category="news")
        assert "sparkmailapp.com" not in desc
        assert "Applied AI" in desc
        assert "2026-06-25" in desc
        assert "#AIニュース" in desc

    def test_spark_without_citation_hides_url(self) -> None:
        m = _meta(SPARK_URL, "メール要約（タイトル取得中）")
        assert "sparkmailapp.com" not in _build_description(m)

    def test_local_pdf_hides_absolute_path_shows_filename(self) -> None:
        # ローカル PDF ソースは絶対パス（ユーザー名・ディレクトリ）を公開面に出さず、
        # ファイル名（＝資料名）だけを出典として表示する
        local = (
            "/Users/ikuta/Zotero/storage/A3EIDWYF/"
            "Wang - 2026 - Position Stop Hardcoding.pdf"
        )
        m = PageMetadata(
            url=local, title="日本語タイトル", description="Local file: x.pdf",
            og_image_url=None, site_name="Local PDF", language=None,
            favicon_url=None,
        )
        desc = _build_description(m, category="paper")
        assert "/Users/" not in desc
        assert "ikuta" not in desc
        assert "Local PDF" not in desc
        assert "Wang - 2026 - Position Stop Hardcoding" in desc

    def test_multi_source_lists_all_urls(self) -> None:
        m = _meta("https://arxiv.org/abs/1", "Multi")
        desc = _build_description(m, extra_urls=["https://arxiv.org/abs/2"])
        assert "https://arxiv.org/abs/1" in desc
        assert "https://arxiv.org/abs/2" in desc

    def test_multi_source_hides_spark_among_extras(self) -> None:
        m = _meta("https://arxiv.org/abs/1", "Multi")
        desc = _build_description(m, extra_urls=[SPARK_URL])
        assert "https://arxiv.org/abs/1" in desc
        assert "sparkmailapp.com" not in desc
        assert "メールニュースレター" in desc

    def test_primary_spark_citation_still_lists_extra_sources(self) -> None:
        # 先頭がメール(citation)でも、追加の非メールソースは出典に列挙される
        m = _meta(SPARK_URL, "Multi")
        c = EmailCitation(sender="Applied AI", date="2026-06-25", domain=None)
        desc = _build_description(
            m, citation=c, extra_urls=["https://arxiv.org/abs/9"]
        )
        assert "Applied AI" in desc                  # メール出典
        assert "https://arxiv.org/abs/9" in desc     # 追加ソースも列挙
        assert "sparkmailapp.com" not in desc        # 生 Spark URL は秘匿

    def test_email_anywhere_is_sanitized(self) -> None:
        # site_name 等にメールアドレスが紛れても最後の砦で除去される
        m = PageMetadata(
            url="https://example.com/x", title="T", description="",
            og_image_url=None, site_name="From me@personal.example",
            language=None, favicon_url=None,
        )
        desc = _build_description(m)
        assert "me@personal.example" not in desc
        assert "[メールアドレス非公開]" in desc


def _settings(tmp_path: Path) -> Settings:
    state_path = tmp_path / "data" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    return Settings(
        notebooklm=NotebookLMConfig(prompt_presets={"default": "p"}),
        youtube=YouTubeConfig(),
        thumbnail=ThumbnailConfig(),
        credentials=CredentialsConfig(),
        general=GeneralConfig(
            tmp_dir=str(tmp_path / "tmp"), state_file=str(state_path)
        ),
    )


def _spark_job() -> dict:
    return {
        "url": SPARK_URL, "slug": "spark1", "audio_length": "short",
        "prompt": "default", "status": "generating", "notebook_id": "nb-1",
        "task_id": "t-1",
        "metadata": {
            "title": "メール要約（タイトル取得中）", "description": "",
            "og_image_url": None, "site_name": "メールニュースレター",
            "language": None,
        },
        "audio_path": None, "thumbnail_path": None, "video_path": None,
        "youtube_url": None, "error": None,
        "submitted_at": "2026-01-01T00:00:00+00:00", "collected_at": None,
        "uploaded_at": None,
    }


@pytest.mark.asyncio()
async def test_collect_extracts_spark_citation(tmp_path: Path) -> None:
    """Spark の collect で chat 抽出(①)＋日本語タイトル生成(②)が行われる.

    ask は2回呼ばれ、出典は①、公開タイトルは②の日本語タイトルで上書きされる。
    """
    settings = _settings(tmp_path)
    state_path = Path(settings.general.state_file)
    state_path.write_text(
        json.dumps({"last_run": None, "jobs": [_spark_job()]}), encoding="utf-8"
    )

    backend = AsyncMock()
    backend.check_audio_status = AsyncMock(return_value=_completed())
    backend.ask = AsyncMock(side_effect=[
        (
            '```json\n{"title": "Are Anthropic Bills Accurate?", '
            '"sender": "Applied AI", "domain": null, "date": "2026-06-25"}\n```'
        ),
        "Anthropicの請求は本当に正確なのか検証",
    ])
    audio = tmp_path / "tmp" / "audio" / "spark1.mp3"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"x")
    backend.download_audio = AsyncMock(return_value=audio)
    backend.delete_notebook = AsyncMock()

    with (
        patch("automator.pipeline._create_backend", return_value=backend),
        patch(
            "automator.pipeline._generate_backgrounds",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "automator.pipeline._generate_thumb_base",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "automator.pipeline.generate_thumbnail",
            return_value=tmp_path / "t.png",
        ),
        patch(
            "automator.pipeline.convert_to_video",
            return_value=tmp_path / "v.mp4",
        ),
    ):
        results = await collect_audio(settings, poll=False)

    assert results[0].status == "video_ready"
    # 従来の2回に加え、サムネ3層コピー生成の chat 呼び出しが1回増える
    assert backend.ask.await_count == 3

    job = _load_state(state_path)["jobs"][0]
    # ② が件名を上書きして日本語タイトルになる
    assert job["metadata"]["title"] == "Anthropicの請求は本当に正確なのか検証"
    # 出典(送信元/日付)は ① で取得済み
    assert job["citation"]["sender"] == "Applied AI"
    assert job["citation"]["date"] == "2026-06-25"
    # 生 URL は state の url にのみ残り、公開フィールドには出さない
    assert job["url"] == SPARK_URL


def _multi_job_with_user_title() -> dict:
    return {
        "url": "https://arxiv.org/abs/1", "slug": "multi1",
        "audio_length": "short", "prompt": "default", "status": "generating",
        "notebook_id": "nb-2", "task_id": "t-2",
        "metadata": {
            "title": "仮タイトル", "description": "", "og_image_url": None,
            "site_name": None, "language": None,
        },
        "extra_urls": ["https://arxiv.org/abs/2"],
        "user_title": "今週のAIニュースまとめ", "citation": None,
        "audio_path": None, "thumbnail_path": None, "video_path": None,
        "youtube_url": None, "error": None,
        "submitted_at": "2026-01-01T00:00:00+00:00", "collected_at": None,
        "uploaded_at": None,
    }


def _paper_job() -> dict:
    return {
        "url": "https://arxiv.org/abs/2304.02643", "slug": "paper1",
        "audio_length": "short", "prompt": "default", "status": "generating",
        "notebook_id": "nb-3", "task_id": "t-3",
        "metadata": {
            "title": "Segment Anything", "description": "", "og_image_url": None,
            "site_name": None, "language": None,
        },
        "extra_urls": [], "user_title": None, "citation": None,
        "audio_path": None, "thumbnail_path": None, "video_path": None,
        "youtube_url": None, "error": None,
        "submitted_at": "2026-01-01T00:00:00+00:00", "collected_at": None,
        "uploaded_at": None,
    }


@pytest.mark.asyncio()
async def test_collect_prepends_paper_shortname(tmp_path: Path) -> None:
    """論文カテゴリでは通称(SAM 等)を抽出しタイトル先頭に【略称】を付与する.

    ask は ② 日本語タイトル → 論文略称 → サムネ3層コピー の3回（arxiv は
    確定カテゴリなので _refine_category は chat を呼ばない）。
    """
    settings = _settings(tmp_path)
    state_path = Path(settings.general.state_file)
    state_path.write_text(
        json.dumps({"last_run": None, "jobs": [_paper_job()]}), encoding="utf-8"
    )

    backend = AsyncMock()
    backend.check_audio_status = AsyncMock(return_value=_completed())
    backend.ask = AsyncMock(side_effect=[
        "あらゆる物体を一発で切り抜く基盤モデル",   # ② JP タイトル
        "SAM",                                       # 論文略称
        '{"top":"基盤モデル","bottom":"衝撃の実力"}',  # サムネ3層コピー
    ])
    audio = tmp_path / "tmp" / "audio" / "paper1.mp3"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"x")
    backend.download_audio = AsyncMock(return_value=audio)
    backend.delete_notebook = AsyncMock()

    with (
        patch("automator.pipeline._create_backend", return_value=backend),
        patch(
            "automator.pipeline._generate_backgrounds",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "automator.pipeline._generate_thumb_base",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "automator.pipeline.generate_thumbnail", return_value=tmp_path / "t.png"
        ),
        patch(
            "automator.pipeline.convert_to_video", return_value=tmp_path / "v.mp4"
        ),
    ):
        results = await collect_audio(settings, poll=False)

    assert results[0].status == "video_ready"
    assert backend.ask.await_count == 3
    job = _load_state(state_path)["jobs"][0]
    assert job["metadata"]["title"] == "【SAM】あらゆる物体を一発で切り抜く基盤モデル"


@pytest.mark.asyncio()
async def test_collect_honors_user_title(tmp_path: Path) -> None:
    """user_title があれば ② を呼ばずにそのタイトルを使う（非Sparkなので ① も無し）."""
    settings = _settings(tmp_path)
    state_path = Path(settings.general.state_file)
    state_path.write_text(
        json.dumps({"last_run": None, "jobs": [_multi_job_with_user_title()]}),
        encoding="utf-8",
    )

    backend = AsyncMock()
    backend.check_audio_status = AsyncMock(return_value=_completed())
    audio = tmp_path / "tmp" / "audio" / "multi1.mp3"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"x")
    backend.download_audio = AsyncMock(return_value=audio)
    backend.delete_notebook = AsyncMock()

    with (
        patch("automator.pipeline._create_backend", return_value=backend),
        patch(
            "automator.pipeline._generate_backgrounds",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "automator.pipeline._generate_thumb_base",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "automator.pipeline.generate_thumbnail", return_value=tmp_path / "t.png"
        ),
        patch(
            "automator.pipeline.convert_to_video", return_value=tmp_path / "v.mp4"
        ),
    ):
        results = await collect_audio(settings, poll=False)

    assert results[0].status == "video_ready"
    # user 指定タイトルは尊重され chat 生成しない。ask はサムネ3層コピー生成の1回のみ
    assert backend.ask.await_count == 1
    job = _load_state(state_path)["jobs"][0]
    assert job["metadata"]["title"] == "今週のAIニュースまとめ"
