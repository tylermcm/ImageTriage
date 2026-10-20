"""Windows prerequisites of the managed AI runtime, and a plain-words explanation when one is missing.

PyTorch and ONNX Runtime wheels for Windows are built with a recent Microsoft C++ toolchain and need a matching Microsoft Visual C++ Redistributable.
With an older one (seen: 14.36 from 2023) importing them fails with "WinError 1114: A dynamic link library (DLL) initialization routine failed",
which says nothing about the cause. ``dll_failure_hint`` turns that into an instruction.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess

RECOMMENDED_VC_RUNTIME = (14, 40, 0)  # PyTorch and ONNX Runtime wheels need the 14.4x C++ runtime; 14.36 failed to load both (verified)
VC_REDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
_DLL_FAILURE = re.compile(r"WinError 1114|WinError 126\b|WinError 127\b|DLL load failed|dynamic link library|DLL initialization", re.IGNORECASE)


def is_windows() -> bool:
    return os.name == "nt"


def vc_runtime_version() -> tuple[int, int, int] | None:
    """The installed Microsoft Visual C++ Redistributable (x64) as (major, minor, build), or None when unknown or not on Windows."""
    if not is_windows():
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\X64") as key:
            if int(winreg.QueryValueEx(key, "Installed")[0]) != 1:
                return None
            return int(winreg.QueryValueEx(key, "Major")[0]), int(winreg.QueryValueEx(key, "Minor")[0]), int(winreg.QueryValueEx(key, "Bld")[0])
    except (OSError, ValueError, ImportError):
        return None


def looks_like_dll_failure(message: str) -> bool:
    return bool(_DLL_FAILURE.search(message or ""))


def dll_failure_hint(message: str) -> str:
    """One or two sentences telling the person how to fix a Windows DLL load failure, or "" when ``message`` is not one (or this is not Windows)."""
    if not is_windows() or not looks_like_dll_failure(message):
        return ""
    version = vc_runtime_version()
    if version is not None and version < RECOMMENDED_VC_RUNTIME:
        shown = ".".join(str(part) for part in version)
        needed = ".".join(str(part) for part in RECOMMENDED_VC_RUNTIME[:2])
        return (
            f"This PC has the Microsoft Visual C++ Redistributable {shown}; the AI libraries need {needed} or newer. "
            f"Install the latest x64 package from {VC_REDIST_URL}, restart Image Triage and try again."
        )
    return (
        "A Windows library the AI runtime needs failed to start. Installing or repairing the latest Microsoft Visual C++ Redistributable "
        f"(x64, {VC_REDIST_URL}) usually fixes this."
    )


@functools.lru_cache(maxsize=1)
def nvidia_gpu_name() -> str | None:
    """The first NVIDIA graphics card's name, or None when there is none (or the driver tool is missing). Asked once per run: it starts a process."""
    exe = shutil.which("nvidia-smi")
    if exe is None and is_windows():
        candidate = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "nvidia-smi.exe")
        exe = candidate if os.path.exists(candidate) else None
    if exe is None:
        return None
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        done = subprocess.run([exe, "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True, timeout=6, creationflags=flags, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    names = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    return names[0] if names else None
