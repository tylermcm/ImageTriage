# AI Runtime Failure Map (Phase 0)

Baseline: branch `codex/ui-ux-polish`, HEAD `91c5ba1`.

This is the concrete architecture and failure-point report required before the
hardening phases. Every entry cites the file and function that produces the
behaviour, not a conceptual sketch.

## 1. Root selection is not one system

Two different "user cache root" helpers exist, and only one of them undoes
Windows Store Python's path virtualization.

| Helper | File | De-virtualizes Store Python? |
| --- | --- | --- |
| `_default_user_cache_root` | `image_triage/ai_runtime_packages.py:994` | yes |
| `_default_user_cache_root` | `image_triage/ai_model.py:823` | **no** |
| `_default_depth_cache_root` | `image_triage/depth_maps.py:53` | **no** |
| `_default_prompt_mask_root` | `image_triage/prompt_masks.py:64` | **no** |
| `_default_semantic_mask_root` | `image_triage/semantic_masks.py:145` | partial |
| `_default_subject_mask_root` | `image_triage/subject_masks.py:72` | **no** |
| `_app_data_root` | `image_triage/aiculler_global_store.py:253` | n/a (`APPDATA`) |
| `_app_data_root` | `image_triage/scan_cache.py:9` | n/a (`APPDATA`) |
| perf log root | `image_triage/perf.py:148` | **no** |

Commit `91c5ba1` shortened the *runtime* root to
`%USERPROFILE%\.image-triage\AI\rt\<tag>` but left every **model** and cache root
on raw `LOCALAPPDATA`. Under Store Python that expands to

```
%LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python.3.13_*\LocalCache\Local\image_triage_ai_cache\models\...
```

which is the exact shape reported in field failure #1. Fixing only the runtime
root did not fix model acquisition.

**Root cause A — model and cache roots still land in the virtualized Store path.**

## 2. "Installed" means "a directory exists"

* `AIModelInstallation.is_installed` (`image_triage/ai_model.py:176`) is
  `not missing_files`, and `missing_files` (`:168`) is a pure `Path.exists()`
  sweep. Size, hash and loadability are never consulted.
* `download_ai_model` (`image_triage/ai_model.py:470`) skips any file that
  already exists unless `force=True`, so a truncated or wrong-revision file is
  never re-checked once present.
* Files are written straight into the live `install_dir`, one at a time. Only
  the individual file replace is atomic (`_download_file`, `:768`); the *bundle*
  is not. An interrupted multi-file download leaves a directory that reports
  `is_installed == True` on the next launch as soon as the last filename happens
  to exist.
* `DEFAULT_AICULLER_TOPIQ_MODEL_SHA256` (`:62`) and
  `DEFAULT_AICULLER_FACE_MODEL_SHA256` (`:132`) are empty dicts, so TOPIQ and
  AuraFace are downloaded with **no** integrity check at all.
* There is no cross-process lock, no retry/backoff, no free-space preflight, and
  no resume. A single reset connection surfaces as a raw `URLError`.

**Root cause B — model installation is neither verified nor transactional.**

## 3. Runtime readiness is computed four different ways

| Consumer | Check | Required modules |
| --- | --- | --- |
| Settings / `MainWindow` | `load_ai_runtime_installation_status` → `_profile_status` (`ai_runtime_packages.py:573`) | base ± DINO, gated on `dino_enabled_variants` |
| Scene masks | `_resolve_semantic_runtime` (`semantic_mask_service.py:65`) | torch, transformers, safetensors, PIL, numpy |
| Subject masks | `_resolve_subject_runtime` (`subject_masks.py:435`) | torch, transformers, timm, safetensors |
| Mask engine host | `_resolve_engine_runtime` (`mask_engine_service.py:586`) | torch, transformers, safetensors, timm, PIL, numpy |

All three service checks are `(site_dir / name).exists()` — directory presence,
not importability. All four can disagree, and they disagree in a specific,
reproducible way:

