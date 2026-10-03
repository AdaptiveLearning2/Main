"""The batch read functions copy two main.py rules into SQL; these hold each copy to main's."""
import os
import re
from pathlib import Path

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import main  # noqa: E402

MIGRATIONS = Path(__file__).resolve().parents[4] / "supabase" / "migrations"


def _function_sql(name):
    """`(header, body)` of the newest migration defining `public.<name>`, comments stripped."""
    header = re.compile(rf'CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+"public"\."{name}"\s*\(', re.I)
    texts = [re.sub(r"--[^\n]*", "", p.read_text("utf-8")) for p in sorted(MIGRATIONS.glob("*.sql"))]
    defining = [t for t in texts if header.search(t)]
    assert defining, f"no migration defines {name}"
    sql = defining[-1]
    start = header.search(sql).start()
    opened = sql.index("$$", start)
    return sql[start:opened], sql[opened + 2:sql.index("$$", opened + 2)]


def test_last_activity_for_sessions_counts_what_main_counts_as_activity():
    """Each table's timestamp and measurement columns equal `_ACTIVITY_SOURCES`, both ways.

    Limit: it reads which columns gate each table, not how they combine (OR) or whether the
    filter takes effect; the fixtures in `scripts/assert_signal_rls.sql` test the behaviour.
    """
    _, body = _function_sql("last_activity_for_sessions")
    reads = {}
    # One LATERAL subquery per table, up to the next.
    for chunk in re.split(r"\bLATERAL\b", body, flags=re.I)[1:]:
        table, alias = re.search(r'FROM\s+"public"\."(\w+)"\s+(\w+)', chunk).groups()
        ts = re.search(rf"(?:max\(\s*|ORDER\s+BY\s+){alias}\.(\w+)", chunk, re.I).group(1)
        measured = set(re.findall(rf"\b{alias}\.(\w+)\s+IS\s+NOT\s+NULL", chunk, re.I))
        assert table not in reads, f"{table} is read twice"
        reads[table] = (ts, measured)
    assert reads == {t: (c, set(m)) for t, c, m in main._ACTIVITY_SOURCES}


def test_recent_sessions_for_users_returns_the_client_columns():
    """Its rows reach a browser, so its columns are `_SESSION_CLIENT_COLUMNS`, no more."""
    header, _ = _function_sql("recent_sessions_for_users")
    returned = re.search(r"RETURNS\s+TABLE\s*\((.*?)\)\s*LANGUAGE", header, re.S | re.I)
    assert returned, "recent_sessions_for_users must name its columns (RETURNS TABLE)"
    names = [c.split()[0].strip('"') for c in returned.group(1).split(",")]
    assert sorted(names) == sorted(c.strip() for c in main._SESSION_CLIENT_COLUMNS.split(","))
