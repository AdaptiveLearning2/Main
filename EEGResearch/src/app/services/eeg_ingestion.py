from __future__ import annotations

import json
import queue
import random
import socket
import threading
from datetime import datetime, timezone
from typing import Any, TextIO

from src.app.config import Settings
from src.app.models import EegSample

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


def enrich_ingestion_dict(settings: Settings, meta: dict[str, Any]) -> dict[str, Any]:
    """Normalize ingestion metadata for the API (includes human-readable connection state)."""
    meta_no_bands = {k: v for k, v in meta.items() if k not in {"delta", "theta", "alpha", "beta", "gamma"}}
    out: dict[str, Any] = {
        "eeg_source": settings.eeg_source.lower().strip(),
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
        "connection_state",
        "kind",
        "active_muse_name",
        "firmware_version",
        "delta",
        "theta",
        "alpha",
        "beta",
        "gamma",
    ):
        if key not in payload:
            continue
        if key in {"muse_connected", "muse_discovered"}:
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
    """Local adapter: emits simulated Muse-like EEG values."""

    def __init__(self) -> None:
        self.connected = False

    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def read_sample(self) -> EegSample:
        if not self.connected:
            raise RuntimeError("Muse adapter not connected")
        # Keep simulator ranges aligned with SignalProcessor calibration.
        base = random.uniform(620.0, 760.0)
        return EegSample(
            timestamp=datetime.now(tz=timezone.utc),
            channel_tp9=base + random.uniform(-20.0, 20.0),
            channel_af7=base + random.uniform(-20.0, 20.0),
            channel_af8=base + random.uniform(-20.0, 20.0),
            channel_tp10=base + random.uniform(-20.0, 20.0),
        )

    def get_ingestion_meta(self) -> dict[str, Any]:
        return {
            "bridge_mode": "python_sim",
            "muse_connected": False,
            "muse_discovered": False,
            "connection_state": -1,
            "muse_devices": [],
            "active_muse_name": "",
            "firmware_version": "",
            "delta": 0.0,
            "theta": 0.0,
            "alpha": 0.0,
            "beta": 0.0,
            "gamma": 0.0,
        }

    def send_bridge_command(self, _payload: dict[str, Any]) -> None:
        raise RuntimeError("Muse bridge commands require EEG_SOURCE=muse (TCP bridge)")


class TcpMuseBridgeAdapter:
    """Reads normalized samples from a native bridge over localhost TCP."""

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
            "connection_state": None,
            "muse_devices": [],
            "active_muse_name": "",
            "firmware_version": "",
            "delta": 0.0,
            "theta": 0.0,
            "alpha": 0.0,
            "beta": 0.0,
            "gamma": 0.0,
        }
        self._ingestion_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._eeg_queue: queue.Queue[EegSample] = queue.Queue()
        self._reader_stop = threading.Event()
        self._reader_thread: threading.Thread | None = None

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
            self._eeg_queue.put(sample)

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

    def read_sample(self) -> EegSample:
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
            return self._eeg_queue.get(timeout=timeout_s)
        except queue.Empty as e:
            # Reader thread died (bridge disconnected) — reset so next call retries.
            if self._reader_thread and not self._reader_thread.is_alive():
                self.disconnect()
            raise RuntimeError(f"No EEG sample received from bridge within {timeout_s:.1f}s") from e

    def get_ingestion_meta(self) -> dict[str, Any]:
        with self._ingestion_lock:
            return dict(self._ingestion_meta)


def build_ingestion_adapter(settings: Settings):
    source = settings.eeg_source.lower().strip()
    if source == "muse":
        return TcpMuseBridgeAdapter(
            host=settings.muse_bridge_host,
            port=settings.muse_bridge_port,
            timeout_seconds=settings.muse_bridge_timeout_seconds,
        )
    if source == "sim":
        return SimulatedMuseIngestionAdapter()
    raise ValueError("Unsupported EEG_SOURCE. Use 'sim' for local runs or 'muse' for live bridge mode.")
