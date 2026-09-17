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


def test_eeg_age_is_null_while_a_fresh_link_settles_then_counts_from_the_last_packet():
    # The bridge zeroes its packet clock on CONNECTED and a preset switch
    # keeps it null for seconds; the page calls that "settling" and refuses
    # to adopt it. Once the stream delivers, the age is time since the last
    # read -- under 3 s at the sidecar's 4 Hz, so the link is alive.
    adapter, clock = _adapter()
    adapter.connect()
    _pair(adapter)
    assert adapter.get_ingestion_meta()["eeg_age_ms"] is None
    clock.now += adapter.PAIR_SETTLE_SECONDS - 0.5
    adapter.read_sample()
    assert adapter.get_ingestion_meta()["eeg_age_ms"] is None
    clock.now += 0.5
    adapter.read_sample()
    clock.now += 0.25
    assert adapter.get_ingestion_meta()["eeg_age_ms"] == 250


def test_a_silent_stream_lets_the_age_climb_which_is_the_drop():
    # CONNECTED-but-silent is the hardware state linkAlive and the bridge
    # watchdog exist for; on the simulator the stopped stream is what stands
    # in for the headband going quiet. Modelled from the pairing alone the
    # age wrapped inside 0-3 ms for ever and this state was unreachable.
    adapter, clock = _adapter()
    adapter.connect()
    _pair(adapter)
    clock.now += adapter.PAIR_SETTLE_SECONDS
    adapter.read_sample()
    adapter.disconnect()
    clock.now += 60.0
    meta = adapter.get_ingestion_meta()
    assert meta["muse_connected"] is True
    assert meta["eeg_age_ms"] == 60_000


def test_a_delivered_packet_moves_the_age_origin_forward():
    adapter, clock = _adapter()
    adapter.connect()
    _pair(adapter)
    clock.now += adapter.PAIR_SETTLE_SECONDS + 10.0
    assert adapter.get_ingestion_meta()["eeg_age_ms"] == 10_000
    adapter.read_sample()
    clock.now += 0.25
    assert adapter.get_ingestion_meta()["eeg_age_ms"] == 250


def test_a_link_that_never_delivered_counts_from_the_end_of_the_settle():
    adapter, clock = _adapter()
    _pair(adapter)
    clock.now += adapter.PAIR_SETTLE_SECONDS + 2.0
    assert adapter.get_ingestion_meta()["eeg_age_ms"] == 2000


def test_restarting_the_stream_revives_the_link_before_the_first_read():
    # Under pull, Connect starts the stream and reads the status before the
    # first 4 Hz tick; a re-paired link must be adoptable at that moment.
    adapter, clock = _adapter()
    adapter.connect()
    _pair(adapter)
    clock.now += adapter.PAIR_SETTLE_SECONDS
    adapter.read_sample()
    adapter.disconnect()
    clock.now += 600.0
    assert adapter.get_ingestion_meta()["eeg_age_ms"] >= 3000
    adapter.connect()
    assert adapter.get_ingestion_meta()["eeg_age_ms"] == 0


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
    adapter.connect()
    _pair(adapter)
    clock.now += adapter.PAIR_SETTLE_SECONDS + 1.0
    adapter.read_sample()
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


# --- battery ---------------------------------------------------------------


def _paired_for(adapter, clock, seconds):
    _pair(adapter)
    clock.now += seconds
    return adapter.get_ingestion_meta()["battery_percent"]


def test_battery_is_null_before_the_first_report_and_a_charge_after():
    # libMuse fires BATTERY on its own schedule, so the bridge reports null
    # for most of the first minute; the badge renders nothing rather than a
    # broken-looking empty slot. Null, never 0, which is a real reading.
    adapter, clock = _adapter()
    assert _paired_for(adapter, clock, adapter.BATTERY_FIRST_REPORT_SECONDS - 1.0) is None
    clock.now += 1.0
    pct = adapter.get_ingestion_meta()["battery_percent"]
    lo, hi = adapter.BATTERY_START_RANGE
    assert isinstance(pct, float) and lo <= pct <= hi


