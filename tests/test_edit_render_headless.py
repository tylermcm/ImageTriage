from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image

from image_triage.edit_render_headless import render_edited_image
from image_triage.edit_storage import editor_session_path
from image_triage.photo_terminal.adjustments import EditRecipe
from image_triage.photo_terminal.session import SCHEMA_NAME, SCHEMA_VERSION

IMAGE_SIZE = (120, 100)


def _write_source_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", IMAGE_SIZE, color=(120, 130, 140)).save(path)


def _write_session(
    image_path: Path,
    *,
    operations: list[dict] | None = None,
    masks: list[dict] | None = None,
) -> Path:
    session_path = editor_session_path(image_path)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(
        json.dumps(
            {
                "version": SCHEMA_VERSION,
                "schema": SCHEMA_NAME,
                "coordinateSpaces": [
                    {
                        "id": "space-source-full",
                        "sourceWidth": IMAGE_SIZE[0],
                        "sourceHeight": IMAGE_SIZE[1],
                        "cropInEffect": None,
                    }
                ],
                "assets": {"dir": "assets", "bitmapMasks": []},
                "operations": operations or [],
                "masks": masks or [],
            }
        )
    )
    return session_path


class RenderEditedImageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory(prefix="edit_render_headless_")
        self.dir = Path(self._tmp.name)
        self.image_path = self.dir / "IMG_0001.jpg"
        _write_source_image(self.image_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_session_returns_none(self) -> None:
        self.assertIsNone(render_edited_image(self.image_path))

    def test_opened_but_untouched_session_returns_none(self) -> None:
        _write_session(self.image_path, operations=[], masks=[])

        self.assertIsNone(render_edited_image(self.image_path))

    def test_global_adjustment_renders_and_differs_from_plain_decode(self) -> None:
        _write_session(
            self.image_path,
            operations=[
                {
                    "id": "op-1",
                    "type": "adjust.exposure",
                    "enabled": True,
                    "params": {"exposure": 1.5},
                }
            ],
        )

        rendered = render_edited_image(self.image_path)

        self.assertIsNotNone(rendered)
        self.assertFalse(rendered.isNull())
        self.assertEqual(IMAGE_SIZE, (rendered.width(), rendered.height()))
        base = Image.open(self.image_path).convert("RGB")
        # A strong exposure boost must change pixel values from the flat
        # source color; comparing one pixel is enough to prove the recipe
        # was actually applied, not just decoded and passed through.
        rendered_pixel = rendered.pixelColor(0, 0)
        self.assertNotEqual(base.getpixel((0, 0)), (rendered_pixel.red(), rendered_pixel.green(), rendered_pixel.blue()))

    def test_mask_group_renders_without_error(self) -> None:
        _write_session(
            self.image_path,
            operations=[
                {
                    "id": "op-1",
                    "type": "adjust.exposure",
                    "enabled": True,
                    "maskId": "mask-1",
                    "params": {"exposure": 1.5},
                }
            ],
            masks=[
                {
                    "id": "mask-1",
                    "type": "radial",
                    "enabled": True,
                    "params": {
                        "cx": 60,
                        "cy": 50,
                        "rx": 40,
                        "ry": 30,
                        "angle": 0,
                        "density": 100,
                        "feather": 50,
                        "invert": False,
                    },
                }
            ],
        )

        rendered = render_edited_image(self.image_path)

        self.assertIsNotNone(rendered)
        self.assertFalse(rendered.isNull())
        self.assertEqual(IMAGE_SIZE, (rendered.width(), rendered.height()))

    def test_background_mode_without_cached_matte_still_renders(self) -> None:
        # EditRecipe.background_mode / lensblur_amount are not currently
        # persisted to the saved session schema at all (see
        # edit_render_headless's module docstring, limitation 1), so a real
        # session can never carry a non-off background_mode today. This test
        # exercises the code path anyway by patching recipe_from_session (the
        # one seam render_edited_image reads the global recipe through) to
        # return a recipe with background_mode set, while the on-disk
        # session still supplies a real global adjustment so
        # session_has_edits is true. It proves: when the subject-mask cache
        # has nothing for this source (guaranteed here -- no BiRefNet model
        # is installed in the test environment), the background effect is
        # silently skipped and the rest of the render still completes.
        _write_session(
            self.image_path,
            operations=[
                {
                    "id": "op-1",
                    "type": "adjust.exposure",
                    "enabled": True,
                    "params": {"exposure": 1.5},
                }
            ],
        )
        blurred_recipe = EditRecipe.from_dict({"exposure": 1.5, "background_mode": "blur", "background_amount": 80.0})

        with patch(
            "image_triage.ui.photo_editor_panel.recipe_from_session",
            return_value=blurred_recipe,
        ):
            rendered = render_edited_image(self.image_path)

        self.assertIsNotNone(rendered)
        self.assertFalse(rendered.isNull())
        self.assertEqual(IMAGE_SIZE, (rendered.width(), rendered.height()))

    def test_missing_source_file_returns_none_not_a_crash(self) -> None:
        _write_session(
            self.image_path,
            operations=[{"id": "op-1", "type": "adjust.exposure", "enabled": True, "params": {"exposure": 1.5}}],
        )
        self.image_path.unlink()

        self.assertIsNone(render_edited_image(self.image_path))


if __name__ == "__main__":
    unittest.main()
