"""公開テキストの個人情報サニタイズと Spark 共有 URL の判定.

ソースが Spark メールの場合、共有 URL や個人メールアドレスを公開面に
出してはいけない。両モード（podcast / lecture）の公開成果物が使う
最後の砦として、ここに一元化する。
"""
from __future__ import annotations

import re

# メールアドレス
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Spark の公開共有リンク
_SPARK_SHARE_RE = re.compile(
    r"https?://[\w.-]*sparkmailapp\.com/web-share/\S+", re.IGNORECASE
)
# ローカル絶対パス（ユーザー名・ディレクトリ構造の漏洩防止の最後の砦）。
# 行頭または空白の直後に来る個人ホーム配下の絶対パスだけを対象にし、
# `https://host/Users/...` のような正規 URL のパス部を誤って巻き込まないようにする。
_LOCAL_PATH_RE = re.compile(
    r"(?:(?<=\s)|^)(?:/(?:Users|home|Volumes)/|[A-Za-z]:\\)[^\n]*",
    re.MULTILINE,
)


def is_spark_share_url(url: str) -> bool:
    """Spark の公開共有リンクか判定する."""
    return "sparkmailapp.com/web-share/" in url.lower()


def sanitize_public_text(text: str) -> str:
    """公開テキストの個人情報を除去する最後の砦（メール・Spark URL・ローカルパス）."""
    text = _SPARK_SHARE_RE.sub("[出典リンクは非公開]", text)
    text = _EMAIL_RE.sub("[メールアドレス非公開]", text)
    text = _LOCAL_PATH_RE.sub("[ローカルパス非公開]", text)
    return text
