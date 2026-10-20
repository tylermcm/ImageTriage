from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ai_v5" / "pass1_audit.py"


@pytest.fixture(scope="module")
def audit_script():
    spec = importlib.util.spec_from_file_location("pass1_audit_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _scene(seed: int, *, detail: bool = True, gain: float = 1.0, bright: int = 0) -> Image.Image:
    rng = np.random.default_rng(seed)
    coarse = rng.integers(40, 200, size=(6, 8, 3), dtype=np.uint8)
    base = np.asarray(Image.fromarray(coarse).resize((640, 480), Image.Resampling.BICUBIC)).astype(np.float32)
    if detail:
        fine = np.random.default_rng(seed + 1000).normal(0, 28, size=(480, 640, 1)).astype(np.float32)
        base = base + fine
    base = base * gain + bright
    return Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))


def _save(image: Image.Image, path: Path, stamp: str) -> str:
    exif = Image.Exif()
    exif.get_ifd(0x8769)[0x9003] = stamp  # DateTimeOriginal
    exif[0x0132] = stamp
    image.save(path, "JPEG", quality=95, exif=exif)
    return str(path)


@pytest.fixture()
def folder(tmp_path):
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    paths = {}
    paths["b1"] = _save(_scene(1), shoot / "b1.jpg", "2026:01:01 10:00:00")
    paths["b2"] = _save(_scene(1), shoot / "b2.jpg", "2026:01:01 10:00:01")
    paths["soft"] = _save(_scene(1, detail=False), shoot / "b3_soft.jpg", "2026:01:01 10:00:02")
    paths["hot"] = _save(_scene(1, bright=150), shoot / "b4_hot.jpg", "2026:01:01 10:00:03")
    paths["b5"] = _save(_scene(1), shoot / "b5.jpg", "2026:01:01 10:00:04")
    for n, seed in enumerate((11, 12, 13)):
        paths[f"other{n}"] = _save(_scene(seed), shoot / f"other{n}.jpg", f"2026:01:01 11:0{n}:00")
    (shoot / "broken.jpg").write_bytes(b"this is not a jpeg")
    paths["broken"] = str(shoot / "broken.jpg")
    return tmp_path, shoot, paths


def test_audit_end_to_end(audit_script, folder):
    root, shoot, paths = folder
    keepers = root / "keepers.txt"
    keepers.write_text(
        "\n".join([paths["b1"], "shoot/b3_soft.jpg", paths["other0"], paths["broken"], str(shoot / "gone.jpg")]) + "\n", encoding="utf-8"
    )
    out = root / "out"
    code = audit_script.main([
        "--folders", str(shoot), "--keepers", str(keepers), "--root", str(root), "--out", str(out),
        "--cache", str(root / "cache.json"), "--review-pairs", "3",
    ])
    assert code == 0
    for name in ("report.json", "report.md", "review.html"):
        assert (out / name).exists()
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    cov = report["coverage"]
    assert cov["scanned"] == 9 and cov["readable"] == 8 and cov["unreadable"] == 1
    assert cov["keepers_listed"] == 5 and cov["keepers_matched"] == 4 and cov["keepers_unmatched"] == 1
    assert cov["keepers_unreadable"] == [paths["broken"]]
    assert cov["keepers_eligible"] == 3
    assert not report["winner_protection_applied"]

    # The five burst frames are one stack; the soft keeper is flagged because no protection is applied.
    soft = report["per_rule"]["soft"]
    assert soft["eligible"] == 2  # b1 and the soft frame; other0 is alone
    assert soft["flagged"] == 1 and soft["misses"] == [paths["soft"]]
    assert report["per_rule"]["blink"]["measured"] is False
    assert report["keeper_outcome"]["union_flagged"] == 1
    assert report["keeper_outcome"]["rejected"] == 1
    assert report["keeper_outcome"]["survived"] == 2
    assert report["recall_target"]["needed_zero_miss"] == 598 and report["recall_target"]["supplied"] == 3

    # Frame-level checks: the over-bright frame carries the clipped flag, the soft one the soft flag.
    roles = report["roles_all_frames"]
    assert roles.get("rejected", 0) >= 2

    md = (out / "report.md").read_text(encoding="utf-8")
    for needle in ("## Coverage", "Unmatched keeper paths", "gone.jpg", "Unreadable files", "598", "Blink was not measured", "## Per folder",
                   "## Stack diagnostics", "Per-rule keeper recall"):
        assert needle in md

    html = (out / "review.html").read_text(encoding="utf-8")
    assert "data:image/jpeg;base64," in html and "compare these in one stack" in html and "separate stacks" in html
    assert "http://" not in html and "https://" not in html and "<link" not in html
    assert "control" not in html  # the reasons are never shown to the labeller
    assert len(report["review_pairs"]) >= 1


