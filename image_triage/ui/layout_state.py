from __future__ import annotations

import json

from PySide6.QtCore import QByteArray, QRect, QSettings


LAYOUT_STATE_VERSION = 2


def restore_window_layout(window, settings: QSettings, geometry_key: str, state_key: str, workspace_docks=None) -> tuple[bool, str]:
    geometry = settings.value(geometry_key, QByteArray(), QByteArray)
    raw_state = settings.value(state_key, "", str)
    payload = _load_layout_payload(raw_state)
    saved_window_state = _normalize_window_state(payload.get("window_state") if payload else None)
    normal_geometry = _decode_rect(payload.get("normal_geometry") if payload else None)

    if normal_geometry is not None:
        window.setGeometry(normal_geometry)
    elif isinstance(geometry, QByteArray) and not geometry.isEmpty():
        window.restoreGeometry(geometry)

    if saved_window_state is None:
        if window.isFullScreen():
            saved_window_state = "fullscreen"
        elif window.isMaximized():
            saved_window_state = "maximized"
        else:
            saved_window_state = "normal"

    restored_workspace = False
    if workspace_docks is None:
        return restored_workspace, saved_window_state

    if isinstance(payload, dict):
        workspace_payload = payload.get("workspace")
        if isinstance(workspace_payload, dict):
            restored_workspace = workspace_docks.restore_state(workspace_payload)
    return restored_workspace, saved_window_state


def save_window_layout(window, settings: QSettings, geometry_key: str, state_key: str, workspace_docks=None) -> None:
    settings.setValue(geometry_key, window.saveGeometry())
    if window.isFullScreen():
        window_state = "fullscreen"
    elif window.isMaximized():
        window_state = "maximized"
    else:
        window_state = "normal"
    payload = {
        "version": LAYOUT_STATE_VERSION,
        "window_state": window_state,
        "normal_geometry": _encode_rect(window.normalGeometry()),
        "workspace": workspace_docks.save_state() if workspace_docks is not None else {},
    }
    settings.setValue(state_key, json.dumps(payload))


def clear_window_layout(settings: QSettings, geometry_key: str, state_key: str) -> None:
    settings.remove(geometry_key)
    settings.remove(state_key)


def _load_layout_payload(raw_state: object) -> dict[str, object] | None:
    if not isinstance(raw_state, str) or not raw_state:
        return None
    try:
        payload = json.loads(raw_state)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != LAYOUT_STATE_VERSION:
        return None
    return payload


def _normalize_window_state(value: object) -> str | None:
    text = str(value or "").strip().lower()
    if text in {"normal", "maximized", "fullscreen"}:
        return text
    return None


def _encode_rect(rect: QRect) -> list[int]:
    if not isinstance(rect, QRect) or rect.isNull():
        return []
    return [int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height())]


def _decode_rect(value: object) -> QRect | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        x, y, width, height = (int(component) for component in value)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return QRect(x, y, width, height)
