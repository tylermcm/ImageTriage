from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, QSize, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QMainWindow, QMessageBox, QToolButton, QWidget

from image_triage.ui.actions import format_action_tooltip
from image_triage.ui.theme import default_theme
from image_triage.window import MainWindow


class _ActionBag:
    def __getattr__(self, name: str) -> QAction:
        action = QAction(name.replace("_", " ").title())
        setattr(self, name, action)
        return action


class TopbarStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_topbar_action_uses_icon_over_small_label_layout(self) -> None:
        host = SimpleNamespace(
            TOPBAR_SLOT_BUTTON_WIDTH=MainWindow.TOPBAR_SLOT_BUTTON_WIDTH,
            TOPBAR_BUTTON_HEIGHT=MainWindow.TOPBAR_BUTTON_HEIGHT,
            TOPBAR_HOVER_MARGIN=MainWindow.TOPBAR_HOVER_MARGIN,
        )
        button = QToolButton()
        button.setText("Review")
        pixmap = QPixmap(18, 18)
        pixmap.fill(Qt.GlobalColor.white)

        MainWindow._apply_topbar_button_style(host, button, QIcon(pixmap))

        self.assertEqual(Qt.ToolButtonStyle.ToolButtonIconOnly, button.toolButtonStyle())
        self.assertEqual("appTopBarIconButton", button.objectName())
        self.assertEqual(38, button.width())
        self.assertEqual(38, button.height())
        self.assertEqual(36, MainWindow.TOPBAR_SLOT_CELL_MIN)
        self.assertEqual(4, MainWindow.TOPBAR_SLOT_SPACING)

        content = button.findChild(QWidget, "appTopBarButtonContent")
        glyph = button.findChild(QToolButton, "appTopBarGlyph")
        caption = button.findChild(QLabel, "appTopBarButtonCaption")
        self.assertIsNotNone(content)
        self.assertIsNotNone(glyph)
        self.assertIsNotNone(caption)
        self.assertEqual(2, content.x())
        self.assertEqual(2, content.y())
        self.assertEqual(34, content.width())
        self.assertEqual(34, content.height())
        self.assertEqual(22, glyph.height())
        self.assertEqual(22, glyph.iconSize().height())
        self.assertEqual(12, caption.height())
        self.assertEqual("Review", caption.text())
        self.assertEqual("Review", button.accessibleName())
        self.assertEqual(Qt.FocusPolicy.TabFocus, button.focusPolicy())
        button.show()
        self.app.processEvents()
        self.assertEqual(0, glyph.y())
        self.assertEqual(22, caption.y())
        button.hide()

    def test_search_uses_truthful_placeholder_and_keyboard_focus(self) -> None:
        field = MainWindow._build_search_field(SimpleNamespace())

        self.assertEqual("Search by content, person, or filename...", field.placeholderText())
        self.assertTrue(field.isClearButtonEnabled())

    def test_action_tooltip_uses_live_shortcut_on_its_own_line(self) -> None:
        action = QAction("Open Preview")
        action.setProperty("imageTriageBaseText", "Open Preview")
        action.setShortcut("Ctrl+Return")

        MainWindow._refresh_action_shortcut_hint(SimpleNamespace(), action)

        self.assertEqual(format_action_tooltip("Open Preview", action.shortcut()), action.toolTip())
        self.assertEqual("Open Preview\nShortcut: Ctrl+Return", action.toolTip())

    def test_fluent_icon_has_theme_specific_interaction_states(self) -> None:
        host = SimpleNamespace(_theme=default_theme())

        icon = MainWindow._fluent_toolbar_icon(host, "E710")

        normal = icon.pixmap(QSize(64, 64), QIcon.Mode.Normal, QIcon.State.Off).toImage()
        active = icon.pixmap(QSize(64, 64), QIcon.Mode.Active, QIcon.State.Off).toImage()
        checked = icon.pixmap(QSize(64, 64), QIcon.Mode.Normal, QIcon.State.On).toImage()
        disabled = icon.pixmap(QSize(64, 64), QIcon.Mode.Disabled, QIcon.State.Off).toImage()
        self.assertNotEqual(normal.cacheKey(), active.cacheKey())
        self.assertNotEqual(normal.cacheKey(), checked.cacheKey())
        self.assertNotEqual(normal.cacheKey(), disabled.cacheKey())

    def test_legacy_toolbar_preferences_normalize_to_fixed_style(self) -> None:
        for saved_style in ("text", "icons", "large_icons", "icon_text", None):
            with self.subTest(saved_style=saved_style):
                self.assertEqual("icon_subtext", MainWindow._normalize_toolbar_style(saved_style))

    def test_topbar_icon_trims_internal_transparent_padding(self) -> None:
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.fillRect(20, 22, 18, 16, QColor("white"))
        painter.end()

        trimmed = MainWindow._trim_icon_transparency(QIcon(pixmap), padding=3)

        self.assertEqual([trimmed.availableSizes()[0].width(), trimmed.availableSizes()[0].height()], [24, 22])

    def test_toolbar_edit_banner_is_centered_below_topbar(self) -> None:
        parent = QWidget()
        parent.resize(1000, 700)
        topbar = QWidget(parent)
        topbar.setGeometry(0, 0, 1000, 52)
        hud = QFrame(parent)
        hud.resize(280, 44)
        host = SimpleNamespace(
            _toolbar_edit_hud=hud,
            _toolbar_edit_mode=True,
            app_top_bar=topbar,
        )

        MainWindow._position_toolbar_edit_hud(host)

        self.assertEqual(360, hud.x())
        self.assertEqual(61, hud.y())

    def test_toolbar_edit_banner_keeps_and_clamps_user_position(self) -> None:
        parent = QWidget()
        parent.resize(500, 300)
        hud = QFrame(parent)
        hud.resize(280, 44)
        host = SimpleNamespace(
            _toolbar_edit_hud=hud,
            _toolbar_edit_mode=True,
            _toolbar_edit_hud_user_position=QPoint(420, 275),
            app_top_bar=None,
        )

        MainWindow._position_toolbar_edit_hud(host)

        self.assertEqual(QPoint(212, 248), hud.pos())
        self.assertEqual(hud.pos(), host._toolbar_edit_hud_user_position)

    def test_toolbar_edit_marker_drags_banner(self) -> None:
        parent = QWidget()
        parent.resize(700, 400)
        hud = QFrame(parent)
        hud.setGeometry(100, 60, 280, 44)
        handle = QFrame(hud)
        handle.setGeometry(8, 10, 4, 24)
        host = SimpleNamespace(
            _toolbar_edit_hud=hud,
            _toolbar_edit_hud_drag_handle=handle,
            _toolbar_edit_hud_drag_offset=None,
            _toolbar_edit_hud_user_position=None,
            _toolbar_item_picker_dialog=None,
        )
        local = QPoint(2, 8)
        press_global = handle.mapToGlobal(local)
        press = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(local),
            QPointF(press_global),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.assertTrue(MainWindow._handle_toolbar_edit_hud_drag(host, press))

        offset = handle.mapTo(hud, local)
        target = QPoint(300, 180)
        move_global = parent.mapToGlobal(target + offset)
        move = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(local),
            QPointF(move_global),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.assertTrue(MainWindow._handle_toolbar_edit_hud_drag(host, move))
        self.assertEqual(target, hud.pos())
        self.assertEqual(target, host._toolbar_edit_hud_user_position)

        release = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(local),
            QPointF(move_global),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.assertTrue(MainWindow._handle_toolbar_edit_hud_drag(host, release))
        self.assertIsNone(host._toolbar_edit_hud_drag_offset)

    def test_toolbar_reset_requires_explicit_confirmation(self) -> None:
        calls = []
        original_warning = QMessageBox.warning
        QMessageBox.warning = lambda *args: calls.append(args) or QMessageBox.StandardButton.Cancel
        try:
            host = SimpleNamespace(
                _toolbar_edit_active_mode="manual",
                _end_inplace_toolbar_edit=lambda: self.fail("reset continued after cancellation"),
            )
            MainWindow._reset_inplace_toolbar_edit(host)
        finally:
            QMessageBox.warning = original_warning

        self.assertEqual(1, len(calls))
        self.assertEqual("Reset Toolbar Layout?", calls[0][1])
        self.assertTrue(calls[0][3] & QMessageBox.StandardButton.Reset)
        self.assertTrue(calls[0][3] & QMessageBox.StandardButton.Cancel)
        self.assertEqual(QMessageBox.StandardButton.Cancel, calls[0][4])

    def test_toolbar_slot_model_uses_the_cell_previously_reserved_for_add(self) -> None:
        host = SimpleNamespace(
            TOPBAR_SLOT_COUNT=4,
            TOPBAR_REPEATABLE_ITEMS=frozenset(),
            _is_cluster_item=lambda value: isinstance(value, str) and bool(value),
        )

        slots = MainWindow._items_to_slots(host, ["one", "two", "three", "four"])

        self.assertEqual(["one", "two", "three", "four"], slots)

    def test_toolbar_drag_can_target_the_final_edit_cell(self) -> None:
        host = SimpleNamespace(
            _toolbar_edit_cell_width=40.0,
            _toolbar_edit_visible_cell_count=4,
            _topbar_visible_slot_count=lambda: 4,
        )

        self.assertEqual(3, MainWindow._slot_at_x(host, 159.0))

    def test_toolbar_catalog_exposes_current_workflows(self) -> None:
        expected = {
            "new_folder",
            "open_preview",
            "rename_selection",
            "move_selection_to_new_folder",
            "restore_selection",
            "zen_mode",
            "winner_ladder_mode",
            "quick_rerank_ai_culling",
            "manage_people",
            "show_ai_review_summary",
            "taste_calibration",
            "dispute_current_ai_result",
            "review_ai_disagreements",
            "projects",
            "catalog",
            "save_filter_preset",
        }
        allowed = set().union(*map(set, MainWindow.WORKSPACE_TOOLBAR_ALLOWED_ITEMS.values()))

        self.assertTrue(expected.issubset(allowed))
        self.assertTrue(expected.issubset(MainWindow.WORKSPACE_TOOLBAR_ITEM_LABELS))
        self.assertTrue(expected.issubset(MainWindow.WORKSPACE_TOOLBAR_FLUENT_ICONS))
        self.assertIn("quick_filter", allowed)
        self.assertIn("quick_filter", MainWindow.TOPBAR_PICKER_HIDDEN_ITEMS)

        host = SimpleNamespace(actions=_ActionBag())
        action_specs = MainWindow._workspace_toolbar_action_specs(host)
        popup_items = {"projects", "catalog"}
        self.assertTrue((expected - popup_items).issubset(action_specs))

    def test_toolbar_group_menus_include_new_workflows(self) -> None:
        host = QMainWindow()
        host.actions = _ActionBag()

        review_menu = MainWindow._build_review_toolbar_menu(host)
        projects_menu = MainWindow._build_projects_toolbar_menu(host)
        catalog_menu = MainWindow._build_catalog_toolbar_menu(host)

        self.assertIn(host.actions.open_preview, review_menu.actions())
        self.assertIn(host.actions.winner_ladder_mode, review_menu.actions())
        self.assertIn(host.actions.create_virtual_collection, projects_menu.actions())
        self.assertIn(host.actions.browse_catalog, catalog_menu.actions())

    def test_toolbar_remove_badge_sits_on_the_button_top_right(self) -> None:
        parent = QWidget()
        button = QToolButton(parent)
        button.setGeometry(40, 30, 38, 38)
        badge = QToolButton(parent)
        badge.setFixedSize(12, 12)

        MainWindow._position_toolbar_edit_badge(badge, button)

        expected_corner = button.mapTo(parent, QPoint(button.width(), 0))
        self.assertEqual(expected_corner.x() - 6, badge.x())
        self.assertEqual(expected_corner.y() - 6, badge.y())


if __name__ == "__main__":
    unittest.main()
