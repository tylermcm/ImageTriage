# Commercial readiness audit

Checked 24 November 2026 against `main` at 3.1.1 (`807ace0`) and the PhotoCraft fork at the pinned commit
(`ec8ad28655f30a8129bba7527b6deedcda3e60bc`). This is a technical review of what each licence and source says, not legal advice:
have a lawyer confirm the red items, and the amber ones you decide to keep, before selling.

It extends `docs/ai_model_licence_audit.md` (17 October), which covers the models that existed then in more depth.

## The short version

Image Triage can be sold as a closed-source app, but not as it ships today. Five things must change first:

1. **Your own licence forbids commercial use.** `LICENSE` is PolyForm Noncommercial 1.0.0, and Help > About > Licences opens it.
   As written it tells every user, free or paying, that they may not use the app for paid work.
2. **Two AI models are non-commercial, and one of them installs for everyone:**
   - the People face recognizer (AdaFace, a core download, trained on WebFace data);
   - TOPIQ in the native culler (PolyForm Noncommercial upstream).
3. **pillow-heif's wheels are GPLv2 as a whole,** because they bundle the x265 encoder, and the installers freeze it in.
4. **Almost no third-party notices ship.** That covers about 720 Rust crates, Qt (LGPL), LibRaw, the models, and the fonts and icons.
   The Mac DMG ships none at all.
5. **The bundled editor is still branded PhotoCraft and ArtCraft,** with upstream's company name on the binary. Their
   trademark terms require a modified fork to remove the ArtCraft marks.

Nothing found is unfixable. Everything in the Rust graph and in PhotoCraft's own code is permissive (MIT, Apache, BSD, ISC,
Zlib, Unicode), and every red item has a permissive replacement or a simple packaging fix.

## Status of the red items (fixed on `claude/commercial-audit`)

| # | Done | Still yours to do |
|---|---|---|
| R1 | `LICENSE` is a draft end-user licence: commercial use allowed, with the LGPL relink and reverse-engineering carve-out. `pyproject.toml` says `LicenseRef-Proprietary`. | Have a lawyer review it, and set the price and seat terms. Copies already released stay under PolyForm. |
| R2 | People uses AuraFace `glintr100` (Apache-2.0, pinned revision and SHA-256) in place of AdaFace. The cache tag changed, so existing People data is re-indexed, and names carry over by face position. | Ask fal in writing about glintr100's provenance (A1). |
| R3 | The native culler loads TOPIQ only when `IMAGE_TRIAGE_AICULLER_TOPIQ` names a file. Setup never installed it, and an old download is no longer picked up. | Take down or mark the `Skulleton12/TOPIQ` mirror non-commercial. |
| R4 | HEIC decoding uses `pi-heif` (decode-only, LGPL libheif and libde265, without x265). | HEVC patents (A7). |
| R5 | `scripts/build_third_party_notices.py` writes `THIRD_PARTY_NOTICES.txt`: models, assets, the Python closure, and PhotoCraft's 720 crates with their licence texts. Every installer regenerates it for its build and ships it with `LICENSE` at the root, and Help > About opens both. A build missing the editor's licences now stops, and the Mac build names the checkout. | Rebuild one MSI and one DMG and check that both files are in them. |
| R6 | In the fork (branch `imagetriage/brand`, commit `008ce9f`, now the pin), the ArtCraft Discord and website links and the Discord button are removed. The editor shows itself as "Image Triage Editor", with "Based on PhotoCraft by the ArtCraft team" in its About, and the exe's CompanyName, ProductName and copyright are yours. In Image Triage, the messages and the button say "the editor" / "Open in Editor", and the website line is updated. | Upstream's kitsune icon (MIT/Apache, so allowed) and internal names such as `photocraft.exe` remain. Swap the icon if you want the editor fully your own. |

## Red: blocks a paid release

