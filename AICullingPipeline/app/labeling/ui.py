"""PySide6 desktop UI for the Image Triage Speed Cull surface."""

from __future__ import annotations

import os
import json
from datetime import datetime
from pathlib import Path
import sys
import time
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QSettings, Qt, QSize, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedLayout,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from app.config import LabelingConfig
from app.host_sync import HostSyncController
from app.labeling.models import ClusterItem, ImageItem
from app.labeling.previews import load_oriented_pixmap
from app.labeling.session import LabelingSession
from app.labeling.theme import apply_labeling_theme


_READY_FILE_ENV = "IMAGE_TRIAGE_LABELING_READY_FILE"
_PERF_LOG_ENV = "IMAGE_TRIAGE_PERFORMANCE_LOG_PATH"


def launch_labeling_app(
    config: LabelingConfig,
    *,
    session: Optional[LabelingSession] = None,
) -> int:
    """Launch the PySide6 labeling application."""

    start = time.perf_counter()
    _notify_host_startup("starting", "Starting label collection process")
    app = QApplication.instance() or QApplication(sys.argv)
    _notify_host_startup("qt_ready", "Qt application created", started_at=start)
    apply_labeling_theme(app)
    _notify_host_startup("theme_ready", "Theme loaded", started_at=start)
    if session is None:
        session = LabelingSession(config, progress_callback=_emit_phash_progress)
    window = LabelingMainWindow(config, session=session)
    _notify_host_startup("window_created", "Label collection window built", started_at=start)
    window._host_sync_controller = HostSyncController(
        on_appearance_mode_changed=lambda _mode: apply_labeling_theme(app),
        on_shutdown_requested=window.request_parent_shutdown,
        parent=window,
    )
    _notify_host_startup("sync_ready", "Host sync connected", started_at=start)
    window.show()
    app.processEvents()
    _notify_host_startup("shown", "Label collection window shown", started_at=start)
    _notify_host_ready()
    return app.exec()


def _notify_host_ready() -> None:
    ready_file = os.environ.get(_READY_FILE_ENV, "").strip()
    if not ready_file:
        return
    ready_path = Path(ready_file).expanduser()
    try:
        ready_path.parent.mkdir(parents=True, exist_ok=True)
        ready_path.write_text('{"state":"ready"}', encoding="utf-8")
    except OSError:
        pass


def _emit_phash_progress(done: int, total: int, _state: str) -> None:
    """Stream pHash compute progress to the host via the ready-file."""

    if total <= 0:
        return
    percent = int(round(100.0 * done / total))
    _notify_host_startup(
        "computing_phashes",
        f"Computing image fingerprints ({done}/{total}, {percent}%)",
    )


def _notify_host_startup(state: str, message: str, *, started_at: float | None = None) -> None:
    duration_ms = (time.perf_counter() - started_at) * 1000.0 if started_at is not None else None
    _child_perf_log(
        "labeling.child.startup_state",
        duration_ms=duration_ms,
        child_state=state,
        message=message,
    )
    ready_file = os.environ.get(_READY_FILE_ENV, "").strip()
    if not ready_file:
        return
    ready_path = Path(ready_file).expanduser()
    payload: dict[str, object] = {
        "state": state,
        "message": message,
    }
    if duration_ms is not None:
        payload["elapsed_ms"] = round(duration_ms, 3)
    try:
        ready_path.parent.mkdir(parents=True, exist_ok=True)
        ready_path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    except OSError:
        pass


def _child_perf_log(event: str, *, duration_ms: float | None = None, **fields: object) -> None:
    log_path = os.environ.get(_PERF_LOG_ENV, "").strip()
    if not log_path:
        return
    payload: dict[str, object] = {
        "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "pid": os.getpid(),
        "thread": "labeling-ui",
        "event": event,
        "source": "labeling_child",
    }
    if duration_ms is not None:
        payload["duration_ms"] = round(float(duration_ms), 3)
    for key, value in fields.items():
        payload[key] = _safe_child_perf_value(value)
    try:
        with Path(log_path).open("a", encoding="utf-8", buffering=1) as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass


def _safe_child_perf_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and len(value) > 500:
            return value[:497] + "..."
        return value
    if isinstance(value, Path):
        return str(value)
    text = repr(value)
    return text[:497] + "..." if len(text) > 500 else text


