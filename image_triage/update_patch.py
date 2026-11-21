"""Small updates: only the parts of the installed app that changed are downloaded and replaced, not the whole MSI.

A release publishes an update manifest (``ImageTriage-<version>-update.json``): every installed file with its SHA-256, in groups (the
app's own code, the RAW preview worker, the PhotoCraft editor, Qt, onnxruntime and the rest), and for each group the zip that holds
it. A group that did not change keeps the zip of the release that last changed it, so a release only uploads what it changed and an
install of any older version downloads only the groups it differs in.

The installed app carries the manifest of what it is (``update-manifest.json`` beside ImageTriage.exe, written into the build and
rewritten by each small update). When it is missing, or the files on disk do not match it, or the small update would be most of the
MSI anyway, the full MSI is installed instead.

Everything is verified twice: the zips and the files in them against the manifest after the download, and again by the elevated
install, which first copies them where only an administrator can write (so nothing can swap a file between the check and the
copy). Each replaced file is kept until the install has finished, and put back when it fails.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

MANIFEST_NAME = "update-manifest.json"
FORMAT = 1
# above this share of the MSI's size a small update saves too little to be worth its extra steps: the MSI is installed instead
MAX_SHARE_OF_MSI = 0.7


def group_of(rel: str) -> str:
    """The group an installed file (its path relative to the install folder, with forward slashes) is updated with."""
    parts = PurePosixPath(rel).parts
    head = parts[0].casefold() if parts else ""
    if head == "photocraft":
        return "photocraft"
    if rel.casefold() == "image-triage-raw-preview.exe":
        return "raw-preview"
    if head == "ai_workers":
        return "app"            # the AI workers' scripts: this repository's own code (a copy of _version.py among them)
    if head == "lib" and len(parts) > 1:
        second = parts[1].casefold()
        if second == "image_triage" or second == "library.zip":
            return "app"        # library.zip holds the app's compiled modules: it changes with every release
        if second == "pyside6" or second == "shiboken6":
            return "qt"
        if second == "onnxruntime":
            return "onnxruntime"
    if len(parts) == 1 and (head.endswith(".exe") or head.endswith(".zip")):
        return "app"            # ImageTriage.exe and its helpers, library.zip: built from this repository each release
    return "base"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _group_hash(files: dict[str, dict]) -> str:
    h = hashlib.sha256()
    for rel in sorted(files):
        h.update(f"{rel}\0{files[rel]['sha256']}\n".encode("utf-8"))
    return h.hexdigest()


def build_manifest(frozen_dir: str | Path, version: str) -> dict:
    """The manifest of a frozen build (the folder the MSI installs), without the zips' addresses (see ``publishable_manifest``)."""
    root = Path(frozen_dir)
    groups: dict[str, dict] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if rel == MANIFEST_NAME:
            continue
        groups.setdefault(group_of(rel), {"files": {}})["files"][rel] = {"sha256": sha256_file(path), "size": path.stat().st_size}
    for group in groups.values():
        group["hash"] = _group_hash(group["files"])
    return {"format": FORMAT, "version": version, "groups": groups}


def write_installed_manifest(frozen_dir: str | Path, version: str) -> Path:
    """Writes ``update-manifest.json`` into a frozen build, so the installed app knows what it is."""
    manifest = build_manifest(frozen_dir, version)
    target = Path(frozen_dir) / MANIFEST_NAME
    target.write_text(json.dumps(manifest, separators=(",", ":")), encoding="utf-8")
    return target


def group_asset_name(name: str, group_hash: str) -> str:
    return f"ImageTriage-update-{name}-{group_hash[:16]}.zip"


