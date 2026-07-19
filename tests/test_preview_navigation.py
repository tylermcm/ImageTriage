from __future__ import annotations

from types import SimpleNamespace

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


def test_closing_popout_runs_one_notified_grid_sync() -> None:
    grid = _GridStub(current=2)
    window = SimpleNamespace(
        grid=grid,
        _records=[object(), object(), object()],
        _preview_navigation_dirty=True,
        _winner_ladder_state=None,
    )

    MainWindow._handle_preview_closed(window)

    assert grid.notified_updates == [2]
    assert not window._preview_navigation_dirty
