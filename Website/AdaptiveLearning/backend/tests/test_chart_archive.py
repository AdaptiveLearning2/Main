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
    """A table read: named columns only, `gt`, `order`, `limit`, and every response cut at
    `max_rows`, like PostgREST's `db-max-rows`. `on_execute(query)` runs after each read."""

    def __init__(self, rows, max_rows=1000, on_execute=None):
        self._rows = rows
        self._max_rows = max_rows
        self._on_execute = on_execute
        self.columns, self._gt, self._order, self._limit = None, [], [], None

    def select(self, spec="*", **_k):
        if "*" not in spec:
            self.columns = [c.strip() for c in spec.split(",")]
        return self

    def eq(self, *_a, **_k):
        return self

    def gt(self, col, val):
        self._gt.append((col, val))
        return self

    def gte(self, col, val):
        self._gt.append((col, val, "gte"))
        return self

    def order(self, col, **_k):
        self._order.append(col)
        return self

    def limit(self, n, **_k):
        self._limit = n
        return self

    def execute(self):
        if self._rows is None:              # an update, which returns nothing here
            return type("R", (), {"data": None})()
        rows = [r for r in self._rows
                if all(r.get(f[0]) is not None
                       and (r[f[0]] >= f[1] if len(f) == 3 else r[f[0]] > f[1])
                       for f in self._gt)]
        for col in reversed(self._order):
            rows = sorted(rows, key=lambda r, c=col: r.get(c))
        rows = rows[:min(n for n in (self._limit, self._max_rows) if n is not None)]
        if self.columns:
            rows = [{c: r[c] for c in self.columns if c in r} for r in rows]
        if self._on_execute:
            self._on_execute(self)
        return type("R", (), {"data": rows})()


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

    def remove(self, paths):
        for path in paths:
            self.uploaded.pop(path, None)
            self.removed.append(path)


class _Client:
    def __init__(self, cognitive=(), face=(), heart=(), fail_storage=False, on_execute=None,
                 erasures=(), erasure_read_fails=False):
        # Every stored row has an id, as the tables' own do.
        self._rows = {name: [r if "id" in r else {"id": i + 1, **r} for i, r in enumerate(rows)]
                      for name, rows in (("cognitive_signals", cognitive),
                                         ("face_signals", face), ("heart_signals", heart))}
        # `signal_erasure` rows: {"channel", "erased_at"}.
        self._rows["signal_erasure"] = list(erasures)
        self._erasure_read_fails = erasure_read_fails
        self.updates = []
        self.rpc_calls = []
        self.stored = None
        self.after_record = None
        self.reads = []
        self._storage = _Storage(fail=fail_storage)
        self._storage.removed = []
        self._on_execute = on_execute

    def rpc(self, name, params):
        """The two chart_paths RPCs, applied to `stored` as the SQL does; each write is also in
        `updates`. `after_record(stored)` runs between them, as a concurrent erasure would."""
        self.rpc_calls.append((name, params))
        if name == "record_chart_paths":
            self.stored = dict(params["p_paths"])
            if self.after_record:
                self.after_record(self.stored)
        else:
            assert name == "drop_chart_paths"
            self.stored.update({c: None for c in params["p_charts"] if c in self.stored})
        self.updates.append({"chart_paths": dict(self.stored)})
        result = dict(self.stored)
        return type("Q", (), {"execute": lambda _s: type("R", (), {"data": result})()})()

    def table(self, name):
        if name == "sessions":
            return type("T", (), {
                "update": lambda _s, values: _Update(self.updates, values)})()
        if name == "signal_erasure" and self._erasure_read_fails:
            raise RuntimeError("signal_erasure unavailable")
        query = _Query(self._rows[name], on_execute=self._on_execute)
        query.table = name
        self.reads.append(query)
        return query

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


def test_the_paths_are_written_through_the_locked_rpc_not_a_plain_update():
    """A plain update skips the per-student lock that keeps an erasure from being missed."""
    client = _Client(cognitive=COG)

    paths = chart_archive.archive_session(client, SESSION, USER)

    assert client.rpc_calls == [("record_chart_paths",
                                 {"p_session_id": SESSION, "p_paths": paths})]


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

# ── an erasure that lands while the archive runs ────────────────────────────

