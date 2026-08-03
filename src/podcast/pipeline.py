"""パイプライン全体のオーケストレーション（3フェーズ: submit / collect / upload）."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from google.oauth2.credentials import Credentials

from notebooklm.exceptions import AuthError as NotebookLMAuthError
from notebooklm.exceptions import NetworkError as NotebookLMNetworkError

from lecture.pipeline import LectureArtifacts, generate_lecture
from lecture.thumbnail_backdrop import ThumbnailBackdropOptions
from podcast.category import (
    AMBIGUOUS_CATEGORIES,
    classify_category,
    parse_category,
    resolve_playlist_ids,
    style_for_category,
)
from podcast.citation import (
    EmailCitation,
    clean_paper_shortname,
    format_source_line,
    parse_email_metadata,
    strip_citation_markers,
)
from podcast.config import Settings
from podcast.image_gen import (
    DEFAULT_STYLE,
    ThumbnailStyle,
    generate_background_image,
    generate_thumbnail_image,
    resolve_google_storage_state,
    storage_state_for_profile,
)
from podcast.locking import pipeline_lock
from podcast.metadata import PageMetadata, fetch_metadata, metadata_for_local_file
from podcast.notebooklm import NotebookLMBackend
from podcast.notebooklm_py_backend import NotebookLMPyBackend
from podcast.report import ProcessResult
from podcast.thumbnail import ThumbCopy, compose_thumbnail, generate_thumbnail
from podcast.url_parser import UrlEntry, is_local_path
from podcast.video import convert_to_video, probe_duration
from podcast.youtube import (
    YouTubeUploadParams,
    authenticate,
    set_thumbnail,
    upload_video,
)
from sources.fetch import ExtractedSource, RemoteSource, resolve_source
from sources.sanitize import is_spark_share_url, sanitize_public_text

_NOTEBOOKLM_AUTH_ERROR_MSG = (
    "NotebookLM の認証が期限切れです。"
    "ターミナルで 'uv run notebooklm login' を実行して再認証してください。"
    "再認証後、Web UI からリトライできます。"
)

_AUTH_ERROR_KEYWORDS = ("authentication", "expired", "re-authenticate", "login")

# 複数ソースを1音声にまとめる場合の仮タイトル（最終タイトルは ② chat で生成）
_MULTI_TITLE_PLACEHOLDER = "複数ソースのまとめ"
# NotebookLM chat に投げる定型プロンプト（本文は prompts/ 配下の md で管理）
_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


# メール系ソースの出典抽出に使う chat 質問（受信者情報は出させない）
_EMAIL_META_QUESTION = _load_prompt("email_meta.md")
# 日本語タイトル生成に使う chat 質問。
# タイトルポリシーの根拠は specs/PODCAST_SPEC.md「YouTube タイトルの形式」を参照。
_JP_TITLE_QUESTION = _load_prompt("jp_title.md")
# 論文の通称・略称抽出に使う chat 質問（無ければ none）。有名論文の解説を探す
# 学生・研究者向けに、SAM/YOLO 等の略称をタイトル・サムネに載せて検索性を上げる。
_PAPER_SHORTNAME_QUESTION = _load_prompt("paper_shortname.md")
# 固定マスコット素材（毎回の生成で参照画像として渡すキャラの元。文字はこの上に合成）
_MASCOT_BASE = (
    Path(__file__).resolve().parent.parent.parent
    / "assets" / "thumbnails" / "mascot_default.png"
)
# サムネのマスコットのポーズ型。キャラは固定のまま動画ごとにこの型を回して、
# 縮小時にも各動画が見分けられるよう絵柄を散らす（slug のハッシュで決定的に選択）。
_THUMB_POSE_VARIATIONS = [
    "throwing both arms up in the air in shock",
    "pointing at the object with one arm, wide-eyed",
    "gripping the object in both hands and leaning in, jaw dropped",
    "peeking wide-eyed from the right with one hand raised near its face",
    "one hand on its cheek and the other flung out toward the object",
    "leaning back startled with both hands raised",
]
# サムネ用3層テキスト（上=製品名/導入・中=説明・下=ベネフィット）生成に使う chat 質問。
# 伸びているAI解説チャンネルの「型」に合わせ、1秒で伝わるよう短く・専門用語を避ける。
_THUMB_COPY_QUESTION = _load_prompt("thumb_copy.md")
# サムネテキストが生成できない時のバナー用フォールバックラベル
_CATEGORY_BANNER: dict[str, str] = {
    "paper": "論文解説",
    "news": "AIニュース",
    "engineering": "AI開発",
    "business": "ビジネス",
}
_DEFAULT_BANNER = "AI要約"        # top のフォールバック（カテゴリ不明時）
_THUMB_TOP_MAX_LEN = 9
_THUMB_MID_MAX_LEN = 10
_THUMB_BOTTOM_MAX_LEN = 11

# カテゴリ内容判定に使う chat 質問（曖昧カテゴリのみ）
_CATEGORY_QUESTION = _load_prompt("category.md")
# タイトル先頭から剥がす絵文字・記号と、全体を囲う引用符ペア
_TITLE_LEADING_STRIP = "🎧🎙️📻🔊 　"
_TITLE_QUOTE_PAIRS = (
    ("「", "」"), ("『", "』"), ("“", "”"), ('"', '"'), ("'", "'"),
)


def _is_notebooklm_auth_error(exc: Exception) -> bool:
    """NotebookLM の認証エラーかどうかを判定する."""
    if isinstance(exc, NotebookLMAuthError):
        return True
    msg = str(exc).lower()
    return sum(1 for kw in _AUTH_ERROR_KEYWORDS if kw in msg) >= 2


async def _cleanup_notebook(
    backend: NotebookLMBackend, notebook_id: str
) -> None:
    """部分的失敗時に作成済みノートブックを best-effort で削除する.

    削除自体が失敗しても呼び出し元の元エラーを優先したいので、例外は WARN ログのみ。
    NotebookLM のアカウント上限消費を防ぐためのリーク対策。
    """
    try:
        await backend.delete_notebook(notebook_id)
        logger.info("Cleaned up orphaned notebook: {}", notebook_id)
    except Exception as exc:
        logger.warning(
            "Failed to cleanup orphaned notebook {}: {}", notebook_id, exc
        )


def _make_slug(url: str) -> str:
    """URL から一意な slug を生成する (SHA-256 先頭 12 文字)."""
    return hashlib.sha256(url.encode()).hexdigest()[:12]


def _resolve_prompt_preset(preset_name: str | None, settings: Settings) -> str:
    """プロンプトプリセット名を実際のテキストに解決する."""
    name = preset_name or "default"
    presets = settings.podcast.prompt_presets
    if name not in presets:
        msg = f"Unknown prompt preset: {name!r}"
        raise ValueError(msg)
    return presets[name]


# ⑤ カテゴリ別ハッシュタグ（3〜5個）。未分類は汎用。
_CATEGORY_HASHTAGS: dict[str, list[str]] = {
    "paper": ["#AI", "#論文解説", "#機械学習"],
    "news": ["#AI", "#AIニュース", "#テック"],
    "engineering": ["#AI", "#プログラミング", "#開発"],
    "business": ["#AI", "#ビジネス", "#副業"],
}
_DEFAULT_HASHTAGS = ["#AI", "#音声要約", "#NotebookLM"]


def _build_hashtags(category: str | None) -> str:
    """カテゴリに応じたハッシュタグ行を返す."""
    tags = _CATEGORY_HASHTAGS.get(category or "", _DEFAULT_HASHTAGS)
    return " ".join(tags)


def _local_source_label(source_path: str) -> str:
    """ローカルファイルパスを公開用の資料名（ファイル名のみ）に変換する.

    絶対パス（ユーザー名やディレクトリ構造）を公開面に出さないため、ディレクトリと
    拡張子を落とした stem だけを出典として表示する。Zotero 等の stem は
    「著者 - 年 - タイトル」形式で、公開してよい論文メタデータになっている。
    """
    stem = Path(source_path).stem.strip()
    return stem or "資料"


def _format_source_block(urls: list[str], site_name: str | None) -> str:
    """公開用の出典ブロックを作る（メール共有 URL とローカルパスは秘匿）.

    複数ソースは各 URL を列挙する。メール系ソースは URL を出さず汎用ラベルにする。
    ローカルファイルは絶対パスを出さず、ファイル名（＝資料名）だけを表示する。
    """
    lines: list[str] = []
    has_email = False
    has_local = False
    for source_url in urls:
        if is_spark_share_url(source_url):
            has_email = True
        elif is_local_path(source_url):
            has_local = True
            lines.append(f"📄 元資料: {_local_source_label(source_url)}")
        else:
            lines.append(f"📄 元記事: {source_url}")
    # サイト名は http(s) 単一ソースのときだけ（ローカル/メールは上で出典済み）
    if site_name and len(urls) == 1 and not has_email and not has_local:
        lines.append(f"📰 ソース: {site_name}")
    if has_email:
        lines.append("📰 ソース: メールニュースレター")
    return "\n".join(lines) if lines else "📰 ソース: メールニュースレター"


def _build_description(
    metadata: PageMetadata,
    *,
    citation: EmailCitation | None = None,
    category: str | None = None,
    extra_urls: list[str] | None = None,
) -> str:
    """YouTube 概要欄を生成する（冒頭＋出典＋ハッシュタグ、個人情報はサニタイズ）.

    メール系ソース（Spark 共有等）は生 URL を公開面に出さない。出典が取れていれば
    「出典: 送信元 - 日付」を、取れていなければ汎用ラベルを表示する。複数ソースは
    全 URL を列挙する。内部設定（プロンプト名・音声長）は公開面に出さない。
    """
    if citation is not None:
        source_block = format_source_line(citation)
        # 複数ソースで先頭がメールでも、メール以外の追加ソースは出典として列挙する
        if extra_urls:
            source_block = f"{source_block}\n{_format_source_block(extra_urls, None)}"
    else:
        source_block = _format_source_block(
            [metadata.url, *(extra_urls or [])], metadata.site_name
        )

    intro = (
        "AIが元情報をもとに自動生成した、ポッドキャスト風の音声要約です。\n"
        "通勤や作業のお供にどうぞ。"
    )
    footer = (
        "※ Gemini Notebook（旧 NotebookLM）の音声概要で自動生成。要点把握用です。"
        "正確な内容は元情報をご確認ください。"
    )
    description = (
        f"{intro}\n\n{source_block}\n\n{_build_hashtags(category)}\n\n---\n{footer}"
    )
    # 最後の砦: メールアドレス・Spark 共有 URL を除去する
    return sanitize_public_text(description.strip())


def _sanitize_youtube_title(title: str) -> str:
    """YouTube API が拒否する文字を置換する."""
    replacements = {
        "<": "＜",
        ">": "＞",
    }
    for old, new in replacements.items():
        title = title.replace(old, new)
    return title


def _build_title(metadata: PageMetadata, settings: Settings) -> str:
    """YouTube タイトルを生成する."""
    prefix = settings.youtube.title_prefix
    max_len = settings.youtube.title_max_length
    title = sanitize_public_text(_sanitize_youtube_title(metadata.title))
    if len(title) > max_len:
        title = title[: max_len - 1] + "…"
    return f"{prefix} {title}"


def _metadata_for_extracted_source(url: str, source: ExtractedSource) -> PageMetadata:
    """抽出済みソース（Spark メール等）のメタデータを作る.

    生の共有 URL を公開面に出さないため OGP は取得せず、SSR から抽出済みの
    件名をタイトルに使う（最終タイトルは collect の chat で日本語生成）。
    """
    return PageMetadata(
        url=url,
        title=source.title,
        description="",
        og_image_url=None,
        site_name="メールニュースレター",
        language=None,
        favicon_url=None,
    )


async def _extract_email_citation(
    backend: NotebookLMBackend, notebook_id: str, url: str
) -> EmailCitation | None:
    """メール系ソースから出典(件名/送信元/日付)を chat で抽出する."""
    try:
        answer = await backend.ask(notebook_id, _EMAIL_META_QUESTION)
    except Exception as exc:
        logger.warning("出典抽出に失敗 ({}): {}", url, exc)
        return None
    citation = parse_email_metadata(answer)
    if citation is None:
        logger.warning("出典の解析に失敗 ({})", url)
    return citation


def _clean_generated_title(raw: str, max_len: int) -> str | None:
    """chat が生成した日本語タイトルを整形する（整形できなければ None）.

    引用マーカー除去 → 最初の非空行 → 全体を囲う引用符の除去 →
    先頭の絵文字/空白の除去 → 全角換算の長さ上限。
    """
    if not raw:
        return None
    text = strip_citation_markers(raw)
    title = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if not title:
        return None
    for open_q, close_q in _TITLE_QUOTE_PAIRS:
        if len(title) >= 2 and title[0] == open_q and title[-1] == close_q:
            title = title[1:-1].strip()
            break
    title = title.lstrip(_TITLE_LEADING_STRIP).strip()
    if not title:
        return None
    if len(title) > max_len:
        title = title[: max_len - 1] + "…"
    return title


async def _generate_japanese_title(
    backend: NotebookLMBackend, notebook_id: str, max_len: int
) -> str | None:
    """ノートブックの内容から日本語の YouTube タイトルを chat で生成する."""
    try:
        answer = await backend.ask(notebook_id, _JP_TITLE_QUESTION)
    except Exception as exc:
        logger.warning("日本語タイトル生成に失敗: {}", exc)
        return None
    if not isinstance(answer, str):
        logger.warning("日本語タイトル生成: 予期しない回答型 {}", type(answer).__name__)
        return None
    title = _clean_generated_title(answer, max_len)
    if title is None:
        logger.warning("生成タイトルの整形に失敗")
    return title


async def _extract_paper_shortname(
    backend: NotebookLMBackend, notebook_id: str
) -> str | None:
    """論文の通称・略称（SAM/YOLO 等）を chat で抽出する（無ければ None）."""
    try:
        answer = await backend.ask(notebook_id, _PAPER_SHORTNAME_QUESTION)
    except Exception as exc:
        logger.warning("論文略称の抽出に失敗: {}", exc)
        return None
    return clean_paper_shortname(answer)


def _clean_thumb_text(value: object, max_len: int) -> str | None:
    """chat が返したサムネ用テキスト1項目を検証・整形する."""
    if not isinstance(value, str):
        return None
    text = sanitize_public_text(value.strip().splitlines()[0].strip())
    for open_q, close_q in _TITLE_QUOTE_PAIRS:
        if text.startswith(open_q) and text.endswith(close_q):
            text = text[len(open_q):-len(close_q)].strip()
    if not text or len(text) > max_len:
        return None
    return text


async def _generate_thumb_copy(
    backend: NotebookLMBackend,
    notebook_id: str,
    category: str | None,
    headline: str,
) -> ThumbCopy:
    """サムネ用の3層コピー(top/mid/bottom/highlight)を chat で生成する.

    生成できない項目は妥当なフォールバックで埋める（top=カテゴリラベル、bottom=見出し）。
    """
    fallback = ThumbCopy(
        top=_CATEGORY_BANNER.get(category or "", _DEFAULT_BANNER),
        bottom=headline[:_THUMB_BOTTOM_MAX_LEN],
    )
    try:
        answer = await backend.ask(notebook_id, _THUMB_COPY_QUESTION)
    except Exception as exc:
        logger.warning("サムネコピー生成に失敗: {}", exc)
        return fallback
    if not isinstance(answer, str):
        return fallback
    start, end = answer.find("{"), answer.rfind("}")
    if start == -1 or end <= start:
        logger.warning("サムネコピーの JSON が見つからない: {!r}", answer[:80])
        return fallback
    try:
        data = json.loads(answer[start : end + 1])
    except json.JSONDecodeError as exc:
        logger.warning("サムネコピーの JSON 解析に失敗: {}", exc)
        return fallback
    top = _clean_thumb_text(data.get("top"), _THUMB_TOP_MAX_LEN) or fallback.top
    mid = _clean_thumb_text(data.get("mid"), _THUMB_MID_MAX_LEN) or ""
    bottom = (
        _clean_thumb_text(data.get("bottom"), _THUMB_BOTTOM_MAX_LEN) or fallback.bottom
    )
    highlight = _clean_thumb_text(data.get("highlight"), _THUMB_BOTTOM_MAX_LEN) or ""
    if highlight and highlight not in bottom:
        highlight = ""
    return ThumbCopy(top=top, mid=mid, bottom=bottom, highlight=highlight)


async def _refine_category(
    backend: NotebookLMBackend, notebook_id: str, rule_category: str
) -> str:
    """曖昧カテゴリ(business/default)のみ NotebookLM chat で内容ベースに再判定する.

    確定カテゴリ(arxiv/spark/github 等)はそのまま返す。chat 失敗/解析不可なら
    ルール判定結果にフォールバックする。
    """
    if rule_category not in AMBIGUOUS_CATEGORIES:
        return rule_category
    try:
        answer = await backend.ask(notebook_id, _CATEGORY_QUESTION)
    except Exception as exc:
        logger.warning("カテゴリ再判定に失敗: {}", exc)
        return rule_category
    if not isinstance(answer, str):
        return rule_category
    refined = parse_category(answer)
    if refined is None:
        return rule_category
    if refined != rule_category:
        logger.info("カテゴリ再判定: {} → {}", rule_category, refined)
    return refined


# 背景ローテーション: 1枚あたりの目標表示秒数と生成枚数の上限。
# 同じ画像は動画内で1回しか出さないため、音声長に応じて枚数を決める。
_BG_TARGET_SEC = 45.0
_MAX_BACKGROUNDS = 6
# 複数枚が同じ絵にならないよう構図ヒントを順に変える
_BG_VARIATIONS = [
    "wide cinematic establishing shot",
    "dramatic close-up of the main subject",
    "isometric conceptual illustration",
    "low-angle dynamic scene",
    "abstract macro detail of the subject",
    "aerial overview scene",
]


async def _generate_thumb_base(
    slug: str,
    tmp_dir: Path,
    settings: Settings,
    topic: str,
    style: ThumbnailStyle | None = None,
    storage_state_path: Path | None = None,
) -> Path | None:
    """サムネ用の話題連動・文字なしベース画像を AI 生成する(best-effort).

    固定マスコットを参照画像として渡すことで、キャラの同一性と「大きな顔＋驚いた
    表情」を保ったまま、背景/小物だけを話題に合わせて差し替える。文字は呼び出し側が
    compose_thumbnail(Pillow) で合成するので画像には描かせない。生成失敗(cookie
    失効・地域制限等)は None を返し、呼び出し側が固定マスコット(静止)に縮退する。
    """
    out = tmp_dir / "thumbnails" / f"{slug}_thumbbase.png"
    # slug から決定的にポーズ型を選ぶ（動画ごとに絵柄を散らす／リトライで同じ絵になる）
    pose_idx = int(hashlib.sha256(slug.encode()).hexdigest(), 16) % len(
        _THUMB_POSE_VARIATIONS
    )
    return await generate_thumbnail_image(
        topic,
        out,
        width=settings.thumbnail.width,
        height=settings.thumbnail.height,
        style=style or DEFAULT_STYLE,
        # 固定マスコットを参照画像として渡し、キャラ同一性＋驚き顔を保ったまま
        # ポーズ・小物・配色を話題ごとに変える。素材が無ければ記述だけで生成する。
        reference_image=_MASCOT_BASE if _MASCOT_BASE.exists() else None,
        pose=_THUMB_POSE_VARIATIONS[pose_idx],
        storage_state_path=storage_state_path
        or storage_state_for_profile(settings.podcast.image_profile),
    )


async def _compose_topic_thumbnail(
    slug: str,
    tmp_dir: Path,
    settings: Settings,
    headline: str,
    style: ThumbnailStyle,
    thumb_copy: ThumbCopy,
    thumbnail_path: Path,
    site_name: str | None,
    storage_state_path: Path | None = None,
    ai_enabled: bool = True,
) -> Path:
    """話題連動ベース画像 → 固定マスコット → グラデーションの順に縮退しつつ、
    3層テキストを合成したサムネの Path を返す.

    簡易動画モードでは AI ベース生成を行わず固定マスコット(無ければ縮退)を使う
    (429/cookie 失効の影響を受けない)。
    """
    base_image: Path | None = None
    if not settings.general.simple_video_mode and ai_enabled:
        base_image = await _generate_thumb_base(
            slug,
            tmp_dir,
            settings,
            topic=headline,
            style=style,
            storage_state_path=storage_state_path,
        )
    if base_image is None and _MASCOT_BASE.exists():
        base_image = _MASCOT_BASE
    if base_image is not None:
        return compose_thumbnail(
            base_image, thumb_copy, thumbnail_path, settings.thumbnail
        )
    logger.warning("ベース画像が無い → グラデーションにフォールバック")
    return await generate_thumbnail(
        title=thumb_copy.bottom or headline,
        site_name=site_name,
        og_image_url=None,
        output_path=thumbnail_path,
        config=settings.thumbnail,
        favicon_url=None,
    )


async def _generate_backgrounds(
    slug: str,
    tmp_dir: Path,
    settings: Settings,
    audio_path: Path,
    topic: str | None,
    style: ThumbnailStyle | None = None,
    storage_state_path: Path | None = None,
) -> list[Path]:
    """動画の背景ローテーション用のテキストなし・話題関連AI背景を生成する.

    音声長から必要枚数を決める。単発の失敗（タイムアウト等）はスキップして続行し、
    連続2回失敗したら打ち切る（cookie 失効時に無駄な再試行をしない）。
    空リストなら静止背景に縮退する。
    """
    duration = probe_duration(audio_path)
    count = max(1, min(_MAX_BACKGROUNDS, int(duration // _BG_TARGET_SEC)))
    paths: list[Path] = []
    consecutive_failures = 0
    for i in range(count):
        out = tmp_dir / "thumbnails" / f"{slug}_bg{i}.png"
        bg = await generate_background_image(
            out,
            width=settings.thumbnail.width,
            height=settings.thumbnail.height,
            style=style or DEFAULT_STYLE,
            topic=topic,
            variation=_BG_VARIATIONS[i % len(_BG_VARIATIONS)],
            storage_state_path=storage_state_path
            or storage_state_for_profile(settings.podcast.image_profile),
        )
        if bg is None:
            consecutive_failures += 1
            if consecutive_failures >= 2:
                logger.warning(
                    "AI背景の生成が連続失敗したため打ち切り ({}枚生成済み)",
                    len(paths),
                )
                break
            continue
        consecutive_failures = 0
        paths.append(bg)
    return paths


async def _resolve_image_storage_state(settings: Settings) -> Path | None:
    """画像生成に使える専用 profile または NotebookLM profile を選ぶ."""
    if settings.general.simple_video_mode:
        return None
    selected = await resolve_google_storage_state(
        [
            storage_state_for_profile(settings.podcast.image_profile),
            storage_state_for_profile("default"),
        ]
    )
    if selected is None:
        logger.warning("利用可能なAI画像 profile が無いため静止画像へフォールバック")
    return selected


# --- 状態管理 ---


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _migrate_state(state: dict) -> dict:
    """旧 state.json (processed キー・旧モード名) を現行スキーマへ移行する."""
    if "jobs" in state:
        for job in state["jobs"]:
            job.setdefault("mode", "podcast")
            # 旧モード名 "notebooklm" は "podcast" へ改名（2026-07）
            if job["mode"] == "notebooklm":
                job["mode"] = "podcast"
            job.setdefault("privacy_status", None)
            job.setdefault("script_path", None)
            job.setdefault("upload_metadata", None)
            job.setdefault("upload_metadata_path", None)
            job.setdefault("image_profile_used", None)
            job.setdefault("background_paths", [])
        return state
    old_processed = state.get("processed", [])
    jobs: list[dict[str, Any]] = []
    for entry in old_processed:
        status = entry.get("status", "failed")
        if status == "success":
            status = "uploaded"
        job: dict[str, Any] = {
            "url": entry["url"],
            "slug": _make_slug(entry["url"]),
            "mode": "podcast",
            "audio_length": entry.get("audio_length", "default"),
            "prompt": entry.get("prompt", "default"),
            "privacy_status": None,
            "status": status,
            "notebook_id": entry.get("notebook_id"),
            "task_id": None,
            "metadata": None,
            "extra_urls": [],
            "audio_path": None,
            "thumbnail_path": None,
            "video_path": None,
            "script_path": None,
            "upload_metadata": None,
            "upload_metadata_path": None,
            "image_profile_used": None,
            "background_paths": [],
            "youtube_url": entry.get("youtube_url"),
            "error": entry.get("error"),
            "submitted_at": entry.get("processed_at"),
            "collected_at": entry.get("processed_at") if status == "uploaded" else None,
            "uploaded_at": entry.get("processed_at") if status == "uploaded" else None,
        }
        jobs.append(job)
    logger.info("Migrated {} old entries to new jobs schema", len(jobs))
    return {"last_run": state.get("last_run"), "jobs": jobs}


def _load_state(state_path: Path) -> dict:
    """状態ファイルを読み込む（必要に応じてマイグレーション）."""
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        return _migrate_state(state)
    return {"last_run": None, "jobs": []}


def _save_state(state_path: Path, state: dict) -> None:
    """状態ファイルをアトミックに保存する."""
    content = json.dumps(state, ensure_ascii=False, indent=2)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=state_path.parent, suffix=".tmp", prefix=".state_"
    )
    try:
        with open(tmp_fd, "w", encoding="utf-8") as f:
            f.write(content)
        Path(tmp_path).replace(state_path)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def _update_job_state(
    state_path: Path,
    url: str,
    updates: dict[str, Any],
    mode: str = "podcast",
) -> None:
    """state.json からジョブを検索し、指定フィールドのみ更新して保存する.

    ディスク上の最新 state を読み直すことで、他の操作 (clear, delete 等) との
    競合によるデータ復活を防ぐ。ジョブが既に削除されていた場合は何もしない。
    """
    state = _load_state(state_path)
    for job in state["jobs"]:
        if job["url"] == url and job.get("mode", "podcast") == mode:
            job.update(updates)
            break
    state["last_run"] = _now_iso()
    _save_state(state_path, state)


def _upsert_job_state(
    state_path: Path,
    url: str,
    audio_length: str,
    prompt: str,
    updates: dict[str, Any],
    mode: str = "podcast",
) -> None:
    """state.json を読み直してジョブを find-or-create し、更新して保存する.

    submit フェーズ用。メモリ上の古い state 全体を書き戻すと、並行する
    Web 操作 (delete / clear / retry / add) を巻き戻してしまうため、
    必ずディスク上の最新 state に対して更新する。処理中のジョブが
    並行操作で削除されていた場合は再作成する (オーファン化を防ぐ)。
    """
    state = _load_state(state_path)
    job = _find_or_create_job(state, url, audio_length, prompt, mode)
    job.update(updates)
    state["last_run"] = _now_iso()
    _save_state(state_path, state)


def _get_active_jobs(state: dict) -> set[tuple[str, str]]:
    """生成中・video_ready・uploaded の (URL, mode) セットを返す."""
    return {
        (job["url"], job.get("mode", "podcast"))
        for job in state.get("jobs", [])
        if job.get("status") in ("generating", "video_ready", "uploaded")
    }


def _find_or_create_job(
    state: dict,
    url: str,
    audio_length: str,
    prompt: str,
    mode: str = "podcast",
) -> dict:
    """既存ジョブを探すか新規作成する."""
    for job in state["jobs"]:
        if job["url"] == url and job.get("mode", "podcast") == mode:
            # 再投入時の audio_length / prompt 指定変更を反映する
            job["audio_length"] = audio_length
            job["prompt"] = prompt
            job["mode"] = mode
            return job
    job: dict[str, Any] = {
        "url": url,
        "slug": _make_slug(url if mode == "podcast" else f"{mode}:{url}"),
        "mode": mode,
        "audio_length": audio_length,
        "prompt": prompt,
        "privacy_status": None,
        "status": "generating",
        "notebook_id": None,
        "task_id": None,
        "metadata": None,
        "extra_urls": [],
        "audio_path": None,
        "thumbnail_path": None,
        "video_path": None,
        "script_path": None,
        "upload_metadata": None,
        "upload_metadata_path": None,
        "image_profile_used": None,
        "background_paths": [],
        "youtube_url": None,
        "thumbnail_pending": False,
        "error": None,
        "submitted_at": None,
        "collected_at": None,
        "uploaded_at": None,
    }
    state["jobs"].append(job)
    return job


def _metadata_to_dict(metadata: PageMetadata) -> dict:
    """PageMetadata を dict に変換する."""
    return {
        "title": metadata.title,
        "description": metadata.description,
        "og_image_url": metadata.og_image_url,
        "site_name": metadata.site_name,
        "language": metadata.language,
        "favicon_url": metadata.favicon_url,
    }


def _dict_to_metadata(url: str, d: dict) -> PageMetadata:
    """dict から PageMetadata を復元する."""
    return PageMetadata(
        url=url,
        title=d["title"],
        description=d.get("description", ""),
        og_image_url=d.get("og_image_url"),
        site_name=d.get("site_name"),
        language=d.get("language"),
        favicon_url=d.get("favicon_url"),
    )


def _create_backend(settings: Settings) -> NotebookLMBackend:
    """設定に応じた NotebookLM バックエンドを生成する."""
    if settings.podcast.backend == "notebooklm-py":
        return NotebookLMPyBackend(
            poll_interval=settings.podcast.generation_poll_interval_seconds,
            timeout=settings.podcast.generation_timeout_seconds,
        )
    msg = f"Backend {settings.podcast.backend!r} is not yet implemented"
    raise NotImplementedError(msg)


# --- Phase 1: submit ---


async def _submit_single(
    entry: UrlEntry,
    settings: Settings,
    backend: NotebookLMBackend,
    state_path: Path,
    dry_run: bool,
) -> ProcessResult:
    """1つの URL に対して submit 処理を実行する."""
    url = entry.url
    slug = _make_slug(url)
    audio_length = entry.audio_length or settings.podcast.audio_length
    prompt_preset_name = entry.prompt or "default"
    privacy_status = entry.privacy_status or settings.youtube.privacy_status

    logger.info("Submitting: {} (slug={})", url, slug)

    # ソースの投入形式を決める（Spark はここで SSR 本文を抽出。取得失敗は
    # Fail Fast でジョブを failed にし、本文なしの空音声を作らせない）
    sources = entry.sources
    is_multi = len(sources) > 1
    is_local = is_local_path(url) and not is_multi
    resolved_sources: list[RemoteSource | ExtractedSource] = []
    if not is_local:
        resolved_sources = [
            await asyncio.to_thread(resolve_source, source_url)
            for source_url in sources
        ]

    # メタデータ取得（複数ソースはページ取得せず仮タイトル。最終タイトルは ② で生成）
    if is_multi:
        metadata = PageMetadata(
            url=url,
            title=entry.title or _MULTI_TITLE_PLACEHOLDER,
            description="",
            og_image_url=None,
            site_name=None,
            language=None,
            favicon_url=None,
        )
    elif is_local:
        tmp_dir = Path(settings.general.tmp_dir)
        metadata = metadata_for_local_file(Path(url), tmp_dir=tmp_dir)
    elif isinstance(resolved_sources[0], ExtractedSource):
        metadata = _metadata_for_extracted_source(url, resolved_sources[0])
    else:
        metadata = await fetch_metadata(url)

    if dry_run:
        # state.json には書き込まない。generating を書くと以後の本実行が
        # スキップされ、collect が notebook_id なしのジョブで失敗するため。
        logger.info("[DRY RUN] Would submit: {!r}", metadata.title)
        return ProcessResult(
            url=url,
            title=metadata.title,
            status="generating (dry-run)",
            phase="submit",
        )

    # 旧ノートブックが残っていれば掃除 (force 再実行・リトライ経路でのリーク防止)
    state = _load_state(state_path)
    existing = next((j for j in state["jobs"] if j["url"] == url), None)
    if existing and existing.get("notebook_id"):
        await _cleanup_notebook(backend, existing["notebook_id"])

    # ノートブック作成 → ID を即座に永続化 (クラッシュ時のオーファン追跡用)。
    # 旧 task_id を残すと collect の submit 中断検出ガードをすり抜けるためクリアする。
    notebook_id = await backend.create_notebook(f"Summary: {metadata.title}")
    _upsert_job_state(state_path, url, audio_length, prompt_preset_name, {
        "notebook_id": notebook_id,
        "task_id": None,
        "status": "generating",
        "privacy_status": privacy_status,
    })

    try:
        # ソース追加（複数ソースは同一ノートブックに全て追加。
        # 抽出済みソースは NotebookLM に URL を取得させずテキストで投入する）
        if is_local:
            await backend.add_file_source(notebook_id, Path(url))
        else:
            for source in resolved_sources:
                if isinstance(source, ExtractedSource):
                    await backend.add_text_source(
                        notebook_id, source.title, source.text
                    )
                else:
                    await backend.add_source(notebook_id, source.url)

        # プロンプト解決
        prompt_text = _resolve_prompt_preset(entry.prompt, settings)

        # 音声生成開始（完了を待たない）
        task_id = await backend.start_audio_generation(
            notebook_id,
            language=settings.podcast.audio_language,
            instructions=prompt_text,
            audio_length=audio_length,
        )
    except Exception:
        # create_notebook は通ったが後段が失敗 → 作成済みノートブックを掃除
        await _cleanup_notebook(backend, notebook_id)
        raise

    # state 更新
    _upsert_job_state(state_path, url, audio_length, prompt_preset_name, {
        "status": "generating",
        "task_id": task_id,
        "metadata": _metadata_to_dict(metadata),
        "extra_urls": entry.extra_urls,
        "user_title": entry.title,
        "privacy_status": privacy_status,
        "submitted_at": _now_iso(),
        "error": None,
    })

    return ProcessResult(
        url=url,
        title=metadata.title,
        status="generating",
        phase="submit",
    )


async def submit_urls(
    entries: list[UrlEntry],
    settings: Settings,
    force: bool = False,
    dry_run: bool = False,
) -> list[ProcessResult]:
    """Phase 1 を state.json の排他ロック下で実行する（二重実行防止）."""
    with pipeline_lock(Path(settings.general.state_file)):
        return await _submit_urls_locked(
            entries, settings, force=force, dry_run=dry_run
        )


async def _submit_urls_locked(
    entries: list[UrlEntry],
    settings: Settings,
    force: bool = False,
    dry_run: bool = False,
) -> list[ProcessResult]:
    """Phase 1: 生成方式ごとのジョブを開始可能な状態へ遷移させる。"""
    state_path = Path(settings.general.state_file)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = _load_state(state_path)
    active_jobs = _get_active_jobs(state)

    to_submit: list[UrlEntry] = []
    for entry in entries:
        if not force and (entry.url, entry.mode) in active_jobs:
            logger.info("Skipping already active: {} ({})", entry.url, entry.mode)
            continue
        to_submit.append(entry)

    if not to_submit:
        logger.info("No new URLs to submit")
        return []

    lecture_entries = [entry for entry in to_submit if entry.mode == "lecture"]
    notebook_entries = [entry for entry in to_submit if entry.mode == "podcast"]
    unknown_modes = {
        entry.mode
        for entry in to_submit
        if entry.mode not in ("lecture", "podcast")
    }
    if unknown_modes:
        raise ValueError(f"Unknown generation modes: {sorted(unknown_modes)}")

    results: list[ProcessResult] = []
    for entry in lecture_entries:
        audio_length = entry.audio_length or settings.podcast.audio_length
        prompt_preset_name = entry.prompt or "default"
        privacy_status = entry.privacy_status or settings.youtube.privacy_status
        if not dry_run:
            _upsert_job_state(
                state_path,
                entry.url,
                audio_length,
                prompt_preset_name,
                {
                    "status": "generating",
                    "notebook_id": None,
                    "task_id": None,
                    "metadata": None,
                    "video_path": None,
                    "thumbnail_path": None,
                    "script_path": None,
                    "upload_metadata": None,
                    "upload_metadata_path": None,
                    "youtube_url": None,
                    "privacy_status": privacy_status,
                    "submitted_at": _now_iso(),
                    "error": None,
                },
                mode="lecture",
            )
        results.append(
            ProcessResult(
                url=entry.url,
                status="generating (dry-run)" if dry_run else "generating",
                phase="submit",
            )
        )

    if not notebook_entries:
        return results

    backend = _create_backend(settings)

    async def _safe_submit(entry: UrlEntry) -> ProcessResult:
        try:
            return await _submit_single(
                entry, settings, backend, state_path, dry_run
            )
        except Exception as exc:
            if _is_notebooklm_auth_error(exc):
                logger.error(
                    "NotebookLM 認証エラー (url={}): {}",
                    entry.url,
                    _NOTEBOOKLM_AUTH_ERROR_MSG,
                )
                error_msg = _NOTEBOOKLM_AUTH_ERROR_MSG
            else:
                logger.error("Failed to submit {}: {}", entry.url, exc)
                error_msg = str(exc)
            # state にエラーを記録。notebook_id は破棄しない:
            # 掃除に失敗したノートブックが残っていても、リトライ時の
            # _submit_single 冒頭で best-effort 削除されるため。
            audio_length = entry.audio_length or settings.podcast.audio_length
            prompt_preset_name = entry.prompt or "default"
            _upsert_job_state(
                state_path, entry.url, audio_length, prompt_preset_name, {
                    "status": "failed",
                    "error": error_msg,
                    "submitted_at": _now_iso(),
                },
            )
            return ProcessResult(
                url=entry.url,
                status="failed",
                error=error_msg,
                phase="submit",
            )

    notebook_results = await asyncio.gather(
        *[_safe_submit(entry) for entry in notebook_entries]
    )
    results.extend(notebook_results)
    return results


# --- Phase 2: collect ---


async def _fail_collect_job(
    backend: NotebookLMBackend,
    state_path: Path,
    url: str,
    title: str | None,
    notebook_id: str | None,
    error_msg: str,
) -> ProcessResult:
    """collect 失敗を確定させる: ノートブック掃除 + failed 遷移."""
    if notebook_id:
        await _cleanup_notebook(backend, notebook_id)
    _update_job_state(state_path, url, {
        "status": "failed",
        "error": error_msg,
        "notebook_id": None,
    })
    return ProcessResult(
        url=url,
        title=title,
        status="failed",
        error=error_msg,
        phase="collect",
    )


async def _collect_single(
    job: dict,
    settings: Settings,
    backend: NotebookLMBackend,
    tmp_dir: Path,
    poll: bool,
    state_path: Path,
) -> ProcessResult:
    """1つのジョブに対して collect 処理を実行する."""
    url = job["url"]
    slug = job["slug"]
    notebook_id = job["notebook_id"]
    task_id = job["task_id"]
    title = job["metadata"]["title"] if job.get("metadata") else None

    logger.info("Collecting: {} (slug={})", url, slug)

    # submit が中断されたジョブは回収できない → 明示的に failed にする
    if not notebook_id or not task_id:
        return await _fail_collect_job(
            backend, state_path, url, title, notebook_id,
            "submit が完了していないため音声を回収できません。リトライしてください。",
        )

    # ステータスチェック
    gen_status = await backend.check_audio_status(notebook_id, task_id)

    if gen_status.status.upper() == "FAILED":
        # 生成失敗が確定 → ノートブックは再利用できない。
        # NOT_FOUND は作成直後の一時的な lag の可能性があるため (notebooklm-py の
        # is_not_found docstring 参照) 単発では terminal とみなさず、
        # wait_for_audio 側の連続判定 (5回連続 + 10秒) に委ねる。
        return await _fail_collect_job(
            backend, state_path, url, title, notebook_id,
            f"Audio generation failed: {gen_status.status}",
        )

    if gen_status.status.upper() != "COMPLETED":
        if not poll:
            logger.info("Audio still generating for {}, skipping (use --poll)", url)
            return ProcessResult(
                url=url,
                title=title,
                status="generating",
                phase="collect",
            )

        logger.info("Audio still generating, polling until completion...")
        try:
            gen_status = await backend.wait_for_audio(notebook_id, task_id)
        except (TimeoutError, NotebookLMNetworkError) as exc:
            # タイムアウトや一時的なネットワーク断は terminal ではない:
            # 生成は継続中の可能性があるため generating のまま残し、
            # 次回の collect で再試行できるようにする (ノートブックも残す)
            logger.warning(
                "Audio polling interrupted for {} (notebook={}): {};"
                " keeping status=generating for next collect",
                url, notebook_id, exc,
            )
            return ProcessResult(
                url=url,
                title=title,
                status="generating",
                phase="collect",
            )
        if gen_status.status.upper() != "COMPLETED":
            # 音声生成が terminal FAILED で確定 → ノートブックは再利用できない
            return await _fail_collect_job(
                backend, state_path, url, title, notebook_id,
                f"Audio generation failed: {gen_status.status}",
            )

    # 音声ダウンロード (以降の例外時の掃除と failed 遷移は _safe_collect が担う)
    audio_path = await backend.download_audio(
        notebook_id, output_path=tmp_dir / "audio" / f"{slug}.mp3"
    )

    # メタデータ復元
    metadata = _dict_to_metadata(url, job["metadata"])

    # メール系ソース: ノートブック削除前に chat で出典(件名/送信元/日付)を抽出し、
    # 件名をタイトルに反映する（生 URL を公開面に出さないため）。
    citation_dict = job.get("citation")
    if is_spark_share_url(url):
        citation = await _extract_email_citation(backend, notebook_id, url)
        if citation is not None:
            if citation.title:
                metadata.title = citation.title
            citation_dict = {
                "sender": citation.sender,
                "date": citation.date,
                "domain": citation.domain,
            }

    # タイトル: ユーザー指定(複数ソースの title)があれば尊重。無ければ ② で日本語生成
    # （失敗時は既存タイトルを維持）。
    user_title = job.get("user_title")
    if user_title:
        metadata.title = user_title
    else:
        jp_title = await _generate_japanese_title(
            backend, notebook_id, settings.youtube.generated_title_max_length
        )
        if jp_title:
            logger.info("生成タイトル: {!r} → {!r}", metadata.title, jp_title)
            metadata.title = jp_title

    # カテゴリ判定（③）: ルール第一、曖昧時のみ chat で内容判定（C）。
    # サムネ配色とプレイリスト振り分けに使う。
    category = await _refine_category(backend, notebook_id, classify_category(url))
    logger.info("カテゴリ判定: {} → {}", url, category)

    # 論文カテゴリなら通称（SAM/YOLO 等）を抽出し、タイトル先頭に【略称】を付与する。
    # 有名論文の解説を検索する学生・研究者に見つけてもらいやすくするため。略称は
    # サムネの主役ワード（top）にも反映する。略称の無い論文はそのまま。
    # ユーザーがタイトルを明示指定した場合は尊重し、抽出・付与しない。
    paper_shortname: str | None = None
    if category == "paper" and not user_title:
        paper_shortname = await _extract_paper_shortname(backend, notebook_id)
        # 既にタイトルに略称が含まれていれば二重に付けない
        # （先頭【】は検索用の略称が欠落している場合だけ付ける形式）。
        if paper_shortname and paper_shortname.lower() not in metadata.title.lower():
            metadata.title = f"【{paper_shortname}】{metadata.title}"
            logger.info("論文略称を付与: {}", paper_shortname)

    # サムネイル生成: 話題連動のベース画像を毎回 AI 生成し、その上に3層テキスト
    # （上=製品名/導入・中=説明・下=ベネフィット黄色特大＋数字）を Pillow 合成する。
    # AIには文字を描かせない（文字化け防止）。AI生成が失敗したら固定マスコット、
    # それも無ければグラデーションに縮退する。
    # 見出しは公開される面なので、ここでサニタイズする。
    headline = sanitize_public_text(metadata.title)
    style = style_for_category(category)
    thumbnail_path = tmp_dir / "thumbnails" / f"{slug}_thumb.png"
    thumb_copy = await _generate_thumb_copy(backend, notebook_id, category, headline)
    # 論文略称があれば主役ワード（top）を略称で固定し、縮小時も判別しやすくする。
    if paper_shortname and len(paper_shortname) <= _THUMB_TOP_MAX_LEN:
        thumb_copy = replace(thumb_copy, top=paper_shortname)

    # 通常 profile への退避は、このジョブの NotebookLM 操作を全て終えた後だけ行う。
    # Gemini 側の cookie ローテーションが NotebookLM の後続 RPC を壊す余地をなくすため、
    # ノートブックも画像生成より先に削除する。
    await backend.delete_notebook(notebook_id)
    _update_job_state(
        state_path,
        url,
        {"notebook_id": None, "audio_path": str(audio_path)},
    )
    image_storage_state_path = await _resolve_image_storage_state(settings)

    thumbnail_path = await _compose_topic_thumbnail(
        slug, tmp_dir, settings, headline, style, thumb_copy,
        thumbnail_path, metadata.site_name,
        storage_state_path=image_storage_state_path,
        ai_enabled=image_storage_state_path is not None,
    )

    # 動画背景も話題連動で AI 生成（best-effort、失敗時は静止背景に縮退）。
    # サムネのベース生成とは独立に試みる。
    background_paths: list[Path] = []
    if image_storage_state_path is not None:
        background_paths = await _generate_backgrounds(
            slug, tmp_dir, settings, audio_path,
            topic=headline, style=style,
            storage_state_path=image_storage_state_path,
        )
    video_path = await convert_to_video(
        audio_path=audio_path,
        thumbnail_path=thumbnail_path,
        output_path=tmp_dir / "videos" / f"{slug}.mp4",
        background_paths=background_paths,
    )

    # state 更新 (ディスクから再読込して競合を防ぐ)
    _update_job_state(state_path, url, {
        "status": "video_ready",
        "notebook_id": None,
        "metadata": _metadata_to_dict(metadata),
        "citation": citation_dict,
        "category": category,
        "audio_path": str(audio_path),
        "thumbnail_path": str(thumbnail_path),
        "image_profile_used": (
            image_storage_state_path.parent.name
            if image_storage_state_path is not None
            else None
        ),
        "background_paths": [str(path) for path in background_paths],
        "video_path": str(video_path),
        "collected_at": _now_iso(),
    })

    return ProcessResult(
        url=url,
        title=metadata.title,
        status="video_ready",
        phase="collect",
    )


async def _collect_lecture_single(
    job: dict,
    settings: Settings,
    state_path: Path,
) -> ProcessResult:
    """情報源から澪・透の講義動画と投稿用成果物を生成する。"""
    url = job["url"]
    logger.info("Generating Mio/Toru lecture: {}", url)
    artifacts: LectureArtifacts = await asyncio.to_thread(
        generate_lecture,
        url,
        Path(settings.general.tmp_dir) / "lecture",
        thumbnail_size=(settings.thumbnail.width, settings.thumbnail.height),
        thumbnail_backdrop_options=ThumbnailBackdropOptions(
            mode=settings.thumbnail.background_mode,
        ),
        script_model=settings.lecture.script_model,
        script_effort=settings.lecture.script_effort,
        review_model=settings.lecture.review_model,
        review_effort=settings.lecture.review_effort,
        generation_timeout_seconds=settings.lecture.generation_timeout_seconds,
    )
    metadata = {
        "title": artifacts.title,
        "description": artifacts.description,
        "og_image_url": None,
        "site_name": "澪と透の動画解説",
        "language": "ja",
        "favicon_url": None,
    }
    upload_metadata = {
        "title": artifacts.title,
        "description": artifacts.description,
        "tags": list(artifacts.tags),
        "thumbnail_text": list(artifacts.thumbnail_text),
        "thumbnail_background": artifacts.thumbnail_backdrop.as_metadata(),
        "script_generation": artifacts.script_generation,
    }
    _update_job_state(
        state_path,
        url,
        {
            "status": "video_ready",
            "metadata": metadata,
            "category": classify_category(url),
            "video_path": str(artifacts.video_path),
            "thumbnail_path": str(artifacts.thumbnail_path),
            "thumbnail_background_path": str(artifacts.thumbnail_backdrop.path),
            "thumbnail_background_prompt_path": str(
                artifacts.thumbnail_backdrop.prompt_path
            ),
            "script_path": str(artifacts.script_path),
            "upload_metadata": upload_metadata,
            "upload_metadata_path": str(artifacts.upload_metadata_path),
            "collected_at": _now_iso(),
            "error": None,
        },
        mode="lecture",
    )
    return ProcessResult(
        url=url,
        title=artifacts.title,
        status="video_ready",
        phase="collect",
    )


async def collect_audio(
    settings: Settings,
    poll: bool = False,
    timeout: int | None = None,
) -> list[ProcessResult]:
    """Phase 2 を state.json の排他ロック下で実行する（二重実行防止）."""
    with pipeline_lock(Path(settings.general.state_file)):
        return await _collect_audio_locked(settings, poll=poll, timeout=timeout)


async def _collect_audio_locked(
    settings: Settings,
    poll: bool = False,
    timeout: int | None = None,
) -> list[ProcessResult]:
    """Phase 2: 生成方式に応じて投稿可能な動画一式を完成させる。"""
    tmp_dir = Path(settings.general.tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    state_path = Path(settings.general.state_file)
    state = _load_state(state_path)

    generating_jobs = [
        job for job in state.get("jobs", []) if job.get("status") == "generating"
    ]

    if not generating_jobs:
        logger.info("No generating jobs to collect")
        return []

    lecture_jobs = [job for job in generating_jobs if job.get("mode") == "lecture"]
    notebook_jobs = [
        job for job in generating_jobs if job.get("mode", "podcast") == "podcast"
    ]
    results: list[ProcessResult] = []

    # Playwright・VOICEVOX・ffmpeg を同時に複数起動しないよう講義動画は直列生成する。
    for job in lecture_jobs:
        try:
            result = await _collect_lecture_single(job, settings, state_path)
        except Exception as exc:
            logger.exception("Failed to generate lecture {}: {}", job["url"], exc)
            error_msg = str(exc)
            _update_job_state(
                state_path,
                job["url"],
                {"status": "failed", "error": error_msg},
                mode="lecture",
            )
            result = ProcessResult(
                url=job["url"],
                status="failed",
                error=error_msg,
                phase="collect",
            )
        results.append(result)

    if not notebook_jobs:
        return results

    if timeout is not None:
        settings.podcast.generation_timeout_seconds = timeout

    backend = _create_backend(settings)

    async def _safe_collect(job: dict) -> ProcessResult:
        try:
            return await _collect_single(
                job, settings, backend, tmp_dir, poll, state_path
            )
        except Exception as exc:
            if _is_notebooklm_auth_error(exc):
                logger.error(
                    "NotebookLM 認証エラー (url={}): {}",
                    job["url"],
                    _NOTEBOOKLM_AUTH_ERROR_MSG,
                )
                error_msg = _NOTEBOOKLM_AUTH_ERROR_MSG
            else:
                logger.error("Failed to collect {}: {}", job["url"], exc)
                error_msg = str(exc)
            return await _fail_collect_job(
                backend,
                state_path,
                job["url"],
                job["metadata"]["title"] if job.get("metadata") else None,
                job.get("notebook_id"),
                error_msg,
            )

    notebook_results = await asyncio.gather(
        *[_safe_collect(job) for job in notebook_jobs]
    )
    results.extend(notebook_results)
    return results


# --- Phase 3: upload ---


def _video_id_from_url(youtube_url: str) -> str:
    """https://youtu.be/<id> から動画IDを取り出す."""
    return youtube_url.rstrip("/").rsplit("/", 1)[-1]


