"""Stored shape-fraction questions get the one sentence their stored answer is right for."""
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import repair_shape_fraction_texts as repair  # noqa: E402
import LLM_shape_fractions_generation as shapes  # noqa: E402

FIGURE = {"type": "part_whole", "parts": 4, "shaded": 3}


def _row(id_, text, answer="3/4", figure=FIGURE):
    return {"id": id_, "subject": "shape_fractions", "question_text": text,
            "correct_answer": answer, "figure": figure}


class _Questions:
    """`questions` as the script reads it: filtered, ordered, keyset-paged; updates recorded."""

    def __init__(self, rows):
        self.rows, self.updates, self.pages = rows, [], 0

    def table(self, name):
        assert name == "questions"
        return _Query(self)


class _Query:
    def __init__(self, db):
        self.db, self.filters, self.n, self.patch = db, [], None, None

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
        rows = sorted((r for r in self.db.rows if all(f(r) for f in self.filters)),
                      key=lambda r: r["id"])
        if self.patch is not None:
            self.db.updates.append((rows[0]["id"], self.patch))
            return type("R", (), {"data": rows})()
        self.db.pages += 1
        return type("R", (), {"data": rows[:self.n]})()


def test_a_row_asking_something_else_is_given_the_shaded_question():
    db = _Questions([
        _row("a", shapes.QUESTION_TEXT),
        _row("b", "What fraction of the rectangle is NOT shaded?"),
        _row("c", "What fraction is shaded?", answer="1/4"),                  # not the figure's
        {**_row("d", "x"), "subject": "graphs"},                             # another topic
    ])
    report = repair.repair(db, dry_run=False)
    assert report["ok"] == 1
    assert report["fix"] == ["b"] and report["mismatch"] == ["c"]
    assert db.updates == [("b", {"question_text": shapes.QUESTION_TEXT})]


def test_a_dry_run_writes_nothing():
    db = _Questions([_row("b", "What fraction is not shaded?")])
    assert repair.repair(db)["fix"] == ["b"]
    assert db.updates == []


def test_every_row_is_read_past_the_row_cap(monkeypatch):
    """db-max-rows cuts a read silently; keyset paging must reach the rest."""
    monkeypatch.setattr(repair, "_PAGE", 2)
    db = _Questions([_row(f"r{i}", "What fraction is not shaded?") for i in range(5)])
    assert len(repair.repair(db)["fix"]) == 5
    assert db.pages == 3


def test_a_failed_read_raises_rather_than_reporting_nothing_wrong():
    class Broken(_Questions):
        def table(self, name):
            raise RuntimeError("down")
    with pytest.raises(RuntimeError):
        repair.repair(Broken([]))