def test_battery_drains_on_the_clock_whether_or_not_anything_reads():
    adapter, clock = _adapter()
    first = _paired_for(adapter, clock, adapter.BATTERY_FIRST_REPORT_SECONDS)
    clock.now += 3600.0
    assert adapter.get_ingestion_meta()["battery_percent"] == pytest.approx(
        first - adapter.BATTERY_DRAIN_PCT_PER_HOUR, abs=0.2)


def test_battery_floors_at_zero_and_reports_it_as_a_number():
    adapter, clock = _adapter()
    _paired_for(adapter, clock, adapter.BATTERY_FIRST_REPORT_SECONDS)
    clock.now += 100.0 * 3600.0
    assert adapter.get_ingestion_meta()["battery_percent"] == 0.0


def test_unpaired_and_disconnected_links_report_no_battery_and_a_repair_resumes_it():
    adapter, clock = _adapter()
    assert adapter.get_ingestion_meta()["battery_percent"] is None
    first = _paired_for(adapter, clock, adapter.BATTERY_FIRST_REPORT_SECONDS)
    adapter.send_bridge_command({"cmd": "disconnect"})
    assert adapter.get_ingestion_meta()["battery_percent"] is None
    # One simulated headband: its charge keeps draining while unpaired.
    again = _paired_for(adapter, clock, adapter.BATTERY_FIRST_REPORT_SECONDS)
    assert again is not None and 0 <= first - again < 1.0


def test_a_repeat_connect_goes_null_again_but_keeps_the_same_charge():
    # The bridge's stored value is reset on every connect until the next
    # BATTERY packet; the headband underneath has not been swapped.
    adapter, clock = _adapter()
    first = _paired_for(adapter, clock, adapter.BATTERY_FIRST_REPORT_SECONDS)
    adapter.send_bridge_command({"cmd": "connect", "name": adapter.SIM_DEVICE_NAME})
    assert adapter.get_ingestion_meta()["battery_percent"] is None
    clock.now += adapter.BATTERY_FIRST_REPORT_SECONDS
    again = adapter.get_ingestion_meta()["battery_percent"]
    assert again is not None and 0 <= first - again < 1.0


def test_different_simulators_draw_different_charges():
    seen = set()
    for _ in range(12):
        adapter, clock = _adapter()
        seen.add(_paired_for(adapter, clock, adapter.BATTERY_FIRST_REPORT_SECONDS))
    assert len(seen) > 1


# --- electrode contact -------------------------------------------------------

from src.app.services.signal_processing import SignalProcessor  # noqa: E402


def _contact_shares(seed, hours=1.0, hz=4.0):
    """Drive SignalProcessor._contact_ratio -- smoothing, thresholds and all
    -- over a simulated run, and return the share of ticks in each verdict."""
    clock = _Clock()
    adapter = SimulatedMuseIngestionAdapter(clock=clock, seed=seed)
    processor = SignalProcessor(clock=clock)
    counts = {"good": 0, "degraded": 0, "poor": 0}
    ticks = int(hours * 3600 * hz)
    for _ in range(ticks):
        clock.now += 1.0 / hz
        ratio = processor._contact_ratio(adapter.get_ingestion_meta(), clock.now)
        if ratio >= SignalProcessor.CONTACT_GOOD:
            counts["good"] += 1
        elif ratio >= SignalProcessor.CONTACT_DEGRADED:
            counts["degraded"] += 1
        else:
            counts["poor"] += 1
    return {k: v / ticks for k, v in counts.items()}


def test_contact_is_mostly_degraded_with_poor_as_an_occasional_minority():
    # Degraded is the ordinary state on hardware and poor is the fault
    # (EEG_REFERENCE.md); a simulator pinned at hsi [1,1,1,1] never reached
    # the contact gate at all. Measured through the processor's own
    # smoothing and lines, not the raw hsi.
    for seed in (1, 2, 3):
        shares = _contact_shares(seed)
        assert 0.35 <= shares["degraded"] <= 0.75, shares
        assert 0.02 <= shares["poor"] <= 0.25, shares
        assert shares["good"] >= 0.10, shares


