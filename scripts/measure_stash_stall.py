"""Opt-in measurement: how responsive is the hosted editor while it saves an edited photo (``app.stash``)?

    python scripts/measure_stash_stall.py --executable C:\\path\\photocraft.exe --image C:\\raws\\a.NEF --output C:\\temp\\stall

Launches the editor on its own (no popout), opens the photo in full, edits it, starts the hosted save without waiting and
times a cheap control request (``ui.viewport``) on a second connection every 20 ms until the save is done. The editor answers
control requests from its UI thread, so a slow answer is the window not responding. Prints the slowest answers, the save's
duration and the sizes written. ``--lean`` leaves the full-size render out of the save, which is what the autosave does while a photo is open.
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


def wait_for(check, what, timeout=180.0, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for {what}")


def _connect(port, token):
    try:
        return bridge.PhotoCraftControl(port, token)
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--saves", type=int, default=2, help="edit-and-save rounds (the first also pays one-time costs)")
    parser.add_argument("--redraw", action="store_true", help="zoom in and out while timing, so the canvas redraws during the save")
    parser.add_argument("--cores", type=int, default=0, help="confine the editor to this many logical processors (a smaller machine)")
    parser.add_argument("--lean", action="store_true", help="leave the full-size render out of the save, as the autosave does (needs an editor that says stashWithoutPreview)")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    image = output / Path(args.image).name
    shutil.copyfile(args.image, image)
    write_root = output / ".image_triage_edits"
    write_root.mkdir(exist_ok=True)

    port = bridge._free_tcp_port()
    token = secrets.token_hex(32)
    fd, token_path = tempfile.mkstemp(prefix="photocraft_token_", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token)
    env = dict(os.environ)
    env.setdefault("WGPU_BACKEND", "dx12")
    if args.cores:
        env["RAYON_NUM_THREADS"] = str(args.cores)
    process = subprocess.Popen(
        [args.executable, "--control", str(port), "--control-token-file", token_path,
         "--automation-read-root", str(output), "--automation-write-root", str(write_root)],
        env=env,
    )
    if args.cores:
        import ctypes

        ctypes.windll.kernel32.SetProcessAffinityMask(ctypes.c_void_p(int(process._handle)), ctypes.c_size_t((1 << args.cores) - 1))
    results: dict = {"image": image.name, "cores": args.cores or os.cpu_count(), "lean": args.lean, "rounds": []}
    control = probe = None
    try:
        control = wait_for(lambda: _connect(port, token), "the control channel")
        probe = wait_for(lambda: _connect(port, token), "a second control connection")
        started = time.perf_counter()
        control.call("app.open", {"path": image.name, "replace": True})
        document = wait_for(lambda: (control.execute("session.inspect").get("documents") or [None])[0], "the photo to open")
        results["open_ms"] = round((time.perf_counter() - started) * 1000)
        results["document"] = {key: document.get(key) for key in ("width", "height", "depth") if key in document}
        time.sleep(3.0)  # let the canvas finish drawing: the measurement is of the save, not of the open

        zoom = ["view.zoomIn", "view.zoomOut"]

        def answer_ms() -> float:
            sent = time.perf_counter()
            if args.redraw:
                # Someone working in the window while it saves: each zoom step redraws the canvas.
                zoom.reverse()
                probe.execute(zoom[0])
            probe.call("ui.viewport")
            return (time.perf_counter() - sent) * 1000

        idle = sorted(answer_ms() for _ in range(50))
        results["idle_answer_ms"] = {"median": round(idle[25], 1), "max": round(idle[-1], 1)}

        stem = image.name  # save paths are relative to the write root
        for round_index in range(args.saves):
            control.execute("image.adjustments.invert")
            time.sleep(4.0)  # the full-size redraw after the edit is a separate cost
            sent = time.perf_counter()
            params = {"path": f"{stem}.pcraft", "display": f"{stem}.photocraft.display.jpg", "wait": False}
            if not args.lean:
                params["preview"] = f"{stem}.photocraft.png"
            job = control.call("app.stash", params)
            answers = []
            while True:
                answers.append(answer_ms())
                jobs = probe.call("jobs.list").get("jobs", [])
                mine = [entry for entry in jobs if entry.get("id") == job.get("job")]
                if not any(entry.get("state") == "running" for entry in mine):
                    break
                time.sleep(0.02)
            save_ms = (time.perf_counter() - sent) * 1000
            ordered = sorted(answers)
            results["rounds"].append({
                "job": {key: mine[0].get(key) for key in ("state", "error") if mine and mine[0].get(key)} or job,
                "save_ms": round(save_ms),
                "answers": len(answers),
                "slowest_answer_ms": round(ordered[-1]),
                "answers_over_100_ms": sum(1 for value in answers if value > 100),
                "answers_over_500_ms": sum(1 for value in answers if value > 500),
                "time_in_answers_over_100_ms": round(sum(value for value in answers if value > 100)),
            })
        results["written_mb"] = {
            path.name.replace(image.name, "*"): round(path.stat().st_size / 1048576, 1)
            for path in sorted(write_root.iterdir()) if path.is_file()
        }
    finally:
        try:
            if control is not None:
                control.call("app.quit")
        except Exception:
            pass
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
        Path(token_path).unlink(missing_ok=True)
    (output / "stash-stall.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
