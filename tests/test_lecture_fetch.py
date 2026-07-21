"""講義動画で使う情報源抽出のテスト。"""

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pymupdf

from lecture.fetch import fetch_content

SPARK_URL = "https://app.sparkmailapp.com/web-share/anonymous-test"


def _spark_share_html() -> str:
    email_html = """
    <html><body>
      <div style="display: none">Hidden preview reader@example.com</div>
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
