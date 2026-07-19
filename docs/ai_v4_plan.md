# AI v4 — Dimension Scoring + Preference Weighting (FACET-style)

Living plan for the v4 AI rework. We check items off as we go. Branch: `ai_v4`.

---

## 1. Why we're doing this (the pivot)

Diagnostics on the current adapter proved three things:

- Base scores correlate only **~0.10–0.12** with the user's labels (within a single folder).
- The embedding adapter **fits one folder but does not generalize** — holdout rank lift went to ~0 / negative.
- We **cannot ask the user to label thousands** of images — that defeats the purpose of a culler.

So we stop trying to *learn quality from frozen DINO/CLIP embeddings*. Instead we **measure quality across explicit, interpretable dimensions** (mostly classical CV + a few pretrained specialists), then **personalize with a small per-category weight learner** over those dimensions.

This is the architecture used by **FACET** (github.com/ncoevoet/facet, MIT). We build it ourselves, using FACET as the **spec/reference** (formulas, thresholds, model choices) — not a code copy. MIT is a fallback only if we get stuck.

**Why this works where the adapter didn't:**
- Useful **day one with zero user data** (the triage/reject layer is all classical CV + pretrained models).
- **Transferable** — interpretable axes (sharp, exposed, eyes-open) generalize across folders; memorized embedding directions don't.
- **Overfit-proof personalization** — learning ~N weights per category can't memorize a folder the way an embedding regression does.
- The user's **reason tags map 1:1 onto dimensions**, so we can validate signal directly.

---

## 2. Target architecture

```
per image ──> DimensionScores (≈12 axes, 0–10 each)
                + folder-relative z-score / percentile variants
                       │
        ┌──────────────┴───────────────┐
   Stage A: REJECT                 Stage B: WINNER
   universal, no user data         per-category weighted sum
   technical + dupe + blink        weights learned from cull labels
   1400 ──> ~150 candidates        confidence-gated, advisory until
                                    multi-folder evidence exists
```

- **Stage A (Reject)** ships immediately — it needs no labels and is transferable. This is most of the grind.
- **Stage B (Winner)** is the personalization layer. See the dual-learner design below.
- **Evaluation harness** wraps both: random baseline, base baseline, base-vs-new deltas, folder + leave-one-folder-out holdout, winner-focused metrics, confidence-aware health gate.

### Stage B is a dual learner (decided 2026-06-28)

We learned the winner decision is **"wow factor"** — personal, subjective appeal, *not* literal repetition. It is **not** predictable from any generic dimension (on Canada: classical ~0.25, aesthetic 0.074, uniqueness ≤0.12 — all topped out ~0.29), but the embedding adapter *did* fit ~0.7 **within** a folder. So "wow" is learnable per-folder, not globally. We keep **both** learners, blended:

- **Per-folder learner (primary):** fit on the current folder's labels, ranks that folder. Retrains in-the-loop as you cull. Strong in-folder.
- **Global learner (alongside):** the existing Global Adapter, fit on all accumulated labels — a cross-folder prior, weak now, improving via the passive-label flywheel.
- **Blend by confidence:** `winner = w_local·local + (1-w_local)·global`, `w_local = n_local/(n_local+ramp)`. Cold-start leans on the global prior; as you label the folder, per-folder takes over.
- **Both learners consume the same feature vector:** quality **dimensions** (classical + aesthetic) + existing scores, optionally embeddings. Dimensions are the transferable core; raw embeddings add per-folder capacity but hurt global transfer, so embeddings default to per-folder-only.

The **old adapter is not removed — it *becomes* the global learner.**

---

## 3. Dimension map (FACET spec — what computes each axis)

| Dimension | Method | Library |
|---|---|---|
| Technical sharpness | `cv2.Laplacian(gray, CV_64F).var() / 50`, log boost at high ISO | OpenCV |
| Exposure | clipped-pixel fractions (`≤5` shadows, `≥250` highlights) | OpenCV/NumPy |
| Dynamic range | `log2(p98 / p2)` in stops | NumPy |
| Noise | Immerkaer Laplacian (`cv2.filter2D` 3×3 kernel) | OpenCV |
| Contrast | percentile range (5–95) + RMS std, weighted | OpenCV/NumPy |
| Color harmony | Shannon entropy of HSV histogram | OpenCV |
| Monochrome flag | mean HSV saturation threshold | OpenCV |
| Aesthetic | **TOPIQ via `pyiqa`** (primary) + CLIP/SigLIP text-projection axis (supplementary) | pyiqa, open-clip-torch |
| Composition | rule-of-thirds power points + leading lines (Canny + Hough) on detected subject | OpenCV |
| Subject saliency | `cv2.saliency.StaticSaliencySpectralResidual` + Otsu; Canny-edge fallback | OpenCV |
| Face quality | InsightFace **buffalo_l** detection confidence, `0.7·min + 0.3·avg` | insightface + onnxruntime |
| Eye sharpness | Laplacian variance on eye-region crop, normalized by mean intensity | OpenCV + InsightFace landmarks |
| Blink / eyes-closed | Eye Aspect Ratio from 106-pt landmarks, threshold `0.21`, head-pose gated (`|yaw|,|pitch| > 35°` ignore) | InsightFace + NumPy |
| Duplicate context | perceptual hash groups (already in pipeline) | imagehash |

