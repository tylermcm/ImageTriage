# Terminal Photo Editor

Terminal-first lightweight photo editing tools.

## Install

```powershell
python -m pip install -e .
```

## Quick Use

Create a canonical edit sidecar:

```powershell
photoedit session-new .\IMG_1234.CR3
photoedit session-info .\IMG_1234.edit.json
photoedit validate .\IMG_1234.edit.json
photoedit migrate .\IMG_1234.edit.json --out .\IMG_1234.v1.edit.json
```

Add ordered operations:

```powershell
photoedit op-add .\IMG_1234.edit.json adjust.exposure --param exposure=0.35 --last
photoedit op-add .\IMG_1234.edit.json adjust.contrast --param contrast=12 --after op-001
photoedit op-move .\IMG_1234.edit.json op-002 --after op-001
```

Add masks and local operations:

```powershell
photoedit mask-gradient .\IMG_1234.edit.json --id mask-sky --space space-source-full --x1 0 --y1 0 --x2 0 --y2 1600
photoedit mask-refine-luma .\IMG_1234.edit.json mask-sky --low 120 --high 255
photoedit op-add .\IMG_1234.edit.json adjust.dehaze --mask mask-sky --param dehaze=25 --last
```

Add future-ready editing ops to the sidecar:

```powershell
photoedit crop-preset .\IMG_1234.edit.json --space space-source-full --preset 4:5 --last
photoedit levels .\IMG_1234.edit.json --black 4 --midpoint 1.05 --white 248 --last
photoedit wb-kelvin .\IMG_1234.edit.json --kelvin 5400 --tint 8 --last
photoedit point-curve .\IMG_1234.edit.json --point 0,0 --point 64,58 --point 128,132 --point 255,255 --last
```

Repair path hints and export rough XMP:

```powershell
photoedit relink .\Photos --json
photoedit export-xmp .\IMG_1234.edit.json
```

The JSON sidecar is the only source of truth. XMP is one-way export only and is never read back. Originals are never written.

Schema details: [docs/edit-session-schema-v1.md](docs/edit-session-schema-v1.md)

Inspect and preview:

```powershell
photoedit inspect .\photo.jpg
photoedit preview .\photo.jpg --width 90
```

Render a raw-lite edit:

```powershell
photoedit render .\photo.jpg .\photo_edited.jpg --exposure 0.35 --contrast 12 --highlights -25 --shadows 20 --vibrance 18 --clarity 10 --sharpen 8
```

Save and reuse a recipe:

```powershell
photoedit recipe .\warm-clean.json --exposure 0.25 --temperature 8 --vibrance 15 --clarity 8
photoedit render .\photo.jpg .\photo_edited.jpg --recipe .\warm-clean.json
```

Minor touch-up:

```powershell
photoedit spot .\photo.jpg .\photo_clean.jpg --x 840 --y 520 --radius 18 --strength 0.75
```

## Adjustment Model

Values are intentionally Camera Raw-like:

- `exposure`: EV stops, usually `-2` to `2`
- `contrast`, `highlights`, `shadows`, `whites`, `blacks`: `-100` to `100`
- `temperature`, `tint`, `vibrance`, `saturation`, `clarity`, `dehaze`, `sharpen`, `denoise`, `vignette`: `-100` to `100`

This is not intended to replace a full raw processor yet. It gives you fast terminal edits for proofing and minor cleanup. The current pipeline is Pillow-only, which keeps the CLI light and easy to embed later.
