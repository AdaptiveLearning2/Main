"""The cohort panels: a class-wide signal trend and the roster behind it.

The aggregation itself is SQL, so what these cover is the composition around
it -- and that composition is where every distinction this surface depends on
can quietly collapse:

  * a failed read against a class that recorded nothing,
  * a student who declined a channel against one whose sensor produced nothing,
  * a bucket of one student weighted like a bucket of twenty,
  * a roster of four whose rows must never reach the response at all.

`scripts/assert_signal_rls.sql` is where the RPC's own arithmetic runs against
a real stack; the weighting asserted here is the merge *across* consent
buckets, which is Python and has no other cover.
"""
import os
from datetime import datetime, timezone

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402
from tests.test_access_control import _FakeSupabase  # noqa: E402

TEACHER = {"id": "teacher-1"}
OTHER_TEACHER = {"id": "teacher-2"}
CLASS = "class-1"
TREND_RPC = "class_signal_daily_trend"
TOTALS_RPC = "class_signal_student_totals"

NOW_UTC = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch):
    monkeypatch.setattr(main, "_utc_now", lambda: NOW_UTC)
    monkeypatch.setattr(main, "_retention_window", lambda: {
        "state": main.WINDOW_OPEN, "starts_on": "2000-01-01",
        "ends_on": "2099-12-31", "timezone": "UTC"})


@pytest.fixture(autouse=True)
def _teacher_owns_the_class(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: TEACHER)


def _student(n):
    return f"student-{n}"


def _consent(user, *, eeg=True, heart=True, camera=True):
    """A consent row.

    `heart=False` turns the camera off with the headband, because the heart
    channel is `headband_optical OR camera` -- the camera carries the rPPG
    fallback, so leaving it on keeps the channel readable and the student in
    the permitting bucket.
    """
    return {"user_id": user, "eeg_enabled": eeg,
            "headband_optical_enabled": heart,
            "camera_enabled": camera and heart}


def _tables(roster, consent=None, owner="teacher-1"):
    return {
        "classes": [{"id": CLASS, "teacher_id": owner}],
        "class_memberships": [{"class_id": CLASS, "student_id": s} for s in roster],
        "profiles": [{"id": s, "display_name": s.title(), "role": "student"}
                     for s in roster],
        "signal_consent": list(consent if consent is not None
                               else [_consent(s) for s in roster]),
    }


def _row(day="2026-06-11", channel="cognitive", focus=None, bpm=None,
         trusted=10, samples=None, students=1):
    """One (day, channel) row as the RPC returns it -- already weighted in SQL."""
    return {"day": day, "channel": channel,
            "avg_focus": focus, "avg_stress": None, "avg_engagement": None,
            "avg_heart_rate_bpm": bpm, "avg_rmssd_ms": None,
            "sample_count": samples if samples is not None else trusted,
            "trusted_sample_count": trusted, "student_count": students}


class _BucketedFake(_FakeSupabase):
    """A fake that answers the trend RPC per call rather than per name.

    The endpoint calls one RPC once per consent bucket, so a single canned
    result per name cannot express "this bucket returned these rows and that
    one returned those" -- which is exactly what the cross-bucket weighting
    has to be tested against.

    `trend_by_ids` maps a frozenset of student ids to the rows that call
    returns, or to an Exception to raise. Anything unmatched answers [].
    """

    def __init__(self, tables, trend_by_ids=None, **kw):
        super().__init__(tables, **kw)
        self._trend_by_ids = trend_by_ids or {}

    def rpc(self, name, params=None):
        params = params or {}
        if name == TREND_RPC and self._trend_by_ids:
            self.rpc_calls.append((name, params))
            answer = self._trend_by_ids.get(frozenset(params.get("p_student_ids") or []), [])
            if isinstance(answer, Exception):
                from tests.test_access_control import _Rpc
                return _Rpc([], answer)
            from tests.test_access_control import _Rpc
            return _Rpc(answer)
        return super().rpc(name, params)


