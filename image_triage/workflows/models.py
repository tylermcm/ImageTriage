from __future__ import annotations

"""Workflow recipe and workspace preset definitions."""

from dataclasses import dataclass

from ..archive_ops import archive_format_for_key
from ..image_resize import preset_for_key


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
        return self.transfer_mode == RECIPE_TRANSFER_ARCHIVE or (
            self.uses_transform_export and self.archive_after_export
        )


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
            archive_label = archive_format_for_key(recipe.archive_format).label
            lines.append(f"Then package the exported output as a {archive_label} archive.")
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


def recipe_key_for_name(name: str) -> str:
    text = " ".join((name or "").strip().split())
    if not text:
        return ""
    cleaned = [character.lower() if character.isalnum() else "_" for character in text]
    normalized = "".join(cleaned).strip("_")
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized
