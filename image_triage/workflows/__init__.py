from __future__ import annotations

"""Workflow backend package.

This package keeps recipe definitions, export execution, and shortlist planning
separate so the workflow backend can evolve without forcing unrelated changes
through the window module.
"""

from .best_of import (
    BEST_OF_BALANCED,
    BEST_OF_TOP_N,
    BEST_OF_TOP_PER_GROUP,
    BestOfSetCandidate,
    BestOfSetPlan,
    build_best_of_set_plan,
)
from .export import (
    WorkflowExportItem,
    WorkflowExportPlan,
    WorkflowExportSignals,
    WorkflowExportTask,
    apply_workflow_export_plan,
    build_workflow_export_plan,
)
from .models import (
    RECIPE_CONTENT_BUNDLE,
    RECIPE_CONTENT_EXPORT,
    RECIPE_TRANSFER_ARCHIVE,
    RECIPE_TRANSFER_COPY,
    RECIPE_TRANSFER_MOVE,
    WORKSPACE_AI_REVIEW,
    WORKSPACE_COMPARE,
    WORKSPACE_DELIVERY,
    WORKSPACE_FAST_CULLING,
    WORKSPACE_METADATA,
    WorkflowRecipe,
    WorkspacePreset,
    built_in_workflow_recipes,
    built_in_workspace_presets,
    deserialize_workflow_recipe,
    deserialize_workspace_preset,
    recipe_key_for_name,
    recipe_summary_lines,
    serialize_workflow_recipe,
    serialize_workspace_preset,
)
from .paths import workflow_archive_path, workflow_destination_dir, workflow_record_folder_name
from .storage import (
    dump_saved_workflow_recipes,
    dump_saved_workspace_presets,
    load_saved_workflow_recipes,
    load_saved_workspace_presets,
)

__all__ = [
    "BEST_OF_BALANCED",
    "BEST_OF_TOP_N",
    "BEST_OF_TOP_PER_GROUP",
    "BestOfSetCandidate",
    "BestOfSetPlan",
    "RECIPE_CONTENT_BUNDLE",
    "RECIPE_CONTENT_EXPORT",
    "RECIPE_TRANSFER_ARCHIVE",
    "RECIPE_TRANSFER_COPY",
    "RECIPE_TRANSFER_MOVE",
    "WORKSPACE_AI_REVIEW",
    "WORKSPACE_COMPARE",
    "WORKSPACE_DELIVERY",
    "WORKSPACE_FAST_CULLING",
    "WORKSPACE_METADATA",
    "WorkflowExportItem",
    "WorkflowExportPlan",
    "WorkflowExportSignals",
    "WorkflowExportTask",
    "WorkflowRecipe",
    "WorkspacePreset",
    "apply_workflow_export_plan",
    "build_best_of_set_plan",
    "build_workflow_export_plan",
    "built_in_workflow_recipes",
    "built_in_workspace_presets",
    "deserialize_workflow_recipe",
    "deserialize_workspace_preset",
    "dump_saved_workflow_recipes",
    "dump_saved_workspace_presets",
    "load_saved_workflow_recipes",
    "load_saved_workspace_presets",
    "recipe_key_for_name",
    "recipe_summary_lines",
    "serialize_workflow_recipe",
    "serialize_workspace_preset",
    "workflow_archive_path",
    "workflow_destination_dir",
    "workflow_record_folder_name",
]
