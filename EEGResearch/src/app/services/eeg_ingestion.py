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
from typing import Any, TextIO

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
        # Headband model, preset and optical capability. Only fields named in
        # this tuple are kept; anything the bridge sends but this list omits is
        # dropped silently.
        "muse_model",
        "requested_preset",
        "active_preset",
        "eeg_channel_count",
        "optical_supported",
        # Charge remaining, or None before the first BATTERY packet arrives.
        "battery_percent",
        # Optical capture evidence. Counters and a latest sample rather than a
        # stream: what needs establishing first is whether the packets arrive at
        # all and with how many channels.
        "optics_packets",
        "ppg_packets",
        "optics_values",
        "ppg_values",
        "last_optics",
        "last_ppg",
        "is_ppg_good",
        "is_heart_good",
        "optics_age_ms",
        # The bridge's own recovery of a dropped BLE link. `reconnecting`
        # covers a backoff wait and an attempt in flight; `reconnect_exhausted`
        # means it gave up and a person has to click Connect. Kept beside
        # `muse_connected` rather than folded into it, so a consumer can say
        # "coming back" instead of "gone".
        "auto_reconnect",
        "reconnecting",
        "reconnect_attempt",
        "reconnect_max_attempts",
        "reconnect_exhausted",
        # ms since the last EEG packet on the current link, null when not
        # connected or before the first packet -- the bridge's liveness view.
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
            # null is a real value here, not a parse failure -- the bridge sends
            # it before the headband's configuration arrives, so 0 must not be
            # made to mean "unknown" too.
            v = payload[key]
            if v is None:
                target[key] = None
                continue
            try:
                target[key] = int(v)
            except (TypeError, ValueError):
                continue
        elif key == "battery_percent":
            # Same null-is-real rule as eeg_channel_count. Matters more here: 0
            # is a valid charge and would render as empty for a headband that
            # simply hasn't reported yet.
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
            # null before the first optical packet, so "never arrived" is kept
            # distinct from "arrived this instant" (which 0 would mean).
            v = payload[key]
            if v is None:
                target[key] = None
                continue
            try:
                target[key] = int(v)
            except (TypeError, ValueError):
                continue
        elif key in {"is_ppg_good", "is_heart_good"}:
            # Tri-state: null means not reported yet, distinct from a reported
            # bad signal. Only a reported bad signal justifies preferring
            # another source.
            v = payload[key]
            target[key] = None if v is None else bool(v)
        elif key in {"last_optics", "last_ppg", "hsi", "is_good"}:
            # Float arrays, null when the headband hasn't reported that packet
            # type yet. hsi/is_good are per-electrode contact quality (4
            # values); last_optics/last_ppg are the latest optical sample (up
            # to 16 and 3 values respectively).
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
    instead of independent per-sample noise, so scores read as a plausible
    continuous story rather than jitter. Band powers derive from that same
    state via SignalProcessor's calibrated log-ratio bounds, exercising the
    same spectral-ratio path a real headset would.

    Band powers are emitted in BELS, matching libMuse's ABSOLUTE packets and
    what SignalProcessor expects (see get_ingestion_meta) -- keep the two in
    sync if either changes.

    SignalProcessor's focus ratio (beta / (alpha+theta)) and calm ratio
    (alpha / (beta+gamma)) share alpha and beta, so high focus needs
    beta > alpha while high calm needs the opposite. That makes the
    "focused" label's focus>=70% AND calm>=50% hard to satisfy at once by
    construction -- a property of the real formula, not a simulator bug.
    """

    # Keeps the hidden state continuous tick-to-tick instead of resetting.
    _DRIFT_STEP = 0.03
    # Keep simulator ranges aligned with SignalProcessor calibration.
    _BASE_LEVEL = 690.0
    _LEVEL_SPAN = 130.0
    _CHANNEL_NOISE = 12.0
    # Scales how much beta/(alpha+theta) swings with focus_state. Kept below
    # 1.0 so beta doesn't overwhelm alpha's contribution to calm at high focus.
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
        # doesn't stick near an edge over a long session.
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
        # Band powers are emitted in BELS (base-10 logs), matching libMuse's
        # ABSOLUTE packets and what SignalProcessor expects. Live Muse S
        # captures sit roughly in -0.1 .. 0.85 B, so values below match that.
        #
        # Emitting linear magnitudes instead (alpha ~40-60) would blow up the
        # log-ratios once the processor exponentiates them, clamping both
        # spectral terms and making theta/gamma negligible -- which collapses
        # focus and calm into exact negations of each other, so
        # AdaptationEngine could no longer tell states apart.
        #
        # alpha/theta/gamma are picked first; beta is solved IN LOG SPACE to
        # hit the target focus log-ratio exactly, so focus_score tracks
        # focus_state while calm tapers as focus rises.
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
            # Stay false/empty regardless of self.connected: these represent a
            # real Muse BLE pairing, which the frontend's pairing wizard checks
            # to confirm actual hardware -- the simulator has no device to report.
            "muse_connected": False,
            "muse_discovered": False,
            "bluetooth_enabled": True,
            "connection_state": -1,
            "muse_devices": [],
            "active_muse_name": "",
            "firmware_version": "sim-1.0",
            # None, never a plausible-looking number -- the simulator has no
            # battery, and a made-up percentage is a reading a student could
            # act on.
            "battery_percent": None,
            # Also Bels. delta isn't used by either log-ratio but is persisted
            # to cognitive_signals alongside the others, so it needs the same
            # scale or stored sim rows are inconsistent.
            "delta": 0.40,
            "theta": round(theta, 3),
            "alpha": round(alpha, 3),
            "beta": round(beta, 3),
            "gamma": round(gamma, 3),
            # Good contact on all four channels, so the simulator exercises the
            # same signal-quality path as real hardware.
            "hsi": [1.0, 1.0, 1.0, 1.0],
            "is_good": [1.0, 1.0, 1.0, 1.0],
            "band_channels_used": 4,
            "notch_filtered": False,
        }

    def send_bridge_command(self, _payload: dict[str, Any]) -> None:
        raise RuntimeError("Muse bridge commands require EEG_SOURCE=muse (TCP bridge)")


@dataclass(frozen=True)
class OpticsWindow:
    """A slice of optical history, and what is known about its time base.

    A record rather than a tuple: most fields are gates the caller must check,
    so a window that can't be measured is refused by name instead of silently
    producing a number.
    """

    # (samples, channels) on a uniform grid by sample index. Empty when there's
    # nothing usable -- never partially filled.
    channels: np.ndarray
    # Samples per second measured across the window. None when it couldn't be
    # measured -- the caller must not substitute a nominal rate.
    #
    # This is the *headband's* rate, taken from `seq`. It's unchanged by
    # losing 90% of the samples -- see `received_rate_hz` for what arrived.
    fs: float | None
    # Samples per second that actually arrived. `fs` is the link's nominal
    # rate; this is what survived it, and only this one falls when samples go
    # missing.
    #
    # A window can hold a full 25 seconds, have its largest gap inside
    # tolerance, and still be almost entirely `np.interp` output -- this is
    # the only field that shows that.
    received_rate_hz: float | None
    # Fraction of the grid that is measurement rather than interpolation.
    #
    # Diagnostic only, deliberately not a gate: what breaks the pulse estimate
    # is falling below Nyquist, an absolute rate, not a ratio. On the resting
    # fixture, completeness 0.17 still reads a correct 69.5 bpm because the
    # link is fast enough that a sixth of it clears 10 Hz.
    completeness: float | None
    # Seconds of history actually held, from the bridge's own stamps.
    span_seconds: float
    # Longest interval between consecutive samples. Interpolation fills the
    # small ones; this says whether any were too big to fill.
    largest_gap_seconds: float | None
    # How many optical channels the headband is emitting (4, 8 or 16,
    # preset-dependent), so carried rather than assumed.
    channel_count: int
    # Set when the window was discarded for a specific reason rather than
    # never having existed -- otherwise a corrupt sample index reads the same
    # as `no_samples`, and the two want opposite responses.
    unusable_reason: str | None = None


class TcpMuseBridgeAdapter:
    """Reads normalized samples from a native bridge over localhost TCP."""

    # Bounds the queue so a stalled consumer can't grow it without limit.
    # Mirrors the native bridge's own drop-oldest policy and size
    # (eeg_queue_ cap in muse_bridge_service.cpp).
    EEG_QUEUE_MAXSIZE = 2048

    # Optical samples are buffered as a window, not kept as a latest value.
    # Heart rate needs autocorrelation over ~25s of a ~64Hz signal, so reading
    # only the newest sample at the 4Hz tick rate would undersample it 16x.
    #
    # ~64 samples/s, so this holds ~64s -- comfortably more than the 25s
    # window needed, with room for a late tick to still find a full one.
    # Drop-oldest, matching the EEG queue: a sample older than the window is
    # useless to anybody.
    OPTICS_BUFFER_MAXLEN = 4096

    # Backoff between TCP connection attempts while the bridge process is not
    # answering. Retrying on every 4Hz tick against a port nobody is listening
    # on is harmless to the kernel but prints a log line each time and makes
    # "bridge not up yet" indistinguishable in the log from "bridge down for
    # an hour". Doubles from the floor to the cap, and resets on success.
    #
    # The cap is deliberately short. This is the socket to a process on the
    # same machine; a bridge that comes back should be noticed within a few
    # seconds, not a minute, because every tick until then reports no_signal.
    CONNECT_BACKOFF_MIN_S = 0.5
    CONNECT_BACKOFF_MAX_S = 5.0

    def __init__(self, host: str, port: int, timeout_seconds: int) -> None:
        self.host = host
        self.port = port
        self.timeout_seconds = timeout_seconds
        self._socket: socket.socket | None = None
        self._stream: TextIO | None = None
        self._connect_backoff_s = self.CONNECT_BACKOFF_MIN_S
        # monotonic() before which no connection attempt will be made. 0.0
        # means "now", so the very first attempt is never delayed.
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
            # None until the headband reports HSI_PRECISION / IS_GOOD, so
            # "not reported yet" is distinguishable from a real reading.
            "hsi": None,
            "is_good": None,
            "band_channels_used": 0,
            "notch_filtered": False,
        }
        self._ingestion_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._eeg_queue: queue.Queue[EegSample] = queue.Queue(maxsize=self.EEG_QUEUE_MAXSIZE)
        # (seq, mono_ts_ms, channel values). Its own lock, separate from
        # _ingestion_lock, so copying out a window of up to a few thousand
        # rows doesn't block the reader thread's per-line metadata update.
        self._optics: deque[tuple[int, float, tuple[float, ...]]] = deque(
            maxlen=self.OPTICS_BUFFER_MAXLEN
        )
        self._optics_lock = threading.Lock()
        self._reader_stop = threading.Event()
        self._reader_thread: threading.Thread | None = None

    def _enqueue_sample(self, sample: EegSample) -> None:
        """Push onto the bounded queue, dropping the oldest sample instead of
        blocking on a full queue -- same drop-oldest policy as the native
        bridge."""
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
                # Handled before the metadata update below, not after. An
                # optics line carries no status fields, so running it through
                # that update would flap `kind` between "optics", "eeg" and
                # "status" 64 times a second.
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

        Malformed lines are dropped rather than raised on, like malformed EEG
        ones -- this runs on the reader thread, where a raise costs the
        stream.
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
            # The bridge writes JSON null for a non-finite reading (nan isn't
            # valid JSON). Drop the whole sample: a sample missing a channel
            # can't be placed on the grid.
            return
        if not all(math.isfinite(v) for v in values):
            return
        with self._optics_lock:
            if self._optics and seq <= self._optics[-1][0]:
                # seq is a per-bridge-run counter, so going backwards means a
                # new bridge process, not a reordered sample. Keeping the
                # older rows would splice two recordings onto one clock.
                self._optics.clear()
            self._optics.append((seq, ts_ms, values))

    def optics_window(self, seconds: float) -> OpticsWindow:
        """The most recent `seconds` of optical samples, on a uniform grid.

        The headband equivalent of the camera adapter's `rgb_window`, but it
        reconstructs its time base the opposite way, because the two clocks
        fail differently.

        `mono_ts_ms` is not a per-sample clock -- it records when a BLE batch
        was delivered, so ~9% of samples share a stamp with their predecessor
        and the rest arrive in bursts. A window placed on those stamps would
        measure Bluetooth scheduling instead. `seq` is the real sample index,
        incremented once per optical sample, so samples are placed by seq and
        the stamps are only used to get the average rate across the window
        (where the batching averages out). That's also why the rate here is
        span-based rather than a median of intervals like the camera's: the
        median of a batched stream is 0.

        Gaps in seq are interpolated, the same trade `resample_uniform` makes
        for the camera path, bounded the same way: the caller rejects a
        window whose `largest_gap_s` is too long to fill honestly.
        """
        with self._optics_lock:
            if not self._optics:
                return OpticsWindow(np.empty((0, 0)), None, None, None, 0.0, None, 0)
            newest_ts = self._optics[-1][1]
            # No emptiness check needed: `_store_optics` drops any line with
            # no channel values, so every buffered tuple has at least one.
            width = len(self._optics[-1][2])
            cutoff = -float("inf") if seconds == float("inf") else newest_ts - seconds * 1000.0
            rows: list[tuple[int, float, tuple[float, ...]]] = []
            for row in reversed(self._optics):
                # Both conditions stop the walk rather than skip the row. A
                # channel count that changed mid-buffer means a preset change,
                # so the samples on either side are different measurements --
                # keep only the trailing run.
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
        # Elapsed *samples*, from seq, not `len(rows) - 1`. If samples were
        # dropped anywhere in the window the two differ, and counting rows
        # would report a rate lower than the headband is actually running at,
        # scaling every derived bpm down by the loss.
        seq_span = float(seqs[-1] - seqs[0])
        # Only `span_s` is checked for zero. It genuinely can be zero when a
        # whole window arrives in one BLE batch sharing a stamp. `seq_span`
        # can't: `_store_optics` clears on a non-increasing seq, so with two
        # or more rows it's always at least 1.
        fs = (seq_span / span_s) if span_s > 0 else None
        # What actually arrived, over the same span. `fs` is unchanged by loss
        # since it's read off `seq` (what the headband sent), so it alone
        # can't tell a full window from one that's mostly interpolation.
        # Measured on the resting fixture: decimating to one sample in 32
        # leaves `fs` at 64.2 Hz while the reported rate walks from 69 bpm to
        # 55.8 (one in 64: 44.0) -- both at confidence 1.00, since
        # interpolation manufactures the smooth periodicity autocorrelation
        # rewards. `received_rate` is what catches that.
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
            # A seq jump this large is corruption, not loss -- refuse outright
            # rather than allocate a grid sized from a bad number. Named
            # explicitly, since empty channels alone would read as
            # `no_samples`, identical to a headband that never emitted.
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

        False also while a previous failure's backoff has not elapsed -- no
        attempt is made then. A caller that wants to know which it was reads
        `connect_wait_remaining()`.
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
            # Cleared for the same reason as the EEG queue: whatever spans a
            # disconnect is two recordings, and the reconstructed clock can't
            # represent the join.
            self._optics.clear()
        self._reader_stop.clear()

    def drain_samples(self, max_batch: int) -> list[EegSample]:
        """Return every queued sample, up to max_batch. Blocks only when the
        queue is empty, then drains the rest without blocking."""
        # If not connected, try to connect now (bridge may have started since last attempt).
        if not self._reader_thread:
            if not self._try_connect():
                wait = self.connect_wait_remaining()
                raise RuntimeError(
                    f"Native bridge not available on {self.host}:{self.port}"
                    + (f" (next attempt in {wait:.1f}s)" if wait > 0 else "")
                )
        # Never block forever waiting for EEG data. If the bridge stalls/disconnects,
        # surface a recoverable error so the stream loop can keep running.
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
    """Build an ingestion adapter. `kind`/`host`/`port` override the global
    settings for one device in a multi-device registry; if omitted, falls back
    to the single-device EEG_SOURCE / MUSE_BRIDGE_HOST / MUSE_BRIDGE_PORT settings.
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
    if source == "face":
        # Imported here, not at module scope, so the sidecar still boots on a
        # machine without the `face` extra installed. Everything above this
        # line must keep working when cv2 is absent.
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
