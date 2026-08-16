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


# ── reading them back ───────────────────────────────────────────────────────

class _SigningStorage(_Storage):
    """Signs anything it was given, and refuses anything it was not.

    Modelling the refusal is the point: an object recorded on `chart_paths` and
    absent from the bucket is a fault, and the endpoint has to report it as one
    rather than as a channel that recorded nothing."""

    def create_signed_url(self, path, expires_in):
        if path not in self.uploaded:
            raise RuntimeError("Object not found")
        self.signed = getattr(self, "signed", [])
        self.signed.append((path, expires_in))
        return {"signedURL": f"https://storage.test/{path}?token=x&exp={expires_in}",
                "signedUrl": f"https://storage.test/{path}?token=x&exp={expires_in}"}


class _SigningClient(_Client):
    def __init__(self, **kw):
        super().__init__(**kw)
        self._storage = _SigningStorage()


def test_signing_separates_nothing_recorded_from_nothing_readable():
    """The property the whole payload shape exists for. A null and a missing
    object both leave a blank tile, and only one of them is the truth about the
    session -- a bucket half-emptied by hand would otherwise read as a term in
    which nobody wore a headband."""
    client = _SigningClient(cognitive=COG)
    paths = chart_archive.archive_session(client, SESSION, USER)
    # Recorded, but the object is not there.
    paths["heart_rate"] = f"{USER}/{SESSION}/heart_rate.svg"

    urls, missing = chart_archive.signed_chart_urls(client, paths, USER, SESSION)

    assert urls["cognitive_timeline"].startswith("https://storage.test/")
    assert urls["emotion_pie"] is None          # channel produced nothing
    assert missing == ["heart_rate"]            # object should exist and does not
    assert "heart_rate" not in urls


def test_a_tampered_path_cannot_reach_another_students_object():
    """`chart_paths` is ordinary jsonb on `sessions`, and `sessions` carries a
    `FOR ALL` own-row policy, so before the accompanying migration a student
    could PATCH their own row through PostgREST and point it at another child's
    object. Signing what is stored would then hand it over -- through an
    endpoint whose access check had just correctly confirmed they own *this*
    session.

    So presence is all the stored value decides; the path is derived. The
    migration revokes the write as well, but this must hold without it: a grant
    is one migration away from being widened back, and the endpoint is the layer
    that cannot be.
    """
    victim = "99999999-8888-7777-6666-555555555555"
    client = _SigningClient(cognitive=COG)
    chart_archive.archive_session(client, SESSION, USER)
    # What the attacker's own session row now claims.
    tampered = {"cognitive_timeline": f"{victim}/their-session/cognitive_timeline.svg"}

    urls, missing = chart_archive.signed_chart_urls(client, tampered, USER, SESSION)

    assert victim not in urls.get("cognitive_timeline", "")
    assert urls["cognitive_timeline"].startswith(
        f"https://storage.test/{USER}/{SESSION}/")
    assert missing == []


def test_a_chart_never_attempted_appears_in_neither_half():
    """An absent key is a session closed before Phase 8 shipped. Reporting it as
    a null would claim that channel was on and drew nothing."""
    client = _SigningClient()

    urls, missing = chart_archive.signed_chart_urls(client, {}, USER, SESSION)

    assert urls == {} and missing == []


def test_signed_urls_are_short_lived():
    """There is no revocation: a signed URL stays valid until it expires,
    whatever happens to consent in between, so the TTL is the only bound on a
    leaked one."""
    client = _SigningClient(cognitive=COG)
    paths = chart_archive.archive_session(client, SESSION, USER)

    chart_archive.signed_chart_urls(client, paths, USER, SESSION)

    assert client.bucket.signed == [
        (paths["cognitive_timeline"], chart_archive.SIGNED_URL_TTL_SECONDS)]
    assert chart_archive.SIGNED_URL_TTL_SECONDS <= 900


# ── the endpoint ────────────────────────────────────────────────────────────

class _SessionsClient(_SigningClient):
    """Adds the `sessions` row the endpoint reads before it signs anything."""

    def __init__(self, row, **kw):
        super().__init__(**kw)
        self._row = row

    def table(self, name):
        if name == "sessions":
            row = self._row

            class _T:
                def select(self, *_a, **_k):
                    return self

                def eq(self, *_a, **_k):
                    return self

                def single(self):
                    return self

                def execute(self):
                    if row is None:
                        raise RuntimeError("no such row")
                    return type("R", (), {"data": row})()

            return _T()
        return super().table(name)


