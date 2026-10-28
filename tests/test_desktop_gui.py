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


@pytest.mark.parametrize("scenario", ["available", "current", "offline", "install"])
def test_update_controls_keep_publication_running_until_an_approved_install(tmp_path, scenario):
    pytest.importorskip("PySide6")
    script = textwrap.dedent("""
        import sys
        from pathlib import Path
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QMessageBox
        from copydesk import __version__, desktop_ui, updater

        scenario, root = sys.argv[1], Path(sys.argv[2])
        calls = []

        class Runtime:
            def __init__(self, root):
                self.server = None
            def start(self):
                calls.append('start')
                self.server = True
                return 'http://127.0.0.1:5080'
            def stop(self):
                if self.server:
                    calls.append('stop')
                self.server = None

        class Client:
            def check(self, **kwargs):
                if scenario == 'offline':
                    raise updater.UpdateError('Synthetic offline update service')
                return updater.Update(__version__, __version__ if scenario == 'current' else '9.0.0',
                                      'linux-x86_64', 'beta', 'synthetic.zip', 123, 'a'*64)
            def prepare(self, update, installation, **kwargs):
                assert calls == ['start']
                calls.append('prepare')
                return root / 'synthetic-plan.json'

        def handoff(plan):
            assert calls == ['start', 'prepare', 'stop']
            calls.append('handoff')

        desktop_ui.DesktopRuntime = Runtime
        updater.UpdateClient = Client
        updater.saved_channel = lambda: 'beta'
        updater.save_channel = lambda channel: None
        updater.installation_path = lambda: root / 'synthetic.app'
        updater.launch_installer = handoff
        QMessageBox.exec = lambda self: QMessageBox.StandardButton.Ok
        app = QApplication([])
        state = [0]
        errors = []

        def inspect():
            try:
                windows = [w for w in app.topLevelWidgets() if isinstance(w, QMainWindow)]
                if not windows:
                    return
                window = windows[0]
                if state[0] == 0 and not window.busy and window.runtime.server:
                    window.resize(460, 500)
                    app.processEvents()
                    for label in window.findChildren(QLabel):
                        if label.wordWrap():
                            assert label.height() >= label.heightForWidth(label.width()), label.text()
                    state[0] = 1
                    window.update_button.click()
                elif state[0] == 1 and not window.busy:
                    assert calls == ['start']
                    assert window.open_button.isEnabled()
                    assert not window.update_progress.isVisible()
                    if scenario == 'offline':
                        assert 'Synthetic offline' in window.update_status.text()
                        window.close()
                        state[0] = 3
                    elif scenario == 'current':
                        assert 'up to date' in window.update_status.text()
                        window.close()
                        state[0] = 3
                    elif scenario == 'available':
                        assert '9.0.0' in window.update_button.text()
                        window.update_channel.setCurrentIndex(0)
                        assert window.available_update is None
                        assert window.update_button.text() == 'Check for updates'
                        window.close()
                        state[0] = 3
                    else:
                        state[0] = 2
                        window.update_button.click()
            except BaseException as exc:
                errors.append(repr(exc))
                app.exit(1)

        timer = QTimer()
        timer.timeout.connect(inspect)
        timer.start(10)
        QTimer.singleShot(10_000, lambda: (errors.append('GUI test timed out'), app.exit(1)))
        assert desktop_ui.run(data_dir=root, open_browser=False) == 0, errors
        assert not errors, errors
        assert calls == (['start', 'prepare', 'stop', 'handoff'] if scenario == 'install' else ['start', 'stop']), calls
    """)
    result = subprocess.run([sys.executable, "-c", script, scenario, str(tmp_path)],
                            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}, capture_output=True,
                            text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('action', ['close', 'update'])
def test_instance_switching_keeps_other_publications_running_and_shutdown_stops_all(tmp_path, action):
    pytest.importorskip('PySide6')
    script = textwrap.dedent('''
        import sys
        from pathlib import Path
        import requests
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication, QInputDialog, QMainWindow, QMessageBox
        from copydesk import __version__, desktop_ui, updater

        root, action = Path(sys.argv[1]), sys.argv[2]
        app = QApplication([])
        errors, owned, handoffs = [], [], []
        phase = [0]
        QInputDialog.getText = lambda *a, **k: ('Second publication', True)
        QMessageBox.exec = lambda self: QMessageBox.StandardButton.Ok
        updater.saved_channel = lambda: 'beta'
        updater.save_channel = lambda value: None
        updater.installation_path = lambda: root / 'synthetic.app'
        class Client:
            def check(self, **kwargs):
                return updater.Update(__version__, '9.0.0', 'linux-x86_64', 'beta', 'synthetic.zip', 123, 'a'*64)
            def prepare(self, *args, **kwargs):
                assert all(runtime.server for runtime in owned)
                return root / 'plan.json'
        updater.UpdateClient = Client
        def handoff(plan):
            assert all(runtime.server is None for runtime in owned)
            handoffs.append(plan)
        updater.launch_installer = handoff

        def inspect():
            try:
                windows = [w for w in app.topLevelWidgets() if isinstance(w, QMainWindow)]
                if not windows:
                    return
                window = windows[0]
                if window.busy:
                    return
                if phase[0] == 0 and window.runtime.server:
                    owned.append(window.runtime)
                    phase[0] = 1
                    window.new_instance.click()
                elif phase[0] == 1 and window.runtime.server and len(window.runtimes) == 2:
                    owned.append(window.runtime)
                    assert owned[0] is not owned[1]
                    assert owned[0].url != owned[1].url
                    window.instance_selector.setCurrentIndex(window.instance_selector.findData('default'))
                    assert window.runtime is owned[0]
                    assert all(runtime.server for runtime in owned)
                    phase[0] = 2
                    if action == 'close':
                        window.toggle.click()
                    else:
                        window.update_button.click()
                elif phase[0] == 2:
                    if action == 'close':
                        assert owned[0].server is None
                        assert requests.get(owned[1].url + '/healthz', timeout=5).status_code == 200
                        phase[0] = 3
                        window.close()
                    elif window.available_update:
                        phase[0] = 3
                        window.update_button.click()
            except BaseException as exc:
                errors.append(repr(exc))
                app.exit(1)
        timer = QTimer()
        timer.timeout.connect(inspect)
        timer.start(20)
        QTimer.singleShot(20_000, lambda: (errors.append('Multiple-instance GUI test timed out'), app.exit(1)))
        assert desktop_ui.run(data_dir=root, open_browser=False) == 0, errors
        assert not errors and phase[0] == 3, errors
        assert len(owned) == 2 and all(runtime.server is None for runtime in owned)
        assert len(handoffs) == (1 if action == 'update' else 0)
    ''')
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path / 'default'), action],
                            env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}, capture_output=True,
                            text=True, timeout=35)
    assert result.returncode == 0, result.stdout + result.stderr
