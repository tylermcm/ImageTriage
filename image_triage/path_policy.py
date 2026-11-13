"""Which paths may be asked "do you exist?" on the GUI thread (audit P9 / WI-8.5), and which names a folder can hold.

A share that is asleep, or a VPN that is off, makes ``os.path.isdir`` block for ~20 s and then answer "no".
Two rules follow, and every GUI-side check should go through this module:

* **Only a plain local fixed drive is checked on the GUI thread.** There an existence check is instant.
  Network shares (UNC paths and mapped drive letters), removable drives, a drive letter that is not present
  right now, and anything unrecognised are *not* touched; opening one is left to the scan worker, which
  reports the failure.
* **An unverifiable path is never judged missing.** Saved lists (favorites, recent folders, Move-To
  destinations) used to drop a path that failed the check and then save the shortened list, so a sleeping
  NAS cost the user those entries for good. A path is only pruned when it is *provably* gone on a drive
  where the check is instant.

Off Windows there are no drive letters; the "drive" a path is on is the mount it sits on, read from the kernel's
mount table (which never touches the mounts themselves, so a dead share costs nothing). A network file system and an
automount point are remote, a removable disk or anything the desktop mounted under ``/media`` or ``/run/media`` is
removable, the rest is fixed. Where there is no mount table to read (macOS), paths under the places volumes are
attached (``/Volumes``, ``/media``, ``/run/media``, ``/mnt``, ``/net``) are left unchecked and everything else counts
as plain local, which is how every path was treated there before.

The same mount table decides which file-name rules a destination has (``check_name``): a POSIX file system forbids
only ``/`` and NUL and limits a name to 255 bytes; a FAT or exFAT volume enforces the Windows rules; and a name that
Windows could not read is warned about when the destination is a removable disk or an NTFS/SMB volume.
"""
from __future__ import annotations

import ctypes
import os
import posixpath
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .file_ops import _INVALID_NAME_CHARS, _WINDOWS_RESERVED_NAMES, is_unc_path, unc_share_root

# The numbers Windows' GetDriveTypeW gives; the POSIX classification below answers in the same terms.
DRIVE_UNKNOWN = 0
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5

_NETWORK_FILESYSTEMS = frozenset({
    "nfs", "nfs4", "cifs", "smb3", "smbfs", "afs", "ceph", "glusterfs", "9p", "davfs", "ncpfs",
    "fuse.sshfs", "fuse.rclone", "fuse.gvfsd-fuse", "fuse.davfs2",
})
_REMOVABLE_MOUNT_PARENTS = ("/media/", "/run/media/")
# File systems that enforce the Windows name rules themselves, and ones that take any POSIX name but are read by Windows.
_FAT_FILESYSTEMS = frozenset({"vfat", "msdos", "fat", "exfat", "fuse.exfat", "exfat-fuse"})
_WINDOWS_READ_FILESYSTEMS = frozenset({"ntfs", "ntfs3", "ntfs-3g", "fuse.ntfs-3g", "fuseblk", "cifs", "smb3", "smbfs"})
# Where volumes are attached, for a system whose mount table cannot be read.
_UNREADABLE_TABLE_VOLUME_PARENTS = ("/Volumes/", "/media/", "/run/media/", "/mnt/", "/net/")


def _unescape_mount_field(text: str) -> str:
    # mountinfo writes a space, tab, newline and backslash in a path as a backslash and three octal digits
    out, index = [], 0
    while index < len(text):
        if text[index] == "\\" and text[index + 1:index + 4].isdigit():
            out.append(chr(int(text[index + 1:index + 4], 8)))
            index += 4
        else:
            out.append(text[index])
            index += 1
    return "".join(out)


def parse_mountinfo(text: str) -> list[tuple[str, str, str]]:
    """``(mount point, filesystem type, source)`` for each line of ``/proc/self/mountinfo``."""
    mounts: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        left, separator, right = line.partition(" - ")
        fields, tail = left.split(), right.split()
        if not separator or len(fields) < 5 or len(tail) < 2:
            continue
        mounts.append((_unescape_mount_field(fields[4]), tail[0], _unescape_mount_field(tail[1])))
    return mounts


def mount_for_path(path: str, mounts: list[tuple[str, str, str]]) -> tuple[str, str, str] | None:
    """The mount ``path`` is on: the longest mount point that is the path or one of its parents (the last one mounted there)."""
    target = posixpath.normpath(path)
    best: tuple[str, str, str] | None = None
    for mount in mounts:
        point = posixpath.normpath(mount[0])
        if target == point or target.startswith(point.rstrip("/") + "/"):
            if best is None or len(point) >= len(posixpath.normpath(best[0])):
                best = mount
    return best


def _block_device_is_removable(source: str) -> bool:
    """Does the kernel call the disk under this partition removable (a card reader, a USB stick)?"""
    name = os.path.basename(os.path.realpath(source))
    if not source.startswith("/dev/") or not name:
        return False
    for candidate in (name, name.rstrip("0123456789").rstrip("p") if name.startswith(("mmcblk", "nvme")) else name.rstrip("0123456789")):
        try:
            return Path(f"/sys/block/{candidate}/removable").read_text(encoding="ascii").strip() == "1"
        except OSError:
            continue
    return False


