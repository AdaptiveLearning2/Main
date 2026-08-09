"""The fusion rule, exhaustively, because it decides how hard a child's next
question is and every failure here is a quiet one.

The property the whole file is really testing: **adding a channel can make
sessions gentler and can never make them more aggressive.** If a change to
`signal_fusion` breaks that, something in here fails.
"""

from __future__ import annotations

import itertools

import pytest

from signal_fusion import (
    ChannelState,
    eeg_channel,
    face_channel,
    fuse,
    heart_channel,
)

FOCUSED = eeg_channel(0.8, 0.7, 0.9)
STRESSED = eeg_channel(0.4, 0.2, 0.9)
NEUTRAL_EEG = eeg_channel(0.5, 0.6, 0.9)
ABSENT = ChannelState()


# ── each channel reads itself correctly ──────────────────────────────────────

def test_eeg_labels_match_the_thresholds_already_in_production():
    assert eeg_channel(0.8, 0.7, 0.9).label == "focused"
    assert eeg_channel(0.4, 0.2, 0.9).label == "stressed"
    assert eeg_channel(0.5, 0.6, 0.9).label == "neutral"


def test_poor_electrode_contact_is_not_a_calm_student():
    """The distinction the confidence gate exists for."""
    weak = eeg_channel(0.8, 0.7, 0.1)
    assert weak.label is None
    assert "confidence" in weak.reason
    assert fuse(weak).label == "insufficient_signal"


def test_a_revoked_channel_is_not_a_hardware_fault():
    """A respected refusal must not read as a broken sensor to whoever is
    looking at why adaptation stopped responding."""
    assert eeg_channel(0.8, 0.7, 0.9, revoked=True).reason == "eeg revoked"
    assert heart_channel("high", True, "muse_optics", revoked=True).label is None
    assert face_channel("sad", 0.9, revoked=True).label is None


def test_a_calibrating_heart_channel_is_not_a_calm_one():
    """A failover that has not built a baseline yet is a transient absence with
    a known end. Reporting it as 'no reading' makes recovery look like failure."""
    ch = heart_channel("calibrating", True, "rppg")
    assert ch.label is None
    assert "calibrating" in ch.reason


def test_an_untrusted_heart_sample_is_present_but_not_acted_on():
    ch = heart_channel("high", False, "muse_optics")
    assert ch.label is None
    assert "untrusted" in ch.reason
    assert fuse(NEUTRAL_EEG, ch).label == "neutral", "an untrusted sample eased difficulty"


# ── the asymmetry, which is the whole point ──────────────────────────────────

def test_a_trusted_elevated_heart_overrides_an_eeg_that_reads_calm():
    """The one case where a channel contradicts EEG and wins."""
    state = fuse(NEUTRAL_EEG, heart_channel("high", True, "muse_optics"))
    assert state.label == "stressed"
    assert "overriding" in state.reason
    assert "muse_optics" in state.reason


def test_heart_alone_can_ease_difficulty():
    state = fuse(ABSENT, heart_channel("high", True, "rppg"))
    assert state.label == "stressed"


def test_heart_alone_can_never_raise_difficulty():
    """The asymmetry stated directly. There is no heart value that produces
    'focused' without EEG agreeing."""
    for category in ("low", "moderate", "high", "calibrating", None):
        for trusted in (True, False, None):
            state = fuse(ABSENT, heart_channel(category, trusted, "muse_ppg"))
            assert state.label != "focused", f"{category}/{trusted} raised difficulty"


def test_raising_difficulty_needs_every_opinion_to_agree():
    assert fuse(FOCUSED).label == "focused"
    assert fuse(FOCUSED, heart_channel("low", True, "muse_optics")).label == "focused"

    contradicted = fuse(FOCUSED, heart_channel("high", True, "muse_optics"))
    assert contradicted.label == "stressed", "a contradicted increase still went up"


def test_a_negative_expression_withholds_an_increase_without_causing_a_decrease():
    """Facial is the weakest input: it can hold difficulty where it is and can
    never move it down on its own. FER+ is unvalidated on children."""
    held = fuse(FOCUSED, ABSENT, face_channel("sad", 0.9))
    assert held.label == "neutral"
    assert "withholding" in held.reason

    alone = fuse(ABSENT, ABSENT, face_channel("sad", 0.99))
    assert alone.label != "stressed", "facial affect eased difficulty by itself"


def test_a_low_confidence_expression_is_ignored_entirely():
    assert fuse(FOCUSED, ABSENT, face_channel("sad", 0.1)).label == "focused"


