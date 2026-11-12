"""A catalog refresh does not hold the library locked for the whole walk: a collection can be changed while it runs."""
from __future__ import annotations

import sqlite3

from image_triage import library_store
from image_triage.library_store import LibraryStore


def test_a_collection_can_be_changed_while_a_refresh_is_walking(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_TRIAGE_APPDATA", str(tmp_path / "appdata"))
    root = tmp_path / "library"
    for day in ("day1", "day2", "day3"):
        (root / day).mkdir(parents=True)
        (root / day / "a.jpg").write_bytes(b"\xff\xd8\xff\xd9" + day.encode())
    store = LibraryStore()
    picks = store.create_collection(name="Picks")
    store.add_catalog_root(str(root))
    real_scan = library_store.scan_folder
    written = []

    def scan_and_edit(folder, *args, **kwargs):
        # what the user does while the walk is on a slow folder; it must not wait for the walk to end, so it does not wait at all
        connection = sqlite3.connect(store._db_path, timeout=0)
        try:
            connection.execute(
                "INSERT INTO collection_items (collection_id, item_path, sort_order) VALUES (?, ?, ?)",
                (picks.id, f"{folder}/a.jpg", len(written)),
            )
            connection.commit()
            written.append(folder)
        finally:
            connection.close()
        return real_scan(folder, *args, **kwargs)

    monkeypatch.setattr(library_store, "scan_folder", scan_and_edit)

    summary = store.refresh_catalog([str(root)])

    assert len(written) == 3 and summary.record_count == 3
    assert store.load_collection(picks.id).item_count == 3
