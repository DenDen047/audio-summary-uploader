# ruff: noqa: E501
"""Codexの美術判断を、従量課金なしのローカルSVG背景へ変換する。"""

# SVG断片は図形ごとのまとまりを保つ方が視覚調整しやすいため、行長制限を除外する。
# 外部画像APIへ戻さないのは、追加の従量課金を発生させないことが製品要件だからである。

from __future__ import annotations

import hashlib
import html
import random
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from playwright.sync_api import sync_playwright

THUMBNAIL_VISUAL_PROMPT_MAX_CHARS = 240
SVG_WIDTH = 1600
SVG_HEIGHT = 900
MOTIFS = {
    "packages",
    "network",
    "code",
    "security",
    "database",
    "cloud",
    "research",
    "speed",
    "comparison",
    "generic",
}


@dataclass(frozen=True)
class ThumbnailBackdropOptions:
    """codex-svgはローカル描画、staticは固定素材を使う。"""

    mode: str = "codex-svg"

    def __post_init__(self) -> None:
        if self.mode not in {"codex-svg", "static"}:
            raise ValueError("thumbnail background mode must be codex-svg or static")


@dataclass(frozen=True)
class ThumbnailBackdropResult:
    path: Path
    prompt_path: Path
    provider: str
    model: str | None
    prompt: str
    fallback_reason: str | None
    source_path: Path | None = None

    def as_metadata(self) -> dict[str, str | bool | None]:
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt": self.prompt,
            "path": str(self.path),
            "prompt_path": str(self.prompt_path),
            "source_path": str(self.source_path) if self.source_path else None,
            "fallback_reason": self.fallback_reason,
            "metered_api": False,
        }


