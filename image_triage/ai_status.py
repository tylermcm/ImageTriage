"""What the AI status chip says. Pure (no Qt): the controller feeds it facts about this PC and the widgets show the result.

Needs setup, Installing, Update and Repair are chips in the top bar (they need the person); Ready is quiet text in the status bar.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .ai_setup_plan import format_mb


class AIState(str, Enum):
    READY = "ready"
    NEEDS_SETUP = "needs_setup"
    INSTALLING = "installing"
    UPDATE = "update"
    REPAIR = "repair"


@dataclass(frozen=True)
class AIStatus:
    state: AIState
    label: str
    small: str = ""  # the muted text after the label: a size, a count of problems
    detail: str = ""  # one line for the popover
    percent: int | None = None  # while installing

    @property
    def needs_attention(self) -> bool:
        """Shown as a chip in the top bar (Ready is not)."""
        return self.state is not AIState.READY


def compute_status(
    *,
    core_installed: bool,
    installing: bool = False,
    install_message: str = "",
    default_download_mb: int = 0,
    missing_mb: int = 0,
    problems: int = 0,
    percent: int | None = None,
) -> AIStatus:
    """The one status to show. An install in progress wins, then a failed check, then a missing core, then models that arrived with an update."""
    if installing:
        return AIStatus(
            AIState.INSTALLING,
            "Installing AI",
            f"{percent}%" if percent is not None else "",
            install_message or "Working on it. You can keep using Image Triage.",
            percent,
        )
    if problems > 0:
        word = "problem" if problems == 1 else "problems"
        return AIStatus(AIState.REPAIR, "AI needs repair", f"{problems} {word}", "The last check found something to fix.")
    if not core_installed:
        return AIStatus(AIState.NEEDS_SETUP, "Set up AI", format_mb(default_download_mb) if default_download_mb else "", "A one-time download adds eyes-closed detection, People and search.")
    if missing_mb > 0:
        return AIStatus(AIState.UPDATE, "AI update", format_mb(missing_mb), "Models this version uses are not installed yet.")
    return AIStatus(AIState.READY, "AI ready", detail="Everything installed and verified.")
