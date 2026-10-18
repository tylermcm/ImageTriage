# AI culling quality: diagnosis and plan

Written 17 October 2026, from the repo's own evidence (`docs/ai_v4_plan.md`, `docs/ai_v4_handoff.md`, the code) plus today's
licence checks (`docs/ai_model_licence_audit.md`). No labelled folders exist on the machine this was written on, so **nothing
below was re-measured today**. Every number is quoted from the earlier docs and marked as such.

## Decisions made with the user

| Question | Decision |
|---|---|
| Replace TOPIQ (not licensable for commercial use) | **Find a permissive image-quality model.** Shortlist and verify licences, then benchmark. |
| Embeddings: TinyCLIP 8M vs a bigger model | **One embedding model only**, serving both search and culling. TinyCLIP was added for search; the culling use is a byproduct. Look for a model that is as fast as TinyCLIP and as accurate as OpenCLIP. Do not run two. |
| Evaluation data | **Own labelled folders plus public data**, after licences are checked. |
| Dispute chord (D then 1 to 5) | Left disconnected until its effect on later culling is measured (see Evaluation). |

## 1. What the current pipeline gets wrong

Evidence is from the earlier docs unless stated.

1. **The base quality score barely predicts your choices.** Base scores correlate about 0.10 to 0.12 with your labels inside a
   folder (v4 plan §1). The TOPIQ stack scored 0.146 in the per-folder test, against a random floor of -0.07.
2. **The cross-folder prior is no better than shuffling.** Leave-one-folder-out over Banff, Canada and China put the global
   adapter at -0.132, -0.001 and -0.053, with winners-in-first-20 of 15% against an 18 to 27% random floor (v4 plan §4,
   2026-07-19). Cold start now uses the base composite instead (+0.238 on China, one folder).
3. **What works is per-folder learning, and its headline number may be stale.** A ridge learner on one folder's own labels
   reached 0.55 Spearman cross-validated, but that was on CLIP ViT-L/14 768-dimension embeddings. The culling pipeline's default
   CLIP model is now the managed TinyCLIP 8M bundle (`ai_manifest.py`, `aiculler_workflow.py`). I found no record of the
   per-folder result being re-measured on TinyCLIP embeddings. This is the largest unknown.
4. **Dimensions help with rejects, not winners.** Classical dimensions top out near 0.29 to 0.36 and add nothing over
   embeddings for winner picking (v4 handoff §2). Whether dimensions are persisted and used as a reject pre-filter in the app
   (handoff tasks A and D) was not checked today.
5. **Stored scores grade their own homework.** `adapter_scores` and `images.final_score` are contaminated by adapter training on
   the same labels (+0.74 on Banff and Canada), so only leave-one-folder-out numbers count. China is the only uncontaminated
   folder (v4 plan §7).
6. **The evidence base is three folders and about 410 labels.** Category-sliced adapters showed a weak, unproven lean (median
   60th percentile against a size-matched null; only `night_astro` stood out). Too few folders to trust differences of this size.
7. **Two tooling traps already cost time.** The label store exists in two copies under Windows Store Python, and diagnostics must
   run under `py -3.13` (v4 plan §7).
8. **Licensing now constrains the design.** TOPIQ and the three InsightFace-looking face files cannot ship commercially
   (audit). Replacing TOPIQ removes the input to the base score, so the base composite changes whatever we choose.
9. **Results were hard to see and to act on.** Before today the grid showed no AI information at all. The new dots, bucket bar
   and inspector rows fix visibility, but burst-level decisions ("which frame in this burst?") have no tool yet, and nothing
   measures whether the AI's burst pick matches yours.
10. **Corrections are not known to help.** The dispute chord's effect on later culling was never confirmed.

## 2. Evaluation: what would show progress

### Data

- **Own folders (primary).** Every folder where you marked winners and rejects. Target at least 8 folders across genres before
  any general claim, and at least 3 before leave-one-folder-out means anything. Record per folder: label counts, genre, camera,
  and whether any adapter was ever trained on it (contamination flag).
