"""A session that recorded nothing is discarded at close, and only then.

Pressing Connect Headband creates the session, since signals need one to
attach to. That means every *failed* pairing attempt used to leave a row, so
History showed empty practice sessions -- on one afternoon, four of five had
no questions and no samples at all.

This deletes on **absence**, which is the dangerous kind of job, so most of
what's asserted here is the cases where it must *not* fire. A student who wore
a headband and answered nothing is a real session; so is one who answered ten
questions with no headband. Only the row with nothing on either side goes.
"""

import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402

SESSION = "11111111-2222-3333-4444-555555555555"


class _Client:
    """Signal tables that answer with rows, or raise."""

    def __init__(self, populated=(), raises=()):
        self._populated = set(populated)
        self._raises = set(raises)
        self.deleted = []

    def table(self, name):
        client = self
        table_name = name

        class _Q:
            def select(self, *_a):
                return self

            def delete(self):
                self._deleting = True
                return self

            def eq(self, col, value):
                if getattr(self, "_deleting", False):
                    client.deleted.append(value)
                return self

            def limit(self, *_a):
                return self

            def execute(self):
                if table_name in client._raises:
                    raise RuntimeError(f"{table_name} unavailable")
                if getattr(self, "_deleting", False):
                    return type("R", (), {"data": []})()
                rows = [{"session_id": SESSION}] if table_name in client._populated else []
                return type("R", (), {"data": rows})()

        return _Q()


@pytest.fixture
def _client(monkeypatch):
    def _install(**kw):
        c = _Client(**kw)
        monkeypatch.setattr(main, "supabase", c)
        return c
    return _install


def test_a_session_with_nothing_at_all_is_discarded(_client):
    c = _client()

    assert main._discard_if_nothing_recorded(SESSION, 0) is True
    assert c.deleted == [SESSION]


def test_an_answered_session_survives_a_counter_that_says_zero(_client):
    """The counter and the answer rows are written by two separate statements.

    `record_answer` inserts into `session_answers` and then bumps
    `sessions.questions_answered`, with no transaction around the pair. If the
    update fails -- or the read it's computed from fails -- real answers sit
    behind a counter reading 0. Since this job deletes on absence, a database
    hiccup mid-lesson could otherwise delete an answered session for looking
    empty, taking the answers with it.

    The counter is a denormalised cache. The rows are the record.
    """
    c = _client(populated=["session_answers"])

    assert main._discard_if_nothing_recorded(SESSION, 0) is False
    assert c.deleted == []


def test_answering_questions_keeps_it_even_with_no_signals(_client):
    """The student practised. That is the session, headband or not -- but it
    looks empty from the signal tables' side, so a careless implementation
    would delete it."""
    c = _client()

    assert main._discard_if_nothing_recorded(SESSION, 10) is False
    assert c.deleted == []


@pytest.mark.parametrize("table", ["session_answers", "cognitive_signals",
                                   "face_signals", "heart_signals"])
def test_any_one_signal_table_with_a_row_keeps_it(_client, table):
    """A student who wore a headband and answered nothing recorded something
    real. Parametrised over all three tables, since "the loop body is the
    same" is an argument about the code, not about which table it checks."""
    c = _client(populated=[table])

    assert main._discard_if_nothing_recorded(SESSION, 0) is False
    assert c.deleted == []


@pytest.mark.parametrize("table", ["session_answers", "cognitive_signals",
                                   "face_signals", "heart_signals"])
def test_a_failed_read_keeps_the_session(_client, table):
    """The whole reason deleting on absence is dangerous: a table that can't be
    read is not the same as a table with no rows. Treating them the same would
    delete a student's session because the database blinked."""
    c = _client(raises=[table])

    assert main._discard_if_nothing_recorded(SESSION, 0) is False
    assert c.deleted == []


def test_a_failed_delete_reports_that_it_did_not_happen(_client):
    """The caller skips the rollup and archive when this returns True, so a
    delete that silently failed would leave a session with neither."""
    c = _client(raises=["sessions"])

    assert main._discard_if_nothing_recorded(SESSION, 0) is False


def test_a_null_question_count_is_treated_as_none_answered(_client):
    """PostgREST returns None for a column that wasn't selected. The
    stale-session sweep used to select only `id, started_at`, which made every
    stale session look answer-free. It now asks for the column; this pins the
    behaviour it depends on."""
    c = _client()

    assert main._discard_if_nothing_recorded(SESSION, None) is True
