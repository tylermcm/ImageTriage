"""The inspector's Capture section: the camera, lens, exposure, focal length, capture time and pixel size of the selected photo, read from the photo itself.

The values are the Widgets app's own (`metadata.load_capture_metadata`, its EXIF reader, the same one its inspector and its metadata filters use), read for
the photo on the one worker thread that skips photos the person has already moved past (photo_jobs.py). The pixel size is the EXIF size when the file
has one, else what the image reader says without decoding the picture (a RAW the reader cannot open and EXIF does not size has none).
`captureData.get(path)` is what QML reads: {"ready": true, "camera", "lens", "exposure", "focal", "captured", "pixels", "width", "height"}; a field the
file does not have is "".
"""
from __future__ import annotations

from PySide6.QtCore import QObject

from .photo_jobs import LatestPhotoJobs

CACHE_SIZE = 400                        # a flick through a folder keeps its last few hundred, and the next visit to one of them costs nothing


def compute(path: str) -> dict | None:
    """The capture data of one photo, or None when it cannot be read. Reads the file: only ever called from a worker thread."""
    from PySide6.QtGui import QImageIOHandler, QImageReader

    from ..metadata import load_capture_metadata

    meta = load_capture_metadata(path)
    width, height = meta.width, meta.height
    if not (width > 0 and height > 0):
        reader = QImageReader(path)
        size = reader.size()
        width, height = size.width(), size.height()
        if reader.transformation() & QImageIOHandler.Transformation.TransformationRotate90:
            width, height = height, width                 # a portrait photo stored sideways is the size it is shown (the reader's size is the stored one)
    if not meta.camera and not meta.captured_at and not meta.lens and not (width > 0 and height > 0):
        return None                                       # nothing could be read from it
    return {
        "camera": meta.camera, "lens": meta.lens, "focal": meta.focal_length, "captured": meta.captured_at,
        "exposure": " · ".join(part for part in (meta.exposure, meta.aperture, meta.iso) if part),
        "pixels": f"{width} × {height}" if width > 0 and height > 0 else "", "width": max(0, width), "height": max(0, height),
    }


class CaptureService(LatestPhotoJobs):
    """`captureData.get(path)` and `request(path)` for QML (see photo_jobs.py); the loader is `compute` unless a test gives another."""

    def __init__(self, loader=None, parent: QObject | None = None) -> None:
        super().__init__(loader or compute, parent, CACHE_SIZE)
