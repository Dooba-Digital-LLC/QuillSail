"""Create signing keys offline, or publish/refresh the authenticated desktop feed."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from publish_desktop import GitHub, digest
from tuf.api.metadata import Metadata

from copydesk.update_metadata import (
    build_metadata,
    initialize,
    online_keys,
    read_published_metadata,
    release_targets,
)
from copydesk.updater import BOOTSTRAP, METADATA_URL


def publish_feed(client, signing_keys, *, folder=None, channel="beta"):
    root = Metadata.from_file(str(BOOTSTRAP))
    feed = client.request("GET", "releases/tags/desktop-updates")
    if not feed and folder is None:
        print("No update feed exists yet. Publish the first tested desktop release before refreshing metadata.")
        return None
    existing, version = {}, 0
    if feed:
        def fetch(name):
            with urlopen(METADATA_URL + name, timeout=30) as response:
                raw = response.read(5_000_001)
            if len(raw) > 5_000_000:
                raise ValueError("Update metadata is too large.")
            return raw
        try:
            existing, version = read_published_metadata(root, fetch)
        except HTTPError as exc:
            if exc.code != 404 or any(asset["name"] == "timestamp.json" for asset in feed["assets"]):
                raise
    if folder:
        manifest = json.loads((folder / "desktop-builds.json").read_text(encoding="utf-8"))
        existing.update(release_targets(folder, manifest["builds"], channel))
    if not existing:
        raise ValueError("No tested desktop release is available to sign.")
    metadata = build_metadata(root, online_keys(signing_keys), existing, max(version + 1, int(time.time())))
    feed = feed or client.request("POST", "releases", {"tag_name": "desktop-updates", "name": "QuillSail signed update metadata",
        "body": "Authenticated metadata for the desktop updater. Download application bundles from the versioned desktop releases.",
        "draft": False, "prerelease": True, "make_latest": "false"})
    assets = {asset["name"]: asset for asset in feed["assets"]}
    with tempfile.TemporaryDirectory(prefix="quillsail-feed-upload-") as folder_name:
        # Timestamp is the commit point and is always uploaded last.
        for name in sorted(metadata, key=lambda name: name == "timestamp.json"):
            path = Path(folder_name) / name
            path.write_bytes(metadata[name])
            asset = assets.get(name)
            if asset and asset.get("digest") == "sha256:" + digest(path):
                continue
            if asset:
                if name != "timestamp.json":
                    raise ValueError("Versioned update metadata cannot be overwritten.")
                client.request("DELETE", f"releases/assets/{asset['id']}")
            client.request("POST", f"releases/{feed['id']}/assets?name={name}", upload=True, data=metadata[name])
    return feed["html_url"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Create offline root keys, online keys, and a public trust root")
    init.add_argument("--keys", required=True, type=Path)
    init.add_argument("--root", default=BOOTSTRAP, type=Path)
    publish = commands.add_parser("publish", help="Publish a tested release or renew existing metadata")
    publish.add_argument("--downloads", type=Path)
    publish.add_argument("--channel", choices=("stable", "beta"), default="beta")
    args = parser.parse_args()
    if args.command == "init":
        initialize(args.keys, args.root)
        print("Public trust root:", args.root)
        print("Private signing keys:", args.keys, "— keep the root keys offline; never commit this file.")
    else:
        print(publish_feed(GitHub(os.environ["GH_REPO"], os.environ["GH_TOKEN"]),
                           os.environ["DESKTOP_UPDATE_KEYS"], folder=args.downloads, channel=args.channel))


if __name__ == "__main__":
    main()
