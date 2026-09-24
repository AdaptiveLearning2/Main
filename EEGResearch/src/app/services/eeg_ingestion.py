from __future__ import annotations

import json
import math
import queue
import random
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, TextIO

import numpy as np

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
    """Normalize ingestion metadata for the API, adding a human-readable connection state.

    `source` overrides settings.eeg_source to report one device's own kind in a
    multi-device registry, instead of the global default.
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
        # Only keys named in this tuple are kept; anything else the bridge sends is dropped.
        "muse_model",
        "requested_preset",
        "active_preset",
        "eeg_channel_count",
        "optical_supported",
        # None before the first BATTERY packet.
        "battery_percent",
        "optics_packets",
        "ppg_packets",
        "optics_values",
        "ppg_values",
        "last_optics",
        "last_ppg",
        "is_ppg_good",
        "is_heart_good",
        "optics_age_ms",
        # Bridge BLE auto-reconnect, kept apart from muse_connected so "coming back"
        # differs from "gone"; reconnect_exhausted means a person must click Connect.
        "auto_reconnect",
        "reconnecting",
        "reconnect_attempt",
        "reconnect_max_attempts",
        "reconnect_exhausted",
        # ms since the last EEG packet; null when not connected or before the first.
        "eeg_age_ms",
    ):
        if key not in payload:
            continue
        if key == "band_channels_used":
            try:
                target[key] = int(payload[key])
            except (TypeError, ValueError):
                continue
        elif key == "eeg_channel_count":
            # null (config not yet received) is real; 0 must not also mean unknown.
            v = payload[key]
            if v is None:
                target[key] = None
                continue
            try:
                target[key] = int(v)
            except (TypeError, ValueError):
                continue
        elif key == "battery_percent":
            # null is real: 0 is a valid charge, not "not reported yet".
            v = payload[key]
            if v is None:
                target[key] = None
                continue
            try:
                target[key] = float(v)
            except (TypeError, ValueError):
                continue
        elif key in {"optics_packets", "ppg_packets", "optics_values", "ppg_values",
                     "reconnect_attempt", "reconnect_max_attempts"}:
            try:
                target[key] = int(payload[key])
            except (TypeError, ValueError):
                continue
        elif key in {"optics_age_ms", "eeg_age_ms"}:
            # null = never arrived; 0 = arrived this instant.
            v = payload[key]
            if v is None:
                target[key] = None
                continue
            try:
                target[key] = int(v)
            except (TypeError, ValueError):
                continue
        elif key in {"is_ppg_good", "is_heart_good"}:
            # Tri-state: null = not reported; only a reported bad signal justifies failover.
            v = payload[key]
            target[key] = None if v is None else bool(v)
        elif key in {"last_optics", "last_ppg", "hsi", "is_good"}:
            # Float arrays, null until reported: hsi/is_good 4 electrodes; last_optics <=16, last_ppg 3.
            v = payload[key]
            if v is None:
                target[key] = None
                continue
            if not isinstance(v, list):
                continue
            try:
                target[key] = [float(x) for x in v]
            except (TypeError, ValueError):
                # Malformed values keep prior metadata (here and below).
                continue
        elif key in {"muse_connected", "muse_discovered", "bluetooth_enabled", "notch_filtered",
                     "optical_supported", "auto_reconnect", "reconnecting",
                     "reconnect_exhausted"}:
            target[key] = bool(payload[key])
        elif key == "connection_state":
            v = payload[key]
            if v is None or v == "":
                target[key] = None
            else:
                try:
                    target[key] = int(v)
                except (TypeError, ValueError):
                    continue
        elif key in {"delta", "theta", "alpha", "beta", "gamma"}:
            try:
                target[key] = float(payload[key])
            except (TypeError, ValueError):
                continue
        else:
            target[key] = str(payload[key])
    if "muse_devices" in payload and isinstance(payload["muse_devices"], list):
        target["muse_devices"] = [str(x) for x in payload["muse_devices"]]


class SimulatedMuseIngestionAdapter:
    """Local adapter: emits simulated Muse-like EEG values.

    Bands (Bels, like libMuse ABSOLUTE) are solved from the processor's own bounds,
    so a green test here is the formula agreeing with itself -- score formula changes
    on a real capture. `clock` and `seed` make a run reproducible; see docs/signals.md.
    """

    _DRIFT_STEP = 0.03
    # Keep simulator ranges aligned with SignalProcessor calibration.
    _BASE_LEVEL = 690.0
    _LEVEL_SPAN = 130.0
    _CHANNEL_NOISE = 12.0
    # Below 1.0 so beta doesn't overwhelm alpha's contribution to calm at high focus.
    _FOCUS_BAND_GAIN = 0.4

    # Named so a status line or bug report shows no real headband was involved.
    SIM_DEVICE_NAME = "MuseS-SIM0"

    def __init__(self, clock: Callable[[], float] = time.monotonic, seed: int | None = None,
                 sim_optics: bool = False) -> None:
        self.connected = False
        self._focus_state = 0.5
        self._calm_state = 0.5
        self._clock = clock
        # The bridge's pairing state machine, separate from `connected` (the sample
        # stream); pairing survives a stream stop as the bridge's link does.
        self._pair_lock = threading.Lock()
        self._discovered: list[str] = []
        self._paired_name: str | None = None
        # For `eeg_age_ms`: the sample stream stands in for BLE packets, so a stopped
        # stream lets the age climb, which is the drop.
        self._paired_at: float | None = None
        self._last_packet_at: float | None = None
        # Drawn on first connect, drained on the clock, kept across connects; null while
        # unpaired. 0 is a real reading (0.0), never None.
        self._battery_level: float | None = None
        self._battery_drawn_at: float | None = None
        # All draws come from these two, so `seed` replays a run; optics has its own
        # generator so its cadence cannot shift the contact sequence.
        self._rng = random.Random(seed)
        self._optics_rng = np.random.default_rng(seed)
        # Opt-in, like MUSE_ENABLE_OPTICS on hardware.
        self.optics_enabled = bool(sim_optics)
        self._contact_state: list[float] = [1.0] * self.CONTACT_ELECTRODES
        self._contact_until: list[float] = [float("-inf")] * self.CONTACT_ELECTRODES
        # Expired "loose", so the first report draws a seated phase rather than a fault.
        self._strap_phase = "loose"
        self._strap_until = float("-inf")
        # Decaying task bias, applied before bands are solved (see report_answer).
        self._task_focus = 0.0
        self._task_calm = 0.0
        self._task_bpm = 0.0
        self._task_at: float | None = None
        self._heart_rest_bpm = self._rng.uniform(*self.HEART_REST_BPM_RANGE)
        self._stream_started_at: float | None = None
        self._optics_cleared_at: float = float("-inf")

    # No packets for this long after CONNECTED (preset switch); age is null, not 0.
    PAIR_SETTLE_SECONDS = 5.0
    # Null until the first BATTERY packet, as on hardware.
    BATTERY_FIRST_REPORT_SECONDS = 50.0
    # A Muse S lasts roughly ten hours.
    BATTERY_DRAIN_PCT_PER_HOUR = 10.0
    BATTERY_START_RANGE = (55.0, 100.0)

    def connect(self) -> None:
        self.connected = True
        self._focus_state = self._rng.uniform(0.4, 0.6)
        self._calm_state = self._rng.uniform(0.4, 0.6)
        with self._pair_lock:
            # Stamp now, or a re-paired link reads as silent until the first tick.
            self._last_packet_at = self._clock()
            self._stream_started_at = self._clock()

    def disconnect(self) -> None:
        self.connected = False
        with self._pair_lock:
            # Optical history does not survive a stream stop.
            self._stream_started_at = None

    def _pairing_fields(self) -> dict[str, Any]:
        with self._pair_lock:
            paired = self._paired_name
            discovered = list(self._discovered)
            paired_at = self._paired_at
            last_packet = self._last_packet_at
            level = self._battery_level
            drawn_at = self._battery_drawn_at
        age: int | None = None
        battery: float | None = None
        if paired_at is not None:
            now = self._clock()
            if now - paired_at >= self.PAIR_SETTLE_SECONDS:
                settled_at = paired_at + self.PAIR_SETTLE_SECONDS
                origin = max(settled_at, last_packet if last_packet is not None else settled_at)
                age = max(0, int((now - origin) * 1000.0))
            if (now - paired_at >= self.BATTERY_FIRST_REPORT_SECONDS
                    and level is not None and drawn_at is not None):
                drained = self.BATTERY_DRAIN_PCT_PER_HOUR * (now - drawn_at) / 3600.0
                battery = round(max(0.0, level - drained), 1)
        return {
            "muse_connected": paired is not None,
            "muse_discovered": bool(discovered),
            "connection_state": 1 if paired is not None else 3,
            "muse_devices": discovered,
            "active_muse_name": paired or "",
            "eeg_age_ms": age,
            "battery_percent": battery,
        }

    def _drift(self, value: float, step: float) -> float:
        # Reflect rather than clamp, so the walk doesn't stick at an edge.
        value += self._rng.uniform(-step, step)
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
        with self._pair_lock:
            self._last_packet_at = self._clock()
        focus, calm = self._effective_states()
        base = self._BASE_LEVEL + (focus - 0.5) * self._LEVEL_SPAN
        # Lower calm -> wider spread; 0.6..1.6 stays under the processor's 3.5x artifact jump.
        spread_scale = 1.6 - calm
        return EegSample(
            timestamp=datetime.now(tz=timezone.utc),
            channel_tp9=base + self._rng.uniform(-self._CHANNEL_NOISE, self._CHANNEL_NOISE) * spread_scale,
            channel_af7=base + self._rng.uniform(-self._CHANNEL_NOISE, self._CHANNEL_NOISE) * spread_scale,
            channel_af8=base + self._rng.uniform(-self._CHANNEL_NOISE, self._CHANNEL_NOISE) * spread_scale,
            channel_tp10=base + self._rng.uniform(-self._CHANNEL_NOISE, self._CHANNEL_NOISE) * spread_scale,
        )

    def get_ingestion_meta(self) -> dict[str, Any]:
        # Bands in Bels (log10), like libMuse ABSOLUTE; live Muse S sits ~-0.1..0.85 B.
        # beta is solved in log space to hit the target focus log-ratio exactly.
        focus, calm = self._effective_states()
        alpha = 0.10 + calm * 0.45
        theta = 0.10
        gamma = 0.05
        target_focus_log_ratio = self._FOCUS_BAND_GAIN * (
            SignalProcessor.FOCUS_LOG_RATIO_MIN
            + focus * (SignalProcessor.FOCUS_LOG_RATIO_MAX - SignalProcessor.FOCUS_LOG_RATIO_MIN)
        )
        # beta_p = exp(target) * (alpha_p + theta_p), published as log10.
        beta = math.log10(
            math.exp(target_focus_log_ratio) * (10.0**alpha + 10.0**theta)
        )
        return {
            "bridge_mode": "python_sim",
            # Follows the page's pairing commands, not the sample stream; carries battery_percent.
            **self._pairing_fields(),
            "bluetooth_enabled": True,
            "firmware_version": "sim-1.0",
            "optical_supported": self.optics_enabled,
            # Bels too: unused by the ratios but persisted to cognitive_signals.
            "delta": 0.40,
            "theta": round(theta, 3),
            "alpha": round(alpha, 3),
            "beta": round(beta, 3),
            "gamma": round(gamma, 3),
            **self._contact_fields(),
            "notch_filtered": False,
        }

    # --- electrode contact -------------------------------------------------
    # Strap episodes (seated/loose) move electrodes together; each holds an HSI state
    # (1 good, 2 mediocre, 4 poor) for a drawn streak, on the clock, not per read.
    CONTACT_ELECTRODES = 4
    # (hsi state, draw weight) per strap phase.
    CONTACT_WEIGHTS = {
        "seated": ((1.0, 0.50), (2.0, 0.35), (4.0, 0.15)),
        "loose": ((1.0, 0.15), (2.0, 0.30), (4.0, 0.55)),
    }
    CONTACT_STREAK_SECONDS = {1.0: (20.0, 90.0), 2.0: (15.0, 60.0), 4.0: (10.0, 45.0)}
    STRAP_PHASE_SECONDS = {"seated": (90.0, 300.0), "loose": (20.0, 60.0)}

    def _contact_fields(self) -> dict[str, Any]:
        now = self._clock()
        with self._pair_lock:
            if now >= self._strap_until:
                self._strap_phase = "loose" if self._strap_phase == "seated" else "seated"
                lo, hi = self.STRAP_PHASE_SECONDS[self._strap_phase]
                self._strap_until = now + self._rng.uniform(lo, hi)
                # A strap moving re-seats every electrode at once.
                self._contact_until = [float("-inf")] * self.CONTACT_ELECTRODES
            weights = self.CONTACT_WEIGHTS[self._strap_phase]
            for i in range(self.CONTACT_ELECTRODES):
                if now >= self._contact_until[i]:
                    state = self._rng.choices(
                        [s for s, _ in weights], weights=[w for _, w in weights]
                    )[0]
                    lo, hi = self.CONTACT_STREAK_SECONDS[state]
                    self._contact_state[i] = state
                    self._contact_until[i] = now + self._rng.uniform(lo, hi)
            hsi = list(self._contact_state)
        is_good = [1.0 if v <= 2.0 else 0.0 for v in hsi]
        return {
            "hsi": hsi,
            "is_good": is_good,
            # The bridge averages bands over the channels it trusts.
            "band_channels_used": max(1, int(sum(is_good))),
        }

    # --- task-responsive cognitive state -------------------------------------
    # Each answer nudges the hidden states (a miss pulls calm towards stressed) into a
    # bounded offset that decays on the clock, applied before the bands are solved.
    TASK_BIAS_BOUND = 0.25
    TASK_BIAS_DECAY_SECONDS = 90.0
    TASK_NUDGE = {
        # (focus, calm) per answer outcome; a hard miss is scaled up.
        "correct": (0.05, 0.02),
        "wrong": (-0.03, -0.08),
    }
    TASK_HARD_MISS_SCALE = 1.5

    def report_answer(self, correct: bool, difficulty: str | None = None) -> None:
        """A recorded answer: nudge the hidden states."""
        now = self._clock()
        with self._pair_lock:
            self._decay_task_bias_locked(now)
            d_focus, d_calm = self.TASK_NUDGE["correct" if correct else "wrong"]
            if not correct and (difficulty or "").lower() == "hard":
                d_focus *= self.TASK_HARD_MISS_SCALE
                d_calm *= self.TASK_HARD_MISS_SCALE
            b = self.TASK_BIAS_BOUND
            self._task_focus = max(-b, min(b, self._task_focus + d_focus))
            self._task_calm = max(-b, min(b, self._task_calm + d_calm))
            d_bpm = self.HEART_TASK_NUDGE["correct" if correct else "wrong"]
            if not correct and (difficulty or "").lower() == "hard":
                d_bpm *= self.TASK_HARD_MISS_SCALE
            lo, hi = self.HEART_TASK_BOUND
            self._task_bpm = max(lo, min(hi, self._task_bpm + d_bpm))
            self._task_at = now

    def _decay_task_bias_locked(self, now: float) -> None:
        if self._task_at is None:
            return
        dt = max(0.0, now - self._task_at)
        if dt > 0.0:
            factor = math.exp(-dt / self.TASK_BIAS_DECAY_SECONDS)
            self._task_focus *= factor
            self._task_calm *= factor
            self._task_bpm *= factor
            self._task_at = now

    def _effective_states(self) -> tuple[float, float]:
        """The hidden states with the task bias applied, clamped to 0..1."""
        with self._pair_lock:
            self._decay_task_bias_locked(self._clock())
            focus = self._focus_state + self._task_focus
            calm = self._calm_state + self._task_calm
        return max(0.0, min(1.0, focus)), max(0.0, min(1.0, calm))

    # --- optical channel: a simulated pulse -----------------------------------
    # Fed through the unmodified heart path. Opt-in via EEG_SIM_OPTICS; marked
    # `synthetic` so a stored rate can always be told from a measured one.
    OPTICS_FS = 64.0
    OPTICS_CHANNELS = 4
    HEART_REST_BPM_RANGE = (62.0, 84.0)
    HEART_DRIFT_BPM = 2.5
    HEART_DRIFT_PERIOD_SECONDS = 240.0
    HEART_HARMONIC = 0.3
    HEART_NOISE = 0.08
    # Per-answer bpm nudge, bounded, decaying with the task bias.
    HEART_TASK_NUDGE = {"correct": -0.5, "wrong": 2.0}
    HEART_TASK_BOUND = (-5.0, 15.0)

    def _optics_start(self) -> float | None:
        """When the current optical history began, or None without one."""
        with self._pair_lock:
            if self._paired_at is None or self._stream_started_at is None:
                return None
            return max(self._paired_at, self._stream_started_at, self._optics_cleared_at)

    def _heart_bpm(self, now: float) -> float:
        with self._pair_lock:
            self._decay_task_bias_locked(now)
            task = self._task_bpm
        drift = self.HEART_DRIFT_BPM * math.sin(2.0 * math.pi * now / self.HEART_DRIFT_PERIOD_SECONDS)
        return self._heart_rest_bpm + drift + task

    def optics_window(self, seconds: float) -> OpticsWindow:
        """The most recent `seconds` of simulated optical samples, on the
        same record TcpMuseBridgeAdapter.optics_window returns."""
        width = self.OPTICS_CHANNELS
        start = self._optics_start() if self.optics_enabled else None
        now = self._clock()
        if start is None:
            return OpticsWindow(np.empty((0, width)), None, None, None, 0.0, None, width)
        span = min(float(seconds), max(0.0, now - start))
        n = int(span * self.OPTICS_FS)
        if n < 2:
            return OpticsWindow(np.empty((0, width)), None, None, None, 0.0, None, width)
        fs = self.OPTICS_FS
        t = np.arange(n) / fs + (now - span)
        f_hz = self._heart_bpm(now) / 60.0
        phase = 2.0 * math.pi * f_hz * t
        pulse = np.sin(phase) + self.HEART_HARMONIC * np.sin(2.0 * phase)
        noise = self.HEART_NOISE * self._optics_rng.standard_normal((n, width))
        channels = 1000.0 + 50.0 * (pulse[:, None] + noise)
        span_s = float((n - 1) / fs)
        return OpticsWindow(channels, fs, fs, 1.0, span_s, 1.0 / fs, width, synthetic=True)

    def clear_optics(self) -> None:
        """Drop the optical history without touching the link, so the next
        student's first window cannot straddle the previous one's samples."""
        with self._pair_lock:
            self._optics_cleared_at = self._clock()

    def send_bridge_command(self, payload: dict[str, Any]) -> None:
        """Run the bridge's three commands against the simulated device.

        Same vocabulary and refusals as muse_native_bridge; refusals raise
        RuntimeError so the route answers `{"ok": False, "error"}` rather than a silent no-op.
        """
        cmd = payload.get("cmd")
        with self._pair_lock:
            if cmd == "refresh":
                self._discovered = [self.SIM_DEVICE_NAME]
            elif cmd == "connect":
                name = str(payload.get("name") or "").strip()
                if not name:
                    raise RuntimeError('connect command missing "name"')
                if name not in self._discovered:
                    raise RuntimeError(f"connect failed (device not in list): {name}")
                self._paired_name = name
                # Zeroes the packet clock; older packet stamps fall below the settle floor.
                self._paired_at = self._clock()
                if self._battery_level is None:
                    # A repeat connect keeps the charge already draining.
                    self._battery_level = self._rng.uniform(*self.BATTERY_START_RANGE)
                    self._battery_drawn_at = self._paired_at
            elif cmd == "disconnect":
                self._discovered = []
                self._paired_name = None
                self._paired_at = None
                self._last_packet_at = None
                # Charge is kept: the report goes null, a re-pair resumes the level.
            else:
                raise RuntimeError(f"unknown bridge cmd: {cmd!r}")


@dataclass(frozen=True)
class OpticsWindow:
    """A slice of optical history, and what is known about its time base.

    Most fields are gates the caller must check before producing a number.
    """

    # (samples, channels) on a uniform grid; empty when nothing usable, never partial.
    channels: np.ndarray
    # The headband's rate (Hz) from `seq`; None if unmeasurable -- never substitute a nominal rate.
    fs: float | None
    # Hz that actually arrived; the only field that falls when samples go missing.
    received_rate_hz: float | None
    # Fraction measured vs interpolated. Diagnostic, not a gate: Nyquist is what matters.
    completeness: float | None
    # Seconds held, from the bridge's own stamps.
    span_seconds: float
    largest_gap_seconds: float | None
    # 4, 8 or 16 depending on preset.
    channel_count: int
    # Set when discarded for a reason, so it differs from `no_samples`.
    unusable_reason: str | None = None
    # Simulator samples; carried into the row's `raw`.
    synthetic: bool = False


class TcpMuseBridgeAdapter:
    """Reads normalized samples from a native bridge over localhost TCP."""

    # Drop-oldest, same size as the native bridge's eeg_queue_ cap.
    EEG_QUEUE_MAXSIZE = 2048

    # ~64 s at ~64 Hz, well over the 25 s heart window; drop-oldest.
    OPTICS_BUFFER_MAXLEN = 4096

    # Connect backoff while the bridge is down: doubles to the cap, resets on success.
    # Short cap so a returning local bridge is noticed within seconds.
    CONNECT_BACKOFF_MIN_S = 0.5
    CONNECT_BACKOFF_MAX_S = 5.0

    def __init__(self, host: str, port: int, timeout_seconds: int) -> None:
        self.host = host
        self.port = port
        self.timeout_seconds = timeout_seconds
        self._socket: socket.socket | None = None
        self._stream: TextIO | None = None
        self._connect_backoff_s = self.CONNECT_BACKOFF_MIN_S
        # monotonic() before which no attempt is made; 0.0 = the first is never delayed.
        self._next_connect_at = 0.0
        self.connect_failures = 0
        self._ingestion_meta: dict[str, Any] = {
            "bridge_mode": "unknown",
            "muse_connected": False,
            "muse_discovered": False,
            "bluetooth_enabled": True,
            "connection_state": None,
            "muse_devices": [],
            "active_muse_name": "",
            "firmware_version": "",
            # None until a BATTERY packet arrives; 0 is a real charge.
            "battery_percent": None,
            "delta": 0.0,
            "theta": 0.0,
            "alpha": 0.0,
            "beta": 0.0,
            "gamma": 0.0,
            # None until HSI_PRECISION / IS_GOOD arrive.
            "hsi": None,
            "is_good": None,
            "band_channels_used": 0,
            "notch_filtered": False,
        }
        self._ingestion_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._eeg_queue: queue.Queue[EegSample] = queue.Queue(maxsize=self.EEG_QUEUE_MAXSIZE)
        # (seq, mono_ts_ms, values). Own lock, so copying a window doesn't block the reader.
        self._optics: deque[tuple[int, float, tuple[float, ...]]] = deque(
            maxlen=self.OPTICS_BUFFER_MAXLEN
        )
        self._optics_lock = threading.Lock()
        self._reader_stop = threading.Event()
        self._reader_thread: threading.Thread | None = None

    def _enqueue_sample(self, sample: EegSample) -> None:
        """Push onto the bounded queue, dropping the oldest rather than blocking."""
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
            if payload.get("kind") == "optics":
                # Before the metadata update, or `kind` would flap 64 times a second.
                self._store_optics(payload)
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

    def _store_optics(self, payload: dict) -> None:
        """One `kind: optics` line onto the window buffer.

        Malformed lines are dropped, not raised: a raise on the reader thread costs the stream.
        """
        try:
            seq = int(payload["seq"])
            ts_ms = float(payload["mono_ts_ms"])
            raw = payload["ch"]
        except (KeyError, TypeError, ValueError):
            return
        if not isinstance(raw, list) or not raw:
            return
        try:
            values = tuple(float(v) for v in raw)
        except (TypeError, ValueError):
            # JSON null = non-finite reading; a sample missing a channel can't be gridded.
            return
        if not all(math.isfinite(v) for v in values):
            return
        with self._optics_lock:
            if self._optics and seq <= self._optics[-1][0]:
                # seq going backwards means a new bridge process: don't splice two recordings.
                self._optics.clear()
            self._optics.append((seq, ts_ms, values))

    def optics_window(self, seconds: float) -> OpticsWindow:
        """The most recent `seconds` of optical samples, on a uniform grid.

        Placed by `seq` (the true sample index); `mono_ts_ms` is BLE batch delivery
        time, used only for the span-averaged rate. Gaps in seq are interpolated;
        the caller rejects a window whose largest gap is too long.
        """
        with self._optics_lock:
            if not self._optics:
                return OpticsWindow(np.empty((0, 0)), None, None, None, 0.0, None, 0)
            newest_ts = self._optics[-1][1]
            width = len(self._optics[-1][2])
            cutoff = -float("inf") if seconds == float("inf") else newest_ts - seconds * 1000.0
            rows: list[tuple[int, float, tuple[float, ...]]] = []
            for row in reversed(self._optics):
                # Stop, not skip: a channel-count change is a preset change; keep the trailing run.
                if row[1] < cutoff or len(row[2]) != width:
                    break
                rows.append(row)
            rows.reverse()

        if len(rows) < 2:
            return OpticsWindow(np.empty((0, width)), None, None, None, 0.0, None, width)

        seqs = np.array([r[0] for r in rows], dtype=float)
        ts_s = np.array([r[1] for r in rows], dtype=float) / 1000.0
        values = np.array([r[2] for r in rows], dtype=float)

        span_s = float(ts_s[-1] - ts_s[0])
        # Elapsed samples from seq, not row count, or loss would scale bpm down.
        seq_span = float(seqs[-1] - seqs[0])
        # span_s can be 0 (one batch, one stamp); seq_span >= 1 with two rows.
        fs = (seq_span / span_s) if span_s > 0 else None
        # What arrived: fs ignores loss, so only this tells a full window from interpolation.
        received_rate = ((len(rows) - 1) / span_s) if span_s > 0 else None
        if fs is None or received_rate is None:
            return OpticsWindow(np.empty((0, width)), None, None, None, span_s, None, width)
        completeness = min(1.0, received_rate / fs)

        largest_gap_s = float(np.max(np.diff(seqs))) / fs

        grid_len = int(seq_span) + 1
        unusable: str | None = None
        if grid_len == len(rows):
            channels = values
        elif grid_len > self.OPTICS_BUFFER_MAXLEN * 4:
            # A jump this large is corruption; named so it doesn't read as `no_samples`.
            channels = np.empty((0, width))
            unusable = "corrupt_sample_index"
        else:
            grid = np.arange(grid_len, dtype=float) + seqs[0]
            channels = np.column_stack(
                [np.interp(grid, seqs, values[:, c]) for c in range(width)]
            )
        return OpticsWindow(channels, fs, received_rate, completeness,
                            span_s, largest_gap_s, width, unusable)

    def connect_wait_remaining(self) -> float:
        """Seconds until the next TCP attempt is allowed; 0.0 if allowed now."""
        return max(0.0, self._next_connect_at - time.monotonic())

    def _try_connect(self) -> bool:
        """Attempt one TCP connection. Returns True on success, False if bridge not up yet.

        Also False, with no attempt, during backoff; see `connect_wait_remaining()`.
        """
        if self.connect_wait_remaining() > 0.0:
            return False
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout_seconds)
        except OSError:
            self.connect_failures += 1
            self._next_connect_at = time.monotonic() + self._connect_backoff_s
            self._connect_backoff_s = min(self.CONNECT_BACKOFF_MAX_S, self._connect_backoff_s * 2)
            return False
        self._connect_backoff_s = self.CONNECT_BACKOFF_MIN_S
        self._next_connect_at = 0.0
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
        """Connect to the native bridge. If it isn't up yet, returns without
        raising -- read_sample() retries on every poll cycle until it is."""
        if self._socket:
            return
        if not self._try_connect():
            print(
                f"[bridge] Native bridge not available on {self.host}:{self.port} -- "
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
        with self._optics_lock:
            # Whatever spans a disconnect is two recordings.
            self._optics.clear()
        self._reader_stop.clear()

    def clear_optics(self) -> None:
        """Drop the buffered optical samples without touching the link.

        For a push session end that keeps the headband paired (avoids a 12 s re-pair).
        """
        with self._optics_lock:
            self._optics.clear()

    def drain_samples(self, max_batch: int) -> list[EegSample]:
        """Return every queued sample, up to max_batch. Blocks only when the
        queue is empty, then drains the rest without blocking."""
        if not self._reader_thread:
            if not self._try_connect():
                wait = self.connect_wait_remaining()
                raise RuntimeError(
                    f"Native bridge not available on {self.host}:{self.port}"
                    + (f" (next attempt in {wait:.1f}s)" if wait > 0 else "")
                )
        # Never block forever: a stalled bridge raises a recoverable error.
        timeout_s = max(0.1, float(self.timeout_seconds))
        try:
            first = self._eeg_queue.get(timeout=timeout_s)
        except queue.Empty as e:
            # Reader thread died (bridge disconnected) -- reset so the next call retries.
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
    camera_index: int | None = None,
):
    """Build an ingestion adapter; `kind`/`host`/`port` override the global settings per device."""
    source = (kind or settings.eeg_source).lower().strip()
    if source == "muse":
        return TcpMuseBridgeAdapter(
            host=host or settings.muse_bridge_host,
            port=port or settings.muse_bridge_port,
            timeout_seconds=settings.muse_bridge_timeout_seconds,
        )
    if source == "sim":
        return SimulatedMuseIngestionAdapter(sim_optics=settings.eeg_sim_optics)
    if source == "face":
        # Lazy import: the sidecar must boot without the `face` extra (cv2).
        from src.app.services.face_ingestion import build_face_adapter

        return build_face_adapter(
            camera_index=(camera_index if camera_index is not None
                          else settings.face_camera_index),
            fps=settings.face_fps,
            heart_enabled=settings.face_heart_enabled,
            emotion_enabled=settings.face_emotion_enabled,
            emotion_model_path=settings.face_emotion_model_path,
            gaze_enabled=settings.face_gaze_enabled,
            landmark_model_path=settings.face_landmark_model_path,
        )
    raise ValueError(
        "Unsupported EEG_SOURCE. Use 'sim' for local runs, 'muse' for live bridge "
        "mode, or 'face' for camera capture."
    )
