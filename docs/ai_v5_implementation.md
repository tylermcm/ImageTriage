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
2. **MANIQA checkpoint: hosted and pinned (19 October 2026).** `ckpt_koniq10k.pt` is at `Skulleton12/maniqa-koniq10k` (revision `18bbbb8a`, SHA-256 in `bundles.MANIQA_DIGEST`); the face landmarker is at `Skulleton12/mediapipe-face-landmarker` (revision `e8098a34`). Both bundles are pinned in `bundles.py`. Still to do: a real download-and-install run through the app, and the weights-licence decision (`docs/ai_v5_model_hosting.md`).
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

## Work package 4 status: app integration (done in code, 18 October 2026)

Analyze now runs the stack-first cascade by default and feeds the existing results model, so the Analyze card, bucket bar, grid dots, inspector
and AI filters work on it unchanged. The older TOPIQ-based culler is still reachable behind a hidden setting (`ai/use_legacy_culler`).

**What was built**
- `cascade/folder.py`: splits a folder into bursts (consecutive frames at most 5 s apart with a picture difference of at most 20, both needed; a
  frame with no capture time is never joined; exposure brackets and unreadable frames end a burst), runs the core per burst, keeps exposure
  brackets together and uncompared, and writes each outcome in plain words ("Flagged: eyes closed ... Dimmed, not removed.").
- `cascade/export.py`: writes the ranked-export CSV, summary JSON and an HTML report in the layout `ai_results.load_ai_bundle` already reads, with
  two new columns (`cascade_role`, `cascade_summary`) and the reason codes in `triggered_tags`.
- `cascade/task.py`: `CascadeRunTask`, a `QRunnable` with the same signals as the old task. Stages: capture times and brackets, previews, deciding, writing.
  Cached features, cancel, progress with an estimate, and a ranker that cannot start is skipped with a note instead of failing the run.
- `cascade/launch.py`: the ranking decision (GPU: automatic; CPU: only with the new setting, after a prompt that gives the per-frame time; no AI runtime:
  skipped with a note) and the worker rankers.
- `ai_run_controller`: `run_ai_pipeline` picks the engine, shared `_start_ai_task`, Analyze no longer demands the AI runtime (the cheap passes need none).
- `ai_results`: `AIImageResult.cascade_role` and `cascade_summary` decide the bucket directly: `pick` = Winners, `final` = Keepers, `rejected` = Rejects
  (flagged), everything else = Needs review. A pick is offered only when a ranker ordered a stack.
- Catalog migration 9 adds `cascade_role`, `cascade_summary` and `triggered_tags` to the cached results so a cached reload keeps the same buckets and reasons.
- Grid: flagged frames are dimmed (never hidden) and the dim follows the "AI tags in grid" setting. Settings: "Ranking on the CPU" (off by default).
- Frames the person already marked as winners are never flagged, collapsed or cut.

**Tests** (offscreen): `test_cascade_folder`, `test_cascade_run`, `test_cascade_integration` (Analyze on the real MainWindow), `test_cascade_launch`, plus
the existing AI suites, updated where they encoded the old behaviour (Analyze used to open AI setup when the runtime was missing).

**Open items found while building**
1. **Blink:** built on MediaPipe Face Landmarker and validated on real frames (see the Blink section); needs `mediapipe` added to the managed runtime requirements before a shipped build runs it.
2. **Rankers:** validated on real frames through the worker code (see the section above); still never run inside the full app window, and the MANIQA checkpoint is hosted and pinned but its download/install has not been run in the app yet (`docs/ai_v5_model_hosting.md`).
3. **Apply AI Decisions** can still move flagged (Reject-bucket) frames to the program recycle bin, after its confirmation dialog and with undo. The dialog does
   not show the reason; it should before hard hiding or auto-applying anything.