def _trend_calls(fake):
    return [p for n, p in fake.rpc_calls if n == TREND_RPC]


def _totals_calls(fake):
    return [p for n, p in fake.rpc_calls if n == TOTALS_RPC]


# ─── the access check runs before any read ────────────────────────────────
#
# This endpoint reads signal data for a whole roster through the service-role
# client, which bypasses RLS. `_verify_class_owner` is the only thing between a
# teacher and another teacher's class, and it has to run before the roster is
# even resolved.

def test_a_non_owning_teacher_is_refused(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: OTHER_TEACHER)
    fake = _FakeSupabase(_tables([_student(1)]))
    monkeypatch.setattr(main, "supabase", fake)

    with pytest.raises(main.HTTPException) as e:
        main.class_cohort_signals(CLASS, None)
    assert e.value.status_code == 403
    # Refused before anything was read, not after the rows were fetched and
    # then withheld.
    assert not fake.rpc_calls


# ─── the min-N floor ──────────────────────────────────────────────────────
#
# The rows must never reach the response for an under-floor roster, which is
# what makes this a privacy property rather than a rendering preference. A
# client-side hide would put them in the payload for anyone reading it.

def test_the_per_student_rows_are_withheld_below_the_floor(monkeypatch):
    roster = [_student(n) for n in range(1, 5)]           # four
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _tables(roster), rpc_results={TREND_RPC: [_row(focus=0.5)]}))

    out = main.class_cohort_signals(CLASS, None)
    assert out["class_size"] == 4
    assert out["per_student"] is None
    assert out["min_students"] == main._COHORT_MIN_STUDENTS
    # The class-wide trend is still served -- it is the aggregate the floor
    # exists to protect, not another thing to withhold.
    assert out["series"]


def test_the_per_student_rows_appear_at_exactly_the_floor(monkeypatch):
    roster = [_student(n) for n in range(1, 6)]           # five
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _tables(roster), rpc_results={TREND_RPC: [_row(focus=0.5)]}))

    out = main.class_cohort_signals(CLASS, None)
    assert out["class_size"] == 5
    assert [r["student_id"] for r in out["per_student"]] == roster


def test_the_floor_counts_the_roster_not_the_students_who_recorded(monkeypatch):
    """A class of six where two wore a headband is still a class of six.

    Gating on the students who produced data would expose that pair at exactly
    the moment they are most identifiable.
    """
    roster = [_student(n) for n in range(1, 7)]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _tables(roster),
        # The summary RPC answers for two of the six; the rest recorded nothing.
        rpc_results={TREND_RPC: [_row(focus=0.5)],
                     TOTALS_RPC: [{"user_id": _student(1), "avg_focus": 0.6},
                                  {"user_id": _student(2), "avg_focus": 0.4}]}))

    out = main.class_cohort_signals(CLASS, None)
    assert len(out["per_student"]) == 6
    # And the four with nothing recorded still get a row, rather than being
    # dropped into a shorter list that reads as a smaller class.
    assert all(r["summary"] is not None for r in out["per_student"])


# ─── consent ──────────────────────────────────────────────────────────────

def test_students_are_bucketed_by_consent_flags_not_read_as_one_roster(monkeypatch):
    """One roster, two consent shapes, two calls -- never one call with a merged flag.

    A single call for the whole class would either read the declining student's
    heart rows under a classmate's permission, or hide the channel from every
    student who did permit it. The bucket is what avoids choosing.
    """
    permits, declines = _student(1), _student(2)
    fake = _BucketedFake(
        _tables([permits, declines],
                consent=[_consent(permits, heart=True),
                         _consent(declines, heart=False)]),
        trend_by_ids={frozenset([permits]): [_row(focus=0.5)],
                      frozenset([declines]): [_row(focus=0.5)]})
    monkeypatch.setattr(main, "supabase", fake)

    main.class_cohort_signals(CLASS, None)

    calls = _trend_calls(fake)
    assert len(calls) == 2, "the roster was not split by consent"
    by_ids = {frozenset(c["p_student_ids"]): c for c in calls}
    assert by_ids[frozenset([permits])]["p_include_heart"] is True
    assert by_ids[frozenset([declines])]["p_include_heart"] is False


