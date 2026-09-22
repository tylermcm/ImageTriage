"""PocketDrop: send files between this computer and a phone by QR code.

The engine and UI are PocketDrop's own C++ (native/pocketdrop), loaded from
pocketdrop.dll and hosted in a Qt widget by panel.py.
"""
from .panel import PocketDropPanel, PocketDropView

__all__ = ["PocketDropPanel", "PocketDropView"]
