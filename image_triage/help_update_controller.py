"""Help pages, the about box and the application updater: the update button, checking, prompting, downloading and the progress dialog. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""
from __future__ import annotations

from PySide6.QtCore import QObject, QSize, QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog, QToolButton
from textwrap import dedent

from .job_controller import JobSpec
from .tasks.update_tasks import AppUpdateCheckTask, AppUpdateDownloadTask
from .ui import HelpMarkdownDialog, show_paged_help
from .ui.help_topics import library_help_pages
from .updater import UpdateCheckResult, UpdateInfo, current_app_version, launch_update_installer_and_restart

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow


class HelpUpdateController(QObject):
    """Help pages, the about box and the application updater: the update button, checking, prompting, downloading and the progress dialog. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window
        self._pending_update_result: UpdateCheckResult | None = None
        self._update_check_silent = False

    def build_update_download_button(self) -> QToolButton:
        button = QToolButton()
        button.setObjectName("updateDownloadButton")
        button.setText("")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setIconSize(QSize(28, 28))
        button.setFixedSize(48, 28)
        button.setAutoRaise(True)
        button.setCursor(Qt.CursorShape.ArrowCursor)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        button.clicked.connect(self.handle_update_button_clicked)
        return button

    def refresh_update_button_state(self) -> None:
        button = getattr(self._window, "update_download_button", None)
        if button is None:
            return
        checking = self._window._active_update_check_task is not None
        downloading = self._window._active_update_download_task is not None
        installing = bool(getattr(self._window, "_update_installing", False))
        update_available = bool(
            self._pending_update_result is not None and self._pending_update_result.update_available
        )
        theme = self._window._theme
        color = QColor(88, 196, 132) if update_available else QColor(129, 135, 146)
        if theme is not None:
            color = theme.success.qcolor() if update_available else theme.text_muted.qcolor()
        if checking:
            tooltip = "Checking for updates..."
        elif downloading:
            tooltip = "Downloading update..."
        elif installing:
            tooltip = "Installing update..."
        elif update_available and self._pending_update_result is not None:
            tooltip = f"Image Triage {self._pending_update_result.latest.version} is available"
        else:
            tooltip = "Check for updates"
        button.setEnabled(not checking and not downloading and not installing)
        button.setToolTip(tooltip)
        button.setStatusTip(tooltip)
        button.setProperty("updateAvailable", update_available)
        button.setIcon(self._window._appearance.update_download_icon(color))
        button.style().unpolish(button)
        button.style().polish(button)

    def handle_update_button_clicked(self) -> None:
        result = self._pending_update_result
        if result is not None and result.update_available:
            self.prompt_for_update_download(result)
            return
        self.check_for_updates(silent=False)

    def open_keyboard_shortcuts_dialog(self) -> None:
        # Settings > Shortcuts is now the one editor for every rebindable key
        # (WI-3.2); this used to open a second, separate dialog with its own
        # store.
        self._window._settings_ctl.show_settings(initial_section="Shortcuts")

    def show_markdown_help_dialog(self, *, title: str, markdown: str) -> None:
        dialog = HelpMarkdownDialog(title=title, markdown=markdown, parent=self._window)
        self._window._exec_dialog_with_geometry(dialog, f"help_{title}")

    def show_paged_help_dialog(self, *, title: str, pages: tuple[object, ...]) -> None:
        show_paged_help(self._window, title=title, pages=pages)

    def show_documentation(self) -> None:
        from .ui.docs import open_documentation

        open_documentation(self._window)

    def show_library_help(self) -> None:
        self.show_paged_help_dialog(
            title="Library Help",
            pages=library_help_pages(),
        )

    def show_help(self) -> None:
        self.show_markdown_help_dialog(
            title="Image Triage Quick Start",
            markdown=dedent(
                """
                # Quick start

                The fastest path from opening a folder to a sorted set.

                1. **Open a folder** — `File > Open Folder...`.
                2. **Select images** — click, `Ctrl`-click, `Shift`-click, or drag to marquee-select.
                3. **Sort quickly** — `W` accept, `X` reject, `K` move to `_keep`, `M` move, `Delete` trash.
                4. **Preview** — `Space` or `Enter`.
                5. **Run batch actions** — right-click or the **Tools** menu for rename, resize, convert, and archive.
                6. **Organize by drag and drop** — drop onto folders or favorites; hold `Ctrl` to copy instead of move.
                7. **Toggle burst views** — **`View > Review View > Smart Groups`** marks likely burst sequences, while **Smart Stacks** collapses similar frames behind one representative.
                8. **Explore AI** — open **`Help > AI Guide`** for scoring, review, and applying clear decisions.

                ## Need more?

                - **`Help > AI Guide`** — the full AI workflow.
                - **`Help > Advanced Help`** — broader controls and shortcuts.
                - Help or **`?`** buttons in the AI Workflow Center, Settings, Catalog, Collections, and Workflow dialogs — focused, step-by-step help.
                """
            ),
        )

    def show_ai_review_tag_legend(self) -> None:
        self.show_markdown_help_dialog(
            title="AI Review Tag Legend",
            markdown=dedent(
                f"""
                # AI Review tag legend

                A quick reference for the AI badges Image Triage can show.

                {self._window._ai_run.ai_review_tags_markdown()}
                """
            ),
        )

    def show_ai_guide(self) -> None:
        self.show_markdown_help_dialog(
            title="Image Triage AI Guide",
            markdown=dedent(
                f"""
                # AI Guide

                AI is a core part of Image Triage. The current culling workflow groups, scores, ranks, and reviews the images in a folder.

                The guiding principle is simple: **AI suggests, you stay in control.**

                ## AI setup

                The installer opens a first-launch setup step for the optional AI runtime and local model files.

                - Choose the GPU or CPU runtime profile.
                - Setup installs the ONNX runtime and the current CLI-Culler model set: CLIP, TOPIQ, and InsightFace quality models.
                - If you skip it, install later from **`AI > AI Setup And Cache > Set Up AI...`**.

                ## What AI adds to review

                Once AI results are loaded, the app can show:

                - ranked groups
                - per-image AI scores
                - top-pick hints
                - compare groups inside preview
                - a saved HTML report for the folder

                ## AI review workflow

                Use this when you want the app to score a folder and help you review it faster. Open **`AI > AI Workflow Center...`** and use its **`?`** button for the detailed, stage-by-stage guide.

                1. Open the folder you want to review.
                2. Open **`AI > AI Workflow Center...`** and run **Cull & Score**.
                3. Wait for extraction, grouping, scoring, and report export to finish.
                4. The app loads the new results and switches into **AI Review** automatically.
                5. Press **`Ctrl+Alt+N`** to jump to the next AI top pick.
                6. Press **`Ctrl+Alt+G`** to compare the current AI group.
                7. Choose **`AI > Run And Apply > Apply AI Decisions`** to auto-file only the clearest winners and rejects.
                8. Later, use **Load Saved** on the AI task rail, or find **Load Saved AI For Folder** in the Command Palette, to reopen cached results without rerunning the models.

                ## AI review tags

                {self._window._ai_run.ai_review_tags_markdown()}

                ## How the cull is scored

                - **pHash** groups near-duplicate frames.
                - **CLIP** scores visual content and assigns semantic categories.
                - **TOPIQ** adds technical quality signals.
                - **InsightFace** adds face and eye quality when faces are present.
                - Similar-image clustering and diversity penalties keep bursts from dominating the top results.

                ## Where AI files live

                Every AI-enabled folder gets a hidden workspace beside the images:

                - **`.image_triage_ai/artifacts`** — CLI-Culler database and intermediate artifacts.
                - **`.image_triage_ai/ranker_report`** — scored exports and the HTML report.

                ## Best practices

                - Start with folders that match the kind of work you care about most.
                - Use the Guided AI Cull when you want a quick keeper percentage and review band.
                - Review the uncertain middle manually before applying file moves.

                ## Troubleshooting

                - If rankings look stale and the folder is unchanged, use **Quick Rerank**.
                - If images were added or removed, rerun **Cull & Score**.
                - If AI actions are disabled, open **`AI > AI Setup And Cache > Set Up AI...`** and check the setup state.
                """
            ),
        )

    def show_advanced_help(self) -> None:
        self.show_markdown_help_dialog(
            title="Image Triage Advanced Help",
            markdown=dedent(
                """
                # Advanced Help

                A broader reference for the rest of the app.

                ## Selection

                - `Ctrl`-click adds or removes an image
                - `Shift`-click selects a range
                - `Ctrl+A` selects all visible images
                - Drag on empty space to marquee-select, like File Explorer
                - Drag selected thumbnails onto folders or favorites to move them
                - Hold `Ctrl` while dragging to copy instead of move
                - **`View > Review View > Smart Groups`** highlights likely capture bursts in the grid as a toggle, not a permanent regrouping
                - **`View > Review View > Smart Stacks`** adds stacked burst visuals plus burst cycling in the main viewer with `[` and `]`

                ## Core review

                - `Space` or `Enter` opens Preview
                - `W` accepts
                - `X` rejects
                - `K` moves to `_keep`
                - `M` moves to a folder
                - `Delete` trashes
                - `Ctrl+Z` undoes the last change
                - `T` tags
                - `C` toggles compare

                ## Tools

                - Use the **Tools** menu for **Batch Rename**, **Batch Resize**, **Batch Convert**, and archive actions
                - Batch tools use the checkbox mode in the grid
                - Resize and Convert are also available from the image right-click menu
                - RAW files are skipped for Resize and Convert
                - The **AI Workflow Center** shows setup, Cull & Score, result review, and applying decisions in order
                - Long AI tasks show progress and a detailed activity log when that option is enabled in Settings

                ## Preview

                - Mouse wheel or `Z` zooms
                - `0` returns to fit
                - `L` toggles the loupe
                - `C` toggles compare
                - `Tab` changes preview focus
                - Left and Right navigate
                - Before/After compares the original with the latest detected edit
                - Open In Photoshop sends the current preview image to Photoshop

                ## Folders and AI

                - Right-click folders or favorites to create, rename, move, delete, or favorite them
                - Recent destinations appear in the copy and move menus for faster sorting
                - The Library panel's bottom **Help** button explains favorites, collections, and catalog search
                - Workflow dialogs include their own **`?`** help for recipes, content mode, transfer mode, and saved recipes
                - Settings includes a **Settings Guide** button for General, Interface, folders, AI Culling, Duplicates, and Shortcuts
                - **AI Review** lets you inspect results, apply clear decisions, or load saved results for the current folder
                - **`Help > AI Guide`** is the dedicated walkthrough for the AI side of the app
                - `Ctrl+Alt+N` jumps to the next AI top pick
                - `Ctrl+Alt+G` compares the current AI group
                """
            ),
        )

    def show_about_dialog(self) -> None:
        QMessageBox.information(
            self._window,
            "About Image Triage",
            "\n".join(
                [
                    "Image Triage",
                    f"Version {current_app_version()}",
                    "",
                    "A desktop photo triage tool built for speed, keyboard-driven flow, and AI-assisted review.",
                    "Sort, rate, and cull large shoots quickly, then hand off or export the keepers.",
                ]
            ),
        )

    def check_for_updates_on_startup(self) -> None:
        if not self._window._check_updates_on_startup:
            return
        self.check_for_updates(silent=True)

    def check_for_updates(self, checked: bool = False, *, silent: bool = False) -> None:
        if self._window._active_update_check_task is not None or self._window._active_update_download_task is not None:
            return
        self._update_check_silent = bool(silent)
        if self._window.actions is not None:
            self._window.actions.check_for_updates.setEnabled(False)
        if not silent:
            self._window.statusBar().showMessage("Checking for updates...")
        task = AppUpdateCheckTask(current_version=current_app_version())
        self._window._active_update_check_task = task
        self.refresh_update_button_state()
        task.signals.finished.connect(self.handle_update_check_finished)
        task.signals.failed.connect(self.handle_update_check_failed)
        self._window._app_update_pool.start(task)

    def handle_update_check_finished(self, raw_result: object) -> None:
        silent = self._update_check_silent
        self._update_check_silent = False
        self._window._active_update_check_task = None
        self._window._inspector.update_action_states()
        if not isinstance(raw_result, UpdateCheckResult):
            if not silent:
                QMessageBox.warning(self._window, "Check For Updates", "The update check returned an unexpected result.")
            self._window.statusBar().showMessage("Update check failed")
            return
        result = raw_result
        latest = result.latest
        if not result.update_available:
            self._pending_update_result = None
            self.refresh_update_button_state()
            if not silent:
                QMessageBox.information(
                    self._window,
                    "Check For Updates",
                    f"Image Triage is up to date.\n\nInstalled version: {result.current_version}",
                )
                self._window.statusBar().showMessage("Image Triage is up to date")
            return

        self._pending_update_result = result
        self.refresh_update_button_state()
        if silent:
            self._window.statusBar().showMessage(f"Update available: Image Triage {latest.version}")
            return

        self.prompt_for_update_download(result)

    def prompt_for_update_download(self, result: UpdateCheckResult) -> None:
        latest = result.latest

        if not latest.is_verifiable:
            QMessageBox.warning(
                self._window,
                "Update Cannot Be Verified",
                f"Image Triage {latest.version} is available, but this release does not publish a "
                "checksum, so the installer cannot be verified and will not be downloaded."
                + (f"{chr(10)}{chr(10)}Release page: {latest.release_notes_url}" if latest.release_notes_url else ""),
            )
            self._window.statusBar().showMessage("Update skipped: no checksum published")
            return

        details = [
            f"Image Triage {latest.version} is available.",
            "",
            f"Installed version: {result.current_version}",
        ]
        if latest.release_notes_url:
            details.extend(["", f"Release notes: {latest.release_notes_url}"])
        details.extend(
            [
                "",
                "Download and install this update now?",
                "Image Triage will close and restart after the silent MSI install finishes.",
            ]
        )
        choice = QMessageBox.question(
            self._window,
            "Update Available",
            "\n".join(details),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if choice != QMessageBox.StandardButton.Yes:
            self._window.statusBar().showMessage("Update download skipped")
            return
        self.download_update_installer(latest)

    def handle_update_check_failed(self, message: str) -> None:
        silent = self._update_check_silent
        self._update_check_silent = False
        self._window._active_update_check_task = None
        self._window._inspector.update_action_states()
        if not silent:
            QMessageBox.warning(self._window, "Check For Updates", message)
            self._window.statusBar().showMessage("Update check failed")

    def download_update_installer(self, update: UpdateInfo) -> None:
        if self._window._active_update_download_task is not None:
            return
        if self._window.actions is not None:
            self._window.actions.check_for_updates.setEnabled(False)
        task = AppUpdateDownloadTask(update=update)
        self._window._active_update_download_task = task
        self.refresh_update_button_state()
        task.signals.started.connect(self.handle_update_download_started)
        task.signals.progress.connect(self.handle_update_download_progress)
        task.signals.finished.connect(self.handle_update_download_finished)
        task.signals.failed.connect(self.handle_update_download_failed)
        self._window._app_update_pool.start(task)

    def handle_update_download_started(self, filename: str) -> None:
        dialog = self.show_update_progress_dialog()
        dialog.setRange(0, 0)
        dialog.setValue(0)
        dialog.setLabelText(f"Downloading {filename}...")
        dialog.show()
        self._window.statusBar().showMessage("Downloading update...")

    def handle_update_download_progress(self, current: int, total: int, filename: str) -> None:
        dialog = self.show_update_progress_dialog()
        if total > 0:
            unit = 1024 * 1024
            total_units = max(1, (int(total) + unit - 1) // unit)
            current_units = min(total_units, (int(current) + unit - 1) // unit)
            dialog.setRange(0, total_units)
            dialog.setValue(current_units)
            dialog.setLabelText(f"Downloading {filename} ({current_units}/{total_units} MB)...")
        else:
            dialog.setRange(0, 0)
            dialog.setLabelText(f"Downloading {filename}...")

    def handle_update_download_finished(self, installer_path: str) -> None:
        self._window._active_update_download_task = None
        self.close_update_progress_dialog()
        self._window._inspector.update_action_states()
        try:
            self._window._update_installing = True
            self.refresh_update_button_state()
            launch_update_installer_and_restart(installer_path)
        except Exception as exc:
            self._window._update_installing = False
            self.refresh_update_button_state()
            QMessageBox.warning(self._window, "Install Update", str(exc))
            self._window.statusBar().showMessage("Could not launch update installer")
            return
        self._window.statusBar().showMessage("Installing update; Image Triage will restart when finished")
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(300, app.quit)

    def handle_update_download_failed(self, message: str) -> None:
        self._window._active_update_download_task = None
        self.close_update_progress_dialog()
        self._window._inspector.update_action_states()
        QMessageBox.warning(self._window, "Download Update", message)
        self._window.statusBar().showMessage("Update download failed")

    def show_update_progress_dialog(self) -> QProgressDialog:
        return self._window._export_jobs.show_job_progress_dialog(
            key="app_update",
            total_steps=1,
            spec=JobSpec(
                title="Image Triage Update",
                preparing_label="Downloading update...",
                running_label="Downloading update...",
                window_modality=Qt.WindowModality.ApplicationModal,
            ),
        )

    def close_update_progress_dialog(self) -> None:
        self._window._export_jobs.close_job_progress_dialog("app_update")
