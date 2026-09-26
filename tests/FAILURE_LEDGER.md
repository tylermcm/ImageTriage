# Test failure ledger

Rule: the suite must be green. Every known-broken test is a **strict** `xfail`
(it fails the build if it starts passing) and names the work item that resolves it.
Run `pytest tests -rx` to list them. Update this file whenever an xfail is added or removed.

| Test | Class | Reason | Resolved by |
|---|---|---|---|
| `test_window_catalog_cache` x7 (`apply_startup_window_state_fixup`, `handle_ai_run_finished`, `handle_ai_training_finished`, `load_ai_results_uses_catalog_cache`, `reset_ai_review_cache`, `run_ai_pipeline` x2) | Stub drift | Hand-built `MainWindow` stand-ins are missing attributes or are not real `QWidget`s. Some cover AI-mode code retired 2026-09-19. | WI-0.5 (real-window harness); AI-mode ones also D2 |
| `test_topbar_style::test_fluent_icon_has_theme_specific_interaction_states` | Stub drift | `SimpleNamespace` stub lacks `_render_fluent_glyphs`. | WI-0.5 |
| `test_ai_training::test_registered_training_source_enabled_state_is_persisted` | Legacy pipeline | Legacy training-source behaviour. | D2 / WI-2.4-2.6 |
| `test_ai_workflow::test_default_runtime_prefers_generic_bundled_checkpoint_location` | Legacy pipeline | Legacy runtime default (`cuda` vs `auto`). | D2 / WI-2.4-2.6 |
| `test_ai_results_phase1` x5 | Behaviour changed on purpose | AI-Culler foundation pass is observational only and no longer changes bucket assignment. (Pre-existing xfails.) | D2 / AI-mode retirement |

## Repaired in WI-0.3 (no longer failing)
- `test_catalog_repository` x2: stub drift (patched a removed `_catalog` attribute). Repaired; real behaviour unchanged.
- `test_decision_harvest` x6: not schema drift. Windows kept `decisions.sqlite3` locked after `with sqlite3.connect()` (see N18). Test temp-dir now collects garbage first.
- `test_aiculler_technical_tags`: bit-exact float comparison relaxed to 1e-6.
- `test_ai_results_phase1` tag definitions, `test_grid_failures` x4, `test_nav_rail`: expectations rewritten to the current design's invariants.
- `test_preview_polling`: invalid fixture (`None` metadata) fixed; header code hardened.
