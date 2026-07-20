"""タイムライン計算 → ffmpeg 1 パスで mp4 に合成する。

映像はスライド静止画の列 (セリフ同期の段階表示でシーン内も切り替わる)。
ズーム等のカメラ的な動きは入れない (zoompan はサブピクセル揺れの原因になるため不使用)。
キャラ立ち絵は常時表示し、台詞の意味に合わせてポーズを切り替える。
発話中も全身位置は固定し、口パクだけを動かす。
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import budoux
from loguru import logger

from lecture.characters import CharacterAssets
from lecture.reveal import ScenePlan
from lecture.tts import SPEAKER_MAP

LINE_GAP = 0.25   # セリフ間の無音 (秒)
SCENE_GAP = 0.7   # シーン間の無音 (秒)
TAIL = 1.0        # 動画末尾の余韻 (秒)
SAMPLE_RATE = 24000  # VOICEVOX 出力に合わせる
SUB_FONT = "Rounded Mplus 1c Bold"  # fonts/MPLUSRounded1c-Bold.ttf のファミリー名
# ffmpeg 同梱の libass はスペース無しの日本語を折り返せないため、
# budoux で分節して自前で改行 (\N) を入れる
SUB_LINE_LIMIT = 18  # 字幕 1 行の最大文字数 (キャラ立ち絵の間に収める)
SUB_CHUNK_CHARS = 2 * SUB_LINE_LIMIT  # 長セリフは文単位で分け、1 表示を約 2 行に抑える
# PowerPoint の手動レイアウト実測値。澪は左端、透は右端から 95px 内側。
CHAR_MARGINS_X = {"metan": 0, "zunda": 95}
MOUTH_TOGGLES = {
    # 澪は大きく開いた口差分を短く見せ、落ち着いた声に合う間を残す。
    "metan": "lt(mod(t,0.56),0.14)",
    "zunda": "lt(mod(t*4,2),1)",
}

_budoux_parser = budoux.load_default_japanese_parser()


@dataclass
class SubEvent:
    start: float
    end: float
    speaker: str
    text: str
    metan_pose: str = "default"
    zunda_pose: str = "default"

    def pose_for(self, speaker: str) -> str:
        if speaker == "metan":
            return self.metan_pose
        if speaker == "zunda":
            return self.zunda_pose
        raise RuntimeError(f"不明な話者です: {speaker}")


@dataclass(frozen=True)
class EyeCatch:
    image: Path
    audio: Path
    before_scenes: tuple[int, ...]


def assemble(
    script: dict,
    scene_wavs: list[list[Path]],
    scene_state_pngs: list[list[Path]],
    plans: list[ScenePlan],
    job_dir: Path,
    fonts_dir: Path,
    characters: dict[str, CharacterAssets],
    eyecatch: EyeCatch | None = None,
) -> Path:
    if len(scene_wavs) != len(scene_state_pngs):
        raise RuntimeError(
            f"シーン数不一致: 音声 {len(scene_wavs)} "
            f"vs スライド {len(scene_state_pngs)}"
        )

    silence = _make_silences(job_dir)
    audio_entries: list[Path] = []
    events: list[SubEvent] = []
    video_entries: list[tuple[Path, float]] = []  # (PNG, 表示時間)
    hidden_intervals: list[tuple[float, float]] = []

    eyecatch_scenes: set[int] = set()
    eyecatch_duration = 0.0
    if eyecatch is not None:
        if not eyecatch.image.exists() or not eyecatch.audio.exists():
            raise RuntimeError(f"アイキャッチ素材がありません: {eyecatch}")
        eyecatch_scenes = set(eyecatch.before_scenes)
        if len(eyecatch_scenes) != len(eyecatch.before_scenes):
            raise RuntimeError("アイキャッチの挿入位置が重複しています")
        invalid = sorted(
            scene for scene in eyecatch_scenes
            if scene <= 1 or scene > len(scene_wavs)
        )
        if invalid:
            raise RuntimeError(f"アイキャッチの挿入位置が不正です: {invalid}")
        eyecatch_duration = _probe_duration(eyecatch.audio)

    t = 0.0
    last = len(scene_wavs) - 1
    for i, (wavs, scene, plan) in enumerate(zip(scene_wavs, script["scenes"], plans)):
        if i + 1 in eyecatch_scenes:
            start = t
            audio_entries.append(eyecatch.audio)
            video_entries.append((eyecatch.image, eyecatch_duration))
            t += eyecatch_duration
            hidden_intervals.append((start, t))

        line_starts: list[float] = []
        for j, (wav, line) in enumerate(zip(wavs, scene["lines"])):
            if j > 0:
                audio_entries.append(silence[LINE_GAP])
                t += LINE_GAP
            line_starts.append(t)
            duration = _probe_duration(wav)
            events.append(
                SubEvent(
                    t,
                    t + duration,
                    line["speaker"],
                    line["text"],
                    metan_pose=line.get("metan_pose", "default"),
                    zunda_pose=line.get("zunda_pose", "default"),
                )
            )
            audio_entries.append(wav)
            t += duration
        gap = TAIL if i == last else SCENE_GAP
        audio_entries.append(silence[gap])
        t += gap

        # 段階表示: 状態 k は「その状態の最初のセリフ開始」から次の状態の開始まで表示
        state_starts = [
            line_starts[plan.line_state_idx.index(k)] for k in range(len(plan.states))
        ]
        state_ends = state_starts[1:] + [t]
        for png, start, end in zip(scene_state_pngs[i], state_starts, state_ends):
            video_entries.append((png, end - start))

    audio_list = job_dir / "audio_list.txt"
    audio_list.write_text(
        "".join(f"file '{p.resolve()}'\n" for p in audio_entries), encoding="utf-8"
    )
    slides_list = job_dir / "slides_list.txt"
    slide_lines = [
        f"file '{png.resolve()}'\nduration {d:.3f}\n" for png, d in video_entries
    ]
    # concat demuxer は末尾エントリの再掲が必要 (duration を明示して凍結を防ぐ)
    slide_lines.append(f"file '{video_entries[-1][0].resolve()}'\nduration 0.034\n")
    slides_list.write_text("".join(slide_lines), encoding="utf-8")

    subs_path = job_dir / "subs.ass"
    subs_path.write_text(_build_ass(events), encoding="utf-8")

    return _compose(
        slides_list,
        audio_list,
        job_dir,
        fonts_dir,
        characters,
        events,
        t,
        hidden_intervals,
    )


def _compose(
    slides_list: Path,
    audio_list: Path,
    job_dir: Path,
    fonts_dir: Path,
    characters: dict[str, CharacterAssets],
    events: list[SubEvent],
    total: float,
    hidden_intervals: list[tuple[float, float]] | None = None,
) -> Path:
    """スライド列 + ポーズ差分 + 口パク + 字幕 + 音声を合成。"""
    if not events:
        raise RuntimeError("セリフがないため動画を合成できません")

    pose_assets = {
        speaker: {
            key.split(":", 1)[1] if ":" in key else "default": asset
            for key, asset in characters.items()
            if key == speaker or key.startswith(f"{speaker}:")
        }
        for speaker in ("metan", "zunda")
    }
    for event in events:
        for speaker in pose_assets:
            pose = event.pose_for(speaker)
            if pose not in pose_assets[speaker]:
                available = ", ".join(sorted(pose_assets[speaker]))
                raise RuntimeError(
                    f"{speaker} のポーズ '{pose}' がありません。利用可能: {available}"
                )

    inputs: list[Path] = []
    input_indices: dict[Path, int] = {}

    def add_input(path: Path) -> int:
        if path not in input_indices:
            input_indices[path] = 2 + len(inputs)
            inputs.append(path)
        return input_indices[path]

    chains = ["[0:v]fps=30,scale=1920:1080,format=yuv420p[bg]"]
    label_in = "[bg]"
    step = 0
    pose_windows = _pose_windows(events, total)
    for speaker in ("metan", "zunda"):
        for pose, char in pose_assets[speaker].items():
            active = pose_windows.get((speaker, pose), [])
            active = _subtract_intervals(active, hidden_intervals or [])
            if not active:
                continue
            x = (
                CHAR_MARGINS_X[speaker]
                if speaker == "metan"
                else 1920 - CHAR_MARGINS_X[speaker] - char.width
            )
            y_base = 1080 - char.height + char.bleed
            speak_intervals = [
                (event.start, event.end)
                for event in events
                if event.speaker == speaker and event.pose_for(speaker) == pose
            ]
            speak = _intervals_expr(speak_intervals)
            active_expr = _intervals_expr(active)
            # 1px の座標丸めでも髪や輪郭全体が瞬間的に変わって見えるため、
            # 立ち絵は固定し、話者の動きは口パクとポーズ差分だけで表現する。
            y = str(y_base)
            base_idx = add_input(char.image)
            out = f"[v{step + 1}]"
            chains.append(
                f"{label_in}[{base_idx}:v]overlay=x={x}:y='{y}'"
                f":enable='{active_expr}'{out}"
            )
            label_in = out
            step += 1
            if char.mouth_patch is not None and speak_intervals:
                patch_idx = add_input(char.mouth_patch)
                out = f"[v{step + 1}]"
                chains.append(
                    f"{label_in}[{patch_idx}:v]overlay=x={x + char.mouth_x}"
                    f":y='({y})+{char.mouth_y}'"
                    f":enable='({speak})*{MOUTH_TOGGLES[speaker]}'{out}"
                )
                label_in = out
                step += 1
    chains.append(f"{label_in}subtitles=subs.ass:fontsdir={fonts_dir.resolve()}[v]")

    out_path = job_dir / "video.mp4"
    # cwd=job_dir で実行し subs.ass は相対参照 (フィルタ引数のパスエスケープ回避)
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(slides_list.resolve()),
        "-f", "concat", "-safe", "0", "-i", str(audio_list.resolve()),
        *(arg for p in inputs for arg in ("-i", str(p.resolve()))),
        "-filter_complex", ";".join(chains),
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        # 最終フレームの duration 解釈で映像が延びるため総尺で打ち切る
        "-t", f"{total:.3f}",
        str(out_path.resolve()),
    ]
    logger.info("ffmpeg 合成開始 (総尺 {:.1f} 秒)", total)
    result = subprocess.run(cmd, cwd=job_dir, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 失敗: {result.stderr[-2000:]}")
    logger.info("動画を生成: {}", out_path)
    return out_path


def _intervals_expr(intervals: list[tuple[float, float]]) -> str:
    if not intervals:
        return "0"
    return "+".join(
        f"gte(t,{start:.3f})*lt(t,{end:.3f})" for start, end in intervals
    )


def _subtract_intervals(
    intervals: list[tuple[float, float]],
    cuts: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """アイキャッチ区間だけ、保持中のポーズ表示を中断する。"""
    result = intervals
    for cut_start, cut_end in cuts:
        next_result = []
        for start, end in result:
            if end <= cut_start or start >= cut_end:
                next_result.append((start, end))
                continue
            if start < cut_start:
                next_result.append((start, cut_start))
            if end > cut_end:
                next_result.append((cut_end, end))
        result = next_result
    return result


def _pose_windows(
    events: list[SubEvent], total: float
) -> dict[tuple[str, str], list[tuple[float, float]]]:
    """各台詞開始から次の台詞開始まで、指定ポーズを維持する。"""
    windows: dict[tuple[str, str], list[tuple[float, float]]] = {}
    ends = [event.start for event in events[1:]] + [total]
    for event, end in zip(events, ends):
        for speaker in ("metan", "zunda"):
            key = (speaker, event.pose_for(speaker))
            windows.setdefault(key, []).append((event.start, end))
    return windows


def _run_ffmpeg(cmd: list[str], label: str) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"{label} に失敗: {result.stderr[-1500:]}")


def _make_silences(job_dir: Path) -> dict[float, Path]:
    """必要な長さの無音 wav を作る (VOICEVOX wav と同一フォーマット)。"""
    silences = {}
    for sec in (LINE_GAP, SCENE_GAP, TAIL):
        path = job_dir / f"silence_{sec:.2f}.wav"
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"anullsrc=r={SAMPLE_RATE}:cl=mono",
            "-t", f"{sec}", "-c:a", "pcm_s16le", str(path),
        ]
        _run_ffmpeg(cmd, "無音 wav の生成")
        silences[sec] = path
    return silences


def _probe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe 失敗: {path}")
    return float(result.stdout.strip())


def _split_sentences(text: str) -> list[str]:
    """句点で区切り、SUB_CHUNK_CHARS 以内に詰めた字幕チャンク列にする。

    句点のない長文は読点でさらに分割する (字幕が 3 行以上に膨らむのを防ぐ)。
    """
    parts = []
    for sentence in re.split(r"(?<=[。！？])", text):
        if not sentence:
            continue
        if len(sentence) > SUB_CHUNK_CHARS:
            parts.extend(p for p in re.split(r"(?<=、)", sentence) if p)
        else:
            parts.append(sentence)
    chunks: list[str] = []
    current = ""
    for part in parts:
        if current and len(current) + len(part) > SUB_CHUNK_CHARS:
            chunks.append(current)
            current = part
        else:
            current += part
    if current:
        chunks.append(current)
    return chunks


def _wrap_jp(text: str) -> str:
    """budoux の分節を SUB_LINE_LIMIT 字以内で詰め、\\N 区切りの複数行にする。"""
    lines: list[str] = []
    current = ""
    for segment in _budoux_parser.parse(text):
        if current and len(current) + len(segment) > SUB_LINE_LIMIT:
            lines.append(current)
            current = segment
        else:
            current += segment
    if current:
        lines.append(current)
    return "\\N".join(lines)


def _ass_color(hex_color: str) -> str:
    """#RRGGBB → ASS の &H00BBGGRR 形式。"""
    r, g, b = hex_color[1:3], hex_color[3:5], hex_color[5:7]
    return f"&H00{b}{g}{r}".upper()


