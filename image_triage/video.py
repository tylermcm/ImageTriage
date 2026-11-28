"""Video clips beside the photos: their length, size and frame rate, and a frame at any moment, through Qt Multimedia (its FFmpeg
backend ships in PySide6 on Windows, macOS and Linux, so nothing else is installed).

``grab_frame`` and ``probe`` run on any thread that calls them (the thumbnail service's workers, the viewer's loaders): each makes its
own player and waits for it in a local event loop, so no thread is ever shared with a player made elsewhere. A frame comes back as a
``QImage`` the size of the video (or scaled to ``longest``), turned the way the clip is meant to be seen.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEventLoop, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QImage, QTransform

TIMEOUT_MS = 8000


@dataclass(slots=True, frozen=True)
class ClipInfo:
    duration_ms: int = 0
    width: int = 0              # as shown (a phone clip recorded on its side is taller than wide)
    height: int = 0
    fps: float = 0.0
    has_audio: bool = False
    codec: str = ""
    error: str = ""

    @property
    def frame_count(self) -> int:
        return round(self.duration_ms / 1000 * self.fps) if self.fps > 0 else 0


@dataclass(slots=True)
class Frame:
    image: QImage
    position_ms: int = -1       # where the frame shown starts (the frame that holds the moment asked for)
    info: ClipInfo = ClipInfo()


def format_duration(ms: int) -> str:
    """0:34, 12:05, 1:02:03: the length shown on a clip's thumbnail."""
    seconds = max(0, int(round(ms / 1000)))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def format_timecode(ms: int) -> str:
    """00:12.417: the playhead's time, to the millisecond."""
    ms = max(0, int(ms))
    minutes, rest = divmod(ms, 60000)
    return f"{minutes:02d}:{rest // 1000:02d}.{rest % 1000:03d}"


def _info(player) -> ClipInfo:
    from PySide6.QtMultimedia import QMediaMetaData

    data = player.metaData()
    size = data.value(QMediaMetaData.Key.Resolution)
    fps = data.value(QMediaMetaData.Key.VideoFrameRate)
    try:
        codec = data.value(QMediaMetaData.Key.VideoCodec)        # PySide cannot convert this on every version: then left out
    except RuntimeError:
        codec = ""
    width, height = (size.width(), size.height()) if isinstance(size, QSize) and size.isValid() else (0, 0)
    return ClipInfo(duration_ms=max(0, int(player.duration())), width=width, height=height,
                    fps=float(fps) if isinstance(fps, (int, float)) else 0.0, has_audio=bool(player.hasAudio()),
                    codec=str(getattr(codec, "name", "") or ""))


def _upright(image: QImage, rotation: int) -> QImage:
    if not rotation or image.isNull():
        return image
    return image.transformed(QTransform().rotate(rotation), Qt.TransformationMode.SmoothTransformation)


