"""Persistent QML settings, defaults, validation and shortcut bindings."""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import Property, QObject, Qt, Signal, Slot
from PySide6.QtGui import QKeySequence

# -- settings limits, kept in one place -----------------------------------------------------
# The viewer reads ahead at most this many photos and keeps a few more temporary pictures than that (viewer_host.FILES_KEPT), so a full
# read-ahead never pushes out the photo being looked at. The setting offers exactly the range the viewer honours.
PRELOAD_DEFAULT, PRELOAD_MAX = 10, 24
AI_KEEP_TOP = (1, 50, 10)
AI_REVIEW_BAND = (0, 30, 10)
AI_BASE_WEIGHT = (0, 100, 65)
# The three scoring values have no row any more (they reach only the retired engine), but what was stored for them is still applied at start.
_AI_SCORING_KEYS = {"keepTop": "ai/keep_top_percent", "reviewBand": "ai/review_band_percent", "baseWeight": "ai/base_score_weight_percent"}
KEYPAD_MODIFIER = 0x20000000        # Qt.KeypadModifier: set for a key on the number pad, and not part of a chord

# The registry shortcuts the shell carries out through Main.qml's runCommand: registry id -> the shell's command id (the palette's). Their keys (the
# defaults, or what Settings > Shortcuts saved) are bound live; the grid's and the viewer's own keys ask `keyIs` instead.
BOUND_ACTIONS = (
    ("open_folder", "file.open_folder"), ("refresh_folder", "file.refresh_folder"), ("workflow_settings", "file.workflow_settings"),
    ("undo", "edit.undo"), ("rename_selection", "edit.rename_selection"), ("batch_rename_selection", "tools.batch_rename"),
    ("batch_resize_selection", "tools.batch_resize"), ("batch_convert_selection", "tools.batch_convert"), ("handoff_builder", "workflow.handoff_builder"),
    ("share_to_phone", "workflow.share_to_phone"), ("best_of_set_auto_assembly", "workflow.best_of"), ("delete_selection", "review.delete_selection"),
    ("move_selection", "review.move_selection"), ("clear_filters", "search.clear_filters"), ("open_ai_workflow_center", "ai.workflow_center"),
    ("zen_mode", "view.zen_mode"), ("next_ai_pick", "ai.next_top_pick"),
)
# Registry rows whose key is asked for where it is pressed (`keyIs` in the grid and the viewer, `seq` for the palette's own shortcut).
KEY_ACTIONS = ("accept_selection", "reject_selection", "keep_at_cursor", "collect_at_cursor", "open_preview", "open_command_palette")
# Settings > Shortcuts lists these and no others: a row for a command the shell never carries out would take a key and do nothing.
LISTED_ACTIONS = frozenset(action for action, _cmd in BOUND_ACTIONS) | frozenset(KEY_ACTIONS)
# Defaults the shell gives a registry row that has none of its own. Delete used to be a second, fixed shortcut beside the bindable row; a key
# bound on that row then made two shortcuts on one chord and neither fired.
SHELL_DEFAULTS = {"delete_selection": "Delete"}
# Keys the shell binds itself and the registry has no row for: a shortcut changed to one of them is refused, as two rows on one chord are.
FIXED_KEYS = {
    "Ctrl+F": "Search photos",
    "Ctrl+A": "Select all photos", "Ctrl+/": "Keyboard shortcuts", "F1": "Help", "Esc": "Close", "Return": "Open the photo", "Space": "Open the photo",
    "U": "Clear the decision", "I": "Show or hide the inspector", "Left": "Previous photo", "Right": "Next photo", "Up": "Photo above", "Down": "Photo below",
    "Home": "First photo", "End": "Last photo", "0": "Clear the rating", "1": "Rate 1 star", "2": "Rate 2 stars", "3": "Rate 3 stars", "4": "Rate 4 stars", "5": "Rate 5 stars",
}

