"""The sidecar push client: no silent drops, no token outliving its session, counts from receipts."""

import asyncio
import logging
import time
import types

import pytest

from src.app.services.push_client import (MAX_BATCH, MAX_QUEUE, MIN_BATCH, PERMIT_CHECK_SECONDS,
                                          RESULT_FRESH_SECONDS, SHUTDOWN_BUDGET, PushClient)


@pytest.fixture
def anyio_backend():
    """asyncio only; trio isn't installed."""
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
    """httpx.AsyncClient double recording what was posted."""

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
    pc = PushClient("http://backend:8000")
    pc.enqueue("cognitive", {"ts": "t"})
    assert pc.status()["queued"]["cognitive"] == 0


@pytest.mark.anyio
async def test_the_token_does_not_outlive_the_session(client):
    await _started(client)
    assert client._token == "tok"

    await client.stop()
    assert client._token is None
    assert client._session_id is None


@pytest.mark.anyio
async def test_switching_session_drops_the_previous_queue(client):
    """Posting old samples could attribute one session's readings to another."""
    await _started(client, "s1")
    client.enqueue("cognitive", {"ts": "t"})
    assert client.status()["queued"]["cognitive"] == 1

    await client.start("s2", "tok2")
    assert client.status()["queued"]["cognitive"] == 0
    assert client.status()["session_id"] == "s2"


@pytest.mark.anyio
async def test_a_full_queue_drops_oldest_and_counts_it(client):
    """`deque(maxlen=...)` evicts silently, so the drop must be counted."""
    await _started(client)
    for i in range(MAX_QUEUE + 5):
        client.enqueue("face", {"ts": i})

    status = client.status()
    assert status["queued"]["face"] == MAX_QUEUE
    assert status["dropped_locally"]["face"] == 5
    # Oldest gone, newest kept.
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
    """Counting what was sent would make a session that recorded nothing look healthy."""
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
async def test_a_replayed_batch_is_not_read_as_a_silent_sensor(client, monkeypatch):
    """A retried batch the backend already had gets its own bucket, apart from a refused sensor."""
    fake = _FakeClient(responder=lambda *_a, **_k: _Response(
        body={"ok": True, "inserted": 0, "dropped": 0, "duplicates": 2}))
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient",
                        lambda **_k: fake)
    await _started(client)
    client.enqueue("face", {"ts": 0})
    client.enqueue("face", {"ts": 1})

    await client._flush_once()

    status = client.status()
    assert status["duplicates"]["face"] == 2
    assert status["recorded"]["face"] == 0, "not counted twice across a retry"
    # Not local overflow, not an unreadable receipt.
    assert status["dropped_locally"]["face"] == 0
    assert status["unaccounted"]["face"] == 0


@pytest.mark.anyio
async def test_a_backend_that_reports_no_duplicates_still_reads_cleanly(client,
                                                                        monkeypatch):
    """An absent `duplicates` reads as 0, not as an unreadable receipt."""
    fake = _FakeClient(responder=lambda *_a, **_k: _Response(
        body={"ok": True, "inserted": 2}))
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient",
                        lambda **_k: fake)
    await _started(client)
    client.enqueue("face", {"ts": 0})
    client.enqueue("face", {"ts": 1})

    await client._flush_once()

    status = client.status()
    assert status["recorded"]["face"] == 2
    assert status["duplicates"]["face"] == 0
    assert status["unaccounted"]["face"] == 0


def _receipts(client, monkeypatch, *bodies):
    """Answer each post with the next receipt body in turn."""
    queue = list(bodies)
    fake = _FakeClient(responder=lambda *_a, **_k: _Response(body=queue.pop(0)))
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient", lambda **_k: fake)


async def _post_one(client, channel, sample):
    client.enqueue(channel, sample)
    await client._flush_once()


@pytest.mark.anyio
async def test_a_declined_batch_is_counted_with_its_reason(client, monkeypatch):
    """The backend accepts the post and records nothing; the page must be able to say so, and why."""
    _receipts(client, monkeypatch,
              {"ok": True, "inserted": 0, "dropped": 2, "reason": "eeg not consented"})
    await _started(client)
    client.enqueue("cognitive", {"ts": "a"})
    await _post_one(client, "cognitive", {"ts": "b"})

    status = client.status()
    assert status["declined"]["cognitive"] == 2
    assert status["declined_reason"]["cognitive"] == "eeg not consented"
    assert status["last_result"]["cognitive"] == "declined"
    assert status["recorded"]["cognitive"] == 0
    assert status["last_result"]["face"] is None, "a channel with no receipt has no result"


@pytest.mark.anyio
async def test_a_channel_recorded_again_reads_as_recorded(client, monkeypatch):
    """Consent given mid-lesson: the latest receipt decides, and the earlier decline stays counted."""
    _receipts(client, monkeypatch,
              {"ok": True, "inserted": 0, "dropped": 1, "reason": "camera not consented"},
              {"ok": True, "inserted": 1, "dropped": 0, "reason": None})
    await _started(client)
    await _post_one(client, "face", {"ts": 0})
    await _post_one(client, "face", {"ts": 1})

    status = client.status()
    assert status["last_result"]["face"] == "recorded"
    assert status["declined"]["face"] == 1
    assert status["declined_reason"]["face"] == "camera not consented"


