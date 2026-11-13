"""An interrupted model download continues instead of starting again (audit A12).

No network: `urllib.request.urlopen` is replaced by a stand-in model host that honours `Range` and `If-Range` the way a real
one does (206, 200 when it ignores the range or the file changed, 416 past the end) and can drop the connection part way."""
from __future__ import annotations

import hashlib
import json
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from image_triage import ai_model_store
from image_triage.ai_model_store import ModelInstallCancelled, ModelInstallError, _ResumePlan
from test_ai_model_store import CONTENT, PAYLOAD, TEST_BUNDLE, _StoreTestCase

URL = "https://models.invalid/acme/resolve/abc/weights.bin?download=true"
BODY = bytes(range(256)) * 40                      # 10,240 bytes, every chunk different from its neighbours
SHA = hashlib.sha256(BODY).hexdigest()
CHUNK = 1000


class Dropped(ConnectionResetError):
    pass


class Response:
    def __init__(self, status, headers, body, drop_after, host):
        self.status, self.headers, self._body, self._drop_after, self._sent, self._host = status, headers, body, drop_after, 0, host

    def read(self, size):
        if self._drop_after is not None and self._sent >= self._drop_after:
            raise Dropped("connection reset by peer")
        end = self._sent + size
        if self._drop_after is not None:
            end = min(end, self._drop_after)
        chunk = self._body[self._sent:end]
        self._sent += len(chunk)
        self._host.sent += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Host:
    """One file on a model host. `drops` lists, per request, after how many body bytes the connection is lost (None: never)."""

    def __init__(self, body=BODY, etag='"v1"', last_modified=""):
        self.body, self.etag, self.last_modified = body, etag, last_modified
        self.requests: list[dict] = []
        self.drops: list[int | None] = []
        self.ignore_ranges = False
        self.misalign = 0                           # a broken proxy: the 206 starts this many bytes away from what was asked
        self.sent = 0                               # body bytes the client really read, over every request

    def urlopen(self, request, timeout=None):
        asked, condition = request.get_header("Range"), request.get_header("If-range")
        self.requests.append({"range": asked, "if_range": condition})
        drop = self.drops.pop(0) if self.drops else None
        headers = {"Content-Type": "application/octet-stream"}
        if self.etag:
            headers["ETag"] = self.etag
        if self.last_modified:
            headers["Last-Modified"] = self.last_modified
        same_file = condition is None or condition in (self.etag, self.last_modified)
        if asked and not self.ignore_ranges and same_file:
            start = int(asked[len("bytes="):-1])
            if start >= len(self.body):
                raise urllib.error.HTTPError(request.full_url, 416, "Range Not Satisfiable", {"Content-Range": f"bytes */{len(self.body)}"}, None)
            start += self.misalign
            body = self.body[start:]
            headers.update({"Content-Length": str(len(body)), "Content-Range": f"bytes {start}-{len(self.body) - 1}/{len(self.body)}"})
            status = 206
        else:
            body, status = self.body, 200
            headers["Content-Length"] = str(len(body))
        return Response(status, headers, body, drop, self)


@pytest.fixture
def host(monkeypatch):
    server = Host()
    monkeypatch.setattr("urllib.request.urlopen", server.urlopen)
    monkeypatch.setattr(ai_model_store, "DOWNLOAD_CHUNK_SIZE", CHUNK)
    monkeypatch.setattr(ai_model_store, "DOWNLOAD_BACKOFF_SECONDS", 0)
    return server


@pytest.fixture
def plan(tmp_path):
    return _ResumePlan(partial=tmp_path / "kept" / "weights.bin.part", size=len(BODY), sha256=SHA)


def download(tmp_path, plan, **more):
    target = tmp_path / "generation" / "weights.bin"
    written = ai_model_store._download_file(url=URL, destination=target, label="weights.bin", progress_callback=more.get("progress"),
                                            cancel_check=more.get("cancel"), resume=plan)
    return target, written


def keep(plan, body, *, url=URL, etag='"v1"', last_modified="", size=None, sha256=None):
    """A partial left by an earlier attempt, with the sidecar that attempt would have written."""
    plan.partial.parent.mkdir(parents=True, exist_ok=True)
    plan.partial.write_bytes(body)
    plan.sidecar.write_text(json.dumps({"version": 1, "url": url, "size": plan.size if size is None else size,
                                        "sha256": plan.sha256 if sha256 is None else sha256, "etag": etag, "last_modified": last_modified}), encoding="utf-8")


def nothing_kept(plan):
    return not plan.partial.exists() and not plan.sidecar.exists()


# -- 206: the rest is appended ---------------------------------------------------------------------------------------------------------

