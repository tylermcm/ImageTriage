"""The control port is chosen by the host before the editor binds it, so another program can take it in between. The host
cannot close that window; these tests pin what it does when it happens: nothing beyond the sign-in goes to a listener that
is not the editor, the launch fails closed and starts again on a new port with a new token, a bounded number of times.

The listeners here are stand-ins on the loopback interface; the real editor is not started."""
from __future__ import annotations

import json
import socket
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from image_triage import photocraft_bridge as bridge


class Listener:
    """A TCP listener that records every request line it is sent. ``kind``: "editor" answers like the control server,
    "garbage" answers with something that is not a reply, "silent" never answers, "impostor" says yes to everything."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.requests: list[dict] = []
        self.raw = b""
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.port = self.listener.getsockname()[1]
        self.clients: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                client, _ = self.listener.accept()
            except OSError:
                return
            self.clients.append(client)
            threading.Thread(target=self._serve, args=(client,), daemon=True).start()

    def _serve(self, client: socket.socket) -> None:
        stream = client.makefile("rwb")
        try:
            for line in stream:
                self.raw += line
                request = json.loads(line)
                self.requests.append(request)
                if self.kind == "silent":
                    continue
                if self.kind == "garbage":
                    stream.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                else:
                    stream.write((json.dumps({"id": request["id"], "ok": True, "result": {}}) + "\n").encode())
                stream.flush()
        except (OSError, ValueError):
            pass

    @property
    def methods(self) -> list[str]:
        return [request["method"] for request in self.requests]

    @property
    def tokens(self) -> list[str]:
        return [request["params"]["token"] for request in self.requests if request["method"] == "auth"]

    def stop(self) -> None:
        try:
            self.listener.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.listener.close()
        for client in self.clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            client.close()


@pytest.fixture
def listeners():
    made: list[Listener] = []

    def make(kind: str) -> Listener:
        made.append(Listener(kind))
        return made[-1]

    yield make
    for listener in made:
        listener.stop()


def running(pid=4242):
    return SimpleNamespace(pid=pid, poll=lambda: None)


# --- one connection attempt ----------------------------------------------------------------------------------------------

def test_a_listener_that_is_not_the_editor_gets_the_sign_in_and_nothing_else(listeners):
    foreign = listeners("garbage")
    with pytest.raises(bridge.PhotoCraftPortTaken):
        bridge._connect_with_retry(foreign.port, "t" * 64, running(), timeout=5.0)
    assert foreign.methods == ["auth"]                     # one sign-in, never repeated, and no command after it


def test_a_listener_that_never_answers_is_not_given_the_token_for_the_whole_deadline(listeners, monkeypatch):
    foreign = listeners("silent")
    real = bridge.PhotoCraftControl
    monkeypatch.setattr(bridge, "PhotoCraftControl", lambda port, token, **kw: real(port, token, **{**kw, "timeout": .05}))
    with pytest.raises(bridge.PhotoCraftPortTaken):
        bridge._connect_with_retry(foreign.port, "t" * 64, running(), timeout=30.0)
    assert foreign.methods == ["auth"] * bridge.SILENT_SIGN_INS_ALLOWED


def test_a_port_known_to_belong_to_another_process_is_sent_nothing_at_all(listeners, monkeypatch):
    foreign = listeners("impostor")
    monkeypatch.setattr(bridge, "_listener_pids", lambda port: {999})     # what Windows reports: someone else's listener
    with pytest.raises(bridge.PhotoCraftPortTaken):
        bridge._connect_with_retry(foreign.port, "t" * 64, running(pid=4242), timeout=5.0)
    assert foreign.raw == b"" and foreign.clients == []


def test_a_port_reported_as_the_editors_own_is_used(listeners, monkeypatch):
    editor = listeners("editor")
    monkeypatch.setattr(bridge, "_listener_pids", lambda port: {4242})
    control = bridge._connect_with_retry(editor.port, "t" * 64, running(pid=4242), timeout=5.0)
    control.close()
    assert editor.methods == ["auth"]


def test_a_sign_in_accepted_after_the_editor_has_exited_is_not_trusted(listeners):
    """The editor could not bind and exited, and something else on the port says yes to the sign-in."""
    impostor = listeners("impostor")
    polls = iter([None])                                   # alive when the connection is made, gone by the time it answered
    process = SimpleNamespace(pid=4242, poll=lambda: next(polls, 3))
    with pytest.raises(bridge.PhotoCraftPortTaken):
        bridge._connect_with_retry(impostor.port, "t" * 64, process, timeout=5.0)
    assert impostor.methods == ["auth"]


def test_an_editor_that_has_exited_is_not_connected_to(listeners):
    impostor = listeners("impostor")
    with pytest.raises(bridge.PhotoCraftExitedEarly):
        bridge._connect_with_retry(impostor.port, "t" * 64, SimpleNamespace(pid=1, poll=lambda: 3), timeout=5.0)
    assert impostor.raw == b""


def test_later_connections_do_not_send_the_token_once_the_editor_has_gone(listeners, tmp_path):
    impostor = listeners("impostor")
    process = Mock()
    process.pid = 4242
    process.poll.return_value = 1
    proc = bridge.PhotoCraftProcess(process=process, control=Mock(), hwnd=1, token_file=tmp_path / "t", read_root=tmp_path,
                                    port=impostor.port, token="t" * 64)
    with pytest.raises(bridge.PhotoCraftError):
        proc.fast_lane()
    with pytest.raises(bridge.PhotoCraftError):
        proc.render_lane()
    assert impostor.raw == b""


def test_a_quiet_connection_is_not_reopened_towards_another_process(listeners, monkeypatch):
    editor = listeners("editor")
    owners = {4242}
    monkeypatch.setattr(bridge, "_listener_pids", lambda port: set(owners))
    control = bridge._connect_with_retry(editor.port, "t" * 64, running(pid=4242), timeout=5.0)
    try:
        owners.clear()
        owners.add(999)                                    # the editor died and something else now listens there
        control._last_used -= bridge.IDLE_RECONNECT_S + 1
        with pytest.raises(bridge.PhotoCraftError):
            control.call("jobs.list")
        assert editor.methods == ["auth"]
    finally:
        try:
            control.close()
        except OSError:
            pass


# --- the launch ----------------------------------------------------------------------------------------------------------

class FakeProcess:
    """Stands in for the editor process: the launch only starts it, asks whether it runs, and ends it."""

    made: list["FakeProcess"] = []

    def __init__(self, args, **_kwargs) -> None:
        self.args, self.pid, self.ended = args, 4242 + len(FakeProcess.made), False
        FakeProcess.made.append(self)

    def poll(self):
        return 0 if self.ended else None

    def terminate(self) -> None:
        self.ended = True

    def wait(self, timeout=None):
        return 0

    @property
    def port(self) -> int:
        return int(self.args[self.args.index("--control") + 1])

    @property
    def token(self) -> str:
        with open(self.args[self.args.index("--control-token-file") + 1], encoding="utf-8") as handle:
            return handle.read()


@pytest.fixture
def launch(tmp_path, monkeypatch):
    FakeProcess.made = []
    monkeypatch.setattr(bridge.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(bridge, "_open_editor_log", lambda: (bridge.subprocess.DEVNULL, None))
    monkeypatch.setattr(bridge, "_find_window_for_pid", lambda pid: 99)
    monkeypatch.setattr(bridge.PhotoCraftControl, "require_hosted_handoff", lambda self: None)
    monkeypatch.setattr(bridge.tempfile, "tempdir", str(tmp_path))
    if hasattr(bridge.ctypes, "windll"):
        monkeypatch.setattr(bridge.ctypes.windll.user32, "ShowWindow", Mock(), raising=False)
    else:
        monkeypatch.setattr(bridge.ctypes, "windll", Mock(), raising=False)

    def start(ports):
        ports = iter(ports)
        monkeypatch.setattr(bridge, "_free_tcp_port", lambda: next(ports))
        return bridge._launch_photocraft_binary(None, read_root=str(tmp_path), write_root=str(tmp_path), executable="photocraft.exe", idle=True)

    return start


def test_a_port_taken_by_another_program_makes_the_launch_start_again_on_a_new_port(launch, listeners, tmp_path):
    foreign, editor = listeners("garbage"), listeners("editor")
    proc = launch([foreign.port, editor.port])
    try:
        first, second = FakeProcess.made
        assert first.ended and not second.ended and proc.process is second and proc.port == editor.port
        assert foreign.methods == ["auth"]                                   # the sign-in, then nothing: no command reached it
        assert editor.tokens == [proc.token] and foreign.tokens != editor.tokens   # the token it saw is not used again
        assert sorted(path.name for path in tmp_path.glob("photocraft_token_*.txt")) == [proc.token_file.name]
        assert proc.launch_timings["control_port_attempts"] == 2
    finally:
        proc.control.close()
        proc.token_file.unlink(missing_ok=True)


def test_the_launch_gives_up_after_a_bounded_number_of_ports(launch, listeners, tmp_path):
    taken = [listeners("garbage") for _ in range(bridge.CONTROL_PORT_ATTEMPTS)]
    spare = listeners("editor")
    with pytest.raises(bridge.PhotoCraftPortTaken):
        launch([*(listener.port for listener in taken), spare.port])
    assert len(FakeProcess.made) == bridge.CONTROL_PORT_ATTEMPTS and all(process.ended for process in FakeProcess.made)
    assert [listener.methods for listener in taken] == [["auth"]] * bridge.CONTROL_PORT_ATTEMPTS and spare.requests == []
    assert list(tmp_path.glob("photocraft_token_*.txt")) == []


def test_an_editor_that_exits_before_its_channel_opens_is_started_again_on_another_port(launch, listeners, monkeypatch):
    """What an editor that could not bind its port looks like from the host."""
    editor = listeners("editor")
    unused = bridge._free_tcp_port()
    real = bridge._connect_with_retry

    def connect(port, token, process):
        if port == unused:
            process.ended = True
        return real(port, token, process)

    monkeypatch.setattr(bridge, "_connect_with_retry", connect)
    proc = launch([unused, editor.port])
    try:
        assert len(FakeProcess.made) == 2 and proc.port == editor.port
    finally:
        proc.control.close()
        proc.token_file.unlink(missing_ok=True)