def test_contact_streaks_are_held_on_the_clock_not_redrawn_per_read():
    clock = _Clock()
    adapter = SimulatedMuseIngestionAdapter(clock=clock, seed=7)
    first = adapter.get_ingestion_meta()["hsi"]
    # Many reads inside the shortest possible streak: nothing changes.
    for _ in range(40):
        clock.now += 0.1
        assert adapter.get_ingestion_meta()["hsi"] == first
    # Well past the longest streak: at least one electrode has been redrawn.
    clock.now += 200.0
    adapter.get_ingestion_meta()
    seen = {tuple(adapter.get_ingestion_meta()["hsi"])}
    for _ in range(20):
        clock.now += 100.0
        seen.add(tuple(adapter.get_ingestion_meta()["hsi"]))
    assert len(seen) > 1


def test_is_good_follows_hsi_and_band_channels_count_the_seated_ones():
    clock = _Clock()
    adapter = SimulatedMuseIngestionAdapter(clock=clock, seed=11)
    saw_poor = False
    for _ in range(600):
        clock.now += 5.0
        meta = adapter.get_ingestion_meta()
        assert set(meta["hsi"]) <= {1.0, 2.0, 4.0}
        assert meta["is_good"] == [1.0 if v <= 2.0 else 0.0 for v in meta["hsi"]]
        assert meta["band_channels_used"] == max(1, int(sum(meta["is_good"])))
        saw_poor = saw_poor or 4.0 in meta["hsi"]
    assert saw_poor


def test_contact_is_reproducible_under_a_seed_and_varies_without_one():
    a = _contact_shares(5, hours=0.25)
    b = _contact_shares(5, hours=0.25)
    assert a == b
    clock = _Clock()
    x = SimulatedMuseIngestionAdapter(clock=clock)
    y = SimulatedMuseIngestionAdapter(clock=clock)
    seqs = []
    for adapter in (x, y):
        seq = []
        for _ in range(30):
            clock.now += 50.0
            seq.append(tuple(adapter.get_ingestion_meta()["hsi"]))
        seqs.append(seq)
    assert seqs[0] != seqs[1]


# --- task-responsive state ---------------------------------------------------


def _states(adapter):
    return adapter._effective_states()


def test_a_run_of_misses_pulls_calm_down_and_a_run_of_correct_answers_lifts_focus():
    clock = _Clock()
    adapter = SimulatedMuseIngestionAdapter(clock=clock, seed=1)
    adapter._focus_state = adapter._calm_state = 0.5
    f0, c0 = _states(adapter)
    for _ in range(3):
        adapter.report_answer(False)
    f1, c1 = _states(adapter)
    assert c1 < c0 and f1 < f0
    for _ in range(6):
        adapter.report_answer(True)
    f2, c2 = _states(adapter)
    assert f2 > f1 and c2 > c1


def test_the_bias_is_bounded_and_a_hard_miss_counts_for_more():
    clock = _Clock()
    adapter = SimulatedMuseIngestionAdapter(clock=clock, seed=1)
    adapter._focus_state = adapter._calm_state = 0.5
    for _ in range(50):
        adapter.report_answer(False)
    _, calm = _states(adapter)
    assert calm == pytest.approx(0.5 - adapter.TASK_BIAS_BOUND)
    easy = SimulatedMuseIngestionAdapter(clock=clock, seed=1)
    hard = SimulatedMuseIngestionAdapter(clock=clock, seed=1)
    easy._calm_state = hard._calm_state = 0.5
    easy.report_answer(False, "easy")
    hard.report_answer(False, "hard")
    assert _states(hard)[1] < _states(easy)[1]


def test_the_bias_decays_on_the_clock_towards_the_undisturbed_state():
    clock = _Clock()
    adapter = SimulatedMuseIngestionAdapter(clock=clock, seed=1)
    adapter._focus_state = adapter._calm_state = 0.5
    for _ in range(4):
        adapter.report_answer(False)
    _, c_after = _states(adapter)
    clock.now += adapter.TASK_BIAS_DECAY_SECONDS
    _, c_later = _states(adapter)
    clock.now += 10 * adapter.TASK_BIAS_DECAY_SECONDS
    _, c_long = _states(adapter)
    assert c_after < c_later < c_long
    assert c_long == pytest.approx(0.5, abs=0.001)


