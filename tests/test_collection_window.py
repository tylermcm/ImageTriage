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


def test_create_then_edit(main_window, monkeypatch) -> None:
    window = main_window

    try:
        catalog = window._catalog
        catalog.set_active_collection("", show_message=False)
        toolbar = window._toolbar
        QApplication.instance().processEvents()
        assert toolbar.app_search_key_hint.isVisibleTo(window) and not toolbar.also_add_to_button.isVisibleTo(window)

        monkeypatch.setattr(window, "_exec_dialog_with_geometry", _fill_and_accept("Utah Portfolio", color="#3ccf8e"))
        created = catalog.open_collection_window()
        assert created is not None and created.item_count == 0, "it starts empty"
        stored = window._library_store.load_collection_header(created.id)
        assert (stored.name, stored.color) == ("Utah Portfolio", "#3ccf8e")
        assert catalog.active_collection_id() == created.id

        row = _row(window, created.id)
        assert row.text() == "Utah Portfolio  (0)" and not row.icon().isNull() and row.font().bold()
        assert toolbar.also_add_to_button.isVisibleTo(window) and not toolbar.app_search_key_hint.isVisibleTo(window)
        chip = toolbar.also_add_to_button
        assert chip.text().startswith("Also add to: Utah") and chip.text().endswith(" ▾"), "long names are shortened"
        assert "Utah Portfolio" in chip.toolTip()
        width_with_chip = toolbar.app_search_box.maximumWidth()

        # Edit: rename, recolor, and stop taking new winners -> Ctrl K comes back.
        monkeypatch.setattr(window, "_exec_dialog_with_geometry", _fill_and_accept("Utah Picks", color="#ff6b7d", add_new_winners=False))
        catalog.edit_collection(created.id)
        stored = window._library_store.load_collection_header(created.id)
        assert (stored.name, stored.color) == ("Utah Picks", "#ff6b7d")
        assert catalog.active_collection_id() == ""
        assert not _row(window, created.id).font().bold()
        assert toolbar.app_search_key_hint.isVisibleTo(window) and not toolbar.also_add_to_button.isVisibleTo(window)
        assert toolbar.app_search_box.maximumWidth() < width_with_chip, "the box makes room for the chip, then gives it back"
    finally:
        window._catalog.set_active_collection("", show_message=False)  # the window is shared: leave it as found


def test_edit_collection_items_is_gone(main_window) -> None:
    assert not hasattr(main_window.actions, "add_selection_to_collection")


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
