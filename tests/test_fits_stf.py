from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QImage

from image_triage.imaging import FitsDisplaySettings, load_image_for_display

try:
    from astropy.io import fits
except ImportError:  # pragma: no cover - depends on local environment
    fits = None


def _mean_luminance(image: QImage) -> float:
    grayscale = image.convertToFormat(QImage.Format.Format_Grayscale8)
    if grayscale.isNull() or grayscale.width() <= 0 or grayscale.height() <= 0:
        return 0.0
    total = 0
    for y in range(grayscale.height()):
        for x in range(grayscale.width()):
            total += grayscale.pixelColor(x, y).value()
    return total / float(grayscale.width() * grayscale.height())


class FitsStfTests(unittest.TestCase):
    def test_fits_stf_presets_change_preview_tone_mapping(self) -> None:
        if fits is None:
            self.skipTest("astropy is not installed")

        with tempfile.TemporaryDirectory(prefix="image_triage_fits_stf_") as temp_dir:
            path = Path(temp_dir) / "gradient.fits"
            axis = np.linspace(0.0, 1.0, 128, dtype=np.float32)
            data = np.outer(axis, axis)
            fits.PrimaryHDU(data=data).writeto(path, overwrite=True)

            linear_image, linear_error = load_image_for_display(
                str(path),
                QSize(96, 96),
                prefer_embedded=False,
                fits_display_settings=FitsDisplaySettings(stf_preset_id="linear"),
            )
            strong_image, strong_error = load_image_for_display(
                str(path),
                QSize(96, 96),
                prefer_embedded=False,
                fits_display_settings=FitsDisplaySettings(stf_preset_id="strong"),
            )

            self.assertFalse(linear_image.isNull(), linear_error)
            self.assertFalse(strong_image.isNull(), strong_error)
            self.assertIsNone(linear_error)
            self.assertIsNone(strong_error)
            self.assertGreater(_mean_luminance(strong_image), _mean_luminance(linear_image) + 5.0)

    def test_basic_fits_fallback_decodes_without_astropy(self) -> None:
        if fits is None:
            self.skipTest("astropy is not installed")

        with tempfile.TemporaryDirectory(prefix="image_triage_fits_fallback_") as temp_dir:
            path = Path(temp_dir) / "fallback.fits"
            data = (np.linspace(0, 65535, 128 * 128, dtype=np.uint16).reshape(128, 128)).astype(">u2")
            fits.PrimaryHDU(data=data).writeto(path, overwrite=True)

            with patch("image_triage.imaging._import_astropy_modules", return_value=(None, None)):
                image, error = load_image_for_display(
                    str(path),
                    QSize(96, 96),
                    prefer_embedded=True,
                    fits_display_settings=FitsDisplaySettings(stf_preset_id="auto"),
                )

            self.assertFalse(image.isNull(), error)
            self.assertIsNone(error)


if __name__ == "__main__":
    unittest.main()
