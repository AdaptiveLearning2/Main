"""Archiving a closed session's charts to private storage, and reading them back."""

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
    """Honours `range` and PostgREST's `db-max-rows`: every response is cut at
    `max_rows` whatever was asked for, which is what made a `.limit(20000)`
    read return 1000 rows. A fake that returned everything could not fail
    against that."""

    def __init__(self, rows, max_rows=1000):
        self._rows = rows
        self._max_rows = max_rows
        self._range = None

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def execute(self):
        rows = self._rows
        if rows is None:                    # an update, which returns nothing here
            return type("R", (), {"data": None})()
        if self._range is not None:
            rows = rows[self._range[0]:self._range[1] + 1]
        return type("R", (), {"data": rows[:self._max_rows]})()


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
    """The prefix any future storage RLS policy would key on."""
    assert chart_archive.object_path(USER, SESSION, "heart_rate") \
        == f"{USER}/{SESSION}/heart_rate.svg"


def test_a_channel_that_recorded_nothing_gets_no_object():
    """Null on `chart_paths`, not an empty chart, which would render absence as data."""
    client = _Client(cognitive=COG)          # no face rows, no heart rows

    paths = chart_archive.archive_session(client, SESSION, USER)

    assert paths["cognitive_timeline"]
    assert paths["heart_rate"] is None
    assert paths["stress_pie"] is None
    assert paths["emotion_pie"] is None
    assert list(client.bucket.uploaded) == [paths["cognitive_timeline"]]


def test_a_session_with_nothing_at_all_still_records_that_it_tried():
    """Four nulls ("recorded nothing") differs from a NULL column ("archive never ran")."""
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
    """Paths derive from ids; `upsert` must be the string "true" since file_options become headers."""
    client = _Client(cognitive=COG)

    first = chart_archive.archive_session(client, SESSION, USER)
    chart_archive.archive_session(client, SESSION, USER)

    assert len(client.bucket.uploaded) == 1
    assert client.bucket.calls == [first["cognitive_timeline"]] * 2
    _body, opts = client.bucket.uploaded[first["cognitive_timeline"]]
    assert opts["upsert"] == "true", "a bool here becomes the header 'True'"


# ── what the charts are made of ─────────────────────────────────────────────

def test_a_rejected_facial_window_is_not_counted_as_an_emotion():
    """`emotion: None` is a window the quality gate refused."""
    counts = chart_archive._counts(FACE, "emotion")

    assert counts == {"neutral": 1, "happy": 1}


def test_a_heart_row_with_no_stress_category_is_counted_as_unknown():
    """Unlike the facial case, the row is a real heart-rate reading still calibrating."""
    counts = chart_archive._counts(HEART, "stress_category", default="unknown")

    assert counts == {"low": 1, "unknown": 1}


def test_an_unparseable_timestamp_drops_the_point_rather_than_placing_it_at_zero():
    """A point at epoch zero would stretch the x axis to 1970 and flatten the session."""
    rows = [{"ts": _ts(0), "focus": 0.6}, {"ts": "not a date", "focus": 0.9}]

    points = chart_archive._line_points(rows, ("focus",))

    assert len(points["focus"]) == 1


def test_the_two_pies_use_the_palettes_they_are_named_for():
    """Wired by hand, so pinned."""
    client = _Client(face=FACE, heart=HEART)
    chart_archive.archive_session(client, SESSION, USER)

    emotion = client.bucket.uploaded[f"{USER}/{SESSION}/emotion_pie.svg"][0].decode()
    stress = client.bucket.uploaded[f"{USER}/{SESSION}/stress_pie.svg"][0].decode()

    assert chart_render.EMOTION_COLOURS["happy"] in emotion
    assert chart_render.STRESS_COLOURS["low"] in stress
    # Not "Stress": that would invite averaging against cognitive `stress` (1 - calm).
    assert "Autonomic arousal" in stress


def test_untrusted_rows_are_drawn_too():
    """Unlike the rollup: this is a picture of what the reviewer was shown."""
    rows = HEART + [{"ts": _ts(3), "heart_rate_bpm": 130.0,
                     "stress_category": "high", "trusted": False}]

    counts = chart_archive._counts(rows, "stress_category", default="unknown")

    assert counts["high"] == 1


# ── failure containment ─────────────────────────────────────────────────────

def test_a_storage_failure_is_logged_and_does_not_escape(capsys):
    """`_run` is what the pool executes; the session close has already been written."""
    client = _Client(cognitive=COG, fail_storage=True)

    chart_archive._run(client, SESSION, USER)          # must not raise

    assert "archive failed" in capsys.readouterr().out
    assert client.updates == [], "no paths recorded for uploads that failed"


def test_scheduling_never_raises_even_with_the_pool_shut_down(capsys):
    """Submit itself can fail; a session close must not become a 500 over a picture."""
    chart_archive.shutdown_pool()
    pool = chart_archive._pool()
    pool.shutdown(wait=True)

    chart_archive.schedule(_Client(), SESSION, USER)   # must not raise

    assert "could not queue" in capsys.readouterr().out
    chart_archive.shutdown_pool()


def _long_session(n):
    start = datetime(2026, 6, 11, 14, 0, tzinfo=timezone.utc).timestamp()
    return [{"id": i, "ts": datetime.fromtimestamp(start + i, timezone.utc).isoformat(),
             "focus": 0.5, "stress": 0.3} for i in range(n)]


