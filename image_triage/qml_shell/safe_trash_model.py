"""The Safe Trash window: what was deleted, where it came from, putting it back, deleting it for good, and the size limit.

The window lists every photo in the app's safe trash and in the app's recycle folders on the removable drives that are connected
(`safe_trash_store`). A photo is one row, whatever it is made of (RAW, JPEG, sidecars, edit data), and comes back as one: to where
it was, never over a file that now has its name. When its folder is gone the row says so and it can be restored into a folder
the person chooses. A restored photo gets back the decision it had when it was deleted; its sidecar and edit data are part of
it and come back with the files; collections hold a photo by its path, so it is in them again once it is back at that path (and
they are pointed at the new path when it is restored somewhere else).

Nothing here touches files on the UI thread: the listing is read on a pool thread, and restore, delete-for-good and the size
limit run through `file_jobs.FileJobs`, the same single worker (with the same progress card and Cancel) as delete, move and copy.

Delete-for-good and Empty trash cannot be undone: the QML asks with a ConfirmCard before it calls `purge`.
The size limit (`workflow/safe_trash_limit_gb`, 0 for none) applies to the safe trash on this computer only. When that trash is
over the limit, the photos deleted longest ago are deleted for good, whole photos at a time, until it fits. It is applied after each
delete (never to the photos of that delete) and when the limit is changed here.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import Property, QAbstractListModel, QByteArray, QModelIndex, QObject, QRunnable, Qt, QThreadPool, QUrl, Signal, Slot

from .. import safe_trash_store as store
from .file_actions import LIMIT_KEY, TRASH_ROOTS_KEY as ROOTS_KEY, rekey_collections

LIMITS_GB = (0, 1, 5, 10, 25, 50, 100, 250)
GIGABYTE = 1024 ** 3
LIMIT_NOTE = ("When the safe trash on this computer is larger than the limit, the photos deleted longest ago are deleted for good until it fits. "
              "This happens after each delete and when you lower the limit. The photos of the delete you have just made are never removed by it. "
              "Recycle folders on removable drives are not limited.")
_ROLES = ("id", "name", "folder", "folderMissing", "deleted", "size", "files", "thumb", "place", "selected", "decision")


def size_text(size: float) -> str:
    if size < 1024:
        return f"{int(size)} bytes"
    if size < 1024 ** 2:
        return f"{size / 1024:.0f} KB"
    if size < 1024 ** 3:
        return f"{size / 1024 ** 2:.1f} MB"
    return f"{size / 1024 ** 3:.2f} GB"


def when_text(stamp: float, now: float | None = None) -> str:
    if not stamp:
        return "Date not recorded"
    now = time.time() if now is None else now
    day = time.strftime("%Y-%m-%d", time.localtime(stamp))
    if day == time.strftime("%Y-%m-%d", time.localtime(now)):
        return "Today, " + time.strftime("%H:%M", time.localtime(stamp))
    return day + time.strftime(", %H:%M", time.localtime(stamp))


def app_trash_root() -> str:
    """The safe trash in the app's own data: where photos deleted from a fixed or network drive go."""
    from ..safe_trash import SafeTrash

    return str(SafeTrash("").recycle_root_for_folder(""))


def connected_removable_roots() -> list[str]:
    """The app's recycle folders on the removable drives that are mounted now."""
    from PySide6.QtCore import QStorageInfo

    from ..safe_trash import DRIVE_REMOVABLE, SafeTrash

    classify = SafeTrash("")
    volumes = [volume.rootPath() for volume in QStorageInfo.mountedVolumes() if volume.isValid() and volume.isReady()]
    return store.removable_trash_roots(volumes, lambda root: classify.drive_type(classify.folder_drive_root(root)) == DRIVE_REMOVABLE)


