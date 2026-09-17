"""The simulator pairs like a headband: refresh finds one, connect holds it.

The page's Connect button runs one sequence on every source -- scan, wait for
`muse_devices`, connect, poll `muse_connected`, adopt on `eeg_age_ms` -- so
the simulator has to answer each step the way the bridge does, or a sim run
fails at "no device" while the sample stream underneath it flows regardless.
"""

import pytest
from fastapi.testclient import TestClient

from src.app.config import get_settings
from src.app.main import app, stream_manager
from src.app.services.eeg_ingestion import SimulatedMuseIngestionAdapter, enrich_ingestion_dict


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _adapter():
    clock = _Clock()
    return SimulatedMuseIngestionAdapter(clock=clock), clock


def _pair(adapter):
    adapter.send_bridge_command({"cmd": "refresh"})
    adapter.send_bridge_command({"cmd": "connect", "name": adapter.SIM_DEVICE_NAME})


def test_a_fresh_simulator_has_found_nothing_and_holds_nothing():
    adapter, _ = _adapter()
    meta = adapter.get_ingestion_meta()
    assert meta["muse_connected"] is False
    assert meta["muse_discovered"] is False
    assert meta["muse_devices"] == []
    assert meta["active_muse_name"] == ""
    assert meta["eeg_age_ms"] is None
    assert enrich_ingestion_dict(get_settings(), meta)["connection_state_name"] == "disconnected"


def test_refresh_discovers_the_one_device_without_pairing_it():
    adapter, _ = _adapter()
    adapter.send_bridge_command({"cmd": "refresh"})
    meta = adapter.get_ingestion_meta()
    assert meta["muse_devices"] == [adapter.SIM_DEVICE_NAME]
    assert meta["muse_discovered"] is True
    assert meta["muse_connected"] is False
    assert meta["active_muse_name"] == ""


def test_connect_pairs_the_discovered_device():
    adapter, _ = _adapter()
    _pair(adapter)
    meta = adapter.get_ingestion_meta()
    assert meta["muse_connected"] is True
    assert meta["active_muse_name"] == adapter.SIM_DEVICE_NAME
    assert enrich_ingestion_dict(get_settings(), meta)["connection_state_name"] == "connected"


@pytest.mark.parametrize(
    "payload, reason",
    [
        ({"cmd": "connect", "name": "MuseS-9999"}, "not in list"),
        ({"cmd": "connect"}, 'missing "name"'),
        ({"cmd": "connect", "name": "  "}, 'missing "name"'),
    ],
)
def test_connect_refuses_what_the_bridge_refuses_and_stays_unpaired(payload, reason):
    adapter, _ = _adapter()
    adapter.send_bridge_command({"cmd": "refresh"})
    with pytest.raises(RuntimeError, match=reason):
        adapter.send_bridge_command(payload)
    assert adapter.get_ingestion_meta()["muse_connected"] is False


def test_connect_before_any_scan_is_refused():
    adapter, _ = _adapter()
    with pytest.raises(RuntimeError, match="not in list"):
        adapter.send_bridge_command({"cmd": "connect", "name": adapter.SIM_DEVICE_NAME})


def test_an_unknown_command_is_refused_rather_than_ignored():
    adapter, _ = _adapter()
    with pytest.raises(RuntimeError, match="unknown bridge cmd"):
        adapter.send_bridge_command({"cmd": "reboot"})


def test_disconnect_clears_the_pairing_and_the_scan():
    adapter, _ = _adapter()
    _pair(adapter)
    adapter.send_bridge_command({"cmd": "disconnect"})
    meta = adapter.get_ingestion_meta()
    assert meta["muse_connected"] is False
    assert meta["muse_devices"] == []
    assert meta["active_muse_name"] == ""
    assert meta["eeg_age_ms"] is None


