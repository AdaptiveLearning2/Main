"""Sidecar payloads to database rows, for both ingestion paths.

Two paths reach these tables and both need the same arithmetic:

- **pull** -- `eeg_poller` runs inside this backend and polls the sidecar over
  HTTP. Works only because `start.ps1` puts backend and sidecar on one machine.
- **push** -- the sidecar runs on the student's own device and POSTs to
  `/api/signals/*` with the student's bearer token. This is the deployment the
  camera forces: a hosted backend has no route to a laptop.

Kept out of `eeg_client.py` (the pull transport) so the push path doesn't need
to import an HTTP client it never calls just to reach a pure function. A second
copy of this unit conversion is how one path ends up storing percentages while
the other stores ratios, with nothing to notice but a chart that looks wrong
later.

Nothing here does I/O, so both callers can be tested without a sidecar.
"""

from __future__ import annotations

import math

from typing import Any


def _ratio(value: Any) -> float | None:
    """Rescale a 0..100 score to 0..1, clamped.

    `SignalProcessor.update` always returns ratio * 100.0, so the input scale
    is fixed. Divide unconditionally rather than guessing the scale from the
    value -- a low score like 1.2 (meaning 1.2%) must not be mistaken for an
    already-0..1 value and clamped up to 1.0, which would read as full focus
    in exactly the disengaged region that should drive difficulty down.
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    # Check `math.isfinite`, not just clamp. Every comparison against NaN is
    # False, so `min(1.0, nan)` is 1.0 -- a NaN focus_score would be stored
    # as 100% focus and push difficulty up. stdlib json parses NaN happily,
    # so a sidecar can send one. Same rule as `_env_number` in main.py.
    if not math.isfinite(v):
        return None
    return max(0.0, min(1.0, v / 100.0))


def _raw(payload: dict, **derived: Any) -> dict:
    """The `raw` blob: what the caller supplied, plus what we derived.

    The caller's own `raw` is merged in, not replaced -- building a fresh dict
    here would silently drop whatever the client sent.

    Derived keys win on a collision: they describe what this backend observed,
    and a client should not be able to overwrite that by choosing a key name.
    That holds when the derived value is None too -- the client's value under
    that key is removed, not kept. Filtering the None out and leaving the
    client's in place let a posted `raw.confidence` stand for a tick the
    sidecar reported none on, straight into the fusion gate. Nulls are still
    not written, so an absent field doesn't read as a recorded null.
    """
    merged = dict(payload.get("raw") or {})
    for key, value in derived.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


# The eight measurement columns. Nulled together: they all come from the same
# electrodes in the same window, so a row can't vouch for some and not others.
# Bumped whenever the sidecar's population bounds -- the scale every score is
# measured on -- change. Written into `raw.score_scale` on each row.
SCORE_SCALE_VERSION = 2
# The local calm source (the sidecar's own spectrum) is a different number on
# a different span, so it is its own scale: two sidecars on one class, one
# flipped to local, would otherwise write incomparable calm values into one
# rollup under one version. An unknown source takes the SDK scale.
SCORE_SCALE_BY_CALM_SOURCE = {"sdk": SCORE_SCALE_VERSION, "local": 3}
# A local calm carried past this many seconds without a fresh estimate is
# stale rather than held: the row keeps focus and records why, and `stress`
# is nulled so the rollup does not average a number nothing measured.
CALM_HOLD_MAX_SECONDS = 10.0

_MEASUREMENT_COLUMNS = ("focus", "stress", "engagement",
                        "alpha", "beta", "theta", "delta", "gamma")


def eeg_quality(eeg: dict) -> str:
    """What may be stored from this payload: `ok`, `contact_poor`, `no_signal`.

    Lives here rather than in either caller, because both paths need it and the
    answer must not differ between them.

    Zeros are worse than nulls: aggregates average them in as real readings,
    where nulls are excluded. A headband sitting on the desk must read as no
    data, not as sustained zero focus.
    """
    f = eeg.get("features") or {}
    quality = f.get("signal_quality")
    if quality == "no_signal":
        return "no_signal"
    # `quality_basis` matters: the legacy heuristic reports "poor" for any
    # focused student, not just bad contact. Treating that as bad electrodes
    # would silently disable collection for a whole session.
    if quality == "poor" and f.get("quality_basis") == "contact":
        return "contact_poor"
    return "ok"


def map_eeg_to_cognitive(eeg: dict, session_id: str, user_id: str) -> dict | None:
    """Sidecar EEG payload to a `cognitive_signals` row.

    The sidecar reports focus/calm/confidence on a fixed 0..100 scale; this
    table stores 0..1 ratios.

    `None` when there is no signal -- a disconnected headband's zeroed scores
    are not a reading of zero. Same contract as the heart and face mappers: a
    row of zeros would be counted as a sample by every aggregate downstream.

    On poor *contact* the row is kept and the measurements are nulled.
    "Recording but couldn't measure" is different from "no session happened" --
    `class_live` derives staleness from the newest row's `ts`, so dropping
    these would age out a session where the student is still working.
    """
    verdict = eeg_quality(eeg)
    if verdict == "no_signal":
        return None

    f = eeg.get("features") or {}
    b = eeg.get("bands") or {}

    focus = _ratio(f.get("focus_score"))
    calm = _ratio(f.get("calm_score"))
    confidence = _ratio(f.get("confidence"))
    # Client-supplied on the push path. Only a string names a source; a
    # dict or list here was a dict key and 500'd the ingest request.
    calm_source = f.get("calm_source") if isinstance(f.get("calm_source"), str) else None
    # The two keys that gate the stress column, validated the same way. A
    # value that is present and not what the sidecar sends is *withheld*,
    # not recorded: "false" is not False, and "150" is not 150, and both
    # read as a measured, fresh calm -- the number the hold rule exists to
    # withhold. Absent is an older sidecar and means measured.
    calm_measured = f.get("calm_measured")
    calm_measured_ok = calm_measured is None or isinstance(calm_measured, bool)
    held = f.get("calm_held_seconds")
    held_ok = held is None or (isinstance(held, (int, float)) and not isinstance(held, bool)
                               and math.isfinite(held) and held >= 0)

    row = {
        "session_id": session_id,
        "user_id": user_id,
        "ts": eeg.get("timestamp"),
        "focus": focus,
        # `engagement` is the focus index -- beta/(alpha+theta), which is
        # what the literature calls engagement (Pope et al.) and what
        # `focus_score` already is. It was `confidence` until Phase 1 of the
        # EEG accuracy work, and confidence is a signal-quality number, so
        # every Engagement tile was showing how well the strap fitted.
        # `avg_engagement` in the daily rollup and the term trend is
        # discontinuous across the date that landed; see CLAUDE.md.
        "engagement": focus,
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
        # Through `_raw()` like the other two mappers, so a caller-supplied
        # `raw` isn't silently dropped.
        "raw": _raw(
            eeg,
            device_id=eeg.get("device_id"),
            channels=eeg.get("channels"),
            state=eeg.get("state"),
            signal_quality=f.get("signal_quality"),
            # Distinguishes a row nulled for bad electrode contact from one
            # where the legacy heuristic just said "poor", so a null-measurement
            # row can still be explained later.
            quality_basis=f.get("quality_basis"),
            # Why this tick was held, when it was: a held score is the
            # previous tick's, and a row must be able to say so.
            artifact_reason=f.get("artifact_reason"),
            # Which spectrum calm came from, whether it was measured this
            # session at all (a placeholder at the midpoint is not), and how
            # long a local calm has been carried without a fresh estimate.
            # The decider picks the stressed line by source, and two
            # sidecars on one class writing calm on two scales into one
            # rollup is the gap the score-scale version exists to close --
            # so the version is per source too (SCORE_SCALE_BY_CALM_SOURCE).
            calm_source=calm_source,
            calm_measured=calm_measured if calm_measured_ok else None,
            calm_held_seconds=held if held_ok else None,
            ingestion=eeg.get("ingestion"),
            # The EEG signal-quality number, 0..1. No column carries it --
            # `engagement` did until it became the focus index -- and it is
            # what `signal_fusion.eeg_channel` gates on, so it rides in `raw`
            # for `LLM_topic_decider` to read back. Dropping it made the
            # fusion gate a focus threshold: a disengaged student on good
            # contact lost the whole EEG channel, ease-off included.
            confidence=confidence,
            # Which population scale the scores were measured on. The
            # sidecar's bounds were widened on 2026-09-14 (scale 2), which
            # re-anchors every focus and stress value -- 14 to 30 points
            # pre-latch, ~38% of gain after -- and no column records that.
            # Rows without the key predate it. The rollup carries no `raw`,
            # so there the boundary is the date in CLAUDE.md.
            score_scale=SCORE_SCALE_BY_CALM_SOURCE.get(calm_source or "sdk",
                                                       SCORE_SCALE_VERSION),
        ),
    }
    if (calm_measured is False or not calm_measured_ok or not held_ok
            or (held is not None and held > CALM_HOLD_MAX_SECONDS)):
        # A placeholder calm (never measured this session) or a stale one
        # (carried too long without a fresh estimate) is not a stress
        # reading. Focus stays; `raw` says why. Measured: 150 s of unready
        # ticks all reported one calm, with nothing on the row saying so.
        row["stress"] = None
    if verdict == "contact_poor":
        # Every consumer of these columns must handle null: the teacher live
        # view renders "-", and LLM_topic_decider filters `is not None` and
        # bails when nothing usable is left.
        for column in _MEASUREMENT_COLUMNS:
            row[column] = None
        # The confidence goes with them. It rode in `engagement` when that
        # was among the nulled columns; moved to `raw` it survived, so a
        # poor-contact row with no focus still contributed a confidence
        # capped under the gate -- four such rows beside one good one
        # averaged focus 0.8 against confidence 0.36 and dropped the whole
        # EEG channel, ease-off included. An unmeasured row has no opinion.
        row["raw"].pop("confidence", None)
    if f.get("artifact_reason") == "malformed_bands":
        # A tick whose bands could not be read carries a *held* score, or
        # the midpoint if nothing was held -- not a measurement. Contact is
        # fine on such a tick, so the quality verdict is `ok` and nothing
        # above nulls it; stored, the rollup averaged a value never measured
        # and counted it as trusted. Same shape as contact_poor: keep the
        # row, null what was not measured, keep the reason in `raw`.
        for column in _MEASUREMENT_COLUMNS:
            row[column] = None
        row["raw"].pop("confidence", None)
    return row


def map_heart_to_heart_signal(payload: dict, session_id: str, user_id: str) -> dict | None:
    """Sidecar camera/headband payload to a `heart_signals` row.

    Returns None when the payload carries no heart block -- a camera running
    emotion-only, or a headband with its optical channel off. None rather than
    a row of nulls: an absent channel isn't a reading of nothing, and a row
    would be counted as a sample by every aggregate downstream.

    Unlike the EEG mapping, no rescaling here. `heart_rate_bpm`, `rmssd_ms` and
    `stress_score` are absolute units already (bpm, ms, 0..100) and stored
    unchanged.
    """
    heart = payload.get("heart")
    if not heart:
        return None
    source = heart.get("source")
    if not source:
        # Consent is decided per sensor, so a row that can't say which sensor
        # produced it can't be consent-checked. Dropping it is the only safe
        # answer.
        return None

    return {
        "session_id": session_id,
        "user_id": user_id,
        # Prefer the reading's own stamp over the tick's. The headband's block
        # is a 25s window, recomputed every 10s and held on the payload in
        # between, so it arrives on ~40 consecutive ticks. Keyed on the tick,
        # one measurement becomes forty rows; keyed on itself, the unique
        # (session_id, source, ts) makes the repeats no-ops. The camera's
        # block carries no `ts`, so it falls back to the tick's.
        "ts": heart.get("ts") or payload.get("timestamp"),
        "source": source,
        "heart_rate_bpm": heart.get("bpm"),
        "rmssd_ms": heart.get("rmssd_ms"),
        "sqi": heart.get("sqi"),
        "stress_score": heart.get("stress_score"),
        "stress_category": heart.get("stress_category"),
        # Carried, not derived: the sidecar owns the quality gate. Recomputing
        # "trusted" from `confidence` here would create a second, drifting
        # definition.
        "trusted": heart.get("trusted"),
        "raw": _raw(payload,
                    device_id=payload.get("device_id"),
                    confidence=heart.get("confidence"),
                    rejected_by=heart.get("rejected_by"),
                    measured_fps=heart.get("measured_fps"),
                    window_coverage=heart.get("window_coverage"),
                    # Headband counterparts to measured_fps, kept under their
                    # own names: a camera's frame rate and a BLE link's sample
                    # rate shouldn't be merged into one field, or a row can't
                    # say which sensor was struggling. `_raw` drops nulls, so a
                    # camera row is unaffected.
                    sample_rate_hz=heart.get("sample_rate_hz"),
                    largest_gap_s=heart.get("largest_gap_s"),
                    channel_count=heart.get("channel_count"),
                    # RMSSD has its own gate, separate from `rejected_by`
                    # above: a row can have a good heart rate and no RMSSD
                    # (about one window in five does), and these say which of
                    # the two was refused.
                    beat_coverage=heart.get("beat_coverage"),
                    rmssd_rejected_by=heart.get("rmssd_rejected_by"),
                    ingestion=payload.get("ingestion")),
    }


def map_face_to_face_signal(payload: dict, session_id: str, user_id: str) -> dict | None:
    """Sidecar camera payload to a `face_signals` row.

    None when there is no face block, for the same reason as above.

    `emotion_confidence` keeps its qualified name even though it's the only
    confidence here -- a bare `confidence` would reopen an ambiguity that
    already caused a silent bug. See `signal_fusion.face_channel`.
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
        # Where the head points, not where the eyes point within it. Degrees;
        # see face_geometry for sign conventions.
        "head_yaw": face.get("head_yaw"),
        "head_pitch": face.get("head_pitch"),
        "head_roll": face.get("head_roll"),
        "raw": _raw(payload,
                    device_id=payload.get("device_id"),
                    rejected_by=face.get("rejected_by"),
                    # Its own field, like `rmssd_rejected_by` on the heart row:
                    # emotion and gaze are two separate measurements, so one
                    # refusal field can't say which failed.
                    gaze_rejected_by=face.get("gaze_rejected_by"),
                    pose_rejected_by=face.get("pose_rejected_by"),
                    degraded=face.get("degraded"),
                    ingestion=payload.get("ingestion")),
    }
