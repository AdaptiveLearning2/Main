"""Re-render the archived charts of sessions closed before a chart changed.

Archives are written once, at session close, and nothing revisits them. When
what a chart draws changes -- the `engagement` series was dropped from the
cognitive timeline, since it is the focus index under another name -- every
archive written before that keeps the old picture permanently. This is the
regeneration path.

    python rearchive_session_charts.py                      # report only
    python rearchive_session_charts.py --before 2026-09-14  # sessions closed before the change
    python rearchive_session_charts.py --before 2026-09-14 --apply

Oldest first, because the archives this exists to fix are the oldest ones.
Guards, all in `chart_archive.rearchive_sessions`: only charts with a
recorded path are re-rendered (an erasure's nulls stay null), a session with
any recorded chart whose rows have expired is skipped (the archive is the
last copy), a run refuses past a few failed reads, and `--max-rerenders`
bounds how many live sessions' objects one run overwrites. The target
project is printed first, since nothing else here tells production from a
local stack.
"""

from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import urlparse

import chart_archive


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="actually re-render and upload; without it nothing changes")
    ap.add_argument("--before", metavar="YYYY-MM-DD",
                    help="only sessions that ended before this date (the change date)")
    ap.add_argument("--after", metavar="ISO-TIMESTAMP",
                    help="resume: only sessions that ended after this instant -- the "
                         "value a capped run printed as its cursor")
    ap.add_argument("--limit", type=int, default=1000,
                    help="most sessions to consider in one run (oldest first)")
    ap.add_argument("--max-rerenders", type=int, default=200,
                    help="most archives one run may overwrite; the report says if it was hit")
    args = ap.parse_args(argv)

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set", file=sys.stderr)
        return 2
    print(f"target: {urlparse(url).netloc}  ({'APPLY' if args.apply else 'dry run'})")

    from supabase import create_client
    client = create_client(url, key)
    query = (client.table("sessions").select("id, user_id, chart_paths, ended_at")
             .not_.is_("chart_paths", "null").not_.is_("ended_at", "null"))
    if args.before:
        query = query.lt("ended_at", args.before)
    if args.after:
        query = query.gt("ended_at", args.after)
    sessions = query.order("ended_at", desc=False).limit(args.limit).execute().data or []
    report = chart_archive.rearchive_sessions(client, sessions, dry_run=not args.apply,
                                              max_rerenders=args.max_rerenders)

    print(f"considered:          {report['considered']}")
    print(f"skipped, unarchived: {report['skipped_unarchived']}")
    print(f"skipped, expired:    {report['skipped_expired']} (archive is the last copy; left alone)")
    if report["read_failures"]:
        print(f"read failures:       {report['read_failures']}", file=sys.stderr)
    if report["refused"]:
        print(f"REFUSED: {report['refused']}", file=sys.stderr)
        return 1
    if report["hit_cap"]:
        print(f"stopped at --max-rerenders {args.max_rerenders}; "
              f"continue with --after {report['last_ended_at']}")
    if report["dry_run"]:
        print(f"would re-render:     {len(report['would_rerender'])}")
        print("\nDry run. Re-run with --apply to re-render.")
        return 0
    print(f"re-rendered:         {report['rerendered']}")
    if report["failed"]:
        print(f"failed:              {report['failed']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
