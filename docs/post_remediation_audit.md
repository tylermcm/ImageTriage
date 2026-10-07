# Post-Remediation Audit — Image Triage

Date: 2026-10-03 · Branch `codex/ui-ux-polish` · Analysis only. Nothing in the repo was changed to produce this report.

Method: independent of `docs/audit_and_remediation_plan.md`. Prior checklist claims were treated as hypotheses and tested against the code. Evidence came from AST and metric scans, pyflakes, name/attribute/import resolution over every class and module, a reachability pass cross-checked by grep, a full test-suite run (1,946 passed, 2 failed, 1 skipped, 11 xfailed), reproduction of suspected crashes on a real `MainWindow` inside the repo's hermetic test sandbox, and inspection of the live on-disk state. Where a finding is inferred rather than demonstrated, the Confidence field says so.

Not audited in depth (so nothing below should be read as clearing them): the editor internals (`photo_editor_panel`, `photo_terminal/`, being replaced by librawops), the vendored `aiculler/` scoring model quality, the PocketDrop native component, and actual CI run results (no access).

---

## 1. Executive Assessment

**Was the remediation broadly successful?** Partly. It did real, measurable good: `window.py` fell from 28,467 to 21,191 lines, `MainWindow` from 25,900 to 19,133 lines and from 1,138 to 976 methods, large dead subsystems were removed, the share-unreachable freeze (WI-8.5) and several data-safety problems were fixed, and the test suite is large (≈1,950 tests), mostly green and now properly tears down windows. The grid, review-wording and path-policy work is well tested.

**Is the codebase healthier?** Yes, modestly, in the places that were worked on. Structurally it is not as healthy as the plan's own checklist claims.

**Where the checklist overstates the result.**
- *A1 ("god object closed in full") is not true.* `MainWindow` is still a 19,000-line, 976-method, 526-attribute class. The 28 `_init_*` phases are a verbatim split of the old 1,300-line `__init__`. The controllers are code moves, not ownership moves: `records_view_controller.py` makes 826 private-attribute accesses to 202 distinct window attributes, and 98 window attributes are written from controllers. 137 of the 976 methods are pure forwarding delegates. This is a reorganisation of the god object, not its decomposition.
- *A3 is incorrectly completed.* The checklist says `CODEBASE_REVIEW.md` no longer exists. It is tracked at the repo root, along with six other stale plan documents.
- *T7 (test isolation) is incomplete.* `tests/test_ai_health.py` writes to the real `C:\Users\tylle\.image-triage\AI` because `ai_paths` derives its root from `USERPROFILE`, which the sandbox does not redirect.

**The remediation introduced real, reachable crashes that no test covers.** These come from deleting or renaming things while leaving callers behind:
- The first-run **"Set Up AI"** accept path raises `NameError` (`semantic_missing`).
- **View → Show hidden folders** and the settings-dialog accept (when that option changed) raise `AttributeError` (`_current_path_for_index`).
- The folder-tree **"Add To Library"** action raises `AttributeError` (`_start_catalog_refresh`).
- The full-screen preview's overflow menu raises `AttributeError` (`_auto_bracket_enabled`).
- `benchmarks/baseline.py` cannot import.

All of these are found by pyflakes in seconds. CI runs no lint step, so this class of defect will recur with every further deletion. That is the central process finding of this audit.

**Does another phase need to happen?** A short one, yes. It is a fix-and-guard pass, not another multi-week program. Fix the five broken references, add an undefined-name gate to CI, sandbox `USERPROFILE`, and finish the deletions that were started (child-process subsystem, retired AI-mode scaffolding, dead settings, stale root documents).

**Ready for feature development?** Not yet, and not unconditionally. New user-facing work on top of the AI setup, library, or hidden-folder flows would sit next to three known crashes. Once the "Must Fix" list in section 12 is done, the codebase is in a reasonable state for feature development, with the caveat that `MainWindow` remains the place where every feature will have to land. Feature work that touches the grid, review wording, path policy, sidecars, or the decision store is on solid ground now.

---

## 2. Previous Remediation Verification

Status key: **V** verified complete · **M** mostly complete · **P** partially complete · **X** incorrectly completed · **R** regressed · **U** unable to verify.

