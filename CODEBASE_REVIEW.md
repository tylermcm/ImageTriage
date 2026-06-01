# Codebase Review

## Project Overview

`Image Triage` is a desktop image-review application built with Python and PySide6. The codebase is centered on fast folder-based culling, with a custom virtualized thumbnail grid, full-screen preview/compare workflow, persistent review annotations, XMP sidecar sync, catalog/collection features, batch file operations, and an increasingly large AI-assisted review and training surface.

At a repository level, this is a single-package application:

- Main package: `image_triage/`
- UI support package: `image_triage/ui/`
- Tests: `tests/`
- Entry point: `pyproject.toml` -> `image-triage = image_triage.main:main`

The product direction in `README.md`, `PRODUCT_ROADMAP.md`, `POWER_TOOLS_PLAN.md`, and `UI_OVERHAUL_PLAN.md` is coherent. The implementation issue is not lack of ambition; it is that the architecture is already straining under the breadth of features.

## Architecture Summary

### Runtime structure

- `image_triage/main.py` bootstraps Qt and launches `MainWindow`.
- `image_triage/window.py` is the orchestration hub for nearly every feature: folder loading, filtering, annotations, file operations, workflow exports, AI runs, AI training, catalog actions, dialog launching, preview coordination, and status/progress UI.
- `image_triage/grid.py` implements the custom virtualized thumbnail grid.
- `image_triage/preview.py` implements the full-screen preview/compare/speed-cull experience.

### Core domain and infrastructure modules

- Scanning and records: `image_triage/models.py`, `image_triage/scanner.py`
- Thumbnail/image pipeline: `image_triage/thumbnails.py`, `image_triage/cache.py`, `image_triage/imaging.py`, `image_triage/metadata.py`
- Persistence: `image_triage/decision_store.py`, `image_triage/scan_cache.py`, `image_triage/xmp.py`, `image_triage/library_store.py`
- Batch/file workflows: `image_triage/file_ops.py`, `image_triage/image_resize.py`, `image_triage/image_convert.py`, `image_triage/archive_ops.py`, `image_triage/workflows/`
- AI/review layer: `image_triage/ai_results.py`, `image_triage/review_intelligence.py`, `image_triage/review_workflows.py`, `image_triage/ai_workflow.py`, `image_triage/ai_training.py`
- UI composition/helpers: `image_triage/ui/actions.py`, `image_triage/ui/menus.py`, `image_triage/ui/toolbars.py`, `image_triage/ui/docks.py`, `image_triage/ui/theme.py`, plus many dialogs

### Architectural assessment

The package boundaries are understandable, but the control flow is too centralized. `image_triage/window.py` is effectively the application layer, state store, workflow coordinator, background-job manager, and a large part of the domain layer. That concentration is the dominant architectural weakness in the repository.

## Hotspots / Bottlenecks

### 1. `MainWindow` is the primary bottleneck for maintainability and change velocity

- `image_triage/window.py` is roughly 8.5k+ lines and imports almost every subsystem.
- It owns too much mutable state directly and manually synchronizes related caches, counts, progress dialogs, and view refreshes.
- The cost is not just readability. Every new feature increases the chance of incidental regressions because unrelated workflows share the same mutable object.

### 2. Record-view recomputation is heavier than it needs to be

- `image_triage/window.py:8971` (`_apply_records_view`) fully resorts, refilters, rebuilds indexes, recalculates counts, refreshes AI summaries, resets grid items, rebuilds preview-group indexes, and refreshes burst grouping in one pass.
- `image_triage/grid.py:254` (`set_items`) clears the pixmap cache, resets selection/current index, rebuilds visible items, and immediately re-requests thumbnails.
- This means metadata updates, annotation toggles, and many UI actions trigger more full-list work than necessary.

### 3. Folder load does synchronous work on the UI path

- `image_triage/window.py:6158` (`_apply_loaded_records`) immediately loads sidecar annotations and decision-store annotations for the full record set.
- `image_triage/xmp.py:40` (`load_sidecar_annotations`) loops every record and parses XMP files synchronously.
- On larger folders or slow storage, this will directly affect perceived folder-open latency even before review intelligence kicks in.

### 4. Review intelligence is expensive and auto-starts for every loaded folder

- `image_triage/window.py:6180` starts `BuildReviewIntelligenceTask` automatically after records load.
- `image_triage/review_intelligence.py:139` builds fingerprints for every record.
- `image_triage/review_intelligence.py:251` loads preview-sized images and computes detail/exposure stats per record.
- `image_triage/review_intelligence.py:306` can hash full files for exact-duplicate detection.
- This is useful functionality, but it is one of the most expensive pipelines in the app and currently has no user-facing progress, no cancellation, and no incremental behavior.

