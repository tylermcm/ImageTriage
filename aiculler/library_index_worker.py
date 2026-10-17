"""Background embedding worker for the library-wide semantic index.

Runs in the AI runtime's Python (like ``aiculler/cli.py``), as its own process so
TinyCLIP inference never shares the GUI process's interpreter, threads or memory,
and can be paused, stopped or killed without touching the app.

Input is a JSON jobs file (see :func:`main`); for each folder it plans with the
same rules as the in-app per-folder indexer (``image_triage.semantic_plan``) and
writes embeddings into that folder's own feature store, which stays the source of
truth. The app then reconciles the folder into its derived library index.

Control: the app writes ``run``, ``pause`` or ``stop`` into the control file; the
worker checks it between images, so a pause takes effect within one image.

Output: one line per event on stdout, ``LIBRARY_INDEX {json}``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

EVENT_PREFIX = "LIBRARY_INDEX "
FAKE_EXTRACTOR_ENV = "IMAGE_TRIAGE_LIBRARY_INDEX_FAKE_EXTRACTOR"
COMMIT_BATCH = 16


def emit(event: str, **fields: object) -> None:
    sys.stdout.write(EVENT_PREFIX + json.dumps({"event": event, **fields}, sort_keys=True) + "\n")
    sys.stdout.flush()


def read_control(path: Path | None) -> str:
    if path is None:
        return "run"
    try:
        return path.read_text(encoding="utf-8").strip().lower() or "run"
    except OSError:
        return "run"


def wait_while_paused(path: Path | None) -> bool:
    """Block while the control file says pause. Returns False when told to stop."""
    announced = False
    while True:
        state = read_control(path)
        if state == "stop":
            return False
        if state != "pause":
            if announced:
                emit("resumed")
            return True
        if not announced:
            emit("paused")
            announced = True
        time.sleep(0.25)


class _FakeExtractor:
    """Deterministic vectors for tests, so the worker runs without the model."""

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def encode_image(self, path: str) -> np.ndarray:
        seed = int.from_bytes(hashlib.sha1(str(path).casefold().encode("utf-8")).digest()[:4], "little")
        return np.random.default_rng(seed).standard_normal(self.dim).astype(np.float32)


def build_extractor(args: argparse.Namespace, dim: int):
    import os

    if os.environ.get(FAKE_EXTRACTOR_ENV) == "1":
        return _FakeExtractor(dim)
    from aiculler.features import SemanticEmbeddingExtractor

    providers = ["CPUExecutionProvider"] if str(args.device).startswith("cpu") else None
    return SemanticEmbeddingExtractor(
        Path(args.clip),
        clip_fallback_onnx_path=Path(args.clip_fallback) if args.clip_fallback else None,
        providers=providers,
        intra_op_num_threads=max(1, int(args.threads)),
    )


def index_folder(job: dict, *, args: argparse.Namespace, model_identity: str, dim: int, control: Path | None, extractor_box: list) -> bool:
    """Embed what this folder is missing. Returns False when told to stop."""
    from aiculler.storage import SQLiteFeatureStore
    from image_triage.semantic_plan import SEMANTIC_INDEX_SCHEMA_VERSION, collect_indexable_sources, plan_semantic_index

    folder = str(job["folder"])
    db_path = Path(job["db"])
    sources = collect_indexable_sources(job.get("sources", ()))
    if not Path(folder).is_dir():
        emit("folder_offline", folder=folder)
        return True
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        store = SQLiteFeatureStore(db_path)
    except Exception as exc:  # read-only drive, permissions: skip the folder, keep going
        emit("folder_failed", folder=folder, message=str(exc))
        return True
    try:
        plan = plan_semantic_index(store.semantic_index_snapshot(), sources, model_identity=model_identity, expected_dim=dim)
        for item in plan.to_adopt:
            image_id = item.image_id if item.image_id is not None else store.ensure_image(item.source_path)
            store.set_semantic_index_state(
                image_id,
                model_identity=model_identity,
                source_signature=item.signature_key,
                embedding_dim=dim,
                schema_version=SEMANTIC_INDEX_SCHEMA_VERSION,
                commit=False,
            )
        store.connection.commit()
        total = len(plan.to_embed)
        emit("folder_started", folder=folder, total=total, ready=len(plan.valid) + len(plan.to_adopt))
        done = 0
        failed = 0
        pending = 0
        for item in plan.to_embed:
            if not wait_while_paused(control):
                store.connection.commit()
                emit("folder_progress", folder=folder, done=done, total=total, failed=failed)
                return False
            if not extractor_box:
                extractor_box.append(build_extractor(args, dim))
            image_id = item.image_id if item.image_id is not None else store.ensure_image(item.source_path)
            try:
                vector = np.asarray(extractor_box[0].encode_image(item.source_path), dtype=np.float32).reshape(-1)
            except Exception:
                failed += 1  # one unreadable image must not abort the folder
                continue
            store.save_semantic_embedding(image_id, vector, commit=False)
            store.set_semantic_index_state(
                image_id,
                model_identity=model_identity,
                source_signature=item.signature_key,
                embedding_dim=int(vector.size),
                schema_version=SEMANTIC_INDEX_SCHEMA_VERSION,
                commit=False,
            )
            done += 1
            pending += 1
            if pending >= COMMIT_BATCH:
                store.connection.commit()
                pending = 0
                emit("folder_progress", folder=folder, done=done, total=total, failed=failed)
        store.connection.commit()
        emit("folder_done", folder=folder, done=done, total=total, failed=failed)
        return True
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--jobs", required=True, help="JSON file: {model_identity, embedding_dim, folders: [{folder, db, sources}]}")
    parser.add_argument("--control", default="", help="file holding run / pause / stop")
    parser.add_argument("--clip", default="")
    parser.add_argument("--clip-fallback", default="")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args(argv)

    jobs = json.loads(Path(args.jobs).read_text(encoding="utf-8"))
    model_identity = str(jobs["model_identity"])
    dim = int(jobs.get("embedding_dim", 512))
    control = Path(args.control) if args.control else None
    extractor_box: list = []
    emit("started", folders=len(jobs.get("folders", ())))
    for job in jobs.get("folders", ()):
        if read_control(control) == "stop":
            emit("stopped")
            return 0
        try:
            if not index_folder(job, args=args, model_identity=model_identity, dim=dim, control=control, extractor_box=extractor_box):
                emit("stopped")
                return 0
        except Exception as exc:
            emit("folder_failed", folder=str(job.get("folder", "")), message=str(exc))
    emit("finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
