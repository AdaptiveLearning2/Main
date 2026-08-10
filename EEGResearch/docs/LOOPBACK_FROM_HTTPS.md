# Can an HTTPS page reach the sidecar on `http://127.0.0.1:8001`?

**Yes, measured 2026-08-10 on Chromium 148.** This is the check the plan flagged to do *before*
building the browser-to-local-sidecar path, because the fallback if it failed is a tray app — a much
larger change to discover late.

## Why it was in doubt

With the camera on each student's own device, the sidecar is a local per-student process and a
hosted backend has no route to it. Lifecycle control therefore has to come from the browser: the
frontend, served over HTTPS, calls `http://127.0.0.1:8001` directly. That is a plain-HTTP subresource
request from a secure page, which is normally blocked as mixed content.

The exemption relied on is that loopback counts as a *potentially trustworthy origin*, so it is not
treated as mixed content. That is browser policy rather than something the deployment can guarantee,
which is why it was measured rather than assumed.

## What was measured

A stand-in HTTP server on `127.0.0.1:8001` returning permissive CORS headers, and `fetch` from a page
at `https://example.com`:

| Request | Result |
| --- | --- |
| `GET http://127.0.0.1:8001/…` with `Authorization` | **200**, body returned |
| `GET http://localhost:8001/…` | **200**, body returned |
| `POST http://127.0.0.1:8001/…`, `Content-Type: application/json` | **200** — the CORS preflight passed |
| `GET http://neverssl.com/` — *negative control* | **blocked**: `Mixed Content: … has been blocked` |

The negative control is the part that makes this a result. Without it, four successes would be
equally consistent with "this browser does not enforce mixed content at all", which would say nothing
about loopback. Plain HTTP to a non-loopback host from the same page is blocked, and loopback is not:
the exemption is real and specific.

The preflighted POST matters separately. Chrome's Private Network Access work adds a preflight
requirement for public→local requests, and had it been enforced here the POST would have failed while
the simple GETs kept working. It did not, so no `Access-Control-Allow-Private-Network` response
header is needed today.

Browser: `Chrome/148.0.7778.280` (Electron 42, Windows). Not tested on Firefox or Safari.

## What this does and does not settle

- **Settles:** the design is viable. The frontend may call the local sidecar from an HTTPS page, with
  auth headers and JSON bodies, without a tray app.
- **Does not settle:** other browsers, or future Chrome versions. Private Network Access is a
  scheduled change, not a hypothetical, and the failure mode if it lands is the preflight — not the
  request itself. If the local calls start failing after a Chrome update, check for a preflight
  demanding `Access-Control-Allow-Private-Network` before assuming anything about the sidecar.
- **Does not settle:** anything about origins or hosts. `settings.allowed_origins` still has to name
  the hosted frontend origin, and `TrustedHostMiddleware` still has to admit `127.0.0.1`. Mixed
  content and CORS are separate gates and this only clears the first.

Reproduce with `scripts/loopback_probe.py`.
