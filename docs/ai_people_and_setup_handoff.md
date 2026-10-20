# People grouping on the blink pass, and the Set Up AI redesign: handoff

## Decisions (from the owner)
- People grouping stays a core feature and is bundled into the AI runtime, not an add-on.
- One face finder: the blink pass's MediaPipe Face Landmarker. No second detector. The five alignment points (eye centres, nose tip, mouth corners) come from its 478 landmarks by plain geometry.
- Recognizer: AdaFace IR-50 (MIT code; UniFace's ONNX export). EdgeFace is out (BSD-3). The owner accepts the training-data licence question as the model maker's, not ours.
- Faces under 1% of the frame are background people: skipped for blink and for People alike.
- Old AuraFace people stay until the rescan finishes; names carry over by photo and box.
- TOPIQ and AuraFace are retired from setup. TinyCLIP stays (semantic search and the person pre-filter).
- Build everything in one go; push when done.

## Built and pushed
- `cascade/faces.py`: alignment geometry, preprocessing, `FaceFinding`, `FaceCache` (`faces_cache.json`, keyed by file size and mtime, shared by the cascade and the People index), `FACE_MODEL_TAG = "adaface:ir50"`.
- Worker `--kind faces` (`FacesEngine`, `--aux-dir` for the second bundle), `detail` events in the protocol, `WorkerRanker.details`, `faces_ranker()`. Verified end to end under the managed runtime.
- Bundles `faces_identity` (yakhyo/uniface-weights, pinned commit and sha256) and `faces_blink`; `ensure_aux_weights`.
- `face_stack.py` (`FaceStackAnalyzer`) replaces the InsightFace analyzer in `face_index.py`; cluster threshold 0.35 (calibrated on 88 faces of five people, one library: recalibrate on more).
- `people_search.py`: `remember_named_faces` / `apply_remembered_names` (table `face_name_carry`) so names survive the recognizer change, including a People window that clusters before the rescan has produced a face.
- Cascade hook: after the decision, faces of every frame that is not a duplicate or bracket member are found and cached (`CascadeRunTask._index_people`); the People index reads the cache and skips the pre-filter and the models for those files.
- Manifest: `faces` capability is now the landmarker + AdaFace (`face_stack` probe, `ai_probe.probe_face_stack`, installer passes the second directory); new optional `cascade_rankers` capability (MANIQA, Q-ReAlign; torch runtime); `quality_topiq` and the old pack leave setup (`legacy_faces` keeps the old pack defined). `ai_manifest` registers the cascade bundles at import.
- Setup controller: face readiness uses the new bundles; retired models no longer prompt or download; a runtime that is installed but missing the new models now runs the capability download step.
- Tests: `test_cascade_faces.py`, `test_face_stack.py`; related suites pass. Known unrelated failures: `test_ai_runtime_packages` lock-hash count (pre-existing).

## Not built (next)
1. **Done 20 October 2026 (see the section below).** The Set Up AI dialog redesign from `docs/ai_setup_mockup.html`: hardware-based default, component list with sizes, side summary, step-by-step progress and verification view. Today the old two-card dialog still shows (its model line now reads CLIP, Faces and people, MANIQA and Q-ReAlign).
2. **Done 20 October 2026.** The status chip and the first-run prompt on the first Analyze without setup.
3. Remove `insightface` from the runtime requirements and locks once the legacy culler is gone (still installed; needs a lock refresh).
4. Real-library checks: people grouping on a larger set, timing of the cascade people step, the rescan and name carry-over on a real catalog.
5. The face landmarker bundle lives on the owner's Hugging Face; AdaFace is pinned to yakhyo/uniface-weights and can be re-hosted by changing `FACES_IDENTITY_REPO`/`REVISION` in `cascade/rankers/bundles.py`.
6. Age and gender are no longer estimated (the old files were non-commercial); check nothing in the UI expects them.


## Set Up AI redesign, status chip and first-run prompt: built (20 October 2026)

Mockups (the design that was approved): `docs/ai_setup_mockup.html`, `docs/ai_status_chip_mockup.html` (open from `docs/`; the second embeds a real window capture).

**Decisions (owner).** PyTorch is part of the AI runtime and never listed. TinyCLIP and the face models are core. Two optional pills, **Culling** (MANIQA 543 MB, Q-ReAlign 2.1 GB) and **Editor tools**
(BiRefNet 425, SAM 150, Depth 100, OneFormer 196 = 871 MB), each one switch that opens to single models. Defaults: a PC with an NVIDIA GPU has both on, one without has both off. The CPU slowness warning
appears only after the person turns Culling on or opens it. One download number, one Install button, "More" is a floating menu, and the dialog never changes size (the list scrolls).
Needs setup / Installing / Update / Repair are a chip in the top bar left of Search with a slow conic-gradient ring; Ready is quiet clickable text ("AI ready") in the status bar.
The first Analyze with no AI setup shows a card hanging from the chip (arrow up) with "Set up AI (size)" and "Analyze without it"; it is asked once (`ai/first_run_prompt_answered`).

**Real sizes** (from the manifest, not the mockup's first guess): core is 2.9 GB without a GPU (runtime with PyTorch 2.6 GB + faces 170 MB + TinyCLIP 98 MB) and 6.9 GB with one; everything on is 6.4 GB / 10.4 GB.

**Code.**
- `ai_setup_plan.py` (pure): core vs optional models, groups, sizes, defaults by GPU, per-model capabilities, the saved choice (`ai/setup_models`).
- `ai_manifest.py`: one capability per optional model (`rank_quality`, `rank_aesthetic`, `editor_subject`, `editor_select`, `editor_depth`, `editor_scene`); `setup_capabilities(include_torch, optional=...)`.
  The old pair `cascade_rankers` is still there for a caller that asks for no choice.
- `ui/ai_setup_dialog.py`: the dialog. `ai_setup_controller.show_ai_setup_dialog` uses it; it reports what was pressed (install, remove, check, or a menu action), saves the choice, and removes unticked installed models.
  `selected_ai_capabilities()` is now the saved choice plus whatever is installed, so verification never demands a model nobody downloaded.
- `ai_platform.nvidia_gpu_name()`: GPU detection through `nvidia-smi` (there was none; the old dialog defaulted to GPU). Cached.
- `ai_status.py` (pure state), `ui/ai_status_widgets.py` (chip, status text, popover card), `ai_status_controller.py` (refresh every 20 s and after every setup event, the card, the first-run prompt).
  `window.py` builds the controller, `toolbar_controller` puts the chip before the search box, `_init_status_bar` adds the text as a *permanent* status widget (a normal one is hidden whenever a message shows).
- `cascade/launch.py` and `ai_run_controller`: either ranker can run alone; ranking is skipped, with a note, when neither is installed. The launch-time setup modal is gone (the chip says it instead).

**Tests.** `test_ai_setup_plan`, `test_ai_setup_dialog`, `test_ai_status`, `test_ai_status_controller` (real window), `test_cascade_launch` (per-ranker). The AI/cascade/window groups pass except failures that predate this work:
`test_ai_filters_and_menu` (3, a deleted QMenu in the AI menu walk, fails on the commit before this work too) and `test_ai_runtime_packages` lock-hash count (known).

**Differences from the mockup.** "AI ready" sits among the right-hand status items (before "Load: ..."), not right after the photo counts: the counts are the status bar's temporary message, and a normal widget would be hidden by it.

**Leftovers finished (20 October 2026).**
1. *Progress.* `ai_setup_progress.SetupProgress` (pure) turns the runtime installer's lines (pip's "Downloading X (N MB)", "Installing collected packages") and the model downloads' messages into one percent and three steps
   (Downloading, Installing, Checking that it works), each part weighted by its download size; the last 4% is the final check; the ETA follows the pace once it is past 3%. `ui/ai_setup_progress_dialog.py` is the non-modal view
   (bar, three steps, time left, Cancel, then "AI is ready" or what went wrong; a failure always comes to the front). `AiSetupController` begins a run in `start_ai_setup_selection`, feeds it from every progress handler, and ends it on the
   readiness result. The chip shows "Installing AI 42%" and its card has the bar, "Show details" (reopens the view) and "Cancel" (`cancel_setup`: the installer subprocess is terminated through the new `AIRuntimeInstallTask.cancel`,
   bundle tasks are cancelled, the next step never starts, nothing pops up). Not covered: a model download task (`AIModelDownloadTask`) has no cancel of its own, so it finishes its current file first.
2. *Update.* The source is the model store's own `stale` state (installed from a different pinned revision) plus bundles a selected capability needs that are missing. The chip says "AI update 547 MB", the card lists the models, and
   **Update** runs `update_ai_models()`: just those bundles, with the progress view.
3. *Editor tools.* `editor_tools.py` maps each editor tool (`subjectMasks` BiRefNet, `sceneMasks` OneFormer, `peopleMasks` OneFormer + SAM + BiRefNet, `clickSelect` SAM, `depthMap` Depth) to the models it needs; a tool is available exactly
   when it has all of them, so any subset of models works. `camera_raw_host_jobs.HostJobRunner` now has a gate: a pending job of one of those kinds whose models are missing is claimed and finished at once with
   "People masks need Scene regions and Subject masks (621 MB). Open the AI chip, choose Set up AI and turn on Editor tools.", and the window shows the same line in the status bar. The Ready card has an Editor tools row.
   The mask jobs themselves (scene/people/subject host jobs, `mask_warmup`) are still on the other checkout; when they land they only need to run after the gate, which is already in front of every kind.
   Not done: telling the editor *in advance* which mask buttons to grey out. That needs a field in the `ui.hostJob` poll and a PhotoCraft change; `editor_tools.available_kinds()` is the value to send.

**Not built / next.** (1) Tell the editor which mask buttons to grey out in advance (see above). (2) Real-library checks listed above. (3) Remove `insightface` from the runtime requirements. (4) Run the dialog and a real install on a machine with an NVIDIA GPU
(this PC has no `nvidia-smi`, so only the CPU defaults were seen live, and the percent was only tested with recorded installer output).
