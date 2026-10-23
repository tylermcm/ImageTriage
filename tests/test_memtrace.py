"""The memory observer (image_triage.memtrace): off it is inert, on it counts honestly and changes nothing it watches."""
from __future__ import annotations

import ctypes
import gc
import json
import subprocess
import sys
import threading
import time
import weakref
from queue import SimpleQueue

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from image_triage import memtrace
from image_triage.cache import MemoryThumbnailCache, ThumbnailKey
from image_triage.perf import perf_logger


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def traced(monkeypatch):
    monkeypatch.setattr(memtrace, "ENABLED", True)
    memtrace.reset_for_tests()
    yield
    memtrace.reset_for_tests()


@pytest.fixture
def untraced(monkeypatch):
    monkeypatch.setattr(memtrace, "ENABLED", False)
    memtrace.reset_for_tests()
    yield
    memtrace.reset_for_tests()


def image(width=40, height=30):
    picture = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    picture.fill(QColor("teal"))
    return picture


def key(index):
    return ThumbnailKey(path=f"/p/{index}.jpg", modified_ns=1, file_size=1, width=40, height=30)


# --- off ---------------------------------------------------------------------------------------------------------------------


def test_off_registers_nothing_and_starts_no_thread(untraced):
    before = {thread.name for thread in threading.enumerate()}
    assert memtrace.cache("x") is None
    assert memtrace.jobs("x") is None
    assert memtrace.counter("x") is None
    assert memtrace.cache_with_gauge("x", "exact", object(), lambda o: (0, 0)) is None
    memtrace.gauge("x", object(), lambda o: 0)
    assert memtrace.start() is False
    memtrace.mark("anything")
    with memtrace.measure("nothing"):
        pass
    assert {thread.name for thread in threading.enumerate()} == before
    assert memtrace._caches == {} and memtrace._jobs == {} and memtrace._counters == {} and memtrace._gauges == {}


def test_off_caches_carry_no_trace(untraced):
    cache = MemoryThumbnailCache(max_bytes=10_000)
    assert cache._trace is None
    cache.put(key(1), image())
    assert cache.get(key(1)) is not None


def test_environment_variable_parsing():
    assert memtrace._truthy("1") and memtrace._truthy("yes") and memtrace._truthy("true")
    assert not memtrace._truthy("") and not memtrace._truthy("0") and not memtrace._truthy("off") and not memtrace._truthy(None)


# --- on: counters ------------------------------------------------------------------------------------------------------------


def test_thumbnail_cache_counts_hits_misses_peak_and_evictions(traced):
    one = image().sizeInBytes()
    cache = MemoryThumbnailCache(max_bytes=one * 2)
    cache.put(key(1), image())
    cache.put(key(2), image())
    assert cache.get(key(1)) is not None  # hit
    assert cache.get(key(9)) is None  # miss
    cache.put(key(3), image())  # over the limit: the oldest goes
    row = memtrace._caches["thumbnail.memory_cache"].snapshot()
    assert (row["hits"], row["misses"], row["evictions"], row["puts"]) == (1, 1, 1, 3)
    assert row["kind"] == "exact"
    assert row["bytes"] == one * 2 and row["entries"] == 2
    assert row["peak_bytes"] == one * 3, "the peak is the size before the trim"
    assert row["peak_entries"] == 3
    assert row["hit_rate"] == 0.5


def test_instrumentation_does_not_change_what_the_cache_keeps(monkeypatch):
    def run(enabled):
        monkeypatch.setattr(memtrace, "ENABLED", enabled)
        memtrace.reset_for_tests()
        cache = MemoryThumbnailCache(max_bytes=image().sizeInBytes() * 3)
        for index in range(10):
            cache.put(key(index % 7), image())
            cache.get(key((index * 3) % 7))
        return list(cache._entries), cache._current_bytes

    assert run(False) == run(True)
    memtrace.reset_for_tests()


def test_gauge_holds_the_cache_weakly(traced):
    cache = MemoryThumbnailCache(max_bytes=1_000_000)
    cache.put(key(1), image())
    reference = weakref.ref(cache)
    stats = memtrace._caches["thumbnail.memory_cache"]
    before = stats.snapshot()["bytes"]
    del cache
    gc.collect()
    assert reference() is None, "the observer must not keep a cache (and its images) alive"
    assert stats.snapshot()["bytes"] == before, "the last figures stay in the report"


def test_second_instance_gets_its_own_name(traced):
    first = MemoryThumbnailCache()
    second = MemoryThumbnailCache()
    assert {"thumbnail.memory_cache", "thumbnail.memory_cache#2"} <= set(memtrace._caches)
    del first, second