def test_a_declined_channel_is_never_requested_for_that_student(monkeypatch):
    """Asserted on the call, not the payload.

    An absent heart average cannot tell "asked and found nothing" from "never
    asked", which is the whole distinction -- so a test reading only the
    response would pass against a version that read the rows and dropped them.
    """
    declines = _student(1)
    fake = _BucketedFake(
        _tables([declines], consent=[_consent(declines, heart=False, camera=False)]),
        trend_by_ids={frozenset([declines]): [_row(focus=0.5)]})
    monkeypatch.setattr(main, "supabase", fake)

    main.class_cohort_signals(CLASS, None)

    for params in _trend_calls(fake) + _totals_calls(fake):
        assert params["p_include_heart"] is False
        assert params["p_include_emotion"] is False


# ─── the cross-bucket merge ───────────────────────────────────────────────

def test_the_merge_weights_buckets_by_samples_not_by_bucket(monkeypatch):
    """Two buckets, one twenty times the other, merged on the same day.

    Averaging the two bucket means would answer 0.50; weighting on
    `trusted_sample_count` answers close to the larger bucket. The buckets
    exist because of consent, so a class where one student declined the camera
    would otherwise have that student's average count as much as everyone
    else's put together.
    """
    big = [_student(n) for n in range(1, 4)]
    small = [_student(9)]
    fake = _BucketedFake(
        _tables(big + small,
                consent=[_consent(s) for s in big] + [_consent(small[0], camera=False)]),
        trend_by_ids={
            frozenset(big): [_row(focus=0.8, trusted=400, students=3)],
            frozenset(small): [_row(focus=0.2, trusted=20, students=1)],
        })
    monkeypatch.setattr(main, "supabase", fake)

    out = main.class_cohort_signals(CLASS, None)

    day = [r for r in out["series"] if r["channel"] == "cognitive"][0]
    expected = (0.8 * 400 + 0.2 * 20) / 420
    assert day["avg_focus"] == pytest.approx(expected, abs=1e-4)
    assert day["avg_focus"] != pytest.approx(0.5, abs=1e-3), "averaged the averages"
    assert day["trusted_sample_count"] == 420
    # The buckets partition the roster, so the day's student count is their sum.
    assert day["student_count"] == 4


def test_a_null_average_contributes_no_weight_rather_than_a_zero(monkeypatch):
    """A day a channel measured nothing must not drag the class mean down.

    The row still exists -- the student had a session -- so its count is real
    while its average is null. Folding that count in with a zero would put the
    class average below every student in it.
    """
    measured, blank = _student(1), _student(2)
    fake = _BucketedFake(
        _tables([measured, blank],
                consent=[_consent(measured, heart=True), _consent(blank, heart=False)]),
        trend_by_ids={
            frozenset([measured]): [_row(focus=0.6, trusted=100)],
            frozenset([blank]): [_row(focus=None, trusted=100)],
        })
    monkeypatch.setattr(main, "supabase", fake)

    out = main.class_cohort_signals(CLASS, None)
    day = [r for r in out["series"] if r["channel"] == "cognitive"][0]
    assert day["avg_focus"] == pytest.approx(0.6)
    # The count is still the day's real coverage, which is what makes a thin
    # day legible beside a busy one.
    assert day["trusted_sample_count"] == 200


# ─── failed reads ─────────────────────────────────────────────────────────

