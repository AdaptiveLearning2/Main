# Initial Build Checklist

## Foundations

- [ ] Confirm Muse S Athena data access path in Python (SDK bridge and transport).
- [ ] Finalize high-level system design for ingestion, processing, adaptation, and frontend API contract.
- [ ] Define non-medical educational boundaries and user messaging.
- [ ] Keep backend-first scope clear (no production UI dependency for core validation).

## Security and privacy (post-beta, deferred)

Beta does **not** track full security hardening. Schedule these before any public or sensitive deployment:

- [ ] Threat-model end-to-end data flow.
- [ ] Production-grade auth, RBAC, and session handling.
- [ ] TLS, encryption at rest/in transit, and key management.
- [ ] Data retention and deletion policy for EEG data.
- [ ] Dependency and supply-chain scanning in CI.
- [ ] Replace dev-token and websocket query-parameter patterns with production-appropriate mechanisms.

## Real-Time Product Behavior

- [ ] Build low-latency EEG ingestion service.
- [ ] Implement filtering, artifact handling, and feature extraction.
- [x] Define confidence-scored interpretation rules.
- [x] Implement adaptive question selection with safeguards against noisy data.
- [x] Expose user-friendly reason labels and adaptation action in API payload.
- [x] Add payload contract versioning and signal quality field.

## Quality

- [x] Add unit and integration tests for ingestion and adaptation logic.
- [ ] Add security tests and pre-release penetration testing (post-beta, before production).
- [x] Define incident response and monitoring alerts.
