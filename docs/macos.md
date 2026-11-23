# Image Triage on macOS

Status (2026-11-22): built and run on a rented M4 Mac mini (macOS 27.2) with `packaging/build_macos.sh`. On Apple Silicon:

- the disk image installs, the bundle passes `codesign --verify --deep --strict` (signed ad hoc), and the app opens a folder,
  thumbnails and develops a NEF, on the Mac's screen and offscreen;
- the frozen app finds the PhotoCraft editor (through `Contents/MacOS/photocraft`), the RAW preview worker (in `Contents/Resources`)
  and PocketDrop's library, which loads;
- the RAW preview worker runs on Metal (`GPU: Apple M4`): the same steps run on the GPU as on Windows, 14–55 ms per slider change;
- PhotoCraft in its own window: opening a photo hides Image Triage's window and shows the editor's (macOS's own window list says it is
  on screen); putting it away saves the recipe, takes the editor's window off screen, brings Image Triage's back, and keeps the editor
  running; the next photo reuses it. Not yet seen: the red close button raising the request (a person has to click it: scripting the
  click needs the Accessibility permission). On the Mac, with its screen open:
  `open ~/build/test/e2e_click.command`, then click PhotoCraft's close button.

On Intel (the x86_64 build, made on the same M4 under Rosetta and run there under Rosetta, not yet on an Intel Mac): every binary in
the bundle is x86_64 only, it installs, finds the editor, the worker (Metal) and PocketDrop, opens and renders a folder on screen, and
knows it is on an Intel Mac (`darwin-amd64`: no PyTorch, no MediaPipe).

The AI runtime installs from both: the full one on Apple Silicon (PyTorch 2.9.0, MediaPipe, ONNX Runtime 1.27: 1.1 GB, 80 s) and the
compact one on Intel (ONNX Runtime 1.23.2: 429 MB, 47 s), each passing its import check.

