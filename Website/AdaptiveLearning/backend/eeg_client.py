"""HTTP client for the EEGResearch FastAPI sidecar service (port 8001)."""
from __future__ import annotations

import os
import requests
from typing import Optional

EEG_API_URL     = os.getenv("EEG_API_URL", "http://127.0.0.1:8001")
EEG_API_TOKEN   = os.getenv("EEG_API_TOKEN")
EEG_ADMIN_TOKEN = os.getenv("EEG_ADMIN_TOKEN")

# Not validated at import time: the EEG sidecar is optional hardware, so
# requiring these tokens here would make the whole backend refuse to start
# for anyone doing frontend/Supabase-only work with no headband involved.
# Checked instead in the functions that actually send the header.


def _learner_headers() -> dict:
    if not EEG_API_TOKEN:
        raise RuntimeError("Missing EEG_API_TOKEN environment variable")
    return {"Authorization": f"Bearer {EEG_API_TOKEN}"}


def _admin_headers() -> dict:
    if not EEG_ADMIN_TOKEN:
        raise RuntimeError("Missing EEG_ADMIN_TOKEN environment variable")
    return {"Authorization": f"Bearer {EEG_ADMIN_TOKEN}"}


DEFAULT_DEVICE_ID = "default"


def is_alive(timeout: float = 1.5) -> bool:
    try:
        r = requests.get(f"{EEG_API_URL}/healthz", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def start_session(device_id: str = DEFAULT_DEVICE_ID) -> dict:
    """Tells the EEG service to start its simulator/bridge stream for device_id."""
    r = requests.post(
        f"{EEG_API_URL}/api/v1/session/start",
        headers=_admin_headers(), params={"device_id": device_id}, timeout=3,
    )
    r.raise_for_status()
    return r.json()


def stop_session(device_id: str = DEFAULT_DEVICE_ID) -> dict:
    r = requests.post(
        f"{EEG_API_URL}/api/v1/session/stop",
        headers=_admin_headers(), params={"device_id": device_id}, timeout=3,
    )
    r.raise_for_status()
    return r.json()


def get_state(device_id: str = DEFAULT_DEVICE_ID, timeout: float = 2.0) -> Optional[dict]:
    """Returns the latest interpreted EEG snapshot for device_id, or None if idle / unavailable."""
    # Built before the try, so a missing token raises instead of being caught
    # below and reported as "sidecar unavailable". A stopped sidecar is
    # transient; a missing token never fixes itself and needs to be seen.
    headers = _learner_headers()
    try:
        r = requests.get(
            f"{EEG_API_URL}/api/v1/state",
            headers=headers, params={"device_id": device_id}, timeout=timeout,
        )
        if r.status_code != 200:
            return None
        body = r.json()
        if body.get("status") != "ok":
            return None
        return body.get("data")
    except Exception:
        return None


def muse_refresh(device_id: str = DEFAULT_DEVICE_ID) -> dict:
    """Tell the native bridge to scan for nearby Muse headbands."""
    r = requests.post(
        f"{EEG_API_URL}/api/v1/muse/refresh",
        headers=_admin_headers(), params={"device_id": device_id}, timeout=5,
    )
    r.raise_for_status()
    return r.json()


def muse_disconnect(device_id: str = DEFAULT_DEVICE_ID) -> dict:
    """Tell the native bridge to disconnect from the current headband."""
    r = requests.post(
        f"{EEG_API_URL}/api/v1/muse/disconnect",
        headers=_admin_headers(), params={"device_id": device_id}, timeout=5,
    )
    r.raise_for_status()
    return r.json()


def muse_connect(name: str, device_id: str = DEFAULT_DEVICE_ID) -> dict:
    """Tell the native bridge to connect to a specific headband by name."""
    r = requests.post(
        f"{EEG_API_URL}/api/v1/muse/connect", headers=_admin_headers(),
        json={"name": name, "device_id": device_id}, timeout=5,
    )
    r.raise_for_status()
    return r.json()


def get_muse_status(device_id: str = DEFAULT_DEVICE_ID) -> dict:
    # Outside the try for the same reason as get_state.
    headers = _learner_headers()
    try:
        r = requests.get(
            f"{EEG_API_URL}/api/v1/muse/status",
            headers=headers, params={"device_id": device_id}, timeout=2,
        )
        if r.status_code != 200:
            return {"available": False}
        body = r.json().get("data", {}) or {}
        body["available"] = True
        return body
    except Exception:
        return {"available": False}


def list_devices() -> list:
    """List the sidecar's registered devices (stations), for the frontend picker."""
    headers = _learner_headers()
    try:
        r = requests.get(f"{EEG_API_URL}/api/v1/devices", headers=headers, timeout=3)
        if r.status_code != 200:
            return []
        return r.json().get("data", []) or []
    except Exception:
        return []


# Lives in `signal_mapping`, which both ingestion paths import. Re-exported
# here so existing callers keep working. New code should import from
# signal_mapping directly.
from signal_mapping import map_eeg_to_cognitive  # noqa: E402,F401
