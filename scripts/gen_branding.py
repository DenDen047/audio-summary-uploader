"""匿名チャンネル用ブランド素材ジェネレータ（アイコン + バナー）.

NotebookLM と同一アカウントの cookie（storage_state.json）を使い、gemini-webapi
(Nano Banana) でチャンネルのアイコン(800x800, 文字なし)とバナー(2048x1152, 名称入り)を
生成する。サムネと同じ deep-navy/gold のブランド配色で統一する。

事前に cookie を更新しておくこと（数十分で失効するため）:
    uv run notebooklm login

使い方:
    uv run python scripts/gen_branding.py
    uv run python scripts/gen_branding.py --name "ながらAI" --tagline "耳でまとめ聴き"

出力: tmp/branding/icon.png, tmp/branding/banner.png （--out で変更可）
cookie 失効時は画像が返らず WARN を出して終了する（再度 notebooklm login）。
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from loguru import logger
from PIL import Image

from automator.image_gen import _resize_cover, load_google_cookies

_DEFAULT_NAME = "ながらAI"
_DEFAULT_TAGLINE = "AIニュース・論文を耳でまとめ聴き"


def _icon_prompt() -> str:
    return (
        "Create a modern, minimalist YouTube channel logo emblem, square 1:1, the "
        "subject centered with generous padding so it stays intact when cropped to a "
        "circle. Design: a sleek headphone arc merged with a glowing AI soundwave / "
        "neural motif. Palette: deep navy background, vivid gold (#FFD24A) and "
        "electric-blue glow. Flat vector style, bold, high contrast, recognizable at "
        "small sizes. No text, no letters, no watermark, no busy details."
    )


def _banner_prompt(name: str, tagline: str) -> str:
    return (
        "Create a wide 16:9 YouTube channel banner. Background: deep navy to "
        "electric-blue gradient with subtle soundwave and circuit patterns and a soft "
        "AI glow. In the EXACT center, within a narrow horizontal safe band (about 60% "
        "of the width, 30% of the height), render the Japanese channel name large and "
        f"bold in vivid gold (#FFD24A) with a dark outline: 「{name}」. Directly below "
        f"it a smaller clean white tagline: 「{tagline}」. Reproduce every Japanese "
        "character exactly. Keep ALL text strictly within the central band (top, "
        "bottom and far sides get cropped on some devices). Professional, clean, no "
        "watermark, no extra text."
    )


async def _generate(client, prompt: str, width: int, height: int, out: Path) -> bool:
    resp = await asyncio.wait_for(client.generate_content(prompt), timeout=120)
    images = list(getattr(resp, "images", None) or [])
    if not images:
        reason = (getattr(resp, "text", "") or "")[:200]
        logger.warning("画像が返りませんでした（cookie失効の可能性）: {}", reason)
        return False
    saved = await images[0].save(
        path=str(out.parent), filename=f"{out.stem}_raw.png", verbose=False
    )
    with Image.open(saved) as im:
        shaped = _resize_cover(im, width, height)
    shaped.save(str(out), "PNG", optimize=True)
    Path(saved).unlink(missing_ok=True)
    logger.info("生成: {} {} ({} bytes)", out, shaped.size, out.stat().st_size)
    return True


async def _main(name: str, tagline: str, out_dir: Path) -> int:
    psid, psidts = load_google_cookies()
    if not psid:
        logger.error("Google cookie 無し。先に `uv run notebooklm login` を実行。")
        return 2

    from gemini_webapi import GeminiClient

    out_dir.mkdir(parents=True, exist_ok=True)
    client = GeminiClient(psid, psidts)
    ok = True
    try:
        await client.init(timeout=60)
        ok &= await _generate(client, _icon_prompt(), 800, 800, out_dir / "icon.png")
        ok &= await _generate(
            client, _banner_prompt(name, tagline), 2048, 1152, out_dir / "banner.png"
        )
    finally:
        close = getattr(client, "close", None)
        if close:
            result = close()
            if asyncio.iscoroutine(result):
                await result
    if not ok:
        logger.error("一部失敗。`uv run notebooklm login` で cookie を更新して再実行。")
        return 1
    logger.info("完了: {} にアイコンとバナーを保存しました。", out_dir)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="匿名チャンネルのアイコン/バナー生成")
    parser.add_argument("--name", default=_DEFAULT_NAME, help="チャンネル名")
    parser.add_argument("--tagline", default=_DEFAULT_TAGLINE, help="バナーの一言タグ")
    parser.add_argument(
        "--out", default="tmp/branding", help="出力ディレクトリ（既定: tmp/branding）"
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main(args.name, args.tagline, Path(args.out))))


if __name__ == "__main__":
    main()
