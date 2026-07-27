from __future__ import annotations

import json
import math
import queue
import random
import socket
import threading
from datetime import datetime, timezone
from typing import Any, TextIO

from src.app.config import Settings
from src.app.models import EegSample
from src.app.services.signal_processing import SignalProcessor

# interaxon::bridge::ConnectionState (see libMuse bridge_connection_state.h)
CONNECTION_STATE_NAMES: dict[int, str] = {
    0: "unknown",
    1: "connected",
    2: "connecting",
    3: "disconnected",
    4: "needs_update",
    5: "needs_license",
}


def connection_state_name(code: int | None) -> str:
    if code is None:
        return "n/a"
    if code < 0:
        return "n/a"
    return CONNECTION_STATE_NAMES.get(code, f"other({code})")


def enrich_ingestion_dict(settings: Settings, meta: dict[str, Any], source: str | None = None) -> dict[str, Any]:
    """Normalize ingestion metadata for the API (includes human-readable connection state).

    `source` overrides settings.eeg_source when reporting a specific device's own kind
    (multi-device registry) rather than the global default.
    """
    meta_no_bands = {k: v for k, v in meta.items() if k not in {"delta", "theta", "alpha", "beta", "gamma"}}
    out: dict[str, Any] = {
        "eeg_source": (source or settings.eeg_source).lower().strip(),
        **meta_no_bands,
    }
    cs = out.get("connection_state")
    if isinstance(cs, int):
        out["connection_state_name"] = connection_state_name(cs)
    else:
        out["connection_state_name"] = "n/a"
    return out


def parse_bridge_message(message: dict) -> EegSample:
    """Parse a bridge payload into an EegSample."""
    if "tp9" not in message or "af7" not in message or "af8" not in message or "tp10" not in message:
        raise ValueError("Bridge message missing one or more EEG channels")
    if "timestamp" in message and isinstance(message["timestamp"], str):
        timestamp = datetime.fromisoformat(message["timestamp"])
    elif "mono_ts_ms" in message:
        timestamp = datetime.fromtimestamp(float(message["mono_ts_ms"]) / 1000, tz=timezone.utc)
    else:
        timestamp = datetime.now(tz=timezone.utc)
    return EegSample(
        timestamp=timestamp,
        channel_tp9=float(message["tp9"]),
        channel_af7=float(message["af7"]),
        channel_af8=float(message["af8"]),
        channel_tp10=float(message["tp10"]),
    )


def _apply_bridge_ingestion_fields(target: dict[str, Any], payload: dict[str, Any]) -> None:
    for key in (
        "bridge_mode",
        "muse_connected",
        "muse_discovered",
        "bluetooth_enabled",
        "connection_state",
        "kind",
        "active_muse_name",
        "firmware_version",
        "delta",
        "theta",
        "alpha",
        "beta",
        "gamma",
        "hsi",
        "is_good",
        "band_channels_used",
        "notch_filtered",
    ):
        if key not in payload:
            continue
        if key == "band_channels_used":
            try:
                target[key] = int(payload[key])
            except (TypeError, ValueError):
                continue
        elif key in {"hsi", "is_good"}:
            # Per-electrode contact quality from libMuse: 4 floats, or null
            # when the headband hasn't reported that packet type yet.
            v = payload[key]
            if v is None:
                target[key] = None
                continue
            if not isinstance(v, list):
                continue
            try:
                target[key] = [float(x) for x in v]
            except (TypeError, ValueError):
                # Ignore malformed values from bridge and keep prior metadata.
                continue
        elif key in {"muse_connected", "muse_discovered", "bluetooth_enabled", "notch_filtered"}:
            target[key] = bool(payload[key])
        elif key == "connection_state":
            v = payload[key]
            if v is None or v == "":
                target[key] = None
            else:
                try:
                    target[key] = int(v)
                except (TypeError, ValueError):
                    # Ignore malformed state values from bridge and keep prior metadata.
                    continue
        elif key in {"delta", "theta", "alpha", "beta", "gamma"}:
            try:
                target[key] = float(payload[key])
            except (TypeError, ValueError):
                # Ignore malformed numeric values from bridge and keep prior metadata.
                continue
        else:
            target[key] = str(payload[key])
    if "muse_devices" in payload and isinstance(payload["muse_devices"], list):
        target["muse_devices"] = [str(x) for x in payload["muse_devices"]]


