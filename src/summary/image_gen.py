"""AI画像生成 (gemini-webapi / Nano Banana).

NotebookLM と同一アカウントの cookie (storage_state.json) を用いて、サムネ用の
文字なしベース画像（方式A2、文字は thumbnail.compose_thumbnail が Pillow で合成）と
動画背景用の話題関連画像を生成する。非公式 API のため失敗し得る
（cookie 切れ・地域制限・画像が返らない等）。失敗時は None を返し、呼び出し側が
フォールバック（グラデーションサムネ / 静止背景）に切り替える。
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from PIL import Image

# NotebookLM が保存している Google ログイン cookie の場所（同一アカウント）
_DEFAULT_STORAGE_STATE = (
    Path.home() / ".notebooklm" / "profiles" / "default" / "storage_state.json"
)


def storage_state_for_profile(profile: str) -> Path:
    """notebooklm プロファイル名から storage_state.json のパスを返す."""
    return (
        Path.home() / ".notebooklm" / "profiles" / profile / "storage_state.json"
    )
_GOOGLE_DOMAIN = ".google.com"

# gemini-webapi がローテート済み 1PSIDTS を保存するキャッシュ先。
# 既定の $TMPDIR は macOS が定期削除するため、gitignore 済みの credentials/ 配下に置く。
_GEMINI_COOKIE_CACHE_DIR = Path("credentials") / "gemini_cookie_cache"

# 画像生成の直列化ロック。複数ジョブの collect が並列でも Nano Banana へは
# 1リクエストずつ送る（同時実行はタイムアウトと cookie ローテーションの競合を招く）
_GENERATION_LOCK = asyncio.Lock()


@dataclass(frozen=True)
class ThumbnailStyle:
    """サムネのブランド統一用スタイル（カテゴリ別に差し替え可能）."""

    name: str = "default"
    palette: str = (
        "deep navy to electric blue gradient with subtle glowing circuit patterns"
    )
    motif: str = "a luminous abstract AI neural network glowing on the right side"
    text_color: str = "vivid gold (#FFD24A)"
    accent: str = "#D7263D"  # バナー帯など Pillow 合成で使う hex カラー


DEFAULT_STYLE = ThumbnailStyle()


def load_google_cookies(
    storage_state_path: Path | None = None,
) -> tuple[str | None, str | None]:
    """storage_state.json から __Secure-1PSID / __Secure-1PSIDTS を読む.

    値そのものはログに出さない（秘匿情報）。見つからなければ (None, None)。
    """
    path = storage_state_path or _DEFAULT_STORAGE_STATE
    if not path.exists():
        logger.warning("storage_state.json が見つかりません: {}", path)
        return None, None
    data = json.loads(path.read_text(encoding="utf-8"))
    psid = psidts = None
    for cookie in data.get("cookies", []):
        if cookie.get("domain") != _GOOGLE_DOMAIN:
            continue
        if cookie.get("name") == "__Secure-1PSID":
            psid = cookie.get("value")
        elif cookie.get("name") == "__Secure-1PSIDTS":
            psidts = cookie.get("value")
    return psid, psidts


def build_thumbnail_base_prompt(
    topic: str,
    style: ThumbnailStyle = DEFAULT_STYLE,
    pose: str | None = None,
) -> str:
    """方式A2: 固定マスコットのキャラを保ちつつ、ポーズ・小物・色を話題ごとに変える.

    マスコット画像を参照画像(files=)として渡す前提のプロンプト。キャラの同一性
    （姿・色・画風）と「大きな驚き顔」は維持するが、**ポーズ・前景の大きな小道具・
    配色は話題ごとに変える**（縮小時にも各動画が見分けられるようにする）。pose を
    渡すとその動的ポーズを指定する（ローテーションで動画ごとに絵柄を散らす）。
    マスコットは右側に大きく、左1/3は暗く空けて文字用に確保。文字は一切描かせない
    （見出しは Pillow 側で合成）。参照画像が無い場合もキャラ特徴は本文に書いておく。
    """
    pose_part = (
        f"Give the mascot this NEW dynamic pose (do NOT copy the reference pose): "
        f"{pose}. "
        if pose
        else "Give the mascot a NEW dynamic pose reacting to the topic "
        "(do NOT copy the reference pose). "
    )
    return (
        "Use the attached cartoon robot mascot as the character reference. "
        "Keep the SAME character identity: a glossy lavender-white robot with two "
        "ball-tipped antennae, big glowing cyan circular eyes, an expressive "
        "shocked face, bold comic art style. "
        f"{pose_part}"
        f"Scene topic: 「{topic}」. Include ONE big, bold, instantly-recognizable "
        "object representing the topic in the FOREGROUND, large enough to read at "
        "small thumbnail size. "
        f"Use a vibrant, high-contrast color mood themed to the topic (you may use "
        f"{style.palette} as an accent). "
        "16:9 YouTube thumbnail: the mascot and its prop fill the RIGHT ~65% with "
        "an exaggerated SURPRISED expression; keep the LEFT third darker and "
        "simple for large text overlay — do NOT place the mascot's face in the "
        "left third. Bold outlines, cartoon style. "
        "Absolutely NO text, NO letters, NO numbers, NO logos anywhere. "
        "No watermark, no UI elements."
    )


def _resize_cover(img: Image.Image, width: int, height: int) -> Image.Image:
    """アスペクト比維持でリサイズ＋中央クロップして width×height に整形する."""
    img = img.convert("RGB")
    ratio = max(width / img.width, height / img.height)
    new_size = (round(img.width * ratio), round(img.height * ratio))
    img = img.resize(new_size, Image.LANCZOS)
    left = (img.width - width) // 2
    top = (img.height - height) // 2
    return img.crop((left, top, left + width, top + height))


async def _close_client(client: object) -> None:
    """GeminiClient を best-effort でクローズする."""
    close = getattr(client, "close", None)
    if not close:
        return
    try:
        result = close()
        if asyncio.iscoroutine(result):
            await result
    except Exception:  # noqa: BLE001 - クローズ失敗は無視してよい
        pass


def build_background_prompt(
    style: ThumbnailStyle = DEFAULT_STYLE,
    topic: str | None = None,
    variation: str | None = None,
) -> str:
    """動画の背景ローテーション用: テキストなし背景画像の生成プロンプトを作る.

    topic（動画の日本語タイトル等）を渡すと話題に関連した画像になる。
    variation は構図のヒント（複数枚生成時に同じ絵にならないようにする）。
    """
    topic_part = (
        "The imagery must clearly and concretely relate to this topic: "
        f"「{topic}」. Depict the subject matter itself, not generic tech decoration. "
        if topic
        else ""
    )
    variation_part = f"Composition: {variation}. " if variation else ""
    return (
        "Create a professional, atmospheric 16:9 background image for a "
        "tech podcast video segment. "
        f"{topic_part}"
        f"{variation_part}"
        f"Color mood: {style.palette}. "
        "Absolutely NO text, NO letters, NO numbers, NO logos anywhere — "
        "do not write the topic words in the image. Avoid diagrams, charts and "
        "labeled screens; if any screen or document appears, its contents must "
        "be abstract blurred shapes with no readable characters. "
        "High contrast, cinematic lighting. No watermark, no UI elements."
    )


async def generate_thumbnail_image(
    topic: str,
    output_path: Path,
    *,
    width: int,
    height: int,
    style: ThumbnailStyle = DEFAULT_STYLE,
    reference_image: Path | None = None,
    pose: str | None = None,
    storage_state_path: Path | None = None,
    timeout: float = 360.0,
) -> Path | None:
    """Nano Banana でサムネ用の文字なしベース画像を生成し PNG 保存する.

    reference_image（固定マスコット）を渡すと、そのキャラを保ったままポーズ・小物・
    配色を話題に合わせて変える（キャラ同一性は Nano Banana の image editing に委ねる）。
    pose を渡すと動的ポーズを指定する（動画ごとの絵柄の散らしに使う）。
    文字は呼び出し側が Pillow（`thumbnail.compose_thumbnail`）で合成する。
    失敗時は None を返す（呼び出し側がマスコット/グラデーションに縮退）。
    """
    logger.info("AIサムネベース生成中 (topic={!r})", topic)
    return await _generate_image(
        build_thumbnail_base_prompt(topic, style, pose),
        output_path,
        width=width,
        height=height,
        reference_image=reference_image,
        storage_state_path=storage_state_path,
        timeout=timeout,
        label=f"topic={topic!r}",
    )


async def generate_background_image(
    output_path: Path,
    *,
    width: int,
    height: int,
    style: ThumbnailStyle = DEFAULT_STYLE,
    topic: str | None = None,
    variation: str | None = None,
    storage_state_path: Path | None = None,
    timeout: float = 360.0,
) -> Path | None:
    """Nano Banana でテキストなし・話題関連の背景画像を生成する.

    失敗時は None を返す（呼び出し側は背景なし＝静止背景に縮退）。
    """
    logger.info(
        "AI背景生成中 (style={}, topic={!r}, variation={!r})",
        style.name, topic, variation,
    )
    return await _generate_image(
        build_background_prompt(style, topic=topic, variation=variation),
        output_path,
        width=width,
        height=height,
        storage_state_path=storage_state_path,
        timeout=timeout,
        label=f"style={style.name} topic={topic!r}",
    )


async def _generate_image(
    prompt: str,
    output_path: Path,
    *,
    width: int,
    height: int,
    storage_state_path: Path | None,
    timeout: float,
    label: str,
    reference_image: Path | None = None,
) -> Path | None:
    """Nano Banana でプロンプトから画像を生成し整形して保存する共通コア.

    reference_image を渡すと参照画像(files=)として添付する（キャラ固定等）。
    プロセス内で直列化される（_GENERATION_LOCK）。
    """
    async with _GENERATION_LOCK:
        return await _generate_image_locked(
            prompt,
            output_path,
            width=width,
            height=height,
            storage_state_path=storage_state_path,
            timeout=timeout,
            label=label,
            reference_image=reference_image,
        )


async def _generate_image_locked(
    prompt: str,
    output_path: Path,
    *,
    width: int,
    height: int,
    storage_state_path: Path | None,
    timeout: float,
    label: str,
    reference_image: Path | None = None,
) -> Path | None:
    try:
        psid, psidts = load_google_cookies(storage_state_path)
    except Exception as exc:  # noqa: BLE001 - 壊れた cookie ファイルでも縮退する
        logger.warning("AI画像: cookie 読み込みに失敗: {}", exc)
        return None
    if not psid:
        logger.warning("AI画像: Google cookie が無いためスキップ")
        return None

    try:
        from gemini_webapi import GeminiClient
        from gemini_webapi.constants import AccountStatus
        from gemini_webapi.utils import rotate_1psidts
    except ImportError:
        logger.warning("AI画像: gemini-webapi 未インストール")
        return None

    os.environ.setdefault("GEMINI_COOKIE_PATH", str(_GEMINI_COOKIE_CACHE_DIR))

    client = None
    raw_path: Path | None = None
    try:
        client = GeminiClient(psid, psidts)
        await client.init(timeout=60)
        if client.account_status != AccountStatus.AVAILABLE:
            logger.warning(
                "AI画像: Gemini セッション未認証 ({}) — "
                "`uv run notebooklm login` で cookie を更新してください",
                client.account_status.name,
            )
            return None
        # クライアントは生成のたびに使い捨てるため auto_refresh の周期には届かない。
        # 認証確認後に 1PSIDTS を即時ローテートして永続キャッシュへ保存しておくと、
        # storage_state.json 側が失効しても次回 init はキャッシュ側で認証できる。
        try:
            await rotate_1psidts(client.client)
        except Exception as exc:  # noqa: BLE001 - ローテート失敗は生成継続に影響しない
            logger.warning("AI画像: cookie ローテートに失敗（生成は継続）: {}", exc)
        files = None
        if reference_image is not None and reference_image.exists():
            files = [str(reference_image)]
        resp = await asyncio.wait_for(
            client.generate_content(prompt, files=files), timeout=timeout
        )
        images = list(getattr(resp, "images", None) or [])
        if not images:
            # 画像が返らない主因は cookie 失効(PSIDTS)/地域・アカウント制限。
            # 応答テキストに理由が出るので残す（要 notebooklm login で cookie 更新）。
            reason = (getattr(resp, "text", "") or "")[:200]
            logger.warning(
                "AI画像: 画像が返らなかった {} reason={!r}", label, reason
            )
            return None
        output_path.parent.mkdir(parents=True, exist_ok=True)
        saved = await images[0].save(
            path=str(output_path.parent),
            filename=f"{output_path.stem}_airaw.png",
            verbose=False,
        )
        raw_path = Path(saved)
    except Exception as exc:  # noqa: BLE001 - 非公式APIの失敗は握って縮退する
        logger.warning("AI画像生成に失敗 ({}): {}: {}", label, type(exc).__name__, exc)
        return None
    finally:
        if client is not None:
            await _close_client(client)

    # 生成画像を 1280×720 等に整形して PNG 保存
    try:
        with Image.open(raw_path) as img:
            shaped = _resize_cover(img, width, height)
        shaped.save(str(output_path), "PNG", optimize=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI画像整形に失敗 ({}): {}", label, exc)
        return None
    finally:
        if raw_path and raw_path.exists() and raw_path != output_path:
            raw_path.unlink(missing_ok=True)

    logger.info("AI画像生成完了: {}", output_path)
    return output_path