def grab_frame(path: str, position_ms: int = -1, longest: int = 0, timeout_ms: int = TIMEOUT_MS) -> Frame:
    """The frame at ``position_ms`` (-1: a tenth of the way in, the poster a thumbnail shows), at most ``longest`` pixels on its long
    side (0: as large as the video). An empty image, with the reason in ``info.error``, when the clip cannot be read."""
    from PySide6.QtMultimedia import QMediaPlayer, QVideoSink

    player = QMediaPlayer()
    sink = QVideoSink()
    player.setVideoSink(sink)
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    state: dict = {"wanted": position_ms}

    def frame_arrived(frame) -> None:
        if "image" in state or not frame.isValid() or not state.get("seeking_done"):
            return
        if state["wanted"] > 0 and frame.endTime() >= 0 and frame.endTime() / 1000 < state["wanted"] - 1:
            return                                    # still a frame before the moment asked for
        image = frame.toImage()
        if image.isNull():
            return
        state["image"], state["at"] = image, max(0, int(frame.startTime() / 1000))
        rotation = getattr(frame, "rotation", None)
        state["rotation"] = int(getattr(rotation() if callable(rotation) else rotation, "value", 0) or 0) if rotation is not None else 0
        loop.quit()

    def status_changed(status) -> None:
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia) and "info" not in state:
            state["info"] = _info(player)
            if state["wanted"] < 0:
                state["wanted"] = state["info"].duration_ms // 10
            state["wanted"] = min(state["wanted"], max(0, state["info"].duration_ms - 1))
            if state["wanted"] > 0:
                player.setPosition(state["wanted"])
            state["seeking_done"] = True
            player.play()                              # a frame is decoded only while it plays; it is stopped at the first one
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            state["error"] = player.errorString() or "The clip could not be read."
            loop.quit()

    sink.videoFrameChanged.connect(frame_arrived)
    player.mediaStatusChanged.connect(status_changed)
    player.errorOccurred.connect(lambda *_: (state.setdefault("error", player.errorString() or "The clip could not be read."), loop.quit()))
    player.setSource(QUrl.fromLocalFile(path))
    timer.start(timeout_ms)
    if "image" not in state and "error" not in state:
        loop.exec()
    timer.stop()
    player.stop()
    player.setSource(QUrl())
    info = state.get("info") or ClipInfo()
    if "image" not in state:
        return Frame(QImage(), -1, ClipInfo(error=state.get("error") or "The clip took too long to read."))
    image = _upright(state["image"], state.get("rotation", 0))
    if longest > 0 and max(image.width(), image.height()) > longest:
        image = image.scaled(QSize(longest, longest), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    if state.get("rotation", 0) in (90, 270) and info.width > info.height:
        info = ClipInfo(info.duration_ms, info.height, info.width, info.fps, info.has_audio, info.codec)
    return Frame(image.convertToFormat(QImage.Format.Format_RGB32), state["at"], info)


def probe(path: str, timeout_ms: int = TIMEOUT_MS) -> ClipInfo:
    """The clip's length, size and frame rate (read with its first frame, which settles how it is turned)."""
    frame = grab_frame(path, 0, longest=64, timeout_ms=timeout_ms)
    return frame.info


# ---- what is known of each clip, kept between runs ------------------------------------------------------------------------------------
import json as _json
import os as _os
import threading as _threading


class ClipInfoCache:
    """Each clip's length, size and frame rate, by path, file size and modified time: read once (a thumbnail grab or a probe) and kept
    in a small JSON file, so a folder shows its clips' lengths at once the next time."""

    LIMIT = 20000

    def __init__(self, path=None) -> None:
        self._path = path
        self._lock = _threading.Lock()
        self._data: dict | None = None
        self._dirty = False

    def _file(self):
        if self._path is not None:
            return self._path
        from .scan_cache import app_data_root

        return app_data_root() / "clip_info.json"

    @staticmethod
    def _key(path: str) -> str:
        try:
            info = _os.stat(path)
        except OSError:
            return ""
        return f"{_os.path.normcase(_os.path.abspath(path))}|{info.st_size}|{info.st_mtime_ns}"

    def _load(self) -> dict:
        if self._data is None:
            try:
                data = _json.loads(open(self._file(), encoding="utf-8").read())
            except (OSError, ValueError):
                data = {}
            self._data = data if isinstance(data, dict) else {}
        return self._data

    def get(self, path: str) -> ClipInfo | None:
        key = self._key(path)
        with self._lock:
            entry = self._load().get(key) if key else None
        if not isinstance(entry, dict):
            return None
        return ClipInfo(int(entry.get("d", 0)), int(entry.get("w", 0)), int(entry.get("h", 0)), float(entry.get("f", 0.0)), bool(entry.get("a")))

    def put(self, path: str, info: ClipInfo) -> None:
        key = self._key(path)
        if not key or info.error or info.duration_ms <= 0:
            return
        with self._lock:
            data = self._load()
            data[key] = {"d": info.duration_ms, "w": info.width, "h": info.height, "f": round(info.fps, 4), "a": info.has_audio}
            while len(data) > self.LIMIT:
                data.pop(next(iter(data)))
            self._dirty = True

    def save(self) -> None:
        with self._lock:
            if not self._dirty or self._data is None:
                return
            text = _json.dumps(self._data)
            self._dirty = False
        try:
            target = self._file()
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = str(target) + ".tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                handle.write(text)
            _os.replace(temporary, target)
        except OSError:
            pass


_CACHE: ClipInfoCache | None = None


def info_cache() -> ClipInfoCache:
    global _CACHE
    if _CACHE is None:
        _CACHE = ClipInfoCache()
    return _CACHE
