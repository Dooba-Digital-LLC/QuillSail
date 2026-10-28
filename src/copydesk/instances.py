"""Named local publications shared by the desktop launcher and CLI.

The registry contains paths and display names only. Each publication owns its
configuration, database, credentials, uploads, queue, listener and OS lock.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

import requests

from copydesk.config import ConfigError
from copydesk.desktop import FORMAT, DesktopRuntime, InstanceLock, default_data_dir, write_private

REGISTRY_FORMAT = "quillsail.instances.v1"
IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9-]{0,47}\Z")


def default_registry_dir() -> Path:
    return Path(os.environ["QUILLSAIL_INSTANCE_HOME"]).expanduser() if os.environ.get("QUILLSAIL_INSTANCE_HOME") else default_data_dir().with_name(default_data_dir().name + "-instances")


@dataclass(frozen=True)
class Instance:
    id: str
    name: str
    root: Path

    @property
    def config_path(self) -> Path:
        return self.root / "copydesk.toml"


def running_url(instance: Instance) -> str:
    """Recognize this instance, rather than trusting a stale remembered port."""
    try:
        saved = json.loads((instance.root / "desktop.json").read_text(encoding="utf-8"))
        port = saved.get("port")
        if saved.get("format") != FORMAT or type(port) is not int or not 1 <= port <= 65535:
            return ""
        url = f"http://127.0.0.1:{port}"
        with requests.Session() as client:
            client.trust_env = False
            response = client.get(url + "/_desktop/status", timeout=.4, allow_redirects=False)
            if response.status_code == 200 and response.json().get("instance") == hashlib.sha256(str(instance.root).encode()).hexdigest():
                return url
    except (OSError, ValueError, AttributeError, requests.RequestException):
        pass
    return ""


class InstanceRegistry:
    def __init__(self, directory: Path | None = None, *, default_root: Path | None = None):
        self.root = (directory or default_registry_dir()).expanduser().resolve()
        self.path = self.root / "instances.json"
        self.default = Instance("default", "Default publication", (default_root or default_data_dir()).expanduser().resolve())

    def list(self) -> list[Instance]:
        if not self.path.exists():
            return [self.default]
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            if document["format"] != REGISTRY_FORMAT:
                raise ValueError
            rows = [Instance(row["id"], row["name"], Path(row["directory"]).expanduser().resolve()) for row in document["instances"]]
            if any(not IDENTIFIER.fullmatch(row.id) or not isinstance(row.name, str) or not row.name.strip() for row in rows):
                raise ValueError
            if len({row.id for row in rows}) != len(rows) or len({row.root for row in rows}) != len(rows):
                raise ValueError
            if not any(row.id == "default" and row.root == self.default.root for row in rows):
                raise ValueError
            return rows
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ConfigError("The instance registry could not be read. Preserve instances.json and your publication folders before repairing it.") from exc

    def get(self, identifier: str) -> Instance:
        for instance in self.list():
            if instance.id == identifier:
                return instance
        raise ConfigError(f"No instance named {identifier!r}. Run `quillsail instance list`.")

    def add(self, name: str, directory: Path | None = None, *, existing=False) -> Instance:
        name = name.strip()
        identifier = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if not 1 <= len(name) <= 80 or any(ord(c) < 32 for c in name) or not IDENTIFIER.fullmatch(identifier):
            raise ConfigError("Use a name of 1–80 characters that contains letters or numbers; its identifier must fit in 48 characters.")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = InstanceLock(self.root / "registry.lock")
        lock.acquire()
        try:
            rows = self.list()
            folder = (directory or self.root / "publications" / identifier).expanduser().resolve()
            if any(row.id == identifier for row in rows):
                raise ConfigError("That instance name is already in use. Choose another name.")
            if any(row.root == folder or row.root.is_relative_to(folder) or folder.is_relative_to(row.root) for row in rows):
                raise ConfigError("Choose a separate data folder outside other registered instances.")
            instance = Instance(identifier, name, folder)
            if existing:
                try:
                    marker = json.loads((folder / "desktop.json").read_text(encoding="utf-8"))
                    if marker.get("format") != FORMAT or not instance.config_path.is_file():
                        raise ValueError
                except (OSError, ValueError) as exc:
                    raise ConfigError("Choose an existing QuillSail desktop publication folder containing desktop.json and copydesk.toml.") from exc
            else:
                DesktopRuntime(folder).initialize()
            rows.append(instance)
            write_private(self.path, json.dumps({"format": REGISTRY_FORMAT, "instances": [
                {"id": row.id, "name": row.name, "directory": str(row.root)} for row in rows
            ]}, indent=2) + "\n")
            if os.name != "nt":
                self.root.chmod(0o700)
            return instance
        finally:
            lock.release()
