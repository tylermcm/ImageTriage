# Direct CLI-Culler integration: implementation and validation

Work is on `codex/cli-culler-integration`; the contract remains
[the integration plan](cli_culler_integration_plan.md). Engine source and
PhotoCraft are unchanged.

## Implemented

- Immutable CLI-Culler and bracketeering pins, unchanged wheel builds with
  artifact hashes, and a separate worker runtime. The host retains its legacy
  `aiculler` package.
- Hash-locked core dependency resolution and offline wheel installation. The
  optional CPU model runtime reuses the model-library versions in the app's
  existing Windows CPU lock, resolving its own platform-specific closure.
  No model weights are downloaded.
- A plain-file manifest/model-store import closure, included in freeze inputs.
  Source and prepared-worker launches are supported. Runtime wheel identities
  are checked before native Analyze starts. The cache namespace follows the
  resolved lock, isolating engine/runtime generations.
- Structured worker progress and diagnostics, Qt task management, cancellation,
  and process-tree ownership. POSIX runs have a supervisor; Linux supervisors
  reap orphaned metadata/inference descendants without changing the GUI
  process's child-reaping policy. Windows uses a kill-on-close job.
- Unique output generations, native JSON/index validation, and atomic active-run
  pointers with output hashes. Failure/cancellation preserves the previous run
  and committed score caches. Reopening checks integrity.
- Native result loading and catalog migration 10, storing the complete document.
  Unknown fields and null scores survive reload. Capture, physical group, scene,
  and comparison-set identities remain separate. Native results bypass legacy
  percentile enrichment, low-detail verdict changes and single-keeper burst
  demotion. Keep maps to Keeper; no native decision becomes an automatic Winner.
- An opt-in Analyze path using folder scope and the exact selected JSON profile.
  Its settings editor exposes all native keys and validates through
  `CullSettings.from_dict`. Native reruns use the same engine and cache. The
  requested models are never removed by the old CPU/GPU heuristics.
- Native review summaries show actual models, warnings and exact settings.
  Analyze writes analysis artifacts only. Explicit Apply retains its existing
  boundary and excludes manually marked Winners from native Reject file moves.
  Raw engine documents remain independent of human annotations.
- Search/index import into the host's existing storage, propagating a capture's
  vectors to all file paths and retaining provenance. Incompatible search
  revisions are rejected instead of labelled as the host model. People imports
  retain normalized geometry in the immutable native archive, convert boxes
  into upright preview coordinates for host storage, map run-local person IDs
  through image/face records, and preserve named people on repeat imports.
  Index-import failures are visible and do not discard or reclassify decisions.

## Build and use the development runtime

From the ImageTriage checkout, with Python and uv available:

```bash
python scripts/build_cli_culler_wheels.py --out-dir /path/to/culler-wheels
python scripts/prepare_cli_culler_runtime.py \
  --wheelhouse /path/to/culler-wheels --out-dir /path/to/culler-runtime
# Optional CPU model libraries, without downloading weights:
python scripts/prepare_cli_culler_runtime.py --models \
  --wheelhouse /path/to/culler-wheels --out-dir /path/to/culler-runtime-models
```

Choose separate output directories for core/model builds. The generated
`core.lock` records exact resolved versions and hashes, `provenance.json`
records the source pins and wheel hashes, and `runtime.json` selects the
prepared interpreter and worker closure. These development virtualenvs use
absolute paths; they are not relocatable Windows installer artifacts.

Set `IMAGE_TRIAGE_CLI_CULLER_RUNTIME` to the prepared `runtime.json`, or place
it at the managed AI root's `rt/cli-culler/runtime.json`. The rollout stays
opt-in: set `ai/use_cli_culler_native` in the application's `user_settings()`
store to true for native Analyze. Its Culling Preferences action then opens
native JSON settings; `ai/cli_culler_settings_json` stores the exact profile.
An unset profile uses:

```json
{"models": ["faces", "maniqa", "appeal"], "refine_scenes": true}
```

