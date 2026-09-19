from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QToolButton

from image_triage.ui.breadcrumb import BreadcrumbBar, breadcrumb_segments


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_segments_run_from_drive_to_folder() -> None:
    assert breadcrumb_segments(r"K:\Photography\China '26") == [
        ("K:", "K:\\"),
        ("Photography", r"K:\Photography"),
        ("China '26", r"K:\Photography\China '26"),
    ]


def test_unc_share_is_one_readable_root_segment() -> None:
    segments = breadcrumb_segments(r"\\192.168.1.200\share\Trips")
    assert segments[0][0] == r"192.168.1.200\share"
    assert segments[-1] == ("Trips", r"\\192.168.1.200\share\Trips")


def test_empty_path_has_no_segments() -> None:
    assert breadcrumb_segments("") == []


def test_clicking_a_segment_reports_its_path() -> None:
    _app()
    bar = BreadcrumbBar()
    bar.resize(800, 30)
    bar.set_path(r"K:\Photography\China '26")
    clicked: list[str] = []
    bar.segment_clicked.connect(clicked.append)

    buttons = [child for child in bar.findChildren(QToolButton) if child.text() == "Photography"]
    buttons[0].click()

    assert clicked == [r"K:\Photography"]


def test_narrow_trail_collapses_leading_segments() -> None:
    _app()
    bar = BreadcrumbBar()
    bar.resize(140, 30)
    bar.set_path(r"K:\Photography\China '26\Zhangjiajie\Day Two")
    layout = bar.layout()
    shown = [layout.itemAt(index).widget() for index in range(layout.count())]
    texts = [widget.text() for widget in shown if isinstance(widget, QToolButton)]

    assert "\u2026" in texts
    assert "Day Two" in texts
