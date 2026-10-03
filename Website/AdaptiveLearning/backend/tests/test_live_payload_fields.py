"""The live monitor's payload fields: one list, held equal to the SQL that builds them."""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import main  # noqa: E402

_ROOT = Path(__file__).resolve().parents[4]
_MIGRATIONS = _ROOT / "supabase" / "migrations"
# Vitest's LivePayloadFields.test.jsx holds Live.jsx's reads to the same file.
_FIELDS = (_ROOT / "Website" / "AdaptiveLearning" / "frontend" / "src" / "test" / "fixtures"
           / "livePayloadFields.json")
_HEADER = re.compile(
    r'CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+"public"\."latest_signals_for_sessions"\s*\(', re.I)
# Comments and whitespace are dropped; a quoted literal is one token, so its commas are inert.
_TOKEN = re.compile(r"""\s+|--[^\n]*|'(?:[^']|'')*'|"(?:[^"]|"")*"|\$\$|::|\w+|.""", re.S)
_KEY = re.compile(r"'(\w+)'")


def _fields():
    return json.loads(_FIELDS.read_text(encoding="utf-8"))


def _tokens(sql):
    return [t for t in _TOKEN.findall(sql) if not t.isspace() and not t.startswith("--")]


def _args(tokens, i):
    """The top-level arguments of the call whose `(` is `tokens[i]`, and the index after its `)`."""
    depth, args, cur = 0, [], []
    for j in range(i, len(tokens)):
        t = tokens[j]
        if t == "(":
            depth += 1
            if depth == 1:
                continue
        elif t == ")":
            depth -= 1
            if depth == 0:
                return args + [cur], j + 1
        elif t == "," and depth == 1:
            args.append(cur)
            cur = []
            continue
        cur.append(t)
    raise AssertionError("unclosed call in latest_signals_for_sessions")


def _object_keys(args, path, out):
    """`out[path]` = the keys of one jsonb_build_object call; a nested call lands at `path.key`."""
    assert len(args) % 2 == 0, f"{path}: jsonb_build_object with an odd argument count"
    assert path not in out, f"{path}: built twice"
    out[path] = []
    for key, value in zip(args[::2], args[1::2]):
        name = len(key) == 1 and _KEY.fullmatch(key[0])
        assert name, f"{path}: a key that is not a plain literal: {key}"
        out[path].append(name.group(1))
        if [t.lower() for t in value[:2]] == ["jsonb_build_object", "("]:
            nested, end = _args(value, 1)
            assert end == len(value), f"{path}.{name.group(1)}: more than one object"
            _object_keys(nested, f"{path}.{name.group(1)}", out)


def _sql_fields():
    """Payload keys per channel, from the newest definition of latest_signals_for_sessions.

    A tokenizer, not a SQL parser: per UNION ALL branch it reads the `'channel'::text` literal and
    the top-level jsonb_build_object's literal keys. A payload built any other way (to_jsonb, a
    CASE, a computed key) yields no keys or a refused key, so the floors fail rather than pass.
    """
    defining = [p for p in sorted(_MIGRATIONS.glob("*.sql"))
                if _HEADER.search(p.read_text(encoding="utf-8"))]
    assert defining, "no migration defines latest_signals_for_sessions"
    sql = defining[-1].read_text(encoding="utf-8")
    tokens = _tokens(sql[[m.end() for m in _HEADER.finditer(sql)][-1]:])
    start = tokens.index("$$") + 1
    body = tokens[start:tokens.index("$$", start)]

    out, channel, branches, depth, i = {}, None, 1, 0, 0
    while i < len(body):
        t = body[i]
        if t == "(":
            depth += 1
        elif t == ")":
            depth -= 1
        elif depth == 0 and t.upper() == "UNION":
            branches += 1
            channel = None
        elif depth == 0 and _KEY.fullmatch(t) and body[i + 1:i + 3] == ["::", "text"]:
            channel = _KEY.fullmatch(t).group(1)
        elif depth == 0 and t.lower() == "jsonb_build_object" and body[i + 1] == "(":
            assert channel, f"{defining[-1].name}: a payload with no channel literal before it"
            args, i = _args(body, i + 1)
            _object_keys(args, channel, out)
            continue
        i += 1
    channels = [k for k in out if "." not in k]
    assert len(channels) == branches, (
        f"{defining[-1].name}: {branches} UNION branches but {len(channels)} payloads read")
    return out, defining[-1].name


def test_the_live_field_list_is_what_latest_signals_for_sessions_builds():
    """`livePayloadFields.json` is the SQL's payload, key for key, nested `raw` included."""
    sql, source = _sql_fields()
    # Floors: an extraction that read nothing would otherwise equal an emptied list.
    assert set(k for k in sql if "." not in k) == set(main._SIGNAL_CHANNELS), (
        f"{source}: channels {sorted(sql)} are not the ones _latest_signals_many keeps")
    assert all(sql.values()) and sum(map(len, sql.values())) >= len(main._SIGNAL_CHANNELS) * 2
    for path, keys in sql.items():
        assert len(keys) == len(set(keys)), f"{source}: {path} names a key twice"
    assert {k: sorted(v) for k, v in sql.items()} == {k: sorted(v) for k, v in _fields().items()}, (
        f"{source} and livePayloadFields.json disagree: change both, and Live.jsx if it reads one")


class _Recorded(dict):
    """A payload that notes every key `class_live` reads from it."""

    def __init__(self, channel, payload, reads):
        super().__init__(payload)
        self._note = lambda key: reads.setdefault(channel, set()).add(key)

    def get(self, key, default=None):
        self._note(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self._note(key)
        return super().__getitem__(key)

    def __contains__(self, key):
        self._note(key)
        return super().__contains__(key)


def test_class_live_reads_only_listed_payload_fields(monkeypatch):
    """The answer payload stops at `class_live`, so its reads are checked here rather than in Live."""
    fields, reads = _fields(), {}
    now = datetime.now(timezone.utc).isoformat()
    # Every listed key, recent stamps, so the session is live and nothing closes it.
    latest = {ch: _Recorded(ch, {k: now if k in ("ts", "answered_at") else None
                                 for k in fields[ch]}, reads)
              for ch in main._SIGNAL_CHANNELS}
    session = {"id": "sess-1", "user_id": "stu-1", "started_at": now}
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "teacher-1"})
    monkeypatch.setattr(main, "_verify_class_owner", lambda *_a: None)
    monkeypatch.setattr(main, "_profiles_many", lambda ids: {})
    monkeypatch.setattr(main, "_open_sessions_many", lambda ids: {"stu-1": [session]})
    monkeypatch.setattr(main, "_latest_signals_many", lambda ids: {"sess-1": latest})

    class _Members:
        def table(self, name):
            q = type("Q", (), {})()
            q.select = q.eq = lambda *a, **k: q
            q.execute = lambda: type("R", (), {"data": [{"student_id": "stu-1"}]})()
            return q
    monkeypatch.setattr(main, "supabase", _Members())

    out = main.class_live("class-1", None)
    assert out[0]["active_session"] == session
    assert {ch: sorted(k for k in keys if k not in fields[ch]) for ch, keys in reads.items()} \
        == {ch: [] for ch in reads}
    # A recorder that saw nothing would pass the check above.
    assert set(reads) == set(main._SIGNAL_CHANNELS), f"class_live read no field of {reads}"