4. **Loupe card style** does not dim flagged frames (only the grid card paints the dim).
5. **Progress text** still says "AI review" and the old stage count in a few places; the cascade has four stages.
6. **Thresholds** (stack split 18, soft 0.4, clipped floor 10%, blown out 40%) come from two reviewed albums; expose them and review on other genres.
7. **Keeper-recall check** across the library (package 5) is still needed before any frame is hidden.
8. **Fixed: a crash in the shared RAW marker scan.** The full test suite died twice (Windows access violation) in `raw_embedded_jpeg._scan_jpeg_markers`.
   Cause, reproduced in isolation: `mmap.find` reads past the end of a mapping whose size is an exact multiple of the 4096-byte page, and the process dies instead of
   raising (files of 4096, 8192 and 102400 bytes crashed; 4095, 4097 and 100000 did not). Roughly one RAW in 4,096 has such a size, and the cascade scans every RAW in
   the background, so it would have been seen in the field. Such files are now read normally; regression tests cover the crash (in a subprocess) and that the embedded
   JPEG is still found at page-multiple and ordinary sizes.


**Next step, pending the owner's server run (20 October 2026): the pass-1 audit.** Steps, commands, what to bring back and what we decide afterwards: `docs/pass1_audit_runbook.md`.

## Blink signal (19 October 2026)

The "eyes closed" rule was inert because the app has no blink source (its face code defers blink and uses the AuraFace pack the licence audit flagged). It now comes from
**MediaPipe Face Landmarker** (Apache-2.0 code and model cards), run as a third worker in the same machinery as the two rankers:

- `cascade/blink.py`: the decision (pure). A face counts when it covers at least 1% of the frame; its score is the mean of the `eyeBlinkLeft` and `eyeBlinkRight` blendshapes; a frame scores
  the most closed prominent face (a blinker in a group counts); no face scores 0. Blink = score at or above **0.6**. A per-file score cache (`cascade_blink.json`) means re-analysing a folder
  does not rerun the model.
- `rankers/worker.py`: `--kind blink` (CPU only, no torch). The worker now also opens **RAW files through their embedded preview** (it used PIL only, which cannot open most RAW files, so the
  two rankers would have scored every RAW frame minus infinity), with the container orientation and a reduced-size JPEG decode.
- `task.py`: after features are extracted, every readable non-bracket frame is scored for blink before the cleanup runs; a failing or missing blink worker is skipped with a note, never fatal.
- `launch.py`: the blink check runs whenever the AI runtime is installed (it is cheap and CPU-only); the rankers still need a GPU or the CPU opt-in.

**Validated on real frames** (the server, real MediaPipe 0.10.35, 433 frames from the two reviewed sets): 35 ms per frame on a 1,280 px JPEG, about 207 ms per frame through the RAW path (7360 px
embedded preview) on that server; 68 frames (16%) contain a prominent face; 2 frames score at or above 0.6, 5 at or above 0.5. Looked at by eye: 0.82 was a real blink, 0.65 a squint in strong sun,
0.59 a woman laughing with her eyes shut (missed by a hair), and 0.55 to 0.49 were a man grinning with narrowed but open eyes plus a bald man's relaxed heavy-lidded face (scoring 0.42 to 0.49 in
five frames). Smiles and naturally narrow eyes push the score to about 0.5, so 0.6 is conservative: it catches clear blinks and flags nothing wrongly in this set, at the cost of missing laughing-eyes-shut.

**Not done:** `mediapipe` is not in the managed AI runtime's requirements (`ai_runtime_packages`), so a shipped build skips the blink check with a note until it is added (and the hash-pinned locks
in `packaging/ai_runtime_locks` are regenerated; check Windows Python 3.13 wheel availability first, and use the 0.10.x line with its dependencies pinned, not the newest release, which pulls in
`opencv-contrib-python` and clashes with the runtime's headless OpenCV). The thresholds come from one set of photos of two albums; review them on other genres.

Model files to host: see `docs/ai_v5_model_hosting.md`. The MANIQA checkpoint digest is now pinned in `bundles.py`.

## The real rankers, run through the app's worker code (19 October 2026)

Run on the server's V100 with the real models (`scripts/ai_v5/ranker_parity.py`, `qrealign_same_input.py`), 40 frames spread over the 63 marked bursts:

- **MANIQA worker** (fp16 on CUDA): Spearman **1.000** and mean absolute difference 0.0001 against the fp32 scores validated earlier (range 0.172 to 0.573 in both), no NaNs, **156 ms per frame**
  (the earlier 540 ms used 20 crops one at a time in fp32). So running it in half precision is safe.
