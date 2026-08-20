"""Tests for the sidecar-side push client.

Guards against a signal path that produces nothing, raises nothing, and looks
live: a queue silently dropping samples, a token outliving its session, or a
client counting what it sent instead of what was actually stored.
"""

import asyncio

import pytest

from src.app.services.push_client import MAX_BATCH, MAX_QUEUE, PushClient


@pytest.fixture
def anyio_backend():
    """asyncio only -- trio isn't installed and anyio would otherwise also run
    these tests under it."""
    return "asyncio"


class _Response:
    def __init__(self, status_code=200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {"ok": True, "inserted": 0}
        self.content = b"{}"

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    """Stands in for httpx.AsyncClient, recording what was posted."""

    def __init__(self, responder=None):
        self.calls = []
        self._responder = responder or (lambda url, json, headers: _Response(
            body={"ok": True, "inserted": len(json["samples"])}))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self._responder(url, json, headers)


@pytest.fixture
def client(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient",
                        lambda **_k: fake)
    pc = PushClient("http://backend:8000")
    pc._fake = fake
    return pc


async def _started(pc, session_id="s1", token="tok"):
    await pc.start(session_id, token)
    return pc


@pytest.mark.anyio
async def test_nothing_is_queued_before_a_session_starts():
    """No session open means no student samples held, let alone sent."""
    pc = PushClient("http://backend:8000")
    pc.enqueue("cognitive", {"ts": "t"})
    assert pc.status()["queued"]["cognitive"] == 0


@pytest.mark.anyio
async def test_the_token_does_not_outlive_the_session(client):
    """The token lives in memory for one session only."""
    await _started(client)
    assert client._token == "tok"

    await client.stop()
    assert client._token is None
    assert client._session_id is None


@pytest.mark.anyio
async def test_switching_session_drops_the_previous_queue(client):
    """Old samples belong to a session the new token may not own; posting them
    could attribute one session's readings to another."""
    await _started(client, "s1")
    client.enqueue("cognitive", {"ts": "t"})
    assert client.status()["queued"]["cognitive"] == 1

    await client.start("s2", "tok2")
    assert client.status()["queued"]["cognitive"] == 0
    assert client.status()["session_id"] == "s2"


@pytest.mark.anyio
async def test_a_full_queue_drops_oldest_and_counts_it(client):
    """`deque(maxlen=...)` evicts silently; the drop must be counted or nothing
    says data was lost."""
    await _started(client)
    for i in range(MAX_QUEUE + 5):
        client.enqueue("face", {"ts": i})

    status = client.status()
    assert status["queued"]["face"] == MAX_QUEUE
    assert status["dropped_locally"]["face"] == 5
    # Oldest gone, newest kept -- recent samples matter more for a live session.
    assert client._queues["face"][-1]["ts"] == MAX_QUEUE + 4


@pytest.mark.anyio
async def test_a_flush_stays_within_the_backends_batch_bound(client):
    await _started(client)
    for i in range(MAX_BATCH * 2):
        client.enqueue("cognitive", {"ts": i})

    await client._flush_once()

    assert len(client._fake.calls) == 1
    assert len(client._fake.calls[0]["json"]["samples"]) == MAX_BATCH
    assert client.status()["queued"]["cognitive"] == MAX_BATCH


@pytest.mark.anyio
async def test_a_failed_post_puts_the_samples_back_in_order(client, monkeypatch):
    """A transient failure must not reorder samples relative to what comes
    after them."""
    fake = _FakeClient(responder=lambda *_a, **_k: _Response(status_code=500))
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient",
                        lambda **_k: fake)
    await _started(client)
    for i in range(3):
        client.enqueue("cognitive", {"ts": i})

    with pytest.raises(Exception):
        await client._flush_once()

    assert [s["ts"] for s in client._queues["cognitive"]] == [0, 1, 2]


@pytest.mark.anyio
async def test_a_429_is_a_failure_not_a_delivery(client, monkeypatch):
    """A 429 is the backend's rate limit; counting it as delivered would drop
    the samples while reporting success."""
    fake = _FakeClient(responder=lambda *_a, **_k: _Response(status_code=429))
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient",
                        lambda **_k: fake)
    await _started(client)
    client.enqueue("heart", {"ts": 0, "source": "muse_optics"})

    with pytest.raises(RuntimeError, match="rate limited"):
        await client._flush_once()

    assert client.status()["queued"]["heart"] == 1
    assert client.status()["recorded"]["heart"] == 0


