from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from image_triage.editor_render import (
    CpuEditorRenderBackend,
    EditorRenderService,
    _scaled_recipe_for_render,
)
from image_triage.ui.photo_editor_panel import EditRecipe


def _app() -> QApplication:
    app = QApplication.instance()
    return app or QApplication([])


def _solid(value: int, size: int = 8) -> QImage:
    img = QImage(size, size, QImage.Format.Format_RGB32)
    img.fill(value)
    return img


class _GatedBackend:
    """Backend whose render() blocks per request until released, so tests can
    deterministically interleave completions with new requests. Each request is
    tagged by its base_key; raising tags simulate worker exceptions."""

    def __init__(self, raise_tags: set | None = None) -> None:
        self.started: list = []
        self.gates: dict = defaultdict(threading.Event)
        self.raise_tags = raise_tags or set()
        self._lock = threading.Lock()

    def render(
        self, base_image, recipe, masked, *, base_key=None, background=None,
        lensblur=None, view=None,
    ):
        with self._lock:
            self.started.append(base_key)
        self.gates[base_key].wait(timeout=5.0)
        if base_key in self.raise_tags:
            raise RuntimeError(f"boom {base_key}")
        # Distinct non-null image per request (identity checked via source_key).
        return _solid(0x00FF00)

    def release(self, tag) -> None:
        self.gates[tag].set()

    def has_started(self, tag) -> bool:
        with self._lock:
            return tag in self.started


