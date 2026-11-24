"""Write THIRD_PARTY_NOTICES.txt: the licences and credits of everything Image Triage ships or downloads that is not its own.

    python scripts/build_third_party_notices.py --photocraft ../photocraft [--out THIRD_PARTY_NOTICES.txt]

It covers, in order:

* the AI models the app downloads on request, and the assets it bundles (a hand-kept table below: models are not packages);
* the Python packages frozen into the app: the closure of ``pyproject.toml``'s dependencies as installed in this interpreter (run it with
  the interpreter that builds the installer), each with the licence files its distribution ships;
* the PhotoCraft editor: its NOTICE, ATTRIBUTION and licence texts, the licence files its NOTICE refers to, and the fonts egui embeds;
* the Rust crates in the editor and the RAW preview worker (``cargo metadata`` of the PhotoCraft checkout, every normal dependency reachable
  from the ``photocraft`` package and the crates the worker links), each with the licence files in its source.

Identical licence texts are written once, after the list of the components they cover. The installers regenerate this file
(``freeze_support.third_party_notices_file``); the copy in the repository is what a checkout's Help > About opens.
"""
from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
import re
import subprocess
import sys
import tomllib
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULE = "=" * 100
THIN = "-" * 100

# Crates the RAW preview worker links besides those `photocraft` reaches (native/raw_preview/Cargo.toml.in).
WORKER_ROOTS = ("photocraft-io", "photocraft-codecs", "photocraft-raw", "photocraft-algo", "photocraft-cms", "photocraft-doc", "photocraft-gpu",
                "serde_json", "pollster", "rayon")
LICENCE_FILE = re.compile(r"^(licen[cs]e|copying|notice|unlicense|copyright|authors)([-._].*)?$", re.I)

MODELS = (
    ("People face recognizer: AuraFace glintr100", "https://huggingface.co/fal/AuraFace-v1", "Apache-2.0", "AuraFace by fal.ai."),
    ("Eyes-closed check and face landmarks: MediaPipe Face Landmarker", "https://ai.google.dev/edge/mediapipe", "Apache-2.0", "Google LLC."),
    ("Quality ranker: MANIQA (KonIQ-10k checkpoint)", "https://github.com/IIGROUP/MANIQA", "Apache-2.0",
     "Yang et al., MANIQA: Multi-dimension Attention Network for No-Reference Image Quality Assessment. image_triage/cascade/rankers/"
     "_maniqa_net.py is adapted from its models/maniqa.py and models/swin.py (Swin Transformer, Microsoft, MIT); the changes are listed there."),
    ("Appeal ranker: Q-ReAlign-Mini-0.8B", "https://huggingface.co/q-future/Q-ReAlign-Mini-0.8B", "Apache-2.0", "Q-Future; based on Qwen."),
    ("Semantic search: TinyCLIP ViT-8M/16 (ONNX)", "https://huggingface.co/onnx-community/TinyCLIP-ViT-8M-16-Text-3M-YFCC15M-ONNX", "MIT",
     "Microsoft (TinyCLIP, microsoft/Cream)."),
    ("Subject masks: BiRefNet", "https://huggingface.co/ZhengPeng7/BiRefNet", "MIT", "Peng Zheng et al."),
    ("Click selection: SAM 2.1 Hiera-Tiny", "https://huggingface.co/facebook/sam2.1-hiera-tiny", "Apache-2.0", "Meta Platforms, Inc."),
    ("Depth: Depth Anything V2 Small", "https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf", "Apache-2.0", "Lihe Yang et al."),
    ("Scene regions: OneFormer ADE20K Swin-T", "https://huggingface.co/shi-labs/oneformer_ade20k_swin_tiny", "MIT", "SHI Labs."),
    ("AI Erase: LaMa (big-lama, ONNX)", "https://huggingface.co/Carve/LaMa-ONNX", "Apache-2.0", "Suvorov et al., Samsung AI Center (advimman/lama)."),
    ("AI Denoise: RawNIND UtNet2 Bayer", "https://github.com/trougnouf/rawnind_jddc", "CC-BY-4.0",
     "B. Brummer and C. De Vleeschouwer, \"Learning Joint Denoising, Demosaicing, and Compression from the Raw Natural Image Noise "
     "Dataset\". Weights used under CC-BY-4.0 (the authors dual-license them GPL-3.0 and CC-BY-4.0); converted to ONNX for Image Triage."),
    ("Super Resolution: Real-ESRGAN x2plus and x4plus", "https://github.com/xinntao/Real-ESRGAN", "BSD-3-Clause",
     "Copyright (c) 2021, Xintao Wang. Converted to ONNX for Image Triage."),
)

