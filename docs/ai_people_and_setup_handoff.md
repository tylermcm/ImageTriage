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
Install progress is still the existing in-window overlay and the chip's "Installing AI" with its message; the three-step progress view inside the dialog is not built. There is no percent on the chip (the setup reports text only).

**Not built / next.** (1) The three-step progress view and a percent. (2) A real source for the Update state (a pinned model changing in a release); today Update means "a model this version needs is not installed". (3) Editor tools: SAM, Depth and OneFormer
have bundles and capabilities but their Image Triage consumers (scene/people mask jobs, `mask_warmup`) live on the other machine's checkout; gate each tool on its own capability when they land. (4) Real-library checks listed above.
(5) Remove `insightface` from the runtime requirements. (6) Run the dialog on a machine with an NVIDIA GPU (this PC has no `nvidia-smi`, so only the CPU defaults were seen live).