def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def test_an_erasure_during_the_archive_takes_its_charts_back_out():
    """It saw `chart_paths` NULL, so it removed nothing and nulled nothing; the archive must."""
    client = _Client(cognitive=COG, face=FACE, heart=HEART,
                     erasures=[{"channel": "camera", "erased_at": _now_iso()}])

    paths = chart_archive.archive_session(client, SESSION, USER)

    assert paths["cognitive_timeline"]
    assert paths["emotion_pie"] is None and paths["heart_rate"] is None
    assert paths["stress_pie"] is None
    assert client.updates[-1] == {"chart_paths": paths}
    assert sorted(client.bucket.removed) == sorted(
        chart_archive.object_path(USER, SESSION, n)
        for n in ("emotion_pie", "heart_rate", "stress_pie"))
    assert list(client.bucket.uploaded) == [paths["cognitive_timeline"]]


def test_dropping_charts_does_not_restore_one_a_second_erasure_just_nulled():
    """A whole-map write puts back the heart path this erasure removed; nulling by key does not."""
    client = _Client(cognitive=COG, face=FACE, heart=HEART,
                     erasures=[{"channel": "eeg", "erased_at": _now_iso()}])
    client.after_record = lambda stored: stored.update(heart_rate=None, stress_pie=None)

    paths = chart_archive.archive_session(client, SESSION, USER)

    assert client.stored["heart_rate"] is None and client.stored["stress_pie"] is None
    assert client.stored["cognitive_timeline"] is None
    assert client.rpc_calls[-1] == ("drop_chart_paths",
                                    {"p_session_id": SESSION, "p_charts": ["cognitive_timeline"]})
    assert paths == client.stored


def test_an_erasure_stamped_by_a_database_clock_running_behind_still_counts():
    """`erased_at` is the database's `now()`, not this process's clock."""
    from datetime import timedelta
    behind = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    client = _Client(cognitive=COG, erasures=[{"channel": "eeg", "erased_at": behind}])

    paths = chart_archive.archive_session(client, SESSION, USER)

    assert paths["cognitive_timeline"] is None


def test_an_erasure_long_before_the_archive_drops_nothing():
    """Its rows were already gone when the archive read, so what was drawn is clean."""
    client = _Client(cognitive=COG, face=FACE, heart=HEART,
                     erasures=[{"channel": "camera", "erased_at": "2020-01-01T00:00:00+00:00"}])

    paths = chart_archive.archive_session(client, SESSION, USER)

    assert all(paths[n] for n in chart_render.CHART_NAMES)
    assert client.bucket.removed == []


def test_an_unrecognised_erased_channel_takes_every_chart():
    client = _Client(cognitive=COG, face=FACE, heart=HEART,
                     erasures=[{"channel": "somethingnew", "erased_at": _now_iso()}])

    paths = chart_archive.archive_session(client, SESSION, USER)

    assert paths == {n: None for n in chart_render.CHART_NAMES}


def test_a_failed_erasure_check_withdraws_the_archive_so_the_catch_up_retries_it():
    """"Could not ask" is treated as erased; NULL, not four nulls, so the archive runs again."""
    client = _Client(cognitive=COG, face=FACE, heart=HEART, erasure_read_fails=True)

    with pytest.raises(RuntimeError):
        chart_archive.archive_session(client, SESSION, USER)

    assert client.updates[-1] == {"chart_paths": None}
    assert client.bucket.uploaded == {}
    assert len(client.bucket.removed) == len(chart_render.CHART_NAMES)


def test_a_failed_erasure_check_on_a_rerender_raises_and_keeps_its_paths():
    """Nulled, the re-render tool skips it and the catch-up never sees it: the last copy is gone."""
    existing = {n: chart_archive.object_path(USER, SESSION, n) for n in chart_render.CHART_NAMES}
    client = _Client(cognitive=COG, face=FACE, heart=HEART, erasure_read_fails=True)

    with pytest.raises(chart_archive.ErasureRecheckFailed) as caught:
        chart_archive.archive_session(client, SESSION, USER, only={"cognitive_timeline"},
                                      existing_paths=existing)

    assert client.updates == [{"chart_paths": existing}]
    assert client.bucket.removed == []
    # What someone needs to settle it by hand.
    assert (caught.value.session_id, caught.value.user_id) == (SESSION, USER)
    assert caught.value.charts == ["cognitive_timeline"]
    assert caught.value.since < datetime.now(timezone.utc)


