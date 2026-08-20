"""Remove archived chart objects whose session row is gone.

Storage does not cascade. The signal tables are `ON DELETE CASCADE` from both
`sessions` and `profiles`, but the SVGs in the private `session-charts`
bucket aren't reachable from any foreign key, so deleting either leaves them
behind. There's no delete endpoint in `main.py`; those deletes come from the
dashboard or a direct connection, which the backend can't hook. A sweep is
the only thing that catches them.

An orphan is not a leak: `GET /api/signals/session/{id}/charts` resolves the
session row before signing anything, and only `service_role` reads the
bucket. This is storage that should not exist, not data anyone can reach --
until account deletion becomes a product feature, since "delete my account"
would then leave charts of the child behind.

**Dry run by default.** This deletes on absence, so a failed read of `sessions`
is the one input that makes a healthy bucket look disposable. Look at the
report, then pass `--apply`.

    python sweep_orphan_charts.py                 # report only
    python sweep_orphan_charts.py --apply

Needs `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`. Distinct exit codes, so
a scheduled run surfaces trouble instead of looking clean, and each code gets
a different response:

    0  clean, or a dry run, or the per-run cap was hit
    1  refused: nothing was deleted, and re-running changes nothing until
       whatever caused the refusal is looked at
    2  not configured
    3  some objects could not be deleted: work happened and was incomplete,
       so re-running is the right response

Hitting the cap exits 0 deliberately: it's normal operation (the next run
finishes the work), and exiting non-zero would train whoever reads the
alerts to ignore this job.
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
        # Never deleted: an object that doesn't parse as `{uuid}/{uuid}/...`
        # was put there by something this script doesn't understand, and
        # deleting what you can't identify is how a sweep becomes an incident.
        print(f"unrecognised paths: {report['unrecognised']} (left alone)")
    if report["refused"]:
        print(f"REFUSED: {report['refused']}", file=sys.stderr)
        return 1

    code = 0
    if report["dry_run"]:
        print(f"would remove:       {report.get('would_remove', 0)} object(s)")
        print("\nDry run. Re-run with --apply to delete.")
    else:
        print(f"removed:            {report['removed']} object(s)")
        if report["failed"]:
            # `remove_objects` reports a failed batch rather than raising, so
            # this is a real state. Returning 0 here would break the whole
            # exit-code contract in the one case it exists for: a cron job
            # watching status would call an incomplete sweep clean.
            print(f"failed:             {len(report['failed'])}", file=sys.stderr)
            code = 3
    if report["hit_cap"]:
        # Still 0: hitting the cap is normal operation, and the next run
        # finishes the work; alerting on it would train an operator to
        # ignore this job.
        print(f"\nHit the {args.max_deletes} cap -- more orphans remain. "
              "Re-run to continue.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
