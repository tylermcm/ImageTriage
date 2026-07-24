# Edit Session Schema v1

JSON sidecars are the canonical edit format.

```text
IMG_1234.CR3
IMG_1234.edit.json
IMG_1234.edit-assets/
```

Originals are never written. XMP is a one-way export only and is never read back.

## Identity

`source.lastKnownPath` is a hint only. Source identity is based on:

- `source.fileSizeBytes`
- `source.contentHash`
- `source.embeddedImageId` when available

The v1 content hash is `blake2b-64` in `prefix-plus-size-v1` mode. It hashes the first 64 MiB plus the file size. The full RAW is not hashed on every load. `mtimeNsAtHash` is a cache hint only; an mtime change is not proof that content changed. During `relink`, candidate hashes are cached for the run so the same candidate file is not rehashed for every sidecar.

## Coordinate Spaces

Any operation or mask carrying pixel coordinates must reference a `coordinateSpaceId`.

Coordinate spaces are stored once in `coordinateSpaces` and referenced by id. This avoids repeating crop/dimension data per operation and keeps retouch operations, crop operations, parametric masks, and bitmap masks under one rule.

Each coordinate space records:

- `sourceWidth`
- `sourceHeight`
- `cropInEffect`, or `null` for full source pixel space

## Pipeline Order

v1 records operations in an ordered array, but the current renderer is still a fixed hardcoded pipeline. The session stores:

```json
{
  "pipeline": {
    "ordering": "fixed-renderer-order-v1"
  }
}
```

Validation rejects operation order that cannot be represented by the current renderer.

The current renderer order is:

```text
retouch.heal
retouch.clone
retouch.red_eye
retouch.auto_dust
transform.crop
transform.crop_preset
transform.rotate
transform.perspective
adjust.exposure
adjust.levels
adjust.white_balance
adjust.white_balance_kelvin
adjust.contrast
adjust.highlights
adjust.shadows
adjust.whites
adjust.blacks
adjust.vibrance
adjust.saturation
adjust.denoise
adjust.luminance_noise
adjust.color_noise
adjust.clarity
adjust.texture
adjust.dehaze
adjust.sharpen
adjust.hsl_saturation_luminance
adjust.tone_curve
adjust.point_curve
adjust.vignette_correction
adjust.vignette
adjust.chromatic_aberration
adjust.grain
```

Renderer parameter names and scales are preserved from `EditRecipe`; for example `contrast` is `-100..100`, and `exposure` is EV stops.

`adjust.vignette` carries the post-crop shape alongside the amount: `midpoint` and `feather` are `0..100` (`50` neutral), `roundness` is `-100` (frame-shaped) to `100` (circular), and `highlights` is `0..100` (how far bright pixels are spared). They are written only when `vignette` itself is non-zero; a reader that omits them gets the neutral shape.

## Editing Operations Added Ahead Of Rendering

These operations are valid in the v1 session schema and CLI, but the current renderer does not implement them yet:

- `transform.crop_preset`: `preset` is one of `1:1`, `3:2`, `4:3`, `4:5`, `5:4`, `16:9`, `9:16`; `anchor` is `center`, `top`, `bottom`, `left`, or `right`.
- `adjust.levels`: `black`, `midpoint`, `white` use image byte-space values; `0 <= black < white <= 255`, `midpoint` is `0.05..10`.
- `adjust.white_balance_kelvin`: `kelvin` is `1500..50000`; `tint` is `-150..150`.
- `adjust.point_curve`: `points` is an ordered list of `[x, y]` pairs in `0..255`, with strictly increasing x values.

They are placed in `fixed-renderer-order-v1` near their closest existing renderer equivalents so future rendering can call the same pipeline without inventing a second order.

## Masks

Parametric masks are stored as parameters:

- `radial`
- `linear-gradient`
- `subject-select`

Bitmap masks are only for hand-painted masks and cached subject-select results.

Subject-select is not truly parametric because model weights affect output. v1 requires:

- model id
- model version
- weights hash
- cached bitmap asset

The cached bitmap is the reproducibility source for old renders. Regeneration after a model change must be explicit.

## Migration

Every session has a `version` integer. A loader must either load v1, migrate known older versions, or fail loudly. It must never silently half-apply a session.

`photoedit migrate` exists from v1. For now it validates and rewrites v1 sessions, or writes a validated copy with `--out`. Unknown source or target versions fail with exit code `4` until a real migration path exists.
