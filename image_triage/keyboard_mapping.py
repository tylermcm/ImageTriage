from __future__ import annotations

from PySide6.QtGui import QKeyEvent, QKeySequence


def matches_shortcut(event: QKeyEvent, shortcut: QKeySequence) -> bool:
    """Whether a key event exactly matches a (possibly rebound) shortcut.

    Shared by grid.py, details_view.py and preview.py so their raw
    keyPressEvent handlers can check a live, rebindable QKeySequence instead
    of a literal Qt.Key comparison (WI-3.2).
    """
    if shortcut.isEmpty():
        return False
    event_sequence = QKeySequence(event.keyCombination())
    return event_sequence.matches(shortcut) == QKeySequence.SequenceMatch.ExactMatch
