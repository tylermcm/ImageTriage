# Handoff: AI Runtime Reliability Audit and Hardening

## Mission

Image Triage's defining AI features repeatedly fail when the application is demonstrated on computers other than the development machine. Treat this as a release-blocking reliability project, not as another isolated dependency repair.

Audit every path from the packaged application to a working AI result: installer launch, package acquisition, package installation, runtime discovery, Python path and DLL loading, device/provider selection, model download, model integrity, worker startup, inference, error reporting, repair, upgrade, and uninstall. Identify every failure point, prove each one with a focused test or diagnostic, and harden the system so a clean MSI installation works without asking the user to install Python, use a terminal, run as administrator, edit environment variables, map drives, or manually repair packages.

Do not declare success merely because imports or tests pass in the source checkout. The release gate is an end-to-end run from the installed MSI on clean machines.

## User Impact

The current failure mode is severe: the application opens and its ordinary tools work, but its defining AI features fail during demonstrations. Installation can report success while scene masking, culling, search, faces, or other AI capabilities remain unusable. Error messages expose large raw tracebacks but do not tell the user which managed runtime was selected, which package or model is invalid, or how to repair it.

The desired product behavior is:

1. A user chooses AI setup in Settings.
2. Image Triage installs everything needed into its own managed location.
3. It validates the exact runtime, packages, binary providers, models, and worker processes that the installed app will use.
4. It reports readiness per capability rather than presenting one misleading global success state.
5. A one-click Repair AI action can recover from interrupted, partial, corrupt, or obsolete installations.
6. A Demo Ready preflight can prove that every selected AI feature works before a presentation.
7. Failures degrade only the affected capability and produce a concise explanation, remediation, and support log.

## Repository State: Preserve Before Editing

- Workspace: `C:\Users\tylle\OneDrive\Documents\Playground`
- Branch: `codex/ui-ux-polish`
- Current HEAD when this handoff was written: `91c5ba1`
- The branch matches `origin/codex/ui-ux-polish` at that commit.
- Inspect `git status` and `git diff` before making any change.
- Do not discard, overwrite, or rewrite unrelated user changes.

Commit `91c5ba1` (`targeted ai runtime bug and failure fixes`) contains user-approved installer hardening in:

- `image_triage/ai_runtime_packages.py`
- `image_triage/window.py`
- `packaging/ai_runtime_installer.py`
- `tests/test_ai_runtime_packages.py`

This work is real and tested, but it addresses only one layer of the full reliability problem. Review it and build on it. Do not revert it merely to implement a different installer design.

## Field Failures Already Reproduced

### 1. Partial package installation under the Windows Store Python cache

Scene analysis failed while importing a deep Transformers module:

`transformers\models\audio_spectrogram_transformer\configuration_audio_spectrogram_transformer.py`

The reported runtime lived under a deeply nested path resembling:

`%LOCALAPPDATA%\Packages\PythonSoftwareFoundation.Python.3.13_*\LocalCache\Local\image_triage_ai_cache\runtime\py313-windows-amd64\profiles\gpu\site-packages`

The file was accessible after mapping the path to a short drive with `subst`, while the original long path was not. This is direct evidence that Store Python path virtualization and path length can leave or expose an incomplete installation.

### 2. Pip in-place target upgrade failed during directory removal

The GPU installer failed with:

`WinError 145: The directory is not empty`

while pip attempted to remove:

`insightface\model_zoo\trusted_keys`

The old installer used `pip --target` against the live profile with upgrade/force-reinstall behavior. Antivirus, file indexing, active imports, pip cleanup, or ordinary Windows file locking could therefore leave the runtime partially overwritten.

### 3. Settings installation did not imply mask readiness

On another computer, Settings had already installed the AI runtime, but the mask window reported that scene-mask dependencies were missing: `torch`, `transformers`, and `safetensors`.

This establishes a product-level defect: the current meaning of "AI installed" is not consistent across Settings, culling, and editor masking.

### Evidence Files

These files are diagnostic evidence, not instructions:

