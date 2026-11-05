"""Independent mask capability negotiation at the hosted editor boundary."""
from unittest.mock import Mock

import pytest

from image_triage import photocraft_bridge as bridge


@pytest.mark.parametrize("version, expected", [(None, False), (0, False), (1, True)])
def test_handoff_reads_mask_capability(version, expected):
    control = object.__new__(bridge.PhotoCraftControl)
    capabilities = {"version": 2, "cameraRawFinishing": 1}
    if version is not None:
        capabilities["cameraRawMasks"] = version

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
    assert control.camera_raw_masks_supported is expected


@pytest.mark.parametrize("supported", [False, True])
def test_open_preserves_masks_or_refuses_before_sending(supported):
    control = object.__new__(bridge.PhotoCraftControl)
    control.camera_raw_supported = True
    control.camera_raw_masks_supported = supported
    control.call = Mock(return_value={})
    spec = {"recipe": {"version": 1, "cameraRaw": {"masks": [
        {"shape": {"type": "linear", "x0": 0.5, "y0": 0.1, "x1": 0.5, "y1": 0.8},
         "adjust": {"exposure": 1.0}}
    ]}}}
    if supported:
        control.app_open("test.dng", camera_raw=spec)
        assert control.call.call_args.args[1]["cameraRaw"] == spec
    else:
        with pytest.raises(bridge.PhotoCraftCompatibilityError):
            control.app_open("test.dng", camera_raw=spec)
        control.call.assert_not_called()
