"""Removing the AI runtime from the AI Center. A fake stands in for the machine: no runtime exists here and nothing real is deleted
outside the test's own temporary folder."""
from __future__ import annotations

import os
import shutil
import threading
import time

import pytest
from PySide6.QtCore import Q_ARG, QMetaObject, QObject, Qt
from PySide6.QtTest import QTest

from image_triage import ai_runtime_packages
from image_triage.qml_shell.ai_center import AiCenterBackend
from image_triage.qml_shell.cull_model import CullModel
from image_triage.qml_shell.menu_model import ToastModel
from image_triage.qml_shell.photos import PhotoModel
from test_ai_runtime_probe import ON_CARD, ProbeOps
from test_qml_shell import _load_main, pump, qapp  # noqa: F401
from test_qml_shell_ai_center import FakeOps, centre, clean_settings, find, settled, until  # noqa: F401


class RemovableOps(FakeOps):
    """A PC with the runtime and the appeal ranker installed. Removal takes as long as `gate` says and leaves `left` behind."""

    def __init__(self, **changes) -> None:
        super().__init__(**{"installed": ["cpu"], "torch": ["cpu"], "runtimeBytes": 2_400_000_000, **changes})
        self.raw["models"]["appeal"]["state"] = "ready"
        self.left: list[str] = []
        self.threads: list[threading.Thread] = []

    def close_workers(self):
        self.calls.append(("close",))

    def remove_runtime(self):
        self.threads.append(threading.current_thread())
        self.calls.append(("remove-runtime",))
        end = time.monotonic() + 5
        while not self.gate.is_set() and time.monotonic() < end:
            time.sleep(0.01)
        self.raw.update(installed=[], torch=[], runtimeBytes=0)
        if self.left:
            return {"existed": True, "freed": 2_300_000_000, "remaining": list(self.left), "remainingCount": len(self.left) + 3, "remainingBytes": 100_000_000}
        return {"existed": True, "freed": 2_400_000_000, "remaining": [], "remainingCount": 0, "remainingBytes": 0}


@pytest.fixture
def ops():
    return RemovableOps()


@pytest.fixture
def cull(qapp, ops):
    ai = AiCenterBackend(ops, parent=qapp, auto_test=False)
    model = CullModel(PhotoModel(parent=qapp), memory=False, ai=ai, toasts=ToastModel(qapp), parent=qapp)
    until(qapp, lambda: model.view["center"]["rtState"] != "Checking…")
    return model


# -- the files ------------------------------------------------------------------------------------------------------------------------

def _runtime(tmp_path):
    root = tmp_path / "runtimes" / "py313"
    site = root / "profiles" / "cpu" / "site-packages" / "torch" / "lib"
    site.mkdir(parents=True)
    (site / "torch_cpu.dll").write_bytes(b"x" * 5000)
    (root / "profiles" / "cpu" / "site-packages" / "numpy.py").write_bytes(b"y" * 300)
    (root / ai_runtime_packages.AI_RUNTIME_METADATA_FILENAME).write_text("{}", encoding="utf-8")
    return root


def test_removal_deletes_the_runtime_folders_and_says_how_much_went(tmp_path):
    root = _runtime(tmp_path)
    models = tmp_path / "models" / "maniqa"
    models.mkdir(parents=True)
    (models / "weights.bin").write_bytes(b"m" * 10)
    report = ai_runtime_packages.remove_ai_runtime(install_root=root)
    assert report.existed and report.complete and report.freed_bytes == 5302 and not root.exists()
    assert (models / "weights.bin").exists()                               # models live elsewhere and are not part of it
    assert not ai_runtime_packages.load_ai_runtime_installation_status(install_root=root).installed_variants
    again = ai_runtime_packages.remove_ai_runtime(install_root=root)
    assert not again.existed and again.freed_bytes == 0


