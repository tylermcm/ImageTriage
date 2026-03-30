from __future__ import annotations

import json

from PySide6.QtCore import QByteArray, QSettings


LAYOUT_STATE_VERSION = 2


def restore_window_layout(window, settings: QSettings, geometry_key: str, state_key: str, workspace_docks=None) -> bool:
    geometry = settings.value(geometry_key, QByteArray(), QByteArray)
    if isinstance(geometry, QByteArray) and not geometry.isEmpty():
        window.restoreGeometry(geometry)

    if workspace_docks is None:
        return False

    raw_state = settings.value(state_key, "", str)
    if not isinstance(raw_state, str) or not raw_state:
        return False

    try:
        payload = json.loads(raw_state)
    except (TypeError, ValueError):
        return False

    if not isinstance(payload, dict) or payload.get("version") != LAYOUT_STATE_VERSION:
        return False
    workspace_payload = payload.get("workspace")
    if not isinstance(workspace_payload, dict):
        return False
    return workspace_docks.restore_state(workspace_payload)


def save_window_layout(window, settings: QSettings, geometry_key: str, state_key: str, workspace_docks=None) -> None:
    settings.setValue(geometry_key, window.saveGeometry())
    payload = {
        "version": LAYOUT_STATE_VERSION,
        "workspace": workspace_docks.save_state() if workspace_docks is not None else {},
    }
    settings.setValue(state_key, json.dumps(payload))


def clear_window_layout(settings: QSettings, geometry_key: str, state_key: str) -> None:
    settings.remove(geometry_key)
    settings.remove(state_key)
