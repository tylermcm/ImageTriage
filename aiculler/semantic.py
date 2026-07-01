from __future__ import annotations

import json
import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from aiculler.storage import SQLiteFeatureStore
from aiculler.text_scoring import CLIPTextEncoder, cosine_similarity


DEFAULT_CATEGORY_PROMPTS: dict[str, list[str]] = {
    "landscape": [
        "landscape photography, mountains, lakes, rivers, forests, waterfalls, scenic natural vista",
        "wide outdoor nature scene with land, water, sky, trees, or mountains",
    ],
    "wildlife": [
        "wildlife photography, animals, birds, mammals, animal close-up in nature",
        "animal subject outdoors, including full body or close detail of an animal",
    ],
    "people_portrait": [
        "portrait or people photography, face, person, group, candid human subject",
        "human subject photo with expression, pose, or people in the scene",
    ],
    "travel_built": [
        "travel photography with roads, trains, vehicles, signs, buildings, bridges, towns, or human-made structures",
        "built environment, transportation, sign, street, town, bridge, or travel documentary scene",
    ],
    "night_astro": [
        "night photography, stars, milky way, dark sky, moon, city lights, low light scene",
        "dark low-light photograph with night sky, stars, sunset glow, or artificial lights",
    ],
    "macro_detail": [
        "macro photography or close-up detail of flowers, plants, leaves, rocks, objects, or small non-animal subjects",
        "close detail shot, botanical detail, texture, small object, shallow depth of field",
    ],
    "abstract_texture": [
        "abstract photography, patterns, textures, shapes, minimal subject",
        "graphic texture or abstract visual composition",
    ],
    "product_still_life": [
        "product photography, food photography, still life object arranged indoors or on a table",
        "commercial object, product, food, drink, or still life composition",
    ],
    "street_documentary": [
        "street photography, candid documentary scene, people in public spaces, everyday moment",
        "urban documentary photograph with gesture, street life, crowd, market, or public interaction",
    ],
    "architecture": [
        "architecture photography, building facade, interior or exterior architectural design, geometric structure",
        "architectural composition with lines, symmetry, windows, towers, bridges, or designed spaces",
    ],
    "sports_action": [
        "sports photography, athlete, action moment, competition, fast movement, game or race",
        "action photograph with human motion, performance, speed, physical activity, or decisive sports moment",
    ],
    "event_stage": [
        "event photography, concert, stage performance, ceremony, festival, presentation, show lighting",
        "indoor or outdoor event scene with performers, audience, stage lights, celebration, or gathering",
    ],
    "vehicle_transport": [
        "vehicle photography, cars, trains, boats, aircraft, bicycles, motorcycles, transportation subject",
        "transport scene featuring vehicle shape, motion, road, railway, airport, harbor, or transit",
    ],
    "interior_space": [
        "interior photography, room, hallway, museum, hotel, restaurant, indoor architectural space",
        "indoor space with furniture, walls, windows, ceiling, ambient light, or designed interior",
    ],
    "aerial_drone": [
        "aerial photography, drone view, top down landscape, city grid, fields, coastline from above",
        "high viewpoint photograph with map-like patterns, scale, roads, rooftops, terrain, or overhead geometry",
    ],
    "water_coastal": [
        "water photography, ocean, beach, coast, river, lake, waves, reflections, shoreline",
        "coastal or water scene with horizon, boats, surf, wet rocks, reflections, or blue water texture",
    ],
}


@dataclass(frozen=True)
class CategoryAssignment:
    image_id: int
    filename: str
    source_path: str
    primary_category: str
    confidence: float
    category_scores: dict[str, float]


@dataclass(frozen=True)
class SemanticClusterRecord:
    cluster_index: int
    cluster_id: int | None
    primary_category: str
    label: str
    image_count: int


