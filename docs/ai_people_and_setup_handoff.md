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
1. The Set Up AI dialog redesign from `docs/ai_setup_mockup.html`: hardware-based default, component list with sizes, side summary, step-by-step progress and verification view. Today the old two-card dialog still shows (its model line now reads CLIP, Faces and people, MANIQA and Q-ReAlign).
2. The status chip and the first-run prompt on the first Analyze without setup.
3. Remove `insightface` from the runtime requirements and locks once the legacy culler is gone (still installed; needs a lock refresh).
4. Real-library checks: people grouping on a larger set, timing of the cascade people step, the rescan and name carry-over on a real catalog.
5. The face landmarker bundle lives on the owner's Hugging Face; AdaFace is pinned to yakhyo/uniface-weights and can be re-hosted by changing `FACES_IDENTITY_REPO`/`REVISION` in `cascade/rankers/bundles.py`.
6. Age and gender are no longer estimated (the old files were non-commercial); check nothing in the UI expects them.
