"""The uninstall clean-up (audit R7): the MSI tables setup_msi.py hands to cx_Freeze, and what the clean-up tool removes.

No MSI is built and no registry is touched here. setup_msi.py is run with a stand-in for cx_Freeze, so these tests read the
table rows the real build would be given; whether Windows Installer runs them as intended is only known on Windows.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from image_triage.file_associations import supported_file_association_suffixes

ROOT = Path(__file__).resolve().parents[1]

# Windows Installer's standard places in InstallExecuteSequence, and cx_Freeze's place for RemoveExistingProducts.
COST_FINALIZE, REMOVE_FILES, REMOVE_EXISTING_PRODUCTS = 1000, 3500, 1450
# Custom action type bits.
EXE_BY_COMMAND_LINE_IN_DIRECTORY, CONTINUE, ASYNC, IN_SCRIPT, NO_IMPERSONATE = 34, 64, 128, 1024, 2048


@pytest.fixture(scope="module")
def setup_msi():
    """setup_msi.py as the build runs it, with cx_Freeze replaced and the asset staging (which copies an interpreter) skipped."""
    import freeze_support

    cx_freeze = ModuleType("cx_Freeze")
    cx_freeze.Executable = lambda **options: SimpleNamespace(**options)
    cx_freeze.setup = lambda **options: None
    patches = pytest.MonkeyPatch()
    patches.setitem(sys.modules, "cx_Freeze", cx_freeze)
    patches.setattr(freeze_support, "prepare_ai_build_assets", lambda *a, **k: SimpleNamespace(include_files=[]))
    patches.setattr(freeze_support, "photocraft_bundle_files", lambda: [])
    patches.setattr(freeze_support, "raw_preview_bundle_files", lambda: [])
    try:
        spec = importlib.util.spec_from_file_location("setup_msi_under_test", ROOT / "setup_msi.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        patches.undo()


@pytest.fixture(scope="module")
def cleanup_tool():
    spec = importlib.util.spec_from_file_location("image_triage_cleanup_uninstall_test", ROOT / "packaging" / "image_triage_cleanup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tables(setup_msi) -> dict:
    return setup_msi.bdist_msi_options["data"]


def _cleanup_action(setup_msi) -> tuple:
    actions = [row for row in _tables(setup_msi).get("CustomAction", []) if "image_triage_cleanup" in str(row[3])]
    assert len(actions) == 1, "the installer registers exactly one clean-up action"
    return actions[0]


def test_the_installer_runs_the_bundled_cleanup_program_on_uninstall(setup_msi):
    action, kind, source, target = _cleanup_action(setup_msi)
    shipped = {executable.target_name for executable in setup_msi.executables}
    assert "image_triage_cleanup.exe" in shipped
    assert source == "TARGETDIR"                                         # a Directory entry: the working folder
    assert target.startswith('"[TARGETDIR]image_triage_cleanup.exe" ')  # the program the installer itself put there
    arguments = target.split('" ', 1)[1].split()
    assert arguments[:2] == ["--mode", "uninstall"]                      # the scope that keeps the user's data
    assert "--yes" in arguments and "--quiet" in arguments
    assert "--dry-run" not in arguments
    assert action and len(action) <= 72 and action.replace("_", "").isalnum()


def test_the_cleanup_action_runs_as_the_uninstalling_user_and_cannot_fail_the_uninstall(setup_msi):
    _, kind, _, _ = _cleanup_action(setup_msi)
    assert kind & 0x3F == EXE_BY_COMMAND_LINE_IN_DIRECTORY
    assert kind & CONTINUE          # its exit code is ignored
    assert not kind & ASYNC         # and it is waited for: it must finish before its own file is removed
    # Immediate, impersonated: it sees the HKEY_CURRENT_USER of the person uninstalling. A deferred or
    # no-impersonation action would run as SYSTEM and clean nobody's associations.
    assert not kind & IN_SCRIPT
    assert not kind & NO_IMPERSONATE


def test_the_cleanup_action_is_sequenced_for_a_full_removal_only_and_before_the_files_go(setup_msi):
    action = _cleanup_action(setup_msi)[0]
    rows = [row for row in _tables(setup_msi).get("InstallExecuteSequence", []) if row[0] == action]
    assert len(rows) == 1
    _, condition, sequence = rows[0]
    assert condition == 'REMOVE="ALL" AND NOT UPGRADINGPRODUCTCODE'      # not a repair, and not the old version leaving in an upgrade
    assert COST_FINALIZE < sequence < REMOVE_FILES                       # [TARGETDIR] is known, the program is still there
    assert sequence != REMOVE_EXISTING_PRODUCTS
    # nothing runs it at any other time: not in the UI sequence, and no second row in any sequence table
    for table in ("InstallUISequence", "AdminExecuteSequence", "AdvtExecuteSequence"):
        assert all(row[0] != action for row in _tables(setup_msi).get(table, []))
    # the tables the build already relied on are still there
    assert ("ProgramMenuFolder", "TARGETDIR", ".") in _tables(setup_msi)["Directory"]
    assert setup_msi.bdist_msi_options["all_users"] is True


def test_every_suffix_the_cleanup_removes_comes_from_the_one_shared_list(cleanup_tool):
    shared = supported_file_association_suffixes()
    assert tuple(cleanup_tool.SUPPORTED_SUFFIXES) == tuple(shared)
    for registrations_only in (True, False):
        _, values = cleanup_tool._registry_targets(registrations_only=registrations_only)
        assert [key for key, _ in values] == [rf"Software\Classes\{suffix}\OpenWithProgids" for suffix in shared]
        assert {name for _, name in values} == {cleanup_tool.APP_PROG_ID}
    # the tool's source spells out no second list of types to remove besides the fallback for running it on its own
    source = (ROOT / "packaging" / "image_triage_cleanup.py").read_text(encoding="utf-8")
    assert source.count('".jpg"') == 1 and source.count("for suffix in SUPPORTED_SUFFIXES") == 1


def test_the_cleanup_and_the_app_agree_on_what_a_registration_is(cleanup_tool):
    from image_triage import file_associations as fa

    trees, _ = cleanup_tool._registry_targets(registrations_only=True)
    assert trees == [fa._APPLICATIONS_KEY, fa._PROG_ID_KEY]
    assert (cleanup_tool.APP_PROG_ID, cleanup_tool.APP_EXE_NAME) == (fa.APP_PROG_ID, fa.APP_EXE_NAME)


def test_an_uninstall_removes_registrations_and_caches_and_nothing_the_user_made(cleanup_tool, tmp_path, monkeypatch):
    roaming, local, home, temp = (tmp_path / name for name in ("Roaming", "Local", "home", "Temp"))
    kept = [
        roaming / "Image Triage" / "Image Triage" / "decisions.sqlite3",     # decisions, ratings, sessions
        roaming / "ImageTriage" / "catalog.sqlite3",
        roaming / "Codex" / "Image Triage" / "decisions.sqlite3",
        local / "Image Triage" / "Image Triage" / "cache" / "thumb.bin",
        home / ".image-triage" / "AI" / "models" / "model.onnx",
        home / "Pictures" / "trip" / "frame.nef",
        home / "Pictures" / "trip" / ".image_triage_edits" / "frame.nef.pcraft",
    ]
    removed = [
        local / "image_triage_ai_cache" / "models" / "model.onnx",
        temp / "image_triage_ai_cache" / "runtime" / "wheel.whl",
        temp / "image-triage-association-probes" / "image_triage_probe.nef",
    ]
    for path in kept + removed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    for name, value in (("APPDATA", roaming), ("LOCALAPPDATA", local), ("USERPROFILE", home), ("HOME", home)):
        monkeypatch.setenv(name, str(value))
    monkeypatch.setattr(cleanup_tool.tempfile, "gettempdir", lambda: str(temp))

    assert cleanup_tool.main(["--mode", "uninstall", "--yes", "--quiet"]) == 0

    assert [path for path in kept if not path.exists()] == []
    assert [path for path in removed if path.exists()] == []
    # the settings are not a registration either
    trees, _ = cleanup_tool._registry_targets(registrations_only=True)
    assert all(tree.startswith("Software\\Classes\\") for tree in trees)
    # asked for by hand, the tool still removes everything it owns (that is what the manual mode is for)
    full_trees, _ = cleanup_tool._registry_targets()
    assert r"Software\Image Triage" in full_trees
    assert cleanup_tool.main(["--mode", "manual", "--yes", "--quiet"]) == 0
    assert not (roaming / "Image Triage" / "Image Triage").exists()
    assert (home / "Pictures" / "trip" / "frame.nef").exists() and (home / "Pictures" / "trip" / ".image_triage_edits" / "frame.nef.pcraft").exists()


def test_what_uninstall_cleans_and_cannot_clean_is_documented():
    text = (ROOT / "docs" / "KNOWN_ISSUES.md").read_text(encoding="utf-8")
    section = text[text.index("## Uninstalling leaves other Windows users"):]
    section = section[: section.index("\n---")]
    for phrase in ("HKEY_CURRENT_USER", "other users", "decisions", "image_triage_cleanup.exe", "not been run on Windows"):
        assert phrase in section, phrase
