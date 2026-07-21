from pathlib import Path

import pytest

from lecture.assemble import (
    MOUTH_TOGGLES,
    EyeCatch,
    SubEvent,
    _pose_windows,
    _subtract_intervals,
    _validate_eyecatches,
)


def test_pose_is_held_until_the_next_line_starts() -> None:
    events = [
        SubEvent(0.0, 2.0, "metan", "a", metan_pose="viewer", zunda_pose="listen"),
        SubEvent(2.25, 4.0, "zunda", "b", metan_pose="tease", zunda_pose="default"),
    ]

    windows = _pose_windows(events, total=5.0)

    assert windows[("metan", "viewer")] == [(0.0, 2.25)]
    assert windows[("zunda", "listen")] == [(0.0, 2.25)]
    assert windows[("metan", "tease")] == [(2.25, 5.0)]
    assert windows[("zunda", "default")] == [(2.25, 5.0)]


def test_mio_mouth_is_open_for_less_than_half_of_each_cycle() -> None:
    assert MOUTH_TOGGLES["metan"] == "lt(mod(t,0.56),0.14)"


def test_eyecatch_temporarily_hides_held_character_poses() -> None:
    assert _subtract_intervals([(0.0, 5.0)], [(2.0, 3.2)]) == [
        (0.0, 2.0),
        (3.2, 5.0),
    ]


def test_eyecatch_images_must_be_unique(tmp_path: Path) -> None:
    image = tmp_path / "shared.png"
    first_audio = tmp_path / "first.wav"
    second_audio = tmp_path / "second.wav"
    for path in (image, first_audio, second_audio):
        path.touch()

    with pytest.raises(RuntimeError, match="画像は挿入ごとに別"):
        _validate_eyecatches(
            (
                EyeCatch(2, image, first_audio),
                EyeCatch(3, image, second_audio),
            ),
            scene_count=3,
        )
