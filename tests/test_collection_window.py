"""New Collection / Edit Collection window in the real app: creates an empty collection with a color,
the sidebar shows its ring, and the search box swaps Ctrl K for the "Also add to" chip."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from image_triage.library_store import LibraryStore


def _fill_and_accept(name: str, *, color: str = "", add_new_winners: bool = True):
    def run(dialog, _key):
        dialog.name_field.setText(name)
        if color:
            dialog._swatches[color].click()
        dialog.add_winners_check.setChecked(add_new_winners)
        return dialog.DialogCode.Accepted
    return run


def _row(window, collection_id):
    panel = window.projects_list
    return next(panel.item(i) for i in range(panel.count()) if panel.item(i).data(0x0100) == collection_id)










def test_older_library_gains_a_color_column(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("IMAGE_TRIAGE_APPDATA", str(tmp_path))
    db = Path(tmp_path) / "ImageTriage" / "library.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE virtual_collections (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',"
            " kind TEXT NOT NULL DEFAULT 'Custom', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,"
            " updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        connection.execute("INSERT INTO virtual_collections (id, name) VALUES ('old', 'Old Set')")
        connection.execute("PRAGMA user_version = 2")
    store = LibraryStore()
    old = store.load_collection_header("old")
    assert (old.name, old.color) == ("Old Set", "")
