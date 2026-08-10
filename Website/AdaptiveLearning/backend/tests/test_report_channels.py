"""Which channels a report is allowed to read, now that there are two optional ones.

`include_face` was one flag for one channel. Heart and emotion are consented
separately -- a student may permit the headband and refuse the camera, or the
reverse -- so the reporting surfaces need two, and they are not the viewer's to
set. Consent decides what may be read; the request flag only narrows further.

The properties worth protecting: a declined channel is **never queried**, a
viewer cannot widen past consent, and one sibling's refusal cannot suppress
another's data.
"""

import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402
from tests.test_access_control import _FakeSupabase, _ts  # noqa: E402

STUDENT = "student-1"


def _consent_row(user_id=STUDENT, eeg=True, headband=True, camera=True):
    return {"user_id": user_id, "eeg_enabled": eeg,
            "headband_optical_enabled": headband, "camera_enabled": camera}


def _tables(consent, **rows):
    return {
        "signal_consent": consent if isinstance(consent, list) else [consent],
        "cognitive_signals": rows.get("cog", []),
        "face_signals": rows.get("face", []),
        "heart_signals": rows.get("heart", []),
        "sessions": rows.get("sessions", []),
    }


# ── consent decides, the viewer only narrows ─────────────────────────────────

def test_a_declined_channel_is_never_queried(monkeypatch):
    """Not read-then-null. An unfetched row cannot reach a report by a later
    edit, and a stale row written before a withdrawal cannot resurface."""
    fake = _FakeSupabase(_tables(_consent_row(headband=False, camera=False)))
    monkeypatch.setattr(main, "supabase", fake)

    heart, emotion, _ = main._reportable_channels(STUDENT)
    assert (heart, emotion) == (False, False)

    main._weekly_signal_report(STUDENT, include_heart=heart, include_emotion=emotion)
    assert "heart_signals" not in fake.table_calls
    assert "face_signals" not in fake.table_calls


def test_a_viewer_cannot_widen_past_consent(monkeypatch):
    """The request flag is a preference, not an authorisation."""
    monkeypatch.setattr(main, "supabase",
                        _FakeSupabase(_tables(_consent_row(camera=False))))

    # Asking for facial data on a student who declined the camera.
    assert main._reportable_channels(STUDENT, want_emotion=True)[:2] == (True, False)


