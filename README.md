# Image Triage

`Image Triage` is a PySide6 desktop application for rapidly browsing and culling very large folders of images. It uses a virtualized thumbnail grid, asynchronous folder scanning, background thumbnail generation, and both memory and disk caches so scrolling stays responsive even with tens of thousands of files.

## Features

- Left-hand folder tree for fast directory navigation
- Large fixed-column thumbnail layout with 2, 3, or 4 images across
- Virtualized grid that only paints visible rows plus a small buffer
- Progressive loading with immediate placeholders
- Background thumbnail generation with visible-item prioritization
- Memory LRU cache and persistent on-disk thumbnail cache
- Nikon RAW `.NEF` support for thumbnails and preview
- Fast keyboard workflow
- Full-screen preview that loads asynchronously
- EXIF capture details in the inspector
- Persistent accepted/rejected/rating/tag decisions
- Persistent warm-start folder scan cache for faster folder reopen
- Right-click file actions including Explorer reveal and app handoff
- Sorting by name, AI score, rating or decision

## Keyboard Controls

The keys marked * can be changed in Settings > Shortcuts; the others are fixed.

In the grid:

- `Arrow keys`: move between photos (`Shift` + arrows extends the selection)
- `Home / End / Page Up / Page Down`: jump through the grid
- `Ctrl+A`: select every photo; `Esc`: clear the selection
- `Space` or `Enter`: open the photo in the viewer
- `W`*: winner, `K`*: keep, `R`*: reject (pressing the key again takes the decision off); `U`: clear the decision
- `1-5`: rate the photo; `0`: remove the rating
- `B`*: add the photo to the active collection
- `Delete`*: delete the selected photos (Safe Trash or the system trash, as chosen in Settings)
- `I`: show or hide the inspector

In the viewer:

- `Left / Right` (also `A / D`, `Up / Down`, `Page Up / Page Down`): previous or next photo; `Home / End`: first or last
- `W`*, `K`*, `R`*, `U`, `1-5`, `Delete`*: as in the grid, on the photo shown
- `Z`: 100% zoom, `0`: fit (the zoom is kept while stepping)
- `F`: focus assist, `Shift+F`: its colour
- `H`: hide the controls, `Tab`: hide the action bar
- `B` or `G`: add to a collection, `M`: move to a folder
- `E`: open in PhotoCraft
- `Ctrl+Z`: undo
- `Space` or `Esc`: back to the grid

Anywhere:

- `Ctrl+K`*: command palette, `Ctrl+F`: search, `Ctrl+O`*: open a folder, `F5`*: refresh
- `Ctrl+Z`*: undo, `F2`*: rename, `Ctrl+,`*: settings, `F11`*: Zen mode
- `F1`: help, `Ctrl+/`: the full list of shortcuts

## Run

```powershell
py -3 -m pip install -e .
py -3 -m image_triage                     # the QML shell (the application)
py -3 -m image_triage "D:\Photos\Shoot1"  # open a folder
```

If the `py` launcher is not available on your machine, run the commands with your local Python executable instead.

QML is the only application UI. The retired Widgets windows and launchers are
removed. Python modules provide shared backends; screen implementations live in
`image_triage/qml_shell/qml`. See `docs/qml_shell.md` for the current architecture.

If you are updating an existing install, rerun the install command so dependencies stay in sync:

```powershell
py -3 -m pip install -e .
```

## Windows MSI Updates

The Windows app includes `Help > Check For Updates...`. By default it checks the latest GitHub Release for `tylermcm/ImageTriage`, looks for an `.msi` asset, downloads it, and launches `msiexec` so the newer MSI can upgrade the existing install without requiring an uninstall.

For this to work in production:

- Bump `project.version` in [pyproject.toml](pyproject.toml) and `__version__` in [image_triage/_version.py](image_triage/_version.py) for every release.
- Keep the `upgrade_code` in [setup_msi.py](setup_msi.py) unchanged.
- The installer carries the PhotoCraft editor (`<install dir>\photocraft\photocraft.exe`, with its licences; the installed app uses that copy
  before any other). Build it first (`cargo build --release -p photocraft` in the companion `photocraft` checkout, branch
  `codex/image-triage-integration`), or name it with `IMAGE_TRIAGE_BUNDLE_PHOTOCRAFT_EXE`. A build that finds no editor stops;
  `IMAGE_TRIAGE_BUNDLE_PHOTOCRAFT=0` builds an installer without one.
  The release workflow does this itself: it checks out `tylermcm/photocraft` at the commit in [packaging/photocraft_pin.txt](packaging/photocraft_pin.txt),
  builds it with cargo and bundles that exe, and the release notes name the commit. Bump the pin (after validating the new editor, see
  `docs/photocraft_handoff.md`) to ship a newer editor.
- Push a matching version tag, for example `v1.1.4`. The `Build And Release Windows MSI` GitHub Actions workflow builds the MSI and uploads it to that GitHub Release.
- Prefer attaching a release asset digest or hosting a JSON update manifest with a `sha256` value so downloads can be verified before install.

To use a custom feed instead of GitHub Releases, set `IMAGE_TRIAGE_UPDATE_FEED_URL` to the URL of a JSON document like:

```json
{
  "version": "1.1.4",
  "installer_url": "https://example.com/downloads/ImageTriage-1.1.4.msi",
  "sha256": "0123456789abcdef...",
  "release_notes_url": "https://example.com/releases/1.1.4"
}
```

## Linux AppImage

Build and install a local AppImage from this repo with:

```bash
bash packaging/install_linux.sh
```

That script will:

- create `.linux_build_venv`
- install the AppImage build dependencies from [packaging/linux-build-requirements.txt](packaging/linux-build-requirements.txt)
- run [setup_linux.py](setup_linux.py) to build the AppImage
- install the result under `~/.local/opt/ImageTriage`
- create a launcher at `~/.local/bin/image-triage`
- create a desktop entry under `~/.local/share/applications`

If you already have a built AppImage, install just that file:

```bash
bash packaging/install_linux.sh --appimage /path/to/ImageTriage-0.1.0-x86_64.AppImage
```

Useful options:

- `--skip-build`: reuse the newest AppImage in `./dist`
- `--install-dir PATH`: change the install target
- `--no-desktop`: skip desktop entry creation

GitHub Actions also builds an AppImage artifact on pull requests, pushes to `main`, and manual runs through [build-linux-appimage.yml](.github/workflows/build-linux-appimage.yml). Pushing a matching `v*` tag builds the same AppImage and uploads it to that GitHub Release.

The Linux build and the Windows MSI now share the same AI runtime staging logic through [freeze_support.py](freeze_support.py), so both package types bundle the same helper launchers and Python-side AI dependencies. The legacy `AICullingPipeline` engine is no longer part of either package.

The active CLI-Culler source lives in the in-repo [aiculler package](aiculler) and is the default AI culler implementation used by the app. CLIP/TOPIQ weights remain outside git; set `IMAGE_TRIAGE_AICULLER_MODEL_ROOT` to point at a local `models` directory when the defaults do not apply.

On first launch, the app offers to download its AI models into:

```text
~/.cache/image_triage_ai_cache/models
```

If the user skips that step, the older bundled AI pipeline stays unavailable until the model is installed.

## Notes

- Version 1 focuses on scan speed, responsive scrolling, and large visual previews.
- Workflow refinement, metadata polish, and side-by-side compare improvements are natural next steps.

## License

Licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE) — noncommercial use only. This is a temporary license while the project is under active development; it will change once the project reaches a more finished state.
