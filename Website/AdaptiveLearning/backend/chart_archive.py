"""Render a closed session's charts and put them in private storage.

`chart_render.py` (part 1) turns a payload into SVG. This is the half that
decides *what* payload, *when*, and *where it goes* -- and, mostly, what must
not happen when it fails.

Three properties are load-bearing:

- **Out of band.** A storage failure must not fail the session-end request. The
  student's session record, their stats and the daily rollup are all written by
  then; an archive is derived data and the least important thing in that
  endpoint. So the work is handed to a small pool and the request returns.
- **Logged, never swallowed into a 200.** The reporting helpers deliberately
  degrade to an empty payload, because a broken dashboard read costs a view. The
  opposite applies here: this is the copy that survives the end-of-year delete,
  so a failure has to be visible to whoever can fix it before `ends_on`.
- **Idempotent.** Uploads use upsert and the paths are derived from the ids, so
  a replayed close overwrites the same four objects. A close that runs twice --
  the `/end` endpoint and then the stale-session sweep, say -- must not leave two
  archives of one session.

**A channel that recorded nothing gets no object.** Not an empty chart: an empty
chart asserts the session had that channel and it read flat, which is exactly the
absence-rendered-as-data failure the reporting rules exist to stop. `chart_paths`
carries the null instead, and the migration documents the four states it can be
in.
"""

from __future__ import annotations

import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import chart_render

BUCKET = "session-charts"

# What the read is capped at, matching `/api/signals/session/{id}` exactly.
# Shared on purpose: the archive is meant to be what the reviewer saw, so the
# two have to truncate a very long session at the same place. A larger cap here
# would archive a chart nobody was ever shown.
_ROW_CAP = 20000


