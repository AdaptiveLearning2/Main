"""Which channels a report may read: consent decides, the viewer only narrows, siblings stay apart."""

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
    """Not read-then-null."""
    fake = _FakeSupabase(_tables(_consent_row(headband=False, camera=False)))
    monkeypatch.setattr(main, "supabase", fake)

    # By name, not by position: the NamedTuple has defaulted fields.
    channels = main._reportable_channels(STUDENT)
    assert (channels.heart, channels.emotion) == (False, False)

    main._weekly_signal_report(STUDENT, include_heart=channels.heart, include_emotion=channels.emotion)
    assert "heart_signals" not in fake.table_calls
    assert "face_signals" not in fake.table_calls


def test_a_viewer_cannot_widen_past_consent(monkeypatch):
    """The request flag is a preference, not an authorisation."""
    monkeypatch.setattr(main, "supabase",
                        _FakeSupabase(_tables(_consent_row(camera=False))))

    # Asking for facial data on a student who declined the camera.
    assert main._reportable_channels(STUDENT, want_emotion=True)[:2] == (True, False)


def test_a_viewer_can_narrow_within_consent(monkeypatch):
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables(_consent_row())))

    assert main._reportable_channels(STUDENT, want_emotion=False)[:2] == (True, False)
    assert main._reportable_channels(STUDENT, want_emotion=True)[:2] == (True, True)


def test_heart_consent_follows_either_sensor(monkeypatch):
    """Camera-only consent still permits a heart reading, from the camera."""
    monkeypatch.setattr(main, "supabase",
                        _FakeSupabase(_tables(_consent_row(headband=False, camera=True))))
    assert main._reportable_channels(STUDENT)[0] is True

    monkeypatch.setattr(main, "supabase",
                        _FakeSupabase(_tables(_consent_row(headband=False, camera=False))))
    assert main._reportable_channels(STUDENT)[0] is False


def test_unreadable_consent_reports_nothing(monkeypatch):
    """Fails closed, like `_consent` itself."""
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
    """The SQL aggregate applies the same rule, so the two surfaces agree."""
    report = _report_with_heart(monkeypatch, [
        {"user_id": STUDENT, "ts": _ts(1), "source": "muse_optics",
         "heart_rate_bpm": 72.0, "trusted": True},
        {"user_id": STUDENT, "ts": _ts(2), "source": "muse_optics",
         "heart_rate_bpm": 180.0, "trusted": False},
    ])
    assert report["highlights"]["heart_rate_bpm"] == 72.0


def test_a_null_trusted_flag_is_not_treated_as_trusted(monkeypatch):
    """`is True`, not truthiness."""
    report = _report_with_heart(monkeypatch, [
        {"user_id": STUDENT, "ts": _ts(1), "source": "muse_optics",
         "heart_rate_bpm": 200.0, "trusted": None},
    ])
    assert report["highlights"]["heart_rate_bpm"] is None


def test_the_report_names_which_sensor_produced_the_readings(monkeypatch):
    """Accuracy differs materially by source."""
    report = _report_with_heart(monkeypatch, [
        {"user_id": STUDENT, "ts": _ts(1), "source": "muse_optics",
         "heart_rate_bpm": 70.0, "trusted": True},
        {"user_id": STUDENT, "ts": _ts(2), "source": "muse_ppg",
         "heart_rate_bpm": 74.0, "trusted": True},
    ])
    assert report["heart_sources"] == ["muse_optics", "muse_ppg"]


def test_the_emotion_distribution_is_exposed_not_just_its_argmax(monkeypatch):
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
    """The batch RPC takes one flag pair per call, so children are grouped by consent."""
    calls = []

    def _fake_summaries(ids, days=7, include_heart=True, include_emotion=True,
                        channels_by_student=None):
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
    """Through the endpoint, where a swapped flag pair would read `face_signals`."""
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
    """Both suppress every optional channel; only one is a fault."""
    fake = _FakeSupabase(_tables(_consent_row()), table_raises={"signal_consent"})
    monkeypatch.setattr(main, "supabase", fake)
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "teacher-1"})
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *_a: None)
    monkeypatch.setattr(main, "_profile", lambda _s: {"display_name": "Kid"})

    out = main.student_weekly_report(STUDENT, None)

    assert out["consent_retrieved"] is False
    assert out["heart_included"] is False and out["emotion_included"] is False


def test_heart_truncation_sets_the_truncated_flag(monkeypatch):
    monkeypatch.setattr(main, "_REPORT_ROW_CAP", 2)
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables(
        _consent_row(),
        heart=[{"user_id": STUDENT, "ts": _ts(i), "source": "muse_optics",
                "heart_rate_bpm": 70.0, "trusted": True} for i in range(1, 6)],
    ), max_rows={"heart_signals": 2}))

    assert main._weekly_signal_report(STUDENT)["truncated"] is True


def test_untrusted_only_weeks_report_a_count_beside_a_null_average(monkeypatch):
    """"Measured, unusable" is not "sensor off": count is rows retrieved, average is rows trusted."""
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
    """The parent dashboard reads the summary, so `consent_retrieved` must reach it too."""
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
    """`consent_retrieved` doesn't change what is asked for; keying on it breaks the four-group bound."""
    calls = []

    def _fake_summaries(ids, days=7, include_heart=True, include_emotion=True,
                        channels_by_student=None):
        calls.append(sorted(ids))
        # Per-child stamping lives inside `_signal_summaries`; the map must reach the batch.
        return {str(i): {
            "face_included": include_emotion,
            "consent_retrieved": (channels_by_student or {})[i].consent_retrieved,
        } for i in ids}

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
    """bpm, not a 0..1 ratio: scaled by 100 like the others, 72 bpm would draw at 7200%."""
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
    """`heart_retrieved: True` beside a null average is "measured, unusable"."""
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


