"""Handoff workflows export through Batch Export: with an export preset, or (older workflows) with their own fields mapped onto it."""
import pytest
from PIL import Image
from PySide6.QtCore import QCoreApplication, QStandardPaths
from PySide6.QtGui import QGuiApplication

from image_triage.image_resize import ResizeSourceItem
from image_triage.workflows import WorkflowRecipe, apply_workflow_export_plan, build_workflow_export_plan
from image_triage.workflows.export import recipe_export_values
from image_triage.workflows import dump_saved_workflow_recipes, load_saved_workflow_recipes, recipe_summary_lines


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


@pytest.fixture(autouse=True)
def isolated():
    QStandardPaths.setTestModeEnabled(True)
    yield
    QStandardPaths.setTestModeEnabled(False)


@pytest.fixture
def shoot(tmp_path):
    folder = tmp_path / "shoot"
    folder.mkdir()
    for name, size in (("a.jpg", (3000, 2000)), ("b.png", (800, 600))):
        Image.new("RGB", size, (40, 90, 160)).save(folder / name)
    return [ResizeSourceItem(str(folder / n), n) for n in ("a.jpg", "b.png")]


def test_an_older_workflow_keeps_its_export(shoot, tmp_path):
    recipe = WorkflowRecipe(key="p", name="Proofing JPEGs", resize_preset_key="large", convert_suffix=".jpg", strip_metadata=True,
                            rename_prefix="proof_")
    values = recipe_export_values(recipe)
    assert (values["sizeMode"], values["fitW"], values["fitH"], values["noEnlarge"]) == ("fit", "1920", "1080", True)
    assert values["format"] == "jpg" and values["meta"] == "none" and values["xPrefix"] == "proof_"
    plan = build_workflow_export_plan(shoot, recipe, destination_dir=str(tmp_path / "out"))
    assert plan.can_apply and plan.output_label == "JPEG | fits 1920 x 1080"
    outcome = apply_workflow_export_plan(plan)
    sizes = {p.split("/")[-1]: Image.open(p).size for p in outcome}
    assert sizes == {"proof_a.jpg": (1620, 1080), "proof_b.jpg": (800, 600)}         # not enlarged


def test_keep_format_writes_each_photo_in_its_own_format(shoot, tmp_path):
    plan = build_workflow_export_plan(shoot, WorkflowRecipe(key="k", name="Keep"), destination_dir=str(tmp_path / "out"))
    assert [i.target_name for i in plan.items] == ["a.jpg", "b.png"] and plan.output_label == "Original format | Original Size"


def test_a_workflow_with_a_preset_exports_with_it(shoot, tmp_path):
    recipe = WorkflowRecipe(key="w", name="Web", export_preset="Web 2048 px", rename_suffix="_web")
    values = recipe_export_values(recipe)
    assert values["sizeMode"] == "long" and values["long"] == "2048" and values["sharpen"] == "screen" and values["xSuffix"] == "_web"
    outcome = apply_workflow_export_plan(build_workflow_export_plan(shoot, recipe, destination_dir=str(tmp_path / "out")))
    assert {p.split("/")[-1]: Image.open(p).size for p in outcome} == {"a_web.jpg": (2048, 1365), "b_web.jpg": (800, 600)}
    assert "with the Web 2048 px export preset" in recipe_summary_lines(recipe)[0]


def test_a_deleted_preset_falls_back_to_the_workflows_own_fields(shoot):
    recipe = WorkflowRecipe(key="w", name="Gone", export_preset="Nobody's preset", convert_suffix=".png")
    assert recipe_export_values(recipe)["format"] == "png"


def test_the_preset_name_is_saved_with_the_workflow():
    recipe = WorkflowRecipe(key="w", name="Web", export_preset="Web 2048 px")
    assert load_saved_workflow_recipes(dump_saved_workflow_recipes([recipe]))[0].export_preset == "Web 2048 px"
    older = load_saved_workflow_recipes(dump_saved_workflow_recipes([WorkflowRecipe(key="o", name="Old")]))[0]
    assert older.export_preset == ""


def test_handoff_window_choice_disables_the_fields_a_preset_decides(qapp):
    from image_triage.qml_shell.handoff_model import HandoffModel
    from image_triage.qml_shell.photos import PhotoModel

    handoff = HandoffModel(PhotoModel(parent=qapp), memory=True, parent=qapp)
    handoff.begin()
    assert handoff.enabled("resize") and handoff.enabled("preset")
    assert "Web 2048 px" in [o["value"] for o in handoff.options("preset")]
    handoff.set("preset", "Web 2048 px")
    assert not handoff.enabled("resize") and not handoff.enabled("convert") and not handoff.enabled("strip") and handoff.enabled("affixes")
    assert handoff._recipe().export_preset == "Web 2048 px"