def test_the_bias_reaches_the_bands_the_processor_reads():
    # The point of the bias is that the processor sees it through its own
    # path -- so alpha (calm) on the meta moves, not only a hidden number.
    clock = _Clock()
    adapter = SimulatedMuseIngestionAdapter(clock=clock, seed=1)
    adapter._focus_state = adapter._calm_state = 0.5
    before = adapter.get_ingestion_meta()["alpha"]
    for _ in range(4):
        adapter.report_answer(False)
    assert adapter.get_ingestion_meta()["alpha"] < before


def test_no_task_bias_can_look_like_an_artifact_to_the_processor():
    # The raw spread scales with 1.6 - calm over calm in 0..1, a 2.67x
    # range end to end, under SPREAD_JUMP_FACTOR; delta and gamma are
    # constants, so the delta and EMG gates have nothing to trip on either.
    clock = _Clock()
    adapter = SimulatedMuseIngestionAdapter(clock=clock, seed=1)
    adapter.connect()
    widest = 1.6 - 0.0
    narrowest = 1.6 - 1.0
    assert widest / narrowest < SignalProcessor.SPREAD_JUMP_FACTOR
    metas = []
    adapter._focus_state = adapter._calm_state = 0.9
    metas.append(adapter.get_ingestion_meta())
    for _ in range(50):
        adapter.report_answer(False, "hard")
    metas.append(adapter.get_ingestion_meta())
    assert metas[0]["delta"] == metas[1]["delta"]
    assert metas[0]["gamma"] == metas[1]["gamma"]


def test_the_answer_route_reaches_the_simulator_and_hardware_ignores_it():
    client = TestClient(app)
    settings = get_settings()
    admin = {"Authorization": f"Bearer {settings.admin_token}"}
    learner = {"Authorization": f"Bearer {settings.api_token}"}
    session = stream_manager.session()
    adapter = session.adapter
    assert isinstance(adapter, SimulatedMuseIngestionAdapter)
    before = adapter._task_calm
    r = client.post("/api/v1/session/answer", json={"correct": False}, headers=admin)
    assert r.status_code == 200 and r.json()["data"] == {"ok": True, "applied": True}
    assert adapter._task_calm < before
    # Under pull the learner token gains nothing, as for /session/arm.
    assert client.post("/api/v1/session/answer", json={"correct": True}, headers=learner).status_code in (401, 403)
    assert client.post("/api/v1/session/answer", json={"correct": True, "device_id": "nope"}, headers=admin).status_code == 404
    real = session.adapter
    session.adapter = object()  # a hardware adapter has no report_answer
    try:
        r = client.post("/api/v1/session/answer", json={"correct": True}, headers=admin)
    finally:
        session.adapter = real
    assert r.status_code == 200 and r.json()["data"] == {"ok": True, "applied": False}


def test_the_focus_bias_is_bounded_too():
    clock = _Clock()
    adapter = SimulatedMuseIngestionAdapter(clock=clock, seed=1)
    adapter._focus_state = adapter._calm_state = 0.5
    for _ in range(50):
        adapter.report_answer(True)
    focus, _ = _states(adapter)
    assert focus == pytest.approx(0.5 + adapter.TASK_BIAS_BOUND)


def test_the_bias_reaches_the_raw_channels_the_artifact_gate_reads(monkeypatch):
    # The spread the processor's artifact gate measures scales with the
    # *effective* calm: a stressed student's raw signal is the erratic one.
    clock = _Clock()
    adapter = SimulatedMuseIngestionAdapter(clock=clock, seed=1)
    monkeypatch.setattr(adapter._rng, "uniform", lambda a, b: b)
    adapter.connect()
    adapter._focus_state = adapter._calm_state = 0.5

    def deviation():
        adapter._focus_state = adapter._calm_state = 0.5
        s = adapter.read_sample()
        focus, calm = adapter._effective_states()
        base = adapter._BASE_LEVEL + (focus - 0.5) * adapter._LEVEL_SPAN
        return s.channel_tp9 - base, calm

    calm_dev, calm0 = deviation()
    for _ in range(50):
        adapter.report_answer(False, "hard")
    stressed_dev, calm1 = deviation()
    assert calm1 < calm0
    assert stressed_dev == pytest.approx(adapter._CHANNEL_NOISE * (1.6 - calm1))
    assert stressed_dev > calm_dev


# --- simulated pulse ----------------------------------------------------------