def test_a_file_in_use_stays_and_is_named(tmp_path, monkeypatch):
    root = _runtime(tmp_path)
    real = shutil.rmtree

    def rmtree(path, *args, **kwargs):                                     # Windows: a DLL another process has loaded cannot be deleted
        for folder, _dirs, files in os.walk(path):
            for name in files:
                if name != "torch_cpu.dll":
                    os.unlink(os.path.join(folder, name))
        raise PermissionError(13, "The process cannot access the file because it is being used by another process")

    monkeypatch.setattr(ai_runtime_packages.shutil, "rmtree", rmtree)
    monkeypatch.setattr(ai_runtime_packages.time, "sleep", lambda seconds: None)
    report = ai_runtime_packages.remove_ai_runtime(install_root=root)
    assert not report.complete and report.remaining == ("profiles/cpu/site-packages/torch/lib/torch_cpu.dll",)
    assert report.remaining_count == 1 and report.remaining_bytes == 5000 and report.freed_bytes == 302
    assert not (root / ai_runtime_packages.AI_RUNTIME_METADATA_FILENAME).exists()     # what is left is not an installed runtime
    monkeypatch.setattr(ai_runtime_packages.shutil, "rmtree", real)
    assert ai_runtime_packages.remove_ai_runtime(install_root=root).complete and not root.exists()      # the next try finishes it


def test_removal_is_refused_while_an_installer_holds_the_lock(tmp_path):
    root = _runtime(tmp_path)
    with ai_runtime_packages._ai_runtime_install_lock(root):
        with pytest.raises(RuntimeError, match="already running"):
            ai_runtime_packages.remove_ai_runtime(install_root=root)
    assert (root / "profiles" / "cpu" / "site-packages" / "numpy.py").exists()


# -- the AI Center ---------------------------------------------------------------------------------------------------------------------

def test_remove_closes_the_helpers_then_deletes_off_the_ui_thread_and_ends_not_set_up(cull, ops, qapp):
    center = cull.view["center"]
    assert center["rtState"] == "Installed" and center["rtRemoveShow"]
    text = cull.removeRuntimeText()
    assert text["title"] == "Remove the AI runtime?" and "2.4 GB" in text["body"] and "Appeal ranker" in text["body"] and "stay" in text["body"]
    assert ops.calls == []                                                 # asking deletes nothing
    ops.gate.clear()
    cull.removeRuntime()
    until(qapp, lambda: ("remove-runtime",) in ops.calls)
    until(qapp, lambda: "Removing the files" in cull.view["center"]["rtStep"])      # the step arrives from the worker thread, queued
    center = cull.view["center"]
    assert center["rtState"] == "Removing…" and center["rtRemoving"]
    assert not center["rtRemoveShow"] and not center["rtBtnShow"] and not center["rtTestShow"]        # nothing else is offered meanwhile
    assert cull.view["centerText"] == "Removing…" and all(m["locked"] for m in center["models"])
    cull.cancelSetup()                                                     # a removal is not stopped half way
    assert not cull._ai._cancel.is_set()
    ops.gate.set()
    settled(qapp, cull._ai)
    assert ops.calls == [("close",), ("remove-runtime",)] and ops.threads[0] is not threading.main_thread()
    center = cull.view["center"]
    assert center["rtState"] == "Not set up" and center["rtBtn"] == "Set up AI" and not center["rtRemoveShow"]
    assert "2.4 GB freed" in center["rtDesc"] and "models you installed are still" in center["rtDesc"]
    assert next(m for m in center["models"] if m["key"] == "appeal")["state"] == "Installed"     # the models were not touched
    assert cull.view["centerText"] == "Not set up" and not cull.view["eyes"]["on"]


def test_the_test_result_of_the_removed_runtime_is_forgotten(qapp):
    ops = ProbeOps(ON_CARD)
    ops.remove_runtime = lambda: (ops.raw.update(installed=[], torch=[], gpuBuild=False) or {"freed": 10, "remainingCount": 0})
    ai = AiCenterBackend(ops, parent=qapp, auto_test=False)
    ai.refresh()
    until(qapp, lambda: ai.state()["checked"])
    ai.testRuntime()
    settled(qapp, ai)
    assert ai.state()["tested"] and ai.state()["probe"]["ok"]
    ai.removeRuntime()
    settled(qapp, ai)
    state = ai.state()
    assert state["rt"] == "missing" and not state["tested"] and state["probe"] is None and ai._probe is None and not state["gpuUsable"]
    assert ai.needs_test() is False                                        # and nothing waits for a test of a runtime that is gone


