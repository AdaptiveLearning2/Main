"""Combine EEG, heart and facial channels into one difficulty signal.

Pure functions over already-read values, so the rule can be tested exhaustively.
It decides how hard a question a child gets, and failures here are quiet ones.

Why the rule is asymmetric
--------------------------
**Easing off wins; pushing harder defers.** To *raise* difficulty every
available channel must agree. To *lower* it, any one trusted channel is enough.

This is the only shape that fails safely. A wrong ease-off costs a student one
question below their level. A wrong push costs a struggling student a harder
one, and the signals are least reliable exactly when a student is agitated --
which is when a false `focused` is most likely and most damaging.

What each combination does
--------------------------
| channels present | behaviour |
| --- | --- |
| none | correctness, topic history and manual bias only -- today's behaviour |
| EEG only | today's behaviour |
| heart only | can ease difficulty alone; **cannot raise it** |
| facial only | weak modifier; never decides alone |
| EEG + heart | full rule |

So adding a channel can only ever make sessions gentler, never more aggressive.
Correctness still raises difficulty independently; these only modulate it.

The facial caveat, stated where it is enforced
----------------------------------------------
FER+ is trained mostly on adult faces, and is least reliable on this product's
users: children, and children with learning disabilities, whose expressions are
more variable and more often misclassified. So emotion is deliberately the
weakest input here -- it can *withhold* a difficulty increase and can never
cause one, and it cannot trigger an ease-off by itself either.

It should not become a primary adaptation signal without validation on the
actual user group. `EMOTION_MIN_CONFIDENCE` below is a guess, not a
measurement; treat it as one.

Absent is not calm
------------------
Every "no reading" path returns a state that changes nothing, but the reason
string distinguishes cases a reader would otherwise conflate: a **revoked**
channel is a respected refusal, not a hardware fault; a channel **calibrating**
after a failover is not a calm one; a channel **live but untrusted** is not an
absent one. Same reasoning as the reporting rules in CLAUDE.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# EEG thresholds, shared with the rest of the production code.
EEG_MIN_CONFIDENCE = 0.45
EEG_FOCUSED_FOCUS_MIN = 0.7
EEG_FOCUSED_CALM_MIN = 0.5
EEG_STRESSED_CALM_MAX = 0.35

# Facial thresholds. Unvalidated on this user group -- see the module
# docstring. Only ever used to withhold an increase.
EMOTION_MIN_CONFIDENCE = 0.50
NEGATIVE_EMOTIONS = frozenset({"sad", "fear", "anger", "disgust"})

# Heart stress categories that count as "elevated" for an ease-off. `trusted`
# is checked separately: an untrusted sample still carries a category, it is
# just not one worth acting on.
ELEVATED_STRESS = frozenset({"high"})


@dataclass(frozen=True)
class ChannelState:
    """One channel's contribution, or its absence and why.

    `label` is None whenever the channel says nothing. `reason` always says
    why, because "no label" has several causes and they are not interchangeable.

    `cause` carries that distinction as a value, not by parsing `reason` text --
    so a reworded message can't silently change what the code does with it.
    """
    label: str | None = None
    reason: str = "absent"
    source: str | None = None
    cause: str | None = None


@dataclass(frozen=True)
class FusedState:
    """The answer, and enough of its provenance to explain a decision.

    `label` keeps the exact vocabulary the caller already branches on --
    "focused" | "stressed" | "neutral" | "insufficient_signal" | "no_eeg" --
    so downstream code and the frontend badge need no changes.
    """
    label: str
    reason: str
    focus: float | None = None
    calm: float | None = None
    confidence: float | None = None
    channels: dict[str, str] = field(default_factory=dict)
    # True when a channel actively vetoed an increase -- the facial channel's
    # one power. The label is "neutral" either way, and "no opinion" and
    # "withheld" must not collapse into the same string: a caller that pushes
    # harder on its own evidence (a run of correct answers) has to defer to
    # this exactly as it defers to "stressed".
    increase_withheld: bool = False

    @property
    def adjusted(self) -> bool:
        return self.label in ("focused", "stressed")


def eeg_channel(
    focus: float | None,
    calm: float | None,
    confidence: float | None,
    *,
    revoked: bool = False,
) -> ChannelState:
    """The EEG channel's label, by the thresholds already in production."""
    if revoked:
        return ChannelState(None, "eeg revoked", cause="revoked")
    if focus is None or calm is None or confidence is None:
        return ChannelState(None, "no eeg samples", cause="no_samples")
    if confidence < EEG_MIN_CONFIDENCE:
        # Poor electrode contact, not a calm student.
        return ChannelState(None, f"eeg confidence {confidence:.2f} below "
                                  f"{EEG_MIN_CONFIDENCE}", cause="low_confidence")
    if focus >= EEG_FOCUSED_FOCUS_MIN and calm >= EEG_FOCUSED_CALM_MIN:
        return ChannelState("focused", "eeg focused and calm")
    if calm < EEG_STRESSED_CALM_MAX:
        return ChannelState("stressed", f"eeg calm {calm:.2f} below "
                                        f"{EEG_STRESSED_CALM_MAX}")
    return ChannelState("neutral", "eeg neutral")