@pytest.mark.anyio
async def test_a_partly_declined_batch_reads_as_recorded(client, monkeypatch):
    """A heart batch with one sensor declined and the other recorded is still being saved."""
    _receipts(client, monkeypatch, {"ok": True, "inserted": 1, "dropped": 1, "reason": None})
    await _started(client)
    client.enqueue("heart", {"ts": "a", "source": "muse_optics", "device_id": "d"})
    await _post_one(client, "heart", {"ts": "b", "source": "rppg", "device_id": "c"})

    status = client.status()
    assert status["last_result"]["heart"] == "recorded"
    assert status["declined"]["heart"] == 1


@pytest.mark.anyio
async def test_a_receipt_of_only_duplicates_leaves_the_last_result(client, monkeypatch):
    """Rows an earlier attempt saved say nothing about whether this channel records now."""
    _receipts(client, monkeypatch,
              {"ok": True, "inserted": 1},
              {"ok": True, "inserted": 0, "dropped": 0, "duplicates": 1})
    await _started(client)
    await _post_one(client, "cognitive", {"ts": "a"})
    await _post_one(client, "cognitive", {"ts": "b"})

    assert client.status()["last_result"]["cognitive"] == "recorded"


@pytest.mark.anyio
async def test_a_channel_that_stopped_sending_has_no_last_result(client, monkeypatch):
    """A camera switched off mid-lesson must stop counting as being saved."""
    _receipts(client, monkeypatch, {"ok": True, "inserted": 1})
    await _started(client)
    await _post_one(client, "face", {"ts": 0})
    assert client.status()["last_result"]["face"] == "recorded"

    client._last_result_at["face"] -= RESULT_FRESH_SECONDS + 1

    assert client.status()["last_result"]["face"] is None


@pytest.mark.anyio
async def test_a_new_session_forgets_the_last_sessions_declines(client, monkeypatch):
    _receipts(client, monkeypatch,
              {"ok": True, "inserted": 0, "dropped": 1, "reason": "eeg not consented"})
    await _started(client, "s1")
    await _post_one(client, "cognitive", {"ts": "a"})

    await client.start("s2", "tok2")
    status = client.status()
    assert status["declined"]["cognitive"] == 0
    assert status["declined_reason"]["cognitive"] is None
    assert status["last_result"]["cognitive"] is None


@pytest.mark.anyio
async def test_repeated_failures_back_off_and_recover(client, monkeypatch):
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
    """The 0..100 -> 0..1 conversion lives once, in the backend's `signal_mapping`."""
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
    assert queued["cognitive"] == 0
    assert client._queues["face"][0]["emotion_trusted"] is True
    assert client._queues["heart"][0]["heart_rate_bpm"] == 71.0


@pytest.mark.anyio
async def test_a_switched_off_channel_produces_no_row(client):
    """A disabled channel is omitted, not nulled: declined consent is not a failed reading."""
    await _started(client)
    client.submit_payload({
        "kind": "camera", "timestamp": "t", "device_id": "cam0",
        "face": {"emotion": "neutral", "trusted": False},
    })

    assert client.status()["queued"]["heart"] == 0


@pytest.mark.anyio
async def test_a_heart_reading_without_a_source_is_dropped_locally(client):
    """Consent is per sensor, so an unnamed sensor can't be consent-checked."""
    await _started(client)
    client.submit_payload({
        "kind": "camera", "timestamp": "t",
        "heart": {"bpm": 70.0},
    })

    assert client.status()["queued"]["heart"] == 0


@pytest.mark.anyio
async def test_one_channels_failure_does_not_cost_the_others(client, monkeypatch):
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


# A 400, or a 422 not about length, is final at any size; a 413 once halving would pass MIN_BATCH.
@pytest.mark.parametrize("status, n", [(400, 2 * MIN_BATCH), (422, 2 * MIN_BATCH),
                                       (413, 2 * MIN_BATCH - 1)])
@pytest.mark.anyio
async def test_a_batch_refused_whole_is_dropped_and_counted_not_retried(client, monkeypatch, status, n):
    """Restored, it would head its queue and fail every flush for the rest of the lesson, and
    its backoff (to 120 s) would throttle the channels that were delivering."""
    def responder(url, json, headers):
        return _Response(status_code=status) if url.endswith("/face") else _Response(
            body={"ok": True, "inserted": len(json["samples"])})

    fake = _FakeClient(responder=responder)
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient",
                        lambda **_k: fake)
    await _started(client)
    for ts in range(n):
        client.enqueue("face", {"ts": ts})
    client.enqueue("cognitive", {"ts": 0})

    await client._flush_once()        # does not raise: a refused batch is not a failed flush

    status_now = client.status()
    assert status_now["queued"]["face"] == 0
    assert status_now["rejected"]["face"] == n
    assert status_now["recorded"]["cognitive"] == 1
    assert status_now["backoff_seconds"] == 0.0 and client._retry_at == 0.0

    # The next flush does not resend it.
    before = len(fake.calls)
    await client._flush_once()
    assert len(fake.calls) == before


# FastAPI's answer to a list over `max_length`, as the backend's ingest models give it.
_TOO_LONG = {"detail": [{"type": "too_long", "loc": ["body", "samples"],
                         "msg": "List should have at most 12 items after validation"}]}


