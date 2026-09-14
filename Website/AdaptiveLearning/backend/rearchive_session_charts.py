"""Re-render the archived charts of sessions closed before a chart changed.

Archives are written once, at session close, and nothing revisits them. When
what a chart draws changes -- the `engagement` series was dropped from the
cognitive timeline, since it is the focus index under another name -- every
archive written before that keeps the old picture permanently. This is the
regeneration path.

    python rearchive_session_charts.py            # report only
    python rearchive_session_charts.py --apply    # re-render and upload

Only sessions whose per-sample rows still exist are touched: after the
end-of-year delete the archive is the last picture of a session, and
re-rendering from empty tables would replace it with nothing. See
`chart_archive.rearchive_sessions`.
"""

from __future__ import annotations

import argparse
import os
import sys

import chart_archive


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="actually re-render and upload; without it nothing changes")
    ap.add_argument("--limit", type=int, default=1000,
                    help="most sessions to consider in one run (newest first)")
    args = ap.parse_args(argv)

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set", file=sys.stderr)
        return 2

    from supabase import create_client
    client = create_client(url, key)
    sessions = (client.table("sessions").select("id, user_id, chart_paths")
                .not_.is_("chart_paths", "null").not_.is_("ended_at", "null")
                .order("ended_at", desc=True).limit(args.limit).execute().data or [])
    report = chart_archive.rearchive_sessions(client, sessions, dry_run=not args.apply)

    print(f"considered:          {report['considered']}")
    print(f"skipped, unarchived: {report['skipped_unarchived']}")
    print(f"skipped, expired:    {report['skipped_expired']} (archive is the last copy; left alone)")
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