APPEARANCE_MODES = (
    ("Slate", "slate"), ("Indigo", "indigo"), ("Dark", "dark"), ("Midnight", "midnight"), ("Graphite", "graphite"),
    ("Forest", "forest"), ("High Contrast", "high_contrast"), ("Warm Neutral", "warm_neutral"), ("Light", "light"), ("Auto", "auto"),
)
DARK_TONE_OPTIONS = (("Slate (darkest)", "slate"), ("Soft (default)", "soft"), ("Dim (lightest)", "dim"))
GRID_FADE_OPTIONS = (("Soft (default)", "soft"), ("Deep", "deep"), ("Dark", "dark"), ("Off", "off"))
DELETE_MODES = (("Safe Trash (Undoable)", "Safe Trash (Undoable)"), ("System Trash", "System Trash"))
# Accent hues offered as swatches (OKLCH hue); the shell's own theme feature, not a setting of the Widgets app.
ACCENTS = (("Blue", 255), ("Violet", 300), ("Rose", 10), ("Amber", 75), ("Green", 155), ("Teal", 195))

_DEMO_SESSIONS = ("Default", "Wedding", "Sports", "Travel")
_DEMO_CATALOG = "Indexed files: 12,408\nIndexed image bundles: 3,112\nCatalog cache reads: enabled\nFolder watch: on"


@dataclass(frozen=True)
class F:
    """One setting row: what it is, how it is stored, how it is shown."""
    id: str
    type: str                   # toggle | seg | select | step | slider | swatch | text | keys
    label: str
    desc: str = ""
    key: str = ""               # QSettings key ("" = not stored)
    kind: str = "bool"          # bool | int | float | str
    default: object = None
    options: tuple = ()         # ((label, value), ...) for seg / select
    lo: float = 0
    hi: float = 0
    step: float = 1
    fmt: str = ""               # %, x, photos, f2, int
    zero: str = ""              # label for the minimum value ("Off", "Auto")
    dep: str = ""               # id of the toggle this row needs
    btn: str = ""               # trailing button label


@dataclass(frozen=True)
class Group:
    title: str
    hint: str
    rows: tuple


@dataclass(frozen=True)
class Section:
    id: str
    name: str
    icon: str
    intro: str
    groups: tuple = field(default_factory=tuple)


def _t(id, label, desc, key, default, **kw):
    return F(id, "toggle", label, desc, key=key, kind="bool", default=default, **kw)


