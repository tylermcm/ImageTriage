"""Command palette data for the QML shell.

The ranking is the Widgets palette's own (`ui.command_palette._rank_commands`, so a query orders results identically); shortcuts come
from the shortcut registry plus the user's overrides; recent commands use the same settings key as the Widgets window
(`commands/recent`). The command list is the Widgets controller's list of ids, sections, titles and keywords
(`command_palette_controller.py`), minus the entries that need a live window (per-collection, per-preset and per-panel commands).
Most commands are not wired to an action in the shell yet; see docs/qml_unwired.md.
"""
from __future__ import annotations

from PySide6.QtCore import Property, QObject, Signal, Slot

RECENT_KEY = "commands/recent"
RECENT_MAX = 12

# (id, section, title, shortcut registry id, keywords). Titles and keywords are the Widgets controller's.
_COMMANDS: tuple[tuple[str, str, str, str, tuple[str, ...]], ...] = (
    ("file.open_folder", "File", "Open Folder", "open_folder", ("open directory", "browse folder")),
    ("file.refresh_folder", "File", "Refresh Folder", "refresh_folder", ("reload folder", "rescan")),
    ("file.new_folder", "File", "New Folder", "new_folder", ("create folder", "new directory")),
    ("file.workflow_settings", "File", "Settings", "workflow_settings", ("preferences", "settings")),
    ("edit.undo", "Edit", "Undo", "undo", ("revert", "undo last action")),
    ("edit.rename_selection", "Edit", "Rename Image", "rename_selection", ("rename image", "rename file")),
    ("tools.batch_rename", "Tools", "Batch Rename", "batch_rename_selection", ("batch rename tool", "rename many")),
    ("tools.batch_resize", "Tools", "Batch Resize", "batch_resize_selection", ("batch resize tool", "resize many", "convert size")),
    ("tools.batch_convert", "Tools", "Batch Convert", "batch_convert_selection", ("batch convert tool", "convert format", "png jpg webp")),
    ("tools.extract_archive", "Tools", "Extract Archive", "", ("extract archive", "unzip", "decompress", "7z")),
    ("tools.performance_logging", "Tools", "Performance Logging", "", ("diagnostics", "profiler", "performance log", "speed")),
    ("workflow.handoff_builder", "Export", "Deliver / Handoff Builder", "handoff_builder", ("delivery", "handoff", "export workflow")),
    ("workflow.share_to_phone", "Export", "Send to PocketDrop", "share_to_phone", ("phone", "qr", "pocketdrop", "share", "transfer", "send")),
    ("workflow.send_to_editor", "Export", "Send To Editor", "send_to_editor_pipeline", ("retouch", "editor queue", "send to editor")),
    ("workflow.best_of", "Export", "Best-of-Set Auto Assembly", "best_of_set_auto_assembly", ("best of", "shortlist", "auto assembly")),
    ("workflow.keyboard_shortcuts", "Export", "Keyboard Shortcuts", "", ("shortcuts", "keyboard mapping")),
    ("library.create_collection", "Library", "Create Virtual Collection", "", ("virtual collection", "portfolio picks", "proofing set")),
    ("library.add_selected_to_collection", "Library", "Add Selection To Collection", "collect_at_cursor", ("collection", "add selection", "save picks")),
    ("library.browse_catalog", "Library", "Browse Catalog", "", ("catalog", "cross folder search", "global index")),
    ("library.refresh_catalog", "Library", "Refresh Catalog", "", ("refresh catalog", "reindex library")),
    ("review.open_preview", "Review", "Open Preview", "open_preview", ("viewer", "popout", "fullscreen")),
    ("review.accept_selection", "Review", "Mark Winner", "accept_selection", ("winner", "approve", "accept")),
    ("review.reject_selection", "Review", "Reject Selection", "reject_selection", ("reject", "decline")),
    ("review.keep_selection", "Review", "Move Selection To _keep", "keep_at_cursor", ("keep", "_keep")),
    ("review.move_selection", "Review", "Move Selection", "move_selection", ("relocate", "move")),
    ("review.delete_selection", "Review", "Delete Selection", "delete_selection", ("trash", "remove", "delete")),
    ("review.reveal_in_explorer", "Review", "Reveal In Explorer", "", ("show in explorer", "reveal file")),
    ("review.compare_mode", "Review", "Compare", "compare_mode", ("toggle compare",)),
    ("review.auto_advance", "Review", "Auto Advance", "", ("toggle auto advance",)),
    ("view.grid_view", "View", "Grid View", "grid_view", ("grid", "thumbnail grid", "tiles")),
    ("view.details_view", "View", "Details View", "details_view", ("details", "list view", "file explorer")),
    ("view.zen_mode", "View", "Zen Mode", "zen_mode", ("fullscreen", "focus mode", "hide panels")),
    ("view.burst_groups", "View", "Burst Groups", "", ("burst grouping", "burst shots", "toggle bursts", "capture sequence")),
    ("sort.name", "View", "View: Sort By Name", "", ("view", "sort", "name")),
    ("sort.ai", "View", "View: Sort By AI Score", "", ("view", "sort", "ai score")),
    ("columns.5", "View", "View: Columns 5 Across", "", ("view", "columns", "5 across")),
    ("search.advanced_filters", "Search", "Advanced Filters", "", ("metadata filters", "search filters")),
    ("search.save_current", "Search", "Save Current Search", "", ("save search", "save preset")),
    ("search.clear_filters", "Search", "Clear Filters", "clear_filters", ("reset filters", "clear search")),
    ("smart_filter.sharp_keepers", "Search", "Apply Smart Filter: Sharp keepers", "", ("smart filter", "saved search", "sharp keepers")),
    ("ai.workflow_center", "AI", "AI Workflow Center", "open_ai_workflow_center", ("workflow center", "ai workflow", "guide", "steps", "wizard")),
    ("ai.run_pipeline", "AI", "Run AI Culling", "", ("start ai", "run ai culler", "rank images")),
    ("ai.quick_rerank", "AI", "Quick Rerank", "quick_rerank_ai_culling", ("quick rerank", "rerank", "re-rank", "rerun rank", "fast rerank", "rerank only")),
    ("ai.apply_culling", "AI", "Apply AI Culling", "", ("apply ai culling", "auto cull", "move ai picks", "recycle ai rejects")),
    ("ai.next_top_pick", "AI", "Next AI Top Pick", "next_ai_pick", ("next ai pick", "jump ai")),
    ("ai.people", "AI", "People", "", ("people", "faces", "person names", "face search")),
    ("window.reset_layout", "Workspace", "Reset Layout", "", ("restore layout", "default workspace")),
    ("help.keyboard_help", "Help", "Quick Help", "", ("quick help", "shortcuts", "help")),
    ("help.check_updates", "Help", "Check For Updates", "", ("update", "installer", "new version", "upgrade")),
    ("help.about", "Help", "About Image Triage", "", ("about", "version")),
    ("appearance.slate", "Appearance", "Set Theme: Slate", "", ("theme", "appearance", "slate")),
    ("appearance.light", "Appearance", "Set Theme: Light", "", ("theme", "appearance", "light")),
    ("appearance.midnight", "Appearance", "Set Theme: Midnight", "", ("theme", "appearance", "midnight")),
)

