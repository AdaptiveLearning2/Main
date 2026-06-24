# Security Policy

## Beta scope (current)

The project is in **beta focused on EEG signal processing and API shape**, not on production security posture. Expect simple dev tokens, local-oriented defaults, and **no claim** of readiness for public internet exposure.

A fuller security program (TLS, hardened auth, audits, formal data governance) is **planned after beta**, not a current deliverable.

## Supported Versions

Security patches are applied to the latest `main` branch when the project adopts a formal security maintenance process.

## Reporting a Vulnerability

If you discover a serious issue, report it privately to the maintainers rather than filing a public issue. Include reproduction steps and impact.

## After beta (reference checklist)

When moving toward production or public deployment, revisit:

- HTTPS/TLS, secrets management, and dependency/update policy
- Strong authentication and authorization for all sensitive routes
- Data retention, encryption at rest/in transit, and privacy compliance
- Replacing dev-token and query-parameter patterns with production-appropriate mechanisms

## Non-medical use

Adaptation and EEG-derived labels are for **educational feedback only**, not medical diagnosis.
