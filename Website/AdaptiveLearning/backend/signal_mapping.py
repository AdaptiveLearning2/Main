"""Sidecar payloads to database rows, for both ingestion paths.

Two paths now reach these tables and they need the same arithmetic:

- **pull** — `eeg_poller` runs inside this backend and polls the sidecar over
  HTTP. Works only because `start.ps1` puts backend and sidecar on one machine.
- **push** — the sidecar runs on the student's own device and POSTs to
  `/api/signals/*` with the student's bearer token. This is the deployment the
  camera forces: a hosted backend has no route to a laptop.

The mapping used to live in `eeg_client.py`, which is the *pull transport*. The
push path would have had to import an HTTP client it never calls to reach a pure
function, or keep a second copy — and a second copy of a unit conversion is how
one path ends up storing percentages while the other stores ratios, with nothing
to notice but a chart that looks wrong later.

Nothing here does I/O, so both callers can be tested without a sidecar.
"""

from __future__ import annotations

import math

from typing import Any


def _ratio(value: Any) -> float | None:
    """Rescale a 0..100 score to 0..1, clamped.

    This used to sniff the scale with `if v > 1.5: v /= 100`, which inverted the
    bottom of the range: `SignalProcessor.update` always returns ratio * 100.0,
    so a genuine focus_score of 1.2 (meaning 1.2%) fell under the threshold,
    skipped the divide, and clamped to 1.0 -- stored as *100%* focus. That is
    exactly the disengaged region that should be driving difficulty down.

    The producer contract is fixed, so there is nothing to detect: divide
    unconditionally.
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    # `math.isfinite`, not just the clamp. Every comparison against NaN is
    # False, so `min(1.0, nan)` is 1.0 and `max(0.0, 1.0)` is 1.0 -- a NaN
    # focus_score would be stored as **100% focus**, which is the same
    # "disengaged region recorded as full engagement" failure the scale-sniffing
    # above caused, and it pushes difficulty up. stdlib json parses NaN happily,
    # so a sidecar can send one. Same rule as `_env_number`'s in main.py.
    if not math.isfinite(v):
        return None
    return max(0.0, min(1.0, v / 100.0))


def _raw(payload: dict, **derived: Any) -> dict:
    """The `raw` blob: what the caller supplied, plus what we derived.

    The caller's own `raw` is merged rather than replaced. The ingest endpoints
    accept one from the client and stored it verbatim before these mappers
    existed; building a fresh dict here silently dropped it, which is a data
    loss no test noticed because nothing asserted on a field the endpoint only
    passed through.

    Derived keys win on a collision -- they describe what this backend observed,
    and a client should not be able to overwrite that by choosing a key name.
    Nulls are dropped so an absent field does not read as a recorded null.
    """
    merged = dict(payload.get("raw") or {})
    merged.update({k: v for k, v in derived.items() if v is not None})
    return merged


# The eight columns that hold measurements. Nulled together, because a row that
# can vouch for some of them and not others does not arise: they all come from
# the same electrodes in the same window.
_MEASUREMENT_COLUMNS = ("focus", "stress", "engagement",
                        "alpha", "beta", "theta", "delta", "gamma")


def eeg_quality(eeg: dict) -> str:
    """What may be stored from this payload: `ok`, `contact_poor`, `no_signal`.

    Lives here rather than in either caller because both paths need it and the
    answer must not differ between them. It did: the poller had both rules
    inline and the push path had neither, so the same unworn headband wrote
    nothing under pull and a zeroed row per tick under push.

    Zeros are worse than nulls and much worse than nothing. Aggregates average
    them as real readings where nulls are excluded, so a headband sitting on the
    desk read as *sustained zero focus* rather than as no data -- the
    can't-tell-no-data-from-zero failure the reporting rules exist to prevent,
    arriving through the write side where none of those rules can see it.
    """
    f = eeg.get("features") or {}
    quality = f.get("signal_quality")
    if quality == "no_signal":
        return "no_signal"
    # `quality_basis` matters. The legacy heuristic reports "poor" for any
    # focused student and says nothing about contact, so treating that as bad
    # electrodes would silently disable collection for a whole session.
    if quality == "poor" and f.get("quality_basis") == "contact":
        return "contact_poor"
    return "ok"


def map_eeg_to_cognitive(eeg: dict, session_id: str, user_id: str) -> dict | None:
    """Sidecar EEG payload to a `cognitive_signals` row.

    The sidecar reports focus/calm/confidence on a fixed 0..100 scale; this
    table stores 0..1 ratios.

    `None` when there is no signal -- a disconnected headband, whose zeroed
    scores are not a reading of zero. Same contract as the heart and face
    mappers, which already returned None for a channel that produced nothing,
    and the reason all three now do: a row of zeros is counted as a sample by
    every aggregate downstream.

    On poor *contact* the row is kept and the measurements are nulled. "We were
    recording but could not measure" is a different claim from "no session
    happened", and the timeline matters: `class_live` derives staleness from the
    newest row's `ts`, so dropping these would age out a session in which the
    student is still working.
    """
    verdict = eeg_quality(eeg)
    if verdict == "no_signal":
        return None

    f = eeg.get("features") or {}
    b = eeg.get("bands") or {}

    focus = _ratio(f.get("focus_score"))
    calm = _ratio(f.get("calm_score"))
    confidence = _ratio(f.get("confidence"))

    row = {
        "session_id": session_id,
        "user_id": user_id,
        "ts": eeg.get("timestamp"),
        "focus": focus,
        "engagement": confidence,
        # `stress` is `1.0 - calm`, and there is no `calm` column -- so this
        # column *is* the calm score, stored inverted. It is not a measurement
        # of stress and must never be averaged with `heart_signals.stress_score`,
        # which is one. See the CLAUDE.md rule of the same name.
        "stress": (1.0 - calm) if calm is not None else None,
        "alpha": b.get("alpha"),
        "beta": b.get("beta"),
        "theta": b.get("theta"),
        "delta": b.get("delta"),
        "gamma": b.get("gamma"),
        # Through `_raw()` like the other two mappers. Built inline, this dropped
        # any `raw` the caller supplied -- invisible while the only caller was
        # the poller, which supplies none, and a silent data loss the moment the
        # push path started forwarding one.
        "raw": _raw(
            eeg,
            device_id=eeg.get("device_id"),
            channels=eeg.get("channels"),
            state=eeg.get("state"),
            signal_quality=f.get("signal_quality"),
            # Stored alongside signal_quality because it is what distinguishes a
            # row whose measurements were nulled for bad electrode contact from
            # one where the legacy heuristic merely said "poor". Without it, a
            # null-measurement row cannot be explained after the fact.
            quality_basis=f.get("quality_basis"),
            ingestion=eeg.get("ingestion"),
        ),
    }
    if verdict == "contact_poor":
        # Every consumer of these columns has to handle null. Checked when this
        # rule was written: the teacher live view renders "-", and
        # LLM_topic_decider.get_session_signal_state filters `is not None` and
        # bails when nothing usable is left.
        for column in _MEASUREMENT_COLUMNS:
            row[column] = None
    return row


def map_heart_to_heart_signal(payload: dict, session_id: str, user_id: str) -> dict | None:
    """Sidecar camera/headband payload to a `heart_signals` row.

    Returns None when the payload carries no heart block -- a camera running
    emotion-only, or a headband whose optical channel is off. None rather than a
    row of nulls: an absent channel is not a reading of nothing, and a row would
    be counted as a sample by every aggregate downstream.

    Unlike the EEG mapping there is no rescaling. `heart_rate_bpm`, `rmssd_ms`
    and `stress_score` are absolute units all the way through -- the sidecar
    derives them in bpm, ms and 0..100 respectively, and the table stores them
    unchanged.
    """
    heart = payload.get("heart")
    if not heart:
        return None
    source = heart.get("source")
    if not source:
        # The table constrains `source`, and consent is decided per sensor, so a
        # row that cannot say which sensor produced it cannot be consent-checked.
        # Dropping it is the only safe answer.
        return None

    return {
        "session_id": session_id,
        "user_id": user_id,
        # The reading's own stamp in preference to the tick's. The headband's
        # block is a 25s window recomputed every 10s and held on the payload in
        # between -- so that a 1Hz poller sees every reading rather than most of
        # them -- and it arrives on ~40 consecutive ticks. Keyed on the tick,
        # one measurement becomes forty rows; keyed on itself, the unique
        # (session_id, source, ts) makes the repeats no-ops. The camera's block
        # carries no `ts` and falls back to the tick's, as it always did.
        "ts": heart.get("ts") or payload.get("timestamp"),
        "source": source,
        "heart_rate_bpm": heart.get("bpm"),
        "rmssd_ms": heart.get("rmssd_ms"),
        "sqi": heart.get("sqi"),
        "stress_score": heart.get("stress_score"),
        "stress_category": heart.get("stress_category"),
        # Carried rather than derived here. The sidecar owns the quality gate
        # and this is its verdict; recomputing it from `confidence` would put a
        # second, drifting definition of "trusted" in the system.
        "trusted": heart.get("trusted"),
        "raw": _raw(payload,
                    device_id=payload.get("device_id"),
                    confidence=heart.get("confidence"),
                    rejected_by=heart.get("rejected_by"),
                    measured_fps=heart.get("measured_fps"),
                    window_coverage=heart.get("window_coverage"),
                    # The headband's counterparts to measured_fps, under their
                    # own names: one is a camera's frame rate and one is a BLE
                    # link's sample rate, and a row that merged them could not
                    # be read back to say which sensor was struggling. `_raw`
                    # drops the nulls, so a camera row is unchanged.
                    sample_rate_hz=heart.get("sample_rate_hz"),
                    largest_gap_s=heart.get("largest_gap_s"),
                    channel_count=heart.get("channel_count"),
                    # RMSSD's own gates, kept apart from `rejected_by` above.
                    # A row can carry a good heart rate and no RMSSD -- roughly
                    # one window in five does -- and these say which of the two
                    # was refused, so a null `rmssd_ms` beside a real
                    # `heart_rate_bpm` is readable rather than a puzzle.
                    beat_coverage=heart.get("beat_coverage"),
                    rmssd_rejected_by=heart.get("rmssd_rejected_by"),
                    ingestion=payload.get("ingestion")),
    }


def map_face_to_face_signal(payload: dict, session_id: str, user_id: str) -> dict | None:
    """Sidecar camera payload to a `face_signals` row.

    None when there is no face block, for the same reason as above.

    `emotion_confidence` and `identity_confidence` are both carried and are not
    interchangeable: one is how sure the classifier is of the expression, the
    other how sure it is whose face this is. The fusion rule reads only the
    first, and read the second by mistake once.
    """
    face = payload.get("face")
    if not face:
        return None

    return {
        "session_id": session_id,
        "user_id": user_id,
        "ts": payload.get("timestamp"),
        "emotion": face.get("emotion"),
        "emotion_confidence": face.get("emotion_confidence"),
        "emotion_trusted": face.get("trusted"),
        "attention": face.get("attention"),
        "gaze_x": face.get("gaze_x"),
        "gaze_y": face.get("gaze_y"),
        "identity_confidence": face.get("identity_confidence"),
        "raw": _raw(payload,
                    device_id=payload.get("device_id"),
                    rejected_by=face.get("rejected_by"),
                    degraded=face.get("degraded"),
                    ingestion=payload.get("ingestion")),
    }
