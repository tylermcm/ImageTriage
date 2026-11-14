"""Give several photos the look of the one being edited, without opening each.

Each photo is developed with its own masks, retouching, crop and Light Depth and the shared look, and only then is its
recipe published, exactly as Done does for one photo (``RawEditor._work``). Photos are done one after another on a
worker; Cancel stops before the next one.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Property, Signal, Slot

from .. import light_depth, photocraft_bridge as bridge


def sync_look(paths, look, *, save, unsaved=lambda path: False, cancelled=lambda: False, step=lambda index, name: None,
              defaults=None) -> dict:
    """Apply ``look`` to each of ``paths``. Returns the photos saved (with their render), the ones left alone and why,
    and the ones that failed and why. ``save(path, original, draft)`` renders and publishes one photo."""
    from ..raw_presets import with_look

    saved, skipped, failed = [], [], []
    for index, path in enumerate(paths):
        if cancelled():
            break
        name = os.path.basename(path)
        step(index, name)
        try:
            if Path(path).suffix.lower() not in bridge.RAW_SUFFIXES:
                skipped.append((name, "not a RAW photo"))
                continue
            if bridge.sidecar_pcraft_path(path).exists():
                skipped.append((name, "has a PhotoCraft project"))
                continue
            if unsaved(path):
                skipped.append((name, "has unsaved edits of its own"))
                continue
            state, recipe, reason = bridge.recipe_state(path)
            if state == bridge.RECIPE_INVALID:
                skipped.append((name, reason or "its saved edit cannot be read"))
                continue
            original = dict(recipe["cameraRaw"]) if recipe else {}
            placed = light_depth.read_sidecar(bridge.recipe_path(path))
            if placed and light_depth.find_mask(original.get("masks")) >= 0:
                original["lightDepth"] = placed          # so saving keeps the photo's Light Depth settings
            draft = with_look(original, look, original, defaults or {})
            if draft == original:
                skipped.append((name, "already has these settings"))
                continue
            saved.append((path, save(path, original, draft)))
        except Exception as error:
            failed.append((name, str(error) or type(error).__name__))
    return {"saved": saved, "skipped": skipped, "failed": failed}


class RawSync(QObject):
    changed = Signal()
    _progress = Signal(int, int, str)
    _finished = Signal(int, object)

    def __init__(self, raw):
        super().__init__(raw)
        self.raw = raw
        self._running = False
        self._done = self._total = 0
        self._current = ""
        self._summary = ""
        self._ticket = 0
        self._stop = threading.Event()
        self._save = None                       # tests replace the native save
        self._progress.connect(self._report)
        self._finished.connect(self._arrived)

    running = Property(bool, lambda self: self._running, notify=changed)
    done = Property(int, lambda self: self._done, notify=changed)
    total = Property(int, lambda self: self._total, notify=changed)
    current = Property(str, lambda self: self._current, notify=changed)
    summary = Property(str, lambda self: self._summary, notify=changed)   # what the last sync did, in a sentence

    @Slot("QVariantList", result=int)
    def eligible(self, paths) -> int:
        """How many of ``paths`` a sync would try: RAW photos other than the one open."""
        return len(self._targets(paths))

    def _targets(self, paths):
        seen, out = set(), []
        for path in paths or []:
            if (isinstance(path, str) and path and path != self.raw._path and path not in seen
                    and Path(path).suffix.lower() in bridge.RAW_SUFFIXES):
                seen.add(path)
                out.append(path)
        return out

    @Slot("QVariantList")
    def start(self, paths) -> None:
        raw = self.raw
        targets = self._targets(paths)
        if self._running or not raw.loaded or raw.saving or not targets:
            return
        from .raw_editor import DEFAULTS, RawEditor

        look = raw._look()
        save = self._save or (lambda path, original, draft: RawEditor._work("save", path, original, draft, ""))
        self._ticket += 1
        ticket = self._ticket
        self._stop = stop = threading.Event()
        self._running, self._done, self._total, self._current, self._summary = True, 0, len(targets), "", ""
        self.changed.emit()

        def step(index, name):
            try:
                self._progress.emit(ticket, index, name)
            except RuntimeError:
                pass

        def work():
            result = sync_look(targets, look, save=save, unsaved=raw.has_unsaved, cancelled=stop.is_set, step=step, defaults=DEFAULTS)
            result["cancelled"] = stop.is_set() and len(result["saved"]) + len(result["skipped"]) + len(result["failed"]) < len(targets)
            try:
                self._finished.emit(ticket, result)
            except RuntimeError:
                pass                             # the editor was closed meanwhile; what was saved is saved

        threading.Thread(target=work, name="qml-raw-sync", daemon=True).start()

    @Slot()
    def cancel(self) -> None:
        if self._running:
            self._stop.set()
            self._current = "Stopping after this photo…"
            self.changed.emit()

    def _report(self, ticket, index, name):
        if ticket == self._ticket and self._running:
            self._done, self._current = index, name
            self.changed.emit()

    def _arrived(self, ticket, result):
        if ticket != self._ticket:
            return
        self._running = False
        saved, skipped, failed = result["saved"], result["skipped"], result["failed"]
        self._done = len(saved) + len(skipped) + len(failed)
        self._current = ""
        parts = [f"{len(saved)} photo{'s' if len(saved) != 1 else ''} updated"]
        if skipped:
            parts.append(f"{len(skipped)} left alone ({'; '.join(sorted({why for _name, why in skipped}))})")
        if failed:
            parts.append(f"{len(failed)} failed ({failed[0][0]}: {failed[0][1]})")
        if result.get("cancelled"):
            parts.append("stopped before the rest")
        self._summary = ", ".join(parts) + "."
        for path, render in saved:
            self.raw.saved.emit(path, render)    # the grid and the viewer show the new picture
        self.changed.emit()
