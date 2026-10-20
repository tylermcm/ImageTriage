"""The Set Up AI dialog: a Core row that is always installed, then Culling and Editor tools as one switch each (open one to pick single models), and one
download size with one Install button. Fixed size; the model list scrolls instead of growing the dialog. Pure widget: the controller decides what to install.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import ai_setup_plan as plan

_GRADIENT = ("#ff9a5a", "#ff5e8a", "#b25cff", "#5a8cff")
CPU_WARNING = "This PC has no graphics card, so Culling is slow here: about half a minute or more per photo. A large folder can take hours."

OUTCOME_INSTALL = "install"
OUTCOME_REMOVE = "remove"
OUTCOME_CHECK = "check"


@dataclass(frozen=True)
class SetupDialogInput:
    gpu: bool
    variant: str
    core_installed: bool
    installed: frozenset[str]  # optional models whose files are already in place
    selection: frozenset[str]  # what starts ticked
    free_gb: float | None = None
    lede: str = ""


def _size_html(size: str, caption: str) -> str:
    return f"{size}<br><span style='font-size:11px; font-weight:400; color:gray'>{caption}</span>"


class Switch(QAbstractButton):
    """A small on/off switch. On is the aurora gradient, as in the status chip."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(44, 26)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def sizeHint(self) -> QSize:
        return QSize(44, 26)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        path = QPainterPath()
        path.addRoundedRect(track, track.height() / 2, track.height() / 2)
        if self.isChecked():
            gradient = QLinearGradient(track.topLeft(), track.topRight())
            for stop, color in zip((0.0, 0.35, 0.68, 1.0), _GRADIENT):
                gradient.setColorAt(stop, QColor(color))
            painter.fillPath(path, gradient)
        else:
            painter.fillPath(path, self.palette().mid().color().lighter(135))
        knob = 20.0
        x = track.right() - knob - 3 if self.isChecked() else track.left() + 3
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QRectF(x, track.top() + (track.height() - knob) / 2, knob, knob))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QColor(_GRADIENT[3]))
            painter.drawPath(path)


class _Pill(QFrame):
    """One optional group: switch, name, one-line description, total size, and a chevron that opens its single models."""

    def __init__(self, group: str, dialog: "AISetupDialog") -> None:
        super().__init__()
        self._group = group
        self._dialog = dialog
        self.setObjectName("aiSetupCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QWidget(self)
        row = QHBoxLayout(head)
        row.setContentsMargins(16, 12, 14, 12)
        row.setSpacing(14)
        self.switch = Switch(head)
        self.switch.setObjectName(f"aiSetupSwitch_{group}")
        self.switch.setAccessibleName(plan.GROUPS[group][0])
        row.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(1)
        title = QLabel(plan.GROUPS[group][0], head)
        title.setObjectName("aiSetupCardTitle")
        self.note = QLabel(plan.GROUPS[group][1], head)
        self.note.setObjectName("aiSetupMuted")
        text.addWidget(title)
        text.addWidget(self.note)
        row.addLayout(text, 1)
        self.size = QLabel(head)
        self.size.setObjectName("aiSetupSize")
        self.size.setTextFormat(Qt.TextFormat.RichText)
        self.size.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.size, 0)
        self.chevron = QToolButton(head)
        self.chevron.setObjectName(f"aiSetupChevron_{group}")
        self.chevron.setAutoRaise(True)
        self.chevron.setArrowType(Qt.ArrowType.DownArrow)
        self.chevron.setToolTip("Choose models")
        self.chevron.setAccessibleName(f"Choose {plan.GROUPS[group][0]} models")
        self.chevron.setCheckable(True)
        row.addWidget(self.chevron, 0, Qt.AlignmentFlag.AlignVCenter)
        outer.addWidget(head)

        self.warning = QLabel(CPU_WARNING, self)
        self.warning.setObjectName("aiSetupWarning")
        self.warning.setWordWrap(True)
        self.warning.hide()
        outer.addWidget(self.warning)

        self.kids = QFrame(self)
        self.kids.setObjectName("aiSetupKids")
        kids_layout = QVBoxLayout(self.kids)
        kids_layout.setContentsMargins(16, 6, 16, 8)
        kids_layout.setSpacing(2)
        self.boxes: dict[str, QCheckBox] = {}
        self.kid_sizes: dict[str, QLabel] = {}
        for item in plan.models_in(group):
            line = QHBoxLayout()
            box = QCheckBox(item.label, self.kids)
            box.setObjectName(f"aiSetupModel_{item.key}")
            size = QLabel(self.kids)
            size.setObjectName("aiSetupMuted")
            line.addWidget(box, 1)
            line.addWidget(size, 0)
            kids_layout.addLayout(line)
            self.boxes[item.key] = box
            self.kid_sizes[item.key] = size
            box.toggled.connect(lambda _checked, key=item.key: dialog._model_toggled(group, key))
        self.kids.hide()
        outer.addWidget(self.kids)

        self.switch.clicked.connect(lambda checked: dialog._group_toggled(group, checked))
        self.chevron.toggled.connect(self._set_open)

    def _set_open(self, opened: bool) -> None:
        self.kids.setVisible(opened)
        self.chevron.setArrowType(Qt.ArrowType.UpArrow if opened else Qt.ArrowType.DownArrow)
        self._dialog._chevron_opened(self._group, opened)

    def is_open(self) -> bool:
        return self.chevron.isChecked()


