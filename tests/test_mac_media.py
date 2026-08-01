from __future__ import annotations

import plistlib
import struct
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QSize

import image_triage.imaging as imaging
from image_triage.file_ops import plan_rename_bundle_paths
from image_triage.mac_media import (
    APPLEDOUBLE_MAGIC,
    existing_mac_sidecar_paths,
    read_apple_adjustment_sidecar,
    read_appledouble_sidecar,
)
from image_triage.models import ImageRecord
from image_triage.xmp import sidecar_bundle_paths


def _write_appledouble(path: Path) -> None:
    payload = b"metadata"
    descriptor_offset = 26 + 12
    path.write_bytes(
        struct.pack(">II16sHIII", APPLEDOUBLE_MAGIC, 0x00020000, b"\0" * 16, 1, 9, descriptor_offset, len(payload))
        + payload
    )


class MacMediaTests(unittest.TestCase):
    def test_heic_image_decodes_through_pillow_heif(self) -> None:
        if imaging.pillow_heif is None:
            self.skipTest("pillow-heif is not installed")
        with tempfile.TemporaryDirectory(prefix="image_triage_heic_") as temp_dir:
            image_path = Path(temp_dir) / "IMG_0001.HEIC"
            Image.new("RGB", (96, 64), (24, 96, 180)).save(image_path, format="HEIF")

            decoded, error = imaging.load_image_for_display(
                str(image_path),
                QSize(80, 80),
                prefer_embedded=True,
            )

            self.assertFalse(decoded.isNull(), error)
            self.assertLessEqual(decoded.width(), 80)
            self.assertLessEqual(decoded.height(), 80)

    def test_reads_appledouble_and_apple_adjustment_sidecars(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_mac_sidecars_") as temp_dir:
            image_path = Path(temp_dir) / "IMG_0001.HEIC"
            image_path.write_bytes(b"heic-placeholder")
            apple_double_path = image_path.with_name(f"._{image_path.name}")
            adjustment_path = image_path.with_suffix(".AAE")
            _write_appledouble(apple_double_path)
            with adjustment_path.open("wb") as stream:
                plistlib.dump(
                    {
                        "adjustmentFormatIdentifier": "com.apple.photo",
                        "adjustmentFormatVersion": "1.0",
                        "adjustmentEditorBundleID": "com.apple.mobileslideshow",
                        "adjustmentData": b"adjustment-payload",
                    },
                    stream,
                )

            apple_double = read_appledouble_sidecar(apple_double_path)
            adjustment = read_apple_adjustment_sidecar(image_path)

            self.assertIsNotNone(apple_double)
            assert apple_double is not None
            self.assertEqual((9,), apple_double.entry_ids)
            self.assertIsNotNone(adjustment)
            assert adjustment is not None
            self.assertEqual("com.apple.photo", adjustment.format_identifier)
            self.assertGreater(adjustment.adjustment_data_size, 0)
            self.assertEqual(
                {str(apple_double_path), str(adjustment_path)},
                set(existing_mac_sidecar_paths(image_path)),
            )

    def test_mac_sidecars_stay_with_image_bundle_and_rename(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_mac_bundle_") as temp_dir:
            image_path = Path(temp_dir) / "IMG_0001.HEIC"
            apple_double_path = image_path.with_name(f"._{image_path.name}")
            adjustment_path = image_path.with_suffix(".AAE")
            image_path.write_bytes(b"heic-placeholder")
            _write_appledouble(apple_double_path)
            adjustment_path.write_bytes(plistlib.dumps({"adjustmentData": b"edit"}))
            record = ImageRecord(
                path=str(image_path),
                name=image_path.name,
                size=image_path.stat().st_size,
                modified_ns=image_path.stat().st_mtime_ns,
            )

            bundled = sidecar_bundle_paths(record)
            plans = plan_rename_bundle_paths(
                (record.path, *bundled),
                record.path,
                "Vacation.HEIC",
            )
            targets = {Path(plan.source_path).name: Path(plan.target_path).name for plan in plans}

            self.assertEqual("Vacation.HEIC", targets[image_path.name])
            self.assertEqual("._Vacation.HEIC", targets[apple_double_path.name])
            self.assertEqual("Vacation.AAE", targets[adjustment_path.name])


if __name__ == "__main__":
    unittest.main()
