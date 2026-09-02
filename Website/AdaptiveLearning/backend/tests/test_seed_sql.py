"""`supabase/seed.sql` has to survive running *after* the migrations.

`supabase db reset` applies every migration and then seeds. Several migrations
now insert `math_topics` rows themselves, so they take ids from the sequence
before this file runs -- and the seed's explicit `(1, 'ordering')` collided
with them. The reset died part way through seeding, leaving a local database
with four topics and no users, which reads as a corrupt checkout rather than
as a seed that needs regenerating.

This is a source check because nothing else can catch it: CI's migration job
applies migrations to an empty stack and never runs the seed, so the failure
only ever appeared on a developer's machine.

It matters most because the file is *regenerated* -- `supabase db dump --local
--data-only` writes explicit ids by default, so the next person to refresh it
reintroduces the bug unless they know not to.
"""
import os
import re

SEED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "..", "..", "..", "supabase", "seed.sql")


def _seed():
    with open(SEED, encoding="utf-8") as handle:
        return handle.read()


def test_the_seed_does_not_assign_topic_ids_itself():
    """Ids here have to come from the sequence, not from the file. Nothing
    references `math_topics.id` -- `record_topic_attempt` joins on
    `topic_name`, and the one foreign key to it, `user_math_performance`, is
    not seeded -- so the values were never load-bearing, only conflicting.
    """
    match = re.search(r'INSERT INTO "public"\."math_topics" \(([^)]*)\)', _seed())
    assert match, "the math_topics insert moved or changed shape -- re-check this rule"
    columns = [c.strip().strip('"') for c in match.group(1).split(",")]
    assert columns == ["topic_name"], (
        f"seed.sql assigns math_topics ids ({columns}). Migrations seed this "
        "table too and run first, so explicit ids collide and `supabase db "
        "reset` dies part way through seeding.")


def test_the_topic_insert_tolerates_rows_that_already_exist():
    """The migrations may have inserted the same topic already -- `patterns`
    and `missing_number` are seeded by both, since this file predates them."""
    seed = _seed()
    start = seed.index('INSERT INTO "public"."math_topics"')
    statement = seed[start:seed.index(";", start)]
    assert "ON CONFLICT" in statement, (
        "the math_topics insert must tolerate rows the migrations already put "
        "there, or a reset fails on the topics seeded by both")


def test_the_topic_sequence_is_derived_rather_than_hardcoded():
    """A literal `setval(..., 10)` was right only while this file was the sole
    writer. With migrations seeding topics too the sequence is already past 10,
    and winding it back makes the *next* insert collide -- the same failure one
    step later, and harder to trace because the reset itself succeeds."""
    seed = _seed()
    setval = re.search(r'setval\(\s*\'"public"\."math_topics_id_seq"\'[^;]*;', seed)
    assert setval, "the math_topics setval moved -- re-check this rule"
    assert "MAX(" in setval.group(0).upper(), (
        f"setval must be derived from the table: {setval.group(0)[:120]}")