def test_what_could_not_be_deleted_is_reported(cull, ops, qapp):
    ops.left = ["profiles/cpu/site-packages/torch/lib/torch_cpu.dll"]
    cull.removeRuntime()
    settled(qapp, cull._ai)
    center = cull.view["center"]
    assert center["rtState"] == "Needs attention"
    assert "only partly removed" in center["rtDesc"] and "torch_cpu.dll" in center["rtDesc"] and "and 3 more" in center["rtDesc"] and "100 MB" in center["rtDesc"]
    assert "freed" not in center["rtDesc"].split("only partly")[0]          # no claim of a clean removal
    cull.recheckRuntime()                                                  # "Check again" looks at what is really there
    settled(qapp, cull._ai)
    assert cull.view["center"]["rtState"] == "Not set up"


def test_removal_is_refused_while_something_runs_on_it(cull, ops, qapp):
    cull._s["running"] = True
    cull.removeRuntime()
    pump(qapp, 0.1)
    assert ops.calls == [] and cull._toasts.items[-1]["title"] == "The AI runtime is in use" and "analysis" in cull._toasts.items[-1]["body"]
    cull._s["running"] = False
    ops.gate.clear()
    cull.modelAction("quality")                                            # an install is running
    until(qapp, lambda: cull._ai.busy)
    cull.removeRuntime()
    assert ("remove-runtime",) not in ops.calls and "model is being installed" in cull._toasts.items[-1]["body"]
    cull.cancelSetup()
    settled(qapp, cull._ai)
    cull.removeRuntime()
    settled(qapp, cull._ai)
    assert ("remove-runtime",) in ops.calls
    cull.removeRuntime()                                                   # and there is nothing to remove twice
    assert ops.calls.count(("remove-runtime",)) == 1 and "no AI runtime" in cull._toasts.items[-1]["body"]


def test_the_scripted_center_also_ends_not_set_up(qapp):
    model = CullModel(PhotoModel(parent=qapp), memory=True, parent=qapp)
    assert model.view["center"]["rtRemoveShow"]
    model.removeRuntime()
    assert model.view["center"]["rtState"] == "Not set up" and not model.view["center"]["rtRemoveShow"]


# -- the window -----------------------------------------------------------------------------------------------------------------------

def test_the_remove_button_asks_first_and_then_removes(qapp, tmp_path, ops):
    engine, window, warnings = _load_main(qapp, tmp_path)
    window.setWidth(1440)
    window.setHeight(900)
    ai = AiCenterBackend(ops, parent=qapp, auto_test=False)
    real = CullModel(engine.rootContext().contextProperty("photos"), memory=False, ai=ai, parent=qapp)
    engine.rootContext().setContextProperty("cull", real)
    until(qapp, lambda: real.view["center"]["rtState"] != "Checking…")
    real.openCenter()
    pump(qapp, 0.9)
    button = find(window, "aiRuntimeRemove")
    assert button is not None and button.property("visible")
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre(window, "aiRuntimeRemove"))
    pump(qapp, 0.6)
    card = window.findChild(QObject, "aiRuntimeRemoveCard")
    assert card is not None and card.property("opened") and "2.4 GB" in card.property("body") and ops.calls == []
    shot = os.environ.get("IT_SHOT_DIR")
    if shot:
        window.grabWindow().save(os.path.join(shot, "ai_center_remove_card.png"))
    QMetaObject.invokeMethod(card, "accepted", Q_ARG(bool, False))
    until(qapp, lambda: ("remove-runtime",) in ops.calls)
    settled(qapp, ai)
    pump(qapp, 0.3)
    assert real.view["center"]["rtState"] == "Not set up" and not find(window, "aiRuntimeRemove").property("visible")
    if shot:
        window.grabWindow().save(os.path.join(shot, "ai_center_removed.png"))
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "Required property" in w or "Unable to assign" in w], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)
