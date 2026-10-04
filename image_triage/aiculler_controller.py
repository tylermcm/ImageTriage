"""The aiculler integration: per-folder telemetry, the internal and global label stores, dispute and adapter-label review (banner, reason tags), and the ingested-paths bookkeeping. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.3)."""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time

from PySide6.QtCore import QObject, QSignalBlocker, QStandardPaths, QTimer, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMenu, QMessageBox, QPushButton, QToolButton, QVBoxLayout, QWidget
from aiculler.telemetry import TelemetryEvent, ThreadedTelemetryLogger, classify_override, normalize_bucket
from dataclasses import replace
from hashlib import sha1
from pathlib import Path

from .ai_results import ai_cull_bucket_for_result
from .aiculler_global_store import GlobalAdapterLabelStore, default_global_adapter_label_store_path
from .aiculler_workflow import aiculler_db_path, build_aiculler_workflow_paths, default_aiculler_runtime, latest_adapter_model_version, load_adapter_review_candidates, load_adapter_status_summary
from .models import FilterMode, ImageRecord, SessionAnnotation
from .perceptual_hash import hamming_distance_int
from .perf import perf_logger
from .phash_prefilter import build_phash_prefilter_paths
from .records_view_controller import _memory_path_key
from .scanner import normalized_path_key
from .shell_actions import open_with_default

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow

_logger = logging.getLogger(__name__)


def _path_parent_stem_key(path: str) -> str:
    try:
        candidate = Path(path).expanduser()
        parent = normalized_path_key(str(candidate.parent))
        stem = candidate.stem.casefold()
    except (OSError, ValueError):
        return ""
    return f"{parent}|{stem}" if parent and stem else ""


