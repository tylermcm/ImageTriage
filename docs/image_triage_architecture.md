# Image Triage Architecture

This document is the shortest useful map of the application.

## 1. Runtime Shape

Image Triage is a desktop Qt application that stays folder-first:

- the main window loads one real folder at a time
- review state is stored per session and per folder
- optional catalog/index layers accelerate reopen, search, and AI reuse
- AI, review grouping, and handoff workflows are helpers around that folder-first loop

The main entry point is:

- [main.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/main.py)

The main coordinator is:

- [window.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/window.py)

## 2. Core Data Flow

### Folder review path

1. The user opens a folder.
2. [scanner.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/scanner.py) builds `ImageRecord` bundles.
3. [catalog/repository.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/catalog/repository.py) may hydrate cached records first.
4. [grid.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/grid.py) renders the bundle grid and requests thumbnails.
5. [preview.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/preview.py) renders focused inspection views.
6. [decision_store.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/decision_store.py) and XMP helpers persist review state.

### Review intelligence path

1. [review_intelligence.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/review_intelligence.py) computes duplicate/similarity groupings.
2. [review_workflows.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/review_workflows.py) derives burst recommendations and taste-profile adjustments.
3. Cached results are stored in [catalog/repository.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/catalog/repository.py) so large folders can reopen without recomputing everything.

### AI culling path

1. [aiculler_workflow.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/aiculler_workflow.py) shells out to the in-repo [aiculler package](C:/Users/tylle/OneDrive/Documents/Playground/aiculler/cli.py) for ingest, semantic grouping, adapter training, and ranking.
2. [ai_results.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/ai_results.py) loads ranked AI output back into app-native structures.
3. [ai_workflow_center.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/ai_workflow_center.py) presents the active operator workflow.
4. [ai_workflow.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/ai_workflow.py) and [ai_training.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/ai_training.py) are still present for the older AICullingPipeline/backend path and packaging support.

### Workflow / handoff path

1. Recipes and presets live in:
   - [workflows/models.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/workflows/models.py)
2. Export planning/execution lives in:
   - [workflows/export.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/workflows/export.py)
3. Best-of shortlist planning lives in:
   - [workflows/best_of.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/workflows/best_of.py)
4. Persistence and path helpers live in:
   - [workflows/storage.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/workflows/storage.py)
   - [workflows/paths.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/workflows/paths.py)

## 3. Caching Layers

The app intentionally has multiple cache layers because they solve different problems:

- in-memory view caches:
  - fast UI redraws while a window is open
- disk thumbnail / preview caches:
  - avoid re-decoding the same images repeatedly
- catalog SQLite cache:
  - persists folder records, review grouping, review scoring, AI bundles, AI workflow stage reuse

The catalog cache is the durable backend cache. It replaced the old JSON folder cache.

## 4. Main Orchestrators

If you are trying to follow behavior end to end, start here:

- [window.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/window.py): UI orchestration, commands, task lifecycle, folder loading
- [grid.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/grid.py): record tiles, selection, zoom, visible thumbnail work
- [preview.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/preview.py): detailed image inspection and preview controls
- [library_store.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/library_store.py): virtual collections and cross-folder library state
- [catalog/repository.py](C:/Users/tylle/OneDrive/Documents/Playground/image_triage/catalog/repository.py): persistent cache and query surface

## 5. Design Constraints

The codebase is intentionally opinionated about a few things:

- folder-first review remains the primary workflow
- AI and catalog features must not make the basic viewer feel mandatory or cloud-like
- expensive work should either:
  - run in background tasks, or
  - come from the catalog cache
- Windows desktop UX matters:
  - file associations
  - external editor launch
  - MSI packaging
  - responsiveness during large folder loads

## 6. Where To Extend

If you need to add new behavior, prefer these seams:

- new media/metadata handling:
  - provider registry used by imaging and metadata
- new review grouping/scoring logic:
  - plugin seams around review intelligence and review workflows
- new workflow recipe behavior:
  - `image_triage.workflows`
- new persistent cache:
  - `CatalogRepository`

Avoid putting new backend policy directly into `window.py` unless the code is truly UI-specific.
