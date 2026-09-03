from __future__ import annotations

import sqlite3
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..people_search import (
    assign_person_name,
    cluster_face_identities,
    ensure_people_search_schema,
    list_face_identities,
    list_person_clusters,
)
from ..quality.store import ensure_faces_table


class PeopleSearchDialog(QDialog):
    def __init__(self, db_path: str | Path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("People")
        self.setModal(True)
        self.resize(560, 420)
        self._db_path = Path(db_path)
        self._connection: sqlite3.Connection | None = sqlite3.connect(self._db_path)
        self._connection.row_factory = sqlite3.Row
        ensure_faces_table(self._connection)
        ensure_people_search_schema(self._connection)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        self.summary_label = QLabel("", self)
        self.summary_label.setObjectName("mutedText")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(("Cluster", "Name", "Faces"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 88)
        self.table.setColumnWidth(1, 260)
        layout.addWidget(self.table, 1)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        self.rebuild_button = QPushButton("Rebuild Clusters", self)
        self.rebuild_button.clicked.connect(self._rebuild_clusters)
        action_row.addWidget(self.rebuild_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        button_box = QDialogButtonBox(self)
        self.save_button = QPushButton("Save Names", self)
        self.close_button = QPushButton("Close", self)
        button_box.addButton(self.save_button, QDialogButtonBox.ButtonRole.AcceptRole)
        button_box.addButton(self.close_button, QDialogButtonBox.ButtonRole.RejectRole)
        button_box.accepted.connect(self._save_and_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self._load_clusters()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._close_connection()
        super().closeEvent(event)

    def reject(self) -> None:  # type: ignore[override]
        self._close_connection()
        super().reject()

    def accept(self) -> None:  # type: ignore[override]
        self._close_connection()
        super().accept()

    def _close_connection(self) -> None:
        connection = self._connection
        self._connection = None
        if connection is not None:
            connection.close()

    def _load_clusters(self) -> None:
        if self._connection is None:
            return
        clusters = list_person_clusters(self._connection)
        identities = list_face_identities(self._connection)
        self.table.setRowCount(len(clusters))
        for row, cluster in enumerate(clusters):
            cluster_item = QTableWidgetItem(str(cluster.cluster_id))
            cluster_item.setFlags(cluster_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            name_item = QTableWidgetItem(cluster.name)
            faces_item = QTableWidgetItem(str(cluster.face_count))
            faces_item.setFlags(faces_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 0, cluster_item)
            self.table.setItem(row, 1, name_item)
            self.table.setItem(row, 2, faces_item)
        named = sum(1 for cluster in clusters if cluster.name.strip())
        if identities and clusters:
            self.summary_label.setText(f"{len(clusters)} people cluster(s), {named} named, from {len(identities)} detected face identity vector(s).")
        elif identities:
            self.summary_label.setText(f"{len(identities)} face identity vector(s) found. Rebuild clusters before assigning names.")
        else:
            self.summary_label.setText(
                "No face identity vectors found. Install or update the InsightFace models from AI Setup, "
                "then run Index & Score for this folder."
            )

    def _rebuild_clusters(self) -> None:
        self.rebuild_button.setEnabled(False)
        self.summary_label.setText("Rebuilding people clusters...")
        try:
            if self._connection is None:
                return
            cluster_face_identities(self._connection)
            self._connection.commit()
            self._load_clusters()
        finally:
            self.rebuild_button.setEnabled(True)

    def _save_and_accept(self) -> None:
        if self._connection is None:
            self.accept()
            return
        for row in range(self.table.rowCount()):
            cluster_item = self.table.item(row, 0)
            name_item = self.table.item(row, 1)
            if cluster_item is None:
                continue
            try:
                cluster_id = int(cluster_item.text())
            except ValueError:
                continue
            name = name_item.text().strip() if name_item is not None else ""
            assign_person_name(self._connection, cluster_id, name)
        self._connection.commit()
        self.accept()
