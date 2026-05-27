"""Layer contracts for modular culling signal extraction."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Protocol

from app.engine.signals.models import ImageSignalRecord, LayerStatus
from app.storage.ranking_artifacts import RankingArtifacts


@dataclass(frozen=True)
class SignalLayerContext:
    """Shared context passed to signal layers."""

    artifacts_dir: Path
    ranking_artifacts: RankingArtifacts
    profile_name: str = "General Use"
    max_preview_side: int = 768


class SignalLayer(Protocol):
    """Protocol implemented by each signal layer."""

    layer_id: str
    display_name: str
    required_stack_slot: bool

    def status(self) -> LayerStatus:
        """Return the layer's availability/status."""

    def analyze(
        self,
        records: Dict[str, ImageSignalRecord],
        context: SignalLayerContext,
    ) -> Dict[str, ImageSignalRecord]:
        """Return records updated with this layer's signals."""


def append_layer_status(
    records: Dict[str, ImageSignalRecord],
    status: LayerStatus,
    *,
    image_ids: Iterable[str] | None = None,
) -> Dict[str, ImageSignalRecord]:
    """Attach one layer status to selected records."""

    target_ids = list(image_ids) if image_ids is not None else list(records.keys())
    updated = dict(records)
    for image_id in target_ids:
        record = updated.get(image_id)
        if record is None:
            continue
        updated[image_id] = ImageSignalRecord(
            image_id=record.image_id,
            file_path=record.file_path,
            relative_path=record.relative_path,
            file_name=record.file_name,
            schema_version=record.schema_version,
            dino=record.dino,
            technical=record.technical,
            subject=record.subject,
            aesthetic=record.aesthetic,
            semantic=record.semantic,
            personal=record.personal,
            final=record.final,
            layer_statuses=[*record.layer_statuses, status],
        )
    return updated


def base_signal_records(ranking_artifacts: RankingArtifacts) -> Dict[str, ImageSignalRecord]:
    """Create empty signal records aligned to ranking artifacts."""

    return {
        image.image_id: ImageSignalRecord(
            image_id=image.image_id,
            file_path=image.file_path,
            relative_path=image.relative_path,
            file_name=image.file_name,
        )
        for image in ranking_artifacts.ordered_images
    }