def _epoch_ms(value) -> float | None:
    """A timestamp as milliseconds, or None for anything unparseable.

    Deliberately not `main._parse_ts`. That one returns a datetime and logs
    against the consent path, where an unparseable stamp suppresses a notice a
    student needs to see. Here a bad stamp costs one point on one chart, so it
    is dropped quietly -- and `chart_render` already breaks the line at a gap
    rather than bridging it, so the loss reads as a gap and not as a reading.
    """
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
    """`{field: [(x, y), ...]}` for the named fields, x in epoch ms.

    A row with no usable timestamp is dropped rather than placed at zero, which
    would drag the axis back to 1970 and flatten the whole session against the
    right-hand edge.
    """
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

    Rows whose value is missing are skipped when `default` is None -- a rejected
    facial window is not a reading, and counting it as an emotion would inflate
    every slice against a session that mostly failed its quality gate. Where a
    default is given (heart, whose `unknown` bucket the live view draws) it is
    counted under that name instead, because *there* the state is real: the
    window produced a heart rate and no stress category.
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

    **Trusted and untrusted rows alike**, unlike `signal_daily_rollup`, which
    averages trusted rows only. The difference is deliberate and it is about what
    each artefact is for: the rollup publishes a number that outlives the
    evidence, so it must not smuggle a rejected reading past the quality gate;
    this is a picture of the session, and it has to match what the reviewer was
    shown or the drift test protecting the two palettes is protecting a
    likeness of nothing.
    """
    charts = {}

    cog_points = _line_points(cognitive, ("focus", "engagement", "stress"))
    charts["cognitive_timeline"] = (
        chart_render.line_svg(cog_points, "Cognitive signals") if cognitive else None
    )

    heart_points = _line_points(heart, ("heart_rate_bpm", "rmssd_ms"))
    charts["heart_rate"] = (
        chart_render.line_svg(heart_points, "Heart rate and HRV") if heart else None
    )

    # "Autonomic arousal", never a bare "Stress". `heart_signals.stress_score`
    # is a measurement against the session's own baseline; `cognitive_signals.
    # stress` is `1.0 - calm` with a sign flip and no independent quantity
    # behind it. One label over both is how a tile comes to change meaning when
    # a headband disconnects.
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
    """`{user_id}/{session_id}/{chart}.svg`.

    User-id first because that prefix is what a storage RLS policy can be
    written against, if one is ever needed. None is today -- the bucket has no
    policies and only `service_role` reaches it -- but a layout that forecloses
    the option is a migration of every object away.
    """
    return f"{user_id}/{session_id}/{chart}.svg"


def _fetch(client, session_id: str):
    """The same three reads `/api/signals/session/{id}` makes, same order, cap.

    Service-role, so RLS does not apply: this runs with no request behind it and
    has already been authorised by the fact that the session belongs to the
    student whose close triggered it.
    """
    def rows(table):
        return client.table(table).select("*").eq("session_id", session_id) \
            .order("ts").limit(_ROW_CAP).execute().data or []

    return rows("cognitive_signals"), rows("face_signals"), rows("heart_signals")


def archive_session(client, session_id: str, user_id: str) -> dict:
    """Render, upload, and record the paths on the session row.

    Returns the `chart_paths` map it wrote, for the tests and for a caller that
    wants to do this synchronously. Raises on a failure it could not contain --
    `_run` below is what turns that into a log line.
    """
    cognitive, face, heart = _fetch(client, session_id)
    charts = build_session_charts(cognitive, face, heart)

    paths: dict[str, str | None] = {}
    storage = client.storage.from_(BUCKET)
    for name in chart_render.CHART_NAMES:
        svg = charts.get(name)
        if not svg:
            paths[name] = None
            continue
        path = object_path(user_id, session_id, name)
        storage.upload(
            path=path,
            file=svg.encode("utf-8"),
            # `upsert` as a string: storage-py passes file_options through as
            # HTTP headers, and a bool becomes "True", which the server does not
            # recognise -- so a replayed close would 409 instead of overwriting.
            file_options={"content-type": "image/svg+xml", "upsert": "true"},
        )
        paths[name] = path

    client.table("sessions").update({"chart_paths": paths}) \
        .eq("id", session_id).execute()
    return paths


# ── reading them back ───────────────────────────────────────────────────────

# Long enough to load a page of four images on a slow connection, short enough
# that a URL copied out of devtools or a shared screenshot is stale by the time
# anyone else tries it. There is no revocation: a signed URL is valid until it
# expires, whatever happens to the student's consent in between, so the TTL is
# the only bound there is on a leaked one -- which is the argument for keeping
# it small rather than convenient.
SIGNED_URL_TTL_SECONDS = 300


def signed_chart_urls(client, chart_paths, user_id: str,
                      session_id: str) -> tuple[dict, list]:
    """`({chart: url | None}, [charts recorded but unreadable])`.

    **The path is derived here, never taken from `chart_paths`.** That column is
    ordinary jsonb on `sessions`, and `sessions` carries a `FOR ALL` own-row
    policy, so a student can PATCH their own row through PostgREST and put any
    string they like in it -- including another student's object path. Signing
    what is stored would hand them a URL to it, through an endpoint whose access
    check had just correctly confirmed they own *the session*. The stored value
    is a record of **which** charts exist; it is not an address to be trusted.

    So `chart_paths` is read for presence only: a truthy value means "this chart
    was rendered and uploaded", and where it points is not consulted. The
    consequence is that changing `object_path`'s scheme means migrating the
    objects -- which was already true, and is noted there.

    Signed rather than public, and issued per request rather than stored. These
    are charts of a named child's cognitive and physiological signals; a public
    object URL is an access-control bypass that no policy added later can undo,
    because a URL that has been shared stays shared.

    The two return values keep three states apart that would otherwise collapse
    into one null:

    * `url` -- rendered, uploaded, still there.
    * `None` in the dict -- the channel produced nothing to draw. The absence is
      the record.
    * named in the second list -- a path *was* recorded and the object could not
      be signed. That is a fault, not an absence, and a reader has to be able to
      tell them apart or a bucket half-emptied by hand reads as a term in which
      nobody wore a headband.

    A chart absent from `chart_paths` altogether is absent from both, meaning
    it was never attempted -- every session closed before archiving shipped.
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
        # Presence, above, is all the stored value is allowed to decide.
        path = object_path(user_id, session_id, name)
        try:
            signed = storage.create_signed_url(path, SIGNED_URL_TTL_SECONDS)
        except Exception as e:
            print(f"[charts] could not sign {path}: {e}")
            missing.append(name)
            continue
        # storage3 returns both spellings; take either rather than depending on
        # which one a future version keeps.
        url = (signed or {}).get("signedURL") or (signed or {}).get("signedUrl")
        if url:
            urls[name] = url
        else:
            missing.append(name)
    return urls, missing


# ── removing them ───────────────────────────────────────────────────────────

# One `remove` call per batch. Storage-py takes a list, and a parent erasing a
# term's worth of sessions would otherwise be a few hundred round trips inside
# one request.
_REMOVE_BATCH = 100


