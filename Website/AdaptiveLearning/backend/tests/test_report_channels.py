"""Which channels a report may read: consent decides, the viewer only narrows, siblings stay apart."""

import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402
from tests.test_access_control import _FakeSupabase, _ts, _weekly_channels  # noqa: E402

STUDENT = "student-1"
# Saved before conftest's autouse fixture replaces it.
_REAL_FEATURE_FLAGS = main._feature_flags


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
    assert _weekly_channels(fake) == {"cognitive"}
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
        face=[{"user_id": STUDENT, "ts": _ts(i), "emotion": e, "attention": 0.5,
               "emotion_trusted": True}
              for i, e in enumerate(["happy", "happy", "sad", "neutral"], start=1)]
        # Untrusted, so not counted: the distribution is trusted-only, as the rollup is.
        + [{"user_id": STUDENT, "ts": _ts(5), "emotion": "sad", "emotion_trusted": False}],
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
    # The batch form: the dashboard reads every child's consent at once.
    monkeypatch.setattr(main, "_reportable_channels_many",
                        lambda ids, want=True: {cid: main.ReportChannels(True, cid == "kid-yes", True)
                                                for cid in ids})
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

    assert "emotion" not in _weekly_channels(fake), "asked for a declined camera"
    assert "heart" in _weekly_channels(fake), "skipped a consented headband"
    assert "face_signals" not in fake.table_calls
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
    assert "emotion" in _weekly_channels(fake)
    # Camera consent still permits a heart reading -- from the camera.
    assert out["heart_included"] is True


def test_the_weekly_endpoint_says_when_eeg_was_withdrawn(monkeypatch):
    """SignalPanel's EEG tiles read these; without them a withdrawn channel read as on."""
    row = {**_consent_row(eeg=False), "eeg_revoked_at": "2026-09-03T09:00:00+00:00"}
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables(row)))
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "teacher-1"})
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *_a: None)
    monkeypatch.setattr(main, "_profile", lambda _s: {"display_name": "Kid"})

    out = main.student_weekly_report(STUDENT, None)

    assert out["eeg_enabled"] is False
    assert out["eeg_revoked_at"] == "2026-09-03T09:00:00+00:00"


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


def test_heart_readings_past_the_row_ceiling_are_all_counted(monkeypatch):
    """Aggregated in SQL, so a ceiling on table reads no longer cuts the heart week."""
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables(
        _consent_row(),
        heart=[{"user_id": STUDENT, "ts": _ts(i), "source": "muse_optics",
                "heart_rate_bpm": 60.0 + i, "trusted": True} for i in range(1, 6)],
    ), max_rows={"heart_signals": 2}))

    report = main._weekly_signal_report(STUDENT)
    assert report["sample_counts"]["heart"] == 5
    assert report["truncated"] is False
    assert report["highlights"]["heart_rate_bpm"] == 63.0


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
    monkeypatch.setattr(main, "_reportable_channels_many",
                        lambda ids, want=True: {cid: outcomes[cid] for cid in ids})
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


# ── an erasure is reported, so an erased past never reads as "No sensor" ──────

def _ago(days):
    from datetime import timedelta
    return (main._utc_now() - timedelta(days=days)).isoformat()


def _erasure(channel, at, user_id=STUDENT):
    return {"user_id": user_id, "channel": channel, "erased_at": at}


def _weekly(monkeypatch, tables, **fake_kw):
    fake = _FakeSupabase(tables, **fake_kw)
    monkeypatch.setattr(main, "supabase", fake)
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "teacher-1"})
    monkeypatch.setattr(main, "_verify_can_view_student", lambda *_a: None)
    monkeypatch.setattr(main, "_profile", lambda _s: {"display_name": "Kid"})
    return main.student_weekly_report(STUDENT, None), fake