Omitted settings retain engine defaults, including the disabled quality floor,
two burst keepers and one scene keeper. Folder exclusions are explicit native
settings. Missing requested models retain the engine's warning-and-continue
behavior.

The prepared runtime's `ai-culler` entry point also retains `evaluate`,
`learn`, `baseline`, `sample`, `calibrate` and explicit `cull --xmp`.
XMP is never written implicitly by native Analyze; its optional export remains
the engine's writer and overwrite policy.

## Evidence and remaining gates

The original engine suite passed **120 tests** with the CPU model libraries,
including synthetic MANIQA checkpoint loading/scoring. No real weights were
downloaded. The core-only baseline was 117 passed and one optional module
skipped.

Host checks cover direct-engine/worker document parity except timings; bracket
modes, bursts, copied exports, Unicode paths, missing models and cache reuse;
Qt task lifecycle, interruption and descendant reaping; generation validation,
integrity and failed activation; catalog persistence; native UI Analyze/reopen;
manual Winner protection; and search/people adapters.

The combined host command passed **136 tests**, with **5 expected failures**
in existing review tests. Architecture checks retain two
pre-existing failures: this branch exceeds several recorded ratchet limits,
and MainWindow references an undefined `ai_status_chip`. Both were reproduced
against an untouched exported snapshot of this branch. The worker's
internal-import check passes, and the AI controller's window-private accesses
were reduced below its recorded limit. No ratchet limits or assertions were
relaxed.

The prepared plain-file worker was launched from outside the source checkouts
with Git absent from PATH. The real MainWindow completed native Analyze,
reloaded the saved results, and was inspected in an offscreen screenshot.
A Linux frozen application was also built with Python 3.13 and cx_Freeze,
started from /tmp, and browsed a three-image synthetic folder. The worker
shipped inside that build completed native culling with Git absent, and the
frozen application reopened and persisted all three native decisions in its
catalog. This validates the Linux frozen directory build, not an MSI/AppImage
installer or Windows execution.

The existing GPU lock uses `onnxruntime-gpu`, while the untouched engine
declares the `onnxruntime` distribution. A GPU package recipe must reconcile
that dependency contract; installing both over the same import package or
ignoring a failed dependency check is not part of this implementation.

The user explicitly chose to keep the engine unchanged and defer GPU packaging.
Keep both immutable source pins and the engine's dependency declarations intact;
no GPU-related engine metadata change is authorized by this integration.

## Windows portable validation build

`.github/workflows/build-cli-culler-validation.yml` builds on pushes to this
integration branch or manual dispatch. It produces the
`ImageTriage-CLI-Culler-Windows-CPU-Validation` artifact, with a frozen app,
standalone portable CPU runtime, and unchanged pinned engine wheels. It does
not publish a release, upgrade the installed app, bundle PhotoCraft, or enable
GPU packaging.

Extract the whole artifact and launch `Start-ImageTriage-Validation.cmd`.
That launcher enables native Analyze for its process only. The normal app
default and installed application remain unchanged.

After Analyze, inspect results and compare before Apply or editing/moving the
photos. Drag the shoot's
`.image_triage_ai/artifacts/cli_culler/active.json` onto
`Compare-CLI-Culler.cmd`. The validation build snapshots the starting score
database and preview cache before each run. The comparison restores independent
copies, uses the saved settings and same runtime/models, invokes the actual
CLI, and reports every document/index difference except timing. This controls
the known engine cache-history discrepancy without changing the engine.

`AI-Culler.cmd` exposes the matching direct CLI. The package README and
`CLI-Culler-settings.json` document its initial profile. Source validation for
this packaging slice passed 138 tests with 5 existing expected failures; a
relocated Linux package also passed the shipped-worker/direct-CLI smoke test.

Real-shoot and installed-model parity/performance validation is explicitly
deferred by the user because this environment has neither. Windows job
execution, GPU runtime/inference, and a full frozen installer remain unverified.
The normal Analyze default must stay on the existing implementation until those
acceptance gates pass. No silent fallback is used after a native run fails.
