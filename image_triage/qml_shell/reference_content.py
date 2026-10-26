"""The words and tables behind the help and reference windows (mockup: scripts/qml_parity/help.dc.html).

The help topics are written for the QML shell (they describe its rail, tabs and windows, not the Widgets app). The curated shortcut list
names the actions the shell has; each row that has an entry in `ui/shortcuts.SHORTCUT_REGISTRY` shows the key the user has set for it.
"""
from __future__ import annotations

# SVG paths (24x24 box) for the topic icons, the menu and the About links
ICON = {
    "start": "M5 4l12 8-12 8zM19 5v14",
    "review": "M12 4l2.4 5 5.4.6-4 3.7 1.1 5.4L12 16l-4.9 2.7 1.1-5.4-4-3.7 5.4-.6z",
    "coll": "M12 4l9 5-9 5-9-5zM3 14l9 5 9-5",
    "people": "M16 11a3 3 0 100-6 3 3 0 000 6zM8 12a3 3 0 100-6 3 3 0 000 6zM2.5 19c.4-2.6 2.6-4.2 5.5-4.2s5.1 1.6 5.5 4.2M14 15.2c.7-.3 1.4-.4 2-.4 2.5 0 4.5 1.5 5 4",
    "cull": "M12 3.5l1.8 4.7 4.7 1.8-4.7 1.8L12 16.5l-1.8-4.7L5.5 10l4.7-1.8z",
    "phone": "M8 3h8a1 1 0 011 1v16a1 1 0 01-1 1H8a1 1 0 01-1-1V4a1 1 0 011-1zM11 18h2",
    "deliver": "M4 8l8-4 8 4v8l-8 4-8-4zM4 8l8 4 8-4M12 12v8",
    "find": "M16 16l4 4M11 17a6 6 0 100-12 6 6 0 000 12z",
    "settings": "M4 7h10M18 7h2M4 17h2M10 17h10M16 9V5M8 19v-4",
    "trouble": "M12 8v5M12 16.5v.01M10.3 3.9L2.4 18a2 2 0 001.7 3h15.8a2 2 0 001.7-3L13.7 3.9a2 2 0 00-3.4 0z",
    "about": "M12 8v.01M12 12v5M12 21a9 9 0 100-18 9 9 0 000 18z",
    "help": "M9.5 9a2.5 2.5 0 115 0c0 1.7-2.5 2-2.5 4M12 17v.01M12 21a9 9 0 100-18 9 9 0 000 18z",
    "keys": "M3 7h18v10H3zM7 11h.01M11 11h.01M15 11h.01M7 14h10",
    "lib": "M4 5h4v14H4zM10 5h4v14h-4zM16 6l4 1-3 12-4-1z",
    "fa": "M6 3h8l4 4v14H6zM14 3v4h4M9 13h6M9 17h6",
    "upd": "M20 11a8 8 0 10-2.3 5.7M20 4v7h-7",
    "folder": "M3.5 7.5a2 2 0 012-2h4l2 2.5h7a2 2 0 012 2v7.5a2 2 0 01-2 2h-13a2 2 0 01-2-2z",
    "shield": "M12 3l8 4v5c0 4.5-3.4 8-8 9-4.6-1-8-4.5-8-9V7z",
    "note": "M6 3h8l4 4v14H6zM14 3v4h4M9 13h6M9 17h6",
    "warn": "M12 8v5M12 16.5v.01M12 21a9 9 0 100-18 9 9 0 000 18z",
}


def _p(text): return {"kind": "p", "t": text}
def _h(text): return {"kind": "h", "t": text}
def _tip(text): return {"kind": "tip", "t": text}
def _btn(text, to): return {"kind": "btn", "t": text, "to": to}
def _keys(rows): return {"kind": "keys", "rows": [{"t": label, "keys": keys} for label, keys in rows]}


