"""Small updates (image_triage/update_patch.py): only the groups of the install that changed are downloaded, every file is verified,
and the elevated install replaces exactly those files, putting them back when it fails."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from image_triage import update_patch as up
from image_triage import updater


def frozen(root: Path, files: dict[str, bytes]) -> Path:
    for rel, data in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
    return root


V1 = {
    "ImageTriage.exe": b"exe 1",
    "lib/image_triage/app.pyc": b"app 1",
    "lib/image_triage/old_only.pyc": b"gone in 2",
    "lib/PySide6/Qt6Core.dll": b"qt" * 1000,
    "photocraft/photocraft.exe": b"pc" * 1000,
    "image-triage-raw-preview.exe": b"worker 1",
    "python313.dll": b"py",
}
V2 = {**{k: v for k, v in V1.items() if k != "lib/image_triage/old_only.pyc"},
      "ImageTriage.exe": b"exe 2", "lib/image_triage/app.pyc": b"app 2", "lib/image_triage/new.pyc": b"new in 2"}


def test_groups_split_the_app_from_what_rarely_changes():
    assert up.group_of("lib/image_triage/qml_shell/qml/Main.qml") == "app"
    assert up.group_of("ImageTriage.exe") == "app" and up.group_of("library.zip") == "app"
    assert up.group_of("photocraft/photocraft.exe") == "photocraft"
    assert up.group_of("image-triage-raw-preview.exe") == "raw-preview"
    assert up.group_of("lib/PySide6/Qt6Core.dll") == "qt"
    assert up.group_of("python313.dll") == "base"
    # what changes with every release is the app's: its compiled modules and the AI workers' copies of its code
    assert up.group_of("lib/library.zip") == "app" and up.group_of("ai_workers/py/image_triage/_version.py") == "app"


def releases(tmp_path):
    """v1 published in full, v2 publishing only what changed; and an install of v1."""
    one = frozen(tmp_path / "build1", V1)
    two = frozen(tmp_path / "build2", V2)
    m1, up1 = up.publishable_manifest(one, "1.0.0", tmp_path / "out1", download_base="https://example.invalid/v1.0.0")
    m2, up2 = up.publishable_manifest(two, "1.0.1", tmp_path / "out2", download_base="https://example.invalid/v1.0.1", previous=m1)
    install = frozen(tmp_path / "install", V1)
    up.write_installed_manifest(install, "1.0.0")
    assets = {f"https://example.invalid/v1.0.0/{p.name}": p for p in up1} | {f"https://example.invalid/v1.0.1/{p.name}": p for p in up2}
    return m2, up2, install, assets


def test_a_release_uploads_only_the_groups_it_changed(tmp_path):
    m2, up2, _install, _assets = releases(tmp_path)
    assert sorted(p.name.split("-")[2] for p in up2) == ["app"]
    assert m2["groups"]["photocraft"]["url"].startswith("https://example.invalid/v1.0.0/")       # kept from the release that made it


def test_an_install_downloads_only_what_it_differs_in_and_ends_up_identical(tmp_path):
    m2, _up2, install, assets = releases(tmp_path)
    remote = up.validate_remote(json.loads(json.dumps(m2)))
    plan = up.plan_patch(remote, install)
    assert plan is not None and plan.groups == ("app",)
    assert plan.deletes == ("lib/image_triage/old_only.pyc",)
    fetched = []

    def fetch(url, target, on_bytes):
        fetched.append(url)
        shutil.copyfile(assets[url], target)
        on_bytes(target.stat().st_size)

    plan_file = up.download_patch(remote, plan, tmp_path / "staging", fetch=fetch)
    assert len(fetched) == 1 and "-app-" in fetched[0]
    up.verify_staged(plan_file)
    run_handoff(plan_file, install)
    for rel, data in V2.items():
        assert (install / rel).read_bytes() == data, rel
    assert not (install / "lib/image_triage/old_only.pyc").exists()
    assert not (install / ".update-staging").exists()
    assert up.plan_patch(remote, install).groups == ()                 # the install now is 1.0.1, by its own manifest too
    assert up.read_installed_manifest(install)["version"] == "1.0.1"


def test_a_failed_install_puts_every_file_back(tmp_path):
    m2, _up2, install, assets = releases(tmp_path)
    remote = up.validate_remote(m2)
    plan = up.plan_patch(remote, install)
    plan_file = up.download_patch(remote, plan, tmp_path / "staging", fetch=lambda url, target, _cb: shutil.copyfile(assets[url], target))
    # the file the update removes is held open (as a running program would hold it): it cannot be moved, after the replaced files
    # already have been, so the install fails half way through
    with open(install / "lib/image_triage/old_only.pyc", "rb"):
        run_handoff(plan_file, install, expect_failure=True)
    for rel, data in V1.items():
        if (install / rel).is_file():
            assert (install / rel).read_bytes() == data, rel
    assert up.read_installed_manifest(install)["version"] == "1.0.0"


def test_a_staged_file_changed_after_the_download_is_refused(tmp_path):
    m2, _up2, install, assets = releases(tmp_path)
    remote = up.validate_remote(m2)
    plan = up.plan_patch(remote, install)
    plan_file = up.download_patch(remote, plan, tmp_path / "staging", fetch=lambda url, target, _cb: shutil.copyfile(assets[url], target))
    (plan_file.parent / "files" / "ImageTriage.exe").write_bytes(b"swapped")
    with pytest.raises(RuntimeError, match="changed after it was verified"):
        up.verify_staged(plan_file)


def test_the_full_msi_is_used_when_the_install_does_not_match_its_manifest_or_has_none(tmp_path):
    m2, _up2, install, _assets = releases(tmp_path)
    (install / "lib/image_triage/app.pyc").write_bytes(b"edited by hand, a different size")
    assert up.plan_patch(m2, install) is None
    (install / up.MANIFEST_NAME).unlink()
    assert up.plan_patch(m2, install) is None


def test_a_small_update_that_is_most_of_the_msi_is_not_used(tmp_path):
    m2, _up2, install, _assets = releases(tmp_path)
    size = m2["groups"]["app"]["zip_size"]
    assert up.plan_patch(m2, install, msi_size=size) is None
    assert up.plan_patch(m2, install, msi_size=size * 10) is not None


def test_a_manifest_naming_files_outside_the_install_is_refused(tmp_path):
    m2, *_ = releases(tmp_path)
    bad = json.loads(json.dumps(m2))
    bad["groups"]["base"]["files"]["../evil.dll"] = {"sha256": "0" * 64, "size": 1}
    with pytest.raises(ValueError):
        up.validate_remote(bad)


def test_the_release_feed_names_the_update_manifest():
    info = updater._update_info_from_github_release({
        "tag_name": "v9.0.0", "html_url": "https://example.invalid/r",
        "assets": [
            {"name": "ImageTriage-9.0.0-win64.msi", "browser_download_url": "https://example.invalid/a.msi", "digest": "sha256:" + "a" * 64, "size": 250_000_000},
            {"name": "ImageTriage-9.0.0-update.json", "browser_download_url": "https://example.invalid/u.json", "digest": "sha256:" + "b" * 64},
        ],
    })
    assert info.patch_manifest_url == "https://example.invalid/u.json" and info.patch_manifest_sha256 == "b" * 64
    assert info.installer_size == 250_000_000


def run_handoff(plan_file: Path, install: Path, *, expect_failure: bool = False) -> None:
    if os.name != "nt":
        pytest.skip("the handoff is PowerShell")
    script = up.handoff_script(plan_file, install, install / "ImageTriage.exe", 0, updater._powershell_quote)
    script = "\n".join(line for line in script.splitlines() if "explorer.exe" not in line)     # the test starts no app
    path = plan_file.parent / "handoff.ps1"
    path.write_text(script, encoding="utf-8-sig")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(path)], capture_output=True, text=True, timeout=120)
    assert (result.returncode != 0) is expect_failure, (result.returncode, result.stdout, result.stderr, (plan_file.parent / "install.log").read_text() if (plan_file.parent / "install.log").exists() else "")
