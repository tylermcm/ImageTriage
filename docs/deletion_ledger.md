# Deletion ledger

Every removal in Phase 2 is recorded here **before** it is committed: what went, why it is safe, the evidence, and which tests changed. Nothing here is recoverable from an archive branch (decision D2), so the evidence column matters.

## Method
1. Run `py -3.13 scripts/reachability_report.py`. It is name-based, so its findings are *candidates*.
2. For each candidate, verify by hand: search code, tests and strings, and check for `getattr`/string dispatch and Qt connections. Add any legitimate dynamic use to `scripts/reachability_whitelist.txt` with a reason.
3. Delete in small rounds (unused imports first, then methods). Run the full suite after each round.
4. Add a row per removal (group mechanical ones, e.g. "12 unused imports in window.py").

## Baseline (2026-09-26, before any deletion)
| Section | Count |
|---|---|
| orphan modules | 0 |
| unused imports | 46 |
| unreferenced methods (audited classes) | 48 |
| methods referenced only by tests | 8 |
| unreferenced module-level defs | 30 |
| signals never connected or never emitted | 14 |

## Ledger
| Date | Work item | What was removed | Why | Evidence | Tests touched |
|---|---|---|---|---|---|
| 2026-09-26 | 2.3 r1 | 46 unused imports across 13 files (32 in `window.py`: the AI-training task classes and helpers) | Imported, never used | Report + not imported from the module or reached via alias elsewhere; `ratio_px` was a re-export and was restored | none |
| 2026-09-26 | 2.3 r2 | 30 methods: `MainWindow` `_make_action_button`, `_sync_topbar_action_button`, `_sync_columns_combo`, `_resize_selected_record`, `_toggle_compare_shortcut`, `_inspection_stats_for_thumbnail`, `_show_help_menu`, `_show_settings_help`, `_set_winner_by_path`, `_set_reject_by_path`, `_persist_annotation`, `_safe_trash_directory`; `FullScreenPreview` `_studio_group_label`, `_studio_divider`, `_toggle_mockup_maximized`, `compare_count`, `cycle_focus_assist_strength`, `winner_ladder_mode_enabled`; `ThumbnailGridView` `set_zoom_tile_width`, `current_tile_width`, `_single_visible_item_aspect_ratio`; `InspectorPanel` `_make_quick_actions`, `_safe_text`, `_quality_level`, `_motion_blur_level`, `_quality_confidence_label`, `_best_candidate_text`, `_worth_editing_text`; `PhotoEditorPanel` `_tool_toggle`, `add_color_range_mask` | Zero references | Zero references in code, tests, string literals, docs, `aiculler`, `scripts`, `packaging`, `cli_editor`; no dynamic method dispatch | none |
| 2026-09-26 | 2.3 r2 | Import `settings_help_pages` in `window.py`; signals `InspectorPanel.best_of_set_requested`, `open_editor_requested`, `reveal_requested` | Cascade / never connected or emitted | 0 connects, 0 emits | none |
| 2026-09-26 | 2.4a | `MainWindow`: `_active_ai_activity_tag_key`, `_show_legacy_ai_setup_dialog`, `_open_ai_training_stats_dialog`, `_ranker_fit_diagnosis_for_paths`, `_cached_training_label_counts`, `_open_dino_prefilter_artifacts`, `_set_active_ai_checkpoint`, `_clear_active_ai_checkpoint_override`, `_set/_clear_active_reference_bank_path`, `_active/_set_culling_signal_weights_path`, `_configure_general_training_sources`, `_harvest_speed_cull_decisions_for_folders`, `_run_uses_training_paths`, `_reference_bank_output_dir`, `_show_ai_menu`, `_load_correction_events_for_current_folder` (18, 410 lines) | Unreachable (D2) | Zero references in code, tests, strings, docs and non-package trees | none |
| 2026-09-26 | 2.4a | `ai_training.py`: `LaunchLabelingAppTask`, `PrepareLabelingCandidatesTask`, `PrepareTrainingDataTask`, `TrainRankerTask`, `EvaluateRankerTask`, `ScoreCurrentFolderTask`, `BuildCullingSignalsTask`, `EvaluateCullingSignalsTask`, `TuneCullingSignalsTask`, `BuildReferenceBankTask` (10, 1,317 lines) | No importer after 2.3 | Only `tests/test_ai_training.py` touched `LaunchLabelingAppTask` | 3 launcher tests removed |
| 2026-09-26 | 2.4a | `ai_training.py`: 47 helpers unreachable from any production caller (ranker run/eval summaries, labeling command builders, general-pool rebuild, csv/jsonl writers and others; 1,047 lines). Kept: registered-source registry incl. `set_registered_training_source_enabled` | Closure analysis: not reachable from production code outside the file | reachability closure from production references | 6 tests removed (`set_active_ranker_selection`, `prepare_general_training_pool`, labeled include file/clusters, stale-artifact evaluation, `build_labeling_command`) |
| 2026-09-26 | 2.4b | `ui/train_ranker_dialog.py`, `ui/ranker_manager_dialog.py` (761 lines), their `ui/__init__.py` exports and `window.py` imports | Never instantiated | no references outside the package init and window imports | none |
| 2026-09-26 | 2.4d | `image_triage/dino_prefilter.py`, `tests/test_dino_prefilter.py`; `DINOPrefilterRunTask` and 12 DINO methods in `AICullerRunTask` (about 600 lines); 8 DINO methods in `MainWindow`; the DINO settings page and worker control; the DINO workflow-center step; `FilterMode.DINO_REMOVED` / `DINO_RESCUED`; the `dino_rank` signal column | Feature never runs (hard-coded off); decoupled from pHash | Characterization tests for the pHash-only run pass before and after; saved "DINO Removed" filters fall back to All | 4 workflow + 2 worker + 1 settings tests removed; 1 filter test rewritten |
| 2026-09-26 | 2.4d | `MainWindow._delete_phash_prefilter_artifacts` (60 lines) | Unreachable: its only entry point was the dead DINO workflow step; "Reset AI Cache" already offers a pHash-artifacts checkbox | 0 references | none |
