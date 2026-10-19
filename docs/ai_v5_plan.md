# AI v5: a three-pass culling pipeline

**Decision (18 October 2026, the owner): DINO is out permanently.** No DINO or DINOv2 embeddings anywhere in v5: not for ranking
(tested in v1, no better than random), not for burst grouping (round 2 spike: no reliable gain over the strong classical
set) and not for scene stacking (it beat the classical systems narrowly in the scene spike, but the owner is not using it).
Grouping uses time, EXIF and the classical feature set; scene stacking is dropped.

Planned 18 October 2026, after a ChatGPT deep-research report (pasted into the session) and our own checks. v5 starts over
on the problem definition: culling is three different problems, each measured on its own. No learned taste model on
DINO embeddings (that route was built and tested in v1 and was no better than random).

## The pipeline

1. **Pass 1, junk gate.** Cheap measurements on a normalised preview: black or catastrophically blown frames, defocus and
   motion blur, noise, clipping, subject-region sharpness and exposure. Only catastrophic, high-confidence rules reject;
   everything ambiguous stays a feature and a reason code. Tuned for keeper recall (target 99.5% or better on known keepers),
   not accuracy.
2. **Pass 2, best of a burst.** Candidate pairs from an EXIF time window plus classical appearance features (pHash, dHash,
   low-resolution SSIM, colour histogram, ORB geometric match, shooting settings), combined by a small logistic model tuned on
   the owner's labels and clustered with a medoid-consistency check so frames do not chain across scenes. No embedding model.
   Within a burst, features are made relative (robust z-scores against siblings); pick a winner and optionally a materially
   different second frame.
3. **Pass 3, final ranking.** A gradient-boosted ranker (LightGBM LambdaRank) and a calibrated keeper classifier over
   explicit features (technical, face, subject and composition, within-burst relative, optional aesthetic prior), not over
   raw embeddings. Outputs a rank score and P(keep); the three output modes (keep/reject report, ranked list with cutoff,
   numeric rank with proposed keepers) are views of those two numbers. Initial bands 0.35 and 0.65 are starting values to
   calibrate, not constants. Every decision carries reason codes.

## What the research report claimed, and what we have checked

| Claim | Status |
|---|---|
| IQA-PyTorch code is MIT but its pretrained weights are CC-BY-NC-SA-4.0 | Consistent with our earlier checks (pyiqa repo PolyForm Noncommercial; ONNX copies CC BY-NC-SA). Do not ship those weights. |
| DINOv2 code and weights are Apache-2.0 | Consistent with the earlier subagent notes (model card); training-data licence (LVD-142M) not verified. |
| OpenCV Apache-2.0, Faiss MIT, LightGBM MIT, MediaPipe repo Apache-2.0 | MediaPipe repo LICENSE verified Apache-2.0; others are standard and unchanged. |
| MediaPipe BlazeFace, FaceMesh V2 and Blendshape V2 model cards are Apache-2.0 | **Only partly confirmed.** The BlazeFace short-range card links the Apache-2.0 licence URL, but the card text could not be read and no training data is named; the other two cards were not opened. Treat as "review". |
| PaddleDetection Apache-2.0 | Repo licence only; checkpoint and COCO-type training-data terms unread. Optional; not needed for first tests. |
| LAION aesthetic predictor MIT | Repo licence; upstream data (AVA and others) flagged earlier. Low-weight optional feature. |
| Specific thresholds (black frame, clipping, 3 s window, DINO 0.90 to 0.97, pHash 6 to 10) | Search ranges from the report, not literature values. Must be tuned on our data. |
| Reported benchmark figures and dataset sizes (SPAQ 11,125, KonIQ 10,073, KADID 10,125) | Match what we found earlier. |

## What we can and cannot label today

Read-only scan of the server library (18 October 2026):

- `_winners` folders hold Image Triage's winner copies: China '26 (76 files, about 38 shots), China 8-26 (36 files, about 18
  shots) and one in China '26/jpg (1 file).
- 391 XMP sidecars: 100 carry the label "Accepted"; 337 have no rating; 48 are rated 0 and 6 rated 1; no "Rejected" label.
- Edited export folders: China '26/Raw Files/Edited (69), Caddo Lake 11-25/Edited (10), an astro "final" folder (7).
- Per-folder AI databases hold no ratings; the app's decision store on the desktop holds 7 decisions.

