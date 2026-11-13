"""A photo whose Camera Raw recipe could not be read is usable again once the recipe has been set aside: it opens from no
recipe, says so once, keeps every set-aside copy, and what is still good in the copy can be put back."""
from __future__ import annotations

import json

import pytest

from image_triage import edit_storage, photocraft_bridge as bridge
from image_triage.qml_shell.editor_host import EDITING
from test_qml_shell import qapp  # noqa: F401  (the application fixture)
from test_qml_shell_editor import folder, make, wait  # noqa: F401


def write(photo, content):
    target = bridge.recipe_path(str(photo))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return target


def test_a_photo_whose_recipe_was_set_aside_is_usable_again(tmp_path):
    photo = tmp_path / "frame.NEF"
    target = write(photo, "not json")
    assert bridge.recipe_state(str(photo))[0] == bridge.RECIPE_INVALID          # still there, unread: refused, and nothing moved
    assert target.exists()
    assert bridge.load_recipe(str(photo)) is None                               # opening it for editing sets it aside
    # Until the person has been told, an export still fails rather than quietly export the original ...
    state, _recipe, reason = bridge.recipe_state(str(photo))
    assert state == bridge.RECIPE_INVALID and "set aside" in reason and edit_storage.session_has_edits(str(photo))
    assert bridge.take_quarantine_notice(str(photo)) is not None
    assert bridge.recipe_state(str(photo)) == (bridge.RECIPE_ABSENT, None, "")  # ... then the photo has no recipe: the original develop
    assert not edit_storage.session_has_edits(str(photo))
    assert bridge.quarantined_recipes(str(photo)) == [target.with_name(target.name + ".bad")]


def test_the_notice_names_the_copy_and_is_given_once(tmp_path):
    photo = tmp_path / "frame.NEF"
    target = write(photo, "not json")
    assert bridge.take_quarantine_notice(str(photo)) is None
    bridge.load_recipe(str(photo))
    notice = bridge.take_quarantine_notice(str(photo))
    bad = target.with_name(target.name + ".bad")
    assert notice.bad_path == bad and "not valid JSON" in notice.reason
    assert "frame.NEF" in notice.message and bad.name in notice.message and str(bad.parent) in notice.message
    assert bridge.take_quarantine_notice(str(photo)) is None
    assert bridge.load_recipe(str(photo)) is None and bridge.take_quarantine_notice(str(photo)) is None   # nothing new was set aside


def test_set_aside_copies_are_numbered_and_none_is_replaced(tmp_path):
    photo = tmp_path / "frame.NEF"
    target = write(photo, "first")
    bridge.load_recipe(str(photo))
    write(photo, "second")
    bridge.load_recipe(str(photo))
    write(photo, "third")
    assert bridge.recipe_state(str(photo), set_aside=True)[0] == bridge.RECIPE_ABSENT
    copies = bridge.quarantined_recipes(str(photo))
    assert [path.name for path in copies] == [target.name + ".bad", target.name + ".bad.2", target.name + ".bad.3"]
    assert [path.read_text(encoding="utf-8") for path in copies] == ["first", "second", "third"]


@pytest.mark.parametrize("settings", [{"exposure": 99}, {"exposure": 1, "futureSetting": 3}, {"contrast": "high"}])
def test_opening_and_reading_agree_about_a_recipe_with_a_setting_that_cannot_be_used(tmp_path, settings):
    """The two used to disagree: the editor was handed a recipe the rest of the app refused."""
    photo = tmp_path / "frame.NEF"
    write(photo, {"version": 1, "cameraRaw": settings})
    assert bridge.recipe_state(str(photo))[0] == bridge.RECIPE_INVALID
    assert bridge.load_recipe(str(photo)) is None
    assert bridge.take_quarantine_notice(str(photo)) is not None
    assert bridge.recipe_state(str(photo))[0] == bridge.RECIPE_ABSENT


