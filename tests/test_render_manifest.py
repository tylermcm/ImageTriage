"""The render manifest (audit finding F7): pictures of a photo's edits are current only while what they were made from (the original,
the project or the recipe, the render algorithm) is still what is on disk. A damaged manifest is out of date, never a reason to fall
back to file timestamps; timestamps apply only to photos whose edits were saved before manifests existed."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from image_triage import photocraft_bridge as bridge


def photo(tmp_path) -> tuple[str, Path]:
    original = tmp_path / "IMG.NEF"
    original.write_bytes(b"raw bytes")
    root = tmp_path / ".image_triage_edits"
    root.mkdir(exist_ok=True)
    return str(original), root


def save(image: str, root: Path, *, project: bytes | None = b"project v1", recipe: bytes | None = None, generation: int = 10,
         png: bool = True, display: bool = True, algorithm: int = bridge.RENDER_ALGORITHM, source: str | None = "same") -> dict:
    """Writes what the editor writes for one save (or, with ``project=None``, one recipe render) and returns the manifest."""
    name = Path(image).name
    outputs = {}
    if png:
        (root / f"{name}.photocraft.g{generation}.png").write_bytes(b"png pixels")
        outputs["png"] = {"file": f"{name}.photocraft.g{generation}.png", "format": "png", "width": 8, "height": 6, "depth": 16, "icc": True}
    if display:
        (root / f"{name}.photocraft.display.g{generation}.jpg").write_bytes(b"jpeg pixels")
        outputs["display"] = {"file": f"{name}.photocraft.display.g{generation}.jpg", "format": "jpeg", "width": 8, "height": 6, "depth": 8, "icc": True}
    manifest = {
        "version": 1, "generation": generation, "source": bridge.file_signature(image) if source == "same" else source,
        "renderer": {"algorithm": algorithm, "build": None, "version": "0.9.0"}, "outputs": outputs,
    }
    if project is not None:
        (root / f"{name}.pcraft").write_bytes(project)
        manifest["kind"] = "project"
        manifest["project"] = {"size": len(project), "sha256": hashlib.sha256(project).hexdigest()}
    else:
        (root / f"{name}.cameraraw.json").write_bytes(recipe)
        manifest["kind"] = "recipe"
        manifest["recipe"] = {"sha256": hashlib.sha256(recipe).hexdigest()}
    (root / f"{name}.photocraft.renders.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_a_current_save_is_shown_and_exported_from_its_own_pictures(tmp_path):
    image, root = photo(tmp_path)
    save(image, root)
    assert bridge.saved_render_path(image) == root / "IMG.NEF.photocraft.display.g10.jpg"
    assert bridge.full_render_path(image) == root / "IMG.NEF.photocraft.g10.png"
    assert not bridge.render_is_behind(image)


def test_pictures_of_an_older_project_are_out_of_date_even_when_newer_on_disk(tmp_path):
    image, root = photo(tmp_path)
    save(image, root)
    # The editor restarted and the project was edited and saved by something that wrote no new pictures: same size, other bytes.
    (root / "IMG.NEF.pcraft").write_bytes(b"project v2")
    later = time.time() + 60
    for picture in root.glob("*.g10.*"):
        os.utime(picture, (later, later))
    assert bridge.full_render_path(image) is None, "the project's fingerprint differs: no timestamp makes these current"
    # Different size: even the cheap check (thumbnails, viewer) sees it.
    (root / "IMG.NEF.pcraft").write_bytes(b"project v2, longer")
    assert bridge.saved_render_path(image) is None
    assert bridge.render_is_behind(image)


def test_the_cheap_check_never_reads_the_project(tmp_path, monkeypatch):
    image, root = photo(tmp_path)
    save(image, root)
    monkeypatch.setattr(bridge, "file_sha256", Mock(side_effect=AssertionError("read the project")))
    assert bridge.saved_render_path(image) is not None
    assert bridge.full_render_path(image, verify_project=False) is not None
    assert not bridge.render_is_behind(image)


def test_same_size_project_change_invalidates_cheap_manifest_check(tmp_path):
    image, root = photo(tmp_path)
    save(image, root)
    assert bridge.saved_render_path(image) is not None
    project = bridge.sidecar_pcraft_path(image)
    project.write_bytes(b"project v2")
    stamp = bridge.render_manifest_path(image).stat().st_mtime_ns + 1_000_000
    os.utime(project, ns=(stamp, stamp))
    assert bridge.saved_render_path(image) is None


def test_a_project_is_hashed_once_per_version(tmp_path, monkeypatch):
    image, root = photo(tmp_path)
    save(image, root)
    project = root / "IMG.NEF.pcraft"
    opened = []
    real_open = Path.open

    def counting_open(self, *args, **kwargs):
        if self == project:
            opened.append(1)
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)
    for _ in range(3):
        assert bridge.full_render_path(image) is not None
    assert len(opened) == 1
    with open(project, "wb") as handle:  # not Path.open: only the reader's opens are counted
        handle.write(b"project v3")
    stamp = project.stat().st_mtime_ns + 5_000_000
    os.utime(project, ns=(stamp, stamp))
    assert bridge.full_render_path(image) is None
    assert len(opened) == 2, "a new version of the file is read again"


@pytest.mark.parametrize("change", ["algorithm", "source", "missing picture", "corrupt", "wrong version", "picture elsewhere", "no source"])
def test_anything_that_does_not_match_is_out_of_date_with_no_timestamp_fallback(tmp_path, change):
    image, root = photo(tmp_path)
    manifest = save(image, root)
    # Old fixed-name pictures, newer than everything: the timestamp rules would pick them.
    for legacy in ("IMG.NEF.photocraft.png", "IMG.NEF.photocraft.display.jpg"):
        (root / legacy).write_bytes(b"legacy")
    target = root / "IMG.NEF.photocraft.renders.json"
    if change == "algorithm":
        save(image, root, algorithm=bridge.RENDER_ALGORITHM + 1)
    elif change == "source":
        Path(image).write_bytes(b"the original was replaced")
    elif change == "missing picture":
        (root / "IMG.NEF.photocraft.g10.png").unlink()
    elif change == "corrupt":
        target.write_text(json.dumps(manifest)[:-20], encoding="utf-8")
    elif change == "wrong version":
        target.write_text(json.dumps({**manifest, "version": 99}), encoding="utf-8")
    elif change == "picture elsewhere":
        manifest["outputs"]["png"]["file"] = "../IMG.NEF.photocraft.g10.png"
        target.write_text(json.dumps(manifest), encoding="utf-8")
    elif change == "no source":
        target.write_text(json.dumps({**manifest, "source": None}), encoding="utf-8")
    assert bridge.saved_render_path(image) is None
    assert bridge.full_render_path(image) is None
    assert bridge.render_is_behind(image)


def test_a_recipe_render_is_current_while_the_recipe_is_and_the_photo_has_no_project(tmp_path):
    image, root = photo(tmp_path)
    save(image, root, project=None, recipe=b'{"version": 1, "cameraRaw": {"exposure": 1.0}}', png=False)
    assert bridge.saved_render_path(image) == root / "IMG.NEF.photocraft.display.g10.jpg"
    assert not bridge.render_is_behind(image), "no project: nothing is behind"
    (root / "IMG.NEF.cameraraw.json").write_bytes(b'{"version": 1, "cameraRaw": {"exposure": 2.0}}')
    assert bridge.saved_render_path(image) is None, "the recipe changed since"


def test_a_project_takes_precedence_over_a_recipe_render(tmp_path):
    image, root = photo(tmp_path)
    save(image, root, project=None, recipe=b'{"version": 1, "cameraRaw": {"exposure": 1.0}}', png=False)
    (root / "IMG.NEF.pcraft").write_bytes(b"a project made since")
    assert bridge.saved_render_path(image) is None
    assert bridge.render_is_behind(image)


def test_an_autosave_without_the_full_size_render_leaves_it_behind(tmp_path):
    image, root = photo(tmp_path)
    save(image, root, png=False)
    assert bridge.saved_render_path(image) == root / "IMG.NEF.photocraft.display.g10.jpg"
    assert bridge.render_is_behind(image)


def test_without_a_manifest_the_timestamp_rules_still_apply(tmp_path):
    image, root = photo(tmp_path)
    (root / "IMG.NEF.pcraft").write_bytes(b"project")
    png = root / "IMG.NEF.photocraft.png"
    display = root / "IMG.NEF.photocraft.display.jpg"
    png.write_bytes(b"png")
    display.write_bytes(b"jpeg")
    assert bridge.saved_render_path(image) == display
    assert bridge.full_render_path(image) == png
    assert not bridge.render_is_behind(image)


def test_pictures_no_manifest_names_are_cleared_after_a_grace_period(tmp_path):
    image, root = photo(tmp_path)
    save(image, root, generation=10)
    stale = [root / "IMG.NEF.photocraft.g7.png", root / "IMG.NEF.photocraft.display.g7.jpg"]
    young = root / "IMG.NEF.photocraft.display.g11.jpg"
    unrelated = [root / "IMG.NEF.photocraft.png", root / "OTHER.NEF.photocraft.g7.png", root / "IMG.NEF.photocraft.gx.png"]
    for path in (*stale, young, *unrelated):
        path.write_bytes(b"x")
    old = time.time() - bridge.UNREFERENCED_RENDER_GRACE_S - 5
    for path in (*stale, *unrelated, root / "IMG.NEF.photocraft.g10.png"):
        os.utime(path, (old, old))
    assert bridge.prune_render_generations(image) == 2
    assert not any(path.exists() for path in stale)
    assert young.exists(), "may belong to a save still writing its manifest"
    assert all(path.exists() for path in unrelated)
    assert (root / "IMG.NEF.photocraft.g10.png").exists(), "named by the manifest"


def test_nothing_is_cleared_while_the_manifest_cannot_be_read(tmp_path):
    image, root = photo(tmp_path)
    save(image, root)
    (root / "IMG.NEF.photocraft.renders.json").write_text("{", encoding="utf-8")
    old = time.time() - bridge.UNREFERENCED_RENDER_GRACE_S - 5
    picture = root / "IMG.NEF.photocraft.g10.png"
    os.utime(picture, (old, old))
    assert bridge.prune_render_generations(image) == 0
    assert picture.exists()


def test_the_render_algorithm_is_the_editor_s():
    source = Path(__file__).resolve().parents[2] / "photocraft" / "crates" / "ui-egui" / "src" / "render_manifest.rs"
    if not source.is_file():
        pytest.skip("the PhotoCraft checkout is not beside this one")
    match = re.search(r"pub const ALGORITHM: u64 = (\d+);", source.read_text(encoding="utf-8"))
    assert match and int(match.group(1)) == bridge.RENDER_ALGORITHM


# --- what the host asks the editor for -------------------------------------------------------------------------------------


def control(manifest_supported):
    stand_in = bridge.PhotoCraftControl.__new__(bridge.PhotoCraftControl)
    stand_in.camera_raw_supported = True
    stand_in.render_manifest_supported = manifest_supported
    stand_in.call = Mock(return_value={})
    return stand_in


@pytest.mark.parametrize("supported", [True, False])
def test_the_manifest_path_reaches_only_an_editor_that_keeps_manifests(supported):
    stand_in = control(supported)
    spec = {"recipePath": "a.NEF.cameraraw.json", "displayPath": "a.NEF.photocraft.display.jpg", "manifestPath": "a.NEF.photocraft.renders.json"}
    stand_in.app_open("a.NEF", camera_raw=spec)
    sent = stand_in.call.call_args.args[1]["cameraRaw"]
    assert ("manifestPath" in sent) is supported
    assert "manifestPath" in spec, "the caller's spec is left as it was"


def test_saves_and_the_binding_ask_for_the_manifest_when_the_editor_keeps_one(qapp_controller, tmp_path):
    from tests.test_lean_autosave import editing, stashes

    proc = editing(tmp_path, lean_supported=True)
    Path(proc.source_path).write_bytes(b"raw")
    proc.control.render_manifest_supported = True
    qapp_controller._save_photocraft_sidecar_if_dirty(proc)
    (params,) = stashes(proc)
    assert params["manifest"] == "IMG.NEF.photocraft.renders.json"
    assert params["source"] == bridge.file_signature(proc.source_path)
    assert params["display"] == "IMG.NEF.photocraft.display.jpg"
    assert qapp_controller._render_manifest_params(proc, proc.source_path)["display"] == "IMG.NEF.photocraft.display.jpg"
    proc.control.render_manifest_supported = Mock()  # merely truthy: not support
    assert qapp_controller._render_manifest_params(proc, proc.source_path) == {}


@pytest.fixture
def qapp_controller():
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication

    from image_triage.preview_controller import PreviewController

    app = QApplication.instance() or QApplication([])
    parent = QObject()
    ctl = PreviewController(parent)
    ctl._parent_keepalive = parent
    yield ctl
    ctl._photocraft_save_timer.stop()
    ctl._photocraft_dwell_timer.stop()
    ctl._photocraft_executor.shutdown(wait=True)
    ctl._photocraft_preview_executor.shutdown(wait=True)
    app.processEvents()
