"""Shared, hermetic test environment (WI-0.2).

Everything here runs before any test module imports application code, so tests
cannot read or write the developer's real QSettings, AppData or log folders.
"""
from __future__ import annotations

import faulthandler
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SANDBOX = tempfile.TemporaryDirectory(prefix="image_triage_tests_")
_SANDBOX_ROOT = Path(_SANDBOX.name)
for _name, _sub in (("LOCALAPPDATA", "local"), ("APPDATA", "roaming")):
    _path = _SANDBOX_ROOT / _sub
    _path.mkdir(parents=True, exist_ok=True)
    os.environ[_name] = str(_path)
os.environ["IMAGE_TRIAGE_LOG_DIR"] = str(_SANDBOX_ROOT / "logs")

try:
    import PySide6.QtCore as _qtcore

    _settings_dir = _SANDBOX_ROOT / "qsettings"
    _settings_dir.mkdir(parents=True, exist_ok=True)
    _RealQSettings = _qtcore.QSettings
    _RealQSettings.setDefaultFormat(_RealQSettings.Format.IniFormat)
    for _scope in (_RealQSettings.Scope.UserScope, _RealQSettings.Scope.SystemScope):
        _RealQSettings.setPath(_RealQSettings.Format.IniFormat, _scope, str(_settings_dir))

    class _HermeticQSettings(_RealQSettings):
        # QSettings("org", "app") always uses the native format (the Windows
        # registry) regardless of setDefaultFormat, so force it onto the INI sandbox.
        def __init__(self, *args, **kwargs):
            if args and isinstance(args[0], str) and args[0].upper().startswith("HKEY_"):
                # A direct native-registry-path construction, e.g.
                # QSettings(r"HKEY_CURRENT_USER\Software\X", QSettings.Format.NativeFormat)
                # (see image_triage/app_identity.py). Redirect it into its own
                # file in the sandbox instead of the real registry.
                parent = args[2] if len(args) > 2 else kwargs.get("parent")
                safe_name = "".join(ch if ch.isalnum() else "_" for ch in args[0])
                super().__init__(
                    str(_settings_dir / f"{safe_name}.ini"),
                    _RealQSettings.Format.IniFormat,
                    parent,
                )
            elif args and isinstance(args[0], str) and not (len(args) > 1 and not isinstance(args[1], (str, type(None), _qtcore.QObject))):
                organization = args[0]
                application = args[1] if len(args) > 1 and isinstance(args[1], str) else ""
                parent = args[2] if len(args) > 2 else kwargs.get("parent")
                super().__init__(
                    _RealQSettings.Format.IniFormat,
                    _RealQSettings.Scope.UserScope,
                    organization,
                    application,
                    parent,
                )
            else:
                super().__init__(*args, **kwargs)

    _qtcore.QSettings = _HermeticQSettings
except ImportError:
    pass

try:
    from PySide6.QtCore import QStandardPaths as _QStandardPaths

    _real_writable_location = _QStandardPaths.writableLocation
    _standard_root = _SANDBOX_ROOT / "standard_paths"

    def _sandboxed_writable_location(location):
        # On Windows Qt resolves these through known-folder APIs, ignoring
        # LOCALAPPDATA/APPDATA, so without this the app's stores would write
        # into the developer's real profile.
        path = _standard_root / str(int(location.value) if hasattr(location, "value") else int(location))
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    _QStandardPaths.writableLocation = staticmethod(_sandboxed_writable_location)
except ImportError:
    pass

try:
    # Lazy PIL plugin imports racing across worker threads crash the interpreter
    # (access violation during GC); load them once, single-threaded, up front.
    from PIL import Image as _PILImage

    _PILImage.init()
except ImportError:
    pass

TEST_TIMEOUT_SECONDS = float(os.environ.get("IMAGE_TRIAGE_TEST_TIMEOUT", "120"))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "requires_models: needs downloaded AI model weights or a GPU; excluded from the default CI run",
    )


HANG_LOG_PATH = Path(tempfile.gettempdir()) / "image_triage_test_hang.txt"


@pytest.fixture(autouse=True)
def _per_test_hard_timeout(request):
    # A hung test would otherwise stall the whole run forever. faulthandler
    # prints every thread's stack and then terminates the process.
    #
    # The dump goes to a real file, not the default sys.stderr: during a test that is pytest's
    # capture file, which vanishes with the process, so a killed run used to end with the log just
    # stopping mid-test and no clue which test or where. The file holds only the current test's
    # name (rewritten per test) plus, if the timeout fires, the stacks.
    with open(HANG_LOG_PATH, "w", encoding="utf-8") as hang_log:
        hang_log.write(f"{request.node.nodeid}\n")
        hang_log.flush()
        faulthandler.dump_traceback_later(TEST_TIMEOUT_SECONDS, exit=True, file=hang_log)
        try:
            yield
        finally:
            faulthandler.cancel_dump_traceback_later()


@pytest.fixture(autouse=True)
def _forbid_real_mask_engine_worker(monkeypatch):
    real_popen = subprocess.Popen

    def guarded_popen(args, *popen_args, **popen_kwargs):
        command = args if isinstance(args, str) else " ".join(str(part) for part in args)
        if "mask_engine_worker" in command:
            raise AssertionError(
                "test tried to spawn a real mask_engine_worker process; patch subprocess.Popen instead"
            )
        return real_popen(args, *popen_args, **popen_kwargs)

    guarded_popen.__wrapped__ = real_popen
    monkeypatch.setattr(subprocess, "Popen", guarded_popen)
    yield


@pytest.fixture(scope="session")
def _module_dialogs():
    from tests.harness import install_dialog_guards

    patcher = pytest.MonkeyPatch()
    recorder = install_dialog_guards(patcher)
    yield recorder
    patcher.undo()


@pytest.fixture
def dialogs(_module_dialogs):
    from PySide6.QtWidgets import QMessageBox

    _module_dialogs.messages.clear()
    _module_dialogs.question_answer = QMessageBox.StandardButton.Yes
    return _module_dialogs


@pytest.fixture(scope="session")
def _shared_main_window(_module_dialogs):
    # Building MainWindow costs seconds and leaks timers, so the whole session
    # shares one instance; the main_window fixture resets its state per test.
    from tests.harness import dispose_window, make_main_window

    window = make_main_window()
    yield window
    dispose_window(window)


@pytest.fixture
def main_window(_shared_main_window, dialogs):
    # MainWindow applies an app-wide palette/stylesheet; keep it from leaking
    # into unrelated tests that share the QApplication.
    from tests.harness import reset_window_state, restore_app_state, snapshot_app_state

    snapshot = snapshot_app_state()
    reset_window_state(_shared_main_window)
    try:
        yield _shared_main_window
    finally:
        restore_app_state(snapshot)
