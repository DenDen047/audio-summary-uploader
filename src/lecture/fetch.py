"""URL から本文テキストを抽出する。HTML / PDF / GitHub README に対応。"""

import re
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup
from loguru import logger

MAX_TEXT_CHARS = 40_000
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


@dataclass
class SourceContent:
    url: str
    title: str
    text: str
    kind: str  # "html" | "pdf" | "github"


def fetch_content(url: str) -> SourceContent:
    github = re.match(r"https?://github\.com/([\w.-]+)/([\w.-]+)/?$", url)
    if github:
        return _fetch_github_readme(url, github.group(1), github.group(2))

    resp = httpx.get(
        url, headers={"User-Agent": _UA}, follow_redirects=True, timeout=60
    )
    if resp.status_code != 200:
        raise RuntimeError(f"URL の取得に失敗: {url} (HTTP {resp.status_code})")

    content_type = resp.headers.get("content-type", "")
    if "application/pdf" in content_type or url.lower().endswith(".pdf"):
        return _extract_pdf(url, resp.content)
    return _extract_html(url, resp.text)


def _fetch_github_readme(url: str, owner: str, repo: str) -> SourceContent:
    raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md"
    resp = httpx.get(raw_url, follow_redirects=True, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"README の取得に失敗: {raw_url} (HTTP {resp.status_code})")
    logger.info("GitHub README を取得: {} ({} 文字)", raw_url, len(resp.text))
    return SourceContent(
        url=url, title=f"{owner}/{repo}", text=_truncate(resp.text), kind="github"
    )


def _extract_pdf(url: str, data: bytes) -> SourceContent:
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    title = doc.metadata.get("title") or url.rsplit("/", 1)[-1]
    doc.close()
    logger.info("PDF を抽出: {} ページ, {} 文字", doc.page_count, len(text))
    return SourceContent(url=url, title=title, text=_truncate(text), kind="pdf")


def _extract_html(url: str, html: str) -> SourceContent:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else url

    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    body = soup.find("article") or soup.find("main") or soup.body
    if body is None:
        raise RuntimeError(f"本文要素が見つからない: {url}")

    text = re.sub(r"\n{3,}", "\n\n", body.get_text("\n", strip=True))
    if len(text) < 200:
        raise RuntimeError(
            f"抽出テキストが短すぎる ({len(text)} 文字)。"
            f"JS レンダリングが必要な可能性: {url}"
        )
    logger.info("HTML を抽出: {} ({} 文字)", title, len(text))
    return SourceContent(url=url, title=title, text=_truncate(text), kind="html")


def _truncate(text: str) -> str:
    if len(text) > MAX_TEXT_CHARS:
        logger.warning(
            "本文を {} 文字に切り詰め (元 {} 文字)", MAX_TEXT_CHARS, len(text)
        )
        return text[:MAX_TEXT_CHARS]
    return text