def test_invalidations_are_not_evictions(traced):
    stats = memtrace.cache("c")
    stats.evicted(2)
    stats.invalidated(3)
    row = stats.snapshot()
    assert row["evictions"] == 2 and row["invalidations"] == 3


def test_job_stats_track_queue_running_and_estimate(traced):
    jobs = memtrace.jobs("decode")
    assert memtrace.jobs("decode") is jobs
    jobs.queued()
    jobs.queued()
    jobs.started(1000)
    jobs.started(500)
    snapshot = jobs.snapshot()
    assert (snapshot["queued"], snapshot["running"], snapshot["est_bytes_in_flight"]) == (0, 2, 1500)
    assert snapshot["peak_queued"] == 2 and snapshot["peak_running"] == 2 and snapshot["peak_est_bytes_in_flight"] == 1500
    jobs.finished(1000)
    jobs.result(777)
    snapshot = jobs.snapshot()
    assert (snapshot["running"], snapshot["est_bytes_in_flight"], snapshot["finished"]) == (1, 500, 1)
    assert snapshot["peak_est_bytes_in_flight"] == 1500 and snapshot["max_result_bytes"] == 777


def test_preview_task_reports_its_decode(traced, qapp, tmp_path):
    from image_triage.preview import PreviewRequest, PreviewTask

    path = tmp_path / "a.jpg"
    image(80, 60).save(str(path), "JPEG")
    results = SimpleQueue()
    request = PreviewRequest(path=str(path), token=1, slot=0, target_size=QSize(80, 60), load_metadata=False)
    task = PreviewTask(request, results)
    jobs = memtrace._jobs["preview.decode"]
    assert jobs.snapshot()["queued"] == 1
    task.run()
    snapshot = jobs.snapshot()
    assert (snapshot["queued"], snapshot["running"], snapshot["started"], snapshot["finished"]) == (0, 0, 1, 1)
    assert snapshot["peak_est_bytes_in_flight"] == 80 * 60 * 4
    assert snapshot["max_result_bytes"] > 0
    assert results.get_nowait()[0] == "ready"


def test_metadata_only_preview_requests_are_not_decode_jobs(traced, qapp):
    from image_triage.preview import PreviewRequest, PreviewTask

    PreviewTask(PreviewRequest(path="x", token=1, slot=0, target_size=QSize(8, 8), load_image=False), SimpleQueue())
    assert "preview.decode" not in memtrace._jobs or memtrace._jobs["preview.decode"].snapshot()["queued"] == 0


def test_estimate_object_bytes_scales_with_the_collection():
    small = memtrace.estimate_object_bytes([("a" * 10, 1)] * 10)
    large = memtrace.estimate_object_bytes([("a" * 10, 1)] * 1000)
    assert large > small * 50
    assert memtrace.estimate_object_bytes([]) == 0


# --- on: the semantic matrix -------------------------------------------------------------------------------------------------


def test_semantic_index_registers_the_matrix_exactly_and_weakly(traced):
    import numpy as np

    from image_triage.library_semantic import LibraryVectorIndex

    matrix = np.ones((100, 512), dtype=np.float32)
    index = LibraryVectorIndex(paths=[f"/p/{n}.jpg" for n in range(100)], matrix=matrix)
    row = memtrace._caches["semantic.matrix"].snapshot()
    assert row["bytes"] == 100 * 512 * 4 and row["entries"] == 100 and row["kind"] == "exact"
    reference = weakref.ref(index)
    del index
    gc.collect()
    assert reference() is None