@pytest.mark.anyio
async def test_delivery_is_counted_from_the_backends_answer(client, monkeypatch):
    """`recorded` must reflect what the backend actually inserted, not what was
    sent -- otherwise a session that recorded nothing looks healthy."""
    fake = _FakeClient(responder=lambda *_a, **_k: _Response(
        body={"ok": True, "inserted": 0, "dropped": 2, "reason": "camera not consented"}))
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient",
                        lambda **_k: fake)
    await _started(client)
    client.enqueue("face", {"ts": 0})
    client.enqueue("face", {"ts": 1})

    await client._flush_once()

    assert client.status()["recorded"]["face"] == 0


@pytest.mark.anyio
async def test_repeated_failures_back_off_and_recover(client, monkeypatch):
    """A downed backend must not be retried at the flush rate for a whole
    lesson."""
    failing = _FakeClient(responder=lambda *_a, **_k: _Response(status_code=500))
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient",
                        lambda **_k: failing)
    await _started(client)
    client.enqueue("cognitive", {"ts": 0})

    for _ in range(3):
        try:
            await client._flush_once()
        except Exception as exc:  # noqa: BLE001
            client._note_failure(exc)
    first = client.status()["backoff_seconds"]
    assert first > 0

    ok = _FakeClient()
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient",
                        lambda **_k: ok)
    await client._flush_once()
    assert client.status()["backoff_seconds"] == 0
    assert client.status()["last_error"] is None


# ── payload shaping ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_eeg_features_go_up_unconverted(client):
    """No arithmetic on this side. The 0..100 -> 0..1 conversion lives once, in
    the backend's `signal_mapping`, shared by both the poller and this path."""
    await _started(client)
    client.submit_payload({
        "timestamp": "2026-08-10T10:00:00Z", "device_id": "station1",
        "features": {"focus_score": 72.0, "calm_score": 60.0},
        "channels": {"tp9": 1.0}, "state": {"label": "focused"},
    })

    sample = client._queues["cognitive"][0]
    assert sample["features"]["focus_score"] == 72.0, "converted on the wrong side"
    assert sample["raw"]["device_id"] == "station1"


@pytest.mark.anyio
async def test_a_camera_tick_splits_into_the_channels_present(client):
    await _started(client)
    client.submit_payload({
        "kind": "camera", "timestamp": "2026-08-10T10:00:00Z", "device_id": "cam0",
        "face": {"emotion": "happy", "emotion_confidence": 0.8, "trusted": True},
        "heart": {"source": "rppg", "bpm": 71.0, "confidence": 0.4},
    })

    queued = client.status()["queued"]
    assert queued["face"] == 1 and queued["heart"] == 1
    # A camera has no electrodes, so it must not produce a cognitive row.
    assert queued["cognitive"] == 0
    assert client._queues["face"][0]["emotion_trusted"] is True
    assert client._queues["heart"][0]["heart_rate_bpm"] == 71.0


@pytest.mark.anyio
async def test_a_switched_off_channel_produces_no_row(client):
    """`build_camera_payload` omits a disabled channel rather than nulling it --
    declined consent is not the same as a failed reading."""
    await _started(client)
    client.submit_payload({
        "kind": "camera", "timestamp": "t", "device_id": "cam0",
        "face": {"emotion": "neutral", "trusted": False},
    })

    assert client.status()["queued"]["heart"] == 0


@pytest.mark.anyio
async def test_a_heart_reading_without_a_source_is_dropped_locally(client):
    """Consent is per sensor, so a reading with no named sensor can't be
    consent-checked, and is dropped locally instead."""
    await _started(client)
    client.submit_payload({
        "kind": "camera", "timestamp": "t",
        "heart": {"bpm": 70.0},
    })

    assert client.status()["queued"]["heart"] == 0


