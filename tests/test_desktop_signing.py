import importlib.util
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("desktop_signing", ROOT / "scripts/desktop_signing.py")
signing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(signing)


def thin(filetype, magic=b"\xcf\xfa\xed\xfe"):
    order = signing.THIN[magic]
    return magic + struct.pack(order + "III", 0x0100000C, 0, filetype) + bytes(16)


def fat(*slices):
    header = b"\xca\xfe\xba\xbe" + struct.pack(">I", len(slices))
    offset, entries, body = 4096, b"", b""
    for item in slices:
        entries += struct.pack(">5I", 0, 0, offset + len(body), len(item), 12)
        body += item
    return (header + entries).ljust(offset, b"\0") + body


@pytest.mark.parametrize(("content", "expected"), [
    (thin(2), 2), (thin(6), 6), (thin(8), 8),
    (thin(2, b"\xfe\xed\xfa\xce"), 2),
    (fat(thin(2), thin(2)), 2), (fat(thin(6)), 6),
    (b"\xca\xfe\xba\xbe\x00\x00\x00\x41" + bytes(32), None),  # A Java class file, not a fat binary.
    (b"#!/bin/sh\n", None), (b"", None), (b"\xcf\xfa", None),
])
def test_mach_o_files_and_executables_are_recognized(tmp_path, content, expected):
    path = tmp_path / "file"
    path.write_bytes(content)
    assert signing.macho_type(path) == expected


@pytest.mark.parametrize(("environment", "expected"), [
    ({}, "ad-hoc"),
    ({"QUILLSAIL_NOTARY_PROFILE": "notary"}, "ad-hoc"),
    ({"QUILLSAIL_MACOS_IDENTITY": "Developer ID Application: Example (TEAMID1234)"}, "developer-id"),
    ({"QUILLSAIL_MACOS_IDENTITY": "Developer ID Application: Example (TEAMID1234)",
      "QUILLSAIL_NOTARY_PROFILE": "notary"}, "developer-id-notarized"),
])
def test_signature_status_follows_the_configured_identity(monkeypatch, environment, expected):
    for name in ("QUILLSAIL_MACOS_IDENTITY", "QUILLSAIL_NOTARY_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    assert signing.status() == expected


def test_signing_goes_inside_out_with_entitlements_only_on_executables(tmp_path, monkeypatch):
    monkeypatch.setenv("QUILLSAIL_MACOS_IDENTITY", "Developer ID Application: Example (TEAMID1234)")
    monkeypatch.setenv("QUILLSAIL_MACOS_KEYCHAIN", "/tmp/signing.keychain-db")
    app = tmp_path / "QuillSail.app"
    framework = app / "Contents/Frameworks/QtCore.framework"
    (framework / "Versions/A").mkdir(parents=True)
    (framework / "Versions/A/QtCore").write_bytes(thin(6))
    (framework / "QtCore").symlink_to("Versions/A/QtCore")
    (app / "Contents/MacOS").mkdir()
    (app / "Contents/MacOS/QuillSail").write_bytes(thin(2))
    helper = app / "Contents/Resources/updater"
    helper.mkdir(parents=True)
    (helper / "QuillSailUpdater").write_bytes(fat(thin(2), thin(2)))
    (helper / "module.so").write_bytes(thin(8))
    (helper / "notes.txt").write_text("not code")
    calls = []
    monkeypatch.setattr(signing, "run", lambda *args, **_: calls.append([str(arg) for arg in args]))

    signing.sign_app(app)

    names = [Path(path).name for call in calls for path in call if path.startswith(str(app))]
    assert set(names[:2]) == {"QtCore", "module.so"}
    assert set(names[2:4]) == {"QuillSail", "QuillSailUpdater"}
    assert names[4:] == ["QtCore.framework", "QuillSail.app"]
    assert "notes.txt" not in names
    assert ["--entitlements" in call for call in calls] == [False, True, False, True]
    assert all("runtime" in call and "--timestamp" in call and "--deep" not in call for call in calls)
    assert all("/tmp/signing.keychain-db" in call for call in calls)


def test_builds_without_an_identity_stay_ad_hoc_signed(tmp_path, monkeypatch):
    monkeypatch.delenv("QUILLSAIL_MACOS_IDENTITY", raising=False)
    calls = []
    monkeypatch.setattr(signing, "run", lambda *args, **_: calls.append([str(arg) for arg in args]))
    signing.sign_app(tmp_path / "QuillSail.app")
    assert calls == [["codesign", "--force", "--deep", "--sign", "-", str(tmp_path / "QuillSail.app")]]