def classify_mount(mount: tuple[str, str, str] | None, *, removable=None) -> int:
    """A mount as a Windows drive type: remote for a network filesystem, removable for a removable disk or anything the desktop
    mounted under ``/media`` or ``/run/media``, otherwise fixed."""
    if mount is None:
        return DRIVE_UNKNOWN
    point, filesystem, source = mount
    if filesystem.casefold() in _NETWORK_FILESYSTEMS:
        return DRIVE_REMOTE
    if (point.rstrip("/") + "/").startswith(_REMOVABLE_MOUNT_PARENTS) or (removable or _block_device_is_removable)(source):
        return DRIVE_REMOVABLE
    return DRIVE_FIXED


def _read_mounts() -> list[tuple[str, str, str]]:
    try:
        return parse_mountinfo(Path("/proc/self/mountinfo").read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return []


_MOUNT_TABLE_SECONDS = 2.0          # a card that was just plugged in is seen this soon; the table itself is a local read
_mount_lock = threading.Lock()
_mount_table: tuple[float, list[tuple[str, str, str]], dict[tuple[str, str, str], int]] | None = None


def _mounts() -> tuple[list[tuple[str, str, str]], dict[tuple[str, str, str], int]]:
    """The mount table and the drive types already worked out for its mounts, re-read at most every two seconds."""
    global _mount_table
    with _mount_lock:
        now = time.monotonic()
        if _mount_table is None or now - _mount_table[0] > _MOUNT_TABLE_SECONDS:
            _mount_table = (now, _read_mounts(), {})
        return _mount_table[1], _mount_table[2]


def forget_mounts() -> None:
    """Read the mount table again on the next question (a test that changes it, a drive known to have been attached)."""
    global _mount_table
    with _mount_lock:
        _mount_table = None


def posix_mount(path: str | None) -> tuple[str, str, str] | None:
    """``(mount point, file system type, source)`` of the mount ``path`` is on, without touching the path."""
    text = str(path or "")
    if not text:
        return None
    return mount_for_path(posixpath.abspath(text), _mounts()[0])


def filesystem_type(path: str | None) -> str:
    """The file system ``path`` is on (``"ext4"``, ``"vfat"``, ``"nfs4"``), or ``""`` when it cannot be told."""
    if os.name == "nt":
        return ""
    mount = posix_mount(path)
    return mount[1].casefold() if mount is not None else ""


def _posix_drive_type(path: str) -> int:
    mounts, known = _mounts()
    target = posixpath.abspath(path)
    if not mounts:
        # No mount table (macOS): do not ask about a path where volumes are attached, treat the rest as before.
        return DRIVE_UNKNOWN if (target.rstrip("/") + "/").startswith(_UNREADABLE_TABLE_VOLUME_PARENTS) else DRIVE_FIXED
    mount = mount_for_path(target, mounts)
    if mount is None:
        return DRIVE_UNKNOWN
    cached = known.get(mount)
    if cached is None:
        # An automount point is a share that is attached when it is first touched: asking about it is the slow thing.
        cached = known[mount] = DRIVE_REMOTE if mount[1].casefold() == "autofs" else classify_mount(mount)
    return cached


def drive_root(path: str | None) -> str:
    text = str(path or "")
    if is_unc_path(text):
        return unc_share_root(text)
    if os.name != "nt" and text:
        # There are no drive letters: the "drive" a folder is on is where its file system is mounted.
        mount = posix_mount(text)
        if mount is not None:
            return mount[0]
    try:
        return Path(text).anchor
    except (OSError, ValueError):
        return ""


_DRIVE_TYPES: dict[str, int] = {}


def _drive_type(root: str) -> int:
    if not root:
        return 0
    if is_unc_path(root):
        return 4
    key = os.path.normpath(root).casefold()
    cached = _DRIVE_TYPES.get(key)
    if cached is not None:
        return cached
    try:
        drive_type = int(ctypes.windll.kernel32.GetDriveTypeW(root))  # type: ignore[attr-defined]
    except Exception:
        return 0
    # 0 (unknown) and 1 (no such drive right now) are not remembered: the letter may appear later.
    if drive_type >= 2:
        _DRIVE_TYPES[key] = drive_type
    return drive_type


def drive_type(path: str | None) -> int:
    """What kind of drive ``path`` is on, as a ``DRIVE_*`` number, without touching the path."""
    text = str(path or "")
    if not text:
        return DRIVE_UNKNOWN
    if is_unc_path(text):
        return DRIVE_REMOTE
    if os.name != "nt":
        return _posix_drive_type(text)
    return _drive_type(drive_root(text))


def is_removable(path: str | None) -> bool:
    return drive_type(path) == DRIVE_REMOVABLE


def is_plain_local(path: str | None) -> bool:
    """True when asking the filesystem about ``path`` is instant (a fixed local drive)."""
    if os.name != "nt" and not path:
        return True                     # nothing to ask about: what this said off Windows before mounts were read
    return drive_type(path) == DRIVE_FIXED


def confirmed_missing(path: str | None) -> bool:
    """True only when ``path`` is provably not a folder *and* checking it cannot block."""
    if not path or not is_plain_local(path):
        return False
    return not os.path.isdir(path)


def drive_roots() -> list[tuple[str, int]]:
    """The drives this machine has right now as ``("C:/", drive_type)``, from the drive-letter bitmask and
    ``GetDriveTypeW``: neither touches a drive, so an offline mapped drive costs nothing here. (Asking Windows *about* a
    dead network drive, by existence check, label, size or listing, is what blocks, for ~20 s, and a
    ``QFileSystemModel`` rooted at "all drives" does exactly that on its single worker thread.)

    Off Windows there is one root, ``/``, and it counts as a plain local drive.
    """
    if os.name != "nt":
        return [("/", DRIVE_FIXED)]
    try:
        mask = int(ctypes.windll.kernel32.GetLogicalDrives())  # type: ignore[attr-defined]
    except Exception:
        return []
    roots: list[tuple[str, int]] = []
    for offset in range(26):
        if mask & (1 << offset):
            letter = chr(ord("A") + offset)
            roots.append((f"{letter}:/", _drive_type(f"{letter}:\\")))
    return roots


# ----------------------------------------------------------------------------------------------------- file-name rules

RULES_WINDOWS = "windows"        # Windows itself, or a FAT/exFAT volume: the Windows rules are enforced
RULES_PORTABLE = "portable"      # any POSIX name is accepted, but Windows will read this volume: warn about what it cannot
RULES_POSIX = "posix"            # only the file system's own rules

_NAME_BYTES = 255                # NAME_MAX of every common POSIX file system
_NAME_UNITS = 255                # the longest name NTFS, FAT (long names) and exFAT hold, in UTF-16 units


@dataclass(slots=True, frozen=True)
class NameVerdict:
    """What is wrong with a proposed file or folder name. ``error``: the destination cannot hold it. ``warning``: it can,
    but Windows could not read the name, which matters for the kind of volume the destination is."""

    error: str = ""
    warning: str = ""


def name_rules(directory: str | None) -> str:
    """Which rules a new name in ``directory`` has to follow (see ``RULES_*``). Never touches the directory."""
    if os.name == "nt" or is_unc_path(directory):
        return RULES_WINDOWS
    if not directory:
        return RULES_POSIX
    filesystem = filesystem_type(directory)
    if filesystem in _FAT_FILESYSTEMS:
        return RULES_WINDOWS
    if filesystem in _WINDOWS_READ_FILESYSTEMS or drive_type(directory) == DRIVE_REMOVABLE:
        return RULES_PORTABLE
    return RULES_POSIX


def windows_name_problem(name: str) -> str:
    """Why Windows (and a FAT or exFAT volume anywhere) cannot hold ``name``, or ``""``."""
    if any(char in _INVALID_NAME_CHARS for char in name):
        return "Names cannot contain < > : \" / \\ | ? *"
    if any(ord(char) < 32 for char in name):
        return "Names cannot contain control characters."
    if name in {".", ".."}:
        return "That name is not allowed."
    if name[-1:] in {" ", "."}:
        return "Names cannot end with a space or period on Windows."
    stem = Path(name).stem or Path(name).name
    if stem.casefold().upper() in _WINDOWS_RESERVED_NAMES:
        return f"'{stem}' is reserved on Windows."
    if len(name.encode("utf-16-le", "surrogatepass")) // 2 > _NAME_UNITS:
        return f"Names cannot be longer than {_NAME_UNITS} characters."
    return ""


def posix_name_problem(name: str) -> str:
    """Why a POSIX file system cannot hold ``name``, or ``""``: only ``/`` and NUL are forbidden, and the encoded name is
    limited to 255 bytes."""
    if "/" in name:
        return "Names cannot contain /"
    if "\x00" in name:
        return "Names cannot contain a NUL character."
    if name in {".", ".."}:
        return "That name is not allowed."
    try:
        size = len(os.fsencode(name))
    except (UnicodeError, ValueError):
        return "That name cannot be written on this file system."
    if size > _NAME_BYTES:
        return f"Names cannot be longer than {_NAME_BYTES} bytes (this one is {size})."
    return ""


def check_name(name: str, directory: str | None = None, *, rules: str | None = None) -> NameVerdict:
    """Judge a proposed name for a file or folder in ``directory`` by the rules of the file system it is on.

    ``rules`` overrides the choice ``name_rules`` would make (a caller that already knows, or a test)."""
    if not name:
        return NameVerdict(error="Enter a name.")
    chosen = rules or name_rules(directory)
    if chosen == RULES_WINDOWS:
        return NameVerdict(error=windows_name_problem(name))
    problem = posix_name_problem(name)
    if problem:
        return NameVerdict(error=problem)
    if chosen == RULES_PORTABLE:
        portable = windows_name_problem(name)
        if portable:
            return NameVerdict(warning=f"Windows cannot read this name. {portable}")
    return NameVerdict()
