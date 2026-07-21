import struct
import wave

from lecture.cli import EYECATCH_ASSETS


def test_eyecatches_use_local_otologic_instrument_audio() -> None:
    audio_names = [audio.name for _, audio in EYECATCH_ASSETS]

    assert audio_names == [
        "eyecatch_practice_otologic_xylophone06-1.wav",
        "eyecatch_recap_otologic_glocken02-4.wav",
    ]


def test_eyecatch_audio_ends_with_half_second_of_silence() -> None:
    for _, audio in EYECATCH_ASSETS:
        with wave.open(str(audio), "rb") as wav:
            tail_frames = wav.getframerate() // 2
            wav.setpos(wav.getnframes() - tail_frames)
            tail = wav.readframes(tail_frames)

        samples = struct.iter_unpack("<h", tail)
        assert max(abs(sample[0]) for sample in samples) == 0