- **Public data (secondary, evaluation only until licences clear).** I could not confirm the terms of KonIQ-10k (hosted at
  database.mmsp-kn.de) or AVA (images from dpchallenge.com, so uploader copyright is likely); searches returned no licence text.
  The pyiqa README says its datasets are for academic, research and educational purposes. SPAQ and PaQ-2-PiQ were not checked.
  Plan: read each dataset's terms page before use, and treat a dataset as evaluation-only unless it explicitly allows
  commercial training. Public quality datasets measure generic quality, not your "wow" choices, so they validate the reject
  and quality side only.
- **Burst ground truth.** From your own folders, bursts where you kept exactly one frame. This tests the feature people will
  feel most: did the AI pick the same frame?

### Metrics

| Metric | What it answers |
|---|---|
| Spearman (cross-validated) with n, per folder and mean and spread | Does the ranking follow your taste? Never report in-sample fit. |
| Winners in the reviewed top N (N = 10, 20, 50 and 5% of the folder) | If you review only the top of the list, how many winners do you see? |
| Top-k recall of winners, and false-reject rate at fixed pile-reduction (e.g. clearing 50%) | Does the reject stage remove trash without removing winners? |
| Burst pick agreement | Share of bursts where the AI's top frame is the frame you kept. |
| Calibration | Of photos the AI calls "Winner", what fraction did you actually mark? Reliability by bucket, per folder. |
| Few-shot curve | Quality after 0, 8, 16, 32, 64 labels in a new folder. Shows when per-folder learning takes over. |
| Latency and size | Seconds per 1,000 photos on CPU, model MB, for any candidate embedding or quality model. |

Baselines in every table: random, the base composite, and the previous release. Leave-one-folder-out for anything cross-folder.
Keep the existing honesty rules: label in-sample as in-sample, cap health gates at "advisory" without multi-folder holdout,
never use stored score columns as evidence of transfer.

### The dispute experiment

With the chord restored on a branch, replay your corrections in order on labelled folders and measure the next 100 photos'
ranking against the same folder without corrections. Decide whether to keep it from that.

## 3. Approaches worth trying, in priority order

### A. Replace TOPIQ with a permissive quality signal (decided: find a permissive IQA model)

What I found, with verification level:

- **No pretrained quality model has a confirmed commercial-friendly licence for its weights.** None were verified.
- **MUSIQ** (Google): the repo page shows no licence and no LICENSE file. Checkpoints are trained on AVA, KonIQ, PaQ-2-PiQ and
  SPAQ, whose terms are unconfirmed. Not usable until Google's licence and each dataset's terms are clear.
- **CLIP-IQA** (IceClear): NTU S-Lab License 1.0. Its terms were not read; the same family of licence appears alongside
  PolyForm Noncommercial in pyiqa. Treat as non-commercial until read.
- **LAION improved aesthetic predictor:** repo licence is Apache-2.0, but its `.pth` weights are trained on AVA, SAC and
  LAION-Logos with no stated weight licence. The weights sit on CLIP ViT-L/14 embeddings.
- **NIMA:** pyiqa retrains it itself because there are no official weights; any retrained weights inherit AVA's terms.

Because a licensed pretrained model may not exist, the realistic options are:

1. **Zero-shot prompt scoring on our own embedding** (the CLIP-IQA idea: "good photo" against "bad photo", sharp against blurry).
   The idea is not code; implement it ourselves on the chosen embedding's text encoder. The earlier CLIP aesthetic axis scored
   only 0.074 on one folder, so expect it to be a weak supplement.