def test_a_nan_score_is_dropped_rather_than_stored_as_full_engagement(monkeypatch):
    """`min(1.0, nan)` is 1.0, and stdlib json parses NaN, so a sidecar can send one."""
    import signal_mapping

    row = signal_mapping.map_eeg_to_cognitive(
        {"features": {"focus_score": float("nan"), "calm_score": float("inf"),
                      "confidence": float("-inf")}}, "s", "u")

    assert row["focus"] is None
    assert row["engagement"] is None
    assert row["stress"] is None, "an infinite calm became a confident zero stress"


def test_a_capped_heart_read_does_not_blank_the_days_that_came_back(monkeypatch):
    """Coverage is per day, like the other three tables."""
    monkeypatch.setattr(main, "_REPORT_ROW_CAP", 2)
    rows = [{"user_id": STUDENT, "ts": _ts(i), "source": "muse_optics",
             "heart_rate_bpm": 70.0, "trusted": True} for i in range(1, 5)]
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _tables(_consent_row(), heart=rows), max_rows={"heart_signals": 2}))

    report = main._weekly_signal_report(STUDENT)
    whole = [d for d in report["daily"] if d["heart_retrieved"] is True]

    assert whole, "every day was marked unretrieved because one table was capped"


def test_a_day_with_only_heart_data_is_not_dropped(monkeypatch):
    """The skip guard must count heart, or a heart-only day vanishes from `daily`."""
    fake = _FakeSupabase(
        _tables(_consent_row(),
                heart=[{"user_id": STUDENT, "ts": _ts(1), "source": "muse_optics",
                        "heart_rate_bpm": 71.0, "trusted": True}]),
        table_raises={"cognitive_signals", "sessions"})
    monkeypatch.setattr(main, "supabase", fake)

    report = main._weekly_signal_report(STUDENT)
    days = [d for d in report["daily"] if d["heart_rate_bpm"] is not None]

    assert days, "the day was dropped despite a successful heart read"
    assert days[0]["heart_rate_bpm"] == 71.0


def _revoked(headband_at, camera_at):
    """Both heart sensors off, each with its own revocation stamp."""
    return _tables({"user_id": STUDENT, "eeg_enabled": True,
                    "headband_optical_enabled": False, "camera_enabled": False,
                    "headband_optical_revoked_at": headband_at,
                    "camera_revoked_at": camera_at})


def test_the_heart_revocation_date_is_the_later_of_the_two_sensors(monkeypatch):
    """Heart stopped when the *second* sensor went off."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _revoked("2026-08-01T10:00:00+00:00", "2026-08-05T09:00:00+00:00")))

    channels = main._reportable_channels(STUDENT)

    assert channels.heart is False
    assert channels.heart_revoked_at == "2026-08-05T09:00:00+00:00"


def test_the_later_instant_wins_when_only_the_spelling_separates_them(monkeypatch):
    """Compared as instants, not text: these differ only in `.` vs `Z`, so a string max picks wrong."""
    later   = "2026-08-05T09:00:00.500000+00:00"   # the true maximum
    earlier = "2026-08-05T09:00:00Z"               # wins a string comparison
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_revoked(later, earlier)))

    assert main._reportable_channels(STUDENT).heart_revoked_at == later


def test_an_unparseable_stamp_does_not_outrank_a_real_one(monkeypatch):
    monkeypatch.setattr(main, "supabase", _FakeSupabase(
        _revoked("not-a-timestamp", "2026-08-01T10:00:00+00:00")))

    channels = main._reportable_channels(STUDENT)

    assert channels.heart_revoked_at == "2026-08-01T10:00:00+00:00"


class _SummaryRpc:
    """A supabase whose only job is to answer the batch summary RPC."""

    def __init__(self, rows):
        self.rows = rows

    def rpc(self, _name, _params):
        rows = self.rows

        class _R:
            def execute(self):
                return type("X", (), {"data": rows})()

        return _R()


def test_the_batch_summary_stamps_each_childs_own_consent(monkeypatch):
    """The RPC groups by flag pair, so per-child revocation dates are stamped from the map."""
    monkeypatch.setattr(main, "supabase", _SummaryRpc([
        {"student_id": "kid-a", "focus": 0.5},
        {"student_id": "kid-b", "focus": 0.4},
    ]))
    monkeypatch.setattr(main, "_retention_window", lambda: {"timezone": "UTC"})

    out = main._signal_summaries(
        ["kid-a", "kid-b"],
        channels_by_student={
            # Headband switched off on the 5th; camera still on.
            "kid-a": main.ReportChannels(True, True, True, eeg=False,
                                         eeg_revoked_at="2026-08-05T09:00:00Z"),
            "kid-b": main.ReportChannels(True, True, True),
        })

    assert out["kid-a"]["eeg_enabled"] is False
    assert out["kid-a"]["eeg_revoked_at"] == "2026-08-05T09:00:00Z"
    # The sibling in the same RPC call is untouched: per child, not per group.
    assert out["kid-b"]["eeg_enabled"] is True
    assert out["kid-b"]["eeg_revoked_at"] is None


def test_the_batch_summary_without_a_consent_map_still_returns_a_payload(monkeypatch):
    """The map is optional: omitted, the defaults stand."""
    monkeypatch.setattr(main, "supabase",
                        _SummaryRpc([{"student_id": "kid-a", "focus": 0.5}]))
    monkeypatch.setattr(main, "_retention_window", lambda: {"timezone": "UTC"})

    out = main._signal_summaries(["kid-a"])

    assert out["kid-a"]["eeg_enabled"] is True
    assert out["kid-a"]["eeg_revoked_at"] is None
    assert out["kid-a"]["consent_retrieved"] is True
