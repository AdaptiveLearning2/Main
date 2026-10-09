"""Why each served adaptive question was eased, raised or held: the reason, its storage and the admin read."""

import os
import pathlib
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import LLM_topic_decider as td  # noqa: E402
import main  # noqa: E402
import signal_fusion as sf  # noqa: E402

MIGRATION = (pathlib.Path(__file__).resolve().parents[4] / "supabase" / "migrations"
             / "20261012000000_adaptive_decisions.sql")

GOOD_RUN = {"answered": 5, "correct": 5, "accuracy": 1.0, "recent": [True] * 5}
FALLING = {"answered": 10, "correct": 7, "accuracy": 0.7, "recent": [False] * 3 + [True] * 7}


# ─── the reason is the branch that chose the bias ────────────────────────

@pytest.mark.parametrize("args,expected", [
    (("stressed", GOOD_RUN, 1, False), (-1, "stressed")),
    (("neutral", GOOD_RUN, -1, False), (-1, "manual")),
    (("focused", GOOD_RUN, 0, True), (0, "facial_veto")),
    (("focused", FALLING, 0, False), (0, "recent_misses")),
    (("focused", None, 0, False), (1, "focused")),
    (("no_eeg", GOOD_RUN, 0, False), (1, "correct_run")),
    (("neutral", None, 0, False), (0, "nothing_to_act_on")),
])
def test_each_bias_names_the_branch_that_chose_it(args, expected):
    assert td._decide_bias_why(*args) == expected
    # And the bias alone is unchanged for every caller of the old function.
    assert td._decide_bias(*args) == expected[0]


def test_the_reasons_the_code_writes_are_the_ones_the_column_allows():
    sql = MIGRATION.read_text(encoding="utf-8")
    why_check = sql.split('"why"', 1)[1].split("),", 1)[0]
    assert sorted(re.findall(r"'(\w+)'", why_check)) == sorted(td.BIAS_WHYS)


# ─── which consent channels had an opinion ───────────────────────────────

def test_opinions_name_the_consent_each_sensor_needs_and_never_a_reading():
    eeg = sf.ChannelState("focused", "eeg focused and calm")
    heart = sf.ChannelState("calm", "heart low (muse_optics)", "muse_optics")
    face = sf.ChannelState("neutral", "face happy")
    assert sf.fuse(eeg, heart, face).opinions == ("camera", "eeg", "headband_optical")


def test_a_silent_sensor_has_no_opinion():
    eeg = sf.ChannelState(None, "no eeg samples", cause="no_samples")
    heart = sf.ChannelState(None, "heart calibrating (rppg)", "rppg", cause="calibrating")
    assert sf.fuse(eeg, heart).opinions == ()


@pytest.mark.parametrize("source,consent", [("muse_optics", "headband_optical"), ("muse_ppg", "headband_optical"),
                                            ("rppg", "camera")])
def test_each_heart_source_maps_to_its_consent(source, consent):
    heart = sf.ChannelState("stressed", f"heart elevated ({source})", source)
    assert sf.fuse(sf.ChannelState(), heart).opinions == (consent,)


def test_the_heart_mapping_matches_the_one_recording_uses():
    mapped = {s: c for c, sources in {"headband_optical": main._HEART_SOURCES_BY_RECORD_FLAG["record_headband_optical"],
                                      "camera": main._HEART_SOURCES_BY_RECORD_FLAG["record_camera"]}.items()
              for s in sources}
    assert sf._HEART_CONSENT == mapped


# ─── the route stores what it serves ─────────────────────────────────────

class _Inserts:
    def __init__(self, fail=False):
        self.rows, self.fail = [], fail

    def table(self, name):
        owner = self

        class _T:
            def insert(self, row, **_k):
                owner.rows.append((name, row))
                return self

            def execute(self):
                if owner.fail:
                    raise RuntimeError("insert failed")
                return type("R", (), {"data": []})()
        return _T()


DECISION = {"bias": 1, "why": "correct_run", "label": "neutral", "opinions": ["eeg"], "increase_withheld": False}


