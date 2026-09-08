"""Face Groups for the library sidebar.

A short, always-visible list of the people AuraFace found in the current folder.
Clicking one filters the grid to that face's photos, which works whether or not
anyone has named them - the filter keys off the cluster's image paths.

Deliberately not a second copy of the Tag People dialog: this shows only the top
few recurring faces and hands off to that dialog for the full set. The cap is
what keeps it cheap. A folder can hold hundreds of clusters, and decoding that
many face crops on one thread is exactly what used to stall the dialog for
15-20 seconds.
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QSizePolicy, QWidget

from ..people_search import ensure_people_search_schema, list_person_clusters
from ..quality.store import ensure_faces_table
from .people_dialog import _circular_pixmap, _CropTask, rank_faces

Bbox = tuple[float, float, float, float]

# How many faces the sidebar shows before deferring to the Tag People dialog.
MAX_FACE_GROUPS = 8
# A face needs to recur to be worth a sidebar row; one-off faces are noise here.
MIN_FACE_COUNT = 2
THUMB_PX = 34
# Rows are sized in code rather than left to the stylesheet: a QSS
# min-height is only a floor, so font size, DPI scaling or a competing
# ::item rule can all stretch a row and leave the circles floating apart.
_ROW_GAP = 4
_PLAIN_ROW_PX = 34
_EMPTY_ROW_PX = 42
# Sentinel for the row that hands off to the full Tag People dialog.
_BROWSE_ALL = "__browse_all__"


@dataclass(frozen=True)
class FaceGroup:
    key: int
    name: str
    cluster_ids: tuple[int, ...]
    face_count: int
    rep_face: tuple[str, Bbox] | None = None

    @property
    def named(self) -> bool:
        return bool(self.name.strip())

    @property
    def label(self) -> str:
        if self.named:
            return self.name.strip()
        word = "photo" if self.face_count == 1 else "photos"
        return f"Unnamed ({self.face_count} {word})"

    @property
    def filter_label(self) -> str:
        """What the grid's active-filter chip should say."""
        if self.named:
            return self.name.strip()
        word = "photo" if self.face_count == 1 else "photos"
        return f"Unnamed face ({self.face_count} {word})"


