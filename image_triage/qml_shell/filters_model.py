"""Advanced filters and saved searches for the QML shell.

The query, its wording (`active_filter_labels`), the built-in presets and the saved-search format are the Widgets app's own
(`filtering.py`); saved searches are stored under the same settings key (`filters/saved_queries`) so both front ends share them.
The shell can only act on part of a query (decisions, rating and RAW/JPEG on the sample data it has); the rest is stored and shown
but not applied. `unsupported` says how many set filters that is, and docs/qml_unwired.md lists them.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

from PySide6.QtCore import Property, QObject, Signal, Slot

from ..filtering import (
    AIStateFilter,
    FileTypeFilter,
    OrientationFilter,
    RecordFilterQuery,
    ReviewStateFilter,
    SavedFilterPreset,
    active_filter_labels,
    builtin_filter_presets,
    deserialize_saved_filter_preset,
    serialize_saved_filter_preset,
)
from ..models import FilterMode

SAVED_KEY = "filters/saved_queries"

_ENUMS = {"quick": FilterMode, "ftype": FileTypeFilter, "review": ReviewStateFilter, "ai": AIStateFilter, "orient": OrientationFilter}
_QUERY_FIELD = {"quick": "quick_filter", "ftype": "file_type", "review": "review_state", "ai": "ai_state", "orient": "orientation"}
_TEXT = ("camera", "lens", "tags", "folder", "after", "before", "isoMin", "isoMax", "focMin", "focMax")
# the chips, in the order active_filter_labels words them; each is the form fields it clears
_CHIP_GROUPS = (
    ("quick", ("quick",)), ("conf", ("conf",)), ("folder", ("folder",)), ("ftype", ("ftype",)), ("review", ("review",)), ("ai", ("ai",)),
    ("camera", ("camera",)), ("lens", ("lens",)), ("tags", ("tags",)), ("rating", ("rating",)), ("orient", ("orient",)),
    ("after", ("after",)), ("before", ("before",)), ("iso", ("isoMin", "isoMax")), ("focal", ("focMin", "focMax")),
)
# what the shell's sample photos can be filtered on
_SUPPORTED_QUICK = (FilterMode.ALL, FilterMode.WINNERS, FilterMode.REJECTS, FilterMode.UNREVIEWED)
_SUPPORTED_FILE = (FileTypeFilter.ALL, FileTypeFilter.RAW, FileTypeFilter.JPEG)

_DEMO_MINE = (
    SavedFilterPreset("Wedding keepers", RecordFilterQuery(review_state=ReviewStateFilter.ACCEPTED, min_rating=4, camera_text="Sony")),
    SavedFilterPreset("Low light", RecordFilterQuery(iso_min=3200, focal_max=50.0)),
)


def _number(text: str) -> float:
    try:
        value = float(str(text).replace(",", "").strip())
    except ValueError:
        return 0.0
    return value if value > 0 else 0.0


def _date(text: str) -> date | None:
    try:
        return date.fromisoformat(str(text).strip())
    except ValueError:
        return None


def _fmt(value: float) -> str:
    return "" if value <= 0 else f"{value:g}"


class FiltersModel(QObject):
    revisionChanged = Signal()
    appliedChanged = Signal()

    def __init__(self, photos, memory: bool = False, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._photos = photos
        self._memory = memory
        self._form: dict[str, object] = self._blank()
        self._applied = self._query()
        self._sel = ""
        self._mine: list[SavedFilterPreset] = list(_DEMO_MINE) if memory else self._load_mine()
        self._revision = 0

    # -- form <-> query --------------------------------------------------------------------------------------------------------
    @staticmethod
    def _blank() -> dict[str, object]:
        form: dict[str, object] = {key: next(iter(enum)).value for key, enum in _ENUMS.items()}     # the first member of each is "any"
        form.update(rating=0, conf=0.0, **{key: "" for key in _TEXT})
        return form

    def _query(self) -> RecordFilterQuery:
        f = self._form

        def enum(key):
            return _ENUMS[key](f[key])

        return RecordFilterQuery(
            quick_filter=enum("quick"), min_search_confidence=float(f["conf"]), file_type=enum("ftype"), review_state=enum("review"),
            ai_state=enum("ai"), folder_text=str(f["folder"]).strip(), camera_text=str(f["camera"]).strip(), lens_text=str(f["lens"]).strip(),
            tag_text=str(f["tags"]).strip(), min_rating=int(f["rating"]), orientation=enum("orient"),
            captured_after=_date(f["after"]), captured_before=_date(f["before"]),
            iso_min=int(_number(f["isoMin"])), iso_max=int(_number(f["isoMax"])), focal_min=_number(f["focMin"]), focal_max=_number(f["focMax"]),
        )

    def _load(self, query: RecordFilterQuery) -> None:
        self._form = {
            "quick": query.quick_filter.value, "ftype": query.file_type.value, "review": query.review_state.value, "ai": query.ai_state.value,
            "orient": query.orientation.value, "rating": query.min_rating, "conf": query.min_search_confidence,
            "camera": query.camera_text, "lens": query.lens_text, "tags": query.tag_text, "folder": query.folder_text,
            "after": query.captured_after.isoformat() if query.captured_after else "",
            "before": query.captured_before.isoformat() if query.captured_before else "",
            "isoMin": _fmt(query.iso_min), "isoMax": _fmt(query.iso_max), "focMin": _fmt(query.focal_min), "focMax": _fmt(query.focal_max),
        }

    # -- the form --------------------------------------------------------------------------------------------------------------
    @Slot(str, result="QVariant")
    def get(self, key: str):
        return self._form.get(key, "")

    @Slot(str, "QVariant")
    def set(self, key: str, value) -> None:
        if key not in self._form:
            return
        if key == "rating":
            value = max(0, min(5, int(value)))
        elif key == "conf":
            value = max(0.0, min(1.0, round(float(value), 2)))
        elif key in _ENUMS:
            value = _ENUMS[key](value).value
        else:
            value = str(value)
        if self._form[key] != value:
            self._form[key] = value
            self._sel = ""
            self._bump()

    @Slot(str, result="QVariantList")
    def options(self, key: str) -> list:
        return [{"label": member.value, "value": member.value} for member in _ENUMS[key]]

    @Slot(str, result=bool)
    def dateBad(self, key: str) -> bool:
        text = str(self._form.get(key, "")).strip()
        return bool(text) and _date(text) is None

    @Slot()
    def reset(self) -> None:
        self._form = self._blank()
        self._sel = ""
        self._bump()

    # -- chips and wording -----------------------------------------------------------------------------------------------------
    @Slot(result="QVariantList")
    def chips(self) -> list:
        """One removable chip per active filter, worded by the Widgets app's own `active_filter_labels`."""
        query = self._query()
        blank = RecordFilterQuery()
        chips = []
        for group, keys in _CHIP_GROUPS:
            probe = blank
            for key in keys:
                probe = self._only(probe, query, key)
            labels = active_filter_labels(probe)
            if labels:
                chips.append({"label": labels[0], "keys": list(keys)})
        return chips

    @staticmethod
    def _only(probe: RecordFilterQuery, query: RecordFilterQuery, key: str) -> RecordFilterQuery:
        mapping = {
            "quick": ("quick_filter",), "conf": ("min_search_confidence",), "folder": ("folder_text",), "ftype": ("file_type",),
            "review": ("review_state",), "ai": ("ai_state",), "camera": ("camera_text",), "lens": ("lens_text",), "tags": ("tag_text",),
            "rating": ("min_rating",), "orient": ("orientation",), "after": ("captured_after",), "before": ("captured_before",),
            "isoMin": ("iso_min",), "isoMax": ("iso_max",), "focMin": ("focal_min",), "focMax": ("focal_max",),
        }
        return replace(probe, **{name: getattr(query, name) for name in mapping[key]})

    @Slot(str)
    def clear(self, keys: str) -> None:
        blank = self._blank()
        for key in keys.split(","):
            if key in blank:
                self._form[key] = blank[key]
        self._sel = ""
        self._bump()

    # -- saved searches --------------------------------------------------------------------------------------------------------
    def _load_mine(self) -> list[SavedFilterPreset]:
        try:
            from ..app_identity import user_settings

            raw = user_settings().value(SAVED_KEY, "", str)
            payload = json.loads(raw) if isinstance(raw, str) and raw else []
        except Exception:
            return []
        presets: list[SavedFilterPreset] = []
        seen: set[str] = set()
        for item in payload if isinstance(payload, list) else []:
            preset = deserialize_saved_filter_preset(item if isinstance(item, dict) else None)
            if preset is not None and preset.name.casefold() not in seen:
                seen.add(preset.name.casefold())
                presets.append(preset)
        return presets

    def _save_mine(self) -> None:
        if self._memory:
            return
        try:
            from ..app_identity import user_settings

            user_settings().setValue(SAVED_KEY, json.dumps([serialize_saved_filter_preset(p) for p in self._mine]))
        except Exception:
            pass

    @Slot(result="QVariantMap")
    def presets(self) -> dict:
        return {
            "builtin": [{"id": f"b:{p.name}", "name": p.name} for p in builtin_filter_presets()],
            "mine": [{"id": f"m:{p.name}", "name": p.name} for p in self._mine],
        }

    @Slot(str)
    def pick(self, preset_id: str) -> None:
        pool = builtin_filter_presets() if preset_id.startswith("b:") else self._mine
        for preset in pool:
            if f"{preset_id[:2]}{preset.name}" == preset_id:
                self._load(preset.query)
                self._sel = preset_id
                self._bump()
                return

    @Slot(str, result=bool)
    def clash(self, name: str) -> bool:
        wanted = name.strip().casefold()
        return bool(wanted) and any(p.name.casefold() == wanted for p in self._mine)

    @Slot(str, result=bool)
    def saveAs(self, name: str) -> bool:
        """Save the form's filters under `name`, replacing a saved search of the same name (the caller has asked)."""
        name = name.strip()
        query = self._query()
        if not name or not query.has_active_filters:
            return False
        preset = SavedFilterPreset(name=name, query=query)
        for index, existing in enumerate(self._mine):
            if existing.name.casefold() == name.casefold():
                self._mine[index] = preset
                break
        else:
            self._mine.append(preset)
        self._save_mine()
        self._sel = f"m:{name}"
        self._bump()
        return True

    @Slot(str)
    def remove(self, name: str) -> None:
        kept = [p for p in self._mine if p.name != name]
        if len(kept) != len(self._mine):
            self._mine = kept
            if self._sel == f"m:{name}":
                self._sel = ""
            self._save_mine()
            self._bump()

    @Property(str, notify=revisionChanged)
    def selected(self) -> str:
        return self._sel

    @Property(bool, notify=revisionChanged)
    def canSave(self) -> bool:
        return self._query().has_active_filters

    # -- applying --------------------------------------------------------------------------------------------------------------
    @staticmethod
    def spec(query: RecordFilterQuery) -> dict:
        """The part of a query the shell's photo model can act on."""
        decisions: set[str] | None = None

        def narrow(allowed: set[str]) -> None:
            nonlocal decisions
            decisions = allowed if decisions is None else decisions & allowed

        quick = {FilterMode.WINNERS: {"win"}, FilterMode.REJECTS: {"rej"}, FilterMode.UNREVIEWED: {""}}.get(query.quick_filter)
        review = {ReviewStateFilter.ACCEPTED: {"win", "keep"}, ReviewStateFilter.REJECTED: {"rej"}, ReviewStateFilter.UNREVIEWED: {""}}.get(query.review_state)
        for allowed in (quick, review):
            if allowed is not None:
                narrow(allowed)
        file = {FileTypeFilter.RAW: "raw", FileTypeFilter.JPEG: "jpeg"}.get(query.file_type)
        return {"decisions": decisions, "min_rating": query.min_rating, "file": file}

    @staticmethod
    def unsupportedCount(query: RecordFilterQuery) -> int:
        n = 0
        n += query.quick_filter not in _SUPPORTED_QUICK
        n += query.file_type not in _SUPPORTED_FILE
        n += query.ai_state != AIStateFilter.ALL
        n += query.orientation != OrientationFilter.ALL
        n += bool(query.folder_text or query.camera_text or query.lens_text or query.tag_text)
        n += query.captured_after is not None or query.captured_before is not None
        n += query.iso_min > 0 or query.iso_max > 0
        n += query.focal_min > 0 or query.focal_max > 0
        n += query.min_search_confidence > 0
        return int(n)

    @Property(int, notify=revisionChanged)
    def unsupported(self) -> int:
        return self.unsupportedCount(self._query())

    @Property(int, notify=revisionChanged)
    def matchCount(self) -> int:
        return self._photos.count_matching(self.spec(self._query()))

    @Property(int, notify=revisionChanged)
    def filterCount(self) -> int:
        return len(active_filter_labels(self._query()))

    @Property(bool, notify=revisionChanged)
    def dirty(self) -> bool:
        return self._query() != self._applied

    @Slot()
    def begin(self) -> None:
        """Opening the window shows the filters the grid is using."""
        self._load(self._applied)
        self._sel = ""
        self._bump()

    @Slot()
    def apply(self) -> None:
        self._applied = self._query()
        self._photos.apply_advanced(self.spec(self._applied))
        self.appliedChanged.emit()
        self._bump()

    @Slot()
    def clearApplied(self) -> None:
        """The grid's own 'clear filters' also clears these."""
        self._applied = RecordFilterQuery()
        self._load(self._applied)
        self._sel = ""
        self._bump()

    def _bump(self) -> None:
        self._revision += 1
        self.revisionChanged.emit()

    @Property(int, notify=revisionChanged)
    def revision(self) -> int:
        return self._revision
