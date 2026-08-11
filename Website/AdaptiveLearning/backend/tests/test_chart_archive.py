"""Archiving a closed session's charts to private storage.

The renderer has its own tests (`test_chart_render.py`). What is asserted here
is everything around it: which rows become which chart, that a channel with
nothing to show produces **no object** rather than an empty one, that a replay
overwrites instead of duplicating, and -- the property with the longest reach --
that a storage failure cannot cost a student their session close.

That last one is why this runs off the request path at all. These objects are
the human-readable record that outlives Phase 9's end-of-year delete, which
makes them worth retrying and logging loudly, and worth nothing at all compared
to the session row itself.
"""

import os
from datetime import datetime, timezone

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import chart_archive  # noqa: E402
import chart_render  # noqa: E402

USER = "11111111-2222-3333-4444-555555555555"
SESSION = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


# ── a fake client: three signal tables, one sessions row, one bucket ─────────

class _Query:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


class _Update(_Query):
    def __init__(self, sink, values):
        super().__init__(None)
        sink.append(values)


class _Storage:
    def __init__(self, fail=False):
        self.uploaded = {}
        self.calls = []
        self._fail = fail

    def upload(self, path, file, file_options=None):
        if self._fail:
            raise RuntimeError("storage unavailable")
        self.calls.append(path)
        self.uploaded[path] = (file, file_options or {})


class _Client:
    def __init__(self, cognitive=(), face=(), heart=(), fail_storage=False):
        self._rows = {"cognitive_signals": list(cognitive),
                      "face_signals": list(face),
                      "heart_signals": list(heart)}
        self.updates = []
        self._storage = _Storage(fail=fail_storage)

    def table(self, name):
        if name == "sessions":
            return type("T", (), {
                "update": lambda _s, values: _Update(self.updates, values)})()
        return _Query(self._rows[name])

    @property
    def storage(self):
        client = self

        class _S:
            def from_(self_inner, bucket):
                assert bucket == chart_archive.BUCKET
                return client._storage

        return _S()

    @property
    def bucket(self):
        return self._storage


def _ts(minute: int) -> str:
    return datetime(2026, 6, 11, 14, minute, tzinfo=timezone.utc).isoformat()


COG = [{"ts": _ts(0), "focus": 0.6, "engagement": 0.7, "stress": 0.2},
       {"ts": _ts(1), "focus": 0.8, "engagement": 0.5, "stress": 0.3}]
HEART = [{"ts": _ts(0), "heart_rate_bpm": 72.0, "rmssd_ms": 41.0,
          "stress_category": "low", "trusted": True},
         {"ts": _ts(1), "heart_rate_bpm": 75.0, "rmssd_ms": None,
          "stress_category": None, "trusted": True}]
FACE = [{"ts": _ts(0), "emotion": "neutral"},
        {"ts": _ts(1), "emotion": "happy"},
        {"ts": _ts(2), "emotion": None}]


# ── what gets drawn ─────────────────────────────────────────────────────────

def test_every_chart_is_rendered_and_uploaded_under_its_own_path():
    client = _Client(cognitive=COG, face=FACE, heart=HEART)

    paths = chart_archive.archive_session(client, SESSION, USER)

    assert set(paths) == set(chart_render.CHART_NAMES)
    for name in chart_render.CHART_NAMES:
        assert paths[name] == f"{USER}/{SESSION}/{name}.svg"
    assert sorted(client.bucket.uploaded) == sorted(paths.values())
    for body, opts in client.bucket.uploaded.values():
        assert body.startswith(b"<svg")
        assert opts["content-type"] == "image/svg+xml"


def test_the_user_id_comes_first_in_the_object_path():
    """The prefix a storage RLS policy would have to be written against. No
    policy exists today, but a layout that forecloses one is a migration of
    every object away."""
    assert chart_archive.object_path(USER, SESSION, "heart_rate") \
        == f"{USER}/{SESSION}/heart_rate.svg"


def test_a_channel_that_recorded_nothing_gets_no_object():
    """Null on `chart_paths`, not an empty chart. An empty chart asserts the
    session had that channel and it read flat -- absence rendered as data, the
    failure the whole reporting layer is built to avoid."""
    client = _Client(cognitive=COG)          # no face rows, no heart rows

    paths = chart_archive.archive_session(client, SESSION, USER)

    assert paths["cognitive_timeline"]
    assert paths["heart_rate"] is None
    assert paths["stress_pie"] is None
    assert paths["emotion_pie"] is None
    assert list(client.bucket.uploaded) == [paths["cognitive_timeline"]]


def test_a_session_with_nothing_at_all_still_records_that_it_tried():
    """All four null is a different fact from a NULL column, which means the
    archive never ran. A reader has to be able to tell "recorded nothing" from
    "closed before this shipped"."""
    client = _Client()

    paths = chart_archive.archive_session(client, SESSION, USER)

    assert paths == {name: None for name in chart_render.CHART_NAMES}
    assert client.updates == [{"chart_paths": paths}]
    assert client.bucket.uploaded == {}


def test_the_paths_are_written_to_the_session_row():
    client = _Client(cognitive=COG)

    paths = chart_archive.archive_session(client, SESSION, USER)

    assert client.updates == [{"chart_paths": paths}]


