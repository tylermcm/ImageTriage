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
