"""OGP メタデータ取得."""

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

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
    favicon_url: str | None = None


def _extract_favicon_url(soup: BeautifulSoup, page_url: str) -> str | None:
    """HTML から favicon URL を抽出する.

    見つからなければ /favicon.ico にフォールバック。
    """
    for rel_value in (
        ["icon"], ["shortcut", "icon"], ["apple-touch-icon"],
    ):
        link = soup.find("link", rel=rel_value)
        if link and link.get("href"):
            href = str(link["href"]).strip()
            if href:
                return urljoin(page_url, href)

    # フォールバック: /favicon.ico
    parsed = urlparse(page_url)
    return f"{parsed.scheme}://{parsed.netloc}/favicon.ico"


_MAX_PDF_IMAGE_PIXELS = 25_000_000  # 25MP 上限


def _extract_pdf_first_image(file_path: Path) -> bytes | None:
    """PDF から最大の埋め込み画像を抽出する。見つからなければ None."""
    try:
        import pymupdf  # noqa: PLC0415
    except ImportError:
        logger.warning("pymupdf not installed, skipping PDF image extraction")
        return None

    try:
        doc = pymupdf.open(str(file_path))
    except Exception:
        logger.warning("Failed to open PDF: {}", file_path)
        return None

    best_xref: int | None = None
    best_size = 0

    # 最初の 5 ページだけ探索（パフォーマンスのため）
    for page_num in range(min(5, len(doc))):
        page = doc[page_num]
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            try:
                pix = pymupdf.Pixmap(doc, xref)
                img_size = pix.width * pix.height
                if img_size > _MAX_PDF_IMAGE_PIXELS:
                    logger.debug(
                        "Skipping oversized image xref={} ({}px)",
                        xref, img_size,
                    )
                    continue
                if img_size > best_size:
                    best_xref = xref
                    best_size = img_size
            except Exception:
                logger.debug(
                    "Failed to read image xref={} from PDF", xref,
                )
                continue

    # 最大画像が見つかったら PNG エンコード（1回だけ）
    result: bytes | None = None
    if best_xref is not None:
        pix = pymupdf.Pixmap(doc, best_xref)
        if pix.n > 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        result = pix.tobytes("png")
        logger.info("Extracted image from PDF ({} bytes)", len(result))

    doc.close()
    return result


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

    # ファビコン: <link rel="icon"> → /favicon.ico フォールバック
    favicon_url = _extract_favicon_url(soup, url)

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
        favicon_url=favicon_url,
    )


def metadata_for_local_file(
    file_path: Path, tmp_dir: Path | None = None,
) -> PageMetadata:
    """ローカルファイルからメタデータを生成する.

    PDF の場合、埋め込み画像の抽出を試みる。
    抽出成功時は og_image_url に一時ファイルパスを設定する。
    失敗時は favicon_url に PDF アイコンパスを設定する。
    """
    title = file_path.stem.replace("_", " ").replace("-", " ")
    logger.info("Local file metadata: title={!r}", title)

    og_image_url: str | None = None
    favicon_url: str | None = None

    if file_path.suffix.lower() == ".pdf":
        # PDF から画像抽出を試みる
        image_data = _extract_pdf_first_image(file_path)
        if image_data and tmp_dir:
            extracted_path = tmp_dir / "thumbnails" / f"{file_path.stem}_pdf_img.png"
            extracted_path.parent.mkdir(parents=True, exist_ok=True)
            extracted_path.write_bytes(image_data)
            og_image_url = str(extracted_path)
            logger.info("Using extracted PDF image: {}", extracted_path)
        else:
            # PDF アイコンをフォールバックとして使用
            _project_root = Path(__file__).resolve().parent.parent.parent
            pdf_icon_path = _project_root / "data" / "pdf.png"
            if pdf_icon_path.exists():
                favicon_url = str(pdf_icon_path)
                logger.info("Using PDF icon fallback: {}", pdf_icon_path)

    return PageMetadata(
        url=str(file_path),
        title=title,
        description=f"Local file: {file_path.name}",
        og_image_url=og_image_url,
        site_name="Local PDF",
        language=None,
        favicon_url=favicon_url,
    )