def test_eeg_age_is_null_while_a_fresh_link_settles_then_reads_as_a_packet_clock():
    # The bridge zeroes its packet clock on CONNECTED and a preset switch
    # keeps it null for seconds; the page calls that "settling" and refuses
    # to adopt it. Once packets flow the age is under one 256 Hz interval.
    adapter, clock = _adapter()
    _pair(adapter)
    assert adapter.get_ingestion_meta()["eeg_age_ms"] is None
    clock.now += adapter.PAIR_SETTLE_SECONDS - 0.5
    assert adapter.get_ingestion_meta()["eeg_age_ms"] is None
    clock.now += 0.5
    age = adapter.get_ingestion_meta()["eeg_age_ms"]
    assert age is not None and 0 <= age < adapter._PACKET_INTERVAL_MS


def test_the_packet_clock_runs_without_the_sidecar_reading_a_sample():
    # On hardware the bridge's clock is driven by BLE packets, so a paired
    # headband stays adoptable (age under 3 s) across a stream stop. Stamped
    # from read_sample the age grew unbounded and adoption was unreachable.
    adapter, clock = _adapter()
    adapter.connect()
    _pair(adapter)
    adapter.disconnect()
    clock.now += 600.0
    age = adapter.get_ingestion_meta()["eeg_age_ms"]
    assert age is not None and age < 3000


def test_reading_samples_does_not_shorten_the_settle_window():
    # The sidecar's 4 Hz reads must not end the settle within one tick, or
    # the page's linkSettling state is never observable on the simulator.
    adapter, clock = _adapter()
    adapter.connect()
    _pair(adapter)
    for _ in range(8):
        clock.now += 0.25
        adapter.read_sample()
    assert adapter.get_ingestion_meta()["eeg_age_ms"] is None


def test_a_repeat_connect_zeroes_the_packet_clock_like_the_bridge():
    adapter, clock = _adapter()
    _pair(adapter)
    clock.now += adapter.PAIR_SETTLE_SECONDS + 1.0
    assert adapter.get_ingestion_meta()["eeg_age_ms"] is not None
    adapter.send_bridge_command({"cmd": "connect", "name": adapter.SIM_DEVICE_NAME})
    assert adapter.get_ingestion_meta()["eeg_age_ms"] is None


def test_the_sample_stream_runs_whether_or_not_anything_is_paired():
    adapter, _ = _adapter()
    adapter.connect()
    adapter.read_sample()
    _pair(adapter)
    adapter.read_sample()


def test_stopping_the_stream_keeps_the_pairing_like_the_bridge_holds_a_link():
    adapter, _ = _adapter()
    adapter.connect()
    _pair(adapter)
    adapter.disconnect()
    assert adapter.get_ingestion_meta()["muse_connected"] is True


def test_the_muse_routes_pair_the_simulator_end_to_end():
    client = TestClient(app)
    settings = get_settings()
    admin = {"Authorization": f"Bearer {settings.admin_token}"}
    learner = {"Authorization": f"Bearer {settings.api_token}"}
    adapter = stream_manager.session().adapter
    assert isinstance(adapter, SimulatedMuseIngestionAdapter)
    try:
        r = client.post("/api/v1/muse/refresh", headers=admin)
        assert r.status_code == 200 and r.json()["data"] == {"ok": True}
        ing = client.get("/api/v1/muse/status", headers=learner).json()["data"]["ingestion"]
        assert ing["muse_devices"] == [adapter.SIM_DEVICE_NAME]
        assert ing["muse_connected"] is False

        r = client.post("/api/v1/muse/connect", json={"name": ing["muse_devices"][0]}, headers=admin)
        assert r.status_code == 200 and r.json()["data"] == {"ok": True}
        ing = client.get("/api/v1/muse/status", headers=learner).json()["data"]["ingestion"]
        assert ing["muse_connected"] is True
        assert ing["active_muse_name"] == adapter.SIM_DEVICE_NAME
        assert ing["connection_state_name"] == "connected"

        r = client.post("/api/v1/muse/connect", json={"name": "MuseS-9999"}, headers=admin)
        assert r.status_code == 200
        assert r.json()["data"]["ok"] is False and "not in list" in r.json()["data"]["error"]
    finally:
        r = client.post("/api/v1/muse/disconnect", headers=admin)
        assert r.status_code == 200 and r.json()["data"] == {"ok": True}
    ing = client.get("/api/v1/muse/status", headers=learner).json()["data"]["ingestion"]
    assert ing["muse_connected"] is False and ing["muse_devices"] == []
