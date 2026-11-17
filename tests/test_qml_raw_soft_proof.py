"""Soft Proof in the RAW editor: the picture on screen is proofed, the frames the tools read and the edit are not, and the panel's
Proof menu holds the settings."""
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QPointF, QUrl
from PySide6.QtGui import QColor, QColorSpace, QImage

from image_triage import soft_proof
from image_triage.qml_shell import raw_preview
from image_triage.qml_shell.raw_editor import RawEditor
from test_qml_shell import qapp, pump  # noqa: F401


def narrow(tmp_path):
    space = QColorSpace(QPointF(0.3127, 0.3290), QPointF(0.55, 0.36), QPointF(0.33, 0.50), QPointF(0.20, 0.17),
                        QColorSpace.TransferFunction.Gamma, 2.2)
    path = tmp_path / "printer.icc"
    path.write_bytes(bytes(space.iccProfile()))
    return str(path)


@pytest.fixture
def raw(qapp):
    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    image = QImage(80, 60, QImage.Format.Format_RGBA8888)
    image.fill(QColor(0, 0, 255))
    frame = ({"histogram": [[0] * 256] * 3, "fullSize": [4000, 3000]}, image)
    raw._proxy_arrived(raw._revision, (frame, frame, 0, "sample.NEF"))
    yield raw
    raw.shutdown()


def shown(url):
    images = raw_preview._Images()
    from PySide6.QtCore import QSize

    return images.requestImage(url.removeprefix("image://rawpreview/"), QSize(), QSize())


def test_proofing_changes_only_the_picture_on_screen(raw, tmp_path):
    plain = raw.preview
    assert "proof=" not in plain
    proof = raw.softProof
    proof.set("profile", "file")
    proof.set("profileFile", narrow(tmp_path))
    proof.set("gamut", True)
    assert not proof.active and "proof=" not in raw.preview       # not switched on yet
    proof.set("enabled", True)
    assert proof.active and "proof=" in raw.preview and proof.label
    assert shown(raw.preview).pixelColor(10, 10) == QColor(*soft_proof.GAMUT_COLOR)     # pure blue is outside the printer's gamut
    assert raw_preview.frame_image(raw._preview).pixelColor(10, 10) == QColor(0, 0, 255)  # what the tools read is untouched
    assert "aiDenoise" not in raw._draft and not raw.canUndo                              # not an edit


def test_a_missing_profile_is_said_and_nothing_is_proofed(raw, tmp_path):
    proof = raw.softProof
    proof.set("enabled", True)
    proof.set("profile", "file")
    proof.set("profileFile", str(tmp_path / "gone.icc"))
    assert proof.problem == "Choose an ICC profile file." and not proof.active and "proof=" not in raw.preview


def test_named_profiles_proof_without_a_file(raw):
    proof = raw.softProof
    proof.set("enabled", True)
    proof.set("profile", "adobergb")
    assert proof.active and proof.label == "Adobe RGB (1998)"
    assert not shown(raw.preview).isNull()


def test_the_proof_menu_is_in_the_panel(raw, qapp):
    from PySide6.QtQml import QQmlComponent, QQmlEngine

    import image_triage.qml_shell as shell
    from image_triage.qml_shell.theme import Theme

    engine = QQmlEngine()
    raw_preview.install_images(engine)
    engine.rootContext().setContextProperty("theme", Theme())
    engine.rootContext().setContextProperty("rawModel", raw)
    engine.rootContext().setContextProperty("captureData", QObject())
    component = QQmlComponent(engine)
    component.setData(b'import QtQuick\nItem { width: 360; height: 900\n RawEditorPanel { anchors.fill: parent; model: rawModel } }',
                      QUrl.fromLocalFile(str(Path(shell.__file__).parent / "qml" / "ProofHarness.qml")))
    item = component.create()
    assert item is not None, component.errors()
    try:
        button = item.findChild(QObject, "rawSoftProof")
        assert button is not None
        raw.softProof.set("gamut", True)
        raw.softProof.set("enabled", True)
        menu = item.findChild(QObject, "rawSoftProofMenu")
        menu.open()
        pump(qapp, .2)
        assert button.property("selected")

        def walk(node):
            yield node
            for child in node.childItems():
                yield from walk(child)

        switches = {n.objectName(): n for n in walk(menu.property("contentItem")) if n.objectName().startswith("rawSoftProof")}
        assert switches["rawSoftProofOn"].property("checked") is True
        assert switches["rawSoftProof_gamut"].property("checked") is True and switches["rawSoftProof_paper"].property("checked") is False
        menu.close()
    finally:
        item.deleteLater()
        pump(qapp, .05)