def test_an_interrupted_download_continues_from_where_it_stopped(tmp_path, host, plan, monkeypatch):
    monkeypatch.setattr(ai_model_store, "DOWNLOAD_ATTEMPTS", 1)
    host.drops = [4000]
    with pytest.raises(ModelInstallError) as caught:
        download(tmp_path, plan)
    assert caught.value.category == "network"
    assert plan.partial.read_bytes() == BODY[:4000]                # what arrived is kept, on disk
    sidecar = json.loads(plan.sidecar.read_text(encoding="utf-8"))
    assert (sidecar["url"], sidecar["size"], sidecar["sha256"], sidecar["etag"]) == (URL, len(BODY), SHA, '"v1"')
    assert not (tmp_path / "generation" / "weights.bin").exists()

    seen = []
    target, written = download(tmp_path, plan, progress=lambda label, done, total: seen.append((done, total)))    # the next try, later
    assert host.requests[1] == {"range": "bytes=4000-", "if_range": '"v1"'}
    assert target.read_bytes() == BODY and written == len(BODY)
    assert host.sent == len(BODY)                                   # no byte was fetched twice
    assert seen[0] == (5000, len(BODY)) and seen[-1] == (len(BODY), len(BODY))      # progress starts from what was kept
    assert nothing_kept(plan)


def test_the_retries_inside_one_call_continue_too_and_are_bounded(tmp_path, host, plan):
    host.drops = [3000, 2000, 1000]                                 # three lost connections, then a good one
    target, _ = download(tmp_path, plan)
    assert [r["range"] for r in host.requests] == [None, "bytes=3000-", "bytes=5000-", "bytes=6000-"]
    assert target.read_bytes() == BODY and host.sent == len(BODY)

    host.requests.clear()
    host.drops = [0] * 10                                           # a connection that never delivers
    with pytest.raises(ModelInstallError) as caught:
        download(tmp_path / "again", _ResumePlan(tmp_path / "again" / "w.part", len(BODY), SHA))
    assert caught.value.category == "network" and len(host.requests) == ai_model_store.DOWNLOAD_ATTEMPTS


def test_cancelling_keeps_what_arrived_for_the_next_try(tmp_path, host, plan):
    chunks = []

    def cancel():
        return len(chunks) >= 3

    with pytest.raises(ModelInstallCancelled):
        download(tmp_path, plan, progress=lambda *a: chunks.append(a), cancel=cancel)
    assert plan.partial.stat().st_size == 3 * CHUNK and plan.sidecar.exists()
    target, _ = download(tmp_path, plan)
    assert host.requests[-1]["range"] == "bytes=3000-" and target.read_bytes() == BODY


# -- 200: the server sent the whole file ---------------------------------------------------------------------------------------------------

def test_a_server_that_ignores_the_range_restarts_cleanly(tmp_path, host, plan):
    keep(plan, BODY[:4000])
    host.ignore_ranges = True
    target, written = download(tmp_path, plan)
    assert host.requests == [{"range": "bytes=4000-", "if_range": '"v1"'}]
    assert target.read_bytes() == BODY and written == len(BODY)    # replaced from the first byte, not appended to
    assert nothing_kept(plan)


def test_a_file_that_changed_on_the_server_is_fetched_whole(tmp_path, host):
    plan = _ResumePlan(partial=tmp_path / "kept" / "weights.bin.part")      # a source the manifest has no hash for
    keep(plan, b"old revision " * 100, etag='"v0"')
    target, _ = download(tmp_path, plan)
    assert host.requests[0]["if_range"] == '"v0"'                   # the validator made the server send the new file, whole
    assert target.read_bytes() == BODY


def test_a_validator_that_differs_on_a_206_discards_the_partial(tmp_path, host, plan):
    keep(plan, BODY[:4000], etag='"v0"')
    real = host.urlopen

    def careless(request, timeout=None):                            # a host that honours the range without looking at If-Range
        request.remove_header("If-range")
        return real(request, timeout)

    with patch("urllib.request.urlopen", careless):
        target, _ = download(tmp_path, plan)
    assert [r["range"] for r in host.requests] == ["bytes=4000-", None]
    assert target.read_bytes() == BODY


def test_a_range_that_does_not_line_up_is_not_appended(tmp_path, host, plan):
    keep(plan, BODY[:4000])
    host.misalign = 7
    real = host.urlopen

    def once(request, timeout=None):
        response = real(request, timeout)
        host.misalign = 0
        return response

    with patch("urllib.request.urlopen", once):
        target, _ = download(tmp_path, plan)
    assert [r["range"] for r in host.requests] == ["bytes=4000-", None] and target.read_bytes() == BODY


# -- 416: nothing lies beyond the partial -----------------------------------------------------------------------------------------------------