def test_one_failed_bucket_marks_the_whole_trend_unretrieved(monkeypatch):
    """Never a partial series presented as complete.

    The payload carries one `retrieved` flag for the trend, so a merge of the
    buckets that succeeded would report the missing students as a quiet
    fortnight -- the one claim a failed read has not earned.
    """
    ok, broken = _student(1), _student(2)
    fake = _BucketedFake(
        _tables([ok, broken],
                consent=[_consent(ok, heart=True), _consent(broken, heart=False)]),
        trend_by_ids={frozenset([ok]): [_row(focus=0.5)],
                      frozenset([broken]): RuntimeError("PGRST202")})
    monkeypatch.setattr(main, "supabase", fake)

    out = main.class_cohort_signals(CLASS, None)
    assert out["retrieved"] is False
    assert out["series"] == [], "a partial series was served as the class's"


def test_a_failed_roster_read_is_not_an_empty_class(monkeypatch):
    fake = _FakeSupabase(_tables([_student(1)]),
                         table_raises={"class_memberships"},
                         rpc_results={TREND_RPC: []})
    monkeypatch.setattr(main, "supabase", fake)

    out = main.class_cohort_signals(CLASS, None)
    assert out["retrieved"] is False
    # `class_size: 0` beside `retrieved: false` reads as "we could not find
    # out"; beside `retrieved: true` it would be a genuinely empty class.
    assert out["class_size"] == 0


def test_a_failed_summary_read_does_not_blank_the_trend(monkeypatch):
    """The two halves fail independently.

    They are one request so they cannot disagree about when they were taken,
    but a broken summary RPC must still leave the class trend standing.
    """
    roster = [_student(n) for n in range(1, 6)]

    def _boom(name, _params):
        return RuntimeError("totals failed") if name == TOTALS_RPC else None

    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _tables(roster), rpc_results={TREND_RPC: [_row(focus=0.5)]},
        rpc_raises=_boom))

    out = main.class_cohort_signals(CLASS, None)
    assert out["retrieved"] is True and out["series"]
    assert out["summaries_retrieved"] is False
    # Rows are still built, carrying the flag -- a shorter list would read as a
    # smaller class, and an absent one as a roster below the floor.
    assert len(out["per_student"]) == 5


# ─── the window ───────────────────────────────────────────────────────────

def test_the_day_range_is_clamped_like_every_other_report_window(monkeypatch):
    fake = _FakeSupabase(_tables([_student(1)]), rpc_results={TREND_RPC: []})
    monkeypatch.setattr(main, "supabase", fake)

    out = main.class_cohort_signals(CLASS, None, days=9999)
    assert out["days"] == main._CLASS_TREND_MAX_DAYS
    assert _trend_calls(fake)[0]["p_days"] == main._CLASS_TREND_MAX_DAYS


def test_the_trend_is_bucketed_in_the_schools_timezone(monkeypatch):
    """Not UTC, so this panel's Tuesday is the accuracy trend's Tuesday."""
    monkeypatch.setattr(main, "_retention_window", lambda: {
        "state": main.WINDOW_OPEN, "starts_on": "2000-01-01",
        "ends_on": "2099-12-31", "timezone": "America/Los_Angeles"})
    fake = _FakeSupabase(_tables([_student(1)]), rpc_results={TREND_RPC: []})
    monkeypatch.setattr(main, "supabase", fake)

    out = main.class_cohort_signals(CLASS, None)
    assert _trend_calls(fake)[0]["p_timezone"] == "America/Los_Angeles"
    assert out["timezone"] == "America/Los_Angeles"


# ─── both panels read one source ──────────────────────────────────────────
#
# The roster used to come from `student_signal_summary_many`, which reads the
# per-sample tables. Beside a chart built on the rollup that is a pair which
# disagrees on a fixed date: `expire_signal_rows` deletes the per-sample rows
# at the end of a school year and leaves the rollup standing, so the panel pair
# would have shown a full term of class averages above a table reading
# "No sensor" for every student in it.

