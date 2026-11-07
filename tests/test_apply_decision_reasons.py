"""Apply AI Decisions says why each frame is a Reject, with plain words for the cascade's pass-1 flags."""

from __future__ import annotations

from pathlib import Path

from PIL import ImageFilter
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QLabel

from image_triage.ai_why import build_ai_why, flag_labels
from image_triage.models import ImageRecord
from tests.test_cascade_run import record, save, scene


class StubThumbnails(QObject):
    thumbnail_ready = Signal(object, object)

    def get_cached(self, record, size):
        return None

    def request_thumbnail(self, record, size, **kwargs):
        return (record.path, size.width())


def rec(name: str) -> ImageRecord:
    return ImageRecord(path=f"C:/photos/{name}", name=name, size=1, modified_ns=1)


def test_the_cascade_flags_have_plain_labels():
    assert flag_labels("blink,soft,clipped,blown_out") == ("Eyes closed", "Softer than its siblings", "More blown highlights than its siblings", "Highlights blown out")
    assert flag_labels("blownout") == ("Blown highlights",)  # the older flags keep working