def test_a_partial_that_is_already_whole_is_verified_and_used_without_a_request(tmp_path, host, plan):
    keep(plan, BODY)                                                # interrupted after the last byte, before it was moved
    target, written = download(tmp_path, plan)
    assert host.requests == [] and target.read_bytes() == BODY and written == len(BODY) and nothing_kept(plan)


def test_a_416_for_a_whole_partial_of_unknown_size_completes_it(tmp_path, host):
    plan = _ResumePlan(partial=tmp_path / "kept" / "weights.bin.part")
    keep(plan, BODY)
    target, _ = download(tmp_path, plan)
    assert host.requests == [{"range": f"bytes={len(BODY)}-", "if_range": '"v1"'}]
    assert target.read_bytes() == BODY


def test_a_416_for_a_stale_partial_starts_again(tmp_path, host):
    plan = _ResumePlan(partial=tmp_path / "kept" / "weights.bin.part")
    keep(plan, BODY + b"longer than the file the server has now")
    target, _ = download(tmp_path, plan)
    assert [r["range"] for r in host.requests] == [f"bytes={len(BODY) + 39}-", None]
    assert target.read_bytes() == BODY


# -- verification before the file is used ----------------------------------------------------------------------------------------------------

def test_a_hash_mismatch_after_a_resume_discards_the_partial_and_downloads_it_whole(tmp_path, host, plan):
    keep(plan, b"\0" * 4000)                                        # the kept part was damaged on disk
    target, _ = download(tmp_path, plan)
    assert [r["range"] for r in host.requests] == ["bytes=4000-", None]
    assert target.read_bytes() == BODY and nothing_kept(plan)


def test_a_whole_partial_with_the_wrong_hash_is_not_used(tmp_path, host, plan):
    keep(plan, b"\1" * len(BODY))
    target, _ = download(tmp_path, plan)
    assert [r["range"] for r in host.requests] == [None] and target.read_bytes() == BODY


def test_a_file_that_is_not_the_published_one_never_reaches_the_generation(tmp_path, host, plan):
    host.body = BODY[:-1] + b"!"                                    # same size, another file
    with pytest.raises(ModelInstallError) as caught:
        download(tmp_path, plan)
    assert caught.value.category == "hash_mismatch" and len(host.requests) == 1     # a whole download that is wrong is not retried
    assert not (tmp_path / "generation" / "weights.bin").exists() and nothing_kept(plan)

    host.requests.clear()
    keep(plan, host.body[:4000])
    with pytest.raises(ModelInstallError) as caught:
        download(tmp_path, plan)
    assert caught.value.category == "hash_mismatch"
    assert [r["range"] for r in host.requests] == ["bytes=4000-", None]             # resumed, wrong; whole, still wrong: then it stops
    assert not (tmp_path / "generation" / "weights.bin").exists() and nothing_kept(plan)


def test_a_wrong_size_never_reaches_the_generation(tmp_path, host, plan):
    host.body = BODY + b"extra"
    with pytest.raises(ModelInstallError) as caught:
        download(tmp_path, plan)
    assert caught.value.category == "truncated" and nothing_kept(plan)
    assert not (tmp_path / "generation" / "weights.bin").exists()


# -- when a partial may not be continued ------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("change", ["url", "sha256", "size", "no sidecar", "unreadable sidecar", "no validator", "weak etag"])
def test_a_partial_that_is_not_part_of_this_file_is_started_again(tmp_path, host, plan, change):
    kwargs = {"url": {"url": URL.replace("abc", "def")}, "sha256": {"sha256": "0" * 64}, "size": {"size": 99}, "no validator": {"etag": ""},
              "weak etag": {"etag": 'W/"v1"'}}.get(change, {})
    keep(plan, BODY[:4000], **kwargs)
    if change == "no sidecar":
        plan.sidecar.unlink()
    elif change == "unreadable sidecar":
        plan.sidecar.write_text("{not json", encoding="utf-8")
    target, _ = download(tmp_path, plan)
    assert host.requests == [{"range": None, "if_range": None}] and target.read_bytes() == BODY


def test_last_modified_is_the_validator_when_the_etag_is_weak(tmp_path, host, plan):
    host.etag, host.last_modified = 'W/"v1"', "Wed, 21 Oct 2026 07:28:00 GMT"
    host.drops = [4000]
    target, _ = download(tmp_path, plan)
    assert host.requests[1] == {"range": "bytes=4000-", "if_range": "Wed, 21 Oct 2026 07:28:00 GMT"}
    assert target.read_bytes() == BODY and host.sent == len(BODY)


# -- a whole bundle ------------------------------------------------------------------------------------------------------------------------------