class SimulatedMuseIngestionAdapter:
    """Local adapter: emits simulated Muse-like EEG values.

    Drives a slowly drifting hidden focus/calm state (a bounded random walk)
    rather than independent per-sample noise, so scores tell a plausible
    continuous story instead of jittering around a fixed midpoint. Band
    powers are derived from that same hidden state using SignalProcessor's
    own calibrated log-ratio bounds, so the spectral-ratio path is exercised
    the same way it would be with a real headset.

    Band powers are emitted in BELS, matching libMuse's ABSOLUTE band packets
    and what SignalProcessor expects -- see get_ingestion_meta. This adapter
    is a producer of those fields, so any change to how the processor
    interprets them has to change this too.

    Note: SignalProcessor's focus ratio (beta / (alpha+theta)) and calm ratio
    (alpha / (beta+gamma)) share alpha and beta, so pushing focus toward its
    high end structurally requires beta > alpha, while pushing calm toward
    its high end requires the opposite. That means simultaneously satisfying
    both the "focused" label's focus>=70% AND calm>=50% thresholds is
    difficult by construction (a property of the real formula, not a
    simulator defect) -- "stressed" and high-focus states are independently
    reachable, but "focused" may rarely or never fire from bands alone.
    """

    # Bounded random-walk step per sample; keeps the hidden state continuous
    # tick-to-tick instead of resetting on every read.
    _DRIFT_STEP = 0.03
    # Keep simulator ranges aligned with SignalProcessor calibration.
    _BASE_LEVEL = 690.0
    _LEVEL_SPAN = 130.0
    _CHANNEL_NOISE = 12.0
    # Scales how much the beta/(alpha+theta) ratio swings with focus_state.
    # Kept well below 1.0 so beta doesn't overwhelm alpha's contribution to
    # the calm ratio at high focus (see class docstring).
    _FOCUS_BAND_GAIN = 0.4

    def __init__(self) -> None:
        self.connected = False
        self._focus_state = 0.5
        self._calm_state = 0.5

    def connect(self) -> None:
        self.connected = True
        self._focus_state = random.uniform(0.4, 0.6)
        self._calm_state = random.uniform(0.4, 0.6)

    def disconnect(self) -> None:
        self.connected = False

    @staticmethod
    def _drift(value: float, step: float) -> float:
        # Reflect off the [0, 1] boundaries instead of clamping, so the walk
        # doesn't disproportionately "stick" near an edge over a long session.
        value += random.uniform(-step, step)
        if value < 0.0:
            value = -value
        elif value > 1.0:
            value = 2.0 - value
        return max(0.0, min(1.0, value))

    def read_sample(self) -> EegSample:
        if not self.connected:
            raise RuntimeError("Muse adapter not connected")
        self._focus_state = self._drift(self._focus_state, self._DRIFT_STEP)
        self._calm_state = self._drift(self._calm_state, self._DRIFT_STEP)
        base = self._BASE_LEVEL + (self._focus_state - 0.5) * self._LEVEL_SPAN
        # Lower calm -> wider cross-channel spread (more erratic signal).
        spread_scale = 1.6 - self._calm_state
        return EegSample(
            timestamp=datetime.now(tz=timezone.utc),
            channel_tp9=base + random.uniform(-self._CHANNEL_NOISE, self._CHANNEL_NOISE) * spread_scale,
            channel_af7=base + random.uniform(-self._CHANNEL_NOISE, self._CHANNEL_NOISE) * spread_scale,
            channel_af8=base + random.uniform(-self._CHANNEL_NOISE, self._CHANNEL_NOISE) * spread_scale,
            channel_tp10=base + random.uniform(-self._CHANNEL_NOISE, self._CHANNEL_NOISE) * spread_scale,
        )

    def get_ingestion_meta(self) -> dict[str, Any]:
        # Band powers are emitted in BELS (base-10 logarithms), matching what
        # libMuse's ABSOLUTE band packets actually carry and what
        # SignalProcessor now expects. Live Muse S captures put these roughly
        # in -0.1 .. 0.85 B, so the values below stay in that range.
        #
        # Emitting linear magnitudes here (alpha ~40-60) while the processor
        # exponentiates them produces 10**40-scale powers: the log-ratios come
        # out around +-31 against bounds spanning ~1.6, so both spectral terms
        # clamp and only the 25% amplitude term still moves. It also makes
        # theta and gamma numerically negligible, which collapses focus to
        # ln(beta/alpha) and calm to ln(alpha/beta) -- exact negations, so the
        # two scores carry identical information and AdaptationEngine can no
        # longer separate states.
        #
        # alpha/theta/gamma are picked first; beta is then solved IN LOG SPACE
        # to hit the target focus log-ratio exactly, so focus_score responds
        # cleanly to focus_state while calm tapers as simulated focus rises.
        alpha = 0.10 + self._calm_state * 0.45
        theta = 0.10
        gamma = 0.05
        target_focus_log_ratio = self._FOCUS_BAND_GAIN * (
            SignalProcessor.FOCUS_LOG_RATIO_MIN
            + self._focus_state * (SignalProcessor.FOCUS_LOG_RATIO_MAX - SignalProcessor.FOCUS_LOG_RATIO_MIN)
        )
        # focus = ln(beta_p) - ln(alpha_p + theta_p), so
        #   beta_p = exp(target) * (alpha_p + theta_p)
        # and beta must be published as its base-10 log.
        beta = math.log10(
            math.exp(target_focus_log_ratio) * (10.0**alpha + 10.0**theta)
        )
        return {
            "bridge_mode": "python_sim",
            # These intentionally stay false/empty regardless of self.connected:
            # they represent a *real* Muse BLE pairing (interaxon bridge connection
            # state), which the frontend's headband-pairing wizard checks directly
            # (see muse_connected / muse_devices in Adaptive.jsx) to confirm actual
            # hardware paired -- the simulator has no real device to report.
            "muse_connected": False,
            "muse_discovered": False,
            "bluetooth_enabled": True,
            "connection_state": -1,
            "muse_devices": [],
            "active_muse_name": "",
            "firmware_version": "sim-1.0",
            # Also Bels. delta is not used by either log-ratio, but it is
            # persisted to cognitive_signals alongside the others, so it has to
            # be on the same scale or stored sim rows are internally inconsistent.
            "delta": 0.40,
            "theta": round(theta, 3),
            "alpha": round(alpha, 3),
            "beta": round(beta, 3),
            "gamma": round(gamma, 3),
            # Simulated electrode contact: report a good fit on all four
            # channels so the simulator exercises the same signal-quality path
            # as real hardware instead of looking like a headband that isn't on.
            "hsi": [1.0, 1.0, 1.0, 1.0],
            "is_good": [1.0, 1.0, 1.0, 1.0],
            "band_channels_used": 4,
            "notch_filtered": False,
        }

    def send_bridge_command(self, _payload: dict[str, Any]) -> None:
        raise RuntimeError("Muse bridge commands require EEG_SOURCE=muse (TCP bridge)")


