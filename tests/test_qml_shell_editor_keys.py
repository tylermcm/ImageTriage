from image_triage.qml_shell.editor_keys import SpacePress


def test_space_is_scoped_to_the_editor_and_ignores_modified_shortcuts():
    calls = []
    press = SpacePress(lambda: calls.append(True))
    assert not press.consume(0x20, True, False, False)
    assert not press.consume(0x20, True, True, True)
    assert not press.consume(0x41, True, True, False)
    assert not calls


def test_space_press_is_consumed_once_including_repeats_after_return_to_grid():
    calls = []
    press = SpacePress(lambda: calls.append(True))
    assert press.consume(0x20, True, True, False)
    assert press.consume(0x20, True, False, False)
    assert press.consume(0x20, False, False, False)
    assert len(calls) == 1
    assert not press.consume(0x20, True, False, False)
    assert press.consume(0x20, True, True, False)
    assert len(calls) == 2
