"""The sweep's command line, which is a monitoring contract.

`chart_archive.sweep_orphan_charts` has a test per guard. This is the layer
above: the exit code, which is the only thing a scheduled run communicates. It
had no tests, and that is exactly where a defect sat -- a failed batch was
printed to stderr and then returned 0, so a cron job watching status would have
called an incomplete sweep clean.

The codes are distinct because they want different responses. A refusal means
nothing happened and re-running changes nothing until someone looks; a partial
failure means work happened and a re-run retries it.
"""

import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import chart_archive  # noqa: E402
import sweep_orphan_charts  # noqa: E402


def _report(**over):
    base = {"scanned_sessions": 3, "orphaned_sessions": 1, "removed": 2,
            "failed": [], "unrecognised": 0, "hit_cap": False,
            "dry_run": False, "refused": None}
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _no_real_client(monkeypatch):
    """`main` builds a client before doing anything; never reach a network."""
    import supabase
    monkeypatch.setattr(supabase, "create_client", lambda *_a, **_k: object())
    monkeypatch.setenv("SUPABASE_URL", "http://localhost:54321")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-key")


def _run(monkeypatch, report, argv=("--apply",)):
    monkeypatch.setattr(chart_archive, "sweep_orphan_charts",
                        lambda *_a, **_k: report)
    return sweep_orphan_charts.main(list(argv))


def test_a_clean_sweep_exits_zero(monkeypatch):
    assert _run(monkeypatch, _report()) == 0


def test_a_refusal_exits_one(monkeypatch):
    assert _run(monkeypatch, _report(refused="could not read sessions: boom")) == 1


def test_missing_configuration_exits_two(monkeypatch):
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    assert sweep_orphan_charts.main(["--apply"]) == 2


def test_objects_that_could_not_be_deleted_exit_nonzero(monkeypatch, capsys):
    """The defect this file was written for. `remove_objects` reports a failed
    batch rather than raising, so an incomplete sweep is a real state -- and it
    was printed to stderr and then returned 0, which is the exit-code contract
    failing in the one case it exists for."""
    code = _run(monkeypatch, _report(removed=1, failed=["u/s/heart.svg"]))

    assert code != 0, "an incomplete sweep reported success"
    assert code == 3, f"expected the partial-failure code, got {code}"
    assert "failed" in capsys.readouterr().err


def test_a_partial_failure_is_distinguishable_from_a_refusal(monkeypatch):
    """Same alert, different response: a refusal needs a human before the next
    run does anything, a partial failure just needs the next run."""
    failed = _run(monkeypatch, _report(failed=["u/s/heart.svg"]))
    refused = _run(monkeypatch, _report(refused="could not list the bucket: x"))

    # Both halves, or this passes on `failed == 0` -- which differs from 1 while
    # being exactly the bug. Distinctness alone is not the property wanted.
    assert failed != 0, "a partial failure reported success"
    assert refused != 0, "a refusal reported success"
    assert failed != refused, "the two states are indistinguishable to a monitor"


def test_hitting_the_cap_is_not_an_alert(monkeypatch, capsys):
    """Normal operation -- the next run finishes the work. Exiting non-zero
    would train whoever reads the alerts to ignore this job."""
    code = _run(monkeypatch, _report(hit_cap=True))

    assert code == 0
    assert "Re-run to continue" in capsys.readouterr().out


def test_a_failure_still_shows_through_a_hit_cap(monkeypatch):
    assert _run(monkeypatch, _report(hit_cap=True, failed=["u/s/x.svg"])) == 3


def test_a_dry_run_reports_without_deleting_and_exits_zero(monkeypatch, capsys):
    code = _run(monkeypatch, _report(dry_run=True, removed=0, would_remove=4),
                argv=())

    assert code == 0
    out = capsys.readouterr().out
    assert "would remove:       4" in out
    assert "--apply" in out, "a dry run did not say how to act on it"


def test_unrecognised_paths_are_surfaced_rather_than_silently_skipped(
        monkeypatch, capsys):
    _run(monkeypatch, _report(unrecognised=2))

    assert "left alone" in capsys.readouterr().out