- **Q-ReAlign worker** (fp16 on CUDA): runs, **647 ms per frame**. On exactly the same 1,280 px JPEGs it matches Facet's own scorer at Spearman **1.000** (mean difference 0.0004, worst 0.0047). Against
  the aesthetic scores Facet stored from its own scan the correlation is only 0.59, because Facet decoded the RAW files itself and the aesthetic score depends on how the picture is rendered
  (full RAW decode versus the camera's embedded preview). The cascade always uses the embedded preview, so it is consistent with itself; do not mix the two renderings when comparing scores.
- The level-word token handling in the Q-ReAlign worker, flagged earlier as never run, is therefore correct for the Mini model.


## AI runtime hookup, verified on this Windows machine (20 October 2026)

**Packaging.** `mediapipe==0.10.35` is now in the compact (base) runtime: `AI_RUNTIME_BASE_PIP_REQUIREMENTS`, `AI_RUNTIME_BASE_REQUIRED_MODULE_NAMES`, and the four py313 Windows locks (10 new hash-pinned
distributions: mediapipe, absl-py, contourpy, cycler, fonttools, kiwisolver, matplotlib, pyparsing, python-dateutil, six; the rest of its closure was already locked). MediaPipe asks for
`opencv-contrib-python` and `sounddevice`; neither is needed by what the app imports and the contrib wheel shares the `cv2` tree with the headless one, so `AI_RUNTIME_LOCK_EXCLUDED_DISTRIBUTIONS`
keeps them out and `refresh_ai_runtime_lock.py` honours it. The new locks were written by taking wheel hashes from PyPI (the refresh script needs Python 3.13 on the target); pip downloaded every
wheel of the compact lock with `--require-hashes` for cp313/win_amd64 and accepted all of them. Rerun the refresh script on Python 3.13 when convenient to confirm it reproduces them.
Existing installs now report `mediapipe` as missing until the person runs Repair AI.

**The blink worker runs in the compact runtime.** `RankerSpec.needs_torch` (False for blink): it no longer demands the 2.6 GB PyTorch profile.

**Real end-to-end runs, Windows 11, Python 3.13 managed runtime installed by the app's own installer from the locks** (compact base: 55 s; with PyTorch: 138 s; nothing missing in either):
- **Blink:** the app's launcher picked the runtime, downloaded and verified `face_landmarker.task` from the owner's Hugging Face repo, MediaPipe scored real frames: 0.822 and 0.648 on the two real
  blinks (server: 0.823, 0.648), 39 ms per frame once warm. The whole cascade ran through `CascadeRunTask` with the real worker: four stages, "Checking for closed eyes" progress, `blink_checked: true`.
- **Rankers (CPU):** both downloaded from their hosts and verified, both scored real frames, no failures. Speed on a 4-core i5-8265U laptop: **MANIQA 56 s per frame, Q-ReAlign 25 s per frame**
  (the server CPU measured 3.6 s and 3.4 s). CPU ranking is therefore impractical on a laptop; the opt-in prompt now states the measured range (about 7 s per frame on a fast workstation, over a minute on a laptop)
  instead of one optimistic number. GPU ranking is unaffected (0.16 s and 0.65 s per frame on the V100).

**Finding: an old Visual C++ runtime breaks the AI runtime on Windows.** This PC has the Microsoft Visual C++ Redistributable 14.36 (2023). With it, importing `onnxruntime` (the app's own runtime
validation) and `torch` (`c10.dll`) fails with "WinError 1114: A dynamic link library (DLL) initialization routine failed", so Set Up AI rolls back with no useful message. Copying the 14.44 runtime DLLs
next to torch's own made the MANIQA and Q-ReAlign workers start (verified; MediaPipe did not need it). `image_triage/ai_platform.py` now reads the installed version from the registry and, on a DLL
failure, tells the person to install the latest x64 Redistributable (https://aka.ms/vs/17/release/vc_redist.x64.exe) with the version it found; it is wired into the runtime-install validation message and
into ranker worker failures. **This PC still needs that Redistributable installed** before the managed AI runtime will validate.

**Also changed.** A frame that pass 1 flags but that is kept anyway (alone, or because every frame in its stack was flagged) is now `flagged` in **Needs review** with the reason, not a Keeper
(the real run showed two lone blinking frames listed as keepers). Apply AI Decisions shows the reason under each frame it would recycle (plain labels for the cascade flags via `ai_why.FLAG_LABELS`).
