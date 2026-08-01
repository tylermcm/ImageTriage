"""Image loading helpers for model inference."""

from __future__ import annotations

import os
from pathlib import Path
import threading
import time
from typing import Any, BinaryIO, Callable, TypeVar

from PIL import Image


DEFAULT_INFERENCE_DECODE_SCALE = 3
DEFAULT_INFERENCE_LONG_EDGE_MULTIPLIER = 4
DEFAULT_INFERENCE_READ_BLOCK_BYTES = 1024 * 1024
MIN_INFERENCE_READ_BLOCK_BYTES = 64 * 1024
MAX_INFERENCE_READ_BLOCK_BYTES = 8 * 1024 * 1024
INFERENCE_READ_BLOCK_ENV_VAR = "AICULLING_IMAGE_READ_BLOCK_KB"
_T = TypeVar("_T")


def load_rgb_for_inference(
    path: str | Path,
    *,
    target_short_edge: int = 224,
    decode_scale: int = DEFAULT_INFERENCE_DECODE_SCALE,
    long_edge_multiplier: int = DEFAULT_INFERENCE_LONG_EDGE_MULTIPLIER,
    read_block_bytes: int | None = None,
    profile: dict[str, Any] | None = None,
) -> Image.Image:
    """Load an image as RGB after reducing oversized sources for model inference."""

    source_path = Path(path)
    target_short = max(1, int(target_short_edge or 224))
    decode_short = max(target_short, target_short * max(1, int(decode_scale)))
    max_long_edge = decode_short * max(1, int(long_edge_multiplier))
    decoder_read_block = resolve_inference_read_block_bytes(read_block_bytes)

    traced_file: _TracingFile | None = None
    if profile is None:
        source = Image.open(source_path)
    else:
        profile.clear()
        profile["worker_pid"] = os.getpid()
        profile["thread_id"] = threading.get_ident()
        try:
            profile["source_file_bytes"] = source_path.stat().st_size
        except OSError:
            profile["source_file_bytes"] = 0

        raw_file = _timed_call(profile, "os_open", lambda: source_path.open("rb"))
        traced_file = _TracingFile(raw_file)
        try:
            traced_file.phase = "image_open"
            source = _timed_call(profile, "image_open", lambda: Image.open(traced_file))
        except Exception:
            profile.update(traced_file.summary())
            traced_file.close()
            raise

    prepared = source
    try:
        if profile is not None:
            profile["image_format"] = str(source.format or "")
            profile["frame_count"] = int(getattr(source, "n_frames", 1) or 1)
            profile["source_width"], profile["source_height"] = source.size
            profile["decoder_read_block_bytes"] = decoder_read_block
            frame_offsets = getattr(source, "_MpoImageFile__mpoffsets", None)
            if isinstance(frame_offsets, (list, tuple)):
                profile["mpo_frame_offsets"] = [int(value) for value in frame_offsets]
                if traced_file is not None:
                    traced_file.frame_offsets = [int(value) for value in frame_offsets]

        _apply_decoder_read_block(source, decoder_read_block)
        if traced_file is not None:
            traced_file.phase = "draft"
        _timed_call(profile, "draft", lambda: _apply_decoder_draft(prepared, decode_short))
        if profile is not None:
            profile["draft_width"], profile["draft_height"] = prepared.size

        if traced_file is not None:
            traced_file.phase = "resize"
        prepared = _timed_call(
            profile,
            "resize",
            lambda: _resize_for_inference(
                prepared,
                decode_short=decode_short,
                max_long_edge=max_long_edge,
            ),
        )
        if traced_file is not None:
            traced_file.phase = "convert"
        rgb_image = _timed_call(profile, "convert", lambda: prepared.convert("RGB"))
        if rgb_image is prepared:
            rgb_image = prepared.copy()
        if traced_file is not None:
            traced_file.phase = "materialize"
        _timed_call(profile, "materialize", rgb_image.load)
        if profile is not None:
            profile["output_width"], profile["output_height"] = rgb_image.size
        return rgb_image
    finally:
        if prepared is not source:
            prepared.close()
        source.close()
        if traced_file is not None:
            profile.update(traced_file.summary())
            traced_file.close()


def resolve_inference_read_block_bytes(read_block_bytes: int | None = None) -> int:
    """Resolve the Pillow decoder block size used for inference image reads."""

    if read_block_bytes is None:
        raw_kb = (os.environ.get(INFERENCE_READ_BLOCK_ENV_VAR, "") or "").strip()
        if raw_kb:
            try:
                read_block_bytes = int(raw_kb) * 1024
            except ValueError:
                read_block_bytes = DEFAULT_INFERENCE_READ_BLOCK_BYTES
        else:
            read_block_bytes = DEFAULT_INFERENCE_READ_BLOCK_BYTES
    return max(
        MIN_INFERENCE_READ_BLOCK_BYTES,
        min(MAX_INFERENCE_READ_BLOCK_BYTES, int(read_block_bytes)),
    )


