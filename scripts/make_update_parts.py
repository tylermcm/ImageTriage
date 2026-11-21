"""Make a release's small-update assets from the frozen build the MSI was made from.

    py scripts/make_update_parts.py <frozen build folder> <version> <out folder> [previous release's -update.json]

Writes ``ImageTriage-<version>-update.json`` and the zips of the groups that changed since the previous release's manifest (the
others keep the previous release's download address) into <out folder>. Upload all of them as release assets of v<version>.
See image_triage/update_patch.py.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from image_triage.update_patch import publishable_manifest  # noqa: E402

REPO = "tylermcm/ImageTriage"


def load_previous(source: str) -> dict | None:
    if not source:
        return None
    if source.startswith("https://"):
        with urllib.request.urlopen(urllib.request.Request(source, headers={"User-Agent": "image-triage-release"})) as response:
            return json.load(response)
    return json.loads(Path(source).read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    frozen, version, out = Path(argv[0]), argv[1], Path(argv[2])
    previous = load_previous(argv[3] if len(argv) > 3 else "")
    base = f"https://github.com/{REPO}/releases/download/v{version}"
    manifest, uploads = publishable_manifest(frozen, version, out, download_base=base, previous=previous)
    target = out / f"ImageTriage-{version}-update.json"
    target.write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
    reused = [name for name, group in manifest["groups"].items() if not group["url"].startswith(base)]
    print(f"manifest: {target}")
    for path in uploads:
        print(f"upload: {path.name} {path.stat().st_size / 1e6:.1f} MB")
    print(f"reused from earlier releases: {', '.join(sorted(reused)) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
