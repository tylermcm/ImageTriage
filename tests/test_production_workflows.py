from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from image_triage.ai_results import AIBundle, AIConfidenceBucket, AIImageResult
from image_triage.image_resize import ResizeSourceItem
from image_triage.keyboard_mapping import ShortcutBinding, serialize_shortcut_overrides, shortcut_conflicts
from image_triage.models import ImageRecord, SessionAnnotation
from image_triage.production_workflows import (
    BEST_OF_BALANCED,
    WorkflowRecipe,
    WorkspacePreset,
    build_best_of_set_plan,
    build_workflow_export_plan,
    deserialize_workflow_recipe,
    deserialize_workspace_preset,
    serialize_workflow_recipe,
    serialize_workspace_preset,
)
from image_triage.review_intelligence import ReviewInsight, ReviewIntelligenceBundle
from image_triage.review_workflows import REVIEW_ROUND_HERO, BurstRecommendation
from image_triage.scanner import normalized_path_key


def _record(path: str, *, modified_ns: int = 1) -> ImageRecord:
    resolved = Path(path)
    return ImageRecord(
        path=str(resolved),
        name=resolved.name,
        size=1024,
        modified_ns=modified_ns,
    )


def _ai_result(
    path: str,
    *,
    group_id: str,
    group_size: int,
    rank_in_group: int,
    score: float,
    normalized_score: float,
    bucket: AIConfidenceBucket,
) -> AIImageResult:
    resolved = Path(path)
    return AIImageResult(
        image_id=resolved.stem,
        file_path=str(resolved),
        file_name=resolved.name,
        group_id=group_id,
        group_size=group_size,
        rank_in_group=rank_in_group,
        score=score,
        normalized_score=normalized_score,
        confidence_bucket=bucket,
        confidence_summary="",
    )


def _ai_bundle(*results: AIImageResult) -> AIBundle:
    results_by_path = {normalized_path_key(result.file_path): result for result in results}
    return AIBundle(
        source_path="C:/shots",
        export_csv_path="C:/shots/ranked_clusters_export.csv",
        results_by_path=results_by_path,
    )


