"""Render a closed session's charts and put them in private storage.

Out of band (a storage failure never fails session close), logged rather
than swallowed (this is the copy that survives the end-of-year delete), and
idempotent (upsert to id-derived paths). A channel that recorded nothing gets
a null in `chart_paths`, not an empty chart.
"""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import chart_render

BUCKET = "session-charts"

# Rows per table, shared by session review and the archive so both truncate at the same place.
_ROW_CAP = 20000


def _epoch_ms(value) -> float | None:
    """A timestamp as milliseconds, or None (dropped quietly) for anything unparseable."""
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp() * 1000.0


def _line_points(rows, fields) -> dict:
    """`{field: [(x, y), ...]}` for the named fields, x in epoch ms; untimed rows dropped."""
    points = {name: [] for name in fields}
    for row in rows:
        x = _epoch_ms(row.get("ts"))
        if x is None:
            continue
        for name in fields:
            points[name].append((x, row.get(name)))
    return points


def _counts(rows, field: str, default=None) -> dict:
    """Label frequencies for a pie.

    A missing value is skipped when `default` is None, else counted under `default`.
    """
    out: dict[str, int] = {}
    for row in rows:
        label = row.get(field) or default
        if not label:
            continue
        out[str(label)] = out.get(str(label), 0) + 1
    return out


def build_session_charts(cognitive, face, heart) -> dict:
    """The four charts, keyed by `chart_render.CHART_NAMES`. None where empty.

    Draws untrusted rows too (unlike the rollup): it must match what the reviewer saw.
    """
    charts = {}

    # No `engagement`: it is the focus index under another name.
    cog_points = _line_points(cognitive, ("focus", "stress"))
    charts["cognitive_timeline"] = (
        chart_render.line_svg(cog_points, "Cognitive signals") if cognitive else None
    )

    heart_points = _line_points(heart, ("heart_rate_bpm", "rmssd_ms"))
    charts["heart_rate"] = (
        chart_render.line_svg(heart_points, "Heart rate and HRV") if heart else None
    )

    # "Autonomic arousal", never "Stress": cognitive `stress` is `1 - calm`, a different quantity.
    charts["stress_pie"] = (
        chart_render.pie_svg(_counts(heart, "stress_category", default="unknown"),
                             "Autonomic arousal", chart_render.STRESS_COLOURS)
        if heart else None
    )

    charts["emotion_pie"] = (
        chart_render.pie_svg(_counts(face, "emotion"), "Expression",
                             chart_render.EMOTION_COLOURS)
        if face else None
    )

    return charts


def object_path(user_id: str, session_id: str, chart: str) -> str:
    """`{user_id}/{session_id}/{chart}.svg`; user-id first so an RLS policy could key on it."""
    return f"{user_id}/{session_id}/{chart}.svg"


# PostgREST silently cuts every response at `db-max-rows` (1000), service role included.
_PAGE = 1000

# What session review and the archive read; `raw` (~1 KB a row) stays in the database.
# A column a surface renders must be added here, or it arrives missing.
SIGNAL_COLUMNS = {
    "cognitive_signals": "id, ts, focus, stress",
    "face_signals": "id, ts, emotion",
    "heart_signals": "id, ts, source, heart_rate_bpm, rmssd_ms, stress_category",
}


def read_session_signals(client, session_id: str, since: str | None = None):
    """A session's cognitive, face and heart rows in `ts` order, up to `_ROW_CAP` each.

    The one reader for session review and the archive, so both stop at the same row.
    Paged by id, not position, so a row written or deleted mid-read is neither repeated
    nor skipped; only an empty page ends a table, as a short one may be a lower server cap.
    """
    def rows(table):
        out: list = []
        last_id = None
        while len(out) < _ROW_CAP:
            query = client.table(table).select(SIGNAL_COLUMNS[table]) \
                .eq("session_id", session_id)
            if since:
                query = query.gt("ts", since)
            if last_id is not None:
                query = query.gt("id", last_id)
            page = query.order("id").limit(min(_PAGE, _ROW_CAP - len(out))) \
                .execute().data or []
            if not page:
                break
            out.extend(page)
            last_id = page[-1]["id"]
        return sorted(out, key=lambda r: (str(r.get("ts")), r["id"]))

    # The three tables at once, so review waits for the longest, not the sum.
    with ThreadPoolExecutor(max_workers=3) as pool:
        return tuple(pool.map(rows, SIGNAL_COLUMNS))