**Reason-tag → dimension mapping** (used to validate signal):
- `technical_failure` → sharpness, exposure, noise, eye sharpness, blink
- `duplicate` → duplicate context
- `boring_repetitive` → aesthetic + saliency
- `composition` → composition
- `light_color` → color harmony, exposure, dynamic range

---

## 4. Phases (the checklist)

### Phase 0 — Scaffolding & decisions
- [ ] Audit existing deps; add what's missing (`opencv-python`, `pyiqa`, `insightface`, `onnxruntime`, `imagehash`). Note frozen-app/AppImage bundle-size impact; keep CPU-friendly defaults.
- [x] Create `image_triage/quality/` package mirroring FACET's `analyzers/` split.
- [x] Define `DimensionScores` dataclass (0–10 scale). Storage schema decided (`image_dimensions` table) — build pending. Folder-relative z-score/percentile variants pending.
- [ ] Decide where dimension computation hooks into the existing Index & Score pipeline.
- **DoD:** package + data model exist; a stub analyzer runs end-to-end and writes a row.

### Phase 1 — Classical CV dimensions (no models, no data, day-1 value)
- [x] `technical.py`: sharpness, exposure, dynamic range, noise, contrast, color harmony, monochrome — FACET formulas (NumPy-only; cv2 parity deferred to Phase 2).
- [x] Deterministic unit tests on synthetic images (9 tests, all directional assertions passing).
- [x] Computed on a real folder (Canada) and eyeballed: sharpness cleanly separates soft (0.4) vs sharp (10.0). Storage table still pending (next increment).
- **DoD:** 7 classical dimensions computed, tested, stored.

### Phase 2 — Pretrained specialists
- [x] Aesthetic: CLIP/SigLIP text-projection axis built (`aesthetic.py`) — validated weak (0.074) on Canada, supplementary only. (TOPIQ already in pipeline as `technical_score`.)
- [x] Face/eye: InsightFace `buffalo_l` → model download (`ai_model.download_aiculler_face_model`, ~23 MB) + `face.py` analyzer (face quality, eye sharpness via keypoints, gender/age, per-face records for zoom/inspector). Validated on China portraits. **Blink deferred** (needs eye-contour index calibration). Recognition/face-sort excluded (own path). UI/pipeline hooks → handoff §6.
- [ ] Composition/saliency: OpenCV spectral-residual saliency + rule-of-thirds + leading lines.
- [ ] Integrate duplicate context from existing pHash groups.
- [ ] Tests + integration into the pipeline.
- **DoD:** full ≈12-dimension vector computed per image and stored.

### Phase 3 — Reason-sliced diagnostics (SIGNAL GATE — do before any modeling)
- [ ] Extend the diagnostics report: correlate **each dimension against its matching reason tag**, global + per-folder, **Spearman + n**, suppress/flag low-n.
- [ ] Confirm the dimensions actually predict their reasons (the aggregate 0.12 should resolve into strong per-reason correlations, e.g. sharpness↔`technical_failure`).
- **DoD:** a report that says, per reason, which dimensions carry signal. **If signal is absent, stop and rethink before building Stage B.**

### Phase 4 — Reject stage (universal, day-1 triage)
- [ ] Rule/threshold scorer over technical + dupe + blink → a "trash/keep-candidate" decision. No user data.
- [ ] Evaluate vs **random** and **base** baselines; report how much of 1400 it clears and false-reject rate.
- [ ] Wire into the UI as a pre-filter (advisory) — shrink the pile before review.
- **DoD:** reject stage demonstrably beats random/base at clearing obvious trash, on ≥1 folder.

### Phase 5 — Dual winner learner (per-folder primary + global prior)
- [x] Shared learner engine — `learner.py`: ridge preference learner, honest cross-validation, confidence blend.
- [x] End-to-end per-folder ranking — `winner.py`: fit on a folder's labeled embeddings, score all images, blend with global by confidence, DB loader. Tested + validated on Canada.
- [x] **Validated:** per-folder learner on embeddings = **0.55 cross-validated** (vs 0.146 existing, 0.29 best generic dim). Embeddings carry "wow"; dims do not (0.36). In-sample sanity on Canada: top-15 ranked mean label 0.83, bottom-15 0.00.
- [x] **Cold start no longer uses the global prior** (2026-07-19). Below `min_labels`, `rank_folder_winners` ranked with 100% the global prior. Leave-one-folder-out over Banff/Canada/China puts that prior at **-0.132 / -0.001 / -0.053**, with winners-in-first-20 of 15% against an 18–27% random floor — it never beats shuffling. It now prefers the base composite (`COALESCE(final_score, technical_score)`), which scores **+0.238** on China. The blend path above `min_labels` is unchanged. New `source="base"`.
- [ ] In-the-loop retrain hook (retrain as the folder is labeled) + UI surfacing of the ranking.
- [ ] Confidence/coverage badge; advisory gating; feed dimensions into the global learner to test cross-folder transfer.
- **DoD:** per-folder ranking usable while culling; global stays the cold-start prior, improving via the flywheel.