### 5. Annotation hot paths do too much synchronous filesystem work

- `image_triage/window.py:7964` and `image_triage/window.py:8042` toggle winner/reject state.
- Those paths repaint the grid, call `QApplication.processEvents()`, may copy/link files into `_winners`, persist SQLite state, sync XMP sidecars, capture feedback events, and then rebuild the full records view.
- `image_triage/window.py:8498` (`_persist_annotation`) performs both database persistence and XMP sync synchronously.
- `image_triage/xmp.py:61` and `image_triage/xmp.py:123` write XML sidecars immediately.
- For a keyboard-first culling app, the winner/reject path should be as cheap as possible. It currently is not.

### 6. Catalog refresh is brute-force

- `image_triage/library_store.py:389` rescans each catalog root from scratch.
- `image_triage/library_store.py:451` deletes all rows for a root and reinserts the complete snapshot.
- `image_triage/library_store.py:743` walks every directory recursively.
- `image_triage/library_store.py:499` uses plain `LIKE` matching rather than FTS.
- This will work for modest libraries, but it does not scale gracefully.

### 7. AI staging and subprocess handling are expensive in avoidable ways

- `image_triage/ai_workflow.py:314` stages AI input by recursively walking and copying supported images.
- `image_triage/ai_workflow.py:365` copies staged files one by one with `shutil.copy2`.
- `image_triage/ai_workflow.py:580` reads subprocess output one character at a time.
- The AI path is already heavy; the current implementation adds extra overhead on top.

### 8. Memory pressure is easy to accumulate

- `image_triage/cache.py` defaults thumbnail memory cache to 256 MB.
- `image_triage/grid.py:96` keeps a 192 MB pixmap cache.
- `image_triage/preview.py:449` keeps a 320 MB preview cache.
- `image_triage/preview.py:459-460` also keeps additional inspection/focus-assist caches.
- In practice, the application can hold several hundred megabytes of image data before accounting for decoded raw images, Qt overhead, and AI/reporting state.

## Code Quality Issues

### 1. Large god objects and feature concentration

- `image_triage/window.py`, `image_triage/preview.py`, and `image_triage/grid.py` are all large custom widgets with a lot of domain logic embedded in UI classes.
- This makes the code harder to test, harder to reason about, and slower to refactor safely.

### 2. Extensive manual state coordination

- The app frequently mutates raw dicts/lists/sets and then manually calls a sequence of refresh methods.
- Examples: `_annotations`, `_records`, `_all_records_by_path`, `_workflow_insights_by_path`, `_filter_metadata_by_path`, `_burst_group_map`, `_undo_stack`, `_recent_destinations`, etc.
- This style works early, but it becomes fragile once features overlap.

### 3. Reentrancy risk from `QApplication.processEvents()`

- `image_triage/window.py` uses `QApplication.processEvents()` in multiple task/progress and review paths.
- That is often a sign the control flow is fighting the framework rather than working with it.
- It also increases the chance of hard-to-debug state races and reentrant UI behavior.

### 4. Repository hygiene is weak

- `.gitignore` only ignores two input folders.
- The repository contains committed `__pycache__/` directories under `image_triage/` and `tests/`.
- The repo root also contains temporary `_tmp_ui_*.png` assets.
- These are small issues individually, but together they signal that generated artifacts are not clearly separated from source.

### 5. Runtime verification is under-supported in this repo snapshot

- The project requires Python `>=3.11`, but the available interpreter in this environment was Python 3.9.13, so `python -m unittest discover -q` failed immediately on `@dataclass(slots=True)`.
- Optional runtime dependencies were also incomplete here (`rawpy`, `exifread`, and `py7zr` missing).
- This is partly environment-specific, but the repo would benefit from a clearer contributor setup path and a test command that fails earlier and more explicitly.

### 6. Test coverage does not match risk concentration

- Only 5 test modules exist.
- The tests cover some pure logic (`library_store`, `review_intelligence`, `review_workflows`, `workflows`, `ai_results`).
- There is no meaningful automated coverage for the highest-risk areas:
  - `window.py`
  - `grid.py`
  - `preview.py`
  - `scanner.py`
  - `imaging.py`
  - `xmp.py`
  - `file_ops.py`
  - `ai_workflow.py`

## Redundancy Issues