@pytest.mark.anyio
async def test_one_channels_failure_does_not_cost_the_others(client, monkeypatch):
    """Each channel's batch must be drained and flushed independently -- a
    failure in one must not discard batches already popped from the others."""
    def responder(url, json, headers):
        return _Response(status_code=500) if url.endswith("/cognitive") else _Response(
            body={"ok": True, "inserted": len(json["samples"])})

    fake = _FakeClient(responder=responder)
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient",
                        lambda **_k: fake)
    await _started(client)
    client.enqueue("cognitive", {"ts": 0})
    client.enqueue("heart", {"ts": 0, "source": "muse_optics"})
    client.enqueue("face", {"ts": 0})

    with pytest.raises(Exception):
        await client._flush_once()

    status = client.status()
    assert status["queued"]["cognitive"] == 1, "the failed channel lost its batch"
    assert status["recorded"]["heart"] == 1, "heart never got sent"
    assert status["recorded"]["face"] == 1, "face never got sent"
    assert status["queued"]["heart"] == 0 and status["queued"]["face"] == 0


@pytest.mark.anyio
async def test_restoring_into_a_full_queue_counts_what_it_evicts(client, monkeypatch):
    """`extendleft` on a maxlen deque evicts from the far end (newest samples),
    the reverse of the drop-oldest rule -- must still be counted."""
    fake = _FakeClient(responder=lambda *_a, **_k: _Response(status_code=500))
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient",
                        lambda **_k: fake)
    await _started(client)
    for i in range(MAX_BATCH):
        client.enqueue("cognitive", {"ts": i})

    # Queue fills while that batch is notionally in flight.
    taken = client._take("cognitive")
    for i in range(MAX_QUEUE):
        client.enqueue("cognitive", {"ts": 1000 + i})
    before = client.status()["dropped_locally"]["cognitive"]

    client._restore("cognitive", taken)

    dropped = client.status()["dropped_locally"]["cognitive"] - before
    assert dropped == len(taken), "evictions from the restore went uncounted"
    assert len(client._queues["cognitive"]) == MAX_QUEUE


@pytest.mark.anyio
async def test_the_backoff_survives_a_full_queue(client):
    """The wake event fires whenever a batch fills, which during an outage is
    every few samples -- flushing on it regardless would retry a dead backend
    at the sample rate."""
    await _started(client)
    client._note_failure(RuntimeError("backend down"))
    assert client._retry_at > 0

    # A full batch sets the wake event; the backoff deadline must still hold.
    for i in range(MAX_BATCH):
        client.enqueue("cognitive", {"ts": i})
    assert client._wake.is_set()
    assert client._retry_at > 0, "the deadline was cleared by a wake"


@pytest.mark.anyio
async def test_shutdown_flushes_the_whole_backlog(client):
    """`_flush_once` takes at most MAX_BATCH per channel, so shutdown must call
    it repeatedly rather than once, or a large backlog is dropped."""
    await _started(client)
    for i in range(MAX_BATCH * 3):
        client.enqueue("cognitive", {"ts": i})

    await client.stop()

    sent = sum(len(c["json"]["samples"]) for c in client._fake.calls)
    assert sent == MAX_BATCH * 3, f"only {sent} of {MAX_BATCH * 3} were delivered"


@pytest.mark.anyio
async def test_the_emitted_payload_carries_what_the_pull_path_stores():
    """`bands` and `ingestion` are assembled in `snapshot()`, not carried on
    `latest_payload`. Emitting the raw payload instead of the snapshot would
    give push-ingested EEG rows null band powers while pull-ingested ones
    (which read `/api/v1/state`, i.e. `snapshot()`) have them."""
    from src.app.config import DeviceConfig, get_settings
    from src.app.services.stream_manager import DeviceSession

    session = DeviceSession("d1", get_settings(),
                            DeviceConfig(device_id="d1", kind="sim",
                                         host="127.0.0.1", port=8765))
    session.latest_payload = session._no_signal_payload()

    seen = []
    session.on_payload = seen.append
    await session._emit()

    assert "bands" in seen[0], "band powers never reached the push path"
    assert set(seen[0]["bands"]) == {"delta", "theta", "alpha", "beta", "gamma"}


@pytest.mark.anyio
async def test_a_raising_consumer_does_not_kill_the_sampling_loop():
    """A consumer that raises must not kill the local sampling loop -- losing
    it would be worse than losing the remote write."""
    from src.app.config import DeviceConfig, get_settings
    from src.app.services.stream_manager import DeviceSession

    session = DeviceSession("d1", get_settings(),
                            DeviceConfig(device_id="d1", kind="sim",
                                         host="127.0.0.1", port=8765))
    session.latest_payload = session._no_signal_payload()

    def boom(_payload):
        raise RuntimeError("network on fire")

    session.on_payload = boom
    await session._emit()  # must not propagate


