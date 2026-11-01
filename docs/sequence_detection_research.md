# Sequence (bracket and burst) detection: research notes

Rebuilt on 1 November 2026. The original notes were lost with a corrupted environment before they were committed, so this is
a fresh pass, not a copy. Each item is marked **verified** (opened at the primary page this session), **repo** (read in this
repository), **report** (a claim from the pasted deep-research report, not opened at its source) or **analysis** (my reasoning,
not evidence). No model was downloaded or trained.

## What the repo already does and has decided

- **Bracket detection today (repo, `image_triage/brackets.py`).** `BracketDetector` walks neighbours of a photo and calls them
  compatible when EXIF `DateTimeOriginal` differs by 2 s or less (whole seconds, no subsecond), lens matches, focal length is
  within 1.5 mm, aperture within 0.3 and ISO within 5. The group counts as a bracket when at least two distinct exposure times
  exist and all timestamps fall within 2 s. Sizes are limited to 2, 3, 5, 7 and 9, capped at 9. It reads no image content and
  no MakerNote.
- **Why this fails (analysis).** A slow tripod bracket (long exposures) exceeds 2 s and splits. Two separate 3-shot brackets
  taken seconds apart merge into one candidate and are cut to a supported size arbitrarily. Because it has no scene check,
  an unrelated pair that happens to share settings and differ in shutter looks like a bracket. Equal-ISO and equal-aperture
  requirements also reject brackets made with ISO or aperture variation.
- **EXIF reader (repo, `image_triage/metadata.py`).** Reads `EXIF DateTimeOriginal`, exposure time, FNumber, ISO, focal length,
  lens and dimensions. It does not read subsecond time, `ExposureBiasValue`, or any bracket or sequence MakerNote field.
  `scripts/ai_v5/*_extract.py` and `scripts/iqa_synth/nef_preview.py` already parse the subsecond tag in the spike code.
- **Burst grouping spikes (repo, `docs/ai_v5_plan.md`, 18 Oct 2026).** Round 2 judged 508 pairs across 8,745 photos from four
  albums and two photographers, scored leave-one-album-out. A strong classical set (time gap, pHash, dHash, brightness-
  normalised pHash, low-resolution SSIM, colour histogram, ORB geometry, shutter and orientation change) reached AUROC 0.928
  pooled, 0.868 on hard pairs. DINOv2 CLS reached 0.913 and 0.864. The combination reached 0.941 and 0.911. The
  pre-registered rule for adopting DINOv2 was met on hard pairs but not on ordinary sequence steps, so **the owner decided
  DINOv2 is not part of v1 or any later v5 version**. Judge self-consistency was 90% (18 of 20 repeated pairs), which caps
  what any method can show. A separate scene-stacking spike did favour DINOv2 (precision@4 0.484 against 0.378 for ORB), but
  that is a different job from burst grouping.
- **Consequence for this plan (analysis).** The earlier report's default of DINOv2 as the encoder conflicts with that decision.
  Any sequence model here should start from the strong classical features and the already-cached TinyCLIP embedding, and bring
  in DINOv2 only if the owner reopens the decision with evidence on bracket boundaries specifically.
- **TinyCLIP licence (repo, `docs/ai_model_licence_audit.md`).** MIT on the Hugging Face card; the LICENSE text in
  `microsoft/Cream` has not been opened and its training-data terms are unclear (rated Amber).

## Camera metadata that identifies brackets and bursts

All of this is **verified** against the ExifTool tag-name pages, with the caveat that the Nikon and Canon pages were
truncated by the fetch tool (about the first 100,000 of 255,000 and 215,000 characters), so other tags may exist. Decoding
varies by camera model, and values may be stripped on export.

