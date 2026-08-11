"""Combine EEG, heart and facial channels into one difficulty signal.

Pure functions over already-read values. Nothing here touches the database or a
model, so the rule can be tested exhaustively — which matters, because the thing
it decides is how hard a question a child is given, and the failure modes are
quiet ones.

Why the rule is asymmetric
--------------------------
**Easing off wins; pushing harder defers.** To *raise* difficulty every
available channel must agree. To *lower* it, any one trusted channel suffices.

That is not a hedge, it is the only shape that fails safely. A wrong ease-off
costs a student one question below their level. A wrong push costs a student who
is already struggling a harder question, and the signals are least reliable
exactly when a student is agitated — which is when a false `focused` is most
likely and most damaging.

The shipped code was already asymmetric in this direction and this extends it
rather than replacing it: `stressed` overrode a manual bias setting, while
`focused` applied only when the student had left the control on Auto. That
property is preserved.

What each combination does
--------------------------
| channels present | behaviour |
| --- | --- |
| none | correctness, topic history and manual bias only — today's behaviour |
| EEG only | today's behaviour |
| heart only | can ease difficulty alone; **cannot raise it** |
| facial only | weak modifier; never decides alone |
| EEG + heart | full rule |

So adding a channel can only ever make sessions gentler, never more aggressive.
Correctness still raises difficulty independently; these only modulate it.

The facial caveat, stated where it is enforced
----------------------------------------------
FER+ is trained predominantly on adult faces, and expression inference is least
reliable on exactly this product's users: children, and children with learning
disabilities, whose expressions are more variable and more often misclassified.
So emotion is deliberately the weakest input here — it can *withhold* a
difficulty increase and can never cause one, and it cannot trigger an ease-off
by itself either.

It should not graduate to a primary adaptation signal without validation on the
actual user group. `EMOTION_MIN_CONFIDENCE` below is inherited from PR #49 and
is a guess, not a measurement; treat it as one.

Absent is not calm
------------------
Every "no reading" path returns a state that changes nothing, and the reason
string distinguishes the cases a reader would otherwise conflate: a **revoked**
channel is a respected refusal, not a hardware fault; a channel **calibrating**
after a failover is not a calm one; a channel **live but untrusted** is not an
absent one. Same argument as the reporting rules in CLAUDE.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# EEG thresholds. Mirrored from adaptation.py, where the same numbers gate the
# sidecar's own (now removed) policy.
EEG_MIN_CONFIDENCE = 0.45
EEG_FOCUSED_FOCUS_MIN = 0.7
EEG_FOCUSED_CALM_MIN = 0.5
EEG_STRESSED_CALM_MAX = 0.35

# Facial. Inherited from PR #49 and unvalidated on this user group -- see the
# module docstring. Only ever used to withhold an increase.
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
    something, because "no label" has several causes and they are not
    interchangeable.

    `cause` carries that distinction *structurally*. `fuse` used to derive it by
    sniffing for `"confidence" in reason`, which was correct against every
    string here and would have silently reclassified an outcome the first time
    someone reworded a message. The reason stays for humans; control flow reads
    `cause`.
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

    Source-agnostic by design: headband optics, headband PPG and camera rPPG
    all arrive here as the same three fields. The rule does not change when the
    source does; only the reason string names it.

    `calibrating` is called out separately because it is a *transient* absence
    with a known end -- a failover that has not yet built a baseline -- and
    reporting it as "no reading" would make a recovering sensor look broken.
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
    # "calm" is reserved, not consumed: `fuse` never reads it, because a calm
    # heart is not on its own a reason to raise difficulty -- rule 2 requires
    # EEG to agree. Kept so the diagnostic `channels` map distinguishes "read it,
    # it was fine" from "could not read it", which is the same distinction the
    # None branches above exist for.
    return ChannelState("calm", f"heart {stress_category} ({source})", source)


def face_channel(
    emotion: str | None,
    emotion_confidence: float | None,
    emotion_trusted: bool | None = None,
    *,
    revoked: bool = False,
) -> ChannelState:
    """The facial channel, which is only ever allowed to withhold.

    Named `emotion_confidence`, not `confidence`, and it keeps that name now
    that it is the only confidence `face_signals` carries. It used to sit beside
    `identity_confidence` -- how sure we are whose face this is, against how
    sure we are of the expression -- and an earlier revision passed the wrong
    one here, so a clearly identified face with a garbage FER+ label withheld a
    difficulty increase, while a well-classified expression on a poorly
    identified face was thrown away. Both silent. Face identity was retired in
    #86 without ever having a producer, so the specific confusion is gone; the
    qualified name stays, because a bare `confidence` re-opens it the moment a
    second one lands. The migration that added the column predicted exactly
    this ("an unqualified name leaves a reader a 50/50 guess about which one it
    gates"), so the parameter is named for the column rather than the concept.

    Returns "negative" or "neutral", never "stressed" -- the vocabulary is
    deliberately different from the other two so that no later edit can wire it
    into the ease-off branch by pattern-matching on a label name.
    """
    if revoked:
        return ChannelState(None, "face revoked", cause="revoked")
    if not emotion:
        return ChannelState(None, "no face samples", cause="no_samples")
    if emotion_trusted is False:
        # Hard reject, matching how heart_channel treats `trusted`. The
        # classifier said it does not stand behind this label; a confidence
        # figure alongside that is not a second opinion.
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

    # 1. Ease off. Either channel alone is enough, and a trusted elevated heart
    #    overrides an EEG that reads calm -- the one case where a channel
    #    contradicts EEG and wins. Checked first so it cannot be reached past
    #    an increase.
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
                              **common)
        return FusedState("focused", eeg.reason, **common)

    # 3. Nothing to act on. An EEG that said nothing at all is reported as such
    #    rather than as neutral: "we could not read" and "we read, it was fine"
    #    are different, and only one of them is a reason to check the headband.
    if eeg.label is None:
        label = ("insufficient_signal" if eeg.cause == "low_confidence"
                 else "no_eeg")
        return FusedState(label, eeg.reason, **common)
    return FusedState("neutral", eeg.reason, **common)
