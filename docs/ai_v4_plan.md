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
- **Stage B (Winner)** is the personalization layer. It replaces the embedding adapter. Weights are tiny and per-genre.
- **Evaluation harness** wraps both: random baseline, base baseline, base-vs-new deltas, folder + leave-one-folder-out holdout, winner-focused metrics, confidence-aware health gate.
- The **old adapter stays advisory** during the transition; we remove it only after Stage B proves out on multiple folders.

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
- [ ] Create `image_triage/quality/` (or `aiculler/dimensions/`) package mirroring FACET's `analyzers/` split.
- [ ] Define `DimensionScores` dataclass + the storage schema (per-dimension columns or a `image_dimensions` table in the AI SQLite). Decide 0–10 scale + folder-relative z-score/percentile variants.
- [ ] Decide where dimension computation hooks into the existing Index & Score pipeline.
- **DoD:** package + data model exist; a stub analyzer runs end-to-end and writes a row.

### Phase 1 — Classical CV dimensions (no models, no data, day-1 value)
- [ ] `technical.py`: sharpness, exposure, dynamic range, noise, contrast, color harmony, monochrome — FACET formulas verbatim as spec.
- [ ] Deterministic unit tests on synthetic images (a blurred image scores lower sharpness than its sharp original, a clipped image flags exposure, etc.).
- [ ] Compute + store these for a real folder; eyeball the spread.
- **DoD:** 7 classical dimensions computed, tested, stored.

### Phase 2 — Pretrained specialists
- [ ] Aesthetic: TOPIQ via `pyiqa` (primary) + CLIP/SigLIP text-projection axis (cheap supplement; expect ~0.4 corr, supplementary only).
- [ ] Face/eye: InsightFace `buffalo_l` → face quality, eye sharpness, blink (EAR + head-pose gate).
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

### Phase 5 — Per-category weight learner (the personalization, replaces the adapter)
- [ ] Content/genre categorization (reuse existing semantic category or add a light classifier).
- [ ] Learn **per-category weights** over the normalized dimension vector from existing cull labels. Start linear; heavy regularization.
- [ ] Confidence-aware output: held-out accuracy + coverage badge; **advisory until multi-folder evidence**.
- [ ] (Later) A/B comparison + live score preview, FACET-style.
- **DoD:** a learned weighting that beats base ranking on folder holdout, or is honestly gated as advisory/weak.

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
3. Genre/category source for per-category weights: reuse existing semantic category or add a dedicated classifier.
4. Frozen-app footprint: confirm `insightface` + `onnxruntime` + `opencv` are acceptable in the AppImage/MSI bundle; CPU-only default.
