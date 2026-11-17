"""Soft Proof in the RAW editor: the photo is shown as it will look in an output profile (a printer and paper, or a colour space)
while it is edited. A view setting, not an edit: nothing in the recipe or the render changes, and the frames AI tools read stay as
they are; only the picture on screen is proofed (``raw_preview.proof_query``). The settings are remembered between sessions and
are the same ones Batch Export's soft proof uses.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Property, QSettings, Signal, Slot

from .. import soft_proof

PROFILES = (("srgb", "sRGB"), ("p3", "Display P3"), ("adobergb", "Adobe RGB (1998)"), ("prophoto", "ProPhoto RGB"),
            ("file", "ICC profile file (printer and paper)"))
INTENTS = (("perceptual", "Perceptual"), ("relative", "Relative colorimetric"), ("saturation", "Saturation"))
DEFAULTS = {"enabled": False, "profile": "srgb", "profileFile": "", "intent": "perceptual", "bpc": True, "paper": False, "gamut": False}
PREFIX = "rawEditor/softProof/"


class RawSoftProof(QObject):
    changed = Signal()

    def __init__(self, raw, memory: bool = False):
        super().__init__(raw)
        self.raw = raw
        self._memory = memory
        self._v = dict(DEFAULTS)
        self._revision = 0
        if not memory:
            store = QSettings()
            for key, default in DEFAULTS.items():
                value = store.value(PREFIX + key, default)
                if isinstance(default, bool):
                    value = value in (True, "true", "1", 1)
                self._v[key] = value if isinstance(value, type(default)) else default
            self._v["enabled"] = False          # proofing is turned on per session, never left on by surprise
        self.changed.connect(raw.changed)

    def _save(self):
        if not self._memory:
            store = QSettings()
            for key, value in self._v.items():
                store.setValue(PREFIX + key, value)

    @Slot(str, "QVariant")
    def set(self, key: str, value) -> None:
        if key not in DEFAULTS:
            return
        value = bool(value) if isinstance(DEFAULTS[key], bool) else str(value)
        if key == "profile" and value not in {v for v, _ in PROFILES}:
            return
        if key == "intent" and value not in {v for v, _ in INTENTS}:
            return
        if self._v[key] != value:
            self._v[key] = value
            self._revision += 1
            self._save()
            self.changed.emit()

    @Slot(str, result="QVariant")
    def get(self, key: str):
        return self._v.get(key, "")

    @Slot(str, result="QVariantList")
    def options(self, key: str) -> list:
        items = PROFILES if key == "profile" else INTENTS if key == "intent" else ()
        return [{"label": label, "value": value} for value, label in items]

    @Property(str, notify=changed)
    def problem(self) -> str:
        if self._v["profile"] == "file":
            return soft_proof.profile_problem(self._v["profileFile"])
        return ""

    @Property(bool, notify=changed)
    def active(self) -> bool:
        """Proofing is on and its profile can be used."""
        return bool(self._v["enabled"]) and not self.problem

    @Property(str, notify=changed)
    def label(self) -> str:
        if self._v["profile"] == "file" and not self.problem:
            return soft_proof.profile_name(self._v["profileFile"])
        return dict(PROFILES)[self._v["profile"]]

    revision = Property(int, lambda self: self._revision, notify=changed)
    enabled = Property(bool, lambda self: bool(self._v["enabled"]), notify=changed)

    def query(self) -> str:
        """What the editor adds to a displayed frame's address while proofing ("" when not)."""
        if not self.active:
            return ""
        from . import raw_preview

        return raw_preview.proof_query({k: self._v[k] for k in ("profile", "profileFile", "intent", "bpc", "paper", "gamut")})