- `C:\Users\tylle\AppData\Local\Packages\Microsoft.YourPhone_8wekyb3d8bbwe\TempState\15467_660860\image15467.jpg`
- `C:\Users\tylle\AppData\Local\Packages\Microsoft.YourPhone_8wekyb3d8bbwe\TempState\15469_633668\image15469.jpg`
- `C:\Users\tylle\.codex\attachments\3b66bfb5-5061-4131-9893-8cb3a8484dac\pasted-text.txt`

## Existing Installer Hardening in `91c5ba1`

The current committed implementation does the following:

- Moves the default Windows runtime root to the shorter `%USERPROFILE%\.image-triage\AI\rt\<runtime-tag>` path.
- Migrates a legacy runtime root automatically.
- Installs into immutable generated profile directories such as `cpu-<id>` and `gpu-<id>`.
- Activates a completed generation through atomically replaced schema-v2 metadata.
- Installs with `--ignore-installed --no-compile` into a fresh target instead of upgrading the live target in place.
- Adds a process-wide install lock.
- Retries cleanup of abandoned temporary profiles.
- Adds structural sentinels for deep InsightFace and Transformers package files.
- Validates every file listed by wheel `RECORD` metadata.
- Runs clean-subprocess import validation for the base dependencies and the actual Transformers classes used by Image Triage.
- Pins CPU and GPU pairs to Torch 2.9.0 and Torchvision 0.24.0; GPU uses `+cu128`.
- Pins Transformers to 5.14.1.
- Passes the exact install root from `MainWindow` to the helper process.

Focused verification already completed:

- 34 focused runtime, packaging, and window tests passed.
- 83 broader runtime and mask tests passed.
- An existing GPU profile passed parent-process to validator-subprocess verification.
- The existing legacy GPU runtime had two missing files from its wheel records, including `onnxruntime/transformers/fusion_mha_dit.py`, confirming that partial installation is not hypothetical.

One test accidentally exercised migration against the real external runtime. The original runtime was restored and the temporary/intermediate replacement was removed. Confirm external state before doing more tests that can migrate user data.

## Runtime Architecture Inventory

The current package-resolution module has a broad dependency footprint. Graph inspection showed `image_triage/ai_runtime_packages.py` feeding at least:

- `image_triage/window.py`
- `image_triage/ai_workflow.py`
- `image_triage/aiculler_workflow.py`
- `image_triage/subject_masks.py`
- `image_triage/mask_engine_service.py`
- `image_triage/semantic_mask_service.py`
- `image_triage/semantic_index.py`

Do not audit these as independent islands. Trace the complete call path for every capability and identify where each process gets its executable, environment, runtime profile, model path, cache path, provider, and error handling.

### Managed Packages and Installer

Primary files:

- `image_triage/ai_runtime_packages.py`
- `packaging/ai_runtime_installer.py`
- `image_triage/window.py`

Current dependency families include:

- Base: NumPy, ONNX, ONNX Runtime or ONNX Runtime GPU, Pillow, OpenCV headless, scikit-learn, tqdm, PyYAML, and InsightFace.
- Full/masking: Torch, Torchvision, timm, Transformers, safetensors, and tokenizers.
- GPU ONNX Runtime is currently constrained to the CUDA-compatible generation used by the app.

Audit package pins as a compatibility set, not independently. Verify Python ABI, architecture, NumPy ABI, Torch/Torchvision pairing, CUDA generation, ONNX Runtime provider requirements, transitive packages, and binary DLL requirements.

### Frozen Application and Python Runner

Primary files:

- `setup_msi.py`
- `setup_linux.py`
- `freeze_support.py`
- `packaging/ai_python_runner.py`

cx_Freeze produces the main application plus `ai_python_runner`, `ai_runtime_installer`, and cleanup helpers. Heavy AI packages are intentionally managed separately, but frozen staging can also include AI source, standard-library modules, binary modules, and optionally `ai_site_packages`.

This creates multiple possible dependency universes. Test and document the exact final precedence of:

