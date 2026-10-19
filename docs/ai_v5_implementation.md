# AI v5 implementation plan: the stack-first cascade in Image Triage

Decided 18 October 2026 with the owner. The design and evidence are in `docs/ai_v5_plan.md`; this file is the build plan.
Acceptance test (owner): the owner's own review of the cascade output on real sets, not a numeric proxy.

## Decisions taken

- **Replaces the current Analyze pipeline.** Analyze runs the cascade and feeds the existing results model, so the aurora Analyze card, bucket
  bar, grid dots, inspector rows and AI filters keep working. The old TOPIQ-based culler stays only behind an advanced setting (it cannot ship
  commercially) and is removed from the default path.
- **Flag and dim, never hide, in the first release.** A frame with a pass-1 reason shows its reason and is dimmed, with one-click restore. Hard
  hiding waits for the library-wide keeper-recall check (target 99.5% on known keepers, per rule).
- **No GPU: cheap passes only; ranking is opt-in.** Stacks, pass 1, pHash collapse and blink run on any machine (about 0.5 s per frame, less with the
  app's own preview cache). MANIQA and Q-ReAlign run automatically on a usable GPU and on a CPU only when switched on, with a time estimate.
- **Licence gate before commercial release, not before building.** MANIQA and Q-ReAlign weights are downloaded on demand and never bundled. Their
  training-data licences are unverified (see `docs/ai_v5_plan.md`); each ranker sits behind a small interface so it can be swapped.

## How it plugs into what exists

- Input: the app's embedded-JPEG extractor (`raw_embedded_jpeg.extract_embedded_jpeg`, which also returns EXIF orientation) and the preview/thumbnail
  caches, decoded with a reduced-size JPEG decode. No RAW decode.
- Output: `AIImageResult` rows built with `build_ai_bundle_from_results`, the same bundle the old pipeline produced:
  - `group_id` / `group_size` = the stack; `rank_in_group` 1 and 2 for the final frames; `cluster_reason` = why the stack was formed.
  - `triggered_tags` = reason codes (`blink`, `soft`, `clipped`, `blown_out`, `duplicate_of:<file>`); `confidence_bucket` and the cull bucket map as:
    final = Winners/Keepers, duplicates and cut-by-ranking = Needs review, pass-1 flagged = Rejects (flagged, not hidden), no-pick stacks = Needs review.
  - A stack with no pick says so in the inspector ("every frame is blown out").
- Existing pieces to reuse, not rewrite: `perceptual_hash.compute_phash` and the pHash cache, `phash_prefilter` (its representative-preserving logic),
  `bursts.py` and `brackets.py` (exposure brackets and panoramas must stay together), `birefnet_worker` for subject masks later, `face_index` for blink.
- Rankers run in the managed AI runtime as worker subprocesses (like `birefnet_worker`), loaded through `ai_model_store`, so the main process never
  imports torch.

## Work packages (each lands with tests, offscreen only)

1. **Cascade core (pure Python, no Qt, no torch).** `image_triage/cascade/`: stack split (consecutive pHash jump >= 18, thresholds in one settings
   object), pass-1 flags relative to the stack (soft < 0.4x best centre sharpness; clipped > max(2x lowest, 10%); blown out >= 40% of pixels at maximum;
   blink from the face index), pHash complete-linkage duplicate collapse (8) keeping the most evenly exposed frame, a `Ranker` protocol, and the
   MANIQA-top-3 then Q-ReAlign-top-2 selection. Golden test: feature rows exported from the 45 + 63 reviewed bursts must reproduce the reviewed decisions.
2. **Cheap-pass feature extraction.** Preview loader on the app's extractor with orientation, measures (centre Laplacian, clipped fraction, extremes,
   pHash), batched and cached by file signature; timings measured in the app, not assumed (the server number was 370 ms for my scratch reader).
3. **Ranker workers.** MANIQA (KonIQ checkpoint, 20 fixed-seed 224 px crops) and Q-ReAlign Mini (aesthetic task, fp16 on GPU, fp32 on CPU) behind the
   `Ranker` protocol; GPU/CPU detection (a V100-class card needs fp16, not bf16: Q-ReAlign hangs on bf16 there); downloads through the model store with
   hash verification; clear errors and a cancel path.
4. **Integration.** An Analyze run task that produces the bundle; progress and cancel through the existing card; reasons and stack info in the
   inspector; dim-and-flag treatment in the grid; setting for ranking on CPU; removal of the old pipeline from the default path.
