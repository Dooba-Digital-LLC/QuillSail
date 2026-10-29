"""Reach a running desktop launcher: `quillsail quit` and a second launch."""

import os
import socket
import sys
import threading
from contextlib import contextmanager

import pytest
from click.testing import CliRunner

from copydesk.cli import main
from copydesk.launcher import control_address

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the fake launcher uses a Unix socket")


@contextmanager
def fake_launcher(folder):
    """Answer one command like the desktop launcher, then stop listening."""
    address = control_address(folder)
    received = []
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(address)
    server.listen()

    def answer():
        with server:
            connection, _ = server.accept()
            with connection, connection.makefile("rb") as stream:
                received.append(stream.readline().decode().strip())
                connection.sendall(b"ok\n")
        os.unlink(address)

    thread = threading.Thread(target=answer, daemon=True)
    thread.start()
    try:
        yield received
    finally:
        thread.join(5)


def test_each_registry_has_its_own_short_private_address(tmp_path):
    first, second = control_address(tmp_path / "a"), control_address(tmp_path / "b")
    assert first != second and first == control_address(tmp_path / "a")
    assert len(os.fsencode(first)) < 100


def test_quit_asks_the_open_launcher_and_waits_for_it_to_close(tmp_path, monkeypatch):
    monkeypatch.setenv("QUILLSAIL_INSTANCE_HOME", str(tmp_path))
    with fake_launcher(tmp_path) as received:
        result = CliRunner().invoke(main, ["quit"])
    assert result.exit_code == 0, result.output
    assert received == ["quit"]
    assert "QuillSail has quit" in result.output


def test_quit_reports_when_quillsail_is_not_open(tmp_path, monkeypatch):
    monkeypatch.setenv("QUILLSAIL_INSTANCE_HOME", str(tmp_path))
    result = CliRunner().invoke(main, ["quit"])
    assert result.exit_code == 1 and "QuillSail isn't open." in result.output


def test_a_second_launch_shows_the_open_launcher(tmp_path):
    pytest.importorskip("PySide6")
    from copydesk import desktop_ui

    root = tmp_path / "default"
    with fake_launcher(root.with_name("default-instances")) as received:
        assert desktop_ui.run(data_dir=root, open_browser=False) == 0
    assert received == ["show"]
    # The second launch returns before creating a publication or its log.
    assert not root.exists()
