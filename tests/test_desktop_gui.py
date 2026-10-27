"""Exercise native close handling without a server, browser or mailbox."""

import os
import subprocess
import sys
import textwrap

import pytest


def test_gui_closes_after_stopping_without_recursive_quit(tmp_path):
    pytest.importorskip("PySide6")
    script = textwrap.dedent("""
        import sys
        from pathlib import Path
        from copydesk import desktop_ui

        class Runtime:
            starts = stops = 0

            def __init__(self, root):
                self.server = None

            def start(self):
                type(self).starts += 1
                self.server = True
                return "http://127.0.0.1:5080"

            def stop(self):
                if self.server:
                    type(self).stops += 1
                self.server = None

        desktop_ui.DesktopRuntime = Runtime
        assert desktop_ui.run(data_dir=Path(sys.argv[1]), open_browser=False, smoke=True) == 0
        assert (Runtime.starts, Runtime.stops) == (2, 2)
    """)
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
