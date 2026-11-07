# Persistent PhotoCraft RAW preview worker

`image-triage-raw-preview` links directly to PhotoCraft's Rust processing
libraries. It has no desktop window, save commands, project writes or sidecars.
The QML editor owns its private stdio connection and one selected source.
The PhotoCraft main-window session and durable Save/manifest protocol stay separate.

Build from a configured Rust/MSVC shell:

```powershell
.\scripts\build_raw_preview.ps1 -PhotoCraftRoot '<companion checkout>'
```

The default build/worker location is `K:\Builds\ImageTriage\raw-preview`.
The source QML launcher enables that worker automatically when it exists, or
accepts `-RawPreviewExecutable`. Direct module launches can set
`IMAGE_TRIAGE_RAW_PREVIEW_EXE`. Frozen applications look beside their executable;
packaging the new worker is a separate release step. Without a worker configured,
the existing full-render path remains available. Protocol versions must match.

## Processing and transport

- `hello` advertises protocol 2. `open` accepts the source path and a complete
  version-1 Camera Raw recipe; `render` accepts the complete current recipe.
- Replies contain a JSON line followed by the declared count of straight RGBA8
  display bytes. Errors have no pixel payload. The host validates byte counts,
  dimensions and source/revision identity before publishing image and histogram.
- The source sensor is decoded once. PhotoCraft's default camera-look fit is
  selected once, following its RAW import's selection rules. Native
  `develop_reduced` handles RAW-stage Exposure, white balance, Shadows,
  Highlights, Texture, Clarity and Dehaze. Its result is cached separately from
  downstream changes. Remaining adjustments use PhotoCraft's `CameraRaw::develop`.
- The sensor is developed to the settled size (target long edge 1800; 2064 on
  the tested Nikon file). Drag previews are resized from those same developed
  pixels to a 1200-pixel long edge. Both therefore share the same crop and aspect
  ratio; separate sensor reduction factors previously trimmed different edge
  remainders and caused a small framing jump on slider release. Radius scaling
  uses each frame's actual dimensions. The tuned cache retains both sizes.
- Processing stays floating point until display conversion. The worker uses
  PhotoCraft's CMS to produce sRGB display pixels and returns the matching
  RGB histogram. It does not use an embedded JPEG as the adjusted RAW image.
- CPU is the default: the spike measured hardware GPU readback slower for its
  small previews. `IMAGE_TRIAGE_RAW_PREVIEW_BACKEND=auto` allows the existing
  PhotoCraft GPU pass for supported downstream operations, with CPU fallback.
- Native sensor memory is limited to one source, two neutral preview sizes and
  one tuned recipe at both sizes. Decode limits cap sources at 128 MP and individual buffers
  at 512 MiB. The host retains at most eight images and approximately 256 MiB
  of frame data. Oversized/unsupported sensor developments fail visibly.

The latest-request mailbox replaces pending work. Working frames may advance
during a drag, but never move backwards or cross sources. After release, only
the exact current revision can publish. One drag records one undo entry.
The white-balance picker samples an unadjusted sensor-derived frame in linear
sRGB, rather than starting a separate full-resolution RAW export.

## Full resolution and persistence

100% zoom requests the existing full-quality PhotoCraft render asynchronously.
New edits cancel that owned render process, while the persistent proxy keeps
responding. Full-resolution PNG decoding also runs on the worker; QML receives
an in-memory image so Windows file locks cannot interfere with cleanup.
The native source dimensions drive 100% zoom, rather than the embedded thumbnail's
dimensions. The last working frame stays visible while detail loads.

Reduced sensor development remains an approximation to full development and
downsampling; it is not an export-quality pixel oracle. A comparison of the
tested foliage image at Exposure +0.85 against the downsampled full-quality
render gave mean absolute channel error about 8.4/255. No strict color/pixel
parity claim is made from that test. Native detail and final output retain the
full-quality path. Display conversion for native detail explicitly converts its
ICC profile to sRGB, matching the preview's display target.

Done continues to use PhotoCraft's existing durable recipe/render-manifest Save.
The viewer refreshes its saved preview and invalidates the grid thumbnail's edit
stamp. Proxy images are never used as saved/exported originals. Saved-recipe
changes, source-signature changes and existing projects are checked before requests.
Closing the RAW draft interrupts and joins its private preview worker; it never
terminates the separately hosted PhotoCraft main window.

## Verification

```powershell
python -m pytest tests/test_qml_raw_preview.py tests/test_qml_shell_viewer.py tests/test_qml_shell_editor.py tests/test_qml_only_architecture.py -q
python scripts/check_raw_preview.py 'K:\Photos\sample.NEF' --worker 'K:\Builds\ImageTriage\raw-preview\image-triage-raw-preview.exe' --quality settled --output 'K:\Builds\ImageTriage\raw-preview\timings.json'
cargo clippy --manifest-path 'K:\Builds\ImageTriage\raw-preview\Cargo.toml' --no-deps -- -D warnings
```

Validation on a copied 8256 x 5504 Nikon NEF exercised a real multi-step QML
slider drag, one-entry undo history, drafts across image swaps, native-resolution
zoom, cancellation of a detail render by a new edit, cached white-balance sampling
and undo, Done, saved manifest and grid thumbnail invalidation, reopen, and cleanup.
The focused regression run passed 68 tests; Rust Clippy passed with warnings denied.
The final measured Exposure release-to-frame-swap was approximately 152 ms
(one sample, not a percentile claim). Capture plus PNG encoding was about 796 ms;
that capture cost is not interactive display latency. No QML warnings occurred.

First-new-recipe CPU request timings at the sharper settled size, including pixel
transfer, were approximately: Exposure 90 ms, Shadows/Highlights 160 ms, white
balance 97 ms, curves/mixer/grading 40–54 ms, Texture 295 ms, Clarity 186 ms,
Dehaze 126 ms, sharpening 76 ms, manual noise reduction 81–111 ms, Glow 306 ms.
Repeated identical RAW-stage settings reuse the tuned sensor develop and are
faster. These measurements cover individual recipes on one image, not arbitrary
combinations, hardware or unavailable tools such as AI denoise and lens blur.
