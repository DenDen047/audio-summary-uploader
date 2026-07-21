"""URL から本文テキストを抽出する。HTML / PDF / GitHub README に対応。"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from automator.citation import is_spark_share_url, sanitize_public_text

MAX_TEXT_CHARS = 40_000
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_SPARK_STREAM_MARKER = (
    "window.__reactRouterContext.streamController.enqueue("
)
_SPARK_ROUTE = "routes/thread/web-thread"


@dataclass
class SourceContent:
    url: str
    title: str
    text: str
    kind: str  # "html" | "pdf" | "github"


def fetch_content(url: str) -> SourceContent:
    if not url.startswith(("http://", "https://")):
        path = Path(url).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() != ".pdf":
            raise RuntimeError(f"講義動画で扱えないローカル情報源: {path}")
        return _extract_pdf(str(path), path.read_bytes())

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
    if is_spark_share_url(url):
        return _extract_spark_share(url, resp.text)
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
    page_count = doc.page_count
    doc.close()
    logger.info("PDF を抽出: {} ページ, {} 文字", page_count, len(text))
    return SourceContent(url=url, title=title, text=_truncate(text), kind="pdf")


def _extract_spark_share(url: str, html: str) -> SourceContent:
    root = _decode_spark_stream(html)
    loader_data = root.get("loaderData")
    if not isinstance(loader_data, dict):
        raise RuntimeError(f"Spark 共有ページの loaderData が不正: {url}")
    route_data = loader_data.get(_SPARK_ROUTE)
    if not isinstance(route_data, dict):
        raise RuntimeError(f"Spark 共有ページのルートデータが見つからない: {url}")
    thread = route_data.get("threadRaw")
    if not isinstance(thread, dict):
        raise RuntimeError(f"Spark 共有ページのメールデータが見つからない: {url}")

    subject = thread.get("subject")
    title = (
        sanitize_public_text(subject.strip())
        if isinstance(subject, str) and subject.strip()
        else "メールニュースレター"
    )
    messages = thread.get("messages")
    if not isinstance(messages, list):
        raise RuntimeError(f"Spark 共有ページのメッセージ一覧が不正: {url}")

    text_parts: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        web_message = message.get("webMessage")
        if not isinstance(web_message, dict):
            continue
        parts = web_message.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            content = part.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            part_type = part.get("partType")
            text_parts.append(
                _extract_email_html(content)
                if part_type == "html"
                else content.strip()
            )

    text = _normalize_text("\n\n".join(text_parts))
    if len(text) < 200:
        raise RuntimeError(
            f"Spark 共有ページの本文が短すぎる ({len(text)} 文字): {url}"
        )
    logger.info("Spark メールを抽出: {} ({} 文字)", title, len(text))
    return SourceContent(
        url=url,
        title=title,
        text=_truncate(text),
        kind="html",
    )


def _decode_spark_stream(html: str) -> dict:
    """Spark が SSR 応答へ埋め込む React Router の初期データを復元する。"""
    cursor = 0
    decoder = json.JSONDecoder()
    while True:
        marker_at = html.find(_SPARK_STREAM_MARKER, cursor)
        if marker_at < 0:
            break
        argument_at = marker_at + len(_SPARK_STREAM_MARKER)
        payload, consumed = decoder.raw_decode(html[argument_at:])
        cursor = argument_at + consumed
        if not isinstance(payload, str) or "threadRaw" not in payload:
            continue
        values = json.loads(payload)
        if not isinstance(values, list):
            raise RuntimeError("Spark 共有ページの初期データ形式が不正")
        root = _resolve_spark_value(values, 0, {})
        if not isinstance(root, dict):
            raise RuntimeError("Spark 共有ページの初期データが不正")
        return root
    raise RuntimeError("Spark 共有ページにメール本文の初期データがない")


def _resolve_spark_value(
    values: list[object],
    ref: object,
    cache: dict[int, object],
) -> object:
    # React Router はキーと値を同じ配列の添字で共有する。先にキャッシュへ
    # 入れることで、循環参照があるスレッドでも再帰を止められる。
    if type(ref) is not int:
        return ref
    if ref < 0:
        return None
    if ref >= len(values):
        raise RuntimeError("Spark 共有ページの初期データ参照が範囲外")
    if ref in cache:
        return cache[ref]

    value = values[ref]
    if isinstance(value, dict):
        resolved: dict[str, object] = {}
        cache[ref] = resolved
        for raw_key, value_ref in value.items():
            key_ref: object = raw_key
            if raw_key.startswith("_") and raw_key[1:].isdigit():
                key_ref = int(raw_key[1:])
            key = _resolve_spark_value(values, key_ref, cache)
            if not isinstance(key, str):
                raise RuntimeError("Spark 共有ページの初期データキーが不正")
            resolved[key] = _resolve_spark_value(values, value_ref, cache)
        return resolved
    if isinstance(value, list):
        resolved_list: list[object] = []
        cache[ref] = resolved_list
        resolved_list.extend(
            _resolve_spark_value(values, item, cache) for item in value
        )
        return resolved_list

    cache[ref] = value
    return value


def _extract_email_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    for tag in soup.find_all(style=True):
        style = tag.get("style")
        if isinstance(style, str) and re.search(
            r"display\s*:\s*none", style, re.IGNORECASE
        ):
            tag.decompose()
    return soup.get_text("\n", strip=True)


def _normalize_text(text: str) -> str:
    text = sanitize_public_text(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _extract_html(url: str, html: str) -> SourceContent:
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else url

    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    body = soup.find("article") or soup.find("main") or soup.body
    if body is None:
        raise RuntimeError(f"本文要素が見つからない: {url}")

    text = _normalize_text(body.get_text("\n", strip=True))
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