### 1. Repeated task/progress-dialog plumbing in `window.py`

- `window.py` contains near-identical start/progress/finished/failed/show/close patterns for:
  - batch rename
  - resize
  - convert
  - workflow export
  - catalog refresh
  - archive create/extract
  - AI training
- This is a strong candidate for a reusable job-controller abstraction.

### 2. Repeated state-reset blocks

- Similar "clear current records/caches/counts" blocks appear in multiple places, including folder-scan empty-state and failure paths (`image_triage/window.py:6105-6114`, `image_triage/window.py:6276-6285`).
- Repetition here makes it easy for one reset path to drift away from another.

### 3. Convert reuses private resize internals

- `image_triage/image_convert.py` imports `_load_resize_image`, `_save_resized_image`, and `_path_key` from `image_triage/image_resize.py`.
- This is a code smell: the shared logic is real, but the boundary is wrong. It should be promoted into a public shared image-export module.

### 4. Menu duplication creates both maintenance and UX duplication

- `image_triage/ui/menus.py` exposes overlapping AI-training actions in both `&AI` and `&Tools`.
- The duplication is not catastrophic, but it increases menu noise and doubles maintenance effort for related actions.

## Quick Wins

1. Expand `.gitignore`, remove committed `__pycache__/`, and move temp design assets out of the repo root.
2. Extract a reusable background-job helper for `QRunnable` wiring, progress dialogs, and result handling.
3. Stop doing immediate XMP writes on every review toggle; queue/debounce sidecar sync and batch database writes.
4. Avoid calling `grid.set_items()` for metadata-only changes; update only the affected rows or derived labels.
5. Add cancellation support to long-running jobs instead of showing non-cancelable progress dialogs.
6. Replace character-by-character subprocess reads in `ai_workflow.py` with line-based streaming.
7. Replace hardcoded AI defaults such as `C:\Users\tylle\...` with explicit configuration or first-run setup.
8. Add a contributor check script that validates Python version and optional dependencies up front.

## Medium-Term Refactors

### 1. Split `MainWindow` into feature controllers

Suggested seams:

- folder/session state controller
- annotation/review actions controller
- background jobs controller
- library/catalog controller
- AI runtime/training controller
- batch tools/workflow export controller

The goal is not more files for their own sake. The goal is to stop centralizing every workflow in one class.

### 2. Introduce a clearer application state model

The current mutable-state approach is workable but error-prone. A dedicated state object or view-model layer for:

- current scope
- loaded records
- active annotations
- filter query
- AI bundle/review intelligence
- active job state

would reduce the amount of manual synchronization now scattered through `window.py`.

### 3. Build a shared image-transform pipeline

Unify the image loading/saving/metadata handling used by:

- display thumbnails/previews
- resize
- convert
- workflow export

This would remove private cross-module imports and make future format support easier to extend.

### 4. Make cataloging incremental

The catalog should move toward:

- folder signatures / mtimes
- partial refreshes
- changed-folder detection
- FTS-backed search instead of repeated `%LIKE%`

That would make the feature much more viable on real libraries.

### 5. Make review intelligence optional, incremental, and cancelable

This pipeline should not feel like a hidden second scan. It needs:

- progress reporting
- cancellation
- reuse of existing metadata/thumb caches where possible
- incremental refresh for changed folders rather than full recomputation

## Prioritized Action Plan

1. Break up `window.py` first, starting with job handling and annotation/file-operation flows. This is the biggest leverage point for future work.
2. Make the review hot path cheap: batch SQLite writes, defer XMP sync, and stop forcing full-grid/view recomputation after every toggle.
3. Reduce folder-load overhead by moving sidecar parsing and review-intelligence work off the critical path and making it incremental.
4. Replace repeated progress-dialog/task plumbing with a single reusable job framework that also supports cancellation.
5. Fix the image export architecture by extracting shared resize/convert/export internals into a public module.
6. Rework catalog refresh/search for scale: incremental indexing plus FTS.
7. Remove machine-specific AI defaults and improve environment/bootstrap diagnostics.
8. Improve repository hygiene and widen automated test coverage around scanner/imaging/file-op/UI-adjacent logic.

## Bottom Line

The codebase is not directionless. It already contains a substantial amount of useful functionality and a clear product vision. The main problem is architectural concentration: too much of the application is routed through a few oversized UI classes and synchronous hot paths. If the next refactor cycle focuses on `window.py`, annotation persistence, and long-running job infrastructure, the rest of the roadmap will become much easier to deliver safely.