What building on a Mac turned up, now in `packaging/build_macos.sh`: pip is told to prefer wheels (rawpy's newest release has no Intel
wheel, and nothing can be compiled under Rosetta); the command line tools' programs are run natively for the Intel build (`otool` and
friends cannot start under Rosetta); the disk image is made with `hdiutil` (cx_Freeze's `bdist_dmg` runs `SetFile`, which cannot
either); and the app's own programs are thinned to the build's architecture and the bundle signed again (cx_Freeze makes them
universal from python.org's Python, and a universal program starts as arm64 on Apple Silicon and cannot load an x86_64 NumPy).


## Which Macs

macOS 14 (Sonoma) or newer, on Apple Silicon and on Intel. PySide6 6.12, which the app requires, ships wheels for macOS 14 and newer
only, on both architectures. On Intel that means the 2018 and later Macs (MacBook Pro and MacBook Air 2018+, Mac mini 2018+, iMac
2019+, iMac Pro, Mac Pro 2019). Older Intel Macs stopped at macOS 13 or earlier; supporting them would take an older PySide6 (6.9
runs on macOS 12), which this project has had crashes with.

There are two builds, one per architecture. One universal app is not possible: most of the wheels the app freezes (NumPy, rawpy, ONNX
Runtime) ship one architecture each.

| | Apple Silicon | Intel |
|---|---|---|
| Culling, grid, RAW editing, export, PocketDrop | yes | yes |
| PhotoCraft (in a window of its own, as on Linux) | yes | yes |
| RAW preview worker | Metal | Metal |
| AI runtime | full (PyTorch 2.9, ONNX Runtime 1.27, MediaPipe) | compact: ONNX Runtime 1.23.2, no PyTorch, no MediaPipe |
| AI ranking, AI masks (PyTorch) | yes (on the CPU: MPS is not used yet) | **no**: PyTorch has no Intel macOS build for Python 3.13 (its last, 2.2, predates it) |
| Eyes-closed check (MediaPipe) | yes | **no**: MediaPipe has no Intel macOS wheel for Python 3.13 |

On an Intel Mac the AI Center installs the compact runtime and counts it as installed (`ai_center.INTEL_MAC_LIMITS` says what it lacks),
and the Cull window turns ranking and the eyes check off with a reason. Those limits come from `AI_RUNTIME_PLATFORMS_WITHOUT_TORCH`
and `AI_RUNTIME_PLATFORM_REQUIREMENT_OVERRIDES` in `image_triage/ai_runtime_packages.py`.

## Building, on a Mac

Once: the Xcode command line tools (`xcode-select --install`), Rust (https://rustup.rs), the python.org Python 3.13 installer (universal2:
the same Python builds both architectures), and a PhotoCraft checkout at the commit in `packaging/photocraft_pin.txt`.

```bash
bash packaging/build_macos.sh --photocraft ../photocraft                 # this Mac's architecture
rustup target add x86_64-apple-darwin                                    # once, on an Apple Silicon Mac
bash packaging/build_macos.sh --photocraft ../photocraft --arch x86_64   # the Intel build, under Rosetta
```

Each makes `dist/ImageTriage-<version>-macos-<arch>.dmg` (and its `.sha256`): the PhotoCraft editor, the RAW preview worker and PocketDrop
(`native/pocketdrop/build_macos.sh`) built for that architecture, then `setup_macos.py bdist_mac bdist_dmg`. In the bundle:

- `Contents/MacOS`: `ImageTriage` and the helper programs (`ai_python_runner`, `ai_runtime_installer`, `image_triage_cleanup`);
- `Contents/Resources`: everything else, with a link from `Contents/MacOS` for each folder (cx_Freeze's layout). The editor is
  `Resources/photocraft/photocraft`, found through that link; the RAW worker is `Resources/image-triage-raw-preview`
  (`qml_shell/raw_preview.executable` looks there on macOS).

## Signing

Without a signing identity the bundle is signed ad hoc, which Apple Silicon needs to run it at all. Gatekeeper then refuses the first
start of a downloaded copy. On macOS 15 and later, Control-click > Open no longer gets past it: start the app once, then allow it under
System Settings > Privacy & Security > Open Anyway. Or remove the download's quarantine flag after copying the app:
`xattr -dr com.apple.quarantine "/Applications/Image Triage.app"`. An ad hoc signed build is fine for testers, not for a public release.

To sign for distribution (an Apple Developer account), set `IMAGE_TRIAGE_CODESIGN_IDENTITY="Developer ID Application: …"`. The bundle is then
signed with the hardened runtime and `packaging/macos.entitlements` (JIT and writable-executable memory for Python and ONNX Runtime;
library validation off, because the AI runtime's wheels are signed by their own developers). Then notarize and staple the disk image:

```bash
xcrun notarytool submit dist/ImageTriage-<version>-macos-<arch>.dmg --keychain-profile <profile> --wait
xcrun stapler staple dist/ImageTriage-<version>-macos-<arch>.dmg
```

## Where things are kept

- Settings: `~/Library/Preferences` (Qt's QSettings for the "Image Triage" organization); data: `~/Library/Application Support/Image Triage`.
- Logs: `~/Library/Logs/ImageTriage` (Console shows them).
- The managed AI runtime and models: `~/.local/share/ImageTriage/AI` and `~/.cache/image_triage_ai_cache` (as on Linux).
- `image_triage_cleanup --mode manual` removes all of it, never a photo or its edits.

## To check on the first Mac build

1. It starts: `"/Applications/Image Triage.app/Contents/MacOS/ImageTriage" --folder ~/Pictures --grab /tmp/grab.png`.
2. The RAW editor uses the worker (the RAW preview's backend says `gpu` on Metal).
3. PhotoCraft opens in its own window and closing it brings Image Triage back (winit can hide windows on macOS, so it is kept hidden
   between photos as on Windows and X11).
4. Set Up AI on each architecture; on Intel, that ranking and the eyes check say they are not available.
5. PocketDrop's library loads (`native/pocketdrop/build_macos.sh` links frameworks from a reading of `src/mac/platform_mac.mm`; the
   list may need a framework added or removed).
6. Drives: the volumes under `/Volumes` (read from `mount`), Reveal selecting the file in Finder.
7. The window's own macOS buttons (red, yellow, green) at the left of the title bar, clear of the logo and menus, and gone with the bar
   in full screen. The window reaches under a title bar with no background (Main.qml's `macTitleBar`; TopBar.qml keeps 74 px for them).
   Seen working without them on screen (a capture leaves out what macOS draws); not yet looked at.

Not done yet: opening photos from Finder (Open With, file associations: the bundle declares no document types, because the app does not
handle macOS's open-file events yet), the macOS menu bar (the app keeps its own menus in the window), and MPS (Apple's GPU) for PyTorch.
