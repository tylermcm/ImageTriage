## AI Integration

The host app can now load AI outputs from the `AICullingPipeline` project without changing the existing triage workflow.

It also includes an `AI Culling` mode that can run the current AI pipeline for the selected folder, save results into a hidden per-folder cache, and automatically reopen the ranked export in the host app.

### What The Host App Reads

Use the `AI Results...` button in the toolbar and choose an AI export folder.

For the automated path, open the `AI Culling` mode and use `Run AI Culling`. The host app writes results into a hidden folder inside the selected image directory:

- `.image_triage_ai/artifacts`
- `.image_triage_ai/ranker_report`

When the selected folder lives on a remote or removable drive, the host app may first stage the supported image files into a local SSD scratch area under `%LOCALAPPDATA%` for faster embedding extraction. This staging cache is persistent per source folder, so unchanged files can be reused across runs instead of being recopied every time. The saved AI artifacts are still written back to the hidden folder beside the original images, and exported file paths are rewritten to the original image directory so the host app matches them normally.

The loader looks for a ranked export in the selected folder or one of its immediate child folders. Preferred filenames are:

- `ranked_clusters_export.csv`
- `scored_clusters.csv`
- `ranked_clusters.csv`

If present, the host app also discovers:

- `ranked_export_summary.json`
- `ranked_clusters_report.html`

### What The Host App Displays

When an AI export is loaded, the app remains fully usable if some or all images have no AI match. Matching images gain read-only AI context in these places:

- thumbnail overlays: AI score badge and AI top-pick badge
- thumbnail metadata line: normalized AI display score, group id, and group rank
- preview footer: normalized AI display score, group id, rank, and top-pick note
- AI Culling viewport: the existing grid can switch into clustered report-style sections with rank badges and normalized `0-100` per-group scores
- summary/status text: matched-image count and selected-image AI details

### AI-Assisted Workflow Helpers

The host app now exposes a small set of advisory AI helpers without changing the normal triage workflow:

- `View -> AI Top Picks`: show only model-recommended keepers from multi-image groups
- `View -> AI Grouped`: show only images that belong to multi-image AI groups
- `AI Results... -> Next AI Top Pick`: jump to the next model-recommended keeper in the current view
- `AI Results... -> Next Unreviewed AI Top Pick`: jump to the next unreviewed model recommendation
- `AI Results... -> Jump To AI Top Pick In Group`: move from the current group member to the model's group winner
- `AI Results... -> Compare Current AI Group`: open the current AI group as a compare set in preview
- `AI Culling -> Run AI Culling`: run extraction, grouping, scoring, and report export automatically for the current folder
- `AI Culling -> Load Saved AI`: reopen the hidden cached results for the current folder without rerunning the model
- `AI Culling` progress bar: shows live image counts and ETA during staging/scanning/extraction when available, and finishes on `Done` when a fresh run completes

These helpers are intentionally advisory. They do not auto-accept, auto-reject, or reorder the underlying folder scan.

The core manual browsing path stays separate from the AI pipeline:

- folder scanning and thumbnail browsing do not require AI results
- AI bundle lookups are cached before being used by paint-time UI code
- saved AI caches are only probed when the AI mode is active or when AI loading is requested explicitly

### Mapping Strategy

The host app matches AI rows to image records by normalized absolute file path. This keeps the integration independent from internal artifact details while still preserving stable engine `image_id` values inside the loaded export rows.

The host-side adapter code lives in:

- `ai_results.py`

### Behavior When AI Data Is Missing

If no AI export is loaded, or if the chosen export does not match the current folder, the app simply shows no AI overlays and continues to behave normally.

### Current Scope

This integration is intentionally human-in-control. It does not:

- reorder browsing
- auto-accept or auto-reject images
- change compare flow
- write feedback back into the AI engine

### Next Stage

The next integration step will likely connect AI-assisted review actions to feedback capture and tighter host-app workflow integration.
