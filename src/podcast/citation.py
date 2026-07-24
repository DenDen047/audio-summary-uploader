"""NotebookLM chat 回答からの出典・タイトル・略称抽出.

chat 回答には引用マーカー（``[1]`` など）が混入するため、それらを安全に
扱うための純粋関数群を提供する。公開テキストのサニタイズと Spark URL
判定は :mod:`sources.sanitize` に一元化されている。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

# NotebookLM chat 回答の引用マーカー: [1] / [1, 2] / [12] （直前の空白も巻き取る）
_CITATION_RE = re.compile(r"\s*\[\d+(?:\s*,\s*\d+)*\]")
# JSON オブジェクト本体（```json フェンスや前後テキストを許容して最初の {...} を拾う）
_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)
# 論文の通称・略称として妥当な形（英数字始まり・英数字と .+- のみ・1〜16 字）。
# 3DGS/3D-GS のような数字始まりも許すが、英字を1文字も含まない語（年号など）は
# 別途弾く。日本語や長い語句、空白を含むフレーズもここで弾く（ハルシネーション対策）。
_PAPER_SHORTNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+\-]{0,15}$")

_NULLISH = {"null", "none", "n/a", "unknown", "不明"}


def strip_citation_markers(text: str) -> str:
    """NotebookLM chat の引用マーカー（[1] や [1, 2]）を除去する.

    全角の 【 】 など日本語の括弧は対象外（ASCII の角括弧のみ）。
    """
    return _CITATION_RE.sub("", text).strip()


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


def clean_paper_shortname(raw: object) -> str | None:
    """chat が返した論文の通称・略称（SAM/YOLO 等）を検証する（妥当でなければ None）.

    引用マーカー・囲み引用符を剥がし、``none``/``null``/``なし`` 等は None にする。
    英字始まりの短い英数字トークン（略称の形）以外は弾き、誤抽出を公開面に出さない。
    """
    if not isinstance(raw, str):
        return None
    text = strip_citation_markers(raw).strip()
    text = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    for open_q, close_q in (("「", "」"), ("『", "』"), ("“", "”"), ('"', '"')):
        if len(text) >= 2 and text[0] == open_q and text[-1] == close_q:
            text = text[1:-1].strip()
            break
    if text.lower() in _NULLISH or text in {"なし", "無し"}:
        return None
    if not _PAPER_SHORTNAME_RE.match(text):
        return None
    if not any(c.isascii() and c.isalpha() for c in text):
        return None  # 年号など英字を含まない数字トークンは略称ではない
    return text
