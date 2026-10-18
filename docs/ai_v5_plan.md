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

## Experiments, in order

1. **Pass-1 features plus keeper-recall check.** Build the technical feature extractor (OpenCV: luminance percentiles,
   clipping fractions, Laplacian and Tenengrad, anisotropy, Immerkaer noise) and run the gates over every known keeper
   (winners, "Accepted", edited exports). Report how many keepers any rule would have rejected, per rule. Calibrate against
   the synthetic damage set for defect severity.
2. **Burst grouping.** EXIF time window plus DINOv2 similarity plus pHash, with medoid consistency. Inspect on the XQD card
   photos (389 existing review groups) and measure merge and split errors on a small hand-checked sample.
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