def test_a_viewer_can_narrow_within_consent(monkeypatch):
    """The existing opt-out still works where consent permits the channel."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables(_consent_row())))

    assert main._reportable_channels(STUDENT, want_emotion=False)[:2] == (True, False)
    assert main._reportable_channels(STUDENT, want_emotion=True)[:2] == (True, True)


def test_heart_consent_follows_either_sensor(monkeypatch):
    """One channel, two sensors. Camera-only consent still permits a heart
    reading -- from the camera."""
    monkeypatch.setattr(main, "supabase",
                        _FakeSupabase(_tables(_consent_row(headband=False, camera=True))))
    assert main._reportable_channels(STUDENT)[0] is True

    monkeypatch.setattr(main, "supabase",
                        _FakeSupabase(_tables(_consent_row(headband=False, camera=False))))
    assert main._reportable_channels(STUDENT)[0] is False


def test_unreadable_consent_reports_nothing(monkeypatch):
    """Fails closed, like `_consent` itself. A consent table that is down must
    not become a licence to read every channel."""
    monkeypatch.setattr(main, "supabase",
                        _FakeSupabase({}, table_raises={"signal_consent"}))
    assert main._reportable_channels(STUDENT)[:2] == (False, False)


# ── what the report now carries ──────────────────────────────────────────────

def _report_with_heart(monkeypatch, heart_rows):
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables(
        _consent_row(),
        cog=[{"user_id": STUDENT, "ts": _ts(1), "focus": 0.7, "stress": 0.3,
              "engagement": 0.6}],
        heart=heart_rows,
    )))
    return main._weekly_signal_report(STUDENT)


def test_an_untrusted_heart_sample_never_moves_the_average(monkeypatch):
    """It carries a rate; it is just not one worth putting in front of a parent.
    The same rule runs in the SQL aggregate so the two surfaces cannot
    disagree."""
    report = _report_with_heart(monkeypatch, [
        {"user_id": STUDENT, "ts": _ts(1), "source": "muse_optics",
         "heart_rate_bpm": 72.0, "trusted": True},
        {"user_id": STUDENT, "ts": _ts(2), "source": "muse_optics",
         "heart_rate_bpm": 180.0, "trusted": False},
    ])
    assert report["highlights"]["heart_rate_bpm"] == 72.0


def test_a_null_trusted_flag_is_not_treated_as_trusted(monkeypatch):
    """Rows written before the producer set the column must not slip in on
    falsiness. `is True`, not truthiness."""
    report = _report_with_heart(monkeypatch, [
        {"user_id": STUDENT, "ts": _ts(1), "source": "muse_optics",
         "heart_rate_bpm": 200.0, "trusted": None},
    ])
    assert report["highlights"]["heart_rate_bpm"] is None


def test_the_report_names_which_sensor_produced_the_readings(monkeypatch):
    """Accuracy differs materially by source, and after Phase 4 only the
    headband is validated at all."""
    report = _report_with_heart(monkeypatch, [
        {"user_id": STUDENT, "ts": _ts(1), "source": "muse_optics",
         "heart_rate_bpm": 70.0, "trusted": True},
        {"user_id": STUDENT, "ts": _ts(2), "source": "muse_ppg",
         "heart_rate_bpm": 74.0, "trusted": True},
    ])
    assert report["heart_sources"] == ["muse_optics", "muse_ppg"]


def test_the_emotion_distribution_is_exposed_not_just_its_argmax(monkeypatch):
    """Nothing on the frontend could render a pie before this: emotion reached
    it only as a scalar `dominant_emotion`."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables(
        _consent_row(),
        cog=[{"user_id": STUDENT, "ts": _ts(1), "focus": 0.7, "stress": 0.3,
              "engagement": 0.6}],
        face=[{"user_id": STUDENT, "ts": _ts(i), "emotion": e, "attention": 0.5}
              for i, e in enumerate(["happy", "happy", "sad", "neutral"], start=1)],
    )))
    report = main._weekly_signal_report(STUDENT)

    assert report["emotion_distribution"] == {"happy": 2, "neutral": 1, "sad": 1}
    assert report["highlights"]["dominant_emotion"] == "happy"


