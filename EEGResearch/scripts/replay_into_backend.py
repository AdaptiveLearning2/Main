"""Feed the wire payloads a live sidecar actually produced through the real
backend endpoint, with only Supabase stubbed out.

Covers what capture_backend.py cannot: that what the sidecar sends is what
`/api/signals/cognitive` and `signal_mapping` actually expect. Everything
between the HTTP body and the database row is real code — the Pydantic model,
the consent gate, the mapper, the 0..100 -> 0..1 conversion.
"""
import json
import os
import sys
import urllib.request

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "stub")
sys.path.insert(0, r"C:\AdaptiveLearning\Website\AdaptiveLearning\backend")

import main  # noqa: E402

captured = json.load(urllib.request.urlopen("http://127.0.0.1:8000/"))
samples = [s for batch in captured for s in batch["samples"]]
print(f"replaying {len(samples)} live sample(s) from {len(captured)} batch(es)")

rows = []


class _Ins:
    def __init__(self, r): self.r = r
    def execute(self): rows.extend(self.r)


class _Tbl:
    def insert(self, r): return _Ins(r)


main.get_user = lambda _r: {"id": "student-1"}
main._verify_session_owner = lambda *_a: None
main._consent = lambda _u: {"eeg_enabled": True, "retrieved": True}
main.supabase = type("S", (), {"table": lambda _s, _n: _Tbl()})()

out = main.ingest_cognitive(
    main.CognitiveBatch(session_id="e2e-session-1", samples=samples), None)
print("endpoint answered:", out)

row = rows[0]
src = samples[0]
print("\nfirst row as it would be stored:")
for k in ("ts", "focus", "engagement", "stress", "alpha", "beta", "theta", "delta", "gamma"):
    print(f"  {k:11} {row[k]}")

fails = []


def check(label, ok):
    print(("  PASS  " if ok else "  FAIL  ") + label)
    if not ok:
        fails.append(label)


print("\nassertions:")
check("focus converted 0..100 -> 0..1",
      abs(row["focus"] - src["features"]["focus_score"] / 100) < 1e-9)
check("stress is 1 - calm, not the calm score",
      abs(row["stress"] - (1 - src["features"]["calm_score"] / 100)) < 1e-9)
check("every score is a ratio in [0, 1]",
      all(0.0 <= row[k] <= 1.0 for k in ("focus", "engagement", "stress")))
check("band powers survived the trip (the snapshot-vs-latest_payload bug)",
      all(row[b] is not None for b in ("alpha", "beta", "theta", "delta", "gamma")))
check("bands match what the sidecar sent",
      all(row[b] == src["bands"][b] for b in ("alpha", "beta", "theta", "delta", "gamma")))
check("raw carries what explains a nulled measurement",
      row["raw"].get("signal_quality") is not None and row["raw"].get("quality_basis") is not None)
check("raw kept the sidecar's own device_id",
      row["raw"].get("device_id") == "default")
check("every sample produced a row", len(rows) == len(samples))

print("\n" + ("ALL PASS" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
