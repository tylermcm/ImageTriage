"""The AI status chip and its card: works out what to say from the setup state, keeps the top-bar chip and the status-bar "AI ready" text in step, opens the
card that hangs from either, and shows the first-run prompt under the chip on the first Analyze without setup."""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import QObject, QTimer

from . import ai_setup_plan as plan
from .ai_manifest import MODEL_BUNDLES
from .ai_status import AIState, AIStatus, compute_status
from .ui.ai_status_widgets import AIStatusChip, AIStatusPopover, AIStatusText, build_card

if TYPE_CHECKING:
    from .window import MainWindow

REFRESH_INTERVAL_MS = 20_000
FIRST_RUN_KEY = "ai/first_run_prompt_answered"


class AiStatusController(QObject):
    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window
        self.chip: AIStatusChip | None = None
        self.text: AIStatusText | None = None
        self._popover: AIStatusPopover | None = None
        self._status = compute_status(core_installed=False)
        self._install_message = ""
        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_INTERVAL_MS)
        self._timer.timeout.connect(self.refresh)

    # ------------------------------------------------------------------ wiring

    def bind(self, chip: AIStatusChip, text: AIStatusText) -> None:
        self.chip, self.text = chip, text
        chip.clicked.connect(lambda _checked=False: self.show_card(below=True))
        text.clicked.connect(lambda _checked=False: self.show_card(below=False))
        self._timer.start()
        QTimer.singleShot(1500, self.refresh)  # after start-up has settled; resolving the runtime and bundles reads the disk
        self.apply(self._status)

    def status(self) -> AIStatus:
        return self._status

    def note_install_message(self, message: str) -> None:
        """Called with the setup's progress text; it becomes the Installing card's line."""
        self._install_message = message or ""
        self.refresh()

    # ------------------------------------------------------------------ state

    def facts(self) -> AIStatus:
        setup = self._window._ai_setup
        window = self._window
        installing = any(
            task is not None
            for task in (
                window._active_ai_runtime_task,
                window._active_ai_model_task,
                setup._active_ai_bundle_task,
                setup._active_ai_repair_task,
            )
        )
        if installing:  # nothing else is worth working out while a setup run is going
            summary = setup.setup_progress_summary()
            percent, line = summary if summary is not None else (None, "")
            return compute_status(core_installed=True, installing=True, install_message=line or self._install_message, percent=percent)
        core = setup.core_installed()
        problems = sum(1 for health in setup._last_ai_readiness_results.values() if not getattr(health, "ready", True))
        missing_mb = 0
        if core:
            try:
                missing_mb = sum(MODEL_BUNDLES[key].approx_mb for key in setup.missing_capability_bundles() if key in MODEL_BUNDLES)
            except Exception:  # a broken bundle listing must not break the window's chip
                missing_mb = 0
        default_mb = 0
        if not core:
            gpu, variant = setup.ai_setup_hardware()
            installed = setup.installed_optional_models()
            saved = plan.load_selection(window._settings)
            selection = (saved | installed) if saved is not None else plan.default_selection(gpu=gpu, installed=installed)
            default_mb = plan.download_mb(selection, variant=variant, core_installed=False, installed=installed)
        return compute_status(
            core_installed=core,
            installing=installing,
            install_message=self._install_message if installing else "",
            default_download_mb=default_mb,
            missing_mb=missing_mb,
            problems=problems,
        )

    def refresh(self) -> None:
        try:
            status = self.facts()
        except Exception:  # never let the status indicator take the window down
            return
        self.apply(status)

    def apply(self, status: AIStatus) -> None:
        self._status = status
        if self.chip is not None:
            self.chip.set_status(status)
            self.chip.setVisible(status.needs_attention)
        if self.text is not None:
            self.text.setVisible(not status.needs_attention)

    # ------------------------------------------------------------------ the card

    def _open_setup(self) -> None:
        self.close_card()
        self._window._ai_setup.download_ai_model()

    def _check(self) -> None:
        self.close_card()
        self._window._ai_setup.check_ai_readiness()

    def _repair(self) -> None:
        self.close_card()
        self._window._ai_setup.repair_ai_components()

    def _not_now(self) -> None:
        self.close_card()

    def _show_details(self) -> None:
        self.close_card()
        self._window._ai_setup.show_progress_dialog()

    def _cancel_install(self) -> None:
        self.close_card()
        self._window._ai_setup.cancel_setup()

    def card_for(self, status: AIStatus):
        """The card content for a status: (widget, buttons by label)."""
        size = f" ({status.small})" if status.small else ""
        if status.state is AIState.READY:
            installed = self._window._ai_setup.installed_optional_models()
            ranking = bool(installed & {m.key for m in plan.models_in(plan.GROUP_CULL)})
            return build_card(
                "AI is ready",
                "Everything installed and verified.",
                rows=[
                    ("Faces, eyes-closed, People", "Ready"),
                    ("Cleanup (stacks, duplicates, blown-out, soft)", "Ready"),
                    ("Ranking (quality, appeal)", "Ready" if ranking else "Not installed (optional)"),
                ],
                buttons=[("Open AI setup", True, self._open_setup), ("Check installation", False, self._check)],
            )
        if status.state is AIState.NEEDS_SETUP:
            return build_card(
                "AI is not set up",
                status.detail,
                buttons=[(f"Set up AI{size}", True, self._open_setup), ("Not now", False, self._not_now)],
            )
        if status.state is AIState.INSTALLING:
            return build_card(
                "Installing AI",
                status.detail,
                progress=status.percent,
                buttons=[("Show details", False, self._show_details), ("Cancel", False, self._cancel_install)],
            )
        if status.state is AIState.UPDATE:
            return build_card(
                "AI update",
                status.detail,
                buttons=[(f"Update{size}", True, self._open_setup), ("Later", False, self._not_now)],
            )
        return build_card(
            "AI needs repair",
            status.detail,
            buttons=[("Repair", True, self._repair), ("Open AI setup", False, self._open_setup)],
        )

    def show_card(self, *, below: bool) -> None:
        anchor = self.chip if (self.chip is not None and self.chip.isVisible()) else self.text
        if anchor is None:
            return
        self.close_card()
        body, _buttons = self.card_for(self._status)
        popover = AIStatusPopover(self._window)
        popover.set_body(body)
        popover.popup_at(anchor, below=below)
        self._popover = popover

    def close_card(self) -> None:
        if self._popover is not None:
            self._popover.close()
            self._popover.deleteLater()
            self._popover = None

    # ------------------------------------------------------------------ first run

    def first_run_prompt_needed(self) -> bool:
        """True once: Analyze was pressed on a PC with no AI setup, and the person has not answered this prompt before."""
        if self._window._settings.value(FIRST_RUN_KEY, False, bool):
            return False
        return not self._window._ai_setup.core_installed()

    def show_first_run_prompt(self, proceed: Callable[[], None]) -> bool:
        """Hang the prompt from the chip. ``proceed`` runs Analyze with the cleanup passes only (Analyze without it). Returns False when there is no chip to hang it from."""
        self.refresh()
        if self.chip is None or not self.chip.isVisible():
            return False
        self.close_card()
        size = f" ({self._status.small})" if self._status.small else ""

        def set_up() -> None:
            self._answered()
            self.close_card()
            self._window._ai_setup.download_ai_model()

        def without() -> None:
            self._answered()
            self.close_card()
            proceed()

        body, _buttons = build_card(
            "Set up AI?",
            "Analyze works now. A one-time download adds eyes-closed detection and People.",
            buttons=[(f"Set up AI{size}", True, set_up), ("Analyze without it", False, without)],
            width=380,
        )
        popover = AIStatusPopover(self._window)
        popover.set_body(body)
        popover.popup_at(self.chip, below=True)
        self._popover = popover
        return True

    def _answered(self) -> None:
        self._window._settings.setValue(FIRST_RUN_KEY, True)