def test_measure_logs_the_span_with_a_sampled_peak(traced, tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_TRIAGE_LOG_DIR", str(tmp_path))
    logger = perf_logger()
    logger.set_enabled(True, reason="test")
    try:
        with memtrace.measure("test.span", extra=1):
            time.sleep(0.05)
        logger.flush()
        events = [json.loads(line) for line in logger.path.read_text().splitlines()]
    finally:
        logger.set_enabled(False, reason="test")
    spans = [event for event in events if event["event"] == "mem.span"]
    assert len(spans) == 1
    span = spans[0]
    assert span["label"] == "test.span" and span["extra"] == 1 and span["duration_ms"] >= 40
    if memtrace.process_memory() is not None:
        assert span["private_peak_sampled"] >= span["private_before"] > 0


# --- operating-system figures --------------------------------------------------------------------------------------------------


@pytest.mark.skipif(ctypes.sizeof(ctypes.c_size_t) != 8, reason="the documented Windows layouts are the 64-bit ones")
def test_windows_structures_have_the_documented_sizes():
    assert ctypes.sizeof(memtrace._ProcessMemoryCountersEx) == 80  # PROCESS_MEMORY_COUNTERS_EX
    assert ctypes.sizeof(memtrace._ProcessEntry32W) == 568  # PROCESSENTRY32W
    assert memtrace._ProcessMemoryCountersEx.PrivateUsage.offset == 72
    assert memtrace._ProcessEntry32W.th32ParentProcessID.offset == 32
    assert memtrace._ProcessEntry32W.szExeFile.offset == 44


def test_descendants_walk_the_whole_tree():
    processes = [
        memtrace.ProcessInfo(1, 0, "init"),
        memtrace.ProcessInfo(10, 1, "image_triage.exe"),
        memtrace.ProcessInfo(11, 10, "photocraft.exe"),
        memtrace.ProcessInfo(12, 10, "python.exe"),
        memtrace.ProcessInfo(13, 12, "conhost.exe"),
        memtrace.ProcessInfo(99, 1, "unrelated.exe"),
    ]
    assert {info.pid for info in memtrace.descendants(10, processes)} == {11, 12, 13}


def test_process_roles():
    assert memtrace.classify_process("PhotoCraft.exe") == "photocraft"
    assert memtrace.classify_process("python.exe") == "ai"
    assert memtrace.classify_process("ai_python_runner.exe") == "ai"
    assert memtrace.classify_process("conhost.exe") == "other"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the Linux reader is what can be exercised here")
def test_linux_figures_are_plausible_and_include_children():
    own = memtrace.process_memory()
    assert own is not None and own.private_bytes > 0 and own.working_set >= own.private_bytes * 0.5
    assert own.peak_working_set >= own.working_set * 0.9
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        deadline = time.time() + 5
        tree = memtrace.tree_memory()
        while time.time() < deadline and tree["roles"].get("ai", {}).get("n", 0) < 1:
            time.sleep(0.05)
            tree = memtrace.tree_memory()
        assert tree["roles"]["ai"]["n"] >= 1, "a python child counts as an AI worker"
        assert tree["total_private"] == tree["own"]["private"] + sum(r["private"] for r in tree["roles"].values())
    finally:
        child.kill()
        child.wait()


# --- the sampler -----------------------------------------------------------------------------------------------------------------


def test_sampler_writes_samples_into_the_performance_log(traced, qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_TRIAGE_LOG_DIR", str(tmp_path))
    monkeypatch.setenv(memtrace.INTERVAL_ENV_VAR, "0.1")
    cache = MemoryThumbnailCache(max_bytes=1_000_000)
    cache.put(key(1), image())
    logger = perf_logger()
    try:
        assert memtrace.start(qapp) is True
        memtrace.mark("phase:test", detail="x")
        time.sleep(0.45)
        qapp.processEvents()
        memtrace.stop()
        logger.flush()
        events = [json.loads(line) for line in logger.path.read_text().splitlines()]
    finally:
        memtrace.stop()
        logger.set_enabled(False, reason="test")
    names = [event["event"] for event in events]
    assert names[0] == "perf.enabled" or "mem.start" in names
    assert names.count("mem.sample") >= 3 and "mem.final" in names and "mem.mark" in names
    sample = next(event for event in events if event["event"] == "mem.sample" and "thumbnail.memory_cache" in event.get("caches", {}))
    assert sample["caches"]["thumbnail.memory_cache"]["bytes"] == image().sizeInBytes()
    assert "tree" in sample and "own" in sample["tree"] and "ui_lag_ms_max" in sample
    assert next(event for event in events if event["event"] == "mem.mark")["label"] == "phase:test"
    assert not [thread for thread in threading.enumerate() if thread.name == "memtrace-sampler"], "stop() ends the sampler"


def test_preview_cache_reports_distinct_pixel_buffers(traced, qapp):
    from image_triage.preview import FullScreenPreview

    pane = FullScreenPreview()
    try:
        shared = image(200, 100)
        pane._cache_preview_image(("a", None, 1), shared)
        pane._cache_preview_image(("a", (1, 1), 1), shared)  # the same pixels under a second key
        pane._cache_preview_image(("b", None, 1), image(200, 100))
        row = memtrace._caches["preview.cache"].snapshot()
        one = shared.sizeInBytes()
        assert row["bytes"] == 3 * one, "the cache charges every key"
        assert row["unique_bytes"] == 2 * one and row["unique_images"] == 2, "...but only two pixel buffers exist"
        assert row["peak_unique_bytes"] == 2 * one
    finally:
        pane.close()
