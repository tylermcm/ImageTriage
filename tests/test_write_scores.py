from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from PIL import Image

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "ai_v5"
NAMES = ["none", "small", "blink", "group", "open"]


def _module(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ws():
    return _module("write_scores_under_test", "write_scores.py")


@pytest.fixture(scope="module")
def audit_script():
    return _module("pass1_audit_for_scores_test", "pass1_audit.py")


def _square(x0, y0, side):
    """Landmark points whose bounding box is a square (area = side**2 of the frame)."""
    return [(x0, y0), (x0 + side, y0 + side), (x0 + side / 2, y0 + side / 2)]


CLOSED = {"eyeBlinkLeft": 0.9, "eyeBlinkRight": 0.8}  # mean 0.85
OPEN = {"eyeBlinkLeft": 0.05, "eyeBlinkRight": 0.05}
FACES = {
    "none": [],
    "small": [(_square(0.1, 0.1, 0.05), CLOSED)],  # area 0.0025, below the 0.01 cut: a background face
    "blink": [(_square(0.2, 0.2, 0.3), CLOSED)],  # area 0.09
    "group": [(_square(0.0, 0.0, 0.2), OPEN), (_square(0.5, 0.5, 0.4), {"eyeBlinkLeft": 0.7, "eyeBlinkRight": 0.7}), (_square(0.1, 0.6, 0.02), CLOSED)],
    "open": [(_square(0.3, 0.3, 0.1), OPEN)],
}


def _red(index: int) -> int:
    return 30 + index * 40


def _save(path: Path, red: int) -> str:
    Image.new("RGB", (64, 48), (red, 90, 120)).save(path, "JPEG", quality=100)
    return str(path)


def fake_finder(image):
    red = image.getpixel((0, 0))[0]
    index = min(range(len(NAMES)), key=lambda i: abs(_red(i) - red))
    return FACES[NAMES[index]]


@pytest.fixture()
def shoot(tmp_path):
    folder = tmp_path / "shoot"
    folder.mkdir()
    paths = {name: _save(folder / f"{name}.jpg", _red(i)) for i, name in enumerate(NAMES)}
    (folder / "broken.jpg").write_bytes(b"not a jpeg")
    paths["broken"] = str(folder / "broken.jpg")
    return paths


def _run(ws, paths, out, finder=fake_finder, **kw):
    return ws.run(sorted(paths.values()), out, mode="inprocess", finder=finder, log=lambda _m: None, **kw)


def test_outputs_shapes_and_rules(ws, shoot, tmp_path):
    out = tmp_path / "out"
    report = _run(ws, shoot, out)
    blink = json.loads((out / "blink_scores.json").read_text())
    faces = json.loads((out / "faces.json").read_text())
    assert all(os.path.isabs(k) for k in list(blink) + list(faces))

    def key(name):
        return os.path.abspath(shoot[name])

    assert blink[key("none")] == 0.0 and blink[key("small")] == 0.0  # no prominent face scores 0.0
    assert blink[key("blink")] == pytest.approx(0.85)
    assert blink[key("group")] == pytest.approx(0.7)  # max over faces; the tiny closed face does not count
    assert blink[key("open")] == pytest.approx(0.05)
    assert key("broken") not in blink and key("broken") not in faces
    assert key("none") not in faces and key("small") not in faces  # the 0.01 cut: absent, not zero
    assert faces[key("blink")] == pytest.approx(0.09)
    assert faces[key("group")] == pytest.approx(0.16)  # the largest prominent face
    assert report["frames"] == 6 and report["readable"] == 5 and report["with_prominent_face"] == 3
    assert report["blink_scored"] == 5 and report["failed"] == 1
    assert key("broken") in report["failures"]
    assert json.loads((out / "scores_report.json").read_text())["failed"] == 1


def test_resume_skips_scored_and_retries_failures(ws, shoot, tmp_path):
    out = tmp_path / "out"
    _run(ws, shoot, out, limit=3)  # blink, broken (fails), group
    assert len(json.loads((out / "blink_scores.json").read_text())) == 2
    calls = []

    def counting(image):
        calls.append(1)
        return fake_finder(image)

    report = _run(ws, shoot, out, finder=counting, resume=True)
    assert len(calls) == 3  # none, open, small; the broken file is retried and fails before the finder
    assert report["skipped_already_scored"] == 2
    assert len(json.loads((out / "blink_scores.json").read_text())) == 5


def test_limit(ws, shoot, tmp_path):
    report = _run(ws, shoot, tmp_path / "o", limit=3)
    assert report["blink_scored"] + report["failed"] == 3
    assert report["skipped_by_limit"] == 3


def test_worker_mode_with_fake_rankers(ws, shoot, tmp_path):
    class FakeRanker:
        def __init__(self, fn):
            self.fn, self.failures, self.details = fn, {}, {}

        def __call__(self, keys):
            self.details, out = {}, {}
            for k in keys:
                if k.endswith("broken.jpg"):
                    out[k] = float("-inf")
                    self.failures[k] = "OSError: bad"
                else:
                    out[k], self.details[k] = self.fn(k)
            return out

        def close(self):
            pass

    blink = FakeRanker(lambda k: (0.8 if k.endswith("blink.jpg") else 0.0, {}))
    faces = FakeRanker(lambda k: (1.0, {"faces": [{"area": 0.2}, {"area": 0.005}] if k.endswith("blink.jpg") else []}))
    out = tmp_path / "w"
    report = ws.run(sorted(shoot.values()), out, mode="worker", blink_ranker=blink, faces_ranker=faces, log=lambda _m: None)
    assert list(json.loads((out / "faces.json").read_text()).values()) == [pytest.approx(0.2)]
    assert report["failed"] == 1 and report["blink_scored"] == 5
    assert report["failures"][os.path.abspath(shoot["broken"])] == "OSError: bad"


def test_feeds_pass1_audit(ws, audit_script, tmp_path):
    folder = tmp_path / "feed"
    folder.mkdir()
    for i in range(4):
        _save(folder / f"f{i}.jpg", _red(i))
    files = sorted(str(p) for p in folder.iterdir())
    out = tmp_path / "scores"
    blinking = files[1]

    def finder(image):
        return [(_square(0.2, 0.2, 0.3), CLOSED if image.getpixel((0, 0))[0] == _red(1) else OPEN)]

    ws.run(files, out, mode="inprocess", finder=finder, log=lambda _m: None)
    scores = json.loads((out / "blink_scores.json").read_text())
    assert scores[os.path.abspath(blinking)] >= 0.6 and len(scores) == 4
    keepers = tmp_path / "keepers.json"
    keepers.write_text(json.dumps([files[0]]))
    base = ["--folders", str(folder), "--keepers", str(keepers)]
    assert audit_script.main(base + ["--out", str(tmp_path / "a0")]) == 0
    assert json.loads((tmp_path / "a0" / "report.json").read_text())["blink_measured"] is False
    argv = base + ["--out", str(tmp_path / "a1"), "--blink-scores", str(out / "blink_scores.json"), "--faces-json", str(out / "faces.json")]
    assert audit_script.main(argv) == 0
    report = json.loads((tmp_path / "a1" / "report.json").read_text())
    assert report["blink_measured"] is True and report["blink_frames_scored"] == 4
    assert report["per_rule"]["blink"]["measured"] is True


def test_real_landmarker_if_available(ws, tmp_path):
    pytest.importorskip("mediapipe")
    model_dir = os.environ.get("IMAGE_TRIAGE_BLINK_MODEL_DIR")
    if not model_dir or not (Path(model_dir) / "face_landmarker.task").is_file():
        pytest.skip("face_landmarker.task not available")
    path = tmp_path / "x.jpg"
    Image.new("RGB", (320, 240), (120, 120, 120)).save(path)
    finder, close = ws.mediapipe_finder(Path(model_dir))
    try:
        report = ws.run([str(path)], tmp_path / "o", mode="inprocess", finder=finder, log=lambda _m: None)
    finally:
        close()
    assert report["blink_scored"] == 1
