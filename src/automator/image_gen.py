"""AIサムネ画像生成 (gemini-webapi / Nano Banana).

NotebookLM と同一アカウントの cookie (storage_state.json) を用いて、日本語見出しを
焼き込んだ 16:9 サムネを生成する（方式A）。非公式 API のため失敗し得る
（cookie 切れ・地域制限・画像が返らない等）。失敗時は None を返し、呼び出し側は
Pillow グラデーションのフォールバックに切り替える。OGP 画像の流用は行わない。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from PIL import Image

# NotebookLM が保存している Google ログイン cookie の場所（同一アカウント）
_DEFAULT_STORAGE_STATE = (
    Path.home() / ".notebooklm" / "profiles" / "default" / "storage_state.json"
)
_GOOGLE_DOMAIN = ".google.com"


@dataclass(frozen=True)
class ThumbnailStyle:
    """サムネのブランド統一用スタイル（カテゴリ別に差し替え可能）."""

    name: str = "default"
    palette: str = (
        "deep navy to electric blue gradient with subtle glowing circuit patterns"
    )
    motif: str = "a luminous abstract AI neural network glowing on the right side"
    text_color: str = "vivid gold (#FFD24A)"


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


def build_thumbnail_prompt(
    headline: str, style: ThumbnailStyle = DEFAULT_STYLE
) -> str:
    """方式A: 日本語見出しを正確に焼き込むサムネ生成プロンプトを作る."""
    return (
        "Create a professional, eye-catching 16:9 YouTube thumbnail. "
        f"Background: {style.palette}. Visual motif: {style.motif}. "
        "Leave the left ~60% open for a bold headline. "
        "Render this EXACT Japanese headline text on the left, very large, bold, "
        f"highly legible, {style.text_color} with a thick dark outline: 「{headline}」 "
        "Reproduce every Japanese character exactly with no typos, no garbled or "
        "invented glyphs, and add no other text anywhere. "
        "High contrast, cinematic lighting. No watermark, no logos, no UI elements."
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


async def generate_thumbnail_image(
    headline: str,
    output_path: Path,
    *,
    width: int,
    height: int,
    style: ThumbnailStyle = DEFAULT_STYLE,
    storage_state_path: Path | None = None,
    timeout: float = 120.0,
) -> Path | None:
    """Nano Banana で見出し入りサムネを生成し output_path(PNG) に保存する.

    失敗時は None を返す（呼び出し側が Pillow フォールバックに切替）。
    """
    try:
        psid, psidts = load_google_cookies(storage_state_path)
    except Exception as exc:  # noqa: BLE001 - 壊れた cookie ファイルでも縮退する
        logger.warning("AIサムネ: cookie 読み込みに失敗: {}", exc)
        return None
    if not psid:
        logger.warning("AIサムネ: Google cookie が無いためスキップ")
        return None

    try:
        from gemini_webapi import GeminiClient
    except ImportError:
        logger.warning("AIサムネ: gemini-webapi 未インストール")
        return None

    client = None
    raw_path: Path | None = None
    try:
        client = GeminiClient(psid, psidts)
        await client.init(timeout=60)
        prompt = build_thumbnail_prompt(headline, style)
        logger.info("AIサムネ生成中 (headline={!r})", headline)
        resp = await asyncio.wait_for(
            client.generate_content(prompt), timeout=timeout
        )
        images = list(getattr(resp, "images", None) or [])
        if not images:
            # 画像が返らない主因は cookie 失効(PSIDTS)/地域・アカウント制限。
            # 応答テキストに理由が出るので残す（要 notebooklm login で cookie 更新）。
            reason = (getattr(resp, "text", "") or "")[:200]
            logger.warning(
                "AIサムネ: 画像が返らなかった headline={!r} reason={!r}",
                headline, reason,
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
        logger.warning("AIサムネ生成に失敗: {}: {}", type(exc).__name__, exc)
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
        logger.warning("AIサムネ整形に失敗: {}", exc)
        return None
    finally:
        if raw_path and raw_path.exists() and raw_path != output_path:
            raw_path.unlink(missing_ok=True)

    logger.info("AIサムネ生成完了: {}", output_path)
    return output_path