class AiCullerController(QObject):
    """The aiculler integration: per-folder telemetry, the internal and global label stores, dispute and adapter-label review (banner, reason tags), and the ingested-paths bookkeeping. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.3)."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window
        self._adapter_review_rating_paths: tuple[str, ...] = ()
        self._adapter_review_reason_phase = False
        self._aiculler_dedupe_siblings: dict[str, list[str]] = {}
        self._aiculler_force_propagate_siblings: set[str] = set()
        self._aiculler_global_label_pending: dict[str, tuple[str, float, bool]] = {}
        self._aiculler_internal_label_cache: dict[
            str,
            tuple[dict[str, str], dict[str, dict[str, object]], dict[str, tuple[str, ...]]],
        ] = {}
        self._aiculler_internal_label_cache_folders: dict[str, str] = {}
        self._aiculler_internal_label_dirty_paths: set[str] = set()
        self._aiculler_review_burst_snapshot: tuple[bool, bool] | None = None
        self._aiculler_telemetry_adapter_version_cache: dict[str, tuple[int, str]] = {}
        self._aiculler_telemetry_db_path: Path | None = None
        self._aiculler_telemetry_logger: ThreadedTelemetryLogger | None = None
        self._disputed_path_keys: set[str] = set()
        self._user_label_bucket_overrides: dict[str, str] = {}

    def open_aiculler_root(self) -> None:
        try:
            runtime = default_aiculler_runtime()
        except Exception as exc:
            QMessageBox.warning(self._window, "AI Culler", f"Could not resolve the CLI-Culler runtime.\n\n{exc}")
            return
        open_with_default(str(runtime.root))

    def open_aiculler_categories(self) -> None:
        try:
            runtime = default_aiculler_runtime()
            category_path = runtime.categories_csv or (runtime.root / "categories.csv")
        except Exception as exc:
            QMessageBox.warning(self._window, "AI Categories", f"Could not resolve the categories file.\n\n{exc}")
            return
        from .category_prompts_dialog import CategoryPromptsDialog
        dialog = CategoryPromptsDialog(category_path, parent=self._window)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._window.statusBar().showMessage(f"Saved category prompts to {category_path.name}.")

    def aiculler_paths_for_current_folder(self):
        if not self._window._current_folder:
            return None
        # Called on every winner / reject mark (telemetry), so it must not ask a share to resolve the path.
        return build_aiculler_workflow_paths(
            self._window._current_folder, resolve=not self._window._is_slow_source_folder(self._window._current_folder)
        )

    def aiculler_telemetry_logger_for_current_folder(self) -> ThreadedTelemetryLogger | None:
        paths = self.aiculler_paths_for_current_folder()
        if paths is None:
            return None
        db_path = aiculler_db_path(paths)
        if self._aiculler_telemetry_logger is not None and self._aiculler_telemetry_db_path == db_path:
            return self._aiculler_telemetry_logger
        self.shutdown_aiculler_telemetry_logger()
        self._aiculler_telemetry_db_path = db_path
        self._aiculler_telemetry_logger = ThreadedTelemetryLogger(db_path)
        return self._aiculler_telemetry_logger

    def shutdown_aiculler_telemetry_logger(self) -> None:
        self.flush_pending_aiculler_telemetry()
        logger = self._aiculler_telemetry_logger
        self._aiculler_telemetry_logger = None
        self._aiculler_telemetry_db_path = None
        if logger is not None:
            logger.shutdown()

    def flush_pending_aiculler_telemetry(self) -> None:
        pending = getattr(self._window, "_aiculler_pending_telemetry_events", {})
        if not pending:
            return
        for timer, event in list(pending.values()):
            timer.stop()
            self.log_aiculler_telemetry_now(event)
        pending.clear()

    def queue_aiculler_telemetry_event(self, event: TelemetryEvent) -> None:
        if self.aiculler_telemetry_logger_for_current_folder() is None:
            return
        key = event.image_id
        pending = self._window._aiculler_pending_telemetry_events
        existing = pending.pop(key, None)
        if existing is not None:
            timer, previous_event = existing
            timer.stop()
            self.log_aiculler_telemetry_now(
                replace(
                    previous_event,
                    is_final=0,
                    ignored_for_training=1,
                )
            )
            event = replace(event, previous_bucket=previous_event.previous_bucket or previous_event.ai_initial_bucket)
        timer = QTimer(self._window)
        timer.setSingleShot(True)
        timer.setInterval(450)
        timer.timeout.connect(lambda event_key=key: self.flush_pending_aiculler_telemetry_event(event_key))
        pending[key] = (timer, event)
        timer.start()

    def flush_pending_aiculler_telemetry_event(self, key: str) -> None:
        pending = self._window._aiculler_pending_telemetry_events
        item = pending.pop(key, None)
        if item is None:
            return
        _timer, event = item
        self.log_aiculler_telemetry_now(event)

    def log_aiculler_telemetry_now(self, event: TelemetryEvent) -> None:
        logger = self._aiculler_telemetry_logger or self.aiculler_telemetry_logger_for_current_folder()
        if logger is not None:
            logger.log_event(event)

    def aiculler_internal_label_store_path(self, paths) -> Path:
        app_data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        root = Path(app_data) if app_data else Path.home() / ".image-triage"
        folder_key = sha1(str(paths.folder).casefold().encode("utf-8"), usedforsecurity=False).hexdigest()[:20]
        return root / "ai_training" / "adapter_labels" / f"{folder_key}.json"

    def aiculler_global_label_store(self) -> GlobalAdapterLabelStore:
        return GlobalAdapterLabelStore(default_global_adapter_label_store_path())

    def save_aiculler_global_label(
        self,
        source_path: str,
        label: str,
        *,
        weight: float = 1.0,
        is_dispute: bool = False,
        reason_tags: tuple[str, ...] = (),
    ) -> None:
        pending = getattr(self, "_aiculler_global_label_pending", None)
        if pending is not None:
            pending.pop(str(source_path), None)
        try:
            store = self.aiculler_global_label_store()
            try:
                if label.strip():
                    store.upsert_label(
                        source_path,
                        label,
                        folder=self._window._current_folder or str(Path(source_path).parent),
                        weight=weight,
                        is_dispute=is_dispute,
                        reason_tags=reason_tags,
                    )
                else:
                    store.delete_label(source_path)
            finally:
                store.close()
        except Exception:
            # Global labels are a convenience layer. Folder-local labels remain
            # authoritative for the current workflow if the global DB is not
            # writable.
            _logger.debug("Failed to save global aiculler label for %s", source_path, exc_info=True)
            return

    def queue_aiculler_global_label(
        self,
        source_path: str,
        label: str,
        *,
        weight: float = 1.0,
        is_dispute: bool = False,
    ) -> None:
        self._aiculler_global_label_pending[str(source_path)] = (str(label), float(weight), bool(is_dispute))
        self._window._aiculler_global_label_save_timer.start()

    def sync_annotation_to_global_adapter_label(
        self,
        record: ImageRecord,
        annotation: SessionAnnotation | None,
    ) -> None:
        label = self.aiculler_label_for_annotation(annotation)
        self.queue_aiculler_global_label(record.path, label, weight=1.0, is_dispute=False)

    def save_aiculler_global_reason_tags(self, source_path: str, reason_tags: tuple[str, ...]) -> None:
        try:
            store = self.aiculler_global_label_store()
            try:
                store.update_reason_tags(source_path, reason_tags)
            finally:
                store.close()
        except Exception:
            _logger.debug("Failed to save global aiculler reason tags for %s", source_path, exc_info=True)
            return

    def flush_aiculler_global_label_queue(self) -> None:
        pending = dict(getattr(self, "_aiculler_global_label_pending", {}))
        if not pending:
            return
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        self._window._aiculler_global_label_save_timer.stop()
        self._aiculler_global_label_pending.clear()
        for source_path, (label, weight, is_dispute) in pending.items():
            self.save_aiculler_global_label(source_path, label, weight=weight, is_dispute=is_dispute)
        if logger.enabled:
            logger.duration(
                "adapter_review.window.flush_global_labels",
                (time.perf_counter() - start) * 1000.0,
                labels=len(pending),
            )

    def load_aiculler_internal_labels(self, paths) -> dict[str, str]:
        labels, _disputes, _reason_tags = self.load_aiculler_internal_label_cache(paths)
        return dict(labels)

    def load_aiculler_internal_label_cache(self, paths) -> tuple[dict[str, str], dict[str, dict[str, object]], dict[str, tuple[str, ...]]]:
        label_path = self.aiculler_internal_label_store_path(paths)
        cache_key = str(label_path)
        cached = self._aiculler_internal_label_cache.get(cache_key)
        if cached is not None:
            labels, disputes, reason_tags = cached
            return labels, disputes, reason_tags
        if not label_path.exists():
            labels: dict[str, str] = {}
            disputes: dict[str, dict[str, object]] = {}
            reason_tags: dict[str, tuple[str, ...]] = {}
            self._aiculler_internal_label_cache[cache_key] = (labels, disputes, reason_tags)
            self._aiculler_internal_label_cache_folders[cache_key] = str(paths.folder)
            return labels, disputes, reason_tags
        try:
            payload = json.loads(label_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        raw_labels = payload.get("labels") if isinstance(payload, dict) else None
        allowed_labels = {"hero", "portfolio", "strong", "keep", "good", "maybe", "weak", "reject", "bad", "k", "r", "yes", "no", "1", "0"}
        labels = {
            str(path): str(label).strip().lower()
            for path, label in (raw_labels.items() if isinstance(raw_labels, dict) else ())
            if str(label).strip().lower() in allowed_labels
        }
        raw_disputes = payload.get("disputes") if isinstance(payload, dict) else None
        disputes: dict[str, dict[str, object]] = {}
        if isinstance(raw_disputes, dict):
            for path, entry in raw_disputes.items():
                if not isinstance(entry, dict):
                    continue
                disputes[str(path)] = {
                    "user_label": str(entry.get("user_label") or "").strip().lower(),
                    "ai_label": str(entry.get("ai_label") or "").strip(),
                    "ai_score": float(entry.get("ai_score") or 0.0),
                    "ai_bucket": str(entry.get("ai_bucket") or ""),
                    "timestamp": str(entry.get("timestamp") or ""),
                }
        raw_reason_tags = payload.get("reason_tags") if isinstance(payload, dict) else None
        reason_tags: dict[str, tuple[str, ...]] = {}
        if isinstance(raw_reason_tags, dict):
            for path, values in raw_reason_tags.items():
                reason_tags[str(path)] = self.normalize_adapter_reason_tags(values)
        self._aiculler_internal_label_cache[cache_key] = (labels, disputes, reason_tags)
        self._aiculler_internal_label_cache_folders[cache_key] = str(paths.folder)
        return labels, disputes, reason_tags

    def load_aiculler_internal_disputes(self, paths) -> dict[str, dict[str, object]]:
        """Disputes: per-image entries where the user overrode the AI.

        Stored alongside labels in the same JSON file (sibling `disputes` key)
        so there's one source of truth and the existing label flow keeps
        working unchanged. Each entry records the user's corrective label and
        a snapshot of what the AI said at dispute time (for debugging /
        analytics later).
        """
        _labels, disputes, _reason_tags = self.load_aiculler_internal_label_cache(paths)
        return {path: dict(entry) for path, entry in disputes.items()}

    def load_aiculler_internal_reason_tags(self, paths) -> dict[str, tuple[str, ...]]:
        _labels, _disputes, reason_tags = self.load_aiculler_internal_label_cache(paths)
        return {path: tuple(values) for path, values in reason_tags.items()}

    def save_aiculler_internal_labels(
        self,
        paths,
        labels: dict[str, str],
        *,
        disputes: dict[str, dict[str, object]] | None = None,
        reason_tags: dict[str, tuple[str, ...]] | None = None,
        defer: bool = False,
    ) -> None:
        label_path = self.aiculler_internal_label_store_path(paths)
        # If disputes weren't passed in, preserve whatever is already on disk
        # so saving labels doesn't accidentally drop existing disputes.
        if disputes is None:
            _cached_labels, cached_disputes, cached_reason_tags = self.load_aiculler_internal_label_cache(paths)
            disputes = cached_disputes
            if reason_tags is None:
                reason_tags = cached_reason_tags
        elif reason_tags is None:
            _cached_labels, _cached_disputes, cached_reason_tags = self.load_aiculler_internal_label_cache(paths)
            reason_tags = cached_reason_tags
        cache_key = str(label_path)
        cached_labels = dict(labels)
        cached_disputes = {str(path): dict(entry) for path, entry in disputes.items()}
        cached_reason_tags = {
            str(path): self.normalize_adapter_reason_tags(values)
            for path, values in (reason_tags or {}).items()
            if self.normalize_adapter_reason_tags(values)
        }
        self._aiculler_internal_label_cache[cache_key] = (cached_labels, cached_disputes, cached_reason_tags)
        self._aiculler_internal_label_cache_folders[cache_key] = str(paths.folder)
        if defer:
            self._aiculler_internal_label_dirty_paths.add(cache_key)
            self._window._aiculler_internal_label_save_timer.start()
            return
        self.write_aiculler_internal_label_payload(label_path, paths.folder, cached_labels, cached_disputes, cached_reason_tags)
        self._aiculler_internal_label_dirty_paths.discard(cache_key)

    def write_aiculler_internal_label_payload(
        self,
        label_path: Path,
        folder: object,
        labels: dict[str, str],
        disputes: dict[str, dict[str, object]],
        reason_tags: dict[str, tuple[str, ...]] | None = None,
    ) -> None:
        label_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "folder": str(folder),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "labels": dict(sorted(labels.items(), key=lambda item: item[0].casefold())),
            "disputes": dict(sorted(disputes.items(), key=lambda item: item[0].casefold())),
            "reason_tags": {
                path: list(values)
                for path, values in sorted((reason_tags or {}).items(), key=lambda item: item[0].casefold())
                if values
            },
        }
        label_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def flush_aiculler_internal_label_cache(self) -> None:
        dirty = list(getattr(self, "_aiculler_internal_label_dirty_paths", set()))
        if not dirty:
            return
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        for cache_key in dirty:
            cached = self._aiculler_internal_label_cache.get(cache_key)
            if cached is None:
                self._aiculler_internal_label_dirty_paths.discard(cache_key)
                continue
            labels, disputes, reason_tags = cached
            folder = self._aiculler_internal_label_cache_folders.get(cache_key, "")
            self.write_aiculler_internal_label_payload(Path(cache_key), folder, labels, disputes, reason_tags)
            self._aiculler_internal_label_dirty_paths.discard(cache_key)
        if logger.enabled:
            logger.duration(
                "adapter_review.window.flush_internal_labels",
                (time.perf_counter() - start) * 1000.0,
                files=len(dirty),
            )

    @staticmethod
    def aiculler_label_for_annotation(annotation: SessionAnnotation | None) -> str:
        if annotation is None:
            return ""
        if annotation.reject:
            return "reject"
        if annotation.winner:
            return "keep"
        return ""

    def normalize_adapter_reason_tags(self, values: object) -> tuple[str, ...]:
        allowed = {key for key, _label in self._window.ADAPTER_REASON_TAGS}
        if isinstance(values, str):
            raw_values = re.split(r"[;,|]", values)
        elif isinstance(values, (list, tuple, set)):
            raw_values = list(values)
        else:
            raw_values = []
        normalized: list[str] = []
        seen: set[str] = set()
        for value in raw_values:
            text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
            if text not in allowed or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return tuple(normalized)

    def is_adapter_reason_target_label(self, label: object) -> bool:
        normalized = str(label or "").strip().lower()
        return normalized in self._window.ADAPTER_REASON_TARGET_LABELS

    def review_aiculler_adapter_labels(self) -> None:
        paths = self.aiculler_paths_for_current_folder()
        if paths is None:
            self._window.statusBar().showMessage("Choose a folder before reviewing adapter labels.")
            return
        db_path = aiculler_db_path(paths)
        if not db_path.exists():
            self._window.statusBar().showMessage("Run Index & Score in the AI Workflow Center before reviewing adapter labels.")
            return
        saved_labels = self.load_aiculler_internal_labels(paths)
        saved_reason_tags = self.load_aiculler_internal_reason_tags(paths)
        # The CLI-Culler DB stores whatever path the AI run was given (often a
        # UNC path like \\server\share\...), but the grid records use whatever
        # form the user opened the folder with (often a mapped drive like X:).
        # Translate each survivor + sibling path to its matching grid record
        # path so the grid filter actually sees matches and the viewport
        # populates.
        #
        # IMPORTANT: do NOT use normalized_path_key here — it calls Path.resolve(),
        # which on UNC paths makes a network round-trip per call. For a 1400-image
        # folder that's tens of seconds of UI-thread blocking. Instead, match by
        # filename (cheap, IO-free, works perfectly when filenames are unique
        # inside the folder — the norm for a photo shoot). When two records share
        # the same basename, fall back to comparing the casefolded full path
        # (still no IO, just string ops) to disambiguate. RAW/JPG pairs can
        # also mean the DB stores a scored JPG while the grid is showing a RAW
        # primary record, so keep a same-stem fallback after exact basenames.
        records_by_basename: dict[str, list[str]] = {}
        records_by_stem: dict[str, list[str]] = {}

        def _index_grid_path(path: str) -> None:
            basename = os.path.basename(path).casefold()
            if not basename:
                return
            records_by_basename.setdefault(basename, []).append(path)
            stem = os.path.splitext(basename)[0]
            if stem:
                records_by_stem.setdefault(stem, []).append(path)

        grid_records = list(getattr(self._window.grid, "_items", ()) or ()) or list(self._window._all_records)
        for record in grid_records:
            if record.is_folder:
                continue
            _index_grid_path(record.path)
            for variant in getattr(record, "display_variants", ()) or ():
                variant_path = str(getattr(variant, "path", "") or "")
                if variant_path:
                    _index_grid_path(variant_path)

        unresolved_count = 0

        def _resolve_to_grid(db_path: str) -> str | None:
            if not db_path:
                return None
            basename = os.path.basename(db_path).casefold()
            matches = records_by_basename.get(basename)
            if not matches:
                matches = records_by_stem.get(os.path.splitext(basename)[0])
            if not matches:
                return None
            if len(matches) == 1:
                return matches[0]
            # Multiple records with the same filename — pick the one whose
            # casefolded path shares the longest suffix with the DB path.
            db_key = os.path.normpath(db_path).casefold()
            best = matches[0]
            best_score = 0
            for candidate in matches:
                cand_key = os.path.normpath(candidate).casefold()
                # Compare from the right (suffix overlap).
                score = 0
                for a, b in zip(reversed(db_key), reversed(cand_key)):
                    if a != b:
                        break
                    score += 1
                if score > best_score:
                    best = candidate
                    best_score = score
            return best

        pending_reason_paths: list[str] = []
        seen_pending_reason_paths: set[str] = set()
        for label_path, label in saved_labels.items():
            if not self.is_adapter_reason_target_label(label) or saved_reason_tags.get(label_path):
                continue
            resolved = _resolve_to_grid(label_path)
            if resolved is None or resolved in seen_pending_reason_paths:
                continue
            seen_pending_reason_paths.add(resolved)
            pending_reason_paths.append(resolved)

        if pending_reason_paths:
            self._aiculler_dedupe_siblings = {}
            self._aiculler_force_propagate_siblings = set()
            burst_snapshot = (self._window._burst_groups_enabled, self._window._burst_stacks_enabled)
            if burst_snapshot != (False, False):
                self._aiculler_review_burst_snapshot = burst_snapshot
                self._window._burst_groups_enabled = False
                self._window._burst_stacks_enabled = False
                self._window._refresh_burst_group_view()
                self._window._update_action_states()
            else:
                self._aiculler_review_burst_snapshot = None
            self._adapter_review_reason_phase = True
            self._adapter_review_rating_paths = tuple(pending_reason_paths)
            self.clear_adapter_review_reason_tags()
            self._window.grid.set_adapter_review_mode(
                pending_reason_paths,
                saved_labels,
                label_controls_enabled=False,
                reason_controls_enabled=True,
                reason_tags_by_path=saved_reason_tags,
                reason_options=self._window.ADAPTER_REASON_TAGS,
            )
            self.refresh_adapter_review_banner()
            dialog = getattr(self._window, "_ai_workflow_center_dialog", None)
            if dialog is not None:
                dialog.hide_for_adapter_review()
            self._window.statusBar().showMessage(
                f"{len(pending_reason_paths)} winner/reject label(s) need reasons before a new review batch."
            )
            return

        phash_group_by_path, phash_group_members = self.aiculler_phash_group_maps()
        review_group_by_path, review_group_members = self.aiculler_review_group_maps()
        try:
            selection = load_adapter_review_candidates(
                db_path,
                already_labeled=set(saved_labels.keys()),
                phash_group_by_path=phash_group_by_path,
                phash_group_members=phash_group_members,
                review_group_by_path=review_group_by_path,
                review_group_members=review_group_members,
                return_result=True,
            )
        except Exception as exc:
            QMessageBox.warning(self._window, "Adapter Label Review", f"Could not load adapter review candidates.\n\n{exc}")
            return

        review_paths: list[str] = []
        seen_review_paths: set[str] = set()
        unresolved_count = 0
        for row in selection.candidates:
            db_path = str(row.get("file_path") or "")
            if not db_path:
                continue
            resolved = _resolve_to_grid(db_path)
            if resolved is None:
                unresolved_count += 1
                continue
            if resolved in seen_review_paths:
                continue
            seen_review_paths.add(resolved)
            review_paths.append(resolved)

        if not review_paths:
            if unresolved_count:
                message = (
                    "Adapter review candidates were found, but none matched the images currently loaded in the grid. "
                    "Refresh or reopen this folder, then try Review Labels again."
                )
                self._window.statusBar().showMessage(message)
                QMessageBox.information(self._window, "Adapter Label Review", message)
            else:
                self._window.statusBar().showMessage("No adapter label candidates are available for this folder.")
            return

        # Re-key the sibling map so label propagation also lands on grid paths.
        translated_siblings: dict[str, list[str]] = {}
        translated_force_propagate: set[str] = set()
        for survivor_path, sibling_paths in selection.siblings_by_survivor.items():
            grid_survivor = _resolve_to_grid(str(survivor_path))
            if grid_survivor is None:
                continue
            resolved_siblings: list[str] = []
            seen_sibling_paths: set[str] = set()
            survivor_key = os.path.normcase(os.path.normpath(grid_survivor))
            for sib in sibling_paths:
                resolved = _resolve_to_grid(str(sib))
                if resolved is None:
                    continue
                resolved_key = os.path.normcase(os.path.normpath(resolved))
                if resolved_key == survivor_key or resolved_key in seen_sibling_paths:
                    continue
                seen_sibling_paths.add(resolved_key)
                resolved_siblings.append(resolved)
            translated_siblings[grid_survivor] = resolved_siblings
            if str(survivor_path) in selection.force_propagate_survivors:
                translated_force_propagate.add(grid_survivor)
        self._aiculler_dedupe_siblings = translated_siblings
        self._aiculler_force_propagate_siblings = translated_force_propagate

        # Force burst grouping/stacking off while labeling so pHash dedup is the
        # only source of grouping. The previous toggle state is restored when
        # adapter review mode exits via _exit_aiculler_adapter_review_mode().
        burst_snapshot = (self._window._burst_groups_enabled, self._window._burst_stacks_enabled)
        if burst_snapshot != (False, False):
            self._aiculler_review_burst_snapshot = burst_snapshot
            self._window._burst_groups_enabled = False
            self._window._burst_stacks_enabled = False
            self._window._refresh_burst_group_view()
            self._window._update_action_states()
        else:
            self._aiculler_review_burst_snapshot = None

        self._adapter_review_reason_phase = False
        self._adapter_review_rating_paths = tuple(review_paths)
        self.clear_adapter_review_reason_tags()
        self._window.grid.set_adapter_review_mode(review_paths, saved_labels)
        self.refresh_adapter_review_banner()
        dialog = getattr(self._window, "_ai_workflow_center_dialog", None)
        if dialog is not None:
            dialog.hide_for_adapter_review()
        diagnostics = selection.diagnostics
        hidden_count = int(diagnostics.collapsed_sibling_count)
        cap_skips = int(diagnostics.cap_skip_count)
        suffix_parts: list[str] = []
        if hidden_count:
            suffix_parts.append(f"{hidden_count} pHash sibling(s) hidden")
        if cap_skips:
            suffix_parts.append(f"{cap_skips} spread cap skip(s)")
        if diagnostics.warning:
            suffix_parts.append(str(diagnostics.warning))
        suffix = f" ({'; '.join(suffix_parts)})" if suffix_parts else ""
        self._window.statusBar().showMessage(
            f"Reviewing {len(review_paths)} adapter candidates{suffix}. "
            f"Use 1=best, 2=strong, 3=maybe, 4=weak, 5=reject."
        )

    def aiculler_phash_group_maps(self) -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
        if not self._window._current_folder:
            return {}, {}
        phash_paths = build_phash_prefilter_paths(self._window._current_folder)
        if not phash_paths.cache_path.exists():
            return {}, {}
        try:
            payload = json.loads(phash_paths.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}, {}
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, dict):
            return {}, {}
        hashes: list[tuple[str, int]] = []
        for path, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            value = entry.get("hash")
            if isinstance(value, int):
                hashes.append((str(path), value))
        if len(hashes) < 2:
            return {}, {}
        threshold = getattr(getattr(self._window, "_phash_prefilter_settings", None), "hamming_threshold", 6)
        try:
            threshold = max(0, min(64, int(threshold)))
        except (TypeError, ValueError):
            threshold = 6
        threshold = max(threshold, 12)
        parent = list(range(len(hashes)))

        def find(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(left: int, right: int) -> None:
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        for left_index, (_left_path, left_hash) in enumerate(hashes):
            for right_index in range(left_index + 1, len(hashes)):
                _right_path, right_hash = hashes[right_index]
                if hamming_distance_int(left_hash, right_hash) <= threshold:
                    union(left_index, right_index)

        grouped: dict[int, list[str]] = {}
        for index, (path, _hash) in enumerate(hashes):
            grouped.setdefault(find(index), []).append(path)
        group_by_path: dict[str, str] = {}
        group_members: dict[str, tuple[str, ...]] = {}
        group_index = 1
        for members in grouped.values():
            if len(members) < 2:
                continue
            group_id = f"phash:{group_index:04d}"
            group_index += 1
            ordered = tuple(sorted(members, key=lambda item: item.casefold()))
            group_members[group_id] = ordered
            for member in ordered:
                group_by_path[member] = group_id
        return group_by_path, group_members

    def aiculler_review_group_maps(self) -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
        bundle = self._window._review_intelligence
        if bundle is None:
            return {}, {}
        paths_by_record: dict[str, tuple[str, ...]] = {}
        for record in self._window._all_records:
            if record.is_folder:
                continue
            paths = [record.path]
            paths.extend(str(getattr(variant, "path", "") or "") for variant in record.display_variants)
            paths_by_record[os.path.normcase(os.path.normpath(record.path))] = tuple(path for path in paths if path)
        group_by_path: dict[str, str] = {}
        group_members: dict[str, tuple[str, ...]] = {}
        for group in bundle.groups:
            group_id = str(group.id)
            members: list[str] = []
            seen: set[str] = set()
            for member_path in group.member_paths:
                record_paths = paths_by_record.get(os.path.normcase(os.path.normpath(str(member_path))), (str(member_path),))
                for path in record_paths:
                    key = os.path.normcase(os.path.normpath(path))
                    if key in seen:
                        continue
                    seen.add(key)
                    members.append(path)
                    group_by_path[path] = group_id
            if len(members) >= 2:
                group_members[group_id] = tuple(members)
        return group_by_path, group_members

    def handle_dispute_chord_started(self) -> None:
        self._window.statusBar().showMessage(
            "Dispute the AI: press 1=best, 2=strong, 3=maybe, 4=weak, 5=reject. (Esc cancels.)"
        )

    def handle_dispute_chord_cancelled(self) -> None:
        self._window.statusBar().showMessage("Dispute cancelled.")

    def handle_dispute_label_requested(self, record_path: str, label: str) -> None:
        """Record the user's corrective label for a card in AI Review.

        Disputes write to the same internal labels file as adapter labels but
        also append an entry to the sibling 'disputes' map with a snapshot of
        what the AI said at dispute time. At training time, disputed rows are
        duplicated N times in the materialized ratings CSV (where N is the
        user-configurable dispute weight, default 3).
        """

        record = self._window._all_records_by_path.get(record_path)
        if record is None:
            return
        paths = self.aiculler_paths_for_current_folder()
        if paths is None:
            return
        normalized = label.strip().lower()
        if not normalized:
            return

        labels = self.load_aiculler_internal_labels(paths)
        disputes = self.load_aiculler_internal_disputes(paths)
        reason_tags_by_path = self.load_aiculler_internal_reason_tags(paths)
        previous_label = labels.get(record.path)

        ai_result = self._window._ai_run.ai_result_for_record(record)
        ai_label = ""
        ai_score = 0.0
        ai_bucket = ""
        if ai_result is not None:
            ai_score = float(getattr(ai_result, "score", 0.0) or 0.0)
            try:
                bucket = ai_result.confidence_bucket
                ai_bucket = getattr(bucket, "value", str(bucket))
            except Exception:
                ai_bucket = ""
            try:
                ai_label = ai_result.confidence_bucket_short_label or ""
            except Exception:
                ai_label = ""

        labels[record.path] = normalized
        reason_tags_by_path.pop(record.path, None)
        self.record_aiculler_override_telemetry(
            record,
            user_label=normalized,
            previous_label=previous_label,
            action_source="dispute",
        )
        disputes[record.path] = {
            "user_label": normalized,
            "ai_label": ai_label,
            "ai_score": ai_score,
            "ai_bucket": ai_bucket,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        siblings = list(self._aiculler_dedupe_siblings.get(record.path, ()))
        propagate_to_siblings = self.aiculler_should_propagate_to_siblings(record.path, normalized)
        if propagate_to_siblings:
            for sibling_path in siblings:
                sibling_record = self._window._all_records_by_path.get(sibling_path)
                sibling_previous_label = labels.get(sibling_path)
                labels[sibling_path] = normalized
                reason_tags_by_path.pop(sibling_path, None)
                disputes[sibling_path] = dict(disputes[record.path])
                if sibling_record is not None:
                    self.record_aiculler_override_telemetry(
                        sibling_record,
                        user_label=normalized,
                        previous_label=sibling_previous_label,
                        action_source="auto_action",
                        ignored_for_training=True,
                    )
        else:
            for sibling_path in siblings:
                sibling_record = self._window._all_records_by_path.get(sibling_path)
                sibling_previous_label = labels.get(sibling_path)
                labels.pop(sibling_path, None)
                disputes.pop(sibling_path, None)
                reason_tags_by_path.pop(sibling_path, None)
                if sibling_record is not None and sibling_previous_label:
                    self.record_aiculler_override_telemetry(
                        sibling_record,
                        user_label="",
                        previous_label=sibling_previous_label,
                        action_source="auto_action",
                        ignored_for_training=True,
                    )

        self.save_aiculler_internal_labels(paths, labels, disputes=disputes, reason_tags=reason_tags_by_path)
        dispute_weight = max(1, int(self._window._ai_dispute_weight_setting))
        self.save_aiculler_global_label(record.path, normalized, weight=dispute_weight, is_dispute=True)
        for sibling_path in siblings:
            sibling_label = normalized if propagate_to_siblings else ""
            self.save_aiculler_global_label(sibling_path, sibling_label, weight=dispute_weight, is_dispute=True)
        self._window.grid.set_disputed_paths(set(disputes.keys()))
        # Override the AI bucket on the spot so the dispute is visible
        # immediately without waiting for the next adapter retrain.
        self.recompute_user_label_bucket_overrides()
        sibling_suffix = f" (+ {len(siblings)} near-dup sibling(s))" if siblings and propagate_to_siblings else ""
        self._window.statusBar().showMessage(
            f"Disputed AI on {record.name} -> {normalized}"
            f"{sibling_suffix}. Counts as {self._window._ai_dispute_weight_setting}x at next training."
        )

    def exit_aiculler_adapter_review_mode(self) -> None:
        self._adapter_review_reason_phase = False
        self._adapter_review_rating_paths = ()
        self.clear_adapter_review_reason_tags()
        self._aiculler_dedupe_siblings = {}
        self._aiculler_force_propagate_siblings = set()
        snapshot = self._aiculler_review_burst_snapshot
        self._aiculler_review_burst_snapshot = None
        if snapshot is not None:
            self._window._burst_groups_enabled, self._window._burst_stacks_enabled = snapshot
            self._window._refresh_burst_group_view()
            self._window._update_action_states()
        banner = getattr(self._window, "adapter_review_banner", None)
        if banner is not None:
            banner.hide()
        timer = getattr(self._window, "_adapter_review_action_state_timer", None)
        if timer is not None:
            timer.stop()
        self.flush_adapter_review_action_state_update()
        dialog = getattr(self._window, "_ai_workflow_center_dialog", None)
        if dialog is not None:
            dialog.restore_after_adapter_review()

    def build_adapter_review_banner(self) -> QWidget:
        banner = QWidget()
        banner.setObjectName("adapterReviewBanner")
        banner.setStyleSheet(
            "QWidget#adapterReviewBanner {"
            " background: #21344f;"
            " border: 1px solid #2f6fd6;"
            " border-radius: 6px;"
            "} "
            "QLabel#adapterReviewBannerTitle { color: #d4e3f6; font-weight: 600; }"
            "QLabel#adapterReviewBannerStatus { color: #a9bbd3; font-size: 11px; }"
            "QPushButton#adapterReviewBannerExit, QPushButton#adapterReviewBannerStep {"
            " background: rgba(255,255,255,0.08); color: #e6ecf4;"
            " border: 1px solid rgba(255,255,255,0.18);"
            " border-radius: 5px; padding: 5px 14px; font-weight: 600;"
            "} "
            "QPushButton#adapterReviewBannerExit:hover, QPushButton#adapterReviewBannerStep:hover { background: rgba(255,255,255,0.14); } "
            "QPushButton#adapterReviewBannerExit:pressed, QPushButton#adapterReviewBannerStep:pressed { background: rgba(255,255,255,0.04); }"
            "QToolButton#adapterReviewReasonButton {"
            " background: rgba(255,255,255,0.08); color: #e6ecf4;"
            " border: 1px solid rgba(255,255,255,0.18);"
            " border-radius: 5px; padding: 5px 12px; font-weight: 600;"
            "}"
            "QToolButton#adapterReviewReasonButton:hover { background: rgba(255,255,255,0.14); }"
        )
        layout = QHBoxLayout(banner)
        layout.setContentsMargins(14, 8, 10, 8)
        layout.setSpacing(12)
        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(2)
        title = QLabel("Adapter Label Review")
        title.setObjectName("adapterReviewBannerTitle")
        self._adapter_review_banner_title = title
        text_column.addWidget(title)
        self._adapter_review_banner_status = QLabel("")
        self._adapter_review_banner_status.setObjectName("adapterReviewBannerStatus")
        self._adapter_review_banner_status.setWordWrap(False)
        text_column.addWidget(self._adapter_review_banner_status)
        layout.addLayout(text_column, 1)
        reason_phase_button = QPushButton("Step 2: Reasons")
        reason_phase_button.setObjectName("adapterReviewBannerStep")
        reason_phase_button.setCursor(Qt.CursorShape.PointingHandCursor)
        reason_phase_button.setToolTip("After labeling, explain only the rank 1 winners and rank 5 rejects")
        reason_phase_button.clicked.connect(self.enter_adapter_review_reason_phase)
        self._adapter_review_reason_phase_button = reason_phase_button
        layout.addWidget(reason_phase_button, 0)
        reason_button = QToolButton()
        reason_button.setObjectName("adapterReviewReasonButton")
        reason_button.setText("Reasons")
        reason_button.setCursor(Qt.CursorShape.PointingHandCursor)
        reason_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        reason_button.setToolTip("Select reasons for the current winner/reject")
        reason_menu = QMenu(reason_button)
        self._adapter_review_reason_actions: dict[str, QAction] = {}
        for key, label in self._window.ADAPTER_REASON_TAGS:
            action = QAction(label, reason_menu)
            action.setCheckable(True)
            action.toggled.connect(lambda _checked, button=reason_button: self.refresh_adapter_review_reason_button(button))
            reason_menu.addAction(action)
            self._adapter_review_reason_actions[key] = action
        reason_menu.addSeparator()
        clear_action = QAction("Clear reasons", reason_menu)
        clear_action.triggered.connect(self.clear_adapter_review_reason_tags)
        reason_menu.addAction(clear_action)
        reason_button.setMenu(reason_menu)
        self._adapter_review_reason_button = reason_button
        layout.addWidget(reason_button, 0)
        apply_reason_button = QPushButton("Apply")
        apply_reason_button.setObjectName("adapterReviewBannerStep")
        apply_reason_button.setCursor(Qt.CursorShape.PointingHandCursor)
        apply_reason_button.setToolTip("Apply selected reasons to the current image")
        apply_reason_button.clicked.connect(self.apply_adapter_review_reasons_to_selection)
        self._adapter_review_apply_reason_button = apply_reason_button
        layout.addWidget(apply_reason_button, 0)
        back_to_ratings_button = QPushButton("Back to Labels")
        back_to_ratings_button.setObjectName("adapterReviewBannerStep")
        back_to_ratings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        back_to_ratings_button.setToolTip("Return to the labeling pass")
        back_to_ratings_button.clicked.connect(self.exit_adapter_review_reason_phase)
        self._adapter_review_back_to_ratings_button = back_to_ratings_button
        layout.addWidget(back_to_ratings_button, 0)
        exit_button = QPushButton("Exit Review")
        exit_button.setObjectName("adapterReviewBannerExit")
        exit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        exit_button.setToolTip("Exit adapter label review (Esc)")
        exit_button.setShortcut(QKeySequence(Qt.Key.Key_Escape))
        exit_button.clicked.connect(self._window.grid.clear_adapter_review_mode)
        layout.addWidget(exit_button, 0)
        return banner

    def selected_adapter_review_reason_tags(self) -> tuple[str, ...]:
        actions = getattr(self, "_adapter_review_reason_actions", {})
        return tuple(key for key, action in actions.items() if action.isChecked())

    def clear_adapter_review_reason_tags(self) -> None:
        for action in getattr(self, "_adapter_review_reason_actions", {}).values():
            with QSignalBlocker(action):
                action.setChecked(False)
        button = getattr(self, "_adapter_review_reason_button", None)
        if button is not None:
            self.refresh_adapter_review_reason_button(button)

    def refresh_adapter_review_reason_button(self, button: QToolButton | None = None) -> None:
        button = button or getattr(self, "_adapter_review_reason_button", None)
        if button is None:
            return
        count = len(self.selected_adapter_review_reason_tags())
        button.setText(f"Reasons ({count})" if count else "Reasons")

    def enter_adapter_review_reason_phase(self) -> None:
        if not self._window.grid._adapter_review_mode:
            return
        paths = self.aiculler_paths_for_current_folder()
        if paths is None:
            return
        rating_paths = tuple(self._adapter_review_rating_paths or ())
        if not rating_paths:
            rating_paths = tuple(
                record.path
                for record in getattr(self._window.grid, "_items", ())
                if self._window.grid._record_in_adapter_review(getattr(self._window.grid, "_path_to_index", {}).get(record.path, -1))
            )
        labels = self.load_aiculler_internal_labels(paths)
        reason_paths = [
            path
            for path in rating_paths
            if self.is_adapter_reason_target_label(labels.get(path))
        ]
        if not reason_paths:
            message = "No rank 1 winners or rank 5 rejects are labeled yet. Rate candidates first, then run Step 2."
            self._window.statusBar().showMessage(message)
            QMessageBox.information(self._window, "Adapter Label Reasons", message)
            return
        self._adapter_review_reason_phase = True
        self.clear_adapter_review_reason_tags()
        reason_tags_by_path = self.load_aiculler_internal_reason_tags(paths)
        self._window.grid.set_adapter_review_mode(
            reason_paths,
            labels,
            label_controls_enabled=False,
            reason_controls_enabled=True,
            reason_tags_by_path=reason_tags_by_path,
            reason_options=self._window.ADAPTER_REASON_TAGS,
        )
        self.refresh_adapter_review_banner()
        self._window.statusBar().showMessage(
            f"Step 2: add reasons for {len(reason_paths)} winner/reject label(s). "
            "Use each image dropdown to select one or more reasons."
        )

    def exit_adapter_review_reason_phase(self) -> None:
        if not self._window.grid._adapter_review_mode:
            return
        paths = self.aiculler_paths_for_current_folder()
        if paths is None:
            return
        rating_paths = tuple(self._adapter_review_rating_paths or ())
        if not rating_paths:
            return
        labels = self.load_aiculler_internal_labels(paths)
        self._adapter_review_reason_phase = False
        self.clear_adapter_review_reason_tags()
        self._window.grid.set_adapter_review_mode(rating_paths, labels, label_controls_enabled=True)
        self.refresh_adapter_review_banner()
        self._window.statusBar().showMessage("Returned to Step 1: rate adapter candidates with 1-5.")

    def adapter_review_selected_records(self) -> list[ImageRecord]:
        items = list(getattr(self._window.grid, "_items", ()) or ())
        if not items:
            return []
        current_index = self._window.grid.current_index()
        indexes = self._window.grid.selected_indexes()
        if current_index >= 0 and current_index not in indexes:
            indexes = [current_index]
        records: list[ImageRecord] = []
        for index in indexes:
            if 0 <= index < len(items):
                record = items[index]
                if not record.is_folder and self._window.grid._record_in_adapter_review(index):
                    records.append(record)
        return records

    def apply_adapter_review_reasons_to_selection(self) -> None:
        if not getattr(self, "_adapter_review_reason_phase", False):
            return
        selected_reason_tags = self.selected_adapter_review_reason_tags()
        if not selected_reason_tags:
            self._window.statusBar().showMessage("Select at least one reason tag before applying.")
            return
        paths = self.aiculler_paths_for_current_folder()
        if paths is None:
            return
        labels = self.load_aiculler_internal_labels(paths)
        reason_tags_by_path = self.load_aiculler_internal_reason_tags(paths)
        records = [
            record
            for record in self.adapter_review_selected_records()
            if self.is_adapter_reason_target_label(labels.get(record.path))
        ]
        if not records:
            self._window.statusBar().showMessage("Select a rank 1 winner or rank 5 reject before applying reasons.")
            return
        for record in records:
            reason_tags_by_path[record.path] = selected_reason_tags
        self.save_aiculler_internal_labels(paths, labels, reason_tags=reason_tags_by_path)
        self.refresh_adapter_review_banner()
        label = ", ".join(dict(self._window.ADAPTER_REASON_TAGS).get(tag, tag) for tag in selected_reason_tags)
        suffix = f" to {len(records)} image(s)" if len(records) > 1 else f" to {records[0].name}"
        self._window.statusBar().showMessage(f"Applied reasons ({label}){suffix}.")
        self.advance_adapter_review_reason_phase(reason_tags_by_path)

    def advance_adapter_review_reason_phase(self, reason_tags_by_path: dict[str, tuple[str, ...]]) -> None:
        visible_indexes = list(getattr(self._window.grid, "_visible_item_indexes", ()) or ())
        if not visible_indexes:
            return
        current_index = self._window.grid.current_index()
        if current_index in visible_indexes:
            start_slot = visible_indexes.index(current_index)
        else:
            start_slot = -1
        items = list(getattr(self._window.grid, "_items", ()) or ())
        for offset in range(1, len(visible_indexes) + 1):
            index = visible_indexes[(start_slot + offset) % len(visible_indexes)]
            if not 0 <= index < len(items):
                continue
            record = items[index]
            if record.path in reason_tags_by_path:
                continue
            self._window.grid.set_current_index(index)
            try:
                self._window.grid._ensure_index_visible(index)
            except Exception:
                _logger.warning("Failed to scroll grid to next record needing a reason", exc_info=True)
            return
        self._window.statusBar().showMessage("All visible winner/reject labels have reasons.")

    def handle_aiculler_adapter_reasons_requested(self, record_path: str, reason_tags: tuple[str, ...]) -> None:
        if not getattr(self, "_adapter_review_reason_phase", False):
            return
        source_path = str(record_path)
        record = self._window._all_records_by_path.get(source_path)
        paths = self.aiculler_paths_for_current_folder()
        if paths is None:
            return
        labels = self.load_aiculler_internal_labels(paths)
        if not self.is_adapter_reason_target_label(labels.get(source_path)):
            return
        reason_tags_by_path = self.load_aiculler_internal_reason_tags(paths)
        normalized = self.normalize_adapter_reason_tags(reason_tags)
        if normalized:
            reason_tags_by_path[source_path] = normalized
        else:
            reason_tags_by_path.pop(source_path, None)
        self.save_aiculler_internal_labels(paths, labels, reason_tags=reason_tags_by_path)
        self.save_aiculler_global_reason_tags(source_path, normalized)
        self._window.grid.update_adapter_review_reason_tags(reason_tags_by_path)
        self.refresh_adapter_review_banner()
        status = "Saved reasons" if normalized else "Cleared reasons"
        display_name = record.name if record is not None else Path(source_path).name
        self._window.statusBar().showMessage(f"{status} for {display_name}.")

    def refresh_adapter_review_banner(self) -> None:
        banner = getattr(self._window, "adapter_review_banner", None)
        if banner is None:
            return
        if not self._window.grid._adapter_review_mode:
            banner.hide()
            return
        reason_phase = bool(getattr(self, "_adapter_review_reason_phase", False))
        title = getattr(self, "_adapter_review_banner_title", None)
        if title is not None:
            title.setText("Adapter Label Review - Step 2: Reasons" if reason_phase else "Adapter Label Review - Step 1: Rate")
        reason_phase_button = getattr(self, "_adapter_review_reason_phase_button", None)
        if reason_phase_button is not None:
            reason_phase_button.setVisible(not reason_phase)
        reason_button = getattr(self, "_adapter_review_reason_button", None)
        if reason_button is not None:
            reason_button.setVisible(False)
            reason_button.setEnabled(False)
        apply_reason_button = getattr(self, "_adapter_review_apply_reason_button", None)
        if apply_reason_button is not None:
            apply_reason_button.setVisible(False)
        back_to_ratings_button = getattr(self, "_adapter_review_back_to_ratings_button", None)
        if back_to_ratings_button is not None:
            back_to_ratings_button.setVisible(reason_phase)
        candidate_count = len(self._window.grid._adapter_review_paths)
        known_label_keys = {os.path.normpath(str(path)).casefold() for path in self._window.grid._adapter_labels_by_path.keys()}
        labeled = sum(
            1
            for path in self._window.grid._adapter_review_paths
            if os.path.normpath(str(path)).casefold() in known_label_keys
        )
        hidden = sum(len(siblings) for siblings in self._aiculler_dedupe_siblings.values())
        parts = [f"{candidate_count} reason target(s)" if reason_phase else f"{candidate_count} candidate(s)"]
        if reason_phase:
            paths = self.aiculler_paths_for_current_folder()
            reasoned = 0
            if paths is not None:
                reason_tags_by_path = self.load_aiculler_internal_reason_tags(paths)
                known_reason_keys = {os.path.normpath(str(path)).casefold() for path in reason_tags_by_path.keys()}
                reasoned = sum(
                    1
                    for path in self._window.grid._adapter_review_paths
                    if os.path.normpath(str(path)).casefold() in known_reason_keys
                )
            parts.append(f"{reasoned} reasoned")
            parts.append("Winners/rejects only")
            parts.append("Use each image dropdown; changes save immediately")
        else:
            parts.append(f"{labeled} labeled")
            if hidden:
                parts.append(f"{hidden} pHash/near-dup(s) hidden; labels propagate")
            parts.append("Use 1-5 to rate, then Step 2 for reasons")
        self._adapter_review_banner_status.setText(" · ".join(parts))
        banner.show()

    def handle_aiculler_adapter_label_requested(self, record_path: str, label: str) -> None:
        logger = perf_logger()
        total_start = time.perf_counter() if logger.enabled else 0.0
        step_start = total_start

        def log_step(event: str, **fields: object) -> None:
            nonlocal step_start
            if not logger.enabled:
                return
            now = time.perf_counter()
            logger.duration(
                event,
                (now - step_start) * 1000.0,
                path=record_path,
                label=label,
                **fields,
            )
            step_start = now

        record = self._window._all_records_by_path.get(record_path)
        if record is None:
            if logger.enabled:
                logger.duration(
                    "adapter_review.window.label_blocked",
                    (time.perf_counter() - total_start) * 1000.0,
                    path=record_path,
                    label=label,
                    reason="record_missing",
                )
            return
        paths = self.aiculler_paths_for_current_folder()
        if paths is None:
            if logger.enabled:
                logger.duration(
                    "adapter_review.window.label_blocked",
                    (time.perf_counter() - total_start) * 1000.0,
                    path=record_path,
                    label=label,
                    reason="no_folder_paths",
                )
            return
        labels = self.load_aiculler_internal_labels(paths)
        reason_tags_by_path = self.load_aiculler_internal_reason_tags(paths)
        log_step("adapter_review.window.load_labels", label_count=len(labels))
        normalized = label.strip().lower()
        siblings = list(self._aiculler_dedupe_siblings.get(record.path, ()))
        propagate_to_siblings = self.aiculler_should_propagate_to_siblings(record.path, normalized)
        previous_label = labels.get(record.path)
        log_step(
            "adapter_review.window.prepare",
            normalized=normalized,
            siblings=len(siblings),
            propagate_to_siblings=propagate_to_siblings,
            previous_label=previous_label or "",
        )
        if normalized:
            labels[record.path] = normalized
            reason_tags_by_path.pop(record.path, None)
            self.record_aiculler_override_telemetry(
                record,
                user_label=normalized,
                previous_label=previous_label,
                action_source="adapter_label",
            )
            log_step("adapter_review.window.telemetry_primary", ignored=False)
            if propagate_to_siblings:
                for sibling_path in siblings:
                    sibling_record = self._window._all_records_by_path.get(sibling_path)
                    sibling_previous_label = labels.get(sibling_path)
                    labels[sibling_path] = normalized
                    reason_tags_by_path.pop(sibling_path, None)
                    if sibling_record is not None:
                        self.record_aiculler_override_telemetry(
                            sibling_record,
                            user_label=normalized,
                            previous_label=sibling_previous_label,
                            action_source="auto_action",
                            ignored_for_training=True,
                        )
            else:
                for sibling_path in siblings:
                    sibling_record = self._window._all_records_by_path.get(sibling_path)
                    sibling_previous_label = labels.get(sibling_path)
                    labels.pop(sibling_path, None)
                    reason_tags_by_path.pop(sibling_path, None)
                    if sibling_record is not None and sibling_previous_label:
                        self.record_aiculler_override_telemetry(
                            sibling_record,
                            user_label="",
                            previous_label=sibling_previous_label,
                            action_source="auto_action",
                            ignored_for_training=True,
                        )
            log_step(
                "adapter_review.window.sibling_updates",
                siblings=len(siblings),
                propagated=propagate_to_siblings,
            )
        else:
            labels.pop(record.path, None)
            reason_tags_by_path.pop(record.path, None)
            if previous_label:
                self.record_aiculler_override_telemetry(
                    record,
                    user_label="",
                    previous_label=previous_label,
                    action_source="adapter_label",
                    ignored_for_training=True,
                )
            log_step("adapter_review.window.telemetry_primary", ignored=True, had_previous=bool(previous_label))
            for sibling_path in siblings:
                sibling_record = self._window._all_records_by_path.get(sibling_path)
                sibling_previous_label = labels.get(sibling_path)
                labels.pop(sibling_path, None)
                reason_tags_by_path.pop(sibling_path, None)
                if sibling_record is not None and sibling_previous_label:
                    self.record_aiculler_override_telemetry(
                        sibling_record,
                        user_label="",
                        previous_label=sibling_previous_label,
                        action_source="auto_action",
                        ignored_for_training=True,
                    )
            log_step("adapter_review.window.sibling_updates", siblings=len(siblings), propagated=False)
        self.save_aiculler_internal_labels(paths, labels, reason_tags=reason_tags_by_path, defer=True)
        log_step("adapter_review.window.queue_internal_labels", label_count=len(labels))
        self.queue_aiculler_global_label(record.path, normalized, weight=1.0, is_dispute=False)
        log_step("adapter_review.window.queue_global_primary")
        for sibling_path in siblings:
            sibling_label = normalized if propagate_to_siblings else ""
            self.queue_aiculler_global_label(sibling_path, sibling_label, weight=1.0, is_dispute=False)
        log_step("adapter_review.window.queue_global_siblings", siblings=len(siblings))
        self._window.grid.update_adapter_review_labels(labels)
        log_step("adapter_review.window.grid_update_labels", label_count=len(labels))
        # Reflect the label in the AI Review bucket immediately so user
        # decisions show up the moment they save.
        self.apply_user_label_bucket_override_delta(record.path, normalized)
        for sibling_path in siblings:
            sibling_label = normalized if propagate_to_siblings else ""
            self.apply_user_label_bucket_override_delta(sibling_path, sibling_label)
        log_step("adapter_review.window.bucket_override_delta", siblings=len(siblings))
        self.refresh_adapter_review_banner()
        log_step("adapter_review.window.refresh_banner")
        sibling_suffix = f" (+ {len(siblings)} near-dup sibling(s))" if siblings and propagate_to_siblings else ""
        self._window.statusBar().showMessage(
            f"Saved adapter label for {record.name}: {normalized or 'unlabeled'}{sibling_suffix}"
        )
        log_step("adapter_review.window.status_message")
        if logger.enabled:
            logger.duration(
                "adapter_review.window.label_total",
                (time.perf_counter() - total_start) * 1000.0,
                path=record_path,
                label=normalized,
                siblings=len(siblings),
                propagated=propagate_to_siblings,
                final_label_count=len(labels),
            )

    @staticmethod
    def aiculler_should_propagate_label_to_siblings(label: str) -> bool:
        return label.strip().lower() in {"maybe", "weak", "reject", "bad", "r", "no", "0"}

    def aiculler_should_propagate_to_siblings(self, survivor_path: str, label: str) -> bool:
        normalized = str(label).strip().lower()
        if not normalized:
            return False
        if survivor_path in getattr(self, "_aiculler_force_propagate_siblings", set()):
            return True
        return self.aiculler_should_propagate_label_to_siblings(normalized)

    def refresh_adapter_status_indicator(self) -> None:
        if not hasattr(self, "adapter_status_label"):
            return
        summary: dict[str, object] | None = None
        try:
            paths = self.aiculler_paths_for_current_folder()
        except Exception:
            _logger.exception("Failed to resolve aiculler paths for adapter status indicator")
            paths = None
        if paths is not None:
            try:
                summary = load_adapter_status_summary(aiculler_db_path(paths))
            except Exception:
                _logger.exception("Failed to load adapter status summary")
                summary = None
        text, tooltip = self.adapter_status_display(summary)
        self.adapter_status_label.setText(text)
        self.adapter_status_label.setToolTip(tooltip)

    @staticmethod
    def adapter_status_display(summary: dict[str, object] | None) -> tuple[str, str]:
        if summary is None or not summary.get("db_exists"):
            return ("Adapter: —", "Run Index & Score in the AI Workflow Center to begin building the adapter.")
        rating_count = int(summary.get("rating_count") or 0)
        model_version = str(summary.get("model_version") or "")
        if not model_version:
            label = f"Adapter: untrained · {rating_count} label(s)"
            tooltip = (
                "No adapter trained for this folder yet.\n"
                f"Recorded labels: {rating_count}\n"
                "Use Review Adapter Labels then Train Adapter to fit one."
            )
            return (label, tooltip)
        bits: list[str] = [f"v{model_version}", f"{rating_count} label(s)"]
        score_fit = summary.get("score_fit_percent")
        if isinstance(score_fit, (int, float)):
            bits.append(f"fit {float(score_fit):.1f}%")
        train_mae = summary.get("train_mae")
        if isinstance(train_mae, (int, float)):
            bits.append(f"MAE {float(train_mae):.3f}")
        holdout_mae = summary.get("holdout_mae")
        if isinstance(holdout_mae, (int, float)):
            bits.append(f"hold {float(holdout_mae):.3f}")
        train_lift = summary.get("train_rank_lift")
        if isinstance(train_lift, (int, float)):
            bits.append(f"lift {float(train_lift):+.2f}")
        label = "Adapter: " + " · ".join(bits)

        tooltip_lines = [
            f"Adapter version: {model_version}",
        ]
        created_at = str(summary.get("created_at") or "")
        if created_at:
            tooltip_lines.append(f"Trained: {created_at}")
        tooltip_lines.append(f"Recorded labels: {rating_count}")
        scored_count = int(summary.get("scored_count") or 0)
        if scored_count:
            tooltip_lines.append(f"Adapter-scored images: {scored_count}")
        train_count = summary.get("train_count")
        if isinstance(train_count, int):
            tooltip_lines.append(f"Train fold: {train_count} label(s)")
        if isinstance(train_mae, (int, float)):
            tooltip_lines.append(f"Train MAE: {float(train_mae):.4f}")
        if isinstance(score_fit, (int, float)):
            tooltip_lines.append(f"Score Fit: {float(score_fit):.1f}%")
        if isinstance(train_lift, (int, float)):
            tooltip_lines.append(f"Train rank lift: {float(train_lift):+.3f}")
        holdout_count = summary.get("holdout_count")
        if isinstance(holdout_count, int):
            tooltip_lines.append(f"Holdout fold: {holdout_count} label(s)")
        if isinstance(holdout_mae, (int, float)):
            tooltip_lines.append(f"Holdout MAE: {float(holdout_mae):.4f}")
        holdout_lift = summary.get("holdout_rank_lift")
        if isinstance(holdout_lift, (int, float)):
            tooltip_lines.append(f"Holdout rank lift: {float(holdout_lift):+.3f}")
        keeper_recall = summary.get("keeper_recall")
        if isinstance(keeper_recall, (int, float)):
            tooltip_lines.append(f"Keeper recall: {float(keeper_recall) * 100.0:.1f}%")
        false_reject_rate = summary.get("false_reject_rate")
        if isinstance(false_reject_rate, (int, float)):
            tooltip_lines.append(f"False reject rate: {float(false_reject_rate) * 100.0:.1f}%")
        review_reduction = summary.get("review_reduction_percent")
        if isinstance(review_reduction, (int, float)):
            tooltip_lines.append(f"Review reduction: {float(review_reduction):.1f}%")
        return (label, "\n".join(tooltip_lines))

    def adapter_review_mode_active(self) -> bool:
        return bool(getattr(getattr(self._window, "grid", None), "_adapter_review_mode", False))

    def schedule_adapter_review_action_state_update(self) -> None:
        timer = getattr(self._window, "_adapter_review_action_state_timer", None)
        if timer is not None:
            timer.start()

    def flush_adapter_review_action_state_update(self) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        self._window._update_action_states()
        if logger.enabled:
            logger.duration(
                "adapter_review.window.deferred_action_states",
                (time.perf_counter() - start) * 1000.0,
                active=self.adapter_review_mode_active(),
            )

    @staticmethod
    def aiculler_bucket_for_user_label(label: str) -> str:
        normalized = label.strip().lower()
        if normalized in {"hero", "portfolio"}:
            return "ai pick"
        if normalized in {"strong", "keep", "good", "k", "yes", "1"}:
            return "keeper"
        if normalized == "maybe":
            return "needs review"
        if normalized in {"weak", "reject", "bad", "r", "no", "0"}:
            return "reject"
        return normalized

    def record_aiculler_override_telemetry(
        self,
        record: ImageRecord,
        *,
        user_label: str,
        previous_label: str | None,
        action_source: str,
        ignored_for_training: bool = False,
    ) -> None:
        raw_result = self._window._ai_run.raw_ai_result_for_record(record)
        if raw_result is None:
            return
        user_bucket = self.aiculler_bucket_for_user_label(user_label)
        if not user_bucket:
            if not previous_label:
                return
            user_bucket = "unlabeled"
            ignored_for_training = True
        ai_bucket = ai_cull_bucket_for_result(raw_result).value
        previous_bucket = self.aiculler_bucket_for_user_label(previous_label or "") or None
        paths = self.aiculler_paths_for_current_folder()
        adapter_version = ""
        if paths is not None:
            db_path = aiculler_db_path(paths)
            try:
                mtime_ns = db_path.stat().st_mtime_ns if db_path.exists() else 0
                cache_key = str(db_path)
                cached = self._aiculler_telemetry_adapter_version_cache.get(cache_key)
                if cached is not None and cached[0] == mtime_ns:
                    adapter_version = cached[1]
                else:
                    adapter_version = latest_adapter_model_version(db_path)
                    self._aiculler_telemetry_adapter_version_cache[cache_key] = (mtime_ns, adapter_version)
            except Exception:
                _logger.exception("Failed to resolve adapter version for telemetry event")
                adapter_version = ""
        event = TelemetryEvent(
            image_id=str(getattr(raw_result, "image_id", "") or record.path),
            folder_id=str(self._window._current_folder or Path(record.path).parent),
            cluster_id=str(getattr(raw_result, "group_id", "") or "") or None,
            category_id=str(getattr(raw_result, "primary_category", "") or "") or None,
            ai_initial_bucket=normalize_bucket(ai_bucket),
            user_final_bucket=normalize_bucket(user_bucket),
            previous_bucket=normalize_bucket(previous_bucket) if previous_bucket else None,
            override_type=classify_override(ai_bucket, user_bucket),
            action_source=action_source,
            ai_initial_score=float(getattr(raw_result, "score", 0.0) or 0.0),
            base_score=(
                float(getattr(raw_result, "tag_base_score", 0.0) or 0.0)
                if getattr(raw_result, "tag_base_score", None) is not None
                else float(getattr(raw_result, "technical_score", 0.0) or 0.0)
                if getattr(raw_result, "technical_score", None) is not None
                else None
            ),
            adapter_score=None,
            topiq_score=(
                float(getattr(raw_result, "technical_score", 0.0) or 0.0)
                if getattr(raw_result, "technical_score", None) is not None
                else None
            ),
            adapter_version=adapter_version or None,
            model_version=adapter_version or None,
            is_final=1,
            ignored_for_training=1 if ignored_for_training else 0,
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )
        self.queue_aiculler_telemetry_event(event)

    def apply_user_label_override(self, result, record):
        """If the user has saved a label for this path (via adapter combo or
        dispute chord), use it as the authoritative bucket. The user's call
        always wins over the model's call in the live view — disputes don't
        need to wait until the next retrain to be visible. Training still
        picks them up as weighted samples on the next Train Adapter run."""

        if result is None or not getattr(self, "_user_label_bucket_overrides", None):
            return result
        bucket_name = self._user_label_bucket_overrides.get(_memory_path_key(record.path))
        if bucket_name is None:
            return result
        from .ai_results import AIConfidenceBucket, _combine_confidence_summaries, _replace_confidence
        bucket = getattr(AIConfidenceBucket, bucket_name, None)
        if bucket is None:
            return result
        summary = _combine_confidence_summaries(
            getattr(result, "confidence_summary", ""),
            "Bucket set by your saved label (overrides the AI's call).",
        )
        return _replace_confidence(result, bucket, summary)

    def recompute_user_label_bucket_overrides(self) -> None:
        """Rebuild the in-memory map from labeled path -> bucket AND the set
        of disputed path keys. Called when the user labels / disputes a card,
        and when entering a folder so existing labels surface in the AI Review
        badges immediately."""

        overrides: dict[str, str] = {}
        disputed_keys: set[str] = set()
        try:
            paths = self.aiculler_paths_for_current_folder()
        except Exception:
            _logger.exception("Failed to resolve aiculler paths for user label bucket overrides")
            paths = None
        if paths is None:
            self._user_label_bucket_overrides = overrides
            self._disputed_path_keys = disputed_keys
            return
        try:
            labels = self.load_aiculler_internal_labels(paths)
        except Exception:
            _logger.exception("Failed to load aiculler internal labels for bucket overrides")
            labels = {}
        for path, label in labels.items():
            bucket_name = self._window._USER_LABEL_TO_BUCKET.get(str(label).strip().lower())
            if bucket_name is not None:
                overrides[_memory_path_key(path)] = bucket_name
        try:
            disputes = self.load_aiculler_internal_disputes(paths)
        except Exception:
            _logger.exception("Failed to load aiculler internal disputes for bucket overrides")
            disputes = {}
        for path in disputes:
            disputed_keys.add(_memory_path_key(path))
        self._user_label_bucket_overrides = overrides
        self._disputed_path_keys = disputed_keys
        # Force a grid repaint so any visible cards reflect the new bucket.
        if hasattr(self._window, "grid") and self._window.grid is not None:
            self._window.grid.viewport().update()
        # If the user is currently filtering by AI Disagreements, the set of
        # matched records just changed — re-apply the filter so the freshly
        # disputed card appears (or stops appearing if it was undisputed).
        if self._window._filter_query.quick_filter == FilterMode.AI_DISAGREEMENTS:
            self._window._records_view.apply_filter_query_change()

    def apply_user_label_bucket_override_delta(self, path: str, label: str) -> None:
        key = _memory_path_key(path)
        bucket_name = self._window._USER_LABEL_TO_BUCKET.get(str(label).strip().lower())
        if bucket_name is None:
            self._user_label_bucket_overrides.pop(key, None)
        else:
            self._user_label_bucket_overrides[key] = bucket_name
        if hasattr(self._window, "grid") and self._window.grid is not None:
            self._window.grid.viewport().update()

    def is_record_disputed(self, record: ImageRecord | None) -> bool:
        if record is None or not self._disputed_path_keys:
            return False
        return _memory_path_key(record.path) in self._disputed_path_keys

    def refresh_aiculler_ingested_paths_for_current_folder(self) -> None:
        folder_key = normalized_path_key(self._window._current_folder) if self._window._current_folder else ""
        if not folder_key:
            self._window._aiculler_ingested_path_keys = set()
            self._window._aiculler_ingested_sibling_keys = set()
            self._window._aiculler_ingested_cache_folder_key = ""
            return
        if self._window._aiculler_ingested_cache_folder_key == folder_key:
            return
        path_keys: set[str] = set()
        sibling_keys: set[str] = set()
        try:
            paths = build_aiculler_workflow_paths(self._window._current_folder)
            db_path = aiculler_db_path(paths)
            if db_path.exists():
                connection = sqlite3.connect(db_path)
                try:
                    rows = connection.execute(
                        """
                        SELECT images.source_path
                        FROM images
                        INNER JOIN embeddings ON embeddings.image_id = images.id
                        WHERE images.status = 'ready'
                        """
                    ).fetchall()
                finally:
                    connection.close()
                for (source_path,) in rows:
                    key = normalized_path_key(str(source_path))
                    if not key:
                        continue
                    path_keys.add(key)
                    sibling_key = _path_parent_stem_key(str(source_path))
                    if sibling_key:
                        sibling_keys.add(sibling_key)
        except Exception:
            _logger.exception("Failed to refresh aiculler ingested paths for %s", self._window._current_folder)
            path_keys = set()
            sibling_keys = set()
        self._window._aiculler_ingested_path_keys = path_keys
        self._window._aiculler_ingested_sibling_keys = sibling_keys
        self._window._aiculler_ingested_cache_folder_key = folder_key

    def record_was_aiculler_ingested(self, record: ImageRecord | None) -> bool:
        if record is None:
            return False
        if self._window._aiculler_ingested_cache_folder_key != (normalized_path_key(self._window._current_folder) if self._window._current_folder else ""):
            self.refresh_aiculler_ingested_paths_for_current_folder()
        for path in record.stack_paths:
            if normalized_path_key(path) in self._window._aiculler_ingested_path_keys:
                return True
            sibling_key = _path_parent_stem_key(path)
            if sibling_key and sibling_key in self._window._aiculler_ingested_sibling_keys:
                return True
        return False
