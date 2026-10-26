from __future__ import annotations

from types import SimpleNamespace

from image_triage import mask_warmup as mw


class Service:
    def __init__(self, alive=False, loaded=()):
        self.alive = alive
        self.loaded = set(loaded)
        self.calls = []
        self.start_low_priority = False
        self.restored = 0

    def restore_priority(self):
        self.restored += 1
        self.start_low_priority = False

    def keep_alive(self):
        self.calls.append("keep_alive")
        return self.alive

    busy = False

    def loaded_engines(self):
        return None if self.busy else frozenset(self.loaded)

    def warm_imports(self, engine=None):
        self.calls.append(("imports", engine))
        self.alive = True

    def warm_model(self, engine, model_dir):
        self.calls.append(("model", engine, str(model_dir)))
        self.loaded.add(engine)


def installed(**missing):
    return lambda: {k: SimpleNamespace(is_installed=k not in missing, install_dir=f"/models/{k}") for k in mw.ENGINES}


def warmer(service, **kw):
    clock = kw.pop("clock", lambda: 0.0)
    w = mw.MaskWarmer(service=lambda: service, installations=kw.pop("installations", installed()), clock=clock, spawn_thread=lambda target: target(), **kw)
    w._first_tick = -1000.0  # the start delay has long passed (it has its own test)
    return w


def test_the_first_tick_loads_everything_and_later_ticks_only_keep_it_alive():
    service = Service()
    w = warmer(service)
    assert w.tick() is True
    assert service.calls[:2] == ["keep_alive", ("imports", "semantic")]
    assert [c[1] for c in service.calls if isinstance(c, tuple) and c[0] == "model"] == ["semantic", "subject", "prompt"]
    service.calls.clear()
    assert w.tick() is False and service.calls == ["keep_alive"], "loaded: just restart the idle countdown"


def test_a_host_that_went_away_is_loaded_again():
    service = Service(alive=True, loaded=mw.ENGINES)
    w = warmer(service)
    assert w.tick() is False
    service.alive, service.loaded = False, set()  # the idle timeout shut it down
    assert w.tick() is True and service.loaded == set(mw.ENGINES)


def test_a_missing_model_is_skipped_and_with_none_nothing_is_started():
    service = Service()
    assert warmer(service, installations=installed(subject=True)).tick() is True
    assert sorted(service.loaded) == ["prompt", "semantic"]
    none = Service()
    now = [0.0]
    w = warmer(none, installations=installed(semantic=True, subject=True, prompt=True), clock=lambda: now[0])
    w.tick()
    assert not any(isinstance(c, tuple) for c in none.calls), "no torch or CUDA for nothing"
    none.calls.clear()
    assert w.tick() is False and none.calls == [], "and it does not ask again straight away"
    now[0] = mw.RETRY_AFTER_S + 1
    assert w.tick() is True


def test_a_failed_load_is_retried_later_not_every_tick():
    class Broken(Service):
        def warm_imports(self, engine=None):
            raise RuntimeError("no GPU")

    now = [0.0]
    service = Broken()
    w = warmer(service, clock=lambda: now[0])
    assert w.tick() is True and not w.running
    service.calls.clear()
    assert w.tick() is False and service.calls == []
    now[0] = mw.RETRY_AFTER_S + 1
    assert w.tick() is True


def test_it_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("IMAGE_TRIAGE_MASK_WARMUP", "0")
    service = Service()
    assert warmer(service).tick() is False and service.calls == []


def test_a_load_in_progress_is_not_started_twice():
    spawned = []
    w = mw.MaskWarmer(service=lambda: Service(), installations=installed(), spawn_thread=spawned.append)
    w._first_tick = -1000.0
    assert w.tick() is True and w.tick() is False and len(spawned) == 1 and w.running


def test_it_waits_a_few_seconds_after_the_editor_connects_and_loads_at_low_priority():
    now = [100.0]
    service = Service()
    w = mw.MaskWarmer(service=lambda: service, installations=installed(), clock=lambda: now[0], spawn_thread=lambda target: target())
    assert w.tick() is False, "the folder's thumbnails are being made"
    now[0] += mw.START_DELAY_S - 0.5
    assert w.tick() is False
    now[0] += 1.0
    seen = []
    original = service.warm_imports
    service.warm_imports = lambda engine=None: (seen.append(service.start_low_priority), original(engine))[1]
    assert w.tick() is True
    assert seen == [True], "the host is started below normal priority"
    assert service.restored == 1 and service.start_low_priority is False, "and back to normal when it is loaded"


def test_a_host_busy_with_a_request_is_not_mistaken_for_one_with_nothing_loaded():
    service = Service(alive=True)
    service.busy = True  # keep_alive says alive, but which models are loaded cannot be known while the request runs
    assert warmer(service).tick() is False
    assert ("imports", None) not in service.calls
