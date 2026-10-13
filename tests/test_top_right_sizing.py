import os
from types import SimpleNamespace
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QMainWindow, QToolButton, QWidget
from image_triage.appearance_controller import AppearanceController


def test_top_right_controls_follow_bar_when_window_height_changes():
    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    window.app_top_bar = QWidget(window)
    window.app_top_bar.setFixedHeight(36)
    window.update_download_button = QToolButton(window)
    gear = QToolButton(window)
    control = QToolButton(window)
    window._toolbar = SimpleNamespace(app_settings_button=gear,
        _window_control_buttons={"close": control}, size_view_controls=lambda: None)
    def font_px(widget, pixels):
        font = widget.font()
        font.setPixelSize(pixels)
        widget.setFont(font)
    window._set_widget_font_px = font_px
    window._toolbar.app_menu_button = QToolButton(window)
    window._toolbar.app_breadcrumb = QWidget(window)
    window._toolbar.app_search_box = QWidget(window)
    controller = SimpleNamespace(_window=window)
    AppearanceController.apply_chrome_size_ratios(controller, 1280, 700)
    original = (gear.size(), gear.iconSize(), window.update_download_button.size(), control.font().pixelSize())
    AppearanceController.apply_chrome_size_ratios(controller, 1280, 500)
    assert original == (gear.size(), gear.iconSize(), window.update_download_button.size(), control.font().pixelSize())
    assert gear.width() >= 26
    assert gear.iconSize().width() >= 16
    assert control.font().pixelSize() >= 10
    window.app_top_bar.setFixedHeight(60)
    AppearanceController.apply_chrome_size_ratios(controller, 1280, 500)
    assert gear.width() > original[0].width()
    assert gear.iconSize().width() > original[1].width()
    assert control.font().pixelSize() > original[3]
    window.close()


def test_search_and_breadcrumb_follow_bar_instead_of_window_height():
    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    window.app_top_bar = QWidget(window)
    window.app_top_bar.setFixedHeight(36)
    window._toolbar = SimpleNamespace(app_breadcrumb=QWidget(window), app_search_box=QWidget(window))
    controller = SimpleNamespace(_window=window)
    AppearanceController.apply_chrome_text_ratios(controller, 1280, 700)
    original = (window._toolbar.app_breadcrumb.styleSheet(), window._toolbar.app_search_box.styleSheet())
    AppearanceController.apply_chrome_text_ratios(controller, 1280, 500)
    assert original == (window._toolbar.app_breadcrumb.styleSheet(), window._toolbar.app_search_box.styleSheet())
    window.app_top_bar.setFixedHeight(60)
    AppearanceController.apply_chrome_text_ratios(controller, 1280, 500)
    assert original[0] != window._toolbar.app_breadcrumb.styleSheet()
    assert original[1] != window._toolbar.app_search_box.styleSheet()
    window.close()