def _charts(monkeypatch, row, viewer="viewer", **kw):
    import main

    client = _SessionsClient(row, **kw)
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": viewer})
    monkeypatch.setattr(main, "supabase", client)
    return main.session_charts(SESSION, None), client


def test_the_endpoint_checks_the_relationship_not_the_role(monkeypatch):
    """The only thing between a caller and another child's charts. Unlike the
    rows next door, these objects sit in a bucket with no policies at all, so
    there is no second line of defence if this check is wrong."""
    import main

    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "a-stranger"})
    monkeypatch.setattr(main, "supabase",
                        _SessionsClient({"user_id": USER, "chart_paths": {}}))

    with pytest.raises(main.HTTPException) as exc:
        main.session_charts(SESSION, None)
    assert exc.value.status_code == 403


def test_a_session_that_was_never_archived_says_so(monkeypatch):
    """Column-NULL, and no storage call at all. `archived: false` is a different
    fact from four nulls, and a viewer must not be told a student recorded
    nothing when the archive simply never ran."""
    import main

    monkeypatch.setattr(main, "_verify_can_view_student", lambda *_a: None)
    payload, client = _charts(monkeypatch,
                              {"user_id": USER, "chart_paths": None})

    assert payload["archived"] is False
    assert payload["charts"] == {} and payload["unavailable"] == []
    assert not hasattr(client.bucket, "signed")


def test_the_endpoint_returns_a_url_per_recorded_chart(monkeypatch):
    import main

    monkeypatch.setattr(main, "_verify_can_view_student", lambda *_a: None)
    # Archive with the plain client, then read back through one that serves the
    # sessions row -- the same objects, reached the way the endpoint reaches
    # them, rather than a hand-written path map that could agree with nothing.
    archiver = _SigningClient(cognitive=COG)
    paths = chart_archive.archive_session(archiver, SESSION, USER)
    client = _SessionsClient({"user_id": USER, "chart_paths": paths})
    client._storage = archiver._storage
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": USER})
    monkeypatch.setattr(main, "supabase", client)

    payload = main.session_charts(SESSION, None)

    assert payload["archived"] is True
    assert payload["charts"]["cognitive_timeline"].startswith("https://")
    assert payload["charts"]["heart_rate"] is None
    assert payload["expires_in"] == chart_archive.SIGNED_URL_TTL_SECONDS


def test_an_unreadable_session_row_is_a_404_not_an_empty_payload(monkeypatch):
    """Matching the endpoint next door. Degrading to "no charts" here would
    report an absence the read never established."""
    import main

    monkeypatch.setattr(main, "get_user", lambda _r: {"id": USER})
    monkeypatch.setattr(main, "supabase", _SessionsClient(None))

    with pytest.raises(main.HTTPException) as exc:
        main.session_charts(SESSION, None)
    assert exc.value.status_code == 404


# ── every close site archives ───────────────────────────────────────────────

def test_every_session_close_schedules_an_archive():
    """Derived, not a hand-kept list. A fourth close site added later would
    otherwise leave sessions whose raw rows expire on `ends_on` with no picture
    behind them -- and `chart_paths` would be NULL, which reads as "closed
    before this shipped" rather than as a bug.

    The close sequence now lives in `_close_session`, so the check is that
    every site reaches it and that it still archives. Before that consolidation
    the three sites each carried their own copy and each dropped a different
    step; the archive was the step `class_live` and the stale sweep were both
    missing at different times.
    """
    import inspect

    import main
    from conftest import close_sites

    closers = close_sites()

    assert len(closers) >= 3, "close sites vanished; this test is now vacuous"
    for name, source in closers:
        assert "_close_session(" in source or "chart_archive.schedule(" in source, (
            f"{name} ends a session without archiving its charts -- its raw rows "
            "expire on `ends_on` with nothing left behind, and `chart_paths` "
            'would read as "closed before this shipped" rather than as a bug')

    assert "chart_archive.schedule(" in inspect.getsource(main._close_session), (
        "the shared close no longer archives, so no close site does")


