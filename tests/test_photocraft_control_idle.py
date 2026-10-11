"""The editor's control server closes a connection that has been quiet for 30 s. An editor started in the background and left alone for
minutes therefore refused the first request that followed ("[WinError 10053] An established connection was aborted", shown as "PhotoCraft
could not open this photo"). The client now replaces a connection that has been quiet for a while before it sends anything on it."""
from __future__ import annotations

import json
import socket
import threading

import pytest

from image_triage import photocraft_bridge as bridge

TOKEN = "t" * 64


class FakeEditor:
    """A control server that, like the real one, can drop connections that have been idle (``drop_idle_connections``)."""

    def __init__(self) -> None:
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.port = self.listener.getsockname()[1]
        self.connections: list[socket.socket] = []
        self.requests: list[tuple[int, str]] = []  # (connection number, method)
        self._stop = False
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self) -> None:
        while not self._stop:
            try:
                client, _ = self.listener.accept()
            except OSError:
                return
            self.connections.append(client)
            threading.Thread(target=self._serve, args=(client, len(self.connections)), daemon=True).start()

    def _serve(self, client: socket.socket, number: int) -> None:
        reader = client.makefile("rwb")
        try:
            for line in reader:
                request = json.loads(line)
                method = request["method"]
                self.requests.append((number, method))
                if method == "auth":
                    ok = request["params"]["token"] == TOKEN
                    reply = {"id": request["id"], "ok": ok, "error": None if ok else "bad token"}
                else:
                    reply = {"id": request["id"], "ok": True, "result": {"connection": number}}
                reader.write((json.dumps(reply) + "\n").encode())
                reader.flush()
        except (OSError, ValueError):
            pass

    def drop_idle_connections(self) -> None:
        """What the editor does to a connection after 30 s of quiet."""
        for client in self.connections:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            client.close()

    def stop(self) -> None:
        self._stop = True
        self.listener.close()
        self.drop_idle_connections()


@pytest.fixture
def editor():
    server = FakeEditor()
    yield server
    server.stop()


def connect(editor: FakeEditor) -> bridge.PhotoCraftControl:
    return bridge.PhotoCraftControl(editor.port, TOKEN)


def grow_quiet(control: bridge.PhotoCraftControl, seconds: float) -> None:
    """Makes the connection look as if nothing had been sent on it for ``seconds`` more."""
    control._last_used -= seconds


def test_a_connection_the_editor_dropped_while_it_was_quiet_is_replaced_before_the_request(editor):
    control = connect(editor)
    assert control.call("jobs.list") == {"connection": 1}
    editor.drop_idle_connections()
    grow_quiet(control, 300)
    assert control.call("jobs.list") == {"connection": 2}
    assert [method for number, method in editor.requests if number == 2] == ["auth", "jobs.list"], "the new connection signed in first"
    control.close()


def test_a_connection_that_is_in_use_is_kept(editor):
    control = connect(editor)
    for _ in range(5):
        control.call("jobs.list")
    assert len(editor.connections) == 1


def test_a_short_quiet_spell_does_not_replace_the_connection(editor):
    control = connect(editor)
    control.call("jobs.list")
    grow_quiet(control, bridge.IDLE_RECONNECT_S - 5)
    control.call("jobs.list")
    assert len(editor.connections) == 1


def test_the_connection_is_replaced_once_and_the_new_one_is_then_used(editor):
    control = connect(editor)
    editor.drop_idle_connections()
    grow_quiet(control, 300)
    control.call("jobs.list")
    control.call("jobs.list")
    control.call("jobs.list")
    assert len(editor.connections) == 2


def test_a_quiet_connection_the_editor_still_has_open_is_replaced_too(editor):
    control = connect(editor)
    grow_quiet(control, 300)  # quiet for a long time, and the editor has not (yet) dropped it
    assert control.call("jobs.list") == {"connection": 2}


def test_every_connection_replaces_itself_independently(editor):
    """The editor keeps a second connection for cancelling opens (``PhotoCraftProcess.fast_lane``): it goes quiet too."""
    main, fast = connect(editor), connect(editor)
    main.call("jobs.list")
    fast.call("jobs.list")
    editor.drop_idle_connections()
    grow_quiet(fast, 300)
    assert fast.call("jobs.list") == {"connection": 3}
    grow_quiet(main, 300)
    assert main.call("jobs.list") == {"connection": 4}


def test_an_editor_that_has_gone_is_reported_as_an_error_and_not_as_a_hang(editor):
    control = connect(editor)
    editor.stop()
    grow_quiet(control, 300)
    with pytest.raises(bridge.PhotoCraftError):
        control.call("jobs.list")


def test_signing_in_again_with_a_changed_token_is_refused_plainly(editor, monkeypatch):
    control = connect(editor)
    control._token = "x" * 64
    grow_quiet(control, 300)
    with pytest.raises(bridge.PhotoCraftError, match="auth failed"):
        control.call("jobs.list")


def test_closing_the_editor_asks_it_to_quit_on_a_fresh_connection_when_the_old_one_was_dropped(editor):
    control = connect(editor)
    control.call("jobs.list")
    editor.drop_idle_connections()
    grow_quiet(control, 300)
    control.quit()
    assert (2, "app.quit") in editor.requests
    assert control._sock.gettimeout() == 2.0, "the short limit still applies, so a hung editor cannot hold up closing"


def test_quitting_an_editor_that_is_not_there_returns(editor):
    control = connect(editor)
    editor.stop()
    grow_quiet(control, 300)
    control.quit()  # no error, no wait


def test_a_connection_made_without_the_idle_bookkeeping_still_works():
    """Tests (and nothing else) build a control without opening a socket: nothing to replace."""
    control = object.__new__(bridge.PhotoCraftControl)
    control._next_id = 1
    control._file = type("File", (), {"write": lambda self, data: None, "flush": lambda self: None,
                                       "readline": lambda self: b'{"id": 1, "ok": true, "result": {}}\n'})()
    assert control.call("jobs.list") == {}