@pytest.mark.parametrize("status, body", [(413, None), (422, _TOO_LONG)])
@pytest.mark.anyio
async def test_a_size_refusal_halves_the_batch_until_the_backend_takes_it(client, monkeypatch, status, body):
    """A backend capping batches below ours would otherwise refuse every batch of the session."""
    cap = 12
    def responder(url, json, headers):
        n = len(json["samples"])
        return _Response(status_code=status, body=body) if n > cap else _Response(
            body={"ok": True, "inserted": n})

    fake = _FakeClient(responder=responder)
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient",
                        lambda **_k: fake)
    await _started(client)
    for ts in range(MAX_BATCH):
        client.enqueue("face", {"ts": ts})

    for _ in range(8):
        await client._flush_once()

    status_now = client.status()
    assert status_now["recorded"]["face"] == MAX_BATCH
    assert status_now["rejected"]["face"] == 0 and status_now["queued"]["face"] == 0
    assert status_now["batch_limit"]["face"] == MAX_BATCH // 4   # 50 -> 25 -> 12
    assert [c["json"]["samples"][0]["ts"] for c in fake.calls][:3] == [0, 0, 0], \
        "a halved batch is resent from its first sample"
    assert status_now["backoff_seconds"] == 0.0


@pytest.mark.anyio
async def test_a_new_session_starts_at_the_full_batch_size(client, monkeypatch):
    fake = _FakeClient(responder=lambda url, json, headers: _Response(status_code=413))
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient",
                        lambda **_k: fake)
    await _started(client)
    for ts in range(MAX_BATCH):
        client.enqueue("face", {"ts": ts})
    await client._flush_once()
    assert client.status()["batch_limit"]["face"] == MAX_BATCH // 2

    await _started(client, session_id="s2")
    assert client.status()["batch_limit"]["face"] == MAX_BATCH


# A field the versions disagree on; the list too long beside another error, or inside a sample.
@pytest.mark.parametrize("body", [
    {"detail": [{"type": "missing", "loc": ["body", "samples", 0, "ts"]}]},
    {"detail": [_TOO_LONG["detail"][0], {"type": "uuid_parsing", "loc": ["body", "session_id"]}]},
    {"detail": [{"type": "too_long", "loc": ["body", "samples", 0, "last_optics"]}]},
    {"detail": "Unprocessable"},
])
@pytest.mark.anyio
async def test_a_refusal_not_about_size_never_shrinks_the_batch(client, monkeypatch, body):
    """Shrunk to one, each reading would be its own refused request and trip the rate limit."""
    fake = _FakeClient(responder=lambda url, json, headers: _Response(status_code=422, body=body))
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient",
                        lambda **_k: fake)
    await _started(client)
    for ts in range(2 * MAX_BATCH):
        client.enqueue("face", {"ts": ts})
    for _ in range(2):
        await client._flush_once()

    assert client.status()["batch_limit"]["face"] == MAX_BATCH
    assert [len(c["json"]["samples"]) for c in fake.calls] == [MAX_BATCH, MAX_BATCH]
    assert client.status()["rejected"]["face"] == 2 * MAX_BATCH


@pytest.mark.anyio
async def test_a_size_refusal_at_every_size_stops_halving_at_the_floor(client, monkeypatch):
    fake = _FakeClient(responder=lambda url, json, headers: _Response(status_code=413))
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient",
                        lambda **_k: fake)
    await _started(client)
    for ts in range(MAX_BATCH):
        client.enqueue("face", {"ts": ts})
    for _ in range(20):
        await client._flush_once()

    # 50 -> 25 -> 12 -> 6; 6 would halve below the floor, so each batch of 6 is dropped.
    assert client.status()["batch_limit"]["face"] == 6
    assert len(fake.calls) == 3 + -(-MAX_BATCH // 6)
    assert client.status()["queued"]["face"] == 0
    assert client.status()["rejected"]["face"] == MAX_BATCH


@pytest.mark.anyio
async def test_a_server_error_is_still_restored_and_retried(client, monkeypatch):
    """Only a refusal of the batch itself drops it; a 5xx says nothing about the samples."""
    fake = _FakeClient(responder=lambda *_a, **_k: _Response(status_code=503))
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient",
                        lambda **_k: fake)
    await _started(client)
    client.enqueue("face", {"ts": 0})
    with pytest.raises(Exception):
        await client._flush_once()
    assert client.status()["queued"]["face"] == 1
    assert client.status()["rejected"]["face"] == 0


@pytest.mark.anyio
async def test_restoring_into_a_full_queue_counts_what_it_evicts(client, monkeypatch):
    """`extendleft` on a maxlen deque evicts the newest samples; still counted."""
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
    """The wake event fires on every full batch; flushing on it would retry a dead backend at sample rate."""
    await _started(client)
    client._note_failure(RuntimeError("backend down"))
    assert client._retry_at > 0

    for i in range(MAX_BATCH):
        client.enqueue("cognitive", {"ts": i})
    assert client._wake.is_set()
    assert client._retry_at > 0, "the deadline was cleared by a wake"


@pytest.mark.anyio
async def test_shutdown_flushes_the_whole_backlog(client):
    """`_flush_once` takes at most MAX_BATCH per channel."""
    await _started(client)
    for i in range(MAX_BATCH * 3):
        client.enqueue("cognitive", {"ts": i})

    await client.stop()

    sent = sum(len(c["json"]["samples"]) for c in client._fake.calls)
    assert sent == MAX_BATCH * 3, f"only {sent} of {MAX_BATCH * 3} were delivered"