@pytest.mark.anyio
async def test_switching_session_does_not_post_the_old_queue(client):
    """A switch must not flush the old queue via `stop()` -- those samples
    belong to a session the new token may not own."""
    await _started(client, "s1")
    client.enqueue("cognitive", {"ts": "old"})

    await client.start("s2", "tok2")

    assert client._fake.calls == [], "the previous session's queue was posted"
    assert client.status()["queued"]["cognitive"] == 0


@pytest.mark.anyio
async def test_a_stopped_client_does_not_carry_a_queue_into_the_next_session(client):
    """A client whose loop already ended must still clear its queue on the next
    start, or old samples go out under the new session id."""
    await _started(client, "s1")
    client.enqueue("cognitive", {"ts": "old"})
    # Loop ends without a stop(), as a cancelled task would leave it.
    client._task.cancel()
    try:
        await client._task
    except asyncio.CancelledError:
        pass
    assert not client.running

    await client.start("s2", "tok2")
    client.enqueue("cognitive", {"ts": "new"})
    await client._flush_once()

    sent = [s["ts"] for c in client._fake.calls for s in c["json"]["samples"]]
    assert sent == ["new"], f"old session's samples went out as s2: {sent}"


@pytest.mark.anyio
async def test_samples_produced_during_the_shutdown_window_are_counted(client):
    """A session open with no running loop still loses samples, and that loss
    must be counted like every other one on this path."""
    await _started(client)
    client._task.cancel()
    try:
        await client._task
    except asyncio.CancelledError:
        pass

    client.enqueue("cognitive", {"ts": "orphan"})

    assert client.status()["dropped_locally"]["cognitive"] == 1


@pytest.mark.anyio
async def test_no_session_is_not_a_drop():
    """With no session open, samples aren't being lost, just not sent -- must
    not count toward drops."""
    pc = PushClient("http://backend:8000")
    pc.enqueue("cognitive", {"ts": 0})

    assert pc.status()["dropped_locally"]["cognitive"] == 0


@pytest.mark.anyio
async def test_a_rejected_face_window_is_not_a_row(client):
    """`build_face_record` always returns a dict, so a rejected window arrives
    as `emotion: None, rejected_by: "no_face"`, not an absent block. Enqueuing
    must check for a reading, not just block presence, or it writes thousands
    of all-null rows an hour."""
    await _started(client)
    client.submit_payload({
        "kind": "camera", "timestamp": "t", "device_id": "cam0",
        "face": {"emotion": None, "emotion_confidence": None,
                 "trusted": False, "rejected_by": "no_face"},
    })

    assert client.status()["queued"]["face"] == 0


@pytest.mark.anyio
async def test_a_gaze_without_an_emotion_is_still_a_reading(client):
    """A reading is an emotion **or** a gaze. Gating on emotion alone would drop
    a window where FER+ refused but the landmarks succeeded -- exactly the
    faces the emotion classifier is least reliable on."""
    await _started(client)
    client.submit_payload({
        "kind": "camera", "timestamp": "t", "device_id": "cam0",
        "face": {"emotion": None, "rejected_by": "low_confidence",
                 "trusted": False, "attention": None,
                 "gaze_x": 0.42, "gaze_y": -0.03, "gaze_rejected_by": None},
    })

    assert client.status()["queued"]["face"] == 1


@pytest.mark.anyio
async def test_neither_measurement_is_still_not_a_row(client):
    """A window that refuses both emotion and gaze is not a reading of
    anything, and must not be enqueued."""
    await _started(client)
    client.submit_payload({
        "kind": "camera", "timestamp": "t", "device_id": "cam0",
        "face": {"emotion": None, "rejected_by": "no_face", "trusted": False,
                 "attention": None, "gaze_x": None, "gaze_y": None,
                 "gaze_rejected_by": "no_eye"},
    })

    assert client.status()["queued"]["face"] == 0


