# Review packet: AI runtime reliability hardening

**Branch:** `codex/ui-ux-polish`
**Base commit:** `91c5ba1` (`targeted ai runtime bug and failure fixes`)
**State:** uncommitted working tree — 22 files modified, 16 added
**Scope of change:** +1060 / −303 in modified files, plus ~4,900 new lines
**Source of requirements:** `HANDOFF_AI_RUNTIME_RELIABILITY.md`

This implements the handoff's Phases 0–4 in full and Phase 5 partially (harness
and matrix built; no matrix row executed — see *Not done* at the end).

---

## 1. What problem this solves

The application's AI features failed on machines other than the development box
while Settings reported success. Phase 0 traced this to six root causes, written
up with file/function citations in **`docs/ai_runtime_failure_map.md`**. Read
that first; this document assumes it.

The two that directly explain the reported field failures:

**Root cause A — model roots were still Store-virtualized.** Commit `91c5ba1`
moved the *runtime* root to `%USERPROFILE%\.image-triage\AI\rt\<tag>` but left
every **model** and cache root deriving from raw `LOCALAPPDATA`.
`image_triage/ai_model.py` had its own `_default_user_cache_root` that, unlike
the one in `ai_runtime_packages.py`, did **not** undo Store Python's
redirection. So models still landed in
`...\Packages\PythonSoftwareFoundation.Python.3.13_*\LocalCache\Local\image_triage_ai_cache\models\...`.
Four more cache roots (`depth_maps`, `prompt_masks`, `semantic_masks`,
`subject_masks`) had the same bug.

**Root cause C — `--no-dino` installs reported success.**
`_profile_status` only requires the torch module set when the variant appears in
`dino_enabled_variants`. A base-only install therefore yields
`status.is_installed == True`, Settings says "AI Setup Complete", and the editor
then fails with *"missing scene-mask dependencies: torch, transformers,
safetensors"*. That is field failure #3 exactly.

Root causes B (unverified, non-transactional model installs), D (success
announced without verification), E (profile re-derived per process) and F
(silent resolver failures) are documented in the same file.

---

## 2. New modules

| File | Lines | Responsibility |
| --- | ---: | --- |
| `image_triage/ai_paths.py` | 434 | One canonical managed root; Store de-virtualization; explicit migration; storage preflight; redaction |
| `image_triage/ai_manifest.py` | 546 | 10 capabilities, 9 model bundles, per-file size + SHA-256 |
| `image_triage/ai_model_store.py` | 872 | Transactional bundle install/verify/repair; locking; download transport |
| `image_triage/ai_probe.py` | 382 | Capability probes that run *inside* the managed runtime |
| `image_triage/ai_env.py` | 246 | `RuntimeSelection` + the single worker environment builder |
| `image_triage/ai_health.py` | 682 | The readiness authority; repair; diagnostics |
| `image_triage/ui/ai_readiness.py` | 227 | Demo Ready dialog, readiness/repair tasks, failure text |
| `scripts/refresh_ai_model_manifest.py` | 113 | Regenerates the digest table from the HF metadata API |
| `scripts/ai_clean_machine_check.py` | 350 | Drives an **installed MSI**; imports no source |

Docs: `docs/ai_runtime_failure_map.md` (283), `docs/ai_clean_machine_matrix.md` (90).

---

## 3. Design decisions worth challenging

These are the calls a reviewer should push on. Each is deliberate; none is
obviously right.

### 3.1 Path resolution is pure; migration is one explicit call

`ai_paths.managed_model_dir()` used to migrate a legacy directory as a side
effect of resolving a path. I removed that after it moved real user data during
a status check. Migration is now **only** `ai_model_store.migrate_ai_assets()`,
called from `MainWindow._maybe_prompt_for_ai_setup` (frozen builds only).

*Risk:* if a code path reads a managed model directory before startup migration
runs, it sees "missing" and could trigger a re-download. Verify there is no such
path. `bundle_status` is pure and never migrates, which is the property I wanted,
but it does mean ordering matters.

### 3.2 Readiness is shallow by default, deep only on Repair

`bundle_status(deep=False)` compares the recorded revision plus each file's size
against the manifest. It does **not** hash. Hashing DINOv3 (1.2 GB) + CLIP
(605 MB) on every launch was not acceptable, so truncation is caught by size and
substitution-at-identical-size is caught only by `deep=True` (Repair AI and
post-install verification).

*Challenge this if* you think a same-size substitution is a threat worth the
startup cost.

### 3.3 Legacy bundles are adopted, not re-downloaded

