"""Install a protected, stable command for a registered Linux server."""

from __future__ import annotations

import os
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path

from copydesk.server_update import trusted_code, trusted_directory
from copydesk.updater import UpdateError

DEFAULT_COMMAND = Path("/usr/local/bin/quillsail")


def require_operator():
    if sys.platform != "linux" or os.geteuid() != 0:
        raise UpdateError("Run server setup with sudo on the Linux host.")


def command_target(profile):
    if profile.kind == "python":
        # Keep current in the link; resolving it would pin one old release.
        return profile.at("install_root") / "current/venv/bin/quillsail"
    return Path(profile.values.get("command_target") or Path(sysconfig.get_path("scripts")) / "quillsail").absolute()


def lexical(path):
    """Normalize the parent without following the command's final symlink."""
    path = Path(path).expanduser().absolute()
    return path.parent.resolve() / path.name


@dataclass
class CommandLink:
    destination: Path
    target: Path
    created_identity: tuple | None = None

    def validate(self, *, search_path=None):
        self.destination, self.target = lexical(self.destination), Path(self.target).absolute()
        trusted_directory(self.destination.parent)
        if not self.target.is_file() or not os.access(self.target, os.X_OK):
            raise UpdateError("The persistent QuillSail executable is missing or is not executable.")
        trusted_code(self.target.resolve())
        # This is the PATH supplied by sudo, not an interactive shell's aliases.
        entries = (os.environ.get("PATH", "") if search_path is None else search_path).split(os.pathsep)
        if any(not value or not Path(value).is_absolute() for value in entries):
            raise UpdateError("Sudo's PATH must use absolute directories to verify the server command. Ask the administrator to configure secure_path, or use --no-command and the full executable path.")
        folders = [Path(value).resolve() for value in entries]
        if self.destination.parent not in folders:
            raise UpdateError(f"{self.destination.parent} is not on this sudo session's PATH. Choose --command in an approved PATH directory, or ask the system administrator to configure sudo's secure_path. No sudo policy was changed.")
        for folder in folders:
            if folder == self.destination.parent:
                break
            trusted_directory(folder)
            existing = folder / self.destination.name
            if existing.is_file() and os.access(existing, os.X_OK):
                raise UpdateError(f"{existing} would shadow the server command on PATH. Choose --command in an earlier approved directory or use another command name.")
        if os.path.lexists(self.destination):
            # A host-installed Compose tool may already be the global command.
            same_file = not self.destination.is_symlink() and self.destination == lexical(self.target)
            linked_target = Path(os.path.abspath(self.destination.parent / os.readlink(self.destination))) if self.destination.is_symlink() else None
            same_link = linked_target == Path(os.path.abspath(self.target))
            if not same_file and not same_link:
                raise UpdateError(f"{self.destination} already belongs to another command. It was not replaced. Choose another --command path, or use --no-command during setup.")

    def install(self):
        self.validate()
        if os.path.lexists(self.destination):
            return
        self.destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        # symlink creation fails if another operator claims the name first.
        self.destination.symlink_to(self.target)
        info = self.destination.lstat()
        self.created_identity = info.st_dev, info.st_ino
        descriptor = os.open(self.destination.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def undo(self):
        if self.created_identity and self.destination.is_symlink():
            info = self.destination.lstat()
            if (info.st_dev, info.st_ino) == self.created_identity:
                self.destination.unlink()


def plan(profile, destination=None):
    result = CommandLink(Path(destination or profile.values.get("command_path") or DEFAULT_COMMAND), command_target(profile))
    if profile.kind == "python" and result.destination.parent.resolve().is_relative_to(profile.at("install_root") / "releases"):
        raise UpdateError("Choose a stable command directory outside the versioned releases folder.")
    result.validate()
    return result
