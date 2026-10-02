"""What start.ps1 -Hosted and the student kit must agree on, so one table drives both launchers' tests."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

BACKEND = "https://main-u0ki.onrender.com"
ORIGIN = "https://adaptive.pages.dev"
TOKEN = "Ab3_dEf-" * 5 + "xyz"  # 43 characters of token_urlsafe's alphabet, as start.ps1 makes

# (change from the good backend, origin and token; the fields refused)
ADDRESS_CASES = [
    ({}, set()),
    ({"backend": BACKEND + "/", "origin": ORIGIN + "/"}, set()),
    ({"backend": "HTTPS://Main-u0ki.onrender.com:443/"}, set()),
    ({"backend": "http://main-u0ki.onrender.com"}, {"backend"}),
    ({"backend": BACKEND + "/api"}, {"backend"}),
    ({"backend": BACKEND + "/?debug=1"}, {"backend"}),
    ({"backend": BACKEND + "/#top"}, {"backend"}),
    ({"backend": ""}, {"backend"}),
    ({"backend": "https://user:pw@main-u0ki.onrender.com"}, {"backend"}),
    ({"origin": ORIGIN + "/login"}, {"origin"}),
    ({"origin": "http://adaptive.pages.dev"}, {"origin"}),
    ({"origin": "https://user@adaptive.pages.dev"}, {"origin"}),
    ({"token": ""}, {"token"}),
    ({"token": "replace-me-learner-token"}, {"token"}),
    ({"token": "Replace-Me-Learner-Token"}, {"token"}),
    ({"token": "has a space"}, {"token"}),
    # Characters .NET's Uri refuses in a host, where urlsplit takes anything: a CORS mismatch on every machine.
    ({"origin": "https://*"}, {"origin"}),
    ({"origin": "https://*.pages.dev"}, {"origin"}),
    ({"origin": ORIGIN + "\\"}, {"origin"}),
    ({"origin": "https://adaptive pages.dev"}, {"origin"}),
    ({"backend": BACKEND + ";"}, {"backend"}),
    ({"backend": "https://main_u0ki.onrender.com"}, set()),
]

# As a browser sends an origin, lower case and with no default port: CORS compares the strings exactly.
NORMALISED = [
    ("HTTPS://Main-u0ki.onrender.com/", BACKEND),
    ("HTTPS://Adaptive.Pages.Dev:443/", ORIGIN),
    ("https://adaptive.pages.dev:8443", "https://adaptive.pages.dev:8443"),
]

_BRIDGE_SRC = "".join(p.read_text(encoding="utf-8", errors="ignore")
                      for p in (ROOT / "EEGResearch" / "native_bridge" / "src").rglob("*") if p.is_file())
# Direct getenv names plus every "MUSE_*" literal: a name read through a helper (env_flag_off) has no getenv beside it.
BRIDGE_VARS = sorted((set(re.findall(r'getenv\("([A-Z_]+)"\)', _BRIDGE_SRC))
                      | set(re.findall(r'"(MUSE_[A-Z_]+)"', _BRIDGE_SRC))) - {"LOCALAPPDATA"})