def _fetch(client, session_id: str):
    """Service-role, no RLS: authorised by the student's own session close."""
    return read_session_signals(client, session_id)


def archive_session(client, session_id: str, user_id: str, *,
                    only: set[str] | None = None,
                    existing_paths: dict | None = None) -> dict:
    """Render, upload, and record the paths on the session row.

    Returns the `chart_paths` map it wrote; raises (`_run` logs it).
    `only` restricts the re-render; other charts keep their `existing_paths`
    entry, so an erasure's null is never re-rendered from surviving rows.
    """
    cognitive, face, heart = _fetch(client, session_id)
    charts = build_session_charts(cognitive, face, heart)

    paths: dict[str, str | None] = {}
    storage = client.storage.from_(BUCKET)
    for name in chart_render.CHART_NAMES:
        if only is not None and name not in only:
            paths[name] = (existing_paths or {}).get(name)
            continue
        svg = charts.get(name)
        if not svg:
            paths[name] = None
            continue
        path = object_path(user_id, session_id, name)
        storage.upload(
            path=path,
            file=svg.encode("utf-8"),
            # `upsert` must be the string "true": file_options become HTTP headers.
            file_options={"content-type": "image/svg+xml", "upsert": "true"},
        )
        paths[name] = path

    client.table("sessions").update({"chart_paths": paths}) \
        .eq("id", session_id).execute()
    return paths


# ── reading them back ───────────────────────────────────────────────────────

# Signed URLs can't be revoked, so this TTL is the only bound on a leaked one.
SIGNED_URL_TTL_SECONDS = 300


def signed_chart_urls(client, chart_paths, user_id: str,
                      session_id: str) -> tuple[dict, list]:
    """`({chart: url | None}, [charts recorded but unreadable])`.

    Security: the path is derived, never read from `chart_paths` (student-writable);
    that column decides presence only. `None` = nothing drawn; listed = recorded
    but unsignable; absent from both = never attempted.
    """
    urls: dict[str, str | None] = {}
    missing: list[str] = []
    storage = client.storage.from_(BUCKET)
    for name in chart_render.CHART_NAMES:
        if name not in (chart_paths or {}):
            continue
        if not chart_paths[name]:
            urls[name] = None
            continue
        # Presence above is all the stored value is allowed to decide.
        path = object_path(user_id, session_id, name)
        try:
            signed = storage.create_signed_url(path, SIGNED_URL_TTL_SECONDS)
        except Exception as e:
            print(f"[charts] could not sign {path}: {e}")
            missing.append(name)
            continue
        # storage3 returns both spellings; take either.
        url = (signed or {}).get("signedURL") or (signed or {}).get("signedUrl")
        if url:
            urls[name] = url
        else:
            missing.append(name)
    return urls, missing


# ── removing them ───────────────────────────────────────────────────────────

# Paths per `remove` call.
_REMOVE_BATCH = 100


def remove_objects(client, paths) -> tuple[int, list]:
    """Delete archived chart objects. Returns `(removed, failed paths)`.

    Paths must be derived from ids, never read from `chart_paths`. Failure is
    reported, not raised: the database half has already committed.
    """
    paths = [p for p in (paths or []) if p]
    if not paths:
        return 0, []
    storage = client.storage.from_(BUCKET)
    removed, failed = 0, []
    for i in range(0, len(paths), _REMOVE_BATCH):
        batch = paths[i:i + _REMOVE_BATCH]
        try:
            storage.remove(batch)
            removed += len(batch)
        except Exception as e:
            # Loud: the one part of an erasure that can be incomplete.
            print(f"[charts] erasure could not remove {len(batch)} object(s): {e}")
            failed.extend(batch)
    return removed, failed


# ── sweeping objects whose session is gone ──────────────────────────────────
# Deletes on absence, so every guard below aborts on a doubtful read. Objects of
# sessions that still exist are out of scope: the archive outlives expiry on purpose.