# commands shown in the popout's own palette ("Preview Commands"): the ones that act on a photo
_PREVIEW_SECTIONS = ("Review", "AI", "Export", "Edit", "Appearance", "Help")

_SECTION_ICON = {
    "File": "file", "Edit": "edit", "Tools": "tools", "Export": "export", "Library": "library", "Review": "review", "View": "view",
    "Search": "search", "AI": "ai", "Workspace": "workspace", "Help": "help", "Appearance": "theme",
}

_DEMO_RECENT = ("file.open_folder", "review.accept_selection", "tools.batch_rename")
_DEMO_SHORTCUTS = {
    "open_folder": "Ctrl+O", "refresh_folder": "F5", "new_folder": "Ctrl+Shift+N", "workflow_settings": "Ctrl+,", "undo": "Ctrl+Z",
    "rename_selection": "F2", "batch_rename_selection": "Ctrl+Shift+R", "batch_resize_selection": "Ctrl+Shift+E",
    "batch_convert_selection": "Ctrl+Shift+C", "handoff_builder": "Ctrl+Alt+H", "share_to_phone": "Ctrl+Alt+P",
    "send_to_editor_pipeline": "Ctrl+Alt+E", "best_of_set_auto_assembly": "Ctrl+Alt+B", "collect_at_cursor": "B", "open_preview": "Space",
    "accept_selection": "W", "reject_selection": "X", "keep_at_cursor": "K", "compare_mode": "C", "grid_view": "Ctrl+1",
    "details_view": "Ctrl+2", "zen_mode": "F11", "clear_filters": "Ctrl+Shift+X", "open_ai_workflow_center": "Ctrl+Shift+W",
    "quick_rerank_ai_culling": "Ctrl+Shift+Y", "next_ai_pick": "Ctrl+Alt+N",
}
_DEMO_STATES = {"tools.performance_logging": "Off", "review.auto_advance": "On", "view.zen_mode": "Off", "view.burst_groups": "On"}
_SUBS = {"smart_filter.sharp_keepers": "Focus 70+, no reject"}


