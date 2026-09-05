"""Face-only people pass sandbox.

Runs InsightFace face detection + identity embedding over a folder (using the
same preview extraction the app uses, so RAW files work), persists faces through
the production ``upsert_faces`` path into an in-memory SQLite, clusters them with
the production ``cluster_face_identities``, and reports timing + clustering
quality. Optionally writes an HTML contact sheet grouped by person so clustering
can be eyeballed.

The goal is to answer, before wiring anything into the app: is a background
face-only pass on folder-open fast enough, and does clustering produce sensible
people groupings?

Usage (from repo root):
    .msi_build_venv\\Scripts\\python.exe -m sandboxes.face_tagging.cli \\
        --folder "C:\\path\\to\\photos" --limit 300 --html results\\people.html
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

# The sandbox is run against the app's own AI runtime cache; make sure the
# per-user paths resolve the same way the app resolves them.
os.environ.setdefault("USERPROFILE", str(Path.home()))
os.environ.setdefault(
    "LOCALAPPDATA", str(Path(os.environ["USERPROFILE"]) / "AppData" / "Local")
)

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"

RAW_EXTENSIONS = {".nef", ".arw", ".cr2", ".cr3", ".crw", ".dng", ".gpr", ".raf", ".rw2"}
IMAGE_EXTENSIONS = RAW_EXTENSIONS | {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}

# Zero-shot person-presence prototypes. Averaged into two class centroids; an
# image is "likely to contain a person" when it is closer to the person centroid
# than the no-person centroid (by the swept margin/probability).
PERSON_PROMPTS = (
    "a photo of a person",
    "a photo of people",
    "a photo of a man",
    "a photo of a woman",
    "a photo of a child",
    "a photo of a group of people",
    "a portrait of a person",
    "a candid photo of people",
)
NO_PERSON_PROMPTS = (
    "a landscape photo with no people",
    "a photo of scenery",
    "a photo of food",
    "a photo of an animal",
    "a photo of a building",
    "a close-up photo of an object",
    "an empty room with no people",
    "a photo of a plant",
)


def _ensure_ai_runtime_on_path(device: str) -> None:
    """Make the separately installed ONNX Runtime + InsightFace importable."""
    try:
        from image_triage.ai_runtime_packages import resolve_ai_runtime_site_packages
    except Exception:
        return
    for site_packages in resolve_ai_runtime_site_packages(device=device):
        text = str(site_packages)
        if text not in sys.path:
            sys.path.append(text)


def _iter_images(root: Path, limit: int | None) -> list[Path]:
    found: list[Path] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir())
        except (PermissionError, FileNotFoundError):
            continue
        for entry in entries:
            name = entry.name
            if name.startswith(".") or name == "__pycache__":
                continue
            if entry.is_dir():
                stack.append(entry)
            elif entry.is_file() and entry.suffix.lower() in IMAGE_EXTENSIONS:
                found.append(entry)
                if limit is not None and len(found) >= limit:
                    return found
    return found


AURAFACE_REPO = "fal/AuraFace-v1"
AURAFACE_FILES = (
    "scrfd_10g_bnkps.onnx",
    "2d106det.onnx",
    "genderage.onnx",
    "glintr100.onnx",
    "1k3d68.onnx",
)


def _ensure_auraface(root: Path) -> Path | None:
    """Ensure the AuraFace pack exists at ``<root>/models/auraface`` and return root.

    InsightFace ``FaceAnalysis(name='auraface', root=<root>)`` expects the ONNX
    files under ``<root>/models/auraface/``. Downloads from the Apache-2.0 repo
    on first use.
    """
    root = root.expanduser().resolve()
    pack_dir = root / "models" / "auraface"
    if (pack_dir / "glintr100.onnx").exists():
        return root
    pack_dir.mkdir(parents=True, exist_ok=True)
    print(f"Downloading AuraFace pack ({AURAFACE_REPO}) into {pack_dir} ...")
    try:
        from huggingface_hub import hf_hub_download

        for filename in AURAFACE_FILES:
            hf_hub_download(
                repo_id=AURAFACE_REPO,
                filename=filename,
                local_dir=str(pack_dir),
            )
    except Exception as exc:
        print(
            "error: could not download AuraFace automatically "
            f"({exc}).\nFetch it manually, e.g.:\n"
            f'  huggingface-cli download {AURAFACE_REPO} '
            f'--local-dir "{pack_dir}"\n'
            "(the five .onnx files must land directly in that folder), then retry.",
            file=sys.stderr,
        )
        return None
    return root if (pack_dir / "glintr100.onnx").exists() else None


def _build_person_filter(device: str):
    """Build the TinyCLIP image encoder + person/no-person text prototypes.

    Returns ``(image_extractor, person_proto, noperson_proto)`` or ``None`` if
    the CLIP models are unavailable.
    """
    try:
        from aiculler.features import SemanticEmbeddingExtractor
        from aiculler.text_scoring import CLIPTextEncoder
        from image_triage.aiculler_workflow import default_aiculler_runtime
    except Exception:
        return None
    try:
        runtime = default_aiculler_runtime(workers=1, device=device)
        vision = Path(runtime.clip_vision_model)
        text = Path(runtime.clip_text_model)
        tokenizer = Path(runtime.tokenizer)
        if not (vision.exists() and text.exists() and tokenizer.exists()):
            return None
        image_extractor = SemanticEmbeddingExtractor(
            vision,
            clip_fallback_onnx_path=runtime.clip_fallback_vision_model,
            intra_op_num_threads=4,
        )
        text_encoder = CLIPTextEncoder(
            text,
            tokenizer,
            fallback_text_onnx_path=runtime.clip_fallback_text_model,
        )
    except Exception as exc:
        print(f"warning: person pre-filter unavailable ({exc}); running faces on all images", file=sys.stderr)
        return None

    def prototype(prompts):
        vectors = []
        for prompt in prompts:
            vec = np.asarray(text_encoder.encode(prompt), dtype=np.float32).reshape(-1)
            norm = float(np.linalg.norm(vec))
            if norm > 0.0:
                vec = vec / norm
            vectors.append(vec)
        mean = np.mean(np.vstack(vectors), axis=0)
        norm = float(np.linalg.norm(mean))
        return mean / norm if norm > 0.0 else mean

    return image_extractor, prototype(PERSON_PROMPTS), prototype(NO_PERSON_PROMPTS)


def _person_probability(image_embed: np.ndarray, person_proto, noperson_proto, *, scale: float = 100.0) -> float:
    vec = np.asarray(image_embed, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vec))
    if norm > 0.0:
        vec = vec / norm
    sim_person = float(np.dot(vec, person_proto))
    sim_none = float(np.dot(vec, noperson_proto))
    logits = np.array([sim_person, sim_none], dtype=np.float64) * scale
    logits -= logits.max()
    exp = np.exp(logits)
    return float(exp[0] / exp.sum())


def _laplacian_variance(gray: np.ndarray) -> float:
    """Cheap focus metric: variance of a 4-neighbour Laplacian on a crop."""
    if gray.ndim != 2 or gray.shape[0] < 3 or gray.shape[1] < 3:
        return 0.0
    g = gray.astype(np.float32)
    lap = (
        4.0 * g[1:-1, 1:-1]
        - g[:-2, 1:-1]
        - g[2:, 1:-1]
        - g[1:-1, :-2]
        - g[1:-1, 2:]
    )
    return float(lap.var())


def _build_salience_engine(device: str):
    """Load the BiRefNet salience engine once, or None if unavailable."""
    try:
        from image_triage import birefnet_worker
        from image_triage.ai_model import resolve_birefnet_model_installation
    except Exception:
        return None
    installation = resolve_birefnet_model_installation()
    if not installation.is_installed:
        print("warning: BiRefNet salience model not installed; --salience ignored.", file=sys.stderr)
        return None
    try:
        engine = birefnet_worker._BiRefNetEngine(device)
        engine.load_model(Path(installation.install_dir))
    except Exception as exc:
        print(f"warning: could not load BiRefNet ({exc}); --salience ignored.", file=sys.stderr)
        return None
    return birefnet_worker, engine, Path(installation.install_dir)


def _salience_map(salience, preview_path: Path, cache_dir: Path) -> np.ndarray | None:
    """Return a 0..1 salience matte for the preview image, or None on failure."""
    birefnet_worker, engine, model_dir = salience
    out_path = cache_dir / (preview_path.stem + "_matte.png")
    try:
        birefnet_worker.generate_subject_mask(
            model_dir=model_dir,
            input_path=preview_path,
            output_path=out_path,
            components_dir=None,
            requested_device=engine.requested_device if hasattr(engine, "requested_device") else "cpu",
            engine=engine,
            emit_result=False,
        )
        with Image.open(out_path) as matte:
            arr = np.asarray(matte.convert("L"), dtype=np.float32) / 255.0
        return arr
    except Exception:
        return None


def _face_subject_metrics(bgr: np.ndarray, faces, salience_map: np.ndarray | None) -> list[dict]:
    """Per-face signals for subject-vs-background triage."""
    height, width = bgr.shape[:2]
    image_area = float(max(1, width * height))
    gray_full = bgr[:, :, ::-1].mean(axis=2)
    areas = []
    for face in faces:
        x1, y1, x2, y2 = face.bbox
        areas.append(max(0.0, (x2 - x1)) * max(0.0, (y2 - y1)))
    max_area = max(areas) if areas else 1.0
    rows: list[dict] = []
    for face, area in zip(faces, areas):
        x1, y1, x2, y2 = (int(round(v)) for v in face.bbox)
        x1 = max(0, min(x1, width - 1))
        x2 = max(x1 + 1, min(x2, width))
        y1 = max(0, min(y1, height - 1))
        y2 = max(y1 + 1, min(y2, height))
        crop = gray_full[y1:y2, x1:x2]
        salience_mean = None
        if salience_map is not None:
            sh, sw = salience_map.shape[:2]
            # scale bbox into the matte's coordinate space
            mx1 = int(x1 * sw / width)
            mx2 = max(mx1 + 1, int(x2 * sw / width))
            my1 = int(y1 * sh / height)
            my2 = max(my1 + 1, int(y2 * sh / height))
            salience_mean = float(salience_map[my1:my2, mx1:mx2].mean())
        rows.append(
            {
                "bbox": (x1, y1, x2, y2),
                "det_score": float(face.det_score),
                "area_frac": area / image_area,
                "rel_area": (area / max_area) if max_area else 0.0,
                "sharpness": _laplacian_variance(crop),
                "salience": salience_mean,
            }
        )
    return rows


def _keeps_face(metrics: dict, *, min_face_frac: float, min_rel_area: float, min_salience: float) -> bool:
    if metrics["area_frac"] < min_face_frac:
        return False
    if metrics["rel_area"] < min_rel_area:
        return False
    if metrics["salience"] is not None and metrics["salience"] < min_salience:
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Face-only people pass sandbox")
    parser.add_argument("--folder", required=True, help="Folder of photos to scan")
    parser.add_argument("--limit", type=int, default=None, help="Max images to process")
    parser.add_argument("--threshold", type=float, default=0.62, help="Clustering cosine threshold")
    parser.add_argument("--min-conf", type=float, default=0.0, help="Min face detection confidence for clustering")
    parser.add_argument(
        "--cluster-sweep",
        default="",
        help="Comma-separated clustering thresholds to compare, e.g. 0.35,0.40,0.45,0.50,0.62",
    )
    parser.add_argument("--det-size", type=int, default=640, help="Face detection size")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument(
        "--auraface-root",
        type=Path,
        default=ROOT / "models" / "auraface_root",
        help="Directory holding models/auraface/*.onnx (auto-downloaded if missing)",
    )
    parser.add_argument("--html", type=Path, default=None, help="Write a people-cluster contact sheet here")
    parser.add_argument("--per-cluster", type=int, default=12, help="Max thumbnails per person in the HTML")
    parser.add_argument("--faces-html", type=Path, default=None, help="Write a KEPT/DROPPED face-crop sheet here")
    parser.add_argument("--salience", action="store_true", help="Use BiRefNet salience overlap as a subject signal")
    parser.add_argument("--min-face-frac", type=float, default=0.015, help="Drop faces smaller than this fraction of the image")
    parser.add_argument("--min-rel-area", type=float, default=0.30, help="Drop faces smaller than this fraction of the largest face in the image")
    parser.add_argument("--min-salience", type=float, default=0.15, help="Drop faces whose mean salience is below this (only with --salience)")
    args = parser.parse_args(argv)

    folder = Path(args.folder).expanduser()
    if not folder.is_dir():
        print(f"error: not a folder: {folder}", file=sys.stderr)
        return 2

    _ensure_ai_runtime_on_path(args.device)

    try:
        from aiculler.features import PreviewExtractor
        from aiculler.storage import SQLiteFeatureStore
        from image_triage.people_search import cluster_face_identities
        from image_triage.quality.face import FaceQualityAnalyzer
        from image_triage.quality.store import fetch_faces, upsert_faces
    except Exception as exc:  # pragma: no cover - environment probe
        print(f"error: could not import app modules: {exc}", file=sys.stderr)
        return 2

    ctx_id = 0 if args.device == "cuda" else -1
    providers = (
        ["CUDAExecutionProvider", "CPUExecutionProvider"]
        if args.device == "cuda"
        else ["CPUExecutionProvider"]
    )
    model_root = _ensure_auraface(args.auraface_root)
    if model_root is None:
        return 2
    print("recognition backend: AuraFace (Apache-2.0, commercial-safe)")
    analyzer = FaceQualityAnalyzer(
        det_size=args.det_size,
        ctx_id=ctx_id,
        providers=providers,
        enable_identity=True,
        name="auraface",
        root=str(model_root),
    )
    if not analyzer.available:
        print(
            "error: the AuraFace pack is unavailable.\n"
            "  - Install the AI runtime (AI > Runtime And Cache > Install AI Runtime), then retry.\n"
            "  - The pack auto-downloads to --auraface-root on first run.",
            file=sys.stderr,
        )
        return 2

    images = _iter_images(folder, args.limit)
    if not images:
        print(f"No supported images found under {folder}")
        return 1
    print(f"Processing {len(images)} image(s) from {folder} on {args.device}...")

    person_filter = _build_person_filter(args.device)
    if person_filter is None:
        print("note: person pre-filter disabled (CLIP models missing); measuring faces only.")
    else:
        print("person pre-filter: TinyCLIP zero-shot person-presence enabled.")

    salience = _build_salience_engine(args.device) if args.salience else None
    if args.salience and salience is not None:
        print("subject filter: BiRefNet salience overlap enabled.")

    collect_subject = bool(args.faces_html) or salience is not None
    crops_dir = RESULTS_DIR / "faces"
    if args.faces_html:
        import shutil

        if crops_dir.exists():
            shutil.rmtree(crops_dir, ignore_errors=True)
        crops_dir.mkdir(parents=True, exist_ok=True)

    store = SQLiteFeatureStore(":memory:")
    decode_ms: list[float] = []
    detect_ms: list[float] = []
    clip_ms: list[float] = []
    images_with_faces = 0
    total_faces = 0
    faces_with_identity = 0
    decode_failures = 0
    # (person_probability, had_taggable_face) per image, for the filter sweep.
    filter_samples: list[tuple[float, bool]] = []
    subject_faces: list[dict] = []  # per-face subject-vs-background rows

    with tempfile.TemporaryDirectory(prefix="face_sandbox_") as cache_dir:
        preview_extractor = PreviewExtractor(cache_dir)
        for position, source in enumerate(images, start=1):
            try:
                decode_start = time.perf_counter()
                preview_path, _ = preview_extractor.extract(source)
                with Image.open(preview_path) as opened:
                    rgb = np.asarray(opened.convert("RGB"), dtype=np.uint8)
                bgr = rgb[:, :, ::-1]
                decode_ms.append((time.perf_counter() - decode_start) * 1000.0)
            except Exception:
                decode_failures += 1
                continue

            detect_start = time.perf_counter()
            result = analyzer.analyze(bgr)
            detect_ms.append((time.perf_counter() - detect_start) * 1000.0)

            faces = list(result.get("faces") or [])
            if faces:
                images_with_faces += 1
            total_faces += len(faces)
            identity_faces = sum(1 for face in faces if face.identity_embedding)
            faces_with_identity += identity_faces

            if person_filter is not None:
                image_extractor, person_proto, noperson_proto = person_filter
                try:
                    clip_start = time.perf_counter()
                    embed = image_extractor.encode_image(preview_path)
                    clip_ms.append((time.perf_counter() - clip_start) * 1000.0)
                    prob = _person_probability(embed, person_proto, noperson_proto)
                    filter_samples.append((prob, identity_faces > 0))
                except Exception:
                    pass

            if faces and collect_subject:
                salience_map = _salience_map(salience, preview_path, Path(cache_dir)) if salience else None
                for index, metrics in enumerate(_face_subject_metrics(bgr, faces, salience_map)):
                    keep = _keeps_face(
                        metrics,
                        min_face_frac=args.min_face_frac,
                        min_rel_area=args.min_rel_area,
                        min_salience=args.min_salience,
                    )
                    metrics["keep"] = keep
                    metrics["source"] = str(source)
                    if args.faces_html:
                        x1, y1, x2, y2 = metrics["bbox"]
                        crop_name = f"{position:04d}_{index}.jpg"
                        try:
                            Image.fromarray(rgb[y1:y2, x1:x2]).save(crops_dir / crop_name, "JPEG")
                            metrics["crop"] = crop_name
                        except Exception:
                            metrics["crop"] = None
                    subject_faces.append(metrics)

            image_id = store.ensure_image(source)
            upsert_faces(store.connection, image_id, faces)
            store.connection.commit()

            if position % 25 == 0 or position == len(images):
                print(f"  {position}/{len(images)} processed...", flush=True)

    clusters = cluster_face_identities(
        store.connection,
        threshold=args.threshold,
        min_face_confidence=args.min_conf,
    )
    clusters = sorted(clusters, key=lambda cluster: cluster.face_count, reverse=True)

    _print_report(
        images=images,
        decode_ms=decode_ms,
        detect_ms=detect_ms,
        clip_ms=clip_ms,
        images_with_faces=images_with_faces,
        total_faces=total_faces,
        faces_with_identity=faces_with_identity,
        decode_failures=decode_failures,
        clusters=clusters,
        device=args.device,
    )
    _print_filter_sweep(filter_samples, detect_mean_ms=(statistics.mean(detect_ms) if detect_ms else 0.0))
    if subject_faces:
        _print_subject_filter(subject_faces, salience_enabled=salience is not None)
    if args.faces_html and subject_faces:
        faces_html_path = args.faces_html if args.faces_html.is_absolute() else (RESULTS_DIR / args.faces_html.name)
        _write_faces_html(subject_faces, faces_html_path, crops_dir)
        print(f"\nWrote face KEPT/DROPPED sheet: {faces_html_path}")

    if args.html is not None:
        html_path = args.html if args.html.is_absolute() else (RESULTS_DIR / args.html.name)
        _write_html(store.connection, clusters, html_path, per_cluster=args.per_cluster)
        print(f"\nWrote HTML contact sheet: {html_path}")

    if args.cluster_sweep.strip():
        thresholds = []
        for token in args.cluster_sweep.split(","):
            token = token.strip()
            if token:
                try:
                    thresholds.append(float(token))
                except ValueError:
                    pass
        # Runs last: it re-clusters at each threshold and mutates cluster_id.
        _print_cluster_sweep(store.connection, cluster_face_identities, thresholds, args.min_conf)

    store.close()
    return 0


def _stats(values: list[float]) -> tuple[float, float, float, float]:
    if not values:
        return (0.0, 0.0, 0.0, 0.0)
    ordered = sorted(values)
    p50 = statistics.median(ordered)
    p90 = ordered[min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1))))]
    return (sum(values), statistics.mean(values), p50, p90)


def _print_report(
    *,
    images,
    decode_ms,
    detect_ms,
    clip_ms,
    images_with_faces,
    total_faces,
    faces_with_identity,
    decode_failures,
    clusters,
    device,
) -> None:
    processed = len(detect_ms)
    detect_total, detect_mean, detect_p50, detect_p90 = _stats(detect_ms)
    _decode_total, decode_mean, _decode_p50, _decode_p90 = _stats(decode_ms)
    _clip_total, clip_mean, _clip_p50, _clip_p90 = _stats(clip_ms)
    wall_total_ms = detect_total + _decode_total
    imgs_per_sec_detect = (processed / (detect_total / 1000.0)) if detect_total else 0.0
    imgs_per_sec_wall = (processed / (wall_total_ms / 1000.0)) if wall_total_ms else 0.0

    print("\n==================== FACE PASS REPORT ====================")
    print(f"device                : {device}")
    print(f"images found          : {len(images)}")
    print(f"images processed      : {processed}")
    print(f"decode failures       : {decode_failures}")
    print(f"images with >=1 face  : {images_with_faces}")
    print(f"total faces           : {total_faces}")
    print(f"faces with identity   : {faces_with_identity}")
    print("---- timing (per image) ----")
    print(f"decode+preview  mean  : {decode_mean:8.1f} ms")
    if clip_ms:
        print(f"tinyclip embed  mean  : {clip_mean:8.1f} ms  (already computed by semantic index in production)")
    print(f"face detect+embed mean: {detect_mean:8.1f} ms  (p50 {detect_p50:.1f}, p90 {detect_p90:.1f})")
    print(f"throughput (detect)   : {imgs_per_sec_detect:6.1f} img/s")
    print(f"throughput (wall)     : {imgs_per_sec_wall:6.1f} img/s")
    for count in (1000, 5000):
        if imgs_per_sec_wall:
            print(f"projected {count:>5} imgs   : {count / imgs_per_sec_wall:7.1f} s wall")
    print("---- clustering ----")
    print(f"threshold             : (see args)")
    print(f"people clusters       : {len(clusters)}")
    singletons = sum(1 for cluster in clusters if cluster.face_count == 1)
    print(f"singleton clusters    : {singletons}")
    print("top clusters (faces)  :")
    for cluster in clusters[:15]:
        print(f"    cluster {cluster.cluster_id:>4}: {cluster.face_count} face(s)")
    print("==========================================================")


def _print_filter_sweep(samples: list[tuple[float, bool]], *, detect_mean_ms: float) -> None:
    if not samples:
        return
    total = len(samples)
    face_total = sum(1 for _prob, had_face in samples if had_face)
    print("\n=============== PERSON PRE-FILTER (TinyCLIP) ===============")
    print(f"images scored         : {total}")
    print(f"images with a taggable face (InsightFace ground truth): {face_total}")
    # Faces CLIP scores near-zero can't be recovered by lowering the threshold —
    # that gap is a prototype limitation, not a threshold-tuning problem.
    hard_misses = sum(1 for prob, had_face in samples if had_face and prob < 0.05)
    max_recall = ((face_total - hard_misses) / face_total) if face_total else float("nan")
    print(f"face images with person-prob < 0.05 (threshold cannot recover): {hard_misses}")
    print(f"max achievable recall (threshold -> 0): {max_recall:.2f}")
    print("Would gate InsightFace to only images CLIP flags as containing a person.")
    print("  thr  flagged   %run  recall  precision   IF-calls-saved   est-time-saved")
    for threshold in (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90):
        flagged = sum(1 for prob, _f in samples if prob >= threshold)
        tp = sum(1 for prob, had_face in samples if had_face and prob >= threshold)
        fn = sum(1 for prob, had_face in samples if had_face and prob < threshold)
        fp = flagged - tp
        recall = (tp / (tp + fn)) if (tp + fn) else float("nan")
        precision = (tp / flagged) if flagged else float("nan")
        saved = total - flagged
        time_saved_s = saved * detect_mean_ms / 1000.0
        print(
            f"  {threshold:.2f}  {flagged:6d}  {100.0 * flagged / total:5.0f}%"
            f"   {recall:5.2f}     {precision:5.2f}      {saved:6d} ({100.0 * saved / total:3.0f}%)"
            f"     ~{time_saved_s:6.1f}s"
        )
    print("recall = fraction of real faces still sent to InsightFace (want ~1.0).")
    print("Pick the lowest threshold that keeps recall high; that maximizes load saved")
    print("without dropping people. False positives (low precision) are harmless — ")
    print("InsightFace simply finds no face and they are discarded.")
    print("===========================================================")


def _print_cluster_sweep(connection, cluster_fn, thresholds: list[float], min_conf: float) -> None:
    if not thresholds:
        return
    print("\n=============== CLUSTER THRESHOLD SWEEP ===============")
    print("Lower threshold = looser = fewer, larger clusters (more merging).")
    print("  thr   clusters  singletons  largest")
    for threshold in thresholds:
        # Fresh clustering each time: clear prior clusters so nothing is reused.
        connection.execute("DELETE FROM face_identity_clusters")
        connection.execute("UPDATE image_faces SET cluster_id = NULL")
        connection.commit()
        clusters = cluster_fn(connection, threshold=threshold, min_face_confidence=min_conf)
        singles = sum(1 for cluster in clusters if cluster.face_count == 1)
        largest = max((cluster.face_count for cluster in clusters), default=0)
        print(f"  {threshold:.2f}   {len(clusters):6d}    {singles:6d}     {largest:6d}")
    print("Pick the threshold whose cluster count / largest match the true people.")
    print("======================================================")


def _print_subject_filter(faces: list[dict], *, salience_enabled: bool) -> None:
    total = len(faces)
    kept = sum(1 for face in faces if face["keep"])
    dropped = total - kept
    print("\n=============== SUBJECT vs BACKGROUND FILTER ===============")
    print(f"faces evaluated       : {total}")
    print(f"kept (subject)        : {kept}")
    print(f"dropped (background)  : {dropped}  ({100.0 * dropped / total:.0f}%)")
    print(f"salience signal       : {'on (BiRefNet)' if salience_enabled else 'off (size + relative size only)'}")
    areas = sorted(face["area_frac"] for face in faces)
    print(f"face-area fraction    : min {areas[0]:.4f}  median {areas[len(areas) // 2]:.4f}  max {areas[-1]:.4f}")
    print("Open the --faces-html sheet to confirm dropped faces are truly background.")
    print("===========================================================")


def _write_faces_html(faces: list[dict], html_path: Path, crops_dir: Path) -> None:
    html_path.parent.mkdir(parents=True, exist_ok=True)

    def card(face: dict) -> str:
        crop = face.get("crop")
        if not crop:
            return ""
        try:
            uri = (crops_dir / crop).as_uri()
        except ValueError:
            uri = str(crops_dir / crop)
        sal = face["salience"]
        sal_text = "-" if sal is None else f"{sal:.2f}"
        label = (
            f"area {face['area_frac'] * 100:.1f}% · rel {face['rel_area']:.2f} · "
            f"sharp {face['sharpness']:.0f} · sal {sal_text} · det {face['det_score']:.2f}"
        )
        return (
            "<figure><img src='" + uri + "' loading='lazy'>"
            "<figcaption>" + label + "</figcaption></figure>"
        )

    kept = [f for f in faces if f["keep"]]
    dropped = [f for f in faces if not f["keep"]]
    parts = [
        "<!doctype html><html><head><meta charset='utf-8'><title>Subject vs background</title>",
        "<style>body{font-family:system-ui;background:#111;color:#eee;margin:16px}"
        "h2{font-size:15px}.grid{display:flex;flex-wrap:wrap;gap:8px}"
        "figure{margin:0;width:120px}figure img{width:120px;height:120px;object-fit:cover;border-radius:6px}"
        "figcaption{font-size:10px;color:#aaa;word-break:break-word}"
        ".dropped figure img{outline:2px solid #c0392b}.kept figure img{outline:2px solid #27ae60}"
        "</style></head><body>",
        f"<h1>Subject vs background — {len(kept)} kept, {len(dropped)} dropped</h1>",
        f"<h2>Dropped as background ({len(dropped)})</h2><div class='grid dropped'>",
        "".join(card(f) for f in sorted(dropped, key=lambda f: f["area_frac"])),
        "</div>",
        f"<h2>Kept as subject ({len(kept)})</h2><div class='grid kept'>",
        "".join(card(f) for f in sorted(kept, key=lambda f: -f["area_frac"])),
        "</div></body></html>",
    ]
    html_path.write_text("".join(parts), encoding="utf-8")


def _write_html(connection, clusters, html_path: Path, *, per_cluster: int) -> None:
    html_path.parent.mkdir(parents=True, exist_ok=True)
    rows = connection.execute(
        """
        SELECT image_faces.cluster_id AS cluster_id,
               image_faces.det_score AS det_score,
               images.source_path AS source_path
        FROM image_faces
        JOIN images ON images.id = image_faces.image_id
        WHERE image_faces.cluster_id IS NOT NULL
        ORDER BY image_faces.det_score DESC
        """
    ).fetchall()
    by_cluster: dict[int, list[str]] = {}
    for row in rows:
        by_cluster.setdefault(int(row["cluster_id"]), [])
        paths = by_cluster[int(row["cluster_id"])]
        if len(paths) < per_cluster:
            paths.append(str(row["source_path"]))

    parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<title>Face tagging sandbox</title>",
        "<style>body{font-family:system-ui;background:#111;color:#eee;margin:16px}"
        ".cluster{margin-bottom:24px}.cluster h2{font-size:15px;font-weight:600}"
        ".row{display:flex;flex-wrap:wrap;gap:6px}.row img{height:140px;border-radius:6px;object-fit:cover}"
        "</style></head><body>",
        f"<h1>{len(clusters)} people clusters</h1>",
    ]
    for cluster in clusters:
        paths = by_cluster.get(cluster.cluster_id, [])
        parts.append("<div class='cluster'>")
        parts.append(f"<h2>Person {cluster.cluster_id} — {cluster.face_count} face(s)</h2>")
        parts.append("<div class='row'>")
        for path in paths:
            try:
                uri = Path(path).as_uri()
            except ValueError:
                uri = path
            parts.append(f"<img src='{uri}' loading='lazy'>")
        parts.append("</div></div>")
    parts.append("</body></html>")
    html_path.write_text("".join(parts), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
