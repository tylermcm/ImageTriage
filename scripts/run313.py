"""Run a script or module under the Store Python 3.13, logging to a file.

pythonw3.13.exe is the only launcher for the project's real interpreter that
this shell can start, and it has no console — hence the redirect. Codex shells
can omit standard Windows variables, so restore the values Python and Qt need
before importing the requested module.
"""
import os
from pathlib import Path
import runpy
import sys
import traceback


script_path = Path(__file__).resolve()
USER_HOME = next(
    (
        parent
        for parent in script_path.parents
        if parent.parent.name.casefold() == "users"
    ),
    None,
)
if USER_HOME is None:
    USER_HOME = Path.home()
windows_root_value = os.environ.get("SystemRoot") or os.environ.get("WINDIR") or r"C:\Windows"
WINDOWS_ROOT = Path(r"C:\Windows" if "%" in windows_root_value else windows_root_value)
SYSTEM_DRIVE = WINDOWS_ROOT.drive or "C:"
LOCAL_APP_DATA = USER_HOME / "AppData" / "Local"
ROAMING_APP_DATA = USER_HOME / "AppData" / "Roaming"
TEMP_DIR = LOCAL_APP_DATA / "Temp"

for key, value in {
    "SystemDrive": SYSTEM_DRIVE,
    "SystemRoot": WINDOWS_ROOT,
    "WINDIR": WINDOWS_ROOT,
    "USERPROFILE": USER_HOME,
    "HOMEDRIVE": USER_HOME.drive,
    "HOMEPATH": str(USER_HOME)[len(USER_HOME.drive) :],
    "HOME": USER_HOME,
    "LOCALAPPDATA": LOCAL_APP_DATA,
    "APPDATA": ROAMING_APP_DATA,
    "PROGRAMDATA": WINDOWS_ROOT.parent / "ProgramData",
    "TEMP": TEMP_DIR,
    "TMP": TEMP_DIR,
    "USERNAME": USER_HOME.name,
}.items():
    current = os.environ.get(key, "")
    if not current or "%" in current:
        os.environ[key] = str(value)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

repo_root = script_path.parents[1]
venv_packages = repo_root / ".msi_build_venv" / "Lib" / "site-packages"
if venv_packages.is_dir() and str(venv_packages) not in sys.path:
    sys.path.insert(0, str(venv_packages))

log = open(sys.argv[1], "w", encoding="utf-8", buffering=1)
sys.stdout = sys.stderr = log
sys.argv = sys.argv[2:]
code = 0
try:
    target = sys.argv[0]
    if target.endswith(".py"):
        runpy.run_path(target, run_name="__main__")
    else:
        runpy.run_module(target, run_name="__main__", alter_sys=True)
except SystemExit as exc:
    code = exc.code if isinstance(exc.code, int) else 1
except BaseException:
    traceback.print_exc()
    code = 1
log.write(f"\n__EXIT__={code}\n")
log.close()
raise SystemExit(code)
