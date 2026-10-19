# Claude handoff: Camera Raw / Image Triage, 2026-10-18

## Start here

User wants a usable local testing checkpoint and continuation by Claude. Both repos
use `codex/image-triage-integration`. User authorized pushing everything to their
forks; no upstream PR, force push, release or default-editor promotion.

Local host: `C:/Users/tylle/OneDrive/Documents/Playground`
Local editor: `C:/Users/tylle/Documents/photocraft`
Forks: https://github.com/tylermcm/ImageTriage and https://github.com/tylermcm/photocraft.
PhotoCraft remote names differ from AGENTS examples: origin=user fork,
upstream=storytold. Never push to upstream.

## Usable development build

```powershell
& "C:/Users/tylle/OneDrive/Documents/Playground/scripts/test_camera_raw.ps1" -EditorBuild acr-main-adjustments
```

Open photo in popout, press E. Candidate lives at
`C:/Users/tylle/Documents/photocraft/target/acr-main-adjustments/debug/photocraft.exe`.
SHA256 `cac833711778b15699ccac589a30e1cb1ad2fbf3ad88ac0933b261b0fc0f1097`.
The existing default `acr-settings-menu` and known-good target/debug executable are
preserved. Build binaries and target/ are local, not committed to Git.

User is gaming: do not open visible apps or steal focus. Offscreen egui snapshots
are safe. Ask/coordinate before any live hosted test; secondary monitor and no
activation are required if launching is later authorized.

## Implemented

- Accepted ACR visual work: filmstrip, Mixer layout, Effects spacing/Glow row,
  Vibrance/Saturation gradients; leave negligible cosmetic differences alone.
- Glow processing: Diffusion/Bloom/Halation, amount, Range, Spread, Warmth.
- Color Mixer: All Colors displays all eight working HSL band controls.
- Point Color: up to eight samples, click picker, swatch select/delete, Hue,
  Saturation, Luminance, Variance, Range; cached view-only range visualization.
- Shared CameraRaw params support preview, commit/cancel, undo/redo, saved native
  projects, and per-photo recipes. New finishing params strictly validated.
- Private PhotoCraft XMP extension preserves settings; Adobe does not interpret
  it. PSD export explicitly refuses unsupported adjustments.
- `cameraRawFinishing:1` handoff capability; host refuses incompatible editors
  and RAW-recipe renderers rather than silently dropping new controls.

Core source: `crates/algo/src/camera_raw/finishing.rs`,
`crates/ui-egui/src/camera_raw_finishing_ui.rs`, `camera_raw_mixer_ui.rs`,
`camera_raw_ui.rs`, `camera_raw_workspace_ui.rs`; engine lens_cmds, I/O
camera_raw_xmp, UI raw_recipe, host photocraft_bridge and full_render.
Other preexisting host work included in checkpoint: narrow Qt TIFF warning filter
and tests, fork strategy and RAW smart-object planning documents, toolbar reference.
Those planning docs are historical proposals, not current implementation claims.

## Validation and limits

Host focused suite111 passed/2 skipped; Qt warning tests2 passed. UI library3127
passed/13 ignored before last narrow fixes; focused UI26 passed; final Mixer6
passed. Finishing algorithm5, engine finishing2 +noise3, adversarial command test,
I/O13 passed/1 ignored. Final native app and snapshot built. Affected-crate
all-target clippy, layers and wasm passed. Diff whitespace and launcher parse checked.
No claim of complete crate-wide final test matrix or Adobe pixel-output parity.

Optimized Rust compiler stalled/crashed locally. Temporary external Cargo config
uses opt-level0 for algorithm and engine in dev/test, without changing repository
Cargo profiles. Copy of override in `docs/handoff/2026-10-18-main-adjustments/debug-profile.toml`.
Use `cargo --config <absolute-config-path> build -p photocraft -j 1` and
`RUST_MIN_STACK=33554432` if reproducing. Preserve known-good executable before
building into target/debug; do not overwrite selected build until gates pass.
The selected candidate also has `validated-design/layout.json` and `tokens.json`.

Proof images and metadata are in PhotoCraft `docs/handoff/2026-10-18-main-adjustments/`.
They are real offscreen native captures; captures precede the final small SDR
point-selection clamp fix, covered by the final algorithm regression and Mixer tests.
Detailed logs/proof remain locally under
`C:/Users/tylle/.codex/visualizations/2026/10/17/01a14901-444b-7781-b5e6-48730a8f5183/acr-filmstrip`.

