# Push ingestion, run end to end

**2026-08-10.** A real sidecar process, `PUSH_ENABLED=true`, `EEG_SOURCE=sim` at 4 Hz, posting over
real HTTP to a stand-in backend that records the wire and answers like the real endpoint (`inserted`
counted server-side, 401 without a bearer token). The captured payloads were then replayed through
the **real** `/api/signals/cognitive` with only Supabase stubbed, so everything between the HTTP body
and the database row — Pydantic model, consent gate, `signal_mapping`, the 0..100 → 0..1
conversion — is the shipping code path.

Both halves are in `scripts/` (`capture_backend.py`, `replay_into_backend.py`).

## What it showed

| | |
| --- | --- |
| Boot with push on | sidecar starts, `/api/v1/push/status` reports `enabled: true, running: false` |
| Sampling → queue → POST | batches of 20 at 4 Hz on a 5 s flush, `session_id` and the student's token on every request |
| Token forwarded | the value handed to `/api/v1/push/start`, not the sidecar's own `API_TOKEN` |
| `bands` on the wire | present — this is the `snapshot()`-vs-`latest_payload` bug, and it is the thing to re-check if EEG rows ever arrive with null band powers |
| Mapping | `focus_score 50.882` → `focus 0.50882`; `stress` = 1 − calm; every score in [0, 1]; 260/260 samples produced rows |

## Outage and recovery

The backend was killed mid-session and left down for ~45 s.

| After | Queued | Recorded | Backoff | Dropped |
| --- | --- | --- | --- | --- |
| 20 s down | 48 | 280 | 10 s | 0 |
| 45 s down | 229 | 280 | 40 s | 0 |
| 45 s back up | 5 | 680 | 0 s | 0 |

The backoff grew 5 → 10 → 20 → 40 rather than retrying at the sample rate — the specific thing the
wake event used to defeat, since a filling queue sets it every few samples. Nothing was dropped: the
400-sample backlog was delivered on recovery, and `last_error` cleared.

`/api/v1/push/stop` flushed a 13-sample tail (delivered as 14, one more tick having landed), cleared
`session_id`, and left `running: false`.

## Two properties checked directly

- **The token is never logged.** `grep` for the token across the sidecar's full run: 0 occurrences.
- **`PUSH_ENABLED=false` refuses.** `/api/v1/push/start` answers 409 naming the setting, and
  `/status` reports `enabled: false` — so a sidecar in the co-located deployment cannot become a
  second writer alongside the backend's poller.

## What this does not cover

- **No real Supabase.** Row-level security, the real consent read, and the `heart_signals` dedupe key
  are exercised by CI's migration job and by unit tests, not here.
- **No headband and no camera.** `EEG_SOURCE=sim`, so only the `cognitive` channel produced samples;
  `heart` and `face` were live but idle. Their shaping is unit-tested and has not run against
  hardware.
- **No browser.** `frontend/src/lib/sidecar.js` was driven by curl, not by the page, so the
  `TOKEN_REFRESHED` re-hand is still only unit-tested.
