## AI Integration

The host app now supports both sides of the local `AICullingPipeline` workflow:

- inference and ranked report loading inside `AI Culling`
- training-data prep, labeling, training, evaluation, and rescoring from the app menus

The app still stays human-in-control. It helps prepare and use the model, but it does not automatically make keep/reject decisions for you.

### Hidden AI Workspace

For automated AI work, the host app stores data inside a hidden folder next to the selected image folder:

- `.image_triage_ai/artifacts`
- `.image_triage_ai/ranker_report`
- `.image_triage_ai/labels`
- `.image_triage_ai/training`
- `.image_triage_ai/evaluation`
- `.image_triage_ai/reference_bank`

When the selected folder lives on a remote or removable drive, the app may first stage supported image files into a local SSD scratch area under `%LOCALAPPDATA%` for faster embedding extraction. The saved AI artifacts are still written back into the hidden folder beside the original images, and exported paths are rewritten to the original folder so matching continues to work normally.

### What The App Can Do

The host app can:

- load existing AI exports with `Load AI Results...`
- run extraction, clustering, scoring, and report export with `AI -> Run AI Culling`
- reopen the hidden cached ranked report for the current folder with `AI -> Load Saved AI For Folder`
- prepare training data for the current folder with `AI -> Training -> Prepare Training Data`
- launch the local pairwise + cluster labeling app with `AI -> Training -> Data Selection / Ranking...`
- train a new preference ranker with `AI -> Training -> Train Ranker...`
- evaluate the active trained checkpoint with `AI -> Training -> Evaluate Trained Ranker`
- rescore the current folder with the trained checkpoint using `AI -> Training -> Score Current Folder With Trained Ranker`
- build a reusable reference bank from a separate folder with `AI -> Training -> Build Reference Bank...`
- clear the current folder's trained checkpoint or reset back to the default model with `AI -> Training -> Clear Trained Model...`

The same core training actions are also available from `Tools -> AI Training`.

### What The App Displays

When an AI export is loaded, matching images gain read-only AI context in these places:

- thumbnail overlays: AI score badge and AI top-pick badge
- thumbnail metadata line: normalized AI display score, group id, and group rank
- preview footer: normalized AI display score, group id, rank, and top-pick note
- AI Culling viewport: grouped sections with rank badges and normalized `0-100` per-group scores
- summary and status text: matched-image counts and selected-image AI details

### Mapping Strategy

The host app matches AI rows to image records by normalized absolute file path. This keeps the integration independent from internal artifact details while still preserving stable engine `image_id` values inside the loaded export rows.

The host-side adapter code lives in:

- `ai_results.py`
- `ai_workflow.py`
- `ai_training.py`

### Current Scope

This integration is intentionally local-first and operator-driven. It does not:

- reorder normal browsing by default
- auto-accept or auto-reject images
- replace the external labeling UI with an in-app trainer
- manage remote or cloud training jobs
- merge labels across folders or multiple users automatically
