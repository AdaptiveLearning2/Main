"""What the stored-question repair scripts share: reading rows, checking for answers, retiring a row."""
from __future__ import annotations

from datetime import datetime, timezone

PAGE = 1000   # PostgREST's db-max-rows; keyset-paged on id so no row is skipped
ANSWER_TABLES = ("session_answers", "practice_session_answers")


def rows(client, subject, columns):
    """Every unretired `questions` row of `subject`, in id order; raises if a read fails."""
    last = None
    while True:
        q = (client.table("questions").select(columns).eq("subject", subject)
             .is_("retired_at", "null").order("id").limit(PAGE))
        page = (q.gt("id", last) if last is not None else q).execute().data or []
        yield from page
        if len(page) < PAGE:
            return
        last = page[-1]["id"]


def answered(client, question_id):
    """True if a lesson or practice answer points at this question; raises if a read fails."""
    return any(client.table(table).select("id").eq("question_id", question_id)
               .limit(1).execute().data for table in ANSWER_TABLES)


def retire(client, question_id):
    """Take a question out of the bank and out of reuse; its text, options and key stay as answered."""
    client.table("questions").update({"retired_at": datetime.now(timezone.utc).isoformat()}) \
        .eq("id", question_id).is_("retired_at", "null").execute()
