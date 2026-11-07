# CLI-Culler CPU, CUDA and QML validation

November 4, 2026. Local candidate: `codex/cli-culler-qml`, based on Image
Triage `origin/main` at `0034bdb`, with the four published CLI-Culler commits
replayed onto that QML base. The old published culler branch and the other
worktrees are preserved. No upstream engine source or PhotoCraft code changed.

## User direction

QML is the only desktop application UI. The retired Widgets main window and
Widgets popout must not be reachable through flags, environment variables or
new frozen builds. Shared backend/helpers can remain while they are used.

The command/JSON interface is temporary for validation. Keep the existing QML
Cull layout and develop proper option controls, sliders, strictness presets
and thresholds. An optional advanced JSON view can remain. Those controls must
edit actual native `CullSettings` values, retain omitted engine defaults, and
never run the old culling rules over native decisions. In particular:

- Model toggles edit `models`; the eyes controls use `require_eyes_open`,
  `blink_threshold` and `blink_penalty`, with the faces model prerequisite.
- Keeper controls edit `bursts_keep` and `scene_keep` independently.
- Quality/review controls expose the native `gates` fields and their units.
  The quality floor stays disabled by default; a strictness preset must state
  its exact native values rather than reuse the old cascade's thresholds.
- Scene controls expose `refine_scenes`, `split_cos`, `merge_cos` and
  `merge_window`. These are not the old hash-distance sliders.
- CPU/CUDA selection is an execution/runtime choice. Explain model availability
  and the selected device without making the console part of normal use.

This control design is the next UI phase; the profile editor is a temporary
test interface, not the intended finished product.

## Crash evidence

The validation app process (ImageTriage.exe PID 97908) crashed. Windows Event
1000 names `Qt6Core.dll` 6.12.0, exception `0xc000001d`, offset `0xe9ee8`.
The saved dump's exception context points into Qt signal activation, with
text-document/QLabel/layout code on the stack. The local stack scan is not a
symbolicated debugger backtrace, so it does not establish the root cause.

Windows also recorded a `RADAR_PRE_LEAK_64` warning for python.exe earlier.
This is not proof that the worker ran out of memory or caused the GUI crash.
The worker log has no Python exception explaining the application's failure.
There were no WHEA hardware-error records in the preceding 48 hours. The dump
and DLL agree on the instruction bytes at the fault address (an ordinary x64
MOV); this does not identify the cause of the reported illegal-instruction
exception. It is not evidence of a missing CPU instruction-set feature.
Do not blame the CPU, declare MANIQA crashed, or claim the crash is fixed from
this evidence alone. A rebuilt frozen application still needs its own long run.

The dump, failed request and worker log were copied locally under
`logs/cli-culler-investigation/evidence`. Nothing was uploaded. The original
shoot's files and failed-run evidence were not changed.

## Measured inference

Hardware: Core i9-14900K (24 physical / 32 logical cores), RTX 4080 SUPER.
Both tests used the same copied real JPEG, resized to 853 x 1280, the same
unchanged MANIQA model/checkpoint and 20 seeded 224-pixel crops per image.
The engine already batches those 20 crops into one model call.

| Runtime | Threads | Cold inference | Warm inference median |
| --- | ---: | ---: | ---: |
| PyTorch 2.9.0+cpu | 24 | 6.067 s | 5.950 s |
| PyTorch 2.9.0+cpu | 32 | 5.762 s | 5.695 s |
| PyTorch 2.9.0+cu128 | 24 | 0.361 s | 0.111 s |

Warm medians are two repeated measurements on one image. CUDA is about 54x
faster than the 24-thread CPU case for MANIQA inference on this input, not for
the entire culling pipeline. Decoding/resizing this local JPEG took about
0.35 seconds; model construction/loading took about 2.8-3.3 seconds in these
tests. CPU inference is still slow. Raising the thread count gave only a
small improvement here, so no CPU thread limit or scoring shortcut was imposed.
Earlier synthetic checks with 8, 4 and 1 CPU threads were slower than 24.

CUDA uses the engine's existing fp16 path, CPU its fp32 path; small numerical
differences between devices are expected. Exact parity is checked with the
direct CLI using the same runtime/device, settings and starting caches.

Reproduce with `scripts/benchmark_cli_culler_maniqa.py`, passing `--runtime`,
`--image`, `--device cpu|cuda` and `--output`; `--threads` is optional.
Reports are under `logs/cli-culler-investigation/*real-image.json`.

## Candidate changes and checks

- Removed the Widgets launch function and UI-selection environment route;
  retired UI flags cannot select another application window. Removed the
  Widgets popout route. Frozen packaging excludes both retired windows, and
  validation assembly rejects a Widgets-only frozen app before copying it.
