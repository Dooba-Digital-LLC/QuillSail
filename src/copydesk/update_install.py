"""Small, standard-library-only installer run outside the application being replaced.

The main updater authenticates downloads with TUF. This helper rechecks the exact
length and digest, extracts defensively, waits for the old process, and swaps
directories on the same filesystem. It never opens a publication database.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath

MARKER = "quillsail-install.json"
PROTOCOL = 1
MAX_EXPANDED = 8 * 1024**3
MAX_FILES = 50_000


class InstallError(RuntimeError):
    pass


@contextlib.contextmanager
def application_lock(path: Path):
    """One launcher or installer may hold this installation's OS lock."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+b") as handle:
        os.chmod(path, 0o600)
        if path.stat().st_size == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise InstallError("Close all QuillSail windows for this application before updating or opening another copy.") from exc
        yield


def executable(root: Path, target: str) -> Path:
    if target.startswith("macos-"):
        return root / "Contents/MacOS/QuillSail"
    return root / ("QuillSail.exe" if target.startswith("windows-") else "QuillSail")


def marker_path(root: Path, target: str) -> Path:
    return root / "Contents/Resources" / MARKER if target.startswith("macos-") else root / MARKER


def read_install(root: Path, target: str | None = None) -> dict:
    if target is None:
        target = "macos-" if root.suffix == ".app" else "portable"
    try:
        info = json.loads(marker_path(root, target).read_text(encoding="utf-8"))
        if (info.get("format") != "quillsail.install.v1"
                or info.get("target") not in {"macos-arm64", "macos-x86_64", "windows-x86_64", "linux-x86_64"}
                or not isinstance(info.get("version"), str)
                or not executable(root, info["target"]).is_file()):
            raise ValueError
        return info
    except (OSError, ValueError, TypeError) as exc:
        raise InstallError("Choose a QuillSail desktop application with update support.") from exc


def verify_archive(path: Path, length: int, digest: str):
    if path.is_symlink() or path.stat().st_size != length:
        raise InstallError("The downloaded update has the wrong size.")
    check = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            check.update(block)
    if check.hexdigest() != digest:
        raise InstallError("The downloaded update did not pass its checksum check.")


def extract_archive(archive: Path, destination: Path, target: str) -> Path:
    """Extract regular files, directories and contained relative symlinks only."""
    destination = destination.resolve()
    root_name = "QuillSail.app" if target.startswith("macos-") else "QuillSail"
    destination.mkdir(mode=0o700)
    seen, links, total = set(), [], 0

    def member_path(name, size):
        nonlocal total
        name = name.rstrip("/")
        parts = name.split("/")
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        if (not parts or name.startswith("/") or "\\" in name or ":" in name
                or "\0" in name or any(not part or part in {".", ".."} or part.rstrip(" .") != part
                                       or part.split(".", 1)[0].upper() in reserved for part in parts)):
            raise InstallError("The update contains an unsafe archive path.")
        if parts[0] == "__MACOSX" and target.startswith("macos-"):
            return None
        if parts[0] != root_name:
            raise InstallError("The update contains an unexpected application folder.")
        key = name.casefold()
        if key in seen or len(seen) >= MAX_FILES or size < 0:
            raise InstallError("The update contains duplicate files or too many files.")
        seen.add(key)
        total += size
        if total > MAX_EXPANDED:
            raise InstallError("The expanded update is too large.")
        path = destination.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def unpack(name, size, mode, stream, *, directory=False, link=None):
        path = member_path(name, size)
        if path is None:
            return
        if directory:
            path.mkdir(exist_ok=True)
        elif link is not None:
            if not link or PurePosixPath(link).is_absolute() or "\\" in link or ":" in link:
                raise InstallError("The update contains an unsafe symbolic link.")
            resolved = (path.parent / link).resolve()
            if not resolved.is_relative_to(destination / root_name):
                raise InstallError("An update link points outside the application.")
            links.append((path, link))
        else:
            with path.open("xb") as output:
                shutil.copyfileobj(stream, output, length=1024 * 1024)
            if path.stat().st_size != size:
                raise InstallError("An extracted update file has the wrong size.")
            os.chmod(path, 0o755 if mode & 0o111 else 0o644)

    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as handle:
            for item in handle.infolist():
                mode = item.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if kind not in {0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK}:
                    raise InstallError("The update contains an unsupported file type.")
                with handle.open(item) as stream:
                    link = stream.read(4097).decode("utf-8") if kind == stat.S_IFLNK else None
                    if link is not None and len(link) > 4096:
                        raise InstallError("An update link is too long.")
                    unpack(item.filename, item.file_size, mode, stream, directory=item.is_dir(), link=link)
    else:
        with tarfile.open(archive, "r:gz") as handle:
            for item in handle:
                if not (item.isfile() or item.isdir() or item.issym()):
                    raise InstallError("The update contains an unsupported file type.")
                stream = handle.extractfile(item) if item.isfile() else None
                if stream is None:
                    unpack(item.name, item.size, item.mode, None, directory=item.isdir(), link=item.linkname if item.issym() else None)
                else:
                    with stream:
                        unpack(item.name, item.size, item.mode, stream)
    for path, link in links:
        # No archive file is ever written through an archive-supplied symlink.
        path.symlink_to(link)
    root = destination / root_name
    for path, _link in links:
        try:
            if not path.resolve().is_relative_to(root):
                raise InstallError("An update link points outside the application.")
        except RuntimeError as exc:
            raise InstallError("The update contains a symbolic link cycle.") from exc
    return root


