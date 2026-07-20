"""オリジナルキャラ生成 (Nano Banana / gemini-webapi)。口閉じ→口開き差分の2段生成。

使い方 (リポジトリルートで):
    GEMINI_COOKIE_PATH=credentials/gemini_cookie_cache \
      uv run --frozen --with gemini-webapi python scripts/gen_lecture_characters.py
    # 生成後: rembg (isnet-anime) で透過化して assets/characters/ へ置く
    # (specs/LECTURE_SPEC.md §3.4 参照)。cookie 失効時は
    # `uv run notebooklm login --profile imagegen` で更新する。
"""

import asyncio
import json
import os
import sys
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parents[1] / "tmp" / "character_gen"
STORAGE_STATE = (
    Path.home() / ".notebooklm" / "profiles" / "imagegen" / "storage_state.json"
)
COOKIE_CACHE = Path("credentials") / "gemini_cookie_cache"

STYLE = (
    "clean flat anime illustration, soft cel shading, rounded friendly character "
    "design, bright cheerful colors, crisp lineart, high resolution. "
    "Plain solid pure white background (#FFFFFF), full flat white, no shadows on "
    "the background. Absolutely NO text, NO letters, NO logos, NO watermark."
)

CHARS = {
    "metan": (
        "Bust-up portrait (head to waist) of an original anime woman character: "
        "a beautiful school nurse (hokenshitsu health teacher) in her late 20s "
        "with a calm, mature, subtly alluring adult charm. Long dark navy-brown "
        "hair tied loosely and draped over one shoulder, warm dark-brown gently "
        "narrowed eyes with long lashes, a small beauty mark under one eye, "
        "soft confident smile with CLOSED mouth. She wears a white lab coat "
        "over a fitted dusty-pink blouse showing an elegant feminine "
        "silhouette. Tasteful, fully clothed, professional. "
        "Body angled slightly toward the viewer's right, face toward the viewer. "
    ),
    "zunda": (
        "Bust-up portrait (head to waist) of an original anime boy character: "
        "a pure-hearted, unsophisticated country-boy type Japanese elementary "
        "school boy around 11 years old. Plain, slightly bowl-cut messy "
        "black-brown hair, big round warm brown eyes full of curiosity, light "
        "freckles on his cheeks, bashful earnest smile with CLOSED mouth. "
        "He wears a plain, slightly oversized green zip-up hoodie over a simple "
        "white T-shirt, a little unfashionable and homely. "
        "Body angled slightly toward the viewer's left, face toward the viewer. "
    ),
}

OPEN_MOUTH_PROMPT = (
    "Edit the attached character image. Change ONLY the mouth: make it clearly "
    "OPEN as if the character is talking cheerfully (visible open mouth). "
    "Keep EVERYTHING "
    "else pixel-identical: same pose, same hair, same eyes, same clothes, same "
    "colors, same framing, same plain solid white background. "
    "Absolutely NO text, NO letters, NO watermark."
)


def load_cookies():
    data = json.loads(STORAGE_STATE.read_text(encoding="utf-8"))
    psid = psidts = None
    for cookie in data.get("cookies", []):
        if cookie.get("domain") != ".google.com":
            continue
        if cookie.get("name") == "__Secure-1PSID":
            psid = cookie.get("value")
        elif cookie.get("name") == "__Secure-1PSIDTS":
            psidts = cookie.get("value")
    if not psid:
        raise RuntimeError(f"cookie が無い: {STORAGE_STATE}")
    return psid, psidts


async def generate(client, prompt: str, out_stem: str, ref: Path | None) -> Path:
    files = [str(ref)] if ref else None
    resp = await asyncio.wait_for(
        client.generate_content(prompt, files=files), timeout=360
    )
    images = list(getattr(resp, "images", None) or [])
    if not images:
        reason = (getattr(resp, "text", "") or "")[:300]
        raise RuntimeError(f"画像が返らない ({out_stem}): {reason}")
    saved = await images[0].save(
        path=str(OUT_DIR), filename=f"{out_stem}.png", verbose=False
    )
    print("saved:", saved)
    return Path(saved)


async def main():
    os.environ.setdefault("GEMINI_COOKIE_PATH", str(COOKIE_CACHE))
    from gemini_webapi import GeminiClient
    from gemini_webapi.constants import AccountStatus
    from gemini_webapi.utils import rotate_1psidts

    OUT_DIR.mkdir(exist_ok=True)
    psid, psidts = load_cookies()
    client = GeminiClient(psid, psidts)
    await client.init(timeout=60)
    if client.account_status != AccountStatus.AVAILABLE:
        raise RuntimeError(f"Gemini セッション未認証: {client.account_status.name}")
    try:
        await rotate_1psidts(client.client)
    except Exception as exc:
        print("cookie rotate failed (continuing):", exc)

    targets = sys.argv[1:] or ["metan", "zunda"]
    for name in targets:
        if name.endswith("_open"):
            base = OUT_DIR / f"{name.removesuffix('_open')}_closed.png"
            await generate(client, OPEN_MOUTH_PROMPT, name, ref=base)
        else:
            await generate(client, CHARS[name] + STYLE, f"{name}_closed", ref=None)
    await client.close()


asyncio.run(main())
