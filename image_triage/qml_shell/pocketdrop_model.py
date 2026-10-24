"""PocketDrop for the QML shell: PocketDrop's own engine behind an interface drawn in QML.

With the native module present (`pocketdrop.dll` built with API level 2, see native/pocketdrop/capi) `PocketDropHost` runs the real
thing: the server, QR codes, resumable transfers, Anywhere tunnel, offline hotspot, received files. `st` is its state exactly as
the engine reports it (`Ui::stateJson`), `command()` is what a click would do.

On top of that this model adds the part that only exists in Image Triage: **library groups**. Sharing a selection, the winners or a
collection adds one row (stacked thumbnails, count, size) in place of one row per file; the engine is given the files themselves,
the model hides the engine's per-file rows that belong to a group and removes them again when the group is removed.

Without the module (or with `memory=True`, used by --mock and the tests) `st` is a scripted sample built to look like the HTML mockup,
and commands change that sample, so the panel can be checked on any machine without opening a port. docs/qml_unwired.md says which.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

SAMPLE_NAMES = ("DSC_4821.NEF", "Kutaisi picks", "Anna portrait final.jpg", "Meet at the old gate at 6")
_AS_LABELS = ("Originals", "Proofing JPEGs")
_SAMPLE_THUMBS = {"sel": (1, 2, 3), "win": (4, 6, 10), "keep": (2, 7, 9)}
_SAMPLE_COUNTS = {"sel": ("4 photos", "96 MB"), "win": ("96 photos", "1.4 GB"), "keep": ("412 photos", "5.9 GB")}


def _size(n: float) -> str:
    """Sizes the way PocketDrop words them: 28.4 MB, 612 MB, 1.4 GB."""
    value, unit = float(n), "B"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            break
        value /= 1024
    return f"{value:.0f} {unit}" if unit == "B" or value >= 10 else f"{value:.1f} {unit}"


def _js_lcg(seed: int):
    """The mockup's pseudo random numbers, bit for bit (JavaScript doubles lose low bits above 2^53 before the mask)."""
    x = seed

    def step() -> float:
        nonlocal x
        v = float(x) * 1103515245.0 + 12345.0
        x = int(v) % (1 << 32) & 0x7FFFFFFF
        return x / 0x7FFFFFFF

    return step


def sample_qr(seed: int) -> tuple[int, str]:
    """A 29x29 stand-in for a QR code, drawn the same way as in the mockup (finder squares, timing line, alignment, noise)."""
    n = 29
    rnd = _js_lcg(seed)
    mod = [[1 if rnd() < 0.5 else 0 for _x in range(n)] for _y in range(n)]

    def finder(ox: int, oy: int) -> None:
        for y in range(-1, 8):
            for x in range(-1, 8):
                px, py = ox + x, oy + y
                if px < 0 or py < 0 or px >= n or py >= n:
                    continue
                mod[py][px] = 1 if (0 <= x <= 6 and 0 <= y <= 6 and (x in (0, 6) or y in (0, 6) or (2 <= x <= 4 and 2 <= y <= 4))) else 0

    finder(0, 0)
    finder(n - 7, 0)
    finder(0, n - 7)
    for i in range(8, n - 8):
        mod[6][i] = 0 if i % 2 else 1
        mod[i][6] = 0 if i % 2 else 1
    for y in range(-2, 3):
        for x in range(-2, 3):
            mod[22 + y][22 + x] = 1 if max(abs(x), abs(y)) != 1 else 0
    path = []
    for y in range(n):
        x = 0
        while x < n:
            if not mod[y][x]:
                x += 1
                continue
            e = x
            while e < n and mod[y][e]:
                e += 1
            path.append(f"M{x} {y}h{e - x}v1h-{e - x}z")
            x = e
    return n, "".join(path)