| Maker | Tag (table) | Meaning |
|---|---|---|
| Panasonic | `BurstMode` (0x002a) | 0 Off, 1 On, 2 Auto Exposure Bracketing, 3 Focus Bracketing, 4 Unlimited, 8 White Balance Bracketing, 17 On (flash), 18 Aperture Bracketing. Decoding may vary by model. |
| Panasonic | `SequenceNumber` (0x002b) | int32u, no values listed |
| Panasonic | `BracketSettings` (0x0045) | 0 none; 1 to 6 = 3, 5 or 7 images in 0/-/+ or -/0/+ order |
| Panasonic | `FocusBracket` (0x00bd) | signed focus offset (positive further, negative closer) |
| Panasonic | `MergedImages` (0x0076), `BurstSpeed` (0x0077), `HDR` (0x009e) | merged-frame count, images per second, HDR strength |
| Canon | `AutoExposureBracketing` (ShotInfo idx 16), `AEBBracketValue` (idx 17) | AEB on and which shot; bracket step |
| Canon | `SequenceNumber` (ShotInfo idx 9) | valid only for some models |
| Canon | `ContinuousDrive` (CameraSettings idx 5), `FocusBracketing` (CameraSettings idx 50) | drive mode; focus bracketing 0 or 1 |
| Canon | `FocusBracketingInfo` (0x4053), `RawBurstModeRoll` (0x403f) | link to separate tables not read |
| Nikon | `AEBracketingSteps`, `WBBracketingSteps` (BracketingInfoD810 and D500, ShotInfoD4S) | bracket step sizes, model-specific tables |
| Nikon | `ExposureBracketValue` (0x0019), `ShootingMode` (0x0089) | bracket value; bit 4 exposure, bit 6 white balance, bit 8 D-Lighting bracketing |
| Nikon | `BurstShotNumber` and `BurstStart*` (MakerNotes0x56) | burst position and start slot, folder and image number |
| Nikon | `FocusShiftShooting`, `FocusShiftNumberShots`, `IntervalShooting`, `IntervalFrame`, `MultiExposureShots`, `PixelShiftNumberShots` | focus shift, interval and multi-exposure sequences on newer bodies |
| Nikon | `ShutterCount` | cumulative count, **not** a per-burst number |
| Fujifilm | `AutoBracketing` (Off, On, Pre-shot) | seen on an ExifTool forum thread for the X-T3 only; that forum reply also says Fujifilm changes its use of metadata between models |

The first search also showed a forum report that a Panasonic GX9 records `BurstMode: Focus Bracketing` with an incrementing
`SequenceNumber`. I did not find Sony, Olympus or OM System bracket tags and did not look.

Two cautions (analysis). The ExifTool pages list what the tags are, not which camera models populate them, so support has to
be measured on real files. A bracket tag proves intent for its own frames but not that a frame belongs to the same scene
after a user deletes or reorders files.

## Pretrained temporal components

- **TransNet V2** (shot-boundary detection). Repo is MIT-licensed (**verified**). It detects shot transitions in video and
  takes video input. The page states no separate licence for weights, and a PyTorch port's weights have unstated provenance,
  so weight licensing is unconfirmed. Its benchmarks are video sets (ClipShots, BBC Planet Earth, RAI). It targets cuts and
  dissolves, not photo capture patterns. (analysis) Not directly usable here.
- **ASFormer** (action-segmentation transformer, BMVC 2021). Repo is MIT-licensed with pretrained weights for 50Salads,
  Breakfast and GTEA (**verified**). Those are action-video datasets. (analysis) The architecture idea of refining
  frame-wise labels over a sequence is relevant, but the weights are not, and nothing was found suggesting a ready-made photo
  sequence model.
- **DINOv2.** Reported as Apache 2.0 for code and weights in secondary sources (a Hugging Face listing and GitHub discussion
  threads), with NVIDIA's separately packaged NV-DINOv2 under a different licence. Not checked at Meta's repository, so treat
  as **report**. See above for the repo's decision not to adopt it for burst grouping.
- **LightGlue and LoFTR** (geometric matching) were recommended by the report. Their licences and weights were not checked.
  Note that the repo's spike already used ORB geometry, which scored 0.884 AUROC alone.

## Temporal-model options compared

This section is **analysis**, not evidence. None of it has been run.