| Item | Claim | Status | Basis |
|---|---|---|---|
| WI-8.5 GUI never waits on a share | Done | **V** (one gap) | 39 tests with a simulated sleeping share, mutation-checked. Never run against a real asleep NAS. Two GUI-thread waits knowingly left (telemetry shutdown join ≤5 s at exit; aiculler ingested-paths refresh). |
| WI-8.2 NAS folder change detection | Done | **V** | 26 tests, migration 8 present. |
| WI-6.5 AI "why" on hover | Done | **V** | `ai_why.py` pure module, 38 tests. |
| O2 best-frame wording/gating | Done | **V** | Wording and cache key version verified. Scoring itself unchanged and, by the earlier evaluation, indistinguishable from chance. |
| A1 `MainWindow` decomposed "in full" | Closed | **X** | See §6. Metrics contradict. |
| A2 long functions | Reduced | **M** | The doc admits 99 functions >100 lines remain (`settings_dialog.__init__` 642, `preview.__init__` 507, `grid._paint_tile` 414, `ensure_semantic_masks` 313). |
| A3 stale docs removed | Closed | **X** | `CODEBASE_REVIEW.md` still tracked (May 31). Six more stale plan docs, four `*-sandbox.ps1` scripts, `sandboxes/`, `benchmarks/` and `scripts/*_prototype.py` also tracked. |
| Dead-code removal (WI-2.x) | Done | **M** | Large removals confirmed, but 40+ zero-reference functions and two classes remain (§5), and the removals broke callers (§4). |
| T7 hermetic tests | Done | **P** | `conftest.py` redirects `LOCALAPPDATA`, `APPDATA`, QSettings and QStandardPaths, but not `USERPROFILE` / the managed AI root. No test asserts that store default paths are sandboxed. |
| T4/T5 stub-window tests | Partial | **V as stated** | 5 stub-window tests remain xfail; the plan said so. |
| N3 single AI cache-root derivation | Resolved | **P** | `_default_user_cache_root` still defined 3×; two copies do not undo Store-Python AppData virtualization. Two `image_triage_ai_cache` dirs exist on disk. |
| WI-3.6 app-data migration | Done | **V** | Org-name flip is explicit and one-time. Cleanup of 615 polluted label rows was never done (user action). |
| AI-mode retirement | Done | **P** | App is manual-only, but 15 `MainWindow` methods (833 lines) still carry AI-mode branches; `docs/ai_mode_retirement.md` items undecided. |
| Test-suite state "known flaky" aiculler CLI tests | Flaky | **Reclassified** | Not flaky. Deterministic `WinError 6` under `pythonw`; see §8. Probably passes under a console runner, so **U** for CI. |

---

## 3. Remaining Functional Defects

### F-01 First-run "Set Up AI" crashes on accept
- **Category:** Regression (confirmed defect) · **Severity:** High · **Confidence:** Confirmed
- **Location:** `image_triage/window.py:8550` in `_show_ai_setup_dialog` (8345–8551); callers at 8619, 8684, 8709
- **Systems:** AI runtime install, first-run onboarding, settings AI setup
- **Description:** `AISetupSelection(download_semantic_model=semantic_missing)` reads a name that no longer exists. The assignment was deleted in a WI-2.x cleanup.
- **Evidence:** Reproduced on a real `MainWindow`: with the dialog accepted, `NameError: semantic_missing`. pyflakes reports it. No test calls the function (modal `exec`).
- **Flow:** fresh profile → `MainWindow` constructs → prompt is shown → user accepts → `NameError`. `AI_SETUP_PROMPTED_KEY` is already set, so the prompt never reappears and the user has no obvious way back into the flow.
- **Also:** the function `del`s all ten of its caller-supplied parameters (title, prompt text, `allow_*`, `default_*`), so every caller's intent is ignored. The dialog was evidently gutted and the signature kept.
- **Why it matters:** this is the first thing a new user sees.
- **Relation to remediation:** Regression.
- **Next action:** Fix; add test with `QDialog.exec` patched (the repro already exists in scratch).

### F-02 "Show hidden folders" toggle and the settings-accept path crash
- **Category:** Regression · **Severity:** High · **Confidence:** Confirmed
- **Location:** `window.py:14453` (`_handle_show_hidden_folders_toggled`, bound to a View action at `ui/actions.py:304`) and `window.py:19836` (settings dialog accept when `hidden_changed`)
- **Description:** both call `self._current_path_for_index(...)`, which no longer exists (replacement: `_current_visible_record_path`).
- **Evidence:** reproduced: `AttributeError: _current_path_for_index`. The error occurs *before* the folder list is rescanned.
- **Why it matters:** the setting is saved but the view never updates, and in the settings case the exception happens inside the accept handler, so later steps of the accept are skipped.
- **Relation:** Regression.
- **Next action:** Fix.

### F-03 Folder-tree "Add To Library" crashes after adding the root
- **Category:** Regression · **Severity:** Medium · **Confidence:** Confirmed
- **Location:** `window.py:9931` calls `self._start_catalog_refresh(...)`; the method lives on the controller as `CatalogController.start_catalog_refresh` with no window delegate.
- **Evidence:** `hasattr(window, "_start_catalog_refresh")` is False; `window._catalog.start_catalog_refresh` exists.
- **Flow:** `add_catalog_root` succeeds and persists, then the refresh call raises, so the library root is added but never indexed until some later refresh.
- **Relation:** Regression (controller extraction left a caller behind).
- **Next action:** Fix.

