from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QSignalBlocker, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


@dataclass(slots=True)
class PaletteCommand:
    id: str
    title: str
    callback: callable
    subtitle: str = ""
    keywords: tuple[str, ...] = ()
    shortcut: str = ""
    section: str = ""


class CommandPaletteDialog(QWidget):
    finished = Signal(int)

    class DialogCode:
        Rejected = 0
        Accepted = 1

    def __init__(
        self,
        commands: list[PaletteCommand],
        *,
        recent_command_ids: tuple[str, ...] = (),
        title: str = "Command Palette",
        debug_hook=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("commandPaletteOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setWindowTitle(title)
        self._commands = commands
        self._recent_command_ids = recent_command_ids
        self._visible_commands: list[PaletteCommand] = []
        self._selected_command: PaletteCommand | None = None
        self._result_code = self.DialogCode.Rejected
        self._card_width = 720
        self._card_height = 520
        self._debug_hook = debug_hook

        if parent is not None:
            parent.installEventFilter(self)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(24, 28, 24, 24)
        root_layout.setSpacing(0)

        self.card = QFrame(self)
        self.card.setObjectName("commandPaletteCard")
        self.card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.card.setMinimumSize(540, 360)
        self.card.setMaximumWidth(760)
        root_layout.addWidget(self.card, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        root_layout.addStretch(1)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(10)

        self.search_field = QLineEdit(self)
        self.search_field.setPlaceholderText("Type a command, action, preset, or alias")
        self.search_field.textChanged.connect(self._refresh_results)
        self.search_field.installEventFilter(self)
        card_layout.addWidget(self.search_field)

        self.result_list = QListWidget(self)
        self.result_list.setObjectName("commandPaletteList")
        self.result_list.setUniformItemSizes(True)
        self.result_list.installEventFilter(self)
        self.result_list.viewport().installEventFilter(self)
        self.result_list.itemActivated.connect(self._accept_selected_item)
        card_layout.addWidget(self.result_list, 1)

        self.hint_label = QLabel("Enter runs the selected command. Up and down keys move through results.")
        self.hint_label.setObjectName("mutedText")
        card_layout.addWidget(self.hint_label)

        self._refresh_results("")
        self._focus_search_field()

    @property
    def selected_command(self) -> PaletteCommand | None:
        return self._selected_command

    def configure(
        self,
        commands: list[PaletteCommand],
        *,
        recent_command_ids: tuple[str, ...] = (),
        title: str = "Command Palette",
    ) -> None:
        self._commands = commands
        self._recent_command_ids = recent_command_ids
        self._selected_command = None
        self.setWindowTitle(title)
        search_blocker = QSignalBlocker(self.search_field)
        self.search_field.clear()
        del search_blocker
        self._refresh_results("")

    def present(self) -> None:
        self._result_code = self.DialogCode.Rejected
        self._selected_command = None
        self._sync_overlay_geometry()
        self._debug("present show")
        self.show()
        self.raise_()
        QTimer.singleShot(0, self._focus_search_field)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
            return
        if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            self.result_list.setFocus()
            self.result_list.keyPressEvent(event)
            event.accept()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._accept_selected_item(self.result_list.currentItem())
            event.accept()
            return
        super().keyPressEvent(event)

    def eventFilter(self, watched, event) -> bool:
        parent = self.parentWidget()
        if watched is parent and event.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show):
            self._sync_overlay_geometry()
            return False
        if self.isVisible() and event.type() == QEvent.Type.ShortcutOverride and self._is_command_palette_shortcut(event):
            event.accept()
            return True
        if watched is self.search_field and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                self.result_list.setFocus()
                self.result_list.keyPressEvent(event)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._accept_selected_item(self.result_list.currentItem())
                return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self.card.geometry().contains(event.position().toPoint()):
            self._debug("reject outside-card click")
            self.reject()
            event.accept()
            return
        super().mousePressEvent(event)

    def _refresh_results(self, text: str) -> None:
        query = _normalize_query(text)
        ranked = _rank_commands(self._commands, query, self._recent_command_ids)
        self._visible_commands = [command for _, command in ranked[:80]]

        self.result_list.setUpdatesEnabled(False)
        try:
            self.result_list.clear()
            if not self._visible_commands:
                empty = QListWidgetItem("No matching commands")
                empty.setFlags(Qt.ItemFlag.NoItemFlags)
                self.result_list.addItem(empty)
                return

            for command in self._visible_commands:
                item = QListWidgetItem(self.result_list)
                item.setSizeHint(command_palette_item_size_hint())
                item.setData(Qt.ItemDataRole.UserRole, command.id)
                self.result_list.addItem(item)
                self.result_list.setItemWidget(item, _CommandRow(command))

            self.result_list.setCurrentRow(0)
        finally:
            self.result_list.setUpdatesEnabled(True)

    def _accept_selected_item(self, item: QListWidgetItem | None) -> None:
        if item is None:
            return
        command_id = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(command_id, str):
            return
        for command in self._visible_commands:
            if command.id == command_id:
                self._selected_command = command
                self.accept()
                return

    def accept(self) -> None:
        self.done(self.DialogCode.Accepted)

    def reject(self) -> None:
        self.done(self.DialogCode.Rejected)

    def done(self, result: int) -> None:
        self._result_code = result
        self._debug(f"done result={result}")
        self.hide()
        self.finished.emit(result)

    def _focus_search_field(self) -> None:
        self.search_field.setFocus()
        self.search_field.selectAll()

    def _sync_overlay_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.setGeometry(parent.rect())
        width = min(760, max(540, parent.width() - 120))
        height = min(560, max(360, parent.height() - 160))
        self.card.setFixedSize(QSize(width, height))
        self._debug(f"sync-geometry overlay={self.width()}x{self.height()} card={width}x{height}")

    @staticmethod
    def _is_command_palette_shortcut(event) -> bool:
        return (
            isinstance(event, QKeyEvent)
            and event.key() == Qt.Key.Key_K
            and bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        )

    def _debug(self, message: str) -> None:
        if callable(self._debug_hook):
            self._debug_hook(f"dialog {message}")


class _CommandRow(QWidget):
    def __init__(self, command: PaletteCommand, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(10)

        text_container = QWidget(self)
        text_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        text_column = QVBoxLayout(text_container)
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(2)

        title_label = QLabel(command.title, text_container)
        title_label.setObjectName("commandPaletteTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        title_label.setIndent(0)
        title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        text_column.addWidget(title_label)

        subtitle_parts = [part for part in (command.section, command.subtitle) if part]
        subtitle_label = QLabel(" | ".join(subtitle_parts), text_container)
        subtitle_label.setObjectName("commandPaletteSubtitle")
        subtitle_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        subtitle_label.setIndent(0)
        subtitle_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        subtitle_label.setVisible(bool(subtitle_parts))
        text_column.addWidget(subtitle_label)

        layout.addWidget(text_container, 1)

        if command.shortcut:
            shortcut_label = QLabel(command.shortcut, self)
            shortcut_label.setObjectName("commandPaletteShortcut")
            shortcut_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(shortcut_label)


def command_palette_item_size_hint():
    from PySide6.QtCore import QSize

    return QSize(0, 62)


def _rank_commands(
    commands: list[PaletteCommand],
    query: str,
    recent_command_ids: tuple[str, ...],
) -> list[tuple[tuple[int, int, str], PaletteCommand]]:
    recent_positions = {command_id: index for index, command_id in enumerate(recent_command_ids)}
    ranked: list[tuple[tuple[int, int, str], PaletteCommand]] = []
    for command in commands:
        score = _command_match_score(command, query)
        if query and score <= 0:
            continue
        recent_position = recent_positions.get(command.id, 999)
        if not query:
            sort_key = (0 if command.id in recent_positions else 1, recent_position, command.title.casefold())
        else:
            sort_key = (-score, recent_position, command.title.casefold())
        ranked.append((sort_key, command))
    ranked.sort(key=lambda item: item[0])
    return ranked


def _command_match_score(command: PaletteCommand, query: str) -> int:
    if not query:
        return 1
    haystacks = [command.title, command.subtitle, command.section, *command.keywords]
    best = 0
    for haystack in haystacks:
        normalized = _normalize_query(haystack)
        if not normalized:
            continue
        if normalized == query:
            best = max(best, 900)
            continue
        if normalized.startswith(query):
            best = max(best, 780)
            continue
        word_parts = normalized.split()
        if any(part.startswith(query) for part in word_parts):
            best = max(best, 690)
            continue
        if query in normalized:
            best = max(best, 560 - min(200, normalized.index(query)))
            continue
        subsequence = _subsequence_score(query, normalized)
        if subsequence > 0:
            best = max(best, 340 + subsequence)
    return best


def _subsequence_score(query: str, haystack: str) -> int:
    if not query:
        return 0
    position = -1
    spread = 0
    for character in query:
        next_position = haystack.find(character, position + 1)
        if next_position < 0:
            return 0
        if position >= 0:
            spread += next_position - position - 1
        position = next_position
    return max(1, 90 - min(80, spread))


def _normalize_query(value: str) -> str:
    return " ".join((value or "").casefold().replace("&", " ").replace("...", "").split())
