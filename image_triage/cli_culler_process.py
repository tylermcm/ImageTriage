"""Own the worker's process group (POSIX) or kill-on-close job (Windows)."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


class WorkerProcessTree:
    def __init__(self, process):
        self.process = process
        self._job = None
        self._closed = False
        if os.name == "nt":
            self._attach_windows_job()

    def _attach_windows_job(self):
        import ctypes
        from ctypes import wintypes as w

        class BasicLimits(ctypes.Structure):
            _fields_ = [("ProcessTime", ctypes.c_longlong), ("JobTime", ctypes.c_longlong),
                        ("Flags", w.DWORD), ("MinWorkingSet", ctypes.c_size_t),
                        ("MaxWorkingSet", ctypes.c_size_t), ("ActiveProcesses", w.DWORD),
                        ("Affinity", ctypes.c_size_t), ("Priority", w.DWORD), ("Scheduling", w.DWORD)]

        class IOCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("Basic", BasicLimits), ("IO", IOCounters),
                        ("ProcessMemory", ctypes.c_size_t), ("JobMemory", ctypes.c_size_t),
                        ("PeakProcessMemory", ctypes.c_size_t), ("PeakJobMemory", ctypes.c_size_t)]

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        api.CreateJobObjectW.restype = w.HANDLE
        api.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        api.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        api.CloseHandle.argtypes = [w.HANDLE]
        job = api.CreateJobObjectW(None, None)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        try:
            if not api.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not api.AssignProcessToJobObject(job, w.HANDLE(int(self.process._handle))):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            api.CloseHandle(job)
            raise
        self._job, self._api = job, api

    def close(self):
        # Also remove descendants left after a successful parent exit.
        if self._closed:
            return
        self._closed = True
        if os.name == "nt":
            if self._job is not None:
                self._api.TerminateJobObject(self._job, 1)
                self._api.CloseHandle(self._job)
                self._job = None
        else:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(self.process.pid, signal.SIGKILL)
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            except ProcessLookupError:
                pass


def supervise(arguments):
    """A per-run reaper, avoiding any subreaper state in the GUI process."""
    if os.getpgrp() != os.getpid():
        raise RuntimeError("Worker supervisor requires its own process session")
    if sys.platform.startswith("linux"):
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            raise OSError(ctypes.get_errno(), "Could not establish worker subtree reaping")
    stopping = [False]

    def stop(_signal, _frame):
        stopping[0] = True

    signal.signal(signal.SIGTERM, stop)
    child = subprocess.Popen([sys.executable, "-I", "-X", "utf8", *arguments])
    while child.poll() is None and not stopping[0]:
        time.sleep(0.01)
    status = child.returncode
    os.killpg(os.getpgrp(), signal.SIGTERM)
    try:
        child.wait(timeout=1)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
    # Orphans are adopted here on Linux. Kill and reap only this supervisor's
    # children, including metadata grandchildren, before the supervisor exits.
    deadline = time.monotonic() + 1
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        if not pid:
            if sys.platform.startswith("linux") and time.monotonic() >= deadline:
                children = Path(f"/proc/self/task/{os.getpid()}/children").read_text().split()
                for descendant in children:
                    try:
                        os.kill(int(descendant), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            time.sleep(0.01)
    return status if status is not None and status >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(supervise(sys.argv[1:]))
