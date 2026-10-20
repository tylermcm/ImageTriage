# Known issues

Open problems that are recorded but not fixed. Newest first. Each entry says what is known and what is only a guess.

## Saving photo edits to the NAS fails with "automation write ... failed (NotFound)" (recorded 2026-10-18, not fixed)

**What the user sees.** In the hosted Camera Raw workflow, after editing a photo and moving on, up to three orange toasts appear at the
bottom right, and the status bar repeats the message:

> Saving photo edits: I/O: automation write `_DSC6972.jpg.pcraft` failed (NotFound): The system cannot find the path specified. (os error 3)

Windows error 3 is `ERROR_PATH_NOT_FOUND`. The host's own message for the same event (in `errors.log`) ends "Return to this photo to retry", so the
edits for that photo were **not saved** until it is opened again. The toasts stack (the same failure is reported several times).

**Where it happens.** Photos on the NAS share, e.g. `\\192.168.1.200\ColossalBoi\Photography\China '26\` (JPEGs). The editor, not Image Triage, makes
the failing write (the "automation write" is the editor's file API), into the edits folder beside the photo.

**Evidence.** Six occurrences in the logs under `photocraft/target/*/image-triage-test/logs/errors.log` (two builds: `acr-filmstrip`, an older one, and
`acr-calibration-sr`), so it **predates the Super Resolution / Calibration / preview-worker work**:

| photo | file the editor failed to write |
|---|---|
| `_DSC6920.jpg` | `_DSC6920.jpg.photocraft.g1792374586058246400.png` (a full-size render, "generation" file) |
| `_DSC7484.jpg` | `_DSC7484.jpg.photocraft.g1792362877286007600.png` |
| `_DSC7021.jpg` (twice) | `_DSC7021.jpg.photocraft.g1792387063362315800.png`, `...g1792387096549825300.png` |
| `_DSC7062.jpg` | `_DSC7062.jpg.pcraft` (the project file) |
| `_DSC6972.jpg` | `_DSC6972.jpg.pcraft` (the screenshot that prompted this entry) |

Both kinds of write fail the same way: the project file `*.pcraft` and the full-size render PNGs. The photos themselves open and display normally, and
the hidden edits folder (`.image_triage_edits`) exists on the share and holds other photos' edit data, so the folder is not simply missing.

**Not known (guesses only, none tested).**
1. The NAS was briefly unreachable or the SMB session had gone idle when the editor wrote (an intermittent network error, not a code bug).
2. The folder name contains an apostrophe (`China '26`); something in how the editor builds or checks the write path may mishandle it.
3. A race: the edits folder or a sub-path is created by one writer while another writes into it.
4. The editor's allowed write root and the photo's UNC path disagree in some form (`\\host\share` vs `//host/share`), so a path inside it is "not found".

It is also not known whether it happens for photos on a local drive, or every time or only after the editor has been idle.

**Suggested first steps (for whoever picks this up).**
- Reproduce on a local-drive copy of the same folder, and on the NAS with a folder name without an apostrophe. That separates 1/2 quickly.
- Look at what the editor actually tries to open: raise the editor log level for `automation write` and log the resolved absolute path, not just the name.
- Make the host report the failure once per photo (not three toasts) and say what to do; the "Return to this photo to retry" advice is in the log but not in the toast.
- Consider a retry with a short back-off for `ERROR_PATH_NOT_FOUND` on a UNC path before telling the user.

**Where to look.** Host: `image_triage/preview_controller.py` (`_poll_photocraft_stashes`, the "Could not save edits for ..." message),
`image_triage/photocraft_bridge.py`. Editor (PhotoCraft): the control server's `app.stash` / `automation write` handling and its write-root checks
(`path_within_write_root` on the host side decides what the editor is allowed to write).

---

## Subject / Background masks are slow on every use (recorded 2026-10-20)

**What.** In the Camera Raw Masking tab, Subject and Background (BiRefNet, run by Image Triage) took **47.8 s** for one 1362 x 1908 photo on the
author's machine, using the GPU (`device='cuda'`, so this is not a CPU fallback). Measured by calling `subject_mask_service.run_subject_mask`
directly with `Loading subject model...`, `Preparing image...` and `Finding subject...` as the only progress steps; the editor popup shows the same steps.

**Why (probable, not profiled by phase).** Every request starts a new Python process under the managed torch runtime, which imports torch and
transformers and loads the BiRefNet weights from disk before it infers anything. A second request pays all of that again. The inference
itself on a 1024 x 1024 input is expected to be a small part of the 48 s. Timing per phase is available by setting `IMAGE_TRIAGE_AI_METRICS=1`
(`ai.mask.birefnet.worker.*` events) and has not been captured yet.

**Not known.** How the time splits between import, model load and inference; whether a second request is faster (it should not be, by design);
whether the 1024 px inference resolution or the preview size the editor sends matters.

**Suggested fix.** Keep one warm worker (or use the unified inference host, see the inference-host notes) so only the first request pays for loading,
and show "Loading the subject model..." honestly in the popup meanwhile.

**Where.** `image_triage/subject_mask_service.py` (spawns the worker), `image_triage/subject_mask_worker.py`, `image_triage/birefnet_worker.py`.

**Update 2026-10-20 (same day): fixed for the editor's masks.** The cold cost was measured per phase (mask-engine execution log): with the operating
system's file cache cold, starting the host and importing torch and CUDA took **32 s**; with it warm, 7 s; OneFormer, BiRefNet and SAM then load in
about 1, 2 and 0.5 s. Subject, Landscape and People now run on the one resident mask-engine host (`mask_engine_service.py`), and
`image_triage/mask_warmup.py` loads all three models in the background as soon as the editor is connected and keeps them alive while it stays open.
Measured after that warm-up (12 s, in the background): first Landscape 2.0 s, first Subject 0.8 s, first People 5.4 s; later ones are faster
(Landscape 0.5 s, Subject 0.2 s, People about 3.7 s, which is SAM per person plus BiRefNet edges). Turn the warm-up off with
`IMAGE_TRIAGE_MASK_WARMUP=0`. Still true: a mask asked for in the first ~12 to 35 s after the editor first connects waits for the load.

**Where the start-up time goes (measured 2026-10-20, warm file cache, two runs):** numpy/PIL/cv2 0.1 s, `import torch` 1.4 s, `import transformers` 2.2 s,
importing the transformers model classes 3.0 s, CUDA init + first GPU tensor + first matmul 0.35 s. So the cost is Python imports, not CUDA and not model
weights. The 32 s cold case is believed to be the same imports reading thousands of small files and the large DLLs from a cold disk cache; it has not
been captured stage by stage (the host now logs `mask-engine: import stages (ms): ...` to `execution.log` on every start, so the next slow start will be).
Peak GPU memory for the masks (all three models resident, after Landscape, Subject and People ran): about 2.8 GB above the idle level, of which about
0.9 GB is the loaded models. Not yet decided: what to do on 6 to 8 GB cards. The warm-up now waits 5 s after the editor connects and loads at below-normal
CPU priority, restored to normal when it finishes.

---

## NAS save failure: new evidence, and it now blocks switching photos (2026-10-20)

**New symptom.** In the editor, after one photo's settings fail to save, picking another photo in the filmstrip shows it for under a second and then goes
back to the previous photo, which cannot be deselected. Cause: switching photos makes the editor first keep the open photo's settings
(`app.open` -> write `_DSC4918.NEF.cameraraw.json`), that write fails with the same `NotFound (os error 3)`, and the switch is abandoned
(`image_triage.preview_controller._switch_photocraft_document` raises `PhotoCraftError`).

**What is established (all on `\192.168.1.200\ColossalBoi\Photography\China '26`):**
- The editor writes the whole temp file (`._DSC4918.NEF.cameraraw.json.23160-0.pcsave.tmp`, 31,528 bytes, complete) and the failure comes at the rename that
  would put it in place; no final file appears. Failed temps are left behind: 89 `*.pcsave.tmp` files (about 2.2 GB) in `China '26/.image_triage_edits`
  and a few in `Raw Files/.image_triage_edits`.
- It is not the apostrophe in the folder name, and not the leading `._` of the temp name (photos named `_DSC####` get a temp name starting `._`, which
  looked like the macOS AppleDouble prefix): renaming a `._`-named temp over the share works from Python.
- The editor's own `photocraft_format::atomic::atomic_write` (path based) works on the same folder: create, replace, and the temp-name/rename/`replace_existing`
  steps all return Ok (`crates/format/examples/nas_probe.rs`).
- The failing code is therefore the other write path: `crates/automation/src/workspace.rs` `AuthorizedWorkspace::write`, which writes the temp through a
  `cap-std` directory handle and renames with `root.dir.rename(&tmp, &root.dir, &relative)` (a rename relative to an open directory handle).

**Working hypothesis (not yet confirmed):** a handle-relative rename fails over SMB on Windows (the server rejects a rename that names its target through a
root-directory handle), while path-based renames work. A probe was being built to test exactly this: `crates/automation/examples/nas_probe.rs`
(`cargo run -p photocraft-automation --example nas_probe -- "//192.168.1.200/ColossalBoi/Photography/China '26/Raw Files/.image_triage_edits" "_probe_cap.json"`;
use forward slashes, a `\` prefix gets collapsed by some shells and the write lands on `C:\`).

**If confirmed, the fix to try:** when the capability rename fails with `NotFound` on Windows, check that the temp file exists through the capability and that the
write root canonicalises beneath itself, then rename by absolute path (`std::fs::rename`), keeping the symlink-escape protection the capability gives.
Also: the editor should clean up its `*.pcsave.tmp` after a failed rename (it tries, via the same handle, and that fails too), and Image Triage should not
abandon a photo switch because the *previous* photo's settings could not be saved.

**Leftovers from the diagnosis (please delete):** `C:\192.168.1.200` (a stray local folder holding two 30 KB probe files; created when a `\` path was collapsed),
and on the share in `Raw Files/.image_triage_edits`: `probe_a_final.json`, `probe_b_final.json`, `probe_c_final.json`, `_probe_nas.json`, `_probe_nas.json.second`.