class PrimaryCategoryAssigner:
    def __init__(
        self,
        store: SQLiteFeatureStore,
        text_encoder: CLIPTextEncoder,
        *,
        category_prompts: dict[str, list[str]] | None = None,
        confidence_temperature: float = 0.02,
        min_confidence: float = 0.25,
    ):
        self.store = store
        self.text_encoder = text_encoder
        self.category_prompts = category_prompts or DEFAULT_CATEGORY_PROMPTS
        self.confidence_temperature = float(confidence_temperature)
        self.min_confidence = float(min_confidence)

    def assign(self) -> list[CategoryAssignment]:
        rows = self.store.list_images(require_embedding=True)
        if not rows:
            return []

        category_vectors = self._category_vectors()
        categories = list(category_vectors)
        updates: dict[int, dict] = {}
        assignments: list[CategoryAssignment] = []
        for row in rows:
            image_id = int(row["id"])
            embedding = self.store.get_embedding(image_id)
            scores = {
                category: cosine_similarity(embedding, category_vectors[category])
                for category in categories
            }
            ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
            primary_category = ranked[0][0]
            confidence = self._confidence([score for _, score in ranked])
            if confidence < self.min_confidence:
                primary_category = "uncategorized"
            updates[image_id] = {
                "primary_category": primary_category,
                "confidence": confidence,
                "category_scores": scores,
                "assigned_by": "clip_zero_shot",
            }
            assignments.append(
                CategoryAssignment(
                    image_id=image_id,
                    filename=Path(row["source_path"]).name,
                    source_path=row["source_path"],
                    primary_category=primary_category,
                    confidence=confidence,
                    category_scores=scores,
                )
            )
        self.store.update_image_categories(updates)
        return sorted(assignments, key=lambda item: (item.primary_category, -item.confidence, item.image_id))

    def _category_vectors(self) -> dict[str, np.ndarray]:
        vectors: dict[str, np.ndarray] = {}
        for category, prompts in self.category_prompts.items():
            prompt_vectors = [normalize(self.text_encoder.encode(prompt)) for prompt in prompts]
            vectors[category] = normalize(np.mean(np.vstack(prompt_vectors), axis=0))
        return vectors

    def _confidence(self, ranked_scores: list[float]) -> float:
        values = np.asarray(ranked_scores, dtype=np.float32)
        values = values - values.max()
        scaled = values / max(self.confidence_temperature, 1e-6)
        exp = np.exp(scaled)
        probabilities = exp / np.sum(exp)
        return float(probabilities[0])


class CategoryClusterer:
    def __init__(
        self,
        store: SQLiteFeatureStore,
        *,
        run_id: str,
        min_cluster_size: int = 25,
        max_clusters_per_category: int = 8,
        iterations: int = 40,
        seed: int = 13,
    ):
        self.store = store
        self.run_id = str(run_id)
        self.min_cluster_size = max(2, int(min_cluster_size))
        self.max_clusters_per_category = max(1, int(max_clusters_per_category))
        self.iterations = max(1, int(iterations))
        self.seed = int(seed)

    def cluster(self) -> tuple[list[SemanticClusterRecord], list[dict]]:
        category_rows = self.store.list_categories()
        rows_by_category: dict[str, list] = {}
        for row in category_rows:
            rows_by_category.setdefault(row["primary_category"], []).append(row)

        clusters_to_save: list[dict] = []
        memberships: list[dict] = []
        records: list[SemanticClusterRecord] = []
        for category, rows in sorted(rows_by_category.items()):
            image_ids = [int(row["image_id"]) for row in rows]
            embeddings = np.vstack([normalize(self.store.get_embedding(image_id)) for image_id in image_ids])
            k = self._cluster_count(len(image_ids))
            labels, centroids = kmeans(embeddings, k, iterations=self.iterations, seed=self.seed)
            labels, centroids = merge_small_clusters(
                embeddings,
                labels,
                centroids,
                min_cluster_size=self.min_cluster_size,
            )
            cluster_numbers = sorted(int(label) for label in np.unique(labels))
            for output_number, cluster_number in enumerate(cluster_numbers, start=1):
                member_indices = [idx for idx, label in enumerate(labels) if int(label) == cluster_number]
                if not member_indices:
                    continue
                cluster_index = len(clusters_to_save)
                label = f"{category}_{output_number:02d}"
                centroid = normalize(centroids[cluster_number])
                clusters_to_save.append(
                    {
                        "run_id": self.run_id,
                        "primary_category": category,
                        "label": label,
                        "image_count": len(member_indices),
                        "centroid": centroid,
                        "metadata": {"min_cluster_size": self.min_cluster_size},
                    }
                )
                records.append(
                    SemanticClusterRecord(
                        cluster_index=cluster_index,
                        cluster_id=None,
                        primary_category=category,
                        label=label,
                        image_count=len(member_indices),
                    )
                )
                for member_index in member_indices:
                    distance = float(1.0 - cosine_similarity(embeddings[member_index], centroid))
                    memberships.append(
                        {
                            "image_id": image_ids[member_index],
                            "cluster_index": cluster_index,
                            "distance": distance,
                            "rank": 1,
                        }
                    )

        self.store.clear_semantic_clusters(self.run_id)
        cluster_ids = self.store.save_semantic_clusters(clusters_to_save, memberships)
        saved_records = [
            SemanticClusterRecord(
                cluster_index=record.cluster_index,
                cluster_id=cluster_ids[record.cluster_index],
                primary_category=record.primary_category,
                label=record.label,
                image_count=record.image_count,
            )
            for record in records
        ]
        return saved_records, memberships

    def _cluster_count(self, count: int) -> int:
        if count <= self.min_cluster_size:
            return 1
        limit_by_size = max(1, count // self.min_cluster_size)
        heuristic = max(1, round(np.sqrt(count / self.min_cluster_size)))
        return min(self.max_clusters_per_category, limit_by_size, heuristic)


def category_assignment_to_csv(record: CategoryAssignment) -> dict:
    return {
        "id": record.image_id,
        "filename": record.filename,
        "source_path": record.source_path,
        "primary_category": record.primary_category,
        "confidence": record.confidence,
        "category_scores_json": json.dumps(record.category_scores, sort_keys=True),
    }


def load_category_prompts(path: str | Path) -> dict[str, list[str]]:
    prompts: dict[str, list[str]] = {}
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("category prompt CSV must include headers")
        for line_number, row in enumerate(reader, start=2):
            enabled = (row.get("enabled") or "1").strip().lower()
            if enabled in {"0", "false", "no", "n"}:
                continue
            category = (row.get("category") or "").strip()
            prompt = (row.get("prompt") or "").strip()
            if not category or not prompt:
                raise ValueError(f"category prompt row {line_number} requires category and prompt")
            prompts.setdefault(category, []).append(prompt)
    if not prompts:
        raise ValueError("category prompt CSV did not contain any enabled prompts")
    return prompts


def semantic_cluster_to_csv(record: SemanticClusterRecord) -> dict:
    return {
        "cluster_id": record.cluster_id,
        "primary_category": record.primary_category,
        "label": record.label,
        "image_count": record.image_count,
    }


def normalize(vector: np.ndarray) -> np.ndarray:
    arr = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(arr))
    if norm == 0.0:
        return arr
    return (arr / norm).astype(np.float32)