ASSETS = (
    ("Sky replacement pictures (image_triage/assets/skies)", "CC0-1.0",
     "Cropped from Poly Haven HDRIs (https://polyhaven.com); the artists are credited in assets/skies/CREDITS.md."),
    ("Interface icons (QML)", "ISC", "Drawn on the geometry of Lucide (https://lucide.dev), ISC License, Copyright (c) for portions of "
     "Lucide are held by Cole Bemis 2013-2022 as part of Feather (MIT); all other copyright (c) for Lucide are held by Lucide Contributors."),
)

# What the LGPL components need people to know (PySide6, Qt, LibRaw, libheif, libde265, and the LGPL compression modules).
LGPL_NOTE = """Several components are licensed under the GNU Lesser General Public License: Qt and PySide6 (LGPL-3.0), LibRaw in rawpy
(LGPL-2.1, also offered under CDDL-1.0), libheif and libde265 in pi-heif (LGPL-3.0), and py7zr, pybcj, pyppmd, inflate64 and
multivolumefile (LGPL-2.1 or later). They are shipped as separate libraries and modules, unmodified, which you may replace with your own
builds; the licence agreement allows the reverse engineering needed to debug such changes. Their source code is available from
https://download.qt.io/official_releases/qt/, https://code.qt.io/cgit/pyside/pyside-setup.git, https://github.com/LibRaw/LibRaw,
https://github.com/strukturag/libheif, https://github.com/strukturag/libde265 and each Python package's page on https://pypi.org.
On request we will send the source of any of them for three years from the release that included it."""

IJG = "The PhotoCraft editor's JPEG encoder is based in part on the work of the Independent JPEG Group."