class _Unanswered(_FakeClient):
    """Accepts every post and never answers; `posting` is set once one is in flight."""

    def __init__(self):
        super().__init__()
        self.posting = asyncio.Event()

    async def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        self.posting.set()
        await asyncio.Event().wait()


async def _loop_mid_post(monkeypatch) -> PushClient:
    """A started client whose loop is inside a POST the backend never answers."""
    fake = _Unanswered()
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient", lambda **_k: fake)
    pc = await _started(PushClient("http://backend:8000"), "s1", "tok1")
    pc.enqueue("cognitive", {"ts": 1})
    pc._wake.set()
    await asyncio.wait_for(fake.posting.wait(), timeout=5)
    return pc


@pytest.mark.anyio
@pytest.mark.parametrize("call", ["start", "stop"])
async def test_a_request_cancelled_inside_the_internal_stop_ends_cancelled(monkeypatch, call):
    """uvicorn cancels requests still running when its drain ends; one swallowing that went on to start or flush."""
    pc = await _loop_mid_post(monkeypatch)
    request = asyncio.create_task(pc.start("s2", "tok2") if call == "start" else pc.stop())
    await asyncio.wait_for(pc._stopping.wait(), timeout=5)  # now waiting for the loop's POST to finish

    request.cancel()
    await asyncio.wait({request}, timeout=5)

    assert request.cancelled(), f"the cancelled {call} ran to completion"
    assert not pc.running and not pc._lifecycle.locked()
    # A start discards the old session anyway; a stop's session waits for the shutdown's own stop.
    assert (pc._session_id, pc._token) == ((None, None) if call == "start" else ("s1", "tok1"))


@pytest.mark.anyio
async def test_a_stop_cancelled_mid_flush_leaves_its_samples_for_the_shutdowns_stop(monkeypatch, caplog):
    """uvicorn's drain cancels a /push/stop still sending; the app's shutdown then stops again."""
    hung = _Unanswered()
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient", lambda **_k: hung)
    pc = await _started(PushClient("http://backend:8000"))
    pc.enqueue("cognitive", {"ts": 1})
    pc.enqueue("face", {"ts": 1})
    request = asyncio.create_task(pc.stop())
    await asyncio.wait_for(hung.posting.wait(), timeout=5)

    with caplog.at_level(logging.WARNING, logger="src.app.services.push_client"):
        request.cancel()
        await asyncio.wait({request}, timeout=5)

    assert request.cancelled()
    assert "stop cancelled; 1 sample(s) kept for the next stop" in caplog.text
    answered = _FakeClient()
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient", lambda **_k: answered)
    assert await asyncio.wait_for(pc.stop(), timeout=5) is True
    assert [(c["url"].rsplit("/", 1)[1], c["headers"]["Authorization"]) for c in answered.calls] == [
        ("face", "Bearer tok")]
    assert pc._token is None and pc._session_id is None


@pytest.mark.parametrize("stopped", [True, False])
def test_push_stop_reports_a_stop_that_did_not_happen(monkeypatch, stopped):
    """`stop()` gives up when another start or stop holds the lock past its budget; "stopped" would be false."""
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from src.app import main as sidecar  # noqa: PLC0415
    from src.app.config import get_settings  # noqa: PLC0415

    class _Client:
        session_id = "s1"

        async def stop(self):
            return stopped

    ended = []
    monkeypatch.setattr(sidecar, "push_client", _Client())
    monkeypatch.setattr(sidecar.stream_manager, "end_session", lambda: ended.append(True))
    r = TestClient(sidecar.app).post("/api/v1/push/stop",
                                     headers={"Authorization": f"Bearer {get_settings().api_token}"})

    if stopped:
        assert (r.status_code, r.json(), ended) == (200, {"status": "stopped", "ended_session": True}, [True])
    else:
        assert (r.status_code, r.json(), ended) == (503, {"status": "not_stopped", "ended_session": False}, [])
        assert r.headers["retry-after"] == "1"


@pytest.mark.anyio
async def test_a_stop_that_cancels_a_hung_loop_itself_returns(monkeypatch, caplog):
    """That cancellation is the stop's own, so unlike a caller's it is not raised."""
    monkeypatch.setattr("src.app.services.push_client.SHUTDOWN_BUDGET", 0.5)
    pc = await _loop_mid_post(monkeypatch)

    with caplog.at_level(logging.WARNING, logger="src.app.services.push_client"):
        await asyncio.wait_for(pc.stop(), timeout=5)

    assert "cognitive batch cancelled in flight; 1 sample(s) unaccounted" in caplog.text
    assert not pc.running and pc._token is None


@pytest.mark.anyio
async def test_waiting_for_the_lock_is_inside_the_shutdown_budget(client, monkeypatch, caplog):
    """Else a start or stop holding the lock makes the bound two budgets, or none."""
    monkeypatch.setattr("src.app.services.push_client.SHUTDOWN_BUDGET", 0.5)
    await _started(client)
    await client._lifecycle.acquire()  # held throughout, as by a request mid-start
    try:
        with caplog.at_level(logging.WARNING, logger="src.app.services.push_client"):
            stopped = await asyncio.wait_for(client.stop(), timeout=5)
    finally:
        client._lifecycle.release()

    assert stopped is False
    assert "shutdown budget spent waiting for another start or stop; not stopped" in caplog.text
    assert client._token == "tok", "a stop that never held the lock changed the session"
    assert await asyncio.wait_for(client.stop(), timeout=5) is True
    assert client._token is None


