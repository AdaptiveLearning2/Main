"""Stand in for the sidecar: loopback-only HTTP on 8001, CORS open to the caller.

The question is whether an HTTPS page may reach http://127.0.0.1 at all. Chrome
treats loopback as a potentially-trustworthy origin and exempts it from the
mixed-content block, but that is a browser policy rather than a spec guarantee,
and the fallback if it fails is a tray app -- a much larger change to discover
late. So it gets checked against a real HTTPS page before anything is built on
top of it.

Run this, then open any HTTPS page and paste into its console:

    await fetch("http://127.0.0.1:8001/x", {method: "POST",
        headers: {"Content-Type": "application/json"}, body: "{}"})
    await fetch("http://neverssl.com/")   // the control: must be blocked

The second call is the whole test. Four successes without it are equally
consistent with a browser not enforcing mixed content at all, which would say
nothing about loopback being special. Result: docs/LOOPBACK_FROM_HTTPS.md.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class H(BaseHTTPRequestHandler):
    def _cors(self):
        origin = self.headers.get("Origin", "*")
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Headers", "authorization, content-type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        # Drain the body first, or the connection stalls waiting on it.
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        self.do_GET()

    def do_GET(self):
        body = b'{"ok": true, "who": "sidecar-stand-in"}'
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a):
        pass


ThreadingHTTPServer(("127.0.0.1", 8001), H).serve_forever()