class _FaceGroupRow(QWidget):
    """Compact three-column person row matching the generated sidebar."""

    def __init__(self, name: str, count: int | None, *, all_people: bool = False) -> None:
        super().__init__()
        self._name = name
        self._count = count
        self._all_people = all_people
        self._avatar: QPixmap | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def set_avatar(self, image: QImage) -> None:
        self._avatar = _circular_pixmap(image, THUMB_PX)
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        primary = self.palette().color(QPalette.ColorRole.Text)
        secondary = self.palette().color(QPalette.ColorRole.PlaceholderText)
        if not secondary.isValid():
            secondary = QColor("#95a0b0")

        avatar_rect = QRect(8, (self.height() - THUMB_PX) // 2, THUMB_PX, THUMB_PX)
        if self._all_people:
            self._paint_people_icon(painter, avatar_rect, QColor("#5b9cff"))
        elif self._avatar is not None and not self._avatar.isNull():
            painter.drawPixmap(avatar_rect, self._avatar)
        else:
            self._paint_avatar_placeholder(painter, avatar_rect, secondary)

        arrow_width = 16
        count_width = 42 if self._count is not None else 0
        name_left = avatar_rect.right() + 12
        name_right = self.width() - 8 - arrow_width - count_width
        name_rect = QRect(name_left, 0, max(0, name_right - name_left), self.height())
        font = QFont(self.font())
        font.setPointSizeF(max(9.0, font.pointSizeF()))
        painter.setFont(font)
        painter.setPen(primary)
        label = painter.fontMetrics().elidedText(
            self._name, Qt.TextElideMode.ElideRight, name_rect.width()
        )
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)

        if self._count is not None:
            count_rect = QRect(self.width() - 8 - arrow_width - count_width, 0, count_width, self.height())
            painter.setPen(secondary)
            painter.drawText(
                count_rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(self._count)
            )

        self._paint_chevron(painter, self.width() - 10, self.height() / 2.0, secondary)
        painter.end()

    @staticmethod
    def _paint_avatar_placeholder(painter: QPainter, rect: QRect, color: QColor) -> None:
        bg = QColor(color)
        bg.setAlpha(72)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg)
        painter.drawEllipse(QRectF(rect))
        fg = QColor(color)
        fg.setAlpha(220)
        painter.setBrush(fg)
        center_x = rect.center().x()
        painter.drawEllipse(QPointF(center_x, rect.top() + rect.height() * 0.37), rect.width() * 0.14, rect.width() * 0.14)
        body = QPainterPath()
        body.addEllipse(
            QRectF(
                rect.left() + rect.width() * 0.24,
                rect.top() + rect.height() * 0.55,
                rect.width() * 0.52,
                rect.height() * 0.31,
            )
        )
        painter.drawPath(body)

    @staticmethod
    def _paint_people_icon(painter: QPainter, rect: QRect, color: QColor) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        unit = rect.width() / 34.0
        painter.drawEllipse(QPointF(rect.left() + 13 * unit, rect.top() + 11 * unit), 5 * unit, 5 * unit)
        painter.drawEllipse(QPointF(rect.left() + 24 * unit, rect.top() + 13 * unit), 4 * unit, 4 * unit)
        painter.drawRoundedRect(
            QRectF(rect.left() + 4 * unit, rect.top() + 18 * unit, 18 * unit, 12 * unit),
            5 * unit,
            5 * unit,
        )
        painter.drawRoundedRect(
            QRectF(rect.left() + 21 * unit, rect.top() + 20 * unit, 10 * unit, 9 * unit),
            4 * unit,
            4 * unit,
        )

    @staticmethod
    def _paint_chevron(painter: QPainter, x: float, y: float, color: QColor) -> None:
        painter.save()
        painter.setPen(QPen(color, 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(QPointF(x - 3, y - 4), QPointF(x + 1, y))
        painter.drawLine(QPointF(x + 1, y), QPointF(x - 3, y + 4))
        painter.restore()


def _connect(db_path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    return connection


def load_face_groups(
    db_path: str | Path,
    *,
    limit: int = MAX_FACE_GROUPS,
    min_face_count: int = MIN_FACE_COUNT,
) -> list[FaceGroup]:
    """The most-photographed people in a folder, named ones first.

    Named people lead because they are the ones the user curates by; the
    remaining slots go to the most recurring unnamed faces, which are the ones
    worth naming next.
    """
    if not Path(str(db_path)).exists():
        return []
    connection = _connect(db_path)
    try:
        ensure_faces_table(connection)
        ensure_people_search_schema(connection)
        clusters = list_person_clusters(connection)
        faces = _faces_by_cluster(connection)
    except sqlite3.DatabaseError:
        return []
    finally:
        connection.close()

    merged: dict[str, list] = {}
    people: list[FaceGroup] = []
    ordered: list[tuple[str, list[int], int]] = []
    for cluster in clusters:
        name = cluster.name.strip()
        if name and name.casefold() in merged:
            entry = merged[name.casefold()]
            entry[1].append(cluster.cluster_id)
            entry[2] += cluster.face_count
            continue
        entry = [name, [cluster.cluster_id], cluster.face_count]
        if name:
            merged[name.casefold()] = entry
        ordered.append(entry)

    for name, cluster_ids, face_count in ordered:
        if face_count < min_face_count:
            continue
        candidates: list[dict] = []
        for cluster_id in cluster_ids:
            candidates.extend(faces.get(cluster_id, []))
        ranked = rank_faces(candidates)
        rep = (ranked[0]["source"], ranked[0]["bbox"]) if ranked else None
        people.append(
            FaceGroup(
                key=cluster_ids[0],
                name=name,
                cluster_ids=tuple(cluster_ids),
                face_count=face_count,
                rep_face=rep,
            )
        )

    people.sort(key=lambda group: (not group.named, -group.face_count))
    return people[: max(0, limit)]


def face_group_photo_paths(db_path: str | Path, cluster_ids) -> list[str]:
    """Every source image the group's face appears in."""
    ids = [int(cluster_id) for cluster_id in cluster_ids]
    if not ids or not Path(str(db_path)).exists():
        return []
    placeholders = ",".join("?" for _ in ids)
    connection = _connect(db_path)
    try:
        rows = connection.execute(
            f"""
            SELECT DISTINCT images.source_path
            FROM image_faces
            JOIN images ON images.id = image_faces.image_id
            WHERE image_faces.cluster_id IN ({placeholders})
            """,
            tuple(ids),
        ).fetchall()
    except sqlite3.DatabaseError:
        return []
    finally:
        connection.close()
    return [str(row[0]) for row in rows if row[0]]


def _faces_by_cluster(connection: sqlite3.Connection) -> dict[int, list[dict]]:
    rows = connection.execute(
        """
        SELECT image_faces.cluster_id AS cid, images.source_path AS sp,
               image_faces.x1, image_faces.y1, image_faces.x2, image_faces.y2,
               image_faces.det_score AS det, image_faces.eye_sharpness AS sharp
        FROM image_faces JOIN images ON images.id = image_faces.image_id
        WHERE image_faces.cluster_id IS NOT NULL
        """
    ).fetchall()
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        grouped.setdefault(int(row["cid"]), []).append(
            {
                "source": str(row["sp"]),
                "bbox": (float(row["x1"]), float(row["y1"]), float(row["x2"]), float(row["y2"])),
                "det": float(row["det"]),
                "sharp": row["sharp"],
            }
        )
    return grouped


class FaceGroupsPanel(QListWidget):
    """The sidebar list. Owns its crop worker and cancels it on every rebuild."""

    group_activated = Signal(object)  # FaceGroup
    browse_all_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("faceGroupsList")
        self.setIconSize(QSize(THUMB_PX, THUMB_PX))
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.itemActivated.connect(self._handle_item)
        self.itemClicked.connect(self._handle_item)

        self._groups: list[FaceGroup] = []
        self._all_groups: list[FaceGroup] = []
        self._has_index = False
        self._search_text = ""
        self._thumbs: dict[int, QImage] = {}
        self._crop_task: _CropTask | None = None
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._cache_dir = tempfile.mkdtemp(prefix="face_groups_")

    # -- content -----------------------------------------------------------
    def set_groups(self, groups: list[FaceGroup], *, has_index: bool) -> None:
        self._all_groups = list(groups)
        self._has_index = has_index
        self._populate()

    def set_search_text(self, text: str) -> None:
        normalized = str(text or "").strip().casefold()
        if normalized == self._search_text:
            return
        self._search_text = normalized
        self._populate()

    def _populate(self) -> None:
        self._cancel_crops()
        self._groups = [
            group
            for group in self._all_groups
            if not self._search_text
            or self._search_text in (group.name.strip() or "Unnamed").casefold()
        ]
        self.clear()

        if not self._groups:
            message = (
                "No recurring faces\nin this folder"
                if self._has_index
                else "Face groups unavailable\nFolder not indexed"
            )
            if self._search_text and self._all_groups:
                message = "No matching people\nin this folder"
            placeholder = QListWidgetItem(message)
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            placeholder.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            placeholder.setSizeHint(QSize(0, _EMPTY_ROW_PX))
            self.addItem(placeholder)
            self._sync_height()
            return

        for group in self._groups:
            # The visible text belongs exclusively to _FaceGroupRow. Leaving it
            # on the item makes Qt paint a second label underneath the widget.
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, group.key)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, group.label)
            item.setToolTip(f"Show the {group.face_count} photos with this face")
            item.setSizeHint(QSize(0, THUMB_PX + _ROW_GAP))
            self.addItem(item)
            row = _FaceGroupRow(
                group.name.strip() or "Unnamed",
                group.face_count,
            )
            cached = self._thumbs.get(group.key)
            if cached is not None:
                row.set_avatar(cached)
            self.setItemWidget(item, row)

        browse = QListWidgetItem()
        browse.setData(Qt.ItemDataRole.UserRole, _BROWSE_ALL)
        browse.setData(Qt.ItemDataRole.AccessibleTextRole, "All people...")
        browse.setSizeHint(QSize(0, _PLAIN_ROW_PX))
        self.addItem(browse)
        self.setItemWidget(browse, _FaceGroupRow("All people...", None, all_people=True))
        self._sync_height()
        self._start_crops()

    def _sync_height(self) -> None:
        """Hug the content, but never insist on it.

        A maximum rather than a fixed height lets the section fill the pane when
        it is the expanded one and scroll internally when its rows do not fit,
        while still shrinking back to its content when there is room to spare.
        """
        # Summed rather than rows x uniform height: the "All people..." row is
        # shorter than a face row, so an average would cut the list short.
        # The items' own hints, not sizeHintForRow: the view still reports a
        # stale pre-polish height at this point, which caps the list short.
        total = sum(self.item(row).sizeHint().height() for row in range(self.count()))
        if total <= 0:
            total = THUMB_PX + _ROW_GAP
        self.setMinimumHeight(0)
        self.setMaximumHeight(total + 2 * self.frameWidth() + 2)

    # -- thumbnails --------------------------------------------------------
    def _start_crops(self) -> None:
        jobs = [
            (group.key, 0, group.rep_face[0], group.rep_face[1])
            for group in self._groups
            if group.rep_face and group.key not in self._thumbs
        ]
        if not jobs:
            return
        task = _CropTask(jobs, THUMB_PX * 3, self._cache_dir)  # 3x for crisp downscale
        task.signals.loaded.connect(self._on_crop, Qt.ConnectionType.QueuedConnection)
        self._crop_task = task
        self._pool.start(task)

    def _on_crop(self, key: int, _slot: int, image: QImage) -> None:
        self._thumbs[key] = image
        for row in range(self.count()):
            item = self.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == key:
                widget = self.itemWidget(item)
                if isinstance(widget, _FaceGroupRow):
                    widget.set_avatar(image)
                return

    def _cancel_crops(self) -> None:
        if self._crop_task is not None:
            self._crop_task.cancel()
            self._crop_task = None

    # -- interaction -------------------------------------------------------
    def _handle_item(self, item: QListWidgetItem) -> None:
        value = item.data(Qt.ItemDataRole.UserRole)
        if value == _BROWSE_ALL:
            self.browse_all_requested.emit()
            return
        for group in self._groups:
            if group.key == value:
                self.group_activated.emit(group)
                return

    def shutdown(self) -> None:
        self._cancel_crops()
        self._pool.waitForDone(2000)
        if self._cache_dir:
            shutil.rmtree(self._cache_dir, ignore_errors=True)
            self._cache_dir = ""