@pytest.mark.anyio
async def test_a_stop_that_waited_out_the_budget_for_the_lock_flushes_nothing(client, monkeypatch, caplog):
    """The deadline is taken before the lock, not after it, so the wait cannot start a second budget."""
    real, skew = time.monotonic, [0.0]
    monkeypatch.setattr("src.app.services.push_client.time", types.SimpleNamespace(monotonic=lambda: real() + skew[0]))
    await _started(client)
    client.enqueue("cognitive", {"ts": 1})
    await client._lifecycle.acquire()
    stopping = asyncio.create_task(client.stop())
    await asyncio.sleep(0)  # one step: stop() has its deadline and is queued on the lock

    skew[0] = SHUTDOWN_BUDGET  # as if the holder kept the lock for the whole budget
    client._lifecycle.release()
    with caplog.at_level(logging.WARNING, logger="src.app.services.push_client"):
        await asyncio.wait_for(stopping, timeout=5)

    assert client._fake.calls == []
    assert "shutdown budget spent, 1 sample(s) not sent" in caplog.text
    assert client._token is None


@pytest.mark.anyio
async def test_a_timeout_the_final_flush_raises_is_a_failed_flush_not_the_budget(monkeypatch, caplog):
    def _times_out(*_a, **_k):
        raise TimeoutError("connect timed out")

    fake = _FakeClient(responder=_times_out)
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient", lambda **_k: fake)
    pc = await _started(PushClient("http://backend:8000"))
    pc.enqueue("cognitive", {"ts": 1})

    with caplog.at_level(logging.WARNING, logger="src.app.services.push_client"):
        await asyncio.wait_for(pc.stop(), timeout=5)

    assert "final flush failed, 1 sample(s) lost: connect timed out" in caplog.text
    assert "budget spent" not in caplog.text


@pytest.mark.anyio
async def test_a_final_flush_the_backend_never_answers_ends_at_the_budget(monkeypatch, caplog):
    """The kit gives the sidecar's shutdown a fixed time, so `stop()` has to end inside its budget."""
    fake = _Unanswered()
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient", lambda **_k: fake)
    monkeypatch.setattr("src.app.services.push_client.SHUTDOWN_BUDGET", 0.5)
    pc = await _started(PushClient("http://backend:8000"))
    pc.enqueue("cognitive", {"ts": 1})
    pc.enqueue("face", {"ts": 1})

    with caplog.at_level(logging.WARNING, logger="src.app.services.push_client"):
        await asyncio.wait_for(pc.stop(), timeout=5)

    assert [c["url"].rsplit("/", 1)[1] for c in fake.calls] == ["cognitive"]
    assert "cognitive batch cancelled in flight; 1 sample(s) unaccounted" in caplog.text
    assert "shutdown budget spent mid-flush, 1 sample(s) not sent" in caplog.text  # the face sample, never taken
    assert "final flush failed" not in caplog.text
    assert pc._token is None and pc._session_id is None


@pytest.mark.anyio
async def test_the_emitted_payload_carries_what_the_pull_path_stores():
    """`bands` and `ingestion` are added by `snapshot()`, which pull reads via `/api/v1/state`."""
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
    """Losing the local loop is worse than losing the remote write."""
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
    """The old samples belong to a session the new token may not own."""
    await _started(client, "s1")
    client.enqueue("cognitive", {"ts": "old"})

    await client.start("s2", "tok2")

    assert client._fake.calls == [], "the previous session's queue was posted"
    assert client.status()["queued"]["cognitive"] == 0


@pytest.mark.anyio
async def test_a_stopped_client_does_not_carry_a_queue_into_the_next_session(client):
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
    """With no session open, samples aren't lost, just not sent."""
    pc = PushClient("http://backend:8000")
    pc.enqueue("cognitive", {"ts": 0})

    assert pc.status()["dropped_locally"]["cognitive"] == 0


@pytest.mark.anyio
async def test_a_rejected_face_window_is_not_a_row(client):
    """`build_face_record` always returns a dict; enqueue checks for a reading, not block presence."""
    await _started(client)
    client.submit_payload({
        "kind": "camera", "timestamp": "t", "device_id": "cam0",
        "face": {"emotion": None, "emotion_confidence": None,
                 "trusted": False, "rejected_by": "no_face"},
    })

    assert client.status()["queued"]["face"] == 0


@pytest.mark.anyio
async def test_a_gaze_without_an_emotion_is_still_a_reading(client):
    """A reading is an emotion **or** a gaze."""
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
    """The backend's fusion logic gates on `emotion_trusted`; that decision is not made here."""
    await _started(client)
    client.submit_payload({
        "kind": "camera", "timestamp": "t",
        "face": {"emotion": "sad", "emotion_confidence": 0.3, "trusted": False},
    })

    assert client.status()["queued"]["face"] == 1
    assert client._queues["face"][0]["emotion_trusted"] is False


