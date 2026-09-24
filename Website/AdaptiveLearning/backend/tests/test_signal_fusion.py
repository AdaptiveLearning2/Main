"""Fusion: adding a channel can make sessions gentler and never harder."""

from __future__ import annotations

import itertools

import pytest

from signal_fusion import (
    FER_LABELS,
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
    weak = eeg_channel(0.8, 0.7, 0.1)
    assert weak.label is None
    assert "confidence" in weak.reason
    assert fuse(weak).label == "insufficient_signal"


def test_a_revoked_channel_is_not_a_hardware_fault():
    assert eeg_channel(0.8, 0.7, 0.9, revoked=True).reason == "eeg revoked"
    assert heart_channel("high", True, "muse_optics", revoked=True).label is None
    assert face_channel("sad", 0.9, revoked=True).label is None


def test_a_calibrating_heart_channel_is_not_a_calm_one():
    """A baseline still forming is a temporary absence, not "no reading"."""
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
    """Facial is the weakest input; FER+ is unvalidated on children."""
    held = fuse(FOCUSED, ABSENT, face_channel("sad", 0.9))
    assert held.label == "neutral"
    assert "withholding" in held.reason

    alone = fuse(ABSENT, ABSENT, face_channel("sad", 0.99))
    assert alone.label != "stressed", "facial affect eased difficulty by itself"


def test_a_low_confidence_expression_is_ignored_entirely():
    assert fuse(FOCUSED, ABSENT, face_channel("sad", 0.1)).label == "focused"


def test_facial_labels_do_not_share_vocabulary_with_the_other_channels():
    """So facial cannot be wired into the ease-off branch by matching a label name."""
    assert face_channel("sad", 0.9).label == "negative"
    assert face_channel("happy", 0.9).label == "neutral"


def test_the_backend_names_exactly_the_labels_the_sidecar_emits():
    """Read from the sidecar's source: neither package can import the other."""
    import ast
    from pathlib import Path
    import signal_fusion
    src = Path(__file__).resolve().parents[4] / "EEGResearch/src/app/services/face_emotion.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    emitted, = [ast.literal_eval(node.value) for node in tree.body
                if isinstance(node, ast.Assign)
                and any(getattr(t, "id", None) == "EMOTION_LABELS" for t in node.targets)]
    assert signal_fusion.FER_LABELS == emitted
    assert signal_fusion.NEGATIVE_EMOTIONS <= set(emitted)


# Every FER+ label, and whether it withholds; a label added on either side fails until listed.
WITHHOLDS = {"neutral": False, "happy": False, "surprise": False, "sad": True,
             "angry": True, "disgust": True, "fear": True, "contempt": True}


def test_every_fer_label_is_classified():
    assert set(WITHHOLDS) == set(FER_LABELS)


@pytest.mark.parametrize("emotion,withholds", sorted(WITHHOLDS.items()))
def test_each_fer_label_withholds_an_increase_or_does_nothing(emotion, withholds):
    face = face_channel(emotion, 0.9)
    assert face.label == ("negative" if withholds else "neutral")
    assert fuse(FOCUSED, ABSENT, face).label == ("neutral" if withholds else "focused")
    # Never an ease-off on its own, whatever the label.
    for eeg in (ABSENT, NEUTRAL_EEG):
        assert fuse(eeg, ABSENT, face).label == fuse(eeg).label


# ── the property, over every combination ─────────────────────────────────────

def test_no_combination_of_added_channels_makes_a_session_harder():
    """Brute-forced: no added heart/face reading turns a non-focused outcome focused."""
    hearts = [ABSENT] + [heart_channel(c, t, s)
                         for c in ("low", "moderate", "high", "calibrating")
                         for t in (True, False)
                         for s in ("muse_optics", "muse_ppg", "rppg")]
    faces = [ABSENT] + [face_channel(e, c)
                        for e in FER_LABELS
                        for c in (0.2, 0.9)]

    # The last has calm withdrawn (cause no_calm): focus and contact, no stress.
    for eeg in (FOCUSED, STRESSED, NEUTRAL_EEG, eeg_channel(None, None, None),
                eeg_channel(0.9, None, 0.9)):
        assert eeg.cause != "no_calm" or eeg.label == "neutral"
        baseline = fuse(eeg).label
        for heart, face in itertools.product(hearts, faces):
            got = fuse(eeg, heart, face).label
            if got == "focused":
                assert baseline == "focused", (
                    f"adding channels raised difficulty: eeg={eeg.reason}, "
                    f"heart={heart.reason}, face={face.reason}"
                )


def test_no_channels_at_all_behaves_exactly_as_today():
    state = fuse(eeg_channel(None, None, None))
    assert state.label == "no_eeg"
    assert not state.adjusted


@pytest.mark.parametrize("label,expected", [
    ("focused", True), ("stressed", True),
    ("neutral", False), ("no_eeg", False), ("insufficient_signal", False),
])
def test_adjusted_matches_the_frontend_badge_contract(label, expected):
    """`eeg_adjusted` drives the "EEG eased/raised difficulty" badge."""
    from signal_fusion import FusedState
    assert FusedState(label, "").adjusted is expected


def test_every_outcome_explains_which_channel_decided_it():
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
    assert eeg_channel(0.8, 0.7, 0.9, revoked=True).cause == "revoked"
    assert heart_channel("calibrating", True, "muse_ppg").cause == "calibrating"
    assert heart_channel("high", False, "muse_ppg").cause == "untrusted"
    assert face_channel("sad", 0.9, False).cause == "untrusted"
    assert face_channel("sad", 0.1, True).cause == "low_confidence"


def test_an_untrusted_expression_is_rejected_before_its_confidence_is_read():
    """High confidence doesn't redeem an untrusted label, as with `heart_channel`."""
    assert face_channel("sad", 0.99, False).label is None
    assert fuse(FOCUSED, ABSENT, face_channel("sad", 0.99, False)).label == "focused"


def test_the_eeg_lines_match_the_sidecars_rescaled_literals():
    """Both packages carry these literals and neither can import the other."""
    import signal_fusion
    assert signal_fusion.EEG_FOCUSED_FOCUS_MIN == 0.624
    assert signal_fusion.EEG_STRESSED_CALM_MAX == 0.377
    assert signal_fusion.EEG_FOCUSED_CALM_MIN == 0.5
