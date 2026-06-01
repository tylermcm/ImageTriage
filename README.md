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
- EXIF capture details in preview and compare mode
- Persistent accepted/rejected/rating/tag decisions
- Persistent warm-start folder scan cache for faster folder reopen
- Smart compare that can auto-size to detected exposure brackets
- Right-click file actions including Explorer reveal and app handoff
- Sorting by filename, date modified, or file size

## Keyboard Controls

- `Arrow keys`: move selection
- `Page Up / Page Down / Home / End`: jump through the grid
- `Space` or `Enter`: open full-screen preview
- `Delete`: move selected file to trash when available
- `W`: accept the selected image
- `X`: reject the selected image
- `K`: move selected file into a `_keep` subfolder
- `M`: move selected file to a folder you choose
- `0-5`: assign a rating
- `T`: assign tags
- `Esc`: close full-screen preview

## Run

```powershell
py -3 -m pip install -e .
py -3 -m image_triage
```

If the `py` launcher is not available on your machine, run the commands with your local Python executable instead.

If you are updating an existing install, rerun the install command so dependencies stay in sync:

```powershell
py -3 -m pip install -e .
```

## Linux AppImage

Build and install a local AppImage from this repo with:

```bash
bash packaging/install_linux.sh
```

That script will:

- create `.linux_build_venv`
- install the AppImage build dependencies from [packaging/linux-build-requirements.txt](/Users/tylle/OneDrive/Documents/Playground/packaging/linux-build-requirements.txt)
- run [setup_linux.py](/Users/tylle/OneDrive/Documents/Playground/setup_linux.py) to build the AppImage
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

GitHub Actions also builds an AppImage artifact on pull requests, pushes to `main`, and manual runs through [build-linux-appimage.yml](/C:/Users/tylle/OneDrive/Documents/Playground/.github/workflows/build-linux-appimage.yml).

The Linux build and the Windows MSI now share the same AI runtime staging logic through [freeze_support.py](/Users/tylle/OneDrive/Documents/Playground/freeze_support.py), so both package types bundle the same integrated `AICullingPipeline` tree and the same Python-side AI dependencies.

The active CLI-Culler source lives in the in-repo [aiculler package](/Users/tylle/OneDrive/Documents/Playground/aiculler) and is the default AI culler implementation used by the app. CLIP/TOPIQ weights remain outside git; set `IMAGE_TRIAGE_AICULLER_MODEL_ROOT` to point at a local `models` directory when the defaults do not apply.

On first launch, the app offers to download the AI model into:

```text
~/.cache/image_triage_ai_cache/models/DinoV3
```

If the user skips that step, the older bundled AI pipeline stays unavailable until the model is installed.

## Notes

- Version 1 focuses on scan speed, responsive scrolling, and large visual previews.
- Workflow refinement, metadata polish, and side-by-side compare improvements are natural next steps.