class TcpMuseBridgeAdapter:
    """Reads normalized samples from a native bridge over localhost TCP."""

    # Bound the queue so a stalled consumer can't grow it without limit.
    # Mirrors the native bridge's own drop-oldest policy at the same size
    # (eeg_queue_ cap in muse_bridge_service.cpp).
    EEG_QUEUE_MAXSIZE = 2048

    def __init__(self, host: str, port: int, timeout_seconds: int) -> None:
        self.host = host
        self.port = port
        self.timeout_seconds = timeout_seconds
        self._socket: socket.socket | None = None
        self._stream: TextIO | None = None
        self._ingestion_meta: dict[str, Any] = {
            "bridge_mode": "unknown",
            "muse_connected": False,
            "muse_discovered": False,
            "bluetooth_enabled": True,
            "connection_state": None,
            "muse_devices": [],
            "active_muse_name": "",
            "firmware_version": "",
            "delta": 0.0,
            "theta": 0.0,
            "alpha": 0.0,
            "beta": 0.0,
            "gamma": 0.0,
            # None until the headband reports HSI_PRECISION / IS_GOOD, so
            # "not reported yet" stays distinguishable from a real reading.
            "hsi": None,
            "is_good": None,
            "band_channels_used": 0,
            "notch_filtered": False,
        }
        self._ingestion_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._eeg_queue: queue.Queue[EegSample] = queue.Queue(maxsize=self.EEG_QUEUE_MAXSIZE)
        self._reader_stop = threading.Event()
        self._reader_thread: threading.Thread | None = None

    def _enqueue_sample(self, sample: EegSample) -> None:
        """Push onto the bounded queue, dropping the oldest sample instead of
        blocking a full queue -- same drop-oldest policy as the native bridge,
        just enforced here instead of assuming the consumer always keeps up."""
        while True:
            try:
                self._eeg_queue.put_nowait(sample)
                return
            except queue.Full:
                try:
                    self._eeg_queue.get_nowait()
                except queue.Empty:
                    pass

    def _reader_loop(self) -> None:
        assert self._stream is not None
        while not self._reader_stop.is_set():
            try:
                line = self._stream.readline()
            except OSError:
                break
            if not line:
                break
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                with self._ingestion_lock:
                    _apply_bridge_ingestion_fields(self._ingestion_meta, payload)
            except Exception:
                # Protect the background reader thread from malformed metadata payloads.
                continue
            if not all(k in payload for k in ("tp9", "af7", "af8", "tp10")):
                continue
            try:
                sample = parse_bridge_message(payload)
            except (ValueError, TypeError, KeyError):
                continue
            self._enqueue_sample(sample)

    def _try_connect(self) -> bool:
        """Attempt one TCP connection. Returns True on success, False if bridge not up yet."""
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout_seconds)
        except OSError:
            return False
        self._reader_stop.clear()
        self._socket = sock
        self._stream = sock.makefile("r", encoding="utf-8")
        self._reader_thread = threading.Thread(
            target=self._reader_loop, name="muse-bridge-reader", daemon=True
        )
        self._reader_thread.start()
        print(f"[bridge] Connected to {self.host}:{self.port}", flush=True)
        return True

    def connect(self) -> None:
        """Connect to the native bridge. If bridge is not up yet, returns without raising —
        read_sample() will retry on every poll cycle until it becomes available."""
        if self._socket:
            return
        if not self._try_connect():
            print(
                f"[bridge] Native bridge not available on {self.host}:{self.port} — "
                "will retry each poll cycle. Start muse_native_bridge.exe to begin streaming.",
                flush=True,
            )

    def send_bridge_command(self, payload: dict[str, Any]) -> None:
        """Send one JSON line to muse_native_bridge (refresh / connect / disconnect)."""
        if self._socket is None:
            raise RuntimeError("TCP bridge adapter not connected")
        line = json.dumps(payload, separators=(",", ":")) + "\n"
        data = line.encode("utf-8")
        with self._write_lock:
            self._socket.sendall(data)

    def disconnect(self) -> None:
        self._reader_stop.set()
        if self._socket is not None:
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2.0)
            self._reader_thread = None
        while True:
            try:
                self._eeg_queue.get_nowait()
            except queue.Empty:
                break
        self._reader_stop.clear()

    def drain_samples(self, max_batch: int) -> list[EegSample]:
        """Return every queued sample, up to max_batch. Blocks only when the
        queue is empty (same wait/reconnect behaviour read_sample used to have
        on its own get() call), then drains the rest without blocking."""
        # If not connected, try to connect now (bridge may have started since last attempt).
        if not self._reader_thread:
            if not self._try_connect():
                raise RuntimeError(
                    f"Native bridge not available on {self.host}:{self.port}"
                )
        # Never block forever waiting for EEG data. If the bridge stalls/disconnects,
        # surface a recoverable error so the stream loop can keep running.
        timeout_s = max(0.1, float(self.timeout_seconds))
        try:
            first = self._eeg_queue.get(timeout=timeout_s)
        except queue.Empty as e:
            # Reader thread died (bridge disconnected) — reset so next call retries.
            if self._reader_thread and not self._reader_thread.is_alive():
                self.disconnect()
            raise RuntimeError(f"No EEG sample received from bridge within {timeout_s:.1f}s") from e
        samples = [first]
        while len(samples) < max_batch:
            try:
                samples.append(self._eeg_queue.get_nowait())
            except queue.Empty:
                break
        return samples

    def read_sample(self) -> EegSample:
        return self.drain_samples(1)[0]

    def get_ingestion_meta(self) -> dict[str, Any]:
        with self._ingestion_lock:
            return dict(self._ingestion_meta)


def build_ingestion_adapter(
    settings: Settings,
    *,
    kind: str | None = None,
    host: str | None = None,
    port: int | None = None,
):
    """Build an ingestion adapter. `kind`/`host`/`port` override the global settings
    for a specific device in a multi-device registry; omitted, this falls back to the
    existing single-device EEG_SOURCE / MUSE_BRIDGE_HOST / MUSE_BRIDGE_PORT behavior.
    """
    source = (kind or settings.eeg_source).lower().strip()
    if source == "muse":
        return TcpMuseBridgeAdapter(
            host=host or settings.muse_bridge_host,
            port=port or settings.muse_bridge_port,
            timeout_seconds=settings.muse_bridge_timeout_seconds,
        )
    if source == "sim":
        return SimulatedMuseIngestionAdapter()
    raise ValueError("Unsupported EEG_SOURCE. Use 'sim' for local runs or 'muse' for live bridge mode.")