`_profile_status` only requires the DINO module set when the variant appears in
`dino_enabled_variants`. A `--no-dino` install therefore yields
`status.is_installed == True`, Settings reports success, and the editor then
fails with *"missing scene-mask dependencies: torch, transformers,
safetensors"*. This is field failure #3, exactly.

**Root cause C — one global boolean stands in for per-capability readiness.**

## 4. Success is announced without verifying anything

`_handle_ai_runtime_install_finished` (`image_triage/window.py:10130`) shows
**"AI Setup Complete"** as soon as the installer process exits zero. It does not
consult a capability probe, and it has no knowledge of the editor mask models at
all — those are installed separately by `_MaskModelDownloadTask`
(`image_triage/ui/photo_editor_panel.py:135`) and gated by
`_mask_models_are_installed` (`:2951`), another pure existence check.

**Root cause D — the completion message is decoupled from the thing it claims.**

## 5. Device selection can diverge between parent and worker

`_inject_ai_runtime_pythonpath` (`image_triage/ai_workflow.py:1532`) resolves
site-packages from `resolve_ai_runtime_site_packages(device=...)`, which calls
`_select_runtime_variant` (`ai_runtime_packages.py:649`). With `device="auto"`
that function prefers the GPU profile whenever a GPU profile *exists on disk* —
`_profile_status` checks for `torch/lib/*cuda*.dll` presence, never
`torch.cuda.is_available()`. A machine with no NVIDIA driver that once installed
the GPU profile will silently select it.

Worse, each worker re-resolves independently (`mask_engine_service.py:587`,
`subject_masks.py:436`, `semantic_mask_service.py:66`), so parent and child can
pick different profiles within one job. Nothing reports which profile actually
loaded.

**Root cause E — profile selection is re-derived per process instead of pinned.**

## 6. Runtime resolution failures are swallowed

`packaging/ai_python_runner.py:_cached_runtime_site_packages` wraps both the
import and the call in bare `except Exception: return ()`. A broken metadata
file, an unreadable root or a permissions error therefore produces *no managed
site-packages at all*, and the failure resurfaces much later as
`ModuleNotFoundError: No module named 'torch'` inside a worker — pointing the
user at the wrong problem.

**Root cause F — the resolver fails silently at the worst boundary.**

## 7. `sys.path` precedence is order-dependent and unasserted

`_configure_runtime_environment` (`packaging/ai_python_runner.py:139`) calls four
prepend helpers in sequence, each of which does
`sys.path.remove` + `sys.path.insert(0, ...)`. Final precedence is therefore the
*reverse* of the call order and, within `_prepend_ai_site_packages`, bundled
`ai_site_packages` ends up **below** the managed profile only because the managed
loop runs second. Nothing asserts the result, so a staged
`ai_site_packages` directory shipped in the MSI can shadow the managed runtime
if that ordering is ever changed.

## 8. Confirmed failure classes with no current detection

* Partial wheel installs — real: the legacy GPU runtime was missing
  `onnxruntime/transformers/fusion_mha_dit.py` and one other RECORD entry.
* `onnxruntime` **and** `onnxruntime-gpu` co-installed into one target: nothing
  rejects this, and the import order decides which provider set wins.
* ONNX Runtime GPU imports fine while the CUDA provider is absent — never probed;
  `_torch_cuda_binaries_present` only looks for DLL files on disk.
* TinyCLIP embedding-dimension drift against previously stored embeddings.
* HTML error pages saved under a model filename (no content-type or hash check
  for TOPIQ/AuraFace).

## Consequences for the plan

The phases that follow address these six root causes in order:

1. Phase 1 — one canonical root (A), one manifest describing capabilities (C).
2. Phase 2 — transactional, hash-verified model bundles (B).
3. Phase 3 — per-capability health, probes, repair, diagnostics (C, D).
4. Phase 4 — pinned profile selection, one env builder, no silent failures (E, F).
5. Phase 5 — packaged clean-machine validation harness.

