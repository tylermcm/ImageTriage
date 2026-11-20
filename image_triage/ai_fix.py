"""What fixes an AI tool's error, so the panel that shows it can offer the fix as a button instead of leaving the person to work it out.

``kind(message)`` sorts an error into what fixes it:

- ``"repairApp"``: a file of the app itself is missing (a worker script, the RAW preview worker). Windows Installer puts the missing
  files back (``repair_app``: ``msiexec /fp``, which reinstalls only files that are missing, so nothing in use is replaced).
- ``"aiCenter"``: the AI runtime or a model is not set up, or does not start. The AI Center installs and repairs those.
- ``""``: nothing to offer (a run that was cancelled).

An error an AI tool shows itself (``ai_tool=True``) always gets a fix: one that is not recognised goes to the AI Center, which checks
the runtime, the graphics card and every model and repairs what does not work. Elsewhere (a toast) only a recognised AI error does.
"""
from __future__ import annotations

import subprocess
import sys

REPAIR_APP = "repairApp"
AI_CENTER = "aiCenter"

_APP_FILES = ("host script is missing", "worker is missing", "worker unavailable", "reinstall image triage",
              "needs the raw preview worker", "needs the updated raw preview worker", "raw preview worker needs to be rebuilt")
_AI_SETUP = ("ai runtime", "ai center", "set up ai", "not installed", "no depth model", "model is missing", "does not start",
             "install it in ai")

_NOT_A_FAULT = ("cancelled", "canceled")

LABELS = {REPAIR_APP: "Repair Image Triage", AI_CENTER: "Fix in AI Center"}

# The installer's upgrade code (setup_msi.py): every version of Image Triage shares it.
UPGRADE_CODE = "{2C50910A-F1EA-4C94-A730-2D39264677E1}"


def kind(message: str, ai_tool: bool = False) -> str:
    text = (message or "").lower()
    if not text or any(part in text for part in _NOT_A_FAULT):
        return ""
    if any(part in text for part in _APP_FILES):
        return REPAIR_APP
    if any(part in text for part in _AI_SETUP) or ai_tool:
        return AI_CENTER
    return ""


def installed_product_code() -> str | None:
    """The product code of the installed Image Triage (None when it was not installed with its installer, as in a source checkout)."""
    if sys.platform != "win32":
        return None
    import winreg

    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                uninstall = winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", 0, winreg.KEY_READ | view)
            except OSError:
                continue
            with uninstall:
                index = 0
                while True:
                    try:
                        name = winreg.EnumKey(uninstall, index)
                    except OSError:
                        break
                    index += 1
                    if not name.startswith("{"):
                        continue
                    try:
                        with winreg.OpenKey(uninstall, name) as entry:
                            display = str(winreg.QueryValueEx(entry, "DisplayName")[0])
                    except OSError:
                        continue
                    if display == "Image Triage" or display.startswith("Image Triage "):
                        return name
    return None


def repair_app(run=subprocess.Popen) -> str:
    """Ask Windows Installer to put back the app's missing files. Returns "" once it is started, or why it could not be."""
    if not getattr(sys, "frozen", False):
        return "This copy of Image Triage runs from its source folder: restore the missing file from the repository."
    code = installed_product_code()
    if code is None:
        return "Image Triage's installation was not found. Reinstall it from its installer."
    try:
        run(["msiexec", "/fp", code])          # only files that are missing; Windows asks for permission itself
    except OSError as exc:
        return f"The repair could not be started: {exc}"
    return ""