def build_schema(shortcut_registry=()) -> tuple:
    """The settings pages. Wording and ranges follow the Widgets dialog; sections are regrouped as in the mockup (Appearance and
    Browsing are the old Interface page split in two). Every stored row is read by something (tests/test_qml_shell_settings.py checks it):
    a switch that is saved and never read is not shown."""
    general = Section("general", "General", "M4 7h9M17 7h3M4 17h3M11 17h9M15 4.5v5M9 14.5v5",
        "Choose what happens to deleted images, and manage reusable review presets.", (
        Group("Review behavior", "Defaults used when a new review starts", (
            F("session", "select", "Profile preset", "Named workspace preset used for review behaviour like accepted-image handling and delete behaviour.",
              key="workflow/session", kind="str", default="Default"),
            F("deleteMode", "select", "Delete behavior", "Where rejected or deleted images go when you delete from Image Triage.",
              key="workflow/delete_mode", kind="str", default=DELETE_MODES[0][1], options=DELETE_MODES),
            F("trashLimit", "step", "Safe Trash size limit (GB)", "When the safe trash on this computer is larger than this, the photos deleted longest ago are deleted for good "
              "until it fits. Applied after each delete, never to the photos of that delete. Restore or empty it from File, Safe Trash.",
              key="workflow/safe_trash_limit_gb", kind="int", default=0, lo=0, hi=500, step=5, zero="No limit"),
        )),
        Group("App updates", "Keep Image Triage current automatically", (
            _t("updates", "Check for app updates on startup", "Looks for a newer installer when Image Triage starts. If one is available, the download button in the top right lights up.",
               "updates/check_on_startup", True),
        )),
        Group("Processing", "Performance and visibility", (
            _t("perfLog", "Log detailed performance timings", "Writes step timings to a log for diagnosing slowness. Also available from the Tools menu.",
               "diagnostics/performance_logging", False),
        )),
    ))
    appearance = Section("appearance", "Appearance", "M12 3.5a8.5 8.5 0 100 17 8.5 8.5 0 000-17zM12 3.5v17",
        "The theme sets the base and accent colours. Accent and text contrast adjust every surface and text colour in the app; the window behind this one is the preview.", (
        Group("Theme", "Scale and presentation", (
            F("theme", "select", "Theme", "The app's colour theme. Also available from the View menu; both change the same setting.",
              key="window/appearance", kind="str", default="slate", options=APPEARANCE_MODES),
            F("darkTone", "select", "Dark theme", "How dark the Slate theme is (and Auto when the system is dark). Slate is the original near-black; Soft and Dim lift it for screens that show it as almost black. The sun button in the top bar swaps between this and Light.",
              key="view/dark_tone", kind="str", default="soft", options=DARK_TONE_OPTIONS),
            F("gridFade", "select", "Fade behind the button bar", "A gradient from the bottom of the grid up past the floating button bar, so the last row does not end in a hard edge. Soft and Deep use the theme's colour; Dark is a black vignette.",
              key="view/grid_fade", kind="str", default="soft", options=GRID_FADE_OPTIONS),
            F("accent", "swatch", "Accent colour", "Replaces the theme's accent for selection, focus and the progress ring.",
              key="view/accent_hue", kind="int", default=-1),
            F("contrast", "slider", "Text contrast", "Raises or lowers the strength of text against the background.",
              key="view/text_contrast", kind="int", default=90, lo=70, hi=100, step=1, fmt="%"),
        )),
        Group("Photo review", "How rejected photos look", (
            _t("dimRejects", "Gray out rejected photos", "Dims and desaturates rejects in the grid and preview filmstrip. The Rejected tab's grid stays in full color. Does not hide decision badges.",
               "view/dim_rejected_photos", True),
        )),
    ))
    browsing = Section("browsing", "Browsing", "M3.5 7a2.5 2.5 0 012.5-2.5h12A2.5 2.5 0 0120.5 7v10a2.5 2.5 0 01-2.5 2.5H6A2.5 2.5 0 013.5 17zM9.5 4.5v15",
        "How the grid scrolls, how previews load, and how review moves between photos.", (
        Group("Navigation and preview", "How browsing feels", (
            _t("smooth", "Use free smooth scrolling", "Scrolls pixel by pixel instead of snapping by rows.", "view/free_smooth_scroll", False),
            F("preload", "step", "Preview preload", "Nearby images to preload while using the popout preview. Higher values can speed up rapid navigation but use more CPU and memory.",
              key="preview/preload_batch_size", kind="int", default=PRELOAD_DEFAULT, lo=0, hi=PRELOAD_MAX, step=2, fmt="images", zero="Off"),
        )),
        Group("Review flow", "How review moves between images", (
            _t("autoAdvance", "Advance after Winner, Keep or Reject", "Moves to the next image automatically after you mark the current one Winner, Keep or Reject.", "view/auto_advance", True),
        )),
    ))
    library = Section("library", "Library & folders", "M3.5 7.5a2 2 0 012-2h4l2 2.5h7a2 2 0 012 2v7.5a2 2 0 01-2 2h-13a2 2 0 01-2-2z",
        "Control folder navigation, automatic refresh, and the lightweight catalog data used for faster browsing.", (
        Group("Folder browsing", "Remember and refresh locations", (
            _t("hidden", "Show hidden folders", "Shows dot folders and hidden folders in the folder browser.", "view/show_hidden_folders", False),
            _t("oneBranch", "Keep only one branch expanded per level", "Opening a drive or folder collapses its expanded siblings at the same level. The active path stays open.",
               "view/single_drive_expansion", True),
            _t("watch", "Refresh the open folder when files change on disk", "Picks up files added, removed or renamed outside the app. Network and removable drives are checked when you open them and when you switch back.",
               "catalog/watch_current_folder", True),
            _t("restorePos", "Reopen folders where I left off", "Remembers the photo you were on in each folder across restarts. When off, folders open at their start.",
               "view/restore_folder_position", True),
        )),
        Group("Catalog", "Local browsing acceleration", (
            F("catalogInfo", "text", "Catalog summary", "Current catalog cache status and indexed-file summary."),
        )),
    ))
    ai = Section("ai", "AI culling", "M12 3.5l1.8 4.7 4.7 1.8-4.7 1.8L12 16.5l-1.8-4.7L5.5 10l4.7-1.8zM18.5 15.5l.8 2 2 .8-2 .8-.8 2-.8-2-2-.8 2-.8z",
        "How the AI culling uses this computer.", (
        Group("Processing", "Performance and visibility", (
            _t("cpuRank", "Rank survivors on the CPU when there is no GPU", "Off by default. Ranking uses two small AI models: automatic on a GPU, roughly 3 to 4 seconds per frame on a CPU.",
               "ai/cascade_rank_on_cpu", False),
        )),
    ))
    by_cat: dict = {}
    for attr, category, default, display in shortcut_registry:
        if attr in LISTED_ACTIONS:
            by_cat.setdefault(category, []).append(F("k:" + attr, "keys", display, "", default=SHELL_DEFAULTS.get(attr, default)))
    keys = Section("keys", "Shortcuts", "M5 6.5h14A2.5 2.5 0 0121.5 9v6a2.5 2.5 0 01-2.5 2.5H5A2.5 2.5 0 012.5 15V9A2.5 2.5 0 015 6.5zM6.5 10.5h.01M10 10.5h.01M13.5 10.5h.01M17 10.5h.01M7.5 14h9",
        "Customize keyboard commands used throughout Image Triage. Press Change, then the new chord. Backspace clears a binding. Conflicts are reported when you save.",
        tuple(Group(cat, "", tuple(rows)) for cat, rows in by_cat.items()))
    return (general, appearance, browsing, library, ai, keys)


