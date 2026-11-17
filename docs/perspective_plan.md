# Perspective (Crop tab > Transform) — plan

Planned 2026-11-17. Lightroom's Transform panel, inside our Crop tab: keystone correction for buildings and architecture.

## Where it sits

Perspective is a host-side step like the crop, applied to the developed picture before the crop rectangle:

    PhotoCraft render (as recorded) -> perspective warp -> crop rectangle -> straighten -> quarter turns and mirrors

- Masks, retouching, red eye and AI Erase stay in the picture as recorded (the "source view"), so they are not affected.
- The crop rectangle is in the warped frame; the crop tool shows the warped frame (quarter turns and mirrors, as now).
- The settings join `hostCrop` (so undo, presets' photo-specific rules, copy/paste and saving already treat them right):
  `vertical`, `horizontal`, `aspect` (-100..100), `scale` (50..150, 100 = none), `offsetX`, `offsetY` (-100..100), `constrain`
  (bool), later `upright` and `guides`. A recipe without them is unchanged.
- Sliders are as the person sees the picture: a quarter turn or mirror is undone before the warp is built, so Vertical always
  corrects lines that are vertical on screen.

## The model

The picture is treated as a plane seen by a camera with a normalised focal length (1 = the long side). Vertical turns that plane
about the horizontal axis, Horizontal about the vertical axis (up to about 25 degrees at 100); then Aspect stretches across,
Scale zooms and the offsets move it. The result is one homography (`raw_perspective.homography`), used:

- forward, by `QPainter` with a projective `QTransform`, to warp the picture (preview frames through the image provider's
  `persp=` address part, full-size renders and exports in `raw_crop`), 16-bit kept;
- backward, to map a point of the finished picture to the picture as recorded (white balance and point colour pickers).

**Constrain crop** zooms the warped picture just enough that no empty corner shows (corners of the frame mapped back must fall
inside the picture; found by bisection). Without it the empty parts are left transparent/black, as Lightroom shows them white.

## Phases

1. **Manual Transform** — Vertical, Horizontal, Aspect, Scale, X Offset, Y Offset, Constrain crop; a grid over the photo while a
   slider moves; Reset Transform. Preview, editor saves and exports.
2. **Guided Upright** — draw two to four guides on lines that should be vertical or horizontal; the sliders are solved from them
   (least squares over Vertical, Horizontal and the straighten angle).
3. **Auto Upright** — Auto, Level, Vertical, Full: line segments found on the preview (edges plus a Hough transform in numpy,
   no OpenCV in the app), sorted into near-vertical and near-horizontal families, fed to the same solver as Guided.

## Tests

The homography (identity at zero, inverse round trip, sign of each slider, quarter-turn mapping), constrain-crop leaves no empty
corner, a synthetic keystoned rectangle becomes a rectangle, preview/file/export agree, old recipes unchanged, guides and auto
solve a synthetic building.
