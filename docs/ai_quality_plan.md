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

## Data found on this machine (17 October 2026, read-only)

The XQD card is the NIKON Z 7_2 volume (E:). `DCIM@NZ7_2` holds 2,054 JPG and 2,016 NEF files (about 138 GB);
`DCIMGNZ7_2` holds 16 of each. The card's own AI database (`E:\.image_triage_ai`) is empty (0 images, 0 ratings,
0 embeddings), no XMP ratings exist for these shots, and the app's decision store on this machine holds 7 decisions in total.
So the card is **an unlabelled corpus, not yet a benchmark**. The app's catalog has already grouped it into 389 review
groups (1,607 members), which gives burst structure to label against.

It can be used for: speed and size timing of candidate embedding and quality models on real RAW and JPG pairs, and as the
folder you cull to create labels. It cannot yet produce a Spearman or winners-in-top-N figure. The earlier evaluation folders
(Banff, Canada, China) are not on this machine.

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

Search result (17 October 2026, second pass): **no commercially usable pretrained quality model was found.** The blocker is
the training data rather than the code:

- **KonIQ-10k, KADID-10k, SPAQ:** each site or repo says the data is "freely available to the research community" or gives no
  licence at all. None grants commercial training rights. AVA and TID2013 (used by NIMA) were not found with terms either.
  So any weights trained on these datasets carry unresolved dataset terms, whatever the code licence says.
- **TOPIQ ONNX copies:** `nocturna/topiq-nr-koniq-onnx` is CC BY-NC-SA 4.0 per the search summary. The TOPIQ model itself is
  PolyForm Noncommercial. Same status as the file we ship now.
- **idealo NIMA (MobileNet):** code Apache-2.0, weights trained on AVA and TID2013, no stated weight licence, repo archived
  since December 2024.
- **`ZhengyuZhao/koniq-PyTorch`:** MIT code only; no weights, and KonIQ's terms apply to anything trained on it.
- **`matthewyuan/image-quality-fusion`:** MIT per the search summary, trained on SPAQ (terms unconfirmed); no ONNX export.
- **MUSIQ:** no licence file; checkpoints trained on AVA, KonIQ, PaQ-2-PiQ and SPAQ.
- **CLIP-IQA:** NTU S-Lab License 1.0 (not read). **LAION aesthetic predictors** (`improved-aesthetic-predictor`,
  `shunk031/aesthetics-predictor-*`): code Apache-2.0 or no licence field, weights trained on AVA, SAC and LAION data with no
  weight licence stated.
- Not retrievable: `discord/aesthetic-predictor-v2-5` returned 401.

Given that, the realistic options are:

1. **Zero-shot prompt scoring on our own embedding** (the CLIP-IQA idea: "good photo" against "bad photo", sharp against blurry).
   The idea is not code; implement it ourselves on the chosen embedding's text encoder. The earlier CLIP aesthetic axis scored
   only 0.074 on one folder, so expect it to be a weak supplement.