def test_an_excluded_channel_reports_none_rather_than_an_empty_tally(monkeypatch):
    """`{}` would read as "the camera saw nothing"; None says it was not read."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables(_consent_row())))
    report = main._weekly_signal_report(STUDENT, include_heart=False,
                                        include_emotion=False)

    assert report["emotion_distribution"] is None
    assert report["heart_sources"] is None
    assert report["emotion_included"] is False
    assert report["heart_included"] is False


# ── the parent dashboard, where consent differs per child ────────────────────

def test_one_childs_refusal_does_not_suppress_a_siblings_data(monkeypatch):
    """The batch RPC takes one flag pair for the whole call, so a single pair
    would either read a channel one child declined or hide one the other
    permitted. Grouped by consent instead."""
    calls = []

    def _fake_summaries(ids, days=7, include_heart=True, include_emotion=True):
        calls.append((sorted(ids), include_heart, include_emotion))
        return {str(i): {"face_included": include_emotion} for i in ids}

    monkeypatch.setattr(main, "_signal_summaries", _fake_summaries)
    monkeypatch.setattr(main, "_reportable_channels",
                        lambda cid, want=True: main.ReportChannels(True, cid == "kid-yes", True))
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "parent-1"})
    monkeypatch.setattr(main, "_profile", lambda _c: {})
    monkeypatch.setattr(main, "supabase", _FakeSupabase({
        "parent_child_links": [
            {"parent_id": "parent-1", "child_id": "kid-yes", "created_at": _ts(1)},
            {"parent_id": "parent-1", "child_id": "kid-no", "created_at": _ts(1)},
        ],
        "user_stats": [], "sessions": [], "user_math_performance": [],
    }))

    main.my_children(None)

    groups = {(h, e): ids for ids, h, e in calls}
    assert groups[(True, True)] == ["kid-yes"]
    assert groups[(True, False)] == ["kid-no"], (
        "the sibling who declined the camera was read with it enabled"
    )


# ── the endpoint, not just the helper ────────────────────────────────────────

def test_the_weekly_endpoint_does_not_read_a_declined_camera(monkeypatch):
    """The regression this file was missing.

    Every consent test above calls `_weekly_signal_report` directly with
    keywords, so none of them went through the endpoint -- where the resolved
    pair was unpacked with `*reversed(...)` and the two flags arrived swapped.
    A student who allowed the headband and declined the camera therefore had
    `face_signals` read for them, which is the exact violation this whole change
    exists to prevent, and it was invisible whenever both flags agreed.
    """
    fake = _FakeSupabase(_tables(_consent_row(headband=True, camera=False)))
    monkeypatch.setattr(main, "supabase", fake)
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "teacher-1"})
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *_a: None)
    monkeypatch.setattr(main, "_profile", lambda _s: {"display_name": "Kid"})

    out = main.student_weekly_report(STUDENT, None)

    assert "face_signals" not in fake.table_calls, "read a declined camera"
    assert "heart_signals" in fake.table_calls, "skipped a consented headband"
    assert out["emotion_included"] is False
    assert out["heart_included"] is True


def test_the_weekly_endpoint_reads_a_declined_headband_neither_way(monkeypatch):
    """The mirror, so the test above cannot pass on a coin flip."""
    fake = _FakeSupabase(_tables(_consent_row(headband=False, camera=True)))
    monkeypatch.setattr(main, "supabase", fake)
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "teacher-1"})
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *_a: None)
    monkeypatch.setattr(main, "_profile", lambda _s: {"display_name": "Kid"})

    out = main.student_weekly_report(STUDENT, None)

    assert out["emotion_included"] is True
    assert "face_signals" in fake.table_calls
    # Camera consent still permits a heart reading -- from the camera.
    assert out["heart_included"] is True


def test_a_failed_consent_read_is_not_reported_as_a_refusal(monkeypatch):
    """"We could not find out" and "the student declined" both suppress every
    optional channel, and only one of them is a fault."""
    fake = _FakeSupabase(_tables(_consent_row()), table_raises={"signal_consent"})
    monkeypatch.setattr(main, "supabase", fake)
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "teacher-1"})
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *_a: None)
    monkeypatch.setattr(main, "_profile", lambda _s: {"display_name": "Kid"})

    out = main.student_weekly_report(STUDENT, None)

    assert out["consent_retrieved"] is False
    assert out["heart_included"] is False and out["emotion_included"] is False


def test_heart_truncation_sets_the_truncated_flag(monkeypatch):
    """A capped heart read reported truncated: False -- the same failure the
    comment beside that flag documents for the other tables."""
    monkeypatch.setattr(main, "_REPORT_ROW_CAP", 2)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables(
        _consent_row(),
        heart=[{"user_id": STUDENT, "ts": _ts(i), "source": "muse_optics",
                "heart_rate_bpm": 70.0, "trusted": True} for i in range(1, 6)],
    ), max_rows={"heart_signals": 2}))

    assert main._weekly_signal_report(STUDENT)["truncated"] is True


def test_untrusted_only_weeks_report_a_count_beside_a_null_average(monkeypatch):
    """Three states, not two: "measured, unusable" must not read as "the sensor
    was off". The count is rows retrieved; the average is rows trusted."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables(
        _consent_row(),
        heart=[{"user_id": STUDENT, "ts": _ts(1), "source": "muse_optics",
                "heart_rate_bpm": 190.0, "trusted": False}],
    )))
    report = main._weekly_signal_report(STUDENT)

    assert report["sample_counts"]["heart"] == 1
    assert report["highlights"]["heart_rate_bpm"] is None
    assert report["heart_sources"] == [], (
        "a source whose every sample was rejected was listed as if it worked"
    )


def test_the_summary_payload_also_distinguishes_declined_from_unknown(monkeypatch):
    """The parent dashboard is the surface that renders this, and it reads the
    summary rather than the weekly report -- so `consent_retrieved` has to reach
    both. `retrieved` is about the aggregate query; this is about the consent
    read that decided which channels it could ask for."""
    fake = _FakeSupabase(_tables(_consent_row()), table_raises={"signal_consent"})
    monkeypatch.setattr(main, "supabase", fake)
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "teacher-1"})
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *_a: None)

    out = main.student_signal_summary(STUDENT, None)
    assert out["consent_retrieved"] is False

    # A genuine refusal, read successfully, must not look the same.
    monkeypatch.setattr(main, "supabase",
                        _FakeSupabase(_tables(_consent_row(headband=False, camera=False))))
    assert main.student_signal_summary(STUDENT, None)["consent_retrieved"] is True