TOPICS = (
    {"id": "start", "title": "Getting started", "icon": ICON["start"], "lead": "Image Triage helps you go through a lot of photos quickly: look, decide, move on.",
     "blocks": (_h("Open a folder"), _p("Press Ctrl+O or use the folder button on the left rail. Your photos appear in the grid. Click one to see it larger in the panel on the right."),
                _h("Decide as you go"),
                _keys([("Mark a winner", ["W"]), ("Keep it for now", ["K"]), ("Reject it", ["X"]), ("Clear the decision", ["U"]), ("Give it stars", ["1", "–", "5"]), ("Move around", ["←", "→", "↑", "↓"])]),
                _tip("Nothing here moves or deletes your files unless a button says so. Decisions are notes about each photo."), _btn("See every shortcut", "keys"))},
    {"id": "review", "title": "Reviewing photos", "icon": ICON["review"], "lead": "Every photo can carry a decision and a star rating. Both are quick to change and easy to filter by.",
     "blocks": (_h("Decisions"), _p("Winner is for the shots you will use, Keep for good ones you are not sure about, and Reject for the ones you do not want. They show as small badges in the grid."),
                _h("Looking closer"), _p("The panel on the right shows details, a quality summary and notes for the selected photo. Press I to hide or show it, and C to compare photos side by side."),
                _h("Showing less"), _p("Use the bar at the bottom of the grid to show only Winners, Keep or Rejected, or to change the thumbnail size and the sort order."))},
    {"id": "coll", "title": "Collections and winners", "icon": ICON["coll"], "lead": "A collection gathers photos from any folder without copying or moving them. Use one for an album, a print order or a client.",
     "blocks": (_h("Make one"), _p("Open the Collections tab and press +. Give it a name, and a colour or an icon so you can spot it."),
                _h("Fill it"), _keys([("Mark a winner, and it goes in", ["W"]), ("Add the photo you are on", ["B"])]),
                _p("You can also right-click a photo and tick the collection, or narrow the grid with a filter and use Add All to Collection above it. Turn on “New winners go here” to make one collection collect your winners automatically."),
                _tip("A collection remembers where your photos are. If you move a photo outside Image Triage, the collection cannot find it any more."), _btn("Open Collections", "coll"))},
    {"id": "people", "title": "People and faces", "icon": ICON["people"], "lead": "Image Triage finds the same person across a folder and groups them, so you can see everyone’s photos together.",
     "blocks": (_h("Find someone"), _p("Open the Faces tab and click a person to show only their photos. Click again to go back to the whole folder."),
                _h("Give them names"), _p("Choose All people to name someone, merge two groups that are really the same person, or ignore a group you do not care about. You can undo an ignore straight away."),
                _tip("Faces need the folder to have been scanned. If the list is empty, the Faces tab says so."), _btn("Open Faces", "faces"))},
    {"id": "cull", "title": "Finding photos to skip", "icon": ICON["cull"], "lead": "Press Analyze and Image Triage looks for frames you probably do not want: blurry, too bright, eyes closed, or nearly the same as the next shot.",
     "blocks": (_h("You stay in charge"), _p("It never picks winners and never deletes anything. Flagged frames are only dimmed, with the reason shown. Restore any you want to keep, then choose whether to mark the rest as Rejected."),
                _h("Eyes closed, and ranking"), _p("The closed-eyes check needs a one-time setup that takes about a minute. Ranking is optional and works best with a graphics card."),
                _tip("Not sure why something was flagged? Right-click it and choose “Why was it flagged?”"), _btn("Open Cull", "cull"))},
    {"id": "phone", "title": "Share to your phone", "icon": ICON["phone"], "lead": "PocketDrop sends photos to your phone over your Wi-Fi: scan a code and download. No cable, no app.",
     "blocks": (_h("Same Wi-Fi"), _p("Add photos or a collection, then scan the QR code with your phone’s camera. Everything stays on your network."),
                _h("From anywhere"), _p("Anywhere mode makes a private link you can send to someone. Anyone with the link can download, so Image Triage asks first, and the link stops working when you close the app."),
                _p("Drag a collection onto the PocketDrop button on the left rail to add it, or right-click a photo and choose Share to phone."), _btn("Open PocketDrop", "drop"))},
    {"id": "deliver", "title": "Deliver and export", "icon": ICON["deliver"], "lead": "When the keepers are chosen, hand them off: copy, move or export them the way your client or editor needs.",
     "blocks": (_h("Deliver / Handoff"), _p("Ctrl+Alt+H opens the builder. Pick a recipe, such as proofs for a client or full bundles for an editor, check the preview, then run it. You can save your own recipes."),
                _h("Rename, resize, convert"), _p("Ctrl+Shift+R, E and C open the batch tools. They show exactly what will change before anything is applied."))},
    {"id": "find", "title": "Search and filters", "icon": ICON["find"], "lead": "Press Ctrl+K to search your photos or run any command by typing what you want.",
     "blocks": (_h("Filters"), _p("Open Filters from the left rail to narrow the grid by rating, file type, review state and more. Save a combination as a preset to use again."),
                _p("Clear everything at once with Ctrl+Shift+X. To search across folders, use Browse Library from the Help menu."))},
    {"id": "settings", "title": "Settings and themes", "icon": ICON["settings"], "lead": "Ctrl+, opens Settings.",
     "blocks": (_p("Change the theme (there is a light one too), the thumbnail size, which buttons appear on the rail, and what happens when you open a folder."),
                _h("Shortcuts"), _p("Every shortcut can be changed in Settings. The shortcuts guide always shows the keys you have now."), _btn("Open Settings", "settings"))},
    {"id": "trouble", "title": "If something looks wrong", "icon": ICON["trouble"], "lead": "Most problems have a quick fix.",
     "blocks": (_h("The closed-eyes check will not set up"), _p("On Windows this usually means the Microsoft Visual C++ runtime is out of date. Open the AI Center from the Cull window: it shows the version it found and a link to the update."),
                _h("Thumbnails are blank or slow"), _p("The first time a folder opens, thumbnails are made in the background. They are kept, so the next visit is quick."),
                _h("A photo type will not open"), _p("Open File associations from the Help menu to see which types Image Triage can open, and to make it the default."),
                _h("Still stuck?"), _p("Open the log folder from About Image Triage and send the newest file with your question."))},
    {"id": "about", "title": "About Image Triage", "icon": ICON["about"], "about": True, "sepBefore": True, "lead": "", "blocks": ()},
)

