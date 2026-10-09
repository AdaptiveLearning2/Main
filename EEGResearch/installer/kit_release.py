"""Signs the kit's update feeds. newkey: a passphrase-protected signing key. newdownloadkey, newlinksecret: the gate's
secrets, to files. sign: a feed for one Update installer. verify: a feed against the kit's keys. setup: the gate's
setup/current.json for a Setup installer. link: a signed link to it. See DEVELOPER_SETUP_WINDOWS.md.

A feed is {manifest, signature}, the Ed25519 signature over update.FEED_PREFIX and the manifest's bytes.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import getpass
import hashlib
import hmac
import json
import re
import secrets
import sys
import time
from pathlib import Path

EEG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EEG))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from src.kit import update, update_keys, update_settings  # noqa: E402


def public_text(private: Ed25519PrivateKey) -> str:
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def new_key(path: Path, passphrase: bytes) -> str:
    """Writes the encrypted key to path, which must not exist, and returns its public half."""
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.BestAvailableEncryption(passphrase))
    with open(path, "xb") as f:  # a signing key is never overwritten
        f.write(pem)
    return public_text(private)


def new_secret(path: Path) -> None:
    """A gate secret (download key or link secret), to a file that must not exist: never a command line or clipboard."""
    with open(path, "x", encoding="ascii", newline="") as f:
        f.write(secrets.token_urlsafe(32))


def load_key(path: Path, passphrase: bytes) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(Path(path).read_bytes(), passphrase)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit(f"{path} is not an Ed25519 key")
    return key


def _described(installer: Path, kind: str) -> dict:
    """An installer's version (from its name), name, SHA-256 and size; kind is "Update" or "Setup"."""
    match = re.fullmatch(rf"AdaptiveLearningSensors-{kind}-(.+)\.exe", installer.name)
    version = update.parse_version(match[1] if match else None)
    digest = hashlib.sha256()
    with open(installer, "rb") as f:
        while chunk := f.read(update.CHUNK):
            digest.update(chunk)
    return {"version": update.version_text(version), "file": installer.name, "sha256": digest.hexdigest(),
            "size": installer.stat().st_size}


def release_of(installer: Path) -> dict:
    """The feed's description of an Update installer."""
    return _described(installer, "Update")


def setup_current(installer: Path, published: dt.datetime) -> bytes:
    """setup/current.json, which the gate serves to an admin's signed link: the Setup installer it names."""
    return json.dumps({**_described(installer, "Setup"), "published": published.isoformat(timespec="seconds")},
                      indent=2, sort_keys=True).encode("ascii") + b"\n"


LINK_TTL_S = 600


def link_signature(secret: str, kind: str, exp: int) -> str:
    """What the gate checks a link against: HMAC-SHA256 of "kind:exp" in hex, as backend/kit_gate.py signs it."""
    return hmac.new(secret.encode("utf-8"), f"{kind}:{exp}".encode("ascii"), hashlib.sha256).hexdigest()


def link(gate: str, secret: str, kind: str, now: float) -> str:
    """A link to the Setup installer (kind "setup") or its details ("meta"), honoured for LINK_TTL_S."""
    exp = int(now) + LINK_TTL_S
    path = "/v1/setup/current.json" if kind == "meta" else "/v1/setup/current"
    return f"{gate.rstrip('/')}{path}?exp={exp}&sig={link_signature(secret, kind, exp)}"


def as_release(release: update.Release) -> dict:
    return {"version": update.version_text(release.version), "file": release.file, "sha256": release.sha256,
            "size": release.size}


def history_from(published: list[update.Manifest], release: dict) -> list[dict]:
    """Every installer the published feeds name, older than release: the ways back a computer may need.

    One version with two different installers stops the publish, as does this version published with other bytes.
    """
    this = update.parse_version(release["version"])
    found: dict[update.Version, update.Release] = {}
    for manifest in published:
        for entry in (*manifest.history, manifest.release):
            if entry.version == this and entry.sha256 != release["sha256"]:
                raise SystemExit(f"{release['version']} is already published with other bytes")
            if entry.version >= this:
                continue
            if found.setdefault(entry.version, entry).sha256 != entry.sha256:
                raise SystemExit(f"the published feeds name two installers for {update.version_text(entry.version)}")
    return [as_release(found[v]) for v in sorted(found) if v >= update.ROLLBACK_FLOOR]