def _latest_function(name: str) -> str:
    """The newest migration's definition of `name`, up to its closing `$$;`, with `--` comments
    stripped (a mention is not a use)."""
    import re
    from pathlib import Path
    migrations = sorted((Path(__file__).resolve().parents[4] / "supabase" / "migrations")
                        .glob("*.sql"))
    header = re.compile(r'CREATE\s+OR\s+REPLACE\s+FUNCTION\s+"?public"?\."?' + name + r'"?\s*\(')
    defining = [m for m in migrations if header.search(m.read_text("utf-8"))]
    sql = re.sub(r"--[^\n]*", "", defining[-1].read_text("utf-8"))
    start = header.search(sql).start()
    return sql[start:sql.index("$$;", sql.index("$$", start) + 2)]


def _latest_erase_signals() -> str:
    return _latest_function("erase_signals")


# Both functions must lock this one key, or they exclude nothing.
_LOCK_KEY = r"hashtextextended\(\s*'chart_paths:'\s*\|\|\s*{who}::text,\s*0\s*\)"


def test_an_erasure_and_the_archive_write_take_one_student_lock_in_opposite_modes():
    """Source check, stated: the race needs two live transactions, which the fake cannot give
    (assert_signal_rls.sql checks the held locks for real). Unlocked, an archive can write and
    re-check between the erasure's UPDATE of sessions and its commit."""
    import re
    erase = _latest_erase_signals()
    lock = re.search(r"pg_advisory_xact_lock\(\s*" + _LOCK_KEY.format(who="p_user_id"), erase)
    first_delete = re.search(r"\bDELETE\s+FROM\b", erase)
    assert lock and first_delete and lock.start() < first_delete.start()

    write = _latest_function("record_chart_paths")
    shared = re.search(r"pg_advisory_xact_lock_shared\(\s*" + _LOCK_KEY.format(who="owner"), write)
    update = re.search(r"UPDATE\s+sessions\s+SET\s+chart_paths", write)
    assert shared and update and shared.start() < update.start()
    # A row lock would hold up every answer and /end on the student's sessions.
    assert not re.search(r"\bFOR\s+(NO\s+KEY\s+)?UPDATE\b", erase)


def test_the_erasure_stamps_its_tombstone_when_it_finishes():
    """now() is the transaction's start; a slow erasure would stamp before the re-check window."""
    import re
    assert re.search(r"VALUES\s*\(\s*p_user_id,\s*p_channel,\s*clock_timestamp\(\)",
                     _latest_erase_signals())


def test_the_charts_an_erasure_takes_match_erase_signals():
    """Two copies of one mapping: the SQL deletes by it, this re-check drops by it."""
    import re
    sql = _latest_erase_signals()
    case = re.search(r"charts\s*:=\s*CASE\s+p_channel(.*?)END;", sql, re.S).group(1)
    arrays = dict(re.findall(r"WHEN\s+'(\w+)'\s+THEN\s+ARRAY\[([^\]]*)\]", case))
    other = re.search(r"ELSE\s+ARRAY\[([^\]]*)\]", case).group(1)

    def names(text):
        return set(re.findall(r"'(\w+)'", text))

    assert {ch: names(a) for ch, a in arrays.items()} == {
        ch: set(c) for ch, c in chart_archive.ERASURE_CHARTS.items() if ch in arrays}
    others = set(chart_archive.ERASURE_CHARTS) - set(arrays)
    assert others and all(set(chart_archive.ERASURE_CHARTS[ch]) == names(other) for ch in others)


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
    """A 45-minute lesson at 1 Hz is 2700 rows, past PostgREST's silent 1000-row cut."""
    rows = _long_session(2700)
    cognitive, _, _ = chart_archive._fetch(_Client(cognitive=rows), SESSION)
    assert [r["id"] for r in cognitive] == list(range(2700))


def test_the_row_cap_still_binds():
    rows = _long_session(chart_archive._ROW_CAP + 1500)
    cognitive, _, _ = chart_archive._fetch(_Client(cognitive=rows), SESSION)
    assert len(cognitive) == chart_archive._ROW_CAP


def test_session_review_reads_the_same_rows_the_archive_draws(monkeypatch):
    """The archive must be what the reviewer saw; asserted on returned rows, not source."""
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


def test_the_reader_names_its_columns_and_never_fetches_raw():
    """`raw` is ~1 KB a row and nothing reads it; at 20,000 rows it was tens of MB to a browser."""
    client = _Client(cognitive=[{**r, "raw": {"blob": "x" * 1000}} for r in COG],
                     face=[{**r, "raw": {}} for r in FACE], heart=[{**r, "raw": {}} for r in HEART])
    fetched = chart_archive._fetch(client, SESSION)

    assert {q.table for q in client.reads} == set(chart_archive.SIGNAL_COLUMNS)
    for query in client.reads:
        assert query.columns and "raw" not in query.columns, query.table
    assert not any("raw" in row for rows in fetched for row in rows)


