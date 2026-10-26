"""Ratings and decisions that last: the photo model's changes saved to the Widgets app's decision store, and read back when a folder opens.

The store is the same one the Widgets app uses (`decision_store.DecisionStore`, session = the "Profile preset" setting), so a rating or a winner set
here shows up there and the other way round. The saving goes through the Widgets' own `AnnotationPersistenceQueue`: the database row and the XMP
sidecar (when the sidecar setting is on) are written on a worker thread a moment after the change.

How the shell's three decisions map onto a `SessionAnnotation`:
  Winner -> winner; Reject -> reject; Keep -> the tag "keep" (the Widgets' own Keep moves a file to a _keep folder, which is a different thing;
  "keep for now" has no flag of its own there, and a tag shows in both apps and does not move anything). The other tags, "photoshop" and the
  review round of a photo are left as they were.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Slot

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
    def __init__(self, photos, toasts=None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._photos = photos
        self._toasts = toasts
        from ..annotation_queue import AnnotationPersistenceQueue

        self._queue = AnnotationPersistenceQueue(parent=self)
        self._queue.failed.connect(self._failed)
        self.failures: list[tuple[str, str]] = []
        photos.set_annotation_loader(self.load)
        photos.annotationChanged.connect(self.save)

    # called on the scan thread, with the folder's records
    def load(self, records) -> dict:
        from ..decision_store import DecisionStore

        stored = DecisionStore().load_annotations(session_id(), list(records))
        out = {}
        for path, annotation in stored.items():
            decision, rating = annotation_to_view(annotation)
            out[path] = {"decision": decision, "rating": rating, "annotation": annotation}
        return out

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
