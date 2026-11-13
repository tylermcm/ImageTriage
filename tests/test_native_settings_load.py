"""The native culler's settings are read away from the UI thread (audit A17): reading them starts the engine's Python."""
from __future__ import annotations

import sys
import threading
import time

import pytest
from PySide6.QtCore import QObject, QSettings, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from image_triage import cli_culler_runtime
from image_triage.qml_shell import native_cull_controls
from image_triage.qml_shell.cull_model import CullModel
from image_triage.qml_shell.native_cull_controls import NativeDefaultsRead
from image_triage.qml_shell.photos import PhotoModel

DEFAULTS = {"models": [], "bursts_keep": 2, "gates": {"blur_ratio": .35, "clip_region_reject": .35}, "blink_threshold": .6}


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def engine(monkeypatch):
    """A stand-in for the engine's Python: `read` blocks until the test lets it answer, and records the thread it ran on."""
    class Engine:
        def __init__(self):
            self.release, self.threads, self.answers, self.cancelled = threading.Event(), [], [DEFAULTS], []

    state = Engine()

    def read(self, python, timeout=30):
        state.threads.append(threading.current_thread())
        answer = state.answers[min(len(state.threads), len(state.answers)) - 1]
        assert state.release.wait(10)
        if isinstance(answer, Exception):
            raise answer
        return answer

    cancel = NativeDefaultsRead.cancel
    monkeypatch.setattr(NativeDefaultsRead, "read", read)
    monkeypatch.setattr(NativeDefaultsRead, "cancel", lambda self: (state.cancelled.append(self), cancel(self))[1])
    monkeypatch.setattr(cli_culler_runtime, "load_native_runtime", lambda: {"python": "engine-python", "default_settings": {"models": ["faces"]}})
    monkeypatch.setattr(NativeDefaultsRead, "validate", lambda self, python, value: None)
    yield state
    state.release.set()


def native_model(app, tmp_path):
    model = CullModel(PhotoModel(parent=app), memory=True, parent=app)
    model._native = True
    model._settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    return model


def wait(condition, what):
    for _ in range(600):
        if condition():
            return
        QTest.qWait(5)
    raise AssertionError(what)


def test_the_window_draws_while_the_engine_is_still_being_asked(app, tmp_path, engine):
    model = native_model(app, tmp_path)
    started = time.monotonic()
    for _ in range(4):
        model._rebuild()                            # every redraw used to wait for the engine's Python
    assert time.monotonic() - started < 2
    view = model.view
    assert view["nativeLoading"] and not view["nativeReady"] and view["nativeGroups"] == [] and view["nativeError"] == ""
    wait(lambda: engine.threads, "the read never started")
    assert len(engine.threads) == 1                 # one read, however often the window redraws
    assert engine.threads[0] is not threading.main_thread()
    assert not model.saveNativeDraft()              # Start and Save wait for the settings rather than saving an empty profile
    assert "still loading" in model.view["nativeError"] and model._settings.value("ai/cli_culler_settings_json") is None
    engine.release.set()
    wait(lambda: model._native_controls is not None, "the settings never arrived")
    view = model.view
    assert view["nativeReady"] and not view["nativeLoading"] and view["nativeError"] == "" and view["nativeGroups"]
    assert model._native_controls.value("models.search") is True          # the app's search-on default is applied as before
    assert model._native_controls.value("bursts_keep") == 2
    model.shutdown()


def test_a_result_overtaken_by_a_saved_profile_is_dropped(app, tmp_path, engine):
    model = native_model(app, tmp_path)
    model._rebuild()
    wait(lambda: len(engine.threads) == 1, "the read never started")
    first = model._native_load
    assert not model.saveNativeSettings('{"models": ["appeal"], "bursts_keep": 5}')  # accepted asynchronously
    wait(lambda: model._native_save is None, "validation did not finish")
    assert first.cancelled and model._native_load is not first
    wait(lambda: len(engine.threads) == 2, "the profile was not read again")
    engine.release.set()
    wait(lambda: model._native_controls is not None, "the settings never arrived")
    QTest.qWait(50)                                 # time for a late first answer to arrive, were it not dropped
    assert model._native_controls.draft == {"models": ["appeal"], "bursts_keep": 5}
    model.shutdown()


def test_a_late_result_carrying_an_old_token_changes_nothing(app, tmp_path, engine):
    model = native_model(app, tmp_path)
    model._rebuild()
    stale = model._native_load_token
    model._cancel_native_load()
    model._native_load_failed = True                # nothing newer is started: only the stale answer is in play
    model._nativeLoaded.emit(stale, ({"models": ["old"]}, DEFAULTS), "")
    QTest.qWait(20)
    assert model._native_controls is None
    engine.release.set()
    model.shutdown()


def test_shutdown_does_not_wait_for_the_engine(app, tmp_path, engine):
    model = native_model(app, tmp_path)
    model._rebuild()
    wait(lambda: engine.threads, "the read never started")
    started = time.monotonic()
    model.shutdown()
    assert time.monotonic() - started < 2 and len(engine.cancelled) == 1 and model._native_load is None
    engine.release.set()                            # the engine answers after the model has shut down
    engine.threads[0].join(5)
    QTest.qWait(50)
    assert model._native_controls is None
    model._rebuild()
    QTest.qWait(20)
    assert len(engine.threads) == 1 and not model.view["nativeLoading"]     # and a late redraw does not start it again


