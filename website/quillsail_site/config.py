"""Website settings, read once from the environment.

Every value has a development default, so `flask run` works from a checkout.
Production sets the public addresses, for example:

    QUILLSAIL_SITE_URL=https://quillsail.blog
    QUILLSAIL_DOCS_URL=https://docs.quillsail.blog
    QUILLSAIL_UPDATES_URL=https://updates.quillsail.blog
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

PACKAGE = Path(__file__).resolve().parent
# The website lives in the QuillSail repository; the docs and changelog are one level up.
DEFAULT_REPO_ROOT = PACKAGE.parents[1]
REPOSITORY = "Dooba-Digital-LLC/QuillSail"
CHANNELS = ("stable", "beta")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Address:
    """A public base address such as https://docs.quillsail.blog or http://localhost:8000/docs."""

    url: str
    scheme: str
    host: str  # host[:port], lowercase
    prefix: str  # "" or "/docs", without a trailing slash

    @classmethod
    def parse(cls, name: str, value: str) -> Address:
        parts = urlsplit(value.strip())
        if parts.scheme not in {"http", "https"} or not parts.netloc or parts.query or parts.fragment:
            raise ConfigError(f"{name} must be an http:// or https:// address without a query, such as https://quillsail.blog")
        if "@" in parts.netloc:
            raise ConfigError(f"{name} must not contain credentials.")
        prefix = parts.path.rstrip("/")
        return cls(f"{parts.scheme}://{parts.netloc.lower()}{prefix}", parts.scheme, parts.netloc.lower(), prefix)

    def join(self, path: str = "/") -> str:
        return self.url + (path if path.startswith("/") else "/" + path)


@dataclass(frozen=True)
class Config:
    site: Address
    docs: Address
    updates: Address | None
    repository: str = REPOSITORY
    branch: str = "main"
    channel: str = "beta"
    metadata_url: str = ""
    trust_root: Path = PACKAGE / "trust/root.json"
    repo_root: Path = DEFAULT_REPO_ROOT
    release_ttl: int = 600
    proxy_hops: int = 0
    releases_fixture: Path | None = None
    warm_releases: bool = True

    @property
    def github_url(self) -> str:
        return f"https://github.com/{self.repository}"

    @property
    def releases_url(self) -> str:
        return f"{self.github_url}/releases"

    @property
    def feed_url(self) -> str:
        return self.metadata_url or f"{self.releases_url}/download/desktop-updates/"

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None, **overrides) -> Config:
        env = os.environ if environ is None else environ

        def address(name: str, default: str) -> Address:
            return Address.parse(name, env.get(name) or default)

        updates_value = env.get("QUILLSAIL_UPDATES_URL", "http://updates.localhost:8000")
        repository = env.get("QUILLSAIL_REPOSITORY", REPOSITORY)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ConfigError("QUILLSAIL_REPOSITORY must look like owner/repository.")
        channel = env.get("QUILLSAIL_CHANNEL", "beta")
        if channel not in CHANNELS:
            raise ConfigError("QUILLSAIL_CHANNEL must be stable or beta.")
        fixture = env.get("QUILLSAIL_RELEASES_FIXTURE")
        values = {
            "site": address("QUILLSAIL_SITE_URL", "http://localhost:8000"),
            "docs": address("QUILLSAIL_DOCS_URL", "http://docs.localhost:8000"),
            "updates": Address.parse("QUILLSAIL_UPDATES_URL", updates_value) if updates_value else None,
            "repository": repository,
            "branch": env.get("QUILLSAIL_BRANCH", "main"),
            "channel": channel,
            "metadata_url": env.get("QUILLSAIL_METADATA_URL", ""),
            "trust_root": Path(env.get("QUILLSAIL_TRUST_ROOT") or PACKAGE / "trust/root.json"),
            "repo_root": Path(env.get("QUILLSAIL_REPO_ROOT") or DEFAULT_REPO_ROOT),
            "release_ttl": _integer(env, "QUILLSAIL_RELEASE_TTL", 600, minimum=30),
            "proxy_hops": _integer(env, "QUILLSAIL_PROXY_HOPS", 0, minimum=0),
            "releases_fixture": Path(fixture) if fixture else None,
        }
        values.update(overrides)
        config = cls(**values)
        hosts = [(config.site.host, config.site.prefix), (config.docs.host, config.docs.prefix)]
        if config.updates:
            hosts.append((config.updates.host, config.updates.prefix))
        if len(set(hosts)) != len(hosts):
            raise ConfigError("The site, docs and updates addresses must differ by host or path.")
        return config


def _integer(env, name: str, default: int, *, minimum: int) -> int:
    raw = env.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a whole number.") from None
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}.")
    return value