def settings_guide() -> tuple:
    """The Settings topic's article, written from the settings schema itself (`settings_model.build_schema`) so it lists every setting that exists,
    under the page it is on, with the same words as the Settings window. Built once."""
    global _SETTINGS_GUIDE
    if _SETTINGS_GUIDE is None:
        from .settings_model import build_schema

        blocks = [_p("Everything Image Triage lets you change is in one window: press Ctrl+, or the gear at the bottom of the left rail. Search at the top left finds a setting by "
                     "what it does. Changes are kept when you press Save, and a dot beside a page means it has changes you have not saved. This guide lists every page and "
                     "what each setting does.")]
        for section in build_schema(()):
            blocks.append(_h(section.name))
            blocks.append(_p(section.intro))
            for group in section.groups:
                for row in group.rows:
                    if row.type != "keys" and row.desc:
                        blocks.append(_p(f"{row.label}: {row.desc}"))
        blocks.append(_btn("Open Settings", "settings"))
        _SETTINGS_GUIDE = tuple(blocks)
    return _SETTINGS_GUIDE


_SETTINGS_GUIDE: tuple | None = None

ABOUT_LINKS = (
    {"id": "notes", "t": "Release notes", "d": ICON["note"]},
    {"id": "report", "t": "Report a problem", "d": ICON["warn"]},
    {"id": "logs", "t": "Open the log folder", "d": ICON["folder"]},
    {"id": "licences", "t": "Licences and credits", "d": ICON["shield"]},
)

# the Help menu: (id, label, icon, key cap shown at the right); None is a divider
MENU = (
    ("help", "Help", ICON["help"], "F1"), ("keys", "Keyboard shortcuts", ICON["keys"], "Ctrl /"), None,
    ("lib", "Browse Library…", ICON["lib"], ""), ("fa", "File associations…", ICON["fa"], ""), None,
    ("update", "Check for updates…", ICON["upd"], ""), ("about", "About Image Triage", ICON["about"], ""),
)