def _serve(monkeypatch, session_id, decision=DECISION, db=None):
    db = db or _Inserts()
    monkeypatch.setattr(main, "supabase", db)
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "kid"})
    monkeypatch.setattr(main, "_verify_session_owner", lambda *_a: None)
    monkeypatch.setattr(main, "_served_grade", lambda *_a: "5th Grade")
    monkeypatch.setattr(main, "_admit_generation", lambda *_a, **_k: (lambda: None))
    monkeypatch.setattr(main, "_ensure_queue", lambda *_a, **_k: None)
    question = {"question_text": "2+2", "difficulty": "hard",
                **({"adaptive_decision": dict(decision)} if decision else {})}
    monkeypatch.setattr(main.LLM_topic_decider, "LLM_single_prompt_topic_and_difficulty_decider",
                        lambda *_a, **_k: dict(question))
    out = main.generate_question(request=None, grade="5th Grade", class_id=None, bias=0, session_id=session_id)
    return out, db


def test_a_served_question_stores_its_decision_and_does_not_send_it(monkeypatch):
    out, db = _serve(monkeypatch, "sess-1")

    assert "adaptive_decision" not in out
    [(table, row)] = [r for r in db.rows if r[0] == "adaptive_decisions"]
    assert row == {"session_id": "sess-1", "user_id": "kid", "served_from": "inline", "difficulty": "hard",
                   "bias": 1, "why": "correct_run", "label": "neutral", "opinions": ["eeg"],
                   "increase_withheld": False}


def test_a_question_with_no_session_stores_nothing(monkeypatch):
    out, db = _serve(monkeypatch, None)
    assert out["question_text"] == "2+2"
    assert not [r for r in db.rows if r[0] == "adaptive_decisions"]


def test_a_failed_write_never_costs_the_question(monkeypatch, capsys):
    out, _db = _serve(monkeypatch, "sess-1", db=_Inserts(fail=True))
    assert out["question_text"] == "2+2"
    assert "[adaptive] could not record" in capsys.readouterr().out


# ─── the admin read ──────────────────────────────────────────────────────

class _Rpc:
    def __init__(self, result):
        self.result, self.calls = result, []

    def rpc(self, name, params):
        self.calls.append((name, params))
        owner = self
        return type("Q", (), {"execute": lambda _s: owner._answer()})()

    def _answer(self):
        if isinstance(self.result, Exception):
            raise self.result
        return type("R", (), {"data": self.result})()


@pytest.fixture
def _admin(monkeypatch):
    monkeypatch.setattr(main, "_require_admin", lambda _r: {"id": "admin"})
    monkeypatch.setattr(main, "_school_timezone", lambda: ZoneInfo("America/Chicago"))
    monkeypatch.setattr(main, "_utc_now", lambda: datetime(2026, 10, 8, 20, 0, tzinfo=timezone.utc))


DAY = {"day": "2026-10-08", "withheld": False, "students": 7, "decisions": 60, "eased": 6, "raised": 14,
       "held": 40, "withheld_increase": 2, "by_why": {"nothing_to_act_on": 30, "correct_run": 14},
       "by_label": {"neutral": 50, "stressed": 6}}


def test_the_read_asks_for_whole_school_days_and_the_floor(monkeypatch, _admin):
    db = _Rpc([DAY])
    monkeypatch.setattr(main, "supabase", db)

    got = main.admin_adaptive(None, days=3)

    assert db.calls == [("admin_adaptive_reasons", {"p_since": "2026-10-06", "p_tz": "America/Chicago",
                                                    "p_min_students": 5})]
    # A one-day answer is a list of one, never unwrapped into the day itself.
    assert got["decisions"] == [DAY] and got["retrieved"] is True


def test_an_unread_answer_is_not_a_quiet_fortnight(monkeypatch, _admin):
    monkeypatch.setattr(main, "supabase", _Rpc(RuntimeError("down")))
    got = main.admin_adaptive(None)
    assert got["retrieved"] is False and got["decisions"] == []


@pytest.mark.parametrize("days,expected", [(-1, 1), (0, 1), (5, 5), (10**6, 60)])
def test_the_day_range_is_clamped_not_refused(monkeypatch, _admin, days, expected):
    monkeypatch.setattr(main, "supabase", _Rpc([]))
    assert main.admin_adaptive(None, days=days)["days"] == expected