def generate_thumbnail_backdrop(
    script: dict,
    output_path: Path,
    fallback_path: Path,
    options: ThumbnailBackdropOptions,
) -> ThumbnailBackdropResult:
    """台本の意味モチーフからSVGを組み立て、ChromiumでPNGへ描画する。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prompt = build_thumbnail_backdrop_prompt(script)
    prompt_path = output_path.with_name("thumbnail-background-prompt.txt")
    prompt_path.write_text(prompt, encoding="utf-8")

    if options.mode == "static":
        if not fallback_path.is_file():
            raise RuntimeError(f"固定サムネイル背景がありません: {fallback_path}")
        shutil.copyfile(fallback_path, output_path)
        return ThumbnailBackdropResult(
            path=output_path,
            prompt_path=prompt_path,
            provider="static",
            model=None,
            prompt=prompt,
            fallback_reason="設定で固定背景を選択",
        )

    source_path = output_path.with_suffix(".svg")
    source_path.write_text(_build_svg(script), encoding="utf-8")
    _rasterize_svg(source_path, output_path)
    logger.info("Codex指示からローカルSVGサムネイル背景を生成: {}", output_path)
    return ThumbnailBackdropResult(
        path=output_path,
        prompt_path=prompt_path,
        provider="codex-directed-local-svg",
        model=None,
        prompt=prompt,
        fallback_reason=None,
        source_path=source_path,
    )


def build_thumbnail_backdrop_prompt(script: dict) -> str:
    """Codexが出した短い美術案と、実際のローカル描画条件を記録する。"""
    visual = _visual_prompt(script)
    motif = _select_motif(visual, str(script.get("title", "")))
    seed = _seed_for(script)
    return "\n".join(
        (
            "AI cost policy: subscription-only; metered image APIs are prohibited",
            "Renderer: deterministic local SVG rasterized by local Chromium",
            f"Codex art direction: {visual}",
            f"Selected motif: {motif}",
            f"Deterministic seed: {seed}",
            "Canvas: 16:9 landscape, 1600x900",
            (
                "Composition: strong topic motifs around both sides; calm high-contrast "
                "center for two headline lines and separately composited characters"
            ),
            (
                "Palette: deep plum and navy, coral pink, warm gold, cream, "
                "with small cyan highlights"
            ),
            "Constraints: background only; no people, text, logos, or watermarks",
        )
    )


def _visual_prompt(script: dict) -> str:
    visual = script.get("thumbnail_visual_prompt")
    if not isinstance(visual, str) or not visual.strip():
        title = str(script.get("title", "技術解説")).strip() or "技術解説"
        visual = f"motif=generic; {title}の中心概念を抽象的な技術図形で表す"
    visual = visual.strip()
    if len(visual) > THUMBNAIL_VISUAL_PROMPT_MAX_CHARS:
        raise RuntimeError(
            "thumbnail_visual_prompt は"
            f"{THUMBNAIL_VISUAL_PROMPT_MAX_CHARS}文字以内にする"
        )
    return visual


def _select_motif(visual: str, title: str) -> str:
    explicit = re.search(r"(?:^|\s)motif=([a-z]+)", visual.lower())
    if explicit and explicit.group(1) in MOTIFS:
        return explicit.group(1)
    haystack = f"{title} {visual}".lower()
    keyword_groups = (
        ("packages", ("package", "依存", "pip", "uv", "npm", "環境")),
        ("security", ("security", "安全", "脆弱", "認証", "暗号", "権限")),
        ("database", ("database", "db", "データベース", "sql", "保存")),
        ("cloud", ("cloud", "クラウド", "serverless", "aws", "azure")),
        ("code", ("code", "コード", "python", "javascript", "cli", "開発")),
        ("research", ("paper", "論文", "研究", "ニュース", "調査")),
        ("speed", ("speed", "高速", "速い", "性能", "最適化")),
        ("comparison", ("比較", "違い", "vs", "対比")),
        ("network", ("ai", "ネットワーク", "接続", "agent", "モデル")),
    )
    for motif, keywords in keyword_groups:
        if any(keyword in haystack for keyword in keywords):
            return motif
    return "generic"


def _seed_for(script: dict) -> int:
    value = f"{script.get('title', '')}\n{_visual_prompt(script)}"
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:12], 16)


def _build_svg(script: dict) -> str:
    visual = _visual_prompt(script)
    motif = _select_motif(visual, str(script.get("title", "")))
    seed = _seed_for(script)
    rng = random.Random(seed)
    rotation = rng.randint(-10, 10)
    dots = "".join(
        f'<circle cx="{rng.randint(35, 1565)}" cy="{rng.randint(35, 865)}" '
        f'r="{rng.choice((3, 4, 5))}" fill="#fff" opacity="{rng.uniform(0.08, 0.2):.2f}"/>'
        for _ in range(52)
    )
    motif_svg = _motif_svg(motif)
    description = html.escape(visual)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_WIDTH}" height="{SVG_HEIGHT}" viewBox="0 0 {SVG_WIDTH} {SVG_HEIGHT}">
  <desc>{description}</desc>
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#1b1630"/><stop offset="0.48" stop-color="#30203f"/><stop offset="1" stop-color="#122b43"/>
    </linearGradient>
    <radialGradient id="glow" cx="50%" cy="42%" r="60%">
      <stop offset="0" stop-color="#765071" stop-opacity=".48"/><stop offset=".7" stop-color="#2d203e" stop-opacity=".12"/><stop offset="1" stop-color="#0d1728" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="coral" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#ff8fab"/><stop offset="1" stop-color="#d94e83"/></linearGradient>
    <linearGradient id="gold" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#ffe29a"/><stop offset="1" stop-color="#f5a94c"/></linearGradient>
    <filter id="soft"><feGaussianBlur stdDeviation="22"/></filter>
    <filter id="shadow"><feDropShadow dx="0" dy="14" stdDeviation="12" flood-color="#070b18" flood-opacity=".42"/></filter>
    <pattern id="grid" width="52" height="52" patternUnits="userSpaceOnUse"><path d="M52 0H0V52" fill="none" stroke="#fff" stroke-opacity=".045" stroke-width="1"/></pattern>
  </defs>
  <rect width="1600" height="900" fill="url(#bg)"/>
  <rect width="1600" height="900" fill="url(#grid)"/>
  <ellipse cx="800" cy="390" rx="690" ry="470" fill="url(#glow)"/>
  <g transform="rotate({rotation} 800 450)" opacity=".22"><path d="M-100 690L720 -30" stroke="#ff8fab" stroke-width="70"/><path d="M880 950L1700 210" stroke="#55d8e6" stroke-width="44"/></g>
  <circle cx="170" cy="150" r="150" fill="#f14f87" opacity=".14" filter="url(#soft)"/>
  <circle cx="1450" cy="720" r="190" fill="#49d4e0" opacity=".13" filter="url(#soft)"/>
  {dots}
  <g filter="url(#shadow)">{motif_svg}</g>
  <rect x="395" y="170" width="810" height="510" rx="54" fill="#15182b" opacity=".38" stroke="#fff" stroke-opacity=".1" stroke-width="2"/>
  <path d="M505 720H1095" stroke="#ffd88c" stroke-width="5" stroke-linecap="round" opacity=".7"/>
  <circle cx="475" cy="720" r="8" fill="#ff7fa4"/><circle cx="1125" cy="720" r="8" fill="#59d5e4"/>
</svg>'''


def _motif_svg(motif: str) -> str:
    if motif == "packages":
        return '''<g transform="translate(80 245)"><path d="M70 70l95-48 95 48-95 48z" fill="url(#gold)"/><path d="M70 70v120l95 50V118z" fill="#e78f49"/><path d="M260 70v120l-95 50V118z" fill="#c96a78"/><path d="M165 22v96" stroke="#fff4cb" stroke-width="8" opacity=".7"/></g><g transform="translate(1280 400)"><path d="M0 55l90-45 90 45-90 45z" fill="url(#coral)"/><path d="M0 55v112l90 45V100z" fill="#b93b71"/><path d="M180 55v112l-90 45V100z" fill="#773d78"/></g><path d="M325 440C475 300 510 565 650 420M950 455c150-155 190 120 315-15" fill="none" stroke="#57d7e5" stroke-width="10" stroke-dasharray="18 16"/>'''
    if motif == "security":
        return '''<path d="M210 180l135 50v115c0 105-63 164-135 202-72-38-135-97-135-202V230z" fill="url(#coral)" stroke="#ffd5df" stroke-width="8"/><rect x="145" y="300" width="130" height="110" rx="24" fill="#281e3d"/><path d="M175 300v-35a35 35 0 0170 0v35" fill="none" stroke="#ffd98d" stroke-width="16"/><path d="M1320 330l105 40v90c0 82-49 128-105 157-56-29-105-75-105-157v-90z" fill="url(#gold)"/>'''
    if motif == "database":
        return _database_svg()
    if motif == "cloud":
        return '''<g transform="translate(55 285)"><path d="M75 170h205c55 0 78-73 33-104-9-61-84-84-125-40-52-35-118 1-113 61-66 9-65 83 0 83z" fill="url(#coral)"/><path d="M168 200v120m-58-60h116" stroke="#ffd98d" stroke-width="10" stroke-linecap="round"/></g><g transform="translate(1275 335) scale(.8)"><path d="M40 170h250c55 0 78-73 33-104-9-61-84-84-125-40-52-35-118 1-113 61-66 9-65 83 0 83z" fill="url(#gold)"/></g>'''
    if motif == "code":
        return '''<g transform="translate(50 230)"><rect width="330" height="250" rx="30" fill="#151b31" stroke="#ff8fab" stroke-width="7"/><circle cx="35" cy="35" r="9" fill="#ff7797"/><circle cx="65" cy="35" r="9" fill="#ffd475"/><path d="M85 105l-48 34 48 34M245 105l48 34-48 34M190 82l-50 118" fill="none" stroke="#62d7e3" stroke-width="13" stroke-linecap="round" stroke-linejoin="round"/></g><g transform="translate(1260 390) scale(.78)"><rect width="330" height="250" rx="30" fill="#151b31" stroke="#ffd475" stroke-width="7"/><path d="M90 90l-45 35 45 35m150-70l45 35-45 35" fill="none" stroke="#ff8fab" stroke-width="14" stroke-linecap="round"/></g>'''
    if motif == "research":
        return '''<g transform="translate(75 180) rotate(-7 145 180)"><rect width="290" height="360" rx="28" fill="#fff8ed"/><path d="M48 78h190M48 125h150M48 250l45-55 44 28 65-92 42 68" fill="none" stroke="#d45583" stroke-width="13" stroke-linecap="round"/><circle cx="92" cy="195" r="13" fill="#f7b959"/><circle cx="202" cy="131" r="13" fill="#55cfdc"/></g><g transform="translate(1310 330)"><circle cx="55" cy="55" r="82" fill="none" stroke="#ffd98d" stroke-width="20"/><path d="M115 115l85 85" stroke="#ff8fab" stroke-width="30" stroke-linecap="round"/></g>'''
    if motif == "speed":
        return '''<path d="M50 250h300l-100-90m100 90l-100 90" fill="none" stroke="#ff8fab" stroke-width="30" stroke-linecap="round" stroke-linejoin="round"/><path d="M1250 590h300l-100-90m100 90l-100 90" fill="none" stroke="#ffd475" stroke-width="30" stroke-linecap="round" stroke-linejoin="round"/><path d="M0 390h260M1320 450h280" stroke="#55d7e5" stroke-width="10" stroke-dasharray="40 24"/>'''
    if motif == "comparison":
        return '''<g transform="translate(70 220)"><rect width="280" height="330" rx="38" fill="#d94e83"/><path d="M65 105h150M65 165h100M65 225h175" stroke="#fff" stroke-width="17" stroke-linecap="round" opacity=".85"/></g><g transform="translate(1250 220)"><rect width="280" height="330" rx="38" fill="#278da4"/><path d="M65 105h150M65 165h100M65 225h175" stroke="#fff" stroke-width="17" stroke-linecap="round" opacity=".85"/></g>'''
    if motif == "network":
        return _network_svg()
    return _network_svg()


def _network_svg() -> str:
    return '''<g stroke="#62d7e3" stroke-width="8" opacity=".75"><path d="M90 260l165-90 110 140-145 135zM1235 520l120-170 165 95-80 170z" fill="none"/></g><g fill="url(#coral)" stroke="#ffdbe3" stroke-width="6"><circle cx="90" cy="260" r="38"/><circle cx="255" cy="170" r="31"/><circle cx="365" cy="310" r="42"/></g><g fill="url(#gold)" stroke="#fff1c6" stroke-width="6"><circle cx="1235" cy="520" r="40"/><circle cx="1355" cy="350" r="34"/><circle cx="1520" cy="445" r="43"/></g>'''


def _database_svg() -> str:
    return '''<g transform="translate(70 235)"><ellipse cx="145" cy="55" rx="145" ry="55" fill="#ff9ab4"/><path d="M0 55v230c0 31 65 55 145 55s145-24 145-55V55c0 31-65 55-145 55S0 86 0 55z" fill="#bd4c7c"/><path d="M0 165c0 31 65 55 145 55s145-24 145-55" fill="none" stroke="#ffd6df" stroke-width="8"/></g><g transform="translate(1285 350) scale(.78)"><ellipse cx="145" cy="55" rx="145" ry="55" fill="#ffe093"/><path d="M0 55v230c0 31 65 55 145 55s145-24 145-55V55c0 31-65 55-145 55S0 86 0 55z" fill="#c98945"/></g>'''


def _rasterize_svg(source_path: Path, output_path: Path) -> None:
    svg = source_path.read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": SVG_WIDTH, "height": SVG_HEIGHT})
        page.set_content(
            f'<html><body style="margin:0;background:#15182b">{svg}</body></html>'
        )
        page.screenshot(path=str(output_path), animations="disabled")
        browser.close()
