"""Create a tested Python server release artifact alongside the desktop bundles."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from copydesk import __version__


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist/server-release"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    build = root / "build/server-release"
    build.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "build", "--outdir", str(build)], check=True)
    wheel = build / f"copydesk-{__version__}-py3-none-any.whl"
    source = build / f"copydesk-{__version__}.tar.gz"
    for archive in (wheel, source):
        subprocess.run([sys.executable, "scripts/check_public_files.py", "--archive", str(archive)], check=True, cwd=root)
    subprocess.run([sys.executable, "-m", "twine", "check", "--strict", str(wheel), str(source)], check=True)
    subprocess.run([sys.executable, "scripts/package_smoke.py", str(wheel)], check=True, cwd=root)
    subprocess.run([sys.executable, "scripts/server_update_smoke.py", str(wheel)], check=True, cwd=root)
    subprocess.run([sys.executable, "-m", "pytest", "tests/test_server_update.py", "tests/test_server_command.py", "tests/test_updater.py"], check=True, cwd=root)
    for archive in (wheel, source):
        shutil.copy2(archive, output / archive.name)
    report = {"version": __version__, "commit": os.environ.get("GITHUB_SHA", "local"), "archive": wheel.name,
              "source": source.name, "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
              "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "package_install_check": True,
              "server_update_check": True, "server_update_protocol": 1}
    (output / "server-build.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
