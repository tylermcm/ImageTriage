# AI v5 implementation plan: the stack-first cascade in Image Triage

Decided 18 October 2026 with the owner. The design and evidence are in `docs/ai_v5_plan.md`; this file is the build plan.
Acceptance test (owner): the owner's own review of the cascade output on real sets, not a numeric proxy.

## Decisions taken

- **Replaces the current Analyze pipeline.** Analyze runs the cascade and feeds the existing results model, so the aurora Analyze card, bucket
  bar, grid dots, inspector rows and AI filters keep working. The old TOPIQ-based culler stays only behind an advanced setting (it cannot ship
  commercially) and is removed from the default path.
- **Flag and dim, never hide, in the first release.** A frame with a pass-1 reason shows its reason and is dimmed, with one-click restore. Hard
  hiding waits for the library-wide keeper-recall check (target 99.5% on known keepers, per rule).
- **No GPU: cheap passes only; ranking is opt-in.** Stacks, pass 1, pHash collapse and blink run on any machine (about 0.5 s per frame, less with the
  app's own preview cache). MANIQA and Q-ReAlign run automatically on a usable GPU and on a CPU only when switched on, with a time estimate.
- **Licence gate before commercial release, not before building.** MANIQA and Q-ReAlign weights are downloaded on demand and never bundled. Their
  training-data licences are unverified (see `docs/ai_v5_plan.md`); each ranker sits behind a small interface so it can be swapped.

## How it plugs into what exists

- Input: the app's embedded-JPEG extractor (`raw_embedded_jpeg.extract_embedded_jpeg`, which also returns EXIF orientation) and the preview/thumbnail
  caches, decoded with a reduced-size JPEG decode. No RAW decode.
- Output: `AIImageResult` rows built with `build_ai_bundle_from_results`, the same bundle the old pipeline produced:
  - `group_id` / `group_size` = the stack; `rank_in_group` 1 and 2 for the final frames; `cluster_reason` = why the stack was formed.
  - `triggered_tags` = reason codes (`blink`, `soft`, `clipped`, `blown_out`, `duplicate_of:<file>`); `confidence_bucket` and the cull bucket map as:
    final = Winners/Keepers, duplicates and cut-by-ranking = Needs review, pass-1 flagged = Rejects (flagged, not hidden), no-pick stacks = Needs review.
  - A stack with no pick says so in the inspector ("every frame is blown out").
- Existing pieces to reuse, not rewrite: `perceptual_hash.compute_phash` and the pHash cache, `phash_prefilter` (its representative-preserving logic),
  `bursts.py` and `brackets.py` (exposure brackets and panoramas must stay together), `birefnet_worker` for subject masks later, `face_index` for blink.
- Rankers run in the managed AI runtime as worker subprocesses (like `birefnet_worker`), loaded through `ai_model_store`, so the main process never
  imports torch.

## Work packages (each lands with tests, offscreen only)

1. **Cascade core (pure Python, no Qt, no torch).** `image_triage/cascade/`: stack split (consecutive pHash jump >= 18, thresholds in one settings
   object), pass-1 flags relative to the stack (soft < 0.4x best centre sharpness; clipped > max(2x lowest, 10%); blown out >= 40% of pixels at maximum;
   blink from the face index), pHash complete-linkage duplicate collapse (8) keeping the most evenly exposed frame, a `Ranker` protocol, and the
   MANIQA-top-3 then Q-ReAlign-top-2 selection. Golden test: feature rows exported from the 45 + 63 reviewed bursts must reproduce the reviewed decisions.
2. **Cheap-pass feature extraction.** Preview loader on the app's extractor with orientation, measures (centre Laplacian, clipped fraction, extremes,
   pHash), batched and cached by file signature; timings measured in the app, not assumed (the server number was 370 ms for my scratch reader).
3. **Ranker workers.** MANIQA (KonIQ checkpoint, 20 fixed-seed 224 px crops) and Q-ReAlign Mini (aesthetic task, fp16 on GPU, fp32 on CPU) behind the
   `Ranker` protocol; GPU/CPU detection (a V100-class card needs fp16, not bf16: Q-ReAlign hangs on bf16 there); downloads through the model store with
   hash verification; clear errors and a cancel path.
4. **Integration.** An Analyze run task that produces the bundle; progress and cancel through the existing card; reasons and stack info in the
   inspector; dim-and-flag treatment in the grid; setting for ranking on CPU; removal of the old pipeline from the default path.
5. **Library-wide validation.** Pass-1 keeper-recall check on known keepers (winners, Accepted, edited exports), per rule; loosen any rule that costs
   keepers; then decide whether rejects may ever be hidden.
6. **Licence close-out.** Verify the remaining training-data licences (Q-ReAlign: KonIQ, SPAQ, KADID, AGIQA-20K, AVA, LSVQ; MANIQA: KonIQ, KADID, PIPAL)
   from primary sources, or swap the rankers. Replace the three InsightFace-looking face files in the audit (YuNet and SFace are MIT/Apache).

Order: 1 first (everything else depends on it and it is testable alone), then 2 and 3 can proceed in parallel, then 4, then 5 and 6.

## Risks to watch

- Stack boundaries were set from one reviewed album and one example (pHash jump 18); expose the thresholds in settings and review them on a
  different genre before treating them as final.
- Relative flags misfire when a stack has little contrast between frames (seen once: a 0% clipped baseline); the floors exist for that.
- Night, astro and long-exposure shooting will trip the blown-out and extremes measures; genre loosening is still an open decision.
- The review pages were built on a V100 server; MANIQA and Q-ReAlign on a typical desktop CPU are about 7x slower per frame than on that GPU.
