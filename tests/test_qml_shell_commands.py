"""Command palette commands that run something in the QML shell (the ids are the Widgets palette's)."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QObject

from image_triage.qml_shell.ai_scores import AiScores
from image_triage.qml_shell.photos import PhotoModel
from test_qml_shell import _load_main, pump, qapp, rows  # noqa: F401  (the same helpers and application fixture)


@pytest.fixture
def shell(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    context = engine.rootContext()
    context.contextProperty("photos").setRows(rows(12), "/p")
    window.setWidth(1440)
    window.setHeight(900)
    window.show()
    pump(qapp, 0.4)
    yield window, context
    window.close()
    pump(qapp, 0.1)


def run(context, command):
    context.contextProperty("cmdPalette").executed.emit(command)
    pump(qapp_of(context), 0.05)


def qapp_of(context):
    from PySide6.QtCore import QCoreApplication

    return QCoreApplication.instance()


def test_help_commands_open_the_help_windows(shell):
    window, context = shell
    reference = context.contextProperty("reference")
    run(context, "help.about")
    assert reference.view["window"] == "help" and reference.view["help"]["kind"] == "about"
    reference.close()
    run(context, "help.keyboard_help")
    assert reference.view["window"] == "help"
    reference.close()
    run(context, "workflow.keyboard_shortcuts")
    assert reference.view["window"] == "keys"
    reference.close()
    run(context, "library.browse_catalog")
    assert reference.view["window"] == "lib"


def test_ai_commands_open_the_cull_and_ai_center_windows(shell):
    window, context = shell
    cull = context.contextProperty("cull")
    run(context, "ai.workflow_center")
    assert cull.view["window"] == "center"
    cull.close()
    run(context, "ai.run_pipeline")
    assert cull.view["window"] == "cull"


def test_auto_advance_toggles_the_setting_and_its_on_off_tag(shell):
    window, context = shell
    settings, palette = context.contextProperty("settings"), context.contextProperty("cmdPalette")
    before = settings.value("autoAdvance")
    run(context, "review.auto_advance")
    assert settings.value("autoAdvance") == (not before)
    run(context, "review.auto_advance")
    assert settings.value("autoAdvance") == before


def test_zen_mode_puts_the_panels_away_and_brings_them_back(shell):
    window, context = shell
    assert window.property("panelTarget") > 0 and window.property("inspTarget") > 0
    run(context, "view.zen_mode")
    pump(qapp_of(context), 0.2)
    assert window.property("panelTarget") == 0 and window.property("inspTarget") == 0
    run(context, "view.zen_mode")
    pump(qapp_of(context), 0.2)
    assert window.property("panelTarget") > 0 and window.property("inspTarget") > 0


class _Result:
    def __init__(self, top):
        self.is_top_pick = top


class _Bundle:
    def __init__(self, tops):
        self.tops = tops

    def result_for_path(self, path):
        return _Result(path in self.tops)


def test_next_top_pick_finds_the_next_one_after_the_current_and_wraps(qapp):
    model = PhotoModel(parent=qapp)
    model.setRows(rows(8), "/p")
    scores = AiScores(model, memory=True, parent=qapp)
    assert scores.nextTopPick(0) == -1                       # no results loaded
    scores._bundle = _Bundle({model.row_at(3)["path"], model.row_at(6)["path"]})
    assert scores.nextTopPick(0) == 3
    assert scores.nextTopPick(3) == 6
    assert scores.nextTopPick(6) == 3                        # wraps round
    scores._bundle = _Bundle(set())
    assert scores.nextTopPick(2) == -1