from src.app.services.optics_processing import RATE_WINDOW_SECONDS, EMIT_EVERY_SECONDS, build_heart_record  # noqa: E402
from src.app.services.ppg_processing import HeartRateTracker  # noqa: E402


def _streaming_paired(seed=3, sim_optics=True):
    clock = _Clock()
    adapter = SimulatedMuseIngestionAdapter(clock=clock, seed=seed, sim_optics=sim_optics)
    adapter.connect()
    _pair(adapter)
    return adapter, clock


def _windows(adapter, clock, count, step=EMIT_EVERY_SECONDS):
    tracker = HeartRateTracker()
    out = []
    for i in range(count):
        clock.now += step
        out.append(build_heart_record(adapter.optics_window(RATE_WINDOW_SECONDS), tracker, step))
    return out


def test_no_optics_without_a_paired_streaming_device():
    clock = _Clock()
    adapter = SimulatedMuseIngestionAdapter(clock=clock, seed=3, sim_optics=True)
    tracker = HeartRateTracker()
    assert build_heart_record(adapter.optics_window(RATE_WINDOW_SECONDS), tracker, 10)["rejected_by"] == "no_samples"
    adapter.connect()
    clock.now += 60.0
    assert build_heart_record(adapter.optics_window(RATE_WINDOW_SECONDS), tracker, 10)["rejected_by"] == "no_samples"
    _pair(adapter)
    clock.now += 5.0
    # Paired and streaming, but under the 25 s window: warming up, not absent.
    assert build_heart_record(adapter.optics_window(RATE_WINDOW_SECONDS), tracker, 10)["rejected_by"] == "warming_up"


def test_the_pulse_goes_through_the_real_heart_path_anchor_hold_included():
    adapter, clock = _streaming_paired()
    records = _windows(adapter, clock, 6)
    # The first full window is withheld until a second agrees (the
    # unconfirmed-anchor rule), never published outright.
    first_full = next(r for r in records if r["rejected_by"] != "warming_up")
    assert first_full["bpm"] is None and first_full["rejected_by"] == "unconfirmed_anchor"
    accepted = [r for r in records if r["bpm"] is not None]
    assert accepted, records
    for r in accepted:
        assert r["source"] == "muse_optics" and r["trusted"] is True
        assert abs(r["bpm"] - adapter._heart_bpm(clock.now)) < 4.0
        assert r["sample_rate_hz"] == adapter.OPTICS_FS and r["channel_count"] == adapter.OPTICS_CHANNELS


def test_rmssd_is_derived_on_the_same_window_when_beats_agree():
    adapter, clock = _streaming_paired()
    records = _windows(adapter, clock, 8)
    with_rate = [r for r in records if r["bpm"] is not None]
    assert with_rate
    # Either a value or a named refusal on every accepted window -- the
    # enrichment's own field, never the rate's.
    for r in with_rate:
        assert (r["rmssd_ms"] is not None) != (r["rmssd_rejected_by"] is not None)
    assert any(r["rmssd_ms"] is not None for r in with_rate), [r["rmssd_rejected_by"] for r in with_rate]


def test_misses_raise_the_rate_and_it_settles_back():
    adapter, clock = _streaming_paired()
    rest = adapter._heart_bpm(clock.now)
    for _ in range(8):
        adapter.report_answer(False, "hard")
    raised = adapter._heart_bpm(clock.now)
    assert rest < raised <= rest + adapter.HEART_TASK_BOUND[1] + 0.01
    clock.now += 10 * adapter.TASK_BIAS_DECAY_SECONDS
    settled = adapter._heart_bpm(clock.now)
    assert abs(settled - adapter._heart_bpm(clock.now)) < 1e-9
    assert settled < raised


def test_a_stream_stop_or_a_disconnect_clears_the_optical_history():
    adapter, clock = _streaming_paired()
    clock.now += 40.0
    assert adapter.optics_window(RATE_WINDOW_SECONDS).span_seconds > 20.0
    adapter.disconnect()
    assert len(adapter.optics_window(RATE_WINDOW_SECONDS).channels) == 0
    adapter.connect()
    clock.now += 5.0
    assert adapter.optics_window(RATE_WINDOW_SECONDS).span_seconds < 6.0
    clock.now += 40.0
    adapter.clear_optics()
    clock.now += 3.0
    assert adapter.optics_window(RATE_WINDOW_SECONDS).span_seconds < 4.0
    adapter.send_bridge_command({"cmd": "disconnect"})
    assert len(adapter.optics_window(RATE_WINDOW_SECONDS).channels) == 0


