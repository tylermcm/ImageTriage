"""Address-bar folder suggestions: the worker task and the controller that debounces and applies them."""
from __future__ import annotations

import os
import stat

from PySide6.QtCore import QEvent, QObject, QPoint, QRunnable, QSignalBlocker, Qt, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QFrame, QListWidget, QListWidgetItem, QVBoxLayout

from .. import path_policy
from ..scanner import normalize_filesystem_path


class _SuggestionSignals(QObject):
    ready = Signal(int, str, object)


class _SuggestionTask(QRunnable):
    """One folder listing for the address-bar suggestions, run off the GUI thread."""

    def __init__(self, token: int, text: str, is_stale, lister) -> None:
        super().__init__()
        self.token = token
        self._text = text
        self._is_stale = is_stale
        self._lister = lister
        self.signals = _SuggestionSignals()
        self.setAutoDelete(False)

    def run(self) -> None:
        suggestions = None  # None = stale, nothing to show; the owner still hears back so it can let go of us
        if not self._is_stale():  # if the user typed on, do not even list the share for this one
            try:
                suggestions = self._lister(self._text)
            except Exception:  # a share that errors out simply offers no suggestions
                suggestions = []
        self.signals.ready.emit(self.token, self._text, suggestions)


class _DirectorySuggestionController(QObject):
    """Segment-aware folder suggestions for the workspace address field."""

    MAX_VISIBLE_ROWS = 5

    def __init__(self, combo: QComboBox, *, on_accept_path=None) -> None:
        super().__init__(combo)
        self._combo = combo
        self._line_edit = combo.lineEdit()
        self._on_accept_path = on_accept_path
        self._popup = QFrame(combo.window(), Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self._popup.setObjectName("pathSuggestionPopup")
        self._popup.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self._popup.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        popup_layout = QVBoxLayout(self._popup)
        popup_layout.setContentsMargins(0, 0, 0, 0)
        popup_layout.setSpacing(0)
        self._list = QListWidget(self._popup)
        self._list.setObjectName("pathSuggestionList")
        self._list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._list.setUniformItemSizes(True)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._list.itemClicked.connect(self._accept_item)
        self._list.itemActivated.connect(self._accept_item)
        popup_layout.addWidget(self._list)
        self._last_query_text = ""
        # Folders on a network / removable drive are listed on this one worker, newest request wins.
        self._suggestion_token = 0
        self._suggestion_tasks: set[_SuggestionTask] = set()
        self._suggestion_pool = QThreadPool(self)
        self._suggestion_pool.setMaxThreadCount(1)

        if self._line_edit is not None:
            self._line_edit.textEdited.connect(self._handle_text_edited)
            self._line_edit.installEventFilter(self)
        self._combo.installEventFilter(self)
        self._popup.installEventFilter(self)
        self._list.installEventFilter(self)
        self._combo.activated.connect(lambda _index: self.hide_popup())

    @staticmethod
    def _strip_wrapping_quotes(text: str) -> str:
        return text.strip().strip('"').strip("'")

    @classmethod
    def _split_directory_query(cls, text: str) -> tuple[str, str]:
        raw_text = cls._strip_wrapping_quotes(text)
        if not raw_text:
            return "", ""
        normalized = normalize_filesystem_path(raw_text)
        if not normalized:
            return "", ""
        if raw_text.endswith(("\\", "/")):
            return normalized, ""
        parent_dir, fragment = os.path.split(normalized)
        return parent_dir, fragment

    @classmethod
    def _list_directory_suggestions(cls, text: str) -> list[tuple[str, str]]:
        parent_dir, fragment = cls._split_directory_query(text)
        if not parent_dir or not os.path.isdir(parent_dir):
            return []
        fragment_casefold = fragment.casefold()
        suggestions: list[tuple[str, str]] = []
        try:
            with os.scandir(parent_dir) as entries:
                for entry in entries:
                    try:
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                    except OSError:
                        continue
                    name = entry.name
                    if not name:
                        continue
                    if name.startswith("."):
                        continue
                    if cls._is_hidden_directory_entry(entry):
                        continue
                    if fragment_casefold and not name.casefold().startswith(fragment_casefold):
                        continue
                    suggestions.append((name, normalize_filesystem_path(entry.path)))
        except OSError:
            return []
        suggestions.sort(key=lambda item: item[0].casefold())
        return suggestions

    @staticmethod
    def _is_hidden_directory_entry(entry: os.DirEntry[str]) -> bool:
        try:
            attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
        except OSError:
            return False
        hidden_flag = int(getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0) or 0)
        if hidden_flag and attributes & hidden_flag:
            return True
        return False

    def hide_popup(self) -> None:
        if self._popup.isVisible():
            self._popup.hide()

    def _handle_text_edited(self, text: str) -> None:
        self._last_query_text = text
        self._show_suggestions_for_text(text)

    def _show_suggestions_for_text(self, text: str) -> None:
        if self._line_edit is None:
            return
        parent_dir, _fragment = self._split_directory_query(text)
        if parent_dir and not path_policy.is_plain_local(parent_dir):
            # Listing a folder on a share on every keystroke would stall the window (and a sleeping NAS
            # would freeze it), so it is done on a worker; only the newest request's answer is shown.
            self._request_suggestions_off_thread(text)
            return
        self._suggestion_token += 1  # a still-pending share listing must not overwrite this local answer
        self._show_suggestions(self._list_directory_suggestions(text))

    def _request_suggestions_off_thread(self, text: str) -> None:
        self._suggestion_token += 1
        token = self._suggestion_token
        self.hide_popup()
        task = _SuggestionTask(token, text, lambda: token != self._suggestion_token, self._list_directory_suggestions)
        task.signals.ready.connect(self._handle_suggestions_ready, Qt.ConnectionType.QueuedConnection)
        self._suggestion_tasks.add(task)
        self._suggestion_pool.start(task)

    def _handle_suggestions_ready(self, token: int, text: str, suggestions: object) -> None:
        self._suggestion_tasks = {task for task in self._suggestion_tasks if task.token != token}
        if token != self._suggestion_token or self._line_edit is None or self._line_edit.text() != text:
            return
        self._show_suggestions(list(suggestions) if isinstance(suggestions, list) else [])

    def _show_suggestions(self, suggestions: list[tuple[str, str]]) -> None:
        self._list.clear()
        if not suggestions:
            self.hide_popup()
            return
        for name, full_path in suggestions:
            item = QListWidgetItem(name, self._list)
            item.setData(Qt.ItemDataRole.UserRole, full_path)
            item.setToolTip(full_path)
        self._list.setCurrentRow(0)
        self._position_popup()
        self._popup.show()
        self._popup.raise_()

    def _position_popup(self) -> None:
        if self._line_edit is None:
            return
        row_height = self._list.sizeHintForRow(0)
        if row_height <= 0:
            row_height = 24
        visible_rows = min(self.MAX_VISIBLE_ROWS, max(1, self._list.count()))
        frame_width = max(self._combo.width(), self._line_edit.width())
        frame_height = (row_height * visible_rows) + 8
        global_pos = self._line_edit.mapToGlobal(QPoint(0, self._line_edit.height() + 4))
        self._popup.resize(frame_width, frame_height)
        self._popup.move(global_pos)

    def _accept_current_item(self) -> None:
        item = self._list.currentItem()
        if item is not None:
            self._accept_item(item)

    def _accept_item(self, item: QListWidgetItem) -> None:
        if self._line_edit is None:
            return
        full_path = str(item.data(Qt.ItemDataRole.UserRole) or "").strip()
        if not full_path:
            self.hide_popup()
            return
        completed = full_path
        if not completed.endswith(("\\", "/")):
            completed = f"{completed}{os.sep}"
        with QSignalBlocker(self._combo):
            self._combo.setEditText(completed)
        self._line_edit.setText(completed)
        self._line_edit.setCursorPosition(len(completed))
        self.hide_popup()
        if callable(self._on_accept_path):
            self._on_accept_path(full_path)

    def _move_selection(self, delta: int) -> None:
        count = self._list.count()
        if count <= 0:
            return
        current_row = self._list.currentRow()
        if current_row < 0:
            current_row = 0
        next_row = max(0, min(count - 1, current_row + delta))
        self._list.setCurrentRow(next_row)
        self._list.scrollToItem(self._list.currentItem(), QAbstractItemView.ScrollHint.PositionAtCenter)

    @staticmethod
    def _is_navigation_key(key: int) -> bool:
        return key in (
            Qt.Key.Key_Down,
            Qt.Key.Key_Up,
            Qt.Key.Key_Tab,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Escape,
        )

    def _handle_navigation_key(self, key: int) -> bool:
        if not self._popup.isVisible():
            return False
        if key == Qt.Key.Key_Down:
            self._move_selection(1)
            return True
        if key == Qt.Key.Key_Up:
            self._move_selection(-1)
            return True
        if key in (Qt.Key.Key_Tab, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._accept_current_item()
            return True
        if key == Qt.Key.Key_Escape:
            self.hide_popup()
            return True
        return False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched in (self._line_edit, self._combo):
            if event.type() == QEvent.Type.ShortcutOverride:
                key = event.key()
                if self._popup.isVisible() and self._is_navigation_key(key):
                    event.accept()
                    return True
            elif event.type() == QEvent.Type.KeyPress:
                key = event.key()
                if self._handle_navigation_key(key):
                    event.accept()
                    return True
        if watched is self._line_edit:
            if event.type() in (QEvent.Type.FocusOut, QEvent.Type.Hide):
                QTimer.singleShot(0, self._hide_popup_if_inactive)
            elif event.type() in (QEvent.Type.Move, QEvent.Type.Resize):
                if self._popup.isVisible():
                    self._position_popup()
        elif watched is self._combo:
            if event.type() in (QEvent.Type.Hide, QEvent.Type.Move, QEvent.Type.Resize):
                self.hide_popup()
            elif event.type() == QEvent.Type.MouseButtonPress:
                self.hide_popup()
        elif watched in (self._popup, self._list):
            if event.type() == QEvent.Type.Hide:
                self.hide_popup()
        return super().eventFilter(watched, event)

    def _hide_popup_if_inactive(self) -> None:
        if self._popup.underMouse() or self._list.underMouse():
            return
        if self._line_edit is not None and self._line_edit.hasFocus():
            return
        self.hide_popup()
