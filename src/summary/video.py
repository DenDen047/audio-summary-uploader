"""FFmpeg による動画変換: 画像 + MP3 → MP4（FFT イコライザ・背景ローテーション付き）."""

import asyncio
import shutil
import subprocess
from pathlib import Path

from loguru import logger
from PIL import Image

_WIDTH = 1280
_HEIGHT = 720
_FPS = 24

# FFT イコライザ（LED ブロック風）の描画パラメータ。
# showfreqs を白で低解像度描画 → neighbor 拡大 → 透明グリッドでブロック分割し、
# 縦グラデーション（下=緑/中=ゴールド/上=赤）をアルファマスク合成して
# 「音量が大きいほど先端が赤くなる」VU メーター風にする。
# 音声帯域は低域に寄るため、log 目盛の下半分（24 列）だけを使って横に広げる。
_EQ_COLS = 24
_EQ_ROWS = 12
_EQ_BLOCK_W = 53   # 1272 = 24 列 × 53px（左右 4px パッド）
_EQ_BLOCK_H = 18   # 216 = 12 段 × 18px
_EQ_HEIGHT = _EQ_ROWS * _EQ_BLOCK_H
_EQ_GRADIENT_STOPS = [  # 上 → 下
    (0xFF, 0x5E, 0x5E),  # 赤（ピーク）
    (0xFF, 0xD2, 0x4A),  # ゴールド（中域）
    (0x2B, 0xFF, 0x88),  # グリーン（ベース）
]
_EQ_ALPHA = 0.85
_EQ_GAIN_DB = 14  # 可視化専用のゲイン（出力音声には影響しない）

# 背景ローテーション: タイトルの表示秒数（背景は残り時間を等分し各1回のみ表示）
_TITLE_SEC = 20.0