def _pump(condition, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        QCoreApplication.processEvents()
        if condition():
            return True
        time.sleep(0.003)
    QCoreApplication.processEvents()
    return condition()


class EditorRenderServiceRaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _app()

    def _service(self, backend):
        svc = EditorRenderService(backend)
        delivered: list = []
        svc.rendered.connect(lambda seq, key, img: delivered.append(key))
        return svc, delivered

    def _req(self, svc, tag):
        svc.request(_solid(0), None, [], base_key=tag, source_key=tag)

    def test_stale_completion_is_dropped_when_newer_is_pending(self) -> None:
        backend = _GatedBackend()
        svc, delivered = self._service(backend)
        self._req(svc, ("A",))
        self.assertTrue(_pump(lambda: backend.has_started(("A",))))
        # B arrives while A is still in flight -> B becomes pending.
        self._req(svc, ("B",))
        backend.release(("A",))  # A finishes first, but it is now stale
        self.assertTrue(_pump(lambda: backend.has_started(("B",))))
        backend.release(("B",))
        self.assertTrue(_pump(lambda: delivered))
        # Only the newest (B) is ever delivered; A is suppressed.
        self.assertEqual([("B",)], delivered)

    def test_cancel_invalidates_the_in_flight_render(self) -> None:
        backend = _GatedBackend()
        svc, delivered = self._service(backend)
        self._req(svc, ("A",))
        self.assertTrue(_pump(lambda: backend.has_started(("A",))))
        svc.cancel()  # reset while A is running
        backend.release(("A",))
        _pump(lambda: False, timeout=0.4)  # give A's completion a chance to arrive
        self.assertEqual([], delivered)  # A must never be delivered after cancel

    def test_worker_exception_then_pending_request_succeeds(self) -> None:
        backend = _GatedBackend(raise_tags={("bad",)})
        svc, delivered = self._service(backend)
        self._req(svc, ("bad",))
        self.assertTrue(_pump(lambda: backend.has_started(("bad",))))
        self._req(svc, ("good",))  # queued behind the failing render
        backend.release(("bad",))  # raises -> null result, not delivered
        self.assertTrue(_pump(lambda: backend.has_started(("good",))))
        backend.release(("good",))
        self.assertTrue(_pump(lambda: delivered))
        self.assertEqual([("good",)], delivered)

    def test_service_survives_cancel_and_late_completion(self) -> None:
        backend = _GatedBackend()
        svc, delivered = self._service(backend)
        self._req(svc, ("A",))
        self.assertTrue(_pump(lambda: backend.has_started(("A",))))
        svc.cancel()
        # Late completion arriving after cancel must not crash or deliver.
        backend.release(("A",))
        _pump(lambda: False, timeout=0.3)
        self.assertEqual([], delivered)


class EditorRenderMaskCacheTests(unittest.TestCase):
    def test_rewritten_subtract_bitmap_invalidates_cached_group_strength(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_render_mask_") as temp_dir:
            root_path = Path(temp_dir) / "root.png"
            subtract_path = Path(temp_dir) / "subtract.png"
            Image.new("L", (16, 8), 255).save(root_path)
            Image.new("L", (16, 8), 0).save(subtract_path)
            components = [
                ("bitmap", {"assetPath": str(root_path)}, "add"),
                ("bitmap", {"assetPath": str(subtract_path)}, "subtract"),
            ]
            masked = [
                (
                    components,
                    (16, 8),
                    EditRecipe.from_dict({"exposure": 1.0}),
                )
            ]
            base = QImage(16, 8, QImage.Format.Format_RGB32)
            base.fill(QColor(64, 64, 64))
            backend = CpuEditorRenderBackend()

            before = backend.render(
                base,
                EditRecipe(),
                masked,
                base_key=("source",),
            )
            self.assertGreater(before.pixelColor(2, 4).red(), 64)

            subtract = Image.new("L", (16, 8), 0)
            subtract.paste(255, (0, 0, 8, 8))
            subtract.save(subtract_path)
            now = time.time_ns()
            os.utime(subtract_path, ns=(now, now))
            after = backend.render(
                base,
                EditRecipe(),
                masked,
                base_key=("source",),
            )

            self.assertAlmostEqual(64, after.pixelColor(2, 4).red(), delta=1)
            self.assertGreater(after.pixelColor(13, 4).red(), 64)


class BoundedEditorPreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _app()

    def test_source_space_geometry_scales_without_mutating_recipe(self) -> None:
        recipe = EditRecipe.from_dict(
            {
                "crop": (100, 50, 900, 450),
                "retouch": [
                    {
                        "id": "spot-1",
                        "kind": "clone",
                        "x": 400.0,
                        "y": 200.0,
                        "sx": 300.0,
                        "sy": 100.0,
                        "r": 40.0,
                    }
                ],
            }
        )

        scaled = _scaled_recipe_for_render(recipe, (1000, 500), (500, 250))

        self.assertEqual((50, 25, 450, 225), scaled.crop)
        self.assertEqual((200.0, 100.0), (scaled.retouch[0]["x"], scaled.retouch[0]["y"]))
        self.assertEqual((150.0, 50.0), (scaled.retouch[0]["sx"], scaled.retouch[0]["sy"]))
        self.assertEqual(20.0, scaled.retouch[0]["r"])
        self.assertEqual((100, 50, 900, 450), recipe.crop)
        self.assertEqual(400.0, recipe.retouch[0]["x"])

    def test_interactive_render_is_bounded_and_crop_stays_aligned(self) -> None:
        base = QImage(1000, 500, QImage.Format.Format_RGB32)
        base.fill(QColor(80, 120, 160))
        recipe = EditRecipe.from_dict({"crop": (100, 50, 900, 450), "exposure": 0.2})

        rendered = CpuEditorRenderBackend().render(
            base,
            recipe,
            [],
            base_key=("preview",),
            view={"source_size": (1000, 500), "max_edge": 200, "bypass_crop": False},
        )

        self.assertEqual((160, 80), (rendered.width(), rendered.height()))

    def test_export_style_render_has_no_implicit_resolution_cap(self) -> None:
        base = QImage(640, 360, QImage.Format.Format_RGB32)
        base.fill(QColor(80, 120, 160))

        rendered = CpuEditorRenderBackend().render(
            base,
            EditRecipe.from_dict({"exposure": 0.2}),
            [],
            base_key=("export",),
            view={"bypass_crop": False},
        )

        self.assertEqual((640, 360), (rendered.width(), rendered.height()))


class PreviewResetRaceTests(unittest.TestCase):
    """End-to-end: a render in flight when the user resets must not overwrite
    the base image once it finally arrives."""

    def setUp(self) -> None:
        self.app = _app()

    def test_cache_miss_queues_render_without_blocking_the_ui_thread(self) -> None:
        from image_triage.models import ImageRecord
        from image_triage.preview import FullScreenPreview, PreviewEntry

        preview = FullScreenPreview()
        base = QImage(800, 600, QImage.Format.Format_RGB32)
        base.fill(QColor(40, 90, 160))
        record = ImageRecord(path="C:/photos/frame.jpg", name="frame.jpg", size=1, modified_ns=1)
        preview._entries = [PreviewEntry(record=record, source_path=record.path)]
        preview._current_images = [base]
        preview._source_versions = [None]
        preview._current_image_display_tokens = [()]
        preview._focused_slot = 0
        preview._editor_recipe = EditRecipe.from_dict({"exposure": 1.0})
        preview._editor_recipe_version = 1
        backend = _GatedBackend()
        preview._editor_render_service._backend = backend

        started = time.perf_counter()
        shown = preview._editor_image_for_slot(0, base)
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.1)
        self.assertEqual(base.cacheKey(), shown.cacheKey())
        self.assertTrue(_pump(lambda: bool(backend.started)))
        for tag in list(backend.gates):
            backend.release(tag)
        _pump(lambda: preview._editor_render_service._active_seq is None)
        preview.close()

    def test_reset_is_not_overwritten_by_a_late_edited_render(self) -> None:
        import tempfile
        from pathlib import Path
        from PIL import Image
        from image_triage.preview import FullScreenPreview, PreviewEntry
        from image_triage.models import ImageRecord
        from image_triage.ui.photo_editor_panel import EditRecipe

        tmp = Path(tempfile.mkdtemp())
        pp = tmp / "p.png"
        Image.new("RGB", (400, 300), (40, 90, 160)).save(pp)
        st = pp.stat()
        record = ImageRecord(path=str(pp), name=pp.name, size=st.st_size, modified_ns=st.st_mtime_ns)

        preview = FullScreenPreview()
        preview.resize(700, 520)
        preview.show_entries([PreviewEntry(record=record, source_path=str(pp))])
        self.assertTrue(
            _pump(lambda: preview._current_images and not preview._current_images[0].isNull(), 15.0)
        )
        preview._focused_slot = 0
        QCoreApplication.processEvents()

        # Inject a gated backend so we control when the edited render completes.
        backend = _GatedBackend()
        preview._editor_render_service._backend = backend
        pane = preview._panes[0]

        def pane_sig():
            pm = pane.image_label.pixmap()
            if pm is None or pm.isNull():
                return None
            return bytes(pm.toImage().convertToFormat(QImage.Format.Format_RGB32).constBits())

        base_sig = pane_sig()
        self.assertIsNotNone(base_sig)

        # 1. Start an edit -> render is queued and blocks in the gated backend.
        preview._handle_editor_recipe_changed(EditRecipe.from_dict({"exposure": 1.0}))
        self.assertTrue(_pump(lambda: backend.started != []))

        # 2. Reset before the edit render finishes.
        preview._handle_editor_recipe_changed(EditRecipe())
        QCoreApplication.processEvents()
        self.assertEqual(base_sig, pane_sig(), "reset should restore the base")

        # 3. The blocked edit render now completes -- it must be dropped, not shown.
        for tag in list(backend.gates.keys()):
            backend.release(tag)
        _pump(lambda: False, timeout=0.5)
        self.assertEqual(base_sig, pane_sig(), "a late edited render must not overwrite the reset base")

        preview.close()
        preview.deleteLater()
        QCoreApplication.processEvents()


if __name__ == "__main__":
    unittest.main()