def _ass_time(sec: float) -> str:
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _build_ass(events: list[SubEvent]) -> str:
    # 明るいスライド向け: 濃い話者色 + 白フチ + 薄い影
    styles = []
    for name, (_, _, color) in SPEAKER_MAP.items():
        styles.append(
            f"Style: {name},{SUB_FONT},50,{_ass_color(color)},&H00FFFFFF,"
            "&H00FFFFFF,&H80707070,0,0,0,0,100,100,0,0,1,4,2,2,500,500,46,1"
        )
    dialogues = []
    for ev in events:
        text = ev.text.replace("{", "").replace("}", "").replace("\n", "")
        chunks = _split_sentences(text)
        # 表示時間を文字数比で配分 (読み上げ速度が一定という近似)
        total_chars = sum(len(c) for c in chunks)
        t = ev.start
        for chunk in chunks:
            end = t + (ev.end - ev.start) * len(chunk) / total_chars
            dialogues.append(
                f"Dialogue: 0,{_ass_time(t)},{_ass_time(end)},"
                f"{ev.speaker},,0,0,0,,{_wrap_jp(chunk)}"
            )
            t = end
    return (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n"
        "WrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        + "\n".join(styles)
        + "\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
        "Effect, Text\n"
        + "\n".join(dialogues)
        + "\n"
    )
