"""The Ed25519 public keys an update feed may be signed with, and the self-test's own signed feed.

Two keys, either of which can sign: the everyday one, and a recovery one kept offline to replace it. Empty, the
self-test fails, so no kit is built that could never verify an update. Made by `installer/kit_release.py newkey`.
"""

# One key a line, named everyday or recovery: a line holding "key" beside the value reads as a credential to gitleaks.
PUBLIC_KEYS: dict[str, str] = {}

# A throwaway key and a feed it signed, discarded after: the self-test proves verification works in the frozen build.
TEST_SIGNER = "R6RKKR8i7ZLe2vRKC0kntILMyUUz7X+4eHnMkYi3idc="
TEST_FEED = b"""{
  "manifest": "eyJmZWVkIjogImxhdGVzdCIsICJmaWxlIjogIkFkYXB0aXZlTGVhcm5pbmdTZW5zb3JzLVVwZGF0ZS0xLjAuMC5leGUiLCAiaGlzdG9yeSI6IFt7ImZpbGUiOiAiQWRhcHRpdmVMZWFybmluZ1NlbnNvcnMtVXBkYXRlLTAuMi4wLmV4ZSIsICJzaGEyNTYiOiAiNjAzMDNhZTIyYjk5ODg2MWJjZTNiMjhmMzNlZWMxYmU3NThhMjEzYzg2YzkzYzA3NmRiZTlmNTU4YzExYzc1MiIsICJzaXplIjogNSwgInZlcnNpb24iOiAiMC4yLjAifV0sICJwdWJsaXNoZWQiOiAiMjAyNi0xMC0wOFQwMDowMDowMCswMDowMCIsICJyb2xsb3V0IjogMTAwLCAic2hhMjU2IjogIjlmODZkMDgxODg0YzdkNjU5YTJmZWFhMGM1NWFkMDE1YTNiZjRmMWIyYjBiODIyY2QxNWQ2YzE1YjBmMDBhMDgiLCAic2l6ZSI6IDQsICJ2ZXJzaW9uIjogIjEuMC4wIn0=",
  "signature": "mJJ8M66PMBLRQ0Uf88j1nGxYcBYk3qLcrFlP6N5uz7w9v1cWyS32FHwj6wEglRjkhMMeZ2e5fqRSyJh5x/6NAQ=="
}"""
