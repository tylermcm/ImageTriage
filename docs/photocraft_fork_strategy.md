# PhotoCraft fork strategy

Status: proposal, 2026-10-07. Nothing here is implemented yet. Written against stock
PhotoCraft `main` (upstream `7a9b591`; the local clone at `Documents\photocraft` is 2 commits behind).

Upstream is `storytold/photocraft` (Apache-2.0 OR MIT). Our fork is `tylermcm/photocraft`.
PhotoCraft is a few days old and moving fast (PR numbers already past #840), so the plan optimises
for a **small, well-fenced diff** over a large, well-merged one.

## 1. What we carry, and where it lives

| Patch | Side | Status |
|---|---|---|
| Hosted mode: `app.stash`, `app.bind`, `app.open {replace}`, hosted startup (no taskbar/activation, off-screen until embedded), snapshot cache, hosted Save routing, `ui.theme`+`dock` token, Windows render change | PhotoCraft (Rust) | **Not in stock** (verified: stock `control.rs` has only `app.open/save/quit`). Original is on the work PC, unpushed. |
| Embedding, stash policy (1.5 s poll, close/quit handling), pending/failed stash tracking, thumbnails refresh | Image Triage (Python) | In `remove-builtin-editor` |
| AI masking tools | split, see section 4 | Not started on the PhotoCraft side |

Image Triage refuses a PhotoCraft binary that lacks `app.stash`/`app.bind`
(`photocraft_bridge.py:157`), so **no stock release can be used until the hosted patch lands
in the fork**.

## 2. Branch model

- `main`: exact mirror of `upstream/main`. Never commit to it. Fast-forward only.
- `triage`: our integration branch = upstream + our patches. This is what Image Triage builds and pins.
- `up/<topic>`: one branch per upstreamable change, cut from `upstream/main` (not from `triage`),
  so a PR never drags our other patches along. After it merges upstream, drop it from `triage` on the next sync.
- Remotes: `origin` = our fork, `upstream` = storytold. (Already added on the local clone.)
- `git config rerere.enabled true` so a repeated conflict is resolved once.

## 3. Update cadence

1. **Weekly, fixed day**: `git fetch upstream`, fast-forward `main`, merge `main` into `triage`
   (merge, not rebase: one resolution, history stays shared, safe to push).
2. Run PhotoCraft's own gates on `triage`: `cargo xtask layers`, `cargo xtask wasm`, `cargo xtask ci`,
   and the `panic_hunt` test. Then run Image Triage's live check
   `python scripts/check_photocraft_handoff.py --executable <exe> --output <dir>`.
3. Only then bump the **pin**. Image Triage records the exact PhotoCraft commit it was tested
   against (`photocraft.lock`: SHA + expected hosted-protocol version), and a release artifact
   (versioned `photocraft-host.exe`) is built from that SHA by CI on the fork.
4. Scheduled CI on the fork does step 1 on a scratch branch each week and opens an issue on
   conflict or failing gates. A failing sync never touches `triage`.
5. Today Image Triage finds PhotoCraft by sibling-folder lookup and newest mtime
   (`shell_actions.py:127`). That cannot ship in the MSI. Replace it with: bundled, pinned exe
   first; dev-checkout lookup only as an opt-in.
6. Keep `LICENSE-APACHE`, `LICENSE-MIT`, `NOTICE` and `ATTRIBUTION.md` in the bundled copy.

## 4. Keeping the diff small

Rules for any change inside the fork:

- **New files over edited files.** Our code goes in files upstream does not have; touch upstream
  files only to register it (one line in `commands.rs`, one match arm in `control.rs`).
- **`PATCHES.md`** in the fork root: every upstream file we modify, why, and the PR link if one exists.
  A CI check fails if that list gains a file without an entry, the same ratchet idea as `MainWindow`.
- **Follow their rules**, otherwise their CI and `panic_hunt` fail and nothing is upstreamable
  (`AGENTS.md`): no `unwrap/expect/panic`, no `unsafe`, indexes via `get()`, every behaviour is an
  engine *command* with id, params doc, `enabled`, tests; layer rules enforced by `cargo xtask layers`;
  L0-L6 must still `cargo check --target wasm32-unknown-unknown`.
- **Upstream the generic parts.** Hosted mode (`app.stash`/`bind`/`replace`/`ui.theme`) is useful
  to any embedder and fits their "every feature drivable by agents" goal. Open an issue first;
  their control protocol is young and may change under us. Anything they accept leaves `triage`.

## 5. AI masking: where it goes

What stock PhotoCraft already has (so we do not duplicate it): classical `select.subject`,
`select.sky`, `select.object` (GrabCut), `select.quick`, `select.focusArea`, `select.refineEdge`,
`layer.removeBackground` (matting). All are **non-ML**, in `crates/algo` + `crates/engine/src/smartselect_cmds.rs`.
What it lacks, and has no roadmap for, is model-based masks (scene regions, subject/person
matting, prompt-based).

Two options:

**A. Models stay in Image Triage; PhotoCraft gets a generic mask-import seam (recommended).**
Image Triage already owns model runtimes, downloads, capability manifest and GPU handling.
It computes a mask and hands it to the running editor.
- New engine command, e.g. `select.fromMask`: takes an 8-bit coverage buffer + bounds + `mode`
  (`replace|add|subtract|intersect`) + `output` (`selection|layerMask`), applied through the
  same `apply()`/`combine_region` path as `select.quick`, so history, undo and modes behave identically.
- Control-channel front door as an `app.*` method (like `app.open`), not an engine command with a
  path param: the channel **refuses ambient file paths** (`docs/control-protocol.md`), so the mask
  arrives either as base64 PNG (precedent: `ui.screenshot` returns base64 PNG) or as a path
  relative to the automation read root (precedent: `app.open`).
- Diff: roughly one new engine file + one registration line + one control arm + tests.
  Model-free, so it should be upstreamable, and it is exactly the shape of their agent-first design.

**B. Inference inside PhotoCraft (Rust, e.g. ONNX).** Self-contained and works without Image
Triage, but we would carry a model runtime, weights distribution and GPU-provider handling inside
a fast-moving foreign codebase. Defer unless we want PhotoCraft to stand alone.

Not an option: their plug-in system. ABI v1 is **filters only** (`crates/plugins/src/manifest.rs`),
so it cannot create selections or masks.

Open questions for A: mask resolution vs. document size (edit canvas vs. original RAW dimensions),
feathered vs. binary edges, and whether the mask should be a live layer mask or a one-shot
selection. The old Image Triage mask backends (OneFormer, BiRefNet, SAM) were deleted on
`remove-builtin-editor`; they are recoverable from git history (`36cf879^`), and the engine-side
`mask_strength.py` / `subject_masks.py` remain.

## 6. Immediate steps

1. **Work PC:** commit the hosted-mode work to a branch and push (`origin image-triage-hosted`).
   Back up the built `photocraft-host-v5.exe`. This is the blocker for everything else.
2. Merge that branch into `triage`; split it into `up/*` branches by topic; open the upstream issue.
3. Add `PATCHES.md`, `photocraft.lock`, and the weekly sync workflow to the fork.
4. Prototype `select.fromMask` against stock `main` (needs no hosted patch) to measure the diff.
5. Replace the sibling-folder lookup with the pinned, bundled executable.
