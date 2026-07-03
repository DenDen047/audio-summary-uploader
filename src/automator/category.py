"""ソースのカテゴリ自動判定と、カテゴリ別のサムネスタイル/プレイリスト解決.

分類はルールベース（ドメイン/拡張子）を第一とする。カテゴリは ④ のサムネ配色と
③ のプレイリスト振り分けの両方に使われ、1チャンネル内でジャンル別に整理する。
"""
from __future__ import annotations

from urllib.parse import urlparse

from automator.image_gen import DEFAULT_STYLE, ThumbnailStyle

# カテゴリキー
PAPER = "paper"
NEWS = "news"
ENGINEERING = "engineering"
BUSINESS = "business"
DEFAULT = "default"

ALL_CATEGORIES: tuple[str, ...] = (PAPER, NEWS, ENGINEERING, BUSINESS, DEFAULT)
# ルールで確定しづらい（ドメインだけでは内容が判らない）カテゴリ。chat 再判定の対象。
AMBIGUOUS_CATEGORIES: frozenset[str] = frozenset({BUSINESS, DEFAULT})

# ドメイン部分一致 → カテゴリ のルール（上から順に評価）
_DOMAIN_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("arxiv.org", "openreview.net", "aclanthology.org", "nature.com"), PAPER),
    (
        (
            "sparkmailapp.com", "theinformation.com", "deeplearning.ai",
            "substack.com", "beehiiv.com",
        ),
        NEWS,
    ),
    (
        ("github.com", "zenn.dev", "qiita.com", "stackoverflow.com", "huggingface.co"),
        ENGINEERING,
    ),
    (("youtube.com", "youtu.be"), BUSINESS),
)

# カテゴリ別のサムネスタイル（ブランド統一しつつジャンルで配色を変える）
_STYLES: dict[str, ThumbnailStyle] = {
    PAPER: ThumbnailStyle(
        name=PAPER,
        palette="deep navy to violet gradient with subtle academic grid lines",
        motif="a glowing neural network and abstract 3D geometry on the right",
        text_color="vivid gold (#FFD24A)",
        accent="#5B4BE0",
    ),
    NEWS: ThumbnailStyle(
        name=NEWS,
        palette="dark charcoal background with a bold crimson accent band",
        motif="an abstract broadcast/newspaper motif with motion streaks on the right",
        text_color="bright white (#FFFFFF)",
        accent="#D7263D",
    ),
    ENGINEERING: ThumbnailStyle(
        name=ENGINEERING,
        palette="dark teal to deep green gradient with faint code/terminal glyphs",
        motif="glowing circuit traces and a stylized terminal window on the right",
        text_color="cyan-tinted white (#E8FFFF)",
        accent="#0E8A6D",
    ),
    BUSINESS: ThumbnailStyle(
        name=BUSINESS,
        palette="black to dark gold gradient with a premium look",
        motif="an upward glowing growth chart on the right",
        text_color="vivid gold (#FFD24A)",
        accent="#C9971C",
    ),
}


def classify_category(url: str) -> str:
    """URL（拡張子/ドメイン）からカテゴリを判定する（未分類は DEFAULT）."""
    lower = url.lower()
    if lower.endswith(".pdf"):
        return PAPER
    host = (urlparse(url).netloc or "").lower()
    for domains, category in _DOMAIN_RULES:
        if any(domain in host for domain in domains):
            return category
    return DEFAULT


def parse_category(answer: str) -> str | None:
    """chat 回答から既知のカテゴリキーを1つ抽出する（見つからなければ None）."""
    lowered = answer.lower()
    for category in ALL_CATEGORIES:
        if category in lowered:
            return category
    return None


def style_for_category(category: str | None) -> ThumbnailStyle:
    """カテゴリに対応するサムネスタイルを返す（未知/None は DEFAULT_STYLE）."""
    if category is None:
        return DEFAULT_STYLE
    return _STYLES.get(category, DEFAULT_STYLE)


def resolve_playlist_id(
    category: str | None,
    playlists: dict[str, str],
    default_playlist_id: str | None,
) -> str | None:
    """カテゴリ→playlist_id を解決する（無ければ既定 playlist_id へ）."""
    if category and category in playlists:
        return playlists[category]
    return default_playlist_id


def resolve_playlist_ids(
    category: str | None,
    playlists: dict[str, str],
    default_playlist_id: str | None,
    all_playlist_id: str | None,
) -> list[str]:
    """追加先プレイリスト ID の一覧を返す（カテゴリ解決＋全動画横断、重複除外）."""
    resolved = resolve_playlist_id(category, playlists, default_playlist_id)
    ids = []
    for playlist_id in (resolved, all_playlist_id):
        if playlist_id and playlist_id not in ids:
            ids.append(playlist_id)
    return ids
