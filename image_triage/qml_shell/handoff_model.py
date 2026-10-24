"""Deliver / Handoff Builder for the QML shell.

The recipes, their wording and their storage are the Widgets app's own: `workflows.built_in_workflow_recipes`, `recipe_summary_lines`,
`recipe_key_for_name`, the resize presets, the convert formats, the archive formats, and the saved recipes under the same settings key
(`workflow/recipes`) as `window.MainWindow`, so a recipe saved here shows up there and the other way round. The scope is every photo
the grid shows.

**Running is not connected yet.** `run()` hands the recipe to the transfer cards (transfer_model.py), which play a scripted progress; no
file is written, copied, moved or archived. docs/qml_unwired.md lists this.
"""
from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot

from ..app_identity import user_settings
from ..archive_ops import ARCHIVE_FORMATS
from ..image_convert import convert_formats
from ..image_resize import resize_presets
from ..workflows import (
    RECIPE_CONTENT_BUNDLE,
    RECIPE_CONTENT_EXPORT,
    RECIPE_TRANSFER_ARCHIVE,
    RECIPE_TRANSFER_MOVE,
    WorkflowRecipe,
    built_in_workflow_recipes,
    dump_saved_workflow_recipes,
    load_saved_workflow_recipes,
    recipe_key_for_name,
    recipe_summary_lines,
)

RECIPES_KEY = "workflow/recipes"          # window.MainWindow.WORKFLOW_RECIPES_KEY
_ICON_EXPORT = "M12 15V4M8 8l4-4 4 4M5 14v4a2 2 0 002 2h10a2 2 0 002-2v-4"
_ICON_BUNDLE = "M8 7h11v13H8zM5 4h11v3"
_ICON_PACKAGE = "M3 7l9-4 9 4v10l-9 4-9-4zM3 7l9 4 9-4M12 11v10"
_ICON_FOLDER = "M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z"
_ICON_STRIP = "M5 5l14 14M9 4h6l1 3h4v3M4 10v8a2 2 0 002 2h10"
_ICON_LIST = "M4 6h16M4 12h16M4 18h10"
_ICON_PIN = "M12 21s-7-5.6-7-11a7 7 0 1114 0c0 5.4-7 11-7 11zM12 12a2 2 0 100-4 2 2 0 000 4"
_ICON_WARN = "M12 4l9 16H3zM12 10v4M12 17.5v.01"
_DEMO_SAVED = (WorkflowRecipe(key="wedding_handoff", name="Wedding handoff", description="Full bundles per shot, moved to the archive drive.",
                              content_mode=RECIPE_CONTENT_BUNDLE, transfer_mode=RECIPE_TRANSFER_MOVE, destination_subfolder="Wedding",
                              group_by_record_folder=True),)
_DEMO_ROOT = "D:\\Photos\\2026\\Georgia trip\\Kutaisi"


def _values(recipe: WorkflowRecipe) -> dict:
    return {"name": recipe.name, "desc": recipe.description, "content": recipe.content_mode, "transfer": recipe.transfer_mode,
            "sub": recipe.destination_subfolder, "group": recipe.group_by_record_folder, "resize": recipe.resize_preset_key,
            "convert": recipe.convert_suffix, "strip": recipe.strip_metadata, "pre": recipe.rename_prefix, "suf": recipe.rename_suffix,
            "arch": recipe.archive_after_export, "archFmt": recipe.archive_format}


