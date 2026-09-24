"""A run of correct answers can raise difficulty alone; stressed and the manual control still win."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import LLM_topic_decider as td  # noqa: E402

GOOD_RUN = {"answered": 5, "correct": 5, "accuracy": 1.0, "recent": [True] * 5}
THIN_RUN = {"answered": 2, "correct": 2, "accuracy": 1.0, "recent": [True] * 2}
MIXED    = {"answered": 5, "correct": 3, "accuracy": 0.6, "recent": [True, False, True, False, True]}
# 7 of 10 either way; only the order differs. Newest first.
RISING   = {"answered": 10, "correct": 7, "accuracy": 0.7, "recent": [True] * 7 + [False] * 3}
FALLING  = {"answered": 10, "correct": 7, "accuracy": 0.7, "recent": [False] * 3 + [True] * 7}
ONE_SLIP = {"answered": 10, "correct": 9, "accuracy": 0.9, "recent": [False] + [True] * 9}


def test_a_run_of_correct_answers_pushes_up_on_its_own():
    assert td._decide_bias("neutral", GOOD_RUN) == 1
    assert td._decide_bias("no_eeg", GOOD_RUN) == 1
    assert td._decide_bias("insufficient_signal", GOOD_RUN) == 1


def test_stressed_still_eases_whatever_the_answers_say():
    assert td._decide_bias("stressed", GOOD_RUN) == -1
    assert td._decide_bias("stressed", GOOD_RUN, manual_bias=1) == -1


def test_the_push_needs_enough_answers_and_enough_of_them_right():
    assert td._decide_bias("neutral", THIN_RUN) == 0
    assert td._decide_bias("neutral", MIXED) == 0
    assert td._decide_bias("neutral", None) == 0
    at_threshold = {"answered": td.PERFORMANCE_PUSH_MIN_ANSWERS,
                    "correct": td.PERFORMANCE_PUSH_MIN_ANSWERS,
                    "accuracy": td.PERFORMANCE_PUSH_ACCURACY,
                    "recent": [True] * td.PERFORMANCE_PUSH_MIN_ANSWERS}
    assert td._decide_bias("neutral", at_threshold) == 1


def test_the_push_reads_the_direction_not_only_the_aggregate():
    """7 of 10 is 0.7 either way; only the order shows three misses just now."""
    assert td._decide_bias("neutral", RISING) == 1
    assert td._decide_bias("neutral", FALLING) == 0
    # One slip on a strong run gates the push until the next right answer.
    assert td._decide_bias("neutral", ONE_SLIP) == 0
    # A caller predating `recent` fails closed: no direction, no push.
    assert td._decide_bias("neutral", {"answered": 5, "correct": 5, "accuracy": 1.0}) == 0


def test_get_session_performance_keeps_the_order_newest_first(monkeypatch):
    """`recent[:2]` is the newest two only because of `desc`, so the fake honours `order()`."""
    class _Q:
        def __init__(self, rows): self.rows = list(rows)
        def select(self, *_a): return self
        def eq(self, *_a): return self
        def order(self, column, desc=False, **_k):
            self.rows.sort(key=lambda r: r[column], reverse=desc)
            return self
        def limit(self, *_a): return self
        def execute(self): return type("R", (), {"data": self.rows})()
    # Stored oldest-first, as a table would hand them back unordered.
    rows = [{"correct": True,  "answered_at": "2026-09-04T10:00:01Z"},
            {"correct": True,  "answered_at": "2026-09-04T10:00:02Z"},
            {"correct": False, "answered_at": "2026-09-04T10:00:03Z"},
            {"correct": False, "answered_at": "2026-09-04T10:00:04Z"}]
    monkeypatch.setattr(td, "supabase", type("S", (), {"table": lambda self, _n: _Q(rows)})())
    perf = td.get_session_performance("sess")
    assert perf == {"answered": 4, "correct": 2, "accuracy": 0.5,
                    "recent": [False, False, True, True]}
    # The two most recent were wrong: no push, whatever the aggregate says.
    assert td._decide_bias("neutral", {**perf, "accuracy": 0.7}) == 0


def test_a_manual_setting_still_wins_over_a_push():
    """Pushing harder defers to the control."""
    assert td._decide_bias("neutral", GOOD_RUN, manual_bias=-1) == -1
    assert td._decide_bias("focused", GOOD_RUN, manual_bias=-1) == -1
    assert td._decide_bias("neutral", GOOD_RUN, manual_bias=1) == 1


def test_focused_pushes_unless_the_answers_are_falling():
    """A falling run is an opinion, and every channel with one must agree to raise."""
    assert td._decide_bias("focused", None) == 1
    assert td._decide_bias("focused", RISING) == 1
    assert td._decide_bias("focused", GOOD_RUN) == 1
    # MIXED's newest answer is right and its second newest wrong: falling.
    assert td._decide_bias("focused", MIXED) == 0
    assert td._decide_bias("focused", FALLING) == 0
    assert td._decide_bias("focused", ONE_SLIP) == 0
    # A caller predating `recent` has no opinion: focused still pushes.
    assert td._decide_bias("focused", {"answered": 5, "correct": 5, "accuracy": 1.0}) == 1


def test_a_falling_run_vetoes_a_real_focused_reading_end_to_end():
    import signal_fusion as sf
    fused = sf.fuse(sf.eeg_channel(0.9, 0.9, 0.9))
    assert fused.label == "focused"
    assert td._decide_bias(fused.label, FALLING,
                           increase_withheld=fused.increase_withheld) == 0
    # Easing still wins over everything, and a manual setting is the student's.
    assert td._decide_bias("stressed", RISING) == -1
    assert td._decide_bias("focused", FALLING, manual_bias=1) == 1


@pytest.mark.parametrize("label", ["neutral", "focused", "no_eeg", "insufficient_signal", "stressed"])
@pytest.mark.parametrize("perf", [None, THIN_RUN, MIXED, GOOD_RUN])
@pytest.mark.parametrize("manual", [-1, 0, 1])
def test_adding_evidence_never_raises_what_stressed_lowered(label, perf, manual):
    bias = td._decide_bias(label, perf, manual)
    if label == "stressed":
        assert bias == -1
    elif manual:
        assert bias == manual
    assert bias in (-1, 0, 1)


def test_a_withheld_increase_is_not_overridden_by_the_answers():
    """A neutral label may be a withheld increase, and the push defers to that."""
    assert td._decide_bias("neutral", GOOD_RUN, increase_withheld=True) == 0
    assert td._decide_bias("focused", GOOD_RUN, increase_withheld=True) == 0
    # Easing still wins, and a manual setting is still the student's.
    assert td._decide_bias("stressed", GOOD_RUN, increase_withheld=True) == -1
    assert td._decide_bias("neutral", GOOD_RUN, manual_bias=1, increase_withheld=True) == 1


def test_the_veto_reaches_the_decider_from_the_real_fusion():
    """End to end through `signal_fusion.fuse`, not a hand-built flag."""
    import signal_fusion as sf
    fused = sf.fuse(sf.ChannelState("focused", "eeg focus high"),
                    face=sf.ChannelState("negative", "face sad"))
    assert fused.label == "neutral" and fused.increase_withheld is True
    assert td._decide_bias(fused.label, GOOD_RUN,
                           increase_withheld=fused.increase_withheld) == 0
    # And without the veto the same answers do push.
    clear = sf.fuse(sf.ChannelState("focused", "eeg focus high"))
    assert clear.increase_withheld is False
    assert td._decide_bias(clear.label, GOOD_RUN,
                           increase_withheld=clear.increase_withheld) == 1


def _eeg_states():
    import signal_fusion as sf
    return [
        ("neutral", sf.ChannelState("neutral", "eeg neutral")),
        ("no_eeg", sf.ChannelState(None, "no eeg samples", cause="no_samples")),
        ("low confidence", sf.ChannelState(None, "eeg confidence low", cause="low_confidence")),
        ("focused", sf.ChannelState("focused", "eeg focused and calm")),
    ]


@pytest.mark.parametrize("name,eeg", _eeg_states())
def test_a_negative_face_withholds_the_accuracy_push_whatever_the_eeg_says(name, eeg):
    """The push fires from neutral or no EEG, so the veto must ride on every state."""
    import signal_fusion as sf
    fused = sf.fuse(eeg, face=sf.ChannelState("negative", "face sad"))
    assert fused.increase_withheld is True, name
    assert td._decide_bias(fused.label, GOOD_RUN,
                           increase_withheld=fused.increase_withheld) == 0, name
    # Without the face, the same EEG lets the answers push.
    clear = sf.fuse(eeg)
    assert clear.increase_withheld is False, name
    assert td._decide_bias(clear.label, GOOD_RUN,
                           increase_withheld=clear.increase_withheld) == 1, name


def test_a_negative_face_never_stops_an_ease_off():
    """Withholding is the face's only power."""
    import signal_fusion as sf
    fused = sf.fuse(sf.ChannelState("stressed", "eeg calm low"),
                    face=sf.ChannelState("negative", "face sad"))
    assert fused.label == "stressed"
    assert td._decide_bias(fused.label, GOOD_RUN,
                           increase_withheld=fused.increase_withheld) == -1


def test_the_decider_applies_the_shared_rule():
    """The decider calls the one rule, not a copy, and hands it the veto."""
    import inspect
    src = inspect.getsource(td.LLM_single_prompt_topic_and_difficulty_decider)
    assert "_decide_bias(" in src
    assert 'increase_withheld=bool(getattr(signal_state, "increase_withheld", False))' in src
