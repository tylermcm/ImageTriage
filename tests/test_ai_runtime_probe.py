"""Whether the AI runtime works is found by running it, not by looking at its files (audit A15).

Nothing here has a graphics card or the managed runtime: the runtime is a folder of small stand-in packages (a `torch` whose CUDA
is absent), started by the real probe helper in a real interpreter, and the AI Center is given a stand-in for the machine."""
from __future__ import annotations

import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtTest import QTest

from image_triage import ai_health, ai_probe
from image_triage.ai_env import AIRuntimeUnavailable, RuntimeSelection
from image_triage.ai_probe import ProbeResult
from image_triage.cascade.rankers import client as ranker_client
from image_triage.cascade.rankers import worker as ranker_worker
from image_triage.cascade.rankers.spec import MANIQA
from image_triage.qml_shell.ai_center import AiCenterBackend
from image_triage.qml_shell.cull_model import CullModel
from image_triage.qml_shell.photos import PhotoModel
from test_qml_shell import _load_main, pump, qapp  # noqa: F401 - the application fixture
from test_qml_shell_ai_center import FakeOps, centre, find, settled, until

FAKE_TORCH = '''
__version__ = "9.9.9+fake"
CUDA = {cuda}


class _Tensor:
    def __init__(self, value):
        self.value = value

    def __matmul__(self, other):
        return _Tensor(512.0)

    def sum(self):
        return self

    def item(self):
        return self.value


class cuda:
    @staticmethod
    def is_available():
        return CUDA

    @staticmethod
    def device_count():
        return 1 if CUDA else 0

    @staticmethod
    def get_device_name(index):
        return "Stand-in RTX"

    @staticmethod
    def synchronize(index=0):
        pass


def ones(*shape, device="cpu"):
    if str(device).startswith("cuda") and not CUDA:
        raise RuntimeError("Torch not compiled with CUDA enabled")
    return _Tensor(1.0)
'''


def managed_runtime(root: Path, *, cuda: bool, variant: str = "gpu", broken: str = "") -> RuntimeSelection:
    """A managed runtime made of stand-in packages. `broken` names a package that fails to import, with a real error."""
    site = root / "profiles" / f"{variant}-abc123" / "site-packages"
    for name in ("numpy", "PIL", "torch"):
        (site / name).mkdir(parents=True, exist_ok=True)
        text = FAKE_TORCH.format(cuda=cuda) if name == "torch" else '__version__ = "1.0"\n'
        if name == broken:
            text = 'raise ImportError("DLL load failed while importing _C: The specified module could not be found.")\n'
        (site / name / "__init__.py").write_text(textwrap.dedent(text), encoding="utf-8")
    return RuntimeSelection(variant=variant, device="cuda" if variant == "gpu" else "cpu", site_packages=(site,), profile_generation=f"{variant}-abc123", root=root)


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    """`select_runtime` answers with whatever the test installs here; with nothing installed it says so."""
    box = SimpleNamespace(selection=None, asked=[])

    def select(device="auto", **_):
        box.asked.append(device)
        if box.selection is None:
            raise AIRuntimeUnavailable("The AI runtime is not installed yet.", category="runtime_missing", remediation="Run Set Up AI.")
        return box.selection

    monkeypatch.setattr(ai_health, "select_runtime", select)
    monkeypatch.setattr(ai_health, "_SERVICE", None)
    return box


# -- the probe: a real interpreter, real imports, a real (stand-in) tensor operation -------------------------------------------------

def test_the_torch_probe_runs_an_operation_on_the_requested_device_and_reports_it(tmp_path):
    selection = managed_runtime(tmp_path, cuda=False)
    site = selection.primary_site_packages
    before = list(sys.path)
    try:
        for name in ("torch",):
            sys.modules.pop(name, None)
        on_cpu = ai_probe.run_probe(capability_key="runtime_check", site_packages=site, modules=("torch",), probe_kind="torch", requested_device="cpu")
        on_card = ai_probe.run_probe(capability_key="runtime_check", site_packages=site, modules=("torch",), probe_kind="torch", requested_device="cuda")
    finally:
        sys.modules.pop("torch", None)
        sys.path[:] = before
    assert on_cpu.ok and on_cpu.selected_device == "cpu" and on_cpu.inference_ran and on_cpu.modules["torch"] == "9.9.9+fake"
    assert not on_card.ok and on_card.category == "provider_unavailable" and "cannot see a CUDA device" in on_card.message
    assert on_card.selected_device == ""            # a request that could not be honoured is never reported as the CPU


