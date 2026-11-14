"""Compare: two to four photos side by side in the viewer (`qml/CompareView.qml`), reached from QML as `popout.compare`.

The panes show photos out of an ordered set of candidates: the photos picked in the grid, or the photo shown and the ones after it.
One pane is the active one, and its photo is the viewer's current photo, so everything the viewer does to its photo (decide, rate,
delete, collect, the palette's commands) acts on that pane. `next` swaps the other panes for the candidates not seen yet, which is how
a set is worked through with the winner staying; `back` takes the last swap back.

The pictures are the viewer's own (`ViewerHost._picture_for`, read on its workers); nothing is read here.
"""
from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot

MIN_PANES, MAX_PANES = 2, 4
DECISION_NAMES = {"win": "Winner", "keep": "Keep", "rej": "Reject", "": "Undecided"}


class CompareModel(QObject):
    changed = Signal()

    def __init__(self, host, photos, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._host, self._photos = host, photos
        self._on = False
        self._paths: list[str] = []            # the panes, left to right
        self._at = 0                           # the active pane
        self._candidates: list[str] = []       # the set being worked through, in order
        self._seen: set[str] = set()           # candidates that were shown and swapped out
        self._trail: list[tuple[list[str], int, set[str]]] = []      # what `back` returns to
        self._revision = 0
        self._held: dict[str, tuple] = {}      # path -> its picture, kept while it is in a pane: a decision redraws the panes, and must not read the files again
        photos.revisionChanged.connect(self._refresh)
        photos.modelReset.connect(self._photos_changed)
        host.pictureArrived.connect(self._picture_arrived)

    # -- what QML reads -----------------------------------------------------------------------------------------------------
    active = Property(bool, lambda self: self._on, notify=changed)
    activeIndex = Property(int, lambda self: self._at, notify=changed)
    count = Property(int, lambda self: len(self._paths), notify=changed)
    revision = Property(int, lambda self: self._revision, notify=changed)

    @Property("QVariantList", notify=changed)
    def panes(self) -> list:
        """One entry per pane: the photo's row, name, decision, rating and its picture (a file URL and its size in pixels)."""
        out = []
        for path in self._paths if self._on else ():
            row = self._photos.row_for_path(path)
            item = self._photos.row_at(row) or {}
            picture = self._held.get(path) or self._host._picture_for(path)
            if picture is not None:
                self._held[path] = picture
            error = self._host._failed.get(path, "")
            out.append({
                "path": path, "row": row, "name": item.get("name", ""), "decision": item.get("decision", ""),
                "decisionName": DECISION_NAMES.get(item.get("decision", ""), ""), "rating": int(item.get("rating", 0) or 0),
                "src": picture[0] if picture else "", "w": picture[1] if picture else 0, "h": picture[2] if picture else 0,
                "loading": picture is None and not error, "error": error,
            })
        return out

    @Property(int, notify=changed)
    def remaining(self) -> int:
        """How many candidates have not been shown yet."""
        return len(self._pool())

    def paths(self) -> list[str]:
        return list(self._paths) if self._on else []

    # -- opening and closing ------------------------------------------------------------------------------------------------
    @Slot("QVariantList", int)
    def openRows(self, rows: list, current: int) -> None:
        """Compare the photos on `rows` (two or more picked in the grid), or with fewer the photo on `current` and the one after it.
        Opens the viewer when it is not open."""
        picked = [int(r) for r in rows if self._photos.row_at(int(r)) is not None]
        count = self._photos.rowCount()
        if count < 2 or self._photos.row_at(current) is None and not picked:
            self._host._tell("There is nothing to compare this photo with", "info")      # the shell's toast when the viewer is not open
            return
        if self._host.rawEditor.active:
            self._host.notice("Close the RAW editor to compare photos")
            return
        if len(picked) >= 2:
            order = sorted(picked)
            panes = min(MAX_PANES, len(order))
            first = order.index(current) if current in order[:panes] else 0
        else:
            start = current if current < count - 1 else count - 2      # the last photo is compared with the one before it
            order = list(range(start, count))
            panes, first = MIN_PANES, current - start
        self._candidates = [self._photos.row_at(r)["path"] for r in order]
        self._paths = self._candidates[:panes]
        self._at, self._seen, self._trail = first, set(), []
        self._on = True
        self._show_active(open_viewer=True)
        self._refresh()

    @Slot(int)
    def openFor(self, row: int) -> None:
        """Compare the photo on `row` with the one after it (the viewer's own button and key)."""
        self.openRows([], row)

    @Slot()
    def close(self) -> None:
        if self._on:
            self._on = False
            self._paths, self._candidates, self._seen, self._trail = [], [], set(), []
            self._refresh()

    # -- the active pane ----------------------------------------------------------------------------------------------------
    def _show_active(self, open_viewer: bool = False) -> None:
        """The viewer's photo is the active pane's."""
        row = self._photos.row_for_path(self._paths[self._at]) if self._paths else -1
        if row < 0:
            return
        if open_viewer and not self._host.isOpen:
            self._host.open(row)
        elif row != self._host.row:
            self._host.go(row)

    @Slot(int)
    def setActive(self, index: int) -> None:
        if self._on and 0 <= index < len(self._paths) and index != self._at:
            self._at = index
            self._show_active()
            self._refresh()

    @Slot(int)
    def moveActive(self, delta: int) -> None:
        self.setActive(max(0, min(len(self._paths) - 1, self._at + (1 if delta > 0 else -1))))

    def row_shown(self, path: str) -> None:
        """The viewer went to `path` (the filmstrip, a deleted photo): a photo already in a pane becomes the active one, any other
        takes the active pane's place."""
        if not self._on or not self._paths:
            return
        if path in self._paths:
            self._at = self._paths.index(path)
        else:
            self._seen.add(self._paths[self._at])
            self._seen.discard(path)
            self._paths[self._at] = path
        self._refresh()

    # -- working through the set --------------------------------------------------------------------------------------------
    def _pool(self) -> list[str]:
        shown = set(self._paths)
        return [p for p in self._candidates if p not in shown and p not in self._seen]

    @Slot()
    def next(self) -> None:
        """Swap every pane but the active one for the candidates not seen yet (as many as there are left): the winner stays."""
        if not self._on:
            return
        pool = self._pool()
        if not pool:
            self._host.notice("No more photos to compare")
            return
        self._trail.append((list(self._paths), self._at, set(self._seen)))
        for index in range(len(self._paths)):
            if index != self._at and pool:
                self._seen.add(self._paths[index])
                self._paths[index] = pool.pop(0)
        self._refresh()

    @Slot()
    def back(self) -> None:
        """Take the last swap back."""
        if not self._on or not self._trail:
            return
        paths, at, seen = self._trail.pop()
        gone = [p for p in paths if self._photos.row_for_path(p) < 0]
        if gone:                                             # a photo of that step was deleted since: there is nothing to go back to
            self._trail = []
            return
        self._paths, self._at, self._seen = paths, at, seen
        self._show_active()
        self._refresh()

    @Slot(int)
    def setCount(self, count: int) -> None:
        """Two, three or four panes. More panes take the next candidates; fewer drop panes from the right, never the active one."""
        if not self._on:
            return
        count = max(MIN_PANES, min(MAX_PANES, int(count)))
        while len(self._paths) > count:
            drop = len(self._paths) - 1 if self._at != len(self._paths) - 1 else len(self._paths) - 2
            del self._paths[drop]
            if drop < self._at:
                self._at -= 1
        if len(self._paths) < count:
            extra = self._pool() + [p for p in self._candidates if p in self._seen]
            if len(self._paths) + len(extra) < count:        # the set is too small: the photos after the last pane fill in
                last = max((self._photos.row_for_path(p) for p in self._paths), default=-1)
                known = set(self._paths) | set(extra)
                more = [self._photos.row_at(r)["path"] for r in range(last + 1, self._photos.rowCount())]
                more = [p for p in more if p not in known]
                self._candidates += more
                extra += more
            for path in extra[:count - len(self._paths)]:
                self._seen.discard(path)
                self._paths.append(path)
        self._trail = []
        self._refresh()

    # -- keeping up ---------------------------------------------------------------------------------------------------------
    def _photos_changed(self) -> None:
        """Photos went away or the list was filtered again: a pane whose photo is gone takes the next candidate, and with fewer than
        two photos left to show the compare ends."""
        if not self._on:
            return
        here = lambda path: self._photos.row_for_path(path) >= 0      # noqa: E731
        self._candidates = [p for p in self._candidates if here(p)]
        self._seen = {p for p in self._seen if here(p)}
        current = self._paths[self._at] if self._paths else ""
        self._paths = [p for p in self._paths if here(p)]
        pool = self._pool()
        while len(self._paths) < MIN_PANES and pool:
            self._paths.append(pool.pop(0))
        if len(self._paths) < MIN_PANES:
            self.close()
            return
        self._at = self._paths.index(current) if current in self._paths else min(self._at, len(self._paths) - 1)
        self._trail = []
        self._show_active()
        self._refresh()

    def _picture_arrived(self, path: str) -> None:
        if self._on and path in self._paths:
            self._refresh()

    def _refresh(self, *args) -> None:
        self._held = {path: picture for path, picture in self._held.items() if self._on and path in self._paths}
        if self._on:
            for path in self._paths + self._pool()[:len(self._paths) - 1]:      # the panes, and what `next` would bring in
                self._host._ask("picture", path)
        self._revision += 1
        self.changed.emit()
