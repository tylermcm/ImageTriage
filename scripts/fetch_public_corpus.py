"""Download a small public photo corpus for profiling (real camera files, not synthetic ones).

    python scripts/fetch_public_corpus.py --out ~/corpus --nef 24 --jpeg 60

* NEF: Nikon RAW samples from https://raw.pixls.us/ (the Raw Samples database; its files are CC0 / public domain).
  Camera bodies: D3 (12 MP) and Z 6 (24 MP), the two the repository's own checks already use.
* JPEG: photographs from Wikimedia Commons' "Quality images of landscapes" category (JPEG; the source is at least
  ``--min-width`` pixels wide). Commons rate-limits its original-file host, so the standard 3840 px-wide rendition
  (``--jpeg-width``, one of the sizes Commons publishes) is fetched, which is still a full-frame ~8-10 MP JPEG. Licences vary per file (mostly CC BY / CC BY-SA, some public domain); each file's licence,
  author and source page are written to ``LICENSES.csv``. They are for local profiling: do not redistribute the corpus.

Layout written under ``--out``:

    raw/*.NEF               the NEF samples as downloaded
    jpeg/*.jpg              the Commons JPEGs
    mixed/                  JPEG and NEF interleaved (hard links where possible, copies otherwise)
    mixed_large/, jpeg_large/   (with ``--replicate N``) the same files linked under N distinct names. Cache keys are
                            per path, so each link is a separate photo to the application; the bytes on disk are not
                            duplicated, and the operating system's file cache is hotter than a real shoot would be.
    LICENSES.csv, manifest.json

Downloads are sequential and throttled (the Wikimedia API rate-limits bursts) and resumable: files already present with the right size
are skipped.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "ImageTriageProfiling/1.0 (https://github.com/tylermcm/ImageTriage; memory profiling corpus fetch)"
PIXLS = "https://raw.pixls.us/data/Nikon"
PIXLS_BODIES = (("D3", "D3"), ("Z 6", "Z6"))
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
COMMONS_CATEGORY = "Category:Quality_images_of_landscapes"


def http(url: str, *, binary: bool = False, retries: int = 4):
    delay = 5.0
    for attempt in range(retries):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                data = response.read()
                return data if binary else data.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as error:
            if error.code in (429, 503) and attempt + 1 < retries:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise RuntimeError(f"unreachable: {url}")


def download(url: str, target: Path, expected: int | None = None) -> bool:
    if target.exists() and (expected is None or target.stat().st_size == expected):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    delay = 20.0
    for attempt in range(5):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=300) as response, partial.open("wb") as handle:
                shutil.copyfileobj(response, handle, 1 << 20)
            partial.replace(target)
            return True
        except urllib.error.HTTPError as error:
            if error.code in (429, 503) and attempt + 1 < 5:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    return False


def fetch_nef(out: Path, per_body: int, manifest: list[dict]) -> None:
    for folder, label in PIXLS_BODIES:
        listing = http(f"{PIXLS}/{urllib.parse.quote(folder)}/")
        names = sorted(set(re.findall(r'href="([^"?/][^"]*\.NEF)"', listing, flags=re.IGNORECASE)))
        for name in names[:per_body]:
            url = f"{PIXLS}/{urllib.parse.quote(folder)}/{urllib.parse.quote(name)}"
            target = out / "raw" / f"{label}_{name}"
            print(f"[nef] {label} {name}", flush=True)
            download(url, target)
            manifest.append({"kind": "nef", "file": str(target.relative_to(out)), "bytes": target.stat().st_size, "source": url,
                             "license": "CC0 (raw.pixls.us Raw Samples)", "author": "raw.pixls.us contributors"})
            time.sleep(0.5)


def fetch_jpeg(out: Path, count: int, min_width: int, rendition_width: int, manifest: list[dict]) -> None:
    got = 0
    cont: dict[str, str] = {}
    while got < count:
        params = {
            "action": "query", "format": "json", "generator": "categorymembers", "gcmtitle": COMMONS_CATEGORY, "gcmtype": "file",
            "gcmlimit": "40", "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": str(rendition_width), "iiextmetadatafilter": "LicenseShortName|Artist",
            **cont,
        }
        reply = json.loads(http(f"{COMMONS_API}?{urllib.parse.urlencode(params)}"))
        pages = sorted(reply.get("query", {}).get("pages", {}).values(), key=lambda page: page.get("index", 0))
        for page in pages:
            info = (page.get("imageinfo") or [{}])[0]
            if info.get("mime") != "image/jpeg" or int(info.get("width", 0)) < min_width or int(info.get("size", 0)) > 40 * 1024 * 1024:
                continue
            title = page["title"].removeprefix("File:")
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", title)[:80]
            if not safe.lower().endswith((".jpg", ".jpeg")):
                safe += ".jpg"
            target = out / "jpeg" / f"{got:03d}_{safe}"
            print(f"[jpeg] {got + 1}/{count} {title} (source {info['width']}x{info['height']})", flush=True)
            try:
                download(info.get("thumburl") or info["url"], target)
            except urllib.error.HTTPError as error:
                print(f"       skipped: HTTP {error.code}", flush=True)
                time.sleep(30)
                continue
            meta = info.get("extmetadata", {})
            manifest.append({
                "kind": "jpeg", "file": str(target.relative_to(out)), "bytes": target.stat().st_size, "source": info.get("descriptionurl", info["url"]),
                "license": meta.get("LicenseShortName", {}).get("value", ""),
                "author": re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip(),
            })
            got += 1
            time.sleep(4.0)
            if got >= count:
                return
        if "continue" not in reply:
            return
        cont = {key: str(value) for key, value in reply["continue"].items()}


def link_or_copy(source: Path, target: Path) -> None:
    if target.exists():
        return
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def build_layout(out: Path, replicate: int) -> dict[str, int]:
    raws = sorted((out / "raw").glob("*.NEF"))
    jpegs = sorted((out / "jpeg").glob("*.jp*g"))
    mixed_sources: list[Path] = []
    for index in range(max(len(raws), len(jpegs))):
        if index < len(jpegs):
            mixed_sources.append(jpegs[index])
        if index < len(raws):
            mixed_sources.append(raws[index])
    counts = {}
    for name, sources in (("mixed", mixed_sources), ("jpeg_set", jpegs)):
        directory = out / name
        directory.mkdir(parents=True, exist_ok=True)
        for source in sources:
            link_or_copy(source, directory / source.name)
        counts[name] = len(sources)
    if replicate > 1:
        for name, sources in (("mixed_large", mixed_sources), ("jpeg_large", jpegs)):
            directory = out / name
            directory.mkdir(parents=True, exist_ok=True)
            total = 0
            for round_number in range(replicate):
                for source in sources:
                    link_or_copy(source, directory / f"r{round_number:02d}_{source.name}")
                    total += 1
            counts[name] = total
    return counts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--nef", type=int, default=24, help="NEF files per camera body (default 24; two bodies)")
    parser.add_argument("--jpeg", type=int, default=60, help="Commons JPEGs (default 60)")
    parser.add_argument("--min-width", type=int, default=4000, help="smallest JPEG width to take (default 4000)")
    parser.add_argument("--jpeg-width", type=int, default=3840, help="Commons rendition width (a published size; default 3840)")
    parser.add_argument("--replicate", type=int, default=0, help="also build *_large folders with every file linked under N distinct names")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    if args.nef:
        fetch_nef(args.out, args.nef, manifest)
    if args.jpeg:
        fetch_jpeg(args.out, args.jpeg, args.min_width, args.jpeg_width, manifest)
    counts = build_layout(args.out, args.replicate)
    (args.out / "manifest.json").write_text(json.dumps({"files": manifest, "folders": counts}, indent=2), encoding="utf-8")
    with (args.out / "LICENSES.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["kind", "file", "bytes", "source", "license", "author"])
        writer.writeheader()
        writer.writerows(manifest)
    total = sum(item["bytes"] for item in manifest)
    print(f"\n{len(manifest)} files, {total / 1e9:.2f} GB; folders: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
