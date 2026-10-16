"""LibraryIndexController end to end: a real catalog, the real worker process (fake
extractor), reconciling into the library index, coverage, and pause."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QStatusBar, QWidget

from image_triage.library_index_controller import ENABLED_KEY, LibraryIndexController
from image_triage.library_semantic import LibrarySemanticStore
from image_triage.library_store import LibraryStore

REPO_ROOT = Path(__file__).resolve().parents[1]


def _pump_until(condition, timeout: float = 90.0) -> bool:
    app = QApplication.instance()
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return bool(condition())


class _Host(QWidget):
    def __init__(self, temp: Path, library_store: LibraryStore, *, runtime_available: bool = True) -> None:
        super().__init__()
        self._settings = QSettings(str(temp / "settings.ini"), QSettings.Format.IniFormat)
        self._library_store = library_store
        self._current_folder = ""
        self._background_indexing_suspended = False
        clip = temp / "fake_clip.onnx"
        clip.write_bytes(b"not a real model")
        runtime = SimpleNamespace(
            root=REPO_ROOT,
            cli_entrypoint=REPO_ROOT / "aiculler" / "cli.py",
            python_executable=Path(sys.executable),
            clip_vision_model=clip,
            clip_fallback_vision_model=None,
            device="cpu",
        )
        self._ai_setup = SimpleNamespace(
            ai_runtime_available=lambda: runtime_available,
            aiculler_clip_model_available=lambda: runtime_available,
        )
        self._settings_ctl = SimpleNamespace(configured_aiculler_runtime=lambda workers=1: runtime)
        self.actions = SimpleNamespace(
            library_index_status=QAction(self), start_library_index=QAction(self), pause_library_index=QAction(self)
        )
        self._status = QStatusBar(self)

    def statusBar(self) -> QStatusBar:
        return self._status


class LibraryIndexControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="library_index_ctl_")
        self.temp = Path(self.temp_dir.name)
        self.previous_env = {key: os.environ.get(key) for key in ("IMAGE_TRIAGE_APPDATA", "IMAGE_TRIAGE_LIBRARY_INDEX_FAKE_EXTRACTOR")}
        os.environ["IMAGE_TRIAGE_APPDATA"] = str(self.temp / "appdata")
        os.environ["IMAGE_TRIAGE_LIBRARY_INDEX_FAKE_EXTRACTOR"] = "1"
        self.library_root = self.temp / "Photos"
        for folder, names in (("2024 Moab", ("a.jpg", "b.jpg")), ("2025 Zion", ("c.jpg", "d.jpg", "e.jpg"))):
            (self.library_root / folder).mkdir(parents=True)
            for index, name in enumerate(names):
                Image.new("RGB", (16, 12), (40 * index, 90, 140)).save(self.library_root / folder / name, "JPEG")
        self.library_store = LibraryStore()
        self.library_store.add_catalog_root(str(self.library_root))
        self.library_store.refresh_catalog()
        self.semantic_store = LibrarySemanticStore(self.temp / "library_semantic.sqlite3")

    def tearDown(self) -> None:
        for controller in getattr(self, "_controllers", []):
            controller.shutdown()
        for key, value in self.previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.temp_dir.cleanup()

    def _controller(self, **host_kwargs) -> tuple[_Host, LibraryIndexController]:
        host = _Host(self.temp, self.library_store, **host_kwargs)
        controller = LibraryIndexController(host, store_factory=lambda: self.semantic_store)
        controller._gate.idle_seconds = 0.0
        self._controllers = [*getattr(self, "_controllers", []), controller]
        return host, controller

    def test_start_indexes_the_whole_library_and_search_works(self) -> None:
        host, controller = self._controller()
        self.assertEqual("Library search: not indexed yet", controller.status_text())
        controller.start()
        self.assertTrue(host._settings.value(ENABLED_KEY, False, bool))
        finished = _pump_until(lambda: controller.coverage() is not None and controller.coverage().complete and controller._process is None)
        self.assertTrue(finished, controller.status_text())
        coverage = controller.coverage()
        self.assertEqual((5, 5), (coverage.total, coverage.indexed))
        self.assertIn("5 of 5 photos (100%)", host.actions.library_index_status.text())

        index = controller.vector_index()
        self.assertEqual(5, len(index))
        moab_a = str(self.library_root / "2024 Moab" / "a.jpg")
        result = index.similar_to([moab_a])
        self.assertEqual(4, len(result))
        self.assertNotIn(moab_a, result.paths)

    def test_pause_holds_the_worker_and_start_resumes(self) -> None:
        host, controller = self._controller()
        controller._gate.idle_seconds = 3600.0  # the user is "busy": the worker must start paused
        controller._window._settings.setValue(ENABLED_KEY, True)
        controller._begin_reconcile()
        self.assertTrue(_pump_until(lambda: controller._process is not None and controller.state() == "paused", 60), controller.state())
        time.sleep(1.0)
        self.app.processEvents()
        self.assertEqual(0, controller.coverage().indexed if controller.coverage() else 0)
        controller._gate.idle_seconds = 0.0
        controller.start()
        self.assertTrue(_pump_until(lambda: controller.coverage() is not None and controller.coverage().complete, 90), controller.status_text())

    def test_without_the_ai_runtime_it_says_so(self) -> None:
        host, controller = self._controller(runtime_available=False)
        controller.start()
        self.assertEqual("unavailable", controller.state())
        self.assertIn("Install the AI runtime", host.actions.library_index_status.text())


if __name__ == "__main__":
    unittest.main()
