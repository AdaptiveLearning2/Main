"""Signs the kit's update feeds. newkey: a passphrase-protected signing key. sign: a feed for one Update installer.
verify: a feed against the keys baked into the kit. See DEVELOPER_SETUP_WINDOWS.md, "Publishing a kit update".

A feed is {manifest, signature}, the Ed25519 signature over update.FEED_PREFIX and the manifest's bytes.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import getpass
import hashlib
import json
import re
import sys
from pathlib import Path

EEG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EEG))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from src.kit import update, update_keys  # noqa: E402


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


def load_key(path: Path, passphrase: bytes) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(Path(path).read_bytes(), passphrase)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit(f"{path} is not an Ed25519 key")
    return key


def release_of(installer: Path) -> dict:
    """The feed's description of an Update installer, its version read from its name."""
    match = re.fullmatch(r"AdaptiveLearningSensors-Update-(.+)\.exe", installer.name)
    version = update.parse_version(match[1] if match else None)
    digest = hashlib.sha256()
    with open(installer, "rb") as f:
        while chunk := f.read(update.CHUNK):
            digest.update(chunk)
    return {"version": update.version_text(version), "file": installer.name, "sha256": digest.hexdigest(),
            "size": installer.stat().st_size}


def as_release(release: update.Release) -> dict:
    return {"version": update.version_text(release.version), "file": release.file, "sha256": release.sha256,
            "size": release.size}


def way_back(published: update.Manifest, release: dict) -> dict | None:
    """The version a published feed names; when that is this very release, a rollout change, the feed's own way back."""
    if update.version_text(published.release.version) != release["version"]:
        return as_release(published.release)
    if published.release.sha256 != release["sha256"]:
        raise SystemExit(f"the published feed names {release['version']} with other bytes")
    return as_release(published.rollback_to) if published.rollback_to else None


def sign(private: Ed25519PrivateKey, release: dict, rollout: int, rollback_to: dict | None,
         published: dt.datetime) -> bytes:
    """The feed's bytes, read back through the kit's own verifier against the key that signed them."""
    manifest = {**release, "published": published.isoformat(timespec="seconds"), "rollout": rollout,
                "rollback_to": rollback_to}
    signed = json.dumps(manifest, sort_keys=True).encode("utf-8")
    signature = private.sign(update.FEED_PREFIX + signed)
    body = json.dumps({"manifest": base64.b64encode(signed).decode("ascii"),
                       "signature": base64.b64encode(signature).decode("ascii")}, indent=2).encode("ascii") + b"\n"
    update.verify_feed(body, update.load_keys({"signer": public_text(private)}))
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
    signer = commands.add_parser("sign", help="write a signed feed for one Update installer")
    signer.add_argument("--key", type=Path, required=True)
    signer.add_argument("--installer", type=Path, required=True)
    signer.add_argument("--rollout", type=int, required=True, help="percent of computers, 0-100")
    signer.add_argument("--rollback-to-feed", type=Path, help="a published feed whose version is the way back")
    signer.add_argument("--out", type=Path, required=True)
    checker = commands.add_parser("verify", help="read a feed as a kit would; prints its manifest")
    checker.add_argument("path", type=Path)
    args = parser.parse_args(argv)

    if args.command == "newkey":
        print(new_key(args.path, _passphrase(confirm=True)))
        print("Add that line to PUBLIC_KEYS in EEGResearch/src/kit/update_keys.py; keep the key file off the repo.",
              file=sys.stderr)
        return 0
    if args.command == "verify":
        manifest, by = verify(args.path.read_bytes())
        print(json.dumps({**as_release(manifest.release), "published": manifest.published.isoformat(),
                          "rollout": manifest.rollout, "signed_by": by,
                          "rollback_to": as_release(manifest.rollback_to) if manifest.rollback_to else None}))
        return 0
    private = load_key(args.key, _passphrase())
    if public_text(private) not in update_keys.PUBLIC_KEYS.values():
        raise SystemExit("that key is not in update_keys.PUBLIC_KEYS, so no kit would accept what it signs")
    release = release_of(args.installer)
    rollback = way_back(verify(args.rollback_to_feed.read_bytes())[0], release) if args.rollback_to_feed else None
    body = sign(private, release, args.rollout, rollback, dt.datetime.now(dt.UTC))
    with open(args.out, "xb") as f:
        f.write(body)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
