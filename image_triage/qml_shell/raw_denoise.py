"""Detail > Denoise in the RAW editor: a checkbox and an Amount slider, as in Camera Raw.

Ticking the box makes the photo's denoised raw (``image_triage.ai_denoise``) in the background, once; the preview switches to it
when it is ready. The setting itself (``aiDenoise``) is an ordinary undoable draft value. Changing Amount rewrites four bytes of the
denoised raw, but the preview worker then reads the whole raw again, so Amount is committed when the slider pauses or is let go,
not on every mouse event.
"""
from __future__ import annotations

import math
import threading

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from .. import ai_denoise

COMMIT_MS = 350


class RawDenoise(QObject):
    changed = Signal()
    _finished = Signal(int, str, str)
    _progress = Signal(int, str)

    def __init__(self, raw):
        super().__init__(raw)
        self.raw = raw
        self._path = ""
        self._busy = False
        self._message = self._error = self._reason = ""
        self._ticket = 0
        self._cancel = threading.Event()
        self._maker = ai_denoise.make        # tests replace it
        self._queued = None                  # an Amount waiting to be committed
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(COMMIT_MS)
        self._timer.timeout.connect(self.flush)
        self._finished.connect(self._arrived)
        self._progress.connect(self._report)
        raw.changed.connect(self._sync)

    busy = Property(bool, lambda self: self._busy, notify=changed)
    message = Property(str, lambda self: self._message, notify=changed)
    error = Property(str, lambda self: self._error, notify=changed)
    unavailable = Property(str, lambda self: self._reason, notify=changed)

    @Property(bool, notify=changed)
    def ready(self):
        return bool(self._path) and ai_denoise.ready(self._path)

    @Property("QVariantMap", notify=changed)
    def settings(self):
        current = ai_denoise.normalized(self.raw._draft.get(ai_denoise.KEY))
        if self._queued is not None:
            current["amount"] = self._queued
        return current

    def _sync(self):
        if self._path != self.raw._path:
            self._path = self.raw._path
            self._cancel.set()
            self._ticket += 1
            self._busy, self._message, self._error, self._queued = False, "", "", None
            self._timer.stop()
        self.changed.emit()

    @Slot()
    def refresh(self):
        self._reason = ai_denoise.unavailable_reason()
        self.changed.emit()

    @Slot(bool)
    def setEnabled(self, enabled):
        if not self.raw.loaded or self.raw.saving:
            return
        current = ai_denoise.normalized(self.raw._draft.get(ai_denoise.KEY))
        self.raw._set_values({ai_denoise.KEY: {**current, "enabled": bool(enabled)}}, "Denoise")
        if enabled and not ai_denoise.ready(self._path):
            self._start(current["amount"])

    @Slot(float)
    def setAmount(self, value):
        if not math.isfinite(value):
            return
        self._queued = float(max(0.0, min(100.0, value)))
        self.changed.emit()
        self._timer.start()

    @Slot()
    def flush(self):
        self._timer.stop()
        queued, self._queued = self._queued, None
        if queued is None or not self.raw.loaded or self.raw.saving:
            return
        current = ai_denoise.normalized(self.raw._draft.get(ai_denoise.KEY))
        self.raw._set_values({ai_denoise.KEY: {**current, "amount": queued}}, "Denoise Amount")

    @Slot()
    def cancel(self):
        if self._busy:
            self._cancel.set()
            self._ticket += 1
            self._busy, self._message = False, "Cancelled"
            self.changed.emit()

    @Slot()
    def retry(self):
        current = ai_denoise.normalized(self.raw._draft.get(ai_denoise.KEY))
        if current["enabled"] and not self._busy:
            self._start(current["amount"])

    def _start(self, amount):
        self.refresh()
        if self._reason:
            self._error = self._reason
            self.changed.emit()
            return
        if self._busy:
            return
        self._ticket += 1
        ticket, path = self._ticket, self._path
        self._cancel = cancel = threading.Event()
        self._busy, self._message, self._error = True, "Preparing…", ""
        self.changed.emit()
        maker = self._maker

        def progress(text):
            if not cancel.is_set():
                try:
                    self._progress.emit(ticket, str(text))
                except RuntimeError:
                    pass

        def work():
            error = ""
            try:
                maker(path, amount, progress=progress, cancel=cancel)
            except InterruptedError:
                error = "Cancelled"
            except Exception as exc:
                error = str(exc) or "AI Denoise failed"
            try:
                self._finished.emit(ticket, path, error)
            except RuntimeError:
                pass  # the editor was closed meanwhile

        threading.Thread(target=work, name="qml-ai-denoise", daemon=True).start()

    def _report(self, ticket, text):
        if ticket == self._ticket and self._busy:
            self._message = text
            self.changed.emit()

    def _arrived(self, ticket, path, error):
        if ticket != self._ticket:
            return
        self._busy, self._message = False, ""
        self._error = error
        self.changed.emit()
        if not error and path == self.raw._path and self.raw.loaded:
            self.raw._draft_changed()        # develop the denoised raw now that it exists
