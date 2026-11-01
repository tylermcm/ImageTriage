from __future__ import annotations

from image_triage import bracket_patterns as bp


def test_ids_are_unique_and_lookup_works():
    ids = [pattern.id for pattern in bp.PATTERNS]
    assert len(ids) == len(set(ids))
    assert bp.get_pattern("ae_sony").id == "ae_sony"


def test_every_pattern_is_sourced_and_labelled():
    for pattern in bp.PATTERNS:
        assert pattern.sources, pattern.id
        assert pattern.status in bp.STATUSES, pattern.id
        assert pattern.summary, pattern.id
        assert all(source.startswith("https://") for source in pattern.sources), pattern.id


def test_phone_capture_is_not_in_the_library():
    banned = ("apple", "iphone", "google", "smartphone")
    for pattern in bp.PATTERNS:
        text = " ".join((pattern.id, pattern.label, *pattern.makers)).casefold()
        assert not any(word in text for word in banned), pattern.id


def test_exposure_ladders_declare_order_and_counts():
    for pattern in bp.exposure_ladder_patterns():
        if pattern.id == "in_camera_hdr":
            continue
        assert pattern.orders, pattern.id
        assert pattern.frame_counts, pattern.id


def test_non_ev_patterns_include_the_look_alikes():
    ids = {pattern.id for pattern in bp.patterns_not_found_by_ev()}
    assert {"pixel_shift", "focus_bracket", "aperture_dof_bracket", "motion_shutter_bracket"} <= ids
    assert "ae_mode_mapped" not in ids


def test_tag_lookup_by_maker():
    names = {tag.name for _, tag in bp.tags_for_maker("pentax")}
    assert {"BracketShotNumber", "ExposureBracketStepSize"} <= names