_LIST_PAGE = 100
# storage-py pages silently; a backend that ignored `offset` would loop for ever.
_LIST_MAX_PAGES = 1000
_ID_BATCH = 200
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _list_all(storage, prefix: str) -> list[str]:
    """Every entry name under `prefix`, paged: a single `list` silently caps at 100."""
    names: list[str] = []
    for page_no in range(_LIST_MAX_PAGES):
        page = storage.list(prefix, {"limit": _LIST_PAGE,
                                     "offset": page_no * _LIST_PAGE}) or []
        names.extend(e["name"] for e in page if isinstance(e, dict) and e.get("name"))
        if len(page) < _LIST_PAGE:
            return names
    raise RuntimeError(
        f"listing {prefix!r} did not terminate after {_LIST_MAX_PAGES} pages")


def sweep_orphan_charts(client, *, dry_run: bool = True, max_deletes: int = 500,
                        max_orphan_fraction: float = 0.5) -> dict:
    """Remove archived charts whose session row no longer exists.

    Returns a report and never raises for an ordinary failure; when `refused`
    is set nothing was deleted. `dry_run` defaults to True.
    """
    report = {"scanned_sessions": 0, "orphaned_sessions": 0, "removed": 0,
              "failed": [], "unrecognised": 0, "hit_cap": False,
              "dry_run": dry_run, "refused": None}
    storage = client.storage.from_(BUCKET)

    # List the bucket BEFORE reading `sessions`: a session created in between
    # would otherwise look orphaned. This order is stale only in the safe direction.
    try:
        found: list[tuple[str, str]] = []
        for user_id in _list_all(storage, ""):
            if not _UUID.match(user_id):
                report["unrecognised"] += 1
                continue
            for session_id in _list_all(storage, user_id):
                if _UUID.match(session_id):
                    found.append((user_id, session_id))
                else:
                    report["unrecognised"] += 1
    except Exception as e:                                     # noqa: BLE001
        report["refused"] = f"could not list the bucket: {e}"
        return report

    report["scanned_sessions"] = len(found)
    if not found:
        return report

    ids = sorted({s for _, s in found})
    live: set[str] = set()
    try:
        for i in range(0, len(ids), _ID_BATCH):
            batch = ids[i:i + _ID_BATCH]
            rows = (client.table("sessions").select("id")
                    .in_("id", batch).execute().data) or []
            live.update(str(r["id"]) for r in rows if r.get("id"))
    except Exception as e:                                     # noqa: BLE001
        # Refuse: a failed read would make a live bucket look deletable.
        report["refused"] = f"could not read sessions: {e}"
        return report

    orphans = [(u, s) for u, s in found if s not in live]
    report["orphaned_sessions"] = len(orphans)
    if not orphans:
        return report

    # Most of the bucket looking orphaned means a broken read that didn't raise.
    fraction = len(orphans) / len(found)
    if fraction > max_orphan_fraction:
        report["refused"] = (
            f"{len(orphans)} of {len(found)} sessions look orphaned "
            f"({fraction:.0%} > {max_orphan_fraction:.0%}); refusing in case the "
            "sessions read is wrong. Re-run with a higher max_orphan_fraction "
            "once the count has been checked by hand.")
        return report

    if len(orphans) > max_deletes:
        report["hit_cap"] = True
        orphans = orphans[:max_deletes]

    paths: list[str] = []
    try:
        for user_id, session_id in orphans:
            paths.extend(f"{user_id}/{session_id}/{name}"
                         for name in _list_all(storage, f"{user_id}/{session_id}"))
    except Exception as e:                                     # noqa: BLE001
        report["refused"] = f"could not list an orphaned session: {e}"
        return report

    if dry_run:
        report["would_remove"] = len(paths)
        return report

    removed, failed = remove_objects(client, paths)
    report["removed"], report["failed"] = removed, failed
    return report


# ── running it off the request path ─────────────────────────────────────────
# Two workers, lazily built, shut down from `main._lifespan`.

_POOL: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()


