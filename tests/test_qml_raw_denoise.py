"""Detail > Denoise in the RAW editor: the checkbox starts the job once, Amount is committed when the slider pauses, undo works,
and the panel shows the controls."""
import time

import pytest
from PySide6.QtCore import QObject
from PySide6.QtGui import QColor, QImage

from image_triage import ai_denoise
from image_triage.qml_shell.raw_editor import RawEditor
from test_qml_shell import qapp, pump  # noqa: F401


def wait(qapp, predicate):
    end = time.monotonic() + 5
    while not predicate() and time.monotonic() < end:
        pump(qapp, .02)
    assert predicate()


@pytest.fixture
def raw(qapp, monkeypatch):
    made = set()
    monkeypatch.setattr(ai_denoise, "unavailable_reason", lambda: "")
    monkeypatch.setattr(ai_denoise, "ready", lambda path: path in made)
    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    image = QImage(80, 60, QImage.Format_RGBA8888)
    image.fill(QColor("gray"))
    frame = ({"histogram": [[0] * 256] * 3, "fullSize": [4000, 3000]}, image)
    raw._proxy_arrived(raw._revision, (frame, frame, 0, "sample.NEF"))
    calls = []

    def maker(path, amount, progress=None, cancel=None):
        calls.append((path, amount))
        progress("Denoising… 50%")
        made.add(path)

    raw.denoiseEditor._maker = maker
    raw.denoise_calls = calls
    yield raw
    raw.shutdown()


def test_ticking_denoise_makes_the_denoised_raw_once(raw, qapp):
    denoise = raw.denoiseEditor
    denoise.setEnabled(True)
    assert raw._draft["aiDenoise"] == {"enabled": True, "amount": 50.0}
    wait(qapp, lambda: not denoise.busy)
    assert denoise.ready and not denoise.error and raw.denoise_calls == [("sample.NEF", 50.0)]
    denoise.setEnabled(False)
    denoise.setEnabled(True)
    assert len(raw.denoise_calls) == 1                     # kept: not made again


def test_amount_is_committed_when_the_slider_pauses(raw, qapp):
    denoise = raw.denoiseEditor
    denoise.setEnabled(True)
    wait(qapp, lambda: not denoise.busy)
    denoise.setAmount(20)
    denoise.setAmount(35)
    assert denoise.settings["amount"] == 35 and raw._draft["aiDenoise"]["amount"] == 50.0
    wait(qapp, lambda: raw._draft["aiDenoise"]["amount"] == 35)
    raw.undo()
    assert raw._draft["aiDenoise"]["amount"] == 50.0
    raw.undo()
    assert "aiDenoise" not in raw._draft


def test_a_failure_is_shown_and_can_be_retried(raw, qapp):
    denoise = raw.denoiseEditor
    attempts = []

    def failing(path, amount, progress=None, cancel=None):
        attempts.append(1)
        raise RuntimeError("The AI Denoise model is not installed.")

    denoise._maker = failing
    denoise.setEnabled(True)
    wait(qapp, lambda: not denoise.busy)
    assert denoise.error == "The AI Denoise model is not installed." and not denoise.ready
    denoise.retry()
    wait(qapp, lambda: len(attempts) == 2 and not denoise.busy)


def test_unavailable_denoise_does_not_start(raw, qapp, monkeypatch):
    monkeypatch.setattr(ai_denoise, "unavailable_reason", lambda: "AI Denoise needs the RAW preview worker.")
    raw.denoiseEditor.setEnabled(True)
    assert raw.denoiseEditor.error == "AI Denoise needs the RAW preview worker." and raw.denoise_calls == []


def test_the_detail_section_shows_the_controls(raw, qapp):
    from pathlib import Path

    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlComponent, QQmlEngine

    import image_triage.qml_shell as shell
    from image_triage.qml_shell import raw_preview
    from image_triage.qml_shell.theme import Theme

    engine = QQmlEngine()
    raw_preview.install_images(engine)
    engine.rootContext().setContextProperty("theme", Theme())
    engine.rootContext().setContextProperty("rawModel", raw)
    component = QQmlComponent(engine)
    component.setData(b'import QtQuick\nItem { width: 320; height: 700\n RawTechnicalAdjustments { width: 300; model: rawModel; section: "Detail" } }',
                      QUrl.fromLocalFile(str(Path(shell.__file__).parent / "qml" / "DenoiseHarness.qml")))
    item = component.create()
    assert item is not None, component.errors()
    try:
        check = item.findChild(QObject, "rawDenoise")
        amount = item.findChild(QObject, "rawDenoiseAmount")
        assert check is not None and amount is not None
        assert not amount.property("visible")
        raw.denoiseEditor.setEnabled(True)
        wait(qapp, lambda: not raw.denoiseEditor.busy)
        pump(qapp, .05)
        assert amount.property("visible")
    finally:
        item.deleteLater()
        pump(qapp, .05)