- Frozen application paths
- Staged AI source
- Bundled `ai_site_packages`
- Managed CPU profile
- Managed GPU profile
- User site-packages
- Global site-packages
- Current working directory
- Environment-provided `PYTHONPATH`

`ai_python_runner.py` prepends paths repeatedly. Do not infer precedence from source order; assert the resulting `sys.path`. Runtime-resolution exceptions are currently capable of being swallowed and reappearing later as misleading import failures. Remove silent failure at this boundary.

Audit DLL search paths for Torch, ONNX Runtime `capi`, OpenCV, InsightFace dependencies, package `*.libs` directories, and every native extension. Prove the installed executables work from an actual MSI directory with no source checkout present.

### Model Management

Primary file:

- `image_triage/ai_model.py`

Managed model families include DINO, CLIP, OneFormer, BiRefNet, TinyCLIP ONNX, TOPIQ, AuraFace, SAM 2.1, and depth estimation. Repositories and revisions are generally pinned, but inspect every declaration directly.

Confirmed weaknesses:

- `AIModelInstallation.is_installed` primarily treats required path existence as installation success.
- Existing files can be skipped without rechecking hash or loadability.
- A multi-file model bundle is downloaded into its live directory one file at a time, so an interrupted or mixed-revision bundle can look installed.
- File-level `.download` replacement is atomic, but bundle activation is not.
- TOPIQ and AuraFace currently have empty expected-hash maps.
- There is no shared download/install lock, bundle generation metadata, disk-space preflight, reliable retry/backoff policy, resumable download contract, or proxy/CA diagnosis.
- Model paths still derive from a deep Store-Python-sensitive cache root even though the runtime root has been shortened.

Replace existence checks with manifest-based bundle validation. Every model artifact must have expected size and cryptographic hash. Install each bundle into a staged generation, validate every file and a representative model load, then activate it atomically. Never mix revisions in one live bundle.

### Settings Versus Editor Mask Models

Primary file:

- `image_triage/ui/photo_editor_panel.py`

`_MaskModelDownloadTask` separately installs OneFormer, BiRefNet, and SAM model bundles. `_mask_models_are_installed` relies on file existence. Main Settings setup can complete without proving these editor capabilities are ready.

Unify this under one capability manifest and one setup/repair system. The user may choose a minimal feature set, but the UI must state exactly what is and is not ready. A global success message must never be emitted when a selected capability is missing or unverified.

### Direct Runtime Consumers

Audit imports, providers, model contracts, and subprocess behavior in at least:

- `aiculler/features.py`: ONNX Runtime
- `aiculler/topiq_onnx.py`: ONNX and `numpy_helper`
- `aiculler/text_scoring.py`: ONNX Runtime and tokenizers
- `image_triage/face_index.py`: ONNX Runtime
- `image_triage/semantic_index.py`: ONNX Runtime
- `image_triage/quality/face.py`: InsightFace `FaceAnalysis`
- `image_triage/depth_worker.py`: Torch and Transformers depth classes
- `image_triage/oneformer_worker.py`: Torch and OneFormer classes
- `image_triage/birefnet_worker.py`: OpenCV, Torch, and `AutoModelForImageSegmentation`
- `image_triage/sam_worker.py`: Torch and SAM 2
- `image_triage/mask_engine_worker.py`: lazy composition of mask engines

An import-only probe is necessary but insufficient. Add a low-cost representative initialization or tiny inference probe wherever binary operators or model configuration can fail after import.

### AI Workflow Runtime

Primary file:

- `image_triage/ai_workflow.py`

`AIWorkflowRuntime.validate()` mostly validates paths, not execution, imports, providers, or model loading. Device selection can infer GPU from profile presence even when the GPU runtime is unusable. Process launch injects a managed runtime into `PYTHONPATH`; inspect whether resolution uses the exact device selected by the workflow. A concrete risk is resolving with `device="auto"` while the workflow selected a different device.

