# MainWindow Decomposition Plan (A1, reopened)

Written 2026-10-03. This is a plan only; no code was changed to produce it. It replaces the A1 checkbox in `docs/audit_and_remediation_plan.md` (WI-4.4), which the post-remediation audit (`docs/post_remediation_audit.md`, A-01) found was closed on a claim the code does not support.

All numbers are measured from the code on branch `codex/ui-ux-polish` at 89c6cd9 using AST scripts (listed in the appendix). Where a number is an estimate, it says so.

---

## 0. Summary

`MainWindow` is 19,133 lines, 976 methods and 526 instance attributes. WI-4.4 removed about 6,800 lines from it, but it did so by moving code into six controllers that each hold a back-reference to the window. That reduced line count and did not reduce coupling: the controllers still make about 1,350 private-attribute accesses into the window, and 352 of the window's 526 state attributes are still owned by it alone.

This plan fixes the cause rather than repeating the move. The core change in approach is **state first**: define who owns each piece of state, give it to that owner, then move the behaviour that uses it, and give the owner a small public surface. A controller that still reads `window._something` is not finished, no matter where its file lives.

The plan has six phases:

| Phase | What | Size | Needs from you |
|---|---|---|---|
| P0 | Guardrails: fix the 4 known crashes, add an undefined-name gate, add an action sweep test, add an architecture ratchet | S–M | nothing |
| P1 | Subtract: delete retired AI-mode scaffolding, dead state and dead methods | M | **Decision D1** |
| P2 | Unpile: move 1,460 lines of non-window classes out of `window.py`, retire the 137 forwarding delegates | M | nothing |
| P3 | Shared state objects: `AppPreferences`, `FolderSession`, `ReviewModeState`, plus ports for the grid and status bar | M | **Decision D2** |
| P4 | Domain slices (AI setup, AI run, adapter review, export jobs, chrome, navigation, preview/inspector) | XL, 7 slices | order is the recommendation, you can reorder |
| P5 | Retrofit the six existing controllers so none touches the window's privates; `RecordsViewController` last | L–XL | nothing |

End state, with the numbers the ratchet will enforce: `MainWindow` at or under 4,000 lines, 300 methods and 200 attributes; no non-window class left in `window.py`; zero private-attribute accesses into the window from any other module.

Each phase leaves the app working and shippable. You can stop after any phase; section 9 shows what each stop point buys.

---

## 1. Evidence: why A1 is not closed

| Measure | Original audit | Now | Comment |
|---|---|---|---|
| `window.py` lines | 28,467 | 21,191 | −7,276 |
| `MainWindow` lines | 25,900 | 19,133 | −6,767 |
| `MainWindow` methods | 1,138 | 976 | −162 |
| `MainWindow` instance attributes | 558 | 526 | −32 |
| `__init__` | 1,301 lines | split into 28 `_init_*` methods, 1,275 lines | a verbatim split; `__init__` itself is now short, the body did not shrink |
| Classes still in `window.py` besides `MainWindow` | not counted | 38, about 1,460 lines | `QRunnable` tasks, signal holders, dialogs, a controller |
| Pure forwarding delegates | not counted | 137 methods | `def _x(self, ...): return self._ctrl.x(...)`; 322 methods are two statements or fewer |
| `.connect(...)` calls inside `MainWindow` | not counted | 279, with 145 `_handle_*`/`_on_*` slots | |
| Qt widget / QTimer / QThreadPool attributes | not counted | 109 widgets, 20 timers, 17 thread pools | |

Coupling of the extracted code back into the window:

| Module | Lines | Private accesses into window | Distinct attributes | Window **state** (not methods) it touches |
|---|---|---|---|---|
| `records_view_controller` | 2,365 | 826 | 202 | 132 |
| `record_ops_controller` | 965 | 197 | 43 | 12 |
| `catalog_controller` | 567 | 114 | 36 | 25 |
| `ui/actions.py` | — | 97 | 91 | 0 |
| `command_palette_controller` | 717 | 70 | 41 | 23 |
| `folder_ops_controller` | 272 | 47 | 15 | 4 |

Who owns the state:

