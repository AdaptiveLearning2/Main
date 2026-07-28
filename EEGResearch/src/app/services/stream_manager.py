from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from src.app.config import DEFAULT_DEVICE_ID, DeviceConfig, Settings, get_settings, parse_eeg_devices
from src.app.services.adaptation import AdaptationEngine
from src.app.services.eeg_ingestion import build_ingestion_adapter, enrich_ingestion_dict
from src.app.services.signal_processing import SignalProcessor

logger = logging.getLogger(__name__)

CONTRACT_VERSION = "1.2.0"


class UnknownDeviceError(KeyError):
    """Raised when a device_id doesn't match any device in the registry."""


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
            settings, kind=device_config.kind, host=device_config.host, port=device_config.port
        )
        self.processor = SignalProcessor()
        self.adaptation = AdaptationEngine()
        self.latest_payload: dict[str, Any] = {}
        self.samples_processed = 0
        self.errors_seen = 0
        self._task: asyncio.Task[None] | None = None
        self.running = False

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
            # session. Skip this when stop() is called without an active
            # stream (e.g. a duplicate stop, or one that races ahead of
            # start()) so a session that was never started still reports
            # "idle" via /api/v1/state instead of a fabricated zero reading.
            self.processor.reset()
            self.adaptation.reset_for_signal_loss()
            self.latest_payload = self._no_signal_payload()

    async def _loop(self) -> None:
        period = 1 / max(1, self.settings.eeg_sample_hz)
        while self.running:
            try:
                # Adapter reads can block (especially TCP bridge mode before first EEG frame),
                # so move them off the event loop thread to keep API endpoints responsive.
                # Adapters backed by a queue (TcpMuseBridgeAdapter) support draining every
                # frame buffered since the last tick; others (SimulatedMuseIngestionAdapter)
                # only ever produce one sample per read, so fall back to that.
                if hasattr(self.adapter, "drain_samples"):
                    samples = await asyncio.to_thread(self.adapter.drain_samples, self.DRAIN_MAX_BATCH)
                else:
                    samples = [await asyncio.to_thread(self.adapter.read_sample)]
                # Metadata access can also block (e.g., lock contention in TCP adapter),
                # so keep it off the event loop thread as well.
                raw_meta = (
                    await asyncio.to_thread(self.adapter.get_ingestion_meta)
                    if hasattr(self.adapter, "get_ingestion_meta")
                    else {}
                )
            except Exception as exc:
                # No EEG data arrived this cycle (headset unplugged, bridge idle, etc.).
                # Zero the reported scores instead of leaving the last successful
                # reading frozen in place, and reset the processor/adaptation state so
                # real scores don't resume by blending pre-gap and post-gap samples.
                self.errors_seen += 1
                logger.debug(
                    "EEG read failed for device %s, reporting no signal: %s: %s",
                    self.device_id, type(exc).__name__, exc,
                )
                self.processor.reset()
                self.adaptation.reset_for_signal_loss()
                self.latest_payload = self._no_signal_payload()
                await asyncio.sleep(period)
                continue

            try:
                # Feed only the freshest drained sample through the processor.
                # SignalProcessor's rolling window and per-session baseline
                # (window_size, BASELINE_SAMPLES) are calibrated in *ticks*, not
                # raw samples -- one processor.update() call per tick is the
                # invariant those constants assume. Calling update() once per
                # drained sample broke that: at the bridge's native rate a
                # single tick can carry dozens of samples sharing the same
                # raw_meta (fetched once per tick, see above), so the baseline
                # would latch after one tick instead of ~15s, and the window
                # would span milliseconds instead of ~5s. Draining the queue
                # every tick already fixes the original bug (unbounded backlog
                # / ever-staler reads); it doesn't require re-processing every
                # buffered sample, just always processing the newest one.
                sample = samples[-1]
                features = self.processor.update(sample, raw_meta)
                features["batch_size"] = len(samples)
                state = self.adaptation.infer_state(features)
                policy = self.adaptation.next_question_policy(state)
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
                    "question_policy": policy,
                }
                self.samples_processed += len(samples)
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
                # 0 samples drained/processed this tick. Present (not omitted)
                # so the features shape is identical on signal and no-signal
                # ticks, and consumers never have to special-case its absence.
                "batch_size": 0,
            },
            "state": {
                "label": "no_signal",
                "reason": "No EEG data received",
                "confidence": 0.0,
                "focus_score": 0.0,
                "calm_score": 0.0,
            },
            "question_policy": {
                "action": "fallback_default",
                "difficulty": self.adaptation.current_difficulty,
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
            out["ingestion"] = ing
            if no_signal:
                # Adapters cache their last-known band values and don't reset
                # them on disconnect, so pulling live meta here would keep
                # showing stale non-zero bands even though features/scores
                # have already been zeroed for the same no-signal condition.
                out["bands"] = {"delta": 0.0, "theta": 0.0, "alpha": 0.0, "beta": 0.0, "gamma": 0.0}
            else:
                out["bands"] = {
                    "delta": float(raw_meta.get("delta", 0.0)),
                    "theta": float(raw_meta.get("theta", 0.0)),
                    "alpha": float(raw_meta.get("alpha", 0.0)),
                    "beta": float(raw_meta.get("beta", 0.0)),
                    "gamma": float(raw_meta.get("gamma", 0.0)),
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
            return enrich_ingestion_dict(self.settings, self.adapter.get_ingestion_meta(), source=self.device_config.kind)
        return enrich_ingestion_dict(self.settings, {}, source=self.device_config.kind)


class StreamManager:
    """Registry of DeviceSessions, keyed by device_id. Single-device deployments
    (no EEG_DEVICES set) get exactly one session named "default" and every method
    below defaults to it, so existing callers that never pass a device_id see no
    behavior change."""

    CONTRACT_VERSION = CONTRACT_VERSION
    DEFAULT_DEVICE_ID = DEFAULT_DEVICE_ID

    def __init__(self) -> None:
        self.settings = get_settings()
        self._lock = threading.Lock()
        device_configs = parse_eeg_devices(self.settings)
        self._sessions: dict[str, DeviceSession] = {
            device_id: DeviceSession(device_id, self.settings, cfg) for device_id, cfg in device_configs.items()
        }

    def session(self, device_id: str = DEFAULT_DEVICE_ID) -> DeviceSession:
        """Return the DeviceSession for device_id, or raise UnknownDeviceError."""
        with self._lock:
            session = self._sessions.get(device_id)
        if session is None:
            raise UnknownDeviceError(device_id)
        return session

    async def start(self, device_id: str = DEFAULT_DEVICE_ID) -> None:
        await self.session(device_id).start()

    async def stop(self, device_id: str = DEFAULT_DEVICE_ID) -> None:
        await self.session(device_id).stop()

    def snapshot(self, device_id: str = DEFAULT_DEVICE_ID) -> dict[str, Any]:
        return self.session(device_id).snapshot()

    def metrics(self, device_id: str = DEFAULT_DEVICE_ID) -> dict[str, int | bool]:
        return self.session(device_id).metrics()

    def send_muse_bridge_command(self, device_id: str, cmd: str, **kwargs: Any) -> dict[str, Any]:
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