def _key(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


# ---- Python ----------------------------------------------------------------------------------------------------------------------------
def python_closure() -> list[metadata.Distribution]:
    from packaging.markers import Marker
    from packaging.requirements import Requirement

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    todo, seen, missing = [Requirement(r).name for r in project["dependencies"]], OrderedDict(), []
    while todo:
        name = todo.pop(0)
        if _key(name) in seen:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            missing.append(name)
            seen[_key(name)] = None
            continue
        seen[_key(name)] = dist
        for text in dist.requires or ():
            requirement = Requirement(text)
            if requirement.marker is not None and not requirement.marker.evaluate({"extra": ""}):
                continue
            todo.append(requirement.name)
    if missing:
        raise SystemExit(f"Not installed in {sys.executable}, so their licences cannot be read: {', '.join(missing)}")
    return sorted((d for d in seen.values() if d is not None), key=lambda d: _key(d.metadata["Name"]))


def python_licence(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    expression = (meta.get("License-Expression") or "").strip()
    if expression:
        return expression
    text = (meta.get("License") or "").strip().splitlines()
    if text and len(text[0]) < 80:
        return text[0]
    classifiers = [c.split(" :: ")[-1] for c in meta.get_all("Classifier") or () if c.startswith("License ::")]
    return "; ".join(classifiers) or "see the licence text below"


def python_texts(dist: metadata.Distribution) -> list[tuple[str, str]]:
    texts = []
    for file in dist.files or ():
        name = Path(str(file)).name
        if ".dist-info" in str(file) and (LICENCE_FILE.match(name) or "/licenses/" in str(file).replace("\\", "/")) and name != "RECORD":
            try:
                texts.append((str(file), dist.locate_file(file).read_text(encoding="utf-8", errors="replace")))
            except (OSError, IsADirectoryError):
                continue
    if not texts:
        body = (dist.metadata.get("License") or "").strip()
        if len(body) > 200:
            texts.append(("METADATA License field", body))
    return texts


# ---- Rust ------------------------------------------------------------------------------------------------------------------------------
def cargo_packages(photocraft: Path) -> list[dict]:
    command = ["cargo", "metadata", "--format-version", "1", "--locked"]
    result = subprocess.run(command, cwd=photocraft, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"cargo metadata failed in {photocraft}:\n{result.stderr[-2000:]}")
    data = json.loads(result.stdout)
    packages = {p["id"]: p for p in data["packages"]}
    nodes = {n["id"]: n for n in data["resolve"]["nodes"]}
    by_name = {}
    for package in data["packages"]:
        by_name.setdefault(package["name"], []).append(package["id"])
    roots = [pid for name in ("photocraft", *WORKER_ROOTS) for pid in by_name.get(name, ())]
    if not any(packages[pid]["name"] == "photocraft" for pid in roots):
        raise SystemExit(f"{photocraft} has no photocraft package")
    reached, todo = set(), list(roots)
    while todo:
        pid = todo.pop()
        if pid in reached:
            continue
        reached.add(pid)
        for dep in nodes[pid]["deps"]:
            if any(kind.get("kind") in (None, "normal") for kind in dep["dep_kinds"]):
                todo.append(dep["pkg"])
    workspace = set(data["workspace_members"])
    return sorted((packages[pid] for pid in reached if pid not in workspace and packages[pid]["source"]), key=lambda p: (p["name"], p["version"]))


def _licence_files(folder: Path) -> list[tuple[str, str]]:
    return [(path.name, path.read_text(encoding="utf-8", errors="replace"))
            for path in sorted(folder.iterdir()) if path.is_file() and LICENCE_FILE.match(path.name)]


def crate_texts(package: dict, fallback: dict[str, str]) -> list[tuple[str, str]]:
    folder = Path(package["manifest_path"]).parent
    texts = _licence_files(folder)
    if not texts and str(package.get("source") or "").startswith("git+"):
        # A crate of a git repository (a workspace member) often keeps its licence at the repository's root.
        for parent in folder.parents:
            texts = _licence_files(parent)
            if texts or (parent / ".git").exists() or parent.name.startswith(("checkouts", "git")):
                break
    if package.get("license_file"):
        extra = (folder / package["license_file"]).resolve()
        if extra.is_file() and all(name != extra.name for name, _ in texts):
            texts.append((extra.name, extra.read_text(encoding="utf-8", errors="replace")))
    if not texts:
        # The crate ships no licence file: the standard text of a licence it names, with its authors as the copyright holders.
        expression = package.get("license") or ""
        for spdx in ("MIT", "Apache-2.0"):
            if re.search(rf"\b{re.escape(spdx)}\b", expression) and spdx in fallback:
                authors = ", ".join(package.get("authors") or ()) or f"the {package['name']} authors"
                # The standard text, under the crate's own authors (PhotoCraft's copy names PhotoCraft's)
                standard = re.sub(r"^Copyright \(c\).*$", f"Copyright (c) {authors}", fallback[spdx], count=1, flags=re.M)
                if standard == fallback[spdx] and spdx == "MIT":
                    standard = f"Copyright (c) {authors}\n\n{standard}"
                texts.append((f"{spdx} (standard text)", standard))
                break
    return texts


def egui_font_texts(packages: list[dict]) -> list[tuple[str, str]]:
    found = [p for p in packages if p["name"] == "epaint_default_fonts"]
    texts = []
    for package in found:
        fonts = Path(package["manifest_path"]).parent / "fonts"
        for path in sorted(fonts.glob("*.txt")) if fonts.is_dir() else ():
            texts.append((f"egui default font licence: {path.name}", path.read_text(encoding="utf-8", errors="replace")))
    return texts


# ---- writing ---------------------------------------------------------------------------------------------------------------------------
def grouped(entries: list[tuple[str, list[tuple[str, str]]]]) -> list[tuple[list[str], str, str]]:
    """``[(components, file name, text)]`` with each distinct text once, in first-seen order."""
    order: OrderedDict[str, tuple[list[str], str]] = OrderedDict()
    for component, texts in entries:
        for name, text in texts:
            normal = re.sub(r"\s+", " ", text).strip()
            if normal not in order:
                order[normal] = ([], name)
            if component not in order[normal][0]:
                order[normal][0].append(component)
    return [(components, name, next(t for c, ts in entries for n, t in ts if re.sub(r"\s+", " ", t).strip() == normal))
            for normal, (components, name) in order.items()]


def build(photocraft: Path) -> str:
    out: list[str] = []
    w = out.append
    w("THIRD-PARTY NOTICES FOR IMAGE TRIAGE")
    w(RULE)
    w("Image Triage is licensed under the agreement in LICENSE. It includes, and downloads at your request, software, models and")
    w("assets made by others, which are licensed to you under their own terms, listed below. Nothing in Image Triage's licence")
    w("limits the rights these licences give you.")
    w("")
    w(LGPL_NOTE)
    w("")
    w(IJG)
    w("")

    w(RULE)
    w("AI MODELS (downloaded only when you install them in the AI Center; they are not part of the installer)")
    w(RULE)
    for name, source, licence, credit in MODELS:
        w(f"{name}\n  Source: {source}\n  Licence: {licence}\n  Credit: {credit}\n")
    w(RULE)
    w("BUNDLED ASSETS")
    w(RULE)
    for name, licence, credit in ASSETS:
        w(f"{name}\n  Licence: {licence}\n  {credit}\n")

    dists = python_closure()
    w(RULE)
    w(f"PYTHON COMPONENTS ({len(dists)}, frozen into the application)")
    w(RULE)
    for dist in dists:
        w(f"{dist.metadata['Name']} {dist.version}: {python_licence(dist)}")
    w("")
    for components, name, text in grouped([(f"{d.metadata['Name']} {d.version}", python_texts(d)) for d in dists]):
        w(THIN)
        w(f"{name}, for: {', '.join(components)}")
        w(THIN)
        w(text.rstrip())
        w("")

    w(RULE)
    w("THE PHOTOCRAFT EDITOR")
    w(RULE)
    w("The image editor bundled with Image Triage is a modified version of PhotoCraft (https://github.com/storytold/photocraft),")
    w("by the ArtCraft Team and the PhotoCraft contributors, used under the MIT License. ArtCraft and its marks are trademarks of")
    w("the ArtCraft Team; Image Triage is not an ArtCraft product.")
    w("")
    for name in ("NOTICE", "ATTRIBUTION.md", "LICENSE-MIT", "LICENSE-APACHE"):
        path = photocraft / name
        if not path.is_file():
            raise SystemExit(f"{path} is missing")
        w(THIN)
        w(f"PhotoCraft {name}")
        w(THIN)
        w(path.read_text(encoding="utf-8").rstrip())
        w("")
    for path in sorted({*photocraft.glob("assets/fonts/OFL-*.txt"), *photocraft.glob("assets/icons/LICENSE-*.txt"),
                        *photocraft.glob("assets/dict/LICENSE-*.txt"), *photocraft.glob("crates/algo/data/LICENSE-*.txt")}):
        w(THIN)
        w(f"PhotoCraft {path.relative_to(photocraft).as_posix()}")
        w(THIN)
        w(path.read_text(encoding="utf-8", errors="replace").rstrip())
        w("")

    packages = cargo_packages(photocraft)
    fallback = {"MIT": (photocraft / "LICENSE-MIT").read_text(encoding="utf-8"), "Apache-2.0": (photocraft / "LICENSE-APACHE").read_text(encoding="utf-8")}
    for name, text in egui_font_texts(packages):
        w(THIN)
        w(name)
        w(THIN)
        w(text.rstrip())
        w("")
    w(RULE)
    w(f"RUST CRATES ({len(packages)}, in the PhotoCraft editor and the RAW preview worker)")
    w(RULE)
    w("Where a crate offers a choice of licences, it is used under the permissive one (MIT or Apache-2.0, never GPL or LGPL).")
    w("")
    for package in packages:
        w(f"{package['name']} {package['version']}: {package.get('license') or 'see the licence text below'}")
    w("")
    for components, name, text in grouped([(f"{p['name']} {p['version']}", crate_texts(p, fallback)) for p in packages]):
        w(THIN)
        w(f"{name}, for: {', '.join(components)}")
        w(THIN)
        w(text.rstrip())
        w("")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--photocraft", type=Path, required=True, help="the PhotoCraft checkout the installer's editor is built from")
    parser.add_argument("--out", type=Path, default=ROOT / "THIRD_PARTY_NOTICES.txt")
    args = parser.parse_args(argv)
    text = build(args.photocraft.resolve())
    args.out.write_text(text, encoding="utf-8")
    print(f"Wrote {args.out} ({len(text) // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
