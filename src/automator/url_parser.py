"""URL リスト読み込み・バリデーション."""

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml
from loguru import logger


@dataclass
class UrlEntry:
    url: str
    audio_length: str | None = None
    prompt: str | None = None


def _validate_url(url: str) -> bool:
    """URL の基本バリデーション."""
    parsed = urlparse(url)
    return bool(parsed.scheme in ("http", "https") and parsed.netloc)


def is_local_path(source: str) -> bool:
    """ソースがローカルファイル/フォルダパスかどうかを判定する."""
    return not source.startswith(("http://", "https://"))


def _validate_audio_length(value: str | None) -> bool:
    """audio_length のバリデーション."""
    return value is None or value in ("short", "long")


def parse_url_file(
    file_path: Path,
    valid_prompt_presets: set[str] | None = None,
) -> list[UrlEntry]:
    """YAML ファイルから URL エントリを読み込みバリデーションする.

    Args:
        file_path: YAML ファイルパス
        valid_prompt_presets: 有効なプロンプトプリセット名のセット

    Returns:
        バリデーション済みの UrlEntry リスト（重複除去済み）
    """
    if not file_path.exists():
        msg = f"URL file not found: {file_path}"
        raise FileNotFoundError(msg)

    raw = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        msg = f"URL file must contain a YAML list, got {type(raw).__name__}"
        raise ValueError(msg)

    entries: list[UrlEntry] = []
    seen_urls: set[str] = set()

    for i, item in enumerate(raw):
        if not isinstance(item, dict) or "url" not in item:
            logger.warning("Skipping entry {}: missing 'url' key", i + 1)
            continue

        url = str(item["url"]).strip()

        # ローカルパスの場合: フォルダならPDFを展開、ファイルならそのまま
        if is_local_path(url):
            local_path = Path(url).expanduser().resolve()
            if local_path.is_dir():
                pdf_files = sorted(local_path.glob("*.pdf"))
                if not pdf_files:
                    logger.warning("No PDF files found in directory: {}", local_path)
                    continue
                for pdf_file in pdf_files:
                    pdf_str = str(pdf_file)
                    if pdf_str in seen_urls:
                        continue
                    seen_urls.add(pdf_str)
                    entries.append(UrlEntry(
                        url=pdf_str,
                        audio_length=item.get("audio_length"),
                        prompt=item.get("prompt"),
                    ))
                    logger.debug("Added local PDF: {}", pdf_file.name)
                continue
            if not local_path.exists():
                logger.warning("Skipping non-existent path: {}", url)
                continue
            if local_path.suffix.lower() != ".pdf":
                logger.warning("Skipping non-PDF file: {}", url)
                continue
            url = str(local_path)

        elif not _validate_url(url):
            logger.warning("Skipping invalid URL: {}", url)
            continue

        if url in seen_urls:
            logger.warning("Skipping duplicate URL: {}", url)
            continue

        audio_length = item.get("audio_length")
        if not _validate_audio_length(audio_length):
            logger.warning(
                "Skipping URL {} — invalid audio_length: {!r}", url, audio_length
            )
            continue

        prompt = item.get("prompt")
        is_unknown = (
            prompt is not None
            and valid_prompt_presets
            and prompt not in valid_prompt_presets
        )
        if is_unknown:
            logger.warning(
                "Skipping URL {} — unknown prompt preset: {!r}", url, prompt
            )
            continue

        seen_urls.add(url)
        entries.append(UrlEntry(url=url, audio_length=audio_length, prompt=prompt))

    logger.info("Parsed {} valid URL entries from {}", len(entries), file_path)
    return entries
