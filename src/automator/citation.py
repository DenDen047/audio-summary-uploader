"""チャット回答からの出典・タイトル抽出と、公開テキストの個人情報サニタイズ.

NotebookLM の chat 回答には引用マーカー（``[1]`` など）が混入し、
ソースが Spark メールの場合は共有 URL や個人メールアドレスを公開面に出してはいけない。
本モジュールはそれらを安全に扱うための純粋関数群を提供する。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

# NotebookLM chat 回答の引用マーカー: [1] / [1, 2] / [12] （直前の空白も巻き取る）
_CITATION_RE = re.compile(r"\s*\[\d+(?:\s*,\s*\d+)*\]")
# メールアドレス
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Spark の公開共有リンク
_SPARK_SHARE_RE = re.compile(
    r"https?://[\w.-]*sparkmailapp\.com/web-share/\S+", re.IGNORECASE
)
# JSON オブジェクト本体（```json フェンスや前後テキストを許容して最初の {...} を拾う）
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)

_NULLISH = {"null", "none", "n/a", "unknown", "不明"}


def strip_citation_markers(text: str) -> str:
    """NotebookLM chat の引用マーカー（[1] や [1, 2]）を除去する.

    全角の 【 】 など日本語の括弧は対象外（ASCII の角括弧のみ）。
    """
    return _CITATION_RE.sub("", text).strip()


def is_spark_share_url(url: str) -> bool:
    """Spark の公開共有リンクか判定する."""
    return "sparkmailapp.com/web-share/" in url.lower()


@dataclass(frozen=True)
class EmailCitation:
    """メール由来ソースの公開用出典情報（生 URL・個人アドレスは含めない）."""

    title: str | None = None
    sender: str | None = None
    date: str | None = None
    domain: str | None = None


def _clean_value(value: object) -> str | None:
    """文字列を正規化し、空や "null" 等は None にする."""
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s or s.lower() in _NULLISH:
        return None
    return s


def parse_email_metadata(answer: str) -> EmailCitation | None:
    """chat の JSON 回答を EmailCitation に変換する.

    ```json フェンスや末尾の引用マーカー、前後の説明文が混じっていても、
    最初の JSON オブジェクトを抽出して解釈する。解釈できなければ None。
    """
    match = _JSON_OBJ_RE.search(strip_citation_markers(answer))
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return EmailCitation(
        title=_clean_value(data.get("title")),
        sender=_clean_value(data.get("sender")),
        date=_clean_value(data.get("date")),
        domain=_clean_value(data.get("domain")),
    )


def format_source_line(citation: EmailCitation) -> str:
    """概要欄用の出典 1 行を作る（生 URL・個人アドレスは出さない）.

    例: ``出典: The Batch（deeplearning.ai） - 2026-06-20``
    """
    parts = [f"出典: {citation.sender or '不明な送信元'}"]
    if citation.domain:
        parts.append(f"（{citation.domain}）")
    if citation.date:
        parts.append(f" - {citation.date}")
    return "".join(parts)


def sanitize_public_text(text: str) -> str:
    """公開テキストから個人情報を除去する（メール・Spark 共有 URL、最後の砦）."""
    text = _SPARK_SHARE_RE.sub("[出典リンクは非公開]", text)
    text = _EMAIL_RE.sub("[メールアドレス非公開]", text)
    return text
