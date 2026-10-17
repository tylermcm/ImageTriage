"""Step 5 of docs/winners_collections_redesign.md: the "Add all to collection" bar shows up
when the grid is narrowed, and the Collections menu keeps only the essentials."""
from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from image_triage.models import FilterMode
from tests.harness import make_jpegs, open_folder, pump_until


def test_bar_follows_filters_and_selection(main_window, tmp_path) -> None:
    window = main_window
    window._fast_rating_hint_disabled = True
    shoot = tmp_path / "Shoot"
    make_jpegs(shoot, ["a.jpg", "b.jpg", "c.jpg"])
    open_folder(window, shoot, 3)
    assert window.add_all_bar.isHidden(), "nothing narrowed yet"

    window._annotation_ctl.batch_set_winner([r for r in window._records if r.name in {"a.jpg", "b.jpg"}])
    window.actions.filter_actions[FilterMode.WINNERS].trigger()
    assert pump_until(lambda: len(window._records) == 2, 5)
    assert not window.add_all_bar.isHidden()
    assert window.add_all_label.text() == "2 photos match"
    assert window.add_all_button.text() == "Add All to Collection..."
    with patch.object(window._catalog, "add_visible_to_collection") as add_visible:
        window.add_all_button.click()
    add_visible.assert_called_once()

    window.grid.set_selected_indexes([0, 1], current_index=0)
    assert window.add_all_label.text() == "2 photos selected"
    with patch.object(window._catalog, "add_selected_to_collection") as add_selected:
        window.add_all_button.click()
    add_selected.assert_called_once()

    window._catalog.begin_collection_mode("create")
    assert window.add_all_bar.isHidden(), "collection mode has its own bar"
    window._catalog.cancel_collection_mode(show_message=False)

    window.grid.set_selected_indexes([0], current_index=0)
    window.actions.clear_filters.trigger()
    assert pump_until(lambda: len(window._records) == 3, 5)
    assert window.add_all_bar.isHidden()


def test_collections_menu_keeps_the_essentials(main_window) -> None:
    window = main_window
    library = next(action.menu() for action in window.menuBar().actions() if action.text().replace("&", "") == "Library")
    collections = next(action.menu() for action in library.actions() if action.text() == "Collections")
    top = [action.text() for action in collections.actions() if action.text()]
    assert top[:5] == ["New Collection...", "Add Selection to Collection...", "Export...", "Also Add Winners To...", "More"]
    more = next(action.menu() for action in collections.actions() if action.text() == "More")
    assert "Delete Collection..." in [action.text() for action in more.actions()]