class HandoffModel(QObject):
    revisionChanged = Signal()
    runRequested = Signal(str, int, str, str)          # kind, item count, from folder, to folder

    def __init__(self, photos, memory: bool = False, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._photos = photos
        self._memory = memory
        self._settings = None if memory else user_settings()
        self._saved: list[WorkflowRecipe] = list(_DEMO_SAVED) if memory else self._load_saved()
        self._builtin = built_in_workflow_recipes()
        self._recipe_id = "builtin:" + self._builtin[0].key
        self._v = _values(self._builtin[0])
        self._root = _DEMO_ROOT if memory else ""
        self._revision = 0
        photos.countsChanged.connect(self._bump)

    # -- storage (the Widgets app's key) ---------------------------------------------------------------------------------------
    def _load_saved(self) -> list[WorkflowRecipe]:
        return load_saved_workflow_recipes(str(self._settings.value(RECIPES_KEY, "", str)))

    def _store_saved(self) -> None:
        if self._settings is not None:
            self._settings.setValue(RECIPES_KEY, dump_saved_workflow_recipes(self._saved))

    def _bump(self) -> None:
        self._revision += 1
        self.revisionChanged.emit()

    revision = Property(int, lambda self: self._revision, notify=revisionChanged)

    # -- lifecycle ------------------------------------------------------------------------------------------------------------
    @Slot()
    def begin(self) -> None:
        """Opening the window: the first built-in recipe, destination root = the folder being viewed (as the Widgets dialog starts)."""
        self._saved = list(_DEMO_SAVED) if self._memory else self._load_saved()
        self._recipe_id = "builtin:" + self._builtin[0].key
        self._v = _values(self._builtin[0])
        if not self._memory:
            self._root = self._photos.folderPath
        self._bump()

    @Slot(str)
    def setRecipe(self, recipe_id: str) -> None:
        recipe = self._find(recipe_id)
        if recipe is None:
            self._recipe_id = "custom"
        else:
            self._recipe_id = recipe_id
            self._v = _values(recipe)
        self._bump()

    def _find(self, recipe_id: str) -> WorkflowRecipe | None:
        origin, _, key = recipe_id.partition(":")
        pool = self._builtin if origin == "builtin" else self._saved if origin == "saved" else ()
        return next((r for r in pool if r.key == key), None)

    # -- fields ---------------------------------------------------------------------------------------------------------------
    @Slot(str, result="QVariant")
    def get(self, key: str):
        if key == "load":
            return self._recipe_id
        return self._root if key == "root" else self._v.get(key, "")

    @Slot(str, "QVariant")
    def set(self, key: str, value) -> None:
        if key == "root":
            self._root = str(value)
        else:
            self._v[key] = bool(value) if key in ("group", "strip", "arch") else str(value)
            self._recipe_id = "custom"          # editing a field makes it a custom recipe, as in the mockup
        self._bump()

    @Slot(str, result="QVariantList")
    def options(self, key: str) -> list:
        if key == "load":
            return ([{"value": "custom", "label": "Custom"}] + [{"value": f"builtin:{r.key}", "label": f"Built-in: {r.name}"} for r in self._builtin]
                    + [{"value": f"saved:{r.key}", "label": f"Saved: {r.name}"} for r in self._saved])
        if key == "resize":
            return [{"value": "", "label": "Keep Original Size"}] + [{"value": p.key, "label": p.name} for p in resize_presets()]
        if key == "convert":
            return [{"value": "", "label": "Keep Original Format"}] + [{"value": f.suffix, "label": f.name} for f in convert_formats()]
        if key == "archFmt":
            return [{"value": f.key, "label": f.label} for f in ARCHIVE_FORMATS]
        return []

    @Slot(result="QVariantList")
    def contentLabels(self) -> list:
        return ["Export deliverables", "Full bundle"]

    @Slot(result="QVariantList")
    def transferLabels(self) -> list:
        return ["Copy", "Move", "Archive"]

    # -- derived state --------------------------------------------------------------------------------------------------------------
    def _recipe(self) -> WorkflowRecipe:
        v = self._v
        name = " ".join(str(v["name"]).split()) or "Export Recipe"
        return WorkflowRecipe(key=recipe_key_for_name(name) or "workflow_recipe", name=name, description=str(v["desc"]).strip(), content_mode=v["content"],
                              transfer_mode=v["transfer"], destination_subfolder=str(v["sub"]).strip(), group_by_record_folder=bool(v["group"]),
                              archive_after_export=bool(v["arch"]), archive_format=v["archFmt"] or "zip", resize_preset_key=v["resize"],
                              convert_suffix=v["convert"], strip_metadata=bool(v["strip"]), rename_prefix=v["pre"], rename_suffix=v["suf"])

    @property
    def _export(self) -> bool:
        return self._v["content"] == RECIPE_CONTENT_EXPORT

    @Slot(str, result=bool)
    def enabled(self, key: str) -> bool:
        """Which fields apply to the chosen content (the Widgets dialog's `_refresh_mode_visibility`)."""
        export = self._export
        if key == "transfer" or key == "group":
            return not export
        if key in ("resize", "convert", "affixes", "strip", "arch"):
            return export
        if key == "archFmt":
            return (export and bool(self._v["arch"])) or not export
        return True

    @property
    def count(self) -> int:
        return self._photos.shown

    countText = Property(str, lambda self: f"{self.count} image bundle{'s' if self.count != 1 else ''}", notify=revisionChanged)
    title = Property(str, lambda self: " ".join(str(self._v["name"]).split()) or "Export Recipe", notify=revisionChanged)
    blurb = Property(str, lambda self: str(self._v["desc"]).strip() or "A custom recipe.", notify=revisionChanged)
    recipeId = Property(str, lambda self: self._recipe_id, notify=revisionChanged)
    canDelete = Property(bool, lambda self: self._recipe_id.startswith("saved:"), notify=revisionChanged)
    saveLabel = Property(str, lambda self: "Update saved" if self._recipe_id.startswith("saved:") else "Save recipe", notify=revisionChanged)
    hasRoot = Property(bool, lambda self: bool(self._root.strip()), notify=revisionChanged)
    destructive = Property(bool, lambda self: self._v["content"] == RECIPE_CONTENT_BUNDLE and self._v["transfer"] == RECIPE_TRANSFER_MOVE, notify=revisionChanged)

    def _destination(self) -> str:
        root = self._root.strip()
        if not root:
            return "(choose a destination root)"
        sub = str(self._v["sub"]).strip()
        return root + ("\\" + sub if sub else "")

    @Slot(result="QVariantList")
    def steps(self) -> list:
        recipe = self._recipe()
        out = [{"tone": "", "d": _ICON_LIST, "head": "Selected", "text": f"{self.count} selected image bundle(s)."}]
        for index, line in enumerate(recipe_summary_lines(recipe)):
            if line.startswith("Then package"):
                d, head = _ICON_PACKAGE, "Package"
            elif line.startswith("Default destination"):
                d, head = _ICON_FOLDER, "Destination"
            elif "strip" in line:
                d, head = _ICON_STRIP, "Metadata"
            else:
                d, head = (_ICON_EXPORT if self._export else _ICON_BUNDLE), ("Export" if self._export else "Handoff") if index == 0 else "Metadata"
            out.append({"tone": "", "d": d, "head": head, "text": line})
        dest = self._destination()
        if self._export:
            where = f"Exports land in: {dest}"
        elif recipe.transfer_mode == RECIPE_TRANSFER_ARCHIVE:
            where = f"Archive will be created from the selected bundles near: {dest}"
        else:
            where = f"Bundle handoff root: {dest}"
        out.append({"tone": "acc" if self.hasRoot else "warn", "d": _ICON_PIN, "head": "Where it goes", "text": where})
        if self.destructive:
            out.append({"tone": "warn", "d": _ICON_WARN, "head": "Destructive", "text": "This recipe moves source bundles and is treated as destructive."})
        return out

    foot = Property(str, lambda self: f"Ready to run on {self.countText}." if self.hasRoot else "Choose a destination root before running the workflow.", notify=revisionChanged)

    # -- save and delete (the Widgets dialog's rules) --------------------------------------------------------------------------------
    @Slot()
    def save(self) -> None:
        recipe = self._recipe()
        index = next((i for i, r in enumerate(self._saved) if r.key == recipe.key), None)
        if index is None:
            index = next((i for i, r in enumerate(self._saved) if r.name.casefold() == recipe.name.casefold()), None)
        if index is None:
            self._saved.append(recipe)
        else:
            self._saved[index] = recipe
        self._store_saved()
        self._recipe_id = f"saved:{recipe.key}"
        self._v = _values(recipe)
        self._bump()

    @Slot()
    def remove(self) -> None:
        if not self._recipe_id.startswith("saved:"):
            return
        key = self._recipe_id.partition(":")[2]
        self._saved = [r for r in self._saved if r.key != key]
        self._store_saved()
        self._recipe_id = "custom"
        self._bump()

    # -- run ------------------------------------------------------------------------------------------------------------------------
    @Slot(result=str)
    def runKind(self) -> str:
        """What the transfer cards call this run: export, archive, copy or move."""
        v = self._v
        if v["content"] == RECIPE_CONTENT_EXPORT:
            return "archive" if v["arch"] else "export"
        return {RECIPE_TRANSFER_ARCHIVE: "archive", RECIPE_TRANSFER_MOVE: "move"}.get(v["transfer"], "copy")

    @Slot()
    def run(self) -> None:
        """Hand the recipe to the transfer cards (which ask first when it moves files)."""
        if not self.hasRoot:
            return
        self.runRequested.emit(self.runKind(), self.count, self._photos.folderPath or _DEMO_ROOT, self._destination())
