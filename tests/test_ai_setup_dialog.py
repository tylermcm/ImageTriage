"""The Set Up AI dialog: defaults per PC, pills and single models, the one download number, the CPU warning on click, and what pressing the button means."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from image_triage import ai_setup_plan as plan
from image_triage.ai_runtime_packages import AI_RUNTIME_CPU_VARIANT, AI_RUNTIME_GPU_VARIANT
from image_triage.ui.ai_setup_dialog import CPU_WARNING, OUTCOME_CHECK, OUTCOME_INSTALL, OUTCOME_REMOVE, AISetupDialog, SetupDialogInput


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def make(app, *, gpu: bool, installed=frozenset(), core_installed: bool = False, selection=None, **kwargs) -> AISetupDialog:
    data = SetupDialogInput(
        gpu=gpu,
        variant=AI_RUNTIME_GPU_VARIANT if gpu else AI_RUNTIME_CPU_VARIANT,
        core_installed=core_installed,
        installed=frozenset(installed),
        selection=plan.default_selection(gpu=gpu, installed=installed) if selection is None else frozenset(selection),
    )
    dialog = AISetupDialog(data, **kwargs)
    dialog.show()
    QApplication.processEvents()
    return dialog


def test_a_laptop_starts_with_the_core_only(app) -> None:
    dialog = make(app, gpu=False)
    assert dialog.selection() == frozenset()
    assert dialog.total.text() == plan.format_mb(plan.core_mb(AI_RUNTIME_CPU_VARIANT)) == "2.9 GB"
    assert not dialog._pills["cull"].switch.isChecked()
    assert not dialog._pills["edit"].switch.isChecked()
    assert dialog.install.text() == "Install"
    dialog.close()


def test_a_gpu_pc_starts_with_everything(app) -> None:
    dialog = make(app, gpu=True)
    assert dialog.selection() == plan.all_model_keys()
    assert dialog.total.text() == "10.4 GB"
    assert dialog._pills["cull"].switch.isChecked() and dialog._pills["edit"].switch.isChecked()
    assert "2.7 GB" in dialog._pills["cull"].size.text() and "871 MB" in dialog._pills["edit"].size.text()
    dialog.close()


def test_the_dialog_keeps_one_size_when_pills_open(app) -> None:
    dialog = make(app, gpu=True)
    before = dialog.size()
    dialog._pills["cull"].chevron.setChecked(True)
    dialog._pills["edit"].chevron.setChecked(True)
    QApplication.processEvents()
    assert dialog.size() == before
    assert dialog._pills["edit"].kids.isVisible()
    dialog.close()


def test_a_pill_switch_selects_all_of_its_models_and_a_single_model_makes_it_partial(app) -> None:
    dialog = make(app, gpu=False)
    pill = dialog._pills["edit"]
    pill.switch.click()
    assert dialog.selection() == {m.key for m in plan.models_in("edit")}
    assert dialog.total.text() == plan.format_mb(plan.core_mb(AI_RUNTIME_CPU_VARIANT) + 871)
    pill.boxes["depth"].setChecked(False)
    pill.boxes["oneformer"].setChecked(False)
    assert dialog.selection() == {"birefnet", "sam"}
    assert "2 of 4 models" in pill.note.text()
    assert "575 MB" in pill.size.text()
    assert pill.switch.isChecked()
    pill.switch.click()  # off
    assert not any(key in dialog.selection() for key in pill.boxes)
    dialog.close()


def test_the_cpu_warning_appears_only_after_culling_is_touched(app) -> None:
    dialog = make(app, gpu=False)
    warning = dialog._pills["cull"].warning
    assert not warning.isVisible()
    assert warning.text() == CPU_WARNING
    dialog._pills["cull"].switch.click()
    QApplication.processEvents()
    assert warning.isVisible()
    dialog._pills["cull"].switch.click()  # off again: no warning for something that is not selected
    QApplication.processEvents()
    assert not warning.isVisible()
    # Editor tools never show it, and a GPU never does.
    dialog._pills["edit"].switch.click()
    QApplication.processEvents()
    assert not warning.isVisible()
    dialog.close()
    gpu = make(app, gpu=True)
    gpu._pills["cull"].chevron.setChecked(True)
    QApplication.processEvents()
    assert not gpu._pills["cull"].warning.isVisible()
    gpu.close()


def test_installed_models_are_not_downloaded_again_and_removal_is_offered(app) -> None:
    everything = plan.all_model_keys()
    dialog = make(app, gpu=True, installed=everything, core_installed=True)
    assert dialog.total.text() == "Up to date"
    assert dialog.install.text() == "Check installation"
    dialog.install.click()
    assert dialog.outcome == OUTCOME_CHECK
    again = make(app, gpu=True, installed=everything, core_installed=True)
    again._pills["edit"].switch.click()
    assert again.to_remove() == {m.key for m in plan.models_in("edit")}
    assert again.install.text() == "Remove 4 models"
    again.install.click()
    assert again.outcome == OUTCOME_REMOVE
    again.close()


def test_install_needs_something_to_download(app) -> None:
    dialog = make(app, gpu=False)
    dialog.install.click()
    assert dialog.outcome == OUTCOME_INSTALL
    assert dialog.result()


def test_not_now_and_the_more_menu_leave_no_outcome(app) -> None:
    calls: list[str] = []
    dialog = make(app, gpu=False, on_repair=lambda: calls.append("repair"))
    labels = [a.text() for a in dialog._menu.actions() if a.text()]
    assert labels[1:] == ["Check installation", "Repair", "Remove everything"]
    assert "Needs about" in labels[0]
    repair = next(a for a in dialog._menu.actions() if a.text() == "Repair")
    check = next(a for a in dialog._menu.actions() if a.text() == "Check installation")
    assert repair.isEnabled() and not check.isEnabled()  # only the actions the controller passed in
    repair.trigger()
    assert dialog.outcome is None and not dialog.result()
    dialog.pending_menu_action()()
    assert calls == ["repair"]
    later = make(app, gpu=False)
    later.later.click()
    assert later.outcome is None and not later.result()
