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
