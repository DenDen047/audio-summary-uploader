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

from automator.video import convert_to_video

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
