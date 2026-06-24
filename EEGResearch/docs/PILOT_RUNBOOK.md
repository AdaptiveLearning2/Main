# Pilot Runbook

## Pre-Flight Checks

- Verify `.env` is configured with non-default tokens.
- Confirm host is local/internal only.
- Run `pytest` and ensure all tests pass.
- Start combined flow: `.\scripts\run_and_watch.ps1 -LearnerToken "<learner>" -AdminToken "<admin>"`.

## Session Operations

1. If needed, start stream session with admin token via `POST /api/v1/session/start`.
2. Validate learner state flow through `GET /api/v1/state` (check `status`, `data`, `message`).
3. Validate realtime output through `GET /ws/live?token=<learner_token>`.
4. Validate metrics via `GET /api/v1/metrics` using admin token.
5. Stop stream session with `POST /api/v1/session/stop`.

## Incident Steps

- If stream stalls: stop/start session and check adapter connectivity.
- If token leak suspected: rotate tokens and restart service.
- If adaptation appears unstable: fallback to default question policy and review signal confidence.
- If repeated "Unable to connect to remote server": confirm server is up (`GET /healthz`) and no stale watcher loops are running.

## Go/No-Go Gates (beta)

- Stable stream updates through a full pilot lesson.
- Adaptation explanation remains clear and non-medical.
- Signal pipeline and API contract behave as expected for frontend integration.

**Post-beta:** add security-focused gates (auth, TLS, reviews) before public or sensitive rollout.