@pytest.mark.anyio
async def test_the_gaze_refusal_reaches_raw_separately_from_the_emotion_one(client):
    """Two measurements, two refusal fields -- one shared field couldn't say
    which failed."""
    await _started(client)
    client.submit_payload({
        "kind": "camera", "timestamp": "t", "device_id": "cam0",
        "face": {"emotion": "sad", "emotion_confidence": 0.7, "trusted": True,
                 "rejected_by": None, "attention": None,
                 "gaze_x": None, "gaze_y": None, "gaze_rejected_by": "no_eye"},
    })

    row = client._queues["face"][-1]
    assert row["raw"]["gaze_rejected_by"] == "no_eye"
    assert row["raw"]["rejected_by"] is None


@pytest.mark.anyio
async def test_an_untrusted_emotion_is_still_a_reading(client):
    """`emotion_trusted` is a column the backend's fusion logic gates on --
    dropping untrusted readings here would take that decision away from it."""
    await _started(client)
    client.submit_payload({
        "kind": "camera", "timestamp": "t",
        "face": {"emotion": "sad", "emotion_confidence": 0.3, "trusted": False},
    })

    assert client.status()["queued"]["face"] == 1
    assert client._queues["face"][0]["emotion_trusted"] is False


@pytest.mark.anyio
async def test_a_rejected_heart_window_is_not_a_row(client):
    """`build_heart_record` sets `source: "rppg"` unconditionally, even on
    rejects, so checking `source` alone isn't enough -- must check for an
    actual bpm reading."""
    await _started(client)
    client.submit_payload({
        "kind": "camera", "timestamp": "t",
        "heart": {"source": "rppg", "bpm": None, "confidence": 0.0,
                  "rejected_by": "warming_up"},
    })

    assert client.status()["queued"]["heart"] == 0


@pytest.mark.anyio
async def test_an_unreadable_receipt_does_not_re_post_a_committed_batch(client, monkeypatch):
    """Past `raise_for_status()` the rows are already written. Raising while
    reading the response body would restore and re-post the batch, and neither
    `cognitive_signals` nor `face_signals` has a dedupe key."""
    class _BadBody(_Response):
        def json(self):
            raise ValueError("truncated body")

    fake = _FakeClient(responder=lambda *_a, **_k: _BadBody(status_code=200))
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient",
                        lambda **_k: fake)
    await _started(client)
    client.enqueue("cognitive", {"ts": 0})

    await client._flush_once()  # must not raise

    assert client.status()["queued"]["cognitive"] == 0, "a committed batch was re-queued"
    # Neither recorded nor lost: the write happened but we couldn't confirm it.
    assert client.status()["recorded"]["cognitive"] == 0
    assert client.status()["unaccounted"]["cognitive"] == 1


@pytest.mark.anyio
async def test_counters_do_not_carry_into_the_next_session(client):
    """A fresh session must start at zero, not show readings recorded before
    it began."""
    await _started(client, "s1")
    client.enqueue("cognitive", {"ts": 0})
    await client._flush_once()
    assert client.status()["recorded"]["cognitive"] == 1

    await client.start("s2", "tok2")

    assert client.status()["recorded"]["cognitive"] == 0
    assert client.status()["dropped_locally"]["cognitive"] == 0


@pytest.mark.anyio
async def test_submit_payload_counts_the_shutdown_window_like_enqueue(client):
    """`submit_payload` must go through the same shutdown-window accounting as
    `enqueue`, not skip it with its own separate guard."""
    await _started(client)
    client._task.cancel()
    try:
        await client._task
    except asyncio.CancelledError:
        pass

    client.submit_payload({"timestamp": "t", "features": {"focus_score": 1.0}})

    assert client.status()["dropped_locally"]["cognitive"] == 1


@pytest.mark.anyio
async def test_an_empty_pass_does_not_clear_the_backoff(client, monkeypatch):
    """A lull in sampling during an outage must not reset the backoff or clear
    `last_error` -- nothing was actually delivered to confirm recovery."""
    fake = _FakeClient(responder=lambda *_a, **_k: _Response(status_code=500))
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient",
                        lambda **_k: fake)
    await _started(client)
    client.enqueue("cognitive", {"ts": 0})
    try:
        await client._flush_once()
    except Exception as exc:  # noqa: BLE001
        client._note_failure(exc)
    client._queues["cognitive"].clear()
    backoff = client.status()["backoff_seconds"]
    assert backoff > 0

    await client._flush_once()  # nothing queued

    assert client.status()["backoff_seconds"] == backoff, "an empty pass claimed recovery"
    assert client.status()["last_error"] is not None