async def _reapply_pending_thumbnails(
    state_path: Path, creds: Credentials, jobs: list[dict]
) -> None:
    """サムネ未適用（thumbnail_pending）のアップロード済み動画を自己修復する.

    過去に 429（クォータ上限）でカスタムサムネが貼れなかった動画へ、後日クォータが
    回復したタイミングで再適用する。再び 429 を受けたらクォータ枯渇とみなして
    残りを打ち切る（次回のパイプライン実行でまた再開される・冪等）。
    """
    for job in jobs:
        thumb = job.get("thumbnail_path")
        if not thumb or not Path(thumb).exists():
            logger.warning(
                "サムネ再適用をスキップ（ファイル無し）: {}", job.get("youtube_url")
            )
            continue
        video_id = _video_id_from_url(job["youtube_url"])
        result = await set_thumbnail(creds, video_id, Path(thumb))
        if result == "ok":
            _update_job_state(
                state_path,
                job["url"],
                {"thumbnail_pending": False},
                mode=job.get("mode", "podcast"),
            )
            logger.info("サムネ再適用に成功: {}", job["youtube_url"])
        elif result == "quota":
            logger.warning("サムネ再適用: クォータ上限のため中断（次回リトライ）")
            break
        # "error": pending のまま残して次のジョブへ


