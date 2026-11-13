"""Ratings and decisions that last: the photo model's changes saved to the shared decision store, and read back when a folder opens.

The store is the same one the Widgets app uses (`decision_store.DecisionStore`, session = the "Profile preset" setting), so a rating or a winner set
here shows up there and the other way round. The saving goes through the Widgets' own `AnnotationPersistenceQueue`: the database row and the XMP
sidecar (when the sidecar setting is on) are written on a worker thread a moment after the change.

How the shell's three decisions map onto a `SessionAnnotation`:
  Winner -> winner; Reject -> reject; Keep -> the tag "keep" (the Widgets' own Keep moves a file to a _keep folder, which is a different thing;
  "keep for now" has no flag of its own there, and a tag shows in both apps and does not move anything). The other tags, "photoshop" and the
  review round of a photo are left as they were.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from ..models import SessionAnnotation

KEEP_TAG = "keep"


def annotation_to_view(annotation: SessionAnnotation | None) -> tuple[str, int]:
    """(the shell's decision, the stars) of a stored annotation."""
    if annotation is None:
        return "", 0
    if annotation.reject:
        return "rej", annotation.rating
    if annotation.winner:
        return "win", annotation.rating
    return ("keep" if KEEP_TAG in annotation.tags else ""), annotation.rating


def view_to_annotation(decision: str, rating: int, previous: SessionAnnotation | None) -> SessionAnnotation:
    """The annotation to store for a photo that now has `decision` and `rating`, keeping what the shell does not show."""
    base = previous or SessionAnnotation()
    tags = tuple(t for t in base.tags if t != KEEP_TAG) + ((KEEP_TAG,) if decision == "keep" else ())
    return SessionAnnotation(winner=decision == "win", reject=decision == "rej", photoshop=base.photoshop, rating=max(0, min(5, int(rating))),
                             tags=tags, review_round=base.review_round)


def session_id() -> str:
    from ..app_identity import user_settings
    from ..decision_store import DecisionStore

    value = str(user_settings().value("workflow/session", DecisionStore.DEFAULT_SESSION, str) or "").strip()
    return value or DecisionStore.DEFAULT_SESSION


class DecisionBridge(QObject):
    relinked = Signal(int, int)          # decisions, collection entries that followed photos moved outside the app
    imported = Signal(int)               # photos whose stars or keywords were read from another program's XMP sidecar

    def __init__(self, photos, toasts=None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._photos = photos
        self._toasts = toasts
        from ..annotation_queue import AnnotationPersistenceQueue

        self._queue = AnnotationPersistenceQueue(parent=self)
        self._queue.failed.connect(self._failed)
        self._queue.warning.connect(self._sidecar_warning)
        self.failures: list[tuple[str, str]] = []
        self._sidecar_warned: set[str] = set()         # folders already told that their sidecars cannot be written
        self.relinks: list = []                        # a RelinkReport per folder whose moved photos got their decisions back
        self.relinked.connect(self._say_relinked)      # emitted on the scan thread, said on this one
        self.imported.connect(self._say_imported)
        photos.set_annotation_loader(self.load)
        photos.annotationChanged.connect(self.save)

    # called on the scan thread, with the folder's records
    def load(self, records) -> dict:
        from ..decision_store import DecisionStore

        records = list(records)
        store = DecisionStore()
        stored = store.load_annotations(session_id(), records)
        # Rows for photos that are no longer in the folder are kept: a photo moved out and back, or renamed outside the app, must not
        # lose its decision for it (`DecisionStore.prune_missing` exists for an explicit clean-up).
        if len(stored) < len(records) and self._relink(store, records):
            stored = store.load_annotations(session_id(), records)
        if len(stored) < len(records):
            stored.update(self._import_sidecars(store, [record for record in records if record.path not in stored]))
        out = {}
        for path, annotation in stored.items():
            decision, rating = annotation_to_view(annotation)
            out[path] = {"decision": decision, "rating": rating, "annotation": annotation}
        return out

    def _import_sidecars(self, store, records) -> dict:
        """Stars, keywords and rejected marks other programs wrote in XMP sidecars, for photos nothing is saved for here.
        They are saved like any decision, so they are read once; after that this app's own record is the one that counts.
        On the scan thread, like `load`."""
        from ..xmp import import_sidecar_annotations

        try:
            found = import_sidecar_annotations(records)
            if found:
                by_path = {record.path: record for record in records}
                store.save_annotations(session_id(), [(by_path[path], annotation) for path, annotation in found.items()])
        except Exception:                              # reading other programs' sidecars is a convenience: the folder still opens
            return {}
        if found:
            self.imported.emit(len(found))
        return found

    @Slot(int)
    def _say_imported(self, count: int) -> None:
        if self._toasts is not None:
            self._toasts.push("info", "Read ratings from XMP sidecars",
                              f"{count} photo{'s' if count != 1 else ''} had stars, keywords or a rejected mark written by another program.")

    def _relink(self, store, records) -> bool:
        """Decisions (and collection entries) saved for photos that were moved or renamed outside the app follow them to
        this folder (`decision_relink`). On the scan thread, like `load`: it asks the file system about the old paths."""
        from ..decision_relink import relink_folder

        try:
            from ..library_store import LibraryStore

            report = relink_folder(store, session_id(), records, library=LibraryStore())
        except Exception:                              # relinking is a convenience: the folder still opens without it
            return False
        if report.relinked or report.collections_changed:
            self.relinks.append(report)
            self.relinked.emit(len(report.relinked), report.collections_changed)
        return bool(report.relinked)

    @Slot(int, int)
    def _say_relinked(self, decisions: int, collection_entries: int) -> None:
        if self._toasts is None:
            return
        parts = ([f"{decisions} saved decision{'s' if decisions != 1 else ''}"] if decisions else []) + (
            [f"{collection_entries} collection entr{'ies' if collection_entries != 1 else 'y'}"] if collection_entries else [])
        self._toasts.push("info", "Followed photos that were moved", " and ".join(parts) + " now point at the photos' new location.")

    @Slot(object)
    def save(self, row: dict) -> None:
        record = row.get("record")
        if record is None:
            return
        previous = row.get("annotation")
        annotation = view_to_annotation(row["decision"], row["rating"], previous)
        row["annotation"] = None if annotation.is_empty else annotation
        self._queue.enqueue(record.path, annotation, record=record, session_id=session_id(), previous_annotation=previous)

    def flush(self) -> None:
        """Write whatever is waiting now (the app is closing)."""
        self._queue.flush_blocking()

    def _failed(self, path: str, message: str) -> None:
        self.failures.append((path, message))
        if self._toasts is not None:
            self._toasts.push("error", "Could not save the decision", f"{path.replace(chr(92), '/').rsplit('/', 1)[-1]}: {message}")

    def _sidecar_warning(self, path: str, message: str) -> None:
        """The decision is saved, but its XMP sidecar could not be written (a read-only card, a full disk). Said once for a
        folder: every photo in it will fail the same way, and one toast per keypress would bury the screen."""
        folder, _, name = path.replace(chr(92), "/").rpartition("/")
        if folder in self._sidecar_warned:
            return
        self._sidecar_warned.add(folder)
        if self._toasts is not None:
            self._toasts.push("warn", "Could not write the XMP sidecar", f"{name}: {message}. The decision itself is saved; other programs will not see it for photos in this folder.")