A directory with no `.bundle.json` whose files all match the published sizes is
adopted (metadata written, `adopted: true`). The alternative — treating it as
stale — would force a multi-gigabyte re-download on upgrade for every existing
user. `tests/test_ai_model_store.py::VerificationTests::test_a_legacy_directory_with_correct_sizes_is_adopted`
pins this.

### 3.4 Repair does not reinstall the package runtime

`AIHealthService.repair` reinstalls **model bundles** only. A runtime-level
failure is reported with an action ("Open Settings and run Set Up AI") rather
than silently triggering a multi-GB pip install. Arguable — Repair AI could
plausibly be expected to fix a broken runtime too.

### 3.5 Probe results are cached for 15 minutes

Each probe is a subprocess (0.1–6 s observed). `PROBE_CACHE_TTL_SECONDS = 900`,
keyed on `(capability, requested_device, profile_id)`. `invalidate()` is called
after every install/repair. If a driver changes mid-session the cache goes stale
until the TTL expires or the user re-runs Demo Ready.

### 3.6 The probe rides in `ai_runtime_installer.exe`

Rather than adding a fourth frozen executable, `probe` is a new subcommand on
the existing installer exe. This keeps `setup_msi.py` almost unchanged and means
the probe uses the exact binary the MSI ships. It does make the installer's
argparse surface do two unrelated jobs.

---

## 4. Change-by-change

### 4.1 Canonical storage — `ai_paths.py`

- `managed_ai_root()` → `%USERPROFILE%\.image-triage\AI` (Windows),
  `$XDG_DATA_HOME/ImageTriage/AI` otherwise. Override: `IMAGE_TRIAGE_AI_ROOT`.
- Subdirectories: `rt/`, `models/`, `cache/`, `logs/`, `staging/`.
- `devirtualized_local_appdata()` is the single Store-Python fix, now shared.
- `preflight_storage()` proves a directory is creatable, writable and has space
  before anything is downloaded.
