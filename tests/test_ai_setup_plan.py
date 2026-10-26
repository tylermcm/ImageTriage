"""The Set Up AI plan: groups, sizes, defaults for a PC with and without a GPU, the capabilities a choice maps to, and the saved choice."""
from __future__ import annotations

import pytest

from image_triage import ai_setup_plan as plan
from image_triage.ai_manifest import CAPABILITIES, MODEL_BUNDLES, setup_capabilities
from image_triage.ai_runtime_packages import AI_RUNTIME_CPU_VARIANT, AI_RUNTIME_GPU_VARIANT


class MemorySettings:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}

    def value(self, key, default="", type=None):  # noqa: A002 - mirrors QSettings
        return self.data.get(key, default)

    def setValue(self, key, value) -> None:
        self.data[key] = value


def test_every_optional_model_has_a_bundle_and_a_capability_that_installs_it() -> None:
    for item in plan.OPTIONAL_MODELS:
        assert item.bundle in MODEL_BUNDLES
        capability = CAPABILITIES[item.capability]
        assert capability.model_bundles == (item.bundle,)
        assert capability.requires_torch


def test_group_sizes_match_what_the_setup_page_shows() -> None:
    assert plan.group_mb(plan.GROUP_EDIT) == 425 + 150 + 100 + 196 == 871
    assert plan.group_mb(plan.GROUP_CULL) == 543 + 2130 == 2673
    assert plan.format_mb(plan.group_mb(plan.GROUP_EDIT)) == "871 MB"
    assert plan.format_mb(plan.group_mb(plan.GROUP_CULL)) == "2.7 GB"


def test_core_includes_the_runtime_with_pytorch_faces_and_search() -> None:
    faces_and_search = 4 + 166 + 98
    assert plan.core_mb(AI_RUNTIME_CPU_VARIANT) == 2600 + faces_and_search
    assert plan.core_mb(AI_RUNTIME_GPU_VARIANT) == 6600 + faces_and_search
    assert set(plan.CORE_BUNDLES) <= set(MODEL_BUNDLES)


def test_defaults_everything_with_a_gpu_and_nothing_extra_without_one() -> None:
    assert plan.default_selection(gpu=True) == plan.all_model_keys()
    assert plan.default_selection(gpu=False) == frozenset()
    # Whatever is already installed stays selected, so a PC is never offered its own removal as the default.
    assert plan.default_selection(gpu=False, installed={"maniqa", "not-a-model"}) == {"maniqa"}


def test_download_size_counts_the_core_only_when_missing_and_skips_installed_models() -> None:
    everything = plan.all_model_keys()
    fresh = plan.download_mb(everything, variant=AI_RUNTIME_GPU_VARIANT, core_installed=False)
    assert fresh == plan.core_mb(AI_RUNTIME_GPU_VARIANT) + plan.group_mb(plan.GROUP_CULL) + plan.group_mb(plan.GROUP_EDIT)
    assert plan.format_mb(fresh) == "10.4 GB"
    assert plan.download_mb(everything, variant=AI_RUNTIME_GPU_VARIANT, core_installed=True, installed=everything) == 0
    assert plan.download_mb({"sam"}, variant=AI_RUNTIME_CPU_VARIANT, core_installed=True) == 150
    assert plan.download_mb((), variant=AI_RUNTIME_CPU_VARIANT, core_installed=False) == plan.core_mb(AI_RUNTIME_CPU_VARIANT)


def test_capabilities_follow_the_selection_in_a_stable_order() -> None:
    assert plan.capabilities_for(()) == ()
    assert plan.capabilities_for({"sam", "maniqa"}) == ("rank_quality", "editor_select")
    base = setup_capabilities(include_torch=True, optional=plan.capabilities_for({"qrealign"}))
    assert base[-1] == "rank_aesthetic"
    assert "rank_quality" not in base
    # With no choice made, the PyTorch runtime keeps adding the ranker pair as it always did.
    assert setup_capabilities(include_torch=True)[-1] == "cascade_rankers"
    assert setup_capabilities(include_torch=False) == setup_capabilities(include_torch=False, optional=("rank_quality",))


def test_the_saved_choice_round_trips_and_ignores_junk() -> None:
    settings = MemorySettings()
    assert plan.load_selection(settings) is None
    plan.save_selection(settings, {"depth", "maniqa", "ghost"})
    assert plan.load_selection(settings) == {"depth", "maniqa"}
    plan.save_selection(settings, ())
    assert plan.load_selection(settings) == frozenset()  # an explicit "none" is a choice, not "never chose"
    for junk in ("not json", "{}", "[1, 2]", ""):
        settings.data[plan.SETTINGS_KEY] = junk
        assert plan.load_selection(settings) in (None, frozenset())


@pytest.mark.parametrize("mb,text", [(0, "0 MB"), (871, "871 MB"), (999, "999 MB"), (1000, "1.0 GB"), (6868, "6.9 GB")])
def test_format_mb(mb: int, text: str) -> None:
    assert plan.format_mb(mb) == text