class ProductionWorkflowTests(unittest.TestCase):
    def test_recipe_and_workspace_preset_roundtrip(self) -> None:
        recipe = WorkflowRecipe(
            key="client_delivery",
            name="Client Delivery",
            description="Export ready-to-share JPEGs.",
            destination_subfolder="Delivery",
            archive_after_export=True,
            archive_format="zip",
            resize_preset_key="2k",
            convert_suffix=".jpg",
            rename_prefix="proof_",
            rename_suffix="_final",
        )
        preset = WorkspacePreset(
            key="delivery_export",
            name="Delivery / Export",
            description="Optimized for handoff.",
            ui_mode="manual",
            columns=4,
            compare_enabled=False,
            auto_advance=False,
            burst_groups=True,
            burst_stacks=False,
            library_panel_mode="collapsed",
            inspector_panel_mode="expanded",
            workspace_state={"dock_layout": {"splitter": "abc"}},
        )

        self.assertEqual(deserialize_workflow_recipe(serialize_workflow_recipe(recipe)), recipe)
        self.assertEqual(deserialize_workspace_preset(serialize_workspace_preset(preset)), preset)

    def test_build_workflow_export_plan_applies_affixes_and_unique_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            source_a = temp_root / "capture.raw"
            source_b = temp_root / "capture.jpg"
            source_a.write_bytes(b"raw")
            source_b.write_bytes(b"jpg")
            destination_dir = temp_root / "Delivery"

            plan = build_workflow_export_plan(
                [
                    ResizeSourceItem(source_path=str(source_a), source_name=source_a.name),
                    ResizeSourceItem(source_path=str(source_b), source_name=source_b.name),
                ],
                WorkflowRecipe(
                    key="proofing_jpegs",
                    name="Proofing JPEGs",
                    destination_subfolder="Delivery",
                    resize_preset_key="large",
                    convert_suffix=".jpg",
                    rename_prefix="proof_",
                    rename_suffix="_client",
                ),
                destination_dir=str(destination_dir),
            )

            self.assertTrue(plan.can_apply)
            self.assertEqual(len(plan.executable_items), 2)
            self.assertEqual(plan.error_count, 0)
            self.assertEqual(plan.items[0].target_name, "proof_capture_client.jpg")
            self.assertEqual(plan.items[1].target_name, "proof_capture_client_1.jpg")
            self.assertTrue(all(str(destination_dir) in item.target_path for item in plan.items))
            self.assertIn("JPEG", plan.output_label)

    def test_build_best_of_set_plan_balances_group_leaders(self) -> None:
        records = [
            _record("C:/shots/burst_01.jpg", modified_ns=1),
            _record("C:/shots/burst_02.jpg", modified_ns=2),
            _record("C:/shots/final_01.jpg", modified_ns=3),
            _record("C:/shots/final_02.jpg", modified_ns=4),
        ]
        ai_bundle = _ai_bundle(
            _ai_result(records[0].path, group_id="burst-ai", group_size=2, rank_in_group=1, score=0.95, normalized_score=98.0, bucket=AIConfidenceBucket.OBVIOUS_WINNER),
            _ai_result(records[1].path, group_id="burst-ai", group_size=2, rank_in_group=2, score=0.61, normalized_score=55.0, bucket=AIConfidenceBucket.NEEDS_REVIEW),
            _ai_result(records[2].path, group_id="finals", group_size=2, rank_in_group=1, score=0.88, normalized_score=90.0, bucket=AIConfidenceBucket.LIKELY_KEEPER),
            _ai_result(records[3].path, group_id="finals", group_size=2, rank_in_group=2, score=0.49, normalized_score=42.0, bucket=AIConfidenceBucket.LIKELY_REJECT),
        )
        review_bundle = ReviewIntelligenceBundle(
            groups=(),
            insights_by_path={
                records[3].path: ReviewInsight(
                    path=records[3].path,
                    group_id="dup-1",
                    group_kind="likely_duplicate",
                    group_label="Near Dup",
                    group_size=2,
                    rank_in_group=2,
                ),
                normalized_path_key(records[3].path): ReviewInsight(
                    path=records[3].path,
                    group_id="dup-1",
                    group_kind="likely_duplicate",
                    group_label="Near Dup",
                    group_size=2,
                    rank_in_group=2,
                ),
            },
        )
        burst_recommendations = {
            records[0].path: BurstRecommendation(
                path=records[0].path,
                group_id="burst-1",
                group_label="Burst",
                group_size=2,
                recommended_path=records[0].path,
                rank_in_group=1,
                score=96.0,
                recommended_score=96.0,
                is_recommended=True,
            ),
            records[1].path: BurstRecommendation(
                path=records[1].path,
                group_id="burst-1",
                group_label="Burst",
                group_size=2,
                recommended_path=records[0].path,
                rank_in_group=2,
                score=84.0,
                recommended_score=96.0,
                is_recommended=False,
            ),
        }
        annotations = {
            records[2].path: SessionAnnotation(winner=True, review_round=REVIEW_ROUND_HERO),
        }

        plan = build_best_of_set_plan(
            records,
            ai_bundle=ai_bundle,
            review_bundle=review_bundle,
            burst_recommendations=burst_recommendations,
            annotations_by_path=annotations,
            limit=2,
            strategy=BEST_OF_BALANCED,
        )

        self.assertEqual(len(plan.candidates), 2)
        self.assertEqual({candidate.path for candidate in plan.candidates}, {records[0].path, records[2].path})
        self.assertTrue(any("Built 2 proposed best-of pick(s)" in line for line in plan.summary_lines))

    def test_shortcut_conflicts_detect_duplicate_assignments(self) -> None:
        bindings = [
            ShortcutBinding(id="review.accept", label="Accept", section="Review", shortcut="Ctrl+1"),
            ShortcutBinding(id="review.reject", label="Reject", section="Review", shortcut="Ctrl+1"),
            ShortcutBinding(id="workspace.palette", label="Command Palette", section="Workspace", default_shortcut="Ctrl+K"),
        ]

        conflicts = shortcut_conflicts(bindings)

        self.assertEqual(conflicts, {"Ctrl+1": ["review.accept", "review.reject"]})
        self.assertEqual(
            serialize_shortcut_overrides({"review.accept": " ctrl+1 ", "blank": "", "workspace.palette": "Ctrl+K"}),
            {"review.accept": "Ctrl+1", "workspace.palette": "Ctrl+K"},
        )


if __name__ == "__main__":
    unittest.main()