- Ported native culling into the existing QML Cull window. It displays the
  actual current engine stage/counts, retains exact native reasons and
  decisions, protects manual choices and keeps Review separate from Reject.
  The profile editor is explicit about saving the selected native settings.
- Added owned worker cancellation/shutdown for QML. The standalone Windows
  worker cancellation and descendant tests pass; QML shutdown cancellation
  has its own real subprocess test.
- Added a CUDA runtime recipe using `torch==2.9.0+cu128` and matching torchvision.
  It retains plain `onnxruntime`, as the unchanged engine requires. It does
  not install `onnxruntime-gpu` alongside that distribution. CUDA accelerates
  PyTorch rankers; this is not an all-models-GPU claim.
- Prepared `build/native-cuda-candidate`; hash-locked installation and
  `pip check` passed. CUDA resolves to the actual 4080 SUPER. Explicit CUDA
  requests fail clearly when CUDA is unavailable, before scoring starts.
- Persisted runtime/device/thread information and progress/elapsed events in
  run diagnostics, and enabled native Python fault traces in the worker.
- Fixed rounded clipping's software-renderer fallback: unsupported shader
  effects had hidden the whole Cull window in offscreen CPU captures.

Real-file acceptance used six copied files (five captures, including one
JPEG/NEF pair). The first two-capture sample was uncontested and did not run
MANIQA/appeal; it was expanded so those paths were actually exercised.
The expanded CUDA run completed faces on five captures, MANIQA on five and
appeal on three, in 27.0 seconds including startup/decoding/model loads.
The direct CLI comparison matched every document/index value except timings.

Analyze also completed through the actual QML controller on that local sample,
reopening native results in the QML review screen. Prepare and Review captures
were inspected. The full-folder follow-up is recorded below.
The source UI validation used Qt 6.11.0, whereas the crashed portable build used
Qt 6.12.0. A fresh frozen build still needs its own runtime/stability gate.

The combined focused gate passed 87 tests; subsequent shutdown/assembly
regressions are additionally covered in `tests/test_qml_cli_culler.py`.
The final launch/lifecycle/comparison subset passed 28 tests, including QML
shutdown and the old-UI assembly rejection. Python compilation, PowerShell
launcher parsing and `git diff --check` passed. Graphify was refreshed.
The CUDA runtime and source launch are development candidates. No new frozen
portable app, installer, release or editor promotion has been produced.

For this checkout, `scripts/run_cli_culler_qml.ps1` launches the QML source app
with the prepared CUDA runtime, optionally taking `-Folder`. It leaves the
installed application and downloaded validation build unchanged. The workflow
now defines separate CPU and CUDA QML validation artifacts; it has not yet
been run for these local changes.

## Full-folder follow-up

The original NAS folder was read in full, with this pass's caches, logs, runs
and active pointer redirected to a unique local diagnostic directory. No
decisions were Applied and the original failed-run artifacts were preserved.
The engine found 2,304 file paths representing 1,137 captures; QML showed
1,137 photo rows. The cold engine run completed without warnings or a crash:

| Stage | Work | Seconds |
| --- | ---: | ---: |
| Ingest | whole folder | 65.5 |
| TinyCLIP | 1,137 captures | 158.1 |
| Exposure/sharpness/clipping | 836 captures | 74.8 |
| Faces | 644 captures | 27.6 |
| MANIQA | 644 captures | 174.7 |
| Appeal | 422 captures | 208.2 |

Cold scoring/export took about 12 minutes. These timings include the real
stage's preprocessing/cache work, unlike the isolated model benchmark.
The observed worker private-memory high-water sample was about 13.3 GB;
it is a sampled observation, not continuously measured peak memory.

The first cold source capture timed out at the UI completion boundary because
the diagnostic `cullScene=cu-prep` preset could fire after analysis started and
reset the model's running state. Its exported engine results were valid. The
saved full result was replayed successfully, and capture presets are now
prevented from resetting an active real run (covered by a regression).
The normal QML path, without that capture preset, then completed on the same
full folder using the saved score cache in 63.6 seconds and displayed all
Keep/Reject/Review outcomes. The results capture was inspected. This is not a
claim that cold whole-folder analysis takes 64 seconds.

Evidence: `full-shoot-cold.log`, `full-shoot-proof.json`, `full-shoot-qml.log`
and `qml-full-shoot-results.png` under `logs/cli-culler-investigation`.
The frozen Qt 6.12 crash's cause remains unproven; the successful source UI
tests used Qt 6.11. The published validation ZIP has not been replaced.

