"""The four desktop downloads, and working out which one a visitor needs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Platform:
    key: str
    system: str  # macOS, Windows or Linux
    variant: str  # shown after the system name
    computer: str  # "Mac with Apple silicon"
    suffix: str
    installer: str  # "sh" or "ps1"


PLATFORMS: dict[str, Platform] = {
    p.key: p
    for p in (
        Platform("macos-arm64", "macOS", "Apple silicon", "Mac with Apple silicon", ".zip", "sh"),
        Platform("macos-x86_64", "macOS", "Intel", "Mac with an Intel processor", ".zip", "sh"),
        Platform("windows-x86_64", "Windows", "64-bit", "64-bit Windows PC", ".zip", "ps1"),
        Platform("linux-x86_64", "Linux", "x86-64", "64-bit Linux PC with a desktop", ".tar.gz", "sh"),
    )
}


def archive_name(version: str, platform: str) -> str:
    return f"QuillSail-{version}-{platform}{PLATFORMS[platform].suffix}"


def detect(user_agent: str | None) -> str | None:
    """Guess the visitor's download from their browser.

    Browsers report "Intel Mac OS X" even on Apple silicon, so Macs default to
    Apple silicon; the page offers Intel next to it, and site.js refines the
    choice where the browser reports its architecture. Phones and tablets get
    no guess because QuillSail runs on computers.
    """
    agent = (user_agent or "").lower()
    if any(word in agent for word in ("iphone", "ipad", "android", "mobile")):
        return None
    if "windows" in agent:
        return "windows-x86_64"
    if "macintosh" in agent or "mac os x" in agent:
        return "macos-arm64"
    if "linux" in agent and "cros" not in agent:
        return "linux-x86_64"
    return None


INSTALLER_SUFFIXES = {"macos-arm64": ".dmg", "macos-x86_64": ".dmg", "windows-x86_64": "-setup.exe", "linux-x86_64": ".deb"}