class AISetupDialog(QDialog):
    def __init__(
        self,
        data: SetupDialogInput,
        parent: QWidget | None = None,
        *,
        on_check: Callable[[], None] | None = None,
        on_repair: Callable[[], None] | None = None,
        on_remove_everything: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._data = data
        self._selection = set(data.selection)
        self._touched_cull = False
        self._after_close: Callable[[], None] | None = None
        self.outcome: str | None = None
        self.setObjectName("aiSetupDialog")
        self.setWindowTitle("Set Up AI")
        self.setModal(True)
        self.setFixedSize(820, 420)
        self.setStyleSheet(
            """
            QDialog#aiSetupDialog { background: palette(window); }
            QFrame#aiSetupCard { background: palette(base); border: 1px solid palette(midlight); border-radius: 12px; }
            QFrame#aiSetupKids { background: palette(alternate-base); border-top: 1px solid palette(midlight); border-bottom-left-radius: 12px; border-bottom-right-radius: 12px; }
            QFrame#aiSetupSide { background: palette(alternate-base); border-left: 1px solid palette(midlight); }
            QLabel#aiSetupTitle { font-size: 21px; font-weight: 700; }
            QLabel#aiSetupCardTitle { font-size: 14px; font-weight: 650; }
            QLabel#aiSetupMuted { color: palette(mid); }
            QLabel#aiSetupSize { font-size: 14px; font-weight: 600; }
            QLabel#aiSetupBig { font-size: 30px; font-weight: 700; }
            QLabel#aiSetupWarning { margin: 0 14px 10px 14px; padding: 8px 10px; border-radius: 8px; background: rgba(194,124,14,0.12); color: #b8730d; }
            QToolButton { border: 1px solid palette(midlight); border-radius: 7px; padding: 3px; }
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        aurora = QFrame(self)
        aurora.setFixedHeight(3)
        aurora.setStyleSheet(
            "background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 %s, stop:0.35 %s, stop:0.68 %s, stop:1 %s);" % _GRADIENT
        )
        root.addWidget(aurora)
        head = QWidget(self)
        head_layout = QVBoxLayout(head)
        head_layout.setContentsMargins(24, 16, 24, 6)
        head_layout.setSpacing(2)
        title = QLabel("Set up AI", head)
        title.setObjectName("aiSetupTitle")
        self.lede = QLabel(data.lede or ("One download. Your graphics card makes it fast." if data.gpu else "One download, and you can add more later."), head)
        self.lede.setObjectName("aiSetupMuted")
        head_layout.addWidget(title)
        head_layout.addWidget(self.lede)
        root.addWidget(head)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        scroll = QScrollArea(self)
        scroll.setObjectName("aiSetupScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        holder.setObjectName("aiSetupHolder")
        holder.setStyleSheet("QWidget#aiSetupHolder { background: transparent; }")
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        column = QVBoxLayout(holder)
        column.setContentsMargins(24, 8, 18, 12)
        column.setSpacing(10)
        column.addWidget(self._build_core_card())
        self._pills: dict[str, _Pill] = {}
        for group in plan.GROUPS:
            pill = _Pill(group, self)
            self._pills[group] = pill
            column.addWidget(pill)
        column.addStretch(1)
        scroll.setWidget(holder)
        body.addWidget(scroll, 1)

        side = QFrame(self)
        side.setObjectName("aiSetupSide")
        side.setFixedWidth(250)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(22, 18, 22, 18)
        side_layout.setSpacing(10)
        side_layout.addStretch(1)
        caption = QLabel("Download", side)
        caption.setObjectName("aiSetupMuted")
        self.total = QLabel(side)
        self.total.setObjectName("aiSetupBig")
        self.eta = QLabel(side)
        self.eta.setObjectName("aiSetupMuted")
        side_layout.addWidget(caption)
        side_layout.addWidget(self.total)
        side_layout.addWidget(self.eta)
        self.install = QPushButton("Install", side)
        self.install.setObjectName("editorPrimaryButton")
        self.install.setDefault(True)
        self.later = QPushButton("Not now", side)
        self.later.setObjectName("aiSetupLater")
        side_layout.addWidget(self.install)
        side_layout.addWidget(self.later)
        self.more = QToolButton(side)
        self.more.setObjectName("aiSetupMore")
        self.more.setText("More")
        self.more.setArrowType(Qt.ArrowType.NoArrow)
        self.more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more.setStyleSheet("QToolButton { border: none; color: palette(mid); text-align: left; } QToolButton::menu-indicator { image: none; }")
        self.more.setText("More  ▾")
        self._menu = QMenu(self.more)
        self._menu_info = self._menu.addAction("")
        self._menu_info.setEnabled(False)
        self._menu.addSeparator()
        self._actions: list[tuple[str, Callable[[], None] | None]] = [
            ("Check installation", on_check),
            ("Repair", on_repair),
            ("Remove everything", on_remove_everything),
        ]
        for label, callback in self._actions:
            action = self._menu.addAction(label)
            action.setEnabled(callback is not None)
            action.triggered.connect(lambda _checked=False, cb=callback: self._run_after_close(cb))
        self.more.setMenu(self._menu)
        side_layout.addWidget(self.more, 0, Qt.AlignmentFlag.AlignLeft)
        side_layout.addStretch(1)
        body.addWidget(side, 0)
        root.addLayout(body, 1)

        self.install.clicked.connect(self._install_clicked)
        self.later.clicked.connect(self.reject)
        for group, pill in self._pills.items():
            for key, box in pill.boxes.items():
                box.blockSignals(True)
                box.setChecked(key in self._selection)
                box.blockSignals(False)
        self._refresh()

    # ------------------------------------------------------------------ pieces

    def _build_core_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("aiSetupCard")
        row = QHBoxLayout(card)
        row.setContentsMargins(16, 12, 16, 12)
        row.setSpacing(14)
        tick = QLabel("✓", card)
        tick.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tick.setFixedSize(26, 26)
        tick.setStyleSheet("background: #1f9d63; color: white; border-radius: 13px; font-weight: 700;")
        row.addWidget(tick, 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setSpacing(1)
        title = QLabel("Core", card)
        title.setObjectName("aiSetupCardTitle")
        note = QLabel("The AI engine, search, and faces and People", card)
        note.setObjectName("aiSetupMuted")
        text.addWidget(title)
        text.addWidget(note)
        row.addLayout(text, 1)
        self.core_size = QLabel(card)
        self.core_size.setObjectName("aiSetupSize")
        self.core_size.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.core_size, 0)
        return card

    # ------------------------------------------------------------------ state

    def selection(self) -> frozenset[str]:
        return frozenset(self._selection)

    def to_add(self) -> frozenset[str]:
        return frozenset(self._selection - self._data.installed)

    def to_remove(self) -> frozenset[str]:
        return frozenset(self._data.installed - self._selection)

    def download_mb(self) -> int:
        return plan.download_mb(self._selection, variant=self._data.variant, core_installed=self._data.core_installed, installed=self._data.installed)

    def _group_toggled(self, group: str, checked: bool) -> None:
        for item in plan.models_in(group):
            (self._selection.add if checked else self._selection.discard)(item.key)
        if group == plan.GROUP_CULL:
            self._touched_cull = True
        self._sync_boxes(group)
        self._refresh()

    def _model_toggled(self, group: str, key: str) -> None:
        box = self._pills[group].boxes[key]
        (self._selection.add if box.isChecked() else self._selection.discard)(key)
        if group == plan.GROUP_CULL:
            self._touched_cull = True
        self._refresh()

    def _chevron_opened(self, group: str, opened: bool) -> None:
        if group == plan.GROUP_CULL and opened:
            self._touched_cull = True
        self._refresh()

    def _sync_boxes(self, group: str) -> None:
        for key, box in self._pills[group].boxes.items():
            box.blockSignals(True)
            box.setChecked(key in self._selection)
            box.blockSignals(False)

    def _refresh(self) -> None:
        data = self._data
        self.core_size.setText("installed" if data.core_installed else plan.format_mb(plan.core_mb(data.variant)))
        for group, pill in self._pills.items():
            items = plan.models_in(group)
            chosen = [m for m in items if m.key in self._selection]
            full = len(chosen) == len(items)
            installed_all = all(m.key in data.installed for m in items)
            pill.switch.setChecked(bool(chosen))
            pill.note.setText(
                plan.GROUPS[group][1] + (f" · {len(chosen)} of {len(items)} models" if chosen and not full else "")
            )
            if installed_all and full:
                pill.size.setText(_size_html(plan.format_mb(plan.group_mb(group)), "installed"))
            else:
                shown = plan.group_mb(group) if (full or not chosen) else sum(plan.model_mb(m.key) for m in chosen)
                pill.size.setText(_size_html(plan.format_mb(shown), "download"))
            for item in items:
                installed = item.key in data.installed
                pill.kid_sizes[item.key].setText("installed" if installed else plan.format_mb(plan.model_mb(item.key)))
            show_warning = group == plan.GROUP_CULL and not data.gpu and self._touched_cull and any(m.key in self._selection for m in items)
            pill.warning.setVisible(show_warning)
        mb = self.download_mb()
        remove = self.to_remove()
        self.total.setText(plan.format_mb(mb) if mb else ("Nothing to download" if remove else "Up to date"))
        self.total.setStyleSheet("font-size: 30px;" if mb else "font-size: 18px;")
        self.eta.setText(f"about {max(1, round(mb / 60 / 12))} min" if mb else "")
        if mb:
            self.install.setText("Install")
        elif remove:
            self.install.setText(f"Remove {len(remove)} model" + ("s" if len(remove) > 1 else ""))
        else:
            self.install.setText("Check installation")
        free = f" Free on this drive: {self._data.free_gb:.0f} GB." if self._data.free_gb is not None else ""
        self._menu_info.setText((f"Needs about {plan.format_mb(mb * 1.3)} of disk space." if mb else "Everything selected is installed.") + free)

    # ------------------------------------------------------------------ actions

    def _install_clicked(self) -> None:
        if self.download_mb():
            self.outcome = OUTCOME_INSTALL
        elif self.to_remove():
            self.outcome = OUTCOME_REMOVE
        else:
            self.outcome = OUTCOME_CHECK
        self.accept()

    def _run_after_close(self, callback: Callable[[], None] | None) -> None:
        self._after_close = callback
        self.outcome = None
        self.reject()

    def pending_menu_action(self) -> Callable[[], None] | None:
        return self._after_close
