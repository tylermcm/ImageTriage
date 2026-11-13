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

- `exposure`: EV stops, `-5` to `5`
- `contrast`, `highlights`, `shadows`, `whites`, `blacks`: `-100` to `100`
- `temperature`, `tint`, `vibrance`, `saturation`, `clarity`, `dehaze`, `sharpen`, `denoise`, `vignette`: `-100` to `100`
- `vignette` is `+` to darken the corners and `-` to lighten them, two stops at either end. Its shape is set by `--vignette-midpoint` and `--vignette-feather` (`0..100`, `50` neutral), `--vignette-roundness` (`-100` frame-shaped to `100` circular), and `--vignette-highlights` (`0..100`, how far bright pixels are spared)

### Exposure is in stops, and saved edits keep their look

`--exposure 1` is one stop: twice the light, applied in linear light (the picture is decoded from sRGB, scaled and encoded
again), the same factor on all three channels. Raising the exposure bends into white above 80% linear instead of clipping; below
that the result is exact, so a mid-grey patch (sRGB 118) at `+1` lands at 162.

The picture is assumed to use the sRGB transfer curve whatever colour profile it carries. The profile is passed through to the
output, not applied.

Exposure used to follow a curve measured from screenshots, where `+1` was well over a stop. Edits saved with that curve are not
re-interpreted. Every exposure value carries the model it is in:

| where | marker | meaning |
|---|---|---|
| recipe file | `"exposure_model": 2` | stops |
| recipe file | `"exposure_model": 1`, or no marker | the first curve |
| session, `adjust.exposure` operation | `"model": 2` in its params | stops |
| session, `adjust.exposure` operation | `"model": 1`, or no marker | the first curve |

`photoedit render`/`recipe` with `--exposure`, `op-add adjust.exposure` and `op-set ... --param exposure=...` write model 2. A
recipe or session written before the marker existed has none and renders exactly as it did. To move an old recipe to stops, give
`--exposure` again on the command line or add `"exposure_model": 2` and choose the value anew; the old number does not mean the
same thing. `EditRecipe(exposure=...)` built in code without a model uses the first curve, for the same reason.

### 16 bits per channel

A 16-bit PNG or TIFF rendered to a PNG or TIFF stays 16-bit: it is read at full depth, edited in floating point and written as
16-bit RGB with its colour profile and EXIF. Crop, rotation, exposure, white balance, the tone controls, vibrance and saturation
are computed at full precision. The other steps (retouch, calibration, noise reduction, clarity, texture, dehaze, sharpening,
the colour mixer and grading, curves, vignette, defringe, grain) exist only as 8-bit operations; they run on an 8-bit copy and
the change they make is added to the 16-bit picture, so the picture keeps its own gradations and only the adjustment has 8-bit
precision. Alpha is dropped, as in the 8-bit path. A 16-bit TIFF is written uncompressed. Reading 16-bit files uses the Qt image
reader that Image Triage already depends on; without it the picture is edited at 8 bits and a warning says so. Any other output
format (JPEG, WebP, BMP) and any 8-bit input take the 8-bit path as before. The whole picture is held in memory as 32-bit float
(about 12 bytes per pixel, plus working copies).

This is not intended to replace a full raw processor yet. It gives you fast terminal edits for proofing and minor cleanup. The 8-bit pipeline is Pillow and NumPy only, which keeps the CLI light and easy to embed later.
