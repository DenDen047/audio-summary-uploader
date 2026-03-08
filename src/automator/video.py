"""FFmpeg による動画変換: 静止画 + MP3 → MP4."""

import asyncio
import shutil
from pathlib import Path

from loguru import logger


def _check_ffmpeg() -> str:
    """FFmpeg の存在確認."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        msg = "ffmpeg not found. Please install ffmpeg: brew install ffmpeg"
        raise RuntimeError(msg)
    return ffmpeg


async def convert_to_video(
    audio_path: Path,
    thumbnail_path: Path,
    output_path: Path,
) -> Path:
    """静止画 + 音声から MP4 動画を生成する."""
    ffmpeg = _check_ffmpeg()

    if not audio_path.exists():
        msg = f"Audio file not found: {audio_path}"
        raise FileNotFoundError(msg)
    if not thumbnail_path.exists():
        msg = f"Thumbnail file not found: {thumbnail_path}"
        raise FileNotFoundError(msg)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg,
        "-y",
        "-loop", "1",
        "-i", str(thumbnail_path),
        "-i", str(audio_path),
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        "-movflags", "+faststart",
        str(output_path),
    ]

    logger.info(
        "Converting to video: {} + {} → {}",
        thumbnail_path, audio_path, output_path,
    )
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        logger.error("FFmpeg stderr: {}", stderr.decode())
        msg = f"FFmpeg failed with return code {process.returncode}"
        raise RuntimeError(msg)

    logger.info("Video created: {}", output_path)
    return output_path