@pytest.mark.anyio
async def test_concurrent_starts_do_not_leave_a_running_loop_without_a_token(client):
    """Two concurrent `start()` calls must not leave a running loop with its
    token cleared -- e.g. by the slower call's internal `stop()` wiping what
    the other just installed."""
    await _started(client, "s0")

    await asyncio.gather(client.start("s1", "tok1"), client.start("s1", "tok1"))

    assert client.running
    assert client._token == "tok1"
    assert client._session_id == "s1"
    client.enqueue("cognitive", {"ts": 0})
    assert client.status()["queued"]["cognitive"] == 1


@pytest.mark.anyio
async def test_a_successful_pass_still_clears_the_backoff(client, monkeypatch):
    """A real successful flush must still clear the backoff."""
    failing = _FakeClient(responder=lambda *_a, **_k: _Response(status_code=500))
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient",
                        lambda **_k: failing)
    await _started(client)
    client.enqueue("cognitive", {"ts": 0})
    try:
        await client._flush_once()
    except Exception as exc:  # noqa: BLE001
        client._note_failure(exc)
    assert client.status()["backoff_seconds"] > 0

    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient",
                        lambda **_k: _FakeClient())
    await client._flush_once()

    assert client.status()["backoff_seconds"] == 0
    assert client.status()["last_error"] is None


@pytest.mark.anyio
async def test_a_held_heart_block_is_enqueued_once(client):
    """The heart block is a 25s window recomputed every 10s and held on the
    payload in between, so one measurement arrives on ~40 consecutive ticks.
    Must be keyed on the reading's own timestamp, not the tick's, or each
    arrival becomes a distinct row.
    """
    await _started(client)
    block = {"source": "muse_optics", "bpm": 68.2, "confidence": 0.8,
             "ts": "2026-08-10T10:00:00+00:00"}
    for i in range(5):
        client.submit_payload({
            "timestamp": f"2026-08-10T10:00:0{i}Z", "device_id": "station1",
            "features": {}, "heart": block,
        })

    assert client.status()["queued"]["heart"] == 1
    # The reading's own timestamp, not the tick it arrived on.
    assert client._queues["heart"][0]["ts"] == "2026-08-10T10:00:00+00:00"


@pytest.mark.anyio
async def test_rmssd_gating_fields_are_carried_into_the_enqueued_sample(client):
    """`beat_coverage` and `rmssd_rejected_by` are RMSSD's own gates, kept
    apart from `rejected_by` -- a row can carry a good bpm with no RMSSD, and
    these fields say why. The enqueue path must carry both."""
    await _started(client)
    client.submit_payload({
        "timestamp": "2026-08-10T10:00:00Z", "device_id": "station1",
        "features": {},
        "heart": {"source": "muse_optics", "bpm": 68.2, "rmssd_ms": None,
                  "beat_coverage": 0.91, "rmssd_rejected_by": "coverage",
                  "ts": "2026-08-10T10:00:00+00:00"},
    })

    sample = client._queues["heart"][0]
    assert sample["beat_coverage"] == 0.91
    assert sample["rmssd_rejected_by"] == "coverage"


@pytest.mark.anyio
async def test_a_new_heart_reading_is_enqueued_again(client):
    await _started(client)
    for stamp in ("2026-08-10T10:00:00+00:00", "2026-08-10T10:00:10+00:00"):
        client.submit_payload({
            "timestamp": "2026-08-10T10:00:00Z", "device_id": "station1",
            "features": {},
            "heart": {"source": "muse_optics", "bpm": 68.2, "ts": stamp},
        })

    assert client.status()["queued"]["heart"] == 2


@pytest.mark.anyio
async def test_two_devices_do_not_suppress_each_others_readings(client):
    """A headband and a camera can feed one client at once; a single
    last-stamp slot would let one device's reading hide the other's."""
    await _started(client)
    for device, source in (("station1", "muse_optics"), ("cam0", "rppg")):
        client.submit_payload({
            "timestamp": "2026-08-10T10:00:00Z", "device_id": device,
            "features": {},
            "heart": {"source": source, "bpm": 70.0,
                      "ts": "2026-08-10T10:00:00+00:00"},
        })

    assert client.status()["queued"]["heart"] == 2