def _pool() -> ThreadPoolExecutor:
    global _POOL
    with _pool_lock:
        if _POOL is None:
            _POOL = ThreadPoolExecutor(max_workers=2,
                                       thread_name_prefix="chart-archive")
        return _POOL


def shutdown_pool() -> None:
    """Drop the queue on the way out; pending archives are abandoned, not awaited."""
    global _POOL
    with _pool_lock:
        pool, _POOL = _POOL, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


def _run(client, session_id: str, user_id: str) -> None:
    try:
        paths = archive_session(client, session_id, user_id)
        drawn = sum(1 for v in paths.values() if v)
        print(f"[charts] {session_id[:8]}: archived {drawn}/{len(paths)}")
    except Exception as e:
        # The log is the only place this surfaces; the fix window closes on `ends_on`.
        print(f"[charts] {session_id[:8]}: archive failed: {e}")


def schedule(client, session_id: str, user_id: str) -> None:
    """Queue an archive for a session that has just closed. Never raises, even on submit."""
    try:
        _pool().submit(_run, client, session_id, user_id)
    except Exception as e:
        print(f"[charts] {session_id[:8]}: could not queue archive: {e}")


# ── regenerating archives already written ───────────────────────────────────

def rearchive_sessions(client, sessions: list[dict], *, dry_run: bool = True,
                       max_rerenders: int = 200, max_read_failures: int = 5) -> dict:
    """Re-render and re-upload the charts of sessions already archived.

    Dry run by default. Skips a session whose chart rows have expired (the archive
    is the last copy); re-renders only charts with a recorded path (an erasure's
    null stays null); refuses after `max_read_failures` failed reads.
    """
    report = {"dry_run": dry_run, "considered": 0, "rerendered": 0,
              "skipped_expired": 0, "skipped_unarchived": 0, "failed": 0,
              "read_failures": 0, "refused": None, "hit_cap": False,
              "last_ended_at": None, "would_rerender": []}
    for row in sessions:
        if len(report["would_rerender"]) + report["rerendered"] >= max_rerenders:
            report["hit_cap"] = True
            break
        report["considered"] += 1
        session_id, user_id = row.get("id"), row.get("user_id")
        recorded = row.get("chart_paths") or {}
        wanted = {name for name in chart_render.CHART_NAMES if recorded.get(name)}
        if not wanted or not session_id or not user_id:
            report["skipped_unarchived"] += 1
            # Handled: nothing to read, so the cursor may pass it.
            report["last_ended_at"] = row.get("ended_at")
            continue
        try:
            cognitive, face, heart = _fetch(client, session_id)
        except Exception as exc:  # noqa: BLE001 -- counted, and refused past the cap
            print(f"[rearchive] {session_id}: read failed: {exc}")
            report["read_failures"] += 1
            # Refuse at the cap, not past it, or a run of failures ends looking clean.
            if report["read_failures"] >= max_read_failures:
                report["refused"] = (f"{report['read_failures']} session reads failed; "
                                     "a failed read is indistinguishable from an expired session")
                break
            continue
        present = {name for name, rows in CHART_SOURCES.items()
                   if {"cognitive": cognitive, "face": face, "heart": heart}[rows]}
        # `last_ended_at` is the `--after` resume cursor: advance it only once a
        # session is handled, so a failed read or render is retried next run.
        if not wanted <= present:
            report["skipped_expired"] += 1
            report["last_ended_at"] = row.get("ended_at")
            continue
        if dry_run:
            report["would_rerender"].append(session_id)
            report["last_ended_at"] = row.get("ended_at")
            continue
        try:
            archive_session(client, session_id, user_id, only=wanted, existing_paths=recorded)
            report["rerendered"] += 1
            report["last_ended_at"] = row.get("ended_at")
        except Exception as exc:  # noqa: BLE001 -- one failure must not stop the run
            print(f"[rearchive] {session_id}: {exc}")
            report["failed"] += 1
    return report


# Per-sample table each chart draws on (`stress_pie` is heart, not cognitive `stress`).
CHART_SOURCES = {"cognitive_timeline": "cognitive", "heart_rate": "heart",
                 "stress_pie": "heart", "emotion_pie": "face"}
