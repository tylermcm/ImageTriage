"""The AI Review complete dialog and the small tag-legend widgets it shows."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QToolButton, QVBoxLayout, QWidget
from collections import Counter
from pathlib import Path

from ..ai_results import AICullBucket, ai_review_tag_definitions
from ..shell_actions import open_in_file_explorer, open_with_default


class _AIBadgePreview(QLabel):
    def __init__(self, text: str, *, background: str, foreground: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            f"""
            QLabel {{
                background-color: {background};
                color: {foreground};
                border-radius: 9px;
                padding: 4px 10px;
                font-weight: 600;
            }}
            """
        )


class _AITagSampleCard(QFrame):
    def __init__(
        self,
        *,
        left_badges: tuple[tuple[str, str, str], ...] = (),
        right_badges: tuple[tuple[str, str, str], ...] = (),
        filename: str = "_DSC0001.NEF",
        compact: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("aiTagSampleCard")
        self.setStyleSheet(
            """
            QFrame#aiTagSampleCard {
                background-color: #141922;
                border: 1px solid #364152;
                border-radius: 14px;
            }
            QFrame#aiTagSampleImage {
                background-color: #212936;
                border: 1px solid #2c3645;
                border-radius: 10px;
            }
            QLabel#aiTagSampleFilename {
                color: #b7c4d7;
                font-size: 11px;
                font-weight: 600;
            }
            """
        )
        if compact:
            self.setFixedSize(176, 108)
        else:
            self.setFixedSize(228, 136)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8 if compact else 10, 8 if compact else 10, 8 if compact else 10, 8 if compact else 10)
        layout.setSpacing(6 if compact else 8)

        image_area = QFrame(self)
        image_area.setObjectName("aiTagSampleImage")
        image_area.setMinimumHeight(68 if compact else 84)
        image_layout = QHBoxLayout(image_area)
        image_layout.setContentsMargins(8 if compact else 10, 8 if compact else 10, 8 if compact else 10, 8 if compact else 10)
        image_layout.setSpacing(6 if compact else 8)

        left_column = QVBoxLayout()
        left_column.setContentsMargins(0, 0, 0, 0)
        left_column.setSpacing(6)
        for text, background, foreground in left_badges:
            left_column.addWidget(_AIBadgePreview(text, background=background, foreground=foreground, parent=image_area), 0, Qt.AlignmentFlag.AlignLeft)
        left_column.addStretch(1)
        image_layout.addLayout(left_column, 1)

        right_column = QVBoxLayout()
        right_column.setContentsMargins(0, 0, 0, 0)
        right_column.setSpacing(6)
        for text, background, foreground in right_badges:
            right_column.addWidget(_AIBadgePreview(text, background=background, foreground=foreground, parent=image_area), 0, Qt.AlignmentFlag.AlignRight)
        right_column.addStretch(1)
        image_layout.addLayout(right_column, 1)

        layout.addWidget(image_area)

        filename_label = QLabel(filename, self)
        filename_label.setObjectName("aiTagSampleFilename")
        layout.addWidget(filename_label)


class AIReviewCompleteDialog(QDialog):
    def __init__(
        self,
        *,
        folder: str,
        hidden_root: str,
        artifacts_dir: str,
        report_dir: str,
        export_csv_path: str,
        report_html_path: str,
        bucket_counts: Counter[AICullBucket] | None = None,
        same_folder: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._hidden_root = hidden_root
        self._report_html_path = report_html_path
        self.setModal(True)
        self.setWindowTitle("AI Review Complete")
        self.resize(1120, 760)
        self.setMinimumSize(1040, 720)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(10)

        heading = QLabel("AI review finished successfully.", self)
        heading.setStyleSheet("font-size: 20px; font-weight: 700;")
        root_layout.addWidget(heading)

        summary_lines = [f"Folder: {folder}"]
        if same_folder:
            summary_lines.append("The new results were loaded automatically.")
        else:
            summary_lines.append("The review outputs were written successfully.")
        summary = QLabel("\n".join(summary_lines), self)
        summary.setWordWrap(True)
        summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root_layout.addWidget(summary)

        if bucket_counts:
            counts_row = QHBoxLayout()
            counts_row.setContentsMargins(0, 0, 0, 0)
            counts_row.setSpacing(8)
            counts_row.addWidget(QLabel("Result buckets:", self))
            counts_row.addWidget(
                _AIBadgePreview(
                    f"Winner {bucket_counts.get(AICullBucket.AI_PICK, 0)}",
                    background="rgba(70, 189, 120, 218)",
                    foreground="#fffaf2",
                    parent=self,
                )
            )
            counts_row.addWidget(
                _AIBadgePreview(
                    f"Reject {bucket_counts.get(AICullBucket.REJECT, 0)}",
                    background="rgba(214, 90, 103, 218)",
                    foreground="#fffaf2",
                    parent=self,
                )
            )
            counts_row.addWidget(
                _AIBadgePreview(
                    f"Review {bucket_counts.get(AICullBucket.NEEDS_REVIEW, 0)}",
                    background="rgba(210, 135, 53, 218)",
                    foreground="#fffaf2",
                    parent=self,
                )
            )
            counts_row.addStretch(1)
            root_layout.addLayout(counts_row)

        outputs_frame = QFrame(self)
        outputs_frame.setStyleSheet(
            """
            QFrame {
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
            }
            QToolButton[sectionToggle="true"] {
                background: transparent;
                border: none;
                color: #dbe6f5;
                font-weight: 700;
                padding: 0px;
            }
            QToolButton[sectionToggle="true"]:hover {
                color: #f4f7fb;
            }
            QLabel[outputTitle="true"] {
                color: #dbe6f5;
                font-weight: 700;
            }
            QLabel[outputDescription="true"] {
                color: #b7c4d7;
            }
            QLabel[outputPath="true"] {
                color: #9aa9bd;
            }
            QLineEdit[outputPathField="true"] {
                background-color: rgba(10, 15, 20, 0.42);
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 8px;
                color: #9aa9bd;
                min-height: 24px;
                padding: 2px 8px;
            }
            QFrame#aiOutputEntry {
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.07);
                border-radius: 10px;
            }
            QFrame#aiLegendEntry {
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.07);
                border-radius: 12px;
            }
            """
        )
        outputs_layout = QVBoxLayout(outputs_frame)
        outputs_layout.setContentsMargins(14, 12, 14, 12)
        outputs_layout.setSpacing(10)
        outputs_header = QHBoxLayout()
        outputs_header.setContentsMargins(0, 0, 0, 0)
        outputs_header.setSpacing(8)
        self.outputs_toggle = QToolButton(outputs_frame)
        self.outputs_toggle.setProperty("sectionToggle", True)
        self.outputs_toggle.setCheckable(True)
        self.outputs_toggle.setChecked(False)
        self.outputs_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.outputs_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.outputs_toggle.setText("Outputs")
        self.outputs_toggle.toggled.connect(self._set_outputs_expanded)
        outputs_header.addWidget(self.outputs_toggle)
        outputs_hint = QLabel("Show generated files and what each one is for.", outputs_frame)
        outputs_hint.setProperty("outputDescription", True)
        outputs_header.addWidget(outputs_hint, 1)
        outputs_layout.addLayout(outputs_header)

        self.outputs_body = QWidget(outputs_frame)
        self.outputs_body.setObjectName("aiReviewOutputsBody")
        outputs_grid = QGridLayout(self.outputs_body)
        outputs_grid.setContentsMargins(0, 0, 0, 0)
        outputs_grid.setHorizontalSpacing(10)
        outputs_grid.setVerticalSpacing(10)
        output_rows = (
            ("Hidden AI folder", "The folder-local AI workspace beside your images.", hidden_root),
            ("Artifacts", "Embeddings, IDs, and cluster data used by AI scoring.", artifacts_dir),
            ("Ranked export", "The scored CSV used for ranking and review.", export_csv_path),
            ("Report folder", "Summary files and reports generated for this run.", report_dir),
            ("HTML report", "The browser-friendly review report for this folder.", report_html_path),
        )
        for index, (label_text, description_text, value) in enumerate(output_rows):
            outputs_grid.addWidget(
                self._build_output_entry(label_text, description_text, value, parent=self.outputs_body),
                index // 2,
                index % 2,
            )
        outputs_grid.setColumnStretch(0, 1)
        outputs_grid.setColumnStretch(1, 1)
        self.outputs_body.setVisible(False)
        outputs_layout.addWidget(self.outputs_body)
        root_layout.addWidget(outputs_frame)

        legend_title = QLabel("AI tag guide", self)
        legend_title.setStyleSheet("font-size: 16px; font-weight: 700;")
        root_layout.addWidget(legend_title)

        legend_subtitle = QLabel(
            "Use this as the quick reference for the AI badges you just generated.",
            self,
        )
        legend_subtitle.setWordWrap(True)
        root_layout.addWidget(legend_subtitle)

        legend_host = QWidget(self)
        legend_host.setObjectName("aiReviewLegendHost")
        legend_grid = QGridLayout(legend_host)
        legend_grid.setContentsMargins(0, 0, 0, 0)
        legend_grid.setHorizontalSpacing(12)
        legend_grid.setVerticalSpacing(12)
        for index, (tag_name, description) in enumerate(ai_review_tag_definitions()):
            legend_grid.addWidget(
                self._build_tag_legend_entry(tag_name, description, parent=legend_host),
                index // 2,
                index % 2,
            )
        legend_grid.setColumnStretch(0, 1)
        legend_grid.setColumnStretch(1, 1)
        root_layout.addWidget(legend_host, 1)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok, Qt.Orientation.Horizontal, self)
        self.open_outputs_button = button_box.addButton("Open AI Output Folder", QDialogButtonBox.ButtonRole.ActionRole)
        self.open_report_button = button_box.addButton("Open Report", QDialogButtonBox.ButtonRole.ActionRole)
        self.open_outputs_button.clicked.connect(self._open_outputs_folder)
        self.open_report_button.clicked.connect(self._open_report)
        self.open_outputs_button.setEnabled(Path(hidden_root).exists())
        self.open_report_button.setEnabled(bool(report_html_path and Path(report_html_path).exists()))
        button_box.accepted.connect(self.accept)
        root_layout.addWidget(button_box)

    def _build_output_entry(self, title: str, description: str, path: str, *, parent: QWidget | None = None) -> QWidget:
        frame = QFrame(parent)
        frame.setObjectName("aiOutputEntry")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(4)
        title_label = QLabel(title, frame)
        title_label.setProperty("outputTitle", True)
        description_label = QLabel(description, frame)
        description_label.setProperty("outputDescription", True)
        description_label.setWordWrap(True)
        path_field = QLineEdit(path, frame)
        path_field.setProperty("outputPathField", True)
        path_field.setReadOnly(True)
        path_field.setCursorPosition(0)
        layout.addWidget(title_label)
        layout.addWidget(description_label)
        layout.addWidget(path_field)
        return frame

    def _build_tag_legend_entry(self, tag_name: str, description: str, *, parent: QWidget | None = None) -> QWidget:
        frame = QFrame(parent)
        frame.setObjectName("aiLegendEntry")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        layout.addWidget(self._build_tag_preview(tag_name), 0, Qt.AlignmentFlag.AlignTop)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(4)
        tag_label = QLabel(tag_name, frame)
        tag_label.setStyleSheet("font-weight: 700; font-size: 13px;")
        description_label = QLabel(description, frame)
        description_label.setWordWrap(True)
        description_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        description_label.setStyleSheet("color: #b7c4d7;")
        text_column.addWidget(tag_label)
        text_column.addWidget(description_label)
        text_column.addStretch(1)
        layout.addLayout(text_column, 1)
        return frame

    def _set_outputs_expanded(self, expanded: bool) -> None:
        self.outputs_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.outputs_body.setVisible(expanded)

    @staticmethod
    def _build_tag_preview(tag_name: str) -> QWidget:
        preview_map: dict[str, tuple[tuple[tuple[str, str, str], ...], tuple[tuple[str, str, str], ...], str]] = {
            "Winner": (
                (),
                (("Winner", "rgba(70, 189, 120, 218)", "#fffaf2"),),
                "_DSC1024.NEF",
            ),
            "Review": (
                (),
                (("Review", "rgba(210, 135, 53, 218)", "#fffaf2"),),
                "_DSC1040.NEF",
            ),
            "Reject": (
                (),
                (("Reject", "rgba(214, 90, 103, 218)", "#fffaf2"),),
                "_DSC1044.NEF",
            ),
            "AI Miss": (
                (("AI Miss", "rgba(215, 84, 122, 218)", "#fffaf2"),),
                (),
                "_DSC1063.NEF",
            ),
        }
        left_badges, right_badges, filename = preview_map.get(tag_name, ((), (), "_DSC0001.NEF"))
        return _AITagSampleCard(
            left_badges=left_badges,
            right_badges=right_badges,
            filename=filename,
            compact=True,
        )

    def _open_outputs_folder(self) -> None:
        open_in_file_explorer(self._hidden_root)

    def _open_report(self) -> None:
        if self._report_html_path:
            open_with_default(self._report_html_path)
