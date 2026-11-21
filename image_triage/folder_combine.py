"""Combine folders: the files of several folders (a trip's camera folders, 107NZ7_2, 108NZ7_2, …) moved into one, their numbering
continued so nothing is named twice.

A camera starts its numbering again in each new folder (or rolls it over), so the folders hold the same names: moved together
they would come out as ``_DSC0001 (1).NEF``. Here each moved photo keeps its name while its number is above every number of its
kind (the same prefix, ``_DSC`` or ``DSC_``) already in the destination; otherwise it takes the next number after them, with the
same width (``_DSC0001`` after ``_DSC1928`` becomes ``_DSC1929``). The folders are taken in the order given, each in its own
numbering order, so the combined folder keeps the order the photos were taken in.

Everything with the same base name moves and is renamed together: the RAW, its JPEG, an XMP sidecar (``_DSC0001.NEF.xmp``), a
clip with the same number. The photos' edit data (in the folder's hidden edit root) and saved decisions follow them; the caller
does that from the moves (``edit_carry.carry_edits``, ``DecisionStore.move_annotation``).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .edit_storage import EDIT_STORAGE_ROOT_NAME

NUMBERED = re.compile(r"^(?P<prefix>.*?)(?P<number>\d+)$")
# hidden app folders: the edit root follows its photos (edit_carry); the AI cache is made again where it is needed
APP_FOLDERS = (EDIT_STORAGE_ROOT_NAME, ".image_triage_ai")


def base_of(name: str) -> str:
    """The name a file shares with the rest of its photo: ``_DSC0001`` for ``_DSC0001.NEF``, ``_DSC0001.JPG``, ``_DSC0001.NEF.xmp``."""
    return name.split(".", 1)[0] if not name.startswith(".") else name


def _natural(text: str):
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", text)]


@dataclass(frozen=True)
class FileStep:
    source: str
    target: str


@dataclass
class CombinePlan:
    destination: str
    steps: list[FileStep] = field(default_factory=list)       # every file that moves, in order
    renamed: int = 0                                            # photos (bases) that take a new number
    kept: int = 0                                               # photos that keep their name
    folders: list[str] = field(default_factory=list)            # the folders whose files move
    left: dict[str, list[str]] = field(default_factory=dict)    # per folder, what stays in it (subfolders): it is not removed
    error: str = ""

    @property
    def can_apply(self) -> bool:
        return not self.error and bool(self.steps)


def _files(folder: str) -> tuple[list[str], list[str]]:
    """(the files directly in ``folder``, its subfolders other than the app's own) — hidden files left out."""
    files, folders = [], []
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.name.startswith("."):
                    if entry.is_dir() and entry.name not in APP_FOLDERS:
                        folders.append(entry.name)
                    continue
                if entry.is_dir(follow_symlinks=False):
                    folders.append(entry.name)
                elif entry.is_file():
                    files.append(entry.name)
    except OSError:
        pass
    return sorted(files, key=_natural), sorted(folders, key=_natural)


def plan_combine(sources: list[str], destination: str, *, renumber: bool = True) -> CombinePlan:
    """What combining ``sources`` into ``destination`` does. ``destination`` may be one of ``sources`` (its files stay as they are)
    or another folder (their parent, say, or a new one). With ``renumber`` off, a name already taken gets `` (n)`` instead."""
    dest = os.path.normpath(destination)
    key = lambda path: os.path.normcase(os.path.abspath(path))
    plan = CombinePlan(destination=dest)
    moving = [os.path.normpath(s) for s in sources if key(s) != key(dest)]
    if not moving:
        plan.error = "Pick at least one folder to move into the destination."
        return plan
    for folder in moving:
        if not os.path.isdir(folder):
            plan.error = f"{folder} is not a folder."
            return plan
        if key(dest).startswith(key(folder) + os.sep):
            plan.error = f"{Path(dest).name} is inside {Path(folder).name}: a folder cannot be moved into itself."
            return plan
    plan.folders = moving

    taken: set[str] = set()                     # names in the destination, now and as the plan fills it (casefolded)
    highest: dict[str, tuple[int, int]] = {}    # per prefix: the highest number there, and the widest it was written
    def note(name: str) -> None:
        taken.add(name.casefold())
        match = NUMBERED.match(base_of(name))
        if match:
            prefix, digits = match["prefix"], match["number"]
            number, width = highest.get(prefix.casefold(), (-1, 0))
            highest[prefix.casefold()] = (max(number, int(digits)), max(width, len(digits)))

    existing, _ = _files(dest) if os.path.isdir(dest) else ([], [])
    for name in existing:
        note(name)

    for folder in moving:
        names, subfolders = _files(folder)
        if subfolders:
            plan.left[folder] = subfolders
        groups: dict[str, list[str]] = {}
        for name in names:
            groups.setdefault(base_of(name), []).append(name)
        for base in sorted(groups, key=_natural):
            members = groups[base]
            new_base = base
            match = NUMBERED.match(base)
            clash = any(m.casefold() in taken for m in members)
            if renumber and match:
                prefix, digits = match["prefix"], match["number"]
                number, width = highest.get(prefix.casefold(), (-1, 0))
                if int(digits) <= number or clash:
                    following = number + 1
                    new_base = f"{prefix}{following:0{max(len(digits), width)}d}"
                    while any((new_base + m[len(base):]).casefold() in taken for m in members):
                        following += 1
                        new_base = f"{prefix}{following:0{max(len(digits), width)}d}"
            elif clash:
                count = 1
                while any((f"{base} ({count})" + m[len(base):]).casefold() in taken for m in members):
                    count += 1
                new_base = f"{base} ({count})"
            if new_base != base:
                plan.renamed += 1
            else:
                plan.kept += 1
            for member in members:
                target_name = new_base + member[len(base):]
                plan.steps.append(FileStep(os.path.join(folder, member), os.path.join(dest, target_name)))
                note(target_name)
    if not plan.steps:
        plan.error = "The folders have no files to move."
    return plan


def apply_combine(plan: CombinePlan, *, step=None, cancelled=lambda: False) -> tuple[list[FileStep], list[tuple[str, str]]]:
    """Moves the plan's files (a rename when the folders are on the same drive, as they usually are). Returns (the moves made, the
    failures as (file, reason)). Never writes over a file: one that has appeared at its target since the plan was made fails."""
    import shutil

    done, failed = [], []
    os.makedirs(plan.destination, exist_ok=True)
    for index, item in enumerate(plan.steps):
        if cancelled():
            break
        if step is not None:
            step(index, Path(item.source).name)
        try:
            if os.path.lexists(item.target):
                raise FileExistsError(f"{Path(item.target).name} is already there")
            try:
                os.rename(item.source, item.target)
            except OSError:
                shutil.move(item.source, item.target)        # another drive
            done.append(item)
        except OSError as exc:
            failed.append((Path(item.source).name, str(exc) or type(exc).__name__))
    return done, failed


def remove_emptied(folders: list[str]) -> list[str]:
    """Removes each folder that has nothing left but the app's own hidden folders (an emptied edit root, the AI cache). Returns the
    ones removed; a folder with anything else in it stays."""
    import shutil

    removed = []
    for folder in folders:
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        rest = [n for n in names if n not in APP_FOLDERS]
        edits = os.path.join(folder, EDIT_STORAGE_ROOT_NAME)
        if rest or (os.path.isdir(edits) and any(True for _ in os.scandir(edits))):
            continue
        try:
            shutil.rmtree(os.path.join(folder, ".image_triage_ai"), ignore_errors=True)
            if os.path.isdir(edits):
                os.rmdir(edits)
            os.rmdir(folder)
            removed.append(folder)
        except OSError:
            pass
    return removed