async def upload_videos(
    settings: Settings,
    allow_interactive_auth: bool = True,
) -> list[ProcessResult]:
    """Phase 3 を state.json の排他ロック下で実行する（二重アップロード防止）."""
    with pipeline_lock(Path(settings.general.state_file)):
        return await _upload_videos_locked(
            settings, allow_interactive_auth=allow_interactive_auth
        )


async def _upload_videos_locked(
    settings: Settings,
    allow_interactive_auth: bool = True,
) -> list[ProcessResult]:
    """Phase 3: video_ready のジョブを YouTube にアップロードする.

    allow_interactive_auth=False (Web サーバー等) では、対話 OAuth フローを
    開始せずに認証失敗を failed として記録する。同期的な認証処理で
    イベントループをブロックしないよう to_thread でラップする。
    アップロード前に、過去にサムネ未適用のジョブを自己修復する。
    """
    state_path = Path(settings.general.state_file)
    state = _load_state(state_path)

    ready_jobs = [
        job for job in state.get("jobs", []) if job.get("status") == "video_ready"
    ]
    pending_thumb_jobs = [
        job
        for job in state.get("jobs", [])
        if job.get("status") == "uploaded"
        and job.get("thumbnail_pending")
        and job.get("youtube_url")
    ]

    if not ready_jobs and not pending_thumb_jobs:
        logger.info("No video_ready jobs to upload")
        return []

    # YouTube 認証（1回）
    try:
        creds = await asyncio.to_thread(
            authenticate,
            client_secret_path=Path(settings.credentials.youtube_client_secret),
            token_path=Path(settings.credentials.youtube_token),
            allow_interactive=allow_interactive_auth,
        )
    except Exception as exc:
        if allow_interactive_auth:
            # CLI コンテキストでは Fail Fast でそのままクラッシュさせる
            raise
        # Web コンテキストではジョブを failed にして UI にエラーを可視化する。
        # 動画ファイルは video_path に残るため、リトライでアップロードのみ再試行できる。
        logger.error("YouTube authentication failed: {}", exc)
        results = []
        for job in ready_jobs:
            _update_job_state(
                state_path,
                job["url"],
                {
                    "status": "failed",
                    "error": str(exc),
                },
                mode=job.get("mode", "podcast"),
            )
            results.append(
                ProcessResult(
                    url=job["url"],
                    title=job["metadata"]["title"] if job.get("metadata") else None,
                    status="failed",
                    error=str(exc),
                    phase="upload",
                )
            )
        return results

    # 自己修復: 過去にサムネ未適用（429等）のジョブを先に再適用する
    if pending_thumb_jobs:
        logger.info("サムネ未適用 {} 件を再適用します", len(pending_thumb_jobs))
        await _reapply_pending_thumbnails(state_path, creds, pending_thumb_jobs)

    results: list[ProcessResult] = []
    daily_limit = settings.youtube.daily_upload_limit

    for i, job in enumerate(ready_jobs):
        if i >= daily_limit:
            logger.warning(
                "Daily upload limit ({}) reached, stopping", daily_limit
            )
            break

        url = job["url"]
        try:
            metadata = _dict_to_metadata(url, job["metadata"])
            category = job.get("category")
            is_lecture = job.get("mode") == "lecture"

            if is_lecture:
                upload_metadata = job.get("upload_metadata")
                if not isinstance(upload_metadata, dict):
                    raise RuntimeError("講義動画の投稿情報がありません")
                raw_title = str(upload_metadata.get("title", "")).strip()
                description = str(upload_metadata.get("description", "")).strip()
                raw_tags = upload_metadata.get("tags")
                if not raw_title or not description or not isinstance(raw_tags, list):
                    raise RuntimeError("講義動画の投稿情報が不完全です")
                title = _sanitize_youtube_title(raw_title)
                max_len = settings.youtube.title_max_length
                if len(title) > max_len:
                    title = title[: max_len - 1] + "…"
                tags = [str(tag) for tag in raw_tags]
            else:
                citation_data = job.get("citation")
                citation = EmailCitation(**citation_data) if citation_data else None
                description = _build_description(
                    metadata,
                    citation=citation,
                    category=category,
                    extra_urls=job.get("extra_urls", []),
                )
                title = _build_title(metadata, settings)
                tags = settings.youtube.default_tags

            # ③ カテゴリ→プレイリスト解決（無ければ既定 playlist_id へフォールバック）
            # ＋ all_playlist_id（全動画横断）を常に追加
            playlist_ids = resolve_playlist_ids(
                category,
                settings.youtube.playlists,
                settings.youtube.playlist_id,
                settings.youtube.all_playlist_id,
            )

            # 簡易動画モードではカスタムサムネを設定しない（429 でサムネ上限の
            # 24h ローリングをリセットしないため。動画には静止背景が入る）
            upload_thumb = (
                None
                if settings.general.simple_video_mode and not is_lecture
                else Path(job["thumbnail_path"])
            )
            params = YouTubeUploadParams(
                file_path=Path(job["video_path"]),
                title=title,
                description=description,
                tags=tags,
                category_id=settings.youtube.category_id,
                privacy_status=(
                    job.get("privacy_status") or settings.youtube.privacy_status
                ),
                thumbnail_path=upload_thumb,
                playlist_ids=playlist_ids,
            )

            result = await upload_video(creds, params)

            # サムネが 429 等で貼れなかった場合は thumbnail_pending を立て、
            # 次回のアップロード時に _reapply_pending_thumbnails で自己修復する
            _update_job_state(
                state_path,
                url,
                {
                    "status": "uploaded",
                    "youtube_url": result.youtube_url,
                    "thumbnail_pending": not result.thumbnail_set,
                    "uploaded_at": _now_iso(),
                },
                mode=job.get("mode", "podcast"),
            )
            if not result.thumbnail_set:
                logger.warning(
                    "サムネ未適用のまま公開（次回再適用予定）: {}", result.youtube_url
                )

            results.append(
                ProcessResult(
                    url=url,
                    title=metadata.title,
                    youtube_url=result.youtube_url,
                    status="uploaded",
                    phase="upload",
                )
            )
        except Exception as exc:
            logger.error("Failed to upload {}: {}", url, exc)
            _update_job_state(
                state_path,
                url,
                {
                    "status": "failed",
                    "error": str(exc),
                },
                mode=job.get("mode", "podcast"),
            )
            results.append(
                ProcessResult(
                    url=url,
                    title=job["metadata"]["title"] if job.get("metadata") else None,
                    status="failed",
                    error=str(exc),
                    phase="upload",
                )
            )

    return results


