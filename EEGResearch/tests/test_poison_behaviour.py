"""A malformed band dict leaves the estimator ready; a raw artifact (clench) poisons it, live and in replay."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import numpy as np

from src.app.config import get_settings
from src.app.services.eeg_spectrum import CHANNELS, EPOCH_SECONDS, SAMPLE_RATE_HZ, poisons_buffer
from src.app.services.stream_manager import DeviceConfig, DeviceSession
from tests.test_eeg_spectrum import T0, pink, samples_from
from tests.test_signal_processing import CONTACT_GOOD, RELAXED

GOOD = {**RELAXED, **CONTACT_GOOD, "band_channels_used": 4}
MALFORMED = {**GOOD, "alpha": float("nan")}
CLENCH = {**GOOD, "gamma": RELAXED["beta"] + 1.0}  # gamma - beta > EMG_GAMMA_EXCESS


def test_the_decision():
    assert poisons_buffer("delta_jump") and poisons_buffer("emg_gamma") and poisons_buffer("spread_jump")
    assert not poisons_buffer("malformed_bands") and not poisons_buffer(None)


class _Adapter:
    """Ticks good, malformed, clench; records the estimator state each previous tick left."""

    def __init__(self, session: DeviceSession):
        self.session = session
        n = int(EPOCH_SECONDS * SAMPLE_RATE_HZ)
        all_samples = samples_from({c: pink(n + 128, i) for i, c in enumerate(CHANNELS)})
        self.batches = [all_samples[:n], all_samples[n:n + 64], all_samples[n + 64:]]
        self.metas = [GOOD, MALFORMED, CLENCH]
        self.seen: list[dict] = []
        self.calls = 0

    def drain_samples(self, _max):
        self.seen.append(dict(self.session.spectrum.latest()))
        if self.calls == len(self.batches):
            self.session.running = False
            raise RuntimeError("done")
        batch = self.batches[self.calls]
        self.calls += 1
        return batch

    def get_ingestion_meta(self):
        return dict(self.metas[self.calls - 1])

    def disconnect(self):
        pass


def test_a_malformed_band_dict_does_not_poison_the_stream_loop_and_a_clench_does():
    session = DeviceSession("station1", get_settings(),
                            DeviceConfig(device_id="station1", kind="sim",
                                         host="127.0.0.1", port=8765))
    session.device_config = DeviceConfig(device_id="station1", kind="muse",
                                         host="127.0.0.1", port=8765)
    adapter = _Adapter(session)
    session.adapter = adapter
    session.running = True
    asyncio.run(session._loop())
    after_good, after_malformed, after_clench = adapter.seen[1:4]
    assert after_good["ready"] is True
    assert session.errors_seen == 1, "only the stop"
    assert after_malformed["ready"] is True, "a fault in the band dict is not an artifact in the samples"
    assert after_clench["ready"] is False and after_clench["reason"] == "artifact"


def test_the_replay_applies_the_same_rule(tmp_path):
    import sys
    sys.path.insert(0, str(tmp_path.parents[0]))
    from scripts.replay_raw_capture import replay
    n = int(EPOCH_SECONDS * SAMPLE_RATE_HZ)
    per_tick = int(SAMPLE_RATE_HZ / 4)
    # 6 s: "a" for 4 s (fills, then one ready second), "b" 1 s malformed, "c" 1 s clench.
    total = 6 * n // 4
    chans = {c: pink(total, i) for i, c in enumerate(CHANNELS)}
    path = tmp_path / "raw.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for i in range(total):
            sec = i / SAMPLE_RATE_HZ
            seg, bands = ("a", GOOD) if sec < 4 else ("b", MALFORMED) if sec < 5 else ("c", CLENCH)
            row = {"kind": "eeg", "t": (T0 + timedelta(seconds=sec)).isoformat(), "segment": seg,
                   **{c: float(chans[c][i]) for c in CHANNELS}, **bands}
            fh.write(json.dumps(row) + "\n")
    out = replay(str(path), hz=4.0)
    ticks_per_second = SAMPLE_RATE_HZ / per_tick
    assert len(out["b"]["alpha"]) == ticks_per_second, "every malformed tick still had an estimate"
    assert len(out["c"]["alpha"]) <= 1, "the clench poisoned the rest of its second"