# ── the orphan sweep (#107) ─────────────────────────────────────────────────
#
# Storage does not cascade, so a session deleted through the dashboard leaves
# its SVGs behind. The sweep is the only thing that catches that, and it deletes
# on *absence* -- so most of what is asserted here is that it refuses.

USER2 = "99999999-8888-7777-6666-555555555555"
GONE = "12121212-3434-5656-7878-909090909090"


class _SweepStorage:
    """A bucket laid out `{user}/{session}/{chart}.svg`, paged like storage-py."""

    def __init__(self, tree, page=chart_archive._LIST_PAGE):
        # {user: {session: [filenames]}}
        self.tree = tree
        self.removed = []
        self.page = page
        self.list_calls = []

    def _entries(self, prefix):
        if prefix == "":
            return sorted(self.tree)
        parts = prefix.split("/")
        if len(parts) == 1:
            return sorted(self.tree.get(parts[0], {}))
        return sorted(self.tree.get(parts[0], {}).get(parts[1], []))

    def list(self, prefix, opts=None):
        self.list_calls.append(prefix)
        names = self._entries(prefix)
        off = (opts or {}).get("offset", 0)
        lim = (opts or {}).get("limit", self.page)
        return [{"name": n} for n in names[off:off + lim]]

    def remove(self, paths):
        self.removed.extend(paths)


class _SweepClient:
    """Sessions that exist, plus a record of the order calls arrived in."""

    def __init__(self, storage, live_ids, fail_sessions=False):
        self._storage = storage
        self._live = set(live_ids)
        self._fail = fail_sessions
        self.order = []

    def table(self, name):
        assert name == "sessions"
        client = self

        class _T:
            def select(self_inner, *_a):
                return self_inner

            def in_(self_inner, _col, ids):
                client.order.append("sessions")
                if client._fail:
                    raise RuntimeError("sessions unavailable")
                self_inner._ids = ids
                return self_inner

            def execute(self_inner):
                rows = [{"id": i} for i in self_inner._ids if i in client._live]
                return type("R", (), {"data": rows})()

        return _T()

    @property
    def storage(self):
        client = self

        class _S:
            def from_(self_inner, bucket):
                assert bucket == chart_archive.BUCKET
                client.order.append("bucket")
                return client._storage

        return _S()


def _tree(*sessions):
    out = {}
    for user, sid in sessions:
        out.setdefault(user, {})[sid] = ["cognitive.svg", "heart.svg"]
    return out


def test_a_session_that_still_exists_keeps_its_charts():
    storage = _SweepStorage(_tree((USER, SESSION)))
    client = _SweepClient(storage, live_ids=[SESSION])

    report = chart_archive.sweep_orphan_charts(client, dry_run=False)

    assert report["orphaned_sessions"] == 0
    assert storage.removed == [], "deleted the charts of a live session"


def test_a_deleted_session_loses_its_charts():
    storage = _SweepStorage(_tree((USER, SESSION), (USER, GONE)))
    client = _SweepClient(storage, live_ids=[SESSION])

    report = chart_archive.sweep_orphan_charts(client, dry_run=False)

    assert report["orphaned_sessions"] == 1
    assert sorted(storage.removed) == [f"{USER}/{GONE}/cognitive.svg",
                                       f"{USER}/{GONE}/heart.svg"]
    assert report["removed"] == 2


def test_a_failed_sessions_read_refuses_instead_of_emptying_the_bucket():
    """The whole reason this job is dangerous. Every object looks orphaned when
    the table cannot be read, and treating that as a result is a bucket wipe.

    `max_orphan_fraction=1.0` on purpose: a failed read always leaves *nothing*
    live, so the fraction guard would catch this too and the test would pass
    with the read guard deleted -- which is what the first version of it did.
    Disabling the backstop is what makes this test about the guard it names, and
    1.0 is a real operator choice rather than a contrived one."""
    storage = _SweepStorage(_tree((USER, SESSION), (USER2, GONE)))
    client = _SweepClient(storage, live_ids=[SESSION], fail_sessions=True)

    report = chart_archive.sweep_orphan_charts(
        client, dry_run=False, max_orphan_fraction=1.0)

    assert report["refused"], "a failed sessions read was treated as a result"
    assert "could not read sessions" in report["refused"], (
        f"refused for the wrong reason: {report['refused']}")
    assert storage.removed == [], "deleted objects after failing to read sessions"
    assert report["removed"] == 0


