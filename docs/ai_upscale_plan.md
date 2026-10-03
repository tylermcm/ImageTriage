# AI upscaling (Real-ESRGAN) — backend implementation plan

Status: **plan only, nothing implemented in the repo.** Written 2026-10-03 and deferred by the user until the
upcoming librawops editor changes land. Frontend is explicitly out of scope for this plan.

Evidence and the decisions behind it are recorded in `docs/audit_and_remediation_plan.md`, section
"Super resolution exploration (2026-10-03)". This file turns that into a build plan.

## 1. Scope

**In scope (backend only):** an upscaling capability that takes a raster file and writes a 2x (or 4x) result file,
using the official Real-ESRGAN `x2plus` and `x4plus` models exported to ONNX, run on the managed AI runtime, with
progress and cancel, pinned model files, a health/availability check, and packaging.

**Out of scope:** any UI (actions, dialogs, progress bars, settings pages); the librawops render step that
produces the input image; non-AI resampling (librawops owns that — its Catmull-Rom resize was judged good enough);
RAW-domain joint demosaic + super resolution (a separate research track, see the audit plan).

## 2. Decisions already made

| Decision | Source |
|---|---|
| Implement **both** `x2plus` (native 2x) and `x4plus` (4x); the models are cheap and the architecture is the same | user, 2026-10-03 |
| Image Triage owns only the AI side; classical resampling stays in librawops | user, 2026-10-03 |
| No Lanczos work in Image Triage | user, 2026-10-03 |
| Engine is **ONNX Runtime in the managed runtime**, not the ncnn executable | proposed in the audit plan; follows `ai_manifest.py` |
| Outputs are new files; originals are never touched | standing design |
| Build is deferred; frontend later | user, 2026-10-03 |

## 3. Evidence the design rests on (all measured 2026-10-03 on the RTX 4080 SUPER)

* ONNX export of both official checkpoints loads with a strict key match and agrees with PyTorch to ~2e-6 (CPU and DirectML).
* The ONNX `x4plus` agrees with the ncnn `x4plus` used in the earlier visual tests at 46 dB mean (35.5 dB worst crop).
* **Tiling margin:** a 96 px margin matches a single pass to 0.06 levels mean with 0 % of pixels more than 2 levels off;
  16 px (near the official default of 10) leaves ~14 % of pixels more than 2 levels off (visible seams).
* **Tile size:** DirectML is correct up to 640 px tiles; at 768 px `x4plus` was ~20x slower (and single passes at 1024 px+ were unusable).
* **Speed (fp32, 640 px tiles):** `x2plus` ~42 s per 45 MP frame to 2x; `x4plus` ~4x slower per crop (2.9 s vs 0.7 s per 512 px crop).
* Ground-truth crop test at 2x (PSNR / SSIM): Catmull-Rom 38.08 / 0.942, Lanczos 38.77 / 0.951, ncnn `x4plus` 33.48 / 0.877,
  ONNX `x4plus` 33.46 / 0.880, ONNX `x2plus` 33.22 / 0.847. The user judges by the images, not these numbers.
* Full-size 45 MP frames, 100 % pixel views: clearly better than interpolation on hair, handwriting, hard edges, fur, night noise;
  costs: painterly foliage, slightly smoother skin, deeper reds/shadows, faint mottling in blank sky.

## 4. Architecture

```
librawops render (1x, rendered file)   <- integration step, later
        |
        v  input_path
 upscale_service  (GUI process, no torch)  --spawns-->  upscale_worker  (managed runtime python, ONNX only)
   request/result/availability/cancel                      reads job JSON, verifies model, tiles, writes <name>.partial -> rename
        |                                                       |
        +---- uses ----> upscale_core (pure numpy/PIL: specs, limits, planner, tiler, provider choice, output + provenance)
```

* One **subprocess per job** (batch work, GPU-bound, one job at a time), unlike the persistent `MaskEngine` host which exists to
  share a CUDA context between interactive masks. Session load time was not measured; if it proves material, a persistent
  host is a later optimisation behind the same service interface.
* The worker is a **standalone script** that imports `upscale_core` as a sibling (the same trick `mask_engine_worker.py` uses),
  because it runs under the managed runtime where the application package is not importable.
* **Input is a plain raster file** (PNG/TIFF/JPEG/WEBP). Rendering RAW or applying edits is the caller's job, so this backend
  does not depend on librawops or Qt and keeps working whichever editor produces the file.

## 5. Module plan

### 5.1 `image_triage/upscale_core.py` (pure; numpy + PIL; no onnxruntime, no Qt)

* `UpscaleModelSpec` (key, name, scale, filename, size, sha256, source checkpoint filename/size/sha256/url) and `MODEL_SPECS`:

  | key | scale | ONNX file | size (bytes) | SHA-256 (ONNX) |
  |---|---|---|---|---|
  | `realesrgan-x2plus` | 2 | `RealESRGAN_x2plus.onnx` | 67,073,419 | `d7168c9a30373ee8b8ce941bf935e2cd69a0e35355742a77cb998d8a3c1274a6` |
  | `realesrgan-x4plus` | 4 | `RealESRGAN_x4plus.onnx` | 67,051,617 | `72fd9556e5019537aa9222902d2f891dbe8cf3b12abbd2891f32988057e3549b` |

  Source checkpoints (fp32): `RealESRGAN_x2plus.pth` 67,061,725 B `49fafd45…266abb` (GitHub release v0.2.1);
  `RealESRGAN_x4plus.pth` 67,040,989 B `4fa0d389…682f1` (GitHub release v0.1.0). The ONNX hash is of *our* export and may change with
  the torch/onnx versions, so the **artifact** is pinned, not the recipe (see B0).
* `verify_model_file(dir, spec, deep)` / `model_status(dir)`: size check always, SHA-256 when deep.
* **Limits:** long side ≤ 65,000 px and ≤ 500 MP total (Camera Raw's own ceilings), plus per-format long-side caps
  (JPEG 65,500, WEBP 16,383). `plan_upscale(width, height, model_key, output_suffix, provider, core, pad, has_alpha)` returns an
  `UpscalePlan` (sizes, tile grid, estimated peak bytes, warnings) or raises `UpscaleError("too_large", …)`.
* **Tiling constants:** `DEFAULT_CORE=448`, `MIN_PAD=96` (refuse smaller), `DEFAULT_PAD=96`, tile cap 640 (the DirectML-verified limit, applied
  to every provider until others are measured); `core` kept even (the 2x model un-shuffles pixel pairs).
* `upscale_array(rgb_uint8, run_tile, scale, core, pad, progress, should_cancel)`: reflect-pad to whole tiles, **every call has the same
  input shape** (avoids provider recompiles), keep only each tile's centre, clip to [0,1], round to uint8. `run_tile` is any callable from
  float32 `1x3xTxT` to `1x3xsTxsT`, so tests use fakes.
* `select_providers(available, device)`: CUDA, then DirectML, then CPU, always ending in CPU; `cpu` device means CPU only.
* Provider self-check helpers: `self_check_input()` (deterministic textured tile) and `providers_agree(candidate, reference, tolerance=5e-3)`.
* Output: `upscale_output_path(source, scale, suffix, folder)` → `<stem>_<scale>x_ai<.ext>` beside the source, never an existing file
  (`" (2)"` suffixing); `save_output(...)` writes `<name>.partial` then renames, refuses to overwrite unless asked, carries ICC and (for JPEG)
  EXIF with orientation reset to 1, and records provenance (`Software` tag / PNG text / TIFF tag): *"Image Triage AI upscale 2x
  (realesrgan-x2plus, model sha256 d7168c9a, <provider>)"*. Alpha is resized with a bicubic filter, not the model; JPEG drops alpha.

### 5.2 `image_triage/upscale_worker.py` (standalone script; ONNX Runtime only)

* CLI: `--job <json file>`. Line protocol on stdout: `PROGRESS {stage, done, total}`, optional `AI_METRIC {…}`, exactly one `RESULT {…}` on success,
  or `ERROR {category, message, remediation}` with exit code 1. Progress is throttled (~4 per second) so large jobs do not flood the pipe.
* `run_job(job, session_factory=None, emit=print)` is the testable core: checks protocol version, **verifies the model file by SHA-256 on every job**,
  refuses an existing output, loads the source (EXIF-orient by default; 8-bit RGB; alpha split off), validates limits *before* any model work, builds the
  session, plans with the real provider, tiles, writes atomically, emits the result with timings (load / session / inference / save / total).
* `default_session_factory`: tries providers in `select_providers` order; for every non-CPU provider runs the self-check tile and compares with the CPU result;
  a provider that errors, returns non-finite numbers or disagrees is skipped with a warning, ending at CPU. This guards against the silent-wrongness we
  saw (DirectML at oversized tensors) on whichever provider the managed runtime actually offers.
* Job fields: `protocolVersion, inputPath, outputPath, modelKey, modelDir, device, applyOrientation, keepMetadata, jpegQuality, overwrite` (+ optional `core`, `pad`).
* Cancel = kill the process. Because output is renamed into place only when complete, a killed job leaves at most a `<name>.partial` that the service removes.

### 5.3 `image_triage/upscale_service.py` (GUI side; no UI, no Qt widgets)

* `UpscaleRequest` (input_path, model_key, output_path / output_suffix, device, apply_orientation, keep_metadata, jpeg_quality, overwrite, model_dir),
  `UpscaleProgress(stage, done, total)` with a `fraction`, `UpscaleResult`, `UpscaleServiceError(category, remediation)`, `UpscaleAvailability`.
* `run_upscale(request, progress_callback, cancel_event)` — blocking, meant for a worker thread: resolves the runtime with
  `select_runtime(device, require_torch=False)` (ONNX needs only the base profile), builds the worker command with `resolve_ai_python_script_command`,
  the environment with `build_worker_env(…, protocol_version=UPSCALE_PROTOCOL_VERSION)`, writes the job to a temp JSON file, streams lines, watches the
  `cancel_event` and terminates the worker, removes the `.partial`, maps results/errors. `spawn` and `runtime_resolver` are injectable for tests.
* `check_availability(model_dir, deep)` → per model: runtime installed? model file present and exact? with remediation text for the UI/health page.
* `upscale_model_dir()`: env override `IMAGE_TRIAGE_UPSCALE_MODEL_DIR`, else the managed models root (`managed_model_dir("Real-ESRGAN")`) until the
  manifest registration (5.4) supplies the bundle directory.

### 5.4 Manifest, health, packaging (the integration step)

* **`ai_manifest.py`:** one `ModelBundle` `realesrgan` holding *both* ONNX files (atomic install, ~128 MB), with `FileDigest`s from the table above; and one
  `Capability` `upscale` (`modules=("numpy","onnxruntime","PIL")`, `requires_onnx=True`, `worker_module="image_triage.upscale_worker"`,
  `probe_kind="onnx_model"`, `expected_outputs=("output",)`). The existing probe checks one model file per capability, so either probe `x2plus` only or add a
  second capability / extend the probe — decide in B4. Both need the repo-hosted artifact below before they can be registered, which is why registration is last.
* **Hosting (blocker):** `ModelBundle` expects `repo_id` + an immutable `revision` + per-file SHA-256 on Hugging Face. Plan: a repo owned by the user holding
  the two ONNX files at a pinned commit. That makes the user the redistributor, so the weights' licence must be reviewed first (the Real-ESRGAN repo is BSD-3 but
  states no licence for the weights; they were trained on DF2K + OST).
