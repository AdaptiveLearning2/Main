# Beta Architecture (Local Pilot)

## Services

- API service: FastAPI endpoints for session control and state retrieval.
- Ingestion adapter: Muse stream source (currently simulated adapter for pilot development).
- Signal processor: smoothing and feature extraction.
- Adaptation engine: maps features to learner state and next-question policy.
- WebSocket stream: pushes latest state at heartbeat intervals.

## Data Flow

1. Admin starts session.
2. Ingestion reads EEG samples.
3. Processor computes focus/calm/confidence plus signal quality.
4. Adaptation maps features to user-facing state and question policy.
5. Learner clients pull snapshot endpoint or subscribe to websocket updates.
6. Both REST and WebSocket return the same envelope (`status`, `data`, `message`).

## Current API Surface

- `POST /api/v1/session/start` (admin token)
- `POST /api/v1/session/stop` (admin token)
- `GET /api/v1/state` (learner token)
- `GET /api/v1/metrics` (admin token)
- `GET /ws/live?token=<learner_token>`

## Contract Notes

- Payload includes `contract_version` for forward compatibility.
- `features` includes `signal_quality` (`good`, `degraded`, `poor`).
- `state.reason` and `question_policy.action` are intended for user-friendly frontend display.

## Access during beta (not full security)

- Simple learner/admin bearer tokens for local development convenience.
- Some middleware defaults (hosts, CORS, headers) exist as sensible baselines, not as a completed security review.

**Post-beta:** replace dev patterns (e.g. websocket query tokens) and harden auth/TLS before any public or sensitive deployment.
