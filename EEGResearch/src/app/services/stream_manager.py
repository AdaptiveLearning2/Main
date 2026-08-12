from __future__ import annotations

import asyncio
import logging
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
from src.app.services.signal_processing import SignalProcessor

logger = logging.getLogger(__name__)

# 1.3.0 removed `question_policy`. It is a breaking removal from the EEG
# payload, so the version says so, even though nothing gates on this string --
# a consumer diffing two recordings should not have to guess why a field
# vanished.
CONTRACT_VERSION = "1.3.0"


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
            settings,
            kind=device_config.kind,
            host=device_config.host,
            port=device_config.port,
            camera_index=device_config.camera_index,
        )
        self.processor = SignalProcessor()
        self.adaptation = AdaptationEngine()
        # Heart rate off the headband's optical channels. Held per session
        # because continuity -- the only check that catches an octave error --
        # compares each window against the last one *this device* produced.
        self._heart_tracker: HeartRateTracker | None = None
        self._heart_block: dict[str, Any] | None = None
        # Two clocks, because they answer different questions: when the window
        # was last *recomputed* (the emit cadence) and when a rate was last
        # *accepted* (how stale the continuity anchor is).
        self._heart_emitted_at: float | None = None
        self._heart_accepted_at: float | None = None
        self.latest_payload: dict[str, Any] = {}
        self.samples_processed = 0
        self.errors_seen = 0
        self._task: asyncio.Task[None] | None = None
        self.running = False
        # Set by StreamManager when push ingestion is on. A plain callable
        # rather than a reference to the push client, so this module stays
        # unaware of how -- or whether -- anything is shipped off the machine;
        # the sampling loop must not be able to fail because of the network.
        self.on_payload: Callable[[dict[str, Any]], None] | None = None

    async def _emit(self) -> None:
        """Hand the tick's payload to whatever is consuming it.

        Swallows everything. A consumer that raises would otherwise kill the
        sampling loop, which is the one thing in this process that must keep
        running: losing the local stream to fix up a remote write is strictly
        worse than losing the remote write.

        `snapshot()` off the event loop thread. It calls
        `adapter.get_ingestion_meta()`, which is the very call `_loop` already
        wraps in `to_thread` because it can block on lock contention in the TCP
        adapter -- taking it inline here put it straight back on the loop and
        made every API request wait behind a tick.
        """
        if self.on_payload is None:
            return
        try:
            # `snapshot()`, not `latest_payload`. The two are not the same
            # record: `bands` and `ingestion` are assembled in snapshot() and
            # were never in latest_payload, so emitting the raw payload gave the
            # push path EEG rows with null alpha/beta/theta/delta/gamma while
            # the pull path -- which reads /api/v1/state, i.e. snapshot() --
            # stored them. One deployment silently recording less than the
            # other is exactly the divergence the shared mapping was meant to
            # rule out, reintroduced one layer upstream of it.
            self.on_payload(await asyncio.to_thread(self.snapshot))
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.warning("payload consumer failed for device %s: %s: %s",
                           self.device_id, type(exc).__name__, exc)

    def _optical_heart_block(self) -> dict[str, Any] | None:
        """The `heart` block for this tick, or None if this device has no optics.

        Two properties, both of which the ingestion paths depend on.

        **Recomputed on a cadence, not per tick.** It is a 25s window either
        way, so a 4Hz tick would run an autocorrelation over 1600x4 samples
        sixteen times to produce the same answer -- and `MAX_BPM_CHANGE_PER_S`,
        the continuity rule that rejects octave errors, is calibrated against
        the 10s step the estimator was validated on. A quarter-second step
        would make every window's predecessor 99% itself.

        **Held between recomputes, and carrying its own `ts`.** A block that
        appeared for one tick and vanished would be seen by a 4Hz push consumer
        and mostly missed by a 1Hz poller, so the two deployments would record
        different sessions from the same headband. Holding it means both see
        every reading; the stamp is what lets both write it exactly once.
        """
        window_fn = getattr(self.adapter, "optics_window", None)
        if window_fn is None:
            # Not a headband, or a headband whose adapter cannot buffer optics.
            # The simulator is deliberately included in that: it does not model
            # an optical channel, and a simulated pulse would be a number on a
            # parent's chart with nothing behind it.
            return None

        now = time.monotonic()
        if self._heart_emitted_at is not None and now - self._heart_emitted_at < EMIT_EVERY_SECONDS:
            return self._heart_block
        if self._heart_tracker is None:
            self._heart_tracker = HeartRateTracker()
        # Time since the last **accepted** rate, not since the last recompute.
        # The tracker uses it to size how far a heart is allowed to have moved
        # from its anchor, and the anchor is the last accepted rate -- so
        # measuring from the recompute holds the allowance at one step's worth
        # however long the refusals ran, and judges the first good window in a
        # minute against 30 bpm of movement. It self-corrects within a window or
        # two either way, but in the direction of discarding good readings.
        #
        # On the first window there is no anchor at all; the nominal step is the
        # honest answer for "nothing measured yet", and the tracker ignores the
        # value entirely until it has one.
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

        Called on an EEG no-data tick. The block describes a stretch of signal
        that may have ended, so publishing it on would keep a rate on the
        dashboard -- and, under fresh timestamps, in the database -- for a
        headband that is off.

        **The clock is deliberately left running, and that is the whole point
        of splitting this from `_reset_heart`.** `drain_samples` raises whenever
        no EEG sample arrives within its timeout, so flapping electrode contact
        or a stalled bridge takes this path repeatedly. Clearing
        `_heart_emitted_at` here made the next good tick recompute immediately
        and mint a **new `ts`** for materially the same 25 seconds of optical
        signal; since both writers dedupe on `ts`, `heart_session_source_ts_key`
        could not collapse those, and a flapping session wrote up to four
        near-identical rows a second, every one counted as a sample by the
        aggregates.

        The tracker is left alone too. EEG dropping out says nothing about the
        optical emitters -- the optics buffer is untouched here, and is cleared
        only by a real disconnect -- so discarding the continuity anchor would
        throw away the one test that catches an octave error, on evidence that
        is not about the heart channel at all. A genuinely stale anchor is
        already handled: `HeartRateTracker` re-acquires after repeated
        continuity rejections.
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

        Deliberately carries the same envelope fields -- contract_version,
        device_id, timestamp -- so a consumer does not need to know which kind
        of device produced a record. `channels`, `features`, `state` and
        `question_policy` are absent rather than faked: a camera has no
        electrode channels and no cognitive state, and inventing empty ones
        would let a caller average them into an EEG session's numbers.
        """
        # Imported here rather than at module scope. It is cheap and pulls in no
        # camera dependency -- face_processing -> face_ingestion -> face_roi ->
        # pos_rppg are all plain numpy, and the no-dependency import test proves
        # it. But that chain only stays safe by convention, and a module-scope
        # import would put it on every sidecar start including headband-only
        # ones. Keeping it local means the property is enforced by structure
        # rather than by remembering.
        from src.app.services.face_processing import (  # noqa: PLC0415
            RATE_WINDOW_SECONDS,
            build_camera_payload,
        )

        rgb, measured, quality, stamps = self.adapter.rgb_window(RATE_WINDOW_SECONDS)
        payload: dict[str, Any] = {
            # Names the shape, so the API union and any consumer can branch on
            # it rather than probing for which fields happen to be present.
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
                gaze=getattr(self.adapter, "latest_gaze", lambda: None)(),
                gaze_enabled=getattr(self.adapter, "gaze_enabled", False),
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
            # session. Skip this when stop() is called without an active
            # stream (e.g. a duplicate stop, or one that races ahead of
            # start()) so a session that was never started still reports
            # "idle" via /api/v1/state instead of a fabricated zero reading.
            self.processor.reset()
            self.adaptation.reset_for_signal_loss()
            self._reset_heart()
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
                # Not `_reset_heart()`. This path is taken on every tick that
                # reads no EEG sample, which flapping contact does repeatedly;
                # restarting the cadence there re-stamps the same window every
                # tick and neither dedupe can collapse the rows. See the method.
                self._drop_held_heart_block()
                self.latest_payload = self._no_signal_payload()
                await asyncio.sleep(period)
                continue

            if self.device_config.kind == "face":
                # A camera produces colour, not EEG channels, so it cannot go
                # through SignalProcessor at all. Before this branch existed the
                # loop reached for sample.channel_tp9 on a FaceSample and raised
                # every tick -- swallowed by the handler below into errors_seen
                # and a warning, so the session looked alive and produced
                # nothing.
                try:
                    self.latest_payload = self._face_payload(samples, raw_meta)
                    self.samples_processed += len(samples)
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
                # Alongside the cognitive block, not inside it. Two sensors on
                # one device, separately consented and separately capable of
                # failing, so the key is absent rather than null when the
                # device has no optical channel at all -- "switched off" and
                # "could not measure" are the distinction the camera payload
                # keeps for the same reason.
                heart = self._optical_heart_block()
                if heart is not None:
                    self.latest_payload["heart"] = heart
                self.samples_processed += len(samples)
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
        # _sessions is populated once below and never mutated afterward, so
        # this lock guards nothing today -- it's future-proofing for runtime
        # device (de)registration, which doesn't exist yet.
        self._lock = threading.Lock()
        device_configs = parse_eeg_devices(self.settings)
        self._sessions: dict[str, DeviceSession] = {
            device_id: DeviceSession(device_id, self.settings, cfg) for device_id, cfg in device_configs.items()
        }

    def set_payload_consumer(self, consumer: Callable[[dict[str, Any]], None] | None) -> None:
        """Route every device's ticks to one consumer, or to none.

        Every device, because a student's own machine can have both a headband
        and a camera registered and both feed the same session. Setting it to
        None detaches cleanly, which is what stopping push ingestion does -- the
        sampling loops carry on serving the local dashboard either way.
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