def _build_chrome_toggle(
    *,
    settings: QSettings,
    settings_key: str,
    panel: QWidget,
    on_toggled: Optional[Callable[[bool], None]] = None,
) -> QPushButton:
    """Build a flat full-width toggle that hides or shows a chrome panel.

    State is persisted in QSettings so the user's choice survives restarts.
    The returned button itself stays visible at all times so the user always
    has a way to bring the panel back. The optional on_toggled callback fires
    after each click so callers can recompute child geometry (the parent tab
    does not get a resizeEvent when only its contents reflow).
    """

    initial_visible = bool(settings.value(settings_key, True, type=bool))
    panel.setVisible(initial_visible)

    button = QPushButton(_chrome_toggle_text(initial_visible))
    button.setObjectName("chromeToggleButton")
    button.setFlat(True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _on_click() -> None:
        new_visible = not panel.isVisible()
        panel.setVisible(new_visible)
        button.setText(_chrome_toggle_text(new_visible))
        settings.setValue(settings_key, new_visible)
        if on_toggled is not None:
            on_toggled(new_visible)

    button.clicked.connect(_on_click)
    return button


def _chrome_toggle_text(visible: bool) -> str:
    return "▲ Hide controls" if visible else "▼ Show controls"


def _group_initial_assignment(
    members: List[ImageItem],
    saved_assignments: Dict[str, str],
) -> str:
    """Pick a card's starting assignment from per-image saved labels.

    If every member shares the same label, use it. Mixed labels fall back to
    'unlabeled' so the user is prompted to relabel after expanding the stack.
    """

    if not members:
        return "unlabeled"
    seen: set[str] = set()
    for member in members:
        seen.add(saved_assignments.get(member.image_id, "unlabeled"))
        if len(seen) > 1:
            return "unlabeled"
    return next(iter(seen)) if seen else "unlabeled"


def _has_mixed_saved_assignments(
    members: List[ImageItem],
    saved_assignments: Dict[str, str],
) -> bool:
    """Return True when members have different saved labels (warrants auto-expand)."""

    labels = {saved_assignments.get(member.image_id, "unlabeled") for member in members}
    return len(labels) > 1


class ClusterImageCard(QFrame):
    """Small card used for cluster-level labeling.

    May represent a single image or a near-duplicate stack of images that
    share one label. When stacked, the first member is the visible
    representative and the user can click Expand to break the stack apart.
    """

    def __init__(
        self,
        members: List[ImageItem],
        *,
        display_index: int,
        preview_height: int,
        sync_controller: PairZoomSyncController,
        on_changed: Callable[["ClusterImageCard"], None],
        on_selected: Callable[["ClusterImageCard"], None],
        on_expand_requested: Optional[Callable[["ClusterImageCard"], None]] = None,
    ) -> None:
        super().__init__()
        if not members:
            raise ValueError("ClusterImageCard requires at least one member image.")
        self.members: List[ImageItem] = list(members)
        self.image = self.members[0]
        self.display_index = display_index
        self.on_changed = on_changed
        self.on_selected = on_selected
        self.on_expand_requested = on_expand_requested
        self.setObjectName("clusterCard")
        self.setProperty("active", False)
        self.setProperty("stacked", len(self.members) > 1)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setLineWidth(2)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.preview_height = preview_height
        self.preview_view = ZoomableImageView(
            sync_controller,
            minimum_size=QSize(160, 160),
            on_selected=lambda: self.on_selected(self),
        )
        self.preview_view.setProperty("image_id", self.image.image_id)
        self.preview_view.setProperty("file_name", self.image.file_name)
        self.preview_view.setProperty("cluster_id", self.image.cluster_id)
        self.preview_view.setMinimumWidth(0)
        self.preview_view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.preview_view.setFixedHeight(preview_height)
        self.preview_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.preview_message = QLabel("No image loaded.")
        self.preview_message.setObjectName("previewMessageLabel")
        self.preview_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_message.setMinimumSize(QSize(0, preview_height))
        self.preview_message.setFixedHeight(preview_height)
        self.preview_message.setWordWrap(True)
        self.preview_message.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.preview_container = QWidget()
        self.preview_container.setMinimumWidth(0)
        self.preview_container.setFixedHeight(preview_height)
        self.preview_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.preview_stack = QStackedLayout(self.preview_container)
        self.preview_stack.setContentsMargins(0, 0, 0, 0)
        self.preview_stack.addWidget(self.preview_message)
        self.preview_stack.addWidget(self.preview_view)
        layout.addWidget(self.preview_container)

        representative = self.image
        if not representative.file_exists:
            self.preview_message.setText("Missing image file.")
            self.preview_stack.setCurrentWidget(self.preview_message)
        else:
            preview_start = time.perf_counter()
            preview_max_side = max(768, min(1600, preview_height * 4))
            pixmap = load_oriented_pixmap(representative.file_path, max_side=preview_max_side)
            preview_ms = (time.perf_counter() - preview_start) * 1000.0
            _child_perf_log(
                "labeling.child.cluster_card.preview",
                duration_ms=preview_ms,
                image_id=representative.image_id,
                file_name=representative.file_name,
                file_path=representative.file_path,
                cluster_id=representative.cluster_id,
                cluster_size=representative.cluster_size,
                stack_size=len(self.members),
                max_side=preview_max_side,
                pixmap_null=pixmap.isNull(),
            )
            if preview_ms >= 250.0:
                _notify_host_startup(
                    "cluster_preview_slow",
                    f"Loaded cluster preview in {preview_ms:.0f} ms: {representative.file_name}",
                )
            if pixmap.isNull():
                self.preview_message.setText("Unable to render preview.")
                self.preview_stack.setCurrentWidget(self.preview_message)
            else:
                self.preview_view.set_preview_pixmap(pixmap)
                self.preview_stack.setCurrentWidget(self.preview_view)

        stack_size = len(self.members)
        if stack_size > 1:
            stack_row = QHBoxLayout()
            stack_row.setSpacing(8)
            badge = QLabel(f"Stack of {stack_size}")
            badge.setObjectName("clusterStackBadge")
            badge.setToolTip(
                "These frames look near-identical. Your label below will apply to all of them."
            )
            stack_row.addWidget(badge)
            expand_button = QPushButton(f"Expand ({stack_size})")
            expand_button.setObjectName("clusterStackExpandButton")
            expand_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            expand_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            expand_button.clicked.connect(self._on_expand_clicked)
            stack_row.addWidget(expand_button)
            stack_row.addStretch(1)
            layout.addLayout(stack_row)

        if stack_size > 1:
            info_text = (
                f"<b>{display_index}. {representative.file_name}</b> "
                f"<span style='color:#888;'>+{stack_size - 1} similar</span><br>"
                f"{representative.relative_path}<br>"
                f"timestamp: {representative.capture_timestamp or 'missing'}"
            )
        else:
            info_text = (
                f"<b>{display_index}. {representative.file_name}</b><br>"
                f"{representative.relative_path}<br>"
                f"timestamp: {representative.capture_timestamp or 'missing'}"
            )
        info = QLabel(info_text)
        info.setObjectName("clusterInfoLabel")
        info.setWordWrap(True)
        info.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        tooltip_lines = [str(member.file_path) for member in self.members]
        info.setToolTip("\n".join(tooltip_lines))
        layout.addWidget(info)

        self._assignment = "unlabeled"
        assignment_layout = QHBoxLayout()
        assignment_layout.setSpacing(6)
        self.assignment_buttons: Dict[str, QPushButton] = {}
        for label, value in (
            ("Accept", "accept"),
            ("Reject", "reject"),
        ):
            button = QPushButton(label)
            button.setObjectName("labelAssignmentButton")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setCheckable(True)
            button.setProperty("assignmentRole", value)
            button.setProperty("assignmentSelected", False)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _checked=False, chosen=value: self._choose_assignment(chosen))
            assignment_layout.addWidget(button)
            self.assignment_buttons[value] = button
        layout.addLayout(assignment_layout)

        self.layout().activate()
        self._card_overhead = max(0, self.sizeHint().height() - preview_height)
        self.set_preview_height(preview_height)

    def assignment(self) -> str:
        """Return the current label assignment for this card."""

        return self._assignment

    def member_image_ids(self) -> List[str]:
        """Return image_ids covered by this card (one or many for a stack)."""

        return [member.image_id for member in self.members]

    def _on_expand_clicked(self) -> None:
        """Forward an expand request to the cluster tab if a handler is wired."""

        if self.on_expand_requested is not None:
            self.on_expand_requested(self)

    def set_assignment(self, assignment: str, *, notify: bool = False) -> None:
        """Set the current assignment without changing the image."""

        if assignment not in {"unlabeled", "accept", "reject"}:
            assignment = "unlabeled"
        if self._assignment == assignment:
            self._refresh_assignment_buttons()
            return

        self._assignment = assignment
        self._refresh_assignment_buttons()
        if notify:
            self._emit_changed()

    def set_active(self, active: bool) -> None:
        """Highlight the card that keyboard labeling currently targets."""

        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_preview_height(self, preview_height: int) -> None:
        """Resize the preview region while keeping metadata and controls visible."""

        self.preview_height = preview_height
        self.preview_view.setFixedHeight(preview_height)
        self.preview_message.setFixedHeight(preview_height)
        self.preview_container.setFixedHeight(preview_height)
        self.setFixedHeight(self._card_overhead + preview_height)

    def _emit_changed(self) -> None:
        """Notify the parent tab that this card changed."""

        self.on_changed(self)

    def _choose_assignment(self, assignment: str) -> None:
        """Apply an assignment from a direct card action."""

        self.on_selected(self)
        self.set_assignment(assignment, notify=True)

    def _refresh_assignment_buttons(self) -> None:
        """Refresh button checked states and dynamic styling."""

        for value, button in self.assignment_buttons.items():
            selected = self._assignment == value
            button.blockSignals(True)
            button.setChecked(selected)
            button.blockSignals(False)
            button.setProperty("assignmentSelected", selected)
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        """Let users click a card to make it the active keyboard target."""

        self.on_selected(self)
        super().mousePressEvent(event)


