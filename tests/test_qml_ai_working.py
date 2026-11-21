"""The AI working overlay: while an editor AI tool works, a sweep of light runs over the photo with the tool's name and step."""
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest

from test_qml_shell_viewer import shell, qapp, pump  # noqa: F401


def editing(shell, qapp):
    engine, window, photos, popout, warnings = shell
    popout.open(0)
    raw = popout.rawEditor
    raw.open("sample.NEF")
    image = QImage(800, 500, QImage.Format_RGB32)
    image.fill(QColor("#bf843f"))
    frame = ({"histogram": [[0] * 256] * 3, "fullSize": [8000, 5000]}, image)
    raw._proxy_arrived(raw._revision, (frame, frame, 0, "sample.NEF"))
    pump(qapp, .7)
    return window, raw, warnings


def working(tool, busy, message=""):
    tool._busy, tool._message = busy, message
    tool.changed.emit()


def test_the_overlay_covers_the_photo_while_denoise_works_and_cancel_stops_it(shell, qapp):
    window, raw, warnings = editing(shell, qapp)
    overlay = window.findChild(QObject, "aiWorkingOverlay")
    photo = window.findChild(QObject, "viewerPhotoImage")
    assert not overlay.isVisible()
    working(raw.denoiseEditor, True, "Denoising… 34%")
    pump(qapp, .5)
    assert overlay.isVisible() and overlay.property("title") == "AI Denoise" and overlay.property("step") == "Denoising… 34%"
    assert abs(overlay.width() - photo.property("paintedWidth")) < 2 and abs(overlay.height() - photo.property("paintedHeight")) < 2
    assert overlay.property("canCancel")
    cancel = window.findChild(QObject, "aiWorkingCancel")
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, cancel.mapToScene(QPointF(cancel.width() / 2, cancel.height() / 2)).toPoint())
    pump(qapp, .8)
    assert not raw.denoiseEditor.busy and not overlay.isVisible()
    assert not [w for w in warnings if "AiWorking" in w or "aiJob" in w], warnings


def test_each_ai_tool_names_itself(shell, qapp):
    window, raw, _ = editing(shell, qapp)
    overlay = window.findChild(QObject, "aiWorkingOverlay")
    for depth_tool in (raw.lightDepthEditor, raw.bokehEditor):
        depth_tool._pending = object()                 # an edit waits on the depth map: it is shown working, not in the background
    for tool, title in ((raw.skyEditor, "Sky Replacement"), (raw.eraseEditor, "AI Erase"), (raw.lightDepthEditor, "Light Depth"),
                        (raw.bokehEditor, "Bokeh"), (raw.maskEditor.ai, "AI Masking")):
        working(tool, True, "Working")
        pump(qapp, .1)
        assert overlay.property("active") and overlay.property("title") == title, title
        working(tool, False)
        pump(qapp, .1)
        assert not overlay.property("active")


def test_a_depth_map_made_in_the_background_shows_no_overlay(shell, qapp):
    # the depth map is made when the photo opens, with nothing waiting on it: no sweep over the photo then, only once an edit waits
    window, raw, _ = editing(shell, qapp)
    overlay = window.findChild(QObject, "aiWorkingOverlay")
    for tool in (raw.lightDepthEditor, raw.bokehEditor, raw.atmosphereEditor):
        tool._pending = None
        working(tool, True, "Making the depth map")
        pump(qapp, .1)
        assert tool.background and not overlay.property("active")
        working(tool, False)


def test_the_viewer_export_button_opens_batch_export_for_this_photo_alone(shell, qapp):
    from PySide6.QtCore import QObject, QPointF
    engine, window, photos, popout, warnings = shell
    popout.open(1)
    pump(qapp, .5)
    def walk(item):
        yield item
        for child in item.childItems():
            yield from walk(child)
    button = next((i for i in walk(window.contentItem()) if i.objectName() == "viewerTool_export"), None)
    assert button is not None and button.isVisible()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint())
    pump(qapp, .6)
    card = window.findChild(QObject, "batchWindow")
    batch = engine.rootContext().contextProperty("batch")
    assert card.property("shown") and card.property("mode") == "export"
    assert batch.only == photos.get(1)["path"] and len(card.property("rows")) == 1
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w], warnings
