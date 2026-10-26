"""Help, the keyboard shortcuts guide, Browse Library, File associations, the splash and the Help menu (mockup: scripts/qml_parity/help.dc.html).

One object (`reference` in QML) because only one of these windows is open at a time. It keeps which window is open and each window's own state,
and publishes a `view` map the QML only draws.

Real: the version (`_version`), the update check (`updater.check_for_update`, in a worker thread), Open the log folder, Release notes and Report
a problem (the default browser), the keys (`ui/shortcuts.SHORTCUT_REGISTRY` and the user's overrides), the library (`LibraryStore`: roots,
search, add, remove, re-index) and the Windows file associations (`file_associations.py`; on any other system the window says they are managed by
the system). Not connected yet (they say so in a toast): downloading an update, Licences and credits, and "Show in the grid" for library
results. `memory=True` (--mock, tests) uses the mockup's sample library, sample file-association states and a scripted update check.
See docs/qml_unwired.md, section "Help, shortcuts, library, file types and splash".
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Property, QObject, QRunnable, QThreadPool, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from . import reference_content as C

REPO = "https://github.com/tylermcm/ImageTriage"
KEY_SPLIT = "+"


def _keys(text: str) -> list[str]:
    """'Ctrl+Shift+N' -> ['Ctrl', 'Shift', 'N']; 'Ctrl++' keeps the plus key."""
    text = (text or "").strip()
    if not text:
        return []
    if text.endswith("++"):
        return [part for part in text[:-2].split(KEY_SPLIT) if part] + ["+"]
    return [part for part in text.split(KEY_SPLIT) if part]


def _ago(stamp: str) -> str:
    """'2026-10-23T14:08:00' -> 'today', 'yesterday', '3 days ago'; '' -> 'never'."""
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return stamp or "never"
    days = (datetime.now().date() - when.date()).days
    return "today" if days <= 0 else "yesterday" if days == 1 else f"{days} days ago"


def _blocks_text(block: dict) -> str:
    if block["kind"] == "keys":
        return " ".join(row["t"] for row in block["rows"])
    return block.get("t", "")


class _UpdateSignals(QObject):
    done = Signal(object, str)          # the UpdateCheckResult (or None), the error text


class _UpdateTask(QRunnable):
    def __init__(self) -> None:
        super().__init__()
        self.signals = _UpdateSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            from ..updater import check_for_update

            result, error = check_for_update(), ""
        except Exception as exc:                                    # no feed, no network, a bad answer: the window says so
            result, error = None, str(exc) or exc.__class__.__name__
        self.signals.done.emit(result, error)


class _IndexSignals(QObject):
    done = Signal(object, str)


class _IndexTask(QRunnable):
    def __init__(self, roots: tuple[str, ...]) -> None:
        super().__init__()
        self.roots = roots
        self.signals = _IndexSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            from ..library_store import LibraryStore

            store = LibraryStore()
            summary, error = store.refresh_catalog(self.roots or tuple(root.path for root in store.list_catalog_roots() if root.enabled)), ""
        except Exception as exc:
            summary, error = None, str(exc) or exc.__class__.__name__
        self.signals.done.emit(summary, error)


class ReferenceModel(QObject):
    changed = Signal()
    closedWindow = Signal()                      # a window was closed
    uiRequest = Signal(str)                      # the window wants something only the main window can do: settings, coll, faces, drop, cull, showGrid

    def __init__(self, photos=None, toasts=None, memory: bool = False, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._photos = photos
        self._toasts = toasts
        self._memory = memory
        self._window = ""                        # "", "help", "keys", "lib" or "fa"
        self._menu = False
        self._topic = "start"
        self._hq = ""
        self._kq = ""
        self._lq = ""
        self._lscope = "all"
        self._fopen: dict[str, bool] = {"raw": True}
        self._upd = "ok" if memory else "idle"   # idle (real, not asked yet), ok, avail, checking, error
        self._upd_info = None                    # the real UpdateInfo of a newer version
        self._upd_error = ""
        self._last_checked = "14:08" if memory else ""
        self._task = None
        self._splash = {"on": False, "p": 8, "status": "Starting…", "preview": False}
        # the library
        self._roots: list[dict] = [dict(root) for root in C.SAMPLE_ROOTS] if memory else []
        self._lib_empty = False
        self._lib_busy = ""
        self._lib_results: list[dict] = []
        # file associations
        self._fa_os = True if memory else os.name == "nt"
        self._fa_mode = "sample"                 # memory only: "sample" (a mix), "none" (nothing set up), "all" (everything default)
        self._fa_cache: dict[str, tuple[str, str]] | None = None
        self._fa_busy = False
        if photos is not None:
            photos.countsChanged.connect(self._photos_changed)

    def _photos_changed(self) -> None:
        if self._window == "lib" and self._memory:      # the sample results are the folder's photos, which load after the window opens
            self._lib_refresh()
            self._emit()

    # ---------------- state ----------------
    view = Property("QVariantMap", lambda self: self._view(), notify=changed)
    window = Property(str, lambda self: self._window, notify=changed)
    menuOpen = Property(bool, lambda self: self._menu, notify=changed)
    version = Property(str, lambda self: _version(), notify=changed)

    def _emit(self) -> None:
        self.changed.emit()

    def _toast(self, kind: str, title: str, body: str = "") -> None:
        if self._toasts is not None:
            self._toasts.push(kind, title, body)

    @Slot()
    def toggleMenu(self) -> None:
        self._menu = not self._menu
        if self._menu:
            self._window = ""
        self._emit()

    @Slot()
    def closeMenu(self) -> None:
        if self._menu:
            self._menu = False
            self._emit()

    @Slot(str)
    def open(self, window: str) -> None:
        """Open one of help, keys, lib, fa (any open one closes), or run a Help menu entry (update, about)."""
        if window == "update":
            self.openAbout(check=True)
            return
        if window == "about":
            self.openAbout()
            return
        self._menu = False
        self._window = window
        if window == "lib":
            self._lib_refresh()
        elif window == "fa":
            self._fa_cache = None
        self._emit()

    @Slot()
    def openAbout(self, check: bool = False) -> None:
        self._menu = False
        self._window = "help"
        self._topic = "about"
        if check:
            self.checkUpdates()
        self._emit()

    @Slot()
    def toggleHelp(self) -> None:
        """F1: open Help, or close it when it is already open."""
        if self._window == "help":
            self.close()
        else:
            self.open("help")

    @Slot()
    def toggleKeys(self) -> None:
        if self._window == "keys":
            self.close()
        else:
            self.open("keys")

    @Slot()
    def close(self) -> None:
        if self._window or self._menu:
            self._window = ""
            self._menu = False
            self._emit()
            self.closedWindow.emit()

    @Slot(str)
    def go(self, target: str) -> None:
        """A button in a help article: open another window or tab."""
        if target == "keys":
            self.open("keys")
            return
        self.close()
        self.uiRequest.emit(target)

    # ---------------- help ----------------
    @Slot(str)
    def setQuery(self, text: str) -> None:
        if text != self._hq:
            self._hq = text
            self._emit()

    @Slot(str)
    def setTopic(self, topic: str) -> None:
        self._topic = topic
        self._emit()

    @Slot()
    def checkUpdates(self) -> None:
        """The About page's button: check, cancel a check, or (when one is waiting) start the download."""
        if self._upd == "checking":
            self._upd = "ok" if self._memory else "idle"
            self._task = None
            self._emit()
            return
        if self._upd == "avail":
            self._toast("warn", "Downloading the update isn’t connected yet", "Use Check for updates in the Widgets app for now.")
            return
        self._upd = "checking"
        self._upd_error = ""
        self._emit()
        if self._memory:
            QTimer.singleShot(1200, self._memory_check_done)
            return
        task = _UpdateTask()
        self._task = task
        task.signals.done.connect(self._update_done)
        QThreadPool.globalInstance().start(task)

    def _memory_check_done(self) -> None:
        if self._upd == "checking":
            self._upd = "ok"
            self._last_checked = datetime.now().strftime("%H:%M")
            self._emit()

    def _update_done(self, result, error: str) -> None:
        if self._task is None:                   # cancelled meanwhile
            return
        self._task = None
        self._last_checked = datetime.now().strftime("%H:%M")
        if error or result is None:
            self._upd, self._upd_error = "error", error
        elif result.update_available:
            self._upd, self._upd_info = "avail", result.latest
        else:
            self._upd = "ok"
        self._emit()

    @Slot(str)
    def openLink(self, link: str) -> None:
        if link == "notes":
            url = self._upd_info.release_notes_url if self._upd_info is not None and self._upd_info.release_notes_url else f"{REPO}/releases"
            QDesktopServices.openUrl(QUrl(url))
        elif link == "report":
            QDesktopServices.openUrl(QUrl(f"{REPO}/issues/new"))
        elif link == "logs":
            if self._memory:
                self._toast("info", "Opens the log folder", "Not shown while the sample data is on.")
            else:
                from ..perf import performance_log_dir

                folder = performance_log_dir()
                folder.mkdir(parents=True, exist_ok=True)
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        elif link == "licences":
            licence = self._licence_file()
            if licence is None:
                self._toast("warn", "The LICENSE file was not found", "It is meant to sit next to the app.")
            elif self._memory:
                self._toast("info", "Opens the LICENSE file", "Not done while the sample data is on.")
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(licence)))

    @staticmethod
    def _licence_file():
        """The LICENSE text that ships with the app: next to the package (a checkout or an installed copy), or in the frozen bundle."""
        import sys
        from pathlib import Path

        roots = [Path(__file__).resolve().parents[2], Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "_MEIPASS", "") else None, Path(sys.executable).parent]
        for root in filter(None, roots):
            for name in ("LICENSE", "LICENSE.txt", "LICENSE.md"):
                if (root / name).is_file():
                    return root / name
        return None

    def _help_view(self) -> dict:
        q = self._hq.strip().lower()

        def blocks_of(topic: dict):
            return C.settings_guide() if topic["id"] == "settings" else topic["blocks"]      # the Settings topic is written from the real settings

        def hits(topic: dict) -> int:
            if not q:
                return 0
            return sum(1 for block in blocks_of(topic) if q in _blocks_text(block).lower())

        def match(topic: dict) -> bool:
            return not q or q in topic["title"].lower() or q in topic["lead"].lower() or hits(topic) > 0

        shown = [t for t in C.TOPICS if match(t)]
        cur = next((t for t in shown if t["id"] == self._topic), shown[0] if shown else None)
        rows: list[dict] = []
        for topic in shown:
            if topic.get("sepBefore"):
                rows.append({"sep": True})
            rows.append({"sep": False, "id": topic["id"], "title": topic["title"], "d": topic["icon"], "cur": bool(cur and cur["id"] == topic["id"]), "n": str(hits(topic)) if hits(topic) else ""})
        upd = {
            "ok": ("win", "You are up to date", f"Version {_version()} is the newest." + (f" Last checked today at {self._last_checked}." if self._last_checked else ""), "Check again", False),
            "idle": ("fg3", f"Version {_version()}", "Check whether a newer version is available.", "Check for updates", False),
            "avail": ("accent", f"Version {self._upd_info.version} is available" if self._upd_info is not None else "Version 2.4.0 is available",
                      (self._upd_info.summary if self._upd_info is not None and self._upd_info.summary else "It adds faster thumbnails and fixes the closed-eyes setup on older Windows."), "Download", True),
            "checking": ("rev", "Checking for updates…", "This takes a moment.", "Cancel", False),
            "error": ("rej", "Could not check for updates", self._upd_error or "Check your internet connection and try again.", "Try again", False),
        }[self._upd]
        about = bool(cur and cur.get("about"))
        return {
            "query": self._hq, "topics": rows, "kind": "none" if cur is None else "about" if about else "article",
            "title": cur["title"] if cur else "", "lead": cur["lead"] if cur else "", "blocks": list(blocks_of(cur)) if cur and not about else [],
            "version": _version(), "upColor": upd[0], "upTitle": upd[1], "upBody": upd[2], "upBtn": upd[3], "upPrimary": upd[4],
            "links": [dict(link) for link in C.ABOUT_LINKS],
        }

    # ---------------- shortcuts ----------------
    @Slot(str)
    def setKeyQuery(self, text: str) -> None:
        if text != self._kq:
            self._kq = text
            self._emit()

    def _key_overrides(self) -> dict[str, str]:
        if self._memory:
            return {"compare_mode": "V"}         # the mockup's one changed key
        try:
            from ..ui.shortcuts import load_shortcut_overrides

            return load_shortcut_overrides()
        except Exception:                        # unreadable settings: show the defaults
            return {}

    def _keys_view(self) -> dict:
        q = self._kq.strip().lower()
        overrides = self._key_overrides()
        cats = []
        for name, rows in C.KEY_GROUPS:
            out = []
            for label, attr, fixed in rows:
                text = overrides.get(attr, fixed) if attr else fixed
                keys = _keys(text)
                if q and q not in label.lower() and q not in name.lower() and not any(k.lower() == q for k in keys):
                    continue
                out.append({"t": label, "keys": keys, "changed": bool(attr and attr in overrides and overrides[attr] != fixed)})
            if out:
                cats.append({"name": name, "rows": out})
        return {"query": self._kq, "cats": cats, "none": not cats, "count": sum(len(c["rows"]) for c in cats)}

    # ---------------- library ----------------
    @Slot(str)
    def setLibQuery(self, text: str) -> None:
        if text != self._lq:
            self._lq = text
            self._lib_refresh()
            self._emit()

    @Slot(str)
    def setLibScope(self, scope: str) -> None:
        self._lscope = scope
        self._lib_refresh()
        self._emit()

    def _store(self):
        from ..library_store import LibraryStore

        return LibraryStore()

    def _lib_load_roots(self) -> None:
        if self._memory:
            return
        try:
            roots = self._store().list_catalog_roots()
        except Exception:
            roots = []
        self._roots = [{"path": r.path, "name": Path(r.path).name or r.path, "count": r.indexed_record_count, "when": _ago(r.last_indexed_at), "error": r.last_error} for r in roots]

    def _lib_refresh(self) -> None:
        """Roots from the library store and the results for the search and scope."""
        self._lib_load_roots()
        if self._lscope != "all" and not any(r["path"] == self._lscope or r["name"] == self._lscope for r in self._roots):
            self._lscope = "all"
        q = self._lq.strip().lower()
        results: list[dict] = []
        if self._memory:
            rows = list(getattr(self._photos, "_all", ())) if self._photos is not None else []
            for i, row in enumerate(rows):
                root = self._roots[min(i // 4, len(self._roots) - 1)] if self._roots else None
                if root is None:
                    break
                results.append({"path": row["path"], "name": f"{row['name']}.{str(row.get('ext', 'jpg')).lower()}", "where": root["path"], "root": root["path"]})
            results = [r for r in results if (self._lscope == "all" or r["root"] == self._lscope) and (not q or q in r["name"].lower() or q in r["where"].lower())]
        elif self._roots:
            scope = "" if self._lscope == "all" else self._lscope
            try:
                records = self._store().search_catalog(search_text=self._lq.strip(), root_path=scope, limit=120)
            except Exception:
                records = []
            results = [{"path": rec.path, "name": Path(rec.path).name, "where": str(Path(rec.path).parent), "root": ""} for rec in records]
        self._lib_results = results

    @Slot(str)
    def addFolder(self, folder: str) -> None:
        """Add a folder to the library and index it (the real store; the sample list gets a new empty entry)."""
        folder = folder.strip()
        if not folder:
            return
        if self._memory:
            self._roots.append({"path": folder, "name": Path(folder).name or folder, "count": 0, "when": "never"})
            self._lib_empty = False
        else:
            try:
                self._store().add_catalog_root(folder)
            except Exception as exc:
                self._toast("error", "Could not add that folder", str(exc))
                return
            self._index((folder,), f"Indexing {Path(folder).name or folder}…")
        self._lib_refresh()
        self._emit()

    @Slot(str)
    def reindex(self, root: str) -> None:
        if self._memory:
            for entry in self._roots:
                if entry["path"] == root:
                    entry["when"] = "today"
            self._toast("info", "Re-indexed", "Sample data: nothing was read from disk.")
            self._emit()
            return
        self._index((root,), f"Indexing {Path(root).name or root}…")

    @Slot(str)
    def removeRoot(self, root: str) -> None:
        if self._memory:
            self._roots = [r for r in self._roots if r["path"] != root]
        else:
            try:
                self._store().remove_catalog_root(root)
            except Exception as exc:
                self._toast("error", "Could not remove that folder", str(exc))
                return
        self._toast("info", "Removed from the library", "No files were moved or deleted.")
        self._lib_refresh()
        self._emit()

    def _index(self, roots: tuple[str, ...], busy: str) -> None:
        self._lib_busy = busy
        task = _IndexTask(roots)
        task.signals.done.connect(self._index_done)
        QThreadPool.globalInstance().start(task)
        self._emit()

    def _index_done(self, _summary, error: str) -> None:
        self._lib_busy = ""
        if error:
            self._toast("error", "Indexing stopped", error)
        self._lib_refresh()
        self._emit()

    @Slot()
    def showInGrid(self) -> None:
        self._toast("warn", "Showing library results in the grid isn’t connected yet", "The results stay in this window.")

    def _lib_view(self) -> dict:
        roots = self._roots
        empty = self._lib_empty or not roots
        total = sum(r["count"] for r in roots)
        cards = [] if empty else [{"path": r["path"], "name": r["name"], "where": r["where"]} for r in self._lib_results]
        shown = len(cards)
        return {
            "empty": empty, "query": self._lq, "scope": self._lscope, "busy": self._lib_busy,
            "stats": [{"n": f"{len(roots):,}", "l": "Indexed folders"}, {"n": f"{total:,}", "l": "Image bundles"}, {"n": f"{shown:,}", "l": "Matches shown"}],
            "scopes": [{"id": "all", "label": "All indexed folders", "n": f"{total:,}"}] + [{"id": r["path"], "label": r["name"], "n": f"{r['count']:,}"} for r in roots],
            "cards": cards, "noResults": not cards and not empty,
            "roots": [{"path": r["path"], "name": r["path"], "meta": f"{r['count']:,} bundles · indexed {r['when']}" + (f" · {r['error']}" if r.get("error") else "")} for r in roots],
            "foot": self._lib_busy or ("Nothing indexed" if empty else f"Searching {total:,} bundles in {len(roots)} {'folder' if len(roots) == 1 else 'folders'}"),
            "show": f"Show {shown} in the grid", "canShow": shown > 0,
        }

    # ---------------- file associations ----------------
    @Slot(str)
    def toggleGroup(self, key: str) -> None:
        self._fopen = dict(self._fopen, **{key: not self._fopen.get(key, False)})
        self._emit()

    def _fa_states(self) -> dict[str, tuple[str, str]]:
        """suffix -> (state, other app): state is default, available, other or none."""
        if self._fa_cache is not None:
            return self._fa_cache
        from .. import file_associations as fa

        out: dict[str, tuple[str, str]] = {}
        if self._memory:
            sample = {".nef": "default", ".cr2": "default", ".jpg": "default", ".jpeg": "default", ".png": "available", ".nrw": "available", ".arw": "available", ".cr3": "other:Photos", ".dng": "other:Adobe Lightroom",
                      ".heic": "other:Photos", ".raf": "none", ".webp": "available", ".tif": "available", ".psd": "other:Photoshop", ".fits": "none", ".gif": "available"}
            for suffix in fa.supported_file_association_suffixes():
                raw = "none" if self._fa_mode == "none" else "default" if self._fa_mode == "all" else sample.get(suffix, "available")
                state, _, app = raw.partition(":")
                out[suffix] = (state, app)
        else:
            for state in fa.query_windows_file_association_states():
                if state.is_default:
                    out[state.suffix] = ("default", "")
                elif state.default_progid:
                    out[state.suffix] = ("other", fa.describe_windows_default_handler(state))
                elif state.registered:
                    out[state.suffix] = ("available", "")
                else:
                    out[state.suffix] = ("none", "")
        self._fa_cache = out
        return out

    def _fa_view(self) -> dict:
        if not self._fa_os:
            return {"supported": False, "groups": [], "pct": 0, "none": False, "title": "", "sub": "", "canRemove": False, "busy": False}
        from .. import formats, file_associations as fa

        states = self._fa_states()
        groups_def = (("raw", "Camera RAW", set(formats.RAW_SUFFIXES)), ("photo", "Photos", set(formats.STANDARD_IMAGE_SUFFIXES)))
        placed: set[str] = set()
        groups = []
        order = {s: i for i, s in enumerate(fa.supported_file_association_suffixes())}
        for key, name, members in groups_def + (("other", "Other", set()),):
            suffixes = sorted(([s for s in states if s in members] if members else [s for s in states if s not in placed]), key=lambda s: order.get(s, 999))
            placed.update(suffixes)
            rows = []
            for suffix in suffixes:
                state, app = states[suffix]
                rows.append({"ext": suffix, "name": C.TYPE_NAMES.get(suffix, suffix.lstrip(".").upper() + " file"), "state": state,
                             "text": {"default": "Opens in Image Triage", "available": "Available in Open with", "other": "Opens in " + (app or "another app"), "none": "Not set up"}[state], "canMake": state != "default"})
            defaults = sum(1 for r in rows if r["state"] == "default")
            groups.append({"key": key, "name": name, "open": bool(self._fopen.get(key)), "rows": rows, "sum": f"{len(rows)} types" + (f" · {defaults} default" if defaults else "")})
        total = len(states)
        defaults = sum(1 for s, _ in states.values() if s == "default")
        avail = sum(1 for s, _ in states.values() if s in ("default", "available"))
        none = avail == 0
        return {
            "supported": True, "groups": [g for g in groups if g["rows"]], "pct": round(100 * defaults / total) if total else 0, "none": none,
            "title": "Image Triage is not set up for any photo type" if none else f"Image Triage opens {defaults} of {total} photo types by default",
            "sub": "Make it available and it will be listed in “Open with” for every type." if none else f"It is listed in “Open with” for {avail}. Types that open in another app can be switched one at a time.",
            "canRemove": not none, "busy": self._fa_busy,
        }

    @Slot()
    def faRegister(self) -> None:
        if self._memory:
            self._fa_mode = "all" if self._fa_mode != "none" else "sample"
            self._fa_cache = None
        else:
            try:
                from ..file_associations import register_windows_file_associations

                register_windows_file_associations()
            except Exception as exc:
                self._toast("error", "Could not set up file types", str(exc))
                return
            self._fa_cache = None
        self._toast("success", "Image Triage is available for every photo type", "Windows still decides the default for each one.")
        self._emit()

    @Slot()
    def faRemove(self) -> None:
        if self._memory:
            self._fa_mode = "none"
        else:
            try:
                from ..file_associations import remove_windows_file_associations

                remove_windows_file_associations()
            except Exception as exc:
                self._toast("error", "Could not remove the file types", str(exc))
                return
        self._fa_cache = None
        self._emit()

    @Slot(str)
    def faMakeDefault(self, suffix: str) -> None:
        if self._memory:
            self._toast("info", f"Windows would ask which app should open {suffix}", "Sample data: nothing was changed.")
            return
        try:
            from ..file_associations import open_windows_file_association_chooser

            open_windows_file_association_chooser(suffix)
        except Exception as exc:
            self._toast("error", "Could not open the Windows chooser", str(exc))
            return
        QTimer.singleShot(4000, self._fa_recheck)       # the choice is made in Windows; look again afterwards

    def _fa_recheck(self) -> None:
        self._fa_cache = None
        self._emit()

    @Slot()
    def faDefaultApps(self) -> None:
        if self._memory:
            self._toast("info", "Opens Windows Default Apps", "Sample data: nothing was opened.")
            return
        try:
            from ..file_associations import open_windows_default_apps_settings

            open_windows_default_apps_settings()
        except Exception as exc:
            self._toast("error", "Could not open Windows settings", str(exc))

    # ---------------- splash ----------------
    @Slot(str, int)
    def splashShow(self, status: str, progress: int) -> None:
        self._splash = {"on": True, "p": max(0, min(100, int(progress))), "status": status, "preview": False}
        self._emit()

    @Slot()
    def splashHide(self) -> None:
        if self._splash["on"]:
            self._splash = dict(self._splash, on=False)
            self._emit()

    # ---------------- view ----------------
    def _view(self) -> dict:
        w = self._window
        out: dict = {
            "window": w, "menu": self._menu, "version": _version(),
            "menuItems": [{"sep": True} if item is None else {"sep": False, "id": item[0], "label": item[1], "d": item[2], "kbd": item[3]} for item in C.MENU],
            "splash": dict(self._splash, version=_version()),
        }
        if w == "help":
            out["help"] = self._help_view()
        elif w == "keys":
            out["keys"] = self._keys_view()
        elif w == "lib":
            out["lib"] = self._lib_view()
        elif w == "fa":
            out["fa"] = self._fa_view()
        return out

    # ---------------- test hook ----------------
    @Slot(str)
    def scene(self, name: str) -> None:
        """Put the windows in one of the mockup's scenes (hlp-*, hm-open, sk-*, lib-*, fa-*, sp-*)."""
        self._window, self._menu, self._topic, self._hq, self._kq, self._lq, self._lscope = "", False, "start", "", "", "", "all"
        self._lib_empty, self._fopen, self._fa_os, self._fa_mode, self._upd, self._fa_cache = False, {"raw": True}, True, "sample", "ok", None
        self._splash = {"on": False, "p": 8, "status": "Starting…", "preview": False}
        table = {
            "hlp-start": ("help", {}), "hlp-search": ("help", {"_hq": "collection", "_topic": "coll"}), "hlp-cull": ("help", {"_topic": "cull"}), "hlp-trouble": ("help", {"_topic": "trouble"}),
            "hlp-about": ("help", {"_topic": "about"}), "hlp-about-new": ("help", {"_topic": "about", "_upd": "avail"}),
            "hm-open": ("", {"_menu": True}), "sk-main": ("keys", {}), "sk-search": ("keys", {"_kq": "rename"}),
            "lib-results": ("lib", {}), "lib-search": ("lib", {"_lq": "wedding"}), "lib-empty": ("lib", {"_lib_empty": True}),
            "fa-main": ("fa", {"_fopen": {"raw": True, "photo": True}}), "fa-none": ("fa", {"_fa_mode": "none"}), "fa-other-os": ("fa", {"_fa_os": False}),
            "sp-start": ("", {"_splash": {"on": True, "p": 8, "status": "Starting…", "preview": True}}), "sp-mid": ("", {"_splash": {"on": True, "p": 56, "status": "Reading your folder…", "preview": True}}),
            "sp-end": ("", {"_splash": {"on": True, "p": 100, "status": "Ready", "preview": True}}),
        }
        window, attrs = table.get(name, ("", {}))
        self._window = window
        for key, value in attrs.items():
            setattr(self, key, value)
        if window == "lib":
            self._lib_refresh()
        self._emit()


def _version() -> str:
    from .._version import __version__

    return __version__
