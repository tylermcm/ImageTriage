# AI v4 — Handoff to Codex (Stage B data/learner layer done; integration next)

Branch `ai_v4`. Context + rationale: `docs/ai_v4_plan.md`. This doc is the precise
handoff: what's built (new `image_triage/quality/` package, fully tested, no app
deps) and the exact app seams left to wire (your territory).

---

## 1. What I built (all in `image_triage/quality/`, 35 tests passing, NumPy-only)

| Module | Public API | Purpose |
|---|---|---|
| `model.py` | `DimensionScores` (dataclass, ~12 fields, `.to_dict()`) | per-image dimension vector |
| `technical.py` | `analyze_technical(image_bgr, *, iso=None) -> DimensionScores` | 7 classical CV dims (FACET formulas) |
| `aesthetic.py` | `build_aesthetic_direction(encode, pos?, neg?) -> np.ndarray`; `aesthetic_score(image_embedding, direction) -> float` | CLIP text-projection aesthetic axis |
| `store.py` | `ensure_table(conn)`, `upsert_dimensions(conn, image_id, scores)`, `fetch_dimensions`, `fetch_all_dimensions` | `image_dimensions` table (keyed on `images.id`) |
| `analysis.py` | `spearman(x, y) -> (rho, n)`; `dimension_label_correlations(rows, *, dimensions, reasons?)` | reason-sliced correlation diagnostics |
| `learner.py` | `RidgePreferenceLearner(alpha).fit/predict`; `cross_val_predict(X, y, ...)`; `feature_matrix(rows, names)`; `blend_local_global(local, global_, n_local, ramp=20)` | shared preference learner + confidence blend |
| `winner.py` | `load_winner_inputs(conn, *, model_version=None)`; `rank_folder_winners(labeled_emb, labels, all_ids, all_emb, global_scores, *, alpha=30, ramp=20, min_labels=8) -> list[WinnerScore]` | end-to-end per-folder ranking + global blend |

`WinnerScore`: `image_id, blended (0-1 ranking key), per_folder, global_score, source ("blend"|"per_folder"|"global")`.

**Inputs use the EXISTING DB.** `winner.load_winner_inputs(conn)` reads `ratings` (labels), `embeddings` (CLIP ViT-L/14, 768-d, float32 — already stored for all images), and `adapter_scores` (the global prior). No new data needed to run the per-folder ranking.

## 2. Validated on real Canada data (read-only)

**Reproducible:** `python scripts/eval_per_folder_winner.py --db <aiculler.sqlite>` regenerates the numbers below (embeddings-only, no image decode) with random floor + existing-score baselines and the CV-vs-in-sample gap. This is also the Phase 6 eval seed — point it at the next folders.

- Per-folder learner on **embeddings = 0.55 Spearman, cross-validated** (vs existing TOPIQ stack 0.146; random floor −0.07; best generic dimension 0.29; CLIP aesthetic 0.074; classical dims combined 0.36). In-sample is 0.96 — the gap is why we only ever quote CV.
- **Embeddings carry the "wow" signal; dimensions do not** (dims+embeddings = 0.543 ≈ embeddings alone). So the **per-folder learner is embedding-based**.
- End-to-end smoke (139 labeled / 1404 total): top-15 ranked mean label 0.83, bottom-15 0.00.
- Dimensions are a **reject** signal, not a winner signal. Classical dims beat the existing stack but top out ~0.29 — useful for triage, not winner-picking.

## 3. Architecture (decided with the user)

Stage B is a **dual learner**:
- **Per-folder (primary):** `RidgePreferenceLearner` fit on the current folder's labeled embeddings. Retrains in-the-loop. Strong (0.55), does not need to transfer.
- **Global (prior):** the **existing Global Adapter IS this** — do not remove it. `adapter_scores` are the global signal.
- **Blend by confidence:** `w_local = n_local/(n_local+ramp)`; cold-start leans global, hands over to per-folder as labels accrue. `winner.rank_folder_winners` already does this (percentile-normalizes both before blending).

## 4. Integration tasks (app side — yours)

**A. Persist dimensions during Index & Score.**
In the scoring pass, for each image compute `analyze_technical(bgr, iso=exif_iso)` and `quality.store.upsert_dimensions(conn, image_id, scores)`. Image decode already exists in the pipeline (`aiculler/features.py` / preview path) — reuse it; pass BGR uint8. Aesthetic dim is optional here (needs the CLIP text encoder — see note 5). `image_dimensions` table self-creates via `ensure_table`.

**B. Per-folder winner ranking → grid.**
The grid's AI order comes from `_load_ranked_gui_rows` ([aiculler_workflow.py:3064](image_triage/aiculler_workflow.py:3064)) which ranks by `final_score = base + adapter_score*weight`. Add a winner path that mirrors it:
1. `labeled_emb, labels, ids, all_emb, global_scores = winner.load_winner_inputs(conn)`
2. `ranked = winner.rank_folder_winners(labeled_emb, labels, ids, all_emb, global_scores)`
3. Persist `ranked[i].blended` (suggest a new `winner_scores` table, or a column — your call; keep it separate from `adapter_scores` so the global model is untouched).
4. Add a grid sort option "AI: Wow (per-folder)" that orders by the blended score. This is a new ranking *source* alongside the existing adapter ranking, not a replacement.