def test_a_graphics_card_request_that_only_the_cpu_can_serve_is_reported_not_downgraded(runtime, tmp_path):
    runtime.selection = managed_runtime(tmp_path, cuda=False)
    result = ai_health.probe_runtime("auto")
    assert result["ok"] and not result["honoured"]                  # it computes, but not where it was asked to
    assert (result["requested"], result["device"]) == ("cuda", "cpu")
    assert "cannot see a CUDA device" in result["message"] and result["torch"] == "9.9.9+fake"
    assert result["identity"] == f"{tmp_path}|gpu|gpu-abc123"


def test_a_working_graphics_card_is_reported_with_the_device_that_ran(runtime, tmp_path):
    runtime.selection = managed_runtime(tmp_path, cuda=True)
    result = ai_health.probe_runtime("auto")
    assert result["ok"] and result["honoured"] and result["device"] == "cuda" and result["deviceName"] == "Stand-in RTX"
    assert result["message"] == ""


def test_a_cpu_build_passes_on_the_cpu(runtime, tmp_path):
    runtime.selection = managed_runtime(tmp_path, cuda=False, variant="cpu")
    result = ai_health.probe_runtime("auto")
    assert result["ok"] and result["honoured"] and (result["requested"], result["device"], result["deviceName"]) == ("cpu", "cpu", "")


def test_a_runtime_that_cannot_import_fails_with_the_real_error(runtime, tmp_path):
    runtime.selection = managed_runtime(tmp_path, cuda=False, broken="torch")
    result = ai_health.probe_runtime("auto")
    assert not result["ok"] and result["device"] == "" and result["category"] == "package_missing"
    assert "torch could not be imported" in result["message"] and "DLL load failed" in result["detail"]


def test_a_runtime_that_is_not_installed_is_not_started(runtime, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("nothing to start"))
    result = ai_health.probe_runtime("auto")
    assert not result["ok"] and result["category"] == "runtime_missing" and "not installed" in result["message"] and result["identity"] == ""
    assert ai_health.runtime_identity() == ""


def test_the_probe_can_be_cancelled_and_its_process_is_ended(tmp_path):
    command = [sys.executable, "-c", "import time; time.sleep(60)"]
    stop = threading.Event()
    threading.Timer(0.4, stop.set).start()
    started = time.monotonic()
    with pytest.raises(ai_health.ProbeCancelled):
        ai_health._run_cancellable(command, env=dict(__import__("os").environ), cwd=str(tmp_path), cancel=stop.is_set)
    assert time.monotonic() - started < 10
    with pytest.raises(ai_health.ProbeCancelled):   # cancelled before it began: nothing is started
        ai_health._run_cancellable(["no-such-program"], env={}, cwd=str(tmp_path), cancel=lambda: True)
    done = ai_health._run_cancellable([sys.executable, "-c", "print('ok')"], env=dict(__import__("os").environ), cwd=str(tmp_path), cancel=lambda: False)
    assert done.returncode == 0 and done.stdout.strip() == "ok"


# -- the ranker worker: an explicit request is honoured or refused, never swapped ----------------------------------------------------

def fake_torch(cuda: bool):
    return SimpleNamespace(__version__="2.9.0", cuda=SimpleNamespace(is_available=lambda: cuda))


def test_the_worker_refuses_an_explicit_graphics_card_request_it_cannot_honour():
    with pytest.raises(ranker_worker.WorkerFatal) as caught:
        ranker_worker.pick_device(fake_torch(False), "cuda")
    assert caught.value.category == "gpu_unavailable" and "graphics card was requested" in str(caught.value)
    with pytest.raises(ranker_worker.WorkerFatal):
        ranker_worker.pick_device(fake_torch(False), "cuda:1")
    assert ranker_worker.pick_device(fake_torch(True), "cuda:1") == "cuda:1"
    assert ranker_worker.pick_device(fake_torch(False), "auto") == "cpu"        # "whatever works" may still choose the CPU
    assert ranker_worker.pick_device(fake_torch(True), "auto") == "cuda"
    assert ranker_worker.pick_device(fake_torch(True), "cpu") == "cpu"
    failure = ranker_client._worker_failure(MANIQA, {"event": "error", "category": "gpu_unavailable", "message": str(caught.value)})
    assert type(failure).__name__ == "RankerUnavailable" and "graphics card was requested" in str(failure)


@pytest.mark.parametrize("asked, passed", [("auto", "auto"), ("gpu", "cuda"), ("cuda:1", "cuda"), ("cpu", "cpu")])
def test_the_launcher_pins_only_a_device_that_was_asked_for(monkeypatch, tmp_path, asked, passed):
    from image_triage import ai_env

    selection = RuntimeSelection(variant="gpu", device="cpu" if asked == "cpu" else "cuda", site_packages=(tmp_path,), profile_generation="gpu-abc123", root=tmp_path)
    monkeypatch.setattr(ai_env, "select_runtime", lambda device="auto", **_: selection)
    commands = []
    monkeypatch.setattr(ranker_client.subprocess, "Popen", lambda command, **_: commands.append(command) or "process")
    assert ranker_client.default_launcher(ranker_client.WorkerLaunch(MANIQA, tmp_path, asked)) == "process"
    assert commands[0][commands[0].index("--device") + 1] == passed


