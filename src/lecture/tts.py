"""VOICEVOX ENGINE のライフサイクル管理とセリフ音声合成。"""

import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
from loguru import logger

ENGINE_URL = "http://127.0.0.1:50021"
ENGINE_EXECUTABLE = Path("/Applications/VOICEVOX.app/Contents/Resources/vv-engine/run")
ENGINE_LAUNCH_CMD = f"{ENGINE_EXECUTABLE} --host 127.0.0.1 --port 50021"
ENGINE_START_TIMEOUT_SECONDS = 45
# 同一文の実測尺を合わせる補正。もち子さん(セクシー)は素の発話が長く、
# 満別花丸(ノーマル)は短いため、共通値では掛け合いのテンポ差が大きくなる。
SPEED_SCALES = {"zunda": 0.90, "metan": 1.18}

# 話者名 → (VOICEVOX style_id, 表示名, 字幕色)
# 字幕色は明るいスライド上で読めるよう濃いめ (白フチと組み合わせる)
SPEAKER_MAP = {
    "zunda": (69, "麦野透", "#8A641E"),
    "metan": (66, "紫ノ宮澪", "#B43A73"),
}


def _engine_is_ready() -> bool:
    try:
        response = httpx.get(f"{ENGINE_URL}/version", timeout=1.0)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


@contextmanager
def managed_voicevox_engine(log_path: Path) -> Iterator[None]:
    """必要なときだけローカルENGINEを起動し、生成後に終了する。"""
    if _engine_is_ready():
        # 既存プロセスの所有者は別にいるため、このコンテキストでは停止しない。
        logger.info("起動済みの VOICEVOX ENGINE を使用")
        yield
        return
    if not ENGINE_EXECUTABLE.is_file():
        raise RuntimeError(
            "VOICEVOX ENGINE が起動しておらず、自動起動用の実行ファイルも"
            f"見つかりません。起動コマンド: {ENGINE_LAUNCH_CMD}"
        )

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as engine_log:
        process = subprocess.Popen(
            [
                str(ENGINE_EXECUTABLE),
                "--host",
                "127.0.0.1",
                "--port",
                "50021",
            ],
            stdout=engine_log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        logger.info("VOICEVOX ENGINE を自動起動: pid={}", process.pid)
        deadline = time.monotonic() + ENGINE_START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    "VOICEVOX ENGINE が起動中に終了しました。"
                    f"ログを確認してください: {log_path}"
                )
            if _engine_is_ready():
                break
            time.sleep(0.25)
        else:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            raise RuntimeError(
                "VOICEVOX ENGINE の自動起動がタイムアウトしました。"
                f"ログを確認してください: {log_path}"
            )

        try:
            yield
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            logger.info("自動起動した VOICEVOX ENGINE を終了")


class VoicevoxClient:
    def __init__(self, base_url: str = ENGINE_URL):
        self._client = httpx.Client(base_url=base_url, timeout=120)
        resp = self._client.get("/version")
        if resp.status_code != 200:
            raise RuntimeError(
                f"VOICEVOX ENGINE に接続できない ({base_url})。"
                f"起動コマンド: {ENGINE_LAUNCH_CMD}"
            )
        logger.info("VOICEVOX ENGINE {} に接続", resp.json())

    def synthesize(self, text: str, speaker: str, out_path: Path) -> Path:
        style_id, _, _ = SPEAKER_MAP[speaker]
        query_resp = self._client.post(
            "/audio_query", params={"text": text, "speaker": style_id}
        )
        if query_resp.status_code != 200:
            raise RuntimeError(
                f"audio_query 失敗 (HTTP {query_resp.status_code}): {text[:40]}"
            )
        query = query_resp.json()
        query["speedScale"] = SPEED_SCALES[speaker]

        synth_resp = self._client.post(
            "/synthesis", params={"speaker": style_id}, json=query
        )
        if synth_resp.status_code != 200:
            raise RuntimeError(
                f"synthesis 失敗 (HTTP {synth_resp.status_code}): {text[:40]}"
            )
        out_path.write_bytes(synth_resp.content)
        return out_path


def synthesize_all(script: dict, audio_dir: Path) -> list[list[Path]]:
    """全セリフを合成し、シーンごとの wav パスリストを返す。"""
    audio_dir.mkdir(parents=True, exist_ok=True)
    client = VoicevoxClient()
    scene_wavs = []
    for i, scene in enumerate(script["scenes"], 1):
        wavs = []
        for j, line in enumerate(scene["lines"], 1):
            path = audio_dir / f"scene_{i:02d}_line_{j:02d}.wav"
            client.synthesize(line["reading"], line["speaker"], path)
            wavs.append(path)
        scene_wavs.append(wavs)
        logger.info(
            "TTS: scene {}/{} ({} セリフ) 完了", i, len(script["scenes"]), len(wavs)
        )
    return scene_wavs
