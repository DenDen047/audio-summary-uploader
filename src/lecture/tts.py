"""VOICEVOX ENGINE (REST) によるセリフ音声合成。"""

from pathlib import Path

import httpx
from loguru import logger

ENGINE_URL = "http://127.0.0.1:50021"
ENGINE_LAUNCH_CMD = (
    "/Applications/VOICEVOX.app/Contents/Resources/vv-engine/run"
    " --host 127.0.0.1 --port 50021"
)
# 同一文の実測尺を合わせる補正。もち子さん(セクシー)は素の発話が長く、
# 満別花丸(ノーマル)は短いため、共通値では掛け合いのテンポ差が大きくなる。
SPEED_SCALES = {"zunda": 0.90, "metan": 1.18}

# 話者名 → (VOICEVOX style_id, 表示名, 字幕色)
# 字幕色は明るいスライド上で読めるよう濃いめ (白フチと組み合わせる)
SPEAKER_MAP = {
    "zunda": (69, "麦野透", "#8A641E"),
    "metan": (66, "紫ノ宮澪", "#B43A73"),
}


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
