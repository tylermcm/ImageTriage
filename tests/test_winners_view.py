"""The Winners view: every photo marked winner, from every shoot, grouped by folder
(docs/winners_collections_redesign.md, step 2)."""
from __future__ import annotations

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from image_triage.catalog_controller import arrange_winner_records
from image_triage.models import ImageRecord
from tests.harness import make_jpegs, open_folder, pump_until


def _record(path: str, modified_ns: int) -> ImageRecord:
    return ImageRecord(path=path, name=os.path.basename(path), size=1, modified_ns=modified_ns)


def test_grouped_newest_shoot_first_with_a_folder_tile_per_shoot() -> None:
    records = [
        _record("C:/shoots/2024 Moab/b.jpg", 10),
        _record("C:/shoots/2025 Zion/z.jpg", 50),
        _record("C:/shoots/2024 Moab/a.jpg", 20),
    ]
    arranged = arrange_winner_records(records, grouped=True)
    assert [(r.is_folder, r.name) for r in arranged] == [
        (True, "2025 Zion  \u00b7  1"),
        (False, "z.jpg"),
        (True, "2024 Moab  \u00b7  2"),
        (False, "a.jpg"),
        (False, "b.jpg"),
    ]
    assert Path(arranged[0].path).name == "2025 Zion"


def test_flat_has_no_folder_tiles() -> None:
    records = [_record("C:/s/x/a.jpg", 1), _record("C:/s/y/b.jpg", 2)]
    assert all(not r.is_folder for r in arrange_winner_records(records, grouped=False))


def test_winners_view_collects_every_shoot_and_hides_missing_files(main_window, tmp_path) -> None:
    window = main_window
    window._fast_rating_hint_disabled = True
    moab = tmp_path / "2024 Moab"
    zion = tmp_path / "2025 Zion"
    moab_paths = make_jpegs(moab, ["m1.jpg", "m2.jpg"])
    zion_paths = make_jpegs(zion, ["z1.jpg", "z2.jpg"])
    time.sleep(0.02)
    os.utime(zion_paths[0])  # Zion is the newer shoot
    # The test session's window is shared, so other tests' winners may already be there.
    before = window._catalog.winner_count()

    def mine() -> list[tuple[bool, str]]:
        root = str(tmp_path).casefold()
        return [(r.is_folder, Path(r.path).name) for r in window._records if str(r.path).casefold().startswith(root)]

    for folder, paths in ((moab, moab_paths), (zion, zion_paths)):
        open_folder(window, folder, len(paths))
        window.grid.set_current_index(window._record_index_by_path[next(r.path for r in window._records if r.name == Path(paths[0]).name)])
        window.grid.setFocus()
        QTest.keyClick(window.grid, Qt.Key.Key_W)
        assert not (folder / "_winners").exists(), "W marks only; it never creates a _winners folder"
    window._annotation_persistence_queue.flush_blocking()
    assert pump_until(lambda: window._catalog.winner_count() == before + 2, 10)

    window._projects.refresh_winners_row()
    assert window.winners_list.item(0).text() == f"\u2665 Winners  ({before + 2})"

    window._catalog.open_winners_view()
    assert window._scope_kind == "winners"
    assert mine() == [(True, "2025 Zion"), (False, "z1.jpg"), (True, "2024 Moab"), (False, "m1.jpg")]

    # A winner whose file is gone is left out rather than shown broken.
    os.remove(moab_paths[0])
    window._catalog.open_winners_view()
    assert mine() == [(True, "2025 Zion"), (False, "z1.jpg")]

    window._catalog.set_winners_grouped(False)
    try:
        assert mine() == [(False, "z1.jpg")]
    finally:
        window._catalog.set_winners_grouped(True)