## Face-session resource-lifetime issue and unapplied fix

Both the failed CPU run and the cold CUDA run logged 85 MediaPipe landmarker
initializations for the 644-frame face stage, despite an eight-thread limit.
`FacesScorer.score` creates a new executor per batch. `FacesEngine` creates
one landmarker per thread and retains every instance in `_all` until close.
The number of retained models therefore grows across batches. This explains
avoidable model setup and memory retention; it does not prove the Qt crash's
cause.

`docs/patches/cli_culler_face_pool_lifetime.patch` is a reviewable, unapplied
fix against the pinned CLI-Culler source. It makes the engine own a reusable
executor and joins its threads before closing their model sessions. It includes
`tests/test_face_pool_lifetime.py`. The installed engine, validated wheel and
source pins remain unchanged.

- Six 64-frame synthetic-detector batches: original created 49 sessions,
  proposed fix 9; every session closed and returned-value hashes matched.
- Three 16-frame real-face batches using the copied portrait: original created
  25 sessions in 2.91 seconds, proposed fix 9 in 1.61 seconds. All 48 returned
  real model results matched exactly; this is a focused test, not a whole-shoot
  speedup or memory measurement.
- The proposed engine regression passed against the isolated patched copy.

Applying this fix requires deliberately updating the CLI-Culler source pin and
wheel provenance, then rerunning direct CLI parity and full-shoot validation.
The previous unchanged-engine contract is preserved in the current candidate.

## QML culling controls, November 5

The source UI follows the supplied preparation-screen reference: four feature
cards, Careful / Balanced / Strict, and a Fine-tune link. Detailed thresholds,
keeper counts, device selection and Advanced JSON stay hidden until requested.
Fine-tune closes when leaving preparation and stays closed on the next opening.

Controls read defaults from the pinned isolated engine. Untouched settings and
unexposed keys survive editing. Start analysis, Save changes, closing Fine-tune,
and closing the preparation window validate and persist the selected profile.
An invalid profile retains the previously saved settings and displays the error.

Balanced uses the engine's default clipping, relative-blur and blink thresholds.
Careful and Strict are new sensitivity presets for those three thresholds;
they have not been calibrated against reviewed albums. They preserve existing
quality floors, keeper counts and every other native setting. No engine or
scoring-wheel changes are included.

CPU and CUDA selections pass through to the worker and use separate score-cache
namespaces. Runtime default preserves the manifest-selected backend. Semantic
search toggles the native search model; background result loading imports the
verified optional index into the host store. Import errors are reported without
discarding decisions.

The focused checks cover default preservation, feature toggles, profile
save/reload and invalid-profile rejection, CPU/CUDA cache separation, clicking
Fine-tune, committing slider drags on release, real native worker completion
and cancellation, and search-index adapters. Synthetic-photo captures are in
`logs/cli-culler-investigation/native-controls-reference.png` and
`native-controls-finetune.png`. These source changes do not replace the portable
release or resolve its previously observed Qt crash.

## Photo search restored, November 6

Semantic search is on in new default profiles, including when an older installed
runtime manifest omits it. Explicitly saved profiles remain authoritative;
turning search off and saving is preserved. The engine's own defaults and wheel
remain unchanged.

The top-bar field now searches photos directly. Commands have a separate button
and keep Ctrl+K; Ctrl+F focuses photo search. Escape or the clear button removes
the photo query. Commands do not consume or overwrite the photo query.

The QML model applies filename matches immediately, then merges semantic and
named-person hits from the existing shared search worker. Queries are debounced,
late responses from replaced queries/folders are ignored, and results intersect
the current filters and collection/person scope. A folder change clears the
query. Without an index or text runtime, filename search remains usable and the
field explains what is missing. Native index import refreshes the current query.

The search worker now lives in `image_triage/unified_search_task.py`, shared by
the QML shell and the existing backend controller. Search and native index
imports resolve the installed managed TinyCLIP bundle instead of accidentally
using the old cache's missing TinyCLIP path. Explicit model-path overrides are
preserved.

Validation includes typing into the rendered QML field, independent command
palette use, clearing/search shortcuts, filter/scope composition, stale-query
handling, saved opt-out preservation, canonical model lookup, and importing
synthetic native vectors through the real SQL/search worker into the grid.
A separate real smoke test indexed six synthetic JPEGs with the unchanged native
engine, imported all six vectors, encoded a 512-dimensional TinyCLIP text query,
and completed the query through the QML search bridge. This checks wiring, not
search relevance on a real album. The source capture is
`logs/cli-culler-investigation/photo-search-bar.png`.
