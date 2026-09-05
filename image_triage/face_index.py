"""Automatic, person-filtered face indexing for people tagging.

Decoupled from the full AI workflow, and layered on top of the semantic index:
it reuses the TinyCLIP image embeddings the semantic pass already stored to
cheaply decide which photos likely contain a person (zero-shot), and only runs
the heavier AuraFace detect+embed on that flagged subset. Detected faces are
persisted and clustered so the People panel can surface recurring people.

Pipeline: stored TinyCLIP embeddings -> person pre-filter (@0.15) -> AuraFace on
the flagged subset -> cluster (scoped to the active recognizer).

The planner and scoring are pure so they can be unit tested without Qt, ONNX,
or real models; the QRunnable is a thin wrapper.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
from PIL import Image

from PySide6.QtCore import QObject, QRunnable, Signal

from aiculler.features import PreviewExtractor, _file_signature
from aiculler.storage import SQLiteFeatureStore
from .ai_model import AICULLER_FACE_PACK_NAME, active_face_identity_model, aiculler_face_model_root
from .people_search import cluster_face_identities
from .perf import write_execution_log
from .quality.face import FaceQualityAnalyzer
from .quality.store import upsert_faces
from .semantic_index import ensure_semantic_onnx_runtime

# Bump when the person-filter contract or threshold changes so folders are
# transparently re-evaluated.
FACE_INDEX_SCHEMA_VERSION = 2
# Zero-shot person-presence cutoff. Validated on a real library: recall ~0.93
# while skipping ~74% of the AuraFace work. Favor recall — a false positive just
# costs one AuraFace call that finds no face; a false negative drops a person.
PERSON_PRESENCE_THRESHOLD = 0.15
# Clustering threshold calibrated for AuraFace (glintr100) embeddings.
FACE_CLUSTER_THRESHOLD = 0.40
FACE_INDEX_COMMIT_BATCH = 16
# Full-image TinyCLIP margin below which an image is confidently animal-led.
# This catches animal faces AuraFace can mistake for humans without imposing a
# detector-confidence floor that would discard small or difficult human faces.
ANIMAL_REJECTION_MARGIN = -0.05

# Averaged into two class centroids for the zero-shot person/no-person decision.
PERSON_PROMPTS: tuple[str, ...] = (
    "a photo of a person",
    "a photo of people",
    "a photo of a man",
    "a photo of a woman",
    "a photo of a child",
    "a photo of a group of people",
    "a portrait of a person",
    "a candid photo of people",
)
NO_PERSON_PROMPTS: tuple[str, ...] = (
    "a landscape photo with no people",
    "a photo of scenery",
    "a photo of food",
    "a photo of an animal",
    "a photo of a building",
    "a close-up photo of an object",
    "an empty room with no people",
    "a photo of a plant",
)
HUMAN_PROMPTS: tuple[str, ...] = (
    "a photo of a human person",
    "a photograph of a man",
    "a photograph of a woman",
    "a photograph of a child",
    "a close-up portrait of a human face",
    "a group of human people",
)
ANIMAL_PROMPTS: tuple[str, ...] = (
    "a photo of an animal",
    "a photo of a monkey",
    "a photo of an ape",
    "a close-up portrait of an animal face",
    "a photo of wildlife",
    "a photo of a dog or cat",
)


def _signature_key(signature: dict[str, object]) -> str:
    return json.dumps(signature, sort_keys=True)


def _normalize(vector: np.ndarray) -> np.ndarray:
    values = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(values))
    return values / norm if norm > 0.0 else values


def person_probability(
    image_embedding: Sequence[float] | np.ndarray,
    person_proto: np.ndarray,
    noperson_proto: np.ndarray,
    *,
    scale: float = 100.0,
) -> float:
    """Softmax person-presence probability from a stored TinyCLIP image embedding."""
    vector = _normalize(np.asarray(image_embedding, dtype=np.float32))
    logits = np.array(
        [float(np.dot(vector, person_proto)), float(np.dot(vector, noperson_proto))],
        dtype=np.float64,
    ) * scale
    logits -= logits.max()
    exp = np.exp(logits)
    return float(exp[0] / exp.sum())


def build_person_prototypes(text_encoder) -> tuple[np.ndarray, np.ndarray]:
    """Encode the person / no-person prompt sets into two normalized centroids."""

    def prototype(prompts: Sequence[str]) -> np.ndarray:
        vectors = [_normalize(np.asarray(text_encoder.encode(prompt), dtype=np.float32)) for prompt in prompts]
        return _normalize(np.mean(np.vstack(vectors), axis=0))

    return prototype(PERSON_PROMPTS), prototype(NO_PERSON_PROMPTS)


def build_human_animal_prototypes(text_encoder) -> tuple[np.ndarray, np.ndarray]:
    """Encode conservative human / animal centroids for false-face rejection."""

    def prototype(prompts: Sequence[str]) -> np.ndarray:
        vectors = [_normalize(np.asarray(text_encoder.encode(prompt), dtype=np.float32)) for prompt in prompts]
        return _normalize(np.mean(np.vstack(vectors), axis=0))

    return prototype(HUMAN_PROMPTS), prototype(ANIMAL_PROMPTS)


def human_animal_margin(
    image_embedding: Sequence[float] | np.ndarray,
    human_proto: np.ndarray,
    animal_proto: np.ndarray,
) -> float:
    vector = _normalize(np.asarray(image_embedding, dtype=np.float32))
    return float(np.dot(vector, human_proto) - np.dot(vector, animal_proto))


@dataclass(frozen=True, slots=True)
class FaceIndexItem:
    image_id: int
    source_path: str
    signature_key: str
    person_prob: float
    human_animal_margin: float | None = None
    existing_face_count: int | None = None


@dataclass(frozen=True, slots=True)
class FaceIndexPlan:
    to_embed: tuple[FaceIndexItem, ...] = ()  # flagged: run AuraFace
    to_mark: tuple[FaceIndexItem, ...] = ()  # rejected: clear faces and record state
    to_preserve: tuple[FaceIndexItem, ...] = ()  # accepted schema migration: retain faces
    skipped: int = 0  # already processed at this model + signature


def plan_face_index(
    store: SQLiteFeatureStore,
    person_scorer: Callable[[np.ndarray], float],
    *,
    model_identity: str,
    animal_scorer: Callable[[np.ndarray], float] | None = None,
    threshold: float = PERSON_PRESENCE_THRESHOLD,
    animal_rejection_margin: float = ANIMAL_REJECTION_MARGIN,
    schema_version: int = FACE_INDEX_SCHEMA_VERSION,
) -> FaceIndexPlan:
    """Decide which embedded images need the AuraFace pass.

    Reuses the stored TinyCLIP embeddings to score person-presence. An image is
    skipped when a matching face-index-state row already exists for the active
    recognizer and current file signature.
    """
    to_embed: list[FaceIndexItem] = []
    to_mark: list[FaceIndexItem] = []
    to_preserve: list[FaceIndexItem] = []
    skipped = 0
    for row in store.list_images(require_embedding=True):
        image_id = int(row["id"])
        source = str(row["source_path"])
        signature_key = _signature_key(_file_signature(Path(source)))
        state = store.get_face_index_state(image_id)
        state_matches_source = bool(
            state is not None
            and state["model_identity"] == model_identity
            and state["source_signature"] == signature_key
        )
        if state_matches_source and int(state["schema_version"]) == schema_version:
            skipped += 1
            continue
        embedding = store.get_embedding(image_id)
        prob = person_scorer(embedding)
        animal_margin = animal_scorer(embedding) if animal_scorer is not None else None
        item = FaceIndexItem(
            image_id=image_id,
            source_path=source,
            signature_key=signature_key,
            person_prob=prob,
            human_animal_margin=animal_margin,
            existing_face_count=int(state["face_count"]) if state_matches_source else None,
        )
        if prob >= threshold and (animal_margin is None or animal_margin > animal_rejection_margin):
            if state_matches_source:
                to_preserve.append(item)
            else:
                to_embed.append(item)
        else:
            to_mark.append(item)
    return FaceIndexPlan(
        to_embed=tuple(to_embed),
        to_mark=tuple(to_mark),
        to_preserve=tuple(to_preserve),
        skipped=skipped,
    )


class FaceIndexSignals(QObject):
    progress = Signal(str, int, int, int)  # folder, token, completed, total_flagged
    finished = Signal(str, int, int, int)  # folder, token, faces_indexed, people_count
    failed = Signal(str, int, str)  # folder, token, message


class FaceFolderIndexTask(QRunnable):
    """Background person-filtered AuraFace pass + clustering for one folder."""

    def __init__(
        self,
        *,
        folder: str,
        token: int,
        db_path: str | Path,
        clip_text_model: str | Path | None = None,
        clip_tokenizer: str | Path | None = None,
        clip_fallback_text_model: str | Path | None = None,
        face_model_root: str | Path | None = None,
        face_pack_name: str = AICULLER_FACE_PACK_NAME,
        device: str = "auto",
        det_size: int = 640,
        person_threshold: float = PERSON_PRESENCE_THRESHOLD,
        cluster_threshold: float = FACE_CLUSTER_THRESHOLD,
        commit_batch: int = FACE_INDEX_COMMIT_BATCH,
        model_identity: str | None = None,
        person_scorer: Callable[[np.ndarray], float] | None = None,
        animal_scorer: Callable[[np.ndarray], float] | None = None,
        analyzer: object | None = None,
        image_loader: Callable[[str], np.ndarray] | None = None,
    ) -> None:
        super().__init__()
        self.folder = str(folder)
        self.token = int(token)
        self.db_path = Path(db_path)
        self.clip_text_model = Path(clip_text_model) if clip_text_model else None
        self.clip_tokenizer = Path(clip_tokenizer) if clip_tokenizer else None
        self.clip_fallback_text_model = Path(clip_fallback_text_model) if clip_fallback_text_model else None
        self.face_model_root = Path(face_model_root) if face_model_root else None
        self.face_pack_name = str(face_pack_name)
        self.device = str(device or "auto")
        self.det_size = int(det_size)
        self.person_threshold = float(person_threshold)
        self.cluster_threshold = float(cluster_threshold)
        self.commit_batch = max(1, int(commit_batch))
        self.model_identity = model_identity or active_face_identity_model()
        self._person_scorer = person_scorer
        self._animal_scorer = animal_scorer
        self._analyzer = analyzer
        self._image_loader = image_loader
        self.signals = FaceIndexSignals()
        self.setAutoDelete(True)
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _build_image_scorers(
        self,
    ) -> tuple[Callable[[np.ndarray], float], Callable[[np.ndarray], float] | None]:
        if self._person_scorer is not None:
            return self._person_scorer, self._animal_scorer
        if self.clip_text_model is None or self.clip_tokenizer is None:
            raise RuntimeError("No TinyCLIP text model configured for the person pre-filter")
        ensure_semantic_onnx_runtime(device=self.device)
        from aiculler.text_scoring import CLIPTextEncoder

        encoder = CLIPTextEncoder(
            self.clip_text_model,
            self.clip_tokenizer,
            fallback_text_onnx_path=self.clip_fallback_text_model,
        )
        person_proto, noperson_proto = build_person_prototypes(encoder)
        human_proto, animal_proto = build_human_animal_prototypes(encoder)
        return (
            lambda embedding: person_probability(embedding, person_proto, noperson_proto),
            lambda embedding: human_animal_margin(embedding, human_proto, animal_proto),
        )

    def _build_analyzer(self):
        if self._analyzer is not None:
            return self._analyzer
        ensure_semantic_onnx_runtime(device=self.device)
        import onnxruntime

        use_cuda = (
            not self.device.startswith("cpu")
            and "CUDAExecutionProvider" in onnxruntime.get_available_providers()
        )
        ctx_id = 0 if use_cuda else -1
        providers = (
            ["CUDAExecutionProvider", "CPUExecutionProvider"]
            if use_cuda
            else ["CPUExecutionProvider"]
        )
        root = str(self.face_model_root) if self.face_model_root is not None else str(aiculler_face_model_root())
        return FaceQualityAnalyzer(
            root=root,
            name=self.face_pack_name,
            det_size=self.det_size,
            ctx_id=ctx_id,
            providers=providers,
            enable_identity=True,
        )

    def run(self) -> None:
        if self._cancelled:
            return
        try:
            write_execution_log(f"face-index: run START (device={self.device}, model={self.model_identity})")
            store = SQLiteFeatureStore(self.db_path)
            try:
                embedded = store.list_images(require_embedding=True)
                if not embedded:
                    write_execution_log("face-index: run END — no images have embeddings yet (0 to scan)")
                    self.signals.finished.emit(self.folder, self.token, 0, 0)
                    return

                person_scorer, animal_scorer = self._build_image_scorers()
                plan = plan_face_index(
                    store,
                    person_scorer,
                    model_identity=self.model_identity,
                    animal_scorer=animal_scorer,
                    threshold=self.person_threshold,
                )
                write_execution_log(
                    f"face-index: plan — {len(embedded)} embedded images, "
                    f"{len(plan.to_embed)} flagged for AuraFace, {len(plan.to_mark)} rejected by "
                    f"person/animal filters, {len(plan.to_preserve)} migrated without AuraFace"
                )

                # Rejected images: clear stale detections and record state so
                # they are not re-scored every run. No AuraFace work.
                for item in plan.to_mark:
                    if self._cancelled:
                        store.connection.commit()
                        return
                    upsert_faces(store.connection, item.image_id, [])
                    store.set_face_index_state(
                        item.image_id,
                        model_identity=self.model_identity,
                        source_signature=item.signature_key,
                        person_prob=item.person_prob,
                        face_count=0,
                        schema_version=FACE_INDEX_SCHEMA_VERSION,
                        commit=False,
                    )
                if plan.to_mark:
                    store.connection.commit()

                for item in plan.to_preserve:
                    store.set_face_index_state(
                        item.image_id,
                        model_identity=self.model_identity,
                        source_signature=item.signature_key,
                        person_prob=item.person_prob,
                        face_count=item.existing_face_count or 0,
                        schema_version=FACE_INDEX_SCHEMA_VERSION,
                        commit=False,
                    )
                if plan.to_preserve:
                    store.connection.commit()

                total = len(plan.to_embed)
                if total == 0:
                    if plan.to_mark:
                        self._cluster(store)
                    write_execution_log(
                        "face-index: run END — no new images require AuraFace"
                    )
                    self.signals.finished.emit(self.folder, self.token, 0, self._people_count(store))
                    return

                analyzer = self._build_analyzer()
                if not getattr(analyzer, "available", True):
                    detail = str(getattr(analyzer, "initialization_error", "") or "model/runtime load failed")
                    write_execution_log(f"face-index: FAILED — AuraFace analyzer unavailable ({detail})")
                    self.signals.failed.emit(
                        self.folder, self.token, f"Face analyzer could not start: {detail}"
                    )
                    return
                write_execution_log(f"face-index: analyzer ready, detecting faces on {total} flagged images...")

                with tempfile.TemporaryDirectory(prefix="face_index_") as cache_dir:
                    loader = self._image_loader or _default_image_loader(cache_dir)
                    completed = 0
                    faces_indexed = 0
                    pending = 0
                    faces_since_cluster = 0
                    for item in plan.to_embed:
                        if self._cancelled:
                            store.connection.commit()
                            return
                        try:
                            bgr = loader(item.source_path)
                            result = analyzer.analyze(bgr)
                            faces = list(result.get("faces") or [])
                        except Exception:
                            # Unreadable image: record state so we do not retry it
                            # forever (a changed file gets a new signature).
                            faces = []
                        upsert_faces(store.connection, item.image_id, faces)
                        store.set_face_index_state(
                            item.image_id,
                            model_identity=self.model_identity,
                            source_signature=item.signature_key,
                            person_prob=item.person_prob,
                            face_count=len(faces),
                            schema_version=FACE_INDEX_SCHEMA_VERSION,
                            commit=False,
                        )
                        faces_indexed += len(faces)
                        completed += 1
                        pending += 1
                        faces_since_cluster += len(faces)
                        if pending >= self.commit_batch:
                            store.connection.commit()
                            pending = 0
                            self.signals.progress.emit(self.folder, self.token, completed, total)
                            # Re-cluster progressively so the People panel fills in
                            # as faces are found, instead of staying empty until the
                            # whole folder finishes. Cheap for a few hundred faces.
                            if faces_since_cluster:
                                self._cluster(store)
                                faces_since_cluster = 0
                                self.signals.progress.emit(self.folder, self.token, completed, total)
                    store.connection.commit()
                    self.signals.progress.emit(self.folder, self.token, completed, total)

                # Final clustering over the active recognizer's embeddings only.
                self._cluster(store)
                people = self._people_count(store)
                write_execution_log(
                    f"face-index: run END — {faces_indexed} faces detected across {completed} images, "
                    f"{people} person clusters"
                )
                self.signals.finished.emit(
                    self.folder, self.token, faces_indexed, people
                )
            finally:
                store.close()
        except Exception as exc:  # pragma: no cover - defensive; surfaced to UI
            if self._cancelled:
                return
            write_execution_log(f"face-index: FAILED — {exc}")
            self.signals.failed.emit(self.folder, self.token, str(exc))

    def _cluster(self, store: SQLiteFeatureStore) -> None:
        cluster_face_identities(
            store.connection,
            threshold=self.cluster_threshold,
            identity_model=self.model_identity,
        )

    @staticmethod
    def _people_count(store: SQLiteFeatureStore) -> int:
        row = store.connection.execute(
            "SELECT COUNT(*) AS c FROM face_identity_clusters"
        ).fetchone()
        return 0 if row is None else int(row["c"])


def _default_image_loader(cache_dir: str | Path) -> Callable[[str], np.ndarray]:
    preview_extractor = PreviewExtractor(cache_dir)

    def load(source_path: str) -> np.ndarray:
        preview_path, _ = preview_extractor.extract(Path(source_path))
        with Image.open(preview_path) as opened:
            rgb = np.asarray(opened.convert("RGB"), dtype=np.uint8)
        return rgb[:, :, ::-1]  # BGR for the analyzer

    return load