---

# Outcomes

Each root cause above and where it is now addressed.

| Root cause | Fix | Proof |
| --- | --- | --- |
| A — model/cache roots under virtualized `LOCALAPPDATA` | `image_triage/ai_paths.py` gives one short managed root; every `default_*_install_dir` in `ai_model.py` resolves through it; migration is explicit (`migrate_ai_assets`) so a status check never moves a user's files | `tests/test_ai_paths.py` |
| B — model install neither verified nor transactional | `image_triage/ai_model_store.py` stages, hash-verifies and atomically activates every bundle, with a cross-process lock, retry/backoff, resume, disk preflight and abandoned-staging cleanup | `tests/test_ai_model_store.py` |
| C — one global boolean stood in for readiness | `image_triage/ai_manifest.py` declares ten capabilities and nine bundles; `image_triage/ai_health.py` computes readiness per capability; the three duplicate service checks now call it | `tests/test_ai_health.py`, `tests/test_ai_env.py` |
| D — success announced without verification | `_handle_ai_runtime_install_finished` / `_handle_ai_model_download_finished` now run a real readiness pass before saying anything; **Check AI Readiness**, **Repair AI** and **Copy AI Diagnostics** added to the AI Setup menu | `tests/test_ai_health.py::ReadinessSummaryTests` |
| E — profile re-derived per process | `image_triage/ai_env.py` pins one `RuntimeSelection` per job and hands the same identity to every worker; workers report what they actually loaded | `tests/test_ai_env.py` |
| F — resolver failed silently | `ai_python_runner` reports every resolution problem on stderr and asserts that bundled packages cannot shadow the managed profile; `_inject_ai_runtime_pythonpath` logs instead of returning empty | `tests/test_ai_python_runner.py` |

## Model integrity: before and after

| Bundle | Files with a published SHA-256 before | After |
| --- | --- | --- |
| DINOv3 | 2 / 2 | 2 / 2 |
| CLIP | 7 / 7 | 7 / 7 |
| OneFormer | 7 / 7 | 7 / 7 |
| BiRefNet | 1 / 4 | 4 / 4 |
| SAM 2.1 | 5 / 5 | 5 / 5 |
| Depth Anything V2 | 0 / 3 (revision was the moving branch `main`) | 3 / 3, pinned to `5426e4f0` |
| TinyCLIP | 2 / 2 | 2 / 2 |
| **TOPIQ** | **0 / 1** | **1 / 1** |
| **AuraFace** | **0 / 4** | **4 / 4** |

Every file also carries an expected size now, which is what makes the cheap
per-launch check able to detect truncation without hashing gigabytes.
Regenerate with `python scripts/refresh_ai_model_manifest.py`.

## Residual risks the application cannot control

1. **Hugging Face availability.** Every model download depends on
   `huggingface.co`. An outage, a removed revision or an org-level block
   produces a clear message and a retry, but no local fallback exists. Mitigation
   would be a mirrored artefact store, which is a distribution decision.
2. **NVIDIA driver version.** The probe detects an absent or too-old driver and
   offers CPU, but cannot install a driver.
3. **TLS-inspecting proxies.** Detected and named, but the fix is to trust the
   proxy's CA at the OS level — an IT action.
4. **Windows ARM64.** No managed wheels are published for the pinned
   Torch/ONNX Runtime pair. `ai_paths.is_windows_arm64` identifies the machine so
   the UI can say so, rather than failing during install.
5. **Antivirus quarantining a downloaded model.** Activation retries a locked
   directory, and a quarantined file is caught by the next verification, but a
   scanner that deletes the file on every download cannot be worked around.
6. **Disk exhaustion mid-write.** Preflight checks free space before staging and
   again before each file, but a concurrent process can still fill the volume;
   the staged generation is discarded and the live one is untouched.

## Test record

