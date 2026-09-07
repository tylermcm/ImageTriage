import json
import os
from pathlib import Path
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidgetItem

from image_triage.ui.photo_editor_panel import (
    BUILTIN_PHOTO_PRESETS,
    EditRecipe,
    MASK_ADJUSTMENT_KEYS,
    PRESET_RECIPE_KEYS,
    _load_custom_photo_presets,
    _preset_values_from_recipe,
    _recipe_with_preset,
    recipe_for_mask,
)


def test_builtin_presets_have_unique_keys_and_valid_recipe_values() -> None:
    keys = [key for key, _name, _values in BUILTIN_PHOTO_PRESETS]

    assert len(keys) == len(set(keys))
    assert all(values for _key, _name, values in BUILTIN_PHOTO_PRESETS)
    for _key, _name, values in BUILTIN_PHOTO_PRESETS:
        assert set(values).issubset(PRESET_RECIPE_KEYS)
        EditRecipe.from_dict(values)


def test_applying_preset_replaces_look_but_preserves_structural_edits() -> None:
    current = EditRecipe(
        exposure=1.5,
        contrast=40,
        saturation=25,
        crop=(10, 20, 300, 400),
        background_mode="blur",
        background_amount=45,
        lensblur_amount=30,
    )

    applied = _recipe_with_preset(current, {"contrast": 8, "vibrance": 16})

    assert applied.exposure == 0
    assert applied.saturation == 0
    assert applied.contrast == 8
    assert applied.vibrance == 16
    assert applied.crop == current.crop
    assert applied.background_mode == "blur"
    assert applied.background_amount == 45
    assert applied.lensblur_amount == 30


def test_applying_preset_to_mask_uses_only_local_adjustments() -> None:
    current = EditRecipe(contrast=35, shadows=-20)

    applied = _recipe_with_preset(
        current,
        {"contrast": 8, "vibrance": 16, "vignette": -40},
        keys=MASK_ADJUSTMENT_KEYS,
    )

    assert applied.contrast == 8
    assert applied.shadows == 0
    assert applied.vibrance == 16
    assert applied.vignette == 0


def test_panel_applies_preset_as_selected_mask_operations() -> None:
    app = QApplication.instance() or QApplication([])
    from image_triage.ui.photo_editor_panel import PhotoEditorPanel

    panel = PhotoEditorPanel()
    panel._source_path = Path("photo.jpg")
    mask = {"id": "mask-001", "type": "radial", "params": {}}
    panel._session = {"masks": [mask], "operations": [], "coordinateSpaces": []}
    item = QListWidgetItem("Mask")
    item.setData(Qt.ItemDataRole.UserRole, "mask-001")
    panel.masks_list.addItem(item)
    panel.masks_list.setCurrentItem(item)
    panel._refresh_preset_targets()
    panel.preset_target_combo.setCurrentIndex(panel.preset_target_combo.findData("mask-001"))

    panel._apply_preset("Local Contrast", {"contrast": 18, "vibrance": 12, "vignette": -30})

    recipe = recipe_for_mask(panel._session, "mask-001")
    assert recipe.contrast == 18
    assert recipe.vibrance == 12
    assert recipe.vignette == 0
    assert panel._mask_commit_timer.isActive()
    panel._mask_commit_timer.stop()
    panel.close()
    app.processEvents()


def test_image_without_sidecar_drops_previous_images_mask_session() -> None:
    app = QApplication.instance() or QApplication([])
    from image_triage.ui.photo_editor_panel import PhotoEditorPanel

    with tempfile.TemporaryDirectory(prefix="image_triage_preset_session_") as temp_dir:
        root = Path(temp_dir)
        previous = root / "previous.jpg"
        current = root / "current.jpg"
        panel = PhotoEditorPanel()
        panel._source_path = previous
        panel._session = {
            "masks": [{"id": "mask-old", "type": "bitmap", "params": {}}],
            "operations": [],
            "coordinateSpaces": [],
        }

        panel.set_image(current)

        assert panel._session is None
        assert panel.masked_adjustments() == []
        assert panel.preset_target_combo.count() == 1
        assert panel.preset_target_combo.currentData() == "photo"
        panel.close()
        app.processEvents()


def test_custom_preset_round_trip_drops_invalid_and_duplicate_entries() -> None:
    values = _preset_values_from_recipe(EditRecipe(contrast=12, curve_rgb=[[0, 0], [255, 255]]))
    raw = json.dumps(
        [
            {"name": "Editorial", "values": values},
            {"name": " editorial ", "values": {"contrast": 99}},
            {"name": "", "values": values},
            {"name": "Broken"},
        ]
    )

    loaded = _load_custom_photo_presets(raw)

    assert [preset["name"] for preset in loaded] == ["Editorial"]
    assert loaded[0]["values"]["contrast"] == 12
    assert loaded[0]["values"]["curve_rgb"] == [[0, 0], [255, 255]]
