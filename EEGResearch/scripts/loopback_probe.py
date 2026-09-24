"""Stand in for the sidecar: loopback-only HTTP on 8001, CORS open to the caller.

Checks an HTTPS page can reach http://127.0.0.1. From any HTTPS page's console, POST to
http://127.0.0.1:8001/x, then fetch http://neverssl.com/ as the control (must be blocked).
Result in docs/LOOPBACK_FROM_HTTPS.md.
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
        # Must drain the body, or the connection stalls waiting on it.
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
