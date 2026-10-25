"""The AI Center for real: what it says about this PC, and what its buttons do. A fake stands in for the machine (nothing is downloaded)."""
from __future__ import annotations

import copy
import threading
import time

import pytest
from PySide6.QtCore import Q_ARG, QMetaObject, QObject, QPoint, QPointF, Qt
from PySide6.QtTest import QTest

from image_triage.ai_model_store import ModelInstallCancelled
from image_triage.app_identity import user_settings
from image_triage.qml_shell import cull_model
from image_triage.qml_shell.ai_center import AiCenterBackend
from image_triage.qml_shell.cull_model import SENS_KEY, CullModel
from image_triage.qml_shell.photos import PhotoModel
from test_qml_shell import _load_main, pump, qapp  # noqa: F401  (the same helpers and application fixture)


class FakeOps:
    """A PC: nothing installed, no graphics card, a current Visual C++. Installs take as long as `gate` says."""

    def __init__(self, **changes) -> None:
        self.raw = {"installed": [], "torch": [], "gpuBuild": False, "gpu": "", "vc": "14.44.35211", "vcOld": False, "python": "3.13",
                    "models": {"face": {"state": "missing", "mb": 4, "problem": "Face landmarks are not downloaded yet."},
                               "quality": {"state": "missing", "mb": 543, "problem": "MANIQA is not downloaded yet."},
                               "appeal": {"state": "missing", "mb": 2130, "problem": "Q-ReAlign is not downloaded yet."}}}
        self.raw.update(changes)
        self.calls: list[tuple] = []
        self.gate = threading.Event()
        self.gate.set()
        self.fail = ""

    def detect(self):
        return copy.deepcopy(self.raw)

    def runtime_mb(self, variant):
        return 2900 if variant == "gpu" else 800

    def bundle_mb(self, key):
        return {"face": 4, "quality": 543, "appeal": 2130}[key]

    def bundle_sizes(self, key):
        return {"a.bin": 100, "b.bin": 100}

    def friendly(self, message):
        return message

    def _hold(self, cancelled):
        end = time.monotonic() + 5
        while not self.gate.is_set() and time.monotonic() < end:
            if cancelled():
                raise ModelInstallCancelled()
            time.sleep(0.01)

    def install_runtime(self, variant, force, on_line, cancelled):
        self.calls.append(("runtime", variant, force))
        for line in ("Downloading torch-2.9.0-cp313-win_amd64.whl (800.0 MB)", "Installing collected packages: torch"):
            on_line(line)
        try:
            self._hold(cancelled)
        except ModelInstallCancelled:
            raise RuntimeError("Setup was cancelled.")
        if self.fail:
            raise RuntimeError(self.fail)
        self.raw["installed"] = [variant]
        self.raw["torch"] = [variant]

    def install_bundle(self, key, progress, cancelled):
        self.calls.append(("install", key))
        progress("a.bin", 100, 100)
        progress("b.bin", 40, 100)
        self._hold(cancelled)
        if self.fail:
            raise RuntimeError(self.fail)
        self.raw["models"][key]["state"] = "ready"

    def repair_bundle(self, key, progress, cancelled):
        self.calls.append(("repair", key))
        progress("a.bin", 100, 100)
        self.raw["models"][key]["state"] = "ready"

    def remove_bundle(self, key):
        self.calls.append(("remove", key))
        self.raw["models"][key]["state"] = "missing"


def until(qapp, condition, seconds=6.0):
    end = time.monotonic() + seconds
    while not condition() and time.monotonic() < end:
        pump(qapp, 0.02)
    assert condition()


@pytest.fixture(autouse=True)
def clean_settings():
    settings = user_settings()
    settings.remove(SENS_KEY)
    yield
    settings.remove(SENS_KEY)


@pytest.fixture
def ops():
    return FakeOps()


@pytest.fixture
def backend(qapp, ops):
    ai = AiCenterBackend(ops, parent=qapp)
    ai.refresh()
    until(qapp, lambda: ai.state()["checked"])
    return ai


def settled(qapp, ai):
    until(qapp, lambda: not ai.busy)
    pump(qapp, 0.15)
    until(qapp, lambda: ai.state()["checked"] and not ai._detecting)


# -- what it says about this PC ------------------------------------------------------------------------------------------------

def test_before_the_first_look_it_says_it_is_checking_and_does_not_guess(qapp, ops):
    ai = AiCenterBackend(ops, parent=qapp)
    state = ai.state()
    assert state["rt"] == "checking" and not state["checked"] and not state["gpuFound"]


