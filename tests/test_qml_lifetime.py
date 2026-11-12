"""Keep native QML wrappers usable during GC and repeated engine teardown."""
import gc

from PySide6.QtCore import QMetaObject
from PySide6.QtQuick import QQuickItem
from shiboken6 import isValid

from image_triage.qml_shell.ai_center import AiCenterBackend
from image_triage.qml_shell.cull_model import CullModel
from test_qml_shell import _load_main, pump, qapp  # noqa: F401
from test_qml_shell_ai_center import FakeOps, find, until


def test_qml_wrappers_survive_collection_and_engine_recreation(qapp, tmp_path):
    for cycle in range(3):
        engine, window, warnings = _load_main(qapp, tmp_path / str(cycle))
        try:
            window.setWidth(1440)
            window.setHeight(900)
            context = engine.rootContext()
            context.contextProperty("photos").setRows([
                {"path": str(tmp_path / "frame.jpg"), "name": "frame.jpg", "ext": "JPG",
                 "raw": False, "decision": "", "rating": 0, "ai": -1}], str(tmp_path))
            cull = CullModel(context.contextProperty("photos"), memory=False,
                             ai=AiCenterBackend(FakeOps(), parent=engine), parent=engine)
            context.setContextProperty("cull", cull)
            until(qapp, lambda: cull.view["center"]["rtState"] != "Checking…")
            cull.openCenter()
            pump(qapp, 0.4)
            for _ in range(3):
                # Drop temporary childItems wrappers between traversals. A stale
                # binding must fail here, rather than being ignored by find().
                gc.collect()
                pending = [window.contentItem()]
                while pending:
                    item = pending.pop()
                    assert isinstance(item, QQuickItem) and isValid(item)
                    item.objectName()
                    pending.extend(item.childItems())
                assert find(window, "aiFolderData").property("visible")
            cull.close()
            settings = window.findChild(QQuickItem, "settingsWindow")
            assert QMetaObject.invokeMethod(settings, "open")
            pump(qapp, 0.3)
            gc.collect()
            assert settings.property("shown")
            assert not [w for w in warnings if "TypeError" in w or "ReferenceError" in w], warnings
        finally:
            window.close()
            engine.deleteLater()
            pump(qapp, 0.1)
            gc.collect()
