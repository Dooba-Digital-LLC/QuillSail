"""One-command bootstrap for servers whose installed CLI predates update support.

Use a wheel and SHA-256 digest obtained through a trusted first-install channel.
All deployment, backup and recovery policy is in the wheel's regular updater.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--profile", type=Path, default=Path("/etc/copydesk/update.json"))
    parser.add_argument("--install-root", type=Path, default=Path("/opt/copydesk"))
    parser.add_argument("--instance-root", type=Path, default=Path("/var/lib/copydesk"))
    parser.add_argument("--backup-root", type=Path, default=Path("/var/backups/copydesk"))
    parser.add_argument("--service-user", default="copydesk")
    parser.add_argument("--web-service", default="copydesk-web")
    parser.add_argument("--worker-service", default="copydesk-worker")
    parser.add_argument("--health-url")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    if sys.platform != "linux" or os.geteuid() != 0:
        parser.error("run this installer with sudo on the Linux host")
    wheel = args.wheel.expanduser().resolve()
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != args.sha256:
        parser.error("the wheel does not match the trusted SHA-256 checksum")
    with tempfile.TemporaryDirectory(prefix="quillsail-bootstrap-") as directory:
        root = Path(directory)
        installer = root / wheel.name
        shutil.copyfile(wheel, installer)
        if hashlib.sha256(installer.read_bytes()).hexdigest() != args.sha256:
            parser.error("the installer changed while being staged")
        wheel = installer
        subprocess.run([sys.executable, "-m", "venv", str(root / "venv")], check=True)
        python = root / "venv/bin/python"
        subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "--only-binary=:all:",
                        str(wheel) + "[server,postgres,mysql]"], check=True)
        command = [str(root / "venv/bin/quillsail"), "update"]
        if not args.profile.exists():
            setup = [*command, "setup", "--profile", str(args.profile), "--install-root", str(args.install_root),
                     "--instance-root", str(args.instance_root), "--backup-root", str(args.backup_root),
                     "--service-user", args.service_user, "--web-service", args.web_service, "--worker-service", args.worker_service]
            if args.health_url:
                setup += ["--health-url", args.health_url]
            if args.yes:
                setup.append("--yes")
            subprocess.run(setup, check=True)
        install = [*command, "bootstrap", "--profile", str(args.profile), "--wheel", str(wheel), "--sha256", args.sha256]
        if args.yes:
            install.append("--yes")
        subprocess.run(install, check=True)
    print("Future updates:", args.install_root / "current/venv/bin/quillsail", "update")


if __name__ == "__main__":
    main()