| Option | Input | Fit | Main risk |
|---|---|---|---|
| Improve the rules: subsecond time, MakerNote tags, exposure-bias ladder, drop the equal-ISO/aperture requirement | Metadata only | Cheapest; high precision on cameras that write tags | Silent on files without tags |
| Pairwise classifier on the strong classical features (the spike's logistic model) plus a partitioning step (dynamic programming over neighbours) | Existing features | Already measured at 0.928 AUROC pooled; interpretable | Pairwise only; blind to ladder patterns across 9 frames |
| Bidirectional GRU or 1D temporal CNN over per-photo and per-gap features | Same features, window of 16 to 32 | Learns monotonic EV ladders, resets, centre-first order 0, -1, +1, -2, +2; small and fast on CPU | Needs labelled sessions to avoid shortcuts such as "every nine frames is a bracket" |
| Small temporal transformer, 2 to 4 layers, d_model about 256 | Same | Same capability as above; attention can use a distant frame to explain an extreme one | More parameters than the data may justify; no evidence yet it beats a GRU or TCN |

A defensible order is to run all four on identical labelled data, as the earlier conversation proposed, and accept the simplest
one that meets the targets. The transformer should have to earn its place against the GRU or temporal CNN.

## Targets and data (from the pasted report, unverified)

Everything here is the report's proposal, not a demonstrated result: false-merge rate at or below 0.5%, at or below 0.1% in the
high-confidence tier, exact-group accuracy at or above 98%, exposure-bracket recall at or above 99%, focus-bracket recall at or
above 98%. Report false merges separately, split by photographer, session and camera, never by image, resample sessions for
confidence intervals, and plot coverage against false-merge rate for the abstention option. The report's dataset figures (Real-HDRV
500 pairs, HDR+ 3,640 bursts, BurstSR 200 bursts, 94 focus-stack bursts) were not opened at their sources. They are seed
positives for exposure, burst and focus; none has session-level boundaries or hard negatives, so the owner's own sessions
(including the failed shoot that triggered this work) are the evaluation set that matters.

## Recommended next steps

1. Label the failed shoot and a few other sessions at session level, with a physical-capture label kept separate from any
   near-duplicate label. Commit the labels, not only the photos' paths.
2. Extend the EXIF reader: subsecond time, `ExposureBiasValue`, and the maker bracket and sequence fields above, kept as
   optional evidence with presence flags. Never fill a missing value.
3. Build the evaluation harness first: false merges, missed brackets, exact-group rate, by session.
4. Measure the current `BracketDetector` on those sessions as the baseline.
5. Then compare the four options in the table on the same folds.

## Not verified

- Nikon and Canon ExifTool pages were only partly read; Sony and others unread.
- Which camera bodies actually populate these fields.
- Weights licences for TransNet V2, LightGlue and LoFTR; the DINOv2 licence at its primary repository.
- Every dataset size and every numeric target quoted from the report.
- The `microsoft/Cream` TinyCLIP licence text.

## Sources

- ExifTool tag names: [Nikon](https://exiftool.org/TagNames/Nikon.html), [Canon](https://exiftool.org/TagNames/Canon.html),
  [Panasonic](https://exiftool.org/TagNames/Panasonic.html)
- [ExifTool forum: Fujifilm "Auto Bracketing"](https://exiftool.org/forum/index.php?msg=68574),
  [Find All AutoBracketing Series Images](https://exiftool.org/forum/index.php?msg=29597)
- [DPReview: How do you manage focus bracketed files](https://www.dpreview.com/forums/post/66188002)
- [TransNetV2 repository](https://github.com/soCzech/TransNetV2), [ASFormer repository](https://github.com/ChinaYi/ASFormer)
- [DINOv2 licence discussion](https://github.com/facebookresearch/dinov2/issues/364) (secondary)
- In this repo: `docs/ai_v5_plan.md`, `docs/ai_model_licence_audit.md`, `image_triage/brackets.py`, `image_triage/metadata.py`