def test_facial_labels_do_not_share_vocabulary_with_the_other_channels():
    """Deliberate: 'negative', never 'stressed'. A later edit that wires facial
    into the ease-off branch by matching on a label name should not compile
    silently into a behaviour change."""
    assert face_channel("sad", 0.9).label == "negative"
    assert face_channel("happy", 0.9).label == "neutral"


# ── the property, over every combination ─────────────────────────────────────

def test_no_combination_of_added_channels_makes_a_session_harder():
    """The safety property, brute-forced.

    For every EEG state, adding any heart and any facial reading must never turn
    a non-focused outcome into a focused one. Adding a sensor is allowed to ease
    difficulty and never to push it.
    """
    hearts = [ABSENT] + [heart_channel(c, t, s)
                         for c in ("low", "moderate", "high", "calibrating")
                         for t in (True, False)
                         for s in ("muse_optics", "muse_ppg", "rppg")]
    faces = [ABSENT] + [face_channel(e, c)
                        for e in ("happy", "sad", "anger", "neutral", "surprise")
                        for c in (0.2, 0.9)]

    for eeg in (FOCUSED, STRESSED, NEUTRAL_EEG, eeg_channel(None, None, None)):
        baseline = fuse(eeg).label
        for heart, face in itertools.product(hearts, faces):
            got = fuse(eeg, heart, face).label
            if got == "focused":
                assert baseline == "focused", (
                    f"adding channels raised difficulty: eeg={eeg.reason}, "
                    f"heart={heart.reason}, face={face.reason}"
                )


def test_no_channels_at_all_behaves_exactly_as_today():
    """Phase 5 lands before the channels are fed. With nothing present the
    caller must see the same label it saw before fusion existed."""
    state = fuse(eeg_channel(None, None, None))
    assert state.label == "no_eeg"
    assert not state.adjusted


@pytest.mark.parametrize("label,expected", [
    ("focused", True), ("stressed", True),
    ("neutral", False), ("no_eeg", False), ("insufficient_signal", False),
])
def test_adjusted_matches_the_frontend_badge_contract(label, expected):
    """`eeg_adjusted` drives an 'EEG eased/raised difficulty' badge. It must
    stay true only when something actually moved."""
    from signal_fusion import FusedState
    assert FusedState(label, "").adjusted is expected


def test_every_outcome_explains_which_channel_decided_it():
    """A reason that does not name its cause is the thing this file exists to
    prevent -- three different absences would otherwise look identical."""
    for state in (
        fuse(FOCUSED),
        fuse(STRESSED),
        fuse(NEUTRAL_EEG, heart_channel("high", True, "rppg")),
        fuse(FOCUSED, ABSENT, face_channel("sad", 0.9)),
        fuse(eeg_channel(None, None, None)),
    ):
        assert state.reason and state.reason != "absent"
        assert set(state.channels) == {"eeg", "heart", "face"}


# ── the cause field, which control flow now reads instead of the reason text ──

def test_insufficient_signal_is_classified_structurally_not_by_wording():
    """`fuse` used to decide this by sniffing for "confidence" in the reason
    string. Correct against every message then, and one reword away from
    silently reclassifying an outcome."""
    from signal_fusion import ChannelState

    low_conf = eeg_channel(0.8, 0.7, 0.1)
    assert low_conf.cause == "low_confidence"
    assert fuse(low_conf).label == "insufficient_signal"

    # Same cause, completely different wording: the classification must hold.
    reworded = ChannelState(None, "electrode contact too poor to score",
                            cause="low_confidence")
    assert fuse(reworded).label == "insufficient_signal"

    # And an absence that is *not* low confidence must not be mislabelled.
    assert fuse(eeg_channel(None, None, None)).label == "no_eeg"
    assert fuse(eeg_channel(0.8, 0.7, 0.9, revoked=True)).label == "no_eeg"


def test_every_absence_carries_a_machine_readable_cause():
    """Three absences that are not the same thing, distinguishable without
    parsing prose."""
    assert eeg_channel(0.8, 0.7, 0.9, revoked=True).cause == "revoked"
    assert heart_channel("calibrating", True, "muse_ppg").cause == "calibrating"
    assert heart_channel("high", False, "muse_ppg").cause == "untrusted"
    assert face_channel("sad", 0.9, False).cause == "untrusted"
    assert face_channel("sad", 0.1, True).cause == "low_confidence"


def test_an_untrusted_expression_is_rejected_before_its_confidence_is_read():
    """A classifier that does not stand behind a label is not answered by a
    confidence figure. Matches how heart_channel treats `trusted`."""
    assert face_channel("sad", 0.99, False).label is None
    assert fuse(FOCUSED, ABSENT, face_channel("sad", 0.99, False)).label == "focused"
