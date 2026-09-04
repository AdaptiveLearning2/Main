"""A run of correct answers can raise difficulty without the headband agreeing.

Found on hardware (2026-09-03): five correct answers in a row, every question
served on easy. The deterministic shift only pushed up on a "focused" reading
at the moment of choosing, and that is a state a student cannot hold with a
headband that reads "stressed" through a loose strap and "neutral" the rest
of the time. Accuracy is the one channel here with no quality gate, so it is
allowed to push on its own -- under the same asymmetry as everything else:
stressed still wins, and the manual control still wins over a push.
"""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import LLM_topic_decider as td  # noqa: E402

GOOD_RUN = {"answered": 5, "correct": 5, "accuracy": 1.0}
THIN_RUN = {"answered": 2, "correct": 2, "accuracy": 1.0}
MIXED    = {"answered": 5, "correct": 3, "accuracy": 0.6}


def test_a_run_of_correct_answers_pushes_up_on_its_own():
    assert td._decide_bias("neutral", GOOD_RUN) == 1
    assert td._decide_bias("no_eeg", GOOD_RUN) == 1
    assert td._decide_bias("insufficient_signal", GOOD_RUN) == 1


def test_stressed_still_eases_whatever_the_answers_say():
    """The asymmetry signal_fusion documents, unchanged: a wrong push costs a
    struggling student a harder question."""
    assert td._decide_bias("stressed", GOOD_RUN) == -1
    assert td._decide_bias("stressed", GOOD_RUN, manual_bias=1) == -1


def test_the_push_needs_enough_answers_and_enough_of_them_right():
    assert td._decide_bias("neutral", THIN_RUN) == 0
    assert td._decide_bias("neutral", MIXED) == 0
    assert td._decide_bias("neutral", None) == 0
    at_threshold = {"answered": td.PERFORMANCE_PUSH_MIN_ANSWERS,
                    "correct": td.PERFORMANCE_PUSH_MIN_ANSWERS,
                    "accuracy": td.PERFORMANCE_PUSH_ACCURACY}
    assert td._decide_bias("neutral", at_threshold) == 1


def test_a_manual_setting_still_wins_over_a_push():
    """Pushing harder defers to the control -- a student who asked for Easier
    is not overruled by their own accuracy, exactly as "focused" never
    overruled it."""
    assert td._decide_bias("neutral", GOOD_RUN, manual_bias=-1) == -1
    assert td._decide_bias("focused", GOOD_RUN, manual_bias=-1) == -1
    assert td._decide_bias("neutral", GOOD_RUN, manual_bias=1) == 1


def test_focused_pushes_as_before():
    assert td._decide_bias("focused", None) == 1
    assert td._decide_bias("focused", MIXED) == 1


@pytest.mark.parametrize("label", ["neutral", "focused", "no_eeg", "insufficient_signal", "stressed"])
@pytest.mark.parametrize("perf", [None, THIN_RUN, MIXED, GOOD_RUN])
@pytest.mark.parametrize("manual", [-1, 0, 1])
def test_adding_evidence_never_raises_what_stressed_lowered(label, perf, manual):
    """Brute force over the inputs: the bias is -1 whenever the label is
    stressed, and never exceeds the manual setting when one is set."""
    bias = td._decide_bias(label, perf, manual)
    if label == "stressed":
        assert bias == -1
    elif manual:
        assert bias == manual
    assert bias in (-1, 0, 1)


def test_a_withheld_increase_is_not_overridden_by_the_answers():
    """The facial channel's one power is to veto an increase, and it does so
    by downgrading "focused" to "neutral". Keyed on the label alone, the
    accuracy push turned that veto into a push: "no opinion" and "withheld"
    were the same string. The fused state now says which, and a push defers
    to it exactly as it defers to stressed."""
    assert td._decide_bias("neutral", GOOD_RUN, increase_withheld=True) == 0
    assert td._decide_bias("focused", GOOD_RUN, increase_withheld=True) == 0
    # Easing still wins, and a manual setting is still the student's.
    assert td._decide_bias("stressed", GOOD_RUN, increase_withheld=True) == -1
    assert td._decide_bias("neutral", GOOD_RUN, manual_bias=1, increase_withheld=True) == 1


def test_the_veto_reaches_the_decider_from_the_real_fusion():
    """End to end through `signal_fusion.fuse`, not a hand-built flag: EEG
    focused, a trusted negative face, five correct answers -- no push."""
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


def test_the_decider_applies_the_shared_rule():
    """The rule lives in one function; the decider must call it rather than
    carry a second copy that can drift -- and must hand it the veto."""
    import inspect
    src = inspect.getsource(td.LLM_single_prompt_topic_and_difficulty_decider)
    assert "_decide_bias(" in src
    assert 'increase_withheld=bool(getattr(signal_state, "increase_withheld", False))' in src