def test_the_device_session_holds_a_heart_block_from_the_simulator():
    from src.app.config import DeviceConfig
    from src.app.services.stream_manager import DeviceSession
    settings = get_settings()
    session = DeviceSession("sim-heart", settings, DeviceConfig(device_id="sim-heart", kind="sim", host="", port=0))
    adapter, clock = _streaming_paired()
    session.adapter = adapter
    clock.now += 30.0
    block = session._optical_heart_block()
    assert block is not None and block["source"] == "muse_optics"
    assert block["rejected_by"] in ("unconfirmed_anchor", None)


def test_each_optical_channel_carries_its_own_noise():
    # Four opinions of one heart: the beat consensus and the agreement term
    # of the confidence are only meaningful when the channels differ.
    import numpy as np
    adapter, clock = _streaming_paired()
    clock.now += 30.0
    channels = adapter.optics_window(RATE_WINDOW_SECONDS).channels
    assert channels.shape[1] == adapter.OPTICS_CHANNELS
    for a in range(channels.shape[1]):
        for b in range(a + 1, channels.shape[1]):
            assert float(np.std(channels[:, a] - channels[:, b])) > 0.0


# --- review on #190 ------------------------------------------------------------


def test_the_pulse_is_opt_in_and_a_plain_simulator_stores_no_heart_rate():
    # Like MUSE_ENABLE_OPTICS on hardware: off, every window is refused as
    # no_samples, so no rate is ever recorded from a made-up pulse by default.
    adapter, clock = _streaming_paired(sim_optics=False)
    assert adapter.get_ingestion_meta()["optical_supported"] is False
    clock.now += 60.0
    record = build_heart_record(adapter.optics_window(RATE_WINDOW_SECONDS), HeartRateTracker(), 10)
    assert record["bpm"] is None and record["rejected_by"] == "no_samples"
    assert "synthetic" not in record


def test_a_synthesised_window_marks_its_record_and_a_real_one_does_not():
    import numpy as np
    from src.app.services.eeg_ingestion import OpticsWindow
    adapter, clock = _streaming_paired()
    records = _windows(adapter, clock, 4)
    assert all(r["synthetic"] is True for r in records)
    real = OpticsWindow(np.zeros((1600, 4)), 64.0, 64.0, 1.0, 25.0, 0.02, 4)
    assert "synthetic" not in build_heart_record(real, HeartRateTracker(), 10)


def test_the_setting_reaches_the_adapter(monkeypatch):
    from src.app.services.eeg_ingestion import build_ingestion_adapter
    settings = get_settings()
    monkeypatch.setattr(settings, "eeg_sim_optics", True)
    assert build_ingestion_adapter(settings, kind="sim").optics_enabled is True
    monkeypatch.setattr(settings, "eeg_sim_optics", False)
    assert build_ingestion_adapter(settings, kind="sim").optics_enabled is False


def _run(seed, with_optics_windows):
    clock = _Clock()
    adapter = SimulatedMuseIngestionAdapter(clock=clock, seed=seed, sim_optics=True)
    adapter.connect()
    _pair(adapter)
    trace = []
    for i in range(400):
        clock.now += 0.25
        s = adapter.read_sample()
        meta = adapter.get_ingestion_meta()
        trace.append((round(s.channel_tp9, 6), tuple(meta["hsi"]), meta["battery_percent"]))
        if with_optics_windows and i % 40 == 0:
            adapter.optics_window(RATE_WINDOW_SECONDS)
    return trace, adapter._heart_rest_bpm


def test_a_seed_replays_every_draw_the_simulator_makes():
    a, rest_a = _run(9, False)
    b, rest_b = _run(9, False)
    assert a == b and rest_a == rest_b
    c, _ = _run(10, False)
    assert c != a


def test_building_optics_windows_does_not_shift_the_contact_or_sample_sequence():
    a, _ = _run(9, False)
    b, _ = _run(9, True)
    assert a == b
