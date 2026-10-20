# Pass-1 audit: the server run and what happens after (written 20 October 2026)

**Status.** The tooling is built, tested on synthetic folders only, and pushed (`origin/main`, commit `d88f69f` or later). The owner runs it on the home server in the evening of 20 October 2026. Nothing has been measured on real photos yet, so no threshold or option has been changed on evidence. Why this exists and what the independent audit found: `docs/cascade_logic_audit.md`. The cascade itself: `docs/ai_v5_plan.md`, `docs/ai_v5_implementation.md`.

**The point of the order.** Keepers are chosen AFTER the run, from flag-free contact sheets, so the picks cannot be shaped by what the run flagged. Do not choose keepers from the app's normal view (flagged frames are dimmed there), and do not feed it winner/Accepted lists from folders that were already culled (they work as input but carry selection bias).

## On the server, once

1. `git pull` the ImageTriage checkout (the work is on `main`).
2. The Python used needs `numpy`, `Pillow`, `PySide6` and `exifread` (the script opens no window; PySide6 only has to import). Check: `python scripts/ai_v5/pass1_audit.py --help`.
3. Pick the folders to measure. Variety matters more than size: a few hundred frames each from several trips, including a night/astro or long-exposure folder, a folder with people, a landscape folder, and a folder with RAW+JPEG pairs. Folders are judged one at a time, as the app does (`--recursive` treats every subfolder that holds images as its own folder).

## The run (no keepers needed)

```
python scripts/ai_v5/pass1_audit.py --folders D:\Photos\trip1 D:\Photos\trip2 --out run1 --cache audit_cache.json --pick-sheet --review-pairs 60
```

It reads each image once (RAW through the embedded preview, as the app does) and writes into `run1/`: `frozen.json` (every frame's hash, measures, capture time, focal length; this is what later evaluations read, so keep it), `report.md` and `report.json` (flag rates, stack diagnostics, coverage; no recall yet), `pick/index.html` with the contact sheets, and `review.html` with blind stack pairs.

## By eye

4. Open `run1\pick\index.html`. On each page click the frames you would keep (capture order, nothing from the run is shown) and press "download keepers". Each page downloads `keepers_NN_NN.json` (a list of paths). Choosing is saved in the browser, so a page can be left and resumed.
5. Open `run1\review.html`. For each pair answer "would I look at these two side by side when choosing which to keep?" (Y = one stack, N = separate stacks; they do not have to be duplicates). Press "download labels" for `labels.json`.

## Evaluate (reads no images)

```
python scripts/ai_v5/pass1_audit.py --from-frozen run1\frozen.json --keepers keepers_01_01.json keepers_01_02.json --out run1_eval --sweep-json sweep.json
```

`sweep.json` is optional, one setting at a time unless `--full-grid` is added. Suggested first sweep:
`{"soft_ratio": [0.3, 0.4, 0.5], "clip_floor": [0.05, 0.10, 0.15], "blown_out": [0.3, 0.4, 0.5], "duplicate_distance": [6, 8, 10], "split_jump": [14, 18, 22], "linkage": ["first_fit", "closest"], "max_stack_span": [null, 18, 24], "split_focal_ratio": [null, 1.1, 1.25], "all_flagged_skips_collapse": [false, true]}`.
Because the evaluation reads no images it can be re-run as often as wanted with other keepers, settings or sweeps.

## Bring back

`run1_eval/report.md` (and `report.json` if it is easy), `labels.json`, and anything that failed (an error, a folder that scanned oddly, a RAW that would not read). The keepers files are not needed unless the numbers look wrong.

## Limits to keep in mind when reading the report

- About 200 independent keepers can show where rules fail; they cannot prove 99.5% recall (that needs at least 598 with zero misses; the report prints this). Keepers from one burst or event are not independent, so the lower bound is optimistic.
- **Blink is not measured** unless `--blink-scores` is given, and nothing in the repo writes that file yet. The same goes for `--faces-json` (needed for `split_face_ratio`). A small script that runs the blink and face workers over the folders and writes both JSON files still has to be written (the workers are `image_triage/cascade/rankers/worker.py`, `--kind blink` and `--kind faces`). Until then the blink rule's recall is unknown.
- `split_focal_ratio` uses the plain EXIF focal length (the metadata reader has no 35 mm equivalent), so it only compares frames from one lens/body family.
- Not yet tried on real RAW folders. A RAW and its same-named JPEG in one folder are treated as one frame (the RAW), as in the app.
- The run uses the cascade's current defaults; sweeps change one setting at a time. The first run is for finding failures, not for proving a result.

## What happens when the results are back

1. Read the keeper-recall table per rule and the union; list every keeper a rule flagged and look at why.
2. Read the stack diagnostics and `labels.json`: how many labelled "separate stacks" pairs sit in one stack, and whether `max_stack_span`, the focal ratio, or a smaller `split_jump` removes them without splitting pairs labelled "one stack".
3. Decide, with numbers, which of the new options become defaults (`linkage`, `max_stack_span`, `split_focal_ratio`, `all_flagged_skips_collapse`), and which thresholds move (`soft_ratio`, `clip_floor`, `clip_ratio`, `blown_out`, `duplicate_distance`, `split_jump`).
4. Open product decisions the audit raised: whether blown-out-only frames and stacks of equally blurred frames need their own rule; whether an all-flagged stack should skip duplicate collapse; genre handling for night/astro.
5. Change the defaults in `image_triage/cascade/model.py`, update the golden fixture and tests, record the numbers in `docs/ai_v5_plan.md`, and only then consider exposing any setting in the app.
6. Freeze the thresholds, then repeat the measurement on albums that were not used for tuning before any frame is ever hidden.