Make runtime selection explicit and immutable for one job. Pass the selected profile identity to every worker. A worker must report the profile it actually loaded, not merely the profile requested by its parent.

### CLI Culler Runtime

Primary file:

- `image_triage/aiculler_workflow.py`

`AICullerRuntime.validate()` also relies heavily on path existence. It supports many independent environment overrides for executable, CLI, roots, models, TOPIQ, tokenizers, and categories. Manual `PYTHONPATH` construction appears at multiple process-launch sites.

Collapse this into the same runtime resolver and capability health system. Verify TinyCLIP input/output names and embedding dimensions, TOPIQ loadability, ONNX providers, tokenizer/model pairing, and explicit CPU/GPU fallback behavior. Do not permit a global package or stale model path to win silently.

### Mask Services

Primary files:

- `image_triage/subject_masks.py`
- `image_triage/mask_engine_service.py`
- `image_triage/semantic_mask_service.py`

These services duplicate readiness logic and often infer availability from directories or required files. They can disagree with Settings and with the runtime validator. Replace duplicate checks with a central capability health API while keeping worker-specific launch code localized.

### Other Cache and Install Roots

Audit all path derivation in:

- `image_triage/depth_maps.py`
- `image_triage/prompt_masks.py`
- `image_triage/semantic_masks.py`
- `image_triage/subject_masks.py`
- `image_triage/ai_workflow.py`
- `image_triage/aiculler_workflow.py`
- `image_triage/aiculler_global_store.py`
- `image_triage/scan_cache.py`
- `image_triage/perf.py`

These modules independently use combinations of `LOCALAPPDATA`, `APPDATA`, `USERPROFILE`, XDG variables, folder-local hidden directories, and application-relative staging paths. Inventory every location and owner. Unify managed AI assets under a short canonical root with explicit subdirectories, backward-compatible migration, writable checks, free-space checks, and logged resolution.

Folder-local artifacts must be tested on read-only, OneDrive, network, removable, and very long paths. Local staging must not accidentally make correctness depend on writing beside the user's photos.

## Required Capability Model

Create one central, reusable health service or equivalent source of truth. It must report readiness separately for:

- Core AI culling
- TinyCLIP semantic text scoring
- TOPIQ quality scoring
- Faces and named-person workflows
- Semantic search/indexing
- DINO features
- OneFormer scene selection
- BiRefNet subject selection
- SAM click selection
- Depth estimation

For each capability, report:

- Capability identifier and user-facing name
- Requested device and actual selected device
- Runtime profile ID and absolute root
- Worker executable and entry point
- Required package distributions and pinned versions
- Imported module `__file__` locations and runtime versions
- Required model bundle, revision, files, sizes, and hashes
- Requested and available ONNX providers
- Torch CUDA availability and device information where relevant
- Validation stage reached
- Exact failure category
- Concise user-facing remediation
- Detailed redacted diagnostic log path

Readiness must be computed from this source of truth everywhere: Settings, toolbar actions, editor mask UI, culling, search, and worker launch.

## Exhaustive Failure Audit

At minimum, investigate and test every category below. Add categories discovered during tracing.

### Installer and Filesystem

- Unsupported Python tag or architecture
- x64 versus ARM64 mismatch
- Store Python path virtualization
- Windows long paths disabled
- Username containing spaces or non-ASCII characters
- Missing, unwritable, or redirected profile directory
- OneDrive/network/removable path behavior
- Low disk before install and disk exhaustion mid-operation
- File locked by a running worker, antivirus, indexer, or another installer
- Directory-not-empty cleanup failures
- Interrupted install from cancellation, shutdown, sleep, reboot, or process crash
- Concurrent Settings install, repair, model download, and worker startup
- Stale lock recovery without allowing unsafe concurrent mutation
- Atomic metadata failure or stale active-generation pointer
- Orphaned staging/profile generations and bounded cleanup
- Upgrade from every supported prior runtime/cache layout
- MSI update or uninstall while AI workers are alive
- Clean uninstall without deleting unrelated user data

### Network and Acquisition