def sign(private: Ed25519PrivateKey, release: dict, rollout: int, history: list[dict], published: dt.datetime,
         feed: str) -> bytes:
    """The feed's bytes, read back through the kit's own verifier against the key that signed them."""
    manifest = {**release, "feed": feed, "published": published.isoformat(timespec="seconds"), "rollout": rollout,
                "history": history}
    signed = json.dumps(manifest, sort_keys=True).encode("utf-8")
    signature = private.sign(update.FEED_PREFIX + signed)
    body = json.dumps({"manifest": base64.b64encode(signed).decode("ascii"),
                       "signature": base64.b64encode(signature).decode("ascii")}, indent=2).encode("ascii") + b"\n"
    update.verify_feed(body, update.load_keys({"signer": public_text(private)}), feed)
    return body


def verify(raw: bytes) -> tuple[update.Manifest, str]:
    """Against the keys the kits trust, so what passes here is what a kit would install."""
    return update.verify_feed(raw, update.load_keys(update_keys.PUBLIC_KEYS))


def _passphrase(confirm: bool = False) -> bytes:
    first = getpass.getpass("Signing key passphrase: ")
    if confirm and getpass.getpass("Again: ") != first:
        raise SystemExit("the passphrases differ")
    if len(first) < 12:
        raise SystemExit("use a passphrase of at least 12 characters")
    return first.encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kit_release.py")
    commands = parser.add_subparsers(dest="command", required=True)
    newkey = commands.add_parser("newkey", help="make a signing key; prints the public key for update_keys.py")
    newkey.add_argument("path", type=Path)
    commands.add_parser("newdownloadkey", help="make the gate's download key, in a file").add_argument("path", type=Path)
    commands.add_parser("newlinksecret", help="make the gate's link secret, in a file").add_argument("path", type=Path)
    setup = commands.add_parser("setup", help="write setup/current.json for a Setup installer")
    setup.add_argument("--installer", type=Path, required=True)
    setup.add_argument("--out", type=Path, required=True)
    linker = commands.add_parser("link", help="print a signed link to the published Setup installer or its details")
    linker.add_argument("--secret-file", type=Path, required=True)
    linker.add_argument("--kind", choices=("setup", "meta"), required=True)
    linker.add_argument("--gate", default=update_settings.GATE_URL)
    signer = commands.add_parser("sign", help="write a signed feed for one Update installer")
    signer.add_argument("--key", type=Path, required=True)
    signer.add_argument("--installer", type=Path, required=True)
    signer.add_argument("--feed", choices=update_settings.FEEDS, required=True)
    signer.add_argument("--rollout", type=int, required=True, help="percent of computers, 0-100")
    signer.add_argument("--history-from", type=Path, action="append", default=[],
                        help="a published feed; its installers become ways back (repeat for each feed)")
    signer.add_argument("--out", type=Path, required=True)
    checker = commands.add_parser("verify", help="read a feed as a kit would; prints its manifest")
    checker.add_argument("path", type=Path)
    args = parser.parse_args(argv)

    if args.command == "newkey":
        print(new_key(args.path, _passphrase(confirm=True)))
        print("Add that line to PUBLIC_KEYS in EEGResearch/src/kit/update_keys.py; keep the key file off the repo.",
              file=sys.stderr)
        return 0
    if args.command in ("newdownloadkey", "newlinksecret"):
        new_secret(args.path)
        print(f"wrote {args.path}")
        return 0
    if args.command == "setup":
        with open(args.out, "xb") as f:
            f.write(setup_current(args.installer, dt.datetime.now(dt.UTC)))
        print(f"wrote {args.out}")
        return 0
    if args.command == "link":
        print(link(args.gate, args.secret_file.read_text(encoding="ascii").strip(), args.kind, time.time()))
        return 0
    if args.command == "verify":
        manifest, by = verify(args.path.read_bytes())
        print(json.dumps({**as_release(manifest.release), "feed": manifest.feed,
                          "published": manifest.published.isoformat(), "rollout": manifest.rollout, "signed_by": by,
                          "history": [update.version_text(entry.version) for entry in manifest.history]}))
        return 0
    private = load_key(args.key, _passphrase())
    if public_text(private) not in update_keys.PUBLIC_KEYS.values():
        raise SystemExit("that key is not in update_keys.PUBLIC_KEYS, so no kit would accept what it signs")
    release = release_of(args.installer)
    history = history_from([verify(path.read_bytes())[0] for path in args.history_from], release)
    body = sign(private, release, args.rollout, history, dt.datetime.now(dt.UTC), args.feed)
    with open(args.out, "xb") as f:
        f.write(body)
    print(f"wrote {args.out}, with ways back to {', '.join(entry['version'] for entry in history) or 'nothing'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
