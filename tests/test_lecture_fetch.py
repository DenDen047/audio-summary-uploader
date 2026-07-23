"""講義動画で使う情報源抽出のテスト。"""

import json
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import httpx
import pymupdf

from lecture.fetch import (
    SourceContent,
    SourceFigure,
    fetch_content,
    materialize_source_figures,
)

SPARK_URL = "https://app.sparkmailapp.com/web-share/anonymous-test"


def _spark_share_html() -> str:
    email_html = """
    <html><body>
      <div style="display: none">
        Hidden preview reader@example.com
        <span style="color: gray">Nested hidden preview</span>
      </div>
      <h1>匿名AIニュース</h1>
      <p>新しいAIチップについて、性能、消費電力、供給計画の観点から詳しく解説します。</p>
      <p>開発チームは推論処理の高速化と運用コストの削減を同時に目指しています。</p>
      <p>複数のクラウド事業者が評価を開始し、来年から段階的に提供する予定です。</p>
      <p>この記事では既存製品との違い、導入時の注意点、今後の展望を整理します。</p>
      <p>利用企業は小規模な検証環境から始め、処理時間と費用を計測して判断できます。</p>
      <p>開発者向けの資料と移行支援も順次公開されるため、実用面にも注目が集まります。</p>
      <footer>Sent to reader@example.com</footer>
    </body></html>
    """
    values = [
        {"_1": 2},
        "loaderData",
        {"_3": 4},
        "routes/thread/web-thread",
        {"_5": 6},
        "threadRaw",
        {"_7": 8, "_9": 10},
        "subject",
        "匿名AIニュース",
        "messages",
        [11],
        {"_12": 13},
        "webMessage",
        {"_14": 15},
        "parts",
        [16],
        {"_17": 18, "_19": 20},
        "partType",
        "html",
        "content",
        email_html,
    ]
    payload = json.dumps(values, ensure_ascii=False)
    argument = json.dumps(payload, ensure_ascii=False)
    return (
        "<html><head><title>Spark</title></head><body>"
        "<script>window.__reactRouterContext.streamController.enqueue("
        f"{argument});</script></body></html>"
    )


def test_fetch_content_reads_spark_share_without_rendering_browser() -> None:
    response = httpx.Response(
        200,
        text=_spark_share_html(),
        headers={"content-type": "text/html; charset=utf-8"},
    )

    with patch("lecture.fetch.httpx.get", return_value=response):
        source = fetch_content(SPARK_URL)

    assert source.url == SPARK_URL
    assert source.title == "匿名AIニュース"
    assert source.kind == "html"
    assert "新しいAIチップ" in source.text
    assert "Hidden preview" not in source.text
    assert "reader@example.com" not in source.text


def test_fetch_content_requests_spark_server_rendered_payload() -> None:
    response = httpx.Response(
        200,
        text=_spark_share_html(),
        headers={"content-type": "text/html; charset=utf-8"},
    )

    def get_spark_share(
        url: str,
        *,
        headers: dict[str, str],
        follow_redirects: bool,
        timeout: int,
    ) -> httpx.Response:
        assert url == SPARK_URL
        assert headers["User-Agent"] == "Mozilla/5.0"
        assert follow_redirects is True
        assert timeout == 60
        return response

    with patch("lecture.fetch.httpx.get", side_effect=get_spark_share):
        source = fetch_content(SPARK_URL)

    assert source.title == "匿名AIニュース"
    assert "新しいAIチップ" in source.text


def test_fetch_content_reads_local_pdf(tmp_path: Path) -> None:
    pdf_path = tmp_path / "source.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Local lecture source")
    document.set_metadata({"title": "Local PDF"})
    document.save(pdf_path)
    document.close()

    source = fetch_content(str(pdf_path))

    assert source.url == str(pdf_path)
    assert source.title == "Local PDF"
    assert source.kind == "pdf"
    assert "Local lecture source" in source.text


def test_fetch_content_extracts_captioned_html_figures() -> None:
    html = """
    <html><head><title>Paper title</title></head><body><article>
      <p>本文です。図の前提を十分な長さで説明します。</p>
      <figure id="figure-1">
        <img src="images/overview.png" alt="overview">
        <figcaption>Figure 1: Geometry と Language の関係</figcaption>
      </figure>
      <figure id="external-tracker">
        <img src="https://tracker.example/pixel.png" alt="tracking figure">
        <figcaption>外部ホストの図</figcaption>
      </figure>
      <p>""" + "追加の説明です。" * 30 + """</p>
    </article></body></html>
    """
    response = httpx.Response(
        200,
        text=html,
        headers={"content-type": "text/html; charset=utf-8"},
    )

    with patch("lecture.fetch.httpx.get", return_value=response):
        source = fetch_content("https://papers.example/article/index.html")

    assert source.figures == (
        SourceFigure(
            url="https://papers.example/article/images/overview.png",
            caption="Figure 1: Geometry と Language の関係",
        ),
    )


def test_fetch_content_reads_youtube_automatic_captions() -> None:
    video_url = "https://www.youtube.com/watch?v=example"
    metadata = {
        "title": "Transcript Example",
        "language": "en",
        "automatic_captions": {
            "en": [
                {
                    "ext": "json3",
                    "url": "https://captions.example/transcript",
                }
            ]
        },
    }
    caption_text = (
        "This transcript explains a concrete technical example, the evidence "
        "behind it, and the limits of the result for a general audience. "
    ) * 3
    captions = {
        "events": [{"segs": [{"utf8": caption_text}]}],
    }
    metadata_result = CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps(metadata),
        stderr="",
    )
    response = httpx.Response(200, json=captions)

    with (
        patch("lecture.fetch.shutil.which", return_value="/opt/yt-dlp"),
        patch(
            "lecture.fetch.subprocess.run", return_value=metadata_result
        ) as run,
        patch("lecture.fetch.httpx.get", return_value=response) as get,
    ):
        source = fetch_content(video_url)

    assert source.url == video_url
    assert source.title == "Transcript Example"
    assert source.kind == "youtube"
    assert "concrete technical example" in source.text
    assert run.call_args.args[0][-1] == video_url
    assert get.call_args.args[0] == "https://captions.example/transcript"


def test_materialize_source_figures_downloads_only_selected_indices(
    tmp_path: Path,
) -> None:
    source = SourceContent(
        url="https://papers.example/article",
        title="Paper",
        text="本文",
        kind="html",
        figures=(
            SourceFigure(
                url="https://papers.example/figure-1.png",
                caption="Figure 1",
            ),
            SourceFigure(
                url="https://papers.example/figure-2.png",
                caption="Figure 2",
            ),
        ),
    )
    response = httpx.Response(
        200,
        content=b"selected-image",
        headers={"content-type": "image/png"},
    )

    with patch("lecture.fetch.httpx.get", return_value=response) as get:
        paths = materialize_source_figures(source, tmp_path, {2})

    assert paths == (tmp_path / "figure_02.png",)
    assert paths[0].read_bytes() == b"selected-image"
    assert get.call_args.args[0] == "https://papers.example/figure-2.png"
