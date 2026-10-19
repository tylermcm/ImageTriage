"""Calibration survives recipe classification and requires explicit editor support."""
from unittest.mock import Mock

import pytest

from image_triage import photocraft_bridge as bridge


CALIBRATION = [
    {"calibShadowTint": 25},
    {"calibHue": [0, -20, 0]},
    {"calibSat": [0, 0, 40]},
]


def spec(settings):
    return {"recipe": {"version": 1, "cameraRaw": settings, "identity": True}}


@pytest.mark.parametrize("settings", CALIBRATION)
def test_active_calibration_overrides_stale_identity(settings):
    assert bridge.classify_recipe(spec(settings)["recipe"])[0] == bridge.RECIPE_EDITED


def test_neutral_calibration_is_identity():
    settings = {"calibShadowTint": 0, "calibHue": [0, 0, 0], "calibSat": [0, 0, 0]}
    assert bridge.classify_recipe(spec(settings)["recipe"])[0] == bridge.RECIPE_IDENTITY


@pytest.mark.parametrize("settings", [
    {"calibShadowTint": True}, {"calibShadowTint": [0]},
    {"calibShadowTint": -101}, {"calibShadowTint": float("nan")},
    {"calibHue": [0, 0]}, {"calibSat": [0, 0, 0, 0]},
    {"calibHue": [0, True, 0]}, {"calibSat": [0, float("inf"), 0]},
    {"calibSat": [0, 101, 0]}, {"calibHue": "000"},
])
def test_bad_calibration_is_invalid(settings):
    assert bridge.classify_recipe(spec(settings)["recipe"])[0] == bridge.RECIPE_INVALID


@pytest.mark.parametrize("settings", CALIBRATION + [{"calibHue": [0, 0, 0]}])
def test_older_editor_is_refused_before_opening_calibration(settings):
    control = object.__new__(bridge.PhotoCraftControl)
    control.camera_raw_supported = True
    control.camera_raw_finishing_supported = True
    control.call = Mock(return_value={})
    with pytest.raises(bridge.PhotoCraftCompatibilityError, match="Calibration"):
        control.app_open("photo.dng", camera_raw=spec(settings))
    control.call.assert_not_called()
    control.camera_raw_calibration_supported = True
    control.app_open("photo.dng", camera_raw=spec(settings))
    control.call.assert_called_once_with("app.open", {"path": "photo.dng", "cameraRaw": spec(settings)})


def test_old_editor_still_opens_recipes_without_calibration():
    control = object.__new__(bridge.PhotoCraftControl)
    control.camera_raw_supported = True
    control.call = Mock(return_value={})
    control.app_open("photo.dng", camera_raw=spec({"exposure": 1}))
    control.call.assert_called_once()


@pytest.mark.parametrize("capability, expected", [(None, False), (0, False), (1, True)])
def test_handoff_reads_independent_calibration_capability(capability, expected):
    control = object.__new__(bridge.PhotoCraftControl)
    capabilities = {"version": 2, "cameraRawFinishing": 1}
    if capability is not None:
        capabilities["cameraRawCalibration"] = capability

    def call(method, params=None):
        if method == "app.handoff":
            return capabilities
        if method == "app.stash":
            raise bridge.PhotoCraftError("stash requires path and preview")
        if method == "app.bind":
            raise bridge.PhotoCraftError("bind requires an open document, .pcraft path and .png preview")
        raise AssertionError(method)

    control.call = call
    control.require_hosted_handoff()
    assert control.camera_raw_calibration_supported is expected