def _apply_decoder_read_block(image: Image.Image, read_block_bytes: int) -> None:
    if str(image.format or "").upper() not in {"JPEG", "MPO"}:
        return
    if hasattr(image, "decodermaxblock"):
        image.decodermaxblock = read_block_bytes


def _apply_decoder_draft(image: Image.Image, decode_short: int) -> None:
    try:
        image.draft("RGB", (decode_short, decode_short))
    except (AttributeError, OSError, ValueError):
        return


def _resize_for_inference(
    image: Image.Image,
    *,
    decode_short: int,
    max_long_edge: int,
) -> Image.Image:
    width, height = image.size
    if width <= 0 or height <= 0:
        return image

    short_edge = min(width, height)
    scale = 1.0
    if short_edge > decode_short:
        scale = min(scale, float(decode_short) / float(short_edge))
    if max(width, height) * scale > max_long_edge:
        scale = min(scale, float(max_long_edge) / float(max(width, height)))

    if scale >= 1.0:
        return image

    new_size = (
        max(1, int(round(width * scale))),
        max(1, int(round(height * scale))),
    )
    return image.resize(new_size, Image.Resampling.BOX)


def _timed_call(
    profile: dict[str, Any] | None,
    name: str,
    operation: Callable[[], _T],
) -> _T:
    if profile is None:
        return operation()
    wall_start = time.perf_counter_ns()
    cpu_start = time.thread_time_ns()
    try:
        return operation()
    finally:
        profile[f"{name}_wall_ms"] = (time.perf_counter_ns() - wall_start) / 1_000_000.0
        profile[f"{name}_thread_cpu_ms"] = (time.thread_time_ns() - cpu_start) / 1_000_000.0