def read_trash(roots, exists=os.path.isdir) -> list[dict]:
    """The rows of the window for these trash folders. Runs on a pool thread."""
    rows = []
    folders: dict[str, bool] = {}
    first = True
    for root in roots:
        place = "This computer" if first else str(Path(root).parent)
        first = False
        for entry in store.list_entries(root):
            folder = entry.folder
            if folder and folder not in folders:
                folders[folder] = not exists(folder)
            thumb = entry.thumbnail
            rows.append({"id": entry.id + "@" + root, "entry": entry, "name": entry.name, "folder": folder or "Not recorded",
                         "folderMissing": bool(folder) and folders[folder], "unknown": not folder, "deleted": when_text(entry.deleted), "stamp": entry.deleted,
                         "bytes": entry.size, "size": size_text(entry.size), "files": len(entry.files),
                         "thumb": QUrl.fromLocalFile(thumb).toString() if thumb else "", "place": place, "selected": False,
                         "decision": _decision_text(entry.annotation)})
    rows.sort(key=lambda row: (-row["stamp"], row["name"].casefold()))
    return rows


def _decision_text(annotation) -> str:
    if not isinstance(annotation, dict):
        return ""
    parts = ["Winner"] if annotation.get("winner") else ["Reject"] if annotation.get("reject") else []
    rating = int(annotation.get("rating") or 0)
    if rating:
        parts.append(f"{rating} star{'s' if rating != 1 else ''}")
    return ", ".join(parts)


class _LoadSignals(QObject):
    loaded = Signal(int, object)


class _Load(QRunnable):
    def __init__(self, token: int, roots_fn, signals: _LoadSignals) -> None:
        super().__init__()
        self.token, self.roots_fn, self.signals = token, roots_fn, signals

    def run(self) -> None:
        try:
            rows = read_trash(self.roots_fn())
        except Exception:
            rows = []
        try:
            self.signals.loaded.emit(self.token, rows)
        except RuntimeError:
            pass