@pytest.mark.anyio
async def test_a_rejected_heart_window_is_not_a_row(client):
    """`build_heart_record` sets `source` even on rejects, so enqueue checks for a bpm."""
    await _started(client)
    client.submit_payload({
        "kind": "camera", "timestamp": "t",
        "heart": {"source": "rppg", "bpm": None, "confidence": 0.0,
                  "rejected_by": "warming_up"},
    })

    assert client.status()["queued"]["heart"] == 0


@pytest.mark.anyio
async def test_an_unreadable_receipt_does_not_re_post_a_committed_batch(client, monkeypatch):
    """Past `raise_for_status()` the rows are written; a re-post would duplicate them."""
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
    # Written but unconfirmed.
    assert client.status()["recorded"]["cognitive"] == 0
    assert client.status()["unaccounted"]["cognitive"] == 1


@pytest.mark.anyio
async def test_counters_do_not_carry_into_the_next_session(client):
    await _started(client, "s1")
    client.enqueue("cognitive", {"ts": 0})
    await client._flush_once()
    assert client.status()["recorded"]["cognitive"] == 1

    await client.start("s2", "tok2")

    assert client.status()["recorded"]["cognitive"] == 0
    assert client.status()["dropped_locally"]["cognitive"] == 0


@pytest.mark.anyio
async def test_submit_payload_counts_the_shutdown_window_like_enqueue(client):
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
    """Nothing was delivered, so nothing confirms recovery."""
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
    """The slower call's internal `stop()` must not wipe the token the other installed."""
    await _started(client, "s0")

    await asyncio.gather(client.start("s1", "tok1"), client.start("s1", "tok1"))

    assert client.running
    assert client._token == "tok1"
    assert client._session_id == "s1"
    client.enqueue("cognitive", {"ts": 0})
    assert client.status()["queued"]["cognitive"] == 1


@pytest.mark.anyio
async def test_a_successful_pass_still_clears_the_backoff(client, monkeypatch):
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
    """A held block arrives on ~40 ticks; keyed on the reading's own `ts`, not the tick's."""
    await _started(client)
    block = {"source": "muse_optics", "bpm": 68.2, "confidence": 0.8,
             "ts": "2026-08-10T10:00:00+00:00"}
    for i in range(5):
        client.submit_payload({
            "timestamp": f"2026-08-10T10:00:0{i}Z", "device_id": "station1",
            "features": {}, "heart": block,
        })

    assert client.status()["queued"]["heart"] == 1
    assert client._queues["heart"][0]["ts"] == "2026-08-10T10:00:00+00:00"


@pytest.mark.anyio
async def test_rmssd_gating_fields_are_carried_into_the_enqueued_sample(client):
    """RMSSD's own gates, apart from `rejected_by`: a good bpm can carry no RMSSD."""
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
async def test_the_stress_fields_and_their_baseline_are_carried(client):
    await _started(client)
    client.submit_payload({
        "timestamp": "2026-08-10T10:00:00Z", "device_id": "station1",
        "features": {},
        "heart": {"source": "muse_optics", "bpm": 81.0, "trusted": True,
                  "stress_score": 77.5, "stress_category": "high",
                  "stress_baseline_bpm": 70.0, "ts": "2026-08-10T10:00:00+00:00"},
    })

    sample = client._queues["heart"][0]
    assert (sample["stress_score"], sample["stress_category"],
            sample["stress_baseline_bpm"]) == (77.5, "high", 70.0)


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
    """A single last-stamp slot would let one device's reading hide the other's."""
    await _started(client)
    for device, source in (("station1", "muse_optics"), ("cam0", "rppg")):
        client.submit_payload({
            "timestamp": "2026-08-10T10:00:00Z", "device_id": device,
            "features": {},
            "heart": {"source": source, "bpm": 70.0,
                      "ts": "2026-08-10T10:00:00+00:00"},
        })

    assert client.status()["queued"]["heart"] == 2


@pytest.mark.anyio
async def test_the_synthetic_mark_travels_top_level_not_inside_raw(client):
    """The backend strips `synthetic` from a posted `raw` and derives it from the sample."""
    await _started(client)
    client.submit_payload({
        "timestamp": "2026-08-10T10:00:00Z", "device_id": "station1",
        "features": {},
        "heart": {"source": "muse_optics", "bpm": 68.2, "synthetic": True,
                  "ts": "2026-08-10T10:00:00+00:00"},
    })
    sample = client._queues["heart"][0]
    assert sample["synthetic"] is True
    assert "synthetic" not in sample["raw"]
    client.submit_payload({
        "timestamp": "2026-08-10T10:00:10Z", "device_id": "station1",
        "features": {},
        "heart": {"source": "muse_optics", "bpm": 68.5,
                  "ts": "2026-08-10T10:00:10+00:00"},
    })
    assert client._queues["heart"][1]["synthetic"] is None


# ── what the student may record: withheld, and refused sensors stopped ───────

ALL_PERMITTED = {"eeg": "permitted", "headband_optical": "permitted", "camera": "permitted"}
# The backend's answer, as `my_recording_permits` builds it; the sidecar keeps no copy of these.
_REASONS = {"declined": {"eeg": "eeg not consented", "headband_optical": "headband heart sensor not consented",
                         "camera": "camera not consented"},
            "switched_off": "recording is switched off by an administrator",
            "school_year_ended": "the school year has ended"}
_HEART_SOURCES = {"muse_optics": "headband_optical", "muse_ppg": "headband_optical", "rppg": "camera"}


