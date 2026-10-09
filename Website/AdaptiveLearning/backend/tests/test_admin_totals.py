"""The admin console's read-only totals: the funnel, consent changes by week, and signal quality."""

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402


class _Rpc:
    def __init__(self, owner, name, params):
        self.owner, self.name, self.params = owner, name, params

    def execute(self):
        self.owner.calls.append((self.name, self.params))
        result = self.owner.results.get(self.name)
        if isinstance(result, Exception):
            raise result
        return type("R", (), {"data": result})()


class _Db:
    """PostgREST's RPC answers: a jsonb object or array comes back as that value."""

    def __init__(self, **results):
        self.results, self.calls = results, []

    def rpc(self, name, params):
        return _Rpc(self, name, params)


@pytest.fixture(autouse=True)
def _admin_at_the_school(monkeypatch):
    monkeypatch.setattr(main, "_require_admin", lambda _r: {"id": "admin"})
    monkeypatch.setattr(main, "_school_timezone", lambda: ZoneInfo("America/Chicago"))
    # Thursday 8 October 2026, 15:00 in Chicago.
    monkeypatch.setattr(main, "_utc_now", lambda: datetime(2026, 10, 8, 20, 0, tzinfo=timezone.utc))


FUNNEL = {"students": {"signed_up": 30, "joined_class": 28, "parent_linked": 20, "consenting_now": 18,
                       "had_a_lesson": 25, "had_a_headband_lesson": 12},
          "teachers": {"signed_up": 2, "made_a_class": 2, "class_has_a_student": 2},
          "parents": {"signed_up": 22, "linked_a_child": 20}}


# ─── the funnel ──────────────────────────────────────────────────────────

def test_the_funnel_is_one_read_with_no_parameter(monkeypatch):
    db = _Db(admin_funnel=FUNNEL)
    monkeypatch.setattr(main, "supabase", db)

    assert main.admin_funnel(None) == {"retrieved": True, "funnel": FUNNEL}
    assert db.calls == [("admin_funnel", {})]


def test_a_funnel_wrapped_in_a_one_row_list_is_unwrapped(monkeypatch):
    monkeypatch.setattr(main, "supabase", _Db(admin_funnel=[FUNNEL]))
    assert main.admin_funnel(None)["funnel"] == FUNNEL


@pytest.mark.parametrize("result", [RuntimeError("rpc down"), None, [], "nonsense"])
def test_an_unread_or_misshapen_funnel_is_not_a_school_of_nobody(monkeypatch, result):
    monkeypatch.setattr(main, "supabase", _Db(admin_funnel=result))
    assert main.admin_funnel(None) == {"retrieved": False, "funnel": None}


# ─── consent changes ─────────────────────────────────────────────────────

def test_consent_changes_start_on_a_school_monday_and_read_in_the_schools_zone(monkeypatch):
    db = _Db(admin_consent_ops=[])
    monkeypatch.setattr(main, "supabase", db)

    got = main.admin_consent_ops(None, weeks=2)

    # Two weeks, this one included: from Monday 28 September, local midnight (05:00 UTC).
    assert db.calls == [("admin_consent_ops", {"p_since": "2026-09-28T05:00:00+00:00",
                                               "p_tz": "America/Chicago"})]
    assert got["retrieved"] is True and got["changes"] == []
    assert got["enablements_recorded_from"] == "2026-09-28"


def test_a_week_of_one_change_is_not_mistaken_for_a_wrapped_object(monkeypatch):
    one = [{"week": "2026-10-05", "channel": "camera", "kind": "withdrawn", "n": 1}]
    monkeypatch.setattr(main, "supabase", _Db(admin_consent_ops=one))
    assert main.admin_consent_ops(None)["changes"] == one


def test_an_unread_consent_history_is_not_a_quiet_term(monkeypatch):
    monkeypatch.setattr(main, "supabase", _Db(admin_consent_ops=RuntimeError("down")))
    got = main.admin_consent_ops(None)
    assert got["retrieved"] is False and got["changes"] == []


@pytest.mark.parametrize("weeks,expected", [(-3, 1), (0, 1), (4, 4), (10**6, 26)])
def test_the_week_range_is_clamped_not_refused(monkeypatch, weeks, expected):
    monkeypatch.setattr(main, "supabase", _Db(admin_consent_ops=[]))
    assert main.admin_consent_ops(None, weeks=weeks)["weeks"] == expected


# ─── signal quality ──────────────────────────────────────────────────────

QUALITY = {
    "eeg": [{"day": "2026-10-07", "withheld": True},
            {"day": "2026-10-08", "withheld": False, "students": 9, "samples": 5400, "trusted": 4700}],
    "heart": [{"day": "2026-10-08", "source": "muse_optics", "withheld": False, "students": 8, "rows": 900,
               "synthetic_rows": 900, "sqi_deciles": {"8": 400, "9": 500}, "sessions": 8, "never_calibrated": 1,
               "median_seconds_to_calibrate": 95.0, "p90_seconds_to_calibrate": 140.5}],
}


def test_signal_quality_asks_for_whole_school_days_and_the_cohort_floor(monkeypatch):
    db = _Db(admin_signal_quality=QUALITY)
    monkeypatch.setattr(main, "supabase", db)

    got = main.admin_signal_quality(None, days=3)

    assert db.calls == [("admin_signal_quality", {"p_since": "2026-10-06", "p_tz": "America/Chicago",
                                                  "p_min_students": main._COHORT_MIN_STUDENTS})]
    assert got["eeg"] == QUALITY["eeg"] and got["heart"] == QUALITY["heart"]
    assert got["min_students"] == 5 and got["retrieved"] is True


def test_unread_signal_quality_is_not_a_quiet_fortnight(monkeypatch):
    monkeypatch.setattr(main, "supabase", _Db(admin_signal_quality=RuntimeError("down")))
    got = main.admin_signal_quality(None)
    assert got["retrieved"] is False and got["eeg"] == [] and got["heart"] == []


@pytest.mark.parametrize("days,expected", [(-1, 1), (0, 1), (7, 7), (10**6, 60)])
def test_the_day_range_is_clamped_not_refused(monkeypatch, days, expected):
    monkeypatch.setattr(main, "supabase", _Db(admin_signal_quality={"eeg": [], "heart": []}))
    assert main.admin_signal_quality(None, days=days)["days"] == expected


def test_a_missing_function_names_the_migration_to_apply(monkeypatch, capsys):
    missing = RuntimeError("{'code': 'PGRST202', 'message': 'Could not find the function public.admin_funnel'}")
    monkeypatch.setattr(main, "supabase", _Db(admin_funnel=missing))

    assert main.admin_funnel(None)["retrieved"] is False
    assert "20261011000000" in capsys.readouterr().out
