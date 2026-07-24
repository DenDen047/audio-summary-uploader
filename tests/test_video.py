"""機能6: 出力 mp4 に元音声のメタデータ(個人情報/ローカルパス)が残らないことの検証.

実際の ffmpeg/ffprobe を使う統合テスト。どちらか無ければスキップする。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from podcast.video import (
    _write_eq_gradient,
    build_slideshow_entries,
    convert_to_video,
)

_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")

pytestmark = pytest.mark.skipif(
    not (_FFMPEG and _FFPROBE), reason="ffmpeg/ffprobe が無い環境ではスキップ"
)

_SECRET_TITLE = "SECRET_NOTEBOOK_TITLE me@personal.example"
_SECRET_PATH = "/Users/secret/local/path"


def _all_tag_values(probe: dict) -> str:
    """format と全 stream の tags 値を1つの文字列に連結する."""
    values: list[str] = []
    fmt_tags = probe.get("format", {}).get("tags", {})
    values.extend(str(v) for v in fmt_tags.values())
    for stream in probe.get("streams", []):
        values.extend(str(v) for v in stream.get("tags", {}).values())
    return "\n".join(values)


@pytest.mark.asyncio()
async def test_output_mp4_has_no_source_metadata(tmp_path: Path) -> None:
    # PII 付きのソース mp3 を生成
    audio = tmp_path / "src.mp3"
    subprocess.run(
        [
            _FFMPEG, "-y", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=0.5",
            "-metadata", f"title={_SECRET_TITLE}",
            "-metadata", f"artist={_SECRET_PATH}",
            str(audio),
        ],
        check=True, capture_output=True,
    )

    thumb = tmp_path / "thumb.png"
    Image.new("RGB", (320, 180), (12, 24, 48)).save(thumb)

    out = tmp_path / "out.mp4"
    await convert_to_video(audio_path=audio, thumbnail_path=thumb, output_path=out)
    assert out.exists()

    probe = json.loads(subprocess.run(
        [
            _FFPROBE, "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", str(out),
        ],
        check=True, capture_output=True, text=True,
    ).stdout)

    tags_blob = _all_tag_values(probe)
    assert _SECRET_TITLE not in tags_blob
    assert "me@personal.example" not in tags_blob
    assert _SECRET_PATH not in tags_blob


class TestBuildSlideshowEntries:
    def test_title_once_then_each_background_once(self) -> None:
        thumb = Path("t.png")
        bgs = [Path("b0.png"), Path("b1.png")]
        entries = build_slideshow_entries(thumb, bgs, duration=100.0)
        assert entries == [(thumb, 20.0), (bgs[0], 40.0), (bgs[1], 40.0)]

    def test_no_image_appears_twice(self) -> None:
        thumb = Path("t.png")
        bgs = [Path(f"b{i}.png") for i in range(5)]
        entries = build_slideshow_entries(thumb, bgs, duration=300.0)
        paths = [p for p, _ in entries]
        assert len(paths) == len(set(paths))
        assert sum(sec for _, sec in entries) >= 300.0

    def test_short_audio_is_title_only(self) -> None:
        entries = build_slideshow_entries(
            Path("t.png"), [Path("b.png")], duration=5.0
        )
        assert entries == [(Path("t.png"), 20.0)]

    def test_no_backgrounds_is_title_only(self) -> None:
        entries = build_slideshow_entries(Path("t.png"), [], duration=90.0)
        assert entries == [(Path("t.png"), 90.0)]


class TestWriteEqGradient:
    def test_gradient_colors_by_height(self, tmp_path: Path) -> None:
        out = tmp_path / "grad.png"
        _write_eq_gradient(out)
        with Image.open(out) as img:
            assert img.size == (1280, 216)
            top = img.getpixel((640, 0))
            bottom = img.getpixel((640, 215))
        assert top == (0xFF, 0x5E, 0x5E)   # 上端 = 赤（ピーク）
        assert bottom == (0x2B, 0xFF, 0x88)  # 下端 = 緑（ベース）


@pytest.mark.asyncio()
async def test_convert_with_backgrounds_produces_mp4(tmp_path: Path) -> None:
    """背景ローテーション経路（concat demuxer）でも mp4 が生成される."""
    audio = tmp_path / "src.mp3"
    subprocess.run(
        [
            _FFMPEG, "-y", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=0.5",
            str(audio),
        ],
        check=True, capture_output=True,
    )
    thumb = tmp_path / "thumb.png"
    Image.new("RGB", (1280, 720), (12, 24, 48)).save(thumb)
    bg = tmp_path / "bg.png"
    Image.new("RGB", (1280, 720), (48, 12, 24)).save(bg)

    out = tmp_path / "out.mp4"
    await convert_to_video(
        audio_path=audio,
        thumbnail_path=thumb,
        output_path=out,
        background_paths=[bg],
    )
    assert out.exists()
    # concat リストファイルは後始末される
    assert not out.with_suffix(".slides.txt").exists()
