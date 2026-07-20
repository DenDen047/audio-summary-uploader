from pathlib import Path

import pytest

from lecture.slides import _background_for


def test_background_mood_resolves_to_existing_asset() -> None:
    mood, url = _background_for({"background_mood": "warm"})

    assert mood == "warm"
    assert Path(url.removeprefix("file://")).exists()


def test_background_moods_use_same_fixed_asset() -> None:
    urls = {
        _background_for({"background_mood": mood})[1]
        for mood in ("explain", "safety", "warm")
    }

    assert len(urls) == 1


def test_unknown_background_mood_fails_fast() -> None:
    with pytest.raises(RuntimeError, match="不明な background_mood"):
        _background_for({"background_mood": "nightmare"})