def publishable_manifest(
    frozen_dir: str | Path, version: str, out_dir: str | Path, *, download_base: str, previous: dict | None = None
) -> tuple[dict, list[Path]]:
    """The release's update manifest, and the group zips this release must upload (those not already published by an earlier one).

    ``download_base`` is where this release's assets are downloaded from (``.../releases/download/v<version>``)."""
    manifest = build_manifest(frozen_dir, version)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    known = {}
    for name, group in ((previous or {}).get("groups") or {}).items():
        if isinstance(group, dict) and group.get("hash") and group.get("url") and group.get("zip_sha256"):
            known[(name, group["hash"])] = group
    uploads = []
    for name, group in manifest["groups"].items():
        old = known.get((name, group["hash"]))
        if old is not None:
            group.update(url=old["url"], zip_sha256=old["zip_sha256"], zip_size=old["zip_size"])
            continue
        asset = out / group_asset_name(name, group["hash"])
        with zipfile.ZipFile(asset, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for rel in sorted(group["files"]):
                archive.write(Path(frozen_dir) / rel, rel)
        group.update(url=f"{download_base.rstrip('/')}/{asset.name}", zip_sha256=sha256_file(asset), zip_size=asset.stat().st_size)
        uploads.append(asset)
    return manifest, uploads


# ---- the installed app's side ----------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class PatchPlan:
    """What a small update downloads and changes. Empty ``groups`` means the install already matches."""

    version: str
    groups: tuple[str, ...]
    download_bytes: int
    deletes: tuple[str, ...]


def read_installed_manifest(install_dir: str | Path) -> dict | None:
    try:
        manifest = json.loads((Path(install_dir) / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) and manifest.get("format") == FORMAT and isinstance(manifest.get("groups"), dict) else None


def _install_matches(install_dir: Path, manifest: dict) -> bool:
    """The files on disk are the ones the installed manifest lists (by size: hashing 700 MB on every check would take too long;
    the files that are replaced are verified in full)."""
    for group in manifest["groups"].values():
        for rel, info in (group.get("files") or {}).items():
            try:
                if (install_dir / rel).stat().st_size != int(info["size"]):
                    return False
            except (OSError, KeyError, TypeError, ValueError):
                return False
    return True


def _safe_rel(rel: str) -> bool:
    path = PurePosixPath(rel)
    return bool(rel) and not path.is_absolute() and ".." not in path.parts and ":" not in rel and "\\" not in rel


def validate_remote(manifest: object) -> dict:
    """A remote manifest, checked for shape (and for paths that stay inside the install folder). Raises ValueError otherwise."""
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT or not isinstance(manifest.get("groups"), dict):
        raise ValueError("the update manifest is not one this version can read")
    for name, group in manifest["groups"].items():
        if not isinstance(group, dict) or not isinstance(group.get("files"), dict):
            raise ValueError(f"the update manifest's group {name!r} is damaged")
        if not str(group.get("url", "")).startswith("https://") or len(str(group.get("zip_sha256", ""))) != 64:
            raise ValueError(f"the update manifest's group {name!r} has no verifiable download")
        for rel, info in group["files"].items():
            if not _safe_rel(rel) or not isinstance(info, dict) or len(str(info.get("sha256", ""))) != 64:
                raise ValueError(f"the update manifest lists a file it may not: {rel!r}")
            if group_of(rel) != name:
                raise ValueError(f"the update manifest puts {rel!r} in the wrong group")
    return manifest


def plan_patch(remote: dict, install_dir: str | Path, *, msi_size: int = 0) -> PatchPlan | None:
    """What a small update would do to ``install_dir``, or None when the full MSI must be installed instead."""
    install = Path(install_dir)
    local = read_installed_manifest(install)
    if local is None or not _install_matches(install, local):
        return None
    changed = [name for name, group in remote["groups"].items() if (local["groups"].get(name) or {}).get("hash") != group["hash"]]
    remote_files = {rel for group in remote["groups"].values() for rel in group["files"]}
    deletes = sorted(
        rel for name, group in local["groups"].items() for rel in (group.get("files") or {})
        if rel not in remote_files and _safe_rel(rel)
    )
    size = sum(int(remote["groups"][name].get("zip_size") or 0) for name in changed)
    if msi_size and size > MAX_SHARE_OF_MSI * msi_size:
        return None
    return PatchPlan(str(remote.get("version", "")), tuple(changed), size, tuple(deletes))


Progress = Callable[[int, int], None]


def download_patch(
    remote: dict, plan: PatchPlan, staging_root: str | Path, *, progress: Progress | None = None,
    fetch: Callable[[str, Path, Callable[[int], None]], None] | None = None,
) -> Path:
    """Downloads and unpacks the changed groups, verifies every file, and writes the install plan the elevated handoff carries
    out (``<staging>/patch.json``). Returns that file."""
    staging = Path(staging_root) / f"patch-{plan.version}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    files_dir = staging / "files"
    files_dir.mkdir(parents=True)
    done_before = 0
    copies = []
    for name in plan.groups:
        group = remote["groups"][name]
        archive_path = staging / f"{name}.zip"
        (fetch or _fetch)(group["url"], archive_path, lambda n, base=done_before: progress and progress(base + n, plan.download_bytes))
        if sha256_file(archive_path).casefold() != str(group["zip_sha256"]).casefold():
            raise RuntimeError(f"The downloaded update part {name!r} does not match its published checksum.")
        done_before += archive_path.stat().st_size
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
            if names != set(group["files"]):
                raise RuntimeError(f"The update part {name!r} does not hold the files its manifest lists.")
            for rel, info in group["files"].items():
                target = files_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(rel) as source, open(target, "wb") as sink:
                    shutil.copyfileobj(source, sink, 1024 * 1024)
                if sha256_file(target).casefold() != info["sha256"].casefold():
                    raise RuntimeError(f"{rel} in the update part {name!r} does not match its checksum.")
                copies.append({"rel": rel, "sha256": info["sha256"].lower()})
        archive_path.unlink()
    # the manifest the install will have afterwards, written by the handoff once every file is in place (zip addresses dropped)
    after = {"format": FORMAT, "version": plan.version,
             "groups": {n: {"hash": g["hash"], "files": g["files"]} for n, g in remote["groups"].items()}}
    (staging / MANIFEST_NAME).write_text(json.dumps(after, separators=(",", ":")), encoding="utf-8")
    plan_file = staging / "patch.json"
    plan_file.write_text(json.dumps({
        "version": plan.version, "copies": copies, "deletes": list(plan.deletes),
        "manifest_sha256": sha256_file(staging / MANIFEST_NAME),
    }, separators=(",", ":")), encoding="utf-8")
    return plan_file


def _fetch(url: str, target: Path, on_bytes: Callable[[int], None]) -> None:
    from .updater import UPDATER_USER_AGENT, _require_https, _require_https_response

    _require_https(url, "An update part")
    request = urllib.request.Request(url, headers={"User-Agent": UPDATER_USER_AGENT})
    read = 0
    with urllib.request.urlopen(request, timeout=120) as response, open(target, "wb") as handle:
        _require_https_response(response, "An update part")
        while chunk := response.read(1024 * 1024):
            handle.write(chunk)
            read += len(chunk)
            on_bytes(read)


def verify_staged(plan_file: str | Path) -> None:
    """Hash the staged files again before they are handed over (they sat in a temporary folder meanwhile)."""
    staging = Path(plan_file).parent
    plan = json.loads(Path(plan_file).read_text(encoding="utf-8"))
    for copy in plan["copies"]:
        if sha256_file(staging / "files" / copy["rel"]) != copy["sha256"]:
            raise RuntimeError("A downloaded update file changed after it was verified. The update was not installed.")
    if sha256_file(staging / MANIFEST_NAME) != plan["manifest_sha256"]:
        raise RuntimeError("The downloaded update changed after it was verified. The update was not installed.")


def handoff_script(plan_file: Path, install_dir: Path, app_executable: Path, current_pid: int, quote: Callable[[str], str]) -> str:
    """The elevated PowerShell that installs a staged small update (see the module docs): one at a time, machine-wide; after Image
    Triage and anything else running from its folder has exited; files copied into the install folder's own staging folder and
    verified there; each replaced file kept and put back if anything fails; the app started once, as the user, when it is done."""
    plan_hash = sha256_file(plan_file)
    return "\n".join([
        "$ErrorActionPreference = 'Stop'",
        f"$imageTriagePid = {max(0, int(current_pid))}",
        f"$planFile = {quote(str(plan_file))}",
        f"$planHash = '{plan_hash}'",
        f"$install = {quote(str(install_dir))}",
        f"$app = {quote(str(app_executable))}",
        "$log = Join-Path (Split-Path $planFile) 'install.log'",
        "function Note($t) { Add-Content -LiteralPath $log -Value ((Get-Date -Format o) + ' ' + $t) -ErrorAction SilentlyContinue }",
        "$lock = New-Object System.Threading.Mutex($false, 'Global\\ImageTriageUpdateHandoff')",
        "try { $owned = $lock.WaitOne(0) } catch [System.Threading.AbandonedMutexException] { $owned = $true }",
        "if (-not $owned) { exit 0 }",
        "if ($imageTriagePid -gt 0) { try { Wait-Process -Id $imageTriagePid -Timeout 180 -ErrorAction SilentlyContinue } catch {} }",
        # the RAW preview worker, PhotoCraft and the AI helpers run from the install folder too: they are given a moment, then stopped
        "$prefix = [IO.Path]::GetFullPath($install).TrimEnd('\\') + '\\'",
        "$deadline = (Get-Date).AddSeconds(20)",
        "do { $left = @(Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) }); if ($left.Count -eq 0) { break }; Start-Sleep -Milliseconds 500 } while ((Get-Date) -lt $deadline)",
        "$left | ForEach-Object { try { Stop-Process -Id $_.Id -Force -ErrorAction Stop } catch {} }",
        "$stage = Join-Path $install '.update-staging'",
        "$backup = Join-Path $stage 'backup'",
        "$done = New-Object System.Collections.Generic.List[string]",
        "$code = 0",
        "try {",
        "    if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }",
        "    New-Item -ItemType Directory -Force -Path $stage | Out-Null",
        # the plan and the files, where only an administrator can write; then checked there against the plan the app hashed
        "    Copy-Item -LiteralPath $planFile -Destination (Join-Path $stage 'patch.json')",
        "    if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $stage 'patch.json')).Hash -ne $planHash) { throw 'the update plan changed after it was verified' }",
        "    $plan = Get-Content -Raw -LiteralPath (Join-Path $stage 'patch.json') | ConvertFrom-Json",
        "    $src = Split-Path $planFile",
        "    Copy-Item -LiteralPath (Join-Path $src 'update-manifest.json') -Destination (Join-Path $stage 'update-manifest.json')",
        "    if ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $stage 'update-manifest.json')).Hash -ne $plan.manifest_sha256) { throw 'the update manifest changed after it was verified' }",
        "    foreach ($c in $plan.copies) {",
        "        $from = Join-Path (Join-Path $src 'files') $c.rel; $to = Join-Path (Join-Path $stage 'files') $c.rel",
        "        New-Item -ItemType Directory -Force -Path (Split-Path $to) | Out-Null",
        "        Copy-Item -LiteralPath $from -Destination $to",
        "        if ((Get-FileHash -Algorithm SHA256 -LiteralPath $to).Hash -ne $c.sha256) { throw ('changed after it was verified: ' + $c.rel) }",
        "    }",
        "    foreach ($rel in @($plan.copies | ForEach-Object { $_.rel }) + @($plan.deletes)) {",
        "        $target = Join-Path $install $rel",
        "        if (Test-Path -LiteralPath $target) {",
        "            $keep = Join-Path $backup $rel",
        "            New-Item -ItemType Directory -Force -Path (Split-Path $keep) | Out-Null",
        "            Move-Item -LiteralPath $target -Destination $keep -Force",
        "        }",
        "        $done.Add($rel)",
        "    }",
        "    foreach ($c in $plan.copies) {",
        "        $to = Join-Path $install $c.rel",
        "        New-Item -ItemType Directory -Force -Path (Split-Path $to) | Out-Null",
        "        Copy-Item -LiteralPath (Join-Path (Join-Path $stage 'files') $c.rel) -Destination $to -Force",
        "    }",
        "    Copy-Item -LiteralPath (Join-Path $stage 'update-manifest.json') -Destination (Join-Path $install 'update-manifest.json') -Force",
        "    Note ('installed ' + $plan.version)",
        "} catch {",
        "    $code = 1",
        "    Note ('failed: ' + $_)",
        # put back what was moved aside: the app is left as it was
        "    foreach ($rel in $done) {",
        "        $keep = Join-Path $backup $rel; $target = Join-Path $install $rel",
        "        try {",
        "            if (Test-Path -LiteralPath $keep) { New-Item -ItemType Directory -Force -Path (Split-Path $target) | Out-Null; Move-Item -LiteralPath $keep -Destination $target -Force }",
        "            elseif (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force }",
        "        } catch { Note ('could not restore ' + $rel + ': ' + $_) }",
        "    }",
        "}",
        "try { Remove-Item -LiteralPath $stage -Recurse -Force } catch {}",
        "Start-Process -FilePath 'explorer.exe' -ArgumentList ('\"' + $app + '\"')",
        "$lock.ReleaseMutex()",
        "exit $code",
    ])
