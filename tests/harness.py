"""Real-window test harness (WI-0.5).

Builds the genuine ``MainWindow`` offscreen against the sandboxed settings that
``conftest.py`` installs. Modal dialogs never block: informational ones are
recorded, questions return a configurable answer, and anything that would call
``exec()`` fails the test instead of hanging it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

from image_triage.app_identity import legacy_settings_sources, user_settings


@dataclass
class DialogRecorder:
    question_answer: QMessageBox.StandardButton = QMessageBox.StandardButton.Yes
    messages: list[tuple[str, str, str]] = field(default_factory=list)


def install_dialog_guards(monkeypatch) -> DialogRecorder:
    recorder = DialogRecorder()

    def make_message(kind: str):
        def _message(parent=None, title="", text="", *args, **kwargs):
            recorder.messages.append((kind, str(title), str(text)))
            return QMessageBox.StandardButton.Ok

        return staticmethod(_message)

    def _question(parent=None, title="", text="", *args, **kwargs):
        recorder.messages.append(("question", str(title), str(text)))
        return recorder.question_answer

    monkeypatch.setattr(QMessageBox, "information", make_message("information"))
    monkeypatch.setattr(QMessageBox, "warning", make_message("warning"))
    monkeypatch.setattr(QMessageBox, "critical", make_message("critical"))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(_question))

    def _blocked(self, *args, **kwargs):
        raise AssertionError(f"{type(self).__name__}.exec() would block a headless test")

    monkeypatch.setattr(QDialog, "exec", _blocked)
    monkeypatch.setattr(QMessageBox, "exec", _blocked)
    # Each stub must return what its real counterpart returns when the user cancels: a plain "" for
    # getExistingDirectory, a (path, filter) pair for the others. (A loop-variable lambda here used to
    # hand every stub the last name's value, so getExistingDirectory returned a tuple.)
    for name, cancelled in (
        ("getExistingDirectory", ""),
        ("getOpenFileName", ("", "")),
        ("getSaveFileName", ("", "")),
        ("getOpenFileNames", ([], "")),
    ):
        monkeypatch.setattr(QFileDialog, name, staticmethod(lambda *a, _cancelled=cancelled, **k: _cancelled))
    return recorder


def prepare_application() -> QApplication:
    QCoreApplication.setOrganizationName("Image Triage")
    QCoreApplication.setApplicationName("Image Triage")
    user_settings().clear()
    for legacy in legacy_settings_sources():
        legacy.clear()
    return QApplication.instance() or QApplication([])


def snapshot_app_state():
    app = QApplication.instance()
    return (app.styleSheet(), app.palette(), app.font())


def restore_app_state(snapshot) -> None:
    """Put the app-wide style back, touching only what actually changed.

    Qt does not short-circuit an identical ``setStyleSheet`` (or palette / font): each call
    repolishes or re-notifies every live widget, and late in a full run the widgets leaked by
    earlier tests make that take tens of seconds. Hundreds of tests restore a state they never
    changed, so an unconditional restore dominated the suite and pushed single tests towards
    conftest's per-test hard timeout."""
    app = QApplication.instance()
    stylesheet, palette, font = snapshot
    if app.styleSheet() != stylesheet:
        app.setStyleSheet(stylesheet)
    if app.palette() != palette:
        app.setPalette(palette)
    if app.font() != font:
        app.setFont(font)


def make_main_window():
    from image_triage.window import MainWindow

    app = prepare_application()
    snapshot = snapshot_app_state()
    window = MainWindow()
    app.processEvents()
    restore_app_state(snapshot)
    return window


def dispose_window(window) -> None:
    """Close a test window and really destroy it.

    ``processEvents()`` never runs ``deleteLater()`` (deferred deletes need an explicit
    ``sendPostedEvents(..., DeferredDelete)``), so windows disposed with ``deleteLater`` alone were
    never destroyed: ~600 widgets and ~140 top-level popups survived *each* test, tens of thousands
    after a few dozen tests. Everything that scales with live widgets (app-wide style, palette and
    font changes, every ``processEvents`` turn servicing their timers) then got slower the further a
    full run went, until single tests crossed conftest's per-test hard timeout."""
    app = QApplication.instance()
    window.close()
    if app is not None:
        app.processEvents()
    QThreadPool.globalInstance().waitForDone(5000)
    window.deleteLater()
    if app is not None:
        _pump_past_destroyed_widgets(app)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        _pump_past_destroyed_widgets(app)


def _pump_past_destroyed_widgets(app, turns: int = 4) -> None:
    """``processEvents`` a few times, tolerating deferred callbacks (``QTimer.singleShot`` lambdas,
    queued signals) that were still pending when their window's widgets were destroyed: they raise
    "Internal C++ object ... already deleted" from inside the event loop. They fire here, at
    teardown, instead of leaking into whichever test runs next; anything else still propagates."""
    for _ in range(turns):
        try:
            app.processEvents()
        except RuntimeError as exc:
            if "already deleted" not in str(exc):
                raise


def make_jpegs(directory, names, size=(64, 48)):
    from pathlib import Path

    from PIL import Image

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, name in enumerate(names):
        path = directory / name
        Image.new("RGB", size, (30 + index * 40, 80, 120)).save(path, "JPEG")
        paths.append(str(path))
    return paths


def pump_until(condition, timeout: float = 10.0) -> bool:
    import time

    app = QApplication.instance()
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(condition())


def open_folder(window, folder, expected_count: int) -> None:
    window._scan.load_folder(str(folder))
    assert pump_until(lambda: len(window._records) == expected_count), (
        f"folder did not load {expected_count} records (got {len(window._records)})"
    )
    QThreadPool.globalInstance().waitForDone(5000)


def reset_window_state(window) -> None:
    """Return a shared MainWindow to a neutral state between tests."""
    window._undo_stack.clear()
    window._annotations.clear()
    window._records = []
    window._current_folder = ""
    window._inspector.update_action_states()
    QApplication.processEvents()


def controller_over(controller_class, window, attribute: str, **overrides):
    """A real controller of ``controller_class`` working on ``window`` (a stand-in), published as ``window.<attribute>``.

    ``overrides`` replace controller methods the test wants to record instead of run. A controller is a ``QObject`` and
    needs a parent object to own it; the parent is kept alive on the stand-in.
    """
    from PySide6.QtCore import QObject

    parent = QObject()
    setattr(window, f"{attribute}_parent", parent)
    controller = controller_class(parent)
    controller._window = window
    for name, value in overrides.items():
        setattr(controller, name, value)
    setattr(window, attribute, controller)
    return controller
