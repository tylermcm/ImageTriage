from __future__ import annotations

import json
import unittest

from PySide6.QtCore import QByteArray, QRect

from image_triage.ui.layout_state import restore_window_layout, save_window_layout


class _SettingsStub:
    def __init__(self) -> None:
        self._values: dict[str, object] = {}

    def setValue(self, key: str, value: object) -> None:
        self._values[key] = value

    def value(self, key: str, default=None, _type=None):
        return self._values.get(key, default)

    def remove(self, key: str) -> None:
        self._values.pop(key, None)


class _WorkspaceDockStub:
    def __init__(self) -> None:
        self.restored_payload: dict[str, object] | None = None

    def save_state(self) -> dict[str, object]:
        return {"panels": {"library": {"visible": True}}}

    def restore_state(self, payload: dict[str, object]) -> bool:
        self.restored_payload = payload
        return True


class _WindowStub:
    def __init__(self) -> None:
        self.saved_geometry = QByteArray(b"geometry")
        self.normal_rect = QRect(12, 34, 960, 720)
        self.restore_geometry_calls: list[QByteArray] = []
        self.set_geometry_calls: list[QRect] = []
        self._maximized = False
        self._fullscreen = False
        self.maximized_after_restore = False
        self.fullscreen_after_restore = False

    def saveGeometry(self) -> QByteArray:
        return self.saved_geometry

    def restoreGeometry(self, geometry: QByteArray) -> bool:
        self.restore_geometry_calls.append(geometry)
        self._maximized = self.maximized_after_restore
        self._fullscreen = self.fullscreen_after_restore
        return True

    def normalGeometry(self) -> QRect:
        return QRect(self.normal_rect)

    def setGeometry(self, rect: QRect) -> None:
        self.set_geometry_calls.append(QRect(rect))

    def isMaximized(self) -> bool:
        return self._maximized

    def isFullScreen(self) -> bool:
        return self._fullscreen


class LayoutStateTests(unittest.TestCase):
    def test_save_window_layout_persists_window_state_and_normal_geometry(self) -> None:
        settings = _SettingsStub()
        docks = _WorkspaceDockStub()
        window = _WindowStub()
        window._maximized = True

        save_window_layout(window, settings, "window/geometry", "window/state", docks)

        self.assertEqual(settings.value("window/geometry"), window.saved_geometry)
        payload = json.loads(settings.value("window/state"))
        self.assertEqual(payload["window_state"], "maximized")
        self.assertEqual(payload["normal_geometry"], [12, 34, 960, 720])
        self.assertEqual(payload["workspace"], {"panels": {"library": {"visible": True}}})

    def test_restore_window_layout_prefers_saved_normal_geometry_and_window_state(self) -> None:
        settings = _SettingsStub()
        settings.setValue("window/geometry", QByteArray(b"legacy"))
        settings.setValue(
            "window/state",
            json.dumps(
                {
                    "version": 2,
                    "window_state": "maximized",
                    "normal_geometry": [20, 40, 1280, 800],
                    "workspace": {"panels": {"inspector": {"visible": True}}},
                }
            ),
        )
        docks = _WorkspaceDockStub()
        window = _WindowStub()

        restored, window_state = restore_window_layout(window, settings, "window/geometry", "window/state", docks)

        self.assertTrue(restored)
        self.assertEqual(window_state, "maximized")
        self.assertEqual(window.set_geometry_calls, [QRect(20, 40, 1280, 800)])
        self.assertEqual(window.restore_geometry_calls, [])
        self.assertEqual(docks.restored_payload, {"panels": {"inspector": {"visible": True}}})

    def test_restore_window_layout_infers_legacy_maximized_state_from_geometry(self) -> None:
        settings = _SettingsStub()
        settings.setValue("window/geometry", QByteArray(b"legacy"))
        settings.setValue("window/state", json.dumps({"version": 2, "workspace": {}}))
        docks = _WorkspaceDockStub()
        window = _WindowStub()
        window.maximized_after_restore = True

        restored, window_state = restore_window_layout(window, settings, "window/geometry", "window/state", docks)

        self.assertTrue(restored)
        self.assertEqual(window_state, "maximized")
        self.assertEqual(window.restore_geometry_calls, [QByteArray(b"legacy")])


if __name__ == "__main__":
    unittest.main()
