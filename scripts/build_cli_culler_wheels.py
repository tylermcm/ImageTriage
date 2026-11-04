"""Build unchanged, revision-pinned engine wheels, outside the host environment.

Requires Git and uv on the build machine only. The wheel metadata is not rewritten.
The engine's branch-based bracketeering requirement must be overridden with the
built bracketeering wheel when resolving the isolated runtime dependency lock.
This does not install either package into ImageTriage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def build(output: Path) -> None:
    pins = json.loads((ROOT / "packaging/cli_culler_pins.json").read_text())
    output.mkdir(parents=True, exist_ok=True)
    artifacts = {}
    with tempfile.TemporaryDirectory(prefix="image-triage-culler-build-") as directory:
        for name, pin in pins.items():
            source = Path(directory) / name
            source.mkdir()
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            subprocess.run(["git", "-C", str(source), "fetch", "--depth=1",
                            pin["url"], pin["commit"]], check=True)
            subprocess.run(["git", "-C", str(source), "checkout", "--detach", "-q",
                            "FETCH_HEAD"], check=True)
            actual = subprocess.check_output(
                ["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
            if actual != pin["commit"]:
                raise RuntimeError(f"Unexpected revision for {name}: {actual}")
            # Build each package into a fresh directory; never select a stale wheel.
            wheel_dir = Path(directory) / (name + "-wheel")
            subprocess.run(["uv", "--system-certs", "build", "--wheel",
                            "--out-dir", str(wheel_dir), str(source)], check=True)
            wheels = list(wheel_dir.glob("*.whl"))
            if len(wheels) != 1:
                raise RuntimeError(f"Expected exactly one wheel for {name}")
            wheel = wheels[0]
            data = wheel.read_bytes()
            (output / wheel.name).write_bytes(data)
            artifacts[name] = {**pin, "wheel": wheel.name,
                               "sha256": hashlib.sha256(data).hexdigest()}
    (output / "provenance.json").write_text(
        json.dumps(artifacts, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    build(parser.parse_args().out_dir.resolve())