### Phase 6 — Evaluation harness & gating
- [ ] Every report shows **random / base / new** deltas (top-k recall, false-reject, rank corr), per-folder + mean/variance.
- [ ] Reframe metrics to **winners** not keepers: `winner_top_k`, `false_winner_rate`, **winners-in-reviewed-top-N**.
- [ ] Leave-one-folder-out when ≥3–5 folders exist; few-shot curve.
- [ ] Health gate caps at **advisory** without multi-folder holdout (no in-sample "healthy").
- **DoD:** an honest, baseline-anchored, winner-focused report drives go/no-go.

### Phase 7 — Passive labels, UI, reason chips
- [ ] Capture normal cull decisions as labels passively (no special training mode).
- [ ] Surface the per-dimension breakdown per image in the UI.
- [ ] One-tap reason chips on keep/reject (feeds Phase 3 validation + Phase 5 targets).
- **DoD:** the tool learns from normal use and shows *why* it scored an image.

---

## 5. Guardrails (carried from the diagnostics work)

- **Baselines always.** Random + base in every evaluation; the bar is "beats base on unseen folders," not "high Score Fit."
- **In-sample is labeled in-sample.** Never call in-folder fit "generalization."
- **Spearman + n** for all correlations; small-n results flagged, not trusted.
- **Confidence-aware gating**; cap at advisory until enough folders. Noisy gates don't hard-block.
- **Don't rip out the old adapter early** — keep it advisory until Stage B proves out.
- **Diversity beats volume** — prioritize labels across many folders over depth in one.
- **Generate the reject signal, harvest the winner signal.** Synthetic degradation can train/validate technical axes; winner/taste comes from passive cull capture across folders.
- **Build alone; FACET = spec.** Reimplement; the MIT code is a reference and a fallback, not the source of truth.

---

## 6. Open decisions to settle as we start

1. Dimension storage: new columns on the existing images table vs a dedicated `image_dimensions` table.
2. Package location/name: `image_triage/quality/` vs `aiculler/dimensions/`.
3. ~~Genre/category source for per-category weights: reuse existing semantic category or add a dedicated classifier.~~ **Settled 2026-07-19 — reuse the existing CLIP category; do not build a dedicated classifier yet.** Category slicing does not currently earn one. See §7.
4. Frozen-app footprint: confirm `insightface` + `onnxruntime` + `opencv` are acceptable in the AppImage/MSI bundle; CPU-only default.

---

## 7. Settled experiments (don't re-run these from scratch)

### Semantic-category adapters — weak, unproven, not dead (2026-07-19)

**Hypothesis:** the adapter fails across folders because folders differ by *place* (China vs Canada). Pool labels by CLIP scene category instead (`landscape`, `travel_built`, `people_portrait`, `night_astro`, …) and train per category, so a street scene learns from street scenes everywhere.

**The control that matters:** category-pure training also *shrinks the training set*, and a smaller training set changes the score on its own. Comparing category-pure against the full pool conflates the two and shows a fake +0.19 gain. The honest comparison is against **random subsets of the same size** drawn ignoring category.

**Result** (3 folders, 410 labels, held-out folder never in training):

| | |
|---|---|
| median percentile within its own size-matched null | **60%** (50% = category adds nothing) |
| cells beating the null's 90th percentile | 2 / 13 |
| mean advantage, cells with n_te ≥ 25 | **+0.080** |

A weak positive lean, not established. **Do not build per-category adapters over embeddings** regardless of how this resolves — that splits already-scarce labels N ways, which is the opposite of what the label budget allows. Per-category *weights* over the ~12 dimensions (§2 Stage B) stays the affordable form.

**The one live thread:** `night_astro` beat its null outright (100th percentile, +0.476 vs +0.112). Mechanically plausible — astro has quality criteria (star trails, noise floor, focus at infinity) that share nothing with other categories. Worth isolating as a narrow claim before betting on category slicing broadly.

### Two evaluation traps found the hard way

1. **Stored scores grade their own homework.** `adapter_scores.adapter_score` correlates **+0.74** with labels on Banff and Canada — but only because those adapters were trained on those folders' labels. `images.final_score` is contaminated the same way: adapter training *overwrites* it with a blend containing adapter output. Any transfer claim must come from leave-one-folder-out, never from stored columns. China is currently the only uncontaminated folder (no adapter ever trained on it).
2. **The label store has two copies.** The app runs Windows Store Python, which redirects `%APPDATA%` into a per-package sandbox. `AppData\Roaming\ImageTriage\...` and `AppData\Local\Packages\PythonSoftwareFoundation.Python.3.13_*\LocalCache\Roaming\ImageTriage\...` are different files with different row counts; `PRAGMA database_list` reports the same path for both and `integrity_check` says `ok` on both. **Run label-store diagnostics under `py -3.13`**, not the CLI-Culler venv (3.12) or anaconda (3.9), or you will silently analyse weeks-old data. Note `py -3.13` has no scipy — use `quality/analysis.py`'s `spearman`.