class BundleResumeTests(_StoreTestCase):
    """`install_bundle` with the real downloader: a cancelled install leaves its bytes, and the next one continues from them."""

    def setUp(self) -> None:
        super().setUp()
        self.hosts = {name: Host(body) for name, body in CONTENT.items()}
        extra = [patch("urllib.request.urlopen", self.urlopen), patch.object(ai_model_store, "DOWNLOAD_CHUNK_SIZE", 40),
                 patch.object(ai_model_store, "DOWNLOAD_BACKOFF_SECONDS", 0)]
        for item in extra:
            item.start()
            self.addCleanup(item.stop)

    def urlopen(self, request, timeout=None):
        name = request.full_url.split(f"/resolve/{TEST_BUNDLE.revision}/")[1].split("?")[0]
        return self.hosts[name].urlopen(request, timeout)

    def test_a_cancelled_install_is_continued_by_the_next_one(self) -> None:
        seen: list[int] = []
        with self.assertRaises(ModelInstallCancelled):
            ai_model_store.install_bundle(TEST_BUNDLE.key, progress_callback=lambda label, done, total: seen.append(done),
                                          cancel_check=lambda: len(seen) >= 2)
        staging = ai_model_store.managed_staging_root()
        self.assertEqual([child.name for child in staging.iterdir() if child.is_dir()], [ai_model_store.PARTIAL_DIRNAME])    # the generation is gone
        self.assertEqual(ai_model_store.bundle_partial_bytes(TEST_BUNDLE.key), 80)
        self.assertFalse(ai_model_store.bundle_status(TEST_BUNDLE.key).is_ready)

        status = ai_model_store.install_bundle(TEST_BUNDLE.key)
        self.assertTrue(status.is_ready)
        weights = self.hosts["weights.bin"]
        self.assertEqual(weights.requests[-1], {"range": "bytes=80-", "if_range": '"v1"'})
        self.assertEqual(weights.sent, len(PAYLOAD))                # each byte of the big file was fetched once
        self.assertEqual((self.install_dir() / "weights.bin").read_bytes(), PAYLOAD)
        self.assertEqual(ai_model_store.bundle_partial_bytes(TEST_BUNDLE.key), 0)
        self.assertFalse(ai_model_store.bundle_partial_dir(TEST_BUNDLE.key).exists())

    def test_a_failed_install_is_continued_and_a_damaged_partial_is_replaced(self) -> None:
        self.hosts["weights.bin"].drops = [40] * ai_model_store.DOWNLOAD_ATTEMPTS
        with self.assertRaises(ModelInstallError):
            ai_model_store.install_bundle(TEST_BUNDLE.key)
        kept = ai_model_store.bundle_partial_dir(TEST_BUNDLE.key) / "weights.bin.part"
        self.assertEqual(kept.read_bytes(), PAYLOAD[:40 * ai_model_store.DOWNLOAD_ATTEMPTS])
        kept.write_bytes(b"\0" * kept.stat().st_size)               # damaged while it waited
        self.assertTrue(ai_model_store.install_bundle(TEST_BUNDLE.key).is_ready)
        self.assertEqual((self.install_dir() / "weights.bin").read_bytes(), PAYLOAD)

    def test_removing_a_model_removes_its_unfinished_download(self) -> None:
        seen: list[int] = []
        with self.assertRaises(ModelInstallCancelled):
            ai_model_store.install_bundle(TEST_BUNDLE.key, progress_callback=lambda *a: seen.append(1), cancel_check=lambda: len(seen) >= 2)
        self.assertGreater(ai_model_store.bundle_partial_bytes(TEST_BUNDLE.key), 0)
        ai_model_store.uninstall_bundle(TEST_BUNDLE.key)
        self.assertEqual(ai_model_store.bundle_partial_bytes(TEST_BUNDLE.key), 0)


# -- what the AI Center says ------------------------------------------------------------------------------------------------------------------------

def test_the_ai_center_says_that_an_interrupted_download_is_kept_only_when_it_is(qapp):
    from image_triage.qml_shell.ai_center import AiCenterBackend
    from image_triage.qml_shell.cull_model import CullModel
    from image_triage.qml_shell.photos import PhotoModel
    from test_qml_shell_ai_center import FakeOps, until

    ops = FakeOps()
    ops.raw["models"]["quality"]["kept"] = 310_000_000
    cull = CullModel(PhotoModel(parent=qapp), memory=False, ai=AiCenterBackend(ops, parent=qapp), parent=qapp)
    until(qapp, lambda: cull.view["center"]["rtState"] != "Checking…")
    rows = {row["key"]: row for row in cull.view["center"]["models"]}
    assert "An interrupted download is kept (310 MB so far): the next try continues from there." in rows["quality"]["desc"]
    assert "interrupted" not in rows["appeal"]["desc"]
    cull.shutdown()


from test_qml_shell import qapp  # noqa: E402,F401 - the application fixture
