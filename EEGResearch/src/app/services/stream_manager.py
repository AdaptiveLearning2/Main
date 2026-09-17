from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from src.app.config import DEFAULT_DEVICE_ID, DeviceConfig, Settings, get_settings, parse_eeg_devices
from src.app.services.adaptation import AdaptationEngine
from src.app.services.eeg_ingestion import build_ingestion_adapter, enrich_ingestion_dict
from src.app.services.optics_processing import (
    EMIT_EVERY_SECONDS,
    RATE_WINDOW_SECONDS,
    build_heart_record,
)
from src.app.services.ppg_processing import HeartRateTracker
from src.app.services.eeg_spectrum import SpectrumEstimator, poisons_buffer
from src.app.services.signal_processing import SignalProcessor

logger = logging.getLogger(__name__)

# Bumped whenever a field is removed from the EEG payload, even though
# nothing in code gates on this string -- a consumer diffing two recordings
# shouldn't have to guess why a field vanished.
CONTRACT_VERSION = "1.3.0"


class UnknownDeviceError(KeyError):
    """Raised when a device_id doesn't match any device in the registry."""


def _finite_or_none(value) -> float | None:
    """A float, or None when the value is NaN, infinite or not a number.
    For the snapshot's band block; see BandData."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


class DeviceSession:
    """Owns one physical EEG stream: adapter, processor, adaptation engine, and the
    background polling loop. One instance per entry in the device registry."""

    CONTRACT_VERSION = CONTRACT_VERSION
    # Upper bound on samples drained from the adapter in one tick. Matches
    # TcpMuseBridgeAdapter.EEG_QUEUE_MAXSIZE so a full queue can always be
    # caught up in a single drain rather than trailing behind tick after tick.
    DRAIN_MAX_BATCH = 2048

    def __init__(self, device_id: str, settings: Settings, device_config: DeviceConfig) -> None:
        self.device_id = device_id
        self.settings = settings
        self.device_config = device_config
        self.adapter = build_ingestion_adapter(
            settings,
            kind=device_config.kind,
            host=device_config.host,
            port=device_config.port,
            camera_index=device_config.camera_index,
        )
        # Where calm reads its spectrum; "sdk" unless EEG_SPECTRUM_SOURCE
        # says "local" (services/eeg_spectrum.py). The estimator runs either
        # way, so the payload can carry the local figure for comparison
        # while the SDK one is still what the score uses.
        self.processor = SignalProcessor(
            calm_source=settings.eeg_spectrum_source,  # validated in config.py
            calm_centre_on_arm=settings.eeg_calm_centre_on_arm)  # validated in config.py
        self.spectrum = SpectrumEstimator(poison_seconds=settings.eeg_spectrum_poison_seconds)
        self.adaptation = AdaptationEngine()
        # Heart rate off the headband's optical channels. Held per session
        # because continuity -- the only check that catches an octave error --
        # compares each window against the last one this device produced.
        self._heart_tracker: HeartRateTracker | None = None
        self._heart_block: dict[str, Any] | None = None
        # Two clocks for two different questions: when the window was last
        # recomputed (the emit cadence), and when a rate was last accepted
        # (how stale the continuity anchor is).
        self._heart_emitted_at: float | None = None
        self._heart_accepted_at: float | None = None
        self.latest_payload: dict[str, Any] = {}
        self.samples_processed = 0
        self.errors_seen = 0
        # Device health, for the status endpoints. `errors_seen` above is a
        # lifetime total nothing could act on; these say what is happening
        # *now*. Both None/0 until the first good tick, and reset by stop().
        self.last_good_at: float | None = None
        self.last_good_ts: str | None = None
        self.consecutive_errors = 0
        # monotonic() at which requested_preset first disagreed with
        # active_preset, or None while they agree (or either is unknown).
        self._preset_mismatch_since: float | None = None
        self._task: asyncio.Task[None] | None = None
        self.running = False
        # Set by StreamManager when push ingestion is on. A plain callable
        # rather than a reference to the push client, so this module stays
        # unaware of whether anything is shipped off the machine -- the
        # sampling loop must not be able to fail because of the network.
        self.on_payload: Callable[[dict[str, Any]], None] | None = None

    async def _emit(self) -> None:
        """Hand the tick's payload to whatever is consuming it.

        Swallows everything. A consumer that raises would otherwise kill the
        sampling loop, which is the one thing in this process that must keep
        running: losing the local stream to fix up a remote write is strictly
        worse than losing the remote write.

        Runs `snapshot()` off the event loop thread, since it calls
        `adapter.get_ingestion_meta()` -- the same call `_loop` already wraps
        in `to_thread` because it can block on lock contention in the TCP
        adapter. Calling it inline here would put it back on the loop and
        make every API request wait behind a tick.
        """
        if self.on_payload is None:
            return
        try:
            # snapshot(), not latest_payload -- bands and ingestion are only
            # assembled in snapshot(), so emitting latest_payload directly
            # would ship EEG rows with null band powers.
            self.on_payload(await asyncio.to_thread(self.snapshot))
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.warning("payload consumer failed for device %s: %s: %s",
                           self.device_id, type(exc).__name__, exc)

    def _optical_heart_block(self) -> dict[str, Any] | None:
        """The `heart` block for this tick, or None if this device has no optics.

        **Recomputed on a cadence, not per tick.** It's a 25s window either
        way, so a 4Hz tick would run an autocorrelation over the same samples
        sixteen times for the same answer. `MAX_BPM_CHANGE_PER_S`, the
        continuity rule that rejects octave errors, is calibrated against the
        10s step the estimator was validated on -- a quarter-second step
        would make every window's predecessor 99% itself.

        **Held between recomputes, and carrying its own `ts`.** A block that
        appeared for one tick and vanished would be seen by a 4Hz push
        consumer and mostly missed by a 1Hz poller, so the two deployments
        would record different sessions from the same headband. Holding it
        means both see every reading; the stamp is what lets both write it
        exactly once.
        """
        window_fn = getattr(self.adapter, "optics_window", None)
        if window_fn is None:
            # Not a headband, or a headband whose adapter can't buffer
            # optics. The simulator has one since 2026-09-16 (it synthesises
            # a pulse for the classroom simulation, fed through this same
            # path); the camera is what lands here now.
            return None

        now = time.monotonic()
        if self._heart_emitted_at is not None and now - self._heart_emitted_at < EMIT_EVERY_SECONDS:
            return self._heart_block
        if self._heart_tracker is None:
            self._heart_tracker = HeartRateTracker()
        # Time since the last accepted rate, not since the last recompute.
        # The tracker uses this to size how far a heart is allowed to have
        # moved from its anchor, so measuring from the recompute would hold
        # the allowance at one step's worth however long refusals ran, and
        # judge the first good window in a minute against only 30 bpm of
        # movement. Self-corrects within a window or two, but in the
        # direction of discarding good readings, so it's worth getting right.
        #
        # On the first window there's no anchor at all; the nominal step is
        # the honest answer for "nothing measured yet", and the tracker
        # ignores the value entirely until it has one.
        since = (EMIT_EVERY_SECONDS if self._heart_accepted_at is None
                 else now - self._heart_accepted_at)
        self._heart_emitted_at = now
        self._heart_block = build_heart_record(
            window_fn(RATE_WINDOW_SECONDS), self._heart_tracker, since
        )
        if self._heart_block.get("bpm") is not None:
            self._heart_accepted_at = now
        return self._heart_block

    def _drop_held_heart_block(self) -> None:
        """Stop publishing the held reading, without restarting the cadence.

        Called on an EEG no-data tick. The block describes a stretch of
        signal that may have ended, so publishing it on would keep a rate on
        the dashboard -- and, under fresh timestamps, in the database -- for
        a headband that is off.

        **The clock is deliberately left running -- that's the whole point of
        splitting this from `_reset_heart`.** `drain_samples` raises whenever
        no EEG sample arrives within its timeout, so flapping electrode
        contact or a stalled bridge takes this path repeatedly. Clearing
        `_heart_emitted_at` here instead would make the next good tick
        recompute immediately and mint a new `ts` for essentially the same 25
        seconds of optical signal -- and since both writers dedupe on `ts`,
        `heart_session_source_ts_key` can't collapse those, so a flapping
        session would write up to four near-identical rows a second, each
        counted as a real sample by the aggregates.

        The tracker is left alone too. EEG dropping out says nothing about
        the optical emitters -- the optics buffer is untouched here and is
        cleared only by a real disconnect -- so discarding the continuity
        anchor would throw away the one test that catches an octave error,
        over evidence that isn't about the heart channel at all. A genuinely
        stale anchor is already handled: `HeartRateTracker` re-acquires after
        repeated continuity rejections.
        """
        self._heart_block = None

    def _reset_heart(self) -> None:
        """Forget everything about the heart channel. For `stop()` only.

        The session is over: the tracker's anchor, the held block and the
        cadence all describe a recording that has ended, and the adapter's
        optical buffer has been cleared alongside them.
        """
        self._heart_tracker = None
        self._heart_block = None
        self._heart_emitted_at = None
        self._heart_accepted_at = None

    def _face_payload(self, samples: list[Any], raw_meta: dict[str, Any]) -> dict[str, Any]:
        """The camera equivalent of the EEG payload.

        Carries the same envelope fields -- contract_version, device_id,
        timestamp -- so a consumer doesn't need to know which kind of device
        produced a record. `channels`, `features` and `state` are absent
        rather than faked: a camera has no electrode channels and no
        cognitive state, and inventing empty ones would let a caller average
        them into an EEG session's numbers.
        """
        # Imported here rather than at module scope. It's cheap and pulls in
        # no camera dependency -- the whole chain down to face_processing is
        # plain numpy, proven by the no-dependency import test -- but that
        # only holds by convention, and a module-scope import would put it on
        # every sidecar start, including headband-only ones. A local import
        # enforces it structurally instead of by remembering.
        from src.app.services.face_processing import (  # noqa: PLC0415
            RATE_WINDOW_SECONDS,
            build_camera_payload,
        )

        rgb, measured, quality, stamps = self.adapter.rgb_window(RATE_WINDOW_SECONDS)
        payload: dict[str, Any] = {
            # Names the shape, so the API union and any consumer can branch
            # on it rather than probing for which fields happen to be present.
            "kind": "camera",
            "contract_version": self.CONTRACT_VERSION,
            "device_id": self.device_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ingestion": enrich_ingestion_dict(
                self.settings, raw_meta, source=self.device_config.kind
            ),
        }
        payload.update(
            build_camera_payload(
                rgb_window=rgb,
                fps=self.adapter.fps,
                measured_fps=measured,
                timestamps=stamps,
                window_quality=quality,
                samples=samples,
                emotion=self.adapter.latest_emotion(),
                heart_enabled=self.adapter.heart_enabled,
                emotion_enabled=self.adapter.emotion_enabled,
                emotion_meta=raw_meta,
                # getattr, unlike `latest_emotion` beside it, because the
                # adapters here are duck-typed with no base class, and the
                # caller swallows exceptions into a warning. A missing
                # attribute would silently drop the entire camera payload
                # every tick -- heart and emotion included -- for the sake of
                # a channel that's off by default. Degrading gaze to "off"
                # instead is the smaller, more honest failure.
                gaze=getattr(self.adapter, "latest_gaze", lambda: None)(),
                gaze_enabled=getattr(self.adapter, "gaze_enabled", False),
                pose=getattr(self.adapter, "latest_pose", lambda: None)(),
            )
        )
        return payload

    async def start(self) -> None:
        if self.running:
            return
        # Adapter connect can block on network I/O (TCP bridge mode), so keep it
        # off the event loop thread to avoid freezing API request handling.
        await asyncio.to_thread(self.adapter.connect)
        self.running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        was_running = self._task is not None
        self.running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # Adapter disconnect can block on socket shutdown/thread joins.
        await asyncio.to_thread(self.adapter.disconnect)
        if was_running:
            # Stopping an active stream is itself a "no data" condition --
            # without this, snapshot() would keep returning the last reading
            # from before stop() forever, indistinguishable from a live
            # session. Skipped when stop() is called with no active stream
            # (a duplicate stop, or one racing ahead of start()) so a session
            # that never started reports "idle" rather than a fabricated
            # zero reading.
            #
            # clear_session(), not reset(): a stop is the end of a session,
            # not a gap in one. reset() keeps the baseline (rightly, for a
            # no-sample tick), and through here it kept it for the next
            # student on a shared station.
            self.processor.clear_session()
            self.spectrum.reset()
            self.adaptation.end_session()
            self._reset_heart()
            self.latest_payload = self._no_signal_payload()
            # The stream is over, so "last good reading" describes a session
            # that has ended. Cleared for the same reason latest_payload is.
            self.last_good_at = None
            self.last_good_ts = None
            self.consecutive_errors = 0
            self._preset_mismatch_since = None

    # A preset switch after CONNECTED interrupts and restores streaming, and
    # the headband's configuration is re-read live, so the two fields can
    # legitimately disagree for a moment. Only a disagreement that outlasts
    # this is a request the device ignored.
    PRESET_SETTLE_SECONDS = 5.0

    def _note_good_tick(self, timestamp: str) -> None:
        self.last_good_at = time.monotonic()
        self.last_good_ts = timestamp
        self.consecutive_errors = 0

    def _note_preset(self, raw_meta: dict[str, Any]) -> None:
        """Track whether the headband is on the preset the bridge asked for.

        Both empty when there is no headband, and the bridge clears both on
        disconnect, so an unknown side means "nothing to compare" rather than
        a mismatch.
        """
        requested = raw_meta.get("requested_preset") or ""
        active = raw_meta.get("active_preset") or ""
        if not requested or not active or requested == active:
            self._preset_mismatch_since = None
        elif self._preset_mismatch_since is None:
            self._preset_mismatch_since = time.monotonic()

    def health_fields(self) -> dict[str, Any]:
        """What `ingestion` carries about this device's liveness.

        `last_good_age_s` is derived at read time rather than stored, so a
        consumer sees it grow while nothing arrives instead of a fixed number
        that was true when the last tick happened. None before the first good
        tick -- "never" is not "very old".
        """
        age = None if self.last_good_at is None else round(time.monotonic() - self.last_good_at, 3)
        mismatch = (self._preset_mismatch_since is not None
                    and time.monotonic() - self._preset_mismatch_since >= self.PRESET_SETTLE_SECONDS)
        return {
            "last_good_ts": self.last_good_ts,
            "last_good_age_s": age,
            "errors_seen": self.errors_seen,
            "consecutive_errors": self.consecutive_errors,
            "preset_mismatch": mismatch,
        }

    async def _loop(self) -> None:
        period = 1 / max(1, self.settings.eeg_sample_hz)
        while self.running:
            try:
                # Adapter reads can block (especially TCP bridge mode before the first
                # EEG frame), so move them off the event loop thread to keep API
                # endpoints responsive. Queue-backed adapters (TcpMuseBridgeAdapter)
                # drain every frame buffered since the last tick; others
                # (SimulatedMuseIngestionAdapter) only ever produce one sample per
                # read, so fall back to that.
                if hasattr(self.adapter, "drain_samples"):
                    samples = await asyncio.to_thread(self.adapter.drain_samples, self.DRAIN_MAX_BATCH)
                else:
                    samples = [await asyncio.to_thread(self.adapter.read_sample)]
                # Metadata access can also block (lock contention in the TCP
                # adapter), so keep it off the event loop thread too.
                raw_meta = (
                    await asyncio.to_thread(self.adapter.get_ingestion_meta)
                    if hasattr(self.adapter, "get_ingestion_meta")
                    else {}
                )
            except Exception as exc:
                # No EEG data arrived this cycle (headset unplugged, bridge idle,
                # etc.). Zero the reported scores instead of leaving the last
                # successful reading frozen in place, and reset processor/adaptation
                # state so real scores don't resume by blending pre-gap and
                # post-gap samples.
                self.errors_seen += 1
                self.consecutive_errors += 1
                logger.debug(
                    "EEG read failed for device %s, reporting no signal: %s: %s",
                    self.device_id, type(exc).__name__, exc,
                )
                self.processor.reset()
                # The raw buffer too: whatever spans a gap is two recordings.
                self.spectrum.reset()
                self.adaptation.reset_for_signal_loss()
                # Not `_reset_heart()`: this path runs on every tick with no EEG
                # sample, which flapping contact does repeatedly, and restarting
                # the cadence would re-stamp the same window each tick with
                # neither dedupe able to collapse the rows. See that method.
                self._drop_held_heart_block()
                self.latest_payload = self._no_signal_payload()
                await asyncio.sleep(period)
                continue

            if self.device_config.kind == "face":
                # A camera produces colour, not EEG channels, so it can't go
                # through SignalProcessor at all.
                try:
                    self.latest_payload = self._face_payload(samples, raw_meta)
                    self.samples_processed += len(samples)
                    self._note_good_tick(self.latest_payload["timestamp"])
                    await self._emit()
                except Exception as exc:
                    self.errors_seen += 1
                    logger.warning(
                        "camera payload failed for device %s: %s: %s",
                        self.device_id, type(exc).__name__, exc,
                    )
                await asyncio.sleep(period)
                continue

            try:
                # Feed only the freshest drained sample through the processor.
                # SignalProcessor's rolling window and per-session baseline
                # (window_size, the artifact histories) are calibrated in
                # ticks, not raw samples -- they assume one processor.update()
                # call per tick. Calling update() once per drained sample
                # would break that: a single tick can carry dozens of samples
                # at the bridge's native rate, so the window would span
                # milliseconds instead of ~5s. (The baseline and the ratio
                # smoothing are on the sample clock and would survive it.) Draining the queue every tick
                # already prevents an unbounded backlog; it doesn't require
                # re-processing every buffered sample, just the newest one.
                sample = samples[-1]
                self._note_preset(raw_meta)
                # Every drained sample goes to the spectrum estimator -- this
                # is the one consumer of the raw stream, and it belongs here
                # rather than on a second socket the bridge does not offer.
                # The processor still scores one sample per tick.
                # Only a headband delivers the raw stream. The simulator
                # produces one sample per tick, and fed to a buffer windowed
                # by count at 256 Hz that was 256 s analysed as four -- a
                # plausible residual with nothing behind it, scored under
                # the local source. The estimator also checks the stamps'
                # span itself; this is the first line.
                if self.device_config.kind == "muse":
                    spectrum = self.spectrum.push(samples, raw_meta)
                else:
                    spectrum = self.spectrum.latest()
                features = self.processor.update(sample, raw_meta, spectrum=spectrum)
                if poisons_buffer(features.get("artifact_reason")):
                    # The gate held this tick; the window still holds the
                    # blink. No estimate until those samples have left it.
                    self.spectrum.poison()
                features["batch_size"] = len(samples)
                state = self.adaptation.infer_state(features)
                self.latest_payload = {
                    "contract_version": self.CONTRACT_VERSION,
                    "device_id": self.device_id,
                    "timestamp": sample.timestamp.isoformat(),
                    "channels": {
                        "tp9": sample.channel_tp9,
                        "af7": sample.channel_af7,
                        "af8": sample.channel_af8,
                        "tp10": sample.channel_tp10,
                    },
                    "features": features,
                    "state": {
                        "label": state.label,
                        "reason": state.reason,
                        "confidence": state.confidence,
                        "focus_score": state.focus_score,
                        "calm_score": state.calm_score,
                    },
                }
                # Alongside the cognitive block, not inside it: two sensors on
                # one device, separately consented and separately capable of
                # failing. The key is absent rather than null when the device
                # has no optical channel, keeping "switched off" distinct
                # from "could not measure" -- same reasoning as the camera
                # payload.
                heart = self._optical_heart_block()
                if heart is not None:
                    self.latest_payload["heart"] = heart
                self.samples_processed += len(samples)
                self._note_good_tick(self.latest_payload["timestamp"])
                await self._emit()
            except Exception as exc:
                # A real sample was read successfully, so this is a bug in the
                # processing pipeline (not a signal-loss condition) -- log and
                # skip this tick without touching signal-loss state.
                self.errors_seen += 1
                logger.warning(
                    "EEG sample processing failed for device %s: %s: %s",
                    self.device_id, type(exc).__name__, exc,
                )
            await asyncio.sleep(period)

    def _no_signal_payload(self) -> dict[str, Any]:
        return {
            "contract_version": self.CONTRACT_VERSION,
            "device_id": self.device_id,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "channels": {"tp9": 0.0, "af7": 0.0, "af8": 0.0, "tp10": 0.0},
            "features": {
                "focus_score": 0.0,
                "calm_score": 0.0,
                "confidence": 0.0,
                "signal_quality": "no_signal",
                # Present, not omitted, so the features shape is identical on
                # signal and no-signal ticks and consumers never have to
                # special-case its absence.
                "batch_size": 0,
            },
            "state": {
                "label": "no_signal",
                "reason": "No EEG data received",
                "confidence": 0.0,
                "focus_score": 0.0,
                "calm_score": 0.0,
            },
        }

    def snapshot(self) -> dict[str, Any]:
        out = dict(self.latest_payload)
        out.setdefault("contract_version", self.CONTRACT_VERSION)
        out.setdefault("device_id", self.device_id)
        no_signal = out.get("features", {}).get("signal_quality") == "no_signal"
        if hasattr(self.adapter, "get_ingestion_meta"):
            raw_meta = self.adapter.get_ingestion_meta()
            ing = enrich_ingestion_dict(self.settings, raw_meta, source=self.device_config.kind)
            # Inside `ingestion`, which the response model types as an open
            # dict, rather than as new top-level keys -- those would need
            # declaring on InterpretedEegData or /api/v1/state drops them.
            ing.update(self.health_fields())
            out["ingestion"] = ing
            if no_signal:
                # Adapters cache their last-known band values and don't reset
                # them on disconnect, so pulling live meta here would keep
                # showing stale non-zero bands even though features/scores are
                # already zeroed for the same no-signal condition.
                out["bands"] = {"delta": 0.0, "theta": 0.0, "alpha": 0.0, "beta": 0.0, "gamma": 0.0}
            else:
                # A band the bridge reported as NaN, infinite or unparseable
                # is None here: the state model refuses non-finite floats,
                # and the processor has already held the tick for it.
                # No zero default: an absent band is null too, not a
                # measurement of 0 Bels published on the very tick the
                # processor refused to score for its absence.
                out["bands"] = {
                    name: _finite_or_none(raw_meta.get(name))
                    for name in ("delta", "theta", "alpha", "beta", "gamma")
                }
        return out

    def metrics(self) -> dict[str, int | bool]:
        return {
            "contract_version": self.CONTRACT_VERSION,
            "device_id": self.device_id,
            "running": self.running,
            "samples_processed": self.samples_processed,
            "errors_seen": self.errors_seen,
        }

    def send_muse_bridge_command(self, cmd: str, **kwargs: Any) -> dict[str, Any]:
        """Forward JSON control lines to muse_native_bridge when using TCP muse ingestion."""
        send = getattr(self.adapter, "send_bridge_command", None)
        if send is None or not callable(send):
            return {"ok": False, "error": "commands require EEG_SOURCE=muse"}
        body: dict[str, Any] = {"cmd": cmd}
        body.update(kwargs)
        try:
            send(body)
        except (OSError, RuntimeError) as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True}

    def muse_ingestion_snapshot(self) -> dict[str, Any]:
        if hasattr(self.adapter, "get_ingestion_meta"):
            ing = enrich_ingestion_dict(self.settings, self.adapter.get_ingestion_meta(), source=self.device_config.kind)
        else:
            ing = enrich_ingestion_dict(self.settings, {}, source=self.device_config.kind)
        # Same fields as snapshot()'s ingestion block, so /api/v1/muse/status
        # and /api/v1/state cannot disagree about whether a device is alive.
        ing.update(self.health_fields())
        return ing


class StreamManager:
    """Registry of DeviceSessions, keyed by device_id. Single-device deployments
    (no EEG_DEVICES set) get exactly one session named "default" and every method
    below defaults to it, so existing callers that never pass a device_id see no
    behavior change."""

    CONTRACT_VERSION = CONTRACT_VERSION
    DEFAULT_DEVICE_ID = DEFAULT_DEVICE_ID

    def __init__(self) -> None:
        self.settings = get_settings()
        # _sessions is populated once below and never mutated afterward, so
        # this lock guards nothing today. It's future-proofing for runtime
        # device (de)registration, which doesn't exist yet.
        self._lock = threading.Lock()
        device_configs = parse_eeg_devices(self.settings)
        self._sessions: dict[str, DeviceSession] = {
            device_id: DeviceSession(device_id, self.settings, cfg) for device_id, cfg in device_configs.items()
        }

    def set_payload_consumer(self, consumer: Callable[[dict[str, Any]], None] | None) -> None:
        """Route every device's ticks to one consumer, or to none.

        Every device, because a student's machine can have both a headband
        and a camera registered, both feeding the same session. Setting it to
        None detaches cleanly -- what stopping push ingestion does -- and the
        sampling loops keep serving the local dashboard either way.
        """
        with self._lock:
            sessions = list(self._sessions.values())
        for session in sessions:
            session.on_payload = consumer

    def session(self, device_id: str = DEFAULT_DEVICE_ID) -> DeviceSession:
        """Return the DeviceSession for device_id, or raise UnknownDeviceError."""
        with self._lock:
            session = self._sessions.get(device_id)
        if session is None:
            raise UnknownDeviceError(device_id)
        return session

    def report_answer(self, device_id: str = DEFAULT_DEVICE_ID, *, correct: bool,
                      difficulty: str | None = None) -> dict[str, Any]:
        """A recorded answer for the student on this device. Only the
        simulator does anything with it (it nudges its hidden state so a
        sim run's signals respond to the lesson); a real headband's adapter
        has no such method and the answer is acknowledged and ignored --
        `applied` says which, so a caller can tell a sim run from hardware."""
        adapter = self.session(device_id).adapter
        report = getattr(adapter, "report_answer", None)
        if report is None or not callable(report):
            return {"ok": True, "applied": False}
        report(bool(correct), difficulty)
        return {"ok": True, "applied": True}

    def arm_baseline(self, device_id: str = DEFAULT_DEVICE_ID) -> None:
        """Recording has been armed for this device: gather the per-session
        baseline from now, not from stream start. See
        SignalProcessor.restart_baseline. The label engine restarts with it,
        or the lesson opens on a label formed during pairing."""
        session = self.session(device_id)
        session.processor.restart_baseline()
        session.adaptation.restart()

    def end_session(self, device_id: str = DEFAULT_DEVICE_ID) -> None:
        """A recording session has ended without the stream stopping --
        push/stop, where the headband stays paired. Forgets the baseline,
        the histories, the counters and the label state, exactly as a
        stream stop does; through push there was no session end at all,
        and the next student on a shared station inherited everything."""
        session = self.session(device_id)
        session.processor.clear_session()
        session.spectrum.reset()
        session.adaptation.end_session()
        # And the heart channel: its continuity anchor is the previous
        # student's, and inherited it confirms the next one's first window
        # at once -- the population the anchor exists to distrust.
        session._reset_heart()
        # The adapter's optical buffer too, without dropping the link: the
        # tracker reset alone left the previous student's 25 s of samples
        # for the next one's first window to straddle.
        clear = getattr(session.adapter, "clear_optics", None)
        if callable(clear):
            clear()

    async def start(self, device_id: str = DEFAULT_DEVICE_ID) -> None:
        await self.session(device_id).start()

    async def stop(self, device_id: str = DEFAULT_DEVICE_ID) -> None:
        await self.session(device_id).stop()

    def snapshot(self, device_id: str = DEFAULT_DEVICE_ID) -> dict[str, Any]:
        return self.session(device_id).snapshot()

    def metrics(self, device_id: str = DEFAULT_DEVICE_ID) -> dict[str, int | bool]:
        return self.session(device_id).metrics()

    def send_muse_bridge_command(self, cmd: str, device_id: str = DEFAULT_DEVICE_ID, **kwargs: Any) -> dict[str, Any]:
        return self.session(device_id).send_muse_bridge_command(cmd, **kwargs)

    def muse_ingestion_snapshot(self, device_id: str = DEFAULT_DEVICE_ID) -> dict[str, Any]:
        return self.session(device_id).muse_ingestion_snapshot()

    def is_running(self, device_id: str = DEFAULT_DEVICE_ID) -> bool:
        return self.session(device_id).running

    def list_devices(self) -> list[dict[str, Any]]:
        with self._lock:
            sessions = list(self._sessions.values())
        out = []
        for session in sessions:
            ingestion = session.muse_ingestion_snapshot()
            out.append(
                {
                    "device_id": session.device_id,
                    "kind": session.device_config.kind,
                    "running": session.running,
                    "connection_state_name": ingestion.get("connection_state_name", "n/a"),
                }
            )
        return out
