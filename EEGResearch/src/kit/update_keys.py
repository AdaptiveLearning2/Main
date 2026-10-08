"""The Ed25519 public keys an update feed may be signed with, and the self-test's own signed feed.

Two keys, either of which can sign: the everyday one, and a recovery one kept offline to replace it. Empty, the
self-test fails, so no kit is built that could never verify an update. Made by `installer/kit_release.py newkey`.
"""

# One key a line, named everyday or recovery: a line holding "key" beside the value reads as a credential to gitleaks.
PUBLIC_KEYS: dict[str, str] = {}

# A throwaway key and a feed it signed, discarded after: the self-test proves verification works in the frozen build.
TEST_SIGNER = "60IRlXBWN5kJDnWnp0Y1/EpB13aOkExipOPZb+xYyq4="
TEST_FEED = b"""{
  "manifest": "eyJmaWxlIjogIkFkYXB0aXZlTGVhcm5pbmdTZW5zb3JzLVVwZGF0ZS0xLjAuMC5leGUiLCAicHVibGlzaGVkIjogIjIwMjYtMTAtMDhUMDA6MDA6MDArMDA6MDAiLCAicm9sbGJhY2tfdG8iOiB7ImZpbGUiOiAiQWRhcHRpdmVMZWFybmluZ1NlbnNvcnMtVXBkYXRlLTAuMi4wLmV4ZSIsICJzaGEyNTYiOiAiNjAzMDNhZTIyYjk5ODg2MWJjZTNiMjhmMzNlZWMxYmU3NThhMjEzYzg2YzkzYzA3NmRiZTlmNTU4YzExYzc1MiIsICJzaXplIjogNSwgInZlcnNpb24iOiAiMC4yLjAifSwgInJvbGxvdXQiOiAxMDAsICJzaGEyNTYiOiAiOWY4NmQwODE4ODRjN2Q2NTlhMmZlYWEwYzU1YWQwMTVhM2JmNGYxYjJiMGI4MjJjZDE1ZDZjMTViMGYwMGEwOCIsICJzaXplIjogNCwgInZlcnNpb24iOiAiMS4wLjAifQ==",
  "signature": "E6h9/rzFyq9opOIW3qwdLQPhO0cgv4O9EtQk6mFtBYCQEw8T24DaLGby0PoYPNXzm+DbXpzKFNarnOzqxB8nBw=="
}"""
