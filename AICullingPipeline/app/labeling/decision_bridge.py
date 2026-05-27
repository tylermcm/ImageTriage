"""Bridge to the host app's DecisionStore for speed-cull annotation persistence.

When the labeling subprocess runs under the Image Triage host, this module
resolves the host's `decision_store.DecisionStore` and uses it as the durable
annotation surface. The bridge degrades to a no-op when the host modules are
unavailable (standalone runs, tests) so the rest of the labeling code stays
testable without a host import.

Stack-aware writes are the responsibility of the caller; this module only
persists or loads per-image annotations.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


LOGGER = logging.getLogger(__name__)

_HOST_ROOT_ENV = "IMAGE_TRIAGE_HOST_ROOT"


_host_modules: Optional[dict] = None


def _resolve_host_modules() -> Optional[dict]:
    """Lazily import the host's DecisionStore + dataclasses. Cached after the first call."""

    global _host_modules
    if _host_modules is not None:
        return _host_modules

    host_root_text = os.environ.get(_HOST_ROOT_ENV, "").strip()
    if not host_root_text:
        LOGGER.info("DecisionBridge: IMAGE_TRIAGE_HOST_ROOT not set; bridge disabled.")
        return None

    host_root = Path(host_root_text).expanduser().resolve()
    if not host_root.exists():
        LOGGER.info("DecisionBridge: host root %s does not exist; bridge disabled.", host_root)
        return None
    if str(host_root) not in sys.path:
        sys.path.insert(0, str(host_root))

    try:
        from image_triage.decision_store import DecisionStore
        from image_triage.models import ImageRecord, SessionAnnotation
    except Exception as exc:  # pragma: no cover - host import failure
        LOGGER.warning("DecisionBridge: host import failed (%s); bridge disabled.", exc)
        return None

    _host_modules = {
        "store_cls": DecisionStore,
        "record_cls": ImageRecord,
        "annotation_cls": SessionAnnotation,
    }
    return _host_modules


class DecisionBridge:
    """Persist speed-cull decisions into the host DecisionStore.

    All operations are silent no-ops when the host is unavailable. The caller
    is responsible for any stack fan-out (e.g. rejecting a whole stack) before
    invoking save_decision per image.
    """

    DEFAULT_SESSION = "Default"

    def __init__(self, *, session_id: str = DEFAULT_SESSION) -> None:
        self._session_id = session_id
        modules = _resolve_host_modules()
        if modules is None:
            self._store = None
            self._record_cls = None
            self._annotation_cls = None
        else:
            try:
                self._store = modules["store_cls"]()
            except Exception as exc:  # pragma: no cover - DB init failure
                LOGGER.warning("DecisionBridge: failed to init DecisionStore (%s).", exc)
                self._store = None
            self._record_cls = modules.get("record_cls")
            self._annotation_cls = modules.get("annotation_cls")

    @property
    def available(self) -> bool:
        return self._store is not None and self._record_cls is not None and self._annotation_cls is not None

    def _build_record(self, image_path: Path) -> Optional[Any]:
        if not self.available:
            return None
        try:
            stat = image_path.stat()
        except OSError:
            return None
        return self._record_cls(
            path=str(image_path),
            name=image_path.name,
            size=stat.st_size,
            modified_ns=stat.st_mtime_ns,
        )

    def load_decisions(self, image_paths: Iterable[Path]) -> Dict[str, Dict[str, Any]]:
        """Return existing host decisions keyed by absolute path string."""

        if not self.available:
            return {}
        records_by_path: Dict[str, Any] = {}
        for path in image_paths:
            record = self._build_record(Path(path))
            if record is None:
                continue
            records_by_path[record.path] = record
        if not records_by_path:
            return {}
        loaded = self._store.load_annotations_for_paths(
            self._session_id,
            records_by_path,
            list(records_by_path.keys()),
        )
        return {
            path_str: {
                "winner": bool(annot.winner),
                "reject": bool(annot.reject),
                "rating": int(annot.rating or 0),
                "photoshop": bool(annot.photoshop),
                "tags": tuple(annot.tags),
                "review_round": str(annot.review_round or ""),
            }
            for path_str, annot in loaded.items()
        }

    def save_decision(
        self,
        image_path: Path,
        *,
        winner: bool = False,
        reject: bool = False,
        rating: int = 0,
        photoshop: bool = False,
        tags: Optional[tuple] = None,
        review_round: str = "",
    ) -> bool:
        """Persist a per-image annotation. Returns True if written, False if bridge is offline."""

        record = self._build_record(Path(image_path))
        if record is None:
            return False
        annotation = self._annotation_cls(
            winner=bool(winner),
            reject=bool(reject),
            rating=int(rating or 0),
            photoshop=bool(photoshop),
            tags=tuple(tags or ()),
            review_round=str(review_round or ""),
        )
        try:
            self._store.save_annotation(self._session_id, record, annotation)
        except Exception as exc:  # pragma: no cover - DB write failure
            LOGGER.warning("DecisionBridge: failed to save annotation for %s (%s).", image_path, exc)
            return False
        return True

    def save_decisions(
        self,
        per_image: List[Dict[str, Any]],
    ) -> int:
        """Persist multiple annotations in one transaction. Returns count written."""

        if not self.available or not per_image:
            return 0
        entries = []
        for entry in per_image:
            record = self._build_record(Path(entry["path"]))
            if record is None:
                continue
            annotation = self._annotation_cls(
                winner=bool(entry.get("winner", False)),
                reject=bool(entry.get("reject", False)),
                rating=int(entry.get("rating", 0) or 0),
                photoshop=bool(entry.get("photoshop", False)),
                tags=tuple(entry.get("tags") or ()),
                review_round=str(entry.get("review_round") or ""),
            )
            entries.append((record, annotation))
        if not entries:
            return 0
        try:
            self._store.save_annotations(self._session_id, entries)
        except Exception as exc:  # pragma: no cover - DB write failure
            LOGGER.warning("DecisionBridge: batch save failed (%s).", exc)
            return 0
        return len(entries)