class _Rows(QAbstractListModel):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.rows: list[dict] = []

    def roleNames(self) -> dict:
        return {Qt.ItemDataRole.UserRole + 1 + n: QByteArray(name.encode()) for n, name in enumerate(_ROLES)}

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        n = role - Qt.ItemDataRole.UserRole - 1
        return self.rows[index.row()].get(_ROLES[n]) if 0 <= n < len(_ROLES) else None

    def reset(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def touch(self, first: int, last: int) -> None:
        if self.rows and first <= last:
            self.dataChanged.emit(self.index(first), self.index(last), [Qt.ItemDataRole.UserRole + 1 + _ROLES.index("selected")])


class SafeTrashModel(QObject):
    changed = Signal()

    def __init__(self, photos, toasts, jobs, memory: bool = False, parent: QObject | None = None, settings=None, roots=None) -> None:
        super().__init__(parent)
        self._photos, self._toasts, self._jobs, self._memory = photos, toasts, jobs, memory
        self._settings_override = settings
        self._roots_fn = roots                    # fn() -> the trash folders to list, this computer's first (tests give their own)
        self._open = False
        self._loading = False
        self._token = 0
        self._rows = _Rows(self)
        self._report: list[str] = []              # what the last restore or delete left, and why
        self._signals = _LoadSignals(self)
        self._signals.loaded.connect(self._loaded)
        jobs.busyChanged.connect(self.changed)

    # ---------------- settings ----------------
    def _settings(self):
        if self._settings_override is not None:
            return self._settings_override
        from ..app_identity import user_settings

        return user_settings()

    def _limit_gb(self) -> int:
        if self._memory:
            return 0
        try:
            return max(0, int(self._settings().value(LIMIT_KEY, 0, int) or 0))
        except (TypeError, ValueError):
            return 0

    def _roots(self) -> list[str]:
        if self._roots_fn is not None:
            return list(self._roots_fn())
        roots = [app_trash_root()]
        seen = {os.path.normcase(os.path.normpath(roots[0]))}
        known = self._settings().value(ROOTS_KEY, [], list) or []
        for root in [*([known] if isinstance(known, str) else known), *connected_removable_roots()]:
            key = os.path.normcase(os.path.normpath(str(root)))
            if key not in seen and os.path.isdir(root):
                seen.add(key)
                roots.append(str(root))
        return roots

    # ---------------- opening, listing ----------------
    @Slot()
    def show(self) -> None:
        self._open = True
        self._report = []
        self.changed.emit()
        self.refresh()

    @Slot()
    def close(self) -> None:
        if self._open:
            self._open = False
            self.changed.emit()

    @Slot()
    def refresh(self) -> None:
        if self._memory:
            self._rows.reset(_sample_rows())
            self.changed.emit()
            return
        self._token += 1
        self._loading = True
        self.changed.emit()
        QThreadPool.globalInstance().start(_Load(self._token, self._roots, self._signals))

    def _loaded(self, token: int, rows: list) -> None:
        if token != self._token:
            return
        chosen = {row["id"] for row in self._rows.rows if row["selected"]}
        for row in rows:
            row["selected"] = row["id"] in chosen
        self._loading = False
        self._rows.reset(rows)
        self.changed.emit()

    # ---------------- choosing ----------------
    @Slot(int)
    def toggle(self, row: int) -> None:
        if 0 <= row < len(self._rows.rows):
            self._rows.rows[row]["selected"] = not self._rows.rows[row]["selected"]
            self._rows.touch(row, row)
            self.changed.emit()

    @Slot(bool)
    def selectAll(self, on: bool) -> None:
        for row in self._rows.rows:
            row["selected"] = bool(on)
        self._rows.touch(0, len(self._rows.rows) - 1)
        self.changed.emit()

    def _picked(self, everything: bool) -> list[dict]:
        return [row for row in self._rows.rows if everything or row["selected"]]

    # ---------------- restore ----------------
    @Slot(bool)
    def restore(self, everything: bool) -> None:
        """Put the chosen photos (or all of them) back where they came from."""
        self._restore(self._picked(everything), "")

    @Slot(bool, str)
    def restoreTo(self, everything: bool, folder: str) -> None:
        """Put the chosen photos into `folder` instead: for the ones whose own folder is gone or was never recorded."""
        folder = QUrl(folder).toLocalFile() if folder.startswith("file:") else folder
        if folder:
            self._restore(self._picked(everything), os.path.normpath(folder))

    def _restore(self, rows: list[dict], to_folder: str) -> None:
        if not rows:
            return
        if self._memory:
            self._drop(rows, f"Restored {_count(len(rows))}")
            return
        entries = [row["entry"] for row in rows]
        title = f"Restoring {entries[0].name if len(entries) == 1 else _count(len(entries))}"
        if not self._jobs.start(title, lambda ctx: store.restore_entries(entries, ctx, to_folder), lambda result: self._restored(result, len(entries), to_folder),
                                route=f"Safe trash \u2192 {to_folder or 'where they were'}", total=len(entries), failed=self._broke):
            self._refuse()

    def _restored(self, result: dict, count: int, to_folder: str) -> None:
        done, failed = result["done"], result["failed"]
        self._report = [f"{entry.name}: {why}" for entry, why in failed]
        if done:
            self._give_back_decisions(done)
            moved = [(old, new) for _entry, placed in done for old, new in placed.items() if old != new and os.path.isabs(old)]
            if moved and not rekey_collections(moved):
                self._toast("warn", "Collections were not updated", "The photos were restored, but the collections they were in still point at the old place.")
            here = self._photos.folder_dir()
            if here and any(os.path.normcase(str(Path(new).parent)) == os.path.normcase(os.path.normpath(here)) for _entry, placed in done for new in placed.values()):
                self._photos.reload()
        if failed:
            gone = len(result["missing"])
            body = self._report[0] + (f" (and {len(failed) - 1} more)" if len(failed) > 1 else "")
            if gone:
                body += " Choose them and use Restore to folder."
            self._toast("warn" if done else "error", f"{_count(len(failed))} could not be restored", body)
        if result["cancelled"]:
            self._toast("info", f"Stopped after restoring {len(done)} of {count}", "The rest are still in the safe trash.")
        elif done:
            self._toast("success", f"Restored {done[0][0].name if len(done) == 1 else _count(len(done))}",
                        f"To {to_folder}." if to_folder else "To where it was." if len(done) == 1 else "To where they were.")
        self.refresh()

    def _give_back_decisions(self, done: list) -> None:
        """A restored photo has the decision it had when it was deleted, under the profile it was saved with."""
        from ..decision_store import DecisionStore
        from ..models import ImageRecord

        by_session: dict[str, list] = {}
        for entry, placed in done:
            annotation = store.annotation_from_dict(entry.annotation)
            path = placed.get(entry.original or entry.files[0][0], "")
            if annotation is None or annotation.is_empty or not entry.session or not path:
                continue
            try:
                status = os.stat(path)
            except OSError:
                continue
            record = ImageRecord(path=path, name=Path(path).name, size=status.st_size, modified_ns=status.st_mtime_ns,
                                 primary_size=status.st_size, primary_modified_ns=status.st_mtime_ns)
            by_session.setdefault(entry.session, []).append((record, annotation))
        try:
            saver = DecisionStore() if by_session else None
            for session, entries in by_session.items():
                saver.save_annotations(session, entries)
        except Exception as exc:
            self._toast("warn", "Decisions were not restored", f"The photos are back, but their saved decisions could not be written: {exc}")

    # ---------------- delete for good ----------------
    @Slot(bool)
    def purge(self, everything: bool) -> None:
        """Delete the chosen photos (or everything in the trash) for good. The window has asked first."""
        rows = self._picked(everything)
        if not rows:
            return
        if self._memory:
            self._drop(rows, f"Deleted {_count(len(rows))} for good")
            return
        entries = [row["entry"] for row in rows]
        if not self._jobs.start("Emptying the safe trash" if everything else f"Deleting {_count(len(entries))} for good", lambda ctx: store.purge_entries(entries, ctx),
                                lambda result: self._purged(result, len(entries)), total=len(entries), failed=self._broke):
            self._refuse()

    def _purged(self, result: dict, count: int) -> None:
        done, failed = result["done"], result["failed"]
        self._report = [f"{entry.name}: {why}" for entry, why in failed]
        if failed:
            self._toast("error", f"{_count(len(failed))} could not be deleted", self._report[0] + (f" (and {len(failed) - 1} more)" if len(failed) > 1 else ""))
        if result["cancelled"]:
            self._toast("info", f"Stopped after deleting {len(done)} of {count}", "The rest are still in the safe trash.")
        elif done:
            self._toast("success", f"Deleted {_count(len(done))} for good", f"{size_text(store.total_size(done))} freed.")
        self.refresh()

    # ---------------- the size limit ----------------
    @Slot(int, result=int)
    def limitWouldRemove(self, gigabytes: int) -> int:
        """How many photos choosing this limit deletes for good now (the window asks before it does)."""
        return len(self._over(gigabytes))

    def _over(self, gigabytes: int) -> list:
        local = [row["entry"] for row in self._rows.rows if row["place"] == "This computer"]
        return store.over_limit(local, max(0, int(gigabytes)) * GIGABYTE)

    @Slot(int)
    def setLimit(self, gigabytes: int) -> None:
        gigabytes = max(0, int(gigabytes))
        if not self._memory:
            self._settings().setValue(LIMIT_KEY, gigabytes)
        self.changed.emit()
        over = self._over(gigabytes)
        if not over or self._memory:
            return
        if not self._jobs.start("Applying the size limit", lambda ctx: store.purge_entries(over, ctx), lambda result: self._limited(result), total=len(over), failed=self._broke):
            self._refuse()

    def _limited(self, result: dict) -> None:
        done = result["done"]
        if done:
            self._toast("info", f"{_count(len(done), 'older photo')} left the safe trash for good", f"{size_text(store.total_size(done))} freed to fit the limit.")
        self.refresh()

    # ---------------- helpers ----------------
    def _toast(self, kind: str, title: str, body: str = "") -> None:
        if self._toasts is not None:
            self._toasts.push(kind, title, body)

    def _refuse(self) -> None:
        self._toast("warn", "Another file operation is still running", "Wait for it to finish, or cancel it, then try again.")

    def _broke(self, message: str) -> None:
        self._toast("error", "The file operation stopped", message)
        self.refresh()

    def _drop(self, rows: list[dict], title: str) -> None:
        gone = {row["id"] for row in rows}
        self._rows.reset([row for row in self._rows.rows if row["id"] not in gone])
        self._toast("success", title, "Sample photos: nothing on disk was changed.")
        self.changed.emit()

    @Slot(result=bool)
    def waitForIdle(self) -> bool:
        """For tests: until the listing has been read and no file operation runs."""
        from PySide6.QtCore import QCoreApplication

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self._jobs.wait(1000)
            QCoreApplication.processEvents()
            if not self._loading and not self._jobs.busy:
                return True
            time.sleep(0.005)
        return False

    # ---------------- what the window shows ----------------
    open = Property(bool, lambda self: self._open, notify=changed)
    loading = Property(bool, lambda self: self._loading, notify=changed)
    busy = Property(bool, lambda self: self._jobs.busy, notify=changed)
    rows = Property(QObject, lambda self: self._rows, constant=True)
    count = Property(int, lambda self: len(self._rows.rows), notify=changed)
    selectedCount = Property(int, lambda self: sum(1 for row in self._rows.rows if row["selected"]), notify=changed)
    # a chosen photo cannot go back where it was: its folder is gone, or where it came from was never recorded
    selectedNeedFolder = Property(int, lambda self: sum(1 for row in self._rows.rows if row["selected"] and (row["folderMissing"] or row.get("unknown"))), notify=changed)
    missingCount = Property(int, lambda self: sum(1 for row in self._rows.rows if row["folderMissing"]), notify=changed)
    totalText = Property(str, lambda self: size_text(sum(row["bytes"] for row in self._rows.rows)), notify=changed)
    selectedText = Property(str, lambda self: size_text(sum(row["bytes"] for row in self._rows.rows if row["selected"])), notify=changed)
    report = Property("QVariantList", lambda self: list(self._report), notify=changed)
    limit = Property(int, _limit_gb, notify=changed)
    limitOptions = Property("QVariantList", lambda self: [{"label": "No limit" if gb == 0 else f"{gb} GB", "value": gb} for gb in sorted({*LIMITS_GB, self._limit_gb()})], notify=changed)
    limitNote = Property(str, lambda self: LIMIT_NOTE, constant=True)


def _count(count: int, one: str = "photo") -> str:
    return f"{count} {one}{'s' if count != 1 else ''}"


def _sample_rows() -> list[dict]:
    """`--mock`: a few rows to look at; nothing is on disk."""
    now = time.time()
    samples = (("IMG_4411.NEF", "D:/Photos/2026/Georgia trip/Kutaisi", False, 3, 31_400_000, "Reject", 3600),
               ("IMG_4398.NEF", "D:/Photos/2026/Georgia trip/Kutaisi", False, 2, 29_900_000, "", 3700),
               ("DSC_0192.JPG", "E:/DCIM/100NIKON", True, 1, 8_200_000, "2 stars", 90000),
               ("scan_014.tif", "", False, 1, 64_000_000, "", 400000))
    rows = []
    for n, (name, folder, missing, files, size, decision, age) in enumerate(samples):
        rows.append({"id": f"sample{n}", "entry": None, "name": name, "folder": folder or "Not recorded", "folderMissing": missing, "unknown": not folder,
                     "deleted": when_text(now - age, now), "stamp": now - age, "bytes": size, "size": size_text(size), "files": files, "thumb": "",
                     "place": "This computer" if n != 2 else "E:/DCIM", "selected": False, "decision": decision})
    return rows
