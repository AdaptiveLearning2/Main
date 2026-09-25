"""Stored graph comparisons are re-scored from their text; only a wrong answer is rewritten."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import repair_graph_comparisons as repair  # noqa: E402

BARS = {"type": "bar_chart", "bars": [{"label": "cats", "value": 6}, {"label": "dogs", "value": 4},
                                      {"label": "fish", "value": 3}]}


def _row(id_, text, answer, figure=BARS):
    return {"id": id_, "subject": "graphs", "question_text": text, "correct_answer": answer,
            "options": [answer, "1", "3", "5"], "figure": figure}


class _Questions:
    """`questions` as the script reads it: filtered, ordered, keyset-paged; updates recorded.

    `answers` maps an answer table to its rows; each row names a `question_id`.
    """

    def __init__(self, rows, answers=None):
        self.rows, self.updates, self.pages = rows, [], 0
        self.answers = {"session_answers": [], "practice_session_answers": [], **(answers or {})}

    def table(self, name):
        if name == "questions":
            return _Query(self, self.rows)
        return _Query(self, self.answers[name], counted=False)


class _Query:
    def __init__(self, db, rows, counted=True):
        self.db, self.rows, self.counted = db, rows, counted
        self.filters, self.n, self.patch = [], None, None

    def select(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.filters.append(lambda r, c=col, v=val: r.get(c) == v)
        return self

    def gt(self, col, val):
        self.filters.append(lambda r, c=col, v=val: r[c] > v)
        return self

    def limit(self, n):
        self.n = n
        return self

    def update(self, patch):
        self.patch = patch
        return self

    def execute(self):
        rows = sorted((r for r in self.rows if all(f(r) for f in self.filters)),
                      key=lambda r: r["id"])
        if self.patch is not None:
            self.db.updates.append((rows[0]["id"], self.patch))
            return type("R", (), {"data": rows})()
        self.db.pages += self.counted
        return type("R", (), {"data": rows[:self.n]})()


def test_a_stored_answer_scored_the_wrong_way_round_is_found_and_fixed():
    db = _Questions([
        _row("a", "How many more cats than dogs are there?", "2"),            # right
        _row("b", "How many more dogs than fish are there?", "3"),            # stored cats - fish
        _row("c", "How many more dogs than cats are there?", "2"),            # false premise
        _row("d", "How many bars are there altogether?", "13"),               # not a comparison
    ])
    report = repair.repair(db, dry_run=False)
    assert report["ok"] == 1 and report["skip"] == 1
    assert report["fix"] == ["b"] and report["unanswerable"] == ["c"]
    (id_, patch), = db.updates
    assert id_ == "b" and patch["correct_answer"] == "1"
    assert "1" in patch["options"] and len(set(patch["options"])) == 4


@pytest.mark.parametrize("table", ["session_answers", "practice_session_answers"])
def test_an_answered_question_is_reported_and_never_rewritten(table):
    """An answer stores an option's position; reshuffled options would repoint it."""
    db = _Questions([_row("b", "How many more dogs than fish are there?", "3"),
                     _row("e", "How many more dogs than fish are there?", "3")],
                    answers={table: [{"id": "ans1", "question_id": "b"}]})
    report = repair.repair(db, dry_run=False)
    assert report["answered"] == ["b"] and report["fix"] == ["e"]
    assert [id_ for id_, _ in db.updates] == ["e"]


def test_a_failed_answer_read_raises_before_anything_is_written():
    class Broken(_Questions):
        def table(self, name):
            if name == "practice_session_answers":
                raise RuntimeError("down")
            return super().table(name)
    db = Broken([_row("b", "How many more dogs than fish are there?", "3")])
    with pytest.raises(RuntimeError):
        repair.repair(db, dry_run=False)
    assert db.updates == []


def test_a_dry_run_writes_nothing():
    db = _Questions([_row("b", "How many more dogs than fish are there?", "3")])
    assert repair.repair(db)["fix"] == ["b"]
    assert db.updates == []


def test_every_row_is_read_past_the_row_cap(monkeypatch):
    """db-max-rows cuts a read silently; keyset paging must reach the rest."""
    monkeypatch.setattr(repair.repair_common, "PAGE", 2)
    db = _Questions([_row(f"r{i}", "How many more dogs than fish are there?", "3") for i in range(5)])
    assert len(repair.repair(db)["fix"]) == 5
    assert db.pages == 3


def test_a_failed_read_raises_rather_than_reporting_nothing_wrong():
    class Broken(_Questions):
        def table(self, name):
            raise RuntimeError("down")
    with pytest.raises(RuntimeError):
        repair.repair(Broken([]))