* **Packaging:** add `upscale_worker.py` **and** `upscale_core.py` to `freeze_support.py` as `ai_workers/…` (the worker imports its sibling), add the
  matching assertions to `tests/test_packaging_scripts.py` (same pattern as the other workers); check `setup_msi.py` / `setup_linux.py` module lists.
* **Health:** `ai_health.py` already walks `capability.model_bundles`; confirm the new capability appears, reports a missing model with the remediation text, and does
  not count as "unverified" or "unpinned". Adjust `tests/test_ai_health.py` / `tests/test_ai_model_store.py` where they pin the bundle/capability sets.

## 6. Error categories (stable strings the frontend will branch on)

`runtime_missing`, `runtime_incomplete` (from `select_runtime`), `worker_missing`, `protocol_mismatch`, `model_unavailable`, `unknown_model`, `bad_image`,
`too_large`, `bad_tiling`, `bad_format`, `exists`, `no_provider`, `bad_model_output`, `out_of_memory`, `worker_failed` (unexpected exit, with the last output lines), `cancelled`.

## 7. Test plan

All without a GPU or a model file unless marked.

* **Core:** tiled output equals a whole-image run for a purely local fake model (nearest-neighbour ×s) at awkward sizes (1x1, 3x5, exact multiples, off-by-one);
  equals a reference computed with the same reflect padding for a fake model with a small receptive field when `pad` ≥ its radius; fails (mutation check) when `pad` is
  too small or the crop offsets are wrong; progress counts; cancel between tiles; limits (long side, MP, per-format caps); `choose_tile` obeys margin and cap, keeps core even;
  provider selection for `cpu`/`cuda`/missing providers; `providers_agree` (NaN, shape mismatch, tolerance); output naming (collisions, unknown suffix → png);
  `save_output` atomicity (no partial left on failure, no overwrite, ICC and EXIF carried, orientation reset, provenance text present for jpg/png/tiff, alpha handling).
* **Worker:** `run_job` with an injected fake session factory: result payload fields, `PROGRESS` lines, errors for bad protocol / missing or corrupt model / existing output / oversized image;
  provider fallback when the self-check disagrees (fake provider returning garbage). One test gated on the real model file being present (skipped otherwise): ONNX output matches the
  stored expected values for a fixed 96x96 tile within tolerance.
