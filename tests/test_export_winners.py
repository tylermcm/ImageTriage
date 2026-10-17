"""Export: the only way files leave (docs/winners_collections_redesign.md, step 4) -
copy to a folder, or PocketDrop (one photo loose, several zipped)."""
from __future__ import annotations

import os
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests.harness import make_jpegs, open_folder, pump_until


def _records_from(window, folder: Path, names: list[str]):
    open_folder(window, folder, len(list(folder.glob("*.jpg"))))
    return [next(r for r in window._records if r.name == name) for name in names]


def test_copy_to_folder_keeps_originals_and_never_overwrites(main_window, tmp_path) -> None:
    window = main_window
    moab = tmp_path / "Moab"
    zion = tmp_path / "Zion"
    make_jpegs(moab, ["IMG_0001.jpg", "IMG_0002.jpg"])
    make_jpegs(zion, ["IMG_0001.jpg"])  # same name as a Moab photo
    records = _records_from(window, moab, ["IMG_0001.jpg"]) + _records_from(window, zion, ["IMG_0001.jpg"])
    destination = tmp_path / "Export"
    destination.mkdir()

    with patch("image_triage.catalog_controller.confirm_transfer", return_value=(True, True)), patch(
        "image_triage.catalog_controller.show_transfer_complete"
    ) as complete:
        copied = window._catalog.export_records_to_folder(records, name="Winners", destination_dir=str(destination))

    assert copied == 2
    exported = sorted(p.name for p in destination.iterdir())
    assert len(exported) == 2 and "IMG_0001.jpg" in exported, exported  # the clash got a unique name
    assert (moab / "IMG_0001.jpg").exists() and (zion / "IMG_0001.jpg").exists(), "originals stay put"
    complete.assert_called_once()


def test_pocketdrop_gets_one_photo_loose_and_several_as_one_zip(main_window, tmp_path) -> None:
    window = main_window
    moab = tmp_path / "Photos" / "Moab"
    zion = tmp_path / "Photos" / "Zion"
    make_jpegs(moab, ["m1.jpg"])
    make_jpegs(zion, ["z1.jpg"])
    sent: list[list[str]] = []
    fake_panel = SimpleNamespace(available=True, error="", add_paths=lambda paths: sent.append(list(paths)))
    one = _records_from(window, moab, ["m1.jpg"])
    two = one + _records_from(window, zion, ["z1.jpg"])

    with patch.object(window, "pocketdrop_panel", fake_panel, create=True), patch.object(
        window._handoff, "show_pocketdrop_page"
    ):
        window._catalog.export_records_to_pocketdrop(one, name="Portfolio")
        assert sent == [[str(moab / "m1.jpg")]] or [Path(p).name for p in sent[0]] == ["m1.jpg"]

        window._catalog.export_records_to_pocketdrop(two, name="Portfolio")
        assert pump_until(lambda: len(sent) == 2, 20), window.statusBar().currentMessage()

    zip_path = Path(sent[1][0])
    assert zip_path.suffix == ".zip" and zip_path.name.startswith("Portfolio ")
    with zipfile.ZipFile(zip_path) as archive:
        names = sorted(name.replace("\\", "/") for name in archive.namelist())
    assert names == ["Moab/m1.jpg", "Zion/z1.jpg"], names


def test_export_current_in_a_folder_takes_its_winners(main_window, tmp_path) -> None:
    window = main_window
    shoot = tmp_path / "Shoot"
    make_jpegs(shoot, ["a.jpg", "b.jpg"])
    open_folder(window, shoot, 2)
    window._fast_rating_hint_disabled = True
    window._annotation_ctl.batch_set_winner([next(r for r in window._records if r.name == "b.jpg")])
    captured = {}

    with patch.object(window._catalog, "export_records", side_effect=lambda records, name: captured.update(names=[r.name for r in records], name=name)):
        window._catalog.export_current()
    assert captured == {"names": ["b.jpg"], "name": "Shoot winners"}