def test_the_named_columns_draw_the_same_charts_as_every_column():
    """A column the archive draws on and the list leaves out would change a chart, not fail."""
    full = (COG, FACE, HEART)
    named = tuple([{k: r[k] for k in r if k in cols.replace(" ", "").split(",")} for r in rows]
                  for rows, cols in zip(full, chart_archive.SIGNAL_COLUMNS.values()))
    assert chart_archive.build_session_charts(*named) == chart_archive.build_session_charts(*full)


@pytest.mark.parametrize("change", ["insert", "delete"])
def test_a_row_written_or_deleted_mid_read_is_neither_repeated_nor_skipped(change):
    """Paged by position, a late row shifted every later page by one, repeating one row;
    a deleted one (an erasure, the expiry) shifted them back and skipped one."""
    rows = _long_session(2500)
    done = []

    def mid_read(query):
        if query.table == "cognitive_signals" and not done:
            done.append(1)
            if change == "insert":       # a late batch: an early timestamp, the next id
                rows.append({"id": 9999, "ts": rows[10]["ts"], "focus": 0.1, "stress": 0.1})
            else:
                rows.pop(5)

    client = _Client(cognitive=rows, on_execute=mid_read)
    client._rows["cognitive_signals"] = rows
    cognitive, _, _ = chart_archive._fetch(client, SESSION)

    ids = [r["id"] for r in cognitive]
    assert len(ids) == len(set(ids)), "a row came back twice"
    expected = set(range(1000, 2500)) | ({9999} if change == "insert" else set())
    assert expected <= set(ids), "a row past the first page was skipped"
    assert [r["ts"] for r in cognitive] == sorted(r["ts"] for r in cognitive)


def test_the_three_tables_are_read_at_the_same_time():
    """Asserted as an ordering, not a duration: each table's first read waits until all three
    have started, which a one-after-another reader can never reach."""
    import threading
    started = threading.Barrier(3, timeout=5)
    first = set()

    def wait_for_the_others(query):
        if query.table not in first:
            first.add(query.table)
            started.wait()

    client = _Client(cognitive=COG, face=FACE, heart=HEART, on_execute=wait_for_the_others)
    cognitive, face, heart = chart_archive._fetch(client, SESSION)
    assert (len(cognitive), len(face), len(heart)) == (len(COG), len(FACE), len(HEART))


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
        if name in ("class_memberships", "parent_child_links"):
            return _Query([])  # read, and empty: a viewer with no relationship, not a failed check
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
    # Every channel consented: a withdrawn one's charts are withheld (tested below).
    monkeypatch.setattr(main, "_consent", lambda _uid: {**main._CONSENT_ENABLED_ALL,
                                                        "retrieved": True, "exists": True})
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

    def spy(points, title, *args, **kwargs):
        seen[title] = set(points)
        return real(points, title, *args, **kwargs)

    monkeypatch.setattr(chart_render, "line_svg", spy)
    chart_archive.build_session_charts(COG, FACE, HEART)
    assert seen["Cognitive signals"] == {"focus", "stress"}


def test_the_archived_cognitive_chart_is_on_the_0_to_100_percent_axis():
    """Its rows are 0..1 ratios; session review plots them on a fixed [0, 1] domain."""
    from xml.etree import ElementTree
    svg = chart_archive.build_session_charts(COG, [], [])["cognitive_timeline"]
    labels = [t.text for t in ElementTree.fromstring(svg).iter()
              if t.tag.endswith("text") and t.get("text-anchor") == "end"]
    assert labels == ["100%", "0%"]


# ── the catch-up for archives that never ran ────────────────────────────────

class _SessionsQuery:
    """The catch-up's `sessions` read; records every filter, serves the given rows."""

    def __init__(self, rows, fail=False):
        self.rows, self.fail, self.calls = rows, fail, []

    def __getattr__(self, name):
        if name == "not_":
            self.calls.append(("not_",))
            return self

        def record(*args, **kwargs):
            self.calls.append((name, *args, *sorted(kwargs.items())))
            return self
        return record

    def execute(self):
        if self.fail:
            raise RuntimeError("sessions unavailable")
        return type("R", (), {"data": self.rows})()


class _CatchUpClient:
    def __init__(self, rows, fail=False):
        self.sessions = _SessionsQuery(rows, fail)

    def table(self, name):
        assert name == "sessions"
        return self.sessions


