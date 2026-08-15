"""Remove archived chart objects whose session row is gone (#107).

Storage does not cascade. The signal tables are `ON DELETE CASCADE` from both
`sessions` and `profiles`, but the SVGs in the private `session-charts` bucket
are not reachable from any foreign key, so deleting either leaves them behind.
There is no delete endpoint in `main.py`, which means today those deletes come
from the dashboard or a direct connection -- neither of which the backend can
hook. A sweep is the only shape that catches them.

An orphan is not a leak: `GET /api/signals/session/{id}/charts` resolves the
session row before signing anything, and the bucket has no policies, so only
`service_role` reads it. This is storage that should not exist rather than data
anyone can reach. It stops being fine the moment account deletion becomes a
product feature, because "delete my account" would leave charts of the child
behind.

**Dry run by default.** This deletes on absence, so a failed read of `sessions`
is the one input that makes a healthy bucket look disposable. Look at the
report, then pass `--apply`.

    python sweep_orphan_charts.py                 # report only
    python sweep_orphan_charts.py --apply

Needs `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`, and exits non-zero if the
sweep refused, so a scheduled run surfaces rather than looking like a clean one.
"""

from __future__ import annotations

import argparse
import os
import sys

import chart_archive


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="actually delete; without it nothing is removed")
    ap.add_argument("--max-deletes", type=int, default=500,
                    help="per-run cap; the report says if it was hit")
    ap.add_argument("--max-orphan-fraction", type=float, default=0.5,
                    help="refuse if more than this share of sessions in the "
                         "bucket look orphaned (default 0.5)")
    args = ap.parse_args(argv)

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set",
              file=sys.stderr)
        return 2

    from supabase import create_client
    report = chart_archive.sweep_orphan_charts(
        create_client(url, key),
        dry_run=not args.apply,
        max_deletes=args.max_deletes,
        max_orphan_fraction=args.max_orphan_fraction,
    )

    print(f"sessions in bucket: {report['scanned_sessions']}")
    print(f"orphaned:           {report['orphaned_sessions']}")
    if report["unrecognised"]:
        # Never deleted: an object that does not parse as `{uuid}/{uuid}/...`
        # was put there by something this script does not understand, and
        # deleting what you cannot identify is how a sweep becomes an incident.
        print(f"unrecognised paths: {report['unrecognised']} (left alone)")
    if report["refused"]:
        print(f"REFUSED: {report['refused']}", file=sys.stderr)
        return 1
    if report["dry_run"]:
        print(f"would remove:       {report.get('would_remove', 0)} object(s)")
        print("\nDry run. Re-run with --apply to delete.")
    else:
        print(f"removed:            {report['removed']} object(s)")
        if report["failed"]:
            print(f"failed:             {len(report['failed'])}", file=sys.stderr)
    if report["hit_cap"]:
        print(f"\nHit the {args.max_deletes} cap -- more orphans remain. "
              "Re-run to continue.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