- No network
- DNS failure
- Connection reset and timeout
- Partial content and truncated downloads
- Proxy authentication
- Corporate TLS interception or custom CA
- Certificate validation failure
- Hugging Face outage, rate limit, authorization failure, removed revision, or LFS error
- HTML/error response saved under a model filename
- Retry policy with bounded exponential backoff and cancellation
- Resume behavior where supported
- Hash mismatch and repeated mismatch
- Disk-space accounting before package and model downloads
- Offline installation from a deterministic wheel/model cache if supported

### Package Integrity and Compatibility

- Missing files despite valid-looking `.dist-info`
- Incorrect or missing wheel `RECORD`
- Corrupt native extensions
- NumPy ABI mismatch
- Torch/Torchvision mismatch
- Transformers/tokenizers/safetensors incompatibility
- InsightFace/OpenCV/ONNX Runtime incompatibility
- Both `onnxruntime` and `onnxruntime-gpu` distributions installed into one target
- Dependency release drift from loose transitive requirements
- Managed packages shadowed by bundled, global, user, or source-tree modules
- Managed package from the wrong profile imported successfully
- Imports succeed but operator registration or model load fails
- VC++ runtime or required DLL absent
- Unsupported CPU instruction set

### GPU and Provider Selection

- NVIDIA driver absent or too old
- CPU-only, AMD, and Intel machines
- CUDA Torch installed while `torch.cuda.is_available()` is false
- ONNX Runtime GPU package imports but CUDA provider is unavailable
- CUDA/cuDNN DLL missing or wrong generation
- Torch CUDA generation incompatible with ONNX Runtime expectations
- GPU out of memory during startup or inference
- Provider silently falling back to CPU
- Explicit CPU request accidentally loading the GPU profile
- Explicit GPU request accidentally loading the CPU profile
- Auto mode changing profiles between parent and worker
- Multiple GPUs and invalid selected device index
- Sleep/resume or driver reset invalidating a long-lived worker

### Model Integrity and Contracts

- Missing, empty, truncated, or corrupt file
- Model/config/tokenizer from different revisions
- Present file with wrong hash or size
- ONNX external-data companion missing
- Wrong input/output names, dimensions, or dtypes
- TinyCLIP embedding dimension mismatch with existing stored embeddings
- Model repository revision removed or retagged
- Model loads on CPU but not selected GPU/provider
- Model import trust or custom-code assumptions
- Cache migration from an older model format
- Partial bundle falsely treated as installed

### Process and Runtime Boundaries

- Frozen helper executable missing or blocked
- Source and frozen launch paths diverging
- Incorrect current working directory
- Incorrect `sys.path` precedence
- Missing DLL search directory
- Environment variables leaking from user/global Python installs
- Runtime resolver exception swallowed before worker launch
- Worker hangs without timeout or heartbeat
- Worker crashes without captured stderr/exit category
- Parent cancellation leaves worker or files behind
- Broken pipe, malformed JSON, or protocol version mismatch
- OOM and unhandled native process termination
- Multiple workers contending for GPU memory
- App update changes protocol while stale worker remains alive

### Application Behavior and Diagnostics

- Global "AI Setup Complete" shown while any selected capability is unready
- One optional capability failure disables unrelated AI features
- Feature UI permits an operation known to be unavailable without explaining why
- Repair mutates a healthy live runtime in place
- Automatic fallback hides a failure or produces unexpectedly slow behavior
- Raw traceback used as the primary user message
- Logs omit actual package locations, profile ID, provider, model revision, or worker command
- Logs leak usernames, tokens, URLs with credentials, or unrelated environment secrets
- Settings and feature panes report contradictory readiness

## Implementation Plan

Do the work in explicit phases. At the end of each phase, report findings, edited files, tests, unresolved risks, and the exact behavior available for manual validation.

### Phase 0: Complete Architecture and Failure Map

Before broad edits:

