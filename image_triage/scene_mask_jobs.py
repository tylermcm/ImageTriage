"""Scene and people masks for the editor's Camera Raw Masking tab (Landscape and People), found with the models the old editor used.

The editor writes its preview picture to a PNG and asks over the host-job channel (``camera_raw_host_jobs``); these functions do the work
and leave their answer where the editor said: coverage maps as grayscale PNGs beside a ``manifest.json`` (the file the editor is pointed to).

* ``run_scene_masks``: OneFormer (ADE20k) labels the picture; every category it finds becomes ``<category>.png``.
* ``run_people_masks``: OneFormer finds where people are, SAM splits that into one mask per person, and BiRefNet polishes each person's edge
  (the SAM mask is dilated, everything outside it greyed out so BiRefNet cannot merge a touching neighbour, and the matte is clamped back).
  Each person becomes ``person-<n>.png``, largest first.

Both run on the shared mask-engine host process (one CUDA context for all three models), so only the first request in a session pays for
loading them.
"""
from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

ProgressCallback = Callable[[str], None]

# The landscape categories offered in the editor, in the order they are listed. "trees" and "foliage" are one choice there (vegetation).
LANDSCAPE_LABELS = {
    "sky": "Sky",
    "water": "Water",
    "mountains": "Mountains",
    "vegetation": "Vegetation",
    "buildings": "Buildings",
    "animals": "Animals",
}
_VEGETATION = ("trees", "foliage")
# A category covering less of the picture than this is not offered.
MIN_COVERAGE = 0.005


class Cancelled(Exception):
    """The user cancelled in the editor's popup."""


def _check(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise Cancelled()


# The editor keeps a mask map of at most 1536 px a side; the host works on a 1600 px picture. Maps are written at this size, which is
# plenty for a coverage map and keeps the files and their transfer small.
MAP_LONG_SIDE = 1024


def _save_map(array: np.ndarray, path: Path) -> None:
    image = Image.fromarray(array.astype(np.uint8), mode="L")
    longest = max(image.size)
    if longest > MAP_LONG_SIDE:
        scale = MAP_LONG_SIDE / longest
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))), Image.Resampling.BILINEAR)
    image.save(path)


def _write_manifest(path: Path, payload: dict) -> None:
    # Written last and replaced in one step, so the editor never reads half of it.
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(path)


def _gray(path: Path) -> np.ndarray:
    with Image.open(path) as opened:
        return np.asarray(opened.convert("L"), dtype=np.uint8)


def run_scene_masks(
    source: Path,
    manifest: Path,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    *,
    ensure=None,
) -> dict:
    """Label the picture and write one coverage map per landscape category found. Returns the manifest."""
    if ensure is None:
        from .semantic_masks import ensure_semantic_masks as ensure
    _check(cancel)
    result = ensure(source, progress_callback=on_progress)
    _check(cancel)
    out = manifest.parent
    out.mkdir(parents=True, exist_ok=True)
    maps: dict[str, np.ndarray] = {}
    for category, path in result.mask_paths.items():
        if Path(path).is_file():
            maps[category] = _gray(Path(path))
    entries = []
    for key, label in LANDSCAPE_LABELS.items():
        parts = [maps[c] for c in (_VEGETATION if key == "vegetation" else (key,)) if c in maps]
        if not parts:
            continue
        merged = parts[0] if len(parts) == 1 else np.maximum.reduce(parts)
        coverage = float((merged >= 128).mean())
        if coverage < MIN_COVERAGE:
            continue
        name = f"{key}.png"
        _save_map(merged, out / name)
        entries.append({"id": key, "label": label, "file": name, "coverage": round(coverage, 4)})
    entries.sort(key=lambda e: -e["coverage"])
    payload = {"kind": "scene", "categories": entries}
    _write_manifest(manifest, payload)
    return payload


# A piece this small next to a much larger person that it touches is a sleeve or a shoulder SAM cut off, not another person.
FRAGMENT_AREA_RATIO = 0.15
FRAGMENT_GAP = 0.02


