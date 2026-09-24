"""`supabase/seed.sql` has to survive running after migrations that also seed `math_topics`."""
import os
import re

import pytest

SEED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "..", "..", "..", "supabase", "seed.sql")


# seed.sql is gitignored and per-developer, so this is a local guard that skips on CI.
_HAVE_SEED = os.path.exists(SEED)
pytestmark = pytest.mark.skipif(
    not _HAVE_SEED,
    reason="supabase/seed.sql is gitignored and per-developer; absent here")


def _seed():
    with open(SEED, encoding="utf-8") as handle:
        return handle.read()


def test_the_seed_does_not_assign_topic_ids_itself():
    """Nothing seeded references `math_topics.id`, so ids come from the sequence."""
    match = re.search(r'INSERT INTO "public"\."math_topics" \(([^)]*)\)', _seed())
    assert match, "the math_topics insert moved or changed shape -- re-check this rule"
    columns = [c.strip().strip('"') for c in match.group(1).split(",")]
    assert columns == ["topic_name"], (
        f"seed.sql assigns math_topics ids ({columns}). Migrations seed this "
        "table too and run first, so explicit ids collide and `supabase db "
        "reset` dies part way through seeding.")


def test_the_topic_insert_tolerates_rows_that_already_exist():
    seed = _seed()
    start = seed.index('INSERT INTO "public"."math_topics"')
    statement = seed[start:seed.index(";", start)]
    assert "ON CONFLICT" in statement, (
        "the math_topics insert must tolerate rows the migrations already put "
        "there, or a reset fails on the topics seeded by both")


def test_the_topic_sequence_is_derived_rather_than_hardcoded():
    """Winding the sequence back makes the next insert collide, after a reset that succeeded."""
    seed = _seed()
    setval = re.search(r'setval\(\s*\'"public"\."math_topics_id_seq"\'[^;]*;', seed)
    assert setval, "the math_topics setval moved -- re-check this rule"
    assert "MAX(" in setval.group(0).upper(), (
        f"setval must be derived from the table: {setval.group(0)[:120]}")