def wait_for_exit(pid: int, timeout=120):
    if pid <= 0 or pid == os.getpid():
        raise InstallError("The updater received an invalid application process.")
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        process = kernel.OpenProcess(0x00100000, False, pid)
        if not process:
            if ctypes.get_last_error() == 87:  # Process already exited.
                return
            raise InstallError("The updater could not wait for QuillSail to close.")
        try:
            if kernel.WaitForSingleObject(process, int(timeout * 1000)) != 0:
                raise InstallError("Close all QuillSail windows, then try the update again.")
        finally:
            kernel.CloseHandle(process)
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.2)
    raise InstallError("Close all QuillSail windows, then try the update again.")


def atomic_install(plan: dict):
    """Keep the previous executable tree and restore it if replacement fails."""
    with application_lock(Path(plan["install_lock"])):
        return _atomic_install(plan)


def _atomic_install(plan: dict):
    transaction = Path(plan["transaction"]).resolve()
    supplied = Path(plan["installation"])
    install = supplied.resolve()
    if (not transaction.name.startswith(".quillsail-update-") or transaction.parent != install.parent
            or supplied.is_symlink() or install == transaction):
        raise InstallError("The update transaction has an invalid location.")
    target = plan["target"]
    current = read_install(install, target)
    if current["target"] != target or current["version"] != plan["previous_version"]:
        raise InstallError("The installed application changed. Check for updates again.")
    archive = transaction / "update.archive"
    verify_archive(archive, plan["length"], plan["sha256"])
    candidate = extract_archive(archive, transaction / "unpacked", target)
    info = read_install(candidate, target)
    if info["target"] != target or info["version"] != plan["version"]:
        raise InstallError("The downloaded application does not match the signed update.")
    if target.startswith("macos-") and sys.platform == "darwin":
        result = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(candidate)],
                                env=clean_environment(), capture_output=True, timeout=60)
        if result.returncode:
            raise InstallError("The extracted Mac application did not pass its code-signature check.")
    previous = transaction / "previous"
    # Both moves are local renames. Publication files live outside these trees.
    os.replace(install, previous)
    try:
        os.replace(candidate, install)
    except OSError:
        os.replace(previous, install)
        raise
    return install


