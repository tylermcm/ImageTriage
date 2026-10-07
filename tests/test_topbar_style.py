from __future__ import annotations

import os
import unittest

import pytest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QToolButton, QWidget

from image_triage.appearance_controller import AppearanceController
from image_triage.projects_controller import ProjectsController
from image_triage.settings_controller import SettingsController
from image_triage.toolbar_controller import ToolbarController
from image_triage.ui.actions import format_action_tooltip
from image_triage.ui.theme import build_app_stylesheet, default_theme
from image_triage.ui.display_metrics import STANDARD_DISPLAY
from image_triage.ui.toolbar_menus import ToolbarMenuController
from image_triage.window import MainWindow
from tests.harness import controller_over


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
            _window=SimpleNamespace(
                TOPBAR_SLOT_BUTTON_WIDTH=MainWindow.TOPBAR_SLOT_BUTTON_WIDTH,
                TOPBAR_BUTTON_HEIGHT=MainWindow.TOPBAR_BUTTON_HEIGHT,
                TOPBAR_HOVER_MARGIN=MainWindow.TOPBAR_HOVER_MARGIN,
            ),
            toolbar_profile=lambda: STANDARD_DISPLAY,
        )
        button = QToolButton()
        button.setText("Review")
        pixmap = QPixmap(18, 18)
        pixmap.fill(Qt.GlobalColor.white)

        ToolbarController.apply_topbar_button_style(host, button, QIcon(pixmap))

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
        field = controller_over(ProjectsController, SimpleNamespace(), "_projects").build_search_field()

        self.assertEqual("Search by content, person, or filename...", field.placeholderText())
        self.assertTrue(field.isClearButtonEnabled())

    def test_action_tooltip_uses_live_shortcut_on_its_own_line(self) -> None:
        action = QAction("Open Preview")
        action.setProperty("imageTriageBaseText", "Open Preview")
        action.setShortcut("Ctrl+Return")

        SettingsController.refresh_action_shortcut_hint(SimpleNamespace(), action)

        self.assertEqual(format_action_tooltip("Open Preview", action.shortcut()), action.toolTip())
        self.assertEqual("Open Preview\nShortcut: Ctrl+Return", action.toolTip())

    @pytest.mark.xfail(strict=True, reason='WI-0.5: hand-built stub lacks _render_fluent_glyphs the real MainWindow now has; replace with the real-window harness')

    def test_fluent_icon_has_theme_specific_interaction_states(self) -> None:
        host = SimpleNamespace(_theme=default_theme())

        icon = ToolbarController.fluent_toolbar_icon(host, "E710")

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

    def test_pane_toggle_icons_are_true_mirrors_with_bright_checked_panel(self) -> None:
        host = SimpleNamespace(_window=SimpleNamespace(_theme=default_theme()))
        left_icon = AppearanceController.pane_toggle_icon(host, "left")
        right_icon = AppearanceController.pane_toggle_icon(host, "right")
        left = left_icon.pixmap(QSize(64, 64), QIcon.Mode.Normal, QIcon.State.On).toImage()
        right = right_icon.pixmap(QSize(64, 64), QIcon.Mode.Normal, QIcon.State.On).toImage()

        for y in range(64):
            for x in range(64):
                self.assertEqual(left.pixelColor(x, y), right.pixelColor(63 - x, y))

        unchecked = left_icon.pixmap(QSize(64, 64), QIcon.Mode.Normal, QIcon.State.Off).toImage()
        checked_brightness = sum(
            left.pixelColor(x, y).lightness() for y in range(16, 47) for x in range(12, 25)
        )
        unchecked_brightness = sum(
            unchecked.pixelColor(x, y).lightness() for y in range(16, 47) for x in range(12, 25)
        )
        self.assertGreater(checked_brightness, unchecked_brightness)

    def test_pane_and_update_selected_states_have_no_persistent_fill(self) -> None:
        stylesheet = build_app_stylesheet(default_theme())

        self.assertIn(
            'QToolButton#appTopBarPaneButton:checked {\n            background-color: transparent;',
            stylesheet,
        )
        self.assertIn(
            'QToolButton#updateDownloadButton[updateAvailable="true"] {\n            background-color: transparent;',
            stylesheet,
        )

    def test_toolbar_slot_model_uses_the_cell_previously_reserved_for_add(self) -> None:
        host = SimpleNamespace(
            TOPBAR_SLOT_COUNT=4,
            TOPBAR_REPEATABLE_ITEMS=frozenset(),
            _is_cluster_item=lambda value: isinstance(value, str) and bool(value),
        )

        slots = controller_over(ProjectsController, host, "_projects", is_cluster_item=host._is_cluster_item).items_to_slots(
            ["one", "two", "three", "four"]
        )

        self.assertEqual(["one", "two", "three", "four"], slots)

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

        host = SimpleNamespace(_window=SimpleNamespace(actions=_ActionBag()))
        action_specs = ToolbarController.workspace_toolbar_action_specs(host)
        popup_items = {"projects", "catalog"}
        self.assertTrue((expected - popup_items).issubset(action_specs))

    def test_toolbar_group_menus_include_new_workflows(self) -> None:
        host = QMainWindow()
        host.actions = _ActionBag()
        host._toolbar_menus = ToolbarMenuController(host, host.actions)

        review_menu = host._toolbar_menus.build_review_toolbar_menu()
        projects_menu = host._toolbar_menus.build_projects_toolbar_menu()
        catalog_menu = host._toolbar_menus.build_catalog_toolbar_menu()

        self.assertIn(host.actions.open_preview, review_menu.actions())
        self.assertIn(host.actions.winner_ladder_mode, review_menu.actions())
        self.assertIn(host.actions.create_virtual_collection, projects_menu.actions())
        self.assertIn(host.actions.browse_catalog, catalog_menu.actions())

if __name__ == "__main__":
    unittest.main()
