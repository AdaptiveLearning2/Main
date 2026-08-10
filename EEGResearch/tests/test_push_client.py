"""The sidecar half of the ingestion split.

The failure this guards is the same one `INGEST_MODE` guards on the backend: a
signal path that produces nothing, raises nothing, and leaves a session looking
live. Here that would be a queue quietly discarding samples, a token outliving
the session it was granted for, or a client counting what it sent rather than
what was stored.
"""

import asyncio

import pytest

from src.app.services.push_client import MAX_BATCH, MAX_QUEUE, PushClient


@pytest.fixture
def anyio_backend():
    """asyncio only. anyio's plugin is already a dependency (FastAPI pulls it
    in), so async tests need no pytest-asyncio; without this fixture it would
    also run every test under trio, which is not installed."""
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
    """A sidecar streaming for its local dashboard with no session open must not
    be holding a student's samples, let alone sending them."""
    pc = PushClient("http://backend:8000")
    pc.enqueue("cognitive", {"ts": "t"})
    assert pc.status()["queued"]["cognitive"] == 0


@pytest.mark.anyio
async def test_the_token_does_not_outlive_the_session(client):
    """The whole argument for holding a student's bearer token in a process on
    their laptop is that it lives in memory for one session."""
    await _started(client)
    assert client._token == "tok"

    await client.stop()
    assert client._token is None
    assert client._session_id is None


@pytest.mark.anyio
async def test_switching_session_drops_the_previous_queue(client):
    """Those samples belong to a session this token may no longer own. Posting
    them would either be rejected, or -- for the same student -- attribute one
    session's readings to another."""
    await _started(client, "s1")
    client.enqueue("cognitive", {"ts": "t"})
    assert client.status()["queued"]["cognitive"] == 1

    await client.start("s2", "tok2")
    assert client.status()["queued"]["cognitive"] == 0
    assert client.status()["session_id"] == "s2"


@pytest.mark.anyio
async def test_a_full_queue_drops_oldest_and_counts_it(client):
    """`deque(maxlen=...)` evicts silently. Unrecorded, that is a signal path
    losing data with nothing anywhere to say so."""
    await _started(client)
    for i in range(MAX_QUEUE + 5):
        client.enqueue("face", {"ts": i})

    status = client.status()
    assert status["queued"]["face"] == MAX_QUEUE
    assert status["dropped_locally"]["face"] == 5
    # Oldest gone, newest kept: for a live session the recent samples are the
    # ones worth having.
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
    """A transient failure must not reorder a session's samples relative to what
    comes after them."""
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
    """The backend's rate limit. Counting it as delivered would drop the samples
    and report success."""
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
    """It drops samples for a sensor the student declined and says how many. A
    client counting what it *sent* would report a healthy session that recorded
    nothing -- the write-side version of a dashboard that cannot tell "no data"
    from "zero"."""
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
    """A backend that is down must not be retried at the flush rate for a whole
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


# ── payload shaping ──────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_eeg_features_go_up_unconverted(client):
    """No arithmetic on this side. The 0..100 -> 0..1 conversion lives once, in
    the backend's `signal_mapping`, reached identically by the poller and by
    this path -- a second copy here is how one path stores percentages while the
    other stores ratios."""
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
    # A camera has no electrodes; a cognitive row from one would be averaged
    # into an EEG session's numbers.
    assert queued["cognitive"] == 0
    assert client._queues["face"][0]["emotion_trusted"] is True
    assert client._queues["heart"][0]["heart_rate_bpm"] == 71.0


@pytest.mark.anyio
async def test_a_switched_off_channel_produces_no_row(client):
    """`build_camera_payload` omits a disabled channel rather than nulling it. A
    student who refused heart recording is not a camera that failed to get a
    reading, and neither is a row."""
    await _started(client)
    client.submit_payload({
        "kind": "camera", "timestamp": "t", "device_id": "cam0",
        "face": {"emotion": "neutral", "trusted": False},
    })

    assert client.status()["queued"]["heart"] == 0


@pytest.mark.anyio
async def test_a_heart_reading_without_a_source_is_dropped_locally(client):
    """Consent is per sensor, so a reading that cannot name its sensor cannot be
    consent-checked. Dropped here rather than rejected there, so the failure is
    local and counted."""
    await _started(client)
    client.submit_payload({
        "kind": "camera", "timestamp": "t",
        "heart": {"bpm": 70.0},
    })

    assert client.status()["queued"]["heart"] == 0


@pytest.mark.anyio
async def test_one_channels_failure_does_not_cost_the_others(client, monkeypatch):
    """The batches were drained for all three channels up front, so re-raising
    on the first failure discarded the other two outright -- popped, never put
    back, and counted nowhere. Silent loss in the error path of the module
    written to prevent silent loss."""
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
    """`extendleft` on a maxlen deque silently evicts from the far end -- the
    *newest* samples, which is the reverse of the drop-oldest rule and
    uncounted, which is worse than either."""
    fake = _FakeClient(responder=lambda *_a, **_k: _Response(status_code=500))
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient",
                        lambda **_k: fake)
    await _started(client)
    for i in range(MAX_BATCH):
        client.enqueue("cognitive", {"ts": i})

    # The queue fills while that batch is notionally in flight.
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
    every few samples. Flushing on it regardless made the backoff decorative and
    retried a dead backend at the sample rate."""
    await _started(client)
    client._note_failure(RuntimeError("backend down"))
    assert client._retry_at > 0

    # A full batch sets the wake event; the deadline must still hold.
    for i in range(MAX_BATCH):
        client.enqueue("cognitive", {"ts": i})
    assert client._wake.is_set()
    assert client._retry_at > 0, "the deadline was cleared by a wake"