1. Inspect commit `91c5ba1`, plus the current working-tree diff, and preserve its intent.
2. Trace every AI entry point from UI action to result.
3. Produce a machine-readable inventory of packages, models, processes, environment variables, cache roots, and consumers.
4. Record every duplicated resolver/readiness check and every silent exception/fallback.
5. Reproduce the known field failures where practical using isolated temporary roots.
6. Establish source and frozen baselines separately.

Do not stop at a conceptual diagram. Cite concrete files, functions, launch commands, path precedence, and failure behavior.

### Phase 1: Canonical Manifest and Storage

1. Introduce one canonical short managed-AI root provider for Windows and equivalent platform-appropriate roots elsewhere.
2. Define a versioned manifest for runtime profiles, package pins, model bundles, worker protocol versions, and capability requirements.
3. Add safe migration from every legacy root discovered in Phase 0.
4. Preserve immutable runtime generations and atomic activation.
5. Add path, permission, architecture, and free-space preflight.
6. Eliminate conflicting ad hoc root selection while retaining documented environment overrides for testing/support.

### Phase 2: Transactional Model Installation

1. Give every model file an expected size and cryptographic hash, including TOPIQ and AuraFace.
2. Stage complete versioned bundle generations outside the active path.
3. Validate all files plus a representative load before atomic activation.
4. Add cross-process locking, cancellation, retry/backoff, cleanup, and interrupted-install recovery.
5. Never skip an existing file solely because it exists.
6. Never mutate the active model generation in place.

### Phase 3: Central Capability Health and Repair

1. Implement the capability model described above.
2. Add subprocess package/import probes using the exact frozen runner, selected profile, environment, and DLL paths.
3. Add provider/device probes and low-cost model initialization or inference probes.
4. Make Settings and every feature consume the same readiness result.
5. Add Repair AI for a selected capability and all installed capabilities.
6. Add a Demo Ready preflight that verifies the user's selected feature set end to end.
7. Add a redacted Copy/Export Diagnostics action.

### Phase 4: Worker Launch Consistency

1. Use one environment builder and one explicit runtime/profile selection contract for all workers.
2. Assert and log final `sys.path`, imported module locations, DLL search paths, provider list, and model paths.
3. Remove silent runtime-resolution failures.
4. Version worker protocols and fail clearly on parent/worker mismatch.
5. Add startup timeout, heartbeat where warranted, cancellation, and child cleanup.
6. Make fallback an explicit policy and report when it occurs.

### Phase 5: Packaged Clean-Machine Validation

Build and test the actual MSI. The matrix must include:

- Windows 11 x64 with NVIDIA GPU and no Python installed
- Windows 11 x64 CPU-only and no Python installed
- Windows Store Python installed
- Non-admin account
- Username with spaces and non-ASCII characters
- Long paths disabled
- OneDrive Documents enabled
- Low-disk condition
- Offline installation attempt
- Proxy/TLS failure simulation
- Interrupted install followed by relaunch and repair
- Defender or simulated file-lock interference
- Repair over healthy and deliberately partial runtimes
- Application upgrade from a prior runtime/cache layout
- Uninstall and reinstall

The harness must invoke the frozen `ai_runtime_installer.exe`, `ai_python_runner.exe`, and capability probes from the installed location. Do not mount or reference the source checkout.

## Testing Strategy

### Unit Tests

- Manifest parsing and version compatibility
- Root selection and every migration path
- Atomic profile and model activation
- Lock acquisition, stale-lock recovery, and concurrent-operation rejection
- Wheel `RECORD` and model hash/size validation
- Environment and `sys.path` ordering
- Device/profile selection propagation
- Error categorization and redaction
- Capability dependency graph and isolated degradation

### Deterministic Integration Tests

Use local temporary roots, a local fake HTTP server, and a controlled wheel/model fixture repository. Do not make normal CI depend on public package or model services.

Inject failures at every boundary: truncated files, bad hashes, locked directories, subprocess failures, timeouts, stale metadata, no space, interrupted activation, wrong providers, missing DLLs, mismatched model files, and malformed worker responses.

