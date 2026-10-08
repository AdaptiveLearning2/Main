# Hourly operational counters for the admin console: accumulated in memory, flushed to
# `ops_counters`. Never a person, an address or a reading in a key (docs/admin-and-security.md).

import threading
from datetime import datetime, timedelta, timezone
from typing import Callable

from env_config import env_number

# Seconds between flushes; 0 switches the flusher off (counts then only accumulate).
OPS_FLUSH_SECONDS = env_number("OPS_FLUSH_SECONDS", 60.0, float, minimum=0.0)

# Distinct (hour, kind, key) cells held while the database is unreachable; past it, new cells drop.
_MAX_PENDING = 5000

_lock = threading.Lock()
_pending: dict[tuple[str, str, str], list] = {}   # -> [n, sum | None, max | None]
_dropped = 0

_stop = threading.Event()
_silenced = threading.Event()
_print_lock = threading.Lock()
# How long `stop` lets a print already under way finish; a print takes microseconds unless stdout is blocked.
_PRINT_WAIT_SECONDS = 2.0
_thread: threading.Thread | None = None

# PostgREST cuts any read at `db-max-rows` (1000), silently; reads page by `id` up to this many pages.
_PAGE_ROWS = 1000
_MAX_PAGES = 50


def _hour(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.replace(minute=0, second=0, microsecond=0).isoformat()


def _add(kind: str, key: str, n: int, value: float | None) -> None:
    global _dropped
    cell_key = (_hour(), kind, key)
    with _lock:
        cell = _pending.get(cell_key)
        if cell is None:
            if len(_pending) >= _MAX_PENDING:
                _dropped += 1
                return
            cell = _pending[cell_key] = [0, None, None]
        cell[0] += n
        if value is not None:
            cell[1] = (cell[1] or 0.0) + value
            cell[2] = value if cell[2] is None else max(cell[2], value)


def bump(kind: str, key: str, n: int = 1) -> None:
    """Count `n` events. Cheap and non-blocking; safe on the event loop."""
    if n:
        _add(kind, key, n, None)


def observe(kind: str, key: str, value: float) -> None:
    """Count one event carrying a measurement (kept as a sum and a max)."""
    _add(kind, key, 1, float(value))


def _drain() -> list[dict]:
    with _lock:
        cells = list(_pending.items())
        _pending.clear()
    return [{"hour": h, "kind": k, "key": key, "n": c[0], "sum": c[1], "max": c[2]}
            for (h, k, key), c in cells]


def _restore(rows: list[dict]) -> None:
    global _dropped
    for r in rows:
        with _lock:
            cell_key = (r["hour"], r["kind"], r["key"])
            cell = _pending.get(cell_key)
            if cell is None:
                if len(_pending) >= _MAX_PENDING:
                    _dropped += 1
                    continue
                _pending[cell_key] = [r["n"], r["sum"], r["max"]]
                continue
            cell[0] += r["n"]
            if r["sum"] is not None:
                cell[1] = (cell[1] or 0.0) + r["sum"]
                cell[2] = r["max"] if cell[2] is None else max(cell[2], r["max"])


def flush(client) -> bool:
    """Write everything pending; on failure it stays pending for the next flush.

    Not idempotent: a write that lands but whose reply is lost is sent again (docs/admin-and-security.md).
    """
    rows = _drain()
    if not rows:
        return True
    try:
        client.rpc("ops_counters_add", {"p_rows": rows}).execute()
        return True
    except Exception as e:                                     # noqa: BLE001
        # Silent once `stop` gave up waiting: a print during interpreter exit is a fatal abort.
        # Under the lock `stop` takes to silence, so no print can start after `stop` returns.
        with _print_lock:
            if not _silenced.is_set():
                print(f"[ops_metrics] flush of {len(rows)} cells failed, kept for the next one: {e}")
        _restore(rows)
        return False


def _loop(client_of: Callable[[], object]) -> None:
    while not _stop.wait(OPS_FLUSH_SECONDS):
        flush(client_of())
    flush(client_of())   # what accumulated since the last tick


def start(client_of: Callable[[], object]) -> bool:
    """Start the flusher, unless switched off or already running."""
    global _thread
    if OPS_FLUSH_SECONDS <= 0 or (_thread and _thread.is_alive()):
        return False
    _stop.clear()
    _silenced.clear()
    _thread = threading.Thread(target=_loop, args=(client_of,), name="ops-flush", daemon=True)
    _thread.start()
    return True


def stop(timeout: float = 5.0) -> None:
    """Flush once more and join (a print during shutdown is a fatal abort)."""
    global _thread
    _stop.set()
    thread, _thread = _thread, None
    if thread and thread.is_alive():
        thread.join(timeout=timeout)
        if thread.is_alive():
            # Bounded too: a print stuck on a blocked stdout must not hang shutdown.
            locked = _print_lock.acquire(timeout=_PRINT_WAIT_SECONDS)
            _silenced.set()
            if locked:
                _print_lock.release()


def reset() -> None:
    """Drop everything pending. For tests."""
    global _dropped
    with _lock:
        _pending.clear()
        _dropped = 0
    _silenced.clear()


def pending() -> dict[tuple[str, str], int]:
    """Unflushed counts by (kind, key), summed over hours. For tests and the admin read."""
    out: dict[tuple[str, str], int] = {}
    with _lock:
        for (_h, kind, key), cell in _pending.items():
            out[(kind, key)] = out.get((kind, key), 0) + cell[0]
    return out


def read(client, kinds: list[str], hours: int) -> dict:
    """Stored and pending cells for `kinds` in the last `hours` hourly buckets, this one included.

    Returns `{retrieved, complete, rows, dropped}`; rows are `{hour, kind, key, n, sum, max}`, with
    pending cells merged so the current hour is never a flush behind. `complete` is false past `_MAX_PAGES`.
    """
    since = since_hour(hours)
    merged: dict[tuple[str, str, str], dict] = {}
    retrieved, complete = True, True
    try:
        last_id = 0
        for page in range(_MAX_PAGES):
            batch = (client.table("ops_counters").select("id,hour,kind,key,n,sum,max")
                     .in_("kind", kinds).gte("hour", since.isoformat()).gt("id", last_id)
                     .order("id").limit(_PAGE_ROWS).execute()).data or []
            for r in batch:
                hour = datetime.fromisoformat(str(r["hour"])).astimezone(timezone.utc).isoformat()
                merged[(hour, r["kind"], r["key"])] = {k: v for k, v in r.items() if k != "id"} | {"hour": hour}
            if len(batch) < _PAGE_ROWS:
                break
            last_id = batch[-1]["id"]
        else:
            complete = False
    except Exception as e:                                     # noqa: BLE001
        print(f"[ops_metrics] read failed: {e}")
        retrieved = False
    with _lock:
        local = [(k, list(c)) for k, c in _pending.items() if k[1] in kinds]
    for (h, kind, key), (n, s, m) in local:
        if datetime.fromisoformat(h) < since:
            continue
        row = merged.setdefault((h, kind, key),
                                {"hour": h, "kind": kind, "key": key, "n": 0, "sum": None, "max": None})
        row["n"] += n
        if s is not None:
            row["sum"] = (row["sum"] or 0.0) + s
            row["max"] = m if row["max"] is None else max(row["max"], m)
    rows = sorted(merged.values(), key=lambda r: (r["hour"], r["kind"], r["key"]))
    return {"retrieved": retrieved, "complete": complete, "rows": rows, "dropped": _dropped}


def since_hour(hours: int, now: datetime | None = None) -> datetime:
    """Start of the oldest of the last `hours` hourly buckets, the current one included."""
    now = (now or datetime.now(timezone.utc)).replace(minute=0, second=0, microsecond=0)
    return now - timedelta(hours=hours - 1)
