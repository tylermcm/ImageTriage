"""One running app per person: a second launch hands what it was asked to open to the first and exits.

Every launch from the file manager used to be another app, each writing the same settings and decision store. The first app listens on a
local socket (a named pipe on Windows) that only the same user can open; a later launch connects, sends one line of JSON
(`{"folder": ..., "select": ...}`, either may be empty), waits for the answer and exits. A first app that does not answer (it is hung, or
the socket is left over from a crash) does not stop the new one from starting.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_ACK = b"ok\n"
_LIMIT = 64 * 1024                       # a message is a folder and a file path: anything longer is not one of ours


def server_name() -> str:
    """The socket's name: the same for every launch by one user, different between users of one machine."""
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return "ImageTriage-" + hashlib.sha1(user.encode("utf-8", "replace")).hexdigest()[:12]


def _allow_foreground() -> None:
    """Windows only lets the process the person just started bring a window to the front; this passes that right to the running app."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.AllowSetForegroundWindow(-1)  # type: ignore[attr-defined]  # ASFW_ANY
    except Exception:
        pass


def send_to_running(message: dict, name: str | None = None, timeout_ms: int = 2000) -> bool:
    """Hand `message` to the app that is already running. True when it took it; False when there is none (or it does not answer)."""
    socket = QLocalSocket()
    try:
        socket.connectToServer(name or server_name())
        if not socket.waitForConnected(min(500, timeout_ms)):
            return False
        _allow_foreground()
        socket.write(json.dumps(message).encode("utf-8") + b"\n")
        if not socket.waitForBytesWritten(timeout_ms):
            return False
        answer = b""
        while _ACK not in answer:
            if not socket.waitForReadyRead(timeout_ms):
                return False
            answer += bytes(socket.readAll().data())
        return True
    finally:
        socket.abort()


class InstanceServer(QObject):
    """The running app's end: `received` carries each later launch's message."""

    received = Signal(dict)

    def __init__(self, name: str | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._name = name or server_name()
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._accept)
        self._open: list[dict] = []                     # the connections being read: {"socket", "data"}

    def listen(self) -> bool:
        if self._server.listen(self._name):
            return True
        QLocalServer.removeServer(self._name)          # the socket file of an app that crashed (nobody answered on it, or we would not be here)
        return self._server.listen(self._name)

    def close(self) -> None:
        self._server.close()

    def _accept(self) -> None:
        while self._server.hasPendingConnections():
            link = {"socket": self._server.nextPendingConnection(), "data": b""}
            self._open.append(link)
            link["socket"].readyRead.connect(lambda link=link: self._read(link))
            link["socket"].disconnected.connect(lambda link=link: self._done(link))
            self._read(link)

    def _done(self, link: dict) -> None:
        if link in self._open:
            self._open.remove(link)
            link["socket"].deleteLater()

    def _read(self, link: dict) -> None:
        if link not in self._open:
            return
        socket = link["socket"]
        link["data"] += bytes(socket.readAll().data())
        if b"\n" not in link["data"]:
            if len(link["data"]) > _LIMIT:
                socket.abort()
            return
        try:
            message = json.loads(link["data"].split(b"\n", 1)[0].decode("utf-8"))
        except ValueError:
            message = None
        link["data"] = b""
        if not isinstance(message, dict):
            socket.abort()
            return
        socket.write(_ACK)
        socket.flush()
        self.received.emit(message)