2. **Classical technical dimensions** (already built, NumPy-only): sharpness, exposure, noise and so on. These are the
   transferable reject signal (0.36 combined, better than TOPIQ's 0.146 in the same test).
3. **A small quality head trained on permissively-licensed data**, if suitable data is found and its licence confirmed.
4. **A commercial licence** for TOPIQ's source weights, as a fallback only.

Recommended path: do 2 and 1 now, measure against TOPIQ on your folders, and keep 3 as an open research item.
**Decision needed later:** accept a small drop in base-score quality in return for a clean licence, once measured.

### B. One embedding model for search and culling

Candidates and what I verified:

| Model | Licence found | Notes |
|---|---|---|
| TinyCLIP ViT-8M/16 (current) | MIT on the card | Fast and small. Training data YFCC-15M. The card does not name the distillation teacher. |
| SigLIP 2 base patch16 (`google/siglip2-base-patch16-224`) | Apache-2.0 on the card, not gated | No ONNX file in the repo, so we would convert it. A ViT-B model is far larger than 8M. |
| OpenCLIP LAION ViT-B/16 (`laion/CLIP-ViT-B-16-laion2B-s34B-b88K`) and ViT-B/32 | MIT on the card | No LICENSE file and no ONNX in either repo. LAION data terms unreviewed. |
| MobileCLIP2 (Apple) | `apple-amlr` | Not an open-source licence; excluded until read. |

Plan: first measure whether embedding quality is even the limiter. Run the per-folder learner on TinyCLIP embeddings and compare
to the 0.55 on ViT-L/14. If TinyCLIP is close, keep it and skip the bigger model. If it is clearly worse, convert one larger
permissive candidate (SigLIP 2 base first) and measure Spearman, search quality and seconds per 1,000 photos together.
Re-embedding stores is expensive, so decide before the next index format change.
**Decision needed later:** the acceptable speed cost for any accuracy gain.

### C. Better per-folder learning

- Retrain in the loop as you cull (planned, handoff task C) and show a confidence badge so a weak ranking is labelled advisory.
- Try pairwise or listwise ranking losses on within-burst comparisons instead of scalar ridge targets.
- Make the learner burst-aware: rank frames inside a burst separately from ranking across the folder.
- Tune ridge strength and the label ramp (`alpha` 30, `ramp` 20, `min_labels` 8) with the few-shot curve, not by feel.

### D. Calibration and honesty in the buckets

- Bucket thresholds are folder percentiles tuned to "top 10% keepers, bottom 80% rejects" (`ai_results.py`). Check on your
  folders whether "Winner" and "Reject" mean what the labels say, using the calibration metric above.
- Add an abstain state: when the folder has too few labels or low agreement, show "unsure" instead of a confident bucket.

### E. Reject stage

Wire the persisted technical dimensions as an advisory pre-filter, measured by false-reject rate at fixed pile-reduction.
Confirm first whether dimensions are persisted during indexing today.

### F. Faces

Keep AuraFace `glintr100` for recognition. Until the other three files' provenance is confirmed, plan around YuNet (MIT, files
confirmed) for detection and drop age and gender. Eye-sharpness and blink need landmark replacements; MediaPipe's landmarker
was not checked. Blink stays deferred, as before.

## 4. Phases and exit criteria

| Phase | Work | Exit criterion |
|---|---|---|
| 1. Baseline | Build the benchmark from your labelled folders; re-run `scripts/eval_per_folder_winner.py` on TinyCLIP embeddings; add burst agreement and calibration to the report | One table: random, base, TOPIQ, per-folder, per folder, with n |
| 2. TOPIQ replacement | Implement prompt scoring and the technical-dimension composite as the base score; compare with TOPIQ | Replacement within an agreed margin of TOPIQ on winners-in-top-20, or a decision to accept the drop |
| 3. Embedding | Only if phase 1 shows TinyCLIP is the limiter: convert and benchmark one candidate | One embedding chosen on accuracy and speed, with a re-index plan |
| 4. Learning | In-loop retrain, burst-aware ranking, calibration, abstain state | Beats the base composite on held-out folders, and few-shot curve shows a useful ranking by 16 labels |
| 5. Reject stage and corrections | Advisory pre-filter; dispute experiment | False-reject rate and dispute value measured |
| 6. Faces and licences | Detection swap and notices page from the audit | No bundle with an unverified or non-commercial licence |

## 5. Open questions for you

1. Which folders can go into the benchmark, and are any of them private such that they must stay on your machine?
2. Do you accept a measurable quality drop from losing TOPIQ if the replacement is licence-clean?
3. For the embedding, is slower indexing acceptable if it improves culling, given that search shares the index?
