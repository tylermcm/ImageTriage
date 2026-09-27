# Image Triage: Audit and Remediation Plan

**Status:** Source of truth for the stabilisation effort. Written 2026-09-26. Planning only. No code was changed while producing this document.

**How to read this file**

- **Part 1** is the full audit as delivered.
- **Part 2** is the remediation plan built from it.
- Part 2, section 0 ("Corrections and new facts") **overrides Part 1 wherever they disagree**. Corrections found while planning are listed there and marked in the checklist.
- Finding IDs (A1, U3, I2, E1, S1, P1, T1, N1 ...) are used consistently across both parts. The master checklist in Part 2 section 8 accounts for every one.
- File references are relative to the repository root and written as `path:line` (line numbers are as of 2026-09-26 and will drift).
- Standing project rules that apply throughout: the user handles commits; deleting files, registry values, caches and build output is handed to the user as commands; only verified-unreferenced code in tracked source may be deleted.

---

# PART 1: AUDIT REPORT

## 0. How this audit was done and how far to trust it

- **Static analysis.** AST scripts over all 181 package files. They looked for: unreferenced definitions, unused imports, settings keys that are never read or never written, signals that are never connected, actions with no handler, and modules only tests import.
- **Hand verification.** Each candidate was traced by hand before being called dead. Name-based analysis can miss same-named methods, so the dead-code lists are floors, not ceilings.
- **Logs.** Read the AppData perf and execution logs.
- **Tests.** Ran the suite read-only. Result: **1,399 passed, 31 failed, 5 xfailed**, excluding 3 files (2 broken at import, 1 that hangs; see Appendix A).
- **Not done.** The GUI was not used interactively. UX findings come from code, layouts and screenshots shared during the session.
- **Working tree.** The audit is of the current tree, which includes uncommitted changes from the same session. Where a finding is that session's own work, it says so.

## 1. Executive summary

Image Triage works and is unusually capable. It has a virtualised grid, a non-destructive RAW editor with AI masks, semantic and people search, an AI culling pipeline, an offline AI runtime with health checks, phone transfer (PocketDrop), and 1,399 passing tests.

The product is now ahead of the codebase's ability to stay coherent. Five themes account for most of what is wrong:

1. **`MainWindow` is the whole application.** It is 25,900 lines, 1,138 methods and 558 state attributes in one class.
2. **Three generations of AI code coexist.** Only the newest is live, but the older two are still packaged, tested and partly wired.
3. **AI results are computed but barely visible.** Retiring "AI Review" mode left culling output with almost nowhere to show up in the grid, while "Apply AI Decisions" moves files in bulk.
4. **Settings and controls no longer match behaviour.** A whole DINO settings page is built and never added; 3 other AI controls are built and never shown; there are two shortcut editors with different storage; the popout has permanently disabled Undo/Redo buttons; there is one real shortcut collision.
5. **Editor output does not reach the rest of the app.** Edits live in sidecars; grid thumbnails, the "Edited" filter and badge, resize, convert, handoff and PocketDrop ignore them.

### Top findings

| # | Priority | Type | Finding |
|---|---|---|---|
| 1 | Critical | Architecture | `MainWindow` god object (2.1) |
| 2 | High | Bug | `Ctrl+Alt+P` bound to both "Next AI Top Pick" and "Send to PocketDrop" |
| 3 | High | Redundant / bug risk | Two live shortcut editors, two storage schemes, a wrong "default" (2.4) |
| 4 | High | Incomplete / UX | AI results effectively invisible in manual review; "Apply AI Decisions" batch-moves files with only a count as preview (4) |
| 5 | High | Performance / bug | Marking a winner does `shutil.copy2` on the UI thread by default; "Apply AI Decisions" does a full view rebuild per file (8) |
| 6 | High | Dead / obsolete | About 20k lines of legacy pipeline (`AICullingPipeline/`), still required by CI and the MSI (2.2) |
| 7 | High | UX | Editor has no undo; the popout shows disabled Undo/Redo buttons ("not available yet") (5) |
| 8 | High | UX / integration | In-app edits are invisible outside the popout (5) |
| 9 | Medium | Data | Un-marking a winner deletes `_winners/<name>` blindly (8) |
| 10 | Medium | Reliability | Test suite: 31 failures, 3 files excluded, one test hangs, stub-based tests break on any refactor (Appendix A) |
| 11 | Medium | Settings | Four AI settings exist with no UI; the code itself calls one "only read by a dead helper" (6) |
| 12 | Medium | Data placement | Organisation name "Codex"; derived AI data placement (2.6) |

## 2. Architecture and codebase

### 2.1 Size and shape (A1-A3)

- The package is 181 files and 127k lines, with 150 test files and 32.7k test lines. There are 120 commits since 2026-03-29.
- `image_triage/window.py` is 28,467 lines (about 22% of the package). `MainWindow` is 25,900 lines and 1,138 methods; its `__init__` alone is 1,301 lines; it owns 558 distinct instance attributes. It contains folder state, annotations, file operations, 18 thread pools, AI orchestration, catalog, toolbars, palette, settings application and dialogs.
- Other large classes: `PhotoEditorPanel` 7,379 lines / 317 methods; `FullScreenPreview` 4,867 / 194; `ThumbnailGridView` 4,775 / 250.
- 126 of 5,318 functions exceed 100 lines. `build_app_stylesheet` in `image_triage/ui/theme.py:600` is one 2,677-line f-string.
- `CODEBASE_REVIEW.md` in the repo root describes a window of "roughly 8.5k lines". Several of its findings have since been fixed (async annotation persistence, a `JobController`, `__pycache__` ignored), so it is now misleading.

**[Architecture, Critical]** Splitting the window is the highest-leverage change but risky without hermetic tests: most `MainWindow` tests use hand-rolled stubs that fail whenever the class changes.

### 2.2 Three AI generations coexist (A4-A7)

| Layer | Where | Status |
|---|---|---|
| **Active** | `aiculler/` (11k lines, packaged) driven by `image_triage/aiculler_workflow.py`. Stages: ingest with CLIP and TOPIQ, categories, clusters, rank. | Live |
| **Older host wrapper** | `image_triage/ai_workflow.py` and `image_triage/ai_training.py` (5.4k lines together) | Mostly dead. 69 of `ai_training.py`'s 111 top-level definitions (1,317 of 3,273 lines) and 44 of `ai_workflow.py`'s 73 (601 of 1,816 lines) are referenced nowhere outside their own module. |
| **Legacy engine** | `AICullingPipeline/` (90 files, 21.8k lines; `legacy/` is 338 lines) | Its only live caller is the DINO prefilter, which is disabled. |

Evidence:
- `image_triage/window.py:3638-3646` carries the comments "Stub … dead training-prep helper … will be removed alongside the dead pipeline methods".
- `image_triage/window.py:10511` says "DINO was removed from the active culling workflow" and hard-codes `enabled=False`.
- `image_triage/window.py:22794` passes `run_dino_prefilter=False`.
- The Settings dialog forces `enabled=False` on the way out (`image_triage/settings_dialog.py:1568` at the time of the audit).
- `FilterMode` still has `DINO_REMOVED` and `DINO_RESCUED` (`image_triage/models.py`).
- The docs say the pipeline stays "for packaging support". The MSI workflow still fails the build if `AICullingPipeline/app`, `configs` and `scripts` are missing (`.github/workflows/build-windows-msi-release.yml:53`); `freeze_support.py:14` bundles it. The README still tells users the app downloads a "DinoV3" model.

**[Dead or obsolete, High]** The project ships and CI-gates a 22k-line engine for a feature that cannot be switched on.

**[Dead, Medium]** Never instantiated: `TrainRankerDialog`, `PrepareTrainingSourcesDialog`, `EvaluationSourceDialog`, `AITrainingProgressDialog`. `AITrainingStatsDialog` is reachable only through a dead method. About 1,100 lines together.

### 2.3 The editor engine is imported through a `sys.path` hack (A8, A9)

`image_triage/ui/photo_editor_panel.py:111` and `image_triage/edit_storage.py:25` insert `cli_editor/` into `sys.path` at import time and import `photo_terminal.*` for recipes, sessions and masks. `editor_render.py` does it lazily. The MSI copies it to `lib/photo_terminal`. The package is not in `pyproject.toml`'s package list.

Consequences: a hidden dependency (a plain `pip install` gets a broken editor); the app is coupled to a second, independently versioned project; behaviour is duplicated (`mask_overlay.py` reimplements "photo_terminal's linear falloff" for the on-canvas overlay, which is the divergence that produced the misplaced-mask bug fixed during the same session).

**[Architecture, Medium]**

### 2.4 Overlapping systems that should be one (A10-A16)

- **Two shortcut editors, two storage schemes (A10, High).**
  - Settings > Shortcuts uses `image_triage/ui/shortcuts.py`. It reads `QSettings("ImageTriage","ImageTriage")` with per-action keys, 23 rows.
  - The window's own Keyboard Shortcuts dialog (`image_triage/window.py:15925`) uses `ShortcutBinding` and `keyboard_mapping.py`, and writes a JSON blob to the app's own `QSettings` ("Codex").
  - At startup (`window.py:4015` then `4031`) both are applied to the same actions.
  - The window editor's "default" shortcut is captured after the Settings overrides are applied, so its Reset goes to your customised key, not the true default.
  - The two editors use different IDs (`open_folder` vs `file.open_folder`), and the Settings conflict checker only covers its own 23 rows.
- **Six progress UIs (A11, Medium).** `JobController` (about 9 uses), `AIReviewProgressDialog`, `BusyOverlay`, 2 raw `QProgressDialog`s, an orphaned `AITrainingProgressDialog`, and the Move-to dialog added during the session (`image_triage/transfer_progress.py`).
- **"Catalog" means two things (A12, Medium).** `CatalogRepository` is the folder-record and review-feature cache (`image_triage/catalog/repository.py`). `LibraryStore` is the "global catalog" of roots and collections (`image_triage/library_store.py`). Menus and Settings both say "Catalog".
- **Duplicates are computed four separate ways (A13, High).**
  1. `review_intelligence.py` runs on every folder open; it feeds the "Near Duplicates" filter and grid badges.
  2. The pHash prefilter runs only inside the AI run.
  3. `bursts.py` powers Smart Groups and Stacks.
  4. The `aiculler` clusters.
  A fifth, DINO clustering, is dead. `perceptual_hash.find_perceptual_duplicate_groups` is referenced only by tests.
- **"Workflow" means three things (A14, Low).** Settings presets (`WorkflowPreset`), export recipes (`WorkflowRecipe`), and the AI workflow (`AIWorkflowCenter`).
- **Toolbars (A15, Low).** Per-mode layouts, the top-bar action stack, the pinned-tools rail, an in-place edit mode, and the old `ToolbarCustomizerDialog`, which is reachable only through a Settings-dialog callback that the dialog never uses.
- **Provider registry (A16, Low).** `plugins/` has 4 provider registries whose only registrants are the modules' own defaults.

### 2.5 The UI as state holder (A17, A18)

- The popout keeps a hidden legacy "analysis panel" alive "to carry the old inspection/focus/FITS state" (comment in `preview.py`).
- `mode_tabs` is hidden but still the state holder that `_ui_mode` reads.
- 39 `MainWindow` attributes are assigned and never read anywhere (for example `_adapter_review_*`, `_ai_workflow_center_dialog`, `_face_index_*`, `_toolbar_style`): leftovers from removed features.

**[Architecture, Medium]**

### 2.6 Persistence layers and data placement (A19-A23)

About 10 stores overlap in purpose: `QSettings` (registry, 100+ keys including JSON blobs), `decisions.sqlite3` (per session), XMP sidecars, `catalog.sqlite3`, `library.sqlite3`, per-folder `.image_triage_ai/.../aiculler.sqlite`, a global adapter DB plus workspace, a JPEG thumbnail cache, `.image_triage_edits`, and semantic and face indexes.

- **Organisation name (A20).** `image_triage/main.py:55` sets it to "Codex". Registry settings live under `HKCU\Software\Codex\Image Triage`; the thumbnail cache lives under `AppData\Local\Codex\Image Triage\cache`.
- **Roaming (A21).** `image_triage/aiculler_global_store.py:251` puts everything under Roaming. **Corrected by planning (see Part 2, N3):** the 793 MB figure measured at audit time was the stale copy seen by a non-Store interpreter; the live copy is 125 MB.
- **Two annotation sources of truth (A22).** `decision_store` (keyed by session) and XMP sidecars. On load a non-empty session record overrides the sidecar (`window.py:1897-1922`); an empty one does not, so a sidecar can resurrect a mark cleared in another session.
- **Thumbnail cache (A23).** `image_triage/cache.py:71` keys the disk cache by absolute path plus mtime and size, with no size cap or eviction. Moving files invalidates their thumbnails. **Measured later: the live cache is 3.2 GB in 72,860 files.**

### 2.7 Repo and build hygiene (A24-A30)

- **Stray tracked root files (A24).** `xbutton.png`, `verified.png`, `heartbutton.png`, `minus_sign.png`, two `sidebar_icons*.zip` files, empty `ssh_out.txt` and `ssh_err.txt`, and a `package-lock.json` with no `package.json`. `verified.png` and `minus_sign.png` also exist in `ui/assets`.
- **Untracked junk (A25).** A 78 MB `ES50_EScan2_67810_AM.exe` in the repo root; installed `onnx` dependencies (about 51k lines) under `sandboxes/.../export_deps`.
- **OneDrive (A26).** The repository lives inside OneDrive, which is risky for a `.git` folder and large build directories.
- **Binary in source (A27).** `image_triage/pocketdrop/pocketdrop.dll` (680 KB) is committed; it is Windows-only although the native source has Linux and macOS ports.
- **Assets (A28).** 5 splash images (`splash_background`, `-v2`, `-v3`, `-v4`, `-v6`; about 15.7 MB) are referenced by no code but shipped through package-data globs. (Note: packaging names `splash_background-v4.png` explicitly, so that one must stay.) The nav icons `nav_duplicates` and `nav_groups` have no corresponding pages.
- **Package list (A29).** `pyproject.toml` lists `aiculler*` but not `AICullingPipeline` or `cli_editor`.
- **Parked work (A30).** Two git stashes exist. One holds the WinUI migration (`C#UIMigration`).

## 3. UI and UX (U1-U9)