- 352 of the 526 window state attributes are touched by no extracted controller. They live on the window and only on the window.
- Of the 132 state attributes `records_view` uses, 56 could move into it with no other consumer, 61 are also used by other window methods, and 15 are also used by another controller.
- 98 window attributes are assigned from controller code and from `MainWindow` (246 assignments in `records_view_controller` alone). That is two writers on the same state.
- 35 attributes are assigned in `_init_*` and never touched again (dead state), for example `ai_path_combo`, `_toolbar_style`, `_zen_toggle_shortcut`, `adapter_review_banner`.
- Eight methods are transitively unreachable (41 lines); `_register_child_process` is one of them.
- 18 attributes are written from three or more different areas of the code. The worst offenders are preferences mirrored onto the window (`_burst_groups_enabled`, `_burst_stacks_enabled`, `_compare_enabled`, `_auto_advance_enabled`, `_show_hidden_folders`, `_phash_prefilter_settings`) and review-scoring cache state.
- Hub attributes (touched by the most methods): `statusBar` 146, `grid` 106, `_current_folder` 87, `_settings` 73, `_records_view` 52, `_record_ops` 32, `_annotations` 31, `_ai_bundle` 31, `_records` 30, `actions` 28, `_active_ai_task` 22. `QSettings` is read directly by 73 methods.

The one good structural fact: **the extracted controllers barely share state with each other.** The largest overlap between any two is 7 attributes (`records_view` × `catalog`). So the slices are independent of one another, and the hard part is only the state each shares with the window.

---

## 2. What the first attempt got wrong

These are the process failures, not the people. Each one is addressed by a rule below.