**C. In-the-loop retrain.**
After labels change in a folder (the rating-write path in the adapter labeling workflow), re-run B so the ranking sharpens as the user culls. Cheap (no image loading — embeddings are stored; it's a ridge fit over ≤N×768). Gate on `min_labels=8` (below that, `rank_folder_winners` auto-falls back to the global prior).

**D. Reject stage (separate, ships independently).**
Use the persisted dimensions as a pre-filter (e.g., flag low `sharpness`/`noise`/`exposure`) to shrink the review pile before winner ranking. Dimensions are transferable, so this needs no per-user labels.

**E. Global learner (later, multi-folder).**
The existing adapter is the global side — leave it. When ≥3 culled folders exist, the next experiment is feeding **dimensions** (not just embeddings) into the global learner and running leave-one-folder-out, since dimensions transfer where embeddings don't. Not needed for the per-folder feature to ship.

## 5. Constraints / notes

- **No new heavy deps** for the per-folder pipeline: `winner.py`/`learner.py` are NumPy-only and reuse stored embeddings. The reject dims are NumPy-only.
- **Aesthetic dim** needs the CLIP **text** encoder (`aiculler.text_scoring.CLIPTextEncoder`, model files `models/Clip/clip-vit-large-patch14/{onnx/text_model_uint8.onnx,tokenizer.json}` — already present). It's a **supplement** (0.074 here); don't block on it.
- **Embeddings are CLIP ViT-L/14, 768-d** (from `clip_session` in `features.py`), text-alignable — confirmed.
- **Honesty discipline (carry forward):** never report in-sample fit as performance — always `cross_val_predict`. Cap any "healthy" gate at advisory until multi-folder holdout. Show base + random baselines.
- **OpenCV deferred:** Phase-1 dims are NumPy replicas of FACET's formulas. If you later add `opencv-python-headless`, you can swap for exact cv2 parity (and it unlocks FACET's composition/saliency dims) — but it's a packaging decision (bundle size).
- I do **not** commit — the user handles all commits. Tests: `tests/test_quality_*.py` (49, all passing on py 3.13).

---

## 6. Face pass (InsightFace buffalo_l) — built + validated, one UI hook left for Codex

A reject-side dimension for people genres + the data for a face zoom/inspector UI. **Recognition/face-sort is intentionally NOT here** — it gets its own separate download path later.

**Model download (done, in `ai_model.py`):** `download_aiculler_face_model()` mirrors the CLIP/TOPIQ pattern. Repo `Skulleton12/insightface` @ `df17665…`, files `det_10g.onnx` + `2d106det.onnx` + `genderage.onnx` (~23 MB; `w600k_r50.onnx` recognition **excluded**). Lays them out as `<cache>/…/CLI-Culler/insightface/models/buffalo_l/` so `FaceQualityAnalyzer` loads via `aiculler_face_model_root()`. **Verified end to end** — the three files download and load.

**Analyzer (`quality/face.py`):** `FaceQualityAnalyzer.analyze(bgr)` returns aggregate `face_quality` (0.7·min+0.3·avg det conf), `eye_sharpness` (Laplacian on crops around the detector eye **keypoints** — no brittle landmark indices), `face_count`, and a per-face `faces: list[FaceRecord]` (bbox, det_score, eye_sharpness, gender, age) for the **zoom pane + inspector**. Lazy + graceful (no models → all `None`). **Validated on real China portraits:** detection works (det 0.74–0.79), gender/age stable (M, 31 across two shots), eye_sharpness sensible (~8.1), bboxes correct.

**Blink is deferred (intentionally):** reliable EAR needs exact eye-contour indices that need a dedicated calibration against a known closed-eye image; a wrong blink causes false rejects. The pure `eye_aspect_ratio`/`is_blink` helpers are kept and tested for that pass; the analyzer returns `blink=None` until then.

**Codex hooks:**
1. **"Download AI Models" dialog** ([window.py:7639](image_triage/window.py:7639), config at [window.py:476](image_triage/window.py:476), checkboxes ~[window.py:7700](image_triage/window.py:7700)): add a `download_aiculler_face_model` flag + checkbox and call `ai_model.download_aiculler_face_model()` — same shape as the CLIP/TOPIQ entries.
2. **Pipeline:** create one `FaceQualityAnalyzer` per run; for people-category images, merge its `face_quality`/`eye_sharpness` into `DimensionScores` (reject stage) and persist the per-face `FaceRecord`s (suggest a `faces` table) for the inspector.
3. **UI:** face zoom preview pane (crop to `FaceRecord.bbox`) + gender/age in the inspector (label gender/age as *estimates*).
4. **Dependency:** `insightface` must be added to the AI runtime requirements (installed in the venv during this work). It's CPU-friendly; `onnxruntime`/`opencv` already present.

**Blink follow-up (when you have a closed-eye reference):** calibrate the 2d106 eye-contour indices, wire `eye_aspect_ratio` + landmark-derived pose into `is_blink`, set the threshold from open/closed examples.