- **[UX, High] Almost the whole command surface is hidden (U1).** The 10-menu bar is hidden by default (`window.py:4306`) with a Menu button standing in. 102 actions exist, about a third AI-related, and roughly 15 appear in no menu at all. AI training and adapter actions are reachable only through the AI Workflow Center dialog or the command palette. Discoverability depends on Ctrl+K.
- **[UX, High] Review keys (U2).** The heart of the app (Space, Delete, 1-5 ratings, X, Z, arrows) is hard-coded in `grid.py` and `preview.py` key handlers. Only 23 non-review commands are rebindable via Settings. *(Corrected in Part 2: W and X are rebindable through the window's own keyboard dialog; Space, Delete, 1-5, arrows and similar are hard-coded.)*
- **[Bug, High] Shortcut collision (U3).** `image_triage/ui/actions.py:449` and `:527` both bind `Ctrl+Alt+P` (`next_ai_pick` and `share_to_phone`). Qt treats ambiguous shortcuts as inactive, so neither fires. The Settings checker cannot see it because `share_to_phone` is not in its registry.
- **[UX, High] "Edited" means two different things (U4).** `has_edits` (`image_triage/models.py:109`) is true only when an edited *file* exists (Photoshop or exported copy). Work done in the built-in editor lives in `.image_triage_edits` sidecars, so it never shows the grid "Edited" badge or matches the Edited filter.
- **[UX, Medium] Duplicates page implies more than it does (U5).** Settings > Duplicates configures only the pHash step of the AI run. It does not affect the "Near Duplicates" filter, Smart Groups or Stacks.
- **[UX, Medium] Naming drift (U6).** "Winner", "Keeper" and "AI Pick" overlap; `keep_selection` says "Move Selection To _keep" while "Mark Winner" copies to `_winners`; `run_ai_culling` is labelled "Open AI Workflow Center"; `download_ai_model` and `install_ai_runtime` are both "Set Up AI..."; the readiness action is labelled "Check AI Readiness (Demo Ready)..."; "Processing workers" stores under `ai/embed_batch_size`; Session vs Workflow preset vs Recipe; internal terms leak ("prototype", "mockup", "generated_prototype").
- **[UX, Medium] Scattered settings (U7).** Theme (Appearance) is in the View menu, not Settings. Performance logging is in Tools > Diagnostics. Auto-advance, Smart Groups and Smart Stacks are both menu toggles and Settings switches. Focus assist, the FITS stretch, overlay colour and filmstrip state are inline.
- **[UX, Medium] Dev tool in the product (U8).** "Open UI Prototype" is a menu item and a rebindable shortcut target (`window.py:9119`); the 1,543-line `generated_prototype.py` behind it is a standalone mock window.
- **[UX, Low] Stale doc/UI text (U9).** README and `PRODUCT_ROADMAP.md` list features as "missing" that exist (command palette, docks) and describe DINO. The in-app AI Review Tag Legend explains tags that no longer show.
- **Good.** In-app documentation is thorough and mostly current; the rail/sidebar rework is clean; confirmation and undo exist for moves and deletes.

## 4. AI and culling pipeline (I1-I6)

- **[Incomplete / UX, High] The pipeline runs, but its output has nowhere to live (I1).** `_ui_mode` is always `"manual"`, so `window.py:14082` always calls `grid.set_show_ai_annotations(False)`. AI badges, confidence buckets and the dispute badge never paint. `docs/ai_mode_retirement.md` lists 10 disconnected items (results do not auto-load; the AI toolbar page is never shown; "Dispute Current AI Decision" is permanently disabled; the three `dispute_*` grid signals have connections but no emitters). Result: a multi-minute AI run whose findings appear only in the inspector dock, the popout confidence bar, filters and sorts.
- **[UX / safety, High] "Apply AI Decisions" (I2).** `window.py:23031` shows a count-only confirmation, then moves every "AI Pick" to `_winners` and every "Reject" to the recycle folder. The user has never seen the tags. It bypasses the transfer dialog and pays per-record costs (section 8).
- **[Underutilised, Medium] Rich signals are hidden (I3).** TOPIQ aesthetic scores, face and eye quality, semantic categories, technical tags and cluster membership reach the user only as a filter or sort, not as a "why" on a card or tooltip.
- **[Incomplete, Medium] AI v4 is half-built (I4).** The plan in `docs/ai_v4_plan.md` has code in `quality/`. Its `upsert_dimensions`, `fetch_dimensions`, `aesthetic_score`, `dimension_label_correlations` and `learner.*` functions are referenced only by tests. Only the face and winner parts are wired.
- **[Redundant, Medium] Too many AI entry points (I5).** About 35 AI-related actions and 3 overlapping dialogs (Workflow Center, Guided AI Cull, Taste Calibration), plus per-folder and global label sets.
- **[Dead, Medium] DINO (I6).** The prefilter stage, settings, filter modes, `DINOPrefilterRunTask`, `_run_dino_prefilter` (dead in `window.py`, tests only) and the artifact-delete helpers remain with no way to enable them.
- **Good.** Runtime installer, manifests, transactional model bundles and the health service are solid engineering. Metrics and perf logging are consistent.

## 5. Editor and image tools (E1-E6)

Broad feature set: Adjust, Crop, Remove, Red Eye, Masks (shape, brush, subject, scene, click), Backdrop, Lens Blur, Presets.

- **[UX, High] No undo, but the UI shows it (E1).** `image_triage/preview.py:1974-1982` creates Undo and Redo buttons that are permanently disabled (tooltip "Editor undo is not available yet"). The editor panel has no history at all.
- **[Integration, High] Edits are siloed (E2).** `EditRecipe` and sidecars are read only by `preview.py`, `photo_editor_panel.py` and `editor_copy.py`. Grid thumbnails ignore edits. `image_resize`, `image_convert`, `workflows/export`, the handoff builder and PocketDrop all read the original file. "Send To Editor" is an export recipe that copies originals to a folder for an external editor; it has nothing to do with the built-in editor.
- **[Redundant, Medium] Two mask geometries (E3).** The overlay draws masks with its own rasteriser and the renderer uses `mask_strength_qimage`. They have drifted before. The overlay still skips edge refinement under crop or rotation.
- **[Performance, Low] CPU-only renderer (E4).** Described as "swappable (CPU today, GPU later)" but only `CpuEditorRenderBackend` exists. Preview is capped at 1,600 px. Slider ticks measured 10-40 ms.
- **[Bug, Medium] Header crash in the popout (E5).** `preview.py:5453` reads `metadata.exposure` when `_current_metadata[slot]` can be `None`. *(Reclassified in Part 2: production paths guard it; the failing test used an invalid fixture.)*
- **[UX, Low] Click-select add/subtract (E6).** The tool has no add/subtract clicks in the UI although the engine supports labelled points.
- **Good.** Sidecar storage in one hidden root is tidy; the unified inference host shares one CUDA context; live preview coalescing is well designed.

## 6. Settings and configuration (S1-S6)

Current Settings dialog, traced to consumers:

| Setting | Consumer | Verdict |
|---|---|---|
| Session preset | `_session_id` scopes annotations; presets | Works; conflates two ideas |
| Accepted images (Copy/Link/Annotation only) | `_sync_winner_copy_for_paths` | Works; default (copy) is heavy |
| Delete behaviour (Safe Trash/System Trash) | delete paths | Works |
| Check for updates | updater | Works |
| Interface size, Card style, UI gamma | display profile, grid, theme | Works |
| Free smooth scrolling, Preview preload, Show hidden folders | grid/preview/tree | Works |
| Advance after accept/reject | grid and preview | Works |
| Group burst sequences, Stack burst frames | `bursts.py` | Works; duplicated as View menu toggles |
| One expanded branch per level | folder tree | Works |
| Watch folder | `QFileSystemWatcher` | Works locally; unreliable on network shares |
| Reopen where I left off | new during the session | Works (untested in-app) |
| Catalog cache | `CatalogRepository` | Works |
| Processing workers | stored as `ai/embed_batch_size` | Works; label and key disagree |
| Detailed progress log | AI dialog detail | Works |
| Likely winners %, Review band % | `set_cull_thresholds` | Works; visible effect mostly Apply AI Decisions and filters |
| Duplicates (pHash) | AI-run prefilter only | Works; scope narrower than page name |
| Shortcuts | 23 actions | Partial; second editor exists |

Settings with no UI or no effect:

- **[Incomplete, Medium] Four hidden controls (S1).** Built but never added to a page: dispute weight (2-5x), base-score weight, near-duplicate threshold (`window.py:3645` says it is "only read by a dead training-prep helper"), DINO worker count. The full DINO page (`dino_page`) is also built and never added.
- **[Dead, Low] Dead callbacks (S2).** The dialog accepts `file_associations_callback`, `keyboard_shortcuts_callback`, `toolbar_callback` and `reset_layout_callback` and never uses them.
- **[Dead, Low] Dead settings keys (S3).** 11 `TRAIN_RANKER_LAST_*` constants; `APPEARANCE_INDIGO_MIGRATION_KEY`; write-only `DINO_PREFILTER_ENABLED_KEY` and `WORKSPACE_BAR_STATE_KEY`; read-only `COMPACT_CARDS_KEY`; `VIEW_ZOOM_WIDTH_KEY`, which is only ever removed.
- **[Dead, Low] Stubbed state (S4).** `_ai_semantic_sidecar_enabled` is a constant `False` kept for "legacy status-line helpers".
- **[Potential improvement] Missing settings (S5).** Theme, performance logging, default winner-copy location and an "AI tags in grid" switch.

## 7. Unused or questionable code and features (inventory)

| Item | Size | Confidence |
|---|---|---|
| `AICullingPipeline/` legacy engine (CI-required) | 21.8k lines | High (only live caller disabled) |
| `ai_training.py` unreferenced part | 1.3k of 3.3k lines | High |
| `ai_workflow.py` unreferenced part | 0.6k of 1.8k lines | High |
| `MainWindow` methods with no reference (e.g. `_show_legacy_ai_setup_dialog` 164 lines, `_delete_dino_prefilter_artifacts`, `_delete_phash_prefilter_artifacts`, `_set_active_ai_checkpoint`, `_persist_annotation`, `_show_ai_menu`) | 38 methods, about 690 lines | High for named ones; list may be incomplete |
| 44 unused imports in `window.py` | n/a | High |
| Never-instantiated dialogs (train ranker, source pickers, training progress) | about 1.1k lines | High |
| `ToolbarCustomizerDialog` chain | about 400 lines | High |
| `generated_prototype.py` (dev mock in menu) | 1.5k lines | High |
| `quality/` dimension code, tests only | about 400 lines | Medium (planned feature) |
| Unreferenced splash images | 15.7 MB | High |
| Signals never connected or emitted (grid `dispute_*`, 3 on `InspectorPanel`) | n/a | High |
| Popout dead helpers (`_studio_group_label`, `_toggle_mockup_maximized`, ...) | small | High |
| `perceptual_hash.find_perceptual_duplicate_groups`, `mac_media` readers, `edit_storage.consolidate_folder` (tests only) | small | High for "tests only" |
| `plugins/` provider indirection | 0.2k lines | Medium (works, unused flexibility) |

## 8. Performance and reliability (P1-P9)

- **[Performance, High] Winner marking copies files on the UI thread (P1).** With the default "Copy To _winners", every mark runs `shutil.copy2` inline (`window.py:28413-28470`). On the NAS a 50 MB RAW can freeze the UI per keystroke.
- **[Performance, High] "Apply AI Decisions" scales quadratically (P2).** `window.py:23071-23087` loops per record through `_move_record_to_path`. Each call pushes an undo entry, moves one file synchronously, and calls `_remove_record`, which re-runs `_apply_records_view` (a full sort, filter and rebuild). No progress UI. (The transfer dialog covers `_move_records_by_paths` only.)
- **[Data risk, Medium] Un-marking deletes by name (P3).** `_sync_winner_copy_for_paths` removes `_winners/<name>` if it exists, without checking it is the app's own copy.
- **[Reliability, Medium] Updater integrity (P4).** `image_triage/updater.py:138` verifies the installer hash only `if update.sha256`. With no hash in the feed, an unverified MSI is run.
- **[Performance, Medium] Toolbar rebuild (P5).** `toolbar.rebuild_stack` on the UI thread reached 472 ms max (1.4 s over 10 calls) in the logs (`window.py:5987`), triggered from about 12 call sites.
- **[Performance, Medium] Startup and first open (P6).** `MainWindow.__init__` is 1,301 lines. Popout open measured 180 ms.
- **[Reliability, Medium] Blocking calls on shared pools (P7).** The edited-variant folder scan added during the session runs on `FullScreenPreview._pool`, shared with decode tasks (4 threads).
- **[Reliability, Low] `processEvents()` and error handling (P8).** `processEvents` is called 12 times in `window.py` (progress dialogs, 13064-13445), which invites re-entrancy. 250 `except Exception` and 65 `except...: pass` sites; `window.py` has 15 silent ones.
- **[Reliability, Low] Watcher on NAS (P9).** `QFileSystemWatcher` and `QFileSystemModel` do not observe network shares, which is why new folders did not appear until refreshed (fixed with a model rebuild, but a workaround).
- **Not a concern.** Thread pools are capped (mostly at 1); annotation writes are queued; thumbnails are cached in memory and on disk.

## 9. Missing opportunities (systems already close) (O1-O9)

1. Show AI tags and reasons in the grid; the data exists and is hidden ("why" chips from `technical_tags.py`, `tag_penalties.csv`).
2. One "similar frames" system merging `review_intelligence` duplicates, bursts, brackets and `aiculler` clusters, with best-frame selection (Winner Ladder already exists).
3. Edits everywhere: use `EditRecipe` for grid thumbnails and for resize, convert, handoff and PocketDrop (`editor_copy` already renders a full-resolution copy).
4. Editor undo/redo plus batch "copy/paste settings" across a selection.
5. Editor guidance built on the live histogram, clipping and per-photo stats.
6. Add/subtract clicks for point selection.
7. Editor rendering on GPU through the `CpuEditorRenderBackend` seam; `plugins/` as the backend selector.
8. Faster libraries: content-keyed thumbnail cache, disk cache eviction, FTS for the global catalog.
9. Move-to everywhere: drag-drop copy, AI apply, recycle moves and batch operations through the shared transfer dialog.

## 10. Original recommendations (superseded by Part 2 where they differ)

**Fix first:** resolve the `Ctrl+Alt+P` collision and unify the two shortcut editors; move winner copies off the UI thread and make un-marking safe; make "Apply AI Decisions" a single batched, cancellable transfer with a preview; give the editor real undo or remove the disabled buttons; fix the popout header test; sort out the failing tests and make the suite hermetic; confirm updater hash policy.

**Clean up next:** retire DINO, the unreferenced half of `ai_training.py`, `ai_workflow.AIRunTask`, the never-instantiated dialogs and the ranker UI, then decide whether `AICullingPipeline/` can leave the MSI and CI; remove dead `MainWindow` methods, orphan attributes and unused imports; remove the hidden settings controls and dead callbacks and dead keys; remove `generated_prototype.py`; fix stale docs; consider renaming organisation "Codex"; repo hygiene.

**Improve afterward:** split `MainWindow` incrementally; unify duplicates and bursts; consolidate progress dialogs; make edits visible in the grid and honoured by export paths; finish or drop AI v4; bring the Settings dialog to parity with what the app really has.

**Future opportunities:** local AI editing coach, GPU renderer, richer point-select, FTS catalog, cross-platform PocketDrop, WinUI migration decision.

## Appendix A: test baseline

Run: `pytest -v --continue-on-collection-errors` under Python 3.13 with the offscreen Qt platform.

**Result: 31 failed, 1,399 passed, 5 xfailed, 653 subtests passed, 66 s.** Excluded because they cannot run here:
- `tests/test_ai_clean_machine_check.py` (imports `scripts` as a package; it is not one)
- `tests/test_aiculler_topiq_onnx.py` (needs `onnx`)
- `tests/test_labeling_data_quality.py` (**hangs indefinitely**; exercises the legacy `AICullingPipeline` code). Runs that included it also left a real `mask_engine_worker.py` GPU process alive, which was stopped.

**Failures (31)**, as attributed at audit time:
- Caused by changes made earlier in the session (5 at audit time; **6** after planning, see Part 2 N8): `test_editor_crop_retouch::test_pressing_outside_the_box_starts_a_rotation`; `test_editor_render::test_stale_completion_is_dropped_when_newer_is_pending`; 3 in `test_settings_dialog` (two use the removed `section_list`, one asserts a minimum size the offscreen screen clamps); and `test_perf::test_enable_creates_and_flushes_log_file` (added by planning: the stall watchdog holds a file open).
- Pre-existing (26): `test_window_catalog_cache` x7 (stubs lacking newer attributes); `test_grid_failures` x4; `test_topbar_style`; `test_nav_rail` (expects 3 rail destinations, the app has 4 with PocketDrop); `test_decision_harvest` x6, `test_ai_training`, `test_ai_workflow`, `test_ai_results_phase1` (all tied to the legacy AI pipeline); `test_catalog_repository` x2; `test_preview_polling` (invalid fixture, see N7).

Two test files (`test_labeling_data_quality`, `test_decision_harvest`) and several others keep the legacy pipeline "alive", which is a further reason to decide its fate.

---

# PART 2: REMEDIATION PLAN

## 0. Corrections and new facts since the audit

| # | Correction or new fact | Consequence |
|---|---|---|
| N1 | **CI runs almost none of the tests.** The MSI workflow runs 3 modules and the Linux workflow runs 5, including legacy ones (`test_ai_workflow`, `test_dinov2_extractor`). Nothing runs the full suite. | The 31 failures accumulated silently. Legacy retirement must also edit CI. |
| N2 | **Nothing tests the code that mutates user files.** No test references `_sync_winner_copy`, `_apply_ai_culling`, the undo stack (`UndoAction`), `_delete_record`, `unique_destination`, batch rename or convert plans. `file_ops` is touched only by an unrelated mac test. | These are the riskiest paths and have zero safety net. Characterization tests must come first. |
| N3 | **The live app's files are redirected.** It runs on Store Python, so `%APPDATA%` and `%LOCALAPPDATA%` resolve into `...\Packages\PythonSoftware...\LocalCache\`. The audit's "793 MB in Roaming" was the stale copy; the live one is 125 MB and `ai_training` is 1.1 MB. Registry settings are not redirected (the live `last_folder` matches current work). | Any data-location change must be validated in both the Store-Python dev app and the frozen MSI app. |
| N4 | **The live thumbnail cache is 3.2 GB in 72,860 files** (oldest 2026-03-14), with no eviction. It sits under organisation "Codex". Renaming the organisation would move `CacheLocation` and orphan it. | Organisation rename and cache policy are coupled. |
| N5 | **"Session" means two things.** `docs/collections_and_future_projects_decision.md` defines Sessions as automatic per-folder continuity. The code's `session_id` is a named annotation namespace ("Default"). | Vocabulary conflict; needs a decision (D5). |
| N6 | **Earlier decisions are recorded in project memory.** AI tags were hidden in manual review on purpose (2026-07-15). AI Review mode was retired and "do not reintroduce a mode switch" (2026-09-19). Keeper->Winner is a display-only rename. A future opt-in setting for tags was anticipated. | "AI results invisible" is intentional-but-incomplete and needs a product decision, not a bug fix. |
| N7 | **Corrections to the audit.** (a) W and X **are** rebindable through the window's keyboard dialog; only Space, Delete, 1-5, arrows and similar are hard-coded. (b) The popout header "crash" comes from an **invalid test fixture** (`_current_metadata=[None]`); production paths guard it. Reclassified as a test bug. | Two findings shrink. |
| N8 | **The `test_perf` failure is a regression from the session.** The stall watchdog holds `ui_stall_traces.txt` open, so `TemporaryDirectory` cleanup fails on Windows. That makes **6** session-caused failures, not 5. | Fix in Phase 0. |
| N9 | **The session's own changes carry debt.** `_move_records_by_paths` still calls `_remove_record` per file, which rebuilds the view each time. The folder-discovery task shares the preview decode pool. The transfer dialog is a sixth progress UI. | Folded into WI-0.1, 4.1 and 4.2. |
| N10 | **Registry orphans exist.** `view/ai_thumbnail_tag_text`, `focus_folder_tree`, `compact_cards`, `details_preview_pane`, `details_splitter_state`, `ui/pane_width_ratios_reset` and `_v2`. `HKCU\Software\ImageTriage\LabelingApp` belongs to the legacy labeling app. `ai/clip_model_variant=fp16` is saved but ignored. Nothing is saved under `shortcuts/`, and `HKCU\Software\ImageTriage\ImageTriage` is empty. | A settings migration has almost no data to move on this machine, but unknown installs may differ. |
| N11 | **`image_triage/engine/` holds only stale `.pyc`** from the shelved WinUI migration (ignored, untracked). | Harmless cruft. |
| N12 | **The real workflow uses the risky settings.** `winner_mode="Copy To _winners"` with the library on the NAS. Keep-top 14% and review band 12%. | Winner copying must be preserved, not merely "moved to a background thread". |

## 1. Ground rules for the whole effort

1. **One work item per change.** Before starting, each item declares its expected test deltas (added, updated, removed). Any red test outside that list counts as a regression.
2. **Green baseline first.** Zero unexplained failures before structural work. Known failures live in a ledger (strict xfail with a reason).
3. **Characterization before change.** For user-file, annotation and persistence code, write tests that pin *current* behaviour first. Change behaviour only in a separate step.
4. **Delete leaves first.** Dead code is removed from the outside in. After each round, re-run the reachability analysis, because removing dead callers exposes more dead code.
5. **No implicit data moves.** Resolving a path must never move files (an existing invariant in `ai_model_store`). Migrations are explicit, idempotent, copy-not-move, and reversible.
6. **Standing rules apply.** The user commits. Verified-unreferenced code in tracked source may be deleted. Files, registry values, caches and build output are only handed over as commands.
7. **Keep identifiers, change labels.** Renames follow the Keeper->Winner pattern: display text changes, code and stored identifiers do not, unless a migration is planned.

## 2. What the tests can and cannot tell us

**Baseline (excluding 3 files):** 1,399 passed, 31 failed, 5 strict xfail, 653 subtests, 66 s. Excluded: 2 broken at import and 1 that hangs. While testing, a real GPU worker process was spawned by a test. The suite is not hermetic.

### Confidence by subsystem

| Area | Confidence | Why |
|---|---|---|
| Editor math, crop geometry, masks, curves, render service | **High** | Many focused, pure tests. |
| AI runtime install/health/manifest/model store | **High** in code, **unproven** on real machines | The 20-row clean-machine matrix has never been run. |
| Scanner, filtering, catalog repo, review intelligence | Medium-high | Some fail (see below). |
| Grid painting/layout | **Medium, brittle** | Tests assert pixel constants that changed with the card redesign. |
| Settings dialog | Medium, brittle | Tests reach into widget internals (`section_list`). |
| Shortcuts | Low-medium | The registry is tested in isolation; the interplay of the two systems is untested. |
| Packaging | Low-medium | Staging logic tested; no full build in CI; clean-machine untested. |
| **MainWindow orchestration** | **Low** | Only 13 test files touch it, mostly via hand-built stubs (`test_window_catalog_cache`: 71 stub references, 7 failing). |
| **File ops, undo, winner sync, batch tools, decision store** | **None or near-zero** | See N2. |
| Performance | **None** | Only perf logs, no assertions. |

### The 31 failures, classified

| Class | Count | Tests | Action |
|---|---|---|---|
| **Caused by the session** | 6 | `crop...outside_the_box_starts_a_rotation`; `editor_render...stale_completion_is_dropped...`; 3 x `settings_dialog` (`section_list`, min-size clamp); `test_perf` (watchdog handle) | Update tests to the new intended behaviour, except `test_perf`, which needs a code fix. |
| Obsolete expectation | 6 | `ai_results_phase1...tag_definitions`; 4 x `grid_failures` (card redesign, "AI Pick"->"Winner"); `nav_rail` (PocketDrop added) | Rewrite to invariants, not pixel constants. |
| Invalid fixture | 1 | `preview_polling...ready_result...` | Fix the fixture (and cheaply harden the header). |
| Stub drift | 8 | `window_catalog_cache` x7, `topbar_style` | Replace stubs with a real-window harness. |
| Legacy pipeline | 8 | `decision_harvest` x6, `ai_training` x1, `ai_workflow` x1 | Retire with the legacy code. **Investigate first** why `decision_harvest` fails: it reads the live decision store schema. |
| Need investigation | 2 | `catalog_repository...folder_scan_task...` x2 | Determine whether stub drift or a real scan/cache change. |

**Legacy test mass:** about 103 tests in 10 files exercise the legacy engine, and `test_freeze_support` (8 tests) asserts its staging. They must be removed or rewritten in the same item that retires the code.

### How to tell an intentional change from a regression

- Keep a **failure ledger** file listing every currently failing test with a reason and owner item.
- Every work item lists its expected test changes up front.
- Before and after each item, run the full suite and diff the set of failing IDs. The diff must equal the declared list.
- Add a "no real environment" guard: tests may not touch the real registry, AppData, or spawn model workers.

## 3. Systems that look duplicated but are not (preserve these distinctions)

| Looks like one system | Actually |
|---|---|
| Four "duplicate" mechanisms | (a) `review_intelligence` near-duplicates + `bursts`/`brackets`: a **review aid** at folder open. (b) pHash prefilter: an **AI cost reducer** that removes frames before scoring. (c) `aiculler` semantic clusters: **ranking groups** within a category. (d) DINO: dead. Different jobs; they overlap in vocabulary and pHash computation, not purpose. |
| QSettings / decision store / XMP | UI preferences / review data / **interchange** with Lightroom-type tools. All three have a role. The problem is precedence and vocabulary, not existence. |
| Catalog cache vs global catalog | `CatalogRepository` = per-folder performance cache. `LibraryStore` = global roots, collections and search. Different data and lifecycle; the name is the problem. |
| Two mask rasterisers | The overlay is interactive and low-resolution; the renderer is final. Intentional. The need is **parity tests**, not a merge. |
| "Save", "Save copy", external edited files | Sidecar recipe / rendered file / third-party variants. The "Edited" badge conflates them. |
| Progress UIs | `JobController` (modal generic), AI progress dialog (stage-aware), `BusyOverlay` (inline). Only the generic ones should be consolidated. |
| `*_worker.py` files | Engine libraries loaded by the shared mask host and shipped under `ai_workers/`. **Not dead** despite the names. |
| `ai_workflow.py` | Half dead (`AIRunTask`, staging) and half **live** (`AIWorkflowPaths`, hidden-root layout, metrics parsing, device and worker capacity, artifact readiness). It must be *split*, not deleted. Existing per-folder AI data depends on its path layout. |
| `photo_terminal` (`cli_editor/`) | Shared editing engine for the standalone CLI and the app. Keep the separation; fix how it is packaged. |
| `plugins/` registries | Works, unused flexibility. Leave until something needs it. |

## 4. Product decisions needed (not to be made silently)

| ID | Decision | Options (lean) | Blocks |
|---|---|---|---|
| D1 | Where AI results appear now that AI mode is retired | (A) opt-in "AI tags in grid" setting, matching the earlier anticipated setting **(lean)**; (B) inspector and palette only; (C) a dedicated AI review surface (not a mode) | 6.1, 6.2, 6.5 |
| D2 | Fate of the legacy engine and DINO | Delete; archive to a tag/branch then delete **(lean)**; keep dormant | 2.4-2.6 |
| D3 | What counts as "Edited", and whether edits affect thumbnails and exports | Keep external-variant meaning and add a separate "Adjusted" flag **(lean)**; or unify | 5.3 |
| D4 | Winner handling defaults and semantics | Keep Copy as an option; decide whether async with error toast is acceptable; whether to change the *default* for new installs | 4.1c |
| D5 | The word "Session" | Rename the annotation namespace (e.g. "Profile") **(lean)**; or rename the product concept | 3.4, 7.2 |
| D6 | Shortcut scope | Fully rebindable including review keys, or curated. And which `Ctrl+Alt+P` command keeps the key | 1.1, 3.2 |
| D7 | What "Duplicates" means to the user | Names and scope of the Settings page and filters | 4.3, 7.2 |
| D8 | "Catalog" vs "Library" naming | UI-label rename only **(lean)** | 7.2 |
| D9 | AI v4 (dimension scoring): finish, park or drop | Also decides the fate of `quality/` and AI entry points | 6.3, 6.4 |
| D10 | Appetite for data/organisation migration | Keep "Codex" and document; or migrate with cache pinning | 3.1, 3.5, 3.6 |
| D11 | WinUI migration (two git stashes) | Keep parked, or drop; affects how much to invest in Qt structure | 4.4 |
| D12 | Editor undo scope | Global adjustments only, or masks, retouch and crop too | 5.1 |
| D13 | Dev tools in product (UI prototype) | Delete, or behind a developer flag | 2.8 |
| D14 | Updater integrity | Require a hash always, or allow unverified installs | 1.3 |
| D15 | Platform support | Linux and macOS are built in CI but PocketDrop's DLL is Windows-only; is cross-platform still a goal? | 2.2, 4.6 |

## 5. Work items by phase

Notation: **Type** . **Risk** (L/M/H) . **Touches** (FILES, EDITS, SETTINGS, ANNOT, SESS, DB, AI-DATA, PKG, UPDATE, KEYS, EXPORT, NAS, COMPAT).

### Phase 0: Trust the ground (no behaviour change)

**WI-0.1 Land and lock the session's work.** *Test/infra + bug correction . L . SETTINGS, KEYS*
- **Finding:** N8, N9, P7; session-caused tests.
- **Current:** Uncommitted changes: transfer dialog, watchdog, edited-folder discovery, settings redesign, crop/mask/editor-render changes, restore-position, hover, zoom slider and more. 6 tests fail because of them.
- **Cause:** Behaviour was changed on request without updating the tests that encode the old behaviour. The watchdog keeps a file open.
- **Areas:** `tests/test_editor_crop_retouch`, `test_editor_render`, `test_settings_dialog`, `test_perf`; `image_triage/perf.py`; `image_triage/preview.py` pool use.
- **Deps:** none. Everything else depends on it.
- **Approach:** (1) Update the 5 tests to the intended new behaviour. (2) Make the stall watchdog opt-in on the performance-logging toggle, close its file on disable or exit, and never start under tests. (3) Give the edited-file discovery its own single-thread pool. (4) The user commits, in logical chunks.
- **Risk:** Low; mostly tests.
- **Validate:** Full-suite diff equals exactly the 6 declared tests.
- **Done:** The baseline has no session-caused failures and the work is committed.
- **Disposition:** Fix.

**WI-0.2 Test runner hygiene and hermetic environment.** *Test/infra . L*
- **Finding:** T1, T2, T7, T8, N1.
- **Current:** 2 import errors, 1 hanging test, a real GPU worker spawned, tests can touch the real registry, and both `unittest` (CI) and `pytest` (local) are used.
- **Cause:** No shared fixtures, no timeouts, no markers.
- **Approach:** Add `conftest.py`: offscreen Qt application fixture; redirect `QSettings` and app-data to a temp dir; a fixture that fails any test spawning `mask_engine_worker`; `importorskip` for optional deps; a `requires_models` marker; per-test timeout. Quarantine the hanging test (skip with reason; deleted with the legacy code). Make `scripts` importable or fix the import. Choose **pytest** as the single runner.
- **Risk:** Low, but "redirect QSettings" must not hide real registry writes in production code.
- **Validate:** Suite runs to completion with no process left behind; running twice gives the same result.
- **Done:** Deterministic full-suite run in a few minutes.
- **Disposition:** Fix.

**WI-0.3 Failure ledger and triage.** *Test/infra . L* (Depends on 0.1, 0.2)
- **Finding:** Appendix A; classification in Part 2 section 2.
- **Approach:** Encode the 31 failures as strict xfails or repairs per the table. Investigate the two `catalog_repository` failures and the six `decision_harvest` failures **before** deciding whether they are stale tests or real drift.
- **Validate:** 0 unexplained failures; every xfail names the item that will resolve it.
- **Done:** Ledger committed.
- **Disposition:** Fix / investigate first (2 + 6).

**WI-0.4 Real CI gate.** *Test/infra . M . PKG* (Depends on 0.2, 0.3)
- **Finding:** N1.
- **Approach:** Add a headless full-suite job (excluding `requires_models`). Keep packaging jobs but decouple them from legacy test modules.
- **Risk:** CI minutes; Windows vs Linux differences in Qt offscreen.
- **Done:** The full suite runs on every push and fails the build on regressions.
- **Disposition:** Fix.

**WI-0.5 Characterization tests and a real-window harness.** *Test/infra . M . FILES, ANNOT, EDITS, NAS* (Depends on 0.2)
- **Finding:** N2, T5, and the safety net for 1.2, 4.1, 6.2.
- **Current:** No tests for winner sync, move/copy/delete/recycle/restore, undo, batch plans, or decision/XMP merge.
- **Approach:** Pin current behaviour against a temp folder for: winner toggle in COPY/HARDLINK/LOGICAL including unmark and failure rollback; `_move_records_by_paths` and `TransferWorker` (rename vs copy, cancel, rollback, unique names); undo for annotation/move/delete; batch rename/resize/convert plans; `discover_edited_paths`; annotation load precedence (session store vs XMP); folder view-state and restore-position; settings round-trip; both shortcut stores. Build a harness that instantiates the real `MainWindow` offscreen against temp settings and services, and **measure** how slow that is. If prohibitive, use thin seams instead.
- **Risk:** The harness may expose latent bugs; record them in the ledger rather than fixing inline.
- **Validate:** Each characterization test fails when the behaviour it pins is deliberately altered (mutation spot-check).
- **Done:** Every file-mutating path has tests that describe it as it is today.
- **Disposition:** Fix.

**WI-0.6 Baselines and a manual smoke script.** *Test/infra . L*
- **Approach:** Record startup time, folder open (local and NAS), winner toggle latency, slider tick latency, popout open. Write a 15-step manual smoke list: open folder, mark winner/reject, undo, move, popout edit, save, AI setup check. Capture `HKCU\Software\Codex\Image Triage` and `HKCU\Software\ImageTriage` via `reg export`.
- **Done:** Numbers and exports stored outside the repo.
- **Disposition:** Fix.

**WI-0.7 Data safety backups (user-run).** *Migration/data . L . ANNOT, AI-DATA, DB*
- **Approach:** Before any data or settings work: copy `decisions.sqlite3`, `library.sqlite3`, `catalog.sqlite3`, and **both** copies of `global_adapter_labels.sqlite` (the live one is under the Store-Python package path, N3), plus the registry exports.
- **Done:** Dated copies exist.
- **Disposition:** Fix (user action).

### Phase 1: Small defects with tight blast radius

**WI-1.1 `Ctrl+Alt+P` collision.** *Bug correction . L . KEYS* (Needs D6)
- **Finding:** U3.
- **Current:** `next_ai_pick` and `share_to_phone` both bind it; Qt then fires neither.
- **Cause:** Independent shortcuts assigned in one large action table with no checker; the Settings conflict checker only covers its 23 rows.
- **Approach:** Give one command a different default once D6 is decided, then add a test that asserts no two actions share a default. Extend the runtime conflict check to cover all actions, not only the registry rows.
- **Risk:** Low. No stored shortcut overrides exist on this machine.
- **Validate:** The uniqueness test; manual: both commands fire.
- **Done:** No duplicate defaults; the test prevents new ones.
- **Disposition:** Fix.

**WI-1.2 Winner un-mark safety.** *Bug correction + behavioural change . M . FILES, NAS* (Depends on 0.5)
- **Finding:** P3.
- **Current:** Un-marking deletes `_winners/<name>` if it exists.
- **Cause:** No provenance check on what the app created.
- **Approach:** Delete only if the file is provably the app's copy: same file (hard link), or size and mtime match the source, or recorded in a small manifest. Otherwise leave it and tell the user. Investigate first whether real folders already contain user-added files in `_winners`.
- **Risk:** Medium. A change of what un-mark does to files; a wrong rule leaves stray copies.
- **Validate:** Characterization tests for all three modes, plus "same-name different file survives".
- **Done:** Un-mark never deletes a file the app did not create.
- **Disposition:** Fix (after the investigation).

**WI-1.3 Updater integrity.** *Investigation, then behavioural change . M . UPDATE* (Needs D14)
- **Finding:** P4.
- **Current:** The hash is checked only when provided.
- **Approach:** Verify what the GitHub release feed actually supplies (`digest` on assets). If always present, require it and fail closed; otherwise decide policy. Keep `test_updater` green.
- **Risk:** Medium. An overly strict rule could block legitimate updates.
- **Done:** A defined behaviour when the hash is missing, tested.
- **Disposition:** Investigate first.

**WI-1.4 Popout header fixture.** *Bug correction . L*
- **Finding:** E5 (reclassified, N7).
- **Approach:** Fix the test fixture (use `EMPTY_METADATA`); optionally add `or EMPTY_METADATA` in the header as defence in depth.
- **Done:** `test_preview_polling` passes.
- **Disposition:** Fix.

### Phase 2: Remove noise, leaves inward

**WI-2.1 Reachability tooling and deletion ledger.** *Test/infra . L*
- **Finding:** Section 7 inventory; the limits of name-based analysis.
- **Approach:** Commit the analysis scripts used for the audit (unreferenced defs, unused imports, unconnected signals, unused keys). Add a whitelist for dynamic access (`getattr(self, name)` sites, Qt overrides). Keep a **ledger** of every deletion: what, why, evidence, tests touched.
- **Done:** Re-runnable report; ledger started.
- **Disposition:** Fix.

**WI-2.2 Repo and asset hygiene.** *Safe cleanup . L . PKG* (Depends on 0.1; some need D15)
- **Finding:** A24, A25, A27, A28, A29, N11.
- **Approach:** Unused splash images (5, about 15.7 MB) and the two duplicate PNGs; stray root files (`xbutton.png`, `heartbutton.png`, zips, empty ssh files, `package-lock.json`); untracked 78 MB installer and installed `onnx` under `sandboxes/`; stale `image_triage/engine/*.pyc`. **File and folder removal is the user's action**; exact literal paths will be provided. For `pocketdrop.dll`, decide policy (commit vs build in CI, D15). Check `pyproject.toml` package declarations after 4.6.
- **Risk:** Low, but `freeze_support.py` names `splash_background-v4.png` explicitly (currently unreferenced by code but required by packaging): keep it.
- **Validate:** `test_freeze_support`; a packaging dry run.
- **Done:** Only referenced assets remain and the tree is clean.
- **Disposition:** Candidate for removal (with user-run commands).

**WI-2.3 Peel dead MainWindow code, orphan attributes, unemitted signals, unused imports.** *Safe cleanup . L-M* (Depends on 0.5, 2.1)
- **Finding:** A18, section 7 (38 methods, 39 attributes, 44 unused imports, `dispute_*` and `InspectorPanel` signals, popout helpers).
- **Current:** The unused imports hide which AI-training tasks and dialogs are still reachable.
- **Approach:** Rounds. Round 1 removes unused imports (safe and mechanical), then re-runs the analysis. Round 2 removes methods with zero references in code, tests and strings. Repeat until stable. Do not remove "test-only" methods without deciding whether the test itself is obsolete.
- **Risk:** Low. The risk is dynamic access, which the whitelist catches.
- **Validate:** Full suite diff; app starts; a smoke run of each menu.
- **Done:** The analysis reports no unreferenced MainWindow methods beyond the whitelist.
- **Disposition:** Candidate for removal (verified per round).

**WI-2.4 Retire the dormant DINO, ranker and training-UI stack.** *Architectural change . M . SETTINGS, AI-DATA, COMPAT* (Depends on 2.3; needs D2)
- **Finding:** A5, A7, I6, S1 (DINO page), S3.
- **Current:** DINO is forced off in three places. `TrainRankerDialog`, source pickers and the progress dialog are never instantiated. `FilterMode.DINO_*` exists.
- **Cause:** The pivot to CLI-Culler left the old scaffolding.
- **Approach:** Remove in this order: (1) unreachable window code and imports; (2) unreachable `ai_training` members; (3) the dormant DINO run stage and settings page. **Keep** `FilterMode.DINO_REMOVED/RESCUED` values (or map them) until we confirm saved filter presets (`filters/saved_queries`) do not contain them, because deserialising an unknown enum value could break loading. **Never touch** `global_adapter_labels.sqlite` or registered training sources.
- **Risk:** Medium. Touches AI/training data code paths and persisted filters.
- **Validate:** Reachability report; open a saved filter containing each `FilterMode`; AI run end-to-end on a small folder; adapter training still works.
- **Done:** No DINO or ranker UI, settings or run stages remain; live adapter flows unchanged.
- **Disposition:** Candidate for removal, pending D2.

**WI-2.5 Carve out `ai_workflow.py`.** *Internal refactor . M-H . AI-DATA, SETTINGS, COMPAT* (Depends on 2.4)
- **Finding:** A4, S4.
- **Current:** `window._ai_runtime` (an `AIWorkflowRuntime`) still drives device preference, model installation status and the setup dialogs. `aiculler_workflow` imports paths, metrics parsing and hidden-root helpers from it.
- **Cause:** The "runtime" concept predates CLI-Culler.
- **Approach:** (1) Move the live pieces (`AIWorkflowPaths`, `build_ai_workflow_paths`, hidden-root names, metrics parsing, device/worker capacity, artifact readiness) into a small module, **keeping the on-disk layout byte-identical**. (2) Move device selection and model-installation state onto the CLI-Culler runtime. (3) Then delete `AIRunTask`, staging and the AICullingPipeline command runners. `ai_workflow_cache` rows in the catalog database must still resolve.
- **Risk:** Medium-high. Existing per-folder `.image_triage_ai` data must remain readable; device selection must not regress.
- **Validate:** Open a folder with existing AI data and results load; device preference persists; `test_aiculler_workflow` and the catalog cache tests.
- **Done:** No live code imports the old runner code.
- **Disposition:** Refactor, then remove.

**WI-2.6 Decommission `AICullingPipeline/`.** *Architectural change . H . PKG, CI, COMPAT* (Depends on 2.4, 2.5, 0.4; needs D2)
- **Finding:** A6.
- **Current:** CI requires `AICullingPipeline/app|configs|scripts`; `freeze_support` stages it; `ai_python_runner` prepends its root; about 103 legacy tests exercise it.
- **Alternatives:** (A) delete outright; (B) tag/archive, stop packaging, keep one release cycle, then delete **(lean)**; (C) keep dormant and documented.
- **Approach:** Stop staging and CI checks first; update `test_freeze_support` and the Linux test list; remove legacy tests with the code; update README and docs. A real MSI build plus the clean-machine matrix is the acceptance gate.
- **Risk:** High for packaged builds; low for the running app.
- **Validate:** A clean MSI build; an installed app runs a full AI cull; frozen-app smoke.
- **Done:** The engine is out of the shipped app, CI and the default test run.
- **Disposition:** Candidate for removal, blocked.

**WI-2.7 Settings-dialog dead surface, dead toolbar customizer, dead keys.** *Safe cleanup . L* (Depends on 2.4; coordinate with 3.3)
- **Finding:** A15, S1, S2, S3, S4.
- **Approach:** Drop the four hidden controls (dispute weight, base weight, near-duplicate threshold, DINO workers) **after** deciding whether adapter labelling still needs the first two as real settings. Drop the four unused callbacks and `ToolbarCustomizerDialog`. Stop *reading* dead keys. **Do not delete registry values**; only stop referencing them.
- **Risk:** Low, except that dispute weight and base weight feed live adapter code (`window.py:17447`, `_apply_base_score_blend_to_workflow`). **Verify before deciding** whether they become visible settings or fixed constants.
- **Done:** Every constructor argument and widget in the dialog has a consumer.
- **Disposition:** Investigate first (two settings), otherwise remove.

**WI-2.8 UI prototype and dev tooling.** *Safe cleanup . L* (Needs D13)
- **Finding:** U8.
- **Approach:** Remove or gate `generated_prototype.py`, the `open_ui_prototype` action, its shortcut target and its menu item. `prototype_style.py` is **live** (used by `FolderTreeView`); rename it later, do not delete.
- **Done:** No dev-only window in the product menus.
- **Disposition:** Decision, then remove.

### Phase 3: Foundations (settings, shortcuts, persistence)

**WI-3.1 Settings identity and registry inventory.** *Migration/data . M . SETTINGS, COMPAT* (Needs D10)
- **Finding:** A20, S3/S7, N10.
- **Current:** Five `QSettings()` constructions; the organisation is "Codex"; `ui/shortcuts.py` uses a *different* identity (`ImageTriage`).
- **Alternatives:** (A) rename the organisation with copy-migration and pin the cache path; (B) keep "Codex" and document; (C) unify the stray identity onto the existing one **(lean now)**. Do A only if wanted.
- **Approach:** Introduce one settings accessor; read the old identity as a fallback; write only to the current one. Produce a read-only inventory of registry values with no code reference.
- **Risk:** Medium for A (the cache and every setting move); low for C.
- **Validate:** Round-trip tests; a registry export diff before and after.
- **Done:** One settings identity; documented.
- **Disposition:** Refactor, with the rename deferred pending D10.

**WI-3.2 Unify shortcuts.** *Architectural change . M . KEYS, SETTINGS* (Depends on 1.1, 3.1; needs D6)
- **Finding:** A10, U2, U3.
- **Current:** Two editors, two stores (per-action keys vs a JSON blob), different IDs. The window applies its system after the registry one, and captures the "default" *after* overrides, so its "Reset" restores the customised key.
- **Cause:** The window's binding system grew first; the registry was added later for the Settings page.
- **Approach:** Investigate first: enumerate every action, every hard-coded key handler in `grid`/`preview`, and the palette. End state: one registry describing every binding (actions plus review keys), one store, one editor UI (Settings > Shortcuts). Migrate by reading both old stores. Decide which hard-coded keys become rebindable (D6).
- **Risk:** Medium. Keyboard is the core interaction; test on the real grid.
- **Validate:** The uniqueness test; a table-driven test that each default key triggers its action; manual pass over the grid, popout and palette.
- **Done:** One store, one editor, no default drift, collision checker covers everything.
- **Disposition:** Consolidate.

**WI-3.3 Reconcile Settings with real consumers.** *UX change . M . SETTINGS* (Depends on 2.7, 3.1; needs D1, D5)
- **Finding:** S1-S5, U7.
- **Approach:** Add settings that are missing (theme, performance logging, and the AI-tag choice from D1). Remove duplicated toggles (View menu vs Settings) or make one authoritative. Rename "Processing workers" to match its stored meaning, or fix the key. Add a test that every field of the settings result has a consumer.
- **Risk:** Medium. Behaviour of `restore_folder_position` and similar new switches must stay.
- **Done:** Each setting has one home and one consumer.
- **Disposition:** Refactor and UX change.

**WI-3.4 Annotation persistence semantics.** *Investigation . M . ANNOT, SESS, DB* (Depends on 0.5; needs D5)
- **Finding:** A19, A22, N5.
- **Current:** Session store and XMP both feed annotations; a non-empty session record overrides a sidecar; an empty one does not.
- **Approach:** Write a specification of precedence, prove it with the characterization tests, then decide whether the "sidecar resurrects a cleared mark in another session" case is reproducible. Fix only if it is real. Do **not** alter the schema in this item. Rename "Session" in the UI only after D5.
- **Risk:** High if rushed (annotations are the culling work).
- **Done:** A written precedence rule with tests; any confirmed bug fixed or ledgered.
- **Disposition:** Investigate first.

**WI-3.5 Thumbnail cache policy.** *Performance / migration . M . CACHE, NAS* (Depends on 3.1)
- **Finding:** A23 (strengthened by N4: 3.2 GB, 72,860 files).
- **Alternatives:** (A) cap by size with LRU eviction and keep path-keyed **(lean first)**; (B) also key by content (name, size, mtime) so moves do not invalidate; (C) leave unbounded.
- **Approach:** Add a background, throttled eviction with a configurable cap; do it in-app only for the app's own cache directory. Changing the key invalidates the existing cache once, so treat B as a separate step.
- **Risk:** Medium. The organisation rename would move this directory (see 3.1).
- **Validate:** Unit tests with a temp cache and size cap; a manual check on the real cache.
- **Done:** Cache size stays under the cap.
- **Disposition:** Fix.

**WI-3.6 Data placement (Roaming vs Local; Store-Python redirect).** *Investigation . M . AI-DATA, COMPAT* (Needs D10)
- **Finding:** A21, N3.
- **Approach:** Measure what the **running** process sees. Classify each file as irreplaceable (`global_adapter_labels.sqlite`, 425 labels), derived (embedding workspace) or cache. Only after that consider relocating derived data, using an explicit migration, never implicit. Test in Store Python **and** the frozen app.
- **Risk:** High for irreplaceable data.
- **Disposition:** Investigate first; defer.

### Phase 4: Structural enablers

**WI-4.1 File-operation service (three sub-steps).** *Architectural + behavioural + performance . H . FILES, ANNOT, NAS, EDITS* (Depends on 0.5, 1.2; needs D4)
- **Finding:** P1, P2, N9, and the Move-to dialog gaps.
- **Current:** `_sync_winner_copy` copies inline before the undo entry and persistence; `_apply_ai_culling` moves per record with a full view rebuild each time; `_move_records_by_paths` also removes records one by one.
- **Cause:** File I/O, undo, persistence and view refresh are interleaved in `MainWindow` methods.
- **Approach:**
  - **4.1a Extract** winner sync, move/copy/delete, recycle, and undo construction into a module with a UI-free API. No behaviour change; the characterization tests must pass unchanged.
  - **4.1b Batch removal API:** remove N records, then one view refresh. Route `_move_records_by_paths`, Apply-AI and drag-drop through it.
  - **4.1c Async winner copy:** run the copy in the annotation worker with optimistic UI, a visible failure notice and rollback. Decide (D4) how undo behaves while a copy is pending, and keep Copy mode working on the NAS.
- **Risk:** High. This is the path that writes user files.
- **Validate:** Characterization suite; a stress test of 300 moves (time, view rebuild count); a NAS manual test.
- **Done:** No file copy or move on the UI thread; one view refresh per batch; behaviour otherwise identical.
- **Disposition:** Refactor, then behavioural change.

**WI-4.2 Job and progress consolidation.** *Internal refactor . M* (Depends on 4.1)
- **Finding:** A11, P8 (`processEvents`).
- **Approach:** Extend `JobController` with byte and speed reporting, cancel and detail text, then migrate the transfer dialog and the raw `QProgressDialog`s. Keep the stage-aware AI dialog and the inline overlay separate (Part 2 section 3). Remove `processEvents` calls where a worker now exists.
- **Risk:** Medium (reentrancy behaviour changes).
- **Done:** One generic progress path; no `processEvents` in file operations.
- **Disposition:** Consolidate (generic ones only).

**WI-4.3 Duplicate/similar-frame systems.** *Investigation + decision . M* (Needs D7)
- **Finding:** A13, U5.
- **Approach:** Document what each engine produces and who consumes it; measure whether pHash is computed twice per folder; decide whether to share the computation and align vocabulary. Do **not** merge engines. Settings > Duplicates keeps its scope but is renamed or explained.
- **Done:** A one-page map and a decision.
- **Disposition:** Investigate first.

**WI-4.4 MainWindow decomposition.** *Architectural change . H* (Depends on 0.5, 2.3-2.6, 4.1, 3.2; needs D11)
- **Finding:** A1, A2 (init size), P6.
- **Sizing (measured):** about 7,000 lines (27%) AI/ML related; 4,250 toolbar/topbar/palette/menus; 2,570 scan/records-view/filter/search; 2,050 annotations/review/undo; 1,690 file ops; 650 catalog/library; 1,500 layout/appearance/dialogs.
- **Alternatives:** (A) mixins (cheap, no coupling reduction); (B) feature controllers with explicit interfaces **(lean)**; (C) prune only.
- **Order:** AI orchestration (already shrunk by 2.4-2.6) -> toolbar/palette/menus -> file-ops service (from 4.1) -> catalog/library -> **last** records-view/scan (highest coupling). Keep thin delegating methods until callers migrate.
- **Risk:** High. Attribute ownership is tangled (558 attributes); use the real-window harness.
- **Done (per slice):** Public behaviour unchanged; the slice's state lives in its controller; the harness and full suite pass.
- **Disposition:** Refactor incrementally; the last slice deferred.

**WI-4.5 UI-as-state-holder cleanup.** *Internal refactor . M* (Depends on 6.1; touches D1)
- **Finding:** A17.
- **Approach:** Trace every reader of the hidden `mode_tabs` and the popout's legacy analysis panel. Move state to plain objects, then remove the hidden widgets.
- **Disposition:** Investigate first.

**WI-4.6 Editor engine import and packaging.** *Migration/packaging . M-H . PKG* (Depends on 2.6; needs D15)
- **Finding:** A8, A29.
- **Alternatives:** (A) vendor `photo_terminal` into the package; (B) declare `cli_editor` as a real dependency **(lean)**; (C) keep the path hack, documented.
- **Risk:** Medium-high: the frozen app copies it to `lib/photo_terminal`, and a wrong change breaks the editor at startup (a class of failure `setup_msi.py` already documents).
- **Validate:** Frozen-app smoke; a `pip install` of a wheel imports the editor.
- **Disposition:** Investigate first.

### Phase 5: Editor and integration

**WI-5.1 Editor undo/redo.** *UX change . M* (Needs D12)
- **Finding:** E1. The popout shows permanently disabled Undo/Redo ("not available yet").
- **Approach:** Investigate: recipes (`EditRecipe`) are replaced immutably, so global adjustments snapshot cleanly. Masks, retouch, background and crop mutate a session dict and need a clear scope. Start with recipe history; extend by D12. Until it ships, hide the disabled buttons.
- **Validate:** Round-trip tests; interaction with the render-service drop rules.
- **Done:** Ctrl+Z in the editor and enabled buttons.
- **Disposition:** Investigate first, then implement.

**WI-5.2 Mask geometry parity.** *Test/infra + internal refactor . M* (Depends on 0.5)
- **Finding:** A9/E3.
- **Approach:** Add tests asserting the overlay strength field equals the renderer's for shapes under crop, rotate and flip, then decide if the overlay can call the renderer's function. Keep the interactive low-resolution path.
- **Disposition:** Investigate first.

**WI-5.3 What "Edited" means, and downstream use of edits.** *Behavioural + UX . M-H . EDITS, EXPORT* (Needs D3)
- **Finding:** U4, E2.
- **Staged:** (1) a cheap flag for "has an editor recipe" from the sidecar folder listing; (2) a separate badge or filter; (3) thumbnails rendered with the recipe, cached by recipe hash; (4) opt-in "apply edits" for resize/convert/handoff/PocketDrop.
- **Risk:** High for exports (a wrong render silently exports the wrong pixels); NAS listing cost for (1).
- **Disposition:** Decision first, then staged.

**WI-5.4 Click-select add/subtract.** *UX change . L-M* (Depends on 5.2)
- **Finding:** E6. The engine already accepts labelled points.
- **Disposition:** Planned.

### Phase 6: AI experience (after decisions and cleanup)

**WI-6.1 AI visibility.** *UX/behavioural . M* (Needs D1; depends on 2.3-2.5, 3.3)
- **Finding:** I1, N6.
- **Approach:** Implement the chosen option; for (A) an opt-in setting that turns `set_show_ai_annotations` on, plus auto-loading saved results. Keep the manual-clean default.
- **Disposition:** Decision, then fix.

**WI-6.2 Apply AI Decisions.** *Behavioural . H . FILES, ANNOT* (Depends on 4.1, 6.1)
- **Finding:** I2.
- **Approach:** Show a reviewable list, run through the file-operation service with progress and cancel, and make it a single undoable batch.
- **Disposition:** Refactor, then behavioural change.

**WI-6.3 AI entry-point consolidation.** *Investigation . M* (Needs D9)
- **Finding:** I5. About 35 AI actions and 3 overlapping dialogs.
- **Approach:** Map what each dialog owns, pick canonical flows, hide the rest from menus first (reversible), delete later.
- **Disposition:** Investigate first.

**WI-6.4 AI v4 disposition.** *Decision* (Needs D9)
- **Finding:** I4. `quality/` dimension code is referenced only by tests.
- **Disposition:** Decision; otherwise intentionally retained.

**WI-6.5 Explain-why chips.** *UX . L-M* (Depends on 6.1)
- **Finding:** I3. Opt-in only, per N6.
- **Disposition:** Defer.

### Phase 7: UX vocabulary and docs

**WI-7.1 Discoverability.** *UX . L*
- **Finding:** U1. The menu bar is hidden by design; about 16 actions are in no menu.
- **Approach:** Ensure every action is in the palette; consider labelled entries for the orphan actions. Keep the hidden menu (a deliberate v5 decision).
- **Disposition:** Planned.

**WI-7.2 Vocabulary pass.** *UX . L-M* (Needs D5, D7, D8)
- **Finding:** U6, A12, A14, S6.
- **Approach:** Agree a glossary; change display strings only; fix duplicated "Set Up AI...", "Open AI Workflow Center" on the run action, and "Demo Ready".
- **Disposition:** Planned after decisions.

**WI-7.3 Documentation refresh.** *Safe cleanup . L*
- **Finding:** A3, U9, README (DINO), roadmap, in-app tag legend, `docs/image_triage_architecture.md`.
- **Approach:** Update per phase as code changes; a final sweep at the end.
- **Disposition:** Planned.

### Phase 8: Performance and reliability polish

- **WI-8.1 Toolbar rebuild and startup.** *Performance . M.* Finding: P5, P6. Profile the roughly 12 call sites of `_rebuild_topbar_action_stack` (472 ms max on the UI thread); skip rebuilds when nothing changed. Independent of 4.4.
- **WI-8.2 NAS strategy for watch and refresh.** *Investigation . M . NAS.* Finding: P9. `QFileSystemWatcher` does not observe shares; decide on polling.
- **WI-8.3 Silent exception audit.** *Internal refactor . L.* Finding: P8 (250 broad excepts, 15 silent in window). Log instead of swallow, one module at a time.
- **WI-8.4 Renderer/GPU seam.** *Deferred.* Finding: E4; future.

## 6. Sequencing logic and parallelism

- **Phase 0 must finish first.** Without a green, hermetic, CI-gated baseline nothing after it can be judged.
- **Phase 1 can run alongside Phase 0**, once 0.5 has the tests for 1.2. 1.1, 1.3 and 1.4 are independent.
- **Phase 2 comes before Phase 4** because removing dead AI code (about a quarter of `MainWindow`) makes the decomposition seams visible and shrinks what has to be tested.
- **3.2 (shortcuts) and 3.3 (settings)** must precede any UI/vocabulary change, so labels and keys change once.
- **4.1 precedes 6.2** (Apply AI) and 4.2.
- **Editor work (5.1, 5.2, 5.4) is largely independent of `MainWindow`** and can proceed in parallel with Phases 2-4, gated only by 0.5 and the mask tests.
- **Deliberately postponed:** records-view/scan decomposition (last slice of 4.4), organisation rename and data relocation (3.1 rename, 3.6), GPU renderer, AI explanation chips, thumbnail rendering with edits.

## 7. Dependency map

```
Phase 0  0.1 -> 0.2 -> 0.3 -> 0.4 (CI gate)
                 \--> 0.5 (characterization + harness) --+--> 1.2, 3.4, 4.1, 5.2, 2.3
         0.6, 0.7 (baselines, backups) -> before any data/settings work (3.x)

Decisions  D2 -> 2.4 -> 2.5 -> 2.6 -> 4.6
           D6 -> 1.1 -> 3.2 -> (7.2)
           D10 -> 3.1 -> 3.5, 3.6
           D5 -> 3.4, 7.2      D1 -> 6.1 -> 6.2, 6.5, 4.5
           D4 -> 4.1c          D3 -> 5.3        D12 -> 5.1
           D9 -> 6.3, 6.4      D11 -> 4.4       D13 -> 2.8

Cleanup    2.1 -> 2.3 -> 2.4 -> 2.5 -> 2.6      2.7 -> 3.3
Structure  0.5 + 2.x + 3.2 -> 4.1 -> 4.2 -> 6.2
           4.1 + 2.x -> 4.4 (AI slice -> toolbar -> file ops -> catalog -> records-view)
Editor     0.5 -> 5.2 -> 5.4       5.1 (independent)       D3 -> 5.3
```

Two rules follow from the map: **nothing in Phase 4 or later starts before 0.5**, and **2.6 (packaging) never starts before 2.5 and a working full-suite CI**.

## 8. Master remediation checklist

Status key: **P** planned . **I** investigate first . **B** blocked by another item or decision . **X** intentional behaviour . **D** defer . **R** candidate for removal. A tick column is provided so progress can be recorded in this file.

### Architecture and codebase

| Done | ID | Finding | Status | Item |
|---|---|---|---|---|
| [ ] | A1 | `MainWindow` god object | B (0.5, 2.x, 4.1) | 4.4 |
| [ ] | A2 | Long functions / 2,677-line stylesheet | D | 8.x (low value now) |
| [ ] | A3 | Stale `CODEBASE_REVIEW.md` | P | 7.3 |
| [ ] | A4 | Three AI generations | P | 2.4-2.6 |
| [ ] | A5, I6 | DINO stack dead | R (D2) | 2.4 |
| [ ] | A6 | Legacy engine packaged and CI-gated | B | 2.6 |
| [ ] | A7 | Never-instantiated dialogs | R | 2.4 |
| [ ] | A8 | Editor engine `sys.path` and packaging | I | 4.6 |
| [ ] | A9, E3 | Two mask rasterisers | I / X (intentional split) | 5.2 |
| [ ] | A10 | Two shortcut editors | P | 3.2 |
| [ ] | A11 | Six progress UIs | P (generic only) | 4.2 |
| [ ] | A12 | "Catalog" naming | X (systems distinct); label only | 7.2 |
| [ ] | A13 | Four duplicate mechanisms | X (distinct jobs) / I | 4.3 |
| [ ] | A14 | "Workflow" naming | P | 7.2 |
| [ ] | A15 | Toolbar systems, dead customizer | R | 2.7 |
| [ ] | A16 | `plugins/` indirection | X (leave) | none |
| [ ] | A17 | UI as state holder | I | 4.5 |
| [ ] | A18 | 39 orphan attributes | R | 2.3 |
| [ ] | A19 | About 10 persistence layers | X (roles differ); document | 3.4 |
| [ ] | A20 | Organisation "Codex" | I (D10) | 3.1 |
| [ ] | A21 | Roaming derived data (corrected N3) | I / D | 3.6 |
| [ ] | A22 | Two annotation sources | I | 3.4 |
| [ ] | A23 | Thumbnail cache unbounded (3.2 GB) | P | 3.5 |
| [ ] | A24 | Stray tracked root files | R (user-run) | 2.2 |
| [ ] | A25 | Untracked junk (78 MB exe, onnx) | R (user-run) | 2.2 |
| [ ] | A26 | Repo inside OneDrive | D (user environment) | none |
| [ ] | A27 | `pocketdrop.dll` committed | I (D15) | 2.2 |
| [ ] | A28 | Unreferenced splash images | R (keep v4, packaging) | 2.2 |
| [ ] | A29 | `pyproject` package list | I | 4.6 |
| [ ] | A30 | Git stashes / WinUI | B (D11) | 4.4 |

### UI/UX

| Done | ID | Finding | Status | Item |
|---|---|---|---|---|
| [ ] | U1 | Hidden menu, ~16 actions in no menu | P | 7.1 |
| [ ] | U2 | Review keys (corrected N7) | P | 3.2 |
| [x] | U3 | `Ctrl+Alt+P` collision (fixed in WI-1.1) | P | 1.1 |
| [ ] | U4 | "Edited" semantics | B (D3) | 5.3 |
| [ ] | U5 | Duplicates page scope | B (D7) | 4.3, 7.2 |
| [ ] | U6 | Naming drift (Keeper/Winner intentional) | P / X | 7.2 |
| [ ] | U7 | Scattered settings | P | 3.3 |
| [ ] | U8 | UI prototype in product | R (D13) | 2.8 |
| [ ] | U9 | Stale docs and text | P | 7.3 |

### AI and culling

| Done | ID | Finding | Status | Item |
|---|---|---|---|---|
| [ ] | I1 | AI results invisible | X (prior decision) + B (D1) | 6.1 |
| [ ] | I2 | Apply AI Decisions safety | B (4.1, 6.1) | 6.2 |
| [ ] | I3 | Underused signals | D | 6.5 |
| [ ] | I4 | AI v4 half-built | B (D9) | 6.4 |
| [ ] | I5 | Too many AI entry points | I | 6.3 |

### Editor

| Done | ID | Finding | Status | Item |
|---|---|---|---|---|
| [ ] | E1 | No undo; disabled buttons | I | 5.1 |
| [ ] | E2 | Edits siloed | B (D3) | 5.3 |
| [ ] | E4 | CPU-only renderer | D | 8.4 |
| [x] | E5 | Header crash (reclassified as fixture) | P | 1.4 |
| [ ] | E6 | Click-select add/subtract | P | 5.4 |

### Settings

| Done | ID | Finding | Status | Item |
|---|---|---|---|---|
| [ ] | S1 | Four hidden controls, DINO page | I / R | 2.7, 3.3 |
| [ ] | S2 | Dead dialog callbacks | R | 2.7 |
| [ ] | S3 | Dead keys, orphan registry values | R (never delete registry values) | 2.7, 3.1 |
| [ ] | S4 | Stubbed `_ai_semantic_sidecar_enabled` | R | 2.5 |
| [ ] | S5 | Missing settings (theme, perf, AI tags) | P | 3.3 |
| [ ] | S6a | Session preset conflation | B (D5) | 3.4, 7.2 |
| [ ] | S6b | Winner-copy default | B (D4) | 4.1c |
| [ ] | S6c | Watch folder on NAS | I | 8.2 |
| [ ] | S6d | "Processing workers" naming | P | 3.3 |
| [ ] | S6e | AI ranges' visible effect | B (D1) | 6.1 |

### Performance and reliability

| Done | ID | Finding | Status | Item |
|---|---|---|---|---|
| [ ] | P1 | Winner copy on UI thread | B (D4, 4.1) | 4.1c |
| [ ] | P2 | Apply AI per-record view rebuild (also the move code) | B | 4.1b |
| [x] | P3 | Un-mark deletes blindly | I -> P | 1.2 |
| [x] | P4 | Updater hash optional | I (D14) | 1.3 |
| [ ] | P5 | Toolbar rebuild 472 ms | P | 8.1 |
| [ ] | P6 | Startup / 1,301-line `__init__` | P / B | 8.1, 4.4 |
| [x] | P7 | Discovery on shared pool (own 1-thread pool, done in WI-0.1) | P | 0.1 |
| [ ] | P8 | `processEvents`, silent excepts | P | 4.2, 8.3 |
| [ ] | P9 | Watcher on NAS | I | 8.2 |

### Tests, build and new findings

| Done | ID | Finding | Status | Item |
|---|---|---|---|---|
| [x] | T1 | Collection errors (5 modules; fixed in WI-0.2) | P | 0.2 |
| [x] | T2 | "Hanging" test: was a real GPU-worker spawn plus a PIL thread-race crash; both removed (WI-0.2) | P | 0.2 |
| [x] | T3 | 6 session-caused failures (fixed in WI-0.1) | P | 0.1 |
| [~] | T4 | Legacy failures (8) and tests (~103): the 2 legacy failures are strict xfails (0.3); the 6 `decision_harvest` ones were a sqlite lock, now repaired; removal waits for D2 | B (D2) | 2.4-2.6, 0.3 |
| [~] | T5 | Stub-based `MainWindow` tests: the real-window harness now exists (0.5); the 8 strict xfails can be rewritten on it | P | 0.5 |
| [x] | T6 | Obsolete expectations (rewritten to invariants, WI-0.3) | P | 0.3 |
| [x] | T7 | Tests touch real environment (QSettings/AppData/logs sandboxed, WI-0.2) | P | 0.2 |
| [x] | T8 | Two runners: pytest is the single runner and CI is switched (WI-0.4) | P | 0.2 / 0.4 |
| [x] | N1 | CI runs almost no tests (new `tests.yml` full-suite gate; packaging workflows on pytest; unproven until pushed) | P | 0.4 |
| [x] | N2 | No tests on file mutation (characterization tests, mutation-checked) | P | 0.5 |
| [ ] | N3 | Store-Python redirect | I | 3.6 |
| [ ] | N4 | Cache/organisation coupling | I | 3.1, 3.5 |
| [ ] | N5 | "Session" vocabulary | B (D5) | 3.4 |
| [ ] | N6 | Prior product decisions | X | D1 |
| [ ] | N9 | The session's own change debt | P | 0.1, 4.1b |
| [ ] | N10 | Registry orphans | I | 3.1 |
| [ ] | N11 | WinUI `.pyc` leftovers | R (user-run) | 2.2 |
| [ ] | O1-O9 | Opportunities (AI tags/why, unified stacks, edits everywhere, editor undo and batch, coach, click-select, GPU, faster libraries, Move-to everywhere) | D / B | 6.x, 5.x, 4.3, 8.4 |

Nothing from the audit is dropped. Items judged intentional (A9 split, A12, A13 engines, A16, A19, U6 in part, I1 default) stay in the list with that status.

### Product decisions tracker

| Done | ID | Decision | Owner | Decided |
|---|---|---|---|---|
| [ ] | D1 | Where AI results appear (opt-in setting, inspector only, or dedicated surface) | user | |
| [x] | D2 | Fate of legacy engine and DINO | user | **Decided 2026-09-26: delete.** No archive branch or tag (deliberately unrecoverable) |
| [ ] | D3 | Meaning of "Edited"; edits in thumbnails and exports | user | |
| [x] | D4 | Winner handling defaults and async semantics | user | **Decided 2026-09-26: keep Copy as an option; the rest is delegated.** Default for new installs and async-with-error-notice are my call at WI-4.1c (link/symlink option stays available for cross-folder collections) |
| [ ] | D5 | The word "Session" | user | |
| [x] | D6 | Shortcut scope; owner of `Ctrl+Alt+P` | user | **Decided 2026-09-26: fully rebindable (including review keys); PocketDrop keeps `Ctrl+Alt+P`.** Next AI Top Pick moved to `Ctrl+Alt+N` (WI-1.1); full rebinding lands in WI-3.2 |
| [ ] | D7 | Meaning and scope of "Duplicates" | user | |
| [ ] | D8 | "Catalog" vs "Library" naming | user | |
| [ ] | D9 | AI v4 direction and AI entry points | user | |
| [ ] | D10 | Organisation/data migration appetite | user | |
| [ ] | D11 | WinUI migration stashes | user | |
| [ ] | D12 | Editor undo scope | user | |
| [ ] | D13 | UI prototype in the product | user | |
| [x] | D14 | Updater integrity policy | user | **Decided 2026-09-26: require a verified hash** |
| [ ] | D15 | Platform support (Linux/macOS) | user | |

## 9. Recommended starting point

**Start with WI-0.1, then 0.2 and 0.3 together, and begin 0.5 in parallel.** In other words: get to a green, hermetic, CI-gated baseline, and write characterization tests for winner sync, moves and undo before anything else.

Why this order:
1. It resolves the failures introduced during the session, so the baseline is honest.
2. It costs nothing in behaviour and unlocks safe, provable changes everywhere else.
3. The first *behavioural* change should be **WI-1.2 (winner un-mark safety)**, because it is the highest data-loss risk and 0.5 will already cover it.
4. WI-1.1, the `Ctrl+Alt+P` collision, is a good early win once D6 (which command keeps the key) is decided.

**Decisions needed before Phase 2 or later:** D2 (legacy engine and DINO), D6 (shortcut scope and the `Ctrl+Alt+P` owner), and D4 (winner copy semantics). D1, D3 and D5 can wait until Phases 5-6, but they block those items.

No code was modified while producing this plan. The only actions were reads (code, registry, AppData), a test run, and stopping two hung test processes that had been launched for the audit.


## Implementation log

### WI-0.1 Land and lock the session's work: DONE (2026-09-26, uncommitted, awaiting user commit)
- Tests updated to intended behaviour: 3 in `test_settings_dialog` (new nav structure, help-button label, min width is clamped to 94% of screen), `test_editor_render` (every finished frame is delivered; only `cancel()` suppresses), `test_editor_crop_retouch` (press outside the box is inert).
- `perf.py`: disabling performance logging now stops the stall watchdog (cancels the faulthandler timer, stops the QTimer, closes `ui_stall_traces.txt`). This fixed `test_perf`. It already only started when logging was enabled.
- `preview.py`: edited-file discovery uses its own 1-thread pool (P7).
- Earlier session work is already committed (`ca9edfc`).
- Validation: the 4 affected files pass (108). Full suite excluding the known WI-0.2 items: 1391 passed, 26 failed, all in the Appendix A ledger except N13 below.
- Full-suite excluded (WI-0.2): collection errors in `test_tone_controls`, `test_ai_clean_machine_check`, `test_aiculler_topiq_onnx` (no `onnx`), `test_dinov2_extractor` and `test_ranking_dino_fallback` (no `torch`); hang in `test_labeling_data_quality`.

### New findings / follow-ups (not fixed)
- N13: `test_aiculler_technical_tags::test_optimized_metrics_match_legacy_metrics_exactly` fails on a ~2.5e-9 float difference in `harsh_light_score`. It is not in Appendix A and is untouched by this work. Triage in WI-0.3 (probably tolerance, not a bug).
- N14: T1 understated collection errors: five modules fail to import here (three need optional `torch`/`onnx`). WI-0.2 `importorskip` scope should cover them.

### WI-0.2 Test runner hygiene and hermetic environment: DONE (2026-09-26, uncommitted)
- New `tests/conftest.py`: repo root on `sys.path` (fixes `scripts` and `cli_editor` imports); offscreen Qt; `LOCALAPPDATA`, `APPDATA` and `IMAGE_TRIAGE_LOG_DIR` redirected to a temp sandbox; **QSettings forced onto an INI file in the sandbox**; a guard that fails any test spawning a real `mask_engine_worker`; a hard per-test timeout (`IMAGE_TRIAGE_TEST_TIMEOUT`, default 120 s, via `faulthandler`, since `pytest-timeout` is not installed); PIL plugins preloaded; `requires_models` marker registered.
- `importorskip` for `torch` and `onnx` in `test_dinov2_extractor`, `test_ranking_dino_fallback`, `test_aiculler_topiq_onnx`.
- New `tests/test_hermetic_environment.py` proves the sandbox (QSettings not native, app-data/log paths in temp, worker spawn blocked).
- Validation: full suite with no exclusions completes in about 60-90 s; two consecutive runs give the identical result (1422 passed, 5 skipped, 26 failed = exactly the known ledger + N13); no collection errors; no leftover processes.
- Deviation from plan (evidence-driven, minor): the plan said to quarantine the hanging test. It was not hanging; the crashes were (a) a real GPU worker spawn (now blocked) and (b) an intermittent interpreter access violation caused by worker threads racing to lazily import PIL plugins during GC. Preloading PIL plugins fixed it, so nothing is quarantined.
- Caution for WI-0.4: `test_hermetic_environment` and the sandbox only apply under **pytest**. CI's `unittest` invocations bypass `conftest.py` and `pytest.importorskip` is not unittest-friendly, so CI must move to pytest.

### New findings (continued, not fixed)
- N15: `QSettings("org","app")` ignores `setDefaultFormat` and always uses the registry. Any future test helper must go through the sandbox class in `conftest.py`. Earlier tests such as `test_pocketdrop` (`QSettings().clear()`) were only safe by accident.
- N16: The legacy labeling loader (`AICullingPipeline/app/labeling/loaders.py`, dHash thread pool) can crash the interpreter through the same PIL plugin race. It is legacy code slated for D2; note if it is retained.
- N17: The agent test harness (`pythonw`) needs valid OS-level std handles or subprocess tests fail with `WinError 6`. Harness-only, not a repo defect.

### WI-0.3 Failure ledger and triage: DONE (2026-09-26, uncommitted)
- Suite is **green**: 1438 passed, 5 skipped, 15 xfailed (10 new + 5 pre-existing), exit code 0, identical on two consecutive runs. Ledger committed as `tests/FAILURE_LEDGER.md`.
- Investigated first, as planned:
  - `catalog_repository` x2: **stub drift, not a real scan/cache change**. `FolderScanTask` builds `CatalogRepository()` inline; tests patched a removed `_catalog` attribute. Repaired (patch the constructor, wait for the async persist task). Behaviour verified unchanged.
  - `decision_harvest` x6: **not schema drift**. Windows keeps the sqlite file locked after `with sqlite3.connect()` until GC, so the test temp dir cannot be deleted. Repaired in the test module only. See N18.
- Repaired: `aiculler_technical_tags` (float tolerance, N13), 6 obsolete expectations, `preview_polling` fixture (plus one-line hardening of `_update_mockup_image_info`).
- Strict xfails (10): 8 stub-drift tests -> WI-0.5; 2 legacy tests -> D2.
- Product code touched: only the one-line `or EMPTY_METADATA` in `preview.py`.

### New findings (continued, not fixed)
- N18: **Every `with sqlite3.connect(...)` in the codebase leaves the database file locked on Windows until a GC cycle** (Python 3.13: the connection is in a reference cycle, and the context manager commits but does not close). It affects `DecisionStore` (9 sites), `CatalogRepository`, `LibraryStore` and others. Real effects: a DB file cannot be deleted, moved or replaced (backup, migration, "reset cache") right after use. Relevant to WI-3.x (data/migration) and any reset/restore path; fix once with a shared `closing()` connection helper. Not fixed here.
- N19: the two `run_ai_pipeline` stub tests and the AI-run/training/load/reset ones cover AI-mode code that was retired on 2026-09-19; they are candidates for deletion with D2 rather than rewriting in WI-0.5.

### WI-0.4 Real CI gate: DONE (2026-09-26, uncommitted; **not yet run on GitHub**)
- New `.github/workflows/tests.yml`: full suite (`pytest tests -m "not requires_models"`) on push (main, `codex/**`), pull requests and manual dispatch. **The Windows job gates.** The Linux job is `continue-on-error` (informational) until D15 decides cross-platform support, because PocketDrop is Windows-only and Qt/Linux behaviour is unverified.
- `build-windows-msi-release.yml` and `build-linux-appimage.yml` now run pytest. This was needed because `unittest` bypasses `conftest.py`, and strict xfails only work under pytest. The Linux packaging job no longer runs the legacy `test_ai_workflow` and `test_dinov2_extractor`; the full suite covers them.
- `pyproject.toml`: added `[project.optional-dependencies] dev = ["pytest>=8"]`.
- Validated locally: the exact CI commands pass (48 packaging tests; full suite), and the YAML and TOML parse. **The workflows themselves can only be proven by a push.** Action for you: push the branch, confirm the Windows job is green, then decide whether to make it a required check.

### WI-0.5 Characterization tests and real-window harness: DONE (2026-09-26, uncommitted)
- Harness (`tests/harness.py` plus fixtures in `conftest.py`): builds the real `MainWindow` offscreen against the sandbox. **Measured: about 3-6 s to build**, so it is feasible. One instance is shared for the whole session, with a per-test state reset. Any modal dialog is intercepted (recorded, or it fails the test), so nothing can hang.
- New characterization tests, all describing current behaviour: `test_char_transfer` (9), `test_char_winner_sync` (12), `test_char_move_delete_undo` (10), `test_char_batch_plans` (15), `test_char_annotations` (14), `test_char_view_state_and_shortcuts` (16), `test_char_edited_discovery` (7), plus the harness and hermetic tests.
- Pins that are deliberately "wrong" today and will change on purpose: `HAZARD_unmark_deletes_any_same_named_file_in_winners` (WI-1.2) and `window_bindings_have_a_known_conflict_on_ctrl_alt_p` (WI-1.1).
- **Mutation spot-check done.** Eight deliberate breakages (unmark keeps copy, no rollback on failure, stale-row check off, store precedence off, edit-stem match off, restore-position ignored, rename overwrite allowed, delete keeps annotation) each made the pinning tests fail. All mutations were restored.
- Suite: **1524 passed, 5 skipped, 15 xfailed, 0 failed**, about 75 s, identical across two runs.
- Fixed along the way: the harness first leaked the app-wide palette and stylesheet into later tests (2 geometry failures); it now snapshots and restores them. `test_people_dialog_crops` had a pre-existing race (the crop worker could finish before the assertion); the worker is now prevented from running in those tests.
- Sandbox gap found and closed: on Windows `QStandardPaths.AppDataLocation` ignores env vars, so the real window's `DecisionStore()` wrote into the **real profile**. `conftest.py` now redirects `QStandardPaths.writableLocation`, with a test.

### WI-0.6 Baselines and manual smoke script: DONE (2026-09-26)
- `tests/perf_baselines.py` (run on demand, not collected by default). Results are stored outside the repo in `C:\Users\tylle\ImageTriage-baselines\baseline_2026-09-26.json`. They are headless, synthetic, and only comparable on the same machine:
  - MainWindow construct: about 5.8-6.1 s (cold, headless)
  - Local folder open, 300 small JPEGs: about 0.2-0.3 s
  - **Winner toggle: median about 90 ms (Annotation Only) and about 170 ms (Copy)**, max about 210 ms
  - Editor render: 2 MP about 72 ms per tick, 12 MP about 565 ms per tick (first render about 335 ms)
- Not measurable headless (listed in the smoke script): NAS folder open, real slider feel, popout first-open.
- `docs/manual_smoke_test.md`: 15 steps with expected results.
- Registry exports saved next to the baselines: `registry_Codex_Image_Triage_2026-09-26.reg` (60 KB) and `registry_ImageTriage_2026-09-26.reg`.

### WI-0.7 Data safety backups: DONE (2026-09-26)
- Backups made with SQLite's online-backup API (read-only on the source, integrity check `ok`) in `C:\Users\tylle\ImageTriage-baselines\data-backup-2026-09-26\`: `decisions.sqlite3` (both the AppData path and the Store-Python package path), `library.sqlite3` (7.9 MB), `catalog.sqlite3` (55 MB) and `global_adapter_labels.sqlite`, each for both path copies.

### New findings (continued, not fixed)
- N20: **Two test rows leaked into the real decision store** before the sandbox gap was closed (rowids 94 and 95, session `Default`, paths under `%TEMP%\pytest-of-tylle\...\test_marking_winner_clears_rej0\a.jpg`; 89 rows total). They are harmless and I did not delete them. To remove them (after checking the backup above), run against `%APPDATA%\Codex\Image Triage\decisions.sqlite3`: `DELETE FROM decisions WHERE rowid IN (94, 95);`
- N21: **A winner toggle takes 90-170 ms even in Annotation Only mode**, headless, on a 300-photo folder. That is far above the interactive budget for a key press; likely view rebuild and persistence on the UI thread. Input for WI-4.1 and 8.x.
- N22: Building `MainWindow` applies an app-wide palette and stylesheet and leaves timers behind. Constructing several in one process is slow (72 s for one late in the suite), so tests must share one instance (the harness does).
- N23: Two abandoned sandbox folders from killed runs remain in `%TEMP%` (`image_triage_tests_*`); safe to delete by hand.
- N24: Linux CI is unverified; expect Windows-only assumptions (D15).

### Decisions received (2026-09-26)
- D2 delete the legacy engine; D4 keep Copy (default and async behaviour delegated to me); D6 fully rebindable, PocketDrop keeps `Ctrl+Alt+P`.

### WI-1.1 `Ctrl+Alt+P` collision: DONE (2026-09-26, uncommitted)
- `next_ai_pick` default changed to `Ctrl+Alt+N` in `ui/shortcuts.py` (the registry, which is what actually takes effect) and `ui/actions.py`; docs text updated (`ai_culling.py`, `reference.py`, two help texts in `window.py`). PocketDrop keeps `Ctrl+Alt+P`. No user override existed for this key on this machine.
- The audit's pinned-conflict test was replaced by three guards: no two `MainWindowActions` share a default, no two registry defaults collide, and PocketDrop/next-AI-pick own the intended keys. Mutation check: restoring the duplicate in the registry makes 2 tests fail.
- Suite: 1526 passed, 5 skipped, 15 xfailed, 0 failed.
- Finding N25: the default literal in `ui/actions.py` is dead; `_apply_shortcut_overrides` overwrites it from the registry at startup. That is one more symptom of the two-store problem, fixed in WI-3.2. The runtime conflict checker in Settings still covers only the registry rows; extending it belongs with the D6 rebinding work (WI-3.2), not here.

### WI-1.2 Winner un-mark safety: DONE (2026-09-26, uncommitted)
- Investigation (read-only, from the catalog database, no NAS access): 6 `_winners` folders are cataloged; the 75 files in them all match their parent folder by name and size. No user-added files were found, so the new rule strands nothing that exists today.
- Change (`window.py`): un-marking now deletes `_winners/<name>` only if it is provably Image Triage's own artifact: a symlink resolving to the source, the same file (hard link), or a copy with the same size and a modification time within 2 s of the source. Otherwise the file is left alone, and the status bar says `Winner removed: <name> (left <file> in _winners: not a copy Image Triage made)`. If the source no longer exists the copy cannot be proven, so it is kept. `_sync_winner_copy(_for_paths)` now returns the names it kept.
- Behavioural consequence to know about: a copy whose source was modified after marking is now left behind (with the message) rather than deleted. Edits made in the popout do not modify sources, so this should be rare.
- Tests: the audit hazard test was inverted, and five provenance tests plus a status-message test were added. Mutation check (treat everything as ours): 4 tests fail. Suite: 1531 passed, 5 skipped, 15 xfailed, 0 failed.
- Not changed: reject/undo/AI-apply callers still ignore the returned "kept" list (no message there), and the async/error-notice behaviour is still WI-4.1c.

### Decisions received (2026-09-26, second batch)
- D2 confirmed: **delete the legacy engine, no archive branch or tag.** It was deliberately dead and is meant to be unrecoverable. WI-2.4 therefore deletes with no safety branch; the ledger (WI-2.1) still records what was removed and the evidence it was unreferenced.
- D14: **require a verified hash.** No checksum, no install.

### WI-1.3 Updater integrity: DONE (2026-09-26, uncommitted)
- Investigation: the live feed (`api.github.com/repos/tylermcm/ImageTriage/releases/latest`) publishes `digest: sha256:...` on its MSI asset, so requiring it does not block the current release.
- Change (`updater.py`): `UpdateInfo.is_verifiable` (well-formed 64-hex SHA-256). `download_update_installer` now **refuses before any network call** when it is not, and always verifies the hash after download (mismatch still deletes the partial file). `window.py`: when an update is found but unverifiable, the prompt explains why and links the release page instead of offering a download that would fail.
- Tests: no-checksum is refused with zero network access and no files, malformed checksums are unverifiable, a GitHub payload without a digest is unverifiable, and the window prompt refuses or offers accordingly. Mutation check (removing the guard) fails the test. Suite: 1536 passed, 5 skipped, 15 xfailed, 0 failed.
- Behavioural consequence: a manifest feed (`IMAGE_TRIAGE_UPDATE_FEED_URL`) or an older release with no digest can no longer be installed through the updater. Assets uploaded before GitHub began computing digests would need re-uploading.
- Finding N26: the latest release is tagged `V2` while its asset is `Image.Triage-2.1.1-win64.msi`, and the app is 2.2.1. The updater compares the tag digits, so `V2` reads as 2.0 and would never be seen as newer. Future releases need a `vMAJOR.MINOR.PATCH` tag. Not a code defect, but easy to trip on.

### WI-1.4 Popout header fixture: DONE (completed within WI-0.3)
- The fixture uses `EMPTY_METADATA` and `_update_mockup_image_info` falls back to `EMPTY_METADATA`; `test_preview_polling` passes. Nothing further to do.

### WI-2.2 Repo and asset hygiene: INVESTIGATED (2026-09-26); removals are yours to run
- Every candidate was checked against code, packaging, CI, tests and docs. The verified list with literal commands is in `docs/repo_hygiene_removals.md`. Summary: four unused splash images (`v2`, `v3`, `v6`, plain; 12.6 MB), the root `minus_sign.png` (byte-identical to the asset the code uses), two zips, two empty `ssh_*.txt`, and an empty `package-lock.json` are safe to `git rm`. `heartbutton.png` and `xbutton.png` stay until D13 (used by `scripts/loupe_card_prototype.py`). `verified.png` (root) looks like source art.
- The audit's worry about `splash_background-v4.png` is settled: the app uses `-v7`, `freeze_support.py` stages `-v4`, and the built MSI tree contains every splash file because the whole package folder ships. No packaging bug. The v4 include is redundant (WI-4.6).
- Untracked clutter (your call): a 76 MB unrelated `ES50_EScan2_...exe`, 63 ignored `_tmp_*.png` screenshots, the source-less `image_triage/engine/` folder (only stale `.pyc`), and about 1.1 GB of downloaded models and datasets under `sandboxes/`.
- Status: waiting on you to run the commands. Nothing was deleted.

### WI-2.3 Peel dead code: rounds 1-2 DONE (2026-09-26, uncommitted)
- **Round 1, unused imports: 46 removed, 0 left.** One removal broke 74 tests (`popout_layout_ratios.ratio_px`, re-exported and reached through the alias `popout_ratios`); it was restored and whitelisted, and the analysis now checks for that pattern. The 32 `window.py` imports were mostly the AI-training task classes.
- **Round 2, dead methods: 30 removed** (about 260 lines) from `MainWindow` (12), `FullScreenPreview` (6), `InspectorPanel` (7), `ThumbnailGridView` (3) and `PhotoEditorPanel` (2), plus the cascaded `settings_help_pages` import and three dead `InspectorPanel` signals (`best_of_set_requested`, `open_editor_requested`, `reveal_requested`). Evidence per item: zero references in code, tests, string literals, docs and the non-package trees (`aiculler`, `scripts`, `packaging`, `cli_editor`), and no dynamic method dispatch exists (the two `getattr` loops use attribute names only).
- Report now: 0 unused imports, 19 unreferenced methods, 5 signals, 47 module defs, plus a new write-only-attribute section (11). Suite: 1538 passed, 5 skipped, 15 xfailed, 0 failed, same as before the deletions.
- **Deliberately not removed in 2.3:**
  - **19 methods and 12 module defs** are the AI training, DINO, ranker, checkpoint and reference-bank stack (`_show_legacy_ai_setup_dialog`, `_run_uses_training_paths`, the `ai_training.py` task classes, and so on). They belong to WI-2.4, which validates them against the live AI flows.
  - **8 methods referenced only by tests**, pending a decision on whether those tests are obsolete.
  - **11 write-only `MainWindow` attributes** (`_face_index_*`, `_winner_scores_*`, `_correction_events`, `_command_palette_open`, and so on). Their assignments have multiple sites and some side effects (a DB read for `_correction_events`), so they are residual.
  - **`dispute_*` signals**: the handlers are AI-labelling code, which is WI-2.4.
- WI-2.3 is therefore "done for the generic code" and hands the AI-specific remainder to 2.4. That is a scoping choice, not a change to the plan.

### WI-2.4 pre-implementation investigation (2026-09-26, read-only, no code changed)
Findings that adjust the plan's approach (scope unchanged, method refined):
1. **`dino_prefilter.py` is not wholly dead; it is shared prefilter infrastructure.** `phash_prefilter.py` (live: the pHash cost reducer to preserve) imports `DINOPrefilterDecision`, `_bool_value`, `_clamped_score` and `_preserve_duplicate_group_representatives` from it, and `filtering.py` uses `DINOPrefilterDecision` for the "Prefilter dumped" filter. So the module cannot simply be deleted: the shared decision type and duplicate-representative logic must first be moved to a neutral module (for example `prefilter_common.py`, DINO naming dropped), and only the DINO-specific parts removed (`run_dino_prefilter_from_signal_rows`, `DINOPrefilterSettings`, the audit and report writers, the settings page). Order: extract shared, repoint importers, delete the DINO run stage.
2. **The DINO run is genuinely dormant.** `aiculler_workflow` still accepts `dino_prefilter_settings` and passes `dino_enabled`, but the settings dialog hard-codes `enabled=False`, so the stage never runs. The Filter menu still lists DINO/prefilter entries, enabled only when a folder already has `dino_prefilter` rows on disk.
3. **`FilterMode.DINO_REMOVED/RESCUED` are safe to remove.** `_enum_from_value` falls back to `All` for an unknown value, so an old saved filter cannot crash on load. Your registry export contains no saved filters and no "dino" strings. The plan's "keep the enum values until confirmed" precaution is therefore unnecessary; still update `filtering.py`, `menus.py`, the three `window.py` sites and `tests/test_filtering.py` together.
4. **Existing per-folder data**: `.image_triage_ai/dino_prefilter/*` artifacts on disk must be left untouched (only stop reading them if the filters go). `AI_PREFILTER_DUMPED` is fed by pHash decisions too, so that filter stays.
5. **Dead AI-training stack confirmed**: the 12 task classes in `ai_training.py` (`PrepareLabelingCandidatesTask` ... `BuildReferenceBankTask`, about 1,300 lines) have no importer left after WI-2.3 (only `tests/test_ai_training.py` touches `LaunchLabelingAppTask` and two helpers). `TrainRankerDialog`, `ranker_manager_dialog` and `ai_training_progress_dialog` are exported from `ui/__init__.py` but only the progress dialog is instantiated (by `window.py`), so removal order matters. Keep `global_adapter_labels.sqlite` and registered training sources untouched, as planned.
6. **Recommended sub-steps** (each ends with the full suite green): (a) remove unreachable `window.py` AI methods and the 12 `ai_training.py` tasks with their tests; (b) delete the three dormant dialogs and their `ui/__init__` exports; (c) extract the shared prefilter module and repoint `phash_prefilter`, `filtering`, `aiculler_workflow`; (d) remove the DINO run stage, settings page, filter entries and enum values. Validate with the reachability report, an AI run on a small folder, and pHash prefilter tests.

### WI-2.4 steps (a) and (b): DONE (2026-09-26, uncommitted); steps (c)/(d) paused for an approach decision
- **(a) Unreachable AI code removed.**
  - `window.py`: 18 dead `MainWindow` methods (410 lines): the legacy AI setup dialog, training stats/ranker-diagnosis helpers, checkpoint / reference-bank / signal-weights setters, general-training-source configuration, speed-cull harvest, `_show_ai_menu` and others.
  - `ai_training.py`: 10 task classes (1,317 lines: labeling-app launcher, candidate prep, ranker train/evaluate, signal build/tune, reference bank), then a reachability closure found 47 more dead helpers (1,047 lines) and 9 cascaded imports. **Kept, by your rule:** `list_registered_training_sources`, `set_registered_training_source_enabled` and the whole registered-source registry; `global_adapter_labels.sqlite` was never touched.
  - Tests removed with the code (D2): 3 launcher tests and 6 tests of the deleted ranker/labeling helpers. 9 tests total.
- **(b) Dormant dialogs deleted:** `ui/train_ranker_dialog.py` and `ui/ranker_manager_dialog.py` (761 lines), their `ui/__init__.py` exports and `window.py` imports. `AITrainingProgressDialog` and `AITrainingStatsDialog` stay: they are still instantiated.
- Suite after each step: 1529 passed, 5 skipped, 15 xfailed, 0 failed (was 1538; the 9 fewer are the removed tests). Reachability report: unused imports 0, unreferenced methods 8, module defs 35.
- Ledger rows added in `docs/deletion_ledger.md`.

### Plan discrepancy found in step (d) (needs your decision)
The plan describes the DINO run stage as a self-contained dormant stage. It is not:
- `AICullerRunTask` in `aiculler_workflow.py` has about 600 lines of DINO handling (`_run_dino_prefilter`, extraction cache markers, `_run_dino_command`, runtime validation, `DINOPrefilterRunTask`) interleaved with the **live** pHash logic (`phash_hides_dino_startup`, `_start_deferred_dino_phash`, `_wait_for_phash_prefilter`, stage counts).
- The DINO stage can never run today (the settings dialog hard-codes `enabled=False`), but a wrong cut in the shared branches would change the live pHash prefilter and the AI run flow, and this environment cannot execute a real AI cull (no models or GPU here).
- **Proposed adjustment (smaller steps, same end state):** (d1) remove the user-facing DINO surface only: the DINO controls in Settings, `FilterMode.DINO_REMOVED/RESCUED` with their menu entries and `filtering.py` branches, and the window's `_dino_*` settings load/save. That leaves the run stage unreachable by construction. (d2) Then, after adding a characterization test that pins the pHash-only stage sequence (order, counts, cache markers) on `AICullerRunTask`, remove the DINO code inside `AICullerRunTask` and finally delete `dino_prefilter.py`, first extracting the shared decision type and duplicate-representative helpers that `phash_prefilter.py` and `filtering.py` use. Validate (d2) with a real AI run on a small folder on your machine.

### WI-2.4 (c)/(d): DINO prefilter removed from the source, decoupled from pHash (2026-09-26, uncommitted)
Approved approach (your instruction): decouple entirely and delete, not hide.
- **Safety net first:** `tests/test_char_aiculler_prefilter.py` (6 tests) pins the live pHash-only behaviour of `AICullerRunTask` (stage sequence and totals with and without pHash, stage-enable rules, include-file contents, pHash pool removal, empty pool). It passed before the cut and still passes after it.
- **New neutral module** `image_triage/prefilter_common.py`: `PrefilterDecision` (was `DINOPrefilterDecision`) and the duplicate-representative helpers. `phash_prefilter.py`, `filtering.py`, `grid.py` and `window.py` now use it (`dino_decision` became `prefilter_decision`, `set_dino_prefilter_decisions` became `set_prefilter_decisions`).
- **Deleted:** `image_triage/dino_prefilter.py` (about 470 lines) and `tests/test_dino_prefilter.py`.
- **`aiculler_workflow.py`:** removed the whole DINO stage from `AICullerRunTask` (12 methods, about 600 lines: run, extraction cache markers, command runner, runtime validation, deferred-pHash coupling), the `DINOPrefilterRunTask` class, the `run_dino_prefilter` / `dino_prefilter_settings` / `dino_runtime` parameters, the unused `dino_rank` column and the DINO branch of the include-file writer. `_prefilter_stage_flags` (a tuple) became `_phash_stage_enabled` (a bool).
  - **One deliberate on-disk change:** the always-written `aiculler_include_paths.txt` used to live inside the `dino_prefilter` artifact folder; it now lives in the run's artifacts folder. It is regenerated on every run, so nothing needs migrating.
  - Renamed the misnamed `has_dino_clusters` to `has_semantic_clusters` (and its warning text).
- **`window.py`:** removed the DINO settings load/save and keys, the run/finish/delete/open-settings methods, the hidden DINO worker-count setting, the DINO reset-cache option, DINO probing and toolbar enabling, and the DINO filter plumbing. **Registry values are untouched; they are simply no longer read.**
- **`settings_dialog.py`:** removed the hidden DINO page, its controls and the DINO worker control. **`ai_workflow_center.py`:** removed the DINO step, which was already popped before display. **`models.py` / `menus.py`:** removed `FilterMode.DINO_REMOVED` / `DINO_RESCUED` and the menu entry. A saved filter that still holds "DINO Removed" loads as All (pinned by a new test).
- **Tests:** 4 DINO workflow tests, 2 DINO worker tests and 1 DINO settings round-trip test removed; the DINO filter test became a pHash one. Suite: **1514 passed, 5 skipped, 15 xfailed, 0 failed.** Net change since the last commit: 21 files, about 6,500 lines deleted.

### What still says "dino" and why it is a different job
About 40 mentions remain in `window.py`, `ai_runtime_packages.py`, `ai_manifest.py`, `ai_model.py`, `ai_env.py`, `ai_probe.py`, `packaging/ai_runtime_installer.py`, `scripts/refresh_ai_runtime_lock.py`, `freeze_support.py`, `README.md` and about 40 in tests. These are **not a feature any more**; they are the AI runtime package installer where "dino" is the name of the optional PyTorch/transformers stack (`include_dino`, `--no-dino`, `dino_enabled_variants`, `-base` versus full lock files, the `DinoV3` model bundle, `dino_installed_variants`). The stack is also what the editor masking engines need, so the fix is a **rename plus a migration**, not a deletion: `include_dino` becomes something like `include_torch_stack`, and the persisted `dino_enabled_variants` key and existing lock-file names must keep working for runtimes already installed on machines. It changes what gets installed and how the MSI stages it, so it needs a real MSI build and the clean-machine matrix; that is why it belongs to WI-2.5/2.6 packaging, not here.
- Also noted: `_delete_phash_prefilter_artifacts` is now unreferenced. Its only entry point was the dead DINO step, so pHash artifacts have had no working delete button all along (pre-existing). Decide whether to wire it up or delete it.

### Decisions (2026-09-26, third batch) and WI-2.4 close-out
- The DINO runtime/installer naming (`include_dino`, `dino_enabled_variants`, `-base` locks, the `DinoV3` bundle) moves into **WI-2.5/2.6** with the rest of the legacy-runtime and packaging work, where an MSI build and the clean-machine matrix can validate the rename and its migration.
- `_delete_phash_prefilter_artifacts` deleted (dead; "Reset AI Cache" covers pHash artifacts). Suite unchanged: 1514 passed, 5 skipped, 15 xfailed, 0 failed.
- **WI-2.4 is complete** except for the runtime-installer naming handed to 2.5/2.6.

### WI-2.5 Carve out ai_workflow.py: steps (1)/(3) DONE, step (2) deferred (2026-09-26, uncommitted)
- **Finding:** `AIRunTask` (the old AICullingPipeline runner, 421 lines) is never instantiated anywhere; `window.py` only imported it and used it in one type annotation. Its whole support stack (staging, command runners, metrics formatting, log paths, retry helpers) was therefore dead.
- **Method:** a reachability closure from every name production code (outside the file) actually uses, ignoring `window.py`'s import list, which imports names it never uses.
- **Deleted from `image_triage/ai_workflow.py`:** 53 top-level names (about 1,355 lines), including `AIRunTask`, `AIRunSignals`, `AIRunCancelled`, `stage_supported_images`, `rewrite_extraction_artifact_paths`, `_run_command_with_live_output`, `_format_ai_metric_detail`, `_inject_ai_runtime_pythonpath`, `build_ai_stage_cache_keys`, `ai_cluster_artifacts_ready` and the constants only they used, plus 8 cascaded imports. **The file went from 2,096 to 733 lines.** `window.py`'s dead imports were removed and `_active_ai_task` is now typed `AICullerRunTask | None`.
- **Kept (live):** `AIWorkflowPaths` and `build_ai_workflow_paths` (on-disk layout unchanged), `AIWorkflowRuntime` and `default_ai_workflow_runtime`, the artifact-ready checks, `reset_hidden_ai_review_cache`, `existing_hidden_ai_report_dir`, `ai_device_environment_override`, `resolve_ai_python_script_command`, the metrics-line parsers, `_path_signature`, `_mark_hidden` / `FILE_ATTRIBUTE_HIDDEN`, and the constants they need.
- **Tests removed with the code:** 13 in `test_ai_workflow.py` (staging, command runner, stage failure messages, worker helpers, semantic cache key) and 2 stale xfailed tests in `test_window_catalog_cache.py`. Suite: **1501 passed, 5 skipped, 13 xfailed, 0 failed.** `FAILURE_LEDGER.md` updated.
- **Deferred (needs a decision):** plan step (2), moving device selection and model-install state onto the CLI-Culler runtime. `AIWorkflowRuntime` turned out to be **live infrastructure, not legacy**: `mask_engine_service`, `semantic_mask_service` and `subject_masks` use it to find the managed Python and device, and `window.py` reads it in about 20 places (model installation status, checkpoint, device, Python path, diagnostics text). Re-homing it is a real refactor of the editor-masking path, not a deletion. Also deferred: physically splitting the remainder into a smaller module (the file is now 733 lines and cohesive, so a rename would be churn without benefit).
- **Result vs the plan's "Done" criterion:** no live code imports the old runner code; the AICullingPipeline command runners are gone from `image_triage`. WI-2.6 (decommissioning the `AICullingPipeline/` folder and its packaging) is now unblocked.

### WI-2.6 Decommission AICullingPipeline/: source removed, packaging decoupled (2026-09-26, uncommitted; **MSI build not run**)
- **S1 packaging and CI decoupled (before deleting anything):**
  - `freeze_support.py`: removed the legacy engine staging entirely (`stage_ai_runtime`, the `ai_runtime` MSI include, `AI_STAGE_ROOT`, `IMAGE_TRIAGE_AI_SOURCE` / `AICULLING_ENGINE_ROOT` discovery, the local-backbone and default-ranker flags, the script-bootstrap injection; 136 lines). `FreezeAssetLayout` lost its `ai_source` and `ai_stage_root` fields. **Kept, because the managed torch runtime still needs them:** `ai_stdlib`, `ai_python_dlls`, the optional `ai_site_packages` bundle, `packaging/ai_runtime_locks`, the worker scripts, `aiculler` and `photo_terminal`.
  - `build-windows-msi-release.yml`: removed the "Verify bundled AI runtime source" step that required `AICullingPipeline/app|configs|scripts`. `install_linux.sh`: removed the `IMAGE_TRIAGE_AI_SOURCE` option. `.gitignore`: removed the two engine patterns. `README.md` updated.
  - `test_freeze_support.py`: rewritten for the new layout, plus a new test that the layout no longer ships `ai_runtime`.
- **S3 legacy tests deleted (D2):** `test_decision_harvest`, `test_dinov2_extractor`, `test_extract_entrypoint`, `test_image_loading`, `test_labeling_data_quality`, `test_ranker_disagreement_training`, `test_ranking_dino_fallback` (about 1,470 lines) and one labeling-UI test in `test_ai_training`.
- **S4 engine source deleted:** `git rm -r AICullingPipeline` removed all 90 tracked files (app, engine, ranking, labeling, scripts, configs, legacy scripts). Suite: **1466 passed, 3 skipped, 13 xfailed, 0 failed.**
- **Left for you (untracked, ignored, not code):** the `AICullingPipeline/` folder still holds 7 real files: the two trained ranker checkpoints in `outputs/china26_full/ranker_run_mlp_100ep/` (your own training output), the 660 MB DINOv2 model folder `vit_base_patch14_dinov2.lvd142m/`, and 186 stale `.pyc` files. Paths are in the report.
- **Not done in 2.6 yet:**
  1. `AIWorkflowRuntime` still has the engine fields (`engine_root`, config paths, a `validate()` nothing calls) and searches for an engine folder that no longer exists (harmless: it falls back to a nonexistent path). Trimming them touches the ranker-checkpoint chain in `window.py` (`_apply_saved_ai_training_preferences`, `_current_trained_checkpoint_path`, `_default_ai_checkpoint_path`, a diagnostics text block). That chain is legacy ranker code that the reachability tool cannot flag because its pieces call each other; it needs its own trace.
  2. The DINO runtime-installer rename (`include_dino` and friends) approved for this item.
  3. **Acceptance gate (you):** a real MSI build, an installed-app AI cull, and the clean-machine matrix. Nothing in this batch has been verified against an actual MSI.

### WI-2.6 continued: legacy ranker chain and runtime engine fields removed (2026-09-26, uncommitted)
- **`window.py`:** removed the ranker/checkpoint chain (`_apply_saved_ai_training_preferences`, `_active_ranker_run`, `_general_training_pool_status`, `_general_training_source_folders`, `_ai_training_paths_for_folder`, `_general_ai_training_paths`, `_current_trained_checkpoint_path`), their three settings-key constants, the probe entry, and the legacy lines of the AI-status tooltip (Engine, Checkpoint, Embedding workers, Local staging, Semantic sidecar, Reference bank, Stage root). **Kept, because it is live:** the AI training progress machinery (`_start_ai_training_task` and its handlers), which adapter training still uses.
- **`ai_training.py`:** the 18 ranker-run and pool-status definitions that became unreachable (449 lines). The file is now 771 lines (about 3,600 at the start of the cleanup). **The registered-training-source registry is untouched.** 2 tests of the deleted ranker-run/pool code removed.
- **`ai_workflow.py`:** `AIWorkflowRuntime` trimmed to the fields anything reads (`python_executable`, `model_name`, `model_installation`, `device`, `batch_size`, `semantic_model_name`). Removed the engine root, config paths, ranker checkpoint and download URL, staging, worker-count and sidecar fields, the never-called `validate()`, the engine-folder search, and 11 now-dead helpers. The file is now **532 lines (from 2,096)**. 6 tests of that legacy resolution removed.
- **Bug caught on the way:** my test-pruning script rewrote an import under the wrong module name once; it failed loudly at collection and was fixed before anything ran.
- Suite: **1459 passed, 3 skipped, 12 xfailed, 0 failed.** `FAILURE_LEDGER.md` updated.
- **WI-2.6 remaining:** the DINO runtime-installer rename (`include_dino` and friends) and your acceptance gate (a real MSI build, an installed-app AI cull, the clean-machine matrix).

### DINO wiped from the source (2026-09-26, uncommitted; MSI build not run)
Your instruction: no DINO anywhere in the code. Result: **`git grep -i dino` finds nothing in `image_triage/`, `packaging/`, `scripts/`, `freeze_support.py`, the CI workflows, README or the website.** What remains is only two test guards (see below) and dated historical documents.
- **The runtime installer's "dino" was really the PyTorch stack.** Renamed throughout: `include_dino` to `include_torch`, `dino_installed_variants` to `torch_installed_variants`, `dino_enabled_variants` (the persisted metadata key) to `torch_enabled_variants`, `AI_RUNTIME_DINO_*` to `AI_RUNTIME_TORCH_*`, the installer flag `--no-dino` to `--no-torch`, and the lock-refresh script flags `--no-dino` / `--dino-only` to `--no-torch` / `--torch-only`. No compatibility shim: metadata without the new key already defaults to "every installed variant has the PyTorch stack", so an existing standard runtime keeps working. **Edge case:** a runtime that was deliberately installed as base-only (`--no-dino`) will be seen as missing its PyTorch parts and offered a reinstall.
- **The DINOv3 model itself deleted:** its manifest bundle and checksums (1.2 GB weights), its capability and the `OPT_IN_CAPABILITIES` concept, the DINO default resolver and constants in `ai_model.py` (the generic `download_ai_model` and installation type stay: other models use them), the DINO probe path in `ai_probe.py` (the whole unreachable tail of `probe_torch_model`), the `AIWorkflowRuntime.model_name` / `model_installation` fields, and in `window.py` the DINO download request, setup-flow flags (always False), status methods and wording.
- **Also removed as dead:** 7 unreferenced methods flagged since WI-2.3 (five prompt-text helpers, `_path_state_cache_token`, `InspectorPanel._quick_button`). Website copy no longer advertises a DINO prefilter (it now says "Duplicate prefilter").
- **Tests:** DINO fixtures renamed; 4 tests deleted (default-DINO resolution x2, base-runtime DINO capability guard, settings-page absence guard); `test_ai_model` builds its installations directly. **Kept on purpose:** the two `assertNotIn("DINO", ...)` UI guards in `test_current_ai_workflows` and the test that a saved filter containing `"DINO Removed"` falls back to All (that one protects users' old saved filters). Suite: **1455 passed, 3 skipped, 12 xfailed, 0 failed.**
- **Left as history:** `docs/`, the root `HANDOFF_*` / `REVIEW_*` files and `docs/ai_v4_plan.md` mention DINO in past-tense analysis. They describe what was true when written.
