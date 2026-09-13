from __future__ import annotations

from types import SimpleNamespace

from image_triage.models import ImageRecord
from image_triage.window import MainWindow


class _GridStub:
    def __init__(self, current: int) -> None:
        self.current = current
        self.logical_updates: list[tuple[list[int], int | None]] = []
        self.notified_updates: list[int] = []

    def current_index(self) -> int:
        return self.current

    def set_logical_selection(self, indexes: list[int], *, current_index: int | None = None) -> None:
        self.logical_updates.append((indexes, current_index))
        self.current = current_index if current_index is not None else indexes[0]

    def set_current_index(self, index: int) -> None:
        self.notified_updates.append(index)
        self.current = index

    def displayed_variant_path(self, index: int) -> str:
        return f"displayed-{index}.jpg"


class _PreviewStub:
    def __init__(self, *, visible: bool = False) -> None:
        self.visible = visible

    def isVisible(self) -> bool:
        return self.visible


def test_popout_navigation_uses_logical_grid_selection() -> None:
    grid = _GridStub(current=1)
    open_calls: list[tuple[int, bool]] = []
    records = [SimpleNamespace(path=f"frame-{index}.nef") for index in range(4)]

    def open_preview(index: int, *, lightweight_grid_sync: bool = False) -> None:
        open_calls.append((index, lightweight_grid_sync))

    window = SimpleNamespace(
        grid=grid,
        _records=records,
        _preview_navigation_dirty=False,
        _open_preview=open_preview,
        _record_at=lambda index: records[index],
    )

    MainWindow._navigate_preview(window, 1)

    assert grid.logical_updates == [([2], 2)]
    assert grid.notified_updates == []
    assert open_calls == [(2, True)]
    assert window._preview_navigation_dirty


def test_popout_uses_raw_source_for_raw_jpeg_bundle() -> None:
    record = ImageRecord(
        path=r"C:\shoot\IMG_0001.CR3",
        name="IMG_0001.CR3",
        size=100,
        modified_ns=1,
        companion_paths=(r"C:\shoot\IMG_0001.JPG",),
    )

    assert MainWindow._preview_source_path(SimpleNamespace(), record) == record.path


def test_closing_popout_runs_one_notified_grid_sync() -> None:
    grid = _GridStub(current=2)
    window = SimpleNamespace(
        grid=grid,
        _records=[object(), object(), object()],
        _preview_navigation_dirty=True,
        _winner_ladder_state=None,
        _quick_view_mode=False,
        _resume_background_indexing=lambda: None,
    )

    MainWindow._handle_preview_closed(window)

    assert grid.notified_updates == [2]
    assert not window._preview_navigation_dirty


def test_quick_view_finds_and_opens_the_exact_companion_path() -> None:
    raw_path = r"C:\shoot\IMG_0001.CR3"
    jpeg_path = r"C:\shoot\IMG_0001.JPG"
    record = ImageRecord(
        path=raw_path,
        name="IMG_0001.CR3",
        size=200,
        modified_ns=1,
        companion_paths=(jpeg_path,),
    )
    grid = _GridStub(current=0)
    open_calls: list[int] = []
    window = SimpleNamespace(
        _quick_view_mode=True,
        _pending_quick_view_path=jpeg_path,
        _quick_view_source_overrides={},
        _records=[ImageRecord(path=r"C:\shoot\other.jpg", name="other.jpg", size=1, modified_ns=1), record],
        preview=_PreviewStub(),
        grid=grid,
        _open_preview=open_calls.append,
    )
    window._record_and_index_for_loaded_path = lambda path: MainWindow._record_and_index_for_loaded_path(window, path)

    assert MainWindow._maybe_open_startup_quick_view(window)
    assert grid.notified_updates == [1]
    assert open_calls == [1]
    assert window._pending_quick_view_path == ""
    assert window._quick_view_source_overrides == {raw_path: jpeg_path}
    assert MainWindow._displayed_preview_source_path(window, 1, record) == jpeg_path


def test_closing_a_standalone_quick_view_closes_its_hidden_main_window() -> None:
    close_calls: list[bool] = []
    window = SimpleNamespace(
        _quick_view_mode=True,
        _winner_ladder_state=None,
        _preview_navigation_dirty=False,
        _resume_background_indexing=lambda: None,
        close=lambda: close_calls.append(True),
    )

    MainWindow._handle_preview_closed(window)

    assert close_calls == [True]
    assert not window._quick_view_mode
