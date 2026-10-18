"""Host and Rust finishing recipes must agree on validation and edit identity."""
import pytest
from image_triage.photocraft_bridge import classify_recipe, RECIPE_IDENTITY, RECIPE_EDITED, RECIPE_INVALID


def state(settings, **extra):
    return classify_recipe({"version": 1, "cameraRaw": settings, **extra})[0]


def test_finishing_edits_override_stale_identity_flag():
    assert state({"glow": {"amount": 50, "style": "bloom"}}, identity=True) == RECIPE_EDITED
    for key in ("hue", "saturation", "luminance", "variance"):
        assert state({"pointColors": [{"color": [0.8, 0.1, 0.2], key: 25}]}, identity=True) == RECIPE_EDITED
    assert state({"glow": {"amount": 0, "range": 70}, "pointColors": [{"color": [0.8, 0.1, 0.2], "range": 40}]}) == RECIPE_IDENTITY


@pytest.mark.parametrize("settings", [
    {"glow": {"amount": True}}, {"glow": {"amount": 101}}, {"glow": {"style": "unknown"}},
    {"glow": {"spread": float("nan")}}, {"glow": {"other": 1}}, {"pointColors": [{}] * 9},
    {"pointColors": [{"color": [1, 0]}]}, {"pointColors": [{"color": [2, 0, 0]}]},
    {"pointColors": [{"range": -1}]}, {"pointColors": [{"variance": float("inf")}]},
    {"pointColors": [{"unknown": 1}]}, {"pointColors": [True]},
])
def test_invalid_finishing_recipes_are_not_silently_treated_as_unedited(settings):
    assert state(settings) == RECIPE_INVALID


def test_older_editor_is_refused_before_new_adjustments_can_be_lost():
    from image_triage.photocraft_bridge import require_finishing_support, PhotoCraftCompatibilityError
    for fields in ({"glow": {"amount": 40}}, {"pointColors": [{"color": [1, 0, 0]}]}):
        spec={"recipe": {"version": 1, "cameraRaw": fields}}
        with pytest.raises(PhotoCraftCompatibilityError):
            require_finishing_support(spec, False)
        require_finishing_support(spec, True)
    require_finishing_support({"recipe": {"version": 1, "cameraRaw": {"hslHue": [10]*8}}}, False)