@pytest.mark.anyio
async def test_shutdown_flushes_the_whole_backlog(client):
    """`_flush_once` takes at most MAX_BATCH per channel, so one call at
    shutdown delivered 50 samples and dropped the rest of what had backed up."""
    await _started(client)
    for i in range(MAX_BATCH * 3):
        client.enqueue("cognitive", {"ts": i})

    await client.stop()

    sent = sum(len(c["json"]["samples"]) for c in client._fake.calls)
    assert sent == MAX_BATCH * 3, f"only {sent} of {MAX_BATCH * 3} were delivered"


@pytest.mark.anyio
async def test_the_emitted_payload_carries_what_the_pull_path_stores():
    """`bands` and `ingestion` are assembled in `snapshot()` and were never in
    `latest_payload`, so emitting the raw payload gave push-ingested EEG rows
    null alpha/beta/theta/delta/gamma while pull-ingested ones -- which read
    /api/v1/state, i.e. snapshot() -- had them. One deployment silently
    recording less than the other is the divergence the shared mapping exists to
    rule out, one layer upstream of it."""
    from src.app.config import DeviceConfig, get_settings
    from src.app.services.stream_manager import DeviceSession

    session = DeviceSession("d1", get_settings(),
                            DeviceConfig(device_id="d1", kind="sim",
                                         host="127.0.0.1", port=8765))
    session.latest_payload = session._no_signal_payload()

    seen = []
    session.on_payload = seen.append
    session._emit()

    assert "bands" in seen[0], "band powers never reached the push path"
    assert set(seen[0]["bands"]) == {"delta", "theta", "alpha", "beta", "gamma"}


@pytest.mark.anyio
async def test_a_raising_consumer_does_not_kill_the_sampling_loop():
    """Losing the local stream to fix up a remote write is strictly worse than
    losing the remote write."""
    from src.app.config import DeviceConfig, get_settings
    from src.app.services.stream_manager import DeviceSession

    session = DeviceSession("d1", get_settings(),
                            DeviceConfig(device_id="d1", kind="sim",
                                         host="127.0.0.1", port=8765))
    session.latest_payload = session._no_signal_payload()

    def boom(_payload):
        raise RuntimeError("network on fire")

    session.on_payload = boom
    session._emit()  # must not raise


@pytest.mark.anyio
async def test_switching_session_does_not_post_the_old_queue(client):
    """The docstring said "drops" and the code called `stop()`, which flushes --
    so those samples were posted after all. They belong to a session the new
    token may not own."""
    await _started(client, "s1")
    client.enqueue("cognitive", {"ts": "old"})

    await client.start("s2", "tok2")

    assert client._fake.calls == [], "the previous session's queue was posted"
    assert client.status()["queued"]["cognitive"] == 0


@pytest.mark.anyio
async def test_a_stopped_client_does_not_carry_a_queue_into_the_next_session(client):
    """The guard was `if self.running`, so a client whose loop had already ended
    skipped the stop entirely -- and the old queue went out under the *new*
    session id, filing one session's readings under another."""
    await _started(client, "s1")
    client.enqueue("cognitive", {"ts": "old"})
    # The loop ends without a stop(), as a cancelled task would leave it.
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
    """A session is open but the loop has ended. Those samples will never be
    sent, which is a loss, and every other loss on this path is counted."""
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
    """A sidecar streaming for its local dashboard has nowhere to send samples
    and is not losing them. Counting that would make the number meaningless."""
    pc = PushClient("http://backend:8000")
    pc.enqueue("cognitive", {"ts": 0})

    assert pc.status()["dropped_locally"]["cognitive"] == 0
