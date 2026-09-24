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

# Bump whenever a field is removed from the EEG payload; nothing gates on it.
CONTRACT_VERSION = "1.3.0"


class UnknownDeviceError(KeyError):
    """Raised when a device_id doesn't match any device in the registry."""


def _finite_or_none(value) -> float | None:
    """A float, or None when the value is NaN, infinite or not a number."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


class DeviceSession:
    """One physical stream: adapter, processor, adaptation engine and polling loop, per registry entry."""

    CONTRACT_VERSION = CONTRACT_VERSION
    # Samples per drain; equals TcpMuseBridgeAdapter.EEG_QUEUE_MAXSIZE so a full queue drains in one tick.
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
        # The spectrum estimator runs on either EEG_SPECTRUM_SOURCE, for comparison.
        self.processor = SignalProcessor(
            calm_source=settings.eeg_spectrum_source,  # validated in config.py
            calm_centre_on_arm=settings.eeg_calm_centre_on_arm)  # validated in config.py
        self.spectrum = SpectrumEstimator(poison_seconds=settings.eeg_spectrum_poison_seconds)
        self.adaptation = AdaptationEngine()
        # Per device: continuity compares each window with this device's last.
        self._heart_tracker: HeartRateTracker | None = None
        self._heart_block: dict[str, Any] | None = None
        # Last recompute (emit cadence) vs last accepted rate (anchor staleness).
        self._heart_emitted_at: float | None = None
        self._heart_accepted_at: float | None = None
        self.latest_payload: dict[str, Any] = {}
        self.samples_processed = 0
        self.errors_seen = 0
        # Current device health; None/0 until the first good tick, reset by stop().
        self.last_good_at: float | None = None
        self.last_good_ts: str | None = None
        self.consecutive_errors = 0
        # monotonic() when requested_preset first disagreed with active_preset, else None.
        self._preset_mismatch_since: float | None = None
        self._task: asyncio.Task[None] | None = None
        self.running = False
        # Set when push is on; a plain callable so this module never depends on the network.
        self.on_payload: Callable[[dict[str, Any]], None] | None = None

    async def _emit(self) -> None:
        """Hand the tick's payload to its consumer.

        Swallows everything: the sampling loop must outlive a failing consumer.
        `snapshot()` runs off the event loop, since the ingestion meta read can block.
        """
        if self.on_payload is None:
            return
        try:
            # snapshot(), not latest_payload: bands and ingestion are assembled only there.
            self.on_payload(await asyncio.to_thread(self.snapshot))
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.warning("payload consumer failed for device %s: %s: %s",
                           self.device_id, type(exc).__name__, exc)

    def _optical_heart_block(self) -> dict[str, Any] | None:
        """The `heart` block for this tick, or None if this device has no optics.

        Recomputed every EMIT_EVERY_SECONDS (continuity is calibrated to that step)
        and held between, with its own `ts`, so push and poll each record it once.
        """
        window_fn = getattr(self.adapter, "optics_window", None)
        if window_fn is None:
            # No optics buffer: the camera.
            return None

        now = time.monotonic()
        if self._heart_emitted_at is not None and now - self._heart_emitted_at < EMIT_EVERY_SECONDS:
            return self._heart_block
        if self._heart_tracker is None:
            self._heart_tracker = HeartRateTracker()
        # Since the last accepted rate, not the last recompute: it sizes the continuity allowance.
        # With no anchor yet the tracker ignores it.
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
        """Stop publishing the held reading, without restarting the cadence. For EEG no-data ticks.

        The emit clock and tracker are kept: resetting the clock on flapping contact
        would mint a new `ts` per tick for the same optical window.
        """
        self._heart_block = None

    def _reset_heart(self) -> None:
        """Forget everything about the heart channel. For `stop()` only."""
        self._heart_tracker = None
        self._heart_block = None
        self._heart_emitted_at = None
        self._heart_accepted_at = None

    def _face_payload(self, samples: list[Any], raw_meta: dict[str, Any]) -> dict[str, Any]:
        """The camera equivalent of the EEG payload: same envelope fields.

        `channels`, `features` and `state` are absent, never faked as empty.
        """
        # Local import keeps face code off headband-only sidecar starts.
        from src.app.services.face_processing import (  # noqa: PLC0415
            RATE_WINDOW_SECONDS,
            build_camera_payload,
        )

        rgb, measured, quality, stamps = self.adapter.rgb_window(RATE_WINDOW_SECONDS)
        payload: dict[str, Any] = {
            # Consumers branch on this rather than probing for fields.
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
                # getattr: a missing attribute on a duck-typed adapter would drop the whole payload.
                gaze=getattr(self.adapter, "latest_gaze", lambda: None)(),
                gaze_enabled=getattr(self.adapter, "gaze_enabled", False),
                pose=getattr(self.adapter, "latest_pose", lambda: None)(),
            )
        )
        return payload

    async def start(self) -> None:
        if self.running:
            return
        # Can block on network I/O; kept off the event loop.
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
        # Can block on socket shutdown/thread joins.
        await asyncio.to_thread(self.adapter.disconnect)
        if was_running:
            # A stopped stream is "no data", not its last reading; skipped when never started ("idle").
            # clear_session(), not reset(): the next student must not inherit the baseline.
            self.processor.clear_session()
            self.spectrum.reset()
            self.adaptation.end_session()
            self._reset_heart()
            self.latest_payload = self._no_signal_payload()
            self.last_good_at = None
            self.last_good_ts = None
            self.consecutive_errors = 0
            self._preset_mismatch_since = None

    # Seconds requested and active preset may disagree before it counts as ignored.
    PRESET_SETTLE_SECONDS = 5.0

    def _note_good_tick(self, timestamp: str) -> None:
        self.last_good_at = time.monotonic()
        self.last_good_ts = timestamp
        self.consecutive_errors = 0

    def _note_preset(self, raw_meta: dict[str, Any]) -> None:
        """Track whether the headband is on the preset the bridge asked for.

        An unknown side means "nothing to compare", not a mismatch.
        """
        requested = raw_meta.get("requested_preset") or ""
        active = raw_meta.get("active_preset") or ""
        if not requested or not active or requested == active:
            self._preset_mismatch_since = None
        elif self._preset_mismatch_since is None:
            self._preset_mismatch_since = time.monotonic()

    def health_fields(self) -> dict[str, Any]:
        """What `ingestion` carries about this device's liveness.

        `last_good_age_s` is derived at read time so it grows; None before the first good tick.
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
                # Reads can block, so off the event loop. Queue-backed adapters drain; others read one sample.
                if hasattr(self.adapter, "drain_samples"):
                    samples = await asyncio.to_thread(self.adapter.drain_samples, self.DRAIN_MAX_BATCH)
                else:
                    samples = [await asyncio.to_thread(self.adapter.read_sample)]
                # Can block on lock contention too.
                raw_meta = (
                    await asyncio.to_thread(self.adapter.get_ingestion_meta)
                    if hasattr(self.adapter, "get_ingestion_meta")
                    else {}
                )
            except Exception as exc:
                # No EEG data this cycle: report no signal and reset so post-gap scores don't blend.
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
                # Not `_reset_heart()`: see `_drop_held_heart_block`.
                self._drop_held_heart_block()
                self.latest_payload = self._no_signal_payload()
                await asyncio.sleep(period)
                continue

            if self.device_config.kind == "face":
                # Colour, not EEG channels: never through SignalProcessor.
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
                # Only the newest sample is scored: the processor's window is calibrated in ticks.
                sample = samples[-1]
                self._note_preset(raw_meta)
                # Every drained sample feeds the spectrum, headband only: the sim's one sample a tick
                # would fill a 256 Hz count window with minutes of data.
                if self.device_config.kind == "muse":
                    spectrum = self.spectrum.push(samples, raw_meta)
                else:
                    spectrum = self.spectrum.latest()
                features = self.processor.update(sample, raw_meta, spectrum=spectrum)
                if poisons_buffer(features.get("artifact_reason")):
                    # No estimate until the blink's samples have left the window.
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
                # Beside the cognitive block (separately consented); absent, not null, with no optics.
                heart = self._optical_heart_block()
                if heart is not None:
                    self.latest_payload["heart"] = heart
                self.samples_processed += len(samples)
                self._note_good_tick(self.latest_payload["timestamp"])
                await self._emit()
            except Exception as exc:
                # A pipeline bug, not signal loss: skip the tick, leave signal-loss state alone.
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
                # Present so the features shape matches signal ticks.
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
            # Inside `ingestion` (an open dict); new top-level keys would need InterpretedEegData.
            ing.update(self.health_fields())
            out["ingestion"] = ing
            if no_signal:
                # Adapters cache their last band values, so live meta would be stale.
                out["bands"] = {"delta": 0.0, "theta": 0.0, "alpha": 0.0, "beta": 0.0, "gamma": 0.0}
            else:
                # Non-finite or absent bands are None, never 0 Bels.
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
        # Same fields as snapshot(), so the two status routes cannot disagree.
        ing.update(self.health_fields())
        return ing