def heart_channel(
    stress_category: str | None,
    trusted: bool | None,
    source: str | None,
    *,
    revoked: bool = False,
) -> ChannelState:
    """The heart channel's label, whatever sensor produced it.

    Source-agnostic: headband optics, headband PPG and camera rPPG all arrive
    here as the same three fields. The rule doesn't change with the source;
    only the reason string names it.

    `calibrating` is its own case because it's a *temporary* absence with a
    known end -- a failover still building a baseline -- and calling it "no
    reading" would make a recovering sensor look broken.
    """
    if revoked:
        return ChannelState(None, "heart revoked", source, cause="revoked")
    if stress_category is None:
        return ChannelState(None, "no heart samples", source, cause="no_samples")
    if stress_category == "calibrating":
        return ChannelState(None, f"heart calibrating ({source})", source,
                            cause="calibrating")
    if not trusted:
        # Present and readable, just not worth acting on. Distinct from absent.
        return ChannelState(None, f"heart untrusted ({source})", source,
                            cause="untrusted")
    if stress_category in ELEVATED_STRESS:
        return ChannelState("stressed", f"heart elevated ({source})", source)
    # "calm" is kept but `fuse` never reads it: a calm heart alone isn't a
    # reason to raise difficulty -- EEG must also agree. Kept so the
    # diagnostic `channels` map can still tell "read it, it was fine" apart
    # from "could not read it".
    return ChannelState("calm", f"heart {stress_category} ({source})", source)


def face_channel(
    emotion: str | None,
    emotion_confidence: float | None,
    emotion_trusted: bool | None = None,
    *,
    revoked: bool = False,
) -> ChannelState:
    """The facial channel, which is only ever allowed to withhold.

    Named `emotion_confidence`, not `confidence`. A similarly-named
    `identity_confidence` (how sure we are *whose* face this is) used to sit
    beside it, and the two were once swapped: a clearly identified face with a
    garbage FER+ label withheld an increase, while a well-classified
    expression on a poorly identified face was thrown away -- both silently.
    `identity_confidence` is retired (see CLAUDE.md), but the qualified name
    stays so that ambiguity can't come back.

    Returns "negative" or "neutral", never "stressed" -- a different
    vocabulary from the other two channels, so a later edit can't wire this
    into the ease-off branch by matching on a label name.
    """
    if revoked:
        return ChannelState(None, "face revoked", cause="revoked")
    if not emotion:
        return ChannelState(None, "no face samples", cause="no_samples")
    if emotion_trusted is False:
        # Hard reject, same as heart_channel's `trusted`. The classifier
        # itself says it doesn't stand behind this label, so a confidence
        # score next to it doesn't help.
        return ChannelState(None, "face untrusted", cause="untrusted")
    if emotion_confidence is None or emotion_confidence < EMOTION_MIN_CONFIDENCE:
        return ChannelState(None, "face emotion confidence below threshold",
                            cause="low_confidence")
    if emotion.lower() in NEGATIVE_EMOTIONS:
        return ChannelState("negative", f"face {emotion.lower()}")
    return ChannelState("neutral", f"face {emotion.lower()}")


def fuse(
    eeg: ChannelState,
    heart: ChannelState = ChannelState(),
    face: ChannelState = ChannelState(),
    *,
    focus: float | None = None,
    calm: float | None = None,
    confidence: float | None = None,
) -> FusedState:
    """Apply the asymmetric rule across whichever channels are present."""
    channels = {"eeg": eeg.reason, "heart": heart.reason, "face": face.reason}
    common = dict(focus=focus, calm=calm, confidence=confidence, channels=channels)

    # 1. Ease off. Either channel alone is enough. A trusted elevated heart
    #    overrides a calm EEG reading -- the one case where a channel outranks
    #    EEG. Checked first so it can't be reached after an increase.
    if heart.label == "stressed" and eeg.label != "stressed":
        return FusedState("stressed",
                          f"{heart.reason} overriding eeg-{eeg.label or 'absent'}",
                          **common)
    if eeg.label == "stressed":
        return FusedState("stressed", eeg.reason, **common)
    if heart.label == "stressed":
        return FusedState("stressed", heart.reason, **common)

    # 2. Push harder. Every channel that has an opinion must agree, so any
    #    single doubt is enough to hold difficulty where it is.
    if eeg.label == "focused":
        if heart.label == "stressed":          # unreachable above; kept explicit
            return FusedState("neutral", "heart contradicts eeg-focused", **common)
        if face.label == "negative":
            return FusedState("neutral",
                              f"{face.reason} withholding eeg-focused increase",
                              increase_withheld=True, **common)
        return FusedState("focused", eeg.reason, **common)

    # 3. Nothing to act on. An EEG that said nothing is reported as such,
    #    not as neutral: "couldn't read it" and "read it, it's fine" are
    #    different, and only one of them is a reason to check the headband.
    if eeg.label is None:
        label = ("insufficient_signal" if eeg.cause == "low_confidence"
                 else "no_eeg")
        return FusedState(label, eeg.reason, **common)
    return FusedState("neutral", eeg.reason, **common)
