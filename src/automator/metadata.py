"""OGP メタデータ取得."""

from dataclasses import dataclass
from pathlib import Path

import httpx
from bs4 import BeautifulSoup
from loguru import logger

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_TIMEOUT = 10.0


@dataclass
class PageMetadata:
    url: str
    title: str
    description: str
    og_image_url: str | None
    site_name: str | None
    language: str | None


def _get_og_content(soup: BeautifulSoup, property_name: str) -> str | None:
    tag = soup.find("meta", property=property_name)
    if tag and tag.get("content"):
        return str(tag["content"]).strip()
    return None


async def fetch_metadata(url: str) -> PageMetadata:
    """URL から OGP メタデータを取得する."""
    logger.debug("Fetching metadata for {}", url)

    async with httpx.AsyncClient(
        headers={"User-Agent": _USER_AGENT},
        timeout=_TIMEOUT,
        follow_redirects=True,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    # タイトル: og:title → <title>
    title = _get_og_content(soup, "og:title")
    if not title:
        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else url

    # 説明: og:description → meta description
    description = _get_og_content(soup, "og:description")
    if not description:
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            description = str(meta_desc["content"]).strip()
        else:
            description = ""

    og_image_url = _get_og_content(soup, "og:image")
    site_name = _get_og_content(soup, "og:site_name")

    # 言語: html lang 属性
    html_tag = soup.find("html")
    language = str(html_tag.get("lang", "")).strip() or None if html_tag else None

    logger.info("Fetched metadata: title={!r}", title)
    return PageMetadata(
        url=url,
        title=title,
        description=description,
        og_image_url=og_image_url,
        site_name=site_name,
        language=language,
    )


def metadata_for_local_file(file_path: Path) -> PageMetadata:
    """ローカルファイルからメタデータを生成する."""
    title = file_path.stem.replace("_", " ").replace("-", " ")
    logger.info("Local file metadata: title={!r}", title)
    return PageMetadata(
        url=str(file_path),
        title=title,
        description=f"Local file: {file_path.name}",
        og_image_url=None,
        site_name="Local PDF",
        language=None,
    )