### F-04 Full-screen preview overflow menu reads a `MainWindow`-only attribute
- **Category:** Regression · **Severity:** Medium · **Confidence:** High (static trace; not driven interactively)
- **Location:** `image_triage/preview.py:2177`, `FullScreenPreview._populate_header_overflow_menu` reads `self._auto_bracket_enabled`, which exists only on `MainWindow`.
- **Flow:** opens when the "Review" group is hidden by the narrow-width overflow logic and the user opens the menu (`aboutToShow`).
- **Next action:** Fix; verify at narrow widths.

### F-05 `download_ai_model` default path calls an undefined function
- **Category:** Confirmed defect (latent) · **Severity:** Low · **Confidence:** Confirmed
- **Location:** `image_triage/ai_model.py:456` calls `resolve_ai_model_installation`, which is undefined. Every current caller passes `installation`, so it is not reachable today.
- **Next action:** Fix or delete the default branch.

### F-06 `benchmarks/baseline.py` cannot import
- **Category:** Regression · **Severity:** Low · **Confidence:** Confirmed
- **Location:** `benchmarks/baseline.py:20` imports `_run_command_with_live_output` from `ai_workflow`, deleted in WI-2.x.
- **Next action:** Fix or retire the benchmark (see D-12).

### F-07 Two Settings checkboxes control nothing
- **Category:** Incomplete feature · **Severity:** Low · **Confidence:** Confirmed
- **Location:** pHash prefilter `cache_enabled` ("Cache pHash metadata") and `diagnostics_enabled` ("Write per-run diagnostics and audit rows"): stored and passed along, but `phash_prefilter.py` has no consumer for either.
- **Why it matters:** a user turning these off believes something changed.
- **Next action:** Remove the controls, or wire them.

### F-08 pHash prefilter rows are produced only during an AI culling run
- **Category:** Architectural defect · **Severity:** Low · **Confidence:** High
- **Location:** `aiculler_workflow._run_phash_prefilter`; the prefilter is on by default and loaded on every view finalize.
- **Description:** with the app now manual-only (AI mode retired), the data the prefilter loads can only come from an older run. Needs a decision, not a patch.
- **Next action:** Investigation.

---

## 4. Newly Introduced Regressions

Everything in F-01 through F-06 is a regression introduced by the remediation (deleted or moved symbol, caller not updated). Additional regression-type findings:

### R-01 `_show_ai_setup_dialog` ignores all caller parameters
See F-01. Listed separately because the fix is a design decision (restore or remove the parameters), not a one-line fix.

### R-02 Root cause: no automated gate for undefined names
- **Category:** Architectural (process) defect · **Severity:** High · **Confidence:** Confirmed
- **Evidence:** `.github/workflows/tests.yml` runs the Windows suite only; no lint/pyflakes step. Running pyflakes (2.4.0) finds F-01 to F-06 immediately. Static class-attribute resolution (a script over every class whose bases resolve) found the preview defect too. No test references `_start_catalog_refresh`, `_current_path_for_index` or `semantic_missing`.
- **Why it matters:** the project deletes and moves code aggressively (and plans to delete more: librawops, AI-mode scaffolding). Without a gate every such change can orphan a caller. This is the one change most likely to prevent the next regression.
- **Next action:** Fix (add a CI step).

---

## 5. Dead, Obsolete, or Incomplete Systems

### D-01 Child-process supervision is a dead subsystem
- **Severity:** Low · **Confidence:** Confirmed
- `window._register_child_process` (line 7920) has zero callers. `_child_processes` is always empty, a timer prunes the empty dict, and `_shutdown_child_processes` is a no-op. The child-sync state file (`_write_child_sync_state` / `_cleanup_child_sync_state`) is still written. Reachability report flags the one unreferenced method.
- **Next action:** Cleanup (permitted under the dead-code rule: verified unreferenced).