class _TracingFile:
    """Transparent Pillow file wrapper that records logical reads and seeks."""

    def __init__(self, raw_file: BinaryIO) -> None:
        self._file = raw_file
        self._position = int(raw_file.tell())
        try:
            self.file_size = int(os.fstat(raw_file.fileno()).st_size)
        except OSError:
            self.file_size = 0
        self._ranges: list[tuple[int, int]] = []
        self._phase_stats: dict[str, dict[str, float]] = {}
        self.frame_offsets: list[int] = []
        self.phase = "unknown"
        self.read_calls = 0
        self.read_bytes = 0
        self.read_wall_ns = 0
        self.read_thread_cpu_ns = 0
        self.seek_calls = 0
        self.seek_wall_ns = 0
        self.seek_thread_cpu_ns = 0
        self.backward_seek_bytes = 0
        self.highest_offset = self._position

    def read(self, size: int = -1) -> bytes:
        return self._read_with("read", size)

    def read1(self, size: int = -1) -> bytes:
        method = getattr(self._file, "read1", self._file.read)
        return self._read_with("read1", size, method=method)

    def readinto(self, buffer: Any) -> int:
        return self._readinto_with("readinto", buffer)

    def readinto1(self, buffer: Any) -> int:
        method = getattr(self._file, "readinto1", self._file.readinto)
        return self._readinto_with("readinto1", buffer, method=method)

    def seek(self, offset: int, whence: int = 0) -> int:
        wall_start = time.perf_counter_ns()
        cpu_start = time.thread_time_ns()
        previous = self._position
        result = self._file.seek(offset, whence)
        wall_ns = time.perf_counter_ns() - wall_start
        cpu_ns = time.thread_time_ns() - cpu_start
        self._position = int(result)
        self.seek_calls += 1
        self.seek_wall_ns += wall_ns
        self.seek_thread_cpu_ns += cpu_ns
        if self._position < previous:
            self.backward_seek_bytes += previous - self._position
        phase = self._phase_bucket()
        phase["seek_calls"] += 1
        phase["seek_wall_ns"] += wall_ns
        phase["seek_thread_cpu_ns"] += cpu_ns
        return self._position

    def tell(self) -> int:
        return self._position

    def readable(self) -> bool:
        return self._file.readable()

    def seekable(self) -> bool:
        return self._file.seekable()

    def fileno(self) -> int:
        return self._file.fileno()

    @property
    def closed(self) -> bool:
        return self._file.closed

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()

    def __enter__(self) -> "_TracingFile":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._file, name)

    def summary(self) -> dict[str, Any]:
        unique_bytes = _range_union_length(self._ranges)
        result: dict[str, Any] = {
            "read_calls": self.read_calls,
            "logical_read_bytes": self.read_bytes,
            "unique_logical_read_bytes": unique_bytes,
            "repeated_logical_read_bytes": max(0, self.read_bytes - unique_bytes),
            "read_wall_ms": self.read_wall_ns / 1_000_000.0,
            "read_thread_cpu_ms": self.read_thread_cpu_ns / 1_000_000.0,
            "seek_calls": self.seek_calls,
            "seek_wall_ms": self.seek_wall_ns / 1_000_000.0,
            "seek_thread_cpu_ms": self.seek_thread_cpu_ns / 1_000_000.0,
            "backward_seek_bytes": self.backward_seek_bytes,
            "highest_logical_offset": self.highest_offset,
        }
        for phase_name, stats in self._phase_stats.items():
            prefix = f"phase_{phase_name}"
            result[f"{prefix}_read_calls"] = int(stats["read_calls"])
            result[f"{prefix}_read_bytes"] = int(stats["read_bytes"])
            result[f"{prefix}_read_wall_ms"] = stats["read_wall_ns"] / 1_000_000.0
            result[f"{prefix}_read_thread_cpu_ms"] = stats["read_thread_cpu_ns"] / 1_000_000.0
            result[f"{prefix}_seek_calls"] = int(stats["seek_calls"])
            result[f"{prefix}_seek_wall_ms"] = stats["seek_wall_ns"] / 1_000_000.0
            result[f"{prefix}_seek_thread_cpu_ms"] = stats["seek_thread_cpu_ns"] / 1_000_000.0
        if self.frame_offsets:
            boundaries = sorted(set(self.frame_offsets))
            for frame_index, frame_start in enumerate(boundaries):
                frame_end = (
                    boundaries[frame_index + 1]
                    if frame_index + 1 < len(boundaries)
                    else self.file_size
                )
                intersections = [
                    (max(start, frame_start), min(end, frame_end))
                    for start, end in self._ranges
                    if start < frame_end and end > frame_start
                ]
                result[f"frame_{frame_index}_unique_read_bytes"] = _range_union_length(intersections)
            result["reads_crossing_frame_boundaries"] = sum(
                1
                for start, end in self._ranges
                if any(start < boundary < end for boundary in boundaries[1:])
            )
        return result

    def _read_with(
        self,
        operation_name: str,
        size: int,
        *,
        method: Callable[[int], bytes] | None = None,
    ) -> bytes:
        del operation_name
        reader = method or self._file.read
        start_offset = self._position
        wall_start = time.perf_counter_ns()
        cpu_start = time.thread_time_ns()
        data = reader(size)
        wall_ns = time.perf_counter_ns() - wall_start
        cpu_ns = time.thread_time_ns() - cpu_start
        self._record_read(start_offset, len(data), wall_ns, cpu_ns)
        return data

    def _readinto_with(
        self,
        operation_name: str,
        buffer: Any,
        *,
        method: Callable[[Any], int | None] | None = None,
    ) -> int:
        del operation_name
        reader = method or self._file.readinto
        start_offset = self._position
        wall_start = time.perf_counter_ns()
        cpu_start = time.thread_time_ns()
        count = reader(buffer)
        wall_ns = time.perf_counter_ns() - wall_start
        cpu_ns = time.thread_time_ns() - cpu_start
        returned = 0 if count is None else int(count)
        self._record_read(start_offset, returned, wall_ns, cpu_ns)
        return returned

    def _record_read(self, start_offset: int, count: int, wall_ns: int, cpu_ns: int) -> None:
        end_offset = start_offset + max(0, count)
        self._position = end_offset
        self.read_calls += 1
        self.read_bytes += max(0, count)
        self.read_wall_ns += wall_ns
        self.read_thread_cpu_ns += cpu_ns
        self.highest_offset = max(self.highest_offset, end_offset)
        if count > 0:
            self._ranges.append((start_offset, end_offset))
        phase = self._phase_bucket()
        phase["read_calls"] += 1
        phase["read_bytes"] += max(0, count)
        phase["read_wall_ns"] += wall_ns
        phase["read_thread_cpu_ns"] += cpu_ns

    def _phase_bucket(self) -> dict[str, float]:
        safe_phase = "".join(character if character.isalnum() else "_" for character in self.phase).strip("_")
        key = safe_phase or "unknown"
        return self._phase_stats.setdefault(
            key,
            {
                "read_calls": 0,
                "read_bytes": 0,
                "read_wall_ns": 0,
                "read_thread_cpu_ns": 0,
                "seek_calls": 0,
                "seek_wall_ns": 0,
                "seek_thread_cpu_ns": 0,
            },
        )


def _range_union_length(ranges: list[tuple[int, int]]) -> int:
    if not ranges:
        return 0
    ordered = sorted(ranges)
    start, end = ordered[0]
    total = 0
    for next_start, next_end in ordered[1:]:
        if next_start <= end:
            end = max(end, next_end)
            continue
        total += end - start
        start, end = next_start, next_end
    return total + end - start
