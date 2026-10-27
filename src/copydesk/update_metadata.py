"""Maintainer-side TUF metadata creation. Private keys never enter a bundle."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives.serialization import load_pem_private_key
from securesystemslib.signer import CryptoSigner
from tuf.api.metadata import Metadata, MetaFile, Root, Snapshot, TargetFile, Targets, Timestamp


def initialize(keys_path: Path, root_path: Path):
    if keys_path.exists() or root_path.exists():
        raise ValueError("Signing keys and trusted root must be created once, without overwriting existing files.")
    root = Metadata(Root(expires=datetime.now(UTC) + timedelta(days=365 * 5)))
    signers = {role: [CryptoSigner.generate_ed25519() for _ in range(3 if role == "root" else 1)]
               for role in ("root", "targets", "snapshot", "timestamp")}
    for role, members in signers.items():
        for member in members:
            root.signed.add_key(member.public_key, role)
    root.signed.roles["root"].threshold = 2
    for signer in signers["root"]:
        root.sign(signer, append=True)
    keys_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = json.dumps({"format": "quillsail.update-keys.v1",
                      "keys": {role: [signer.private_bytes.decode("utf-8") for signer in members]
                               for role, members in signers.items()}}, indent=2)
    fd = os.open(keys_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(raw + "\n")
    root_path.parent.mkdir(parents=True, exist_ok=True)
    root.to_file(str(root_path))
    os.chmod(root_path, 0o644)
    return root


def online_keys(raw):
    data = json.loads(raw)
    keys = data.get("keys", data)
    return {role: CryptoSigner(load_pem_private_key(keys[role][0].encode("utf-8"), password=None))
            for role in ("targets", "snapshot", "timestamp")}


def build_metadata(root: Metadata, keys: dict, targets: dict, version: int, *, now=None):
    now = now or datetime.now(UTC)
    if root.signed.expires <= now:
        raise ValueError("Rotate the expired offline trust root before publishing updates.")
    result = {}
    target_metadata = Metadata(Targets(version=version, expires=now + timedelta(days=90), targets=targets))
    target_metadata.sign(keys["targets"])
    result[f"{version}.targets.json"] = target_metadata.to_bytes()
    target_info = MetaFile.from_data(version, result[f"{version}.targets.json"], ["sha256"])
    snapshot = Metadata(Snapshot(version=version, expires=now + timedelta(days=14), meta={"targets.json": target_info}))
    snapshot.sign(keys["snapshot"])
    result[f"{version}.snapshot.json"] = snapshot.to_bytes()
    snapshot_info = MetaFile.from_data(version, result[f"{version}.snapshot.json"], ["sha256"])
    timestamp = Metadata(Timestamp(version=version, expires=now + timedelta(days=7), snapshot_meta=snapshot_info))
    timestamp.sign(keys["timestamp"])
    result["timestamp.json"] = timestamp.to_bytes()
    for name, content in result.items():
        role = name.split(".")[-2]
        item = Metadata.from_bytes(content)
        if not root.signed.get_verification_result(role, item.signed_bytes, item.signatures):
            raise ValueError(f"The {role} signing key is not trusted by the shipped root.")
    result[f"{root.signed.version}.root.json"] = root.to_bytes()
    return result


def read_published_metadata(root: Metadata, fetch):
    """Authenticate the previous repository for renewal, including after expiry.

    Publishers must be able to repair an expired feed. Clients still enforce
    expiry through ngclient; this maintainer path verifies signatures, linked
    hashes, lengths and versions using TUF's Metadata API before re-signing.
    """
    timestamp = Metadata.from_bytes(fetch("timestamp.json"))
    root.signed.verify_delegate("timestamp", timestamp.signed_bytes, timestamp.signatures)
    if not isinstance(timestamp.signed, Timestamp):
        raise ValueError("Expected timestamp metadata.")
    descriptor = timestamp.signed.snapshot_meta
    raw = fetch(f"{descriptor.version}.snapshot.json")
    descriptor.verify_length_and_hashes(raw)
    snapshot = Metadata.from_bytes(raw)
    root.signed.verify_delegate("snapshot", snapshot.signed_bytes, snapshot.signatures)
    if not isinstance(snapshot.signed, Snapshot) or snapshot.signed.version != descriptor.version:
        raise ValueError("Expected matching snapshot metadata.")
    descriptor = snapshot.signed.meta["targets.json"]
    raw = fetch(f"{descriptor.version}.targets.json")
    descriptor.verify_length_and_hashes(raw)
    targets = Metadata.from_bytes(raw)
    root.signed.verify_delegate("targets", targets.signed_bytes, targets.signatures)
    if not isinstance(targets.signed, Targets) or targets.signed.version != descriptor.version:
        raise ValueError("Expected matching targets metadata.")
    return targets.signed.targets, max(timestamp.signed.version, snapshot.signed.version, targets.signed.version)


def release_targets(folder: Path, reports: list[dict], channel: str):
    if channel not in {"stable", "beta"}:
        raise ValueError("Choose a stable or beta channel.")
    result = {}
    for report in reports:
        if not all(report.get(key) is True for key in ("setup_restart_check", "gui_lifecycle_check", "updater_install_check")):
            raise ValueError("Only fully tested desktop bundles can enter the update feed.")
        path = folder / report["archive"]
        target = TargetFile.from_file(f"{channel}/{report['target']}", str(path), ["sha256"])
        if target.hashes["sha256"] != report["sha256"]:
            raise ValueError("The update archive changed after native verification.")
        target.unrecognized_fields["custom"] = {key: report[key] for key in ("version", "archive", "target", "commit")}
        target.unrecognized_fields["custom"].update(channel=channel, updater_protocol=1)
        result[target.path] = target
    return result