def test_the_roster_reads_the_rollup_and_never_the_per_sample_tables(monkeypatch):
    """Asserted on what was *not* read, which is the only way to see this.

    A payload built from the rollup and one built from the per-sample tables
    look identical while both sources hold the same data -- which is every day
    of a school year except the ones after expiry. So the test has to name the
    tables that must not be touched rather than check the numbers.
    """
    roster = [_student(n) for n in range(1, 6)]
    fake = _FakeSupabase(_tables(roster), rpc_results={
        TREND_RPC: [_row(focus=0.5)],
        TOTALS_RPC: [{"user_id": _student(1), "avg_focus": 0.6,
                      "cognitive_samples": 100, "days_recorded": 4}],
    })
    monkeypatch.setattr(main, "supabase", fake)

    out = main.class_cohort_signals(CLASS, None)

    called = {name for name, _ in fake.rpc_calls}
    assert TOTALS_RPC in called
    assert "student_signal_summary_many" not in called, \
        "the roster went back to the per-sample tables"
    for table in ("cognitive_signals", "heart_signals", "face_signals"):
        assert table not in fake.table_calls
    assert out["per_student"][0]["summary"]["focus"] == 0.6


def test_a_student_the_rollup_has_nothing_for_still_gets_a_row(monkeypatch):
    """And it says which of the two silences it is.

    A row of nulls with a zero count reads as "no sensor"; the same nulls with
    a nonzero count read as "calibrating". Dropping the student instead would
    make the class look smaller than it is.
    """
    roster = [_student(n) for n in range(1, 6)]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _tables(roster), rpc_results={
            TREND_RPC: [_row(focus=0.5)],
            TOTALS_RPC: [{"user_id": _student(1), "avg_focus": 0.6,
                          "cognitive_samples": 100, "days_recorded": 4}],
        }))

    out = main.class_cohort_signals(CLASS, None)
    assert len(out["per_student"]) == 5
    quiet = out["per_student"][1]["summary"]
    assert quiet["focus"] is None
    assert quiet["cognitive_samples"] == 0
    assert quiet["days_recorded"] == 0
    # The flags still describe the deployment rather than the absence.
    assert quiet["eeg_enabled"] is True
    assert quiet["retrieved"] is True


def test_the_roster_carries_days_recorded_rather_than_a_session_count(monkeypatch):
    """Sessions live in another table with another lifetime.

    Counting them here would put one column of this row back on the per-sample
    side of the expiry, which is the split this reads one source to remove.
    """
    roster = [_student(n) for n in range(1, 6)]
    fake = _FakeSupabase(_tables(roster), rpc_results={
        TREND_RPC: [_row(focus=0.5)],
        TOTALS_RPC: [{"user_id": _student(1), "days_recorded": 9}],
    })
    monkeypatch.setattr(main, "supabase", fake)

    out = main.class_cohort_signals(CLASS, None)
    assert out["per_student"][0]["summary"]["days_recorded"] == 9
    assert "sessions" not in out["per_student"][0]["summary"]
    assert "sessions" not in fake.table_calls


def test_the_per_student_read_is_bucketed_by_consent_like_the_trend(monkeypatch):
    """The second RPC is consent-agnostic too, so the bucketing has to hold for it.

    Asserted on the call rather than the payload: an absent heart average
    cannot tell "asked and found nothing" from "never asked".
    """
    permits, declines = _student(1), _student(2)
    fake = _BucketedFake(
        _tables([permits, declines],
                consent=[_consent(permits, heart=True), _consent(declines, heart=False)]),
        trend_by_ids={frozenset([permits]): [_row(focus=0.5)],
                      frozenset([declines]): [_row(focus=0.5)]})
    monkeypatch.setattr(main, "supabase", fake)

    main.class_cohort_signals(CLASS, None)

    calls = {frozenset(c["p_student_ids"]): c for c in _totals_calls(fake)}
    assert calls[frozenset([permits])]["p_include_heart"] is True
    assert calls[frozenset([declines])]["p_include_heart"] is False


# ─── what a failed bucket does to the roster ──────────────────────────────