class ClusterTab(QWidget):
    """Tab for cluster-level culling annotations."""

    def __init__(
        self,
        session: LabelingSession,
        config: LabelingConfig,
        on_progress_changed: Callable[[], None],
    ) -> None:
        super().__init__()
        self.session = session
        self.config = config
        self.on_progress_changed = on_progress_changed
        self.cards: List[ClusterImageCard] = []
        self.dirty = False
        self.clusters = self.session.cluster_items()
        self.current_index = self.session.next_unlabeled_cluster_index(0)
        self.active_card_index = 0
        self.zoom_controller = PairZoomSyncController()
        self._expanded_cluster_ids: set[str] = set()
        self._ui_settings = QSettings("ImageTriage", "LabelingApp")
        self._current_card_row_count = 1
        self.setObjectName("clusterTab")

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        control_panel = QFrame()
        control_panel.setObjectName("labelingControlPanel")
        control_layout = QVBoxLayout(control_panel)
        control_layout.setContentsMargins(14, 14, 14, 14)
        control_layout.setSpacing(10)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        self.prev_button = QPushButton("Previous")
        self.prev_button.setObjectName("secondaryActionButton")
        self.prev_button.clicked.connect(lambda: self._navigate(self.current_index - 1))
        controls.addWidget(self.prev_button)

        self.cluster_selector = QComboBox()
        self.cluster_selector.currentIndexChanged.connect(self._on_selector_changed)
        controls.addWidget(self.cluster_selector, 1)

        self.next_button = QPushButton("Next")
        self.next_button.setObjectName("secondaryActionButton")
        self.next_button.clicked.connect(lambda: self._navigate(self.current_index + 1))
        controls.addWidget(self.next_button)

        self.next_unlabeled_button = QPushButton("Next Unlabeled")
        self.next_unlabeled_button.setObjectName("secondaryActionButton")
        self.next_unlabeled_button.clicked.connect(self._navigate_next_unlabeled)
        controls.addWidget(self.next_unlabeled_button)

        self.save_button = QPushButton("Save")
        self.save_button.setObjectName("primaryActionButton")
        self.save_button.clicked.connect(self.save_current_cluster)
        controls.addWidget(self.save_button)

        self.save_next_button = QPushButton("Save and Next")
        self.save_next_button.setObjectName("primaryActionButton")
        self.save_next_button.clicked.connect(self._save_and_next)
        controls.addWidget(self.save_next_button)
        control_layout.addLayout(controls)

        batch_actions = QHBoxLayout()
        batch_actions.setSpacing(10)
        self.clear_button = QPushButton("Clear All")
        self.clear_button.setObjectName("secondaryActionButton")
        self.clear_button.clicked.connect(self._clear_all_assignments)
        batch_actions.addWidget(self.clear_button)

        self.reject_all_button = QPushButton("Reject All")
        self.reject_all_button.setObjectName("secondaryActionButton")
        self.reject_all_button.clicked.connect(self._reject_all_assignments)
        batch_actions.addWidget(self.reject_all_button)

        self.auto_card_advance_checkbox = QCheckBox("Auto-select next image")
        self.auto_card_advance_checkbox.setObjectName("labelFlowToggle")
        self.auto_card_advance_checkbox.setChecked(self.config.cluster_auto_advance_cards)
        batch_actions.addWidget(self.auto_card_advance_checkbox)

        self.auto_cluster_advance_checkbox = QCheckBox("Auto-advance cluster")
        self.auto_cluster_advance_checkbox.setObjectName("labelFlowToggle")
        self.auto_cluster_advance_checkbox.setChecked(self.config.cluster_auto_advance_clusters)
        batch_actions.addWidget(self.auto_cluster_advance_checkbox)

        batch_actions.addStretch(1)

        self.progress_label = QLabel("")
        self.progress_label.setObjectName("summaryBadge")
        batch_actions.addWidget(self.progress_label)
        control_layout.addLayout(batch_actions)

        self.cluster_meta_label = QLabel("")
        self.cluster_meta_label.setObjectName("clusterMetaLabel")
        self.cluster_meta_label.setWordWrap(True)
        control_layout.addWidget(self.cluster_meta_label)

        self.cluster_rule_label = QLabel("")
        self.cluster_rule_label.setObjectName("clusterRuleBadge")
        self.cluster_rule_label.setProperty("status", "warning")
        control_layout.addWidget(self.cluster_rule_label)

        instructions_visible = bool(
            self._ui_settings.value("cluster_tab/instructions_visible", True, type=bool)
        )
        self.instructions_toggle = QPushButton(
            self._instructions_toggle_text(instructions_visible)
        )
        self.instructions_toggle.setObjectName("instructionsToggleButton")
        self.instructions_toggle.setFlat(True)
        self.instructions_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.instructions_toggle.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.instructions_toggle.clicked.connect(self._toggle_cluster_instructions)
        control_layout.addWidget(self.instructions_toggle, alignment=Qt.AlignmentFlag.AlignLeft)

        zoom_help_text = (
            "Mouse-wheel zooms and drag pans, synced across every preview in the cluster. "
            "Press Enter to save; N jumps to the next unlabeled cluster."
        )
        self.cluster_guidance_label = QLabel(
            "<b>For each card, decide:</b> "
            "<b>Accept</b> (keep) &nbsp;·&nbsp; "
            "<b>Reject</b> (delete). "
            "Skip cards you're not ready to decide on. Do another pass later to narrow down."
            "<br>"
            "<b>&quot;Stack of N&quot;</b> = N near-identical frames. "
            "<b>Reject</b> applies to all of them. "
            "<b>Accept</b> keeps only the visible top frame. "
            "Hit <b>Expand</b> to decide each one separately."
            "<br>"
            "<b>To save:</b> mark at least one card. Untouched cards keep whatever state they had before."
            "<br>"
            "<span style='color:gray;'>Shortcuts: click or 1-9 to pick a card · "
            "W=Accept · X=Reject · Enter saves · mouse-wheel zoom is synced across the group.</span>"
        )
        self.cluster_guidance_label.setObjectName("clusterInstructions")
        self.cluster_guidance_label.setWordWrap(True)
        self.cluster_guidance_label.setTextFormat(Qt.TextFormat.RichText)
        self.cluster_guidance_label.setVisible(instructions_visible)
        control_layout.addWidget(self.cluster_guidance_label)

        zoom_controls = QHBoxLayout()
        zoom_controls.setSpacing(10)
        zoom_controls.addStretch(1)
        self.cluster_zoom_out_button = QPushButton("Zoom Out")
        self.cluster_zoom_out_button.setObjectName("secondaryActionButton")
        self.cluster_zoom_out_button.setToolTip(zoom_help_text)
        self.cluster_zoom_out_button.clicked.connect(self.zoom_controller.zoom_out)
        zoom_controls.addWidget(self.cluster_zoom_out_button)

        self.cluster_reset_zoom_button = QPushButton("Reset Zoom")
        self.cluster_reset_zoom_button.setObjectName("secondaryActionButton")
        self.cluster_reset_zoom_button.setToolTip(zoom_help_text)
        self.cluster_reset_zoom_button.clicked.connect(self.zoom_controller.reset)
        zoom_controls.addWidget(self.cluster_reset_zoom_button)

        self.cluster_zoom_in_button = QPushButton("Zoom In")
        self.cluster_zoom_in_button.setObjectName("secondaryActionButton")
        self.cluster_zoom_in_button.setToolTip(zoom_help_text)
        self.cluster_zoom_in_button.clicked.connect(self.zoom_controller.zoom_in)
        zoom_controls.addWidget(self.cluster_zoom_in_button)
        control_layout.addLayout(zoom_controls)

        self.control_panel = control_panel
        self._chrome_toggle = _build_chrome_toggle(
            settings=self._ui_settings,
            settings_key="cluster_tab/chrome_visible",
            panel=self.control_panel,
            on_toggled=lambda _visible: QTimer.singleShot(0, self._update_cluster_card_geometry),
        )
        root.addWidget(self._chrome_toggle)
        root.addWidget(control_panel)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("clusterScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_widget = QWidget()
        self.scroll_widget.setObjectName("clusterScrollWidget")
        self.scroll_widget.setMinimumWidth(0)
        self.scroll_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.grid_layout = QGridLayout(self.scroll_widget)
        self.grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.grid_layout.setContentsMargins(8, 8, 8, 8)
        self.grid_layout.setHorizontalSpacing(10)
        self.grid_layout.setVerticalSpacing(10)
        self.scroll_area.setWidget(self.scroll_widget)
        root.addWidget(self.scroll_area, 1)

        self._refresh_selector()
        self._load_cluster(self.current_index, force=True)
        self._configure_keyboard_focus()
        self._install_shortcuts()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        """Recompute cluster card sizing when the tab changes size."""

        super().resizeEvent(event)
        self._update_cluster_card_geometry()

    def refresh_progress(self) -> None:
        """Refresh cluster progress from the session."""

        summary = self.session.progress_summary()
        self.progress_label.setText(
            f"Clusters labeled {summary['labeled_clusters']}/{summary['total_clusters']}"
        )
        self._refresh_selector()

    def has_unsaved_changes(self) -> bool:
        """Return whether the current cluster view has unsaved edits."""

        return self.dirty

    def _install_shortcuts(self) -> None:
        """Register fast cluster labeling shortcuts."""

        shortcuts = [
            QShortcut(QKeySequence("W"), self, activated=lambda: self._assign_active_card("accept")),
            QShortcut(QKeySequence("X"), self, activated=lambda: self._assign_active_card("reject")),
            QShortcut(QKeySequence("Return"), self, activated=self._save_and_next),
            QShortcut(QKeySequence("Enter"), self, activated=self._save_and_next),
            QShortcut(QKeySequence("N"), self, activated=self._navigate_next_unlabeled),
            QShortcut(
                QKeySequence("Left"),
                self,
                activated=lambda: self._navigate(self.current_index - 1),
            ),
            QShortcut(
                QKeySequence("Right"),
                self,
                activated=lambda: self._navigate(self.current_index + 1),
            ),
        ]
        for index in range(9):
            shortcuts.append(
                QShortcut(
                    QKeySequence(str(index + 1)),
                    self,
                    activated=lambda card_index=index: self._select_card_index(card_index),
                )
            )
        for shortcut in shortcuts:
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)

    def _configure_keyboard_focus(self) -> None:
        """Keep Speed Cull shortcuts active after mouse interaction."""

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.scroll_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll_area.viewport().setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for widget in (
            self.prev_button,
            self.next_button,
            self.next_unlabeled_button,
            self.save_button,
            self.save_next_button,
            self.clear_button,
            self.reject_all_button,
            self.auto_card_advance_checkbox,
            self.auto_cluster_advance_checkbox,
            self.instructions_toggle,
            self.cluster_zoom_out_button,
            self.cluster_reset_zoom_button,
            self.cluster_zoom_in_button,
        ):
            widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def confirm_discard_unsaved(self) -> bool:
        """Ask the user before discarding unsaved changes."""

        if not self.dirty:
            return True

        result = QMessageBox.question(
            self,
            "Discard Unsaved Changes?",
            "This cluster has unsaved changes. Discard them?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return result == QMessageBox.StandardButton.Yes

    def save_current_cluster(self) -> bool:
        """Validate and save the current cluster label."""

        save_start = time.perf_counter()
        if not self.clusters:
            _child_perf_log("labeling.child.cluster_save.blocked", reason="no_clusters")
            return False

        card_decisions: List[Tuple[List[ImageItem], str]] = [
            (list(card.members), card.assignment()) for card in self.cards
        ]
        decided_count = sum(1 for _members, decision in card_decisions if decision != "unlabeled")
        if decided_count == 0:
            _child_perf_log(
                "labeling.child.cluster_save.blocked",
                reason="no_decisions",
                card_count=len(self.cards),
            )
            QMessageBox.information(
                self,
                "Nothing to Save",
                "Make a Keep / OK / Reject choice on at least one card before saving.",
            )
            return False

        cluster = self.clusters[self.current_index]
        store_start = time.perf_counter()
        self.session.save_card_decisions(cluster.cluster_id, card_decisions)
        _child_perf_log(
            "labeling.child.cluster_save.store",
            duration_ms=(time.perf_counter() - store_start) * 1000.0,
            cluster_id=cluster.cluster_id,
            cluster_index=self.current_index,
            card_count=len(self.cards),
        )
        self.dirty = False
        ui_start = time.perf_counter()
        self._update_cluster_rule_ui()
        self.on_progress_changed()
        self.refresh_progress()
        _child_perf_log(
            "labeling.child.cluster_save.ui_refresh",
            duration_ms=(time.perf_counter() - ui_start) * 1000.0,
            cluster_id=cluster.cluster_id,
            cluster_index=self.current_index,
        )
        _child_perf_log(
            "labeling.child.cluster_save.total",
            duration_ms=(time.perf_counter() - save_start) * 1000.0,
            cluster_id=cluster.cluster_id,
            cluster_index=self.current_index,
            card_count=len(self.cards),
        )
        return True

    def _save_and_next(self) -> None:
        """Save the current cluster and move to the next unlabeled one."""

        transition_start = time.perf_counter()
        previous_index = self.current_index
        if not self.save_current_cluster():
            _child_perf_log(
                "labeling.child.cluster_transition.blocked",
                duration_ms=(time.perf_counter() - transition_start) * 1000.0,
                source="save_and_next",
                previous_index=previous_index,
            )
            return
        next_lookup_start = time.perf_counter()
        next_index = self.session.next_unlabeled_cluster_index(self.current_index + 1)
        _child_perf_log(
            "labeling.child.cluster_transition.next_lookup",
            duration_ms=(time.perf_counter() - next_lookup_start) * 1000.0,
            source="save_and_next",
            previous_index=previous_index,
            next_index=next_index,
        )
        self._load_cluster(next_index, force=True)
        _child_perf_log(
            "labeling.child.cluster_transition.total",
            duration_ms=(time.perf_counter() - transition_start) * 1000.0,
            source="save_and_next",
            previous_index=previous_index,
            next_index=next_index,
        )

    def _navigate(self, index: int) -> None:
        """Navigate to another cluster by index."""

        if not self.clusters:
            return

        bounded_index = max(0, min(index, len(self.clusters) - 1))
        _child_perf_log(
            "labeling.child.cluster_navigation.requested",
            source="navigate",
            previous_index=self.current_index,
            requested_index=index,
            bounded_index=bounded_index,
        )
        self._load_cluster(bounded_index, force=False)

    def _navigate_next_unlabeled(self) -> None:
        """Jump to the next unlabeled cluster."""

        lookup_start = time.perf_counter()
        next_index = self.session.next_unlabeled_cluster_index(self.current_index + 1)
        _child_perf_log(
            "labeling.child.cluster_transition.next_lookup",
            duration_ms=(time.perf_counter() - lookup_start) * 1000.0,
            source="next_unlabeled",
            previous_index=self.current_index,
            next_index=next_index,
        )
        self._load_cluster(next_index, force=False)

    def _on_selector_changed(self, index: int) -> None:
        """Handle direct cluster selection from the dropdown."""

        if index < 0 or index == self.current_index:
            return
        self._load_cluster(index, force=False)

    def _load_cluster(self, index: int, *, force: bool) -> None:
        """Load one cluster into the grid view."""

        cluster_load_start = time.perf_counter()
        if not self.clusters:
            self.cluster_meta_label.setText("No multi-image clusters are available.")
            self._clear_cards()
            self._set_cluster_controls_enabled(False)
            self.refresh_progress()
            return

        if not force and not self.confirm_discard_unsaved():
            self.cluster_selector.blockSignals(True)
            self.cluster_selector.setCurrentIndex(self.current_index)
            self.cluster_selector.blockSignals(False)
            return

        self.current_index = max(0, min(index, len(self.clusters) - 1))
        cluster = self.clusters[self.current_index]
        _child_perf_log(
            "labeling.child.cluster_load.start",
            cluster_id=cluster.cluster_id,
            cluster_index=self.current_index,
            cluster_total=len(self.clusters),
            member_count=len(cluster.members),
            force=force,
        )
        _notify_host_startup(
            "cluster_load_start",
            f"Loading initial cluster {self.current_index + 1}/{len(self.clusters)} with {len(cluster.members)} images",
        )
        assignments_start = time.perf_counter()
        saved_assignments = self.session.cluster_label_assignments(cluster.cluster_id)
        _child_perf_log(
            "labeling.child.cluster_load.assignments",
            duration_ms=(time.perf_counter() - assignments_start) * 1000.0,
            cluster_id=cluster.cluster_id,
            cluster_index=self.current_index,
            saved_count=len(saved_assignments),
        )

        selector_start = time.perf_counter()
        self.cluster_selector.blockSignals(True)
        self.cluster_selector.setCurrentIndex(self.current_index)
        self.cluster_selector.blockSignals(False)
        _child_perf_log(
            "labeling.child.cluster_load.selector",
            duration_ms=(time.perf_counter() - selector_start) * 1000.0,
            cluster_id=cluster.cluster_id,
            cluster_index=self.current_index,
        )

        clear_start = time.perf_counter()
        self._clear_cards()
        self.zoom_controller.clear_views()
        self.zoom_controller.reset()
        _child_perf_log(
            "labeling.child.cluster_load.clear",
            duration_ms=(time.perf_counter() - clear_start) * 1000.0,
            cluster_id=cluster.cluster_id,
            cluster_index=self.current_index,
        )
        layout_start = time.perf_counter()
        member_groups = self._resolve_member_groups(cluster, saved_assignments)
        columns = min(self.config.cluster_grid_columns, max(1, len(member_groups)))
        self._current_card_row_count = max(1, (len(member_groups) + columns - 1) // columns)
        for column in range(self.config.cluster_grid_columns):
            self.grid_layout.setColumnStretch(column, 0)
            self.grid_layout.setColumnMinimumWidth(column, 0)
        for column in range(columns):
            self.grid_layout.setColumnStretch(column, 1)
        for group_index, group_members in enumerate(member_groups):
            card_start = time.perf_counter()
            card = ClusterImageCard(
                group_members,
                display_index=group_index + 1,
                preview_height=self.config.cluster_preview_height,
                sync_controller=self.zoom_controller,
                on_changed=self._on_card_assignment_changed,
                on_selected=self._select_card,
                on_expand_requested=self._on_expand_requested,
            )
            card.set_assignment(_group_initial_assignment(group_members, saved_assignments))
            row = group_index // columns
            column = group_index % columns
            self.grid_layout.addWidget(card, row, column)
            self.cards.append(card)
            card_ms = (time.perf_counter() - card_start) * 1000.0
            if card_ms >= 250.0:
                _notify_host_startup(
                    "cluster_card_slow",
                    f"Built cluster card in {card_ms:.0f} ms: {group_members[0].file_name}",
                )
        _child_perf_log(
            "labeling.child.cluster_load.cards",
            duration_ms=(time.perf_counter() - layout_start) * 1000.0,
            cluster_id=cluster.cluster_id,
            cluster_index=self.current_index,
            member_count=len(cluster.members),
            columns=columns,
        )

        finalize_start = time.perf_counter()
        card_count = len(member_groups)
        member_count = len(cluster.members)
        if card_count == member_count:
            summary_text = f"<b>{member_count}</b> image(s) in this cluster"
        else:
            summary_text = (
                f"<b>{card_count}</b> card(s) &nbsp;·&nbsp; "
                f"<b>{member_count}</b> images underneath"
            )
        self.cluster_meta_label.setText(summary_text)
        self.cluster_meta_label.setToolTip(
            f"Cluster {cluster.cluster_id}\n"
            f"Reason: {cluster.cluster_reason or 'manual grouping'}\n"
            f"Window: {cluster.window_kind or 'window'} / {cluster.time_window_id or 'n/a'}"
        )
        self.dirty = False
        self._set_cluster_controls_enabled(True)
        starting_index = self._next_unlabeled_card_index(0)
        self._select_card_index(starting_index if starting_index is not None else 0)
        self._update_cluster_rule_ui()
        self._update_cluster_card_geometry()
        self.refresh_progress()
        _child_perf_log(
            "labeling.child.cluster_load.finalize",
            duration_ms=(time.perf_counter() - finalize_start) * 1000.0,
            cluster_id=cluster.cluster_id,
            cluster_index=self.current_index,
        )
        total_ms = (time.perf_counter() - cluster_load_start) * 1000.0
        _child_perf_log(
            "labeling.child.cluster_load.total",
            duration_ms=total_ms,
            cluster_id=cluster.cluster_id,
            cluster_index=self.current_index,
            cluster_total=len(self.clusters),
            member_count=len(cluster.members),
            force=force,
        )
        _notify_host_startup(
            "cluster_load_ready",
            f"Initial cluster loaded in {total_ms:.0f} ms",
        )

    @staticmethod
    def _instructions_toggle_text(visible: bool) -> str:
        return "Hide instructions ▲" if visible else "Show instructions ▼"

    def _toggle_cluster_instructions(self) -> None:
        """Show or hide the labeling instructions panel and persist the choice."""

        new_visible = not self.cluster_guidance_label.isVisible()
        self.cluster_guidance_label.setVisible(new_visible)
        self.instructions_toggle.setText(self._instructions_toggle_text(new_visible))
        self._ui_settings.setValue("cluster_tab/instructions_visible", new_visible)

    def _resolve_member_groups(
        self,
        cluster: ClusterItem,
        saved_assignments: Dict[str, str],
    ) -> List[List[ImageItem]]:
        """Return the list of card groups for a cluster, honoring expand state."""

        base_groups = self.session.cluster_member_groups(cluster.cluster_id)
        if not base_groups:
            return [[member] for member in cluster.members]

        force_expand = cluster.cluster_id in self._expanded_cluster_ids
        resolved: List[List[ImageItem]] = []
        for group in base_groups:
            if force_expand and len(group) > 1:
                resolved.extend([[member] for member in group])
                continue
            if len(group) > 1 and _has_mixed_saved_assignments(group, saved_assignments):
                resolved.extend([[member] for member in group])
                continue
            resolved.append(list(group))
        return resolved

    def _on_expand_requested(self, card: "ClusterImageCard") -> None:
        """Break the current cluster's stacks into per-image cards."""

        if not self.clusters:
            return
        cluster = self.clusters[self.current_index]
        if cluster.cluster_id in self._expanded_cluster_ids:
            return
        self._expanded_cluster_ids.add(cluster.cluster_id)
        _child_perf_log(
            "labeling.child.cluster_stack.expand",
            cluster_id=cluster.cluster_id,
            cluster_index=self.current_index,
            stack_size=len(card.members),
        )
        self._load_cluster(self.current_index, force=True)

    def _refresh_selector(self) -> None:
        """Refresh cluster selector labels and progress state."""

        refresh_start = time.perf_counter()
        decision_statuses = self.session.cluster_decision_statuses()
        self.cluster_selector.blockSignals(True)
        self.cluster_selector.clear()
        for cluster in self.clusters:
            suffix = "labeled" if decision_statuses.get(cluster.cluster_id, False) else "unlabeled"
            self.cluster_selector.addItem(
                f"{cluster.cluster_id} ({len(cluster.members)}) [{suffix}]",
                cluster.cluster_id,
            )
        if self.clusters:
            self.cluster_selector.setCurrentIndex(min(self.current_index, len(self.clusters) - 1))
        self.cluster_selector.blockSignals(False)
        _child_perf_log(
            "labeling.child.cluster_selector.refresh",
            duration_ms=(time.perf_counter() - refresh_start) * 1000.0,
            cluster_index=self.current_index,
            cluster_count=len(self.clusters),
            labeled_count=sum(1 for labeled in decision_statuses.values() if labeled),
        )

    def _clear_all_assignments(self) -> None:
        """Clear the current cluster assignments back to unlabeled."""

        for card in self.cards:
            card.set_assignment("unlabeled")
        self._mark_dirty()
        self._select_card_index(0)

    def _reject_all_assignments(self) -> None:
        """Mark every image in the cluster as reject evidence."""

        for card in self.cards:
            card.set_assignment("reject")
        self._mark_dirty()
        self._select_card_index(0)
        self._advance_after_assignment()

    def _assign_active_card(self, assignment: str) -> None:
        """Assign the active card and advance to the next target."""

        if not self.cards:
            return

        card = self.cards[self.active_card_index]
        if card.assignment() != assignment:
            card.set_assignment(assignment)
            self._mark_dirty()
        self._select_card(card)
        self._advance_after_assignment()

    def _on_card_assignment_changed(self, card: ClusterImageCard) -> None:
        """Track assignment edits from cluster cards."""

        self._mark_dirty()
        self._select_card(card)
        self._advance_after_assignment()

    def _advance_after_assignment(self) -> None:
        """Apply the current trainer flow toggles after a card is labeled."""

        if self._cluster_ready_to_save():
            if self._should_auto_advance_cluster():
                _child_perf_log(
                    "labeling.child.cluster_auto_advance",
                    cluster_index=self.current_index,
                    expanded=self._current_cluster_is_expanded(),
                    all_cards_labeled=self._all_cards_labeled(),
                    card_count=len(self.cards),
                    trigger="assignment",
                )
                self._save_and_advance_cluster_auto()
            else:
                _child_perf_log(
                    "labeling.child.cluster_auto_advance_suppressed",
                    cluster_index=self.current_index,
                    expanded=self._current_cluster_is_expanded(),
                    all_cards_labeled=self._all_cards_labeled(),
                    card_count=len(self.cards),
                    trigger="assignment",
                )
            return

        if not self.auto_card_advance_checkbox.isChecked():
            return

        next_index = self._next_unlabeled_card_index(
            self.active_card_index + 1,
            wrap=True,
        )
        if next_index is not None:
            self._select_card_index(next_index)

    def _select_card(self, card: ClusterImageCard) -> None:
        """Select one card by instance."""

        if card not in self.cards:
            return
        self._select_card_index(self.cards.index(card))

    def _select_card_index(self, index: int) -> None:
        """Highlight one card and keep it visible."""

        if not self.cards:
            self.active_card_index = 0
            return

        bounded_index = max(0, min(index, len(self.cards) - 1))
        self.active_card_index = bounded_index
        for card_index, card in enumerate(self.cards):
            card.set_active(card_index == bounded_index)
        self.scroll_area.ensureWidgetVisible(self.cards[bounded_index], 24, 24)

    def _next_unlabeled_card_index(
        self,
        start_index: int,
        *,
        wrap: bool = False,
    ) -> Optional[int]:
        """Return the next card index that is still unlabeled."""

        if not self.cards:
            return None

        for index in range(max(0, start_index), len(self.cards)):
            if self.cards[index].assignment() == "unlabeled":
                return index

        if wrap:
            for index in range(0, min(max(0, start_index), len(self.cards))):
                if self.cards[index].assignment() == "unlabeled":
                    return index

        return None

    def _all_cards_labeled(self) -> bool:
        """Return whether the current cluster has a label for every card."""

        return all(card.assignment() != "unlabeled" for card in self.cards)

    def _current_cluster_is_expanded(self) -> bool:
        """Return whether the current cluster has been expanded into single-image cards."""

        if not self.clusters:
            return False
        return self.clusters[self.current_index].cluster_id in self._expanded_cluster_ids

    def _should_auto_advance_cluster(self) -> bool:
        """Return whether the current assignment should save and move ahead."""

        if not self.auto_cluster_advance_checkbox.isChecked():
            return False
        if not self._current_cluster_is_expanded():
            return True
        return self._all_cards_labeled()

    def _accept_assignment_count(self) -> int:
        """Return how many cards are currently marked Accept."""

        return sum(1 for card in self.cards if card.assignment() == "accept")

    def _cluster_ready_to_save(self) -> bool:
        """Return whether the current cluster has any decision worth saving.

        Speed-cull rule: at least one card must have a decision (Accept/Reject).
        Unlabeled cards are allowed — they preserve any prior state on save.
        """

        return any(card.assignment() != "unlabeled" for card in self.cards)

    def _save_and_advance_cluster_auto(self) -> None:
        """Save the current cluster and jump to the next unlabeled cluster, if any."""

        if not self.save_current_cluster():
            return

        decision_statuses = self.session.cluster_decision_statuses()
        for index in range(self.current_index + 1, len(self.clusters)):
            if not decision_statuses.get(self.clusters[index].cluster_id, False):
                self._load_cluster(index, force=True)
                return

        self._load_cluster(self.current_index, force=True)

    def _mark_dirty(self) -> None:
        """Mark the current cluster as having unsaved edits."""

        self.dirty = True
        self._update_cluster_rule_ui()

    def _clear_cards(self) -> None:
        """Remove existing cluster cards from the layout."""

        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.cards = []
        self.active_card_index = 0
        self._update_cluster_rule_ui()

    def _update_cluster_rule_ui(self) -> None:
        """Refresh the save rule badge and save-button state."""

        if not self.cards:
            self._set_cluster_rule_status("warning", "Nothing to cull in this cluster")
            self.save_button.setEnabled(False)
            self.save_next_button.setEnabled(False)
            return

        unlabeled_count = sum(1 for card in self.cards if card.assignment() == "unlabeled")
        accept_count = self._accept_assignment_count()
        reject_count = sum(1 for card in self.cards if card.assignment() == "reject")
        decided_count = accept_count + reject_count

        if decided_count == 0:
            status = "warning"
            text = "Mark at least one card to save"
        else:
            status = "success"
            parts: list[str] = []
            if accept_count:
                parts.append(f"Accept {accept_count}")
            if reject_count:
                parts.append(f"Reject {reject_count}")
            summary = " · ".join(parts)
            if unlabeled_count:
                text = f"Ready to save — {summary} ({unlabeled_count} skipped)"
            else:
                text = f"Ready to save — {summary}"

        self._set_cluster_rule_status(status, text)
        can_save = self._cluster_ready_to_save()
        self.save_button.setEnabled(can_save)
        self.save_next_button.setEnabled(can_save)

    def _set_cluster_rule_status(self, status: str, text: str) -> None:
        """Set the cluster rule badge styling and text."""

        self.cluster_rule_label.setText(text)
        self.cluster_rule_label.setProperty("status", status)
        self.cluster_rule_label.style().unpolish(self.cluster_rule_label)
        self.cluster_rule_label.style().polish(self.cluster_rule_label)
        self.cluster_rule_label.update()

    def _update_cluster_card_geometry(self) -> None:
        """Resize cluster cards so each row fits the available viewport space."""

        if not self.cards:
            return

        viewport_height = self.scroll_area.viewport().height()
        if viewport_height <= 0:
            return

        layout_margins = self.grid_layout.contentsMargins()
        vertical_spacing = max(0, self.grid_layout.verticalSpacing())
        row_count = max(1, self._current_card_row_count)
        total_spacing = vertical_spacing * (row_count - 1)
        available_total_height = (
            viewport_height
            - layout_margins.top()
            - layout_margins.bottom()
            - total_spacing
            - 8
        )
        per_row_height = available_total_height / row_count
        card_overhead = max((getattr(card, "_card_overhead", 110) for card in self.cards), default=110)
        preview_height = int(max(48, min(per_row_height - card_overhead - 2, 2000)))
        _child_perf_log(
            "labeling.child.cluster_geometry",
            viewport_height=viewport_height,
            row_count=row_count,
            per_row_height=round(per_row_height, 3),
            card_overhead=card_overhead,
            preview_height=preview_height,
        )

        for card in self.cards:
            card.set_preview_height(preview_height)

    def _set_cluster_controls_enabled(self, enabled: bool) -> None:
        """Enable or disable cluster controls as a group."""

        for widget in (
            self.prev_button,
            self.cluster_selector,
            self.next_button,
            self.next_unlabeled_button,
            self.clear_button,
            self.reject_all_button,
            self.auto_card_advance_checkbox,
            self.auto_cluster_advance_checkbox,
            self.cluster_zoom_out_button,
            self.cluster_reset_zoom_button,
            self.cluster_zoom_in_button,
        ):
            widget.setEnabled(enabled)
        if enabled:
            self._update_cluster_rule_ui()
        else:
            self.save_button.setEnabled(False)
            self.save_next_button.setEnabled(False)


class LabelingMainWindow(QMainWindow):
    """Main application window for local preference labeling."""

    def __init__(
        self,
        config: LabelingConfig,
        *,
        session: Optional[LabelingSession] = None,
    ) -> None:
        startup_start = time.perf_counter()
        _notify_host_startup("main_window_start", "Building label collection window")
        super().__init__()
        self.config = config
        session_start = time.perf_counter()
        self.session = session or LabelingSession(config)
        _notify_host_startup(
            "session_ready",
            (
                f"Loaded labeling dataset: {len(self.session.dataset.ordered_images)} images, "
                f"{len(self.session.dataset.multi_image_clusters)} multi-image groups"
            ),
            started_at=session_start,
        )
        self._force_close_from_parent = False

        self.setWindowTitle("Speed Cull")
        self.resize(1680, 1080)

        central = QWidget()
        central.setObjectName("labelingCentralContainer")
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(14, 14, 14, 12)
        central_layout.setSpacing(12)

        header_panel = QFrame()
        header_panel.setObjectName("labelingHeaderPanel")
        header_layout = QVBoxLayout(header_panel)
        header_layout.setContentsMargins(16, 14, 16, 14)
        header_layout.setSpacing(10)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        self.header_title = QLabel("Speed Cull")
        self.header_title.setObjectName("labelingHeaderTitle")
        title_row.addWidget(self.header_title)
        title_row.addStretch(1)
        header_layout.addLayout(title_row)

        summary_row = QHBoxLayout()
        summary_row.setSpacing(10)
        self.total_images_badge = QLabel("")
        self.total_images_badge.setObjectName("summaryBadge")
        summary_row.addWidget(self.total_images_badge)

        self.cluster_badge = QLabel("")
        self.cluster_badge.setObjectName("summaryBadgeAccent")
        summary_row.addWidget(self.cluster_badge)
        summary_row.addStretch(1)
        header_layout.addLayout(summary_row)
        central_layout.addWidget(header_panel)

        cull_start = time.perf_counter()
        self.cluster_tab = ClusterTab(self.session, config, self.refresh_status)
        _notify_host_startup("cull_view_ready", "Cull view built", started_at=cull_start)
        central_layout.addWidget(self.cluster_tab, 1)
        self.setCentralWidget(central)
        menu_start = time.perf_counter()
        self._build_menu_bar()
        _notify_host_startup("menu_ready", "Label collection menus built", started_at=menu_start)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.refresh_status()
        _notify_host_startup("main_window_ready", "Label collection window object ready", started_at=startup_start)

    def _build_menu_bar(self) -> None:
        """Build a lightweight menu bar for common Speed Cull actions."""

        file_menu = self.menuBar().addMenu("&File")
        save_action = QAction("Save Group", self)
        save_action.setShortcut(QKeySequence.StandardKey.Save)
        save_action.triggered.connect(self.cluster_tab.save_current_cluster)
        file_menu.addAction(save_action)
        file_menu.addSeparator()
        close_action = QAction("Close", self)
        close_action.setShortcut(QKeySequence.StandardKey.Close)
        close_action.triggered.connect(self.close)
        file_menu.addAction(close_action)

        navigate_menu = self.menuBar().addMenu("&Navigate")
        previous_action = QAction("Previous Group", self)
        previous_action.triggered.connect(
            lambda: self.cluster_tab._navigate(self.cluster_tab.current_index - 1)
        )
        navigate_menu.addAction(previous_action)

        next_action = QAction("Next Group", self)
        next_action.triggered.connect(
            lambda: self.cluster_tab._navigate(self.cluster_tab.current_index + 1)
        )
        navigate_menu.addAction(next_action)

        next_unreviewed_action = QAction("Next Unreviewed Group", self)
        next_unreviewed_action.triggered.connect(self.cluster_tab._navigate_next_unlabeled)
        navigate_menu.addAction(next_unreviewed_action)

        view_menu = self.menuBar().addMenu("&View")
        zoom_in_action = QAction("Zoom In", self)
        zoom_in_action.setShortcut(QKeySequence.StandardKey.ZoomIn)
        zoom_in_action.triggered.connect(self._zoom_in_current_tab)
        view_menu.addAction(zoom_in_action)

        zoom_out_action = QAction("Zoom Out", self)
        zoom_out_action.setShortcut(QKeySequence.StandardKey.ZoomOut)
        zoom_out_action.triggered.connect(self._zoom_out_current_tab)
        view_menu.addAction(zoom_out_action)

        reset_zoom_action = QAction("Reset Zoom", self)
        reset_zoom_action.setShortcut(QKeySequence("0"))
        reset_zoom_action.triggered.connect(self._reset_zoom_current_tab)
        view_menu.addAction(reset_zoom_action)

        help_menu = self.menuBar().addMenu("&Help")
        shortcuts_action = QAction("Speed Cull Shortcuts", self)
        shortcuts_action.triggered.connect(self._show_shortcuts_help)
        help_menu.addAction(shortcuts_action)

    def _active_zoom_controller(self) -> "PairZoomSyncController":
        """Return the zoom controller for the cull view."""

        return self.cluster_tab.zoom_controller

    def _zoom_in_current_tab(self) -> None:
        self._active_zoom_controller().zoom_in()

    def _zoom_out_current_tab(self) -> None:
        self._active_zoom_controller().zoom_out()

    def _reset_zoom_current_tab(self) -> None:
        self._active_zoom_controller().reset()

    def _show_shortcuts_help(self) -> None:
        """Show a concise shortcuts guide for the speed cull workflow."""

        QMessageBox.information(
            self,
            "Speed Cull Shortcuts",
            "\n".join(
                [
                    "W = Accept, X = Reject for the active card (matches the main app).",
                    "Click a card or press 1-9 to make it active.",
                    "Stacks: Reject fans out to every frame in the stack.",
                    "Stacks: Accept keeps only the visible top frame. Hit Expand to split.",
                    "Enter saves the group. N jumps to the next unreviewed group.",
                    "Mouse-wheel zoom and drag are synced across every card in the group.",
                ]
            ),
        )

    def refresh_status(self) -> None:
        """Refresh global status bar and child progress labels."""

        summary = self.session.progress_summary()
        self.cluster_tab.refresh_progress()
        self.total_images_badge.setText(f"Photos {summary['total_images']}")
        self.cluster_badge.setText(
            f"Reviewed {summary['labeled_clusters']}/{summary['total_clusters']}"
        )
        self.status.showMessage(
            f"Decisions save to the host Image Triage database."
        )

    def request_parent_shutdown(self) -> None:
        """Close immediately when the Image Triage host is shutting down."""

        self._force_close_from_parent = True
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Warn before closing if the cluster tab has unsaved changes."""

        if self._force_close_from_parent or self.cluster_tab.confirm_discard_unsaved():
            event.accept()
        else:
            event.ignore()


class PairZoomSyncController:
    """Keep the pairwise previews at the same zoom level and pan position."""

    def __init__(self) -> None:
        self.views: List["ZoomableImageView"] = []
        self.zoom_factor = 1.0
        self.center_ratio = (0.5, 0.5)
        self._syncing = False

    def register(self, view: "ZoomableImageView") -> None:
        """Register a zoomable preview view."""

        if view not in self.views:
            self.views.append(view)

    def clear_views(self) -> None:
        """Forget views from the prior screen before repopulating a new one."""

        self.views.clear()

    def zoom_in(self) -> None:
        """Increase shared zoom."""

        self.set_shared_state(zoom_factor=self.zoom_factor * 1.2, source="zoom_in")

    def zoom_out(self) -> None:
        """Decrease shared zoom."""

        self.set_shared_state(zoom_factor=self.zoom_factor / 1.2, source="zoom_out")

    def reset(self) -> None:
        """Reset both views to fit-to-window centered mode."""

        self.set_shared_state(zoom_factor=1.0, center_ratio=(0.5, 0.5), source="reset")

    def set_shared_state(
        self,
        *,
        zoom_factor: Optional[float] = None,
        center_ratio: Optional[Tuple[float, float]] = None,
        source: str = "api",
    ) -> float | None:
        """Broadcast a new shared zoom/pan state to every registered view."""

        if self._syncing:
            return None

        if zoom_factor is not None:
            self.zoom_factor = max(1.0, min(float(zoom_factor), 12.0))

        if center_ratio is not None:
            self.center_ratio = (
                max(0.0, min(float(center_ratio[0]), 1.0)),
                max(0.0, min(float(center_ratio[1]), 1.0)),
            )

        self._syncing = True
        sync_start = time.perf_counter()
        try:
            for view in self.views:
                view.apply_shared_state(self.zoom_factor, self.center_ratio)
        finally:
            self._syncing = False
        duration_ms = (time.perf_counter() - sync_start) * 1000.0
        if duration_ms >= 20.0 or source in {"zoom_in", "zoom_out", "reset", "wheel", "mouse_release"}:
            _child_perf_log(
                "labeling.child.zoom_sync.broadcast",
                duration_ms=duration_ms,
                trigger=source,
                view_count=len(self.views),
                zoom_factor=round(self.zoom_factor, 4),
                center_x=round(self.center_ratio[0], 5),
                center_y=round(self.center_ratio[1], 5),
            )
        return duration_ms


class ZoomableImageView(QGraphicsView):
    """Graphics view that supports synchronized zooming and panning."""

    def __init__(
        self,
        sync_controller: PairZoomSyncController,
        *,
        minimum_size: Optional[QSize] = None,
        on_selected: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__()
        self.sync_controller = sync_controller
        self.sync_controller.register(self)
        self.on_selected = on_selected

        self._shared_zoom_factor = 1.0
        self._shared_center_ratio = (0.5, 0.5)
        self._applying_shared_state = False
        self._updating_interaction_mode = False
        self._pan_active = False
        self._pan_started_perf = 0.0
        self._pan_scroll_events = 0
        self._pan_sync_calls = 0
        self._pan_sync_ms = 0.0
        self._pan_slow_syncs = 0
        self._pan_last_sample_perf = 0.0
        self._pan_last_sample_events = 0

        scene = QGraphicsScene(self)
        self._pixmap_item = QGraphicsPixmapItem()
        scene.addItem(self._pixmap_item)
        self.setScene(scene)

        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setMinimumSize(minimum_size or QSize(420, 320))
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setObjectName("previewGraphicsView")
        self.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform)

        self.horizontalScrollBar().valueChanged.connect(self._on_scroll_changed)
        self.verticalScrollBar().valueChanged.connect(self._on_scroll_changed)

    def set_preview_pixmap(self, pixmap: QPixmap) -> None:
        """Load a new pixmap into the synchronized preview."""

        self._pixmap_item.setPixmap(pixmap)
        self.scene().setSceneRect(self._pixmap_item.boundingRect())
        self.apply_shared_state(
            self.sync_controller.zoom_factor,
            self.sync_controller.center_ratio,
        )

    def clear_preview(self) -> None:
        """Remove the current pixmap."""

        self._pixmap_item.setPixmap(QPixmap())
        self.scene().setSceneRect(0.0, 0.0, 0.0, 0.0)
        self.resetTransform()
        self._update_interaction_mode()

    def has_preview(self) -> bool:
        """Return whether the view currently contains an image."""

        return not self._pixmap_item.pixmap().isNull()

    def apply_shared_state(
        self,
        zoom_factor: float,
        center_ratio: Tuple[float, float],
    ) -> None:
        """Apply the current shared zoom state without feeding back into the controller."""

        if not self.has_preview():
            return

        self._shared_zoom_factor = zoom_factor
        self._shared_center_ratio = center_ratio
        self._applying_shared_state = True
        apply_start = time.perf_counter()
        try:
            self._apply_scale()
            self._center_on_ratio(center_ratio)
            self._update_interaction_mode()
        finally:
            self._applying_shared_state = False
        apply_ms = (time.perf_counter() - apply_start) * 1000.0
        if apply_ms >= 25.0:
            _child_perf_log(
                "labeling.child.zoom_sync.apply_slow",
                duration_ms=apply_ms,
                **self._log_context(),
                zoom_factor=round(zoom_factor, 4),
                center_x=round(center_ratio[0], 5),
                center_y=round(center_ratio[1], 5),
                pixmap_width=self._pixmap_item.pixmap().width(),
                pixmap_height=self._pixmap_item.pixmap().height(),
                viewport_width=self.viewport().width(),
                viewport_height=self.viewport().height(),
            )

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        """Zoom both previews together from the mouse wheel."""

        if not self.has_preview():
            super().wheelEvent(event)
            return

        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return

        factor = 1.2 if delta > 0 else (1.0 / 1.2)
        self.sync_controller.set_shared_state(
            zoom_factor=self.sync_controller.zoom_factor * factor,
            center_ratio=self._current_center_ratio(),
            source="wheel",
        )
        _child_perf_log(
            "labeling.child.zoom_wheel",
            **self._log_context(),
            delta=delta,
            view_count=len(self.sync_controller.views),
            zoom_factor=round(self.sync_controller.zoom_factor, 4),
        )
        event.accept()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        """Re-fit the image whenever the viewport size changes."""

        super().resizeEvent(event)
        if self._updating_interaction_mode:
            return
        if self.has_preview():
            self.apply_shared_state(
                self.sync_controller.zoom_factor,
                self.sync_controller.center_ratio,
            )

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        """Sync panning after drag gestures finish."""

        super().mouseReleaseEvent(event)
        self._emit_center_change(source="mouse_release")
        if self._pan_active:
            duration_ms = (time.perf_counter() - self._pan_started_perf) * 1000.0
            center_ratio = self._current_center_ratio()
            _child_perf_log(
                "labeling.child.pan.end",
                duration_ms=duration_ms,
                **self._log_context(),
                scroll_events=self._pan_scroll_events,
                sync_calls=self._pan_sync_calls,
                sync_ms=round(self._pan_sync_ms, 3),
                slow_syncs=self._pan_slow_syncs,
                view_count=len(self.sync_controller.views),
                zoom_factor=round(self.sync_controller.zoom_factor, 4),
                center_x=round(center_ratio[0], 5),
                center_y=round(center_ratio[1], 5),
            )
        self._pan_active = False

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        """Select this preview before handling normal drag behavior."""

        if self.on_selected is not None:
            self.on_selected()
        if self.has_preview() and event.button() == Qt.MouseButton.LeftButton:
            self._begin_pan_log()
        super().mousePressEvent(event)

    def _on_scroll_changed(self, _: int) -> None:
        """Sync panning during scroll/drag updates."""

        if self._pan_active:
            self._pan_scroll_events += 1
        sync_ms = self._emit_center_change(source="scroll")
        if self._pan_active and sync_ms is not None:
            self._pan_sync_calls += 1
            self._pan_sync_ms += sync_ms
            if sync_ms >= 20.0:
                self._pan_slow_syncs += 1
            self._maybe_log_pan_sample()

    def _emit_center_change(self, *, source: str = "scroll") -> float | None:
        """Push the current pan center back into the shared controller."""

        if (
            self._applying_shared_state
            or self.sync_controller._syncing
            or not self.has_preview()
        ):
            return None

        return self.sync_controller.set_shared_state(
            center_ratio=self._current_center_ratio(),
            source=source,
        )

    def _begin_pan_log(self) -> None:
        """Start aggregate logging for one user pan gesture."""

        self._pan_active = True
        self._pan_started_perf = time.perf_counter()
        self._pan_scroll_events = 0
        self._pan_sync_calls = 0
        self._pan_sync_ms = 0.0
        self._pan_slow_syncs = 0
        self._pan_last_sample_perf = self._pan_started_perf
        self._pan_last_sample_events = 0
        _child_perf_log(
            "labeling.child.pan.start",
            **self._log_context(),
            view_count=len(self.sync_controller.views),
            zoom_factor=round(self.sync_controller.zoom_factor, 4),
            drag_mode=str(self.dragMode()),
            pixmap_width=self._pixmap_item.pixmap().width(),
            pixmap_height=self._pixmap_item.pixmap().height(),
            viewport_width=self.viewport().width(),
            viewport_height=self.viewport().height(),
        )

    def _maybe_log_pan_sample(self) -> None:
        """Periodically log pan event rate while a drag is in progress."""

        now = time.perf_counter()
        elapsed = now - self._pan_last_sample_perf
        if elapsed < 0.25:
            return
        events_since_last = self._pan_scroll_events - self._pan_last_sample_events
        center_ratio = self._current_center_ratio()
        _child_perf_log(
            "labeling.child.pan.sample",
            duration_ms=elapsed * 1000.0,
            **self._log_context(),
            scroll_events=self._pan_scroll_events,
            events_since_last=events_since_last,
            event_rate_per_s=round(events_since_last / max(elapsed, 0.001), 2),
            sync_calls=self._pan_sync_calls,
            sync_ms=round(self._pan_sync_ms, 3),
            slow_syncs=self._pan_slow_syncs,
            view_count=len(self.sync_controller.views),
            zoom_factor=round(self.sync_controller.zoom_factor, 4),
            center_x=round(center_ratio[0], 5),
            center_y=round(center_ratio[1], 5),
        )
        self._pan_last_sample_perf = now
        self._pan_last_sample_events = self._pan_scroll_events

    def _log_context(self) -> dict[str, object]:
        """Return compact identifying fields for pan and zoom logs."""

        return {
            "image_id": str(self.property("image_id") or ""),
            "file_name": str(self.property("file_name") or ""),
            "cluster_id": str(self.property("cluster_id") or ""),
        }

    def _apply_scale(self) -> None:
        """Scale the view so zoom=1 means fit-to-window."""

        pixmap = self._pixmap_item.pixmap()
        if pixmap.isNull():
            return

        viewport_width = max(1, self.viewport().width() - 4)
        viewport_height = max(1, self.viewport().height() - 4)
        base_scale = min(
            viewport_width / max(1, pixmap.width()),
            viewport_height / max(1, pixmap.height()),
        )

        self.resetTransform()
        self.scale(base_scale * self._shared_zoom_factor, base_scale * self._shared_zoom_factor)

    def _update_interaction_mode(self) -> None:
        """Disable panning/scrollbars when the image is already fit to the viewport."""

        if self._updating_interaction_mode:
            return

        zoomed_in = self.has_preview() and self._shared_zoom_factor > 1.01
        drag_mode = (
            QGraphicsView.DragMode.ScrollHandDrag
            if zoomed_in
            else QGraphicsView.DragMode.NoDrag
        )
        policy = (
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if zoomed_in
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._updating_interaction_mode = True
        try:
            if self.dragMode() != drag_mode:
                self.setDragMode(drag_mode)
            if self.horizontalScrollBarPolicy() != policy:
                self.setHorizontalScrollBarPolicy(policy)
            if self.verticalScrollBarPolicy() != policy:
                self.setVerticalScrollBarPolicy(policy)
        finally:
            self._updating_interaction_mode = False

    def _current_center_ratio(self) -> Tuple[float, float]:
        """Measure the current viewport center as a normalized scene position."""

        scene_rect = self.sceneRect()
        if scene_rect.width() <= 0 or scene_rect.height() <= 0:
            return (0.5, 0.5)

        center_scene = self.mapToScene(self.viewport().rect().center())
        x_ratio = (center_scene.x() - scene_rect.left()) / scene_rect.width()
        y_ratio = (center_scene.y() - scene_rect.top()) / scene_rect.height()
        return (
            max(0.0, min(float(x_ratio), 1.0)),
            max(0.0, min(float(y_ratio), 1.0)),
        )

    def _center_on_ratio(self, center_ratio: Tuple[float, float]) -> None:
        """Center the viewport on a normalized scene position."""

        scene_rect = self.sceneRect()
        if scene_rect.width() <= 0 or scene_rect.height() <= 0:
            return

        target = QPointF(
            scene_rect.left() + (scene_rect.width() * center_ratio[0]),
            scene_rect.top() + (scene_rect.height() * center_ratio[1]),
        )
        self.centerOn(target)