# --- 既存互換: process_single_url + run_pipeline ---


async def process_single_url(
    entry: UrlEntry,
    settings: Settings,
    backend: NotebookLMBackend,
    tmp_dir: Path,
    creds: Credentials | None,
    dry_run: bool = False,
) -> ProcessResult:
    """1 URL の処理パイプラインを実行する（後方互換: run-single 用）."""
    slug = _make_slug(entry.url)
    logger.info("Processing: {} (slug={})", entry.url, slug)

    # 1. ソースの投入形式を決め、メタデータを取得
    is_local = is_local_path(entry.url)
    resolved: RemoteSource | ExtractedSource | None = None
    if is_local:
        metadata = metadata_for_local_file(Path(entry.url), tmp_dir=tmp_dir)
    else:
        resolved = await asyncio.to_thread(resolve_source, entry.url)
        if isinstance(resolved, ExtractedSource):
            metadata = _metadata_for_extracted_source(entry.url, resolved)
        else:
            metadata = await fetch_metadata(entry.url)

    if dry_run:
        logger.info("[DRY RUN] Would process: {!r}", metadata.title)
        return ProcessResult(
            url=entry.url, title=metadata.title, status="success (dry-run)"
        )

    # 2. NotebookLM でノートブック作成
    notebook_id = await backend.create_notebook(f"Summary: {metadata.title}")

    # 3. ソース追加（抽出済みソースはテキストで投入）
    if is_local:
        await backend.add_file_source(notebook_id, Path(entry.url))
    elif isinstance(resolved, ExtractedSource):
        await backend.add_text_source(notebook_id, resolved.title, resolved.text)
    else:
        await backend.add_source(notebook_id, entry.url)

    # 4. プロンプト解決
    prompt_text = _resolve_prompt_preset(entry.prompt, settings)

    # 5. audio_length 解決
    audio_length = entry.audio_length or settings.podcast.audio_length

    # 6. Audio Overview 生成
    await backend.generate_audio(
        notebook_id,
        language=settings.podcast.audio_language,
        instructions=prompt_text,
        audio_length=audio_length,
    )

    # 7. 音声ダウンロード
    audio_path = await backend.download_audio(
        notebook_id, output_path=tmp_dir / "audio" / f"{slug}.mp3"
    )

    # 8. サムネイル生成（話題連動AIベース画像 + 高密度3層テキストの Pillow 合成。
    #    AI生成失敗時は固定マスコット→グラデーションに縮退）
    category = classify_category(entry.url)
    # 論文カテゴリなら通称（SAM/YOLO 等）を抽出し、タイトル先頭に【略称】を付与する。
    paper_shortname: str | None = None
    if category == "paper":
        paper_shortname = await _extract_paper_shortname(backend, notebook_id)
        # 既にタイトルに略称が含まれていれば二重に付けない
        # （先頭【】は検索用の略称が欠落している場合だけ付ける形式）。
        if paper_shortname and paper_shortname.lower() not in metadata.title.lower():
            metadata.title = f"【{paper_shortname}】{metadata.title}"
            logger.info("論文略称を付与: {}", paper_shortname)
    headline = sanitize_public_text(metadata.title)
    thumbnail_path = tmp_dir / "thumbnails" / f"{slug}_thumb.png"
    style = style_for_category(category)
    thumb_copy = await _generate_thumb_copy(backend, notebook_id, category, headline)
    # 論文略称があれば主役ワード（top）を略称で固定し、縮小時も判別しやすくする。
    if paper_shortname and len(paper_shortname) <= _THUMB_TOP_MAX_LEN:
        thumb_copy = replace(thumb_copy, top=paper_shortname)

    # 画像生成が通常 profile へ退避しても、以後 NotebookLM RPC は発生しない順序にする。
    await backend.delete_notebook(notebook_id)
    image_storage_state_path = await _resolve_image_storage_state(settings)

    thumbnail_path = await _compose_topic_thumbnail(
        slug, tmp_dir, settings, headline, style, thumb_copy,
        thumbnail_path, metadata.site_name,
        storage_state_path=image_storage_state_path,
        ai_enabled=image_storage_state_path is not None,
    )

    # 9. 動画変換（話題連動のAI背景があれば背景ローテーション、無ければ静止背景）。
    # サムネのベース生成とは独立に試みる。
    background_paths: list[Path] = []
    if image_storage_state_path is not None:
        background_paths = await _generate_backgrounds(
            slug, tmp_dir, settings, audio_path,
            topic=headline, style=style,
            storage_state_path=image_storage_state_path,
        )
    video_path = await convert_to_video(
        audio_path=audio_path,
        thumbnail_path=thumbnail_path,
        output_path=tmp_dir / "videos" / f"{slug}.mp4",
        background_paths=background_paths,
    )

    # 10. YouTube アップロード
    description = _build_description(metadata, category=category)
    title = _build_title(metadata, settings)

    params = YouTubeUploadParams(
        file_path=video_path,
        title=title,
        description=description,
        tags=settings.youtube.default_tags,
        category_id=settings.youtube.category_id,
        privacy_status=settings.youtube.privacy_status,
        # 簡易動画モードはカスタムサムネを設定しない（429 回避）
        thumbnail_path=None if settings.general.simple_video_mode else thumbnail_path,
        playlist_ids=resolve_playlist_ids(
            category,
            settings.youtube.playlists,
            settings.youtube.playlist_id,
            settings.youtube.all_playlist_id,
        ),
    )

    result = await upload_video(creds, params)

    return ProcessResult(
        url=entry.url,
        title=metadata.title,
        youtube_url=result.youtube_url,
        status="success",
    )