# -- the AI Center ---------------------------------------------------------------------------------------------------------------------

GPU_PC = {"gpu": "NVIDIA GeForce RTX 4070", "gpuBuild": True, "installed": ["gpu"], "torch": ["gpu"], "identity": "rt|gpu|gen1"}
ON_CARD = {"ok": True, "honoured": True, "requested": "cuda", "device": "cuda", "deviceName": "NVIDIA GeForce RTX 4070", "torch": "2.9.0",
           "identity": "rt|gpu|gen1", "category": "", "message": "", "detail": ""}
CPU_ONLY = {**ON_CARD, "honoured": False, "device": "cpu", "deviceName": "", "category": "provider_unavailable",
            "message": "The GPU AI runtime is installed but PyTorch cannot see a CUDA device.", "detail": "torch 2.9.0"}
BROKEN = {**ON_CARD, "ok": False, "honoured": False, "device": "", "deviceName": "", "torch": "", "category": "package_broken",
          "message": "torch is installed but failed to load: DLL load failed while importing _C", "detail": "ImportError: DLL load failed"}


class ProbeOps(FakeOps):
    """A PC whose runtime can be tested. `answer` is what the test finds; it takes as long as `gate` says, on the thread recorded."""

    def __init__(self, answer=None, **changes):
        super().__init__(**{**GPU_PC, **changes})
        self.answer = answer or ON_CARD
        self.threads = []

    def probe_runtime(self, cancelled):
        self.threads.append(threading.current_thread())
        self.calls.append(("probe",))
        end = time.monotonic() + 5
        while not self.gate.is_set() and time.monotonic() < end:
            if cancelled():
                raise ai_health.ProbeCancelled()
            time.sleep(0.01)
        if isinstance(self.answer, Exception):
            raise self.answer
        return dict(self.answer)


def center(qapp, ops):
    ai = AiCenterBackend(ops, parent=qapp)
    ai.refresh()
    until(qapp, lambda: ai.state()["checked"])
    return ai


def test_files_on_disk_are_shown_as_installed_and_not_tested(qapp):
    ai = center(qapp, ProbeOps())
    state = ai.state()
    assert state["rt"] == "ready" and not state["tested"] and state["probe"] is None and state["canTest"]
    cull = CullModel(PhotoModel(parent=qapp), memory=False, ai=ai, parent=qapp)
    until(qapp, lambda: cull.view["centerText"] != "Checking…")
    view = cull.view
    assert view["center"]["rtState"] == "Installed, not tested" and "has not been run in this session" in view["center"]["rtDesc"]
    assert view["center"]["gpuState"] == "Found, not tested" and "has not been tested" in view["center"]["gpuDesc"]
    assert view["center"]["rtTestShow"] and view["center"]["rtTestBtn"] == "Test runtime" and view["centerText"] == "Installed, not tested"
    cull.shutdown()


def test_the_test_runs_off_the_ui_thread_and_says_which_device_ran(qapp):
    ops = ProbeOps()
    ai = center(qapp, ops)
    cull = CullModel(PhotoModel(parent=qapp), memory=False, ai=ai, parent=qapp)
    ops.gate.clear()
    cull.testRuntime()
    until(qapp, lambda: ops.threads)
    assert ops.threads[0] is not threading.main_thread() and ai.state()["probing"]
    assert cull.view["center"]["rtState"] == "Testing…" and cull.view["center"]["rtTestBtn"] == "Cancel test"
    ops.gate.set()
    settled(qapp, ai)
    state = ai.state()
    assert state["tested"] and state["probe"]["device"] == "cuda" and state["gpuUsable"] and not state["probing"]
    view = cull.view["center"]
    assert view["rtState"] == "Working" and "ran a computation on the graphics card (NVIDIA GeForce RTX 4070)" in view["rtDesc"] and "PyTorch 2.9.0" in view["rtDesc"]
    assert view["gpuState"] == "Working" and view["rtTestBtn"] == "Test again" and cull.view["centerText"] == "Ready"
    cull.shutdown()