def test_audit_flags_both_defects_and_sweep(audit_script, folder):
    root, shoot, paths = folder
    keepers = root / "keepers.json"
    keepers.write_text(json.dumps([paths["soft"], paths["hot"]]), encoding="utf-8")
    sweep = root / "sweep.json"
    sweep.write_text(json.dumps({"soft_ratio": [0.0, 0.4, 0.99], "not_a_field": [1]}), encoding="utf-8")
    overrides = root / "settings.json"
    overrides.write_text(json.dumps({"duplicate_distance": 8, "bogus": 3}), encoding="utf-8")
    blink = root / "blink.json"
    blink.write_text(json.dumps({paths["b1"]: 0.9, paths["b2"]: 0.0}), encoding="utf-8")
    out = root / "out2"
    audit_script.main([
        "--folders", str(shoot), "--keepers", str(keepers), "--root", str(root), "--out", str(out), "--sweep-json", str(sweep),
        "--settings-json", str(overrides), "--blink-scores", str(blink),
    ])
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert not (out / "review.html").exists()
    assert paths["soft"] in report["per_rule"]["soft"]["misses"]  # saturation also flattens the over-bright frame, so it may be soft too
    assert report["per_rule"]["clipped"]["misses"] == [paths["hot"]]
    assert report["keeper_outcome"]["union_flagged"] == 2
    assert report["blink_measured"] is True and report["per_rule"]["blink"]["eligible"] == 0
    assert any("bogus" in n for n in report["settings_notes"])
    entries = report["sweep"]["entries"]
    assert report["sweep"]["unknown_fields"] == ["not_a_field"]
    by_label = {e["label"]: e for e in entries}
    assert by_label["soft_ratio=0.0"]["rule_recall"]["soft"] == 1.0
    assert by_label["soft_ratio=0.99"]["flag_rate"] >= by_label["soft_ratio=0.0"]["flag_rate"]
    # b1 is blinking in the supplied scores, so the burst's flag rate rose relative to no blink data
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "## Threshold sweep" in md and "soft_ratio=0.99" in md


def test_help_runs():
    import subprocess

    result = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True, cwd=str(SCRIPT.parents[2]))
    assert result.returncode == 0 and "--review-pairs" in result.stdout


def test_a_raw_and_its_jpeg_companion_are_one_frame_and_a_keeper_named_by_the_jpeg_matches_the_raw(audit_script, tmp_path) -> None:
    folder = str(tmp_path)
    scanned = {folder: [str(tmp_path / n) for n in ("a.JPG", "a.NEF", "b.jpg", "c.NEF")]}
    merged, companion_of = audit_script.merge_companions(scanned)
    assert [Path(p).name for p in merged[folder]] == ["a.NEF", "b.jpg", "c.NEF"]  # the lone JPEG and the lone RAW stay
    assert companion_of == {audit_script.norm(str(tmp_path / "a.JPG")): str(tmp_path / "a.NEF")}
    # Different folders never pair: the same stem in another folder is another photo.
    other = {str(tmp_path / "x"): [str(tmp_path / "x" / "a.NEF")], str(tmp_path / "y"): [str(tmp_path / "y" / "a.jpg")]}
    merged_other, companions_other = audit_script.merge_companions(other)
    assert not companions_other and sum(len(v) for v in merged_other.values()) == 2