NOW = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)


def _queued(monkeypatch, expiry=(None, None)):
    monkeypatch.setattr(chart_archive, "_failed_at", {})
    queued = []
    monkeypatch.setattr(chart_archive, "schedule", lambda _c, sid, uid: queued.append((sid, uid)))
    monkeypatch.setattr(chart_archive, "expiry_cutoff", lambda _c: expiry)
    return queued


def test_the_catch_up_queues_closed_sessions_the_archive_never_reached(monkeypatch):
    queued = _queued(monkeypatch)
    client = _CatchUpClient([{"id": "s1", "user_id": "u1", "started_at": "2026-09-20T10:00:00Z"},
                             {"id": "s2", "user_id": "u2", "started_at": "2026-09-21T10:00:00Z"}])

    out = chart_archive.archive_missing(client, now=NOW)

    assert queued == [("s1", "u1"), ("s2", "u2")]
    assert out == {"found": 2, "queued": 2, "skipped_expired": 0, "retrieved": True}


def test_the_catch_up_asks_only_for_null_paths_on_closed_sessions_past_the_grace(monkeypatch):
    """Rule 4: assert on the request. A close's own archive gets the grace first."""
    _queued(monkeypatch)
    client = _CatchUpClient([])

    chart_archive.archive_missing(client, now=NOW)

    calls = client.sessions.calls
    assert ("is_", "chart_paths", "null") in calls
    assert ("not_",) in calls and ("is_", "ended_at", "null") in calls
    assert ("lt", "ended_at", (NOW - chart_archive.CATCH_UP_GRACE).isoformat()) in calls
    assert ("gt", "ended_at", (NOW - chart_archive.CATCH_UP_LOOKBACK).isoformat()) in calls
    assert ("order", "ended_at", ("desc", True)) in calls


def test_the_catch_up_leaves_a_session_begun_on_an_expired_day(monkeypatch):
    """Part of a session read back would be archived as all of it."""
    from datetime import date
    from zoneinfo import ZoneInfo
    queued = _queued(monkeypatch, expiry=(date(2026, 9, 20), ZoneInfo("UTC")))
    client = _CatchUpClient([{"id": "old", "user_id": "u1", "started_at": "2026-09-20T10:00:00Z"},
                             {"id": "new", "user_id": "u1", "started_at": "2026-09-21T10:00:00Z"}])

    out = chart_archive.archive_missing(client, now=NOW)

    assert queued == [("new", "u1")]
    assert out["skipped_expired"] == 1


def _fail_archive(monkeypatch, fail=True):
    def archive(*_a, **_k):
        if fail:
            raise RuntimeError("storage down")
        return {}
    monkeypatch.setattr(chart_archive, "archive_session", archive)


def test_an_archive_that_failed_here_is_left_out_of_the_next_passes(monkeypatch):
    """Else 20 that always fail fill every batch, newest first, and nothing older is reached."""
    _queued(monkeypatch)
    _fail_archive(monkeypatch)
    chart_archive._run(object(), "s-broken", "u1")
    client = _CatchUpClient([])

    chart_archive.archive_missing(client, now=NOW)

    assert ("in_", "id", ["s-broken"]) in client.sessions.calls


def test_a_failed_archive_is_tried_again_after_the_wait_and_forgotten_on_success(monkeypatch):
    _queued(monkeypatch)
    _fail_archive(monkeypatch)
    chart_archive._run(object(), "s-broken", "u1")
    real = chart_archive.time.monotonic
    monkeypatch.setattr(chart_archive.time, "monotonic",
                        lambda: real() + chart_archive.CATCH_UP_RETRY_AFTER_S)
    later = _CatchUpClient([])
    chart_archive.archive_missing(later, now=NOW)
    assert not any(c[0] == "in_" for c in later.sessions.calls)

    monkeypatch.setattr(chart_archive.time, "monotonic", real)
    chart_archive._run(object(), "s-broken", "u1")
    _fail_archive(monkeypatch, fail=False)
    chart_archive._run(object(), "s-broken", "u1")
    assert chart_archive._recent_failures() == []


def test_the_remembered_failures_are_bounded(monkeypatch):
    _queued(monkeypatch)
    _fail_archive(monkeypatch)
    for i in range(chart_archive._FAILED_MAX + 5):
        chart_archive._run(object(), f"s{i}", "u1")
    failed = chart_archive._recent_failures()
    assert len(failed) == chart_archive._FAILED_MAX
    assert "s0" not in failed and f"s{chart_archive._FAILED_MAX + 4}" in failed


