#!/usr/bin/env python3
"""Write the two optional inputs of ``pass1_audit.py``: blink scores and face sizes, for the same folders the audit scans.

  python scripts/ai_v5/write_scores.py --folders D:\\Photos\\trip1 D:\\Photos\\trip2 --out run1

Writes into --out:
  blink_scores.json  absolute path -> eyes-closed score of the most closed prominent face (0 open .. 1 closed; a frame is a blink at >= 0.6).
                     A readable frame with no prominent face scores 0.0.
  faces.json         absolute path -> area of the largest prominent face as a fraction of the frame. A frame with no prominent face (< 0.01) is ABSENT,
                     which the audit reads as "no face".
  scores_report.json counts (frames, readable, with a prominent face, blink-scored, skipped, failures with reasons).

Pass them to the audit with ``--blink-scores run1\\blink_scores.json --faces-json run1\\faces.json``.

Two ways to run the Face Landmarker, both using the production code for the decision (``cascade/blink.py``) and for loading a preview
(``cascade/rankers/worker.load_image``: RAW through the embedded JPEG, EXIF orientation, 1280 px long edge):
  inprocess  import mediapipe in this Python and load face_landmarker.task (same options as the worker's BlinkEngine).
  worker     the app's own path: ``blink_ranker`` / ``faces_ranker`` through the managed AI runtime.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from image_triage.cascade.blink import MIN_FACE_AREA, face_area, frame_blink_score  # noqa: E402

BLINK_FILE = "blink_scores.json"
FACES_FILE = "faces.json"
REPORT_FILE = "scores_report.json"
PROGRESS_EVERY = 25
LONG_EDGE = 1280  # worker.BLINK_LONG_EDGE

# The seam: an image -> one (landmark points as (x, y) in 0..1, blendshape name -> score) per detected face.
FaceFinder = Callable[[Any], "list[tuple[list[tuple[float, float]], dict[str, float]]]"]
Loader = Callable[[str], Any]


def _load_audit():
    spec = importlib.util.spec_from_file_location("pass1_audit_for_scores", ROOT / "scripts" / "ai_v5" / "pass1_audit.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# -- scoring one frame --------------------------------------------------------------------------------------------------------------------------


def judge(faces: Sequence[tuple[Sequence[tuple[float, float]], dict[str, float]]]) -> tuple[float, float | None]:
    """(blink score, largest prominent face area or None) from a finder's output. The production rules: ``frame_blink_score`` and ``MIN_FACE_AREA``."""
    sized = [(face_area(points), shapes) for points, shapes in faces]
    blink = frame_blink_score(sized)
    prominent = [area for area, _ in sized if area >= MIN_FACE_AREA]
    return blink, (max(prominent) if prominent else None)


def default_loader(path: str):
    from image_triage.cascade.rankers.worker import load_image

    return load_image(path, LONG_EDGE)


def mediapipe_finder(model_dir: Path) -> tuple[FaceFinder, Callable[[], None]]:
    """The worker's own BlinkEngine builds the landmarker (same options); this only reads its detections."""
    from image_triage.cascade.rankers.worker import BlinkEngine

    engine = BlinkEngine(model_dir, "cpu")

    def find(image):
        pixels = engine.np.ascontiguousarray(engine.np.asarray(image))
        result = engine.landmarker.detect(engine.mp.Image(image_format=engine.mp.ImageFormat.SRGB, data=pixels))
        faces = []
        for index, landmarks in enumerate(result.face_landmarks or []):
            shapes = {c.category_name: float(c.score) for c in (result.face_blendshapes[index] if result.face_blendshapes else [])}
            faces.append(([(p.x, p.y) for p in landmarks], shapes))
        return faces

    return find, engine.close


# -- state, saving ---------------------------------------------------------------------------------------------------------------------------