class StreamManager:
    """Registry of DeviceSessions, keyed by device_id.

    Without EEG_DEVICES there is one session, "default", which every method defaults to.
    """

    CONTRACT_VERSION = CONTRACT_VERSION
    DEFAULT_DEVICE_ID = DEFAULT_DEVICE_ID

    def __init__(self) -> None:
        self.settings = get_settings()
        # Guards nothing yet: _sessions is never mutated after init.
        self._lock = threading.Lock()
        device_configs = parse_eeg_devices(self.settings)
        self._sessions: dict[str, DeviceSession] = {
            device_id: DeviceSession(device_id, self.settings, cfg) for device_id, cfg in device_configs.items()
        }

    def set_payload_consumer(self, consumer: Callable[[dict[str, Any]], None] | None) -> None:
        """Route every device's ticks to one consumer, or to none (push stopped)."""
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
        """A recorded answer for the student on this device.

        Only the simulator uses it; `applied` is False on hardware.
        """
        adapter = self.session(device_id).adapter
        report = getattr(adapter, "report_answer", None)
        if report is None or not callable(report):
            return {"ok": True, "applied": False}
        report(bool(correct), difficulty)
        return {"ok": True, "applied": True}

    def arm_baseline(self, device_id: str = DEFAULT_DEVICE_ID) -> None:
        """Recording armed: gather the baseline and restart the label engine from now, not stream start."""
        session = self.session(device_id)
        session.processor.restart_baseline()
        session.adaptation.restart()

    def end_session(self, device_id: str = DEFAULT_DEVICE_ID) -> None:
        """A recording session ended with the stream still up (push/stop).

        Forgets everything a stream stop does, so the next student inherits nothing.
        """
        session = self.session(device_id)
        session.processor.clear_session()
        session.spectrum.reset()
        session.adaptation.end_session()
        session._reset_heart()
        # The optical buffer too, keeping the link.
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