def test_a_failure_is_shown_and_kept_until_the_window_is_opened_again(app, tmp_path, engine):
    engine.answers = [ValueError("No module named 'aiculler'"), DEFAULTS]
    engine.release.set()
    model = native_model(app, tmp_path)
    model._rebuild()
    wait(lambda: model._native_load is None, "the read never ended")
    for _ in range(3):
        model._rebuild()
    QTest.qWait(30)
    assert len(engine.threads) == 1 and "aiculler" in model.view["nativeError"] and not model.view["nativeReady"]
    model.openCull()
    wait(lambda: model._native_controls is not None, "the settings were not read again")
    assert len(engine.threads) == 2 and model.view["nativeError"] == ""
    model.shutdown()


def test_the_read_runs_the_engine_python_and_can_be_killed(monkeypatch):
    monkeypatch.setattr(native_cull_controls, "DEFAULTS_CODE", "import json; print(json.dumps({'bursts_keep': 3}))")
    assert NativeDefaultsRead().read(sys.executable) == {"bursts_keep": 3}
    monkeypatch.setattr(native_cull_controls, "DEFAULTS_CODE", "import sys; sys.exit('no engine here')")
    with pytest.raises(ValueError, match="no engine here"):
        NativeDefaultsRead().read(sys.executable)
    monkeypatch.setattr(native_cull_controls, "DEFAULTS_CODE", "import time; time.sleep(60)")
    with pytest.raises(ValueError, match="did not report"):
        NativeDefaultsRead().read(sys.executable, timeout=.3)
    job, errors = NativeDefaultsRead(), []

    def work():
        try:
            job.read(sys.executable)
        except ValueError as error:
            errors.append(str(error))

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    for _ in range(200):
        if job._process is not None:
            break
        time.sleep(.01)
    started = time.monotonic()
    job.cancel()
    thread.join(10)
    assert not thread.is_alive() and time.monotonic() - started < 5 and "cancelled" in errors[0]
    with pytest.raises(ValueError, match="cancelled"):
        job.read(sys.executable)                    # cancelled before it started: no process is created


def test_saving_does_not_block_or_overwrite_newer_edits(app, tmp_path, engine, monkeypatch):
    from image_triage.qml_shell.native_cull_controls import NativeCullControls
    gate, entered = threading.Event(), threading.Event()
    threads = []
    def validate(self, python, value):
        threads.append(threading.current_thread())
        entered.set()
        assert gate.wait(10)
    monkeypatch.setattr(NativeDefaultsRead, "validate", validate)
    model = native_model(app, tmp_path)
    model._native_controls = NativeCullControls(DEFAULTS, DEFAULTS)
    model.setNativeValue("bursts_keep", 3)
    started = time.monotonic()
    assert not model.saveNativeDraft()
    assert time.monotonic() - started < .5
    wait(entered.is_set, "validator never started")
    assert threads[0] is not threading.main_thread() and model.view["nativeSaving"]
    model.setNativeValue("bursts_keep", 4)
    gate.set()
    wait(lambda: model._native_save is None, "validator never finished")
    import json
    assert json.loads(model._settings.value("ai/cli_culler_settings_json"))["bursts_keep"] == 3
    assert model._native_controls.value("bursts_keep") == 4 and model._native_controls.dirty
    model.shutdown()


def test_shutdown_drops_pending_save(app, tmp_path, engine, monkeypatch):
    gate, entered = threading.Event(), threading.Event()
    def validate(self, python, value):
        entered.set()
        assert gate.wait(10)
    monkeypatch.setattr(NativeDefaultsRead, "validate", validate)
    model = native_model(app, tmp_path)
    assert not model.saveNativeSettings('{"models": []}')
    wait(entered.is_set, "validator never started")
    model.shutdown()
    gate.set()
    QTest.qWait(50)
    assert model._settings.value("ai/cli_culler_settings_json") is None


def test_the_controls_say_they_are_loading_or_unavailable(app, tmp_path, engine):
    from PySide6.QtQml import QQmlApplicationEngine
    from image_triage.qml_shell.app import QML_DIR, _configure_qt_dll_search
    from image_triage.qml_shell.theme import Theme
    _configure_qt_dll_search()
    from PySide6 import QtQuick  # noqa: F401 - register the native window wrapper

    engine.answers = [ValueError("Native runtime is missing python")]
    model = native_model(app, tmp_path)
    model.openCull()
    qml = QQmlApplicationEngine()
    qml.addImportPath(str(QML_DIR))
    qml.rootContext().setContextProperty("cull", model)
    qml.rootContext().setContextProperty("theme", Theme(parent=app))
    warnings = []
    qml.warnings.connect(lambda items: warnings.extend(str(item) for item in items))
    qml.loadData(b'import QtQuick; import QtQuick.Window; Window { width: 920; height: 900; visible: true; NativeCullOptions { width: 900 } }', QUrl.fromLocalFile(str(QML_DIR / "NativeLoadTest.qml")))
    assert qml.rootObjects(), warnings
    window = qml.rootObjects()[0]
    QTest.qWait(50)
    state = window.findChild(QObject, "nativeSettingsState")
    text = lambda: " ".join(child.property("text") for child in state.childItems())
    assert state.property("visible") and "Loading" in text()
    engine.release.set()
    wait(lambda: model._native_load is None, "the read never ended")
    QTest.qWait(30)
    assert state.property("visible") and "unavailable" in text()
    assert not warnings, warnings
    window.close()
    qml.deleteLater()
    model.shutdown()
    QTest.qWait(30)