def test_a_card_that_cannot_be_used_is_said_so_and_is_no_longer_offered_for_ranking(qapp):
    ops = ProbeOps(CPU_ONLY)
    ai = center(qapp, ops)
    cull = CullModel(PhotoModel(parent=qapp), memory=False, ai=ai, parent=qapp)
    until(qapp, lambda: cull.view["centerText"] != "Checking…")
    assert ai.state()["gpuUsable"] and cull._s["gpu"]               # what the files say, before anything was run
    cull.testRuntime()
    settled(qapp, ai)
    state = ai.state()
    assert state["tested"] and not state["gpuUsable"] and state["rt"] == "ready"
    view = cull.view["center"]
    assert view["rtState"] == "Working on the CPU only" and view["rtDot"] == "warn"
    assert "graphics card was requested but could not be used" in view["rtDesc"] and "cannot see a CUDA device" in view["rtDesc"]
    assert view["gpuState"] == "Not usable" and "cannot see a CUDA device" in view["gpuDesc"]
    assert not cull._s["gpu"] and cull.view["centerText"] == "Ready on the CPU only"
    assert cull._ranking_decision().device != "gpu"                 # ranking is not started on a card the test showed to be unusable
    cull.shutdown()


def test_a_failed_test_shows_the_real_error(qapp):
    ops = ProbeOps(BROKEN)
    ai = center(qapp, ops)
    cull = CullModel(PhotoModel(parent=qapp), memory=False, ai=ai, parent=qapp)
    cull.testRuntime()
    settled(qapp, ai)
    view = cull.view["center"]
    assert view["rtState"] == "Test failed" and view["rtDot"] == "bad" and "DLL load failed while importing _C" in view["rtDesc"]
    assert cull.view["centerText"] == "Needs attention"
    ops.answer = RuntimeError("the helper is missing")              # the test itself could not be run: shown, not lost
    cull.testRuntime()
    settled(qapp, ai)
    assert "The test could not be run: the helper is missing" in cull.view["center"]["rtDesc"]
    cull.shutdown()


def test_cancel_stops_the_test_and_leaves_no_result(qapp):
    ops = ProbeOps()
    ai = center(qapp, ops)
    ops.gate.clear()
    ai.testRuntime()
    until(qapp, lambda: ops.threads)
    ai.cancel()
    settled(qapp, ai)
    assert not ai.state()["tested"] and not ai.state()["probing"] and ai.state()["error"] == ""
    ops.gate.set()


def test_the_result_is_kept_for_the_session_and_dropped_when_the_runtime_changes(qapp):
    ops = ProbeOps()
    ai = center(qapp, ops)
    ai.testRuntime()
    settled(qapp, ai)
    ai.refresh()                                                    # another look at the same runtime keeps it
    settled(qapp, ai)
    ai.installModel("quality")                                      # a model is not the runtime
    settled(qapp, ai)
    assert ai.state()["tested"] and ops.calls.count(("probe",)) == 1
    ai.repairRuntime()                                              # repaired: what was tested is not what is installed now
    until(qapp, lambda: ai.busy or ("runtime", "gpu", True) in ops.calls)
    settled(qapp, ai)
    assert ("runtime", "gpu", True) in ops.calls and not ai.state()["tested"]
    ai.testRuntime()
    settled(qapp, ai)
    assert ai.state()["tested"]
    ops.raw["identity"] = "rt|gpu|gen2"                             # replaced from outside the AI Center (another generation)
    ai.refresh()
    settled(qapp, ai)
    assert not ai.state()["tested"]
    ai.testRuntime()
    settled(qapp, ai)                                               # a result for a runtime that is no longer the one installed
    assert not ai.state()["tested"]
    ops.raw.update(installed=[], torch=[], gpuBuild=False, identity="")     # removed
    ai.refresh()
    settled(qapp, ai)
    assert ai.state()["rt"] == "missing" and not ai.state()["tested"]


def test_the_test_button_reaches_the_back_end(qapp, tmp_path):
    ops = ProbeOps()
    engine, window, warnings = _load_main(qapp, tmp_path)
    window.setWidth(1440)
    window.setHeight(900)
    ai = AiCenterBackend(ops, parent=qapp)
    real = CullModel(engine.rootContext().contextProperty("photos"), memory=False, ai=ai, parent=qapp)
    engine.rootContext().setContextProperty("cull", real)
    until(qapp, lambda: real.view["center"]["rtState"] != "Checking…")
    real.openCenter()
    pump(qapp, 0.9)
    button = find(window, "aiRuntimeTest")
    assert button.property("visible") and button.property("text") == "Test runtime"
    ops.gate.clear()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre(window, "aiRuntimeTest"))
    until(qapp, lambda: ("probe",) in ops.calls and ai.state()["probing"])
    pump(qapp, 0.3)
    assert find(window, "aiRuntimeTest").property("text") == "Cancel test"
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre(window, "aiRuntimeTest"))      # the same button cancels
    settled(qapp, ai)
    assert not ai.state()["tested"]
    ops.gate.set()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre(window, "aiRuntimeTest"))
    until(qapp, lambda: ai.state()["tested"])
    pump(qapp, 0.3)
    assert find(window, "aiRuntimeTest").property("text") == "Test again"
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "Required property" in w], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)