def remove_objects(client, paths) -> tuple[int, list]:
    """Delete archived chart objects. Returns `(removed, failed paths)`.

    Called by the erasure path with a list `erase_signals` derived from the ids
    -- never one read out of `chart_paths`. A path taken from that column would
    be an instruction to destroy an object of the writer's choosing, which is
    the same trap as the signing endpoint with the consequence reversed.

    Failure is reported, not raised. By the time this runs the database half has
    committed and `chart_paths` no longer points at these objects, so anything
    left behind is orphaned but unservable -- the signing endpoint has nothing
    to sign. That is a smaller problem than an erasure that half-rolls-back, and
    it is why the caller can be told a number instead of an exception.
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
            # Loud: this is the one part of an erasure that can be incomplete,
            # and nobody is watching a return value once the parent is gone.
            print(f"[charts] erasure could not remove {len(batch)} object(s): {e}")
            failed.extend(batch)
    return removed, failed


# ── sweeping objects whose session is gone ──────────────────────────────────
#
# Storage does not cascade. Deleting a `sessions` row -- or a `profiles`
# row, which cascades to sessions -- leaves the SVGs behind, and `main.py` has no
# delete endpoint to hook, so today those deletes come from the dashboard or a
# direct connection. A sweep is the only shape that catches them.
#
# It deletes on **absence**, which is the dangerous kind of job: one failed read
# of `sessions` makes every object in the bucket look orphaned. Every guard below
# exists so that failure aborts instead of emptying the bucket, and the default
# is `dry_run=True` for the same reason.
#
# Deliberately *not* in scope: an object belonging to a session that still
# exists. `expire_signal_rows` leaves the archive standing on purpose -- it and
# `signal_daily_rollup` are what survive the year, and that survival is why a
# same-day delete with no grace period is defensible. A sweep that "corrected"
# that would remove the thing making its own schedule safe.

_LIST_PAGE = 100
# storage-py pages silently; a backend that ignored `offset` would loop for ever.
_LIST_MAX_PAGES = 1000
_ID_BATCH = 200
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _list_all(storage, prefix: str) -> list[str]:
    """Every entry name under `prefix`, paged.

    `list` caps at 100 by default and reports no truncation, so a single call is
    a silent cap -- a bucket would look like its first 100 students for ever, and
    the sweep would report "nothing orphaned" about objects it never saw.
    """
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

    Returns a report; never raises for an ordinary failure, because the caller
    is a cron job or an operator and a traceback is a worse answer than a
    refusal that says why. `refused` is the field to read first: when it is set
    nothing was deleted.

    `dry_run` defaults to **True**. A sweep that deletes by default is one
    mistyped argument away from being the bug it exists to fix.
    """
    report = {"scanned_sessions": 0, "orphaned_sessions": 0, "removed": 0,
              "failed": [], "unrecognised": 0, "hit_cap": False,
              "dry_run": dry_run, "refused": None}
    storage = client.storage.from_(BUCKET)

    # The bucket is listed **before** `sessions` is read, and the order is the
    # guard. Read the table first and a session created in between has objects
    # whose id is missing from the snapshot -- deleted as orphans while its row
    # sits there. Listing first can only ever be stale in the safe direction: an
    # object written after the listing is not considered at all.
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
        # Not "treat them as orphaned". A failed read here is precisely the
        # state that makes a live bucket look deletable.
        report["refused"] = f"could not read sessions: {e}"
        return report

    orphans = [(u, s) for u, s in found if s not in live]
    report["orphaned_sessions"] = len(orphans)
    if not orphans:
        return report

    # A handful of orphans is a deleted session. Most of the bucket being
    # orphaned is a broken read that did not raise -- a filtered-out service
    # role, a schema change, a table pointed at the wrong project. Refuse and
    # let a human look, rather than being the fastest way to lose the archive.
    fraction = len(orphans) / len(found)
    if fraction > max_orphan_fraction:
        report["refused"] = (
            f"{len(orphans)} of {len(found)} sessions look orphaned "
            f"({fraction:.0%} > {max_orphan_fraction:.0%}); refusing in case the "
            "sessions read is wrong. Re-run with a higher max_orphan_fraction "
            "once the count has been checked by hand.")
        return report

    if len(orphans) > max_deletes:
        # Reported, not silent: the next run finishes the work, but "removed 500"
        # with nothing saying more was waiting reads as "the bucket is clean".
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
#
# Two workers, lazily built, shut down from `main._lifespan`. Same shape as the
# strategy and live-signals pools next to it, and sized the same way: this work
# is a handful of reads and four small uploads per closing session, and sessions
# close one student at a time. A bigger pool would only buy the ability to hold
# more of a storage outage in memory.

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
    """Drop the queue on the way out. Called from `main._lifespan`.

    Pending archives are abandoned rather than awaited. That is the right trade
    for a shutdown: the rows they summarise are still in the database and still
    have until `ends_on`, so the archive can be rebuilt, while a shutdown that
    blocks on storage is an outage that will not end.
    """
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
        # Loud, and not into a response. Nothing is waiting on this, so the log
        # is the only place it can surface -- and it has to surface, because the
        # window in which it can still be fixed closes on `ends_on`.
        print(f"[charts] {session_id[:8]}: archive failed: {e}")


def schedule(client, session_id: str, user_id: str) -> None:
    """Queue an archive for a session that has just closed. Never raises.

    Never raises including on submit: a pool that has been shut down, or an
    interpreter already tearing down, must not turn a successful session close
    into a 500 over a picture.
    """
    try:
        _pool().submit(_run, client, session_id, user_id)
    except Exception as e:
        print(f"[charts] {session_id[:8]}: could not queue archive: {e}")
