# Direct CLI-Culler integration plan

Prepared November 3, 2026. Planning and source inspection only; no application or engine implementation has been changed or tested by this work.
Working branch: `codex/cli-culler-integration`, in its own Git worktree. The original ImageTriage checkout stays on `main` for the user's parallel work. The user requested planning and pushing this document only; implementation and builds are deferred.

The user is fixing PhotoCraft integration in another session and explicitly instructed this session to leave PhotoCraft untouched. That instruction overrides the routine companion-check requirements for subsequent culler work: do not fetch, merge, edit, build, promote or push PhotoCraft from this session. Keep culler changes isolated on this branch.

The integration will run the existing CLI-Culler engine unchanged and make ImageTriage its host. ImageTriage will supply folders, settings, model locations and progress handling, then display and persist the engine's decisions. It will not run its own culling rules over those decisions.

The user selected the repository's `my_settings.json` as the initial configuration:

```json
{
  "models": ["faces", "maniqa", "appeal"],
  "refine_scenes": true
}
```

All omitted settings retain `CullSettings` defaults. In particular, the quality floor remains off, `bursts_keep` remains 2, and `scene_keep` remains 1. The calibrated Q-ReAlign floor described in the README is an available configuration, not a setting to enable implicitly.

1. **Pin and package the existing engine without modifying it.**

   The inspected CLI-Culler commit is [`f2e34b1b57f889f59b336619887d71d4bb701118`](https://github.com/tylermcm/CLI-Culler/tree/f2e34b1b57f889f59b336619887d71d4bb701118), package `ai-culler`, version `0.3.0.dev1`. Its dependency reference `bracketeering@ccr-fd1f1f35-hi49ff` currently resolves to [`a494be9a92f85bed7f987359139d12a5a83f718e`](https://github.com/tylermcm/bracketeering/tree/a494be9a92f85bed7f987359139d12a5a83f718e). Record both immutable revisions in ImageTriage's packaging inputs and build unchanged wheels from them. Resolve the engine's dependency to that commit in the packaging lock; do not edit its `pyproject.toml` to achieve this.

   Keep the engine's grouping, preview extraction, gates, scoring, model loading, routing, ranking, quality floor, scene refinement, preference learning, people coverage, output writers and defaults intact. No algorithm transplant, monkey patch, model substitution, threshold tuning or pipeline reordering belongs in this integration.

   The current application bundles a different, legacy `aiculler/` package. Both versions use the same Python import name, and their `storage` APIs are incompatible. Installing the new distribution into the application interpreter or replacing that directory would break existing telemetry, search, face indexing and training imports. Keep them in separate process import environments. Do not add the new engine to the main app's ordinary dependency list where it could overwrite the bundled package.

2. **Add a small ImageTriage worker that calls the public Python API.**

   Proposed new modules: `image_triage/cli_culler_worker.py` for the headless bridge and `image_triage/cli_culler_task.py` for Qt task/process management. The bridge's actual culling call is:

   ```python
   settings = CullSettings.from_dict(request["settings"])
   models = ModelRegistry.from_app_manifest(app_path, device=device)
   result = cull(
       folders,
       settings,
       models=models,
       cache_db=engine_cache,
       cache_dir=preview_cache,
       on_progress=report_progress,
       workers=workers,
   )
   write_decisions(result, decisions_path)
   if result.index:
       write_index(result, index_path)
   ```

   Use the existing managed runtime/bootstrap and model installation infrastructure where compatible. Ship a separate plain-file worker import closure containing the necessary `image_triage` manifest/model-store modules and the pinned engine. Exclude the host's legacy `aiculler/` from that closure. Validate source launches and frozen launches from a directory outside the checkout; assert the loaded `aiculler` and `bracketeering` paths and revisions. A source checkout accidentally making a missing package available is not a packaging pass.

   Add the engine and its complete dependency closure to controlled runtime packaging. Keep a working model-free path; torch and the model weights remain optional capabilities. Reuse current CPU/GPU locks where they satisfy the engine, and regenerate only affected locks. Model files continue to come from ImageTriage's existing manifest and Set Up AI. Do not introduce another downloader or another model manifest.

   Send structured progress events to the parent process and retain stderr for diagnostics. Translate the engine's `(stage, done, total)` callback to the existing task signals. Show indeterminate activity during stages that expose no granular callback; do not invent completed work or keep the cascade's fixed four-stage accounting.

   `cull()` has no cancellation parameter. Cancellation must stop and reap this worker and its process tree, including metadata subprocesses. Do not add cancellation checks inside the engine or rely on raising an exception from a progress callback: model exceptions can be caught as a skipped-model warning. Discard the cancelled output generation while retaining valid committed score-cache entries.

3. **Store and load the native results losslessly.**

   Proposed new module: `image_triage/cli_culler_results.py`, with a small dispatch extension in `ai_results.py` for `aiculler-decisions/1`. Read native JSON directly rather than making the old ranked CSV the authoritative format.

   Preserve the complete document and every per-file field: `decision`, `capture`, `primary`, `group`, `kind`, `role`, `scene`, `comparison_set`, nullable rank/set size/score, individual `scores`, `reasons`, `gates`, `floor`, `people`, `categories`, `techniques` and raw model `outputs`. Preserve document settings, engine and bracketeering versions, used models, warnings, timings and stats. Unknown extra fields should survive persistence; unsupported schema versions should give a clear load error.

   Keep engine capture identity, physical group, scene and comparison set distinct. A bracket or burst can compete as a unit in a scene; flattening these into one legacy group would lose meaning. Keep null scores null, and display scores within their comparison set. The engine's combined scores are not folder-wide quality percentages.

   Add an explicit native-decision path to the app's result model and bucket classifier:

   | Engine decision | App AI bucket |
   | --- | --- |
   | `keep` | Keeper |
   | `reject` | Reject |
   | `review` | Needs Review |

   The engine does not emit an `AI Pick` verdict. Do not turn every keep or rank-1 frame into an automatic Winner. Show rank and representative information as details without changing the verdict.

   The catalog currently serializes a fixed list of legacy result fields. Extend `catalog/schema.py` and `catalog/repository.py` with a backwards-compatible native payload/provider representation, or an equivalent lossless cache representation. A restart or catalog-cache hit must retain the same native decisions and explanations. Do not funnel native results through percentile enrichment when reconstructing them.

   Use a separate score-cache filename and preview directory under the existing `.image_triage_ai` artifact root. Never open the old feature database as a new-engine `ScoreCache`. Keep the engine's capture/scorer/revision cache keys unchanged. Write decisions and the optional index into a per-run staging generation; activate a run only after its expected outputs validate, so an interrupted export cannot pair old decisions with a new index. Runtime/engine changes should select a validated cache namespace without rewriting the cache algorithm.

4. **Connect Analyze and the existing review UI without a second decision engine.**

   Change the Analyze dispatch in `ai_run_controller.py` to use the new task. Reuse current progress, completion, failure and cancellation UI. Retain old saved-result loading and existing non-culling functionality. Keep legacy implementations reachable only through an explicit compatibility path if needed; do not silently fall back to another culling engine after a new-engine failure.

   For native results, bypass these existing app-side verdict changes:

   - folder-percentile confidence classification in `ai_results.py`;
   - `refine_ai_result_with_review_insight` low-detail verdict changes;
   - `recompute_ai_demoted_burst_paths` / `apply_burst_dedup_to_ai_result`, which currently reject every group member except one;
   - old pHash pruning, old bracket detection, old include-list preparation, old score blending and legacy reranking as part of the new run.

   These guards apply only to native engine results. They are necessary integration changes; unrelated legacy behavior stays intact. In particular, the app must not reduce an engine-approved two-frame burst to one, reject protected bracket members, or reinterpret a quality-floor rescue.

   Pass the requested folder roots directly to the engine. Let bracketeering discover and group RAW/JPEG siblings, copies and exports. The API accepts a folder or list of folders, not an arbitrary selected-file include list. Analyze uses folder/shoot scope; it must not silently cull only visible grid representatives or imply selection-only support. Expose the engine's existing `exclude` setting explicitly. If generated application directories need excluding, record that as an actual run setting rather than silently filtering the shoot.

   Persist the exact selected settings JSON and submit it through `CullSettings.from_dict`; use its validation. Existing guided preferences and Quick Rerank must not translate the old algorithm's knobs into unrelated new settings. Native reruns call the same `cull()` with changed native settings and reuse the engine cache. Provide access to all native settings, including advanced gates, weights, baseline and preference-model paths, without changing their meaning.

   Distinguish the engine's raw AI decision from a human's existing or subsequent annotation. Keep manual ratings, Keep/Winner/Reject marks, undo and source-file handling in the host's existing annotation layer. Never rewrite the native document to make it agree with a manual override. A marked Winner must not be moved to Recycle merely because the raw AI decision is Reject. Existing explicit Apply actions remain the boundary for file moves and annotations; running Analyze only produces analysis artifacts.

   Surface warnings and the models actually used. With the selected profile, missing requested models retain the engine's warning-and-continue behavior; the app must make that outcome visible. Do not silently remove requested features based on the old cascade's CPU/GPU heuristics.

5. **Connect indexes and exports without recomputing engine decisions.**

   Load emitted `aiculler-index/1` arrays into existing search/people storage through an adapter. Map every capture to all of its file paths. Preserve model provenance, normalized embeddings and face boxes in upright image coordinates. Engine person numbers are run-local; map them to the host's named-person records without treating them as permanent library IDs or changing people-coverage decisions.

   The selected profile does not explicitly request `search` or `people`. However, `refine_scenes: true` requires TinyCLIP, and the inspected implementation builds a search index whenever TinyCLIP successfully ran. Consume that emitted index without adding feature flags. Do not promise new AdaFace identities from `faces` alone. Keep existing background search/people features working where no compatible engine index was produced; do not let them reclassify AI decisions.

   Preserve optional XMP export through the engine's existing `write_xmp` behavior and overwrite policy. Do not reproduce its writer in ImageTriage or write sidecars on Analyze implicitly. Preserve access to engine CLI functions (`evaluate`, `learn`, `baseline`, `sample`, `calibrate` and the review tools) from the pinned runtime; adding new GUI workflows for each is outside the initial connector. The selected settings do not enable a preference model automatically.

6. **Prove parity before making the new path the normal Analyze implementation.**

   First establish a runnable baseline for the untouched pinned engine and its dependencies. Run its original tests in the isolated engine environment, with meaningful model-dependent tests identified separately from skipped tests. Existing tests already cover synthetic shoots, sibling/copy propagation, unchanged score-cache reuse and model warnings; those are useful oracles rather than behaviors to reimplement.

   Add focused host integration tests and a direct-engine-versus-app comparison over the same shoot, exact settings, models and equivalent starting caches. Compare every file's verdict, grouping/capture identity, ranking, reasons, gates, floor, categories, techniques and model provenance, plus index arrays. Timings can differ; verdict or explanation differences are failures. Use copied, identically warmed cache states for model comparisons: `docs/DEFERRED.md` records a small TinyCLIP cache-history discrepancy, which must not be confused with an adapter regression or silently fixed in this task.

   Required cases include bracket keep-set/pick-one/ignore, moving brackets, multiple burst keepers, scene competition, lone frames, focus stacks/timelapses, RAW+JPEG and copied exports, unreadable files, Windows path case/Unicode, missing models, quality-floor review/rescue, scene refinement, optional people coverage and manual annotations. Exercise nondefault features with appropriate explicit settings even though they are off in the chosen profile.

   Verify catalog reload and reopen, grid badges and filters, group navigation, explanations, optional exports, existing manual Apply/Undo behavior, process cleanup, cancellation during metadata and inference, restart after interruption and cache reuse. Test a packaged app with only shipped worker files, from outside the checkout, with no Git installation needed at runtime. Run the relevant existing app regressions and inspect the existing UI visually after wiring the new result path.

   Start with a small representative real shoot, inspect results, then measure a larger one. Record engine time separately from host loading/index import overhead. Do not tune the engine to improve those measurements within this integration.

   Acceptance is unchanged engine source plus matching native results through Analyze, persistence and review. If integration appears to require an engine behavior change, stop that change and identify the exact incompatibility separately.

**Expected application edit boundary**

| Area | Necessary host work |
| --- | --- |
| New bridge/task/result adapter modules | API call, worker protocol, cancellation and native-schema loading |
| `ai_run_controller.py`, `ai_results.py` | Analyze dispatch, direct buckets and bypass of legacy postprocessing |
| `catalog/schema.py`, `catalog/repository.py` | Lossless native-result persistence |
| `ai_workflow.py` / runtime path helpers | New engine artifacts and cache isolation |
| `settings_controller.py`, existing culling settings UI | Exact profile persistence and native setting access |
| `ai_manifest.py`, runtime locks/bootstrap, `freeze_support.py` | Engine packaging/readiness and isolated worker import closure |
| Existing semantic/people integration points | Import compatible engine indexes while preserving existing features |
| Focused tests | Engine parity, host persistence, process handling and shipped-worker verification |

The exact file count should follow implementation needs. PhotoCraft, Camera Raw, unrelated UI layouts, editing tools, collections and other application features have no role in this change.

**Historical PhotoCraft snapshot from the earlier planning pass**

The following check was completed before the user instructed this session to leave PhotoCraft alone. It is historical context, not an action list for this branch. Both network fetches succeeded using explicit upstream/fork URLs. This checkout's actual `origin` points to the user fork and there is no `fork` remote, so no remote configuration was changed. Its current branch is `scratch/upstream-merge-2026-10-25`, HEAD `d18743cd9`; the escalated status check was clean. No checkout, merge, build promotion or push occurred.

- Storytold upstream main fetched at `37a01ca8746c121170644a07968d11263ef64407`; latest fetched release tag is `v0.20.1` and is an ancestor of that tip.
- Local `codex/image-triage-integration` is `0988078e4f9f8735bb99587a5f6e1d67a4b511e4`: 79 commits unique locally, 5,988 upstream commits absent. It is 3,083 commits behind the fetched fork integration branch, with no commits unique against that branch.
- Fork integration is `ef1a2fd6af5a399b13f34636753f383ecae8856c`: it includes the upstream v0.16.0 merge and a hosted Camera Raw fix. Current scratch HEAD is one commit behind it. Against fetched upstream main, the fork has 82 unique commits and lacks 2,908 upstream commits.
- Merge simulations against both the local integration branch and current HEAD reported conflicts. Current HEAD's simulation conflicts in 41 files, including deleted/reworked Camera Raw and GPU canvas files. This is a large release integration, separate from this culling plan; no conflict resolutions were attempted. Any PhotoCraft follow-up belongs to the user's other session.

Current HEAD's conflicted files:

```text
crates/engine/src/cmd/lens_cmds.rs
crates/io/src/camera_raw_map.rs
crates/io/src/camera_raw_xmp.rs
crates/io/src/flat.rs
crates/io/src/lib.rs
crates/raw/src/lib.rs
crates/ui-egui/Cargo.toml
crates/ui-egui/src/camera_raw_ui.rs
crates/ui-egui/src/control.rs
crates/ui-egui/src/dialogs/camera_raw_preview_ui.rs
crates/ui-egui/src/dialogs/camera_raw_scope_ui.rs
crates/ui-egui/src/gpu_canvas.rs
crates/ui-egui/src/i18n/cs.tsv
crates/ui-egui/src/i18n/de.tsv
crates/ui-egui/src/i18n/el.tsv
crates/ui-egui/src/i18n/es.tsv
crates/ui-egui/src/i18n/fr.tsv
crates/ui-egui/src/i18n/hu.tsv
crates/ui-egui/src/i18n/id.tsv
crates/ui-egui/src/i18n/it.tsv
crates/ui-egui/src/i18n/ja.tsv
crates/ui-egui/src/i18n/ko.tsv
crates/ui-egui/src/i18n/nl.tsv
crates/ui-egui/src/i18n/pl.tsv
crates/ui-egui/src/i18n/pt-br.tsv
crates/ui-egui/src/i18n/ru.tsv
crates/ui-egui/src/i18n/sr.tsv
crates/ui-egui/src/i18n/tr.tsv
crates/ui-egui/src/i18n/uk.tsv
crates/ui-egui/src/i18n/vi.tsv
crates/ui-egui/src/i18n/zh-hans.tsv
crates/ui-egui/src/i18n/zh-hant.tsv
crates/ui-egui/src/jobs_ui.rs
crates/ui-egui/src/lib.rs
crates/ui-egui/src/menus.rs
crates/ui-egui/src/state.rs
crates/ui-egui/src/theme.rs
crates/ui-egui/src/widgets.rs
crates/ui-egui/tests/it/camera_raw_histogram.rs
crates/ui-egui/tests/it/camera_raw_localization.rs
docs/control-protocol.md
```

Existing untracked mockups, reports and research notes in ImageTriage were left untouched. No tests were run for this planning-only change.