def test_the_weekly_endpoint_says_which_channels_were_erased(monkeypatch):
    """Consent is still on after an erasure, so without these the tiles read "No sensor"."""
    hb, eeg = _ago(1), _ago(2)
    # The run's QA-6: headband heart consented, camera not.
    tables = {**_tables(_consent_row(camera=False)), "signal_erasure": [
        _erasure("headband_optical", hb),
        _erasure("eeg", eeg),
        # Another child's erasure is not this one's.
        _erasure("camera", _ago(1), user_id="someone-else"),
    ]}
    out, fake = _weekly(monkeypatch, tables)

    assert out["eeg_erased_at"] == eeg
    assert out["heart_erased_at"] == hb
    assert out["emotion_erased_at"] is None
    # Read for this student, by filter (rule 4).
    reads = [q for name, q in zip(fake.table_calls, fake.queries) if name == "signal_erasure"]
    assert reads and all(("user_id", STUDENT) in q.filters for q in reads), [q.filters for q in reads]


def test_an_erasure_before_the_window_took_nothing_from_it(monkeypatch):
    """An empty week after an old erasure is "No sensor", not the erasure's doing."""
    tables = {**_tables(_consent_row()), "signal_erasure": [
        _erasure("eeg", _ago(40)), _erasure("headband_optical", _ago(40)), _erasure("camera", _ago(40))]}
    out, _ = _weekly(monkeypatch, tables)
    assert (out["eeg_erased_at"], out["heart_erased_at"], out["emotion_erased_at"]) == (None, None, None)


@pytest.mark.parametrize("consent,erased,expect", [
    # Both sensors consented, both erased: the later one.
    (dict(headband=True, camera=True), {"headband_optical": 2, "camera": 1}, "camera"),
    # The camera erased, the headband still consented and unerased: its empty week is not explained.
    (dict(headband=True, camera=True), {"camera": 1}, None),
    # Only the headband consented, and erased.
    (dict(headband=True, camera=False), {"headband_optical": 1}, "headband_optical"),
    # An old camera erasure and a recent headband one: the camera's took nothing from this week.
    (dict(headband=True, camera=True), {"headband_optical": 1, "camera": 40}, None),
])
def test_heart_is_erased_only_when_every_consented_heart_sensor_was(monkeypatch, consent, erased, expect):
    stamps = {sensor: _ago(days) for sensor, days in erased.items()}
    tables = {**_tables(_consent_row(**consent)),
              "signal_erasure": [_erasure(s, at) for s, at in stamps.items()]}
    out, _ = _weekly(monkeypatch, tables)
    assert out["heart_erased_at"] == (stamps[expect] if expect else None)


def test_an_unreadable_erasure_table_reports_none_and_the_report_still_loads(monkeypatch):
    """Fails open, unlike consent: it only picks a tile's words."""
    out, _ = _weekly(monkeypatch, {**_tables(_consent_row()), "signal_erasure": []},
                     table_raises={"signal_erasure"})
    assert (out["eeg_erased_at"], out["heart_erased_at"], out["emotion_erased_at"]) == (None, None, None)
    assert out["eeg_enabled"] is True


def test_the_roster_reads_erasures_once_and_stamps_each_child_its_own(monkeypatch):
    at = _ago(1)
    fake = _FakeSupabase({
        "signal_consent": [_consent_row("kid-a"), _consent_row("kid-b")],
        "signal_erasure": [_erasure("eeg", at, user_id="kid-a")],
    })
    monkeypatch.setattr(main, "supabase", fake)

    channels = main._reportable_channels_many(["kid-a", "kid-b"])

    since = main._window_start(7)
    assert main._erased_fields(channels["kid-a"], since)["eeg_erased_at"] == at
    assert main._erased_fields(channels["kid-b"], since)["eeg_erased_at"] is None
    assert fake.table_calls.count("signal_erasure") == 1