def _shortcut_registry() -> tuple:
    try:
        from ..shortcuts import SHORTCUT_REGISTRY
        return SHORTCUT_REGISTRY
    except Exception:
        return ()


class SettingsModel(QObject):
    revisionChanged = Signal()      # any value, dirty flag or mode changed: bindings re-read
    savedChanged = Signal()
    previewChanged = Signal()       # theme, accent or contrast moved (the window behind is the live preview)
    aiScoringChanged = Signal()     # kept for the window that connects it; nothing changes the scoring values from Settings any more
    shortcutsChanged = Signal()     # the saved shortcuts changed: the keys the shell bound are read again
    closeRequested = Signal()

    def __init__(self, memory: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._memory = memory
        self._store: dict = {}
        self._layout: dict = {}
        self._qs = None
        if not memory:
            from ..app_identity import user_settings
            self._qs = user_settings()
        self._schema = build_schema(_DEMO_SHORTCUTS if memory else _shortcut_registry())
        self._fields = {row.id: row for sec in self._schema for g in sec.groups for row in g.rows}
        self._sessions = list(_DEMO_SESSIONS)
        self._overrides: dict = {}
        self._init: dict = {}
        self._vals: dict = {}
        self._revision = 0
        self._saved = False
        self._conflict = ""
        self._effective: dict = {}          # registry id -> the chord in force (the saved override, else the default); "" for none
        self._shortcut_revision = 0
        self.load()

    # -- window layout (not a settings row: the widths of the side panel and the inspector, remembered between runs) ------------------
    @Slot(str, float, result=float)
    def layoutWidth(self, name: str, default: float) -> float:
        if self._memory:
            return float(self._layout.get(name, default))
        try:
            return float(self._qs.value("window/" + name + "_width", default))
        except (TypeError, ValueError):
            return default

    @Slot(str, float)
    def setLayoutWidth(self, name: str, value: float) -> None:
        if self._memory:
            self._layout[name] = float(value)
            return
        self._qs.setValue("window/" + name + "_width", float(value))
        self._qs.sync()

    # -- storage ---------------------------------------------------------------------------------------------------------------
    def _read(self, f: F):
        if f.type == "keys":
            return self._overrides.get(f.id[2:], f.default or "")
        if f.type in ("text",):
            return None
        if self._memory or not f.key:
            return self._store.get(f.id, f.default)
        kind = {"bool": bool, "int": int, "float": float, "str": str}[f.kind]
        value = self._qs.value(f.key, f.default, kind)
        return self._normalise(f, value)

    def _normalise(self, f: F, value):
        try:
            if f.kind == "bool":
                return bool(value)
            if f.kind == "int":
                value = int(value)
            elif f.kind == "float":
                value = float(value)
            else:
                value = str(value)
        except (TypeError, ValueError):
            return f.default
        if f.options:
            allowed = {v for _l, v in f.options}
            if value not in allowed and f.id != "session":
                return f.default
        if f.type in ("slider", "step") and f.kind in ("int", "float"):
            value = max(f.lo, min(f.hi, value))
            if f.kind == "float":
                value = round(value, 2)
        return value

    @Slot()
    def load(self) -> None:
        if not self._memory:
            try:
                from ..shortcuts import load_shortcut_overrides
                self._overrides = dict(load_shortcut_overrides())
            except Exception:
                self._overrides = {}
            # The registry's loader drops a stored value equal to the registry default, which for these rows is "": a row the person cleared
            # would come back with the shell's default. Read what is stored for them directly.
            for attr, default in SHELL_DEFAULTS.items():
                key = "shortcuts/" + attr
                if self._qs.contains(key):
                    self._overrides[attr] = str(self._qs.value(key) or "").strip()
                if self._overrides.get(attr) == default:
                    self._overrides.pop(attr, None)
        if self._memory:
            self._store = {f.id: f.default for f in self._fields.values() if f.type not in ("text", "keys")}
            self._store["accent"] = 0          # the mockup shows the blue swatch chosen
            self._store["gridFade"] = "off"    # the grid mockup has no fade by default, same reason
            self._store["darkTone"] = "slate"  # the mockup's own near-black, so the pixel checks against it hold
        self._vals = {f.id: self._read(f) for f in self._fields.values() if f.type != "text"}
        self._init = dict(self._vals)
        self._rebuild_effective()
        self._revision += 1
        self.revisionChanged.emit()
        self.previewChanged.emit()

    @Slot(result=bool)
    def save(self) -> bool:
        if self._conflicts():
            return False
        for fid, value in self._vals.items():
            f = self._fields[fid]
            if value == self._init.get(fid):
                continue
            if f.type == "keys":
                continue
            if self._memory or not f.key:
                self._store[fid] = value
            else:
                self._qs.setValue(f.key, value)
        shortcut_ids = [fid for fid, f in self._fields.items() if f.type == "keys"]
        if any(self._vals[i] != self._init[i] for i in shortcut_ids):
            # what was saved for a registry row this page does not list is kept as it is
            overrides = {action: chord for action, chord in self._overrides.items() if "k:" + action not in self._fields}
            overrides.update({i[2:]: self._vals[i] for i in shortcut_ids if self._vals[i] != (self._fields[i].default or "")})
            self._overrides = overrides
            if not self._memory:
                from ..shortcuts import save_shortcut_overrides
                save_shortcut_overrides(overrides, preserve_empty=True)
            self._rebuild_effective()
            self._shortcut_revision += 1
            self.shortcutsChanged.emit()
        if not self._memory:
            self._qs.sync()
            if self._vals.get("perfLog") != self._init.get("perfLog"):
                from ..perf import perf_logger

                perf_logger().set_enabled(bool(self._vals.get("perfLog")), reason="settings")      # the Widgets app starts or stops the logger as it is switched
        self._init = dict(self._vals)
        self._saved = True
        self._revision += 1
        self.revisionChanged.emit()
        self.savedChanged.emit()
        return True

    def apply_ai_scoring(self) -> None:
        """Push the stored Likely winners, Review band and Base score weight into the classifier and the workflow, as the Widgets app does when
        it starts (`settings_controller.apply_cull_thresholds_to_classifier`, `apply_base_score_blend_to_workflow`). Settings no longer shows
        these three: they reach only the retired engine. A value saved by an earlier version is still honoured."""
        from ..ai_results import set_cull_thresholds
        from ..aiculler_workflow import set_base_score_blend_weight

        def clamped(fid: str, limits: tuple) -> int:
            try:
                stored = limits[2] if self._qs is None else self._qs.value(_AI_SCORING_KEYS[fid], limits[2])
                return max(limits[0], min(limits[1], int(stored)))
            except (TypeError, ValueError):
                return limits[2]

        keeper = max(0.0, 100.0 - clamped("keepTop", AI_KEEP_TOP))
        reject = max(0.0, keeper - clamped("reviewBand", AI_REVIEW_BAND))
        set_cull_thresholds(keeper_percentile=keeper, reject_percentile=reject)
        set_base_score_blend_weight(clamped("baseWeight", AI_BASE_WEIGHT) / 100.0)

    @Slot()
    def discard(self) -> None:
        self._vals = dict(self._init)
        self._saved = False
        self._conflict = ""
        self._revision += 1
        self.revisionChanged.emit()
        self.previewChanged.emit()

    @Slot(str)
    def resetSection(self, section_id: str) -> None:
        for sec in self._schema:
            if sec.id != section_id:
                continue
            for g in sec.groups:
                for f in g.rows:
                    if f.type in ("text",):
                        continue
                    self._vals[f.id] = f.default if f.type != "keys" else (f.default or "")
        self._saved = False
        self._conflict = ""
        self._revision += 1
        self.revisionChanged.emit()
        self.previewChanged.emit()

    # -- values ----------------------------------------------------------------------------------------------------------------
    @Slot()
    def toggleLight(self) -> None:
        """The top bar's sun button: swap between Light and the dark theme that was in use (Slate unless another dark one was chosen), and save it."""
        light_themes = ("light", "warm_neutral")
        current = str(self._vals.get("theme", "slate"))
        if current in light_themes:
            self.set("theme", self._last_dark if self._last_dark not in light_themes else "slate")
        else:
            if current != "auto":
                self._last_dark = current
            self.set("theme", "light")
        self.save()

    _last_dark = "slate"

    @Slot(str, "QVariant")
    def set(self, fid: str, value) -> None:
        f = self._fields.get(fid)
        if f is None:
            return
        if f.type != "keys":
            value = self._normalise(f, value) if f.kind != "bool" else bool(value)
        if self._vals.get(fid) == value:
            return
        self._vals[fid] = value
        self._saved = False
        self._conflict = ""
        self._revision += 1
        self.revisionChanged.emit()
        if fid in ("theme", "accent", "contrast", "darkTone", "gridFade"):
            self.previewChanged.emit()

    @Slot(str, result="QVariant")
    def value(self, fid: str):
        _ = self._revision         # QML bindings re-evaluate when the revision changes
        return self._vals.get(fid)

    @Slot(str, result=bool)
    def isDirty(self, fid: str) -> bool:
        return self._vals.get(fid) != self._init.get(fid)

    @Slot(str, result=bool)
    def sectionDirty(self, section_id: str) -> bool:
        return any(self.isDirty(f.id) for sec in self._schema if sec.id == section_id for g in sec.groups for f in g.rows if f.type != "text")

    @Slot(str, result=bool)
    def enabled(self, fid: str) -> bool:
        f = self._fields.get(fid)
        return not (f and f.dep) or bool(self._vals.get(f.dep))

    @Slot(str, result=str)
    def display(self, fid: str) -> str:
        f = self._fields[fid]
        v = self._vals.get(fid)
        if f.zero and v == f.lo:
            return f.zero
        if f.fmt == "%":
            return f"{int(v)}%"
        if f.fmt == "x":
            return f"{int(v)}x"
        if f.fmt == "images":
            return f"{int(v)} images"
        if f.fmt == "f2":
            return f"{float(v):.2f}"
        return str(int(v)) if f.kind == "int" else str(v)

    @Slot(str, result=str)
    def text(self, fid: str) -> str:
        if fid == "catalogInfo":
            return _compact_catalog_summary(self._catalog_text)
        return ""

    _catalog_text = _DEMO_CATALOG

    @Slot()
    def readCatalogSummary(self) -> None:
        """Fill the Catalog summary row from the shared catalog (`catalog.CatalogRepository.stats`, the lines `scan_controller.catalog_debug_summary` writes)."""
        if self._memory:
            return
        try:
            from ..catalog import CatalogRepository, catalog_cache_env_override

            stats = CatalogRepository().stats()
            override = catalog_cache_env_override()
            enabled = True if override is None else override
            lines = [f"Catalog cache reads: {'Enabled' if enabled else 'Disabled'}" + (" (environment override)" if override is not None else ""),
                     f"Folder watch: {'Enabled' if self._vals.get('watch', True) else 'Disabled'}"]
            if stats.error_message:
                lines.append(f"Catalog error: {stats.error_message}")
            else:
                lines += [f"Indexed folders: {stats.folder_count}", f"Indexed image bundles: {stats.record_count}",
                          f"Cached review features: {stats.feature_count}"]
                if stats.last_indexed_at:
                    lines.append(f"Last indexed: {stats.last_indexed_at}")
        except Exception as exc:                          # a catalog that cannot be opened says so instead of showing sample numbers
            lines = [f"The catalog could not be read: {exc}"]
        self.setCatalogSummary("", "\n".join(lines))

    @Slot(str, str)
    def setCatalogSummary(self, _unused: str, text: str) -> None:
        self._catalog_text = text
        self._revision += 1
        self.revisionChanged.emit()

    # -- shortcuts -------------------------------------------------------------------------------------------------------------
    @Slot(int, int, result=str)
    def chord(self, key: int, modifiers: int) -> str:
        """The portable text of a pressed chord (``Ctrl+Shift+N``), or "" for a bare modifier."""
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_unknown):
            return ""
        return QKeySequence(key | modifiers).toString(QKeySequence.SequenceFormat.PortableText)

    @staticmethod
    def _portable(text: str) -> str:
        return QKeySequence(text).toString(QKeySequence.SequenceFormat.PortableText) if text else ""

    def _rebuild_effective(self) -> None:
        """The chord in force for each registry row: what Settings > Shortcuts saved, else the Widgets default."""
        defaults = {row[0]: row[2] for row in _shortcut_registry()}                  # the real registry's defaults, also while the sample page shows its short list
        defaults.update(SHELL_DEFAULTS)
        defaults.update({f.id[2:]: f.default or "" for f in self._fields.values() if f.type == "keys"})
        self._effective = {action: self._portable(self._overrides.get(action, default)) for action, default in defaults.items()}

    @Slot(str, result=str)
    def seq(self, action: str) -> str:
        """The shortcut in force for a registry row ("" for none); QML bindings read `shortcutRevision` first so they follow a save."""
        return self._effective.get(action, "")

    @Slot(str, int, int, result=bool)
    def keyIs(self, action: str, key: int, modifiers: int) -> bool:
        """Is this key press the shortcut in force for `action`? The grid's and the viewer's own keys ask this instead of naming a key."""
        want = self._effective.get(action, "")
        return bool(want) and want == self.chord(key, modifiers & ~KEYPAD_MODIFIER)

    @Property("QVariantList", notify=shortcutsChanged)
    def shortcutBindings(self):
        """[{cmd, seq}] for every shortcut the shell carries out (BOUND_ACTIONS) that has a chord: what Main.qml binds."""
        return [{"cmd": cmd, "seq": self._effective[action]} for action, cmd in BOUND_ACTIONS if self._effective.get(action)]

    shortcutRevision = Property(int, lambda self: self._shortcut_revision, notify=shortcutsChanged)

    @Property("QVariantMap", notify=shortcutsChanged)
    def shortcutMap(self):
        return dict(self._effective)

    @Slot(str, str, result=str)
    def commandShortcut(self, command: str, fallback: str) -> str:
        actions = {cmd: action for action, cmd in BOUND_ACTIONS}
        actions.update({"review.accept_selection": "accept_selection", "review.reject_selection": "reject_selection",
                        "review.keep_selection": "keep_at_cursor", "library.add_selected_to_collection": "collect_at_cursor",
                        "review.open_preview": "open_preview"})
        action = actions.get(command)
        if action == "open_preview" and not self.seq(action):
            return fallback  # Return and Space always open the current photo.
        return self.seq(action) if action else fallback

    def _conflicts(self) -> str:
        seen: dict = {}
        for fid, f in self._fields.items():
            if f.type != "keys":
                continue
            chord = (self._vals.get(fid) or "").strip()
            if not chord:
                continue
            fixed = FIXED_KEYS.get(self._portable(chord))
            if fixed and chord != (f.default or ""):              # a row changed onto a key the shell binds itself (a default the row already had is not a change)
                self._conflict = f"{chord} is used by the shell for “{fixed}” and cannot also be {f.label}"
                self._revision += 1
                self.revisionChanged.emit()
                return self._conflict
            if chord in seen:
                self._conflict = f"{chord} is used by both {self._fields[seen[chord]].label} and {f.label}"
                self._revision += 1
                self.revisionChanged.emit()
                return self._conflict
            seen[chord] = fid
        return ""

    @Slot(result=str)
    def conflict(self) -> str:
        _ = self._revision
        return self._conflict

    # -- schema for QML --------------------------------------------------------------------------------------------------------
    def _row(self, f: F, section: str) -> dict:
        options = []
        if f.id == "session":
            options = [{"label": s, "value": s} for s in self._sessions]
        else:
            options = [{"label": lab, "value": val} for lab, val in f.options]
        return {
            "id": f.id, "type": f.type, "label": f.label, "desc": f.desc, "options": options, "lo": f.lo, "hi": f.hi, "step": f.step,
            "btn": f.btn, "dep": f.dep, "section": section, "zero": f.zero,
            "default": f.default if f.type == "keys" else None,
        }

    @Slot(result="QVariantList")
    def sections(self) -> list:
        return [{"id": s.id, "name": s.name, "icon": s.icon, "intro": s.intro} for s in self._schema]

    @Slot(str, str, result="QVariantList")
    def page(self, section_id: str, query: str) -> list:
        q = query.strip().lower()
        out = []
        for sec in self._schema:
            if not q and sec.id != section_id:
                continue
            groups = []
            for g in sec.groups:
                rows = [self._row(f, sec.name) for f in g.rows if not q or q in f"{f.label} {f.desc} {f.btn}".lower()]
                if rows:
                    groups.append({"title": g.title if not q else sec.name, "hint": g.hint if not q else "", "rows": rows})
            if q:
                merged = [r for gr in groups for r in gr["rows"]]
                if merged:
                    out.append({"title": sec.name, "hint": "", "rows": merged})
            else:
                out = groups
        return out

    @Slot(str, result="QVariantMap")
    def counts(self, query: str) -> dict:
        q = query.strip().lower()
        return {sec.id: sum(1 for g in sec.groups for f in g.rows if q and q in f"{f.label} {f.desc} {f.btn}".lower()) for sec in self._schema}

    @Slot(str, result="QVariantMap")
    def pageInfo(self, section_id: str) -> dict:
        for sec in self._schema:
            if sec.id == section_id:
                return {"title": sec.name, "intro": sec.intro}
        return {"title": "", "intro": ""}

    # -- properties ------------------------------------------------------------------------------------------------------------
    appVersion = Property(str, lambda self: "2.4.0 · PhotoCraft 0.9.2" if self._memory else _app_version(), constant=True)
    revision = Property(int, lambda self: self._revision, notify=revisionChanged)
    dirtyCount = Property(int, lambda self: sum(1 for k in self._vals if self._vals[k] != self._init.get(k)), notify=revisionChanged)
    saved = Property(bool, lambda self: self._saved, notify=revisionChanged)
    themeMode = Property(str, lambda self: str(self._vals.get("theme", "slate")), notify=previewChanged)
    gridFade = Property(str, lambda self: str(self._vals.get("gridFade", "soft")), notify=previewChanged)
    darkTone = Property(str, lambda self: str(self._vals.get("darkTone", "soft")), notify=previewChanged)
    accentIndex = Property(int, lambda self: int(self._vals.get("accent", -1)), notify=previewChanged)
    contrast = Property(int, lambda self: int(self._vals.get("contrast", 90)), notify=previewChanged)


