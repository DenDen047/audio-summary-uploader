from pathlib import Path

import pytest
from PIL import Image

from lecture.characters import (
    CUSTOM_BLEEDS,
    CUSTOM_HEIGHTS,
    _prepare_custom,
    prepare_characters,
)


def _write_cutout(
    path: Path, size: tuple[int, int], box: tuple[int, int, int, int]
) -> None:
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    image.paste((120, 80, 160, 255), box)
    image.save(path)


def test_custom_character_heights_are_fixed_by_role(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()

    for speaker in ("metan", "zunda"):
        source = tmp_path / f"{speaker}.png"
        _write_cutout(source, (300, 600), (50, 40, 250, 560))

        assets = _prepare_custom(source, assets_dir, speaker)
        expected_height = CUSTOM_HEIGHTS[speaker]

        assert assets.height == expected_height
        assert assets.bleed == CUSTOM_BLEEDS[speaker]
        with Image.open(assets.image) as normalized:
            assert normalized.height == expected_height


def test_roles_follow_manual_reference_layout() -> None:
    assert CUSTOM_HEIGHTS == {"metan": 1232, "zunda": 1019}
    assert CUSTOM_BLEEDS == {"metan": 357, "zunda": 170}


def test_preserved_canvas_keeps_manual_layout_padding(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    source = tmp_path / "metan.png"
    _write_cutout(source, (300, 600), (50, 40, 250, 560))

    cropped = _prepare_custom(source, assets_dir, "metan")
    preserved = _prepare_custom(
        source, assets_dir, "metan", preserve_canvas=True
    )

    assert preserved.width > cropped.width


def test_small_mouth_patch_is_detected(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    closed = tmp_path / "zunda.png"
    opened = tmp_path / "zunda_open.png"
    _write_cutout(closed, (300, 600), (50, 100, 250, 600))
    _write_cutout(opened, (300, 600), (50, 100, 250, 600))

    with Image.open(opened).convert("RGBA") as image:
        image.paste((20, 20, 20, 255), (142, 190, 158, 198))
        image.save(opened)

    assets = _prepare_custom(closed, assets_dir, "zunda")

    assert assets.mouth_patch is not None


def test_open_variant_rejects_changes_outside_a_local_mouth_area(
    tmp_path: Path,
) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    closed = tmp_path / "metan.png"
    opened = tmp_path / "metan_open.png"
    _write_cutout(closed, (300, 600), (50, 40, 250, 560))
    _write_cutout(opened, (300, 600), (50, 40, 250, 560))

    with Image.open(opened).convert("RGBA") as image:
        image.paste((20, 20, 20, 255), (90, 130, 210, 250))
        image.save(opened)

    with pytest.raises(RuntimeError, match="口開き差分が広すぎます"):
        _prepare_custom(closed, assets_dir, "metan")


def test_pose_variants_are_loaded_without_output_collisions(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    custom_dir = tmp_path / "custom"
    custom_dir.mkdir()
    for speaker in ("metan", "zunda"):
        _write_cutout(
            custom_dir / f"{speaker}.png", (300, 600), (50, 40, 250, 560)
        )
    _write_cutout(
        custom_dir / "metan__viewer.png", (360, 600), (30, 40, 330, 560)
    )

    characters = prepare_characters(
        assets_dir, custom_dir=custom_dir, preserve_custom_canvas=True
    )

    assert "metan:viewer" in characters
    assert characters["metan"].image != characters["metan:viewer"].image
    assert characters["metan"].height == characters["metan:viewer"].height


def test_pose_uses_dedicated_open_variant_for_its_own_mouth_patch(
    tmp_path: Path,
) -> None:
    assets_dir = tmp_path / "assets"
    custom_dir = tmp_path / "custom"
    custom_dir.mkdir()
    for speaker in ("metan", "zunda"):
        _write_cutout(
            custom_dir / f"{speaker}.png", (300, 600), (50, 40, 250, 560)
        )
    _write_cutout(
        custom_dir / "metan__viewer.png", (300, 600), (50, 40, 250, 560)
    )
    opened = custom_dir / "metan_open.png"
    _write_cutout(opened, (300, 600), (50, 40, 250, 560))
    with Image.open(opened).convert("RGBA") as image:
        image.paste((20, 20, 20, 255), (142, 190, 158, 198))
        image.save(opened)
    viewer_opened = custom_dir / "metan__viewer_open.png"
    _write_cutout(viewer_opened, (300, 600), (50, 40, 250, 560))
    with Image.open(viewer_opened).convert("RGBA") as image:
        image.paste((40, 20, 30, 255), (150, 205, 170, 215))
        image.save(viewer_opened)

    characters = prepare_characters(
        assets_dir, custom_dir=custom_dir, preserve_custom_canvas=True
    )

    assert characters["metan"].mouth_patch is not None
    viewer = characters["metan:viewer"]
    assert viewer.mouth_patch is not None
    assert viewer.mouth_patch != characters["metan"].mouth_patch
    assert viewer.mouth_patch.exists()
    assert viewer.mouth_patch.name == "metan__viewer_mouth.png"
