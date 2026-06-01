## AI Integration

The host app now supports the in-repo CLI-Culler workflow:

- CLI-Culler inference and ranked report loading through the AI menu
- adapter label review, training, evaluation, and rescoring from the app menus

The app still stays human-in-control. It helps prepare and use the model, but it does not automatically make keep/reject decisions for you.

### Hidden AI Workspace

For automated AI work, the host app stores data inside a hidden folder next to the selected image folder:

- `.image_triage_ai/artifacts`
- `.image_triage_ai/ranker_report`
- `.image_triage_ai/training`
- `.image_triage_ai/evaluation`

The older AICullingPipeline backend remains in the repository for now, but the active GUI workflow shells out to the in-repo `aiculler/cli.py` package and writes GUI-compatible exports into this hidden workspace. Model weights stay outside git and can be pointed at with `IMAGE_TRIAGE_AICULLER_MODEL_ROOT`.

### What The App Can Do

The host app can:

- load existing AI exports with `Load AI Results...`
- run ingest, semantic categorization, clustering, scoring, and report export with `AI -> Run AI Culling`
- reopen the hidden cached ranked report for the current folder with `AI -> Load Saved AI For Folder`
- review adapter label candidates with `AI -> Adapter -> Review Adapter Labels`
- manage adapter data selection with `AI -> Adapter -> Data Selection / Ranking...`
- train a new adapter with `AI -> Adapter -> Train Ranker...`
- evaluate the active adapter with `AI -> Adapter -> Evaluate Trained Ranker`
- rescore the current folder with `AI -> Adapter -> Score Current Folder With Trained Ranker`

### What The App Displays

When an AI export is loaded, matching images gain read-only AI context in these places:

- thumbnail overlays: AI score badge and AI top-pick badge
- thumbnail metadata line: normalized AI display score, group id, and group rank
- preview footer: normalized AI display score, group id, rank, and top-pick note
- summary and status text: matched-image counts and selected-image AI details

### Mapping Strategy

The host app matches AI rows to image records by normalized absolute file path. This keeps the integration independent from internal artifact details while still preserving stable engine `image_id` values inside the loaded export rows.

The host-side adapter code lives in:

- `ai_results.py`
- `aiculler_workflow.py`
- `ai_workflow_center.py`

### Current Scope

This integration is intentionally local-first and operator-driven. It does not:

- reorder normal browsing by default
- auto-accept or auto-reject images
- replace the external labeling UI with an in-app trainer
- manage remote or cloud training jobs
- merge labels across folders or multiple users automatically