Both runs use the repository interpreter runner, excluding six modules that
cannot be collected because `onnx`/`torch` are deliberately absent from the host
interpreter (`test_aiculler_topiq_onnx`, `test_dinov2_extractor`,
`test_efficientvit_sam_sandbox`, `test_grounded_sam_sandbox`,
`test_oneformer_sandbox`, `test_ranking_dino_fallback`).

| | Baseline `91c5ba1` (clean worktree) | After |
| --- | --- | --- |
| Passed | 1070 | 1186 |
| Failed | 23 | 23–35 (see flakiness below) |
| xfailed | 5 | 5 |

The AI-focused modules specifically:

| | Baseline | After |
| --- | --- | --- |
| Passed | 129 | 212 |
| Failed | 1 | 0 |

**No failure in the after-run is absent from the baseline.** New coverage:
`test_ai_paths.py`, `test_ai_model_store.py`, `test_ai_env.py`,
`test_ai_health.py`, plus additions to `test_ai_workflow.py` and
`test_ai_runtime_packages.py`.

### Pre-existing failures, unchanged by this work

* **Stable (23).** `test_window_catalog_cache` (6), `test_decision_harvest` (6),
  `test_grid_failures` (4), `test_catalog_repository` (2),
  `test_preview_navigation`, `test_preview_polling`, `test_ai_results_phase1`,
  `test_ai_training`, `test_ai_workflow::test_default_runtime_prefers_generic_bundled_checkpoint_location`.
  The `test_window_catalog_cache` group fails because `_WindowAiLoadStub` and
  `_WindowAiRunStub` lack methods the window calls (for example
  `_recompute_ai_demoted_burst_paths`) — verified identical at `91c5ba1`.
* **Flaky.** `test_decision_harvest`, `test_extract_entrypoint`,
  `test_aiculler_cli_reports` and the `test_run_command_*` cases in
  `test_ai_workflow` intermittently fail with
  `OSError: [WinError 50] The request is not supported` when spawning a piped
  subprocess under the console-less `pythonw` harness. Measured over three
  consecutive runs of that group: baseline 9/9/5 failures, after 8/8/7. The
  variance is the harness, not the code.

### Popout viewer audit

The popout viewer (`PreviewDialog`, `image_triage/preview.py`) has no AI
resolution of its own: it hosts the same `PhotoEditorPanel` as the docked
editor and only relays `subject_warm_requested` / `semantic_warm_requested`
onto the shared thread pools. Both editor surfaces therefore go through the
same runtime contract.

The audit did surface two real defects in the code the popout drives:

* **`depth_maps.py` and `prompt_masks.py` both called `validate_semantic_runtime()`.**
  Depth estimation and SAM click selection were validating *OneFormer's*
  readiness. They now call `validate_mask_runtime("depth")` and
  `validate_mask_runtime("sam_masks")`, so each reports its own capability and
  its own remediation. `semantic_mask_service` gained
  `resolve_mask_runtime(capability_key)` plus a per-capability validation cache;
  `subject_masks` uses the same helper.
* **`_resolve_engine_runtime` required both mask bundles to start the host.**
  This was introduced earlier in this work and would have let one missing model
  (OneFormer, say) block depth estimation and click selection — the exact
  behaviour the handoff forbids. The shared MaskEngine host now requires only
  the managed PyTorch runtime; each caller has already verified its own
  capability before asking the host to warm an engine.

Covered by `tests/test_semantic_mask_service.py::PerCapabilityValidationTests`.

### Fixed in passing

`_directory_signature` (`image_triage/ai_workflow.py`) raised `OSError` on any
unreadable entry, which aborted an AI stage cache-key computation as soon as one
reparse point, WSL symlink or antivirus-locked file appeared in the engine root.
Making it tolerant then exposed a second problem — an unbounded `rglob` over an
arbitrary user directory — so the walk now prunes non-input directories and is
capped at `SIGNATURE_MAX_ENTRIES`. On this checkout that took the affected test
from *hanging indefinitely* to 1.2 s.
