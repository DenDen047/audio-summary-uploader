from lecture.tts import SPEAKER_MAP, SPEED_SCALES


def test_character_voice_styles_and_balanced_speed_scales() -> None:
    assert SPEAKER_MAP["metan"][0] == 66
    assert SPEAKER_MAP["zunda"][0] == 69
    assert SPEED_SCALES == {"zunda": 0.90, "metan": 1.18}