Full real-file corpus attempt blocked: no local photocraft-corpus authoring checkout.
Live hosted gates still pending: persistent filmstrip navigation, automatic stash/
restore, native Save/grid propagation, closed-tab reopen, hidden startup, theme/
resize/fullscreen, shutdown and child-process cleanup. Do these before promotion.

Remaining disabled main controls include Optics, Lens Blur, Calibration and AI
Detail. The three requested features are complete for testing, not the entire
adjustment window. Existing Red Eye handoff describes durable recipe work still
needed. Inspect actual enabled controls before declaring another missing list.

## Upstream state and next actions

Fresh fetch succeeded for both forks and PhotoCraft upstream during checkpoint.
Upstream main advanced from last recorded5c89f16b to56dbc89a. Before checkpoint,
local native HEAD was dcda93d8: 70 local /215 upstream commits divergent;
origin integration had no fork-only commits and was87 commits behind local.
Host pre-checkpoint HEAD4fec508. This is an intentional preservation checkpoint:
upstream merge is deferred to Claude so the tested candidate is not replaced by
an unvalidated merge. No conflict resolutions or upstream-merge validation claimed.
AGENTS requires upstream integration in normal development; reconcile it deliberately,
review automatic merges in shared host/control/theme/window files, and retain ours
only for host functionality upstream lacks. Read docs/photocraft_handoff.md first.

Priority: user testing of this candidate, fix concrete functional regressions,
then remaining tools. No more pixel polishing or upstream-contribution preparation
unless requested. Do not create new goals or schedule background work.

PhotoCraft source checkpoint commit: 20028186 (user fork integration branch).

## Correction, 2026-10-18 (evening): the opt-level 0 workaround is no longer needed

The note above says the optimized compiler stalled and an external config forces `opt-level = 0` for `photocraft-algo` and `photocraft-engine`. That config is what made the sliders
slow (Codex measured 154 ms for Exposure and 1.4 s for Shadows, with a 130 ms histogram on the UI thread). Building with the repo's own dev profile (no `--config`, `opt-level = 1`,
dependencies at 2) completed in under 4 minutes with `RUST_MIN_STACK=33554432` and `-j 2`, no stall. Use `CARGO_TARGET_DIR=target/acr-calibration-sr cargo build -p photocraft -j 2`.
Measured on Codex's fixture (offscreen `snapshot` example, 900x600 proxy; before -> after): neutral 142 -> 12 ms, Exposure 154 -> 13, Saturation 143 -> 11, Clarity 173 -> 21, Glow 299 -> 62,
Shadows 1438 -> 335, histogram 131 -> 7. Also: the preview is now made on a thread (`camera_raw_preview_worker.rs`, newest settings only), and full-resolution detail develops are not started
while a setting is being dragged. The old override file (`docs/handoff/2026-10-18-main-adjustments/debug-profile.toml`) is still used for the unit-test builds in `target/calibration`.

### Shadows / Highlights speed (2026-10-18, later)

Why Shadows was the slowest slider: it is a fast local Laplacian filter with up to 96 reference tones (an eighth of a stop apart), each needing its own pyramid of the image; ~91 pyramids per preview.
* **Upstream fix taken:** `storytold/photocraft` commit `4b660fcf` (PR #7141, "Shadows/Highlights 1.5x faster, bit for bit the same") was not in our fork; applied as a patch to `local_tone.rs` (not committed).
* **Parallel across references:** the references are independent but ran one after another, each parallelised only inside its small images (single-thread CPU cost was 464 ms; with 32 threads still 167 ms).
  `llf` now runs each reference serially in a parallel task (`par_map`) and returns a sparse list of what it adds; the lists are merged in reference order, so every pixel's sum is built exactly as before.
  Verified **bit for bit identical** to the previous output (FNV hash of every output sample, 5 settings x 4 sizes incl. 2400x1600 through the downscale path: `cargo run -p photocraft-algo --example shadows_bench -- --hash`).
* Measured (900x600 proxy, repo dev profile): Shadows ~225 -> ~64 ms, Highlights ~230 -> ~66 ms; through the offscreen dialog harness Shadows 335 -> 69 ms. Optimization level above 1 barely matters (level 2 ~1.25x, level 3 none).
* The two `photocraft-algo` unit tests that fail in my unoptimized test build (`stack::...bit_identical...`, `gallery::tilt_shift...`) do not touch Camera Raw.
