"""Remove archived chart objects whose session row is gone.

Dry run by default (`--apply` deletes). Needs `SUPABASE_URL` and
`SUPABASE_SERVICE_ROLE_KEY`. Exit codes: 0 clean / dry run / cap hit (next run
continues), 1 refused (nothing deleted), 2 not configured, 3 some deletes failed.
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
        # Never deleted: a path that isn't `{uuid}/{uuid}/...` is unidentified.
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
            # `remove_objects` reports a failed batch rather than raising.
            print(f"failed:             {len(report['failed'])}", file=sys.stderr)
            code = 3
    if report["hit_cap"]:
        # Still 0: normal operation; the next run finishes the work.
        print(f"\nHit the {args.max_deletes} cap -- more orphans remain. "
              "Re-run to continue.")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
