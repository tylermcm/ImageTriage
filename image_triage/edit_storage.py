"""Location of photo-editor sidecar bundles.

Historically the editor wrote ``<stem>.edit.json`` and a ``<stem>.edit-assets``
folder next to every original, which littered shoot folders with one visible
folder per edited photo. This module keeps the whole bundle in a single hidden
per-folder root (``.image_triage_edits``) instead — the same pattern the AI
subsystem already uses with ``.image_triage_ai``.

The v1 session schema is unchanged: assets are still referenced relative to the
session file, so moving the session file moves the whole bundle with it. The
standalone CLI (``photo_terminal.session``) intentionally keeps the beside-photo
layout; only the GUI app relocates.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
from pathlib import Path

# The session helpers live in the standalone CLI editor package. Ensure it is
# importable regardless of import order (mirrors photo_editor_panel.py).
_CLI_EDITOR_ROOT = Path(__file__).resolve().parents[1] / "cli_editor"
if _CLI_EDITOR_ROOT.exists() and str(_CLI_EDITOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_CLI_EDITOR_ROOT))

from photo_terminal.session import asset_dir_for_session, default_session_path  # noqa: E402

from .scanner import EDIT_STORAGE_ROOT_NAME

FILE_ATTRIBUTE_HIDDEN = 0x2


def _mark_hidden(path: Path) -> None:
    """Set the Windows hidden attribute on ``path`` (no-op elsewhere).

    Mirrors ``ai_workflow._mark_hidden``; kept local so this path utility does
    not import the heavy AI subsystem. A future shared fs util should absorb
    both copies.
    """

    if os.name != "nt":
        return
    try:
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if attrs == -1:
            return
        if attrs & FILE_ATTRIBUTE_HIDDEN:
            return
        ctypes.windll.kernel32.SetFileAttributesW(str(path), attrs | FILE_ATTRIBUTE_HIDDEN)
    except Exception:
        return


def edit_root_for(folder: str | Path) -> Path:
    """Return the hidden edit-storage root for ``folder`` (not created)."""

    return Path(folder) / EDIT_STORAGE_ROOT_NAME


def ensure_edit_root(folder: str | Path) -> Path:
    """Create the hidden edit-storage root and mark it hidden. Call at write time."""

    root = edit_root_for(folder)
    root.mkdir(parents=True, exist_ok=True)
    _mark_hidden(root)
    return root


def legacy_session_path(image_path: str | Path) -> Path:
    """The old beside-the-original session path."""

    return default_session_path(Path(image_path))


def editor_session_path(image_path: str | Path) -> Path:
    """The current session path inside the hidden root (pure; nothing created)."""

    image_path = Path(image_path)
    name = default_session_path(image_path).name
    return edit_root_for(image_path.parent) / name


def resolve_session_for_read(image_path: str | Path) -> Path:
    """Session path to read: the new location if present, else a legacy bundle,
    else the new location as the default write target."""

    new_path = editor_session_path(image_path)
    if new_path.exists():
        return new_path
    legacy = legacy_session_path(image_path)
    if legacy.exists():
        return legacy
    return new_path


def _relocate_session(legacy_json: Path) -> Path | None:
    """Move a beside-photo ``.edit.json`` and its ``.edit-assets`` dir into the
    hidden root. Returns the new session path, or ``None`` if there was nothing
    to move. Only touches our own reconstructable sidecar data — never originals.
    """

    legacy_json = Path(legacy_json)
    if not legacy_json.exists():
        return None
    folder = legacy_json.parent
    new_json = edit_root_for(folder) / legacy_json.name
    if new_json.exists():
        # Already migrated (or an edited copy exists in both places). Leave the
        # legacy file untouched rather than risk clobbering newer edits.
        return new_json
    legacy_assets = asset_dir_for_session(legacy_json)
    new_assets = new_json.parent / legacy_assets.name
    ensure_edit_root(folder)
    moved_assets = False
    try:
        if legacy_assets.is_dir() and not new_assets.exists():
            shutil.move(str(legacy_assets), str(new_assets))
            moved_assets = True
        # Rename last so the atomic step commits the migration; roll back the
        # asset move if it fails, keeping the bundle fully in the legacy layout.
        os.replace(str(legacy_json), str(new_json))
    except Exception:
        if moved_assets and new_assets.exists() and not legacy_assets.exists():
            try:
                shutil.move(str(new_assets), str(legacy_assets))
            except Exception:
                pass
        return None
    return new_json


def migrate_bundle(image_path: str | Path) -> Path | None:
    """Relocate an existing beside-photo bundle for ``image_path`` if present."""

    return _relocate_session(legacy_session_path(image_path))


def consolidate_folder(folder: str | Path) -> int:
    """Sweep every beside-photo bundle in ``folder`` into the hidden root.

    Returns the number of bundles relocated. Top level only; the hidden root is
    never descended into.
    """

    folder = Path(folder)
    try:
        legacy_sessions = sorted(folder.glob("*.edit.json"))
    except OSError:
        return 0
    relocated = 0
    for legacy_json in legacy_sessions:
        if _relocate_session(legacy_json) is not None and not legacy_json.exists():
            relocated += 1
    return relocated
