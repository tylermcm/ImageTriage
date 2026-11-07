"""The native PocketDrop engine exposes the ABI used by the QML transfer model."""
import pytest
from image_triage.pocketdrop import _bridge

needs_native = pytest.mark.skipif(
    not _bridge.library_path().is_file(), reason="PocketDrop native library is not built"
)

@needs_native
def test_native_library_matches_the_bindings() -> None:
    lib = _bridge.load_library()
    assert lib.pd_abi_version() == _bridge.ABI_VERSION
