"""Orchestration helpers for the modular culling signal stack."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Dict, Mapping

from app.engine.signals.combiner import ScoringProfile, apply_combiner, choose_profile
from app.engine.signals.dino import DinoSignalLayer
from app.engine.signals.layers import SignalLayerContext, base_signal_records
from app.engine.signals.models import ImageSignalRecord
from app.engine.signals.specialists import specialist_layers
from app.engine.signals.technical import TechnicalSignalLayer
from app.storage.ranking_artifacts import RankingArtifacts, load_ranking_artifacts


SIGNALS_FILENAME = "culling_signals.json"
SIGNALS_CSV_FILENAME = "culling_signals.csv"


def build_culling_signals(
    *,
    artifacts_dir: Path,
    ranking_artifacts: RankingArtifacts | None = None,
    profile_name: str = "General Use",
    run_technical: bool = True,
    run_specialists: bool = True,
    max_preview_side: int = 768,
    learned_weights: Mapping[str, float] | None = None,
    metadata_filename: str = "images.csv",
    embeddings_filename: str = "embeddings.npy",
    image_ids_filename: str = "image_ids.json",
    clusters_filename: str = "clusters.csv",
) -> Dict[str, ImageSignalRecord]:
    """Build image signals and apply the transparent combiner."""

    artifacts_dir = Path(artifacts_dir).expanduser().resolve()
    if ranking_artifacts is None:
        ranking_artifacts = load_ranking_artifacts(
            artifacts_dir,
            metadata_filename=metadata_filename,
            embeddings_filename=embeddings_filename,
            image_ids_filename=image_ids_filename,
            clusters_filename=clusters_filename,
        )

    context = SignalLayerContext(
        artifacts_dir=artifacts_dir,
        ranking_artifacts=ranking_artifacts,
        profile_name=profile_name,
        max_preview_side=max_preview_side,
    )
    records = base_signal_records(ranking_artifacts)
    records = DinoSignalLayer().analyze(records, context)
    if run_technical:
        records = TechnicalSignalLayer().analyze(records, context)
    if run_specialists:
        for layer in specialist_layers():
            records = layer.analyze(records, context)

    profile: ScoringProfile = choose_profile(profile_name)
    return apply_combiner(records, profile=profile, learned_weights=learned_weights)


def save_culling_signals(
    records: Mapping[str, ImageSignalRecord],
    output_dir: Path,
    *,
    json_filename: str = SIGNALS_FILENAME,
    csv_filename: str = SIGNALS_CSV_FILENAME,
) -> Dict[str, Path]:
    """Persist signal artifacts for inspector/evaluation/debugging."""

    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / json_filename
    csv_path = output_dir / csv_filename

    ordered_records = sorted(records.values(), key=lambda record: (record.file_path.casefold(), record.image_id))
    json_path.write_text(
        json.dumps([record.to_dict() for record in ordered_records], indent=2),
        encoding="utf-8",
    )
    _save_signal_csv(csv_path, ordered_records)
    return {"signals_json": json_path, "signals_csv": csv_path}


def _save_signal_csv(path: Path, records: list[ImageSignalRecord]) -> None:
    fieldnames = [
        "image_id",
        "file_path",
        "cluster_id",
        "group_size",
        "group_position",
        "dino_rank",
        "dino_centrality",
        "detail",
        "sharpness",
        "exposure_status",
        "exposure_score",
        "noise",
        "face_count",
        "face_quality",
        "eye_open",
        "subject_label",
        "subject_confidence",
        "aesthetic",
        "composition",
        "clutter",
        "personal_score",
        "final_bucket",
        "final_rank",
        "reasons",
        "warnings",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "image_id": record.image_id,
                    "file_path": record.file_path,
                    "cluster_id": record.dino.cluster_id,
                    "group_size": record.dino.group_size,
                    "group_position": record.dino.group_position,
                    "dino_rank": record.dino.group_rank_by_centrality,
                    "dino_centrality": record.dino.centrality_score,
                    "detail": record.technical.detail_score,
                    "sharpness": record.technical.sharpness_score,
                    "exposure_status": record.technical.exposure_status,
                    "exposure_score": record.technical.exposure_score,
                    "noise": record.technical.noise_score,
                    "face_count": record.subject.face.face_count,
                    "face_quality": record.subject.face.face_sharpness_score,
                    "eye_open": record.subject.face.eye_open_score,
                    "subject_label": record.subject.primary_subject_label,
                    "subject_confidence": record.subject.subject_confidence,
                    "aesthetic": record.aesthetic.aesthetic_score,
                    "composition": record.aesthetic.composition_score,
                    "clutter": record.aesthetic.clutter_score,
                    "personal_score": record.personal.score,
                    "final_bucket": record.final.bucket,
                    "final_rank": record.final.rank_in_group,
                    "reasons": "; ".join(record.final.reasons),
                    "warnings": "; ".join(record.final.warnings),
                }
            )