def _keys(text: str) -> list[str]:
    return [part for part in text.replace(" ", "").split("+") if part] if text else []


class PaletteModel(QObject):
    """Commands, ranking and recents for the palette overlay. `memory=True` (the mockup comparison) uses fixed shortcuts and recents and
    never touches the real settings."""

    revisionChanged = Signal()
    executed = Signal(str, arguments=["id"])        # a command was chosen: its id (the shell acts on the ones it has wired)

    def __init__(self, memory: bool = False, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._memory = memory
        self._revision = 0
        self._states: dict[str, str] = dict(_DEMO_STATES) if memory else {}
        self._subs: dict[str, str] = dict(_SUBS)
        if memory:
            self._subs["view.grid_view"] = "Current view"
        self._recent: list[str] = list(_DEMO_RECENT) if memory else self._load_recent()
        self._shortcuts = dict(_DEMO_SHORTCUTS) if memory else self._load_shortcuts()
        self._commands = self._build()

    # -- data ------------------------------------------------------------------------------------------------------------------
    @staticmethod
    def _load_recent() -> list[str]:
        try:
            from ..app_identity import user_settings

            raw = user_settings().value(RECENT_KEY, [], list)
        except Exception:
            return []
        if isinstance(raw, str):
            raw = [raw]
        out: list[str] = []
        for value in raw or []:
            if isinstance(value, str) and value and value not in out:
                out.append(value)
        return out[:RECENT_MAX]

    @staticmethod
    def _load_shortcuts() -> dict[str, str]:
        try:
            from ..ui.shortcuts import SHORTCUT_REGISTRY, load_shortcut_overrides

            overrides = load_shortcut_overrides()
            return {attr: overrides.get(attr, default) for attr, _c, default, _d in SHORTCUT_REGISTRY}
        except Exception:
            return {}

    def _build(self) -> list:
        from ..ui.command_palette import PaletteCommand

        commands = []
        for command_id, section, title, attr, keywords in _COMMANDS:
            commands.append(
                PaletteCommand(
                    id=command_id, title=title, callback=lambda: None, section=section, keywords=keywords,
                    shortcut=self._shortcuts.get(attr, "") if attr else "",
                )
            )
        return commands

    def _visible(self, context: str) -> list:
        if context == "preview":
            return [c for c in self._commands if c.section in _PREVIEW_SECTIONS]
        return self._commands

    # -- state shown next to a command (On / Off, "Current view") ----------------------------------------------------------------
    @Slot(str, str)
    def setState(self, command_id: str, text: str) -> None:
        if self._states.get(command_id, "") != text:
            if text:
                self._states[command_id] = text
            else:
                self._states.pop(command_id, None)
            self._bump()

    @Slot(str, str)
    def setSub(self, command_id: str, text: str) -> None:
        if self._subs.get(command_id, "") != text:
            if text:
                self._subs[command_id] = text
            else:
                self._subs.pop(command_id, None)
            self._bump()

    def _bump(self) -> None:
        self._revision += 1
        self.revisionChanged.emit()

    @Property(int, notify=revisionChanged)
    def revision(self) -> int:
        return self._revision

    # -- queries ---------------------------------------------------------------------------------------------------------------
    @Slot(str, str, result="QVariantList")
    def results(self, query: str, context: str = "main") -> list:
        """Rows for the list. Empty query: the recent commands first, then every command grouped by section (the order the sections
        were declared in). A query: the Widgets palette's ranking, as one flat list, with the matching text marked."""
        from ..ui.command_palette import _normalize_query, _rank_commands

        commands = self._visible(context)
        for command in commands:       # the Widgets palette ranks on the subtitle too (a toggle's On/Off, a preset's description)
            command.subtitle = self._states.get(command.id) or self._subs.get(command.id, "")
        q = _normalize_query(query)
        recent = tuple(self._recent)
        ranked = [command for _, command in _rank_commands(commands, q, recent)][:80]
        rows: list[dict] = []
        if not q:
            sections = list(dict.fromkeys(c.section for c in commands))
            recent_rank = {cid: i for i, cid in enumerate(recent)}
            head = sorted((c for c in ranked if c.id in recent_rank), key=lambda c: recent_rank[c.id])
            tail = sorted((c for c in ranked if c.id not in recent_rank), key=lambda c: (sections.index(c.section), c.title.casefold()))
            last = None
            for command in head + tail:
                label = "Recent" if command.id in recent_rank else command.section
                if label != last:
                    rows.append({"kind": "hdr", "label": label})
                    last = label
                rows.append(self._row(command, "", show_section=label == "Recent"))
            return rows
        for command in ranked:
            rows.append(self._row(command, q, show_section=True))
        return rows

    def _row(self, command, q: str, *, show_section: bool) -> dict:
        title = command.title
        start = title.casefold().find(q) if q else -1
        if start >= 0:
            pre, hit, post = title[:start], title[start:start + len(q)], title[start + len(q):]
        else:
            pre, hit, post = title, "", ""
        state = self._states.get(command.id, "")
        parts = [part for part in (command.section if show_section else "", self._subs.get(command.id, "")) if part]
        return {
            "kind": "row", "id": command.id, "pre": pre, "hit": hit, "post": post, "sub": " · ".join(parts),
            "icon": _SECTION_ICON.get(command.section, "file"), "keys": _keys(command.shortcut),
            "state": state,
        }

    @Slot(str, str, result=str)
    def countText(self, query: str, context: str = "main") -> str:
        from ..ui.command_palette import _normalize_query, _rank_commands

        commands = self._visible(context)
        shown = len(_rank_commands(commands, _normalize_query(query), tuple(self._recent))[:80])
        return f"{len(commands)} commands" if shown == len(commands) else f"{shown} of {len(commands)}"

    @Slot(str, str, result=str)
    def title(self, context: str, zen: str = "") -> str:
        return "Preview Commands" if context == "preview" else "Zen Commands" if context == "zen" else "Command Palette"

    # -- running ---------------------------------------------------------------------------------------------------------------
    @Slot(str)
    def run(self, command_id: str) -> None:
        self._recent = [command_id, *[c for c in self._recent if c != command_id]][:RECENT_MAX]
        if not self._memory:
            try:
                from ..app_identity import user_settings

                user_settings().setValue(RECENT_KEY, self._recent)
            except Exception:
                pass
        self._bump()
        self.executed.emit(command_id)