def test_a_failed_trend_bucket_leaves_the_roster_unretrieved_too(monkeypatch):
    """Breaking out of the bucket loop skips every totals call after it.

    Those students were never asked about, so reporting them with zero counts
    and `retrieved: True` says "recorded nothing" -- which is the one thing the
    read did not establish. It renders as "No sensor", a fault indistinguishable
    from a class that left the headbands in the cupboard.
    """
    ok, broken = _student(1), _student(2)
    roster = [ok, broken] + [_student(n) for n in range(3, 7)]
    fake = _BucketedFake(
        _tables(roster,
                consent=[_consent(ok, heart=True), _consent(broken, heart=False)]
                        + [_consent(s) for s in roster[2:]]),
        trend_by_ids={frozenset([broken]): RuntimeError("PGRST202")})
    monkeypatch.setattr(main, "supabase", fake)

    out = main.class_cohort_signals(CLASS, None)

    assert out["retrieved"] is False
    assert out["summaries_retrieved"] is False, \
        "the roster claimed a read that never happened"
    # Every row still says so, since that flag is what the tile reads.
    for row in out["per_student"]:
        assert row["summary"]["retrieved"] is False


# ─── the roster's consent is one read, not one per student ────────────────

def test_consent_for_the_roster_is_read_in_one_query(monkeypatch):
    """A class of thirty was thirty sequential reads on a page load.

    `my_children` keeps a per-student loop and can -- a family has a handful of
    children. A roster is an order of magnitude larger, so the same shape is a
    different cost.
    """
    roster = [_student(n) for n in range(1, 7)]
    fake = _FakeSupabase(_tables(roster), rpc_results={TREND_RPC: [_row(focus=0.5)]})
    monkeypatch.setattr(main, "supabase", fake)

    main.class_cohort_signals(CLASS, None)

    assert fake.table_calls.count("signal_consent") == 1, \
        f"read consent {fake.table_calls.count('signal_consent')} times for 6 students"


def test_a_failed_consent_read_denies_every_student_rather_than_none(monkeypatch):
    """Fails closed for a roster exactly as `_consent` does for one student.

    The opposite direction would read channels nobody was confirmed to have
    agreed to, which is the one mistake this helper may not make.
    """
    roster = [_student(n) for n in range(1, 7)]
    fake = _FakeSupabase(_tables(roster), table_raises={"signal_consent"},
                         rpc_results={TREND_RPC: [_row(focus=0.5)]})
    monkeypatch.setattr(main, "supabase", fake)

    out = main.class_cohort_signals(CLASS, None)

    for row in out["per_student"]:
        s = row["summary"]
        assert s["consent_retrieved"] is False
        # Denied, not permitted -- and every channel, not just the ones a
        # partial row would have covered.
        assert s["heart_included"] is False
        assert s["emotion_included"] is False
        assert s["eeg_enabled"] is False
    # And nothing was read under a permission that could not be confirmed.
    for params in _trend_calls(fake) + _totals_calls(fake):
        assert params["p_include_heart"] is False
        assert params["p_include_emotion"] is False


def test_a_student_with_no_consent_row_is_denied_but_read_successfully(monkeypatch):
    """The two silences stay apart in the batch form.

    No row means nobody has consented yet, which the read established. A failed
    read means we could not find out. Both deny; only the first is a fact.
    """
    roster = [_student(n) for n in range(1, 7)]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        # Only student 1 has a row; the rest are absent.
        _tables(roster, consent=[_consent(_student(1))]),
        rpc_results={TREND_RPC: [_row(focus=0.5)]}))

    out = main.class_cohort_signals(CLASS, None)
    rows = {r["student_id"]: r["summary"] for r in out["per_student"]}
    assert rows[_student(2)]["consent_retrieved"] is True
    assert rows[_student(2)]["eeg_enabled"] is False
    assert rows[_student(1)]["eeg_enabled"] is True
