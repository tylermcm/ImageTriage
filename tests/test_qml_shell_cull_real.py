"""The Cull window's real analysis: the Widgets app's cascade, what it decides, and what is saved with the folder."""
from __future__ import annotations

import time

import pytest
from PIL import Image, ImageFilter
from PySide6.QtCore import QCoreApplication, QStandardPaths
from PySide6.QtGui import QGuiApplication

from image_triage.cascade.folder import FolderFrame, run_folder
from image_triage.cascade.model import FrameFeatures
from image_triage.qml_shell import decision_bridge as bridge_module
from image_triage.qml_shell import file_actions as fa
from image_triage.qml_shell.cull_model import CullModel
from image_triage.qml_shell.cull_real import Outcome, outcomes_from_bundle, outcomes_from_result, reason_of, stacks_of
from image_triage.qml_shell.file_actions import FileActions
from image_triage.qml_shell.menu_model import ToastModel
from image_triage.qml_shell.photos import PhotoModel


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


def pump(app, seconds=0.2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_for(app, condition, seconds=30.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pump(app, 0.05)
        if condition():
            return True
    return False


# ---- the mapping from what the cascade decides ------------------------------------------------------------------------------


def frame(name, phash, sharp, clipped, t, blink=False):
    return FolderFrame(name, FrameFeatures(name, phash, sharp, clipped, clipped, blink=blink), t)


def test_what_the_cascade_decides_becomes_flags_stacks_and_picks():
    frames = [frame("a", 0, 100.0, 0.01, 1.0), frame("b", 0b1, 20.0, 0.01, 2.0), frame("c", 0b11, 99.0, 0.01, 3.0, blink=True), frame("d", 0, 98.0, 0.01, 4.0)]
    result = run_folder(frames)
    outcomes = outcomes_from_result(result)
    reasons = {path: reason_of(o) for path, o in outcomes.items()}
    assert reasons["b"] == "soft" and reasons["c"] == "blink"
    assert reasons["a"] is None
    stacks = stacks_of([{"path": p, "name": p} for p in "abcd"], outcomes)
    assert sum(len(s) for s in stacks) == 4
    assert all(o.summary for o in outcomes.values())


def test_reasons_by_role():
    mk = lambda role, flags=(): Outcome(role, tuple(flags), "", "x", 2, 1, False)
    assert reason_of(mk("rejected", ("clipped", "soft"))) == "soft"                     # a blink, soft, clipped, blown-out order
    assert reason_of(mk("rejected", ("blink", "soft"))) == "blink"
    assert reason_of(mk("duplicate")) == "duplicate"
    assert reason_of(mk("flagged", ("blown_out",))) == "blown_out"
    assert reason_of(mk("no_pick")) == "blown_out"
    assert reason_of(mk("final")) is None and reason_of(mk("pick")) is None and reason_of(mk("cut")) is None and reason_of(mk("bracket")) is None


def test_a_saved_report_gives_the_same_outcomes(tmp_path):
    from image_triage.ai_results import load_ai_bundle
    from image_triage.cascade.export import build_rows, write_export

    paths = [str(tmp_path / f"{n}.jpg") for n in "abc"]
    for p in paths:
        Image.new("RGB", (8, 8)).save(p)
    frames = [frame(paths[0], 0, 100.0, 0.01, 1.0), frame(paths[1], 1, 20.0, 0.01, 2.0), frame(paths[2], 0, 99.0, 0.01, 3.0)]
    result = run_folder(frames)
    write_export(tmp_path / "report", build_rows(result.outcomes, paths), result)
    bundle = load_ai_bundle(tmp_path / "report")
    rows = [{"path": p, "record": None} for p in paths]
    saved = outcomes_from_bundle(bundle, rows)
    live = outcomes_from_result(result)
    assert {p: (o.role, o.flags) for p, o in saved.items()} == {p: (o.role, o.flags) for p, o in live.items()}
    assert {p: reason_of(o) for p, o in saved.items()} == {p: reason_of(o) for p, o in live.items()}
    assert len({o.cluster for o in saved.values()}) == len({o.cluster for o in live.values()})


# ---- a real run, end to end -----------------------------------------------------------------------------------------------------


def make_image(path, seed, blur=0.0, taken=0):
    from PIL import ImageDraw
    import random

    rng = random.Random(seed)
    image = Image.new("RGB", (256, 192), (200, 200, 200))
    draw = ImageDraw.Draw(image)
    for _ in range(14):                                     # the same scene for the same seed: shapes with edges, so blur changes the sharpness but not the picture
        x, y = rng.randrange(0, 200), rng.randrange(0, 140)
        draw.rectangle((x, y, x + rng.randrange(20, 70), y + rng.randrange(15, 50)), fill=(rng.randrange(256), rng.randrange(256), rng.randrange(256)))
    if blur:
        image = image.filter(ImageFilter.GaussianBlur(blur))
    exif = Image.Exif()
    exif[306] = f"2026:05:14 08:00:{taken:02d}"
    exif.get_ifd(0x8769)[36867] = f"2026:05:14 08:00:{taken:02d}"      # DateTimeOriginal: the cascade only stacks frames that have a capture time
    image.save(path, "JPEG", exif=exif)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    QStandardPaths.setTestModeEnabled(True)
    session = "qml-cull-" + tmp_path.name
    monkeypatch.setattr(bridge_module, "session_id", lambda: session)
    monkeypatch.setattr(fa, "session_id", lambda: session)
    from image_triage.recycle_bin_controller import RecycleBinController

    monkeypatch.setattr(RecycleBinController, "recycle_root_for_folder", lambda self, folder=None: tmp_path / "trash")
    monkeypatch.setattr(RecycleBinController, "is_temporary_storage_folder", lambda self, folder=None: False)
    yield
    QStandardPaths.setTestModeEnabled(False)


@pytest.fixture
def world(qapp, tmp_path):
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    # one scene shot three times (the middle one soft), then a different scene
    for index, (name, seed, blur) in enumerate((("a", 1, 0.0), ("b", 1, 5.0), ("c", 1, 0.0), ("z", 99, 0.0))):
        make_image(shoot / f"{name}.jpg", seed, blur, taken=index)
    photos = PhotoModel(parent=qapp)
    toasts = ToastModel(parent=qapp)
    files = FileActions(photos, toasts, parent=qapp)
    cull = CullModel(photos, memory=False, parent=qapp, toasts=toasts, files=files)
    cull._ai = None
    photos.loadFolder(str(shoot))
    assert wait_for(qapp, lambda: not photos.loading and photos.total == 4)
    return shoot, photos, toasts, files, cull, tmp_path


def test_an_analysis_runs_and_flags_the_soft_frame(qapp, world):
    shoot, photos, toasts, files, cull, tmp = world
    s = cull._s
    s.update(rt="missing", eyes=False, rank=False)                       # no AI runtime on this machine: the cleanup still runs
    finished = []
    cull.runFinished.connect(lambda: finished.append(True))
    cull.start()
    assert cull.view["page"] == "run" and cull.analyzing
    assert wait_for(qapp, lambda: finished), toasts.items
    v = cull.view
    assert cull._real and v["page"] == "rev" and not cull.analyzing
    flagged = {photos.get(i)["name"]: cull._flags.get(photos.get(i)["path"]) for i in range(photos.shown)}
    assert flagged["b.jpg"] == "soft", flagged
    assert flagged["z.jpg"] is None
    detail = v["det"]
    assert detail["has"] and "softer than its siblings" in detail["why"]
    assert any(c["key"] == "soft" and c["n"] == 1 for c in v["chips"])
    assert (shoot / "b.jpg").exists()                                      # nothing was moved or decided


def test_the_results_are_saved_and_found_when_the_folder_is_opened_again(qapp, world):
    shoot, photos, toasts, files, cull, tmp = world
    cull._s.update(rt="missing", eyes=False, rank=False)
    done = []
    cull.runFinished.connect(lambda: done.append(True))
    cull.start()
    assert wait_for(qapp, lambda: done)
    first = dict(cull._flags)
    other_photos = PhotoModel(parent=qapp)
    other = CullModel(other_photos, memory=False, parent=qapp, toasts=toasts)
    other._ai = None
    other_photos.loadFolder(str(shoot))
    assert wait_for(qapp, lambda: other._s["done"] and other._real), "the saved report was not found"
    assert {other_photos.row_at(other_photos.row_for_path(p))["name"]: r for p, r in other._flags.items()} == {photos.row_at(photos.row_for_path(p))["name"]: r for p, r in first.items()}
    assert other.view["steps"][2]["sub"].endswith("flagged")


def test_apply_can_move_the_flagged_frames_to_the_recycle_bin_and_undo_it(qapp, world):
    shoot, photos, toasts, files, cull, tmp = world
    cull._s.update(rt="missing", eyes=False, rank=False)
    done = []
    cull.runFinished.connect(lambda: done.append(True))
    cull.start()
    assert wait_for(qapp, lambda: done)
    cull.toApply()
    cull.setAct("bin")
    assert cull.view["applyLabel"].startswith("Move 1 to the recycle bin")
    cull.apply()
    assert cull.view["page"] == "done" and "recycle bin" in cull.view["doneTitle"]
    assert not (shoot / "b.jpg").exists() and (tmp / "trash" / "b.jpg").exists()
    assert "b.jpg" not in [photos.get(i)["name"] for i in range(photos.shown)]
    cull.undo()
    assert wait_for(qapp, lambda: (shoot / "b.jpg").exists())


def test_apply_as_rejects_marks_them_and_undo_takes_that_back(qapp, world):
    shoot, photos, toasts, files, cull, tmp = world
    cull._s.update(rt="missing", eyes=False, rank=False)
    done = []
    cull.runFinished.connect(lambda: done.append(True))
    cull.start()
    assert wait_for(qapp, lambda: done)
    cull.toApply()
    cull.apply()
    assert photos.rejCount == 1 and (shoot / "b.jpg").exists()
    cull.undo()
    assert photos.rejCount == 0


def test_a_winner_is_never_flagged(qapp, world):
    shoot, photos, toasts, files, cull, tmp = world
    photos.setDecision(next(i for i in range(photos.shown) if photos.get(i)["name"] == "b.jpg"), "win")
    cull._s.update(rt="missing", eyes=False, rank=False)
    done = []
    cull.runFinished.connect(lambda: done.append(True))
    cull.start()
    assert wait_for(qapp, lambda: done)
    flagged = {photos.get(i)["name"] for i in range(photos.shown) if cull._flags.get(photos.get(i)["path"])}
    assert "b.jpg" not in flagged                                          # it would have been flagged soft; the winner is kept


def test_a_run_that_cannot_finish_says_so(qapp, world, monkeypatch):
    shoot, photos, toasts, files, cull, tmp = world
    monkeypatch.setattr(cull._run, "start", lambda **kw: (_ for _ in ()).throw(RuntimeError("no disk")))
    cull.start()
    assert cull.view["page"] == "prep" and not cull.analyzing
    assert toasts.items[-1]["kind"] == "error" and "no disk" in toasts.items[-1]["body"]


def test_sample_photos_keep_the_scripted_run(qapp):
    photos = PhotoModel(parent=qapp)
    photos.setRows([{"path": f"x/{n}.jpg", "name": n, "ext": "JPG", "raw": False, "decision": "", "rating": 0, "ai": 1} for n in ("DSC_4207", "DSC_4210")], "sample")
    cull = CullModel(photos, memory=True, parent=qapp)
    cull.start()
    assert cull.analyzing and cull._real is False