class Scores:
    def __init__(self, out: Path, resume: bool) -> None:
        self.out = out
        self.blink: dict[str, float] = {}
        self.faces: dict[str, float] = {}
        self.failures: dict[str, str] = {}
        if resume:
            self.blink = _read_map(out / BLINK_FILE)
            self.faces = _read_map(out / FACES_FILE)
            try:
                previous = json.loads((out / REPORT_FILE).read_text(encoding="utf-8"))
                self.failures = {k: v for k, v in dict(previous.get("failures", {})).items() if k not in self.blink}
            except (OSError, ValueError, AttributeError, TypeError):
                self.failures = {}

    def record(self, path: str, blink: float, area: float | None) -> None:
        key = os.path.abspath(path)
        self.blink[key] = round(float(blink), 4)
        if area is not None:
            self.faces[key] = round(float(area), 5)
        else:
            self.faces.pop(key, None)
        self.failures.pop(key, None)

    def fail(self, path: str, reason: str) -> None:
        self.failures[os.path.abspath(path)] = reason

    def save(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        _write_json(self.out / BLINK_FILE, self.blink)
        _write_json(self.out / FACES_FILE, self.faces)


def _read_map(path: Path) -> dict[str, float]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return {os.path.abspath(k): float(v) for k, v in data.items()}
    except (OSError, ValueError, AttributeError, TypeError):
        return {}


def _write_json(path: Path, payload: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    os.replace(temporary, path)


# -- the two modes ---------------------------------------------------------------------------------------------------------------------------------


def score_inprocess(chunk: Sequence[str], scores: Scores, finder: FaceFinder, loader: Loader) -> None:
    for path in chunk:
        try:
            image = loader(path)
            faces = finder(image)
        except Exception as error:  # noqa: BLE001 - one bad file never stops the run
            scores.fail(path, f"{type(error).__name__}: {error}")
            continue
        blink, area = judge(faces)
        scores.record(path, blink, area)


def score_worker(chunk: Sequence[str], scores: Scores, blink_ranker, faces_ranker, notes: list[str]) -> None:
    """Production path: the blink worker gives the score; the face worker's detail gives the faces (already limited to prominent ones)."""
    values = blink_ranker(list(chunk))
    areas: dict[str, float | None] = {path: None for path in chunk}
    if faces_ranker is not None:
        try:
            faces_ranker(list(chunk))
            for path, detail in faces_ranker.details.items():
                found = [float(f["area"]) for f in detail.get("faces", ()) if float(f.get("area", 0.0)) >= MIN_FACE_AREA]
                areas[path] = max(found) if found else None
        except Exception as error:  # noqa: BLE001 - the face sizes are optional; the blink scores are not
            note = f"face worker failed ({type(error).__name__}: {error}); face sizes are missing from here on"
            if note not in notes:
                notes.append(note)
            areas = {path: None for path in chunk}
            faces_ranker.close()
            faces_ranker = None
    for path in chunk:
        value = values.get(path, float("-inf"))
        if value == float("-inf") or value != value:
            scores.fail(path, blink_ranker.failures.get(path, "unreadable image"))
        else:
            scores.record(path, value, areas.get(path))
    return faces_ranker


# -- run ---------------------------------------------------------------------------------------------------------------------------------------


def collect_paths(folders: Sequence[str], recursive: bool) -> list[str]:
    audit = _load_audit()
    scanned, _companions = audit.merge_companions(audit.scan_folders(folders, recursive))
    return [p for paths in scanned.values() for p in paths]


def run(
    paths: Sequence[str],
    out: Path,
    *,
    mode: str,
    finder: FaceFinder | None = None,
    loader: Loader | None = None,
    blink_ranker=None,
    faces_ranker=None,
    resume: bool = False,
    save_every: int = 100,
    limit: int | None = None,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    scores = Scores(out, resume)
    notes: list[str] = []
    todo_all = [p for p in paths if os.path.abspath(p) not in scores.blink]
    skipped_resume = len(paths) - len(todo_all)
    if limit is not None:
        todo_all = todo_all[: max(0, limit)]
    started = time.monotonic()
    done = since_save = 0
    next_report = PROGRESS_EVERY
    try:
        for start in range(0, len(todo_all), PROGRESS_EVERY):
            chunk = todo_all[start : start + PROGRESS_EVERY]
            if mode == "worker":
                faces_ranker = score_worker(chunk, scores, blink_ranker, faces_ranker, notes)
            else:
                score_inprocess(chunk, scores, finder, loader or default_loader)
            done += len(chunk)
            since_save += len(chunk)
            if since_save >= save_every:
                scores.save()
                since_save = 0
            if done >= next_report or done == len(todo_all):
                elapsed = time.monotonic() - started
                eta = elapsed / done * (len(todo_all) - done)
                log(f"{done}/{len(todo_all)} frames, {len(scores.failures)} failed, ETA {int(eta // 60)}m{int(eta % 60):02d}s")
                next_report = done + PROGRESS_EVERY
    finally:
        scores.save()
    wanted = {os.path.abspath(p) for p in paths}
    report = {
        "mode": mode,
        "frames": len(paths),
        "readable": sum(1 for k in scores.blink if k in wanted),
        "with_prominent_face": sum(1 for k in scores.faces if k in wanted),
        "blink_scored": sum(1 for k in scores.blink if k in wanted),
        "blinks_at_threshold": sum(1 for k, v in scores.blink.items() if k in wanted and v >= 0.6),
        "skipped_already_scored": skipped_resume,
        "skipped_by_limit": max(0, len(paths) - skipped_resume - len(todo_all)),
        "failed": len(scores.failures),
        "failures": scores.failures,
        "min_face_area": MIN_FACE_AREA,
        "notes": notes + [
            "A readable frame with no prominent face scores 0.0 in blink_scores.json and is absent from faces.json; the audit reads an absent face as 'no face'.",
            "Failed (unreadable) frames are in neither file.",
        ],
    }
    _write_json(out / REPORT_FILE, report)
    return report


def resolve_mode(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import mediapipe  # noqa: F401
    except Exception:  # noqa: BLE001 - any import failure means "use the worker"
        return "worker"
    return "inprocess"


def default_model_dir() -> Path:
    from image_triage.cascade.rankers.bundles import ensure_weights
    from image_triage.cascade.rankers.spec import BLINK

    return ensure_weights(BLINK, download=False)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Write blink_scores.json and faces.json for pass1_audit.py.")
    p.add_argument("--folders", nargs="+", required=True)
    p.add_argument("--recursive", action="store_true")
    p.add_argument("--out", default=".", help="output directory (default: the current directory)")
    p.add_argument("--model-dir", help="folder holding face_landmarker.task (default: the app's managed bundle, or IMAGE_TRIAGE_BLINK_MODEL_DIR)")
    p.add_argument("--device", default="cpu")
    p.add_argument("--mode", choices=("auto", "worker", "inprocess"), default="auto")
    p.add_argument("--limit", type=int, help="score only the first N unscored frames (a quick trial)")
    p.add_argument("--resume", action="store_true", help="keep the existing output files and skip paths already scored")
    p.add_argument("--save-every", type=int, default=100, help="write the files after this many frames")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = collect_paths(args.folders, args.recursive)
    if not paths:
        print("No images found in the given folders.", file=sys.stderr)
    mode = resolve_mode(args.mode)
    model_dir = Path(args.model_dir) if args.model_dir else None
    print(f"{len(paths)} frames, mode {mode}")
    kwargs: dict[str, Any] = {}
    closers: list[Callable[[], None]] = []
    try:
        if mode == "inprocess":
            try:
                directory = model_dir or default_model_dir()
                kwargs["finder"], close = mediapipe_finder(directory)
            except Exception as error:  # noqa: BLE001 - say what to fix, do not dump a traceback
                print(f"Cannot start the in-process landmarker: {type(error).__name__}: {error}", file=sys.stderr)
                return 2
            closers.append(close)
        else:
            from image_triage.cascade.rankers import blink_ranker, faces_ranker

            common = {"device": args.device, "download": False}
            if model_dir is not None:
                common["model_dir"] = model_dir
            kwargs["blink_ranker"] = blink_ranker(**common)
            kwargs["faces_ranker"] = faces_ranker(**common)
            closers += [kwargs["blink_ranker"].close, kwargs["faces_ranker"].close]
        try:
            report = run(paths, Path(args.out), mode=mode, resume=args.resume, save_every=args.save_every, limit=args.limit, **kwargs)
        except Exception as error:  # noqa: BLE001 - a worker that cannot start (weights, runtime)
            print(f"Scoring stopped: {type(error).__name__}: {error}", file=sys.stderr)
            return 2
    finally:
        for close in closers:
            try:
                close()
            except Exception:  # noqa: BLE001
                pass
    print(f"scored {report['blink_scored']} of {report['frames']} frames, {report['with_prominent_face']} with a prominent face, "
          f"{report['failed']} failed; wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
