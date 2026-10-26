"""One honest percent and three steps for a Set Up AI run. Pure (no Qt).

The run is the runtime installer (pip; it prints each wheel it downloads and "Installing collected packages"), then the model downloads (each reports
"Name: file done / total MB"), then a check that the result works. Each part gets a share of the bar by its download size, so a 2 GB model moves it more than a 4 MB one.
"""
from __future__ import annotations

import re
import time
from typing import Callable

STEPS: tuple[str, ...] = ("Downloading", "Installing", "Checking that it works")
STEP_DOWNLOAD, STEP_INSTALL, STEP_CHECK = 0, 1, 2

_PIP_DOWNLOAD = re.compile(r"Downloading\s+\S+\s+\((\d+(?:\.\d+)?)\s*(kB|KB|MB|GB)\)")
_BUNDLE_FILE = re.compile(r"^(?P<name>.+?):\s+(?P<file>.+?)\s+(?P<done>\d+(?:\.\d+)?)\s*/\s*(?P<total>\d+(?:\.\d+)?)\s*MB$")
_BUNDLE_START = re.compile(r"^Downloading\s+(?P<name>.+?)\.\.\.$")
_UNIT_MB = {"kB": 1 / 1000, "KB": 1 / 1000, "MB": 1.0, "GB": 1000.0}

CHECK_SHARE = 0.04  # the last slice of the bar belongs to the final check
RUNTIME_DOWNLOAD_SHARE = 0.80  # of the runtime's own slice; installing the downloaded packages is the rest
RUNTIME_INSTALLING = 0.85  # where the runtime's slice stands once pip starts installing what it downloaded


class SetupProgress:
    def __init__(self, *, runtime_mb: int = 0, bundles: dict[str, int] | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        """``runtime_mb`` is the runtime download still to do (0 when it is installed); ``bundles`` maps a bundle's display name to its download size in MB."""
        self._clock = clock
        self._started = clock()
        self._runtime_mb = max(0, int(runtime_mb))
        self._bundles = {name: max(1, int(mb)) for name, mb in (bundles or {}).items()}
        self._runtime_downloaded = 0.0
        self._runtime_state = "idle" if self._runtime_mb == 0 else "downloading"  # idle | downloading | installing | done
        self._bundle_fraction: dict[str, float] = {name: 0.0 for name in self._bundles}
        self._current_bundle: str | None = None
        self._checking = False
        self._finished = False
        self._step = STEP_DOWNLOAD

    # ------------------------------------------------------------------ input

    def runtime_line(self, text: str) -> None:
        """One line of the runtime installer's output."""
        if self._runtime_mb == 0:
            return
        match = _PIP_DOWNLOAD.search(text)
        if match and self._runtime_state == "downloading":
            self._runtime_downloaded += float(match.group(1)) * _UNIT_MB[match.group(2)]
        elif "Installing collected packages" in text:
            self._runtime_state = "installing"
            self._raise_step(STEP_INSTALL)
        elif "AI runtime installation complete" in text or "Successfully installed" in text:
            self.runtime_done()

    def runtime_done(self) -> None:
        if self._runtime_mb:
            self._runtime_state = "done"
        self._raise_step(STEP_INSTALL)

    def bundle_message(self, text: str) -> None:
        """A progress message of the model download step ("Downloading X...", or "X: file 120 / 543 MB")."""
        started = _BUNDLE_START.match(text)
        if started:
            self._current_bundle = self._known(started.group("name"))
            return
        part = _BUNDLE_FILE.match(text)
        if part:
            name = self._known(part.group("name"))
            total = float(part.group("total"))
            if name is not None and total > 0:
                self._current_bundle = name
                self._bundle_fraction[name] = max(self._bundle_fraction[name], min(0.99, float(part.group("done")) / total))
                self._raise_step(STEP_INSTALL)

    def bundle_fraction(self, name: str, fraction: float) -> None:
        """A download that reports bytes done over bytes total (the model downloader's own callback)."""
        known = self._known(name)
        if known is not None:
            self._bundle_fraction[known] = max(self._bundle_fraction[known], min(0.99, max(0.0, fraction)))
            self._raise_step(STEP_INSTALL)

    def bundle_done(self, name: str) -> None:
        known = self._known(name)
        if known is not None:
            self._bundle_fraction[known] = 1.0

    def all_downloads_done(self) -> None:
        self.runtime_done()
        for name in self._bundle_fraction:
            self._bundle_fraction[name] = 1.0

    def start_check(self) -> None:
        self.all_downloads_done()
        self._checking = True
        self._raise_step(STEP_CHECK)

    def finish(self) -> None:
        self.start_check()
        self._finished = True

    # ------------------------------------------------------------------ output

    @property
    def step(self) -> int:
        return self._step

    @property
    def finished(self) -> bool:
        return self._finished

    def percent(self) -> int:
        weights = self._runtime_mb + sum(self._bundles.values())
        if weights <= 0:
            return 100 if self._finished else (96 if self._checking else 0)
        runtime = {"idle": 0.0, "downloading": RUNTIME_DOWNLOAD_SHARE * min(1.0, self._runtime_downloaded / max(1, self._runtime_mb)), "installing": RUNTIME_INSTALLING, "done": 1.0}[self._runtime_state]
        done = runtime * self._runtime_mb + sum(self._bundle_fraction[name] * mb for name, mb in self._bundles.items())
        value = (done / weights) * (1.0 - CHECK_SHARE) * 100.0
        if self._checking:
            value = (1.0 - CHECK_SHARE) * 100.0
        if self._finished:
            value = 100.0
        return int(max(0, min(100, round(value))))

    def eta_seconds(self) -> float | None:
        """Time left by the pace so far, or None until there is enough to judge by."""
        percent = self.percent()
        if self._finished or percent < 3:
            return None
        elapsed = self._clock() - self._started
        return elapsed * (100 - percent) / percent

    def eta_text(self) -> str:
        seconds = self.eta_seconds()
        if seconds is None:
            return ""
        minutes = max(1, round(seconds / 60))
        return f"about {minutes} min left"

    def step_label(self) -> str:
        return STEPS[self._step]

    def message(self) -> str:
        """A line for the chip's card."""
        if self._finished:
            return "AI is ready."
        return f"{self.step_label()}, {self.percent()}%." + (f" {self.eta_text().capitalize()}." if self.eta_text() else "")

    # ------------------------------------------------------------------ helpers

    def _raise_step(self, step: int) -> None:
        self._step = max(self._step, step)

    def _known(self, name: str) -> str | None:
        name = name.strip()
        if name in self._bundles:
            return name
        for known in self._bundles:
            if known.lower() == name.lower():
                return known
        return None
