"""Reject private workspace files and recognizable credentials in release inputs.

Run without arguments to check Git's staged/tracked files, or pass --archive
for a built wheel/sdist. This is a release-boundary check, not a security audit.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

PRIVATE_ROOTS = {
    ".validation",
    ".venv",
    ".git",
    "instance",
    "data",
    "backups",
    "build",
    "dist",
    ".codex",
}
PRIVATE_NAMES = {"CLAUDE.md", "copydesk.toml", ".DS_Store"}
PATTERNS = {
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "Google client secret": re.compile(rb"GOCSPX-[A-Za-z0-9_-]{15,}"),
    "GitHub token": re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})"),
    "AWS access key": re.compile(rb"(?:AKIA|ASIA)[A-Z0-9]{16}"),
    "local user path": re.compile(rb"/Users/[A-Za-z0-9_.-]+/"),
}


def check(name, content):
    path = PurePosixPath(name)
    failures = []
    if path.is_absolute() or ".." in path.parts:
        failures.append("unsafe archive path")
    if path.parts and path.parts[0] in PRIVATE_ROOTS:
        failures.append("private/generated directory")
    filename = path.name.lower()
    if path.name in PRIVATE_NAMES or (filename.startswith(".env") and filename != ".env.example"):
        failures.append("private instance file")
    if (
        filename.endswith(".env")
        or filename.startswith("client_secret_")
        or "credentials" in filename
        and filename.endswith(".json")
    ):
        failures.append("credential/configuration file")
    if filename.endswith((".pem", ".key", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3")):
        failures.append("private key or database")
    for label, pattern in PATTERNS.items():
        if pattern.search(content):
            failures.append(label)
    return [f"{name}: {reason}" for reason in failures]


def archive_files(path):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                if not name.endswith("/"):
                    yield name, archive.read(name)
    else:
        with tarfile.open(path) as archive:
            for member in archive.getmembers():
                name = member.name.partition("/")[2]
                if member.issym() or member.islnk():
                    raise ValueError("Release archives must not contain links")
                if member.isfile():
                    with archive.extractfile(member) as handle:
                        yield name, handle.read()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    arguments = parser.parse_args()
    if arguments.archive:
        files = archive_files(arguments.archive)
    else:
        names = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
        files = ((name, Path(name).read_bytes()) for name in names if name)
    failures, count = [], 0
    for name, content in files:
        count += 1
        failures.extend(check(name, content))
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        raise SystemExit(1)
    print(f"Public release boundary check passed ({count} files).")


if __name__ == "__main__":
    main()
