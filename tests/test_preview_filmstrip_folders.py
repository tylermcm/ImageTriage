"""The popout preview must stay inside the folder it was opened in.

A folder's record list also holds its subfolders as entries (sorted first). The popout's filmstrip and next/previous
navigation used to walk that list by index, so stepping back from the first photo landed on a subfolder entry, which
``open_preview`` handles by *entering that folder*: the filmstrip lost its thumbnails and the user was pushed out of the
folder they were reviewing. The popout now browses photos only; subfolder entries stay in the grid.
"""
from __future__ import annotations

from pathlib import Path

from tests.harness import make_jpegs, open_folder, pump_until


def _open_shoot(window, tmp_path: Path) -> Path:
    folder = tmp_path / "shoot"
    make_jpegs(folder, ["a.jpg", "b.jpg", "c.jpg"])
    make_jpegs(folder / "sub", ["inner.jpg"])
    open_folder(window, folder, expected_count=4)  # the subfolder entry plus three photos
    return folder


def _index_of(window, name: str) -> int:
    return next(i for i, record in enumerate(window._records) if Path(record.path).name == name)


def _show_popout_on(window, name: str) -> None:
    """What double-clicking a photo does: the grid's current photo is that one, then the popout opens on it."""
    index = _index_of(window, name)
    window.grid.set_current_index(index)
    window._preview_ctl.open_preview(index)


def _current_name(window) -> str:
    return Path(window._records[window.grid.current_index()].path).name


def test_stepping_back_from_the_first_photo_does_not_enter_the_subfolder(main_window, dialogs, tmp_path) -> None:
    window = main_window
    folder = _open_shoot(window, tmp_path)
    assert window._records[0].is_folder  # the premise: the subfolder entry sits in front of the photos

    _show_popout_on(window, "a.jpg")
    window._preview_ctl.navigate_preview(-1)
    window._preview_ctl.navigate_preview(-1)

    assert Path(window._current_folder) == folder
    assert _current_name(window) == "a.jpg"  # clamped on the first photo


def test_stepping_forward_stays_inside_and_stops_on_the_last_photo(main_window, dialogs, tmp_path) -> None:
    window = main_window
    folder = _open_shoot(window, tmp_path)

    _show_popout_on(window, "a.jpg")
    window._preview_ctl.navigate_preview(1)
    assert _current_name(window) == "b.jpg"
    window._preview_ctl.navigate_preview(1)
    window._preview_ctl.navigate_preview(1)  # past the last photo

    assert Path(window._current_folder) == folder
    assert _current_name(window) == "c.jpg"


def test_a_filmstrip_jump_counts_photos_not_records(main_window, dialogs, tmp_path) -> None:
    """The filmstrip reports "go N cells over"; with the subfolder entry out of its list, N is in photos."""
    window = main_window
    _open_shoot(window, tmp_path)

    _show_popout_on(window, "a.jpg")
    window._preview_ctl.navigate_preview(2)

    assert _current_name(window) == "c.jpg"


def test_the_filmstrip_is_told_about_photos_only(main_window, dialogs, tmp_path, monkeypatch) -> None:
    window = main_window
    _open_shoot(window, tmp_path)
    window.preview  # build the popout so its filmstrip can be observed
    seen: list[tuple[int, int, bool]] = []

    def record_context(total, current, thumb_provider=None, tag_provider=None) -> None:
        # the provider for cell 0 must be the first photo, never the subfolder entry (which has no thumbnail)
        seen.append((total, current, thumb_provider is not None and tag_provider is not None))

    monkeypatch.setattr(window.preview, "set_browse_context", record_context)

    _show_popout_on(window, "a.jpg")
    assert seen[-1] == (3, 0, True)  # three photos, first one selected; not 4 records with the folder at 0
    window._preview_ctl.navigate_preview(1)
    assert seen[-1] == (3, 1, True)


def test_a_subfolder_entry_does_not_open_while_the_popout_is_showing(main_window, dialogs, tmp_path) -> None:
    window = main_window
    folder = _open_shoot(window, tmp_path)

    _show_popout_on(window, "b.jpg")
    assert pump_until(window._preview_ctl.preview_is_visible, timeout=5)
    window._preview_ctl.open_preview(0)  # a stray request for the subfolder entry while browsing photos

    assert Path(window._current_folder) == folder
