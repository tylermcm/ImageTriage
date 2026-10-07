from __future__ import annotations

import os
import shutil
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QEventLoop, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .file_ops import FileMove, unique_destination

CHUNK_BYTES = 4 * 1024 * 1024
PROGRESS_INTERVAL_S = 0.05
SHOW_DELAY_MS = 350
SPEED_WINDOW_S = 3.0


class _Cancelled(Exception):
    pass


@dataclass(slots=True)
class TransferItem:
    key: int
    name: str
    source_paths: tuple[str, ...]


@dataclass(slots=True)
class TransferResult:
    moved: dict[int, tuple[FileMove, ...]] = field(default_factory=dict)
    failed: dict[int, str] = field(default_factory=dict)
    cancelled: bool = False


def format_bytes(value: float) -> str:
    size = float(max(0.0, value))
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if size < 1024.0 or unit == "TB":
            if unit == "bytes":
                return f"{int(size)} bytes"
            return f"{size:.1f} {unit}" if size < 100 else f"{size:.0f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


def format_remaining(seconds: float | None) -> str:
    if seconds is None:
        return "Calculating..."
    seconds = max(0.0, seconds)
    if seconds < 1.5:
        return "Less than a second"
    if seconds < 60:
        return f"About {int(round(seconds))} seconds"
    minutes = seconds / 60.0
    if minutes < 60:
        whole = int(round(minutes))
        return f"About {whole} minute{'s' if whole != 1 else ''}"
    hours, rest = divmod(int(minutes), 60)
    return f"About {hours} hour{'s' if hours != 1 else ''} {rest} min"