1. **Code moved, state did not.** The controllers were defined as "holds only the window back-reference, no state of its own" (the plan's own words for `CommandPaletteController`). That is the definition of a code move. Rule: a slice is not done until its state lives in its owner.
2. **The done-criterion could not see coupling.** "Reachability clean + suite green" passes whether the controller has 0 or 826 reaches into the window. Rule: coupling is a number, it is ratcheted, and CI fails when it goes up.
3. **Forwarders were left behind "until callers migrate", and callers never migrated.** 137 of them. Rule: when a slice finishes, its delegates are deleted in the same change and callers are updated mechanically.
4. **Moves were done by hand-splicing line ranges.** That lost `_cancel_hidden_ai_results_load` once, and left `_start_catalog_refresh`, `_current_path_for_index` and `semantic_missing` dangling (audit F-01 to F-03). Rule: an undefined-name gate runs on every slice, and a script verifies every `self.<name>` in the window resolves.
5. **Tests are bound to stubs.** 18 test files call `MainWindow._x` unbound on hand-built stubs; only 10 drive a real window. Every slice breaks stubs, which produced 5 xfails. Rule: tests of moved logic move with it and exercise the extracted object directly; this is the main payoff of extraction.
6. **The riskiest thing was left implicit.** The plan said "last slice deferred" and then closed it anyway on the same pattern. Rule: the records-view/scan slice has its own sub-plan (P5) and is not attempted until the state objects exist.

---

## 3. Target architecture and the rules that define "done"

### 3.1 Shape

```
MainWindow  (composition root + Qt shell)
   |  builds, wires, owns widget layout, Qt event overrides, closeEvent
   v
Controllers / coordinators   (UI-aware: they may touch widgets through ports)
   |  hold explicit collaborators, never the window
   v
Services and state objects   (UI-free: QObject for signals/timers/pools, no QtWidgets imports)
   |
   v
Existing stores: decision_store, catalog, library_store, xmp, ai_* modules
```

`MainWindow` keeps: building the widget tree, assembling controllers and wiring signals, Qt overrides (`resizeEvent`, `closeEvent`, `showEvent`, `eventFilter`, `changeEvent`), and the handful of methods that are genuinely about the window itself (zen mode, geometry/state restore, dock layout). Everything else leaves.

### 3.2 Rules (enforced in CI by P0.4)

- **R1. No private access across the boundary.** No module other than `window.py` reads or writes `<window>._name`, where `<window>` is `window`, `host`, `self._window`. This replaces today's 1,350 accesses; the allowlist is a checked-in file that can only shrink.
- **R2. One writer per attribute.** State belongs to one owner; everyone else reads it through a property or a signal and requests changes through a method.
- **R3. Services are UI-free.** A module declared a service may not import `PySide6.QtWidgets`. `QObject`, `QTimer`, `QThreadPool` and signals are fine. This keeps services testable without a window and is also what any future front end (the shelved WinUI work) needs.
- **R4. Controllers take collaborators, not the window.** Constructor arguments are the owner objects and small ports. No `window` parameter anywhere.
- **R5. Ports instead of widget access.** The two hubs that are really just widgets get a narrow interface: `GridPort` (106 methods touch `grid`) and `StatusPort` (146 methods touch `statusBar`). A controller that needs the grid asks the port, so grid internals can change without touching 106 methods.
- **R6. No forwarding delegates.** When a slice finishes, its delegates are deleted and callers (including `ui/actions.py` and tests) call the owner.
- **R7. Cross-owner communication is a signal or a method on the public surface**, never a shared mutable attribute.
- **R8. Ratchet.** MainWindow lines, methods, attributes, private accesses, and non-window classes in `window.py` are recorded in a JSON file. A change may lower a number and may not raise one.

### 3.3 The shared state objects (P3)

These exist because the hubs and the 18 multi-writer attributes are the places where slices collide. Creating them first makes every later slice cleaner.

| Object | Owns | Replaces |
|---|---|---|
| `AppPreferences` | typed, observable view of the QSettings keys the app reads: burst groups/stacks enabled, compare, auto-advance, show hidden folders, pHash settings, AI thresholds, display options; emits `changed(key)` | `QSettings` reads in 73 methods and the preferences mirrored as window attributes |
| `FolderSession` | current folder, scope kind, scope id, collection mode, session id, browser view mode; emits `scope_changed` | `_current_folder` (87 methods), `_scope_kind`, `_scope_id`, `_collection_mode`, `_session_id`, `_browser_view_mode` |
| `ReviewModeState` | compare enabled/count, manual compare count, review scoring cache (detail/source), the burst-related flags | the review/compare attributes written from 3–5 areas |
| `GridPort`, `StatusPort` | thin interface over the grid widget and status bar | direct `self.grid.x` (106 methods) and `self.statusBar().x` (146) |

---

## 4. The measured map: what is in the window and where it goes

Methods were clustered by call edges plus shared (non-hub) attributes (Louvain communities). "Exclusive" is the share of state a cluster touches that no other cluster touches; low exclusivity marks a hub and predicts a hard slice.

| Cluster | Methods | Lines | Attrs touched | Exclusive | What it is | Target owner |
|---|---|---|---|---|---|---|
| C01 | 57 | 1,668 | 53 | 11% | AI run pipeline, results load, culling apply, toolbar state | `AiRunService` + `AiProgressState` |
| C02 | 61 | 1,654 | 62 | 58% | adapter review, dispute labels, review banner | `AdapterReviewController` (or deleted, see D1) |
| C08 | 45 | 995 | 20 | 20% | AI setup dialog, runtime/model install and uninstall | `AiSetupService` |
| C12 | 55 | 797 | 39 | 41% | archive, convert, resize, workflow export, PocketDrop send | `ExportJobsService` |
| C03 | 83 | 1,650 | 92 | 59% | top bar, fluent icons, workspace toolbar specs | `ChromeController` (builders) |
| C04 | 75 | 1,642 | 90 | 44% | chrome sizing, top bar rebuild, toolbar placement, pane ratios | `ChromeController` |
| C13 | 28 | 439 | 59 | 30% | zen mode, menu bar, startup window-state | stays in `MainWindow` shell |
| C09 | 60 | 899 | 66 | 56% | left rail, folder tree, drives, favorites, recents | `NavigationController` |
| C14 | 31 | 349 | 38 | 34% | filter metadata indexing, folder watch | `FolderWatchService` |
| C10 | 52 | 890 | 56 | 23% | inspector, details view, toolbar placement | `InspectorController` |
| C11 | 62 | 825 | 46 | 26% | preview open/configure, compare, quick view | `PreviewHost` |
| C00 | 98 | 1,792 | 182 | 30% | settings dialog + apply, action state, filters, view preferences | `SettingsCoordinator` over `AppPreferences` |
| C05 | 74 | 1,377 | 87 | 33% | annotations, winner/reject toggles, status, filter presets, palette state | review/annotation controller |
| C06 | 70 | 1,151 | 33 | 45% | context menus, collection mode, help/guide, tool modes | spread across chrome, collections, help |
| C07 | 68 | 1,134 | 144 | 36% | review intelligence, records controller init, workflow insights | `ReviewIntelligenceService` + records |
| C15 | 19 | 69 | 17 | 29% | thread-pool and controller construction | stays; becomes the composition root |

Reading the table: C08 (AI setup) is the cheapest large cluster, 995 lines touching only 20 attributes. C01 is the AI hub: `_ai_bundle` is touched by 31 methods and the progress fields (`_ai_stage_message`, `_ai_progress_current/total`, `_ai_progress_eta_text`) are written by 11–13 methods each, which is exactly what a single `AiProgressState` removes. C00, C05, C07 are the records/review/settings core and carry the highest hub load; they go last.

By domain, roughly (name-token classification, so approximate): AI/ML 4,980 lines in 213 methods; scan/records/view 2,274; toolbar/menus/actions/palette 1,567; review/burst/bracket/duplicate 1,549; catalog/library 1,150; folder tree/navigation 807; file ops/export 682; settings/theme/state 591; the remainder is grid, update/help, preview, people, jobs.

---

## 5. Phases and work items

Size key: S ≈ half a day, M ≈ 1–2 days, L ≈ 3–5 days, XL ≈ a week or more of focused sessions. These are order-of-magnitude, not commitments.

### P0. Guardrails (do first; nothing else is safe without them)

**DC-0.1 Fix the four reachable crashes** from the audit (F-01 `semantic_missing`, F-02 `_current_path_for_index` ×2, F-03 `_start_catalog_refresh`, F-04 `_auto_bracket_enabled`). *S.* Each gets a regression test (the three reproductions already exist in scratch).

*Status 2026-10-03: done (uncommitted).* The four crashes are fixed with four regression tests in `tests/test_post_audit_regressions.py`, each mutation-checked against the old source. The sandbox part (audit I-02) was attempted and reverted because it exposed an unrelated width defect that only shows when no AI tools are installed (audit N-01/N-02); it needs a decision and stays open. Full suite after the fixes: 1,953 passed, 0 failures once the redirect was reverted.

**DC-0.2 Undefined-name CI gate.** Add pyflakes (or ruff F821/F822/F841) to CI and fail on undefined names and undefined `self.` members resolvable by the existing scripts. *S.* This is the single most valuable item in the plan: it makes every move in P1–P5 checkable in seconds.

**DC-0.3 Action sweep test.** One real-window test that walks every `QAction` (menu bar, toolbar, command palette entries, context menus reachable offscreen) and triggers it with dialogs guarded and `exec` patched to return Accepted/Rejected in two passes, failing on any exception. Add a second sweep that calls every `_handle_*`/`_on_*` slot with `None`/empty arguments where the signature allows. *M.* This is the characterization net that the first attempt lacked: today only 10 test files build a real window. Expect it to find more latent crashes at first; those are fixed or recorded, not hidden.

**DC-0.4 Architecture report and ratchet.** Turn the measurement scripts in the appendix into `scripts/architecture_report.py` plus `tests/test_architecture_rules.py`. It records the R8 numbers in `docs/architecture_ratchet.json` and enforces R1 and R3 with allowlists that shrink. *M.* Requires your OK to add a script and a test; no product code changes.

**DC-0.5 Baselines.** Record cold-start time of `MainWindow` construction, widget count after construction, and peak memory, so decomposition cannot silently slow startup. *S.*

**DC-0.6 Correct the record.** Replace the "closed in full" statement on A1 in `docs/audit_and_remediation_plan.md` with a dated note pointing here. *S.*

*Exit:* gates are green on the current tree; a deliberately broken reference fails CI.

### P1. Subtract

Deleting is the cheapest line reduction and it removes code that every later slice would otherwise have to carry.

**DC-1.1 Decide and finish AI-mode retirement (D1).** `_ui_mode` is only ever `'manual'`. Fifteen methods (833 lines) still branch on it, including `_handle_mode_tab_changed` (131), `_update_ai_toolbar_state` (205), `_apply_ai_review_burst_lockout`, plus `ai_path_combo`/`ai_path_control`, `AI_RANK`, the hidden `mode_tabs` state holder, per-mode toolbar layouts, and workflow presets with `ui_mode="ai"`. Delete per the choices in `docs/ai_mode_retirement.md`. *M–L.* This also resolves the audit's D-04.

**DC-1.2 Remove dead state.** The 35 attributes assigned and never read, and the widgets that go with them (verified by the same script, re-run at the time). *S.*

**DC-1.3 Remove unreachable methods.** The eight methods the reachability pass found, including the dead child-process subsystem (`_register_child_process`, `_child_processes`, its pruning timer, `_shutdown_child_processes`, the child-sync state file). *S.* This falls under the standing dead-code allowance because each is verified unreferenced.

**DC-1.4 Remove the retired-mode plumbing** that only exists to carry it: `switch_to_ai_tab` parameters, `WORKSPACE_TOOLBAR_DEFAULTS["ai"]`, the per-mode toolbar layouts. Include a settings-migration note so saved layouts keep loading. *S–M.*

*Exit:* gates green; ratchet lowered; `MainWindow` expected around 17,500 lines (estimate).

### P2. Unpile

**DC-2.1 Move the 38 non-window classes out of `window.py`.** About 1,460 lines. Targets: the `QRunnable`/`Signals` pairs to `image_triage/tasks/` (one module per domain: annotation hydration, scope enrichment, AI model/runtime install, update check, folder probe, prefilter decisions, suggestions), `AIReviewCompleteDialog` and the AI tag sample widgets to `ui/`, `_DirectorySuggestionController` to `ui/` or `navigation`, the `*ExecutionContext` dataclasses beside the export service, `AISetupSelection` beside the AI setup service. Pure moves; the gate (DC-0.2) and the existing tests verify them. *M.*

**DC-2.2 Retire the 137 forwarding delegates.** A script finds every delegate, rewrites callers (`self._x(...)` → `self._ctrl.x(...)`, and the `ui/actions.py`/palette/test callers) and deletes the delegate. *M.* Done in batches per controller with the sweep test after each.

*Exit:* `window.py` holds only `MainWindow` and module constants; delegates gone.

### P3. Shared state objects

**DC-3.1 `AppPreferences`.** Typed accessors over the keys the window reads today; one owner for `_settings` reads; `changed(key)` signal; the 18 multi-writer preference mirrors become reads from it. *M.* **Decision D2** (QObject vs plain object) applies here and to every service below.

**DC-3.2 `FolderSession`.** Owns `_current_folder`, scope kind/id, collection mode, session id, browser view mode. The 87 methods that read `_current_folder` read `session.folder`. *M.* Scope changes emit one signal instead of N methods being called in the right order.

**DC-3.3 `ReviewModeState`.** The compare/review-scoring attributes and burst flags. *S–M.*

**DC-3.4 `GridPort` and `StatusPort`.** Narrow interfaces; migrate the 106 and 146 call sites in mechanical batches. *M.* Keeps the grid widget replaceable and removes the largest read-only hubs.

*Exit:* the hubs `_current_folder`, `_settings`, `grid`, `statusBar` no longer appear in the private-access allowlist; ratchet lowered.

### P4. Domain slices (state first, in this order)

Order is by independence and by how much each slice teaches the next. Slices 1 and 2 are the AI core and establish the pattern on code with clear boundaries.

**DC-4.1 AI setup and runtime (C08).** 995 lines, 20 attributes. `AiSetupService` owns runtime status cache, install/uninstall/model-download tasks and their state; the setup dialog stays a view. Fixes the F-01 area permanently by giving `_show_ai_setup_dialog` a real contract instead of ten deleted parameters. *L.*

**DC-4.2 AI run and results (C01).** 1,668 lines. `AiRunService` owns `_ai_bundle`, active AI/model/runtime tasks and the folder probe; `AiProgressState` (one dataclass emitted by signal) replaces `_ai_stage_message`, `_ai_progress_current/total`, `_ai_progress_eta_text`, which today are written by 11–13 methods each. The service's `shutdown()` also cancels `_active_ai_task` on close (audit C-01). *XL.* Highest-value slice: the AI domain is 4,980 lines and `_ai_bundle` is a 31-method hub.

**DC-4.3 Adapter review and dispute (C02).** 1,654 lines, the highest exclusivity (58%). If D1 deletes the retired path, most of this slice becomes deletion; whatever remains becomes `AdapterReviewController`. *M–L.*

**DC-4.4 Export jobs (C12).** Archive, convert, resize, workflow export, PocketDrop send: `ExportJobsService` owning the four `_active_*_task` attributes, the contexts and the progress dialogs' state. *L.* The long-lived tasks get cancel-and-drain on exit (audit C-01).

**DC-4.5 Chrome (C03 + C04, and the settings/toolbar parts of C00).** 3,300 lines of top bar, toolbar, icons, sizing, pane ratios. The right shape is **builders**, not a controller with state: pure functions that take specs and return widgets plus a small `ChromeController` for placement and overflow. `_ui_mode` disappears here if D1 was taken. *XL.* This is the largest block of widget code and should not be forced into the service model.

**DC-4.6 Navigation (C09 + C14).** Left rail, folder tree, drives, favorites, recents; folder watch and filter-metadata indexing become `FolderWatchService`. *L.*

**DC-4.7 Preview and inspector (C10 + C11).** `PreviewHost` owns the popout lifecycle (deferred build, quick view, compare entry) and `InspectorController` owns inspector context and details view. Coordinate with `preview.py` (4,867 lines), which is outside this plan. *L.*

*Per-slice exit* is the same checklist (section 6) and a ratchet decrease.

### P5. Retrofit the existing controllers

Same rules, applied to the code that already moved. Smallest first, so the pattern is mechanical by the time the large one comes.

| Order | Controller | Window state it touches | Notes |
|---|---|---|---|
| 1 | `recycle_bin`, `batch_rename` | 3, 4 | trivial; constructor takes its collaborators |
| 2 | `folder_ops` | 4 | 47 accesses; needs `FolderSession` |
| 3 | `record_ops` | 12 | 197 accesses to 43 attributes, mostly methods; needs `RecordsRepository` + session |
| 4 | `command_palette` | 23 | 9 of them preferences; falls out of `AppPreferences` |
| 5 | `catalog` | 25 | 11 can move as-is; 10 shared with other controllers |
| 6 | `ui/actions.py` | 0 state, 91 methods | actions bind to owners, not the window |
| 7 | `records_view` | 132 | **sub-plan below** |

**DC-5.7 `RecordsViewController` sub-plan.** 2,365 lines, 826 accesses to 202 attributes, 246 assignments into window state. It is not one thing. Split by the six sub-areas the previous slice already named, each with its own state object:

1. scan/load (`_load_folder`, scan handlers, applying loaded records) → `FolderLoader` (owns active scan tasks, hydration and enrichment tasks)
2. view pipeline (`_apply_records_view`, chunked render, sort/rank) → `RecordsView` (owns the display list and render chunking)
3. filter and search wiring → `FilterController` (owns filter metadata queue and index; pairs with `FolderWatchService`)
4. selection and current-record state → `SelectionState`
5. annotation hydration/persist coordination → `AnnotationCoordinator` (the existing `annotation_queue` is already sound; keep it)
6. review-intelligence kickoff → `ReviewIntelligenceService`

Of the 132 state attributes involved, 56 move with no other consumer; the 61 shared with window methods are resolved by the `FolderSession`/`AppPreferences` work in P3; the 15 shared with other controllers go through the owners' public surface. *XL.* Do not start it until P3 and DC-5.1 to DC-5.6 are done.

*P5 exit:* the private-access allowlist is empty.

### P6 (closing). The shell

Reduce `_init_*` to composition: `build_services()`, `build_chrome()`, `wire_signals()`. Check the ratchet targets. Delete the transitional allowlists. Write the new section of `docs/image_triage_architecture.md`. *M.*

---

## 6. The per-slice recipe

Every slice in P3–P5 follows the same steps. This is the checklist that replaces "extract and delete the dead delegates".

1. **Characterize.** Run the action sweep (DC-0.3). Add focused tests on the seam you are about to cut, against the real window if needed.
2. **Inventory.** Run the report on the slice's methods: every attribute read/written, each classified *exclusive*, *shared with window*, *shared with another controller*, *hub*. Write it into the slice's section of this document before touching code.
3. **Create the owner.** The state object/service with a typed constructor (no window). Move the attributes and their initialisation from `_init_*`.
4. **Move behaviour.** Move methods mechanically with a script (not by hand-splicing line ranges); keep git history readable by doing the pure move in its own change.
5. **Remove reaches.** Replace each remaining `window._x` access with an owner property/method, a port, or a signal. The count for this slice must reach zero.
6. **Delete delegates and migrate callers**, including `ui/actions.py`, the palette, and tests. No forwarders survive the slice.
7. **Ratchet.** Update `architecture_ratchet.json` downward.
8. **Verify.** Undefined-name gate, action sweep, the slice's tests, the full suite, a startup-time check against the DC-0.5 baseline, and a manual smoke list for the affected area (`docs/manual_smoke_test.md` extended).

Hazards seen last time, and the control for each:

| Hazard | Control |
|---|---|
| A method lost at a splice boundary | scripted move; undefined-name gate; a "method inventory before = after" check per slice |
| Caller left pointing at a moved or renamed method | gate + `self_calls.py`-style resolution run in CI |
| Stub-based tests break | tests for moved logic are rewritten against the extracted object in the same change; the unbound-`MainWindow._x` pattern is retired |
| Two writers on shared state | R2 and the multi-writer report |
| Startup slows because services are built eagerly | baseline from DC-0.5; lazy construction where the old code was lazy |
| Behaviour change hidden inside a move | moves and behaviour changes never share a change |

---

## 7. Metrics and the ratchet

Recorded in `docs/architecture_ratchet.json`, produced by `scripts/architecture_report.py`, enforced by `tests/test_architecture_rules.py`.

| Metric | Now | After P1 (est.) | After P4 (est.) | Final target |
|---|---|---|---|---|
| `MainWindow` lines | 19,133 | ≈17,500 | ≈7,500 | ≤ 4,000 |
| `MainWindow` methods | 976 | ≈850 | ≈400 | ≤ 300 |
| `MainWindow` attributes | 526 | ≈450 | ≈250 | ≤ 200 |
| Non-window classes in `window.py` | 38 | 38 | 0 | 0 |
| Forwarding delegates | 137 | 137 | 0 | 0 |
| Private accesses into the window from other modules | ≈1,350 | ≈1,350 | ≈900 | 0 |
| Attributes with more than one writer | 98 | — | — | 0 |
| Dead state attributes | 35 | 0 | 0 | 0 |
| Window-attribute-touching `QSettings` reads | 73 methods | — | ≈10 | 0 outside `AppPreferences` |

The "after" columns are projections from the cluster sizes, not measurements. They are there so progress can be judged against something; the ratchet only enforces "never worse".

---

## 8. Testing strategy

- **Characterization first.** The action sweep (DC-0.3) and the undefined-name gate (DC-0.2) are prerequisites for any move.
- **Tests follow the code.** Logic that moves to a service gets tests against the service, with no window. That removes the 18 files that bind unbound `MainWindow` methods to stubs and resolves the five stub xfails (`test_topbar_style`, three in `test_window_catalog_cache`, and the startup-fixup one) rather than carrying them.
- **A small real-window layer remains** for wiring: one test per controller that builds the window and checks the signal paths.
- **Sandbox first.** DC-0.1 includes the `USERPROFILE`/managed-AI-root redirect from the audit (I-02), so the new tests cannot touch real user data.
- **Manual smoke** per slice, using the existing `docs/manual_smoke_test.md` extended with the areas touched.

---

## 9. Stop points

You can stop after any phase and have a better codebase. This is what each buys.

| Stop after | You have | `MainWindow` (est.) | Remaining risk |
|---|---|---|---|
| P0 | no new crashes can slip in; four known crashes fixed | 19,100 | structure unchanged |
| P1 | retired AI mode gone; dead state and methods gone | ≈17,500 | still one large class |
| P2 | `window.py` is only `MainWindow`; no forwarders | ≈16,000 | still one large class, but honest |
| P3 | the four hubs have owners; settings and session state are explicit | ≈15,500 | behaviour still in the window |
| P4 | AI, export, chrome, navigation, preview extracted with real ownership | ≈7,500 | records core (largest coupling) still on the old pattern |
| P5 + P6 | A1 genuinely closed | ≈4,000 | — |

My recommendation is to commit to P0 through P3 now (they are the part that changes the economics of everything after), then take P4 slice by slice, because each AI slice is useful on its own and none depends on a later one.

---

## 10. Decisions I need from you

**D1. AI Review mode: finish retiring it, or revive it?** The app has been manual-only since 2026-09-19. The remaining scaffolding is about 830 lines in 15 methods plus the AI-mode toolbar page, `ai_path_combo`, AI_RANK, per-mode toolbar layouts, and `ui_mode="ai"` presets, and it forces `_ui_mode` to stay threaded through the window. *Recommendation: finish retiring it* (P1 deletes it; the AI **run** features that work in manual mode stay). If you want AI Review back as a product feature, say so now, because the right design is then a proper feature slice, not leftover branches. Item-by-item choices from `docs/ai_mode_retirement.md` are needed either way (for example whether AI badges on cards return).

**D2. What are services built on?** Options: (a) `QObject` + signals + `QTimer`/`QThreadPool`, no widgets; (b) plain Python with callbacks. *Recommendation: (a).* The code already runs on Qt signals and pools, queued connections keep cross-thread updates correct, and "UI-free" is enforced by forbidding the `QtWidgets` import rather than by avoiding Qt.

**D3. Appetite.** Commit to P0–P3 now and decide on P4 per slice, or commit to the whole plan? *Recommendation: P0–P3 now.*

**D4. Tooling in the repo.** DC-0.4 adds `scripts/architecture_report.py`, `tests/test_architecture_rules.py` and `docs/architecture_ratchet.json`, and DC-0.2 adds a lint step to CI. These are new tracked files and a CI change; I will not add them without your OK.

---

## 11. Out of scope and interactions

- **The editor.** `PhotoEditorPanel` (7,379 lines) and `photo_terminal/` are being replaced by librawops. Nothing here touches them. The window-to-editor surface is small (the mask/edit domain is 6 window methods, 52 lines); it should be described at P6 so librawops integration has a clean seam.
- **`preview.py` and `grid.py`** are large too (4,867 and 4,775 lines) but are separate classes with their own plans. P3/P4 only introduce ports on them.
- **WinUI migration (shelved).** R3 (UI-free services) is deliberately compatible with it: the services this plan produces are what a future front end would call. No work on the shelved branch is part of this plan.
- **Not doing:** a rewrite, mixins (they add files without reducing coupling), a custom event bus (Qt signals suffice), changing behaviour inside a move, and the best-frame/upscaling work deferred earlier.

---

## 12. Appendix: how the numbers were measured

AST-only scripts (Python 3.9, no Qt import), kept in the session scratchpad until DC-0.4 productises them:

- `arch_metrics.py`: sizes, method and attribute counts, private-access counts per module.
- `mw_domains.py`: construction kinds, name-token domains, hub attributes, Louvain communities (calls plus shared non-hub attributes), multi-writer attributes, signal and slot counts, other classes in `window.py`.
- `mw_reach.py`: transitive method reachability (roots are Qt overrides and anything referenced outside the `MainWindow` body).
- `mw_state_owner.py`: per-cluster exclusive state.
- `mw_ctrl_overlap.py`, `mw_movable_state.py`: per-controller state, pairwise overlap, what could move with no other consumer, dead-state candidates.

Limits of the method: attribute access through `getattr`/`setattr`/strings is invisible to it (one `setattr` pattern was handled conservatively), the Louvain clusters are a heuristic grouping that I have named and merged by reading the method lists, and the "after" columns in section 7 are projections. The "ui-mode" counts (52 methods, 2,467 lines mention it) include methods that merely reference it; the 833-line figure is the set that actually branches on AI mode.