def _check_ffmpeg() -> str:
    """FFmpeg の存在確認."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        msg = "ffmpeg not found. Please install ffmpeg: brew install ffmpeg"
        raise RuntimeError(msg)
    return ffmpeg


def probe_duration(audio_path: Path) -> float:
    """ffprobe で音声の長さ（秒）を取得する."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        msg = "ffprobe not found. Please install ffmpeg: brew install ffmpeg"
        raise RuntimeError(msg)
    result = subprocess.run(
        [
            ffprobe, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def build_slideshow_entries(
    thumbnail_path: Path,
    background_paths: list[Path],
    duration: float,
) -> list[tuple[Path, float]]:
    """タイトル1回 → 各背景1回のスライド列を音声の長さ分つくる.

    パターン: タイトル(20s) → 背景1 → 背景2 → …（残り時間を背景で等分）。
    同じ画像は動画を通して1回しか出さない。
    """
    if not background_paths or duration <= _TITLE_SEC:
        return [(thumbnail_path, max(duration, _TITLE_SEC))]
    per_bg = (duration - _TITLE_SEC) / len(background_paths)
    entries: list[tuple[Path, float]] = [(thumbnail_path, _TITLE_SEC)]
    entries.extend((bg, per_bg) for bg in background_paths)
    return entries


def _write_eq_gradient(path: Path) -> None:
    """EQ 用の縦グラデーション画像（下=緑/中=ゴールド/上=赤）を書き出す."""
    stops = _EQ_GRADIENT_STOPS
    rows: list[tuple[int, int, int]] = []
    for y in range(_EQ_HEIGHT):
        t = y / (_EQ_HEIGHT - 1) * (len(stops) - 1)
        i = min(int(t), len(stops) - 2)
        f = t - i
        rows.append(tuple(
            round(stops[i][k] * (1 - f) + stops[i + 1][k] * f) for k in range(3)
        ))
    strip = Image.new("RGB", (1, _EQ_HEIGHT))
    strip.putdata(rows)
    strip.resize((_WIDTH, _EQ_HEIGHT), Image.NEAREST).save(path)


def _write_concat_file(entries: list[tuple[Path, float]], list_path: Path) -> None:
    """concat demuxer 用のリストファイルを書く."""
    lines = ["ffconcat version 1.0"]
    for path, sec in entries:
        lines.append(f"file '{path.resolve()}'")
        lines.append(f"duration {sec}")
    # concat demuxer は末尾エントリの duration を無視するため最終画像を再掲する
    lines.append(f"file '{entries[-1][0].resolve()}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _eq_filter_chain() -> str:
    """音声→VU メーター風イコライザ（透明背景 rgba）のフィルタ列を返す.

    入力 [1:a]=音声、[2:v]=縦グラデーション画像。白バーのアルファを抽出し、
    グラデーションにマスク合成することで段の高さに応じた色を付ける。
    """
    eq_width = _EQ_COLS * _EQ_BLOCK_W
    pad_x = (_WIDTH - eq_width) // 2
    return (
        f"[1:a]volume={_EQ_GAIN_DB}dB,"
        f"showfreqs=s={_EQ_COLS * 2}x{_EQ_ROWS}:mode=bar:fscale=log:ascale=sqrt:"
        f"win_size=2048:colors=white|white:rate={_FPS}[fr];"
        f"[fr]crop={_EQ_COLS}:{_EQ_ROWS}:0:0,"
        f"scale={eq_width}:{_EQ_HEIGHT}:flags=neighbor,"
        f"pad={_WIDTH}:{_EQ_HEIGHT}:{pad_x}:0:black@0,"
        f"drawgrid=w={_EQ_BLOCK_W}:h={_EQ_BLOCK_H}:t=5:c=black@0:replace=1,"
        "alphaextract[mask];"
        "[2:v]format=rgba[grad];"
        f"[grad][mask]alphamerge,colorchannelmixer=aa={_EQ_ALPHA}[eq]"
    )


async def convert_to_video(
    audio_path: Path,
    thumbnail_path: Path,
    output_path: Path,
    background_paths: list[Path] | None = None,
) -> Path:
    """画像 + 音声から MP4 動画を生成する.

    常に FFT イコライザ（音声連動の LED ブロック）を下部にオーバーレイする。
    background_paths があればタイトル画像⇄背景画像を定期的に切り替え、
    無ければタイトル画像1枚の静止背景になる。
    """
    ffmpeg = _check_ffmpeg()

    if not audio_path.exists():
        msg = f"Audio file not found: {audio_path}"
        raise FileNotFoundError(msg)
    if not thumbnail_path.exists():
        msg = f"Thumbnail file not found: {thumbnail_path}"
        raise FileNotFoundError(msg)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    concat_path: Path | None = None
    if background_paths:
        duration = probe_duration(audio_path)
        entries = build_slideshow_entries(
            thumbnail_path, background_paths, duration
        )
        concat_path = output_path.with_suffix(".slides.txt")
        _write_concat_file(entries, concat_path)
        input_args = ["-f", "concat", "-safe", "0", "-i", str(concat_path)]
    else:
        input_args = ["-loop", "1", "-i", str(thumbnail_path)]

    gradient_path = output_path.with_suffix(".eqgrad.png")
    _write_eq_gradient(gradient_path)

    filter_complex = (
        f"[0:v]fps={_FPS},scale={_WIDTH}:{_HEIGHT},setsar=1[bg];"
        f"{_eq_filter_chain()};"
        "[bg][eq]overlay=0:H-h:format=auto[v]"
    )

    cmd = [
        ffmpeg,
        "-y",
        *input_args,
        "-i", str(audio_path),
        "-loop", "1",
        "-i", str(gradient_path),
        # ⑥ 入力(mp3/画像)のメタデータを一切引き継がない（個人情報・ローカルパス除去）
        "-map_metadata", "-1",
        "-filter_complex", filter_complex,
        "-map", "[v]",
        "-map", "1:a",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-r", str(_FPS),
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        "-movflags", "+faststart",
        str(output_path),
    ]

    logger.info(
        "Converting to video: {} + {} (backgrounds={}) → {}",
        thumbnail_path, audio_path,
        len(background_paths or []), output_path,
    )
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    if concat_path is not None:
        concat_path.unlink(missing_ok=True)
    gradient_path.unlink(missing_ok=True)

    if process.returncode != 0:
        logger.error("FFmpeg stderr: {}", stderr.decode())
        msg = f"FFmpeg failed with return code {process.returncode}"
        raise RuntimeError(msg)

    logger.info("Video created: {}", output_path)
    return output_path
