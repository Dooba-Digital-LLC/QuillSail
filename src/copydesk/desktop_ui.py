"""Native launcher. Qt is optional for server and command-line installations."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from logging.handlers import RotatingFileHandler
from pathlib import Path

from copydesk import __version__
from copydesk.desktop import DesktopRuntime, default_data_dir, smoke_test

log = logging.getLogger("copydesk.desktop")


def run(*, data_dir: Path | None = None, open_browser=True, smoke=False):
    try:
        from PySide6.QtCore import QEvent, QObject, QSize, QThread, QTimer, QUrl, Signal, Slot
        from PySide6.QtGui import QAction, QDesktopServices, QIcon, QPixmap
        from PySide6.QtWidgets import (
            QApplication,
            QHBoxLayout,
            QLabel,
            QMainWindow,
            QPushButton,
            QVBoxLayout,
            QWidget,
        )
    except ImportError as exc:
        raise RuntimeError("Install the desktop extra with: pip install 'copydesk[desktop]'") from exc

    root = (data_dir or default_data_dir()).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    logfile = root / "quillsail.log"
    # Create with private permissions before logging opens the file.
    descriptor = os.open(logfile, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    os.close(descriptor)
    os.chmod(logfile, 0o600)
    handler = RotatingFileHandler(logfile, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)

    class Task(QObject):
        finished = Signal(object)
        failed = Signal(str)

        def __init__(self, operation):
            super().__init__()
            self.operation = operation

        @Slot()
        def execute(self):
            try:
                self.finished.emit(self.operation())
            except Exception as exc:  # noqa: BLE001 - report errors inside the GUI
                log.exception("Desktop operation failed")
                self.failed.emit(str(exc))

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            self.runtime = DesktopRuntime(root)
            self.busy = self.closing = False
            self.task = self.thread = None
            self.smoke_starts, self.smoke_error = 0, False
            self.setWindowTitle("QuillSail")
            self.setMinimumSize(460, 400)
            self.resize(540, 460)
            icon_path = Path(__file__).parent / "static/brand/quillsail.png"
            self.setWindowIcon(QIcon(str(icon_path)))
            content = QWidget()
            layout = QVBoxLayout(content)
            layout.setContentsMargins(32, 28, 32, 28)
            layout.setSpacing(18)
            brand = QHBoxLayout()
            image = QLabel()
            image.setPixmap(QPixmap(str(icon_path)).scaledToHeight(64))
            image.setFixedSize(QSize(78, 68))
            brand.addWidget(image)
            title = QLabel("<h1>QuillSail</h1><p>Your publication, on your computer.</p>")
            brand.addWidget(title, 1)
            layout.addLayout(brand)
            self.status = QLabel("Starting your publication…")
            self.status.setObjectName("status")
            self.status.setWordWrap(True)
            layout.addWidget(self.status)
            self.address = QLabel("The dashboard will open in your browser.")
            self.address.setWordWrap(True)
            layout.addWidget(self.address)
            self.open_button = QPushButton("Open dashboard")
            self.open_button.setDefault(True)
            self.open_button.setMinimumHeight(44)
            self.open_button.setObjectName("primary")
            self.open_button.clicked.connect(self.open_dashboard)
            layout.addWidget(self.open_button)
            row = QHBoxLayout()
            self.toggle = QPushButton("Start")
            self.toggle.clicked.connect(lambda: self.stop() if self.runtime.server else self.start())
            row.addWidget(self.toggle)
            logs = QPushButton("View logs")
            logs.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(logfile))))
            row.addWidget(logs)
            files = QPushButton("Open data folder")
            files.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(root))))
            row.addWidget(files)
            layout.addLayout(row)
            note = QLabel("Your local website and background jobs run while QuillSail is open.\nYour content stays saved when you stop or update the app.")
            note.setWordWrap(True)
            layout.addWidget(note)
            layout.addStretch()
            version = QLabel(f"Version {__version__} · Local access only")
            version.setObjectName("version")
            layout.addWidget(version)
            self.setCentralWidget(content)
            self.setStyleSheet("""
                QMainWindow { background: #faf9fc; }
                QLabel { color: #272331; font-size: 13px; }
                QLabel#status { background: #eee8fa; border-radius: 8px; padding: 14px; font-weight: 600; }
                QLabel#version { color: #706879; font-size: 11px; }
                QPushButton { padding: 9px 12px; border: 1px solid #d8d1e1; border-radius: 7px; background: white; color: #332b40; }
                QPushButton#primary { background: #7440aa; border-color: #7440aa; color: white; font-weight: 600; }
                QPushButton:disabled { color: #8c8495; background: #eeeaf2; }
                QPushButton:focus { border: 2px solid #7440aa; }
            """)
            quit_action = QAction("Quit QuillSail", self)
            quit_action.setShortcut("Ctrl+Q")
            quit_action.triggered.connect(self.close)
            self.menuBar().addMenu("QuillSail").addAction(quit_action)
            QTimer.singleShot(0, self.start)

        def operation(self, callback, completed):
            self.busy = True
            self.toggle.setEnabled(False)
            self.open_button.setEnabled(False)
            self.thread = QThread(self)
            self.task = Task(callback)
            self.task.moveToThread(self.thread)
            self.thread.started.connect(self.task.execute)
            self.task.finished.connect(completed)
            self.task.failed.connect(self.failed)
            self.task.finished.connect(self.thread.quit)
            self.task.failed.connect(self.thread.quit)
            self.thread.finished.connect(self.task.deleteLater)
            self.thread.finished.connect(self.thread.deleteLater)
            self.thread.start()

        def start(self):
            if not self.busy:
                self.status.setText("Starting your publication…")
                self.operation(self.runtime.start, self.started)

        @Slot(object)
        def started(self, url):
            self.busy = False
            self.status.setText("Your publication is running")
            self.address.setText(url)
            self.toggle.setText("Stop")
            self.toggle.setEnabled(True)
            self.open_button.setEnabled(True)
            if smoke:
                self.smoke_starts += 1
                self.thread.finished.connect(self.stop if self.smoke_starts == 1 else self.close)
            elif self.closing:
                QTimer.singleShot(0, self.stop)
            elif open_browser:
                self.open_dashboard()

        def open_dashboard(self):
            if self.runtime.server and not QDesktopServices.openUrl(QUrl(self.runtime.launch_url())):
                self.status.setText("Your browser could not be opened. Try Open dashboard again.")

        def stop(self):
            if not self.busy:
                self.status.setText("Finishing background work and stopping…")
                self.operation(self.runtime.stop, self.stopped)

        @Slot(object)
        def stopped(self, _result=None):
            self.busy = False
            self.status.setText("Stopped. Your content is saved.")
            self.address.setText("Start QuillSail to open your dashboard.")
            self.toggle.setText("Start")
            self.toggle.setEnabled(True)
            if self.closing:
                # Let the operation thread finish before its Qt objects are destroyed.
                self.thread.finished.connect(app.quit)
            elif smoke:
                self.thread.finished.connect(self.start)

        @Slot(str)
        def failed(self, message):
            self.busy = False
            self.status.setText("QuillSail could not start: " + message)
            self.address.setText("View logs for details. Your publication files are in the data folder.")
            self.toggle.setText("Start")
            self.toggle.setEnabled(True)
            if smoke:
                self.smoke_error, self.closing = True, True
            if self.closing:
                self.thread.finished.connect(app.quit)

        def closeEvent(self, event):  # noqa: N802 - Qt virtual method
            event.ignore()
            self.closing = True
            if self.busy:
                return
            if self.runtime.server:
                self.stop()
            else:
                app.quit()

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("QuillSail")
    app.setOrganizationName("QuillSail")
    window = Window()
    window.show()
    try:
        result = app.exec()
        return 1 if smoke and (window.smoke_error or window.smoke_starts != 2) else result
    finally:
        window.runtime.stop()
        # Destroy the widget tree while QApplication still exists. Leaving it
        # to Python/Qt's atexit order can double-delete child wrappers in a bundle.
        window.deleteLater()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        logging.getLogger().removeHandler(handler)
        handler.close()


def main():
    parser = argparse.ArgumentParser(description="Open QuillSail on your computer.")
    parser.add_argument("--data-dir", type=Path, help="Use a separate desktop publication folder.")
    parser.add_argument("--no-browser", action="store_true", help="Start the launcher without opening the browser.")
    parser.add_argument("--smoke-test", action="store_true", help="Check a disposable local publication, then exit.")
    parser.add_argument("--smoke-test-gui", action="store_true", help="Check native window startup, stop and restart with disposable data.")
    parser.add_argument("--version", action="version", version="QuillSail " + __version__)
    args = parser.parse_args()
    if args.smoke_test:
        smoke_test()
        return
    if args.smoke_test_gui:
        with tempfile.TemporaryDirectory(prefix="quillsail-gui-smoke-") as directory:
            raise SystemExit(run(data_dir=Path(directory), open_browser=False, smoke=True))
    raise SystemExit(run(data_dir=args.data_dir, open_browser=not args.no_browser))


if __name__ == "__main__":
    main()
