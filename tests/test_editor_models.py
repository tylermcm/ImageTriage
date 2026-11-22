"""The RAW editor's models in the AI Center: recognised by their digest, installed from a picked file, removed. A temporary folder
stands in for the managed model folders."""
from __future__ import annotations

import hashlib
import os

import pytest

from image_triage import editor_models
from image_triage.editor_models import EditorModel, ModelFile


@pytest.fixture
def folders(tmp_path, monkeypatch):
    denoise, upscale, x4 = b"denoise weights", b"x2 weights", b"x4 weights!"
    sha = lambda data: hashlib.sha256(data).hexdigest()
    models = {
        "denoise": EditorModel("denoise", "AI Denoise", 30, (ModelFile(tmp_path / "RawNIND", "rawnind_bayer.onnx", sha(denoise)),)),
        "superres": EditorModel("superres", "Super Resolution", 134, (ModelFile(tmp_path / "ESRGAN", "x2.onnx", sha(upscale), len(upscale)),
                                                                       ModelFile(tmp_path / "ESRGAN", "x4.onnx", sha(x4), len(x4)))),
    }
    monkeypatch.setattr(editor_models, "models", lambda: models)
    picked = tmp_path / "Downloads"
    picked.mkdir()
    for name, data in (("my denoise.onnx", denoise), ("x2.onnx", upscale), ("x4.onnx", x4), ("other.onnx", b"something else")):
        (picked / name).write_bytes(data)
    return tmp_path, picked


def test_a_picked_file_is_recognised_by_its_contents_not_its_name_and_installed(folders):
    root, picked = folders
    assert editor_models.status("denoise")["state"] == "missing"
    assert editor_models.install_file(picked / "my denoise.onnx") == ("denoise", "rawnind_bayer.onnx")
    assert (root / "RawNIND" / "rawnind_bayer.onnx").read_bytes() == b"denoise weights"
    assert editor_models.status("denoise")["state"] == "ready"
    assert not list((root / "RawNIND").glob("*.part"))


def test_a_file_that_is_no_model_is_refused_and_names_the_files_it_takes(folders):
    _root, picked = folders
    with pytest.raises(ValueError, match="rawnind_bayer.onnx"):
        editor_models.install_file(picked / "other.onnx")


def test_super_resolution_is_ready_only_with_both_files_and_a_changed_file_is_damaged(folders):
    root, picked = folders
    editor_models.install_file(picked / "x2.onnx")
    state = editor_models.status("superres")
    assert state["state"] == "partial" and "x4.onnx" in state["problem"]
    editor_models.install_file(picked / "x4.onnx")
    assert editor_models.status("superres")["state"] == "ready"
    changed = root / "ESRGAN" / "x4.onnx"
    changed.write_bytes(b"x4 weights?")                                # same size, other bytes
    # A file changed after it was checked has a later time. Linux keeps file times in clock ticks of a few milliseconds, so a write this
    # soon after the check can keep the same time: move it on, as any later change does.
    os.utime(changed, ns=(changed.stat().st_atime_ns, changed.stat().st_mtime_ns + 1_000_000_000))
    assert editor_models.status("superres")["state"] == "corrupt"


def test_remove_deletes_the_files(folders):
    root, picked = folders
    editor_models.install_file(picked / "my denoise.onnx")
    editor_models.remove("denoise")
    assert editor_models.status("denoise")["state"] == "missing" and not (root / "RawNIND" / "rawnind_bayer.onnx").exists()