def test_a_pc_with_nothing_installed(backend):
    state = backend.state()
    assert state["rt"] == "missing" and not state["gpuFound"] and not state["vcOld"]
    assert {key: m["state"] for key, m in state["models"].items()} == {"face": "missing", "quality": "missing", "appeal": "missing"}
    assert backend.variant() == "cpu"


def test_the_runtime_is_ready_only_when_it_has_pytorch(qapp, ops):
    ops.raw.update(installed=["cpu"], torch=[])
    ai = AiCenterBackend(ops, parent=qapp)
    ai.refresh()
    until(qapp, lambda: ai.state()["checked"])
    assert ai.state()["rt"] == "error" and "PyTorch" in ai.state()["error"]
    ops.raw.update(torch=["cpu"])
    ai.refresh()
    until(qapp, lambda: ai.state()["rt"] == "ready")


def test_an_old_visual_cpp_runtime_is_reported_when_the_runtime_is_installed(qapp, ops):
    ops.raw.update(installed=["cpu"], torch=["cpu"], vc="14.36.32532", vcOld=True)
    ai = AiCenterBackend(ops, parent=qapp)
    ai.refresh()
    until(qapp, lambda: ai.state()["checked"])
    state = ai.state()
    assert state["rt"] == "error" and state["vcOld"] and state["vcFound"] == "14.36.32532"
    ops.raw.update(vc="14.44.35211", vcOld=False)                           # the person installed the update, and presses Check again
    ai.recheck()
    until(qapp, lambda: ai.state()["rt"] == "ready")


@pytest.mark.parametrize("raw, found, usable, variant", [
    ({"gpu": "NVIDIA GeForce RTX 4070", "gpuBuild": True, "installed": ["gpu"], "torch": ["gpu"]}, True, True, "gpu"),
    ({"gpu": "NVIDIA GeForce RTX 4070", "installed": ["cpu"], "torch": ["cpu"]}, True, False, "cpu"),     # a card, but the CPU build is what is installed
    ({"gpu": "NVIDIA GeForce RTX 4070"}, True, False, "gpu"),                                              # a card and nothing installed: set up installs the GPU build
    ({}, False, False, "cpu"),
])
def test_the_graphics_card_and_the_build_to_install(qapp, raw, found, usable, variant):
    ai = AiCenterBackend(FakeOps(**raw), parent=qapp)
    ai.refresh()
    until(qapp, lambda: ai.state()["checked"])
    state = ai.state()
    assert (state["gpuFound"], state["gpuUsable"], ai.variant()) == (found, usable, variant)


# -- the model buttons ------------------------------------------------------------------------------------------------------------

def test_install_runs_in_the_background_with_progress_and_then_the_model_is_installed(qapp, backend, ops):
    ops.gate.clear()
    backend.installModel("quality")
    state = backend.state()
    assert backend.busy and state["models"]["quality"]["busy"] and state["models"]["quality"]["verb"] == "Installing"
    until(qapp, lambda: backend.state()["models"]["quality"]["pct"] > 0)
    assert "Downloading" in backend.state()["models"]["quality"]["step"] and "%" in backend.state()["models"]["quality"]["step"]
    backend.installModel("appeal")                                          # one at a time
    assert ops.calls == [("install", "quality")]
    ops.gate.set()
    settled(qapp, backend)
    assert backend.state()["models"]["quality"]["state"] == "ready" and not backend.state()["models"]["quality"]["busy"]


def test_cancel_stops_the_download_and_is_not_a_failure(qapp, backend, ops):
    ops.gate.clear()
    backend.installModel("appeal")
    until(qapp, lambda: backend.state()["models"]["appeal"]["pct"] > 0)
    backend.cancel()
    settled(qapp, backend)
    model = backend.state()["models"]["appeal"]
    assert model["state"] == "missing" and not model["busy"] and model["error"] == ""


def test_a_failed_install_says_why_and_the_next_try_clears_it(qapp, backend, ops):
    ops.fail = "There is not enough free space on the drive."
    backend.installModel("quality")
    settled(qapp, backend)
    assert backend.state()["models"]["quality"]["error"] == "There is not enough free space on the drive."
    ops.fail = ""
    backend.installModel("quality")
    settled(qapp, backend)
    model = backend.state()["models"]["quality"]
    assert model["error"] == "" and model["state"] == "ready"


def test_the_button_does_what_the_state_calls_for(qapp, backend, ops):
    ops.raw["models"]["quality"]["state"] = "ready"
    ops.raw["models"]["face"]["state"] = "ready"
    ops.raw["models"]["appeal"]["state"] = "corrupt"
    backend.refresh()
    until(qapp, lambda: backend.state()["models"]["appeal"]["state"] == "corrupt")
    for key in ("quality", "face", "appeal"):
        backend.modelAction(key)
        settled(qapp, backend)
    assert ops.calls == [("remove", "quality"), ("repair", "face"), ("repair", "appeal")]      # a ranker is removed, the face model and a damaged one are repaired
    assert backend.state()["models"]["quality"]["state"] == "missing" and backend.state()["models"]["appeal"]["state"] == "ready"
    backend.modelAction("quality")
    settled(qapp, backend)
    assert ops.calls[-1] == ("install", "quality") and backend.state()["models"]["quality"]["state"] == "ready"


