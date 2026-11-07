# In-popout RAW adjustment latency

The original diagnosis below is retained as a baseline. The source QML launcher
now enables a persistent worker when built/configured; see
[`native/raw_preview/README.md`](../native/raw_preview/README.md) for the implemented
processing boundary, launch/build instructions, current timings and limits.

Measured 2026-11-06 with the current v0.20.1 hosted debug candidate and an
8256 x 5504 Nikon NEF. A single Exposure adjustment to +0.60, with no other
adjustments, took 8.47 seconds in `RawEditor._work`:

| Stage | Seconds |
| --- | ---: |
| Start hosted process and connect | 0.57 |
| `app.render` (develop, apply recipe, encode full-resolution 16-bit PNG) | 4.18 |
| Convert to full-resolution 8-bit display PNG | 3.23 |
| Shut down process | 0.48 |

These numbers exclude debounce, any previous render still running, histogram
loading, QML decode/upload, and UI presentation. They are one measured simple
recipe, not a bound on complex recipes or a claim that the reported 15+ seconds
is resolved. Changing the display PNG compression level from 1 to 0 reduced the
worker total to 7.71 seconds (conversion 2.53 seconds). That experiment was
reverted: the disk-space increase is not justified by the small improvement.

## Cause

`RawEditor._work` launches and shuts down a hosted process for each adjustment.
It calls `app.render`, an export-quality operation that reads and develops the
sensor data again, applies the recipe at native resolution, and encodes the
result. The host then decodes and re-encodes the result for QML. Queue coalescing
does not interrupt an obsolete render already running.

The existing full-resolution display conversion fixes Qt's 256 MB allocation
limit; it does not create an interactive rendering path. Reducing the final
image in Python would leave the expensive develop/export work intact.

## Required preview boundary

- Give the in-popout editor a dedicated persistent backend session, independent
  of the PhotoCraft main-window session and its Save/stash behavior.
- Cache decoded sensor data and a working preview proxy for the selected source,
  keyed by source signature and RAW-stage settings. Reuse PhotoCraft's native
  recipe implementation; do not duplicate filters in QML/Python or substitute
  an embedded camera JPEG as an adjusted RAW develop.
- Send the complete recipe plus a generation to an asynchronous preview request.
  Coalesce pending requests, cancel obsolete work where supported, and publish
  only the generation belonging to the current source and recipe.
- Return display pixels directly or a modest display image, rather than a
  full-resolution 16-bit PNG followed by a second encode.
- Keep native-resolution detail rendering for 100% zoom and final output. A
  proxy must not silently become the full-resolution zoom image.
- Reuse the existing durable Save/manifest path so edits still reach the grid.
- Bound cached photos, release processes/files on cancellation and shutdown,
  and retain source-signature/external-recipe-change checks.

Acceptance needs actual slider-to-present timings on a 45 MP RAW, repeated edits,
rapid slider changes, photo switching during work, full-resolution zoom, Save
and grid propagation, and complete worker/process cleanup. Test Light, local
tone, Effects, Curve, Mixer, Grading and Calibration independently.

The companion checkout currently has existing edits in the Camera Raw dialog,
host controls/open path and RAW workspace. Those edits were preserved during
this investigation. Both remotes fetched successfully; the integration branch
matches the user fork, while upstream is 1,336 commits ahead with 27 dry-run
conflicts. No upstream merge, rebuild, or editor promotion was performed.