def _backend_answer(states):
    def reason(channel, state):
        found = _REASONS.get(state)
        return found[channel] if isinstance(found, dict) else found

    def refused(state):
        return state not in ("permitted", "unknown")
    sensors = {"camera": ("camera",), "headband": ("eeg", "headband_optical")}
    return {**{c: {"state": s, "reason": reason(c, s)} for c, s in states.items()},
            "sensors": {name: {"allowed": any(states[c] == "permitted" for c in channels),
                               "refused": all(refused(states[c]) for c in channels)}
                        for name, channels in sensors.items()},
            "heart_sources": dict(_HEART_SOURCES)}


class _PermitClient(_FakeClient):
    """Also answers `GET /api/recording/me` for `states`, recording each read."""

    def __init__(self):
        super().__init__()
        self.states, self.status, self.gets = dict(ALL_PERMITTED), 200, []

    async def get(self, url, headers=None):
        self.gets.append({"url": url, "headers": headers})
        return _Response(status_code=self.status, body=_backend_answer(self.states))


@pytest.fixture
def permits(monkeypatch):
    fake = _PermitClient()
    monkeypatch.setattr("src.app.services.push_client.httpx.AsyncClient", lambda **_k: fake)
    pc = PushClient("http://backend:8000")
    pc._fake = fake
    return pc


async def _answer(pc, wait=True, **states):
    """One check, now, answering `states` over all-permitted; `wait` lets any device stop it began finish."""
    pc._fake.states = {**ALL_PERMITTED, **states}
    pc._permits_due = 0.0
    await pc._check_permits_if_due()
    if wait:
        await asyncio.gather(*pc._refusal_tasks.values())


def _posted(pc):
    return [call["url"].rsplit("/", 1)[1] for call in pc._fake.calls]


def _recorder():
    stops = []

    async def handler(sensors, _still_current):
        stops.append(sensors)
    return stops, handler


@pytest.mark.anyio
async def test_the_session_asks_what_it_may_record_with_the_students_token(permits):
    await _started(permits, token="tok")
    await _answer(permits)
    assert permits._fake.gets
    assert all(g == {"url": "http://backend:8000/api/recording/me", "headers": {"Authorization": "Bearer tok"}}
               for g in permits._fake.gets)
    await permits.stop()


@pytest.mark.anyio
async def test_a_refused_channel_is_withheld_and_reads_as_declined(permits):
    """Not sent at all, and the page still names why, as it does for a backend decline."""
    await _started(permits)
    await _answer(permits, eeg="declined")
    permits.enqueue("cognitive", {"ts": "a"})
    permits.enqueue("face", {"ts": "a"})
    await permits._flush_once()
    assert _posted(permits) == ["face"]
    status = permits.status()
    assert status["declined"]["cognitive"] == 1
    assert status["declined_reason"]["cognitive"] == "eeg not consented"
    assert status["last_result"]["cognitive"] == "declined"
    assert status["recorded"]["face"] == 1
    await permits.stop()


@pytest.mark.anyio
async def test_heart_readings_are_withheld_by_their_sensor(permits):
    await _started(permits)
    await _answer(permits, camera="switched_off")
    permits.enqueue("heart", {"ts": "a", "source": "rppg"})
    permits.enqueue("heart", {"ts": "b", "source": "muse_optics"})
    await permits._flush_once()
    [call] = permits._fake.calls
    assert [s["source"] for s in call["json"]["samples"]] == ["muse_optics"]
    assert permits.status()["declined_reason"]["heart"] == "recording is switched off by an administrator"
    await permits.stop()


@pytest.mark.anyio
async def test_no_answer_or_an_unknown_one_withholds_nothing(permits):
    """An older backend 404s the check; the backend's own gate still decides."""
    permits._fake.status = 404
    await _started(permits)
    await _answer(permits)
    assert permits.status()["permits"] is None
    permits.enqueue("cognitive", {"ts": "a"})
    await permits._flush_once()
    permits._fake.status = 200
    await _answer(permits, eeg="unknown")
    permits.enqueue("cognitive", {"ts": "b"})
    await permits._flush_once()
    assert _posted(permits) == ["cognitive", "cognitive"]
    assert permits.status()["declined"]["cognitive"] == 0
    await permits.stop()


@pytest.mark.anyio
async def test_refused_sensors_are_stopped_the_headband_only_when_both_its_channels_are(permits):
    stops, handler = _recorder()
    permits.set_refusal_handler(handler)
    await _started(permits)
    await _answer(permits, eeg="declined")
    await _answer(permits, camera="declined")
    await _answer(permits, eeg="declined", headband_optical="school_year_ended")
    assert stops == [{"camera"}, {"headband"}]
    await permits.stop()


@pytest.mark.anyio
async def test_a_failed_check_keeps_the_last_answer_and_stops_nothing(permits):
    stops, handler = _recorder()
    permits.set_refusal_handler(handler)
    await _started(permits)
    await _answer(permits, camera="declined")
    permits._fake.status = 503
    permits._permits_due = 0.0
    await permits._check_permits_if_due()
    assert stops == [{"camera"}]
    permits.enqueue("face", {"ts": "a"})
    await permits._flush_once()
    assert _posted(permits) == []
    await permits.stop()


