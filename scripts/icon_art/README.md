# App icon source

`icon.svg` is the app icon (an aperture in an amber-to-orange gradient on a near-black tile); `icon_small.svg` is the same with thicker blades,
used for the 32 px and smaller frames so they stay readable. `render.js` writes every size as a PNG:

    NODE_PATH=$(npm root -g) node render.js OUTDIR

The files in the app are built from those frames with Pillow (`Image.save(..., format="ICO", sizes=..., append_images=...)` so each size keeps
its own render rather than being scaled down): `image_triage/ui/assets/app_icon-v3.{ico,png}`, `build_assets/icons/image_triage-v3.{ico,png}`
and `website/assets/image-triage-icon.png`. Bump the version in the file names when the icon changes.