* **Service:** injected `spawn` returning scripted lines: progress parsing, result mapping, `ERROR` mapping, non-zero exit without `ERROR`, cancel kills the process and removes the `.partial`,
  job file removed, `AIRuntimeUnavailable` mapped to `UpscaleServiceError`, `check_availability` for each missing-piece combination.
* **Packaging / manifest / health:** the assertions in 5.4.
* **Mutation checks (md5-verified restore):** drop the margin check, shrink `pad`, remove the provider self-check, remove the SHA-256 check, overwrite without the flag, skip the `.partial` cleanup.

## 8. Milestones and acceptance

| # | Milestone | Done when |
|---|---|---|
| B0 | Reproducible export and hosting decision | A checked-in `scripts/export_realesrgan_onnx.py` (vendored RRDBNet architecture with its BSD-3 notice; strict key match; ONNX-vs-PyTorch check) reproduces the pinned artifacts or records the new hashes; hosting repo and licence decision recorded here |
| B1 | `upscale_core` + tests | Section 7 core tests pass; reachability report unchanged |
| B2 | `upscale_worker` + tests | Worker tests pass with fakes; the gated real-model test passes on a machine with the files |
| B3 | `upscale_service` + tests | Service tests pass; no Qt imports in the service or core |
| B4 | Manifest, health, packaging | Capability visible in the health report; missing model reports remediation; packaging tests pass |
| B5 | Validation on the real managed runtime | On a machine with the managed GPU profile installed: provider chosen and self-check passes (CUDA, see risks); a full 45 MP frame to 2x finishes; tiling seams absent (single-pass comparison on a mid-size crop); timings recorded here; cancel works |
| B6 | Close-out | Audit plan entry, this plan updated with measured B5 numbers and anything that changed |

## 9. Open decisions and risks

* **Hosting and weights licence** (blocker for B4/B5 and any release): see 5.4.
* **CUDA provider is unvalidated.** All measurements used `onnxruntime-directml` in a scratch environment. The managed GPU profile ships `onnxruntime-gpu` 1.26.0
  (CUDA provider), not DirectML, and the managed runtime is **not installed on the development machine** at the time of writing. B5 must repeat the tile-size and
  numerical checks on the CUDA provider; the self-check and the tile cap exist to catch exactly this, but the cap is a measured DirectML value, not a CUDA one.
* **fp32 only so far.** fp16 should roughly halve time and memory but changes numerics (the self-check tolerance and the visual comparison need redoing); deferred.
* **8-bit pipeline.** The planned worker takes 8-bit RGB. 16-bit input would need a different path (the official code supports it; our tiler does not yet).
* **Quality caveats are model properties, not bugs:** painterly foliage, smoother skin, deeper reds/shadows, mottling in blank sky. The frontend should label results as AI-processed.
* **`x2plus` vs `x4plus`:** both are specified; `x2plus` is the practical default (native 2x, ~4x faster). The 4x pass of `x4plus` is also what the earlier "x4plus downscaled to 2x" evidence used.
* **Memory:** a 45 MP frame to 2x holds ~0.14 GB input + ~0.5 GB output + padded copy; `x4plus` to 4x is ~2.2 GB output. The planner returns an estimate; a hard preflight against free RAM is a possible addition.
* **Process model:** per-job subprocess assumed (section 4); revisit if session load time matters.
* **Interaction with librawops:** none until the integration step; when it happens the input is librawops' rendered output and its Catmull-Rom resize is the non-AI alternative.

## 10. What the frontend will need from this backend (interface only)

`check_availability()` for enabling controls and showing reasons; `UpscaleRequest` for the options (model, output location/format, quality, overwrite); `run_upscale` on a worker thread with
`UpscaleProgress` callbacks and a `threading.Event` for cancel; `UpscaleResult` (paths, sizes, provider, warnings, timings) and `UpscaleServiceError.category/remediation` for messages.

## 11. Reference material

* Unreviewed, untested **draft prototypes** of the three modules exist outside the repo, in the session scratchpad (`…\scratchpad\upscale_draft\`); they were written
  before this plan was agreed and are a starting point, not an implementation.
* Measurement scripts and the ONNX export script used for the evidence are in the same scratchpad (`…\scratchpad\esrgan\`: `rrdbnet.py`, `export_onnx.py`, `sr_tile.py`, `eval_models.py`,
  `fullsize_onnx.py`, `pad_study.py`, `dml_check.py`). They can be turned into B0's export script and B2's gated test.