def test_a_catch_up_that_cannot_list_says_so_and_queues_nothing(monkeypatch):
    queued = _queued(monkeypatch)

    out = chart_archive.archive_missing(_CatchUpClient([], fail=True), now=NOW)

    assert queued == [] and out["retrieved"] is False


# ── a withdrawn channel is not read for review, as the reports do not read it ─

def _consent_with(**flags):
    import main
    base = {**main._CONSENT_ENABLED_ALL, "retrieved": True, "exists": True}
    base.update(flags)
    return lambda _uid: base


def test_a_skipped_table_is_never_queried():
    """Rule 4: an empty list cannot tell "asked and got nothing" from "never asked"."""
    client = _Client(cognitive=COG, face=FACE, heart=HEART)
    cog, face, heart = chart_archive.read_session_signals(
        client, SESSION, skip=frozenset({"face_signals", "heart_signals"}))
    assert cog and face == [] and heart == []
    assert {q.table for q in client.reads} == {"cognitive_signals"}


def test_review_skips_withdrawn_channels_and_says_since_when(monkeypatch):
    import main
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": USER})
    monkeypatch.setattr(main, "_row_or_404", lambda *_a: {"user_id": USER})
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *_a: None)
    monkeypatch.setattr(main, "_consent", _consent_with(
        camera_enabled=False, camera_revoked_at="2026-08-20T09:00:00+00:00",
        headband_optical_enabled=False, headband_optical_revoked_at="2026-08-19T09:00:00+00:00"))
    asked = []
    monkeypatch.setattr(chart_archive, "read_session_signals",
                        lambda _c, _s, _since, skip=frozenset(): asked.append(skip) or ([{"ts": 1}], [], []))

    class _Answers:
        def table(self, _name):
            q = type("Q", (), {})()
            for m in ("select", "eq", "order"):
                setattr(q, m, lambda *a, **k: q)
            q.execute = lambda: type("R", (), {"data": []})()
            return q
    monkeypatch.setattr(main, "supabase", _Answers())

    out = main.session_signals(SESSION, None)
    assert asked == [frozenset({"face_signals", "heart_signals"})]
    assert out["channels"]["face_included"] is False and out["channels"]["heart_included"] is False
    assert out["channels"]["emotion_revoked_at"] == "2026-08-20T09:00:00+00:00"
    assert out["channels"]["heart_revoked_at"] == "2026-08-20T09:00:00+00:00"


def test_a_withdrawn_channels_archived_charts_are_never_signed(monkeypatch):
    """A signed URL cannot be revoked, so withholding has to happen before signing."""
    import main
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *_a: None)
    monkeypatch.setattr(main, "_consent", _consent_with(camera_enabled=False,
                                                        headband_optical_enabled=False))
    archiver = _SigningClient(cognitive=COG, heart=HEART, face=FACE)
    paths = chart_archive.archive_session(archiver, SESSION, USER)
    client = _SessionsClient({"user_id": USER, "chart_paths": paths})
    client._storage = archiver._storage
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": USER})
    monkeypatch.setattr(main, "supabase", client)

    payload = main.session_charts(SESSION, None)
    assert payload["withdrawn"] == ["emotion_pie", "heart_rate", "stress_pie"]
    assert payload["unchecked"] == []
    assert set(payload["charts"]) == {"cognitive_timeline"}
    signed = [path for path, _ttl in getattr(client.bucket, "signed", [])]
    assert signed and all(chart_archive.object_path(USER, SESSION, name) not in signed
                          for name in payload["withdrawn"])


def test_charts_withheld_for_unreadable_consent_are_not_called_withdrawn(monkeypatch):
    """Fail closed, but a failed read is not a decision anyone made."""
    import main
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *_a: None)
    monkeypatch.setattr(main, "_consent", _consent_with(retrieved=False, camera_enabled=False,
                                                        headband_optical_enabled=False))
    archiver = _SigningClient(cognitive=COG, heart=HEART, face=FACE)
    paths = chart_archive.archive_session(archiver, SESSION, USER)
    client = _SessionsClient({"user_id": USER, "chart_paths": paths})
    client._storage = archiver._storage
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": USER})
    monkeypatch.setattr(main, "supabase", client)

    payload = main.session_charts(SESSION, None)
    assert payload["withdrawn"] == []
    assert payload["unchecked"] == ["emotion_pie", "heart_rate", "stress_pie"]
    assert set(payload["charts"]) == {"cognitive_timeline"}