def test_children_are_grouped_on_the_flags_not_the_consent_outcome(monkeypatch):
    """Two children with identical flags but different consent-read outcomes
    belong in one RPC call -- `consent_retrieved` does not change what is asked
    for, and keying on it would break the "at most four groups" bound."""
    calls = []

    def _fake_summaries(ids, days=7, include_heart=True, include_emotion=True):
        calls.append(sorted(ids))
        return {str(i): {"face_included": include_emotion} for i in ids}

    outcomes = {"kid-a": main.ReportChannels(False, False, True),    # declined
                "kid-b": main.ReportChannels(False, False, False)}   # unreadable
    monkeypatch.setattr(main, "_signal_summaries", _fake_summaries)
    monkeypatch.setattr(main, "_reportable_channels", lambda cid, want=True: outcomes[cid])
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "parent-1"})
    monkeypatch.setattr(main, "_profile", lambda _c: {})
    monkeypatch.setattr(main, "supabase", _FakeSupabase({
        "parent_child_links": [
            {"parent_id": "parent-1", "child_id": "kid-a", "created_at": _ts(1)},
            {"parent_id": "parent-1", "child_id": "kid-b", "created_at": _ts(1)},
        ],
        "user_stats": [], "sessions": [], "user_math_performance": [],
    }))

    children = main.my_children(None)

    assert len(calls) == 1, f"identical flags split into {len(calls)} round-trips"
    # ...and the distinction survives anyway, stamped per child.
    by_id = {c["user_id"]: c["signal_summary"] for c in children}
    assert by_id["kid-a"]["consent_retrieved"] is True
    assert by_id["kid-b"]["consent_retrieved"] is False


def test_daily_buckets_carry_heart_in_absolute_units(monkeypatch):
    """The chart needs a per-day series, and it is **not** a 0..1 ratio.

    Every other daily metric is a ratio the frontend multiplies by 100. Feeding
    these through the same scaling would draw a 72 bpm day at 7200%, which is
    why they are named for their unit and get their own axis.
    """
    day = _ts(1)[:10]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables(
        _consent_row(),
        heart=[{"user_id": STUDENT, "ts": _ts(1), "source": "muse_optics",
                "heart_rate_bpm": 70.0, "rmssd_ms": 40.0, "trusted": True},
               {"user_id": STUDENT, "ts": _ts(1), "source": "muse_optics",
                "heart_rate_bpm": 74.0, "rmssd_ms": 44.0, "trusted": True},
               # Rejected: must not move the day's average.
               {"user_id": STUDENT, "ts": _ts(1), "source": "muse_optics",
                "heart_rate_bpm": 190.0, "rmssd_ms": 5.0, "trusted": False}],
    )))
    report = main._weekly_signal_report(STUDENT)
    bucket = next(d for d in report["daily"] if d["date"] == day)

    assert bucket["heart_rate_bpm"] == 72.0
    assert bucket["rmssd_ms"] == 42.0
    assert bucket["heart_retrieved"] is True


def test_a_day_of_only_untrusted_samples_is_null_not_absent(monkeypatch):
    """`heart_retrieved: True` beside a null average is "measured, unusable".
    A gap alone would read as the sensor being off."""
    day = _ts(1)[:10]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables(
        _consent_row(),
        cog=[{"user_id": STUDENT, "ts": _ts(1), "focus": 0.7, "stress": 0.3,
              "engagement": 0.6}],
        heart=[{"user_id": STUDENT, "ts": _ts(1), "source": "muse_optics",
                "heart_rate_bpm": 190.0, "trusted": False}],
    )))
    bucket = next(d for d in main._weekly_signal_report(STUDENT)["daily"]
                  if d["date"] == day)

    assert bucket["heart_rate_bpm"] is None
    assert bucket["heart_retrieved"] is True


def test_an_excluded_heart_channel_leaves_the_daily_series_null(monkeypatch):
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables(
        _consent_row(),
        cog=[{"user_id": STUDENT, "ts": _ts(1), "focus": 0.7, "stress": 0.3,
              "engagement": 0.6}],
    )))
    bucket = main._weekly_signal_report(STUDENT, include_heart=False)["daily"][0]

    assert bucket["heart_rate_bpm"] is None
    assert bucket["heart_retrieved"] is None, "an opt-out is not a failed read"