def _app_version() -> str:
    try:
        from .._version import __version__
        return str(__version__)
    except Exception:
        return ""


def _compact_catalog_summary(summary: str) -> str:
    lines = [line.strip() for line in (summary or "").splitlines() if line.strip()]
    if not lines:
        return "Catalog database has not been created yet."
    wanted = ("Catalog cache reads:", "Folder watch:", "Indexed files:", "Indexed image bundles:", "Cached review features:")
    compact = [line for line in lines if line.startswith(wanted)]
    return "\n".join(compact[:5]) if compact else lines[0]


# the same registry rows as image_triage/shortcuts.py, for the in-memory (mockup comparison) model
_DEMO_SHORTCUTS = (
    ("open_folder", "File", "Ctrl+O", "Open Folder..."), ("refresh_folder", "File", "F5", "Refresh Folder"),
    ("new_folder", "File", "Ctrl+Shift+N", "New Folder..."), ("workflow_settings", "File", "Ctrl+,", "Settings..."),
    ("undo", "Edit", "Ctrl+Z", "Undo"), ("rename_selection", "Edit", "F2", "Rename Image..."),
    ("accept_selection", "Review", "W", "Mark Winner"), ("reject_selection", "Review", "R", "Reject Selection"),
    ("keep_at_cursor", "Review", "K", "Keep Photo At Cursor"), ("compare_mode", "Review", "C", "Compare"),
    ("open_command_palette", "Workspace", "Ctrl+K", "Command Palette..."),
)