def drop_fragments(people: list) -> list:
    """Remove instances that are only a small piece touching a larger one. Largest first in, largest first out."""
    kept: list = []
    for person in sorted(people, key=lambda p: -int(p.mask.sum())):
        height, width = person.mask.shape[:2]
        area = int(person.mask.sum())
        x0, y0, x1, y1 = person.bounds
        piece = False
        for big in kept:
            bx0, by0, bx1, by1 = big.bounds
            gap_x = max(0, max(x0, bx0) - min(x1, bx1)) / width
            gap_y = max(0, max(y0, by0) - min(y1, by1)) / height
            if area < FRAGMENT_AREA_RATIO * int(big.mask.sum()) and gap_x <= FRAGMENT_GAP and gap_y <= FRAGMENT_GAP:
                piece = True
                break
        if not piece:
            kept.append(person)
    return kept


def run_people_masks(
    source: Path,
    manifest: Path,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    *,
    ensure=None,
    split=None,
    refine=None,
) -> dict:
    """Find the people in the picture and write one edge-refined coverage map per person. Returns the manifest."""
    if ensure is None:
        from .semantic_masks import ensure_semantic_masks as ensure
    if split is None:
        from .people_instances import segment_people_instances as split
    if refine is None:
        from .prompt_masks import _refine_with_birefnet as refine
    _check(cancel)
    scene = ensure(source, progress_callback=on_progress)
    _check(cancel)
    out = manifest.parent
    out.mkdir(parents=True, exist_ok=True)
    people_path = scene.mask_paths.get("people")
    people: list = []
    preview_rgb = None
    if people_path is not None and Path(people_path).is_file():
        if on_progress:
            on_progress("Finding each person...")
        people = drop_fragments(list(split(source, people_mask_path=people_path, progress_callback=on_progress)))
    _check(cancel)
    entries = []
    if people:
        # The instances live in the SAM preview; refine against the same picture.
        from .prompt_masks import PROMPT_MASK_PREVIEW_EDGE, _decode_rgb_preview

        preview_rgb = _decode_rgb_preview(Path(source), PROMPT_MASK_PREVIEW_EDGE)
    work = out / "refine"
    work.mkdir(parents=True, exist_ok=True)
    for number, person in enumerate(people, start=1):
        _check(cancel)
        if on_progress:
            on_progress(f"Refining edges of person {number} of {len(people)}...")
        matte = None
        try:
            matte = refine(preview_rgb, person.mask, work, on_progress)
        except Exception:  # a failed polish keeps SAM's own mask for this person
            matte = None
        if matte is not None and float((matte >= 0.5).mean()) > 0.0:
            pixels = np.clip(matte * 255.0, 0, 255).astype(np.uint8)
        else:
            pixels = (person.mask.astype(np.uint8)) * 255
        name = f"person-{number}.png"
        _save_map(pixels, out / name)
        height, width = pixels.shape
        x0, y0, x1, y1 = person.bounds
        entries.append(
            {
                "id": f"person-{number}",
                "label": f"Person {number}",
                "file": name,
                "coverage": round(float((pixels >= 128).mean()), 4),
                "bounds": [round(x0 / width, 4), round(y0 / height, 4), round(x1 / width, 4), round(y1 / height, 4)],
                "refined": matte is not None,
            }
        )
    shutil.rmtree(work, ignore_errors=True)
    payload = {"kind": "people", "people": entries}
    _write_manifest(manifest, payload)
    return payload


def run_subject_mask(
    source: Path,
    output: Path,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    *,
    engine=None,
    fallback=None,
) -> None:
    """The subject's coverage map (BiRefNet) on the warm mask-engine host, so only the first request in a session pays for loading the
    model. If the host cannot be used the standalone one-shot worker still answers (slower: it loads the model every time)."""
    _check(cancel)
    try:
        if engine is None:
            from .ai_model import resolve_birefnet_model_installation
            from .mask_engine_service import default_mask_engine_service

            installation = resolve_birefnet_model_installation()
            if not installation.is_installed:
                raise RuntimeError("The subject model (BiRefNet) is not installed.")
            service = default_mask_engine_service()
            engine = lambda src, out, progress: service.infer_subject(  # noqa: E731
                model_dir=installation.install_dir,
                input_path=src,
                output_path=out,
                components_dir=out.parent / "subject-components",
                progress_callback=progress,
            )
        engine(source, output, on_progress)
        _check(cancel)
        return
    except Cancelled:
        raise
    except Exception:
        if fallback is None:
            from .subject_mask_service import run_subject_mask as fallback  # noqa: PLC0415
        _check(cancel)
        fallback(source, output, on_progress, cancel)