def test_the_archive_reads_past_the_servers_row_cap():
    """PostgREST cuts every response at 1000 rows, service role included, and
    says nothing. At 1 Hz a 45-minute lesson is 2700 rows, and a single
    `.limit(20000)` read archived its first ~17 minutes as the whole lesson."""
    rows = _long_session(2700)
    cognitive, _, _ = chart_archive._fetch(_Client(cognitive=rows), SESSION)
    assert [r["id"] for r in cognitive] == list(range(2700))


def test_the_row_cap_still_binds():
    rows = _long_session(chart_archive._ROW_CAP + 1500)
    cognitive, _, _ = chart_archive._fetch(_Client(cognitive=rows), SESSION)
    assert len(cognitive) == chart_archive._ROW_CAP


def test_session_review_reads_the_same_rows_the_archive_draws(monkeypatch):
    """The archive is meant to be what the reviewer saw, so the two must stop
    at the same row. Asserted on what each returns from one table, not on the
    source: a shared literal said nothing about what either read, and both were
    cut at 1000 while agreeing on 20000."""
    import main

    rows = _long_session(2700)

    class _ReviewClient(_Client):
        def table(self, name):
            if name == "sessions":
                return _Single({"user_id": USER})
            if name == "session_answers":
                return _Query([])
            return super().table(name)

    class _Single(_Query):
        def __init__(self, row):
            super().__init__([row])

        def single(self):
            row = self._rows[0]
            return type("S", (), {"execute": lambda _s: type("R", (), {"data": row})()})()

    client = _ReviewClient(cognitive=rows)
    monkeypatch.setattr(main, "supabase", client)
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": USER})
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *_a, **_k: None)

    reviewed = main.session_signals(SESSION, request=None)["cognitive"]
    archived = chart_archive._fetch(client, SESSION)[0]

    assert len(reviewed) == 2700
    assert reviewed == archived


# ── reading them back ───────────────────────────────────────────────────────

class _SigningStorage(_Storage):
    """Signs anything it was given, and refuses anything it was not."""

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
    """Only a null means the sensor was never used; a missing object is a fault."""
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
    """The stored value decides presence only; the path is derived, even if the write grant returns."""
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
    """An absent key means never attempted; a null would claim the channel drew nothing."""
    client = _SigningClient()

    urls, missing = chart_archive.signed_chart_urls(client, {}, USER, SESSION)

    assert urls == {} and missing == []


def test_signed_urls_are_short_lived():
    """A signed URL can't be revoked, so the TTL is the only bound on a leaked one."""
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
    """The bucket has no policies, so this check is the only defence."""
    import main

    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "a-stranger"})
    monkeypatch.setattr(main, "supabase",
                        _SessionsClient({"user_id": USER, "chart_paths": {}}))

    with pytest.raises(main.HTTPException) as exc:
        main.session_charts(SESSION, None)
    assert exc.value.status_code == 403


def test_a_session_that_was_never_archived_says_so(monkeypatch):
    """Column-NULL means no storage call, and `archived: false` rather than four nulls."""
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
    # Real archived objects, not a hand-written path map that could drift.
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
    """An empty payload would report an absence the read never established."""
    import main

    monkeypatch.setattr(main, "get_user", lambda _r: {"id": USER})
    monkeypatch.setattr(main, "supabase", _SessionsClient(None))

    with pytest.raises(main.HTTPException) as exc:
        main.session_charts(SESSION, None)
    assert exc.value.status_code == 404


# ── every close site archives ───────────────────────────────────────────────

def test_every_session_close_schedules_an_archive():
    """Derived close sites: each reaches `_close_session`, and that still archives."""
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


# ── the orphan sweep ──────────────────────────────────────────────────────
# It deletes on absence, so most of what is asserted here is that it refuses.

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
    """`max_orphan_fraction=1.0` disables the backstop, so only the read guard can pass this."""
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
    """A read that returns nothing without raising is the same danger."""
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
    """Reading sessions first would orphan objects of a session created in between."""
    storage = _SweepStorage(_tree((USER, SESSION)))
    client = _SweepClient(storage, live_ids=[SESSION])

    chart_archive.sweep_orphan_charts(client)

    assert "bucket" in client.order and "sessions" in client.order
    assert client.order.index("bucket") < client.order.index("sessions")


def test_more_than_one_page_of_students_is_swept():
    """`list` caps at 100 with no truncation flag."""
    users = [f"{i:08d}-0000-0000-0000-000000000000" for i in range(150)]
    storage = _SweepStorage({u: {SESSION: ["heart.svg"]} for u in users})
    client = _SweepClient(storage, live_ids=[SESSION])

    report = chart_archive.sweep_orphan_charts(client)

    assert report["scanned_sessions"] == 150, "stopped at a page boundary"


def test_a_path_it_cannot_parse_is_left_alone():
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
    """Without the flag, a capped run reads as a clean bucket."""
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


def test_the_archived_cognitive_chart_does_not_draw_engagement_beside_focus(monkeypatch):
    """`engagement` is the focus index; drawn beside it, it reads as a second measurement."""
    seen = {}
    real = chart_render.line_svg

    def spy(points, title):
        seen[title] = set(points)
        return real(points, title)

    monkeypatch.setattr(chart_render, "line_svg", spy)
    chart_archive.build_session_charts(COG, FACE, HEART)
    assert seen["Cognitive signals"] == {"focus", "stress"}
