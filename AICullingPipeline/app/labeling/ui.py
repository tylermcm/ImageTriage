"""PySide6 desktop UI for local pairwise and cluster labeling."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, Qt, QSize
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
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedLayout,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.config import LabelingConfig
from app.host_sync import HostSyncController
from app.labeling.models import ClusterItem, ImageItem, PairCandidate
from app.labeling.previews import load_oriented_pixmap
from app.labeling.session import LabelingSession
from app.labeling.theme import apply_labeling_theme


_READY_FILE_ENV = "IMAGE_TRIAGE_LABELING_READY_FILE"


def launch_labeling_app(
    config: LabelingConfig,
    *,
    session: Optional[LabelingSession] = None,
) -> int:
    """Launch the PySide6 labeling application."""

    app = QApplication.instance() or QApplication(sys.argv)
    apply_labeling_theme(app)
    window = LabelingMainWindow(config, session=session)
    window._host_sync_controller = HostSyncController(
        on_appearance_mode_changed=lambda _mode: apply_labeling_theme(app),
        on_shutdown_requested=window.request_parent_shutdown,
        parent=window,
    )
    window.show()
    app.processEvents()
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


class ImagePreviewWidget(QGroupBox):
    """Simple image preview panel used by the pairwise tab."""

    def __init__(
        self,
        title: str,
        *,
        max_width: int,
        max_height: int,
        sync_controller: Optional["PairZoomSyncController"] = None,
    ) -> None:
        super().__init__(title)
        self.max_width = max_width
        self.max_height = max_height
        self.sync_controller = sync_controller
        self.setObjectName("imagePreviewPanel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 18, 14, 14)
        layout.setSpacing(10)
        if sync_controller is None:
            self.image_label = PreviewLabel(
                max_width=max_width,
                max_height=max_height,
                minimum_size=QSize(420, 320),
                padding=12,
            )
            layout.addWidget(self.image_label)
            self.image_view = None
            self.message_label = None
            self.preview_stack = None
        else:
            self.image_view = ZoomableImageView(sync_controller)
            self.message_label = QLabel("No image loaded.")
            self.message_label.setObjectName("previewMessageLabel")
            self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.message_label.setMinimumSize(QSize(420, 320))
            self.message_label.setWordWrap(True)
            preview_container = QWidget()
            self.preview_stack = QStackedLayout(preview_container)
            self.preview_stack.addWidget(self.message_label)
            self.preview_stack.addWidget(self.image_view)
            layout.addWidget(preview_container)
            self.image_label = None

        self.path_label = QLabel("")
        self.path_label.setObjectName("previewPathLabel")
        self.path_label.setWordWrap(True)
        layout.addWidget(self.path_label)

        self.meta_label = QLabel("")
        self.meta_label.setObjectName("previewMetaLabel")
        self.meta_label.setWordWrap(True)
        layout.addWidget(self.meta_label)

    def set_image(self, image: Optional[ImageItem]) -> None:
        """Display an image preview and its metadata."""

        if image is None:
            self._set_message("No image loaded.")
            self.path_label.setText("")
            self.meta_label.setText("")
            return

        if not image.file_exists:
            self._set_message("Missing image file.")
        else:
            pixmap = load_oriented_pixmap(
                image.file_path,
                max_side=max(self.max_width, self.max_height) * 3,
            )
            if pixmap.isNull():
                self._set_message("Unable to render preview.")
            else:
                self._set_pixmap(pixmap)

        display_path = image.relative_path or str(image.file_path)
        self.path_label.setText(f"<b>{image.file_name}</b><br>{display_path}")
        self.path_label.setToolTip(str(image.file_path))
        timestamp_text = image.capture_timestamp or "missing"
        self.meta_label.setText(
            f"Cluster <b>{image.cluster_id}</b> · {image.cluster_size} image(s)<br>"
            f"Captured {timestamp_text} [{image.capture_time_source}]<br>"
            f"Image ID {image.image_id}"
        )

    def _set_message(self, text: str) -> None:
        """Show a placeholder message instead of an image preview."""

        if self.image_view is None or self.preview_stack is None or self.message_label is None:
            assert self.image_label is not None
            self.image_label.set_message(text)
            return

        self.image_view.clear_preview()
        self.message_label.setText(text)
        self.preview_stack.setCurrentWidget(self.message_label)

    def _set_pixmap(self, pixmap: QPixmap) -> None:
        """Show the current image preview."""

        if self.image_view is None or self.preview_stack is None:
            assert self.image_label is not None
            self.image_label.set_preview_pixmap(pixmap)
            return

        self.image_view.set_preview_pixmap(pixmap)
        self.preview_stack.setCurrentWidget(self.image_view)


class ClusterImageCard(QFrame):
    """Small card used for cluster-level labeling."""

    def __init__(
        self,
        image: ImageItem,
        *,
        preview_height: int,
        sync_controller: PairZoomSyncController,
        on_changed: Callable[["ClusterImageCard"], None],
        on_selected: Callable[["ClusterImageCard"], None],
    ) -> None:
        super().__init__()
        self.image = image
        self.on_changed = on_changed
        self.on_selected = on_selected
        self.setObjectName("clusterCard")
        self.setProperty("active", False)
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
        self.preview_view.setMinimumWidth(0)
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

        if not image.file_exists:
            self.preview_message.setText("Missing image file.")
            self.preview_stack.setCurrentWidget(self.preview_message)
        else:
            pixmap = load_oriented_pixmap(image.file_path, max_side=2600)
            if pixmap.isNull():
                self.preview_message.setText("Unable to render preview.")
                self.preview_stack.setCurrentWidget(self.preview_message)
            else:
                self.preview_view.set_preview_pixmap(pixmap)
                self.preview_stack.setCurrentWidget(self.preview_view)

        info = QLabel(
            f"<b>{image.file_name}</b><br>"
            f"{image.relative_path}<br>"
            f"timestamp: {image.capture_timestamp or 'missing'}"
        )
        info.setObjectName("clusterInfoLabel")
        info.setWordWrap(True)
        info.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        info.setToolTip(str(image.file_path))
        layout.addWidget(info)

        self.assignment_combo = QComboBox()
        self.assignment_combo.setObjectName("labelAssignmentCombo")
        self.assignment_combo.addItem("Unlabeled", "unlabeled")
        self.assignment_combo.addItem("Best", "best")
        self.assignment_combo.addItem("Acceptable", "acceptable")
        self.assignment_combo.addItem("Reject", "reject")
        self.assignment_combo.currentIndexChanged.connect(self._emit_changed)
        self.assignment_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.assignment_combo)

        self.layout().activate()
        self._card_overhead = max(0, self.sizeHint().height() - preview_height)
        self.set_preview_height(preview_height)

    def assignment(self) -> str:
        """Return the current label assignment for this card."""

        return str(self.assignment_combo.currentData())

    def set_assignment(self, assignment: str) -> None:
        """Set the current assignment without changing the image."""

        index = self.assignment_combo.findData(assignment)
        if index >= 0:
            self.assignment_combo.setCurrentIndex(index)

    def set_active(self, active: bool) -> None:
        """Highlight the card that keyboard labeling currently targets."""

        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def set_preview_height(self, preview_height: int) -> None:
        """Resize the preview region while keeping the metadata and combo visible."""

        self.preview_height = preview_height
        self.preview_view.setFixedHeight(preview_height)
        self.preview_message.setFixedHeight(preview_height)
        self.preview_container.setFixedHeight(preview_height)
        self.setFixedHeight(self._card_overhead + preview_height)

    def _emit_changed(self) -> None:
        """Notify the parent tab that this card changed."""

        self.on_changed(self)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        """Let users click a card to make it the active keyboard target."""

        self.on_selected(self)
        super().mousePressEvent(event)


class PairwiseTab(QWidget):
    """Tab for pairwise preference annotation."""

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
        self.current_candidate: Optional[PairCandidate] = None
        self.zoom_controller = PairZoomSyncController()
        self.setObjectName("pairwiseTab")

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
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Cluster Pairs", "cluster_pair")
        self.mode_combo.addItem("Arbitrary Pairs", "arbitrary_pair")
        self.mode_combo.currentIndexChanged.connect(self._load_next_pair)
        source_label = QLabel("Pair source")
        source_label.setObjectName("hintText")
        controls.addWidget(source_label)
        controls.addWidget(self.mode_combo)

        self.singletons_only_checkbox = QCheckBox("Singletons only for arbitrary pairs")
        self.singletons_only_checkbox.setChecked(
            self.config.default_arbitrary_singletons_only
        )
        self.singletons_only_checkbox.stateChanged.connect(self._load_next_pair)
        controls.addWidget(self.singletons_only_checkbox)

        self.next_pair_button = QPushButton("Next Pair")
        self.next_pair_button.setObjectName("secondaryActionButton")
        self.next_pair_button.clicked.connect(self._load_next_pair)
        controls.addWidget(self.next_pair_button)
        controls.addStretch(1)

        self.progress_label = QLabel("")
        self.progress_label.setObjectName("summaryBadge")
        controls.addWidget(self.progress_label)
        control_layout.addLayout(controls)

        self.guidance_label = QLabel(
            "Label clusters first. Pairwise is mainly for hard or ambiguous comparisons; "
            "the cluster-pair total is an available pool, not a quota."
        )
        self.guidance_label.setObjectName("hintText")
        self.guidance_label.setWordWrap(True)
        control_layout.addWidget(self.guidance_label)

        zoom_controls = QHBoxLayout()
        zoom_controls.setSpacing(10)
        self.zoom_help_label = QLabel(
            "Mouse wheel zooms both images together. Drag either preview to pan both. "
            "Use reset if you get lost."
        )
        self.zoom_help_label.setObjectName("hintText")
        self.zoom_help_label.setWordWrap(True)
        zoom_controls.addWidget(self.zoom_help_label, 1)

        self.zoom_out_button = QPushButton("Zoom Out")
        self.zoom_out_button.setObjectName("secondaryActionButton")
        self.zoom_out_button.clicked.connect(self.zoom_controller.zoom_out)
        zoom_controls.addWidget(self.zoom_out_button)

        self.reset_zoom_button = QPushButton("Reset Zoom")
        self.reset_zoom_button.setObjectName("secondaryActionButton")
        self.reset_zoom_button.clicked.connect(self.zoom_controller.reset)
        zoom_controls.addWidget(self.reset_zoom_button)

        self.zoom_in_button = QPushButton("Zoom In")
        self.zoom_in_button.setObjectName("secondaryActionButton")
        self.zoom_in_button.clicked.connect(self.zoom_controller.zoom_in)
        zoom_controls.addWidget(self.zoom_in_button)
        control_layout.addLayout(zoom_controls)
        root.addWidget(control_panel)

        pair_row = QHBoxLayout()
        pair_row.setSpacing(12)
        self.left_preview = ImagePreviewWidget(
            "Left Image",
            max_width=self.config.pair_preview_max_width,
            max_height=self.config.pair_preview_max_height,
            sync_controller=self.zoom_controller,
        )
        self.right_preview = ImagePreviewWidget(
            "Right Image",
            max_width=self.config.pair_preview_max_width,
            max_height=self.config.pair_preview_max_height,
            sync_controller=self.zoom_controller,
        )
        pair_row.addWidget(self.left_preview, 1)
        pair_row.addWidget(self.right_preview, 1)
        root.addLayout(pair_row, 1)

        self.pair_meta_label = QLabel("")
        self.pair_meta_label.setObjectName("pairMetaLabel")
        self.pair_meta_label.setWordWrap(True)
        root.addWidget(self.pair_meta_label)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.left_button = QPushButton("Left Better [A]")
        self.left_button.setObjectName("primaryActionButton")
        self.left_button.clicked.connect(lambda: self._commit_decision("left_better"))
        actions.addWidget(self.left_button)

        self.tie_button = QPushButton("Tie [S]")
        self.tie_button.setObjectName("secondaryActionButton")
        self.tie_button.clicked.connect(lambda: self._commit_decision("tie"))
        actions.addWidget(self.tie_button)

        self.right_button = QPushButton("Right Better [D]")
        self.right_button.setObjectName("primaryActionButton")
        self.right_button.clicked.connect(lambda: self._commit_decision("right_better"))
        actions.addWidget(self.right_button)

        self.skip_button = QPushButton("Skip [Space]")
        self.skip_button.setObjectName("secondaryActionButton")
        self.skip_button.clicked.connect(lambda: self._commit_decision("skip"))
        actions.addWidget(self.skip_button)
        root.addLayout(actions)

        self._install_shortcuts()
        self._load_next_pair()

    def _install_shortcuts(self) -> None:
        """Register keyboard shortcuts for quick annotation."""

        shortcuts = [
            QShortcut(QKeySequence("A"), self, activated=lambda: self._commit_decision("left_better")),
            QShortcut(QKeySequence("S"), self, activated=lambda: self._commit_decision("tie")),
            QShortcut(QKeySequence("D"), self, activated=lambda: self._commit_decision("right_better")),
            QShortcut(QKeySequence("Space"), self, activated=lambda: self._commit_decision("skip")),
        ]
        for shortcut in shortcuts:
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)

    def refresh_progress(self) -> None:
        """Refresh progress text from the session."""

        summary = self.session.progress_summary()
        self.progress_label.setText(
            f"Pairs saved {summary['labeled_pairs']} · Pool {summary['total_cluster_pairs']}"
        )

    def _load_next_pair(self) -> None:
        """Load the next pair according to the active mode."""

        source_mode = str(self.mode_combo.currentData())
        self.singletons_only_checkbox.setEnabled(source_mode == "arbitrary_pair")
        candidate = self.session.next_pair(
            source_mode=source_mode,
            singletons_only=self.singletons_only_checkbox.isChecked(),
        )
        self.current_candidate = candidate
        self.left_preview.set_image(candidate.image_a if candidate else None)
        self.right_preview.set_image(candidate.image_b if candidate else None)
        self.zoom_controller.reset()

        enabled = candidate is not None
        for button in (self.left_button, self.right_button, self.tie_button, self.skip_button):
            button.setEnabled(enabled)

        if candidate is None:
            self.pair_meta_label.setText("No more unlabeled pairs available for this mode.")
        else:
            cluster_text = candidate.cluster_id or "none"
            self.pair_meta_label.setText(
                f"Source <b>{candidate.source_mode.replace('_', ' ')}</b> · "
                f"Group <b>{cluster_text}</b>"
            )

        self.refresh_progress()

    def _commit_decision(self, decision: str) -> None:
        """Persist the current pairwise decision and advance to the next pair."""

        if self.current_candidate is None:
            return

        self.session.save_pair_label(self.current_candidate, decision=decision)
        self.on_progress_changed()
        self._load_next_pair()


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

        self.cluster_guidance_label = QLabel(
            "Choose exactly one Best image in every cluster. Even if the whole set is weak, "
            "pick the strongest frame. If several feel equally good, still choose the single "
            "best training example and mark the rest Acceptable or Reject."
        )
        self.cluster_guidance_label.setObjectName("hintText")
        self.cluster_guidance_label.setWordWrap(True)
        control_layout.addWidget(self.cluster_guidance_label)

        zoom_controls = QHBoxLayout()
        zoom_controls.setSpacing(10)
        self.cluster_zoom_help_label = QLabel(
            "Active image: use A=Best, S=Acceptable, D=Reject. There must be exactly one Best "
            "image before the cluster can be saved. Mouse-wheel zoom or drag in any preview "
            "syncs across the cluster."
        )
        self.cluster_zoom_help_label.setObjectName("hintText")
        self.cluster_zoom_help_label.setWordWrap(True)
        zoom_controls.addWidget(self.cluster_zoom_help_label, 1)

        self.cluster_zoom_out_button = QPushButton("Zoom Out")
        self.cluster_zoom_out_button.setObjectName("secondaryActionButton")
        self.cluster_zoom_out_button.clicked.connect(self.zoom_controller.zoom_out)
        zoom_controls.addWidget(self.cluster_zoom_out_button)

        self.cluster_reset_zoom_button = QPushButton("Reset Zoom")
        self.cluster_reset_zoom_button.setObjectName("secondaryActionButton")
        self.cluster_reset_zoom_button.clicked.connect(self.zoom_controller.reset)
        zoom_controls.addWidget(self.cluster_reset_zoom_button)

        self.cluster_zoom_in_button = QPushButton("Zoom In")
        self.cluster_zoom_in_button.setObjectName("secondaryActionButton")
        self.cluster_zoom_in_button.clicked.connect(self.zoom_controller.zoom_in)
        zoom_controls.addWidget(self.cluster_zoom_in_button)
        control_layout.addLayout(zoom_controls)
        root.addWidget(control_panel)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("clusterScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
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
            QShortcut(QKeySequence("A"), self, activated=lambda: self._assign_active_card("best")),
            QShortcut(
                QKeySequence("S"),
                self,
                activated=lambda: self._assign_active_card("acceptable"),
            ),
            QShortcut(QKeySequence("D"), self, activated=lambda: self._assign_active_card("reject")),
        ]
        for shortcut in shortcuts:
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)

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

        if not self.clusters:
            return False

        assignments = {card.image.image_id: card.assignment() for card in self.cards}
        unlabeled = [image_id for image_id, label in assignments.items() if label == "unlabeled"]
        if unlabeled:
            QMessageBox.warning(
                self,
                "Incomplete Cluster Label",
                "Assign every image first. Then choose exactly one Best image and use "
                "Acceptable or Reject for the rest.",
            )
            return False

        best_count = sum(1 for label in assignments.values() if label == "best")
        if best_count != 1:
            QMessageBox.warning(
                self,
                "Choose One Best Image",
                "Every cluster must have exactly one Best image before saving. "
                "Even when the whole set is weak, pick the single strongest frame.",
            )
            return False

        cluster = self.clusters[self.current_index]
        self.session.save_cluster_label(cluster.cluster_id, assignments)
        self.dirty = False
        self._update_cluster_rule_ui()
        self.on_progress_changed()
        self.refresh_progress()
        return True

    def _save_and_next(self) -> None:
        """Save the current cluster and move to the next unlabeled one."""

        if not self.save_current_cluster():
            return
        next_index = self.session.next_unlabeled_cluster_index(self.current_index + 1)
        self._load_cluster(next_index, force=True)

    def _navigate(self, index: int) -> None:
        """Navigate to another cluster by index."""

        if not self.clusters:
            return

        bounded_index = max(0, min(index, len(self.clusters) - 1))
        self._load_cluster(bounded_index, force=False)

    def _navigate_next_unlabeled(self) -> None:
        """Jump to the next unlabeled cluster."""

        next_index = self.session.next_unlabeled_cluster_index(self.current_index + 1)
        self._load_cluster(next_index, force=False)

    def _on_selector_changed(self, index: int) -> None:
        """Handle direct cluster selection from the dropdown."""

        if index < 0 or index == self.current_index:
            return
        self._load_cluster(index, force=False)

    def _load_cluster(self, index: int, *, force: bool) -> None:
        """Load one cluster into the grid view."""

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
        saved_assignments = self.session.cluster_label_assignments(cluster.cluster_id)

        self.cluster_selector.blockSignals(True)
        self.cluster_selector.setCurrentIndex(self.current_index)
        self.cluster_selector.blockSignals(False)

        self._clear_cards()
        self.zoom_controller.clear_views()
        self.zoom_controller.reset()
        columns = min(self.config.cluster_grid_columns, max(1, len(cluster.members)))
        for column in range(self.config.cluster_grid_columns):
            self.grid_layout.setColumnStretch(column, 0)
            self.grid_layout.setColumnMinimumWidth(column, 0)
        for column in range(columns):
            self.grid_layout.setColumnStretch(column, 1)
        for member_index, image in enumerate(cluster.members):
            card = ClusterImageCard(
                image,
                preview_height=self.config.cluster_preview_height,
                sync_controller=self.zoom_controller,
                on_changed=self._on_card_assignment_changed,
                on_selected=self._select_card,
            )
            card.set_assignment(saved_assignments.get(image.image_id, "unlabeled"))
            row = member_index // columns
            column = member_index % columns
            self.grid_layout.addWidget(card, row, column)
            self.cards.append(card)

        self.cluster_meta_label.setText(
            f"Group <b>{cluster.cluster_id}</b> - {len(cluster.members)} image(s) - "
            f"{cluster.cluster_reason or 'manual grouping'} - "
            f"{cluster.window_kind or 'window'} / {cluster.time_window_id or 'n/a'}"
        )
        self.dirty = False
        self._set_cluster_controls_enabled(True)
        starting_index = self._next_unlabeled_card_index(0)
        self._select_card_index(starting_index if starting_index is not None else 0)
        self._update_cluster_rule_ui()
        self._update_cluster_card_geometry()
        self.refresh_progress()

    def _refresh_selector(self) -> None:
        """Refresh cluster selector labels and progress state."""

        self.cluster_selector.blockSignals(True)
        self.cluster_selector.clear()
        for cluster in self.clusters:
            suffix = "labeled" if self.session.cluster_store.has_cluster(cluster.cluster_id) else "unlabeled"
            self.cluster_selector.addItem(
                f"{cluster.cluster_id} ({len(cluster.members)}) [{suffix}]",
                cluster.cluster_id,
            )
        if self.clusters:
            self.cluster_selector.setCurrentIndex(min(self.current_index, len(self.clusters) - 1))
        self.cluster_selector.blockSignals(False)

    def _clear_all_assignments(self) -> None:
        """Clear the current cluster assignments back to unlabeled."""

        for card in self.cards:
            card.set_assignment("unlabeled")
        self._mark_dirty()

    def _assign_active_card(self, assignment: str) -> None:
        """Assign the active card and advance to the next target."""

        if not self.cards:
            return

        card = self.cards[self.active_card_index]
        if card.assignment() != assignment:
            card.set_assignment(assignment)

        if self._cluster_ready_to_save():
            self._save_and_advance_cluster_auto()
            return

        next_index = self._next_unlabeled_card_index(
            self.active_card_index + 1,
            wrap=True,
        )
        if next_index is not None:
            self._select_card_index(next_index)

    def _on_card_assignment_changed(self, card: ClusterImageCard) -> None:
        """Track assignment edits from cluster cards."""

        self._mark_dirty()
        self._select_card(card)

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

    def _best_assignment_count(self) -> int:
        """Return how many cards are currently marked Best."""

        return sum(1 for card in self.cards if card.assignment() == "best")

    def _cluster_ready_to_save(self) -> bool:
        """Return whether the current cluster satisfies the save rule."""

        return self._all_cards_labeled() and self._best_assignment_count() == 1

    def _save_and_advance_cluster_auto(self) -> None:
        """Save the current cluster and jump to the next unlabeled cluster, if any."""

        if not self.save_current_cluster():
            return

        for index in range(self.current_index + 1, len(self.clusters)):
            if not self.session.cluster_store.has_cluster(self.clusters[index].cluster_id):
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
            self._set_cluster_rule_status("warning", "Choose exactly 1 Best image")
            self.save_button.setEnabled(False)
            self.save_next_button.setEnabled(False)
            return

        unlabeled_count = sum(1 for card in self.cards if card.assignment() == "unlabeled")
        best_count = self._best_assignment_count()
        acceptable_count = sum(1 for card in self.cards if card.assignment() == "acceptable")
        reject_count = sum(1 for card in self.cards if card.assignment() == "reject")

        if best_count == 1 and unlabeled_count == 0:
            status = "success"
            text = (
                f"Ready to save - Best 1/1 - Acceptable {acceptable_count} - Reject {reject_count}"
            )
        elif best_count == 1:
            status = "info"
            text = (
                f"Best selected 1/1 - {unlabeled_count} image(s) still unlabeled"
            )
        elif best_count == 0:
            status = "warning"
            text = "Pick exactly 1 Best image before saving"
        else:
            status = "warning"
            text = f"Too many Best images selected ({best_count}) - keep exactly 1"

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
        """Resize cluster cards so one row of three fits the viewport cleanly."""

        if not self.cards:
            return

        viewport_height = self.scroll_area.viewport().height()
        if viewport_height <= 0:
            return

        layout_margins = self.grid_layout.contentsMargins()
        vertical_spacing = self.grid_layout.verticalSpacing()
        available_row_height = (
            viewport_height
            - layout_margins.top()
            - layout_margins.bottom()
            - max(0, vertical_spacing)
            - 8
        )
        preview_height = max(220, min(available_row_height - 110, 560))

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
        super().__init__()
        self.config = config
        self.session = session or LabelingSession(config)
        self._force_close_from_parent = False

        self.setWindowTitle("AI Label Collection")
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

        self.header_title = QLabel("AI Label Collection")
        self.header_title.setObjectName("labelingHeaderTitle")
        header_layout.addWidget(self.header_title)

        self.header_subtitle = QLabel(
            "Start with clusters and choose exactly one Best image per set. "
            "Use Acceptable and Reject for the rest, then use pairwise for edge-case comparisons."
        )
        self.header_subtitle.setObjectName("labelingHeaderSubtitle")
        self.header_subtitle.setWordWrap(True)
        header_layout.addWidget(self.header_subtitle)

        summary_row = QHBoxLayout()
        summary_row.setSpacing(10)
        self.total_images_badge = QLabel("")
        self.total_images_badge.setObjectName("summaryBadge")
        summary_row.addWidget(self.total_images_badge)

        self.cluster_badge = QLabel("")
        self.cluster_badge.setObjectName("summaryBadgeAccent")
        summary_row.addWidget(self.cluster_badge)

        self.pair_badge = QLabel("")
        self.pair_badge.setObjectName("summaryBadge")
        summary_row.addWidget(self.pair_badge)

        self.singleton_badge = QLabel("")
        self.singleton_badge.setObjectName("summaryBadge")
        summary_row.addWidget(self.singleton_badge)
        summary_row.addStretch(1)
        header_layout.addLayout(summary_row)
        central_layout.addWidget(header_panel)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("labelingTabs")
        self.pairwise_tab = PairwiseTab(self.session, config, self.refresh_status)
        self.cluster_tab = ClusterTab(self.session, config, self.refresh_status)
        self.tabs.addTab(self.cluster_tab, "Clusters")
        self.tabs.addTab(self.pairwise_tab, "Pairwise")
        central_layout.addWidget(self.tabs, 1)
        self.setCentralWidget(central)
        self._build_menu_bar()

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.refresh_status()

    def _build_menu_bar(self) -> None:
        """Build a lightweight menu bar for common labeling actions."""

        file_menu = self.menuBar().addMenu("&File")
        save_cluster_action = QAction("Save Cluster", self)
        save_cluster_action.setShortcut(QKeySequence.StandardKey.Save)
        save_cluster_action.triggered.connect(self.cluster_tab.save_current_cluster)
        file_menu.addAction(save_cluster_action)

        next_pair_action = QAction("Next Pair", self)
        next_pair_action.setShortcut(QKeySequence("N"))
        next_pair_action.triggered.connect(self.pairwise_tab._load_next_pair)
        file_menu.addAction(next_pair_action)
        file_menu.addSeparator()

        close_action = QAction("Close", self)
        close_action.setShortcut(QKeySequence.StandardKey.Close)
        close_action.triggered.connect(self.close)
        file_menu.addAction(close_action)

        navigate_menu = self.menuBar().addMenu("&Navigate")
        previous_cluster_action = QAction("Previous Cluster", self)
        previous_cluster_action.triggered.connect(
            lambda: self.cluster_tab._navigate(self.cluster_tab.current_index - 1)
        )
        navigate_menu.addAction(previous_cluster_action)

        next_cluster_action = QAction("Next Cluster", self)
        next_cluster_action.triggered.connect(
            lambda: self.cluster_tab._navigate(self.cluster_tab.current_index + 1)
        )
        navigate_menu.addAction(next_cluster_action)

        next_unlabeled_action = QAction("Next Unlabeled Cluster", self)
        next_unlabeled_action.triggered.connect(self.cluster_tab._navigate_next_unlabeled)
        navigate_menu.addAction(next_unlabeled_action)

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
        shortcuts_action = QAction("Labeling Shortcuts", self)
        shortcuts_action.triggered.connect(self._show_shortcuts_help)
        help_menu.addAction(shortcuts_action)

    def _active_zoom_controller(self) -> "PairZoomSyncController":
        """Return the zoom controller for the visible tab."""

        current_widget = self.tabs.currentWidget()
        if current_widget is self.pairwise_tab:
            return self.pairwise_tab.zoom_controller
        return self.cluster_tab.zoom_controller

    def _zoom_in_current_tab(self) -> None:
        self._active_zoom_controller().zoom_in()

    def _zoom_out_current_tab(self) -> None:
        self._active_zoom_controller().zoom_out()

    def _reset_zoom_current_tab(self) -> None:
        self._active_zoom_controller().reset()

    def _show_shortcuts_help(self) -> None:
        """Show a concise shortcuts guide for the labeling workflow."""

        QMessageBox.information(
            self,
            "AI Labeling Shortcuts",
            "\n".join(
                [
                    "Clusters first:",
                    "A = Best, S = Acceptable, D = Reject for the active card.",
                    "Every cluster must have exactly one Best image before it can be saved.",
                    "If the whole set is weak, still choose the single strongest frame.",
                    "",
                    "Pairwise:",
                    "A = Left better, S = Tie, D = Right better, Space = Skip.",
                    "Mouse wheel zooms both previews together, and dragging pans them together.",
                ]
            ),
        )

    def refresh_status(self) -> None:
        """Refresh global status bar and child progress labels."""

        summary = self.session.progress_summary()
        self.pairwise_tab.refresh_progress()
        self.cluster_tab.refresh_progress()
        self.total_images_badge.setText(f"Images {summary['total_images']}")
        self.cluster_badge.setText(
            f"Clusters {summary['labeled_clusters']}/{summary['total_clusters']} labeled"
        )
        self.pair_badge.setText(f"Pair labels {summary['labeled_pairs']}")
        self.singleton_badge.setText(f"Singletons {summary['singleton_images']}")
        self.status.showMessage(
            f"Labels save to {self.config.output_dir}"
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


class PreviewLabel(QLabel):
    """Label that keeps an image upright and rescales it with the viewport."""

    def __init__(
        self,
        *,
        max_width: Optional[int],
        max_height: Optional[int],
        minimum_size: QSize,
        padding: int,
    ) -> None:
        super().__init__("No preview")
        self.max_width = max_width
        self.max_height = max_height
        self.padding = padding
        self._source_pixmap = QPixmap()
        self.setObjectName("previewImageLabel")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(minimum_size)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setContentsMargins(padding, padding, padding, padding)

    def set_preview_pixmap(self, pixmap: QPixmap) -> None:
        """Set the original pixmap and rescale it to the current viewport."""

        self._source_pixmap = pixmap
        self._refresh_pixmap()

    def set_message(self, text: str) -> None:
        """Clear any image and show a text message instead."""

        self._source_pixmap = QPixmap()
        self.setPixmap(QPixmap())
        self.setText(text)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        """Keep the preview fitted when the viewport changes size."""

        super().resizeEvent(event)
        self._refresh_pixmap()

    def _refresh_pixmap(self) -> None:
        """Rescale the current pixmap to fit the label bounds."""

        if self._source_pixmap.isNull():
            return

        available_width = max(1, self.width() - (self.padding * 2))
        available_height = max(1, self.height() - (self.padding * 2))
        if self.max_width is not None:
            available_width = min(available_width, self.max_width)
        if self.max_height is not None:
            available_height = min(available_height, self.max_height)

        scaled = self._source_pixmap.scaled(
            available_width,
            available_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(scaled)
        self.setText("")


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

        self.set_shared_state(zoom_factor=self.zoom_factor * 1.2)

    def zoom_out(self) -> None:
        """Decrease shared zoom."""

        self.set_shared_state(zoom_factor=self.zoom_factor / 1.2)

    def reset(self) -> None:
        """Reset both views to fit-to-window centered mode."""

        self.set_shared_state(zoom_factor=1.0, center_ratio=(0.5, 0.5))

    def set_shared_state(
        self,
        *,
        zoom_factor: Optional[float] = None,
        center_ratio: Optional[Tuple[float, float]] = None,
    ) -> None:
        """Broadcast a new shared zoom/pan state to every registered view."""

        if self._syncing:
            return

        if zoom_factor is not None:
            self.zoom_factor = max(1.0, min(float(zoom_factor), 12.0))

        if center_ratio is not None:
            self.center_ratio = (
                max(0.0, min(float(center_ratio[0]), 1.0)),
                max(0.0, min(float(center_ratio[1]), 1.0)),
            )

        self._syncing = True
        try:
            for view in self.views:
                view.apply_shared_state(self.zoom_factor, self.center_ratio)
        finally:
            self._syncing = False


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
        try:
            self._apply_scale()
            self._center_on_ratio(center_ratio)
            self._update_interaction_mode()
        finally:
            self._applying_shared_state = False

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
        self._emit_center_change()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        """Select this preview before handling normal drag behavior."""

        if self.on_selected is not None:
            self.on_selected()
        super().mousePressEvent(event)

    def _on_scroll_changed(self, _: int) -> None:
        """Sync panning during scroll/drag updates."""

        self._emit_center_change()

    def _emit_center_change(self) -> None:
        """Push the current pan center back into the shared controller."""

        if (
            self._applying_shared_state
            or self.sync_controller._syncing
            or not self.has_preview()
        ):
            return

        self.sync_controller.set_shared_state(center_ratio=self._current_center_ratio())

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