async def run_pipeline(
    entries: list[UrlEntry],
    settings: Settings,
    dry_run: bool = False,
    force: bool = False,
    retry_failed: bool = False,
    allow_interactive_auth: bool = True,
) -> list[ProcessResult]:
    """3フェーズ全体を state.json の排他ロック下で実行する（二重実行防止）.

    ロックは同一プロセス内では再入可能なので、この中から呼ぶ submit / collect /
    upload は素通しで動く。
    """
    with pipeline_lock(Path(settings.general.state_file)):
        return await _run_pipeline_locked(
            entries,
            settings,
            dry_run=dry_run,
            force=force,
            retry_failed=retry_failed,
            allow_interactive_auth=allow_interactive_auth,
        )


async def _run_pipeline_locked(
    entries: list[UrlEntry],
    settings: Settings,
    dry_run: bool = False,
    force: bool = False,
    retry_failed: bool = False,
    allow_interactive_auth: bool = True,
) -> list[ProcessResult]:
    """パイプライン全体を実行する（3フェーズ統合）."""
    if retry_failed:
        # retry_failed は旧互換: failed のジョブを generating にリセットして再実行
        state_path = Path(settings.general.state_file)
        state = _load_state(state_path)
        failed_jobs = {
            (job["url"], job.get("mode", "podcast"))
            for job in state.get("jobs", [])
            if job.get("status") == "failed"
        }
        entries = [e for e in entries if (e.url, e.mode) in failed_jobs]
        if not entries:
            logger.info("No failed URLs to retry")
            return []
        force = True

    all_results: list[ProcessResult] = []

    # Phase 1: submit
    submit_results = await submit_urls(
        entries, settings, force=force, dry_run=dry_run
    )
    all_results.extend(submit_results)

    if dry_run:
        return all_results

    # Phase 2: collect (poll=True で完了まで待機)
    collect_results = await collect_audio(settings, poll=True)
    all_results.extend(collect_results)

    # Phase 3: upload
    upload_results = await upload_videos(
        settings, allow_interactive_auth=allow_interactive_auth
    )
    all_results.extend(upload_results)

    return all_results


def get_status_counts(settings: Settings) -> dict[str, int]:
    """各ステータスのジョブ数を返す."""
    state_path = Path(settings.general.state_file)
    state = _load_state(state_path)
    counts: dict[str, int] = {}
    for job in state.get("jobs", []):
        status = job.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts
