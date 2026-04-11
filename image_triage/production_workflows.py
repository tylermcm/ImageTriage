from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QRunnable, QSize, Signal

from .ai_results import AIBundle, AIConfidenceBucket, find_ai_result_for_record
from .archive_ops import archive_format_for_key
from .formats import suffix_for_path
from .image_resize import (
    OUTPUT_FORMAT_NAMES,
    ResizeSourceItem,
    _load_resize_image,
    _save_resized_image,
    _scaled_image,
    preset_for_key,
)
from .models import ImageRecord, SessionAnnotation
from .review_workflows import (
    REVIEW_ROUND_HERO,
    REVIEW_ROUND_THIRD_PASS,
    BurstRecommendation,
    ai_strength,
    normalize_review_round,
    review_round_label,
)
from .scanner import normalized_path_key

if TYPE_CHECKING:
    from .review_intelligence import ReviewIntelligenceBundle, ReviewInsight


RECIPE_CONTENT_EXPORT = "export_primary"
RECIPE_CONTENT_BUNDLE = "full_bundle"

RECIPE_TRANSFER_COPY = "copy"
RECIPE_TRANSFER_MOVE = "move"
RECIPE_TRANSFER_ARCHIVE = "archive"

WORKSPACE_FAST_CULLING = "fast_culling"
WORKSPACE_COMPARE = "compare_mode"
WORKSPACE_AI_REVIEW = "ai_review"
WORKSPACE_METADATA = "metadata_audit"
WORKSPACE_DELIVERY = "delivery_export"

BEST_OF_TOP_N = "top_n_overall"
BEST_OF_TOP_PER_GROUP = "top_per_group"
BEST_OF_BALANCED = "balanced_shortlist"


@dataclass(slots=True, frozen=True)
class WorkflowRecipe:
    key: str
    name: str
    description: str = ""
    content_mode: str = RECIPE_CONTENT_EXPORT
    transfer_mode: str = RECIPE_TRANSFER_COPY
    destination_subfolder: str = ""
    group_by_record_folder: bool = False
    archive_after_export: bool = False
    archive_format: str = "zip"
    resize_preset_key: str = ""
    convert_suffix: str = ""
    strip_metadata: bool = False
    rename_prefix: str = ""
    rename_suffix: str = ""

    @property
    def uses_transform_export(self) -> bool:
        return self.content_mode == RECIPE_CONTENT_EXPORT

    @property
    def uses_full_bundle(self) -> bool:
        return self.content_mode == RECIPE_CONTENT_BUNDLE

    @property
    def uses_archive_output(self) -> bool:
        return self.transfer_mode == RECIPE_TRANSFER_ARCHIVE or (self.uses_transform_export and self.archive_after_export)


@dataclass(slots=True, frozen=True)
class WorkspacePreset:
    key: str
    name: str
    description: str = ""
    ui_mode: str = "manual"
    columns: int = 3
    compare_enabled: bool = False
    auto_advance: bool = True
    burst_groups: bool = False
    burst_stacks: bool = False
    library_panel_mode: str = "expanded"
    inspector_panel_mode: str = "expanded"
    workspace_state: dict[str, object] | None = None


@dataclass(slots=True, frozen=True)
class WorkflowExportItem:
    source: ResizeSourceItem
    target_path: str
    target_name: str
    target_suffix: str
    width: int
    height: int
    status: str
    message: str = ""


@dataclass(slots=True, frozen=True)
class WorkflowExportPlan:
    recipe: WorkflowRecipe
    destination_dir: str
    items: tuple[WorkflowExportItem, ...]
    executable_items: tuple[WorkflowExportItem, ...]
    output_label: str
    error_count: int
    can_apply: bool
    general_error: str = ""


@dataclass(slots=True, frozen=True)
class BestOfSetCandidate:
    path: str
    score: float
    reason: str
    group_id: str = ""
    ai_bucket: str = ""


@dataclass(slots=True, frozen=True)
class BestOfSetPlan:
    strategy: str
    candidates: tuple[BestOfSetCandidate, ...]
    summary_lines: tuple[str, ...] = ()


def built_in_workflow_recipes() -> tuple[WorkflowRecipe, ...]:
    return (
        WorkflowRecipe(
            key="proofing_jpegs",
            name="Proofing JPEGs",
            description="Export quick proof JPEGs into a dedicated proofs folder.",
            content_mode=RECIPE_CONTENT_EXPORT,
            destination_subfolder="Proofs",
            resize_preset_key="large",
            convert_suffix=".jpg",
        ),
        WorkflowRecipe(
            key="client_delivery",
            name="Client Delivery",
            description="Export delivery-ready JPEGs and package them into a ZIP handoff.",
            content_mode=RECIPE_CONTENT_EXPORT,
            destination_subfolder="Delivery",
            resize_preset_key="2k",
            convert_suffix=".jpg",
            archive_after_export=True,
            archive_format="zip",
        ),
        WorkflowRecipe(
            key="edit_queue",
            name="Edit Queue",
            description="Copy the full selected bundles into an edit queue folder.",
            content_mode=RECIPE_CONTENT_BUNDLE,
            transfer_mode=RECIPE_TRANSFER_COPY,
            destination_subfolder="Edit Queue",
        ),
        WorkflowRecipe(
            key="send_to_editor",
            name="Send To Editor",
            description="Copy each selected bundle into its own editor-ready folder with sidecars and variants preserved.",
            content_mode=RECIPE_CONTENT_BUNDLE,
            transfer_mode=RECIPE_TRANSFER_COPY,
            destination_subfolder="Editor Queue",
            group_by_record_folder=True,
        ),
        WorkflowRecipe(
            key="archive_selection",
            name="Archive Selection",
            description="Package the selected bundles into a single archive without moving the originals.",
            content_mode=RECIPE_CONTENT_BUNDLE,
            transfer_mode=RECIPE_TRANSFER_ARCHIVE,
            archive_format="zip",
        ),
    )


def built_in_workspace_presets() -> tuple[WorkspacePreset, ...]:
    return (
        WorkspacePreset(
            key=WORKSPACE_FAST_CULLING,
            name="Fast Culling",
            description="Minimal chrome with wide grids and auto-advance friendly review.",
            ui_mode="manual",
            columns=5,
            compare_enabled=False,
            auto_advance=True,
            burst_groups=False,
            burst_stacks=True,
            library_panel_mode="collapsed",
            inspector_panel_mode="hidden",
        ),
        WorkspacePreset(
            key=WORKSPACE_COMPARE,
            name="Compare Mode",
            description="Built for side-by-side judgment and smart-group review.",
            ui_mode="manual",
            columns=3,
            compare_enabled=True,
            auto_advance=False,
            burst_groups=True,
            burst_stacks=True,
            library_panel_mode="collapsed",
            inspector_panel_mode="expanded",
        ),
        WorkspacePreset(
            key=WORKSPACE_AI_REVIEW,
            name="AI Review",
            description="Optimized for AI-led review, disagreement checks, and shortlist passes.",
            ui_mode="ai",
            columns=4,
            compare_enabled=False,
            auto_advance=True,
            burst_groups=True,
            burst_stacks=True,
            library_panel_mode="expanded",
            inspector_panel_mode="expanded",
        ),
        WorkspacePreset(
            key=WORKSPACE_METADATA,
            name="Metadata Audit",
            description="Slower, detail-rich inspection with the metadata side visible.",
            ui_mode="manual",
            columns=2,
            compare_enabled=False,
            auto_advance=False,
            burst_groups=False,
            burst_stacks=False,
            library_panel_mode="expanded",
            inspector_panel_mode="expanded",
        ),
        WorkspacePreset(
            key=WORKSPACE_DELIVERY,
            name="Delivery / Export",
            description="Focused on final selection review and downstream handoff tasks.",
            ui_mode="manual",
            columns=4,
            compare_enabled=False,
            auto_advance=False,
            burst_groups=True,
            burst_stacks=False,
            library_panel_mode="collapsed",
            inspector_panel_mode="expanded",
        ),
    )


def serialize_workflow_recipe(recipe: WorkflowRecipe) -> dict[str, object]:
    return {
        "key": recipe.key,
        "name": recipe.name,
        "description": recipe.description,
        "content_mode": recipe.content_mode,
        "transfer_mode": recipe.transfer_mode,
        "destination_subfolder": recipe.destination_subfolder,
        "group_by_record_folder": recipe.group_by_record_folder,
        "archive_after_export": recipe.archive_after_export,
        "archive_format": recipe.archive_format,
        "resize_preset_key": recipe.resize_preset_key,
        "convert_suffix": recipe.convert_suffix,
        "strip_metadata": recipe.strip_metadata,
        "rename_prefix": recipe.rename_prefix,
        "rename_suffix": recipe.rename_suffix,
    }


def deserialize_workflow_recipe(payload: dict[str, object] | None) -> WorkflowRecipe | None:
    if not isinstance(payload, dict):
        return None
    name = str(payload.get("name") or "").strip()
    key = str(payload.get("key") or "").strip()
    if not name or not key:
        return None
    return WorkflowRecipe(
        key=key,
        name=name,
        description=str(payload.get("description") or ""),
        content_mode=str(payload.get("content_mode") or RECIPE_CONTENT_EXPORT),
        transfer_mode=str(payload.get("transfer_mode") or RECIPE_TRANSFER_COPY),
        destination_subfolder=str(payload.get("destination_subfolder") or ""),
        group_by_record_folder=bool(payload.get("group_by_record_folder", False)),
        archive_after_export=bool(payload.get("archive_after_export", False)),
        archive_format=str(payload.get("archive_format") or "zip"),
        resize_preset_key=str(payload.get("resize_preset_key") or ""),
        convert_suffix=str(payload.get("convert_suffix") or ""),
        strip_metadata=bool(payload.get("strip_metadata", False)),
        rename_prefix=str(payload.get("rename_prefix") or ""),
        rename_suffix=str(payload.get("rename_suffix") or ""),
    )


def serialize_workspace_preset(preset: WorkspacePreset) -> dict[str, object]:
    return {
        "key": preset.key,
        "name": preset.name,
        "description": preset.description,
        "ui_mode": preset.ui_mode,
        "columns": preset.columns,
        "compare_enabled": preset.compare_enabled,
        "auto_advance": preset.auto_advance,
        "burst_groups": preset.burst_groups,
        "burst_stacks": preset.burst_stacks,
        "library_panel_mode": preset.library_panel_mode,
        "inspector_panel_mode": preset.inspector_panel_mode,
        "workspace_state": preset.workspace_state or {},
    }


def deserialize_workspace_preset(payload: dict[str, object] | None) -> WorkspacePreset | None:
    if not isinstance(payload, dict):
        return None
    name = str(payload.get("name") or "").strip()
    key = str(payload.get("key") or "").strip()
    if not name or not key:
        return None
    workspace_state = payload.get("workspace_state")
    return WorkspacePreset(
        key=key,
        name=name,
        description=str(payload.get("description") or ""),
        ui_mode=str(payload.get("ui_mode") or "manual"),
        columns=max(1, int(payload.get("columns") or 3)),
        compare_enabled=bool(payload.get("compare_enabled", False)),
        auto_advance=bool(payload.get("auto_advance", True)),
        burst_groups=bool(payload.get("burst_groups", False)),
        burst_stacks=bool(payload.get("burst_stacks", False)),
        library_panel_mode=str(payload.get("library_panel_mode") or "expanded"),
        inspector_panel_mode=str(payload.get("inspector_panel_mode") or "expanded"),
        workspace_state=workspace_state if isinstance(workspace_state, dict) else None,
    )


def recipe_summary_lines(recipe: WorkflowRecipe) -> tuple[str, ...]:
    lines: list[str] = []
    if recipe.uses_transform_export:
        output_parts = ["Export primary deliverables"]
        if recipe.convert_suffix:
            output_parts.append(f"as {recipe.convert_suffix.upper().lstrip('.')}")
        if recipe.resize_preset_key:
            output_parts.append(f"using {preset_for_key(recipe.resize_preset_key).name}")
        if recipe.rename_prefix or recipe.rename_suffix:
            output_parts.append("with filename affixes")
        lines.append(", ".join(output_parts) + ".")
        if recipe.archive_after_export:
            lines.append(f"Then package the exported output as a {archive_format_for_key(recipe.archive_format).label} archive.")
    else:
        action_label = {
            RECIPE_TRANSFER_COPY: "Copy full bundles",
            RECIPE_TRANSFER_MOVE: "Move full bundles",
            RECIPE_TRANSFER_ARCHIVE: "Archive full bundles",
        }.get(recipe.transfer_mode, "Handle full bundles")
        if recipe.group_by_record_folder:
            lines.append(f"{action_label} into per-shot folders.")
        else:
            lines.append(f"{action_label} into a shared handoff folder.")
    if recipe.destination_subfolder:
        lines.append(f'Default destination subfolder: "{recipe.destination_subfolder}".')
    if recipe.strip_metadata:
        lines.append("Output files strip EXIF/ICC metadata.")
    return tuple(lines[:3])


def build_workflow_export_plan(
    sources: list[ResizeSourceItem],
    recipe: WorkflowRecipe,
    *,
    destination_dir: str,
) -> WorkflowExportPlan:
    normalized_destination = str(Path(destination_dir).expanduser())
    if not normalized_destination:
        return WorkflowExportPlan(
            recipe=recipe,
            destination_dir="",
            items=(),
            executable_items=(),
            output_label="",
            error_count=1,
            can_apply=False,
            general_error="Choose a destination folder.",
        )

    destination_path = Path(normalized_destination)
    final_suffix = _normalized_output_suffix(recipe.convert_suffix)
    if final_suffix and final_suffix not in OUTPUT_FORMAT_NAMES:
        return WorkflowExportPlan(
            recipe=recipe,
            destination_dir=normalized_destination,
            items=(),
            executable_items=(),
            output_label="",
            error_count=1,
            can_apply=False,
            general_error="Choose a supported export format.",
        )

    width = 0
    height = 0
    size_label = ""
    if recipe.resize_preset_key:
        preset = preset_for_key(recipe.resize_preset_key)
        width = max(0, preset.width)
        height = max(0, preset.height)
        size_label = preset.name

    items: list[WorkflowExportItem] = []
    executable: list[WorkflowExportItem] = []
    reserved_targets: set[str] = set()
    error_count = 0

    for source in sources:
        if not source.source_path or not os.path.exists(source.source_path):
            item = WorkflowExportItem(
                source=source,
                target_path=source.source_path,
                target_name=source.source_name,
                target_suffix=final_suffix or suffix_for_path(source.source_path) or ".jpg",
                width=width,
                height=height,
                status="Error",
                message="Source file is missing.",
            )
            items.append(item)
            error_count += 1
            continue

        source_suffix = suffix_for_path(source.source_path)
        target_suffix = final_suffix or source_suffix or ".jpg"
        if target_suffix not in OUTPUT_FORMAT_NAMES:
            target_suffix = ".jpg"
        target_name = _export_target_name(source.source_name, target_suffix, recipe.rename_prefix, recipe.rename_suffix)
        target_path = _unique_export_target_path(destination_path, target_name, reserved_targets)
        reserved_targets.add(normalized_path_key(target_path))
        item = WorkflowExportItem(
            source=source,
            target_path=target_path,
            target_name=Path(target_path).name,
            target_suffix=target_suffix,
            width=width,
            height=height,
            status="Export",
            message="",
        )
        items.append(item)
        executable.append(item)

    output_label_parts: list[str] = []
    if final_suffix:
        output_label_parts.append(OUTPUT_FORMAT_NAMES.get(final_suffix, final_suffix.upper().lstrip(".")))
    if size_label:
        output_label_parts.append(size_label)

    return WorkflowExportPlan(
        recipe=recipe,
        destination_dir=normalized_destination,
        items=tuple(items),
        executable_items=tuple(executable),
        output_label=" | ".join(output_label_parts) if output_label_parts else "Original Size",
        error_count=error_count,
        can_apply=bool(executable) and error_count == 0,
        general_error="",
    )


def apply_workflow_export_plan(
    plan: WorkflowExportPlan,
    *,
    progress_callback=None,
) -> tuple[str, ...]:
    if not plan.executable_items:
        return ()

    written_paths: list[str] = []
    total = len(plan.executable_items)
    for index, item in enumerate(plan.executable_items, start=1):
        target_size = QSize(item.width, item.height) if item.width > 0 and item.height > 0 else QSize()
        loaded = _load_resize_image(
            item.source.source_path,
            target_size=target_size,
            ignore_orientation=False,
            strip_metadata=plan.recipe.strip_metadata,
        )
        image = loaded.image
        if item.width > 0 and item.height > 0:
            image = _scaled_image(image, target_size=QSize(item.width, item.height), shrink_only=True)
        _save_resized_image(
            image,
            target_path=item.target_path,
            target_suffix=item.target_suffix,
            exif_bytes=None if plan.recipe.strip_metadata else loaded.exif_bytes,
            icc_profile=None if plan.recipe.strip_metadata else loaded.icc_profile,
        )
        written_paths.append(item.target_path)
        if progress_callback is not None:
            progress_callback(index, total, f"Saved {item.target_name}")
    return tuple(written_paths)


class WorkflowExportSignals(QObject):
    started = Signal(int)
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)


class WorkflowExportTask(QRunnable):
    def __init__(self, plan: WorkflowExportPlan) -> None:
        super().__init__()
        self.plan = plan
        self.signals = WorkflowExportSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        total = max(1, len(self.plan.executable_items))
        self.signals.started.emit(total)
        try:
            written_paths = apply_workflow_export_plan(
                self.plan,
                progress_callback=lambda current, total_steps, message: self.signals.progress.emit(current, total_steps, message),
            )
        except Exception as exc:  # pragma: no cover - worker/runtime path
            self.signals.failed.emit(str(exc))
            return
        self.signals.finished.emit(written_paths)


def build_best_of_set_plan(
    records: list[ImageRecord],
    *,
    ai_bundle: AIBundle | None,
    review_bundle: "ReviewIntelligenceBundle | None",
    burst_recommendations: dict[str, BurstRecommendation],
    annotations_by_path: dict[str, SessionAnnotation] | None = None,
    limit: int = 12,
    strategy: str = BEST_OF_BALANCED,
) -> BestOfSetPlan:
    if not records:
        return BestOfSetPlan(strategy=strategy, candidates=(), summary_lines=("No images are loaded.",))

    annotations = annotations_by_path or {}
    review_lookup = review_bundle.insights_by_path if review_bundle is not None else {}

    ranked_rows: list[tuple[float, BestOfSetCandidate]] = []
    for record in records:
        ai_result = find_ai_result_for_record(ai_bundle, record) if ai_bundle is not None else None
        burst = burst_recommendations.get(record.path) or burst_recommendations.get(normalized_path_key(record.path))
        review_insight = review_lookup.get(record.path) or review_lookup.get(normalized_path_key(record.path))
        annotation = annotations.get(record.path, SessionAnnotation())
        score = ai_strength(ai_result) * 100.0
        reasons: list[str] = []
        group_id = ""

        if ai_result is not None:
            group_id = ai_result.group_id
            if ai_result.rank_in_group == 1 and ai_result.group_size > 1:
                score += 8.0
                reasons.append("AI group leader")
            if ai_result.confidence_bucket == AIConfidenceBucket.OBVIOUS_WINNER:
                score += 6.0
                reasons.append("Obvious winner")
            elif ai_result.confidence_bucket == AIConfidenceBucket.LIKELY_KEEPER:
                score += 3.0
                reasons.append("Likely keeper")
        if burst is not None and burst.is_recommended:
            score += 9.0
            reasons.append("Best frame in group")
            if not group_id:
                group_id = burst.group_id
        if review_insight is not None and getattr(review_insight, "is_duplicate", False):
            score -= 5.0
            reasons.append("Duplicate penalty")
        round_value = normalize_review_round(annotation.review_round)
        if round_value in {REVIEW_ROUND_THIRD_PASS, REVIEW_ROUND_HERO}:
            score += 4.0
            reasons.append(review_round_label(round_value))
        if annotation.winner:
            score += 5.0
            reasons.append("Already accepted")

        ranked_rows.append(
            (
                score,
                BestOfSetCandidate(
                    path=record.path,
                    score=round(score, 3),
                    reason=", ".join(reasons[:3]) or "Strong overall combined score",
                    group_id=group_id,
                    ai_bucket=ai_result.confidence_bucket.value if ai_result is not None else "",
                ),
            )
        )

    ranked_rows.sort(key=lambda item: (-item[0], Path(item[1].path).name.casefold()))
    chosen: list[BestOfSetCandidate] = []

    if strategy == BEST_OF_TOP_PER_GROUP:
        seen_groups: set[str] = set()
        for _score, candidate in ranked_rows:
            group_key = candidate.group_id or normalized_path_key(candidate.path)
            if group_key in seen_groups:
                continue
            seen_groups.add(group_key)
            chosen.append(candidate)
            if len(chosen) >= limit:
                break
    elif strategy == BEST_OF_TOP_N:
        chosen = [candidate for _score, candidate in ranked_rows[:limit]]
    else:
        seen_groups: set[str] = set()
        for _score, candidate in ranked_rows:
            if "Best frame" not in candidate.reason and "group leader" not in candidate.reason.lower():
                continue
            group_key = candidate.group_id or normalized_path_key(candidate.path)
            if group_key in seen_groups:
                continue
            seen_groups.add(group_key)
            chosen.append(candidate)
            if len(chosen) >= limit:
                break
        if len(chosen) < limit:
            chosen_keys = {normalized_path_key(candidate.path) for candidate in chosen}
            for _score, candidate in ranked_rows:
                if normalized_path_key(candidate.path) in chosen_keys:
                    continue
                chosen.append(candidate)
                chosen_keys.add(normalized_path_key(candidate.path))
                if len(chosen) >= limit:
                    break

    summary_lines = (
        f"Built {len(chosen)} proposed best-of pick(s) using {strategy.replace('_', ' ')}.",
        "Selections remain editable after the shortlist is applied.",
    )
    return BestOfSetPlan(strategy=strategy, candidates=tuple(chosen), summary_lines=summary_lines)


def recipe_key_for_name(name: str) -> str:
    text = " ".join((name or "").strip().split())
    if not text:
        return ""
    cleaned = [
        character.lower()
        if character.isalnum()
        else "_"
        for character in text
    ]
    normalized = "".join(cleaned).strip("_")
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized


def _normalized_output_suffix(value: str) -> str:
    text = (value or "").strip().lower()
    if not text:
        return ""
    if not text.startswith("."):
        text = f".{text}"
    if text == ".jpeg":
        return ".jpg"
    if text == ".tif":
        return ".tiff"
    return text


def _export_target_name(source_name: str, target_suffix: str, prefix: str, suffix: str) -> str:
    source_path = Path(source_name)
    stem = f"{prefix}{source_path.stem}{suffix}".strip()
    return f"{stem or source_path.stem}{target_suffix}"


def _unique_export_target_path(destination_dir: Path, requested_name: str, reserved_targets: set[str]) -> str:
    destination_dir.mkdir(parents=True, exist_ok=True)
    requested = Path(requested_name)
    stem = requested.stem
    suffix = requested.suffix
    counter = 0
    while True:
        candidate_name = requested.name if counter == 0 else f"{stem}_{counter}{suffix}"
        candidate_path = destination_dir / candidate_name
        candidate_key = normalized_path_key(candidate_path)
        if candidate_key not in reserved_targets and not candidate_path.exists():
            return str(candidate_path)
        counter += 1
