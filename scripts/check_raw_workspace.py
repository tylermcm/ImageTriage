r"""Opt-in live check of ``photocraft --raw-workspace`` (Camera Raw first).

    python scripts/check_raw_workspace.py --executable C:\path\photocraft.exe --image C:\photos\a.jpg --output C:\temp\rw

Launches the editor on one photo in the Camera Raw workspace, waits for Camera Raw to open by itself
inside the main window, adjusts it, commits, and checks that the ordinary editor is left on the same,
still-open document. Screenshots (the editor's own renders) go to the output folder.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from image_triage import photocraft_bridge as bridge  # noqa: E402


def wait_for(check, what, timeout=60.0, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for {what}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    image = output / Path(args.image).name
    shutil.copyfile(args.image, image)
    write_root = output / ".out"
    write_root.mkdir(exist_ok=True)

    port = bridge._free_tcp_port()
    token = secrets.token_hex(32)
    fd, token_path = tempfile.mkstemp(prefix="photocraft_token_", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token)
    env = dict(os.environ)
    env.setdefault("WGPU_BACKEND", "dx12")
    started = time.perf_counter()
    process = subprocess.Popen(
        [args.executable, "--raw-workspace", "--control", str(port), "--control-token-file", token_path,
         "--automation-read-root", str(output), "--automation-write-root", str(write_root), str(image)],
        env=env,
    )
    results: dict = {}
    control = None
    try:
        control = wait_for(lambda: _connect(port, token), "the control channel")
        results["control_ready_ms"] = round((time.perf_counter() - started) * 1000)

        def camera_raw_open():
            return control.call("ui.viewport").get("cameraRaw")

        wait_for(lambda: len(control.execute("session.inspect").get("documents", [])) == 1, "the photo to open")
        results["document_open_ms"] = round((time.perf_counter() - started) * 1000)
        opened = wait_for(camera_raw_open, "Camera Raw to open by itself")
        results["camera_raw_open_ms"] = round((time.perf_counter() - started) * 1000)
        assert opened.get("open") is True
        view = wait_for(lambda: (camera_raw_open() or {}).get("view"), "Camera Raw to lay out its view")
        window = control.call("ui.viewport")["window"]
        results["window"] = window
        results["camera_raw_view"] = view
        assert view["width"] > 200 and view["height"] > 200, "Camera Raw's view is implausibly small"
        assert view["x"] + view["width"] <= window["width"] + 1 and view["y"] + view["height"] <= window["height"] + 1
        control.call("ui.screenshot", {"path": "1-camera-raw-open.png", "focus": False})

        control.call("ui.cameraRaw", {"set": {"exposure": 1.0}})
        control.call("ui.screenshot", {"path": "2-adjusted.png", "focus": False})
        control.call("ui.cameraRaw", {"commit": True})
        wait_for(lambda: control.call("ui.viewport").get("cameraRaw") is None, "Camera Raw to close")
        time.sleep(0.5)
        documents = control.execute("session.inspect").get("documents", [])
        assert len(documents) == 1, f"the document did not stay open: {documents}"
        results["documents_after_commit"] = len(documents)
        control.call("ui.screenshot", {"path": "3-full-editor.png", "focus": False})
        time.sleep(1.0)
        # Closing Camera Raw must not reopen it on the same document.
        assert control.call("ui.viewport").get("cameraRaw") is None, "Camera Raw reopened after being closed"
        results["ok"] = True
    finally:
        try:
            if control is not None:
                control.call("app.quit")
        except Exception:
            pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        Path(token_path).unlink(missing_ok=True)
    print(json.dumps(results, indent=2))
    return 0


def _connect(port, token):
    try:
        return bridge.PhotoCraftControl(port, token)
    except Exception:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
