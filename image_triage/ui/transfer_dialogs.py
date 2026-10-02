"""Confirmation and completion dialogs for Move/Copy To Folder and To Recent
Destination (WI-4.7). Both are lightweight QMessageBox-based dialogs, by
design: these are routine, frequent actions, not a rare one-shot operation
like Apply AI Decisions, so a heavier thumbnail-review dialog would get old
fast.
"""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QMessageBox, QWidget

from ..shell_actions import open_in_file_explorer


def confirm_transfer(
    parent: QWidget,
    *,
    verb: str,
    count: int,
    destination: str,
    include_companions_default: bool,
) -> tuple[bool, bool]:
    """Ask the user to confirm a Move/Copy To Folder or To Recent Destination
    action before it runs. Returns (confirmed, include_companions)."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(f"{verb} {count} Item{'s' if count != 1 else ''}?")
    box.setText(f"{verb} {count} image{'s' if count != 1 else ''} to:\n\n{destination}\n\nContinue?")
    checkbox = QCheckBox("Include paired RAW/JPEG and sidecar files", box)
    checkbox.setChecked(include_companions_default)
    box.setCheckBox(checkbox)
    box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    confirmed = box.exec() == QMessageBox.StandardButton.Yes
    return confirmed, checkbox.isChecked()


def show_transfer_complete(
    parent: QWidget,
    *,
    verb_past: str,
    count: int,
    destination: str,
) -> None:
    """Completion notice for a batch Move/Copy, with a button to jump
    straight to the destination folder. Callers only show this for batches
    of 2+ -- a single file just gets the existing status-bar message, so this
    never interrupts a routine one-off transfer."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle(f"{verb_past} {count} Item{'s' if count != 1 else ''}")
    box.setText(f"{verb_past} {count} image{'s' if count != 1 else ''} to:\n\n{destination}")
    open_button = box.addButton("Open Destination", QMessageBox.ButtonRole.ActionRole)
    box.addButton(QMessageBox.StandardButton.Ok)
    box.setDefaultButton(QMessageBox.StandardButton.Ok)
    box.exec()
    if box.clickedButton() is open_button:
        open_in_file_explorer(destination)
