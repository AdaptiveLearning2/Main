"""The native bridge's handshake: one copy, for the sidecar, the kit's self-test and the capture scripts.

The client proves the bridge before sending the token, since only the token file's reader can answer the
challenge. Standard library only, so a script can use it without the sidecar's dependencies.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import socket

HUNG_UP = "hung up at the challenge"
WRONG_PROOF = "answered the challenge wrongly"
_MAX_PROOF_BYTES = 256


def bridge_token_path(port: int) -> str | None:
    """Where muse_native_bridge writes this port's token; no override, so both sides always agree."""
    base = os.environ.get("LOCALAPPDATA")
    return os.path.join(base, "AdaptiveLearning", f"muse_bridge_{port}.token") if base else None


def read_token(path: str | None) -> str:
    """The token in path, or "" when there is none to read."""
    try:
        with open(path, encoding="ascii") as f:
            return f.read().strip()
    except (OSError, TypeError, UnicodeDecodeError):
        return ""


def authenticate(sock: socket.socket, token: str) -> str | None:
    """Challenge, proof, then AUTH: None once the token is sent, else HUNG_UP or WRONG_PROOF and it is not.

    The proof is read a byte at a time, so nothing sent after it is left in a buffer here; a silent peer
    raises the socket's timeout.
    """
    nonce = secrets.token_hex(32)
    sock.sendall(f"CHALLENGE {nonce}\n".encode("ascii"))
    proof = b""
    while not proof.endswith(b"\n") and len(proof) < _MAX_PROOF_BYTES:
        chunk = sock.recv(1)
        if not chunk:
            break
        proof += chunk
    expected = "PROOF " + hmac.new(token.encode("ascii"), nonce.encode("ascii"), hashlib.sha256).hexdigest()
    # As bytes: a squatter can send anything, and compare_digest raises on a non-ASCII str.
    if not hmac.compare_digest(proof.rstrip(b"\r\n"), expected.encode("ascii")):
        return HUNG_UP if not proof else WRONG_PROOF
    sock.sendall(f"AUTH {token}\n".encode("ascii"))
    return None