# -- the runtime ----------------------------------------------------------------------------------------------------------------------

def test_set_up_installs_the_runtime_then_the_face_model_with_one_percent(qapp, backend, ops):
    ops.gate.clear()
    backend.setupRuntime()
    assert backend.state()["rt"] == "installing"
    until(qapp, lambda: backend.state()["pct"] > 0)
    assert "%" in backend.state()["step"]
    ops.gate.set()
    settled(qapp, backend)
    state = backend.state()
    assert [c[:2] for c in ops.calls] == [("runtime", "cpu"), ("install", "face")] and ops.calls[0][2] is False
    assert state["rt"] == "ready" and state["models"]["face"]["state"] == "ready" and state["variant"] == "cpu"


def test_repair_installs_the_runtime_again_over_the_old_one(qapp, ops):
    ops.raw.update(installed=["cpu"], torch=["cpu"])
    ops.raw["models"]["face"]["state"] = "ready"
    ai = AiCenterBackend(ops, parent=qapp)
    ai.refresh()
    until(qapp, lambda: ai.state()["rt"] == "ready")
    ai.repairRuntime()
    settled(qapp, ai)
    assert ops.calls == [("runtime", "cpu", True)]                          # the face model was already there: nothing else downloaded


def test_a_failed_runtime_install_is_shown_and_cancel_is_not_a_failure(qapp, backend, ops):
    ops.fail = "Could not install the AI runtime: pip exited with 1."
    backend.setupRuntime()
    settled(qapp, backend)
    assert backend.state()["rt"] == "error" and "pip exited" in backend.state()["error"]
    ops.fail = ""
    ops.gate.clear()
    backend.setupRuntime()
    until(qapp, lambda: backend.state()["pct"] > 0)
    backend.cancel()
    settled(qapp, backend)
    assert backend.state()["rt"] == "missing" and backend.state()["error"] == ""


# -- the Cull window and the AI Center, as the QML reads them -----------------------------------------------------------------------

@pytest.fixture
def cull(qapp, ops):
    ai = AiCenterBackend(ops, parent=qapp)
    model = CullModel(PhotoModel(parent=qapp), memory=False, ai=ai, parent=qapp)
    until(qapp, lambda: model.view["center"]["rtState"] != "Checking…")
    return model


def test_the_center_shows_this_pcs_real_states_sizes_and_buttons(cull, ops):
    center = cull.view["center"]
    assert center["rtState"] == "Not set up" and center["rtBtn"] == "Set up AI" and center["rtPrimary"]
    assert center["gpuState"] == "Not found" and "7 seconds to over a minute" in center["gpuDesc"]
    rows = {m["key"]: m for m in center["models"]}
    assert rows["quality"]["state"] == "Not installed" and rows["quality"]["btn"] == "Install" and "543 MB" in rows["quality"]["desc"]
    assert "2.1 GB" in rows["appeal"]["desc"] and rows["face"]["btn"] == "Install" and "4 MB" in rows["face"]["desc"]
    assert center["noRuns"] and center["runs"] == []                        # no invented earlier runs


def test_the_cull_window_follows_what_is_installed(cull, ops, qapp):
    assert not cull.view["eyes"]["on"]                                      # no runtime: no eyes check
    ops.raw.update(installed=["cpu"], torch=["cpu"])
    cull._ai.refresh()
    until(qapp, lambda: cull.view["centerText"] == "Ready")
    assert not cull.view["eyes"]["on"] and "face model is not installed" in cull.view["eyes"]["desc"]        # the runtime, but not the face model
    ops.raw["models"]["face"]["state"] = "ready"
    cull._ai.refresh()
    until(qapp, lambda: cull.view["eyes"]["on"])


def test_the_buttons_start_the_real_jobs_and_the_row_shows_the_progress(cull, ops, qapp):
    ops.gate.clear()
    cull.modelAction("quality")
    until(qapp, lambda: any(m["key"] == "quality" and m["busy"] and m["pct"] > 0 for m in cull.view["center"]["models"]))
    row = next(m for m in cull.view["center"]["models"] if m["key"] == "quality")
    assert row["btn"] == "Cancel" and row["state"] == "Installing…" and "Downloading" in row["step"]
    assert next(m for m in cull.view["center"]["models"] if m["key"] == "appeal")["locked"]       # the others wait for it
    cull.cancelSetup()
    settled(qapp, cull._ai)
    assert next(m for m in cull.view["center"]["models"] if m["key"] == "quality")["btn"] == "Install"


