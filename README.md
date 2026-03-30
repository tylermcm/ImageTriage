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

## Notes

- Version 1 focuses on scan speed, responsive scrolling, and large visual previews.
- Workflow refinement, metadata polish, and side-by-side compare improvements are natural next steps.
