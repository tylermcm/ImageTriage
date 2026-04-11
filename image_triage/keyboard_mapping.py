from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from PySide6.QtGui import QKeySequence


@dataclass(slots=True, frozen=True)
class ShortcutBinding:
    id: str
    label: str
    section: str
    default_shortcut: str = ""
    shortcut: str = ""

    @property
    def effective_shortcut(self) -> str:
        return normalize_shortcut_text(self.shortcut) or normalize_shortcut_text(self.default_shortcut)


def normalize_shortcut_text(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    sequence = QKeySequence(text)
    normalized = sequence.toString(QKeySequence.SequenceFormat.PortableText)
    return normalized.strip()


def serialize_shortcut_overrides(overrides: dict[str, str]) -> dict[str, str]:
    payload: dict[str, str] = {}
    for binding_id, shortcut in overrides.items():
        normalized = normalize_shortcut_text(shortcut)
        if not binding_id or not normalized:
            continue
        payload[binding_id] = normalized
    return payload


def shortcut_conflicts(bindings: list[ShortcutBinding]) -> dict[str, list[str]]:
    shortcuts_to_bindings: dict[str, list[str]] = defaultdict(list)
    for binding in bindings:
        effective = binding.effective_shortcut
        if not effective:
            continue
        shortcuts_to_bindings[effective].append(binding.id)
    return {
        shortcut: binding_ids
        for shortcut, binding_ids in shortcuts_to_bindings.items()
        if len(binding_ids) > 1
    }