### D-02 Zero-reference functions and classes still present
Verified by grep across `image_triage`, `aiculler`, `tests`, `packaging`, `scripts`, `sandboxes`:
- `aiculler_workflow`: `_proportional_quotas` (48 lines), `_read_include_paths_file`, `_write_text_atomically`, `_file_cache_identity`
- `ai_runtime_packages.uninstall_ai_runtime`; `ai_workflow_center._sorted_count_pairs`, `_format_count_pairs`; `app_logging.get_logger`
- `semantic_search.metadata_from_store_row`; `updater.launch_update_installer` (13 lines, **in the updater**, so confirm intent first); `window._parse_pip_raw_progress`
- `photo_terminal`: `adjustments._offset_channel`, `clone_stamp`, `auto_remove_dust` (docs only), `masks.combine_masks`, `make_radial_mask`, `make_linear_gradient_mask`, `soften_mask`, `adjust_mask_bounds`, `session.find_mask`, `io.iter_images`
- `quality.store.fetch_all_faces_by_path`; `grid_card_renderer._full_card_basis`, `_fill_round_rect`; `help_topics.collection_help_pages`; `xmp.load_sidecar_annotations` (only `CODEBASE_REVIEW.md` mentions it)
- Classes `Segmented` (preview_studio), `PrototypeFileIconProvider`
- **Not dead (tool blind spots, kept honest):** `validate_ai_runtime_imports`, `configure_frozen_stdlib`, `consolidate_folder`, quality-store upsert/fetch are used from `packaging/`, `scripts/`, `sandboxes/`, `aiculler`.
- **Caveat:** the `photo_terminal` items belong to the editor that librawops will replace; leave them for that migration.
- **Next action:** Cleanup, except `photo_terminal` (Leave alone) and `launch_update_installer` (Investigation first).

### D-03 Test-only production helpers
`clip_model_variant_options`, `delete_adapter_model`, `ai_review_badge_label`, `list_registered_training_sources`, `set_registered_training_source_enabled`, `app_log_path`, `display_provider_id_for_path`, the `mac_media` sidecar readers, `_tighten_mask_confidence`, `reset_pane_widths`. Called only from tests. Either the feature is missing a caller or the code and its tests can go together. **Next action:** Investigation, per item.

### D-04 Retired AI-mode scaffolding in `MainWindow`
- **Severity:** Medium (maintainability) · **Confidence:** Confirmed
- `_ui_mode` is only ever `'manual'` (`set_ui_mode` forces 0). Fifteen methods (833 lines) still branch on AI mode, including `_handle_mode_tab_changed` (131), `_update_ai_toolbar_state` (205, the AI toolbar page is never shown), `_apply_ai_review_burst_lockout` (38), plus `ai_path_combo` / control and `AI_RANK`. The retirement document lists undecided items.
- **Why it matters:** this is the largest block of unreachable-in-practice code in the class the plan said it decomposed, and it makes reading `MainWindow` harder than it looks.
- **Next action:** Cleanup, after the product decision recorded in `docs/ai_mode_retirement.md`.

### D-05 One-time migration code that runs every start
`COMPACT_CARDS_KEY` and `LEGACY_TOOLBAR_STYLE_KEY` `remove()` calls on every launch. Harmless, but no version guard. **Low.** Cleanup.

### D-06 Managed runtime installs packages nothing imports
The manifest's `culling` capability requires `cv2` and `sklearn`; `aiculler` and `image_triage` import neither (`cv2` only in `birefnet_worker`). `qrcode>=8.0` is declared in `pyproject.toml` and never imported. **Low–Medium** (install size and time). **Confidence:** High (static import scan; dynamic imports in third-party code not considered). **Next action:** Investigation.

### D-07 Duplicate `_parse_tqdm_progress`
Defined in both `ai_workflow.py:394` and `aiculler_workflow.py:2617`. Low. Cleanup.

### D-08 Stale repo clutter
Seven root plan/handoff documents, four `*-sandbox.ps1`, `sandboxes/` (15 files plus a TinyCLIP benchmark results folder), `benchmarks/` (broken, F-06), `scripts/*_prototype.py`, `image_triage/ui/prototype_style.py`. Some are live references (`HANDOFF_WINUI_MIGRATION.md`, the WinUI stash note), so each needs a human keep/drop decision. **Next action:** Cleanup, user-decided.

### D-09 Disk leftovers (not in the repo; user action)
- `…\LocalCache\…\image_triage_ai_cache\stage` — 9.6 GB, 294 files of legacy staging
- `…\image_triage_ai_cache\models` — 5.2 GB (CLI-Culler 3.9 GB, Sandbox 1.2 GB) duplicating the canonical `.image-triage\AI\models` (2.7 GB)
- DinoV3 model directory still present
- 615 polluted `adapter_labels` rows with `source_path LIKE '%pytest-of-%'` (SQL in the previous summary; back up first, app closed)
- **Next action:** Leave to user; the app should not delete these itself.

---

## 6. Architecture and Ownership

