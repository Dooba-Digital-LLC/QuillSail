"""Reach a running desktop launcher from the command line or a second launch.

The launcher listens on a local socket (a named pipe on Windows) that only the
same user can open. Each connection sends one command, "show", "quit" or
"ping", and the launcher replies "ok".
"""

from __future__ import annotations

import hashlib
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

COMMANDS = frozenset({"show", "quit", "ping"})


def control_address(folder: Path) -> str:
    """The socket for the launcher that manages the instances registered in folder."""
    digest = hashlib.sha256(str(folder.expanduser().resolve()).encode()).hexdigest()[:16]
    if sys.platform == "win32":
        return rf"\\.\pipe\quillsail-launcher-{digest}"
    # Use a private, short folder: Unix socket paths are limited to about 100 bytes.
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") if sys.platform.startswith("linux") else None
    return os.path.join(runtime_dir or tempfile.gettempdir(), f"quillsail-launcher-{digest}.sock")


def send_command(folder: Path, command: str, timeout: float = 2.0) -> str | None:
    """Return the launcher's reply, or None when no launcher is listening."""
    address = control_address(folder)
    message = command.encode() + b"\n"
    try:
        if sys.platform == "win32":
            with open(address, "r+b", buffering=0) as pipe:
                pipe.write(message)
                reply = pipe.readline()
        else:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(timeout)
                client.connect(address)
                client.sendall(message)
                with client.makefile("rb") as stream:
                    reply = stream.readline()
    except OSError:
        return None
    return reply.decode(errors="replace").strip() or None


def wait_until_closed(folder: Path, timeout: float) -> bool:
    """Wait for the launcher to stop answering, after it was asked to quit."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if send_command(folder, "ping") is None:
            return True
        time.sleep(0.25)
    return False
