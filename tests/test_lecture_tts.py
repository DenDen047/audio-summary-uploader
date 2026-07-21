from pathlib import Path
from unittest.mock import patch

from lecture.tts import SPEAKER_MAP, SPEED_SCALES, managed_voicevox_engine


def test_character_voice_styles_and_balanced_speed_scales() -> None:
    assert SPEAKER_MAP["metan"][0] == 66
    assert SPEAKER_MAP["zunda"][0] == 69
    assert SPEED_SCALES == {"zunda": 0.90, "metan": 1.18}


def test_managed_engine_reuses_running_voicevox(tmp_path: Path) -> None:
    with (
        patch("lecture.tts._engine_is_ready", return_value=True),
        patch("lecture.tts.subprocess.Popen") as popen,
        managed_voicevox_engine(tmp_path / "voicevox.log"),
    ):
        pass

    popen.assert_not_called()