So there are on the order of 200 known keepers and almost no recorded rejects or burst picks. That is enough for a first
keeper-recall safety check on pass 1, and far too little to train or fairly evaluate passes 2 and 3. Burst winners and
junk examples need to be labelled; the app itself is the labelling tool, so v5 should record decisions as the owner culls.

## Grouping spike: time/EXIF/pHash against DINOv2 (18 October 2026)

Off-program test on 3,403 NEF previews from four albums (Japan 4-24, Iceland 10-24, WhiteSands '26, Engagement Trip/Canada
10-25). Scripts in `scripts/ai_v5/`, data in `docs/ai_v5_spike/`. The owner judged 256 neighbouring-frame pairs blind ("would
you keep only one of these?"): 230 same moment, 26 different. The sample over-represented the pairs where a rough
time-and-hash rule and a rough DINOv2 rule disagreed, plus agreeing controls and far-apart pairs, and results are weighted
back to the population. DINOv2 here is `Skulleton12/DinoV2` (ViT-B/14, Apache-2.0 tag), mapped into timm.

| Measure | Result |
|---|---|
| AUROC, short time gap alone | 0.90 (weighted 0.93) |
| AUROC, low pHash distance alone | 0.94 (weighted 0.95) |
| AUROC, high DINOv2 cosine alone | 0.95 (weighted 0.96) |
| Cross-validated logistic model, classical features (time gap, pHash, same settings, exposure-bias change) | 0.948 |
| Same, DINOv2 features (time gap, cosine) | 0.969 |
| Same, all features | 0.964 |
| Classical minus DINO, paired bootstrap 95% interval | -0.020 [-0.048, +0.006]: no reliable difference |
| Combined minus classical | +0.016 [+0.002, +0.032]: a small gain |
| Combined minus DINO | -0.004 [-0.023, +0.014]: no reliable difference |
| Same-moment pairs kept while wrongly merging at most 5% of different-moment pairs | time alone 79%, pHash alone 88%, DINOv2 alone 92% |

Findings:

- **No reliable difference between the two ideas.** DINOv2 is nominally ahead (0.969 against 0.948; 92% against 88% at a 5%
  false-merge limit), but the gap is inside the noise: with 26 "different" pairs the interval spans zero.
- **The disagreement pairs were almost all genuinely the same moment** (98% where the classical rule said same and DINO
  said different; 99% the other way). So each rough rule was too strict and missed real burst pairs; the tuned cut-offs are
  much looser: pHash distance up to about 22 (not 12), DINOv2 cosine about 0.80 (not 0.93), time gap about 8 to 20 s.
- **The owner's idea of "same moment" is broad.** 88% of neighbouring frames within a minute were judged alternates, because
  the albums are dense re-takes of one scene. Group size policy therefore matters for pass 2 (see open decisions).
- **DINOv2 can over-merge:** a different-moment pair reached cosine 0.978, and different-moment pairs reached pHash distance 10.
- **Cost:** DINOv2 on CPU only (Xeon E5-2698 v3): 332 ms per photo on 4 threads, 181 ms on 8, 130 ms on 32; on the V100,
  1.6 ms per photo. Time, EXIF and pHash cost almost nothing.

Caveats: adjacent-frame pairs only (transitive chaining untested); one photographer, four albums, one judge; only 26
different-moment pairs, so the power to detect a difference below about 0.03 AUROC is poor; DINOv2 at 224 by 336 px and CLS
token only.

Conclusion for v5: group with time, EXIF and pHash (the app already has most of it). DINOv2 has not earned its CPU cost for
grouping. Revisit only with a larger set that targets hard "different" pairs.

### Grouping spike, round 2 (18 October 2026)

Wider and harder: 8,745 photos from Alaska Aug '21 (the owner's and Lamont's photos, two photographers), Banff 8-25, China '26
and Engagement Trip; 508 judged pairs (247 hard pairs chosen where two models disagreed or were unsure, 261 steps through
29 ten-frame sequences), 71 hard-pair and 17 step "different" answers; 20 repeated round 1 pairs to measure the judge. The
classical side got a stronger feature set (dHash, brightness-normalised pHash, low-resolution SSIM, colour histogram, ORB
geometric match, shutter and orientation change) and DINOv2 got five variants. All models are scored leave-one-album-out.
Files in `docs/ai_v5_spike/round2/`; scripts `grouping_spike2_*.py` and `spike_common.py`.

| Leave-one-album-out AUROC | pooled | hard pairs | sequence steps | Lamont only |
|---|---|---|---|---|
| classical, basic (round 1 set) | 0.859 | 0.717 | 0.961 | 0.776 |
| classical, strong (logistic) | 0.928 | 0.868 | 0.965 | 0.919 |
| classical, strong (boosted trees) | 0.914 | 0.836 | 0.950 | 0.917 |
| DINOv2 CLS | 0.913 | 0.864 | 0.952 | 0.827 |
| DINOv2 patch-mean | 0.919 | 0.861 | 0.969 | 0.822 |
| strong classical + DINOv2 CLS | 0.941 | 0.911 | 0.967 | 0.925 |

Paired bootstrap (95% interval): strong classical against basic classical +0.069 [+0.042, +0.099] pooled and +0.151
[+0.091, +0.211] on hard pairs, so the extra cheap features mattered a great deal. DINOv2 CLS against strong classical -0.015
[-0.042, +0.012] pooled: no reliable difference (against the boosted-tree version, -0.001 [-0.030, +0.029]). Strong classical
plus DINOv2 against strong classical alone: +0.013 [-0.003, +0.029] pooled; +0.043 [+0.009, +0.080] on hard pairs; +0.002
[-0.009, +0.016] on ordinary sequence steps.

**Pre-registered decision rule** (adopt DINOv2 only if the gain is at least +0.02 with the interval above zero on both hard
pairs and sequence steps): met on hard pairs, not met on sequence steps, so **DINOv2 is not adopted**. On ordinary
consecutive frames both approaches are near the judge's ceiling (about 0.965) and the interval excludes any gain above
+0.016.

Sequence grouping (all 1,305 frame pairs in the 29 strips, 290 of them different-moment; average linkage; false merges at a
matched share of same-moment pairs kept together, lower is better; pairs inside a strip are not independent, so no
intervals):

| Keep at least this share of same-moment pairs | 0.99 | 0.95 | 0.90 | 0.80 |
|---|---|---|---|---|
| strong classical | 190 | 137 | 125 | 62 |
| DINOv2 CLS alone | 172 | 156 | 150 | 125 |
| strong classical + DINOv2 | 169 | 145 | 121 | 43 |

Other findings:

- **Judge consistency:** 18 of 20 repeated round 1 pairs got the same answer (90%); the two changes were "different" to
  "same". That caps what any method can show.
- **Single scores, AUROC on all judged pairs:** DINOv2 patch-mean cosine 0.935, CLS 0.919, ORB match 0.884, SSIM 0.870, colour
  histogram 0.852, pHash 0.830, time gap 0.794. On hard pairs the best classical single score (ORB, 0.794) trails DINOv2 (0.88 to 0.89).
- **Second photographer:** on Lamont's photos strong classical (0.919) was well ahead of DINOv2 CLS (0.827); small sample.
- **Gated use:** running DINOv2 only where the classical model is unsure (probability between 0.3 and 0.7: 18% of the judged pairs,
  only 8% of ordinary consecutive-frame pairs in the 0.2 to 0.8 band) lifts hard-pair AUROC from 0.868 to 0.900 (full
  combination: 0.911) and pooled from 0.928 to 0.934 (full: 0.941). A possible cheap refinement later; not needed now.
- **Cost:** DINOv2 ViT-B on CPU alone 130 to 330 ms per photo (older Xeons); all the classical features are cheap.

Conclusion: group with time, EXIF and the strong classical feature set (time gap, pHash, dHash, SSIM, colour histogram, ORB,
shooting settings). DINOv2 is not part of v1 or any later version of v5 (owner's decision).

### Scene-stacking spike: a job that favours DINOv2 (18 October 2026)

Round 2 left DINOv2 out of burst grouping. This spike tests a different culling aid where pixel methods fail by design:
finding the other photos of the same scene taken at another time ("scene stacks"), so a shoot could be browsed one stack per
scene and keepers checked for redundancy across bursts. 80 query photos (16 each from Alaska Aug '21 by the owner and by
Lamont, Banff 8-25, China '26, Engagement Trip). For each, three systems ranked every other photo in the album, excluding the
query's own burst (same stream within 60 s): DINOv2 patch-mean cosine; ORB feature matching with a homography check
(exhaustive over the album); and a fusion of colour histogram, tiny-image correlation and pHash. The top 4 of each were pooled
and shown blind; the owner ticked the ones that are the same place **and the same subject** (a different bear at the same
river did not count). Files in `docs/ai_v5_spike/scene/`; scripts `scene_spike_*.py`.

| System | precision@4 | found at least one in its top 4 |
|---|---|---|
| DINOv2 | 0.484 | 0.575 |
| ORB geometry | 0.378 | 0.450 |
| colour/tiny-image/pHash | 0.281 | 0.438 |
| On the 53 queries that had any same-scene photo in the pool: DINOv2 | 0.731 | 0.868 |
| Same 53 queries: ORB | 0.571 | 0.679 |

Pre-registered rule (DINOv2 adds value only if precision@4 beats the better classical system by at least +0.10 with the
interval above zero): **met, narrowly**: +0.106, 95% interval [+0.009, +0.206].

Robustness: without Lamont's photos the gain is +0.062 [-0.047, +0.176], which is not reliable; on Lamont's photos alone it is
+0.281 [+0.109, +0.469]. Per album and photographer, DINOv2 was ahead in four of five cells (Alaska/Lamont 0.88 against ORB 0.59;
Alaska/owner 0.36 against 0.19; China '26 0.28 against 0.17), level on Engagement Trip (0.34 each) and slightly behind on
Banff (0.56 against 0.59). Combining DINOv2 and ORB by rank fusion did not beat DINOv2 alone (0.438 against 0.484, interval
[-0.106, +0.013]), though each system also found photos the others missed (DINOv2 89, ORB 69, colour/pHash 47). 27 of the 80
queries had no same-scene photo at all, including the last four (q76 to q79), which may have been rushed; leaving those four
out changes little (+0.112 [+0.007, +0.217]).

Cost: exhaustive ORB matching took about 4 ms per pair (216,000 pairs in about 34 s on 24 processes), which grows with the
square of the album size: roughly 12.5 million pairs for a 5,000-photo album, about 34 minutes on 24 cores or hours on a
4-core machine, unless a prefilter is added. DINOv2 needs one embedding per photo (130 to 330 ms on CPU, about 14 minutes for
5,000 photos on 8 threads) and then a cheap vector search.

Reading: DINOv2 is a better fit than the classical systems for finding the same scene at another time, strongest where the
subject and setting are distinctive (wildlife) and no better where geometry or composition carries it (landscape,
portraits). The advantage is modest in absolute terms (about half of top results correct) and depends partly on one
subset. Whether scene stacks are a feature worth building is a separate, undecided product question.

### Burst-pick baselines from the owner's own keeper marks (18 October 2026)

To test a pass-2 picker without a labelling session, the owner's keeper marks (winner copies, "Accepted" XMP labels, edited
exports) were matched to bursts (consecutive frames at most 5 s apart with pHash distance at most 20, 2 to 10 frames, in
China '26 and China 8-26). 63 bursts contain exactly one known keeper (mean 4.1 frames); that frame is the "pick". Weak ground
truth: an unpicked frame is not necessarily bad. Script `scripts/ai_v5/burst_pick_dataset.py`.

Top-1 pick rate, 95% bootstrap interval: random 0.313 [0.280, 0.347]; first frame 0.238; last frame 0.254; sharpest by global
Laplacian 0.317 [0.206, 0.429]; sharpest by centre Laplacian 0.254; sharpest by Tenengrad 0.317; least clipped highlights
0.349 [0.238, 0.476]. Every simple measurement rule is indistinguishable from chance on these bursts. So within a burst the
owner's pick is not explained by sharpness or clipping alone, which supports looking for a model that sees expression, eyes,
subject and moment.

## Experiments, in order

1. **Pass-1 features plus keeper-recall check.** Build the technical feature extractor (OpenCV: luminance percentiles,
   clipping fractions, Laplacian and Tenengrad, anisotropy, Immerkaer noise) and run the gates over every known keeper
   (winners, "Accepted", edited exports). Report how many keepers any rule would have rejected, per rule. Calibrate against
   the synthetic damage set for defect severity.
2. **Burst grouping.** Done as a spike (above): time, EXIF and pHash, with medoid consistency; DINOv2 optional and unproven.
3. **Face signals.** MediaPipe face detection and landmarks, blink and eye-region sharpness; local CPU timings.
4. **Labelling pass.** The owner marks the keeper in a few hundred bursts and flags junk, then passes 2 and 3 are evaluated
   leave-one-album-out with the ablations from the report, including technical-only against technical plus DINO context.
5. **Pass 3 model.** LightGBM ranker and calibrated keep classifier once labels exist.

Evaluation rules: split by album or event, never by frame; bootstrap by album; report false-reject rate of known keepers
first; compare against trivial baselines (random, sharpest frame, latest frame).

## Open decisions

- How the owner will supply burst and junk labels (a short labelling session in the app, or passive capture while culling).
- Whether to keep the synthetic-damage model as one blur and noise input (only if it beats a plain sharpness measure on
  held-out real defects).
- Whether any learned IQA is ever shipped, given that no clean pretrained model was found.
- Group size policy: with the owner's broad idea of "same moment", how many frames pass 2 keeps per group (one winner, or the top few when compositions differ).

## Zero-shot VLM burst-pick spike (2026-10-18)

Qwen3-VL-4B and 2B (Apache-2.0, fp16, 768 px) asked to pick the owner's kept frame from 60 bursts (bursts over 9 frames dropped;
next-token readout). Pre-registered rule: top-1 >= 0.45 and bootstrap interval of the gain over the best simple baseline above 0,
and pairwise accuracy interval above 0.5.

| model / task | top-1 | gain over least-clipped baseline | pair accuracy |
|---|---|---|---|
| 4B listwise | 0.400 | +0.067 [-0.100, +0.233] | 0.551 [0.445, 0.655] |
| 4B pairwise | 0.383 | +0.050 [-0.117, +0.217] | 0.579 [0.473, 0.682] |
| 4B rating | 0.333 | +0.000 | 0.547 |
| 2B (all tasks) | 0.20 to 0.28 | negative | 0.37 to 0.55 |

Verdict: the rule is not met by any model or task. 4B is slightly above chance (random is about 0.31) but not distinguishable
from the simple baselines; 2B is at or below chance. Accuracy falls as bursts grow (4B pairwise: 0.71 on 2-frame bursts, about
0.2 on 4+). The owner's pick is weak ground truth, so a ceiling is unknown, but a zero-shot VLM is not a pass-3 solution as is.
Scripts: scripts/ai_v5/vlm_burst_spike.py, vlm_burst_analyze.py.

## Facet burst logic, run as intended (2026-10-18)

Facet (github.com/ncoevoet/facet, MIT) installed in its own venv on the server, commercial licensing mode (permissive models only:
Q-ReAlign Mini aesthetic, SigLIP 2, MediaPipe face landmarks, YuNet/SFace, BiRefNet), scanning the 257 NEFs of the 63 test bursts.
Needed one fix on a V100: Q-ReAlign loaded in bf16 ("auto"), which Volta cannot run, so it hung; forcing fp16 fixed it.
Cost: about 5.6 s per photo including model loading on a V100 (24 min for 257).

Facet's burst blend (0.4 aggregate + 0.25 aesthetic + 0.2 sharpness + 0.15 no-blink), top-1 on the owner's picks:

| method | top-1 | gain over least-clipped baseline |
|---|---|---|
| random | 0.313 | |
| least clipped highlights | 0.349 | |
| Facet blend (defaults) | 0.286 [0.175, 0.397] | -0.063 [-0.238, +0.111] |
| aesthetic only | 0.381 | +0.032 [-0.127, +0.190] |
| aggregate only | 0.365 | +0.016 |
| tech_sharpness only | 0.206 | -0.143 |
| eyes open, no blink | 0.238 | -0.111 |

Verdict: fails the pre-registered rule; indistinguishable from chance. Scripts: scripts/ai_v5/facet_burst_analyze.py.

## MANIQA (IIGROUP/MANIQA, Apache-2.0 code) as a burst picker (2026-10-18)

KonIQ-10k checkpoint (543 MB), 20 fixed-seed 224 px crops per 1280 px frame, 257 frames, 0.61 s per frame on a V100.
Top-1 on the owner's picks 0.349 [0.238, 0.476] (random 0.313, least clipped 0.349); gain over best baseline +0.000 [-0.159, +0.159];
pair accuracy 0.542 [0.445, 0.640]. Fails the pre-registered rule, so the weights/training-data licence check was not pursued.
Pattern across everything tried (VLM judges, Facet's blend, Q-ReAlign, MANIQA): generic quality scores do not predict the owner's picks.