- `redact()` strips `USERPROFILE`/`USERNAME` from every diagnostic string
  (handles `\`, `/` and `\\` forms; ignores usernames under 3 chars).
- `migrate_managed_assets()` uses `os.replace`, falling back to `copytree` across
  volumes so a partial move can never destroy the only copy. Never overwrites an
  existing destination.

**Wired into:** `ai_runtime_packages.default_ai_runtime_install_root` (now
`managed_runtimes_root() / _python_runtime_tag()`), all nine
`ai_model.default_*_install_dir` functions, and the four editor cache roots.
`ai_model._default_user_cache_root` was deleted (unreferenced after rewiring).

### 4.2 Manifest — `ai_manifest.py`

10 capabilities: `culling`, `text_scoring`, `quality_topiq`, `faces`,
`semantic_search`, `dino`, `scene_masks`, `subject_masks`, `sam_masks`, `depth`.
Each declares required modules, model bundles, whether it needs torch, its
worker module, probe kind, and the Transformers symbols the worker imports.

9 bundles with repo/revision/filenames/install path/size estimate.

**Model integrity, before → after:**

| Bundle | Files with a published SHA-256 before | After |
| --- | --- | --- |
| DINOv3 | 2 / 2 | 2 / 2 |
| CLIP | 7 / 7 | 7 / 7 |
| OneFormer | 7 / 7 | 7 / 7 |
| BiRefNet | 1 / 4 | 4 / 4 |
| SAM 2.1 | 5 / 5 | 5 / 5 |
| Depth Anything V2 | 0 / 3 — revision was the moving branch `main` | 3 / 3, pinned to `5426e4f0` |
| TinyCLIP | 2 / 2 | 2 / 2 |
| **TOPIQ** | **0 / 1** | **1 / 1** |
| **AuraFace** | **0 / 4** | **4 / 4** |

Every file also gained an expected **size**, which is what makes the cheap
per-launch check able to detect truncation.

> **Reviewer note:** digests came from `scripts/refresh_ai_model_manifest.py`
> against the Hugging Face API — LFS entries carry a SHA-256 `oid` directly;
> smaller non-LFS files were downloaded and hashed. **Every hash that already
> existed in the codebase matched**, which is decent evidence the method is
> sound. The new ones (TOPIQ, AuraFace, BiRefNet's three, Depth's three) have
> never been independently verified. Worth spot-checking one.

`tests/test_ai_model_store.py::ManifestIntegrityTests` asserts every bundle file
has a non-zero size and a 64-char hash, no bundle is unverified, every revision
is a 40-hex commit, and no two bundles share an install directory.

### 4.3 Transactional model installs — `ai_model_store.py`

Install path: cross-process lock → disk preflight → clean abandoned staging →
download each file into `staging/<key>-<uuid>` → verify **every** file's size and
hash → write `.bundle.json` → move live dir aside → move staged in → delete the
retired generation.

- `_replace_with_retry` retries 8× with backoff for Windows file locking
  (antivirus, indexer, a worker still exiting).
- An activation journal is written to staging; on crash the live directory is
  simply absent, so the next run reports "missing" rather than "corrupt".
- Download transport: bounded retries (4), exponential backoff, `Range` resume,
  HTML-response detection (captive portal / proxy), free-space check against
  `Content-Length`, and typed error categories: `disk`, `network`, `dns`,
  `certificate`, `intercepted`, `auth`, `rate_limit`, `not_found`, `truncated`,
  `hash_mismatch`, `locked`.
- Unrecoverable categories (`not_found`, `auth`, `certificate`, `manifest`) are
  **not** retried.

`ai_model.download_ai_model` routes to `install_bundle` when the installation
matches a manifest bundle (same repo, revision **and** file set — so the fp16
TinyCLIP variant correctly falls through to the legacy path). The legacy path no
longer skips an existing file that has a published hash unless that hash matches
(`_file_is_trusted`).

`AIModelInstallation.is_installed` now delegates to `bundle_status().is_ready`
when it points at the managed bundle directory, and keeps existence semantics
when a caller overrides `install_dir`.

### 4.4 Runtime selection — `ai_env.py`

`select_runtime(device, require_torch=...)` returns an immutable
`RuntimeSelection` (variant, resolved device — **never** `"auto"` —
site-packages, profile generation, root) or raises `AIRuntimeUnavailable` with a
category and a remediation.

Two behaviour changes to scrutinise:

- **An explicit CPU request can no longer fall through to the GPU profile.**
  `_choose_variant` returns `""` if CPU is requested and no CPU profile exists.
  Previously it silently used GPU, which made "Switch to CPU" ineffective. This
  means a GPU-only install now *fails* an explicit CPU request instead of
  quietly working. I believe that is correct; it is a behaviour change.
- **`require_torch=True` rejects a base-only install** rather than returning a
  profile that will fail later.

`build_worker_env` is the one environment builder: managed site-packages first on
`PYTHONPATH` (de-duplicated so repeated launches cannot stack profiles),
`PYTHONNOUSERSITE=1`, offline HF flags, plus `IMAGE_TRIAGE_AI_PROFILE`,
`IMAGE_TRIAGE_AI_SELECTED_DEVICE`, `IMAGE_TRIAGE_AI_PROTOCOL`.

### 4.5 Capability health — `ai_health.py` + `ai_probe.py`

`check(capability, device)` = runtime selection ∧ every bundle verified ∧ a probe
that ran inside that profile and did real work. Probes:

- `import` — import each module, assert `__file__` is under the profile (catches
  a shadowing global Python), record versions and paths.
- `onnx_providers` — real `get_available_providers()`; a GPU request with no
  `CUDAExecutionProvider` fails with "the NVIDIA driver is missing, too old, or
  the CUDA runtime files did not install".
- `onnx_model` — creates an actual `InferenceSession` on the installed file and
  records input/output names and embedding dimension; detects silent CPU
  fallback after a GPU request.
- `torch` — `torch.cuda.is_available()`, device name, plus a 4×4 matmul to catch
  a torch that imports but whose kernels cannot run (missing VC++ runtime).
- `insightface` — loads `glintr100.onnx` directly rather than building
  `FaceAnalysis` (seconds vs. ~0.7 s).

`CapabilityHealth` carries stage, category, message, remediation, an `action`
token (`setup`/`repair`/`download`/`cpu`/`retry`/`restart`/`diagnostics`), the
profile, the device it actually got, provider lists, module versions and paths.
`headline()` is never a traceback.

`diagnostics()` / `diagnostics_text()` / `write_diagnostics()` produce a redacted
support bundle including managed-root volume kind, free space, long-paths state,
architecture, and any unverified/unpinned bundles.

### 4.6 Worker consistency — Phase 4

- `mask_engine_service` and `subject_masks` / `semantic_mask_service` no longer
  each run their own `(site_dir / name).exists()` module list.
- `MASK_ENGINE_PROTOCOL_VERSION = 1` on both sides; `mask_engine_worker` exits 3
  on mismatch and gained a `handshake` command plus `_loaded_runtime_report()`
  so the worker reports the module paths it **actually** loaded.
- The worker is told the *resolved* device, never `"auto"`.
- `ai_python_runner._cached_runtime_site_packages` no longer swallows exceptions;
  it prints a specific message to stderr for each of three distinct failures.
- `_requested_device_from_argv` prefers the parent's pinned
  `IMAGE_TRIAGE_AI_SELECTED_DEVICE` over the command line.
- `_assert_runtime_precedence` raises if a bundled `ai_site_packages` would
  shadow the managed profile. `IMAGE_TRIAGE_AI_TRACE_PATH=1` dumps `sys.path`.

### 4.7 UI — Phase 3

- `_handle_ai_runtime_install_finished` and `_handle_ai_model_download_finished`
  no longer show "AI Setup Complete" on exit code alone; both call
  `_verify_ai_setup`, which runs a real readiness pass. The success dialog
  appears only if **every** selected capability is ready; otherwise the readiness
  dialog opens with per-capability reasons.
- New actions in **AI → AI Setup And Cache**: *Check AI Readiness (Demo Ready)*,
  *Repair AI*, *Copy AI Diagnostics*.
- `_selected_ai_capabilities()` excludes torch-only capabilities when the user
  installed the compact base runtime, so a deliberately minimal setup is not
  reported as broken.

### 4.8 Popout viewer audit (added after the first pass)

`PreviewDialog` has no AI resolution of its own — it hosts the same
`PhotoEditorPanel` and only relays warm requests. But the audit found two real
defects in the code it drives:

- **`depth_maps.py` and `prompt_masks.py` both called
  `validate_semantic_runtime()`** — depth estimation and SAM click selection were
  validating *OneFormer's* readiness. They now call
  `validate_mask_runtime("depth")` / `validate_mask_runtime("sam_masks")`.
- **`_resolve_engine_runtime` required both mask bundles to start the host.**
  This one was **introduced by me earlier in this work** and would have let a
  missing OneFormer model block depth and click selection — the exact behaviour
  the handoff forbids. The shared MaskEngine host now requires only the managed
  PyTorch runtime; each caller verifies its own capability first.

`semantic_mask_service` gained `resolve_mask_runtime(capability_key)` and a
per-capability validation cache. `subject_masks` uses the same helper.

### 4.9 Additional hardening

- `_validate_onnxruntime_exclusivity` rejects a profile holding both
  `onnxruntime` and `onnxruntime-gpu` (they share a package directory; whichever
  wrote last decides the provider set).
- `setup_msi.py` / `setup_linux.py` explicitly include `image_triage.ai_probe`,
  `ai_manifest` and `ai_paths` — they are imported lazily inside the probe
  subcommand, so static analysis may miss them.
- `ai_runtime_installer status --json` now reports `torch_variants` and
  `site_packages` (the clean-machine harness consumes this).

---

## 5. Fixed in passing (outside the handoff's scope, please sanity-check)

`_directory_signature` in `ai_workflow.py` raised `OSError` on any unreadable
entry, aborting an AI stage cache-key computation as soon as one reparse point,
WSL symlink or antivirus-locked file appeared in the engine root. Making it
tolerant then exposed a second, worse problem: an unbounded `rglob` over an
arbitrary user directory.

It now prunes non-input directories (`.git`, `build`, `dist`, `node_modules`,
`__pycache__`, `*venv`, …), caps at `SIGNATURE_MAX_ENTRIES = 4000`, and records
`truncated: true` when capped. On this checkout the affected test went from
**hanging indefinitely** to 1.2 s.

**Reviewer note:** a truncated signature is coarser, so two different directory
states could in principle produce the same cache key. The walk is deterministic
(sorted, pruned, capped) and the entry count is part of the signature, but if
you think a cache-key collision is unacceptable here, this needs a different
design.

---

## 6. Evidence

### Real probes against the developer machine's installed GPU runtime

Not mocks — actual subprocess probes against
`~/.image-triage/AI/rt/py313-windows-amd64/profiles/gpu/site-packages`:

| Capability | Result | What it reported |
| --- | --- | --- |
| `culling` | PASS | numpy 2.5.1, onnxruntime 1.26.0, cv2 5.0.0, sklearn 1.9.0, PIL 12.3.0 — all resolved under the managed profile; providers Tensorrt/CUDA/CPU |
| `scene_masks` | PASS | torch **2.9.0+cu128**, transformers 5.14.1, `torch_cuda_available: true`, `NVIDIA GeForce RTX 4080 SUPER` |
| `quality_topiq` | PASS | real `InferenceSession`; inputs `["input"]`, outputs `["quality_score"]`; **`CUDAExecutionProvider` active** |
| `text_scoring` | PASS | inputs `["input_ids","pixel_values","attention_mask"]`; embedding dim **512** |
| `faces` | PASS | `glintr100.onnx` loaded; embedding dim **512**; CUDA active |

The ONNX-model probes were run with the documented `*_MODEL_DIR` overrides
pointing at the existing legacy directories, because **I did not migrate the
developer machine's live model cache** (see §8).

### Test results

Runner: `pythonw3.13.exe scripts/run313.py <log> pytest …` with
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`. Both runs exclude six modules that cannot be
collected because `onnx`/`torch` are deliberately absent from the host
interpreter (`test_aiculler_topiq_onnx`, `test_dinov2_extractor`,
`test_efficientvit_sam_sandbox`, `test_grounded_sam_sandbox`,
`test_oneformer_sandbox`, `test_ranking_dino_fallback`).

Baseline was captured from a **clean `git worktree` at `91c5ba1`**, not from
memory.

| | Baseline `91c5ba1` | After |
| --- | ---: | ---: |
| Passed | 1070 | **1191** |
| Failed | 23 | 35 (see below) |
| xfailed | 5 | 5 |

AI-focused modules specifically: **129 → 212 passed, 1 → 0 failed.**

**No failure in the after-run is absent from the baseline.**

New tests: `test_ai_paths.py` (17), `test_ai_model_store.py` (33),
`test_ai_env.py` (16), `test_ai_health.py` (28), plus 12 added to existing
modules — **106 new tests**.

### Why the failure count varies (23 vs 35)

Two groups:

1. **Stable (23), all pre-existing.** `test_window_catalog_cache` (6),
   `test_decision_harvest` (6), `test_grid_failures` (4),
   `test_catalog_repository` (2), `test_preview_navigation`,
   `test_preview_polling`, `test_ai_results_phase1`, `test_ai_training`,
   `test_ai_workflow::test_default_runtime_prefers_generic_bundled_checkpoint_location`.
   The `test_window_catalog_cache` group fails because `_WindowAiLoadStub` /
   `_WindowAiRunStub` lack methods the window calls (e.g.
   `_recompute_ai_demoted_burst_paths`) — verified identical at `91c5ba1`.

2. **Flaky, environmental.** `test_run_command_*` in `test_ai_workflow`,
   `test_extract_entrypoint`, `test_aiculler_cli_reports`,
   `test_decision_harvest` intermittently fail with
   `OSError: [WinError 50] The request is not supported` when spawning a piped
   subprocess under the console-less `pythonw` harness. **Measured over three
   consecutive runs of that group: baseline 9/9/5 failures, after 8/8/7.** The
   variance is the harness, not the code.

---

## 7. Suggested review order

1. `docs/ai_runtime_failure_map.md` — the six root causes and their fixes.
2. `image_triage/ai_manifest.py` — is the capability decomposition right? Are the
   module lists per capability correct and minimal?
3. `image_triage/ai_model_store.py` — `_activate`, `_download_file`, `_fetch`,
   `bundle_status`. This is where a bug loses or corrupts user data.
4. `image_triage/ai_env.py::_choose_variant` — the explicit-CPU behaviour change.
5. `image_triage/ai_health.py::check` and `_failure` — is the stage/category →
   action mapping right?
6. `image_triage/ai_workflow.py::_directory_signature` — the cache-key change.
7. `image_triage/window.py` diff — the readiness/repair/diagnostics handlers.

### Specific things I would like a second opinion on

- **Circular-import risk.** `subject_masks` now imports
  `resolve_mask_runtime` from `semantic_mask_service`; `mask_engine_service`
  imports from `semantic_mask_service`; `ai_env.build_worker_env` does a
  function-local `from .ai_workflow import AI_METRICS_ENV_VAR` specifically to
  avoid a cycle. The import graph is more tangled than I would like.
- **`ai_health` is imported by three mask modules**, which pulls
  `ai_model_store` (and therefore `urllib`) into their import path. Probably
  fine; worth confirming it does not slow editor startup.
- **`_probe_command` in frozen builds** assumes `ai_runtime_installer.exe` sits
  beside `sys.executable`. True for the current cx_Freeze layout — confirm it
  stays true.
- **Cancellation.** `AIRepairTask.cancel()` exists but nothing calls it; there is
  no cancel button on the repair progress. Deliberate scope cut.

---

## 8. Not done / known gaps

**Phase 5 has zero executed rows.** `docs/ai_clean_machine_matrix.md` defines 20
machine conditions and an 8-step workflow pass, and
`scripts/ai_clean_machine_check.py` drives an installed MSI without touching a
checkout. **Nothing in that matrix has been run** — no clean machines were
available, and no MSI was built or installed during this work. This is the
handoff's stated release gate and it remains fully open.

**The MSI was never built.** `setup_msi.py` / `setup_linux.py` changes are
untested beyond syntax. Someone must run `python setup_msi.py bdist_msi` and
confirm cx_Freeze picks up the probe modules.

**No end-to-end workflow was run through the UI.** Probes prove imports,
providers and model loads; they do not prove that AI culling, semantic search,
face grouping or a mask actually completes in the app.

**Developer machine's model cache was not migrated.** During Phase 1 a smoke
test triggered the (then side-effecting) migration and moved TOPIQ and TinyCLIP
out of `%LOCALAPPDATA%`. I restored both and made resolution pure. The models
remain in the legacy location; the new startup migration will move them on the
next frozen launch. This is why the ONNX probes used directory overrides.

**Six model hashes are newly introduced and unverified by a second source**
(TOPIQ ×1, AuraFace ×4 — plus BiRefNet ×3 and Depth ×3 that were previously
absent). They came from the HF API and every *pre-existing* hash matched, but
they have not been independently confirmed.

**Depth model revision changed.** `DEFAULT_DEPTH_MODEL_REVISION` moved from the
moving branch `"main"` to the pinned commit `5426e4f0…`. Any user who already
downloaded depth from `main` will have their bundle marked stale and
re-downloaded (~100 MB). Intentional, but it is a user-visible consequence.

**`_mask_models_are_installed` still gates all three editor mask models
together.** The point-select (SAM) button stays hidden until OneFormer, BiRefNet
and SAM are all present, because they are presented as one "AI Masking Tools"
download. Coherent as a product grouping, but it is the same class of coupling I
removed elsewhere. Flagging rather than changing it unilaterally.

---

## 9. File inventory

**Added (16):**
`image_triage/ai_paths.py`, `ai_manifest.py`, `ai_model_store.py`,
`ai_probe.py`, `ai_env.py`, `ai_health.py`, `image_triage/ui/ai_readiness.py`,
`scripts/refresh_ai_model_manifest.py`, `scripts/ai_clean_machine_check.py`,
`docs/ai_runtime_failure_map.md`, `docs/ai_clean_machine_matrix.md`,
`tests/test_ai_paths.py`, `tests/test_ai_model_store.py`, `tests/test_ai_env.py`,
`tests/test_ai_health.py`, and this file.

**Modified (22):**
`ai_model.py`, `ai_runtime_packages.py`, `ai_workflow.py`, `depth_maps.py`,
`mask_engine_service.py`, `mask_engine_worker.py`, `prompt_masks.py`,
`semantic_mask_service.py`, `semantic_masks.py`, `subject_masks.py`,
`ui/actions.py`, `ui/menus.py`, `ui/photo_editor_panel.py`, `window.py`,
`packaging/ai_python_runner.py`, `packaging/ai_runtime_installer.py`,
`setup_linux.py`, `setup_msi.py`, `tests/test_ai_model.py`,
`tests/test_ai_runtime_packages.py`, `tests/test_ai_workflow.py`,
`tests/test_semantic_mask_service.py`.

**Deleted code:** `ai_model._default_user_cache_root` (unreferenced after
rewiring), `MaskEngineService._site_packages` (write-only), and four unused
manifest/health helpers.

No commits were made. `HANDOFF_AI_RUNTIME_RELIABILITY.md` is unchanged.

---

# Round 2 — response to review

All ten findings addressed. Two of them turned out to be hiding real product
bugs, described at the end.

## Findings 1–7 (P1)

### 1. Activation could destroy the healthy generation — fixed

`_activate` now:

* records `had_previous` and, if the second rename fails, **rolls the retired
  generation back** into place before re-raising;
* keeps the journal on disk when rollback itself fails, so recovery can still
  find the copy;
* is preceded by `recover_interrupted_activations()`, called from
  `install_bundle` and at app startup, which completes or undoes any half-done
  swap — preferring the verified staged copy, falling back to the retired one;
* is protected from cleanup: `_clean_abandoned_staging` reads every open
  journal via `_journal_referenced_paths` and never deletes a referenced
  directory. That was the specific step that turned a failed swap into data
  loss.

Four failure-injection tests in `ActivationFailureTests`, including the exact
sequence you reproduced
(`test_a_failure_between_the_two_renames_restores_the_previous_bundle`) and
`test_staging_cleanup_never_deletes_a_journalled_generation`.

### 2. Setup could not produce the state it verified — fixed

`ai_manifest` now declares the set explicitly: `BASE_CAPABILITIES`,
`TORCH_CAPABILITIES`, `OPT_IN_CAPABILITIES` (DINO only) and
`setup_capabilities(include_torch=...)`.

* `MainWindow._selected_ai_capabilities` returns `setup_capabilities(...)`, so
  verification cannot demand something setup never installs. DINO is excluded
  as opt-in — its 1.2 GB is not part of a normal setup.
* A new stage runs between installation and verification:
  `_start_ai_capability_bundles` → `AIBundleInstallTask` →
  `AIHealthService.install_missing_bundles`, which downloads OneFormer,
  BiRefNet, SAM and depth. A partial failure still proceeds to verification so
  the user sees real per-capability state rather than one error.

`SetupCapabilityTests` asserts the installed and verified sets are identical.

### 3. Repair claimed to fix package failures — fixed

* `_ACTIONS` maps `package_*` to **`setup`**, not `repair`, and the remediation
  text now names Set Up AI.
* `repair()` returns `runtime_repair_required=True` when model repair succeeded
  but the packages are still broken; `repair_all()` aggregates it.
* `_handle_ai_repair_finished` offers to reinstall the runtime directly rather
  than looping the user back to Repair.

`RepairRoutingTests` covers the mapping, the flag and the aggregate.

### 4. Demo Ready could approve a different device — fixed

`_probe` now discards the caller's request and probes `selection.device`, and
that value is part of the cache key. `ProbeDeviceTests` asserts an `auto`
request probes `cuda`, never `auto`.

### 5. Probes did not prove the capabilities — fixed

* **ONNX**: builds synthetic inputs from the declared signature, runs a real
  `session.run`, and asserts the output contract. TinyCLIP requires
  `text_embeds`/`image_embeds` at **512** dims; TOPIQ requires `quality_score`;
  AuraFace's recognition graph requires 512 dims — a drift there would silently
  corrupt stored embeddings.
* **TOPIQ** additionally runs the product's own
  `prepare_topiq_model_for_providers` first. Probing the raw export reported a
  cuDNN failure for a capability that works fine, because the app rewrites a
  malformed conv bias before use.
* **Torch**: the matmul runs on the **selected** device (`cuda` / `cuda:N`),
  with `torch.cuda.synchronize`, a device-count check for indexed requests, and
  a result assertion.
* **Torch models**: new `probe_torch_model` loads the config at `quick` level
  and, at `full`, loads the real weights with the same class the worker uses
  and runs a forward pass at the model's own dtype.
* `inference_ran` is reported, and the clean-machine harness **fails a probe
  that reports success without having run one**.

Two levels because a cold torch probe costs 5–37 s: `require_capability` gates
default to `probe=False` (runtime + bundle only, ~140 ms — the worker is about
to import torch anyway and reports its own failure), while Demo Ready runs
`thorough=True`. `GateTests` asserts the gate spawns nothing and still fails on
a missing model.

### 6. Installation was not reproducible — fixed

* Every floor is now an exact pin. Your `insightface` example was live: `>=0.7`
  resolves to **2.0** today while every validated runtime holds **1.0.1**.
* `scripts/refresh_ai_runtime_lock.py` uses pip's `--dry-run --report` to emit
  a full transitive lock with SHA-256 per wheel. Four locks are committed under
  `packaging/ai_runtime_locks/` (cpu/gpu x base/pytorch), covering 31–63
  distributions each. torch's hash comes from the PyTorch index and matches the
  value published there.
* Installing from a lock adds `--require-hashes` **and `--no-deps`**, so pip
  resolves nothing of its own.
* `IMAGE_TRIAGE_AI_REQUIRE_LOCK=1` makes a missing lock a hard error for
  release builds; diagnostics report per-variant lock status.

`test_shipped_locks_hash_every_distribution` and
`test_requirements_are_pinned_exactly_when_no_lock_ships` (which fails on any
remaining `>=`) enforce this.

### 7. Resolution logged and continued — fixed

* `ai_python_runner` raises `ManagedRuntimeError` and returns exit code 3
  instead of proceeding. A bare invocation with no pinned profile stays
  permissive (`_managed_runtime_required`), so non-AI scripts still run.
* `_inject_ai_runtime_pythonpath(..., required=True)` re-raises
  `AIRuntimeUnavailable`; `_run_command_with_live_output` takes
  `requires_ai_runtime` and aborts before spawning.

`ManagedRuntimeHardFailTests` covers both, plus shadowing and device pinning.

## Findings 8–10 (P2)

**8 — harness.** The docstring now states plainly that the script needs Python
and that the product does not; the release gate is the in-app Demo Ready action
plus the workflow pass. It enumerates **every installed profile** via
`_installed_profiles` (not just the preferred one), runs each at `--level full`,
executes a real script through the frozen `ai_python_runner` and asserts numpy
loaded from the managed profile, and rejects any probe that passed without
running an inference.

**9 — probe identity.** `_probe_identity_problem` validates protocol version,
capability key, profile id, site-packages directory, requested device, and that
a success did not come with a non-zero exit. Eight tests in
`ProbeIdentityTests`.

**10 — `cuda:N`.** `resolve_device` preserves the index; `device_family`
classifies `cuda:N` as CUDA for variant selection; `select_runtime` carries the
index onto `RuntimeSelection.device`; the torch probe validates the index
against `torch.cuda.device_count()` and runs on that device.

## Two real bugs the deeper probes exposed

**BiRefNet is broken on every clean install.** With `--level full`, subject
masking failed at model load:

```
ImportError: This modeling file requires the following packages that were not
found in your environment: einops, kornia
```

Neither is in the managed requirements, and neither is present in either
installed profile on this machine. BiRefNet loads its own modeling code via
`trust_remote_code` and imports them directly. Added `einops==0.8.2` and
`kornia==0.8.3`, added both to `AI_RUNTIME_DINO_REQUIRED_MODULE_NAMES` and to
the `subject_masks` capability modules, and regenerated the locks. Verified in a
scratch directory: with them present BiRefNet loads and completes a forward pass
(output type `list`). The probe now reports the missing package at the import
stage with a specific message.

**The GPU profile installs both ONNX Runtime distributions.** The live GPU
profile holds `onnxruntime-1.27.0` *and* `onnxruntime_gpu-1.26.0` — insightface
pulls the CPU one in transitively. They install the same package directory, so
the provider set is decided by unpack order. The GPU locks now omit plain
`onnxruntime`, `--no-deps` stops pip re-adding it, and
`_validate_onnxruntime_exclusivity` rejects a profile holding both.
`test_shipped_locks_never_contain_both_onnxruntime_distributions` enforces it.

## Evidence

Real probes against the installed GPU runtime (RTX 4080 SUPER, torch
2.9.0+cu128):

| Capability | Level | Result |
| --- | --- | --- |
| `culling` | quick | PASS — providers Tensorrt/CUDA/CPU |
| `text_scoring` | quick | PASS — real inference, `embedding_dim` 512, CUDA provider active |
| `quality_topiq` | quick | PASS on CUDA **after** the graph preparation; PASS on CPU |
| `faces` | quick | PASS — real inference, 512 dims |
| `scene_masks` | full | PASS — weights loaded, forward pass, 7.9 s |
| `sam_masks` | full | PASS — weights loaded, forward pass, 5.9 s |
| `depth` | full | PASS — 287 tensors loaded, forward pass, 6.6 s |
| `subject_masks` | quick | **FAIL — `package_missing: einops`** (correct: this runtime predates the fix) |

Tests: **1233 passed / 29 failed**, up from 1191/35 in round 1 and 1070/23 at
baseline `91c5ba1`. Every remaining failure is in the same pre-existing modules
(`test_window_catalog_cache`, `test_decision_harvest`, `test_grid_failures`,
`test_catalog_repository`, `test_preview_*`, `test_ai_results_phase1`,
`test_ai_training`, plus the `WinError 50` subprocess group). Spot-checked
`test_run_command_can_tee_raw_output_chunks`: still `OSError: [WinError 50]`,
unrelated to these changes.

## Still not done

Phase 5 remains unexecuted: **no MSI has been built and no matrix row has been
run.** Everything above is verified in-source and against the installed managed
runtime, not against a packaged installation on a clean machine. The
`setup_msi.py` / `setup_linux.py` changes (three new `includes`, plus
`aiculler.topiq_onnx` for the TOPIQ preparation step) are untested beyond
syntax.

Locks exist only for `py313-windows-amd64`. Any other interpreter or platform
falls back to the pinned-but-unlocked requirement list, which diagnostics
report.
