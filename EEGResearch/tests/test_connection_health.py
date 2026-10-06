"""Device liveness in the status payloads, and the bridge's reconnect fields passed through."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import socket
import threading
import time

import pytest

from src.app.config import DeviceConfig, get_settings
from src.app.models import EegSample
from src.app.services.eeg_ingestion import (
    TcpMuseBridgeAdapter,
    _apply_bridge_ingestion_fields,
)
from src.app.services.stream_manager import DeviceReleasing, DeviceSession
from datetime import datetime, timezone


# ── the bridge's reconnect fields survive the pass-through ──────────────────

def test_reconnect_fields_are_carried_with_their_types():
    target: dict = {}
    _apply_bridge_ingestion_fields(target, {
        "auto_reconnect": True, "reconnecting": 1, "reconnect_attempt": "2",
        "reconnect_max_attempts": 5, "reconnect_exhausted": False,
        "eeg_age_ms": 137,
    })
    assert target["auto_reconnect"] is True
    assert target["reconnecting"] is True
    assert target["reconnect_attempt"] == 2
    assert target["reconnect_max_attempts"] == 5
    assert target["reconnect_exhausted"] is False
    assert target["eeg_age_ms"] == 137


def test_eeg_age_null_is_kept_apart_from_zero_and_from_absent():
    """null: nothing arrived yet; 0: arrived this instant; absent: an older bridge."""
    fresh: dict = {}
    _apply_bridge_ingestion_fields(fresh, {"eeg_age_ms": None})
    assert fresh["eeg_age_ms"] is None

    now: dict = {}
    _apply_bridge_ingestion_fields(now, {"eeg_age_ms": 0})
    assert now["eeg_age_ms"] == 0

    old_bridge: dict = {}
    _apply_bridge_ingestion_fields(old_bridge, {"muse_connected": True})
    assert "eeg_age_ms" not in old_bridge
    assert "reconnecting" not in old_bridge


def test_malformed_reconnect_fields_keep_the_prior_value():
    target = {"reconnect_attempt": 1, "eeg_age_ms": 40}
    _apply_bridge_ingestion_fields(target, {"reconnect_attempt": "x", "eeg_age_ms": "y"})
    assert target["reconnect_attempt"] == 1
    assert target["eeg_age_ms"] == 40


# ── TCP reconnect backoff ───────────────────────────────────────────────────

@pytest.fixture
def token_file(tmp_path):
    """What muse_native_bridge writes on start; the adapter must send it before anything else."""
    path = tmp_path / "muse_bridge.token"
    path.write_text("a1b2c3d4", encoding="ascii")
    return str(path)


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_a_refused_connection_is_not_retried_until_the_backoff_elapses(token_file):
    adapter = TcpMuseBridgeAdapter(host="127.0.0.1", port=_closed_port(), timeout_seconds=1,
                                   token_file=token_file)
    assert adapter.connect_wait_remaining() == 0.0

    assert adapter._try_connect() is False
    assert adapter.connect_failures == 1
    first_wait = adapter.connect_wait_remaining()
    assert 0.0 < first_wait <= TcpMuseBridgeAdapter.CONNECT_BACKOFF_MIN_S

    # Inside the window: no socket is opened, the failure count stays put.
    assert adapter._try_connect() is False
    assert adapter.connect_failures == 1

    # Says how long, so the log reads as waiting, not a failure every 250ms.
    with pytest.raises(RuntimeError, match=r"next attempt in \d+\.\ds"):
        adapter.drain_samples(1)


def test_the_backoff_doubles_to_a_cap_and_never_past_it(token_file):
    adapter = TcpMuseBridgeAdapter(host="127.0.0.1", port=_closed_port(), timeout_seconds=1,
                                   token_file=token_file)
    waits = []
    for _ in range(6):
        adapter._next_connect_at = 0.0  # let the next attempt through
        assert adapter._try_connect() is False
        waits.append(adapter.connect_wait_remaining())
    # 0.5, 1, 2, 4, 5, 5, minus however long the assertions took.
    for earlier, later in zip(waits, waits[1:]):
        assert later >= earlier - 0.05
    assert waits[-1] <= TcpMuseBridgeAdapter.CONNECT_BACKOFF_MAX_S
    assert waits[-1] > TcpMuseBridgeAdapter.CONNECT_BACKOFF_MAX_S * 0.9
    assert adapter.connect_failures == 6


# ── the bridge's token: proved, then sent, and a bounded read ───────────────

def _listener():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(2.0)
    return listener, listener.getsockname()[1]


class _FakeBridge:
    """Answers the adapter's CHALLENGE as `answer` says ("right", "wrong", "nonascii", "silent" or
    "hangup", as a bridge built before the challenge does) and keeps what follows."""

    def __init__(self, answer="right", token="a1b2c3d4"):
        self.listener, self.port = _listener()
        self.answer, self.token = answer, token
        self.after_proof = b""
        self.heard = b""      # every byte the adapter sent, first line included
        self.conn = None
        self.proved = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        try:
            self.conn, _ = self.listener.accept()
            self.conn.settimeout(2.0)
            stream = self.conn.makefile("rb")
            first = stream.readline()
            self.heard = first
            nonce = first.decode().strip().removeprefix("CHALLENGE ")
            if self.answer == "hangup":
                self.conn.close()
                self.conn = None
                return
            if self.answer == "silent":
                time.sleep(1.5)
                self.heard += stream.read1(4096) if hasattr(stream, "read1") else b""
                return
            key = self.token if self.answer == "right" else "someone-else"
            proof = hmac.new(key.encode(), nonce.encode(), hashlib.sha256).hexdigest()
            if self.answer == "nonascii":
                proof = "é" * 64
            self.conn.sendall(f"PROOF {proof}\n".encode())
            self.proved.set()
            self.after_proof = stream.readline()
            self.heard += self.after_proof
        except OSError:
            pass

    def close(self):
        self.thread.join(3.0)
        if self.conn:
            self.conn.close()
        self.listener.close()


def test_a_successful_connection_resets_the_backoff(token_file):
    bridge = _FakeBridge()
    adapter = TcpMuseBridgeAdapter(host="127.0.0.1", port=bridge.port, timeout_seconds=1,
                                   token_file=token_file)
    try:
        adapter._connect_backoff_s = TcpMuseBridgeAdapter.CONNECT_BACKOFF_MAX_S
        adapter._next_connect_at = 0.0
        assert adapter._try_connect() is True
        assert adapter._connect_backoff_s == TcpMuseBridgeAdapter.CONNECT_BACKOFF_MIN_S
        assert adapter.connect_wait_remaining() == 0.0
    finally:
        adapter.disconnect()
        bridge.close()


def test_the_token_follows_the_bridges_proof(token_file):
    bridge = _FakeBridge("right")
    adapter = TcpMuseBridgeAdapter(host="127.0.0.1", port=bridge.port, timeout_seconds=1, token_file=token_file)
    try:
        assert adapter._try_connect() is True
        bridge.thread.join(3.0)
        assert bridge.after_proof == b"AUTH a1b2c3d4\n"
    finally:
        adapter.disconnect()
        bridge.close()


@pytest.mark.parametrize("answer, says", [("hangup", "rebuild it"), ("wrong", "answered the challenge wrongly")])
def test_an_old_bridge_is_told_apart_from_an_impostor(token_file, answer, says, capsys):
    bridge = _FakeBridge(answer)
    adapter = TcpMuseBridgeAdapter(host="127.0.0.1", port=bridge.port, timeout_seconds=1, token_file=token_file)
    try:
        assert adapter._try_connect() is False
        assert says in capsys.readouterr().out
    finally:
        adapter.disconnect()
        bridge.close()


def test_a_repeated_challenge_failure_is_reported_once_until_a_connect_succeeds(token_file, capsys):
    """An old bridge exe fails every retry, a few seconds apart; the log says so once, not forever."""
    adapter = TcpMuseBridgeAdapter(host="127.0.0.1", port=0, timeout_seconds=1, token_file=token_file)

    def attempt(answer):
        bridge = _FakeBridge(answer)
        adapter.port, adapter._next_connect_at = bridge.port, 0.0
        try:
            return adapter._try_connect()
        finally:
            adapter.disconnect()
            bridge.close()

    try:
        assert attempt("hangup") is False and attempt("hangup") is False
        assert capsys.readouterr().out.count("rebuild it") == 1
        assert attempt("wrong") is False
        assert capsys.readouterr().out.count("answered the challenge wrongly") == 1, "a new reason is new"
        assert attempt("right") is True
        capsys.readouterr()
        assert attempt("wrong") is False
        assert capsys.readouterr().out.count("answered the challenge wrongly") == 1, "reset by a success"
    finally:
        adapter.disconnect()


@pytest.mark.parametrize("answer", ["wrong", "silent", "hangup", "nonascii"])
def test_whatever_cannot_prove_itself_the_bridge_never_gets_the_token(token_file, answer):
    """A process that took the port first would otherwise read the token and feed fabricated EEG."""
    bridge = _FakeBridge(answer)
    adapter = TcpMuseBridgeAdapter(host="127.0.0.1", port=bridge.port, timeout_seconds=1, token_file=token_file)
    try:
        assert adapter._try_connect() is False
        assert adapter.connect_failures == 1
        bridge.close()
        assert bridge.heard.startswith(b"CHALLENGE ")
        assert b"a1b2c3d4" not in bridge.heard
    finally:
        adapter.disconnect()
        bridge.close()


def test_no_token_file_means_no_connection_attempt(tmp_path):
    """No bridge has started on this port, so there is nothing to authenticate to."""
    listener, port = _listener()
    adapter = TcpMuseBridgeAdapter(host="127.0.0.1", port=port, timeout_seconds=1,
                                   token_file=str(tmp_path / "absent.token"))
    try:
        assert adapter._try_connect() is False
        assert adapter.connect_failures == 1
        listener.settimeout(0.3)
        with pytest.raises(socket.timeout):
            listener.accept()
    finally:
        adapter.disconnect()
        listener.close()


def test_a_line_past_the_cap_stops_the_reader(token_file):
    """Whatever sends an unterminated megabyte is not the bridge."""
    bridge = _FakeBridge("right")
    adapter = TcpMuseBridgeAdapter(host="127.0.0.1", port=bridge.port, timeout_seconds=1, token_file=token_file)
    stop = threading.Event()

    def trickle(conn):
        # Never idle long enough for the 1 s read timeout, so only the cap can stop the reader.
        conn.settimeout(0.5)
        try:
            while not stop.is_set():
                conn.sendall(b"x" * 8192)
                time.sleep(0.05)
        except OSError:
            pass

    try:
        assert adapter._try_connect() is True
        bridge.thread.join(3.0)                     # proof sent and the AUTH line read
        sender = threading.Thread(target=trickle, args=(bridge.conn,), daemon=True)
        sender.start()
        deadline = time.monotonic() + 3.0
        while adapter._reader_thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not adapter._reader_thread.is_alive()
        stop.set()
        sender.join(2.0)
    finally:
        stop.set()
        adapter.disconnect()
        bridge.close()


# ── DeviceSession health ────────────────────────────────────────────────────

class _Adapter:
    """Reads succeed or fail on command; carries the preset fields a bridge would."""

    def __init__(self):
        self.fail = False
        self.meta = {"requested_preset": "", "active_preset": ""}

    def connect(self):
        pass

    def disconnect(self):
        pass

    def read_sample(self):
        if self.fail:
            raise RuntimeError("no bridge")
        return EegSample(datetime.now(timezone.utc), 700.0, 700.0, 700.0, 700.0)

    def get_ingestion_meta(self):
        return dict(self.meta)


def _session() -> DeviceSession:
    s = DeviceSession("station1", get_settings(),
                      DeviceConfig(device_id="station1", kind="sim",
                                   host="127.0.0.1", port=8765))
    s.adapter = _Adapter()
    return s


async def _tick(session: DeviceSession, n: int = 1) -> None:
    period = 1 / max(1, session.settings.eeg_sample_hz)
    session.running = True
    task = asyncio.create_task(session._loop())
    await asyncio.sleep(period * n + period * 0.5)
    session.running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def test_before_any_reading_the_device_reports_never_not_old():
    fields = _session().health_fields()
    assert fields["last_good_ts"] is None
    assert fields["last_good_age_s"] is None
    assert fields["consecutive_errors"] == 0
    assert fields["preset_mismatch"] is False


def test_a_good_tick_stamps_the_reading_and_clears_the_error_run():
    session = _session()
    session.consecutive_errors = 4
    asyncio.run(_tick(session))
    fields = session.health_fields()
    assert fields["last_good_ts"] == session.latest_payload["timestamp"]
    assert fields["last_good_age_s"] is not None and fields["last_good_age_s"] >= 0.0
    assert fields["consecutive_errors"] == 0


def test_failed_reads_count_up_and_the_last_good_stamp_stands():
    """The stamp lets a consumer say "quiet for 8s" while the errors climb."""
    session = _session()
    asyncio.run(_tick(session))
    stamp = session.last_good_ts
    session.adapter.fail = True
    asyncio.run(_tick(session, 3))
    fields = session.health_fields()
    assert fields["consecutive_errors"] >= 2
    assert fields["errors_seen"] >= 2
    assert fields["last_good_ts"] == stamp


def test_the_age_grows_between_reads_rather_than_being_frozen():
    session = _session()
    session._note_good_tick("2026-09-02T10:00:00+00:00")
    session.last_good_at -= 7.0
    assert session.health_fields()["last_good_age_s"] == pytest.approx(7.0, abs=0.2)


def test_a_preset_mismatch_is_reported_only_after_it_has_settled():
    """Requested and active disagree briefly on every good connection after a preset switch."""
    session = _session()
    session._note_preset({"requested_preset": "PRESET_1035", "active_preset": "PRESET_21"})
    assert session.health_fields()["preset_mismatch"] is False
    session._preset_mismatch_since -= DeviceSession.PRESET_SETTLE_SECONDS + 1
    assert session.health_fields()["preset_mismatch"] is True

    # Agreement clears it, and so does either side going unknown.
    session._note_preset({"requested_preset": "PRESET_1035", "active_preset": "PRESET_1035"})
    assert session._preset_mismatch_since is None
    session._note_preset({"requested_preset": "PRESET_1035", "active_preset": ""})
    assert session._preset_mismatch_since is None


def test_both_status_shapes_carry_the_same_health_fields():
    """/api/v1/muse/status and /api/v1/state (and the push client) must agree on liveness."""
    session = _session()
    asyncio.run(_tick(session))
    a = session.snapshot()["ingestion"]
    b = session.muse_ingestion_snapshot()
    for key in ("last_good_ts", "last_good_age_s", "errors_seen",
                "consecutive_errors", "preset_mismatch"):
        assert key in a and key in b
    assert a["last_good_ts"] == b["last_good_ts"]


def test_stop_forgets_the_last_reading_with_the_rest_of_the_session():
    session = _session()
    asyncio.run(_tick(session))
    assert session.last_good_ts is not None

    async def run():
        session.running = True
        session._task = asyncio.create_task(asyncio.sleep(60))
        await session.stop()
    asyncio.run(run())
    assert session.last_good_ts is None
    assert session.last_good_at is None
    assert session.consecutive_errors == 0


def test_two_stops_at_once_never_release_the_adapter_at_the_same_time():
    """The page and the push client may each stop one device in the same moment."""
    session = _session()
    guard = threading.Lock()
    inside, overlaps, calls = [0], [], []

    def disconnect():
        with guard:
            inside[0] += 1
            calls.append(1)
            if inside[0] > 1:
                overlaps.append(inside[0])
        time.sleep(0.05)
        with guard:
            inside[0] -= 1
    session.adapter.disconnect = disconnect

    async def run():
        session.running = True
        session._task = asyncio.create_task(asyncio.sleep(60))
        await asyncio.gather(session.stop(), session.stop())
    asyncio.run(run())
    assert calls, "neither stop reached the adapter"
    assert overlaps == []


def test_a_stop_cancelled_while_its_stream_winds_down_is_cancelled_and_still_releases_the_link():
    """Shutdown cancels a stop: honoured at once, not after a disconnect that can hang, which runs behind it."""
    session = _session()
    released = threading.Event()
    session.adapter.disconnect = released.set

    async def run():
        winding_down = asyncio.Event()

        async def slow_stream():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                winding_down.set()
                await asyncio.sleep(0.5)
                raise
        session.running = True
        session._task = asyncio.create_task(slow_stream())
        stop = asyncio.create_task(session.stop())
        await winding_down.wait()
        stop.cancel()
        # Raised, not swallowed: a swallowed cancel would carry on into the disconnect and return.
        with pytest.raises(asyncio.CancelledError):
            await stop
        assert await asyncio.to_thread(released.wait, 5), "the link was never let go"
    asyncio.run(run())


def _releasing_in_the_background(session, during):
    """Cancels a stop `during` its wind-down or its disconnect, which then blocks until `let_go` is set."""
    order, forgets, entered, let_go = [], [], threading.Event(), threading.Event()

    def disconnect():
        entered.set()
        let_go.wait(5)
        order.append("disconnected")
    session.adapter.disconnect = disconnect
    session.adapter.connect = lambda: order.append("connected")
    forget = session._forget_stream
    session._forget_stream = lambda: (forgets.append(1), forget())

    async def cancel_a_stop():
        async def slow_stream():
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                await asyncio.sleep(0.2)
                raise
        session.running = True
        session._task = asyncio.create_task(slow_stream() if during == "wind-down" else asyncio.sleep(60))
        stop = asyncio.create_task(session.stop())
        if during == "wind-down":
            await asyncio.sleep(0.05)
        else:
            assert await asyncio.to_thread(entered.wait, 5), "the disconnect never began"
        stop.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stop
        assert await asyncio.to_thread(entered.wait, 5), "the background disconnect never began"
        session.adapter.disconnect = lambda: order.append("disconnected again")
    return order, forgets, let_go, cancel_a_stop


@pytest.mark.parametrize("during", ["wind-down", "disconnect"])
def test_a_start_after_a_cancelled_stop_waits_for_its_background_disconnect(during):
    """Connected first, the new link would be the one that disconnect lets go."""
    session = _session()
    order, _forgets, let_go, cancel_a_stop = _releasing_in_the_background(session, during)

    async def run():
        await cancel_a_stop()
        start = asyncio.create_task(session.start())
        await asyncio.sleep(0.1)
        assert order == []
        let_go.set()
        await start
        session.running = False
        session._task.cancel()
    asyncio.run(run())
    assert order == ["disconnected", "connected"]


@pytest.mark.parametrize("during", ["wind-down", "disconnect"])
def test_a_stop_after_a_cancelled_stop_waits_for_its_background_disconnect(during):
    """Otherwise one adapter is released twice at once; and a stream already forgotten is not forgotten again."""
    session = _session()
    order, forgets, let_go, cancel_a_stop = _releasing_in_the_background(session, during)

    async def run():
        await cancel_a_stop()
        stop = asyncio.create_task(session.stop())
        await asyncio.sleep(0.1)
        assert order == []
        let_go.set()
        await stop
    asyncio.run(run())
    assert order == ["disconnected", "disconnected again"]
    assert len(forgets) == 1


def test_a_background_disconnect_that_fails_is_logged_and_the_next_start_still_connects(caplog):
    session = _session()
    connected, entered, let_go = [], threading.Event(), threading.Event()

    def disconnect():
        entered.set()
        let_go.wait(5)
        raise OSError("link stuck")
    session.adapter.disconnect = disconnect
    session.adapter.connect = lambda: connected.append(1)

    async def run():
        session.running = True
        session._task = asyncio.create_task(asyncio.sleep(60))
        stop = asyncio.create_task(session.stop())
        assert await asyncio.to_thread(entered.wait, 5), "the disconnect never began"
        stop.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stop
        let_go.set()
        await session.start()
        session.running = False
        session._task.cancel()
    with caplog.at_level(logging.WARNING):
        asyncio.run(run())
    assert connected == [1]
    assert "disconnect failed" in caplog.text


@pytest.mark.parametrize("during", ["wind-down", "disconnect"])
def test_a_start_while_a_disconnect_hangs_is_refused_in_time_and_connects_nothing(during):
    """Connected, the late disconnect would drop the new link; waited on for ever, the device lock is held for good."""
    session = _session()
    session.RELEASE_WAIT_SECONDS = 0.2
    order, _forgets, let_go, cancel_a_stop = _releasing_in_the_background(session, during)

    async def run():
        await cancel_a_stop()
        with pytest.raises(DeviceReleasing):
            await asyncio.wait_for(session.start(), 5)
        assert order == []
        let_go.set()
        await asyncio.wait_for(asyncio.shield(session._releasing), 5)
        await asyncio.wait_for(session.start(), 5)
        session.running = False
        session._task.cancel()
    try:
        asyncio.run(run())
    finally:
        let_go.set()
    assert order == ["disconnected", "connected"]


def test_a_stop_whose_disconnect_hangs_fails_and_starts_no_second_disconnect(caplog):
    """Reported as stopped, the page would say so of a device still held; each new thread would pile up."""
    session = _session()
    session.RELEASE_WAIT_SECONDS = 0.2
    disconnects, connected, let_go = [], [], threading.Event()
    session.adapter.disconnect = lambda: (disconnects.append(1), let_go.wait(5))
    session.adapter.connect = lambda: connected.append(1)

    async def run():
        session.running = True
        session._task = asyncio.create_task(asyncio.sleep(60))
        for _ in range(3):
            with pytest.raises(DeviceReleasing):
                await asyncio.wait_for(session.stop(), 5)
        with pytest.raises(DeviceReleasing):
            await asyncio.wait_for(session.start(), 5)
        assert (disconnects, connected) == ([1], [])
        let_go.set()
        # Ended, it is let go of: the device starts again.
        await asyncio.wait_for(asyncio.shield(session._releasing), 5)
        await asyncio.wait_for(session.start(), 5)
        session.running = False
        session._task.cancel()
    try:
        with caplog.at_level(logging.WARNING):
            asyncio.run(run())
    finally:
        let_go.set()
    assert connected == [1]
    # Waited on once: the later stops and the start refuse at once, not holding the lock for another wait.
    assert caplog.text.count("disconnect still running") == 1


def test_a_stop_cancelled_mid_disconnect_still_forgets_the_session():
    """Otherwise the next student on this device inherits the last one's baseline."""
    session = _session()
    asyncio.run(_tick(session))
    assert session.last_good_ts is not None
    entered = threading.Event()

    def disconnect():
        entered.set()
        time.sleep(0.2)
    session.adapter.disconnect = disconnect

    async def run():
        session.running = True
        session._task = asyncio.create_task(asyncio.sleep(60))
        stop = asyncio.create_task(session.stop())
        await asyncio.to_thread(entered.wait, 5)
        stop.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stop
    asyncio.run(run())
    assert session.last_good_ts is None