@pytest.mark.anyio
async def test_the_check_waits_its_interval(permits, monkeypatch):
    real, skew = time.monotonic, [0.0]
    monkeypatch.setattr("src.app.services.push_client.time",
                        types.SimpleNamespace(monotonic=lambda: real() + skew[0]))
    await _started(permits)
    await _answer(permits)
    reads = len(permits._fake.gets)
    await permits._check_permits_if_due()
    assert len(permits._fake.gets) == reads
    skew[0] = PERMIT_CHECK_SECONDS
    await permits._check_permits_if_due()
    assert len(permits._fake.gets) == reads + 1
    await permits.stop()


@pytest.mark.anyio
async def test_a_new_session_asks_again(permits):
    await _started(permits, session_id="s1")
    await _answer(permits, camera="declined")
    await permits.start("s2", "tok2")
    assert permits.status()["permits"] is None
    await permits.stop()


async def _until(condition):
    """Yield to the loop until `condition()` holds, a bounded number of times."""
    for _ in range(300):
        if condition():
            return
        await asyncio.sleep(0.01)


@pytest.mark.anyio
async def test_a_recheck_decides_the_next_send(permits):
    """A parent turns the camera back on and it is started: its readings go by that answer, not the last."""
    await _started(permits)
    await _answer(permits, camera="declined")
    # Parked in its wait, so the check that decides is the one between waking and sending.
    await asyncio.sleep(0.05)
    permits._fake.states = dict(ALL_PERMITTED)
    permits.enqueue("face", {"ts": "a"})
    permits.recheck()
    await _until(lambda: permits._fake.calls)
    assert _posted(permits) == ["face"]
    assert permits.status()["declined"]["face"] == 0
    await permits.stop()


@pytest.mark.anyio
async def test_a_push_stop_does_not_wait_for_a_device_being_stopped(permits):
    """A slow Bluetooth disconnect must not hold the shutdown inside its budget, or past it."""
    release = asyncio.Event()
    began, current_after = [], []

    async def slow_stop(sensors, still_current):
        began.append(sensors)
        await release.wait()
        current_after.append(still_current())
    permits.set_refusal_handler(slow_stop)
    await _started(permits)
    await _answer(permits, wait=False, camera="declined")
    await _until(lambda: began)
    assert await permits.stop()
    assert not permits._refusal_tasks["camera"].done(), "the push stop returned before the device stop"
    release.set()
    await permits._refusal_tasks["camera"]
    # The lesson that refused it has ended: what is left of the stop must stop nothing more.
    assert current_after == [False]


@pytest.mark.anyio
async def test_one_stop_at_a_time_per_sensor_and_a_slow_one_holds_up_no_other(permits):
    release = asyncio.Event()
    stops = []

    async def slow_stop(sensors, _still_current):
        stops.append(sensors)
        if sensors == {"camera"}:
            await release.wait()
    permits.set_refusal_handler(slow_stop)
    await _started(permits)
    await _answer(permits, wait=False, camera="declined")
    await _until(lambda: stops)
    first = permits._refusal_tasks["camera"]
    await _answer(permits, wait=False, camera="declined", eeg="declined", headband_optical="declined")
    assert permits._refusal_tasks["camera"] is first, "a second camera stop began while the first ran"
    await _until(lambda: len(stops) > 1)
    assert stops == [{"camera"}, {"headband"}]
    release.set()
    await asyncio.gather(*permits._refusal_tasks.values())
    await permits.stop()


@pytest.mark.anyio
async def test_shutdown_cancels_a_device_stop_still_under_way(permits):
    release = asyncio.Event()

    async def slow_stop(_sensors, _still_current):
        await release.wait()
    permits.set_refusal_handler(slow_stop)
    await _started(permits)
    await _answer(permits, wait=False, camera="declined")
    task = permits._refusal_tasks["camera"]
    await permits.stop()
    await permits.cancel_refusals()
    assert task.cancelled()


@pytest.mark.anyio
async def test_a_recheck_drops_the_answer_at_once(permits):
    """A send already under way must not withhold a just-started sensor's readings on the old answer."""
    await _started(permits)
    await _answer(permits, camera="declined")
    permits.recheck()
    assert permits.status()["permits"] is None
    assert permits._withhold("face", [{"ts": "a"}]) == [{"ts": "a"}]
    await permits.stop()


@pytest.mark.anyio
async def test_the_status_passes_on_the_backends_answer(permits):
    """The page reads the same fields, the sensor verdicts included, rather than re-deriving them."""
    await _started(permits)
    await _answer(permits, eeg="declined", headband_optical="declined")
    passed_on = permits.status()["permits"]
    assert passed_on == _backend_answer({**ALL_PERMITTED, "eeg": "declined", "headband_optical": "declined"})
    assert passed_on["sensors"]["headband"] == {"allowed": False, "refused": True}
    await permits.stop()


@pytest.mark.anyio
async def test_the_loop_checks_on_time_even_while_backing_off(permits, monkeypatch):
    monkeypatch.setattr("src.app.services.push_client.PERMIT_CHECK_SECONDS", 0.05)
    await _started(permits)
    # A long backoff, as a backend refusing batches leaves.
    permits._backoff, permits._retry_at = 60.0, time.monotonic() + 60.0
    reads = len(permits._fake.gets)
    for _ in range(200):
        if len(permits._fake.gets) >= reads + 3:
            break
        await asyncio.sleep(0.01)
    assert len(permits._fake.gets) >= reads + 3
    await permits.stop(flush=False)