def test_a_replayed_close_overwrites_rather_than_colliding():
    """`/end` and then the stale-session sweep can both close one session. The
    paths are derived from the ids, so the second run rewrites the same four
    objects -- and `upsert` has to be the string "true", because storage-py
    passes file_options through as HTTP headers and a bool arrives as "True"."""
    client = _Client(cognitive=COG)

    first = chart_archive.archive_session(client, SESSION, USER)
    chart_archive.archive_session(client, SESSION, USER)

    assert len(client.bucket.uploaded) == 1
    assert client.bucket.calls == [first["cognitive_timeline"]] * 2
    _body, opts = client.bucket.uploaded[first["cognitive_timeline"]]
    assert opts["upsert"] == "true", "a bool here becomes the header 'True'"


# ── what the charts are made of ─────────────────────────────────────────────

def test_a_rejected_facial_window_is_not_counted_as_an_emotion():
    """`emotion: None` is a window the quality gate refused. Counting it would
    inflate every slice against a session that mostly failed that gate."""
    counts = chart_archive._counts(FACE, "emotion")

    assert counts == {"neutral": 1, "happy": 1}


def test_a_heart_row_with_no_stress_category_is_counted_as_unknown():
    """Unlike the facial case: the window did produce a heart rate, so the row
    is a reading. Dropping it would understate how much of the session had a
    measurement whose category was still calibrating."""
    counts = chart_archive._counts(HEART, "stress_category", default="unknown")

    assert counts == {"low": 1, "unknown": 1}


def test_an_unparseable_timestamp_drops_the_point_rather_than_placing_it_at_zero():
    """A row at epoch zero drags the x axis back to 1970 and flattens the whole
    session against the right edge -- one bad stamp silently destroying the
    chart rather than costing one point of it."""
    rows = [{"ts": _ts(0), "focus": 0.6}, {"ts": "not a date", "focus": 0.9}]

    points = chart_archive._line_points(rows, ("focus",))

    assert len(points["focus"]) == 1


def test_the_two_pies_use_the_palettes_they_are_named_for():
    """Wired by hand, so worth pinning: `stress_pie` drawn with the emotion
    palette would colour "high" as an emotion nobody felt, and the drift test
    between the renderer and the JSX cannot see a caller passing the wrong one.
    """
    client = _Client(face=FACE, heart=HEART)
    chart_archive.archive_session(client, SESSION, USER)

    emotion = client.bucket.uploaded[f"{USER}/{SESSION}/emotion_pie.svg"][0].decode()
    stress = client.bucket.uploaded[f"{USER}/{SESSION}/stress_pie.svg"][0].decode()

    assert chart_render.EMOTION_COLOURS["happy"] in emotion
    assert chart_render.STRESS_COLOURS["low"] in stress
    # "Stress" alone over a heart-derived category is the label that lets a
    # reader average it against cognitive `stress`, which is `1.0 - calm`.
    assert "Autonomic arousal" in stress


def test_untrusted_rows_are_drawn_too():
    """Deliberately unlike `signal_daily_rollup`, which averages trusted rows
    only. The rollup publishes a number that outlives its evidence; this is a
    picture of what the reviewer was shown, and it has to match."""
    rows = HEART + [{"ts": _ts(3), "heart_rate_bpm": 130.0,
                     "stress_category": "high", "trusted": False}]

    counts = chart_archive._counts(rows, "stress_category", default="unknown")

    assert counts["high"] == 1


# ── failure containment ─────────────────────────────────────────────────────

def test_a_storage_failure_is_logged_and_does_not_escape(capsys):
    """`_run` is what the pool executes. A session close has already written the
    session row, the stats and the rollup by the time this runs, and none of
    them may be lost to a bucket being down."""
    client = _Client(cognitive=COG, fail_storage=True)

    chart_archive._run(client, SESSION, USER)          # must not raise

    assert "archive failed" in capsys.readouterr().out
    assert client.updates == [], "no paths recorded for uploads that failed"


def test_scheduling_never_raises_even_with_the_pool_shut_down(capsys):
    """Submit itself can fail -- a shut-down pool, an interpreter tearing down.
    A successful session close must not become a 500 over a picture."""
    chart_archive.shutdown_pool()
    pool = chart_archive._pool()
    pool.shutdown(wait=True)

    chart_archive.schedule(_Client(), SESSION, USER)   # must not raise

    assert "could not queue" in capsys.readouterr().out
    chart_archive.shutdown_pool()


def test_archiving_reads_no_more_rows_than_session_review_does():
    """The archive is meant to be what the reviewer saw. A larger cap here would
    archive a chart nobody was ever shown."""
    import inspect

    import main

    source = inspect.getsource(main.session_signals)
    assert f"limit({chart_archive._ROW_CAP})" in source


# ── every close site archives ───────────────────────────────────────────────

def test_every_session_close_schedules_an_archive():
    """Derived, not a hand-kept list. A fourth close site added later would
    otherwise leave sessions whose raw rows expire on `ends_on` with no picture
    behind them -- and `chart_paths` would be NULL, which reads as "closed
    before this shipped" rather than as a bug.

    The rollup is the marker: it already runs at every close, for exactly the
    same reason, and the two have been added and forgotten in the same places.
    """
    import inspect

    import main

    lines = inspect.getsource(main).splitlines()
    rollups = [i for i, ln in enumerate(lines) if "_rollup_session_days(" in ln
               and "def " not in ln]
    assert len(rollups) >= 3, "close sites vanished; this test is now vacuous"

    for i in rollups:
        window = "\n".join(lines[i:i + 12])
        assert "chart_archive.schedule(" in window, (
            f"close site at line {i + 1} rolls up but does not archive")