### A-01 `MainWindow` is still the application
- **Severity:** High (maintainability) · **Confidence:** Confirmed
- 19,133 lines, 976 methods, 526 attributes, 28 `_init_*` phases (1,275 lines, a verbatim split of the old `__init__`), 39 other classes still living in `window.py` (tasks, dialogs, a controller).
- Controllers hold a back-reference and reach into private state: `records_view_controller` 826 accesses / 202 attributes; `record_ops` 197 / 43; `catalog` 114 / 36; `ui/actions` 97 / 91; `command_palette` 70 / 41; `folder_ops` 47 / 15.
- 98 window attributes are assigned from controller code (246 assignments in `records_view_controller` alone) *and* by `MainWindow`: two writers for the same state, ordering-dependent.
- 137 methods are pure forwarding delegates.
- **Why it matters:** extraction by back-reference gives the *appearance* of modularity while every controller is still coupled to the whole window. It also directly caused F-02/F-03: when a method moved, the window's callers had no compile-time link to the move.
- **Relation:** Partial fix; the plan's "closed in full" is incorrect.
- **Next action:** Improvements for later. The honest target is explicit state objects (selection/view state, catalog state) that controllers own, not another round of moves.

### A-02 Path identity has no single owner
- **Severity:** Medium · **Confidence:** Confirmed
- At least 15 independent key functions with different semantics: `normpath+casefold` (`global_store._path_key`, `scanner._path_key_fast`, `ai_results`/`grid` `_fast_path_key`), `normcase+normpath` (`archive_ops`, `batch_rename`, `image_resize`), `normcase+abspath` (`phash_prefilter`), `resolve+casefold` (`scanner.normalized_path_key`, aiculler diagnostics, `catalog._normalized_path_key`), plus ones in preview, records_view, filtering, `semantic_sort`.
- **Observed consequence:** the live label store holds the same folder spelled `X:/…`, `\\192.168.1.200\…`, `K:/` and `K:\`.
- **Why it matters:** the same photo can have more than one identity across stores; this silently breaks joins and moves (see I-01).
- **Next action:** Investigation, then a single `path_identity` module with a documented contract. Keep `normalize_filesystem_path` semantics unchanged (catalog and annotation keys depend on it, as WI-8.5 noted).

### A-03 AI root derivation duplicated, partly Store-Python-unaware
- `_default_user_cache_root` is defined in `ai_workflow.py:292`, `aiculler_workflow.py:1170` and `ai_runtime_packages.py:1106`, plus `ai_paths.devirtualized_local_appdata`. The first two do not undo Store-Python virtualization; the last two do.
- Two `image_triage_ai_cache` directories exist on disk (the Store `LocalCache` one holds `depth_maps`, `huggingface`, `subject_masks`; the real one holds models).
- **Severity:** Medium · **Confidence:** Confirmed on this machine · **Relation:** Partial fix of N3.
- **Next action:** Fix (delegate to `ai_paths`).

### A-04 Duplicated "has real edits" logic
`edit_render_headless._session_has_real_edits` duplicates `edit_storage.session_has_edits`, with a comment saying they cannot diverge. **Low.** Cleanup, when the editor is replaced.

### A-05 Two "no window" subprocess helpers plus inline flags
`window._headless_background_popen_kwargs`, `ai_health._no_window_kwargs`, and inline `CREATE_NO_WINDOW` elsewhere. **Low.** Cleanup.

---

## 7. Integration and Data-Flow Problems

### I-01 Only one store follows a moved/renamed photo
- **Severity:** Medium · **Confidence:** High
- On move/rename, `decision_store.move_annotation(s)` is called (three sites). The adapter label store (`source_path`, `path_key`) is never re-keyed. After a move, the label rows point at a path that no longer exists, which weakens any future training or evaluation built on those labels, and compounds A-02.
- **Next action:** Investigation, then Fix.

### I-02 Test runs write to the real user profile
- **Severity:** Medium · **Confidence:** Confirmed
- `ai_paths.default_managed_ai_root()` uses `USERPROFILE`; `conftest.py` does not redirect it. `tests/test_ai_health.py` run alone changed the mtime of `C:\Users\tylle\.image-triage\AI` (15:59:24 → 16:13:52), via `ai_health.py:536` (`mkdir(managed_logs_root())`).
- This is how the earlier pollution of the label store happened (a hand-rolled bypass). WI-3.6 fixed the cause; no test asserts the protection.
- **Next action:** Fix (`monkeypatch.setenv('USERPROFILE', …)` in the sandbox plus one guard test that every store's default path is under the sandbox).

### I-03 `edit_render_headless` / editor storage are in flux
Not audited (editor being replaced). Noted so that the librawops migration plan explicitly accounts for `.image_triage_edits/` sidecar compatibility.

### I-04 First-run constructs a modal dialog inside `MainWindow.__init__`
Constructing a `MainWindow` in a fresh profile opens the modal Set Up AI prompt, which blocks any test that lacks dialog guards. It is also why F-01 is easy to reach and hard to test. **Low–Medium.** Improvements for later: show it after the event loop starts.

---

## 8. Concurrency, Caching, Performance, Resource Management

### C-01 Exit does not cancel most running work
- **Severity:** Medium · **Confidence:** Moderate (static; not demonstrated with a stalled job)
- About 50 `QRunnable` types and ~15 `QThreadPool(self)` instances. `waitForDone` appears only in `annotation_queue.flush_blocking(4 s)`, `face_groups`, and `people_dialog`. `closeEvent` (`window.py:9353`) cancels only the semantic-index and face-index tasks. It does **not** cancel `_active_ai_task` (although `cancel()` exists), scan, hydration, enrichment, review-intelligence, resize, convert or archive tasks. `main.py` returns `app.exec()` with no `aboutToQuit` drain. `QThreadPool`'s destructor waits for running tasks.
- **Failure scenario:** closing the window during an AI run or a large archive/convert leaves a process with no visible window until the job finishes, or indefinitely if it hangs.
- **Next action:** Investigation (reproduce with a long-running task), then Fix.

### C-02 Subprocesses launched without stdin, in a windowless frozen app
- **Severity:** Medium · **Confidence:** High
- `subprocess` calls without a `stdin` argument at `ai_health.py:628`, `ai_runtime_packages.py:620/1022`, `aiculler_workflow.py:794`, `window.py:1900`. The frozen build uses `base="gui"` (`setup_msi.py:91`), so there is no console and handle inheritance fails.
- **Evidence:** the two failing tests (`test_aiculler_cli_reports` ×2) fail deterministically with `WinError 6` when run under `pythonw`; this is the same failure mode, in a test. They are not "flaky". Whether the shipped exe hits it is **Unable to verify** without running the MSI build, but the mechanism is the same.
- **Next action:** Investigation on the built exe; then add `stdin=subprocess.DEVNULL` everywhere (cheap and safe).

### C-03 Review-intelligence fingerprint eagerly SHA-1s every file
- **Severity:** Medium · **Confidence:** High (code), Moderate (timing; estimated, not measured on the full set)
- `review_intelligence._build_fingerprint` hashes every file including RAW, although `_find_exact_duplicate_groups` buckets by size first and has a lazy fallback. A folder like the 1,403-NEF "Canada" set (avg 61 MB, ≈86 GB) at ≈188 MB/s is ≈7.6 minutes on first build, which is automatic for folders with ≤2,400 records. It is cached afterwards.
- **Next action:** Fix (hash only within size-collision buckets; this reuses the lazy fallback that exists).

### C-04 Mask/depth caches have no eviction
92 MB now; unbounded in principle. **Low.** Improvements for later.

### C-05 Health notes
Thumbnail cache has a cap; annotation queue has a bounded flush; the updater verifies its hash. See §11.

---

## 9. Consistency and Maintainability

- **Broad exception handling:** 218 broad `except` clauses package-wide (`window.py` 47, `photo_editor_panel` 30, `ai_probe` 12, `imaging` 11). Classified: 37 log, 20 re-raise, 16 silent, 145 fall back with no log. Several of the regressions in §3 would have surfaced earlier if the 145 had logged; the crash sites are narrow, so this is not their cause. **Low–Medium.** Improvements for later: log-by-default helper.
- **Long functions:** 99 functions over 100 lines remain (A2).
- **Dual implementations of one concept:** progress parsing, no-window kwargs, user-cache root, path keys, session-has-edits (§6).
- **Docs vs reality:** the remediation plan's own checklist is wrong in at least A1 and A3, which means the document cannot be used as a source of truth without re-checking. Recommend a correction note rather than silent edits.
- **Stub-window tests:** 11 xfails: 5 legacy `ai_results_phase1` tests whose policy contradicts the current one, 1 `ai_training`, 5 stub-window tests. The five legacy ones are tests of retired behaviour and should be deleted or rewritten; they currently hide nothing but also assert nothing.

---

## 10. Missed Findings (not in the original audit, present before remediation)

| ID | Finding | Severity | Confidence |
|---|---|---|---|
| M-01 | Managed runtime installs `scikit-learn`/`opencv` that nothing imports (D-06) | Low–Med | High |
| M-02 | Dead pHash settings (F-07) | Low | Confirmed |
| M-03 | Eager SHA-1 of every RAW (C-03) | Medium | High |
| M-04 | Child sync state file written with no consumer (D-01) | Low | Confirmed |
| M-05 | Exit does not cancel running work (C-01) | Medium | Moderate |
| M-06 | Stdin-less subprocess under a windowless exe (C-02) | Medium | High |
| M-07 | Label store not re-keyed on move (I-01) | Medium | High |

---

## 11. False Alarms / Areas Verified Healthy

Checked and found sound. Do not spend time here.

- **Window/controller reference integrity**, apart from F-02/F-03: every other `window.<x>` reference from controllers and `ui/` resolves; no controller-member typos.
- **Duplicate method definitions:** none in any class.
- **Imports:** all internal imports resolve (except `benchmarks/`). No undeclared third-party imports other than the unused ones in D-06.
- **Class liveness:** every class has a user except the two named in D-02.
- **Packaging paths:** MSI/build scripts reference files that exist.
- **Worker scripts** in `ai_workers/` are standalone (no accidental package imports) and follow the JSON-lines protocol.
- **Thumbnail cache** has a size cap.
- **Updater** verifies a hash before launch.
- **Annotation queue** design (single writer, bounded flush) is sound.
- **Share-unreachable handling** (WI-8.5) works as designed under simulation.
- **Test isolation of windows:** `dispose_window` now actually destroys windows (this fixed a real 5–8× slowdown).
- **The "flaky" CLI-report tests** are not a product flake (C-02).
- **`CODEBASE_REVIEW.md`'s remaining mentions** of dead code are descriptive only.

---

## 12. Final Remediation Verdict

### Must Fix Before Moving On
1. **F-01** `semantic_missing` NameError in Set Up AI, and decide what the ignored parameters mean (R-01).
2. **F-02** `_current_path_for_index` in the hidden-folders toggle and settings accept.
3. **F-03** `_start_catalog_refresh` in Add To Library.
4. **F-04** `_auto_bracket_enabled` in the preview overflow menu.
5. **R-02** Add an undefined-name (pyflakes/ruff F821/F822/F401-level) CI gate, plus a test that constructs and exercises each of the above. Without this, items 1–4 will come back.
6. **I-02** Redirect `USERPROFILE` / the managed AI root in the test sandbox and add a guard test.

### Cleanup Still Worth Doing
- F-05, F-06, F-07; D-01 (child-process subsystem), D-02 (except `photo_terminal` and the updater helper, which need a decision), D-04 (AI-mode scaffolding, after the product decision), D-05, D-07, D-08 (stale root docs, with the user choosing which are live).
- A-03 (one cache-root derivation), A-05.
- C-02: `stdin=DEVNULL` on all production subprocess calls, then confirm on the built exe.
- C-03: lazy hashing in `_build_fingerprint`.
- Correct the remediation plan's A1 and A3 claims in place (dated note).
- User-run: D-09 disk leftovers and the 615 label rows.

### Improvements for Later
- A-01: real state ownership for `MainWindow` instead of more back-reference moves.
- A-02: one path-identity module; I-01 re-keying.
- C-01: cancel-and-drain on exit.
- C-04 cache eviction; the 145 silent broad-excepts; the 99 long functions; show the AI prompt after the event loop starts (I-04); D-06 runtime package trim.
- Best-frame options 2 and 3 and the upscaling backend remain deferred by decision, not defect.

### Verified Healthy
See §11.

### Direct answers
- **Was the remediation broadly successful?** It was successful at removing volume and fixing the targeted data-safety and share-freeze problems. It was not successful at the goals it declared closed (A1, A3) and it introduced five unguarded broken references.
- **Is the codebase healthier?** Yes, but less than the checklist says, and it is more fragile to further deletion than before because nothing mechanically checks for orphaned references.
- **Another phase needed?** Yes: a small one, scoped to the "Must Fix" list above. It should take a day or two, not a week.
- **Ready for feature development?** Not until the Must-Fix list is done. After that, yes for features that touch well-tested subsystems; features that must land in `MainWindow` will keep paying the god-object tax.

---

## Status updates

### 2026-10-03: first fixes (uncommitted when written)

- **F-01 to F-04 fixed** in `image_triage/window.py` and `image_triage/preview.py`, each with a regression test in `tests/test_post_audit_regressions.py`. The four tests fail on the pre-fix source and pass on the fix (checked by restoring the old files and re-running).
  - F-01: `download_semantic_model=False`. This is exactly what the deleted `semantic_missing` evaluated to, since the semantic-sidecar flag it depended on was a hard-coded `False` stub. The dialog's ten ignored parameters (R-01) are **not** addressed; that is a design decision.
  - F-02: both call sites now use `_current_visible_record_path()`, the same lookup the old helper performed.
  - F-03: the folder menu now calls `self._catalog.start_catalog_refresh(...)`.
  - F-04: the overflow menu reads `self.auto_bracket_button.isChecked()`, which `set_auto_bracket_mode` keeps in sync with the window's setting.
  - Also added the missing `from collections.abc import Callable` that pyflakes flagged for two string annotations in `window.py`.
- **Full suite after the fixes** (final tree, I-02 redirect reverted): 1,949 passed, 1 skipped, 11 xfailed, 3 failed, in 723 s. Two failures are the `test_aiculler_cli_reports` tests (`WinError 6` under `pythonw`, see C-02). The third, `test_char_batch_rename_controller::test_apply_preview_renames_files_rekeys_records_and_pushes_undo`, passes when run alone (3 passed); the remediation plan had already recorded it as order-dependent. An earlier run on the same fixes but with the I-02 redirect in place had a different single failure (the mask-pane width test described below), which is what led to reverting the redirect.
- **I-02 (test sandbox leaks into the real `~\.image-triage\AI`) is still open.** The redirect is straightforward (patch `ai_paths.default_managed_ai_root` in `tests/conftest.py` while `USERPROFILE` is still the real one; redirecting `USERPROFILE` itself is not an option, see below), and I wrote and verified it, but it exposes a defect (N-01), so it was reverted rather than leaving the suite red. The patch is kept in the session scratchpad as `post_audit/conftest_ai_root_redirect.patch`.

### New findings from that attempt

#### N-01 The mask pane is wider than the editor column when AI tools are not installed
- **Category:** Confirmed defect · **Severity:** Low–Medium (cosmetic, but it is the first-run state) · **Confidence:** Confirmed
- **Location:** `image_triage/ui/photo_editor_panel.py`, the Mask "Create" pane; test `tests/test_mask_panes.py::MaskPaneFootprintTests::test_no_pane_is_wider_than_the_editor_column`
- **Evidence:** with an empty managed AI root the Create pane's minimum width is 352 px; the column is 344 px (the test's own limit). With the AI tools installed it is 322 px. The extra width comes from the "Download AI Masking Tools" button state (that button alone has a 332 px minimum hint, 20 px more with margins), which only exists when the tools are missing. Measured by listing every child widget with a minimum hint over 250 px under both conditions.
- **Why it matters:** a new user, or any machine without the tools, sees the pane clipped with horizontal scrolling disabled. That is the exact failure the test was written to prevent, and CI runs on a clean machine, so I would expect the same failure there (not verified; I have no CI results).
- **Relation to remediation:** Previous task unknown (pre-existing, masked by the developer machine's installed tools).
- **Next action:** Investigation. Editor UI is being replaced by librawops, so the right answer may be to leave it and mark the test's precondition explicit, or a one-line label change; that is your call.

#### N-02 A test passes only because of the developer's real AI install
- **Category:** Inconsistency (test hermeticity) · **Severity:** Medium · **Confidence:** Confirmed
- **Location:** the same test; more generally any test that builds editor UI against the real managed root.
- **Evidence:** identical test, identical code; result flips when only `default_managed_ai_root()` points at an empty folder.
- **Why it matters:** this is the T7 gap seen from the other side. The suite reads real installed state as well as writing it, so green on this machine does not mean green on a clean one. It also means the audit's "CI results unverifiable" caveat deserves more weight.
- **Next action:** Fix after N-01 is decided, by sandboxing the managed root and making the test state its own precondition.

*Also learned:* redirecting `USERPROFILE` itself in the shared sandbox is not a safe way to do I-02. It also changed the result of that test in my first attempt, so `ai_paths` has to be patched directly.

### 2026-10-03: later changes against this audit

- **F-05** fixed (`download_ai_model` now requires its `installation`), and a further undefined name in `aiculler/cli.py` (an unimported type in an annotation) found by the new gate and fixed. **F-06** fixed (the benchmark of the deleted live-output runner was dropped; the parser benchmark stays).
- **R-02** addressed: `scripts/check_undefined_names.py` and `scripts/architecture_report.py --check` run in CI, `tests/test_architecture_rules.py` checks that every `self.<name>` in `MainWindow` and the controllers and every internal import resolves, and `tests/test_action_sweep.py` triggers every enabled action of a real window and fails on any exception, including those PySide would otherwise swallow inside a slot.
- **D-01** done (child-process subsystem deleted; no reader of its state file exists anywhere in the repository). **D-04** partly done (the AI-mode switch is gone; the hidden AI toolbar page and per-mode toolbar layouts remain). **A-01** is being worked under `docs/mainwindow_decomposition_plan.md`.
- A shared test-harness bug was found by the sweep and fixed: `install_dialog_guards` returned a tuple from `QFileDialog.getExistingDirectory`.
- **C-02 note:** the two `test_aiculler_cli_reports` tests failed in some full runs and passed in others on unchanged code, so the `WinError 6` explanation holds for some runs but they are not deterministic under `pythonw`. Still unresolved. Two other tests (`test_char_batch_rename_controller`, `test_char_move_delete_undo` apply-AI-decisions) fail intermittently in long full runs and pass alone; the latter was seen failing once, in a run that took 24 minutes instead of 14, so I attribute it to load, which I have not proven.