| # | What | Evidence | Fix |
|---|---|---|---|
| R1 | Image Triage's own licence is **PolyForm Noncommercial 1.0.0**. It forbids any commercial purpose, which includes a photographer using the app for client work. Help > About > Licences opens this file, and `pyproject.toml` points at it. | `LICENSE`; `qml_shell/reference_model.py:534` | You own the code, so relicense it: a commercial EULA for the app (and keep or drop PolyForm for the public source, see R6). The EULA must allow what the LGPL requires (Y1). |
| R2 | **AdaFace IR-50** face recognizer (People), a **core** download (`CORE_BUNDLES`). The code is MIT, but UniFace says the weights were "trained on WebFace datasets". WebFace260M/4M (and MS1MV2, AdaFace's other option) are licensed for non-commercial research only. | `cascade/rankers/bundles.py:33-38`; `ai_setup_plan.py` `CORE_BUNDLES`; huggingface.co/yakhyo/uniface-weights README (Licenses); AdaFace model zoo | Switch to a recognizer with commercial terms: AuraFace `glintr100` (Apache-2.0, see A1) or SFace (OpenCV Zoo, Apache-2.0). Re-index existing People data. |
| R3 | **TOPIQ** quality model in the native culler. The repo `Skulleton12/TOPIQ` says `license: unknown`; upstream IQA-PyTorch (pyiqa) is **PolyForm Noncommercial 1.0.0**. `aiculler.topiq_onnx` is frozen into every installer. | `ai_manifest.py` `quality_topiq`; `freeze_support.py` `APP_FREEZE_INCLUDES`; github.com/chaofengc/IQA-PyTorch/LICENSE | Remove the native culler's TOPIQ path (the cascade does not need it), or get a licence from the pyiqa authors. Take the mirror down or mark it non-commercial. |
| R4 | **pillow-heif** binary wheels: "License for pillow-heif binary wheels: GPLv2, due to base library licenses". They bundle libx265 (GPLv2) with libheif and libde265 (LGPLv3). `pillow_heif` is frozen into the app. | wheel `LICENSES_bundled.txt`; `freeze_support.py` `APP_FREEZE_INCLUDES` | Use **pi-heif** (the same project's decode-only build, without x265), or decode HEIC through the OS. HEVC patents are a separate question (A7). |
| R5 | **Third-party notices are missing.** <ul><li>Installers carry only PhotoCraft's `LICENSE-APACHE`, `LICENSE-MIT`, `NOTICE` and `ATTRIBUTION.md`, and the **macOS DMG carries none**: `photocraft_bundle_files()` looks two folders above the binary, which on the Mac build is `photocraft-target`, not the checkout, and only warns.</li><li>Nothing covers the ~720 Rust crates (MIT, BSD, ISC, Unicode-3.0, CDLA, IJG), the RAW preview worker, Qt/PySide6 (LGPL-3.0), LibRaw (LGPL-2.1), the LGPL compression modules, MPL certifi/tqdm, MANIQA's adapted code (Apache-2.0), the models (Apache, MIT, BSD, CC-BY-4.0 for RawNIND), or the fonts and icons embedded in the editor.</li></ul> | `freeze_support.py:350-365`; `packaging/build_macos.sh:67-68` | Generate one `THIRD_PARTY_NOTICES` file at build time: cargo-about over both lockfiles, pip-licenses over the frozen packages, and a hand-kept section for models and assets. Ship it in every installer, show it in Help > About, and make a missing file fail the build. |
| R6 | The bundled editor is **branded "PhotoCraft"**, and the fork still shows **ArtCraft** marks. ArtCraft's brand terms say a modified version "must remove the ArtCraft Marks". MIT and Apache grant no trademark rights, and the binary's CompanyName is upstream's "Learning Machines LLC". | `photocraft/docs/brand/LICENSE-brand.txt`; `crates/engine/src/shell_commands.rs:96,98` and `crates/ui-egui/src/links.rs` (ArtCraft Discord and Website links); `apps/photocraft/build.rs:24-26`; Image Triage: "Edit in PhotoCraft" and similar (`Viewer.qml`), `settings_model.py:625` | In the fork, remove the ArtCraft links and set your own CompanyName and ProductName. In Image Triage, rename the editor (for example "Image Triage Editor") with the credit the brand terms allow ("based on PhotoCraft by the ArtCraft team"), or get written permission to keep the name. |

## Amber: fix or decide before selling

| # | What | Notes |
|---|---|---|
| A1 | **AuraFace** bundle (native culler's legacy face pass) | `glintr100` is Apache-2.0, but several companies have asked fal publicly whether it derives from InsightFace's antelopev2, with no answer. `scrfd_10g_bnkps`, `2d106det` and `genderage` match InsightFace's non-commercial files. The `insightface` package isn't in the AI runtime locks, so this path likely can't run, but the code and manifest still offer it. Drop the legacy pass, or keep only `glintr100` after asking fal in writing. |
| A2 | **MANIQA** quality ranker (KonIQ-10k checkpoint) | Code Apache-2.0, adapted with attribution (`_maniqa_net.py`). Its Swin parts come from Microsoft Swin (MIT). The KonIQ-10k dataset is "freely available to the research community", with no commercial grant. Re-hosted by you as `Skulleton12/maniqa-koniq10k`, labelled Apache-2.0. Ask the KonIQ authors, or train the quality head on permissive data. |
| A3 | Models trained on research-only datasets | OneFormer (ADE20K), LaMa Erase (Places365), BiRefNet (DIS5K: its authors ask commercial users to sign an agreement), Real-ESRGAN (DIV2K/Flickr2K), TinyCLIP (YFCC15M). The model licences are permissive, but the training data's terms are a legal grey area. Most commercial apps ship models like these. Get one legal opinion that covers them all. |
| A4 | **OpenAI CLIP** sidecar | MIT, but the model card says any deployed use "is currently out of scope". It's optional; drop it. |
| A5 | **RawNIND** (AI Denoise) | Used under CC-BY-4.0, which requires attribution in the app and the notices (B. Brummer, C. De Vleeschouwer). The training data is CC BY-SA. The earlier audit suggests asking the author to confirm in writing. |
| A6 | **Qt / PySide6 under LGPL-3.0** | Allowed in closed-source apps:<ul><li>keep Qt as separate libraries (cx_Freeze does);</li><li>ship the LGPL text and Qt notices, and say where the Qt source is;</li><li>let users replace the Qt libraries;</li><li>the EULA must not forbid the reverse engineering needed to debug such changes.</li></ul>Every QML module the app imports (QtQuick, Controls, Shapes, Effects, Dialogs, Window, Qt.labs.platform) is LGPL-available. Check a built MSI and DMG for unused GPL-only Qt modules (Charts, Quick3D, VirtualKeyboard, ShaderTools) and exclude them. A Qt commercial licence is the alternative. |
| A7 | **HEIC/HEVC patents** | Decoding HEVC in a paid product can carry patent-pool exposure (Access Advance, Via LA). Decoding through the OS (Windows HEVC extension, macOS) avoids shipping a decoder. PhotoCraft's HEIF feature is off in every build; keep it off. |
| A8 | **PocketDrop uses Cloudflare Quick Tunnels** (trycloudflare.com) for internet sharing | Cloudflare intends Quick Tunnels for testing and development, with no uptime promise and rate limits. A paid feature built on them can break or be blocked. Use a named tunnel on your own Cloudflare account, or a relay you run. |
| A9 | **The source is public** | ImageTriage, CLI-Culler (aiculler, bundled), PocketDrop and the PhotoCraft fork are public on GitHub. Under PolyForm Noncommercial anyone can build a free copy for non-commercial use, which competes with a paid app. Make the repos private before charging, or decide deliberately on open-core. |
| A10 | **Code signing** | Mac builds are signed ad hoc (Gatekeeper asks users to "Open Anyway"); a paid Mac app needs a Developer ID ($99/year) and notarization. `build_macos.sh` already supports it. The Windows workflow does not sign the MSI, so SmartScreen will warn: get a code-signing certificate, or use Azure Trusted Signing. |
| A11 | Adobe names in the UI | A few messages say "Camera Raw" (`editor_host.py:535,577,633,1128`), and the website and FAQ mention Lightroom and Photoshop. Naming other products for compatibility is normal, but call your own tools something else. |
| A12 | Face recognition and privacy law | People recognition runs on the user's computer and nothing is sent to you. Even so, a paid product with face recognition should say so plainly in a privacy policy (GDPR special-category data; Illinois BIPA). |

## Green: verified fine

- **PhotoCraft's own code** is MIT OR Apache-2.0. There is no copyleft anywhere in the ~720 shipped Rust crates (two offer
  GPL or LGPL as one option; choose the permissive one). There is no ffmpeg, libraw, x264/x265, libheif or OpenCV in the Rust
  graph; the decoders are pure Rust. PhotoCraft's "ported/derived" comments point to clean-room work, same-licence sister projects
  or libfacedetection (BSD-3, credited). Its embedded assets are permissive and listed in its ATTRIBUTION.md. Their licence texts
  still have to ship (R5). Separately, egui's embedded fonts (Hack, Noto Emoji, Ubuntu) and the IJG sentence for `jpeg-encoder`
  are credited nowhere; add both to the notices.
- **Python packages** frozen into the app are permissive (MIT, BSD, Apache, PSF, ISC, MIT-CMU), apart from those under R4 and A6:
  LibRaw (LGPL-2.1, loaded as a separate DLL), py7zr and its helpers (LGPL-2.1+, separate modules), and certifi and tqdm
  (MPL-2.0, unmodified). These need notices only.
- **The AI runtime** (PyTorch, NVIDIA CUDA, MediaPipe, OpenCV and others) is downloaded from PyPI and pytorch.org on the
  user's machine. Bundling is off by default (`IMAGE_TRIAGE_BUNDLE_AI_RUNTIME_SITE_PACKAGES`), so you don't redistribute it.
- **Models with clean terms:** SAM 2.1 tiny (Apache-2.0), Depth Anything V2 Small (Apache-2.0; never swap in Base or Large,
  which are CC-BY-NC), MediaPipe Face Landmarker (Apache-2.0), Q-ReAlign-Mini (Apache-2.0, on Qwen), Real-ESRGAN (BSD-3).
- **Your own code:** PocketDrop's QR, deflate and SHA-256 code is its own. The only adapted code in the app is MANIQA's network,
  which is credited with its changes stated. The bundled skies are CC0 (Poly Haven) with credits. The QML icons follow Lucide's
  geometry (ISC: include its notice).
- **No telemetry:** the app sends nothing about its users anywhere.

## Re-hosting you do

You redistribute some files yourself, so their licence and attribution travel with you:

- the `Skulleton12` Hugging Face mirrors (MANIQA, MediaPipe Face Landmarker, TOPIQ);
- the `editor-models-1` GitHub release (RawNIND ONNX, Real-ESRGAN ONNX).

Put each upstream licence and credit in the mirror's README. Mark TOPIQ non-commercial or remove it.

## To sell it, beyond the licences

- A **EULA** in the installer and on the site, with a **privacy policy** and a refund policy.
- **Licensing:** keys or accounts, plus a payment provider that handles sales tax and VAT as the merchant of record (for example
  Paddle or Lemon Squeezy). There is no licence or trial code in the app today.
- **Signed builds** on both platforms (A10).
- **The website** currently says "Free" (branch `claude/site-refresh`, not yet merged). Change it before it goes live if you
  charge. Copies already released stay usable under the licence they shipped with, so decide before the next public release.

## Suggested order

1. Relicense (R1) and make the repos private, or decide on open-core (A9).
2. Swap AdaFace and remove TOPIQ (R2, R3); switch pillow-heif to pi-heif (R4).
3. Build the notices file and fix the Mac lookup (R5); rebrand the editor and clean the fork (R6).
4. Decide the amber models (A1-A5); get one legal opinion on the training-data question (A3).
5. Signing, EULA, privacy policy, payments.

Working files from this audit (licence tables for the 720 crates and the Python packages) were left in the session's scratchpad;
regenerate them with `cargo metadata` and `pip install --dry-run --report` when building the notices file.