def test_too_many_orphans_refuses_rather_than_proceeding():
    """A read that returns nothing without raising is the same danger wearing a
    different shape, and no exception marks it."""
    storage = _SweepStorage(_tree((USER, SESSION), (USER, GONE), (USER2, GONE)))
    client = _SweepClient(storage, live_ids=[])          # everything looks gone

    report = chart_archive.sweep_orphan_charts(client, dry_run=False)

    assert report["refused"] and "orphaned" in report["refused"]
    assert storage.removed == []


def test_the_refusal_threshold_can_be_raised_once_a_human_has_looked():
    storage = _SweepStorage(_tree((USER, SESSION), (USER, GONE)))
    client = _SweepClient(storage, live_ids=[])

    report = chart_archive.sweep_orphan_charts(
        client, dry_run=False, max_orphan_fraction=1.0)

    assert report["refused"] is None
    assert report["removed"] == 4


def test_dry_run_is_the_default_and_deletes_nothing():
    storage = _SweepStorage(_tree((USER, SESSION), (USER, GONE)))
    client = _SweepClient(storage, live_ids=[SESSION])

    report = chart_archive.sweep_orphan_charts(client)

    assert report["dry_run"] is True
    assert report["would_remove"] == 2
    assert storage.removed == [], "a dry run deleted"


def test_the_bucket_is_listed_before_sessions_is_read():
    """Order is a guard, not a detail. Read the table first and a session
    created in between has objects whose id is missing from the snapshot --
    deleted as an orphan while its row sits there."""
    storage = _SweepStorage(_tree((USER, SESSION)))
    client = _SweepClient(storage, live_ids=[SESSION])

    chart_archive.sweep_orphan_charts(client)

    assert "bucket" in client.order and "sessions" in client.order
    assert client.order.index("bucket") < client.order.index("sessions")


def test_more_than_one_page_of_students_is_swept():
    """`list` caps at 100 and reports no truncation, so a single call would make
    a bucket look like its first 100 students for ever -- and report the rest as
    nothing to do."""
    users = [f"{i:08d}-0000-0000-0000-000000000000" for i in range(150)]
    storage = _SweepStorage({u: {SESSION: ["heart.svg"]} for u in users})
    client = _SweepClient(storage, live_ids=[SESSION])

    report = chart_archive.sweep_orphan_charts(client)

    assert report["scanned_sessions"] == 150, "stopped at a page boundary"


def test_a_path_it_cannot_parse_is_left_alone():
    """Deleting what you cannot identify is how a sweep becomes an incident."""
    tree = _tree((USER, GONE))
    tree["not-a-uuid"] = {SESSION: ["heart.svg"]}
    tree[USER]["also-not-a-uuid"] = ["heart.svg"]
    storage = _SweepStorage(tree)
    client = _SweepClient(storage, live_ids=[])

    report = chart_archive.sweep_orphan_charts(
        client, dry_run=False, max_orphan_fraction=1.0)

    assert report["unrecognised"] == 2
    assert all("uuid" not in p for p in storage.removed)


def test_hitting_the_cap_says_so():
    """`removed: 500` with nothing saying more was waiting reads as a clean
    bucket."""
    tree = {USER: {f"{i:08d}-1111-1111-1111-111111111111": ["heart.svg"]
                   for i in range(5)}}
    storage = _SweepStorage(tree)
    client = _SweepClient(storage, live_ids=[])

    report = chart_archive.sweep_orphan_charts(
        client, dry_run=False, max_deletes=2, max_orphan_fraction=1.0)

    assert report["hit_cap"] is True
    assert report["removed"] == 2


def test_a_bucket_that_never_stops_paging_raises_rather_than_looping():
    class _Endless(_SweepStorage):
        def list(self, prefix, opts=None):
            return [{"name": f"{i}"} for i in range(chart_archive._LIST_PAGE)]

    client = _SweepClient(_Endless({}), live_ids=[])
    report = chart_archive.sweep_orphan_charts(client)

    assert report["refused"] and "did not terminate" in report["refused"]