5. **Library-wide validation.** Pass-1 keeper-recall check on known keepers (winners, Accepted, edited exports), per rule; loosen any rule that costs
   keepers; then decide whether rejects may ever be hidden.
6. **Licence close-out.** Verify the remaining training-data licences (Q-ReAlign: KonIQ, SPAQ, KADID, AGIQA-20K, AVA, LSVQ; MANIQA: KonIQ, KADID, PIPAL)
   from primary sources, or swap the rankers. Replace the three InsightFace-looking face files in the audit (YuNet and SFace are MIT/Apache).

Order: 1 first (everything else depends on it and it is testable alone), then 2 and 3 can proceed in parallel, then 4, then 5 and 6.

## Risks to watch

- Stack boundaries were set from one reviewed album and one example (pHash jump 18); expose the thresholds in settings and review them on a
  different genre before treating them as final.
- Relative flags misfire when a stack has little contrast between frames (seen once: a 0% clipped baseline); the floors exist for that.
- Night, astro and long-exposure shooting will trip the blown-out and extremes measures; genre loosening is still an open decision.
- The review pages were built on a V100 server; MANIQA and Q-ReAlign on a typical desktop CPU are about 7x slower per frame than on that GPU.

## Work package 3 note: ranker workers (`image_triage/cascade/rankers/`)

Built and tested offscreen with fake workers; **the real models have not been run from this package** (no torch runtime on the build machine).
Run them with `IMAGE_TRIAGE_RUN_RANKER_TESTS=1 pytest tests/test_cascade_rankers.py` on a machine with the AI runtime and weights.

- **Interface.** `WorkerRanker(spec, ...)` is a `Scorer` (`keys -> {key: score}`, higher is better); `maniqa_ranker()` / `qrealign_ranker()` build the two.
  A ranker is a `RankerSpec` (`spec.py`) plus a worker `--kind`; swapping one means a new spec, an engine class in `worker.py` and a bundle.
  Options: `device`, `resolve_path` (frame key to file, default the key), `cancel_check`, `progress(done, total)`, `batch_size`, `download`.
  Call `close()` (or use `with`) to stop the worker; the worker stays loaded between calls.
- **Process model.** `worker.py` is started with `-m image_triage.cascade.rankers.worker` in the managed runtime (`ai_env.select_runtime(require_torch=True)` +
  `build_worker_env`), JSON lines over stdin/stdout with a `RANKER ` prefix (`protocol.py`). Nothing in the package imports torch/timm/transformers at import time
  (tested in a subprocess). fp16 on CUDA, fp32 on CPU; device chosen like `birefnet_worker`.
- **Cancellation.** `cancel_check` is polled every 50 ms and between batches; on cancel the worker is killed (a forward pass cannot be interrupted) and
  `RankerCancelled` is raised. The next call starts a fresh worker. Any error also kills the worker so no half-finished request survives.
- **Errors** (all `RankerError`, each with `.remediation`): `RankerUnavailable` (no runtime, torch/timm/transformers missing), `RankerWeightsMissing`,
  `RankerWorkerError` (crash with stderr tail, timeout), `RankerCancelled`. An unreadable image does not fail the batch: it scores `-inf` (ranked last) and is listed in `ranker.failures`.
- **Cost.** `costs.estimate_cost(maniqa_frames, aesthetic_frames, "gpu"|"cpu", measured=...)` and `estimate_cascade_cost(survivors_per_stack, maniqa_keep)`
  (mirrors which frames `run_cascade` would score). Reference timings are the V100 / 8-thread numbers; pass `ranker.seconds_per_frame` as `measured` once a run has timed this machine.
- **MANIQA.** `_maniqa_net.py` is the IIGROUP/MANIQA network (Apache-2.0, attributed in the file), adapted: no `pretrained=True` download, no `.cuda()`, batched scoring.
  Score = mean of 20 crops of 224 px, rng seed 20, from the frame bounded to 1280 px (never enlarged; frames under 225 px are enlarged just enough to crop), `(x-0.5)/0.5`.
- **Q-ReAlign Mini.** Qwen3.5 VL via transformers; chat template with `enable_thinking=False`, question + answer stem appended, softmax over the first token of
  ` excellent/good/fair/poor/bad`, weights 1/.75/.5/.25/0; image bounded to 1024 px. The level-word token choice and `AutoModelForImageTextToText` loading are written from the
  model's files and the task description, not yet run: check the first real scores are in [0, 1] and spread sensibly before trusting them.