def cleanup_completed_transactions(installation: Path, current: Path):
    """Keep this update's previous application; remove older successful updates."""
    for old in installation.parent.glob(".quillsail-update-*"):
        try:
            if old == current or old.is_symlink() or not old.is_dir():
                continue
            if (old / "plan.json").stat().st_size > 16_384 or (old / "result.json").stat().st_size > 16_384:
                continue
            plan = json.loads((old / "plan.json").read_text(encoding="utf-8"))
            result = json.loads((old / "result.json").read_text(encoding="utf-8"))
            if (Path(plan["transaction"]).resolve() == old.resolve()
                    and Path(plan["installation"]).resolve() == installation.resolve()
                    and result.get("status") == "installed"):
                shutil.rmtree(old)
        except (OSError, ValueError, KeyError, TypeError):
            continue


def record_success(plan, install):
    transaction = Path(plan["transaction"])
    (transaction / "result.json").write_text(json.dumps({"status": "installed", "version": plan["version"]}), encoding="utf-8")
    (transaction / "update.archive").unlink(missing_ok=True)
    cleanup_completed_transactions(install, transaction)


def apply_plan(path: Path):
    transaction = path.parent.resolve()
    plan = json.loads(path.read_text(encoding="utf-8"))
    if (plan.get("protocol") != PROTOCOL or path.name != "plan.json"
            or Path(plan["transaction"]).resolve() != transaction):
        raise InstallError("The update plan is not supported.")
    (transaction / "helper-ready").write_text("ready", encoding="utf-8")
    wait_for_exit(plan["parent_pid"])
    install = atomic_install(plan)
    record_success(plan, install)
    if plan.get("restart"):
        command = [str(executable(install, plan["target"])), "--data-dir", plan["data_dir"]]
        subprocess.Popen(command, cwd=install.parent, env=clean_environment(), start_new_session=True)


def clean_environment():
    environment = dict(os.environ)
    for key in list(environment):
        if key.startswith(("_PYI_", "PYTHON", "QT_", "QML", "DYLD_")):
            environment.pop(key, None)
    original = environment.pop("LD_LIBRARY_PATH_ORIG", None)
    environment.pop("LD_LIBRARY_PATH", None)
    if original:
        environment["LD_LIBRARY_PATH"] = original
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return environment


def smoke_test():
    command = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, str(Path(__file__).resolve())]
    child = subprocess.Popen([*command, "--smoke-wait-child"], env=clean_environment())
    reaper = threading.Thread(target=child.wait)
    reaper.start()
    try:
        wait_for_exit(child.pid, timeout=10)
    finally:
        reaper.join(timeout=10)
    assert child.returncode == 0
    with tempfile.TemporaryDirectory(prefix="quillsail-update-smoke-") as folder:
        parent = Path(folder)
        target = "windows-x86_64" if sys.platform == "win32" else "linux-x86_64"
        install = parent / "QuillSail"
        install.mkdir()
        exe = executable(install, target)
        exe.write_text("old executable", encoding="utf-8")
        marker = {"format": "quillsail.install.v1", "target": target, "version": "0.0.1"}
        marker_path(install, target).write_text(json.dumps(marker), encoding="utf-8")
        transaction = Path(tempfile.mkdtemp(prefix=".quillsail-update-", dir=parent))
        archive = transaction / "update.archive"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("QuillSail/" + exe.name, "new executable")
            marker["version"] = "0.0.2"
            output.writestr("QuillSail/" + MARKER, json.dumps(marker))
        plan = {"transaction": str(transaction), "installation": str(install), "target": target,
                "previous_version": "0.0.1", "version": "0.0.2", "length": archive.stat().st_size,
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "install_lock": str(parent / "app.lock")}
        atomic_install(plan)
        assert executable(install, target).read_text() == "new executable"
        assert executable(transaction / "previous", target).read_text() == "old executable"
    print(json.dumps({"updater_install_check": True, "publication_files_opened": False}))


def main():
    parser = argparse.ArgumentParser(description="QuillSail update installer")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--smoke-wait-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.smoke_wait_child:
        time.sleep(0.5)
        return
    if args.smoke_test:
        smoke_test()
        return
    if not args.plan:
        parser.error("an update plan is required")
    try:
        apply_plan(args.plan)
    except Exception as exc:  # noqa: BLE001 - a detached helper must preserve the error
        (args.plan.parent / "result.json").write_text(json.dumps({"status": "failed", "error": str(exc)}), encoding="utf-8")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
