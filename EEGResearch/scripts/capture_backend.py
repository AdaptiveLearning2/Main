"""Stands in for the website backend's ingest endpoints, recording the wire.

Not a mock of the sidecar's client -- the real sidecar, with a real sampling
loop, posts to this over real HTTP. What it proves is the half no unit test can:
that the process boots with PUSH_ENABLED on, that the stream loop's payloads
reach the push client, and what exactly lands on the wire.

It answers like the real endpoint: `inserted` counted server-side, 401 without a
bearer token, so the client's own accounting is exercised rather than assumed.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CAPTURED = []


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        auth = self.headers.get("Authorization", "")

        if not auth.startswith("Bearer "):
            return self._json(401, {"detail": "Not authenticated"})

        channel = self.path.rsplit("/", 1)[-1]
        samples = body.get("samples", [])
        CAPTURED.append({"channel": channel, "session_id": body.get("session_id"),
                         "token": auth.removeprefix("Bearer "), "samples": samples})
        print(f"[capture] {channel}: {len(samples)} sample(s), session={body.get('session_id')}",
              flush=True)
        self._json(200, {"ok": True, "inserted": len(samples)})

    def do_GET(self):
        # Drain point for the harness: everything seen so far, then exit.
        self._json(200, CAPTURED)
        if self.path == "/__done":
            sys.stderr.flush()

    def _json(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8000), H).serve_forever()