def kmeans(values: np.ndarray, k: int, *, iterations: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    values = np.asarray(values, dtype=np.float32)
    if values.ndim != 2 or values.shape[0] == 0:
        raise ValueError("kmeans requires a non-empty 2D array")
    k = max(1, min(int(k), values.shape[0]))
    rng = np.random.default_rng(seed)
    first = int(rng.integers(0, values.shape[0]))
    centroids = [values[first]]
    while len(centroids) < k:
        distances = 1.0 - (values @ np.vstack(centroids).T).max(axis=1)
        distances = np.maximum(distances, 0.0)
        if float(distances.sum()) == 0.0:
            next_index = len(centroids)
        else:
            probabilities = distances / distances.sum()
            next_index = int(rng.choice(values.shape[0], p=probabilities))
        centroids.append(values[next_index])

    centroid_array = np.vstack(centroids).astype(np.float32)
    labels = np.zeros(values.shape[0], dtype=np.int64)
    for _ in range(iterations):
        similarities = values @ centroid_array.T
        next_labels = np.argmax(similarities, axis=1)
        next_centroids = centroid_array.copy()
        for idx in range(k):
            members = values[next_labels == idx]
            if len(members):
                next_centroids[idx] = normalize(members.mean(axis=0))
        if np.array_equal(labels, next_labels):
            centroid_array = next_centroids
            break
        labels = next_labels
        centroid_array = next_centroids
    return labels, centroid_array


def merge_small_clusters(
    values: np.ndarray,
    labels: np.ndarray,
    centroids: np.ndarray,
    *,
    min_cluster_size: int,
) -> tuple[np.ndarray, np.ndarray]:
    labels = np.asarray(labels, dtype=np.int64).copy()
    centroids = np.asarray(centroids, dtype=np.float32)
    while True:
        unique, counts = np.unique(labels, return_counts=True)
        small = [(int(label), int(count)) for label, count in zip(unique, counts) if int(count) < min_cluster_size]
        if not small or len(unique) == 1:
            break
        count_by_label = {int(label): int(count) for label, count in zip(unique, counts)}
        small_label, _ = min(small, key=lambda item: item[1])
        candidate_labels = [int(label) for label in unique if int(label) != small_label]
        large_candidates = [label for label in candidate_labels if count_by_label[label] >= min_cluster_size]
        if large_candidates:
            candidate_labels = large_candidates
        source_centroid = centroids[small_label]
        target_label = max(candidate_labels, key=lambda label: cosine_similarity(source_centroid, centroids[label]))
        labels[labels == small_label] = target_label

        for label in np.unique(labels):
            members = values[labels == label]
            if len(members):
                centroids[int(label)] = normalize(members.mean(axis=0))
    return labels, centroids