**Needs the parent session / owner**
1. **Manifest.** `bundles.register_bundles()` adds `maniqa` and `qrealign` to `ai_manifest.MODEL_BUNDLES` at runtime because this work package may not edit the manifest; move both entries
   (and `MODEL_FILE_DIGESTS`) into `ai_manifest.py` and add capabilities when integrating (package 4). Q-ReAlign is pinned to revision `fe1f45a7` with SHA-256 for every file (captured from Hugging Face).
2. **MANIQA checkpoint has no pinned source or SHA-256.** Official weights are on Google Drive/Baidu only. Until `ckpt_koniq10k.pt` is hosted somewhere pinned (set `bundles.MANIQA_DIGEST` and the bundle repo/revision,
   or the env vars `IMAGE_TRIAGE_MANIQA_MODEL_REPO_ID`/`_REVISION`), installation fails with a message telling the user to place the file in the bundle directory (`IMAGE_TRIAGE_MANIQA_MODEL_DIR`, default `<AI root>/models/Rankers/MANIQA`).
3. **Runtime packages.** `timm` and `einops` are already in the managed runtime's requirements; confirm `transformers==5.14.1` knows `qwen3_5` (loads via `AutoModelForImageTextToText`).
4. **Frozen builds.** The launcher prefers `ai_python_runner` next to the executable, then `AICULLING_PYTHON`, then `sys.executable`; confirm the runner accepts `-m image_triage.cascade.rankers.worker` in a packaged build.

## Work package 2 status: cheap-pass extractor (`image_triage/cascade/extract.py`)

Built and tested (`tests/test_cascade_extract.py`, synthetic images: sharpness, centre-only sharpness, clipped/extremes, orientation for plain JPEG and
for the RAW container tag, cache hit and signature change, blink lookup, cancel, progress). Pixels come from `extract_embedded_jpeg` (or the file itself
for non-RAW), JPEG draft decode, then a box reduce to 1280 px on the long edge; no RAW decode anywhere. `blink` is `False` unless a `blink_lookup` is
passed: the face index does not produce blink yet (`quality/face.py` returns `None` pending EAR calibration).

**Timing, measured in the app's code on this machine** (18 Oct 2026, 42 NEF at 7360x4912 with a 1.8 MB full-size embedded preview, 3 JPEG; warm
OS file cache; Pillow 12.3, numpy 2.4):

| | per frame |
|---|---|
| embedded-JPEG extraction (IFD walk and read) | about 3 ms |
| draft decode (7360 px preview to 1840 px) | about 90 ms |
| measures at 1280 px (hash, Laplacian, clipped, extremes) | about 86 ms |
| 1 worker, wall | 197 ms |
| 2 workers, wall | 106 ms |
| 4 workers, wall (default) | 74 ms |
| cache hit | about 0.5 ms |

A 1,000-frame folder is therefore about 75 s cold with 4 workers and well under a second from cache, against 3.5 s per frame for a RAW decode. A first
read from a cold disk adds the file read (about 1.8 MB per NEF, the IFD walk only seeks to the preview) and was not measured. Worker scaling is
sub-linear because decode and numpy share the GIL between C calls; the cost is dominated by the two numpy passes, so a small saving is possible by
measuring at 960 px if this ever matters.

**Hash drift** (the scratch `phash_hex` used OpenCV INTER_AREA on the full-size frame; OpenCV is not installed here, so the reference is the same
operation done with a full-size Pillow box resize, which is an exact area average; the 433 reviewed NEFs of `cascade_golden.json` are not on this
machine, so real-frame checks used 40 aurora NEFs and 2 other NEFs):

- Photos: our hash differs from the full-size area hash by 0 bits on 34 of 42 NEFs and by 2 bits on the other 8 (max 2); `1292769.jpg` and
  `badlands-moon.jpg` are 0.
- One degenerate image, `jigsaw.jpg` (almost pure white, luma 246 to 255, std 1.5), drifts 20 bits: with no structure the median split is noise.
  Such frames have no usable hash in either implementation; they would also be flagged clipped.
- Against `perceptual_hash.compute_phash` (Lanczos from a draft) the mean distance is 1.2 bits but individual frames reach 26 on the degenerate image,
  which is why the cascade extractor does not use it.
- Consecutive aurora frames measure 0 to 16 bits apart (typical 2 to 10), all below the split jump of 18, so `split_jump` 18 and `duplicate_distance` 8
  keep the same meaning; a drift of at most 2 bits moves a pair across a threshold only when it already sits within 2 bits of it.