class TransferWorker(QThread):
    """Moves or copies each item's files, reporting byte-level progress.

    For a move, same-volume transfers are renames and finish instantly;
    anything else is copied in chunks and the source removed afterwards, so a
    cancel or error never leaves a half-moved item behind. For a copy
    (``keep_source=True``), the source is never touched and every transfer is
    a real byte copy (no rename fast path, since a rename can't leave the
    source in place) — a cancel deletes whatever was already copied to the
    destination so far, leaving it exactly as it was before the copy started.
    """

    # object, not int: Qt's int is 32-bit and byte counts pass 2 GB.
    totals = Signal(object, int)
    progress = Signal(object, int, str, bool)
    itemDone = Signal(int, object)
    itemFailed = Signal(int, str)

    def __init__(
        self, items: list[TransferItem], destination: str, parent=None, *, keep_source: bool = False
    ) -> None:
        super().__init__(parent)
        self._items = items
        self._destination = destination
        self._keep_source = keep_source
        self._cancel = False
        self._done_bytes = 0
        self._items_done = 0
        self._last_emit = 0.0
        self.result = TransferResult()

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        sizes = {item.key: sum(_size(path) for path in item.source_paths) for item in self._items}
        self.totals.emit(sum(sizes.values()), len(self._items))
        try:
            Path(self._destination).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            for item in self._items:
                self.result.failed[item.key] = str(exc)
                self.itemFailed.emit(item.key, str(exc))
            return
        for item in self._items:
            if self._cancel:
                break
            base = self._done_bytes
            moves: list[FileMove] = []
            try:
                for source in item.source_paths:
                    if self._cancel:
                        raise _Cancelled
                    target = unique_destination(self._destination, Path(source).name)
                    self._move_file(source, target, item.name)
                    moves.append(FileMove(source_path=source, target_path=target))
            except _Cancelled:
                self._rollback(moves)
                self.result.cancelled = True
                break
            except OSError as exc:
                self._rollback(moves)
                self.result.failed[item.key] = str(exc)
                self.itemFailed.emit(item.key, str(exc))
            else:
                self.result.moved[item.key] = tuple(moves)
                self.itemDone.emit(item.key, tuple(moves))
            self._done_bytes = base + sizes[item.key]
            self._items_done += 1
            self._emit(item.name, False, force=True)
        if self._cancel:
            self.result.cancelled = True

    def _emit(self, name: str, copying: bool, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_emit < PROGRESS_INTERVAL_S:
            return
        self._last_emit = now
        self.progress.emit(self._done_bytes, self._items_done, name, copying)

    def _move_file(self, source: str, target: str, name: str) -> None:
        if not self._keep_source:
            try:
                os.rename(source, target)
                self._done_bytes += _size(target)
                self._emit(name, False)
                return
            except OSError:
                if not os.path.exists(source):
                    raise
        written = 0
        try:
            with open(source, "rb") as reader, open(target, "wb") as writer:
                while True:
                    if self._cancel:
                        raise _Cancelled
                    chunk = reader.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    writer.write(chunk)
                    written += len(chunk)
                    self._done_bytes += len(chunk)
                    self._emit(name, True)
            shutil.copystat(source, target)
        except BaseException:
            self._done_bytes -= written
            try:
                os.remove(target)
            except OSError:
                pass
            raise
        if not self._keep_source:
            os.remove(source)

    def _rollback(self, moves: list[FileMove]) -> None:
        for move in reversed(moves):
            try:
                if not os.path.exists(move.target_path):
                    continue
                if self._keep_source:
                    # The source was never touched; undo a copy by simply
                    # deleting the (partial or finished) destination file.
                    os.remove(move.target_path)
                else:
                    Path(move.source_path).parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(move.target_path, move.source_path)
            except OSError:
                pass


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


class TransferProgressDialog(QDialog):
    cancelRequested = Signal()

    def __init__(self, parent: QWidget | None, *, verb: str, item_count: int, source: str, destination: str) -> None:
        super().__init__(parent)
        self.setObjectName("transferProgressDialog")
        self.setWindowTitle(f"{verb} {item_count} item{'s' if item_count != 1 else ''}")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(460)
        self._verb = verb
        self._item_count = item_count
        self._total_bytes = 0
        self._samples: deque[tuple[float, int]] = deque()
        self._speed = 0.0
        self._smoothed_speed = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(6)

        self.headline = QLabel(self)
        self.headline.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(self.headline)

        self.route = QLabel(self)
        self.route.setStyleSheet("color: palette(placeholder-text);")
        self._route_text = f"from {source or 'library'} to {destination}"
        layout.addWidget(self.route)
        layout.addSpacing(6)

        self.bar = QProgressBar(self)
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(14)
        layout.addWidget(self.bar)

        stats = QHBoxLayout()
        self.percent_label = QLabel("0% complete", self)
        self.speed_label = QLabel("", self)
        self.speed_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        stats.addWidget(self.percent_label, 1)
        stats.addWidget(self.speed_label, 0)
        layout.addLayout(stats)
        layout.addSpacing(6)

        self.name_label = QLabel("", self)
        self.remaining_label = QLabel("", self)
        self.items_label = QLabel("", self)
        for label in (self.name_label, self.remaining_label, self.items_label):
            layout.addWidget(label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.clicked.connect(self._request_cancel)
        buttons.addWidget(self.cancel_button)
        layout.addSpacing(6)
        layout.addLayout(buttons)

        self._refresh_static()
        self.set_progress(0, 0, "", False)

    def _refresh_static(self) -> None:
        self.headline.setText(f"{self._verb} {self._item_count} item{'s' if self._item_count != 1 else ''}")
        metrics = QFontMetrics(self.route.font())
        self.route.setText(metrics.elidedText(self._route_text, Qt.TextElideMode.ElideMiddle, 420))
        self.route.setToolTip(self._route_text)

    def set_totals(self, total_bytes: int, item_count: int) -> None:
        self._total_bytes = max(0, int(total_bytes))
        self._item_count = max(self._item_count, int(item_count))
        self._refresh_static()

    def set_progress(self, done_bytes: int, items_done: int, name: str, copying: bool) -> None:
        total = self._total_bytes
        fraction = min(1.0, done_bytes / total) if total > 0 else min(1.0, items_done / max(1, self._item_count))
        fraction = max(0.0, fraction)
        self.bar.setValue(int(fraction * 1000))
        self.percent_label.setText(f"{int(fraction * 100)}% complete")
        self.setWindowTitle(f"{int(fraction * 100)}% complete")

        now = time.monotonic()
        self._samples.append((now, done_bytes))
        while len(self._samples) > 2 and now - self._samples[0][0] > SPEED_WINDOW_S:
            self._samples.popleft()
        if copying and len(self._samples) >= 2:
            elapsed = self._samples[-1][0] - self._samples[0][0]
            if elapsed > 0.2:
                instant = (self._samples[-1][1] - self._samples[0][1]) / elapsed
                self._smoothed_speed = instant if self._smoothed_speed <= 0 else 0.6 * self._smoothed_speed + 0.4 * instant
        elif not copying:
            self._smoothed_speed = 0.0

        if self._smoothed_speed > 0:
            self.speed_label.setText(f"{format_bytes(self._smoothed_speed)}/s")
        else:
            self.speed_label.setText("")

        remaining_bytes = max(0, total - done_bytes)
        remaining_items = max(0, self._item_count - items_done)
        if remaining_items == 0:
            eta = 0.0
        elif self._smoothed_speed > 0 and total > 0:
            eta = remaining_bytes / self._smoothed_speed
        else:
            eta = None if copying else 0.0
        self.remaining_label.setText(f"Time remaining: {format_remaining(eta)}")
        size_text = f" ({format_bytes(remaining_bytes)})" if total > 0 else ""
        self.items_label.setText(f"Items remaining: {remaining_items}{size_text}")
        if name:
            metrics = QFontMetrics(self.name_label.font())
            self.name_label.setText("Name: " + metrics.elidedText(name, Qt.TextElideMode.ElideMiddle, 380))

    def _request_cancel(self) -> None:
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("Cancelling...")
        self.cancelRequested.emit()

    def closeEvent(self, event) -> None:
        if self.cancel_button.isEnabled():
            self._request_cancel()
        event.ignore()

    def reject(self) -> None:
        if self.cancel_button.isEnabled():
            self._request_cancel()


def run_file_transfer(
    parent: QWidget,
    items: list[TransferItem],
    destination: str,
    *,
    source_label: str = "",
    verb: str = "Moving",
    keep_source: bool = False,
) -> TransferResult:
    """Run a move or copy on a worker thread while showing a Windows-style
    progress dialog. Returns once every item is finished, failed or cancelled,
    so callers can stay synchronous. ``keep_source=True`` copies instead of
    moving (the source is never touched; a cancel removes whatever was
    already copied to the destination)."""
    worker = TransferWorker(items, destination, parent, keep_source=keep_source)
    dialog = TransferProgressDialog(
        parent,
        verb=verb,
        item_count=len(items),
        source=os.path.basename(source_label.rstrip("\\/")) if source_label else "",
        destination=os.path.basename(destination.rstrip("\\/")) or destination,
    )
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    worker.totals.connect(dialog.set_totals)
    worker.progress.connect(dialog.set_progress)
    dialog.cancelRequested.connect(worker.cancel)

    loop = QEventLoop()
    worker.finished.connect(loop.quit)
    # Modal from the start (so nothing can be clicked mid-move) but invisible
    # for the first moment, so a quick move never flashes a dialog.
    dialog.setWindowOpacity(0.0)
    reveal_timer = QTimer()
    reveal_timer.setSingleShot(True)
    reveal_timer.timeout.connect(lambda: dialog.setWindowOpacity(1.0))
    try:
        dialog.show()
        reveal_timer.start(SHOW_DELAY_MS)
        worker.start()
        if not worker.isFinished():
            loop.exec()
        worker.wait()
    finally:
        reveal_timer.stop()
        dialog.hide()
        dialog.deleteLater()
    return worker.result
