"""Build graphical first-install artifacts; updater archives remain separate."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

SUFFIXES = {"macos-arm64": ".dmg", "macos-x86_64": ".dmg", "windows-x86_64": "-setup.exe", "linux-x86_64": ".deb"}


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True, timeout=300)


def smoke(app, target):
    executable = app / ("Contents/MacOS/QuillSail" if target.startswith("macos") else "QuillSail.exe" if target.startswith("windows") else "QuillSail")
    run(executable, "--smoke-test")
    run(executable, "--smoke-test-gui")


def update_smoke(app, target, archive, version):
    from copydesk.update_install import atomic_install

    with tempfile.TemporaryDirectory(prefix=".quillsail-update-", dir=app.parent) as folder:
        transaction = Path(folder)
        shutil.copy2(archive, transaction / "update.archive")
        atomic_install({"installation": str(app), "transaction": str(transaction),
                        "install_lock": str(transaction / "install.lock"), "target": target,
                        "previous_version": version, "version": version, "length": archive.stat().st_size,
                        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()})
    smoke(app, target)


def build(output, app, target, version, icon, archive):
    artifact = output / f"QuillSail-{version}-{target}{SUFFIXES[target]}"
    with tempfile.TemporaryDirectory(prefix="quillsail-installer-") as temporary:
        work = Path(temporary)
        if target.startswith("macos"):
            stage = work / "image"
            stage.mkdir()
            run("ditto", app, stage / "QuillSail.app")
            (stage / "Applications").symlink_to("/Applications", target_is_directory=True)
            run("hdiutil", "create", "-ov", "-volname", "QuillSail", "-srcfolder", stage, "-format", "UDZO", artifact)
            mount = work / "mounted"
            run("hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", mount, artifact)
            try:
                assert (mount / "Applications").is_symlink()
                installed = work / "Applications/QuillSail.app"
                installed.parent.mkdir()
                run("ditto", mount / "QuillSail.app", installed)
            finally:
                run("hdiutil", "detach", mount)
            run("codesign", "--verify", "--deep", "--strict", installed)
            smoke(installed, target)
            update_smoke(installed, target, archive, version)
            shutil.rmtree(installed)
        elif target.startswith("windows"):
            compiler = shutil.which("ISCC") or str(Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Inno Setup 6/ISCC.exe")
            script = work / "setup.iss"
            script.write_text(f'''[Setup]
AppId=org.quillsail.desktop
AppName=QuillSail
AppVersion={version}
DefaultDirName={{localappdata}}\\Programs\\QuillSail
DefaultGroupName=QuillSail
DisableDirPage=yes
UninstallFilesDir={{app}}-uninstall
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={output}
OutputBaseFilename={artifact.stem}
SetupIconFile={icon}
UninstallDisplayIcon={{app}}\\QuillSail.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
[Files]
Source: "{app}\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{{group}}\\QuillSail"; Filename: "{{app}}\\QuillSail.exe"
Name: "{{userdesktop}}\\QuillSail"; Filename: "{{app}}\\QuillSail.exe"; Tasks: desktopicon
[UninstallDelete]
Type: filesandordirs; Name: "{{app}}"
[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; Flags: unchecked
[Run]
Filename: "{{app}}\\QuillSail.exe"; Description: "Launch QuillSail"; Flags: nowait postinstall skipifsilent
''', encoding="utf-8")
            run(compiler, script)
            installed = work / "installed"
            group = "QuillSail installer test-" + work.name
            shortcut = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs" / group / "QuillSail.lnk"
            for _ in range(2):  # reinstall/upgrade uses the same application directory
                run(artifact, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/DIR={installed}", f"/GROUP={group}")
                assert shortcut.is_file()
                smoke(installed, target)
                update_smoke(installed, target, archive, version)
            run(installed.with_name(installed.name + "-uninstall") / "unins000.exe", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART")
            for _ in range(100):
                if not (installed / "QuillSail.exe").exists():
                    break
                time.sleep(0.1)
            assert not shortcut.exists()
            if (installed / "QuillSail.exe").exists():
                raise RuntimeError("Uninstall left the application executable behind.")
        else:
            stage = work / "package"
            control = stage / "DEBIAN"
            control.mkdir(parents=True)
            control.joinpath("control").write_text(f'''Package: quillsail-desktop
Version: {version}
Architecture: amd64
Maintainer: QuillSail contributors
Section: editors
Priority: optional
Depends: libegl1, libopengl0, libxkbcommon-x11-0, libxcb-cursor0, libxcb-icccm4, libxcb-keysyms1, libxcb-shape0, libxcb-xinerama0, libxcb-render-util0, libxcb-xfixes0
Description: QuillSail blog and newsletter desktop app
''', encoding="utf-8")
            shutil.copytree(app, stage / "opt/QuillSail", symlinks=True)
            applications = stage / "usr/share/applications"
            applications.mkdir(parents=True)
            applications.joinpath("quillsail.desktop").write_text('''[Desktop Entry]
Type=Application
Name=QuillSail
Comment=Blog and newsletter desktop app
Exec=/opt/QuillSail/QuillSail
Icon=quillsail
Terminal=false
Categories=Office;Publishing;
StartupWMClass=QuillSail
''', encoding="utf-8")
            icons = stage / "usr/share/icons/hicolor/scalable/apps"
            icons.mkdir(parents=True)
            shutil.copy2(icon, icons / "quillsail.svg")
            run("desktop-file-validate", applications / "quillsail.desktop")
            run("dpkg-deb", "--root-owner-group", "--build", stage, artifact)
            # An isolated dpkg database exercises install, reinstall and removal
            # without modifying the runner's applications or publication data.
            installed = work / "root"
            database = installed / "var/lib/dpkg"
            database.mkdir(parents=True)
            (database / "status").touch()
            for _ in range(2):
                run("dpkg", "--force-not-root", f"--root={installed}", "--unpack", artifact)
                smoke(installed / "opt/QuillSail", target)
                update_smoke(installed / "opt/QuillSail", target, archive, version)
                assert (installed / "usr/share/applications/quillsail.desktop").is_file()
            run("dpkg", "--force-not-root", f"--root={installed}", "--remove", "quillsail-desktop")
            assert not (installed / "opt/QuillSail/QuillSail").exists()
    return artifact