2. **Classical technical dimensions** (already built, NumPy-only): sharpness, exposure, noise and so on. These are the
   transferable reject signal (0.36 combined, better than TOPIQ's 0.146 in the same test).
3. **A small quality head trained on permissively-licensed data**, if suitable data is found and its licence confirmed.
4. **A commercial licence** for TOPIQ's source weights, as a fallback only.

Recommended path (updated 18 October 2026 after an outside research report, see
`docs/topiq_replacement_research_findings.md`): train our own technical-quality model on synthetic degradations of photos we
own (route 3, now first), use zero-shot prompts on the shipped embedding for the aesthetic side (route 1), keep the classical
dimensions (route 2) as the interpretable baseline, and put our own burst-pick labels at the centre of calibration. Open
choice: what initialises our network (random, our TinyCLIP embedding, or a backbone whose licence we have checked).
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

Keep AuraFace `glintr100` for recognition. Replace the three InsightFace-looking files using models whose licences UniFace's
weights table lists as permissive: RetinaFace or CenterFace (MIT) for detection, MediaPipe Face Mesh (Apache-2.0) or PIPNet
(MIT) for eye landmarks, and eDifFIQA (MIT) as a face-quality score. Drop age and gender. UniFace can be used as the library
or only as the source of the ONNX files; decide when implementing. Blink stays deferred, as before.

### Measured training speed (server, 17 October 2026)

`scripts/bench_iqa_training.py`, generated images only, run on the home server (2x Xeon E5-2698 v3, 64 threads, 251 GB RAM,
Tesla V100-SXM2 16 GB, PyTorch 2.6 / CUDA 12.4). Each training item is 4 degraded 224 px crops (two damaged versions of a
512 px region, two crops each); six degradation types at five severity levels, one to three stacked.

| Measurement | Result |
|---|---|
| CPU loader, 32 workers | about 3,900 degraded crops per second |
| GPU only, ResNet-50, mixed precision, 128-crop batches | about 1,130 crops per second, 6.2 GB peak |
| GPU only, MobileNetV3-large | about 2,250 crops per second, 3.0 GB peak |
| End to end, ResNet-50 (32 photos per batch) | about 1,050 crops per second, GPU-bound |
| End to end, MobileNetV3-large | about 1,830 crops per second |

For 10,000 photos (40,000 crops per epoch): ResNet-50 about 38 s per epoch, so about 1 hour for 100 epochs and 2 hours for
200; MobileNetV3 about 22 s per epoch, so about 35 minutes for 100 epochs. The CPU is not the limit, the GPU is. Caveats:
synthetic base images are simpler than photos (real photos would be pre-resized and cached, with the same per-pixel
degradation cost), the loss in the benchmark is a simple contrastive loss, and two other services share the GPU (about 2 GB).
The earlier estimate of 1.5 to 3 hours for 100 epochs was too pessimistic on the CPU side.

### First trained model: results and limits (18 October 2026)

`scripts/iqa_synth/`: MobileNetV3-large, random initial weights, trained 150 epochs (46 minutes on the V100) on 5,396
camera JPEGs from 15 of the owner's trips. Damage types: blur, motion blur, noise, JPEG, downscale, exposure, contrast,
saturation, oversharpen, at five levels, one to four stacked. 852 photos from two held-out trips (Iceland 10-24,
Japan 4-24) were never trained on.

| Check on held-out photos | Result |
|---|---|
| Predicted vs true damage level on synthetic damage, Spearman | 0.74 |
| Stronger damage scored worse, per type (60 crops each) | blur 98%, motion 98%, noise 97%, JPEG 100%, downscale 92%, contrast 88%, oversharpen 80%, exposure 75%, saturation 75% |
| Real photos: quality score vs measured sharpness / measured noise, Spearman | 0.52 / +0.44 |
| Six-tile average vs whole-image score, Spearman | 0.85 |

What it shows: the model clearly learned the direction of each defect. On real photos, though, the contact sheet exposes a
content bias. The lowest scores (0.12 to 0.32) are dark aurora and night shots, an ice scene and a very detailed
vegetation close-up. The highest (0.92 to 0.94) are bright daylight street and temple scenes. Dark and highly textured
scenes are being read as damaged, because the training sources had few night or low-light photos (the weakest-sharpness
images were also dropped and astro folders excluded), so those looks never appeared as clean examples. The positive
correlation with the noise measure fits this: the measure itself counts fine texture as noise.

So this version is **not usable as an absolute quality score across different scenes**. Whether it ranks frames of the
same scene correctly (the burst case that culling needs) is untested; it needs bursts with known picks.

Next steps under consideration: retrain with night and low-light photos and texture-heavy scenes included as clean
sources, balance sources by brightness, and test on burst pairs; use the score relative to similar frames, not absolutely;
and wait for the owner's own keep/reject labels to calibrate.

### Version 2 results (18 October 2026)

Same method and network, trained on a broader, balanced set: 12,149 photos from the whole Photography library (11,000 train,
1,149 held out from Japan 4-24, Iceland 10-24 and Steamboat '20), built from embedded NEF previews, folder quotas by
square root of size, at least 25% low-light picks per folder (ISO 3200+ or 1/15 s+), 90 edited exports, 90-degree rotation
augmentation, 100 epochs, 53 minutes.

| Check on held-out photos | v1 | v2 |
|---|---|---|
| Predicted vs true damage level on synthetic damage, Spearman | 0.74 | 0.88 |
| Per type (blur, motion, JPEG, downscale) | 92 to 98% | 100% |
| Per type (noise, oversharpen, exposure, contrast, saturation) | 75 to 97% | 93 to 98% |
| Real photos: mean quality, ISO 400 or lower | n/a | 0.82 |
| Real photos: mean quality, ISO 3200 or higher | lowest scores 0.12 to 0.32 | 0.70 |
| Real photos: mean quality, exposure 1/15 s or longer | n/a | 0.74 |
| Real photos: quality vs measured sharpness / noise, Spearman | 0.52 / +0.44 | 0.42 / +0.40 |
| Mean quality by trip: Japan 4-24 / Steamboat '20 / Iceland 10-24 | n/a | 0.83 / 0.79 / 0.76 |

Reading: night and low-light photos no longer sit at the bottom (v1 scored them 0.12 to 0.32 against 0.93 for daylight),
but low-light is still about 0.1 lower on average, partly legitimate since real high-ISO noise is a defect. The lowest
scores on the contact sheet are now mostly blown-out, overexposed frames (a real technical flaw) and two dense-texture
subjects (a berry close-up and a pebble beach), so a residual texture bias remains. The highest scores are calm, evenly
lit scenes, including many near-identical frames of one lake scene, all at 0.93. Embedded previews of portrait shots came out
rotated 90 degrees (the NEF orientation tag was not applied); harmless here because rotation is an augmentation, but to fix
before any use that depends on orientation.

Still untested: whether the score ranks frames of the same scene (bursts) the way the owner would. That needs labelled
bursts. ONNX export is pending the `onnx` package (not installed on the server; the checkpoint is saved).

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

1. The card gives about 2,050 shots to label. How many bursts are you willing to cull on it (the 389 groups already exist), and do the earlier Banff, Canada and China folders exist anywhere reachable?
2. Do you accept a measurable quality drop from losing TOPIQ if the replacement is licence-clean?
3. For the embedding, is slower indexing acceptable if it improves culling, given that search shares the index?
