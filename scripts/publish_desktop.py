"""Publish desktop downloads only after all four native builders have passed.

Uses the workflow's built-in token, without publishing containers or packages.
Published releases are immutable here; retries can resume the same draft.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

TARGETS = {"macos-arm64": ".zip", "macos-x86_64": ".zip", "windows-x86_64": ".zip", "linux-x86_64": ".tar.gz"}


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def source_version():
    source = Path(__file__).resolve().parents[1] / "src/copydesk/__init__.py"
    for node in ast.parse(source.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets):
            version = ast.literal_eval(node.value)
            if isinstance(version, str) and re.fullmatch(r"\d+\.\d+\.\d+", version):
                return version
    raise ValueError("The source version must be a numeric release version.")


def validate_downloads(folder, version, commit):
    files, reports = [], []
    for target, suffix in TARGETS.items():
        stem = f"QuillSail-{version}-{target}"
        archive = folder / (stem + suffix)
        report = json.loads((folder / (stem + ".json")).read_text(encoding="utf-8"))
        expected = {"version": version, "target": target, "archive": archive.name, "commit": commit,
                    "setup_restart_check": True, "gui_lifecycle_check": True}
        if any(report.get(key) != value for key, value in expected.items()):
            raise ValueError(f"The {target} build report does not match the tested release commit.")
        if not archive.is_file() or archive.is_symlink() or not archive.stat().st_size or digest(archive) != report.get("sha256"):
            raise ValueError(f"The {target} archive is missing, empty or has the wrong checksum.")
        reports.append(report)
        files.append(archive)
    expected_names = {file.name for file in files}
    actual_names = {file.name for pattern in ("*.zip", "*.tar.gz") for file in folder.glob(pattern)}
    if actual_names != expected_names:
        raise ValueError("Unexpected or outdated desktop downloads are present.")
    checksums = folder / "SHA256SUMS.txt"
    checksums.write_text("".join(f"{digest(file)}  {file.name}\n" for file in files), encoding="utf-8")
    manifest = folder / "desktop-builds.json"
    manifest.write_text(json.dumps({"version": version, "commit": commit, "builds": reports}, indent=2) + "\n", encoding="utf-8")
    return [*files, checksums, manifest]


class GitHub:
    def __init__(self, repository, token):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("GH_REPO must be owner/repository.")
        self.repository, self.token = repository, token

    def request(self, method, endpoint, payload=None, *, upload=False, data=None):
        host = "uploads.github.com" if upload else "api.github.com"
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/octet-stream" if upload else "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode()
        request = Request(f"https://{host}/repos/{self.repository}/{endpoint}", data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=180) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except HTTPError as exc:
            if method == "GET" and exc.code == 404:
                return None
            raise


def publish(client, version, commit, files):
    tag = "v" + version
    existing = client.request("GET", "releases/tags/" + tag)
    if existing is None:
        drafts = client.request("GET", "releases?per_page=100") or []
        existing = next((item for item in drafts if item.get("draft") and item.get("tag_name") == tag), None)
    expected = {file.name: digest(file) for file in files}
    if existing and not existing["draft"]:
        published = {asset["name"]: asset.get("digest") for asset in existing["assets"]}
        if all(published.get(name) == "sha256:" + checksum for name, checksum in expected.items()):
            return existing["html_url"]
        raise ValueError("This version is already published with different or incomplete assets. Use a new version.")
    if existing and existing["target_commitish"] != commit:
        raise ValueError("An existing draft points to a different commit.")
    tag_ref = client.request("GET", "git/ref/tags/" + tag)
    if tag_ref:
        target = tag_ref["object"]
        for _ in range(5):
            if target["type"] != "tag":
                break
            target = client.request("GET", "git/tags/" + target["sha"])["object"]
        if target["type"] != "commit" or target["sha"] != commit:
            raise ValueError("The existing version tag points to a different commit.")
    labels = ["Mac — Apple Silicon", "Mac — Intel", "Windows — x64", "Linux — x64"]
    archives = files[:4]
    rows = [f"| {label} | [{file.name}](https://github.com/{client.repository}/releases/download/{tag}/{file.name}) |" for label, file in zip(labels, archives, strict=True)]
    body = "\n".join([
        "Download the bundle for your computer, extract it, and open QuillSail. Python and Docker are not required.", "",
        "| Platform | Download |", "| --- | --- |", *rows, "",
        "The app opens localhost automatically. Create your publication and administrator on first launch; later launches preserve your data.", "",
        "Each native build passed browser setup/restart and GUI start/stop/restart checks using disposable data. No test email was sent.", "",
        "These are beta builds. macOS bundles are ad-hoc signed; Developer ID signing/notarization and Windows code signing are not configured.",
        "Linux builds target Ubuntu 22.04 or newer with a desktop environment. See the desktop guide for required desktop libraries.", "",
        f"[Desktop guide](https://github.com/{client.repository}/blob/{tag}/docs/desktop.md) · Source commit: `{commit}`", "",
        "SHA256SUMS.txt and desktop-builds.json contain the download checksums and native build reports.",
    ])
    release = existing or client.request("POST", "releases", {"tag_name": tag, "target_commitish": commit,
        "name": f"QuillSail {version} Beta — Desktop", "body": body, "draft": True, "prerelease": True})
    current = {asset["name"]: asset for asset in release.get("assets", [])}
    for file in files:
        asset = current.get(file.name)
        if asset and asset.get("digest") == "sha256:" + expected[file.name]:
            continue
        if asset:
            client.request("DELETE", f"releases/assets/{asset['id']}")
        client.request("POST", f"releases/{release['id']}/assets?name={quote(file.name)}", upload=True, data=file.read_bytes())
    uploaded = client.request("GET", f"releases/{release['id']}")
    actual = {asset["name"]: asset.get("digest") for asset in uploaded["assets"]}
    if any(actual.get(name) != "sha256:" + checksum for name, checksum in expected.items()):
        raise ValueError("Uploaded release checksums did not match; the release stays a draft.")
    result = client.request("PATCH", f"releases/{release['id']}", {"draft": False, "prerelease": True, "body": body, "make_latest": "false"})
    return result["html_url"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("downloads", type=Path)
    args = parser.parse_args()
    commit = os.environ["DESKTOP_COMMIT"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("DESKTOP_COMMIT must be the full tested commit SHA.")
    version = source_version()
    files = validate_downloads(args.downloads, version, commit)
    print(publish(GitHub(os.environ["GH_REPO"], os.environ["GH_TOKEN"]), version, commit, files))


if __name__ == "__main__":
    main()
