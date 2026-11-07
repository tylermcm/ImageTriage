# Standalone RAW preview spike

This is the original isolated experiment. The integrated worker is documented in
[`native/raw_preview/README.md`](../../../native/raw_preview/README.md). Its sensor
cache, RAW-stage processing and timing results supersede this spike's simplified
Exposure/Shadows experiment for application use.

This experiment is separate from the application launch path. A small QML
window drives a persistent Rust subprocess linked directly to PhotoCraft's
`io`, `algo`, `cms`, and `gpu` libraries. No PhotoCraft desktop window, Qt
Widgets, `.pcraft` project, recipe save, or source-folder output is involved.
Neither repository's application code is changed by building/running it.

## Build and run

From a configured Windows Rust/MSVC shell with the PhotoCraft toolchain:

```powershell
.\scripts\spikes\raw_preview\build.ps1 -PhotoCraftRoot '<companion checkout>'
.\scripts\spikes\raw_preview\run.ps1 -RawFile 'K:\Photos\sample.NEF'
```

Pass `-HostPython` to `run.ps1` if the normal Image Triage development Python
with PySide6 is elsewhere. `-Edge` selects 900, 1200 (default), 1800 or 2400.
PhotoCraft's reduced sensor developer chooses a supported reduction factor, so
the actual edge can differ from the requested one; the footer reports its size.

The manifest and Cargo lock are generated under the K: build directory. The
existing hosted executable is preserved. Only the spike executable is built.
Close the experiment to end its backend session. Results and stderr go under
`K:\Builds\ImageTriage\raw-preview-spike`; RAW paths are never written into
the benchmark or presentation JSON.

## What it proves

- Opens a sensor-derived reduced RAW develop, explicitly refusing an embedded
  JPEG fallback. Keeps its float ProPhoto-compatible pixels and tone curve in
  memory for repeated Exposure/Shadows edits. It does **not** yet retain a full
  decoded sensor for native-resolution refinement.
- Reuses PhotoCraft's `raw::retone`, `camera_raw::develop`, `RgbHistogram`, CMS,
  and `CameraRawDevelop` GPU pass. Shadows runs in the CPU RAW-tone stage;
  remaining supported per-pixel operations may run on hardware GPU.
- Sends a JSON header plus binary RGBA8 bytes over private stdio. No PNG
  encoding or temporary preview image is needed.
- Uses a latest-request mailbox, revision checks and a 60 ms live-drag dispatch
  throttle. The image provider and histogram publish one completed revision.
- Records input-to-first-frame-swap timing after QML reports the image ready.
  This is a presentation proxy, not a physical display scanout measurement.
- Invalid/truncated frames are rejected. Closing joins the worker and kills
  only its owned subprocess if a native operation fails to finish promptly.

The display target is explicitly sRGB, using PhotoCraft's relative-colorimetric
CMS transform with black point compensation. Monitor ICC selection, detailed
parity with the existing editor/export path, and raw-stage Exposure equivalence
still need acceptance work. This is not a replacement export implementation.

## First measured results (2026-11-06)

One 8256 x 5504 Nikon NEF on the development desktop, optimized dev dependencies,
1376 x 916 working preview, six warm requests per combination:

| Recipe | CPU median round trip | GPU median round trip |
| --- | ---: | ---: |
| Exposure | 22.8 ms | 35.0 ms |
| Exposure and Shadows +40 | 74.7 ms | 95.9 ms |

GPU: RTX 4080 SUPER. GPU timings include readback and CPU display conversion;
Shadows still runs on CPU. GPU is not faster for this workload/preview size.
The compared Exposure +0.70 outputs differed by at most one 8-bit code value
for both Shadows cases. This is a limited parity check, not all-filter parity.
Opening plus GPU initialization took 1.53 seconds. All requests reported one
source open; neutral restoration was byte-identical to the first frame.

A QML smoke test exercises actual Exposure and Shadows slider drags, then a
burst of 50 updates. Only the final burst revision is published, with one source
open. The initial CPU-only Exposure drag was 79 ms to frame swap. Later mixed
CPU/GPU timings are in `ui-smoke-final/presentation.jsonl`; no percentile claim
is made from these few UI samples.

The DX12 spike must select FXC explicitly, matching PhotoCraft's safe fallback
when no DXC is shipped beside its executable. The default compiler choice found
an incompatible DXC through the environment and failed pipeline construction;
that run fell back to CPU and is not GPU evidence.

## Checks

```powershell
python -m pytest scripts/spikes/raw_preview/test_spike.py -q
python scripts/spikes/raw_preview/benchmark.py 'K:\Photos\sample.NEF' --backend '<spike.exe>' --output 'K:\Builds\ImageTriage\raw-preview-spike\results'
python scripts/spikes/raw_preview/spike.py 'K:\Photos\sample.NEF' --backend '<spike.exe>' --output 'K:\Builds\ImageTriage\raw-preview-spike\ui-smoke' --smoke
cargo clippy --manifest-path 'K:\Builds\ImageTriage\raw-preview-spike\Cargo.toml' --no-deps -- -D warnings
```

Before production integration: verify color/raw-stage recipe equivalence,
source-signature invalidation and switching during work; add sharper settled
preview and actual native-resolution zoom; test the complete recipe schema,
one undo entry per drag, memory bounds and backend failure recovery. Save/grid
propagation must continue through the validated durable manifest path. The
PhotoCraft main-window session remains separate.
