"""A raw's size is the size it develops to (LibRaw's crop, turned as shown), not its embedded thumbnail's (a NEF's first image is
160 x 120)."""
import sys
from types import SimpleNamespace

from image_triage import metadata


class FakeRaw:
    def __init__(self, sizes):
        self.sizes = sizes

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def sizes(flip=0, crop=(8256, 5504)):
    return SimpleNamespace(width=8288, height=5520, crop_width=crop[0], crop_height=crop[1], flip=flip)


def test_a_raw_is_its_cropped_size_turned_as_shown(monkeypatch):
    for flip, expected in ((0, (8256, 5504)), (3, (8256, 5504)), (6, (5504, 8256)), (5, (5504, 8256))):
        monkeypatch.setitem(sys.modules, "rawpy", SimpleNamespace(imread=lambda path, s=sizes(flip): FakeRaw(s)))
        assert metadata._raw_dimensions("a.NEF") == expected
    monkeypatch.setitem(sys.modules, "rawpy", SimpleNamespace(imread=lambda path: FakeRaw(sizes(crop=(0, 0)))))
    assert metadata._raw_dimensions("a.NEF") == (8288, 5520)            # no crop known: the image's own size


def test_an_unreadable_raw_has_no_size(monkeypatch):
    def broken(path):
        raise OSError("not a raw")

    monkeypatch.setitem(sys.modules, "rawpy", SimpleNamespace(imread=broken))
    assert metadata._raw_dimensions("a.NEF") is None
