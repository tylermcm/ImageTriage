# AI v5: a three-pass culling pipeline

Planned 18 October 2026, after a ChatGPT deep-research report (pasted into the session) and our own checks. v5 starts over
on the problem definition: culling is three different problems, each measured on its own. No learned taste model on
DINO embeddings (that route was built and tested in v1 and was no better than random).

## The pipeline

1. **Pass 1, junk gate.** Cheap measurements on a normalised preview: black or catastrophically blown frames, defocus and
   motion blur, noise, clipping, subject-region sharpness and exposure. Only catastrophic, high-confidence rules reject;
   everything ambiguous stays a feature and a reason code. Tuned for keeper recall (target 99.5% or better on known keepers),
   not accuracy.
2. **Pass 2, best of a burst.** Candidate pairs from an EXIF time window (start 1.5 to 3 s) plus appearance (DINOv2
   similarity and a perceptual hash), clustered with a medoid-consistency check so frames do not chain across scenes.
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