class PocketDropModel(QObject):
    stateChanged = Signal()
    rowsChanged = Signal()
    libraryChanged = Signal()
    riskChanged = Signal()
    alertRequested = Signal(str, str)
    notice = Signal(str)

    def __init__(self, photos, memory: bool = False, parent: QObject | None = None, settings=None, collections=None) -> None:
        super().__init__(parent)
        self._photos = photos
        self._collections = collections          # CollectionsModel: its collections are what the Library picker offers
        self._settings = settings
        self._memory = memory
        self._want_sample_group = False
        if memory:
            photos.countsChanged.connect(self._photos_changed)
        self._host = None
        self._reason = ""
        self._st: dict = {}
        self._last = ""
        self._groups: list[dict] = []       # newest first: {key, title, sub, thumbs, paths, size, as}
        self._rows: list[dict] = []
        self._library: dict = {"current": [], "collections": []}
        self._risk = False
        self._sample = self._fresh_sample()
        self._started = False
        self._window = 0
        if not memory:
            self._load()

    # -- native module ---------------------------------------------------------------------------------------------------
    def _load(self) -> None:
        try:
            from ..pocketdrop import _bridge as bridge
            from ..pocketdrop.host import PocketDropHost

            lib = bridge.load_library()
            if not bridge.has_model(lib):
                self._reason = "pocketdrop.dll is older than the QML panel. Rebuild it with native/pocketdrop/build_windows.bat."
                return
            self._host = PocketDropHost(lib, self._settings, parent=self)
            self._host.changed.connect(self._refresh)
            self._host.alertRequested.connect(self.alertRequested)
        except Exception as exc:        # missing or unloadable module: the panel says why
            self._reason = str(exc)
            self._host = None

    def _fresh_sample(self) -> dict:
        return {"mode": 0, "st": ["ready", "ready", "ready"], "act": "none", "items": [], "recv": [], "burn": False, "auto": True, "warn": True,
                "topmost": False, "addr": 0, "link": 0, "copied": False, "pct": 46, "nid": 100, "drag": False}

    available = Property(bool, lambda self: self._host is not None or self._memory, constant=True)
    reason = Property(str, lambda self: self._reason, constant=True)
    sampleMode = Property(bool, lambda self: self._host is None, constant=True)

    @Slot()
    def start(self) -> None:
        """Starts the server the first time PocketDrop is shown (opening a port is not something to do at launch)."""
        if self._started:
            return
        self._started = True
        if self._host is not None:
            self._host.start()
        self._refresh()

    @Slot()
    def shutdown(self) -> None:
        if self._host is not None:
            self._host.shutdown()

    def setWindowHandle(self, handle: int) -> None:
        if self._host is not None:
            self._host.set_window(handle)

    # -- state ---------------------------------------------------------------------------------------------------------------
    def _refresh(self) -> None:
        state = self._host.state() if self._host is not None else self._sample_state()
        if not state:
            return
        key = json.dumps(state, sort_keys=True) + json.dumps([g["key"] + str(g["as"]) for g in self._groups])
        if key == self._last:
            return
        self._last = key
        self._st = state
        self._rows = self._merge_rows(state.get("rows", []))
        self.stateChanged.emit()
        self.rowsChanged.emit()

    st = Property("QVariantMap", lambda self: self._st, notify=stateChanged)
    rows = Property("QVariantList", lambda self: self._rows, notify=rowsChanged)
    library = Property("QVariantMap", lambda self: self._library, notify=libraryChanged)
    riskPending = Property(bool, lambda self: self._risk, notify=riskChanged)

    def _merge_rows(self, engine_rows: list[dict]) -> list[dict]:
        claimed = {p.lower() for g in self._groups for p in g["paths"]}
        received = [r for r in engine_rows if r.get("tone") == "recv"]
        rest = [r for r in engine_rows if r.get("tone") != "recv" and r.get("path", "").lower() not in claimed]
        merged = [dict(r, key=f"e{r['index']}", group="", thumbs=[], asLabel="") for r in received]
        for g in self._groups:
            sub = g["sub"]
            if g["as"]:
                sub = f"{g['count']} photos \u00b7 ~{max(1, round(g['size'] * 0.07 / 1024 ** 2))} MB as JPEG"
            merged.append({"index": -1, "kind": "coll", "title": g["title"], "sub": sub, "path": "", "tone": "", "key": g["key"], "group": g["key"],
                           "thumbs": g["thumbs"], "asLabel": _AS_LABELS[g["as"]]})
        merged += [dict(r, key=f"e{r['index']}", group="", thumbs=[], asLabel="") for r in rest]
        return merged

    # -- commands ------------------------------------------------------------------------------------------------------------
    @Slot(str)
    @Slot(str, str)
    def command(self, name: str, arg: str = "") -> None:
        if self._host is not None:
            self._host.command(name, arg)
            self._refresh()
        else:
            self._sample_command(name, arg)

    @Slot(int)
    def setMode(self, mode: int) -> None:
        """Same Wi-Fi / Anywhere / Offline. Anywhere asks first (the warning in the engine's own words) unless it was dismissed."""
        if mode == 1 and self._st.get("mode") != 1 and self._st.get("menu", {}).get("warn", True):
            self._risk = True
            self.riskChanged.emit()
            return
        self.command("mode", str(mode))

    @Slot(bool, bool)
    def confirmRisk(self, proceed: bool, dismiss: bool) -> None:
        self._risk = False
        self.riskChanged.emit()
        if not proceed:
            return
        if self._host is not None:
            self._host.command("mode", "1", risk_confirmed=True, dismiss_risk=dismiss)
            self._refresh()
        else:
            if dismiss:
                self._sample["warn"] = False
            self._sample_command("mode", "1")

    @Slot("QVariantList")
    def addUrls(self, urls: list) -> None:
        """Files and folders chosen in a dialog or dropped on the panel (QUrl or text)."""
        paths = []
        for url in urls:
            u = url if isinstance(url, QUrl) else QUrl(str(url))
            paths.append(os.path.normpath(u.toLocalFile() if u.isLocalFile() else str(url)))
        self.addPaths(paths)

    def addPaths(self, paths: list[str]) -> None:
        if self._host is not None:
            self._host.add_paths(paths)
            self._refresh()
        else:
            for path in paths:
                name = os.path.basename(path.rstrip("/\\")) or path
                self._sample["items"].insert(0, {"kind": "folder" if os.path.isdir(path) else "file", "title": name, "sub": "5.1 MB"})
            self._refresh()

    @Slot(str)
    def addText(self, text: str) -> None:
        if self._host is not None:
            self._host.add_text(text)
            self._refresh()

    @Slot(QUrl)
    def changeInbox(self, url: QUrl) -> None:
        self.command("set_inbox", os.path.normpath(url.toLocalFile()))

    @Slot(str)
    def removeRow(self, key: str) -> None:
        for g in self._groups:
            if g["key"] == key:
                self._groups.remove(g)
                keep = {p.lower() for other in self._groups for p in other["paths"]}
                if self._host is not None:
                    self._host.remove_paths([p for p in g["paths"] if p.lower() not in keep])
                self._last = ""
                self._refresh()
                self._update_added()
                return
        if key.startswith("e"):
            self.command("remove", key[1:])

    @Slot(str)
    def toggleSendAs(self, key: str) -> None:
        for g in self._groups:
            if g["key"] == key:
                g["as"] = 1 - g["as"]
        self._last = ""
        self._refresh()

    @Slot()
    def clearAll(self) -> None:
        self._groups.clear()
        self.command("clear")
        self._last = ""
        self._refresh()
        self._update_added()

    # -- library -------------------------------------------------------------------------------------------------------------
    def _entries(self, selected_row: int) -> list[tuple[str, str, list[dict]]]:
        p = self._photos
        out = []
        sel = p.collect(row=selected_row) if selected_row >= 0 else []
        out.append(("sel", "Selection", sel))
        out.append(("win", "Winners", p.collect(decision="win")))
        out.append(("keep", "Keep", p.collect(decision="keep")))
        for c in (self._collections.rows if self._collections is not None else []):
            out.append((f"col:{c['id']}", c["name"], self._collections.member_rows(c["id"])))
        return out

    @Slot(int)
    def refreshLibrary(self, selected_row: int = -1) -> None:
        """Re-reads what the Library picker offers from the photo list (call when it opens)."""
        current, collections = [], []
        for key, title, rows in self._entries(selected_row):
            paths = self._paths_of(rows)
            if self._memory and not key.startswith("col:"):
                count, size = _SAMPLE_COUNTS[key]
                sub = f"{count} \u00b7 {size}"
                everything = self._photos.collect()
                rows = [everything[(n - 1) % len(everything)] for n in _SAMPLE_THUMBS[key]] if everything else []
                paths, total_count = [], int(count.split()[0])
            else:
                total = sum(_file_size(path) for path in paths)
                sub = f"{len(rows)} photo{'s' if len(rows) != 1 else ''} \u00b7 {_size(total)}" if rows else "None yet"
                total_count = len(rows)
            entry = {"key": key, "title": title, "sub": sub, "thumbs": [r["path"] for r in rows[:3]] + [""] * (3 - min(3, len(rows))),
                     "paths": paths, "count": total_count, "empty": not rows, "added": any(g["key"] == key for g in self._groups)}
            (current if not key.startswith("col:") else collections).append(entry)
        self._library = {"current": current, "collections": collections}
        self.libraryChanged.emit()

    @staticmethod
    def _paths_of(rows: list[dict]) -> list[str]:
        paths: list[str] = []
        for r in rows:
            paths.append(r["path"])
            paths.extend(r.get("companions", ()))
        return paths

    def _update_added(self) -> None:
        for bucket in self._library.values():
            for entry in bucket:
                entry["added"] = any(g["key"] == entry["key"] for g in self._groups)
        self.libraryChanged.emit()

    @Slot(str)
    def addLibrary(self, key: str) -> None:
        """Adds a Library entry (the current selection, the winners, a collection) as one row; its files go to the engine."""
        entry = next((e for bucket in self._library.values() for e in bucket if e["key"] == key), None)
        if entry is None or entry["empty"] or any(g["key"] == key for g in self._groups):
            return
        size = sum(_file_size(p) for p in entry["paths"]) or _sample_bytes(entry["sub"])
        self._groups.insert(0, {"key": key, "title": entry["title"], "sub": entry["sub"], "thumbs": entry["thumbs"], "paths": entry["paths"],
                                "count": entry["count"], "size": size, "as": 0})
        if self._host is not None:
            self._host.add_paths(entry["paths"])
        self._last = ""
        self._refresh()
        self._update_added()

    # -- sample mode ---------------------------------------------------------------------------------------------------------
    @Slot(str, str, str, str)
    def setSample(self, mode: str, state: str, activity: str, items: str) -> None:
        """Test hook: put the sample into a state (`--ui "pdMode=anywhere;pdState=missing;..."`)."""
        s = self._sample = self._fresh_sample()
        s["mode"] = {"wifi": 0, "anywhere": 1, "offline": 2}.get(mode or "wifi", 0)
        s["st"][s["mode"]] = state or "ready"
        s["act"] = activity or "none"
        mixed = items in ("mixed", "shared")
        s["items"] = [] if not mixed else [{"kind": "file", "title": "DSC_4821.NEF", "sub": "28.4 MB \u00b7 NEF"},
                                           {"kind": "folder", "title": "Kutaisi picks", "sub": "38 files \u00b7 412 MB"}]
        s["recv"] = [{"kind": "rfile", "title": "IMG_2290.HEIC", "sub": "Received \u00b7 3.2 MB"},
                     {"kind": "rnote", "title": "Parking is behind the hotel", "sub": "Received note"}] if items == "mixed" else []
        if items == "mixed":
            s["items"] += [{"kind": "file", "title": "Anna portrait final.jpg", "sub": "6.1 MB \u00b7 JPG"},
                           {"kind": "note", "title": "Meet at the old gate at 6", "sub": "Note \u00b7 26 characters"}]
        self._groups = []
        self._want_sample_group = mixed      # the mockup's list leads with a collection; the photos may not be loaded yet
        self._photos_changed()
        self._last = ""
        self._refresh()

    def _photos_changed(self) -> None:
        if self._memory and self._want_sample_group and not self._groups:
            self.refreshLibrary(-1)
            self.addLibrary("col:c1")

    def _sample_command(self, name: str, arg: str) -> None:
        s = self._sample
        if name == "mode":
            s["mode"] = int(arg)
        elif name == "card":
            if s["st"][1] == "missing":
                s["st"][1] = "ready"       # the real download takes a while; the sample jumps to the end
            else:
                s["st"][s["mode"]] = "ready"
        elif name == "copy":
            s["copied"] = True
        elif name == "new_link":
            s["link"] += 1
        elif name == "remove":
            idx = int(arg)
            rows = s["recv"] + s["items"]
            if 0 <= idx < len(rows):
                target = rows[idx]
                (s["recv"] if target in s["recv"] else s["items"]).remove(target)
        elif name == "clear":
            s["items"], s["recv"] = [], []
        elif name == "paste":
            s["items"].insert(0, {"kind": "note", "title": "Wi-Fi at the flat is gz-guest", "sub": "Note \u00b7 28 characters"})
        elif name == "add_files":
            s["items"].insert(0, {"kind": "file", "title": SAMPLE_NAMES[s["nid"] % 3], "sub": "8.4 MB"})
            s["nid"] += 1
        elif name == "add_folder":
            s["items"].insert(0, {"kind": "folder", "title": "Georgia trip", "sub": "212 files \u00b7 3.1 GB"})
        elif name == "menu" and arg in ("burn", "auto_any", "warn_any", "topmost"):
            key = {"burn": "burn", "auto_any": "auto", "warn_any": "warn", "topmost": "topmost"}[arg]
            s[key] = not s[key]
        elif name == "addr":
            s["addr"] = int(arg)
        self._last = ""
        self._refresh()

    def _sample_state(self) -> dict:
        s = self._sample
        mode, st, act = s["mode"], s["st"][s["mode"]], s["act"]
        qr_card = st in ("ready", "done")
        ips = (("192.168.1.24", "Wi-Fi"), ("10.8.0.2", "VPN"))
        slug = ("quiet-river-lamp", "amber-harbor-nine", "soft-cedar-moon")[s["link"] % 3]
        url = (f"http://{ips[s['addr']][0]}:47291/Xk3hD8" if mode == 0 else f"https://{slug}.trycloudflare.com/Xk3hD8" if mode == 1
               else "http://192.168.137.1:47291/Xk3hD8")
        has_link = qr_card or act != "none"
        tone, status = "idle", "Idle"
        if act == "sending":
            tone, status = "send", "Sending to Pixel 8"
        elif act == "receiving":
            tone, status = "recv", "Receiving from Pixel 8"
        elif act == "done":
            tone, status = "ok", "Pixel 8 connected"
        elif st == "bluetooth":
            tone, status = "warn", "Bluetooth setup"
        elif st == "nonet":
            tone, status = "bad", "Offline"
        elif st == "failed":
            tone, status = "bad", "Hotspot failed" if mode == 2 else "Tunnel failed"
        elif st == "ready":
            tone, status = "ok", "Hotspot ready" if mode == 2 else "Ready"
        badge = lambda k: {"ready": "ok" if k else "", "missing": "warn", "bluetooth": "warn", "downloading": "warn",
                           "failed": "bad", "nonet": "bad"}.get(s["st"][k], "")
        rows = [dict(r, tone="recv") for r in s["recv"]] + [dict(r, tone="") for r in s["items"]]
        for i, r in enumerate(rows):
            r["index"], r["path"] = i, ""
        info = {"kind": "hint", "left": "", "right": "", "code": "", "frac": 0.0, "incoming": False}
        if act in ("sending", "receiving"):
            info.update(kind="active", left="62%  \u00b7  DSC_4821.NEF  \u2192  Pixel 8" if act == "sending" else "38%  \u00b7  IMG_2290.HEIC  \u2190  Pixel 8",
                        right="18.3 MB/s" if act == "sending" else "9.6 MB/s", frac=0.62 if act == "sending" else 0.38, incoming=act == "receiving")
        elif mode == 1 and qr_card:
            info.update(kind="code", code="K7QM-4XD2")
        elif act == "done":
            info.update(kind="done", left="Sent 3 files to Pixel 8")
        elif (s["items"] or self._groups) and qr_card:
            count = len(s["items"]) + len(self._groups)
            total = sum(g["size"] for g in self._groups) / 1024 ** 2 + sum(_row_mb(r) for r in s["items"])
            info.update(kind="sum", left=f"{count} items  \u00b7  " + (f"{total / 1000:.1f} GB" if total >= 1000 else f"{round(total)} MB"),
                        right="direct download" if count == 1 and not self._groups else f"zip {round(total * 0.86)} MB \u00b7 compressed")
        else:
            info["left"] = {"missing": "Anywhere needs a one-time download.", "downloading": "This only happens once.", "busy": "Almost there\u2026",
                            "failed": "", "nonet": "Connect to Wi-Fi, or use Offline mode.", "bluetooth": "Waiting for a connection\u2026",
                            "drag": "Release to add to the share."}.get(st, "Join Wi-Fi PocketDrop-4F2A \u00b7 password 7k2m9xqd, then scan." if mode == 2
                                                                         else "Scan to send or receive  \u00b7  same Wi-Fi")
        copy_label = "Copy hotspot password" if mode == 2 else "Copy QR code" if mode == 1 else "Copy link"
        size, path = sample_qr(7 + s["link"] * 13 + mode * 31) if qr_card else (0, "")
        copied, s["copied"] = s["copied"], False
        return {
            "mode": mode, "offline": True, "status": status, "tone": tone, "card": "drag" if s["drag"] else {"ready": "qr", "done": "qr"}.get(st, st),
            "cardMsg": "Check your internet connection and try again." if mode == 1 else "This PC\u2019s Wi-Fi adapter doesn\u2019t support hosted networks.",
            "pct": s["pct"], "btHotspot": False, "badges": [badge(0), badge(1), badge(2)], "qrSize": size, "qrPath": path,
            "hasLink": has_link, "canOpen": has_link, "linkText": url.replace("https://", "").replace("http://", "") if has_link else "", "linkEmpty": "" if has_link else "Link appears when ready",
            "copied": copied, "copyLabel": copy_label, "info": info, "rows": rows, "sharing": bool(s["items"] or self._groups),
            "menu": {"burn": s["burn"], "auto": s["auto"], "warn": s["warn"], "topmost": s["topmost"], "tunnelOpen": mode == 1 and st == "ready",
                     "inbox": r"C:\Users\You\Downloads\PocketDrop",
                     "addrs": [{"ip": ip, "adapter": name, "selected": i == s["addr"]} for i, (ip, name) in enumerate(ips)]},
            "busy": st in ("busy", "downloading"), "recent": False,
        }


def _row_mb(row: dict) -> float:
    """Megabytes in a sample row's sub line ('28.4 MB · NEF', '38 files · 412 MB'), 0 for notes."""
    import re

    found = re.findall(r"([\d.]+) (GB|MB|KB)", row.get("sub", ""))
    return sum(float(n) * {"GB": 1024, "MB": 1, "KB": 1 / 1024}[u] for n, u in found[-1:])


def _sample_bytes(sub: str) -> int:
    """'84 photos · 612 MB' -> bytes, for the sample entries that have no files behind them."""
    try:
        number, unit = sub.rsplit("\u00b7", 1)[1].split()
        return int(float(number) * 1024 ** ("B KB MB GB TB".split().index(unit)))
    except (IndexError, ValueError):
        return 0


def _file_size(path: str) -> int:
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0