### External and Packaged Smoke Tests

Run gated network tests separately. Record package versions and `__file__` paths, model revision/hashes, selected profile, ONNX providers, CUDA state, worker commands, and final capability result.

Fresh-machine acceptance must cover actual user workflows, not only probes:

1. Install the MSI.
2. Run AI setup from Settings.
3. Restart the application.
4. Open a real library.
5. Complete AI culling.
6. Complete semantic text search.
7. Complete face detection/indexing.
8. Complete OneFormer scene selection.
9. Complete BiRefNet subject selection.
10. Complete SAM click selection.
11. Complete depth estimation.
12. Run Repair AI and repeat representative workflows.

## Current Test Environment Notes

The normal Windows Store Python in this development shell can fail before tests run with `WinError 10106`. Use the repository interpreter runner where necessary:

```powershell
$env:PYTHONPATH=(Get-Location).Path
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
& pythonw3.13.exe scripts/run313.py <log-file> pytest <test-arguments>
```

Current broad baseline:

- Full collection is blocked by optional `onnx` missing from `test_aiculler_topiq_onnx.py` in this local environment.
- Excluding that module produced 1113 passing, 35 unrelated or stale failures, and 5 expected failures.
- Do not silently redefine these existing failures as part of this task. Classify them, fix only relevant failures, and preserve a before/after record.

## Required User-Facing Diagnostics

Do not show a giant traceback as the primary dialog. Present:

- Which capability failed
- Which validation stage failed
- Whether the cause is runtime, package integrity, device/provider, model bundle, network, filesystem, or worker execution
- The selected profile and device in concise form
- A direct action: Retry, Repair, Switch to CPU, Open Settings, or Copy Diagnostics
- The log file location

Detailed logs must retain the traceback and process output while redacting credentials and other secrets. Include enough evidence to diagnose remote machines without asking the user to reproduce the problem in a terminal.

## Non-Negotiable Constraints

- Do not fix this by installing packages globally or into the user's Python.
- Do not require Python to be installed on the target machine.
- Do not require administrator rights, terminal commands, `subst`, registry changes, or enabling long paths.
- Do not treat source-venv success as packaged-app success.
- Do not mutate active runtime or model generations in place.
- Do not infer installation success from directory or file existence alone.
- Do not silently catch runtime-resolution errors.
- Do not silently select a different profile or provider.
- Do not let one optional model disable unrelated capabilities.
- Do not perform unrelated UI, editor, or architecture refactors.
- Keep changes localized and extend existing reusable systems where they are sound.
- Preserve current non-AI behavior.

## Deliverables

1. An architecture and failure-point report with concrete code references.
2. A versioned package/model/capability manifest.
3. A canonical path and migration implementation.
4. Transactional package and model installers.
5. Central capability health, repair, and Demo Ready diagnostics.
6. Unified source/frozen worker launch behavior.
7. Focused unit and deterministic integration tests.
8. A clean-machine MSI validation matrix with captured results.
9. A concise list of residual external risks that cannot be controlled by the application.
10. Every edited file listed at each checkpoint.

## Definition of Done

This project is not done when an installer exits zero or a package imports in the development checkout. It is done when:

- A fresh supported Windows machine can install the MSI and selected AI capabilities entirely through the application UI.
- The application validates the exact frozen executables, managed runtime, packages, providers, models, and worker paths it will use.
- All selected defining workflows complete end to end after an app restart.
- Interrupted and corrupt installations are detected and repaired without manual cleanup.
- CPU-only operation works deliberately and GPU operation proves that the requested GPU providers are actually active.
- Every failure produces an actionable, capability-specific message and a useful redacted diagnostic bundle.
- Automated tests cover every practical injected failure boundary.
- The clean-machine matrix passes before release.

Begin by inspecting the repository, commit `91c5ba1`, and the current working tree. Report the complete failure map and any contradictions in the current architecture before making broad changes, then implement the phases in order with evidence at each checkpoint. Do not repeat narrow trial-and-error repairs without proving which boundary failed.