def test_a_copy_set_aside_earlier_is_announced_once_even_across_a_restart(tmp_path):
    """A copy left by an earlier build (which then refused the photo for good), or set aside just before a crash."""
    photo = tmp_path / "frame.NEF"
    target = write(photo, "old damage")
    bad = target.with_name(target.name + ".bad")
    target.rename(bad)
    assert bridge.recipe_state(str(photo))[0] == bridge.RECIPE_INVALID
    assert bridge.recipe_state(str(photo), set_aside=True)[0] == bridge.RECIPE_ABSENT      # opening for editing is never refused
    notice = bridge.take_quarantine_notice(str(photo))
    assert notice.bad_path == bad and bad.name in notice.message
    bridge._NOTED_THIS_RUN.clear()                                                         # a new run of the app
    assert bridge.take_quarantine_notice(str(photo)) is None
    assert bridge.recipe_state(str(photo))[0] == bridge.RECIPE_ABSENT
    write(photo, "new damage")                                                             # damaged again: a new copy, a new notice
    assert bridge.load_recipe(str(photo)) is None
    assert bridge.recipe_state(str(photo))[0] == bridge.RECIPE_INVALID
    assert bridge.take_quarantine_notice(str(photo)).bad_path == target.with_name(target.name + ".bad.2")
    assert bad.read_text(encoding="utf-8") == "old damage"


def test_recovery_keeps_the_good_settings_and_leaves_out_the_rest(tmp_path):
    photo = tmp_path / "frame.NEF"
    damaged = {"version": 1, "cameraRaw": {"exposure": 99, "contrast": 20, "futureSetting": 3, "tint": "green", "hslHue": [0] * 8,
                                           "shadows": float("nan")}}
    target = write(photo, json.dumps(damaged))
    assert bridge.load_recipe(str(photo)) is None
    bad = target.with_name(target.name + ".bad")
    before = bad.read_bytes()

    result = bridge.recover_quarantined_recipe(str(photo))
    assert result.recovered and result.bad_path == bad
    assert result.recipe == {"version": 1, "cameraRaw": {"contrast": 20, "hslHue": [0] * 8}}
    assert {name for name, _why in result.dropped} == {"exposure", "futureSetting", "tint", "shadows"}
    assert "Left out" in result.message and "futureSetting" in result.message
    assert bridge.recipe_state(str(photo))[:2] == (bridge.RECIPE_EDITED, result.recipe)
    assert bridge.load_recipe(str(photo)) == result.recipe
    assert bad.read_bytes() == before                                           # the copy is never removed
    assert [path.name for path in target.parent.iterdir() if path.name.endswith(".tmp")] == []


def test_recovery_reads_a_recipe_with_something_written_after_it(tmp_path):
    photo = tmp_path / "frame.NEF"
    write(photo, '{"version": 1, "cameraRaw": {"exposure": 0.5}}{"version": 1, "camer')
    assert bridge.load_recipe(str(photo)) is None
    result = bridge.recover_quarantined_recipe(str(photo))
    assert result.recovered and result.recipe["cameraRaw"] == {"exposure": 0.5}


@pytest.mark.parametrize("content", ["not json at all", "[1, 2]", '{"version": 1, "cameraRaw": {"exposure": 99}}', '{"version": 1}'])
def test_recovery_of_a_copy_with_nothing_usable_changes_nothing(tmp_path, content):
    photo = tmp_path / "frame.NEF"
    target = write(photo, content)
    bridge.load_recipe(str(photo))
    result = bridge.recover_quarantined_recipe(str(photo))
    assert not result.recovered and result.recipe is None and result.message
    assert not target.exists() and target.with_name(target.name + ".bad").read_text(encoding="utf-8") == content


def test_recovery_never_replaces_an_edit_made_since(tmp_path):
    photo = tmp_path / "frame.NEF"
    write(photo, {"version": 1, "cameraRaw": {"exposure": 1, "futureSetting": 3}})
    bridge.load_recipe(str(photo))
    newer = {"version": 1, "cameraRaw": {"contrast": 40}}
    target = write(photo, newer)
    result = bridge.recover_quarantined_recipe(str(photo))
    assert not result.recovered and result.recipe == {"version": 1, "cameraRaw": {"exposure": 1}}
    assert json.loads(target.read_text(encoding="utf-8")) == newer and "edited again" in result.message
    # A recipe that changes nothing (the editor writes one when the photo is left untouched) holds no edit to protect.
    write(photo, {"version": 1, "cameraRaw": {}})
    assert bridge.recover_quarantined_recipe(str(photo)).recovered
    assert bridge.load_recipe(str(photo)) == {"version": 1, "cameraRaw": {"exposure": 1}}


def test_recovery_without_writing_only_reports(tmp_path):
    photo = tmp_path / "frame.NEF"
    target = write(photo, {"version": 1, "cameraRaw": {"exposure": 1, "futureSetting": 3}})
    bridge.load_recipe(str(photo))
    result = bridge.recover_quarantined_recipe(str(photo), write=False)
    assert not result.recovered and result.recipe == {"version": 1, "cameraRaw": {"exposure": 1}} and not target.exists()


def test_a_photo_with_nothing_set_aside_has_nothing_to_recover(tmp_path):
    result = bridge.recover_quarantined_recipe(str(tmp_path / "frame.NEF"))
    assert not result.recovered and result.bad_path is None


def test_a_newer_recipe_is_never_set_aside_or_salvaged(tmp_path):
    photo = tmp_path / "frame.NEF"
    target = write(photo, {"version": bridge.RECIPE_VERSION + 1, "cameraRaw": {"exposure": 1}})
    assert bridge.recipe_state(str(photo), set_aside=True)[0] == bridge.RECIPE_INVALID
    assert target.exists() and bridge.quarantined_recipes(str(photo)) == []
    assert bridge.salvage_recipe(json.loads(target.read_text(encoding="utf-8")))[0] is None


# --- the hosted editor --------------------------------------------------------------------------------------------------

def test_the_editor_opens_a_raw_with_an_unreadable_recipe_from_scratch_and_says_so_once(qapp, folder):  # noqa: F811
    path = str(folder / "raw.nef")
    target = write(path, "not json")
    host, _mover, _launched = make(qapp, folder)
    notices, failures = [], []
    host.recipeSetAside.connect(lambda photo, message: notices.append((photo, message)))
    host.failed.connect(failures.append)
    host.openPath(path)
    assert wait(qapp, lambda: host.state == EDITING and bool(notices))
    assert "recipe" not in host._proc.control.raw_specs[0]                      # the original develop
    assert notices[0][0] == path and (target.name + ".bad") in notices[0][1] and not failures
    host.close()
    assert wait(qapp, lambda: host.state == "idle")
    host.openPath(path)
    assert wait(qapp, lambda: host.state == EDITING)
    qapp.processEvents()
    assert len(notices) == 1


def test_recovering_the_open_photo_reopens_it_with_the_recovered_settings(qapp, folder):  # noqa: F811
    path = str(folder / "raw.nef")
    write(path, {"version": 1, "cameraRaw": {"exposure": 1, "futureSetting": 3}})
    host, _mover, launched = make(qapp, folder)
    outcomes = []
    host.recipeRecovery.connect(lambda photo, recovered, message: outcomes.append((photo, recovered, message)))
    host.openPath(path)
    assert wait(qapp, lambda: host.state == EDITING)
    host.recoverRecipe(path)
    assert wait(qapp, lambda: bool(outcomes) and host.state == EDITING)
    assert outcomes[0][:2] == (path, True) and "futureSetting" in outcomes[0][2]
    control = host._proc.control
    assert len(launched) == 1 and [spec.get("recipe") for spec in control.raw_specs] == [None, {"version": 1, "cameraRaw": {"exposure": 1}}]
    assert host.path == path


def test_a_recovery_that_finds_nothing_leaves_the_open_photo_as_it_is(qapp, folder):  # noqa: F811
    path = str(folder / "raw.nef")
    write(path, "not json")
    host, _mover, _launched = make(qapp, folder)
    outcomes, failures = [], []
    host.recipeRecovery.connect(lambda photo, recovered, message: outcomes.append((photo, recovered, message)))
    host.failed.connect(failures.append)
    host.openPath(path)
    assert wait(qapp, lambda: host.state == EDITING)
    host.recoverRecipe(path)
    assert wait(qapp, lambda: bool(outcomes) and host.state == EDITING)
    assert outcomes[0][1] is False and host.path == path and len(host._proc.control.opened) == 1 and not failures


def test_recovering_a_photo_that_is_not_open_only_puts_the_recipe_back(qapp, folder):  # noqa: F811
    path = str(folder / "raw.nef")
    write(path, {"version": 1, "cameraRaw": {"exposure": 1, "futureSetting": 3}})
    bridge.load_recipe(path)
    host, _mover, launched = make(qapp, folder)
    outcomes = []
    host.recipeRecovery.connect(lambda photo, recovered, message: outcomes.append((photo, recovered, message)))
    host.recoverRecipe(path)
    assert wait(qapp, lambda: bool(outcomes))
    assert outcomes[0][1] is True and not launched and host.state == "idle"
    assert bridge.load_recipe(path) == {"version": 1, "cameraRaw": {"exposure": 1}}