def test_the_window_starts_at_school_midnight_as_the_report_does(monkeypatch):
    """A rolling UTC "now minus days" let an erasure up to a day before the window read as inside it.

    In a school zone behind UTC, so a start taken at UTC midnight is a different instant.
    """
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    monkeypatch.setattr(main, "_school_timezone", lambda: ZoneInfo("America/Los_Angeles"))
    # 20:00 on 7 October at the school, already the 8th in UTC.
    monkeypatch.setattr(main, "_utc_now", lambda: datetime(2026, 10, 8, 3, 0, tzinfo=timezone.utc))
    start = main._window_start(7)
    assert start == datetime(2026, 10, 1, 7, 0, tzinfo=timezone.utc)   # 1 October, 00:00 PDT
    ch = lambda at: main.ReportChannels(True, True, True, erasures={"eeg": at})  # noqa: E731
    assert main._erased_fields(ch((start - timedelta(minutes=1)).isoformat()), start)["eeg_erased_at"] is None
    inside = (start + timedelta(minutes=1)).isoformat()
    assert main._erased_fields(ch(inside), start)["eeg_erased_at"] == inside
    # The weekly report reads from this same start.
    fake = _FakeSupabase(_tables(_consent_row()))
    monkeypatch.setattr(main, "supabase", fake)
    main._weekly_signal_report(STUDENT, 7)
    sent = {p["p_since"] for name, p in fake.rpc_calls if name == "weekly_signal_days"}
    assert sent == {start.isoformat()}


def test_every_summary_payload_carries_the_erasure_fields_inside_its_window():
    """The signal-summary, cohort-roster and parent-dashboard payloads, beside `*_revoked_at`."""
    recent, old = _ago(2), _ago(40)
    ch = main.ReportChannels(True, True, True, erasures={"eeg": recent, "headband_optical": old, "camera": recent},
                             heart_sensors=("headband_optical",))
    want = {"eeg_erased_at": recent, "heart_erased_at": None, "emotion_erased_at": recent}
    since = main._window_start(30)
    row = main._cohort_student_row("kid-a", {}, ch, True, since)
    empty = main._shape_summary(None, erased=main._erased_fields(ch, since))
    full = main._shape_summary({"focus": 0.5}, erased=main._erased_fields(ch, since))
    for payload in (row, empty, full):
        assert {k: payload[k] for k in want} == want
    assert {k: main._shape_summary(None)[k] for k in want} == dict.fromkeys(want)


# ── an administrator's switch is a state of its own ──────────────────────────

def _switch_off(monkeypatch, *flags):
    off = {f"recording_{k}_enabled" for k in flags}
    monkeypatch.setattr(main, "_feature_flags", lambda: {
        k: {"enabled": k not in off, "bypass_until": None} for k in main._FEATURE_FLAG_DEFAULTS})


@pytest.mark.parametrize("switches,channels", [(("eeg",), ["eeg"]),
                                               (("camera",), ["emotion"]),
                                               # Heart has two sources: the camera's switch alone leaves one.
                                               (("heart",), []),
                                               (("heart", "camera"), ["heart", "emotion"])])
def test_a_switched_off_channel_is_named_in_both_report_payloads(monkeypatch, switches, channels):
    monkeypatch.setattr(main, "supabase", _FakeSupabase(_tables(_consent_row())))
    assert main._shape_summary(None)["paused_channels"] == []
    assert main._weekly_signal_report(STUDENT)["paused_channels"] == []

    _switch_off(monkeypatch, *switches)

    # Consent is on throughout: the switch is not a consent change.
    assert main._shape_summary({"focus": 0.5})["paused_channels"] == channels
    weekly = main._weekly_signal_report(STUDENT)
    assert weekly["paused_channels"] == channels and weekly["eeg_enabled"] is True


def test_a_channel_the_student_has_off_is_not_called_paused(monkeypatch):
    """A camera never consented to has nothing to pause; neither has a withdrawn headband."""
    _switch_off(monkeypatch, "eeg", "heart", "camera")

    got = main._shape_summary({"focus": 0.5}, include_heart=False, include_emotion=False,
                              eeg_enabled=False)

    assert got["paused_channels"] == []
    assert main._shape_summary({"focus": 0.5}, include_heart=False)["paused_channels"] == ["eeg", "emotion"]


def test_unreadable_flags_pause_nothing(monkeypatch):
    """The real reader answers its declared defaults (all on) on a failed read: no tile claims a pause."""
    class _Down:
        def table(self, _name):
            raise RuntimeError("flags down")
    monkeypatch.setattr(main, "supabase", _Down())
    monkeypatch.setattr(main, "_feature_flags", _REAL_FEATURE_FLAGS)
    main._feature_flags_cache_clear()

    assert main._paused_channels() == []