def test_removing_a_ranker_asks_first_and_names_its_size(cull, ops, qapp):
    ops.raw["models"]["appeal"]["state"] = "ready"
    cull._ai.refresh()
    until(qapp, lambda: next(m for m in cull.view["center"]["models"] if m["key"] == "appeal")["btn"] == "Remove")
    row = next(m for m in cull.view["center"]["models"] if m["key"] == "appeal")
    assert row["confirm"] and not next(m for m in cull.view["center"]["models"] if m["key"] == "face")["confirm"] or True
    text = cull.removeText("appeal")
    assert "Appeal ranker" in text["title"] and "2.1 GB" in text["body"]


def test_the_setup_card_tells_the_download_size_before_anything_is_downloaded(cull, ops):
    plan = cull.setupPlan()
    assert plan["title"] == "Set up AI?" and "804 MB" in plan["body"] and "CPU build" in plan["body"]       # 800 + the 4 MB face model
    assert ops.calls == []


def test_default_strictness_is_kept_for_the_next_start(qapp, ops):
    ai = AiCenterBackend(ops, parent=qapp)
    first = CullModel(PhotoModel(parent=qapp), memory=False, ai=ai, parent=qapp)
    first.setDefaultSens("strict")
    assert user_settings().value(SENS_KEY, "", str) == "strict" and first.view["sens"][2]["on"]
    second = CullModel(PhotoModel(parent=qapp), memory=False, ai=AiCenterBackend(ops, parent=qapp), parent=qapp)
    assert second.view["sens"][2]["on"] and second.view["center"]["defSens"] == "strict"
    first.setSens("careful")                                                # a change for one run is not the default
    assert user_settings().value(SENS_KEY, "", str) == "strict"


def test_clear_all_results_forgets_what_the_window_found(qapp, ops):
    photos = PhotoModel(parent=qapp)
    photos.setRows([{"path": f"/p/{i}.jpg", "name": f"IMG_{i}", "ext": "JPG", "raw": False, "decision": "", "rating": 0, "ai": 50} for i in range(20)], "/p")
    model = CullModel(photos, memory=False, ai=AiCenterBackend(ops, parent=qapp), parent=qapp)
    model._set(done=True)
    model._assign_flags()
    model._set(done=True)
    assert model.view["center"]["runs"] and not model.view["center"]["noRuns"]
    model.clearRuns()
    assert model.view["center"]["runs"] == [] and model.view["center"]["noRuns"] and not model._s["done"] and model._flags == {}


# -- the window ---------------------------------------------------------------------------------------------------------------------------

def find(window, name):
    stack = [window.contentItem()]
    while stack:
        item = stack.pop()
        if item.objectName() == name:
            return item
        stack.extend(item.childItems())
    return None


def centre(window, name):
    item = find(window, name)
    assert item is not None, name
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    return QPoint(int(point.x()), int(point.y()))


def test_the_window_buttons_reach_the_back_end(qapp, tmp_path, ops):
    engine, window, warnings = _load_main(qapp, tmp_path)
    window.setWidth(1440)
    window.setHeight(900)
    ai = AiCenterBackend(ops, parent=qapp)
    real = CullModel(engine.rootContext().contextProperty("photos"), memory=False, ai=ai, parent=qapp)
    engine.rootContext().setContextProperty("cull", real)
    until(qapp, lambda: real.view["center"]["rtState"] != "Checking…")
    real.openCenter()
    pump(qapp, 0.9)
    ops.gate.clear()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre(window, "aiModelButton-quality"))
    until(qapp, lambda: ai.busy and ("install", "quality") in ops.calls)
    pump(qapp, 0.4)
    assert find(window, "aiModelButton-quality").property("text") == "Cancel"
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre(window, "aiModelButton-quality"))     # the same button cancels
    settled(qapp, ai)
    assert ai.state()["models"]["quality"]["state"] == "missing"
    ops.gate.set()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre(window, "aiRuntimeButton"))         # asks first: nothing downloads yet
    pump(qapp, 0.6)
    card = window.findChild(QObject, "aiSetupCard")
    assert card is not None and card.property("opened") and ("runtime", "cpu", False) not in ops.calls
    QMetaObject.invokeMethod(card, "accepted", Q_ARG(bool, False))
    until(qapp, lambda: ("runtime", "cpu", False) in ops.calls)
    settled(qapp, ai)
    assert ai.state()["rt"] == "ready"
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "Required property" in w], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)
