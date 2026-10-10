import json

from image_triage import nav_timing
from image_triage.nav_timing import NavigationTimer, format_summary, percentile, summarize


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance_ms(self, ms: float) -> None:
        self.now += ms / 1000.0


def make(sink=None):
    clock = FakeClock()
    return NavigationTimer(sink=sink, clock=clock), clock


def test_stages_are_milliseconds_since_the_input():
    timer, clock = make()
    timer.begin("a.nef")
    clock.advance_ms(12)
    assert timer.mark("a.nef", "first_pixel") == 12
    clock.advance_ms(38)
    assert timer.mark("a.nef", "preview") == 50
    clock.advance_ms(550)
    assert timer.mark("a.nef", "refined") == 600
    timer.flush()
    (record,) = timer.finished
    assert record.stages == {"first_pixel": 12, "preview": 50, "refined": 600}
    assert not record.superseded


def test_begin_can_be_backdated_to_the_real_input():
    timer, clock = make()
    started = timer.now()
    clock.advance_ms(7)
    timer.begin("a.nef", started=started)
    assert timer.mark("a.nef", "first_pixel") == 7


def test_a_stage_is_recorded_once_and_only_for_the_navigation_in_flight():
    timer, clock = make()
    timer.begin("a.nef")
    clock.advance_ms(5)
    assert timer.mark("a.nef", "preview") == 5
    clock.advance_ms(5)
    assert timer.mark("a.nef", "preview") is None
    assert timer.mark("other.nef", "preview") is None


def test_a_navigation_left_before_its_preview_is_superseded():
    timer, clock = make()
    timer.begin("a.nef")
    clock.advance_ms(3)
    timer.mark("a.nef", "first_pixel")
    timer.begin("b.nef")
    clock.advance_ms(20)
    timer.mark("b.nef", "preview")
    timer.flush()
    first, second = timer.finished
    assert first.superseded and first.path == "a.nef"
    assert not second.superseded
    assert timer.mark("a.nef", "refined") is None, "a late result for an abandoned photo is not counted"


def test_unknown_stage_is_a_programming_error():
    timer, _ = make()
    timer.begin("a.nef")
    try:
        timer.mark("a.nef", "nonsense")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_sink_failure_never_reaches_navigation():
    def broken(_record):
        raise OSError("disk full")

    timer, clock = make(sink=broken)
    timer.begin("a.nef")
    clock.advance_ms(1)
    timer.mark("a.nef", "preview")
    timer.begin("b.nef")  # retires a.nef through the broken sink
    timer.begin("c.nef")  # and the sink is dropped rather than retried forever
    assert len(timer.finished) == 2


def test_environment_variable_appends_json_lines(tmp_path, monkeypatch):
    log = tmp_path / "nav.jsonl"
    monkeypatch.setenv(nav_timing.ENV_VAR, str(log))
    clock = FakeClock()
    timer = NavigationTimer(clock=clock)
    timer.begin("a.nef")
    clock.advance_ms(40)
    timer.mark("a.nef", "preview")
    timer.flush()
    (line,) = log.read_text(encoding="utf-8").splitlines()
    assert json.loads(line) == {"path": "a.nef", "stages": {"preview": 40.0}, "superseded": False}


def test_percentile_is_nearest_rank():
    values = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    assert percentile(values, 0.50) == 50.0
    assert percentile(values, 0.95) == 100.0
    assert percentile([], 0.5) == 0.0
    assert percentile([7.0], 0.95) == 7.0


def test_summary_counts_budget_misses_per_stage():
    records = [
        {"path": "a", "stages": {"first_pixel": 5, "preview": 60}, "superseded": False},
        {"path": "b", "stages": {"first_pixel": 6, "preview": 150}, "superseded": False},
        {"path": "c", "stages": {"first_pixel": 7, "preview": 250, "refined": 900}, "superseded": False},
        {"path": "d", "stages": {"first_pixel": 4}, "superseded": True},
    ]
    summary = summarize(records)
    assert summary["navigations"] == 4 and summary["superseded"] == 1
    assert summary["preview"]["count"] == 3
    assert summary["preview"]["over_target"] == 2
    assert summary["preview"]["over_ceiling"] == 1
    assert summary["first_pixel"]["count"] == 4 and summary["first_pixel"]["over_target"] == 0
    assert summary["refined"]["count"] == 1
    assert "preview" in format_summary(summary)