# the shortcuts guide: (category, [(label, registry attribute or "", fixed keys)]). A row with an attribute shows the key the user has set
# for it (Settings > Shortcuts); the other rows are keys the shell itself handles.
KEY_GROUPS = (
    ("Review", (("Move between photos", "", "←+→+↑+↓"), ("Mark a winner", "accept_selection", "W"), ("Keep it for now", "keep_at_cursor", "K"), ("Reject it", "reject_selection", "X"),
                ("Clear the decision", "", "U"), ("Star rating", "", "1+–+5"), ("Open the large view", "open_preview", "Space"), ("Compare", "compare_mode", "C"), ("Winner ladder", "winner_ladder_mode", "Ctrl+Alt+W"))),
    ("Collections", (("Add the photo you are on", "collect_at_cursor", "B"), ("Best-of-set assembly", "best_of_set_auto_assembly", "Ctrl+Alt+B"), ("Deliver / Handoff Builder", "handoff_builder", "Ctrl+Alt+H"))),
    ("File", (("Open folder", "open_folder", "Ctrl+O"), ("Refresh folder", "refresh_folder", "F5"), ("New folder", "new_folder", "Ctrl+Shift+N"), ("Settings", "workflow_settings", "Ctrl+,"), ("Open in editor", "", "E"))),
    ("Edit", (("Undo", "undo", "Ctrl+Z"), ("Rename", "rename_selection", "F2"), ("Batch rename", "batch_rename_selection", "Ctrl+Shift+R"), ("Batch resize", "batch_resize_selection", "Ctrl+Shift+E"), ("Batch convert", "batch_convert_selection", "Ctrl+Shift+C"))),
    ("View", (("Grid view", "grid_view", "Ctrl+1"), ("Details view", "details_view", "Ctrl+2"), ("Zen mode", "zen_mode", "F11"), ("Show or hide the inspector", "", "I"), ("Clear filters", "clear_filters", "Ctrl+Shift+X"))),
    ("Everywhere", (("Search or run a command", "open_command_palette", "Ctrl+K"), ("Help", "", "F1"), ("Keyboard shortcuts", "", "Ctrl+/"), ("Close a window", "", "Esc"))),
)

# a friendly name for the types people know; any other type is described by its suffix
TYPE_NAMES = {
    ".nef": "Nikon RAW File", ".nrw": "Nikon RAW File", ".cr2": "Canon RAW 2 Image", ".cr3": "Canon RAW 3 Image", ".crw": "Canon RAW Image", ".arw": "Sony RAW File",
    ".srf": "Sony RAW File", ".sr2": "Sony RAW File", ".dng": "Adobe Digital Negative", ".raf": "Fujifilm RAW File", ".orf": "Olympus RAW File", ".rw2": "Panasonic RAW File",
    ".pef": "Pentax RAW File", ".srw": "Samsung RAW File", ".3fr": "Hasselblad RAW File", ".iiq": "Phase One RAW File", ".x3f": "Sigma RAW File", ".rwl": "Leica RAW File",
    ".jpg": "JPEG Image", ".jpeg": "JPEG Image", ".png": "PNG Image", ".heic": "High Efficiency Image", ".heif": "High Efficiency Image", ".webp": "WebP Image",
    ".tif": "TIFF Image", ".tiff": "TIFF Image", ".bmp": "Bitmap Image", ".gif": "Graphics Interchange Format", ".avif": "AVIF Image", ".jxl": "JPEG XL Image",
    ".psd": "Photoshop Document", ".psb": "Photoshop Large Document", ".fits": "FITS Image", ".fit": "FITS Image", ".fts": "FITS Image",
}

# the mockup's sample library (only used when there is no real catalog to read: the --mock mode and the tests)
SAMPLE_ROOTS = (
    {"path": "Photos (D:) / 2026 / Georgia trip", "name": "Georgia trip", "count": 212, "when": "today"},
    {"path": "Photos (D:) / 2026 / Iceland", "name": "Iceland", "count": 3316, "when": "yesterday"},
    {"path": "Photos (D:) / 2026 / Wedding 2026", "name": "Wedding 2026", "count": 1284, "when": "3 days ago"},
)
