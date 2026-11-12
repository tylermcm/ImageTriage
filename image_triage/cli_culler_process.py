"""Own the worker's process group (POSIX) or kill-on-close job (Windows)."""
from __future__ import annotations

import os
import runpy
import select
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path


class WorkerProcessTree:
    def __init__(self, process=None):
        self.process = process
        self._job = None
        self._closed = False
        self.job_name = None
        if os.name == "nt":
            self._create_windows_job()

    def _create_windows_job(self):
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
        api.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        api.QueryInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                 w.DWORD, ctypes.c_void_p]
        api.CloseHandle.argtypes = [w.HANDLE]
        self.job_name = "Local\\ImageTriage-Culler-" + uuid.uuid4().hex
        job = api.CreateJobObjectW(None, self.job_name)
        if not job:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        try:
            if not api.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            api.CloseHandle(job)
            raise
        self._job, self._api = job, api

    def close(self):
        # Also remove descendants left after a successful parent exit.
        if self._closed:
            return
        if os.name == "nt":
            if self._job is not None:
                import ctypes
                try:
                    if not self._api.TerminateJobObject(self._job, 1):
                        raise ctypes.WinError(ctypes.get_last_error())
                    # Termination is asynchronous. Wait for the actual interpreter
                    # and metadata children to release their files before rmtree.
                    from ctypes import wintypes as w

                    class Accounting(ctypes.Structure):
                        _fields_ = [(name, ctypes.c_longlong) for name in
                                    ("UserTime", "KernelTime", "PeriodUserTime", "PeriodKernelTime")]
                        _fields_ += [(name, w.DWORD) for name in
                                     ("PageFaults", "Total", "Active", "Terminated")]

                    deadline = time.monotonic() + 5
                    while True:
                        accounting = Accounting()
                        if not self._api.QueryInformationJobObject(
                                self._job, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                            raise ctypes.WinError(ctypes.get_last_error())
                        if not accounting.Active:
                            break
                        if time.monotonic() >= deadline:
                            raise RuntimeError("The native culler cleanup job did not stop")
                        time.sleep(0.01)
                finally:
                    self._api.CloseHandle(self._job)
                    self._job = None
                    self._closed = True
            self._closed = True
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
            self._closed = True


def join_windows_job(name):
    """Adopt the real interpreter, including Store/venv launcher replacements.

    This runs before the worker can import engines or spawn children. The GUI
    holds the only lasting job handle, so its exit also kills the worker tree.
    """
    import ctypes
    from ctypes import wintypes as w

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenJobObjectW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
    api.OpenJobObjectW.restype = w.HANDLE
    api.GetCurrentProcess.restype = w.HANDLE
    api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    api.IsProcessInJob.argtypes = [w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL)]
    api.CloseHandle.argtypes = [w.HANDLE]
    job = api.OpenJobObjectW(0x0001 | 0x0004, False, name)  # ASSIGN_PROCESS | QUERY
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        process = api.GetCurrentProcess()
        if not api.AssignProcessToJobObject(job, process):
            raise ctypes.WinError(ctypes.get_last_error())
        owned = w.BOOL()
        if not api.IsProcessInJob(process, job, ctypes.byref(owned)):
            raise ctypes.WinError(ctypes.get_last_error())
        if not owned.value:
            raise RuntimeError("The culler interpreter did not join its cleanup job")
    finally:
        api.CloseHandle(job)


class WorkerOutput:
    """Bounded, cancellable protocol reads; no thread can block in readline/close."""
    LIMIT = 1024 * 1024

    def __init__(self, stream):
        self.fd = stream.fileno()
        self.buffer = bytearray()
        self.eof = False
        if os.name == "nt":
            import ctypes
            import msvcrt
            from ctypes import wintypes as w
            self._handle = w.HANDLE(msvcrt.get_osfhandle(self.fd))
            self._peek = ctypes.WinDLL("kernel32", use_last_error=True).PeekNamedPipe
            self._peek.argtypes = [w.HANDLE, ctypes.c_void_p, w.DWORD,
                                   ctypes.c_void_p, ctypes.POINTER(w.DWORD), ctypes.c_void_p]

    def readline(self, *, exited=False):
        """None means no complete line yet, b'' means EOF (or drained dead worker)."""
        if b"\n" not in self.buffer and not self.eof:
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes as w
                available = w.DWORD()
                if not self._peek(self._handle, None, 0, None, ctypes.byref(available), None):
                    error = ctypes.get_last_error()
                    if error != 109:  # ERROR_BROKEN_PIPE
                        raise ctypes.WinError(error)
                    self.eof = True
                elif available.value:
                    self.buffer.extend(os.read(self.fd, min(65536, available.value)))
                elif exited:
                    self.eof = True
                else:
                    time.sleep(0.05)
            elif select.select([self.fd], [], [], 0 if exited else 0.05)[0]:
                chunk = os.read(self.fd, 65536)
                self.buffer.extend(chunk)
                self.eof = not chunk
            elif exited:
                self.eof = True
        end = self.buffer.find(b"\n") + 1
        if not end and self.eof:
            end = len(self.buffer)
        if end > self.LIMIT or (not end and len(self.buffer) > self.LIMIT):
            raise ValueError("Native worker protocol line exceeds 1 MiB")
        if end:
            line = bytes(self.buffer[:end])
            del self.buffer[:end]
            return line
        return b"" if self.eof else None


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
    if os.name == "nt":
        join_windows_job(sys.argv[1])
        sys.argv = sys.argv[2:]
        runpy.run_path(sys.argv[0], run_name="__main__")
    else:
        raise SystemExit(supervise(sys.argv[1:]))
