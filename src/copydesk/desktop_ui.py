"""Native launcher. Qt is optional for server and command-line installations."""

from __future__ import annotations

import argparse
import faulthandler
import logging
import os
import sys
import tempfile
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from copydesk import __version__
from copydesk.desktop import DesktopRuntime, default_data_dir, smoke_test

log = logging.getLogger("copydesk.desktop")


def run(*, data_dir: Path | None = None, instance_id: str | None = None, open_browser=True, smoke=False):
    try:
        from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, QUrl, Signal, Slot
        from PySide6.QtGui import QAction, QDesktopServices, QIcon
        from PySide6.QtWidgets import (
            QApplication,
            QComboBox,
            QFileDialog,
            QHBoxLayout,
            QInputDialog,
            QLabel,
            QMainWindow,
            QMessageBox,
            QProgressBar,
            QPushButton,
            QScrollArea,
            QVBoxLayout,
            QWidget,
        )
    except ImportError as exc:
        raise RuntimeError("Install the desktop extra with: pip install 'copydesk[desktop]'") from exc

    from copydesk.instances import InstanceRegistry, running_url
    from copydesk.update_install import InstallError, application_lock, read_install
    from copydesk.updater import (
        UpdateClient,
        install_lock_path,
        installation_path,
        last_install_result,
        launch_installer,
        save_channel,
        saved_channel,
    )

    default_root = default_data_dir().expanduser().resolve()
    root = (data_dir or default_root).expanduser().resolve()
    registry = InstanceRegistry()
    registered = next((item for item in registry.list() if item.root == root), None) if data_dir else None
    if data_dir and root != default_root and registered is None:
        registry = InstanceRegistry(root.with_name(root.name + "-instances"), default_root=root)
    selected = registry.get(instance_id or (registered.id if registered else "default"))
    root = selected.root
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
        progress = Signal(str, int)

        def __init__(self, operation, parent):
            super().__init__(parent)
            self.operation = operation

        @Slot()
        def execute(self):
            try:
                self.finished.emit(self.operation())
            except Exception as exc:  # noqa: BLE001 - report errors inside the GUI
                log.exception("Desktop operation failed")
                self.failed.emit(str(exc))

    class Logo(QLabel):
        def __init__(self, path):
            super().__init__()
            self.icon = QIcon(str(path))
            self.setFixedSize(QSize(78, 68))
            self.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.setAccessibleName("QuillSail logo")
            self.refresh()

        def refresh(self):
            # Request physical pixels for the current screen from the SVG engine.
            self.setPixmap(self.icon.pixmap(QSize(78, 64), self.devicePixelRatioF()))

        def event(self, event):
            result = super().event(event)
            if event.type() in {QEvent.Type.Show, QEvent.Type.DevicePixelRatioChange} and hasattr(self, "icon"):
                self.refresh()
            return result

    class Window(QMainWindow):
        def __init__(self):
            super().__init__()
            self.runtime = DesktopRuntime(root)
            self.runtimes = {selected.id: self.runtime}
            self.selected_instance = selected
            self.busy = self.closing = False
            self.task = self.thread = None
            self.smoke_starts, self.smoke_error = 0, False
            self.available_update = self.update_plan = None
            self.setWindowTitle("QuillSail")
            self.setMinimumSize(460, 600)
            self.resize(540, 620)
            icon_path = Path(__file__).parent / "static/brand/quillsail.svg"
            self.setWindowIcon(QIcon(str(icon_path)))
            content = QWidget()
            content.setObjectName("launcher-content")
            layout = QVBoxLayout(content)
            layout.setContentsMargins(32, 28, 32, 28)
            layout.setSpacing(18)
            brand = QHBoxLayout()
            image = Logo(icon_path)
            self.logo_ready = not image.pixmap().isNull()
            if not self.logo_ready:
                log.error("The packaged QuillSail vector logo could not be rendered.")
            brand.addWidget(image)
            title = QLabel("<h1 style='margin:0 0 4px'>QuillSail</h1><p style='margin:0'>Your publication, on your computer.</p>")
            title.setWordWrap(True)
            title.setMinimumHeight(title.heightForWidth(240))
            brand.addWidget(title, 1)
            layout.addLayout(brand)
            instance_label = QLabel("Publication instance")
            self.instance_selector = QComboBox()
            self.instance_selector.setObjectName("instance-selector")
            self.instance_selector.setAccessibleName("Publication instance")
            instance_label.setBuddy(self.instance_selector)
            layout.addWidget(instance_label)
            layout.addWidget(self.instance_selector)
            instance_row = QHBoxLayout()
            self.new_instance = QPushButton("New instance…")
            self.new_instance.clicked.connect(self.create_instance)
            instance_row.addWidget(self.new_instance)
            self.add_instance = QPushButton("Add existing…")
            self.add_instance.clicked.connect(self.add_existing_instance)
            instance_row.addWidget(self.add_instance)
            self.refresh_button = QPushButton("Refresh")
            self.refresh_button.clicked.connect(lambda: self.refresh_instances(self.selected_instance.id))
            instance_row.addWidget(self.refresh_button)
            layout.addLayout(instance_row)
            self.refresh_instances(selected.id)
            self.instance_selector.currentIndexChanged.connect(self.select_instance)
            self.instance_summary = QLabel("No instances running in this launcher.")
            self.instance_summary.setWordWrap(True)
            layout.addWidget(self.instance_summary)
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
            files.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.selected_instance.root))))
            row.addWidget(files)
            layout.addLayout(row)
            self.update_button = QPushButton("Check for updates")
            self.update_button.setMinimumHeight(40)
            self.update_button.setAccessibleName("Check for QuillSail updates")
            self.update_button.clicked.connect(self.check_or_install_update)
            self.update_channel = QComboBox()
            self.update_channel.addItem("Stable", "stable")
            self.update_channel.addItem("Beta", "beta")
            self.update_channel.setCurrentIndex(0 if saved_channel() == "stable" else 1)
            channel_label = QLabel("Update channel")
            channel_label.setBuddy(self.update_channel)
            update_row = QHBoxLayout()
            update_row.addWidget(channel_label)
            update_row.addWidget(self.update_channel)
            update_row.addWidget(self.update_button, 1)
            layout.addLayout(update_row)
            self.update_channel.currentIndexChanged.connect(self.update_channel_changed)
            self.update_status = QLabel("Updates are installed only when you choose to install them.")
            self.update_status.setTextFormat(Qt.TextFormat.PlainText)
            self.update_status.setWordWrap(True)
            layout.addWidget(self.update_status)
            self.update_progress = QProgressBar()
            self.update_progress.setAccessibleName("Update progress")
            self.update_progress.hide()
            layout.addWidget(self.update_progress)
            note = QLabel("Instances can run together. Switching instances keeps the others running.\nClosing QuillSail stops all instances started here; their content stays saved.")
            note.setWordWrap(True)
            layout.addWidget(note)
            layout.addStretch()
            version = QLabel(f"Version {__version__} · Local access only")
            version.setObjectName("version")
            layout.addWidget(version)
            scroll = QScrollArea()
            scroll.setObjectName("launcher-scroll")
            scroll.setWidgetResizable(True)
            scroll.setWidget(content)
            self.setCentralWidget(scroll)
            self.setStyleSheet("""
                QMainWindow { background: #faf9fc; }
                QWidget#launcher-content, QScrollArea#launcher-scroll { background: #faf9fc; border: 0; }
                QComboBox { background: white; color: #332b40; padding: 7px; border: 1px solid #d8d1e1; border-radius: 7px; }
                QComboBox QAbstractItemView { background: white; color: #332b40; selection-background-color: #eee8fa; selection-color: #332b40; }
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

        def refresh_instances(self, selected_id):
            self.instance_selector.blockSignals(True)
            self.instance_selector.clear()
            for item in registry.list():
                self.instance_selector.addItem(item.name, item.id)
            self.instance_selector.setCurrentIndex(self.instance_selector.findData(selected_id))
            self.instance_selector.blockSignals(False)

        def refresh_running_labels(self):
            count = sum(bool(runtime.server) for runtime in self.runtimes.values())
            self.instance_summary.setText(f"{count} instance{'s' if count != 1 else ''} running in this launcher.")
            for index in range(self.instance_selector.count()):
                identifier = self.instance_selector.itemData(index)
                item = registry.get(identifier)
                runtime = self.runtimes.get(identifier)
                self.instance_selector.setItemText(index, item.name + (" · Running" if runtime and runtime.server else ""))

        def select_instance(self):
            self.selected_instance = registry.get(self.instance_selector.currentData())
            identifier = self.selected_instance.id
            if identifier not in self.runtimes:
                self.runtimes[identifier] = DesktopRuntime(self.selected_instance.root)
            self.runtime = self.runtimes[identifier]
            self.setWindowTitle(self.selected_instance.name + " · QuillSail")
            external = running_url(self.selected_instance) if not self.runtime.server else ""
            self.external_url = external
            self.status.setText("Running in another launcher or terminal." if external else
                                "Your publication is running" if self.runtime.server else "Stopped. Your content is saved.")
            self.address.setText(external or self.runtime.url if self.runtime.server or external else "Start this instance to open its dashboard.")
            self.toggle.setText("Stop" if self.runtime.server else "Start")
            self.toggle.setEnabled(not external)
            self.open_button.setEnabled(bool(self.runtime.server or external))
            self.refresh_running_labels()

        def create_instance(self):
            name, accepted = QInputDialog.getText(self, "New publication instance", "Instance name")
            if accepted and name.strip():
                self.status.setText("Creating a separate publication…")
                self.operation(lambda: registry.add(name), self.instance_added)

        def add_existing_instance(self):
            directory = QFileDialog.getExistingDirectory(self, "Choose a QuillSail publication data folder")
            if not directory:
                return
            name, accepted = QInputDialog.getText(self, "Add existing instance", "Instance name")
            if accepted and name.strip():
                self.operation(lambda: registry.add(name, Path(directory), existing=True), self.instance_added)

        @Slot(object)
        def instance_added(self, item):
            self.finish_operation()
            self.busy = False
            self.refresh_instances(item.id)
            self.select_instance()
            if self.closing:
                QTimer.singleShot(0, self.close)
            elif not self.external_url:
                self.start()

        def operation(self, callback, completed, on_failed=None):
            self.busy = True
            self.toggle.setEnabled(False)
            self.open_button.setEnabled(False)
            self.update_button.setEnabled(False)
            self.update_channel.setEnabled(False)
            self.instance_selector.setEnabled(False)
            self.new_instance.setEnabled(False)
            self.add_instance.setEnabled(False)
            self.refresh_button.setEnabled(False)
            self.task = Task(callback, self)
            self.task.finished.connect(completed, Qt.ConnectionType.QueuedConnection)
            self.task.failed.connect(on_failed or self.failed, Qt.ConnectionType.QueuedConnection)
            self.task.progress.connect(self.update_progress_changed, Qt.ConnectionType.QueuedConnection)
            self.thread = threading.Thread(target=self.task.execute, name="quillsail-desktop-action", daemon=True)
            self.thread.start()

        def finish_operation(self):
            # The queued result can arrive just before the worker returns.
            # Join it before replacing any task or deleting the window tree.
            self.thread.join()
            self.task.deleteLater()
            self.update_button.setEnabled(True)
            self.update_channel.setEnabled(True)
            self.instance_selector.setEnabled(True)
            self.new_instance.setEnabled(True)
            self.add_instance.setEnabled(True)
            self.refresh_button.setEnabled(True)

        def update_channel_changed(self):
            self.available_update = None
            self.update_button.setText("Check for updates")
            self.update_status.setText("Check for an update on the selected channel.")
            try:
                save_channel(self.update_channel.currentData())
            except OSError:
                self.update_status.setText("This channel is selected, but the preference could not be saved for the next launch.")

        @Slot(str, int)
        def update_progress_changed(self, message, percent):
            self.update_status.setText(message)
            self.update_progress.show()
            self.update_progress.setRange(0, 0 if percent < 0 else 100)
            if percent >= 0:
                self.update_progress.setValue(percent)

        def check_or_install_update(self):
            if self.busy or self.closing:
                return
            if self.available_update is None:
                self.update_progress_changed("Checking for updates…", -1)
                channel = self.update_channel.currentData()
                target = read_install(installation)["target"] if installation else None
                self.operation(lambda: UpdateClient().check(channel=channel, target=target), self.update_checked, self.update_failed)
                return
            release = self.available_update
            dialog = QMessageBox(self)
            dialog.setWindowTitle("Update QuillSail")
            dialog.setTextFormat(Qt.TextFormat.PlainText)
            dialog.setText(f"Download and install QuillSail {release.version}?")
            dialog.setInformativeText("QuillSail will finish background work for all instances started here, install the verified update, and restart. All publication files stay saved. Stop instances launched in other terminals or apps before updating.")
            dialog.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
            dialog.button(QMessageBox.StandardButton.Ok).setText("Download and restart")
            dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
            if dialog.exec() != QMessageBox.StandardButton.Ok:
                return
            try:
                install_root = installation_path()
            except Exception as exc:  # noqa: BLE001 - show a useful source-install message
                self.update_status.setText(str(exc))
                return
            self.update_progress_changed("Downloading update…", 0)
            self.operation(lambda: UpdateClient().prepare(release, install_root, data_dir=root,
                           progress=lambda message, percent: self.task.progress.emit(message, percent)),
                           self.update_prepared, self.update_failed)

        @Slot(object)
        def update_checked(self, update):
            self.finish_operation()
            self.busy = False
            self.toggle.setEnabled(True)
            self.open_button.setEnabled(bool(self.runtime.server))
            self.update_progress.hide()
            if self.closing:
                QTimer.singleShot(0, self.close)
                return
            self.available_update = update if update.available else None
            self.update_button.setText(f"Install QuillSail {update.version}…" if update.available else "Check for updates")
            self.update_status.setText(f"QuillSail {update.version} is available on the {update.channel} channel."
                                       if update.available else f"QuillSail {__version__} is up to date on the {update.channel} channel.")

        @Slot(object)
        def update_prepared(self, plan):
            self.finish_operation()
            self.busy = False
            self.update_plan = plan
            self.closing = True
            self.update_progress_changed("Finishing background work before updating…", -1)
            self.stop()

        @Slot(str)
        def update_failed(self, message):
            handoff_failed = self.update_plan is not None
            self.finish_operation()
            self.busy = False
            self.available_update = self.update_plan = None
            self.update_button.setText("Check for updates")
            self.update_status.setText(message)
            self.update_progress.hide()
            self.toggle.setEnabled(True)
            self.open_button.setEnabled(bool(self.runtime.server))
            if self.closing:
                if handoff_failed:
                    self.closing = False
                    if self.runtime.server is None:
                        QTimer.singleShot(0, self.start)
                else:
                    QTimer.singleShot(0, self.close)

        def start(self):
            if not self.busy:
                if external := running_url(self.selected_instance):
                    self.external_url = external
                    self.select_instance()
                    return
                self.status.setText("Starting your publication…")
                self.operation(self.runtime.start, self.started)

        @Slot(object)
        def started(self, url):
            self.finish_operation()
            self.busy = False
            self.status.setText("Your publication is running")
            self.address.setText(url)
            self.toggle.setText("Stop")
            self.toggle.setEnabled(True)
            self.open_button.setEnabled(True)
            self.refresh_running_labels()
            if smoke:
                self.smoke_starts += 1
                QTimer.singleShot(0, self.stop if self.smoke_starts == 1 else self.close)
            elif self.closing:
                QTimer.singleShot(0, self.stop)
            elif open_browser:
                self.open_dashboard()

        def open_dashboard(self):
            url = self.runtime.launch_url() if self.runtime.server else running_url(self.selected_instance) + "/dashboard/"
            if not QDesktopServices.openUrl(QUrl(url)):
                self.status.setText("Your browser could not be opened. Try Open dashboard again.")

        def stop(self):
            if not self.busy:
                self.status.setText("Finishing background work and stopping…")
                def stop_and_handoff():
                    for runtime in self.runtimes.values() if self.closing else (self.runtime,):
                        runtime.stop()
                    if self.update_plan is not None:
                        launch_installer(self.update_plan)
                self.operation(stop_and_handoff, self.stopped, self.update_failed if self.update_plan else None)

        @Slot(object)
        def stopped(self, _result=None):
            self.finish_operation()
            self.busy = False
            self.status.setText("Stopped. Your content is saved.")
            self.address.setText("Start QuillSail to open your dashboard.")
            self.toggle.setText("Start")
            self.toggle.setEnabled(True)
            self.refresh_running_labels()
            if self.closing:
                QTimer.singleShot(0, self.close)
            elif smoke:
                QTimer.singleShot(0, self.start)

        @Slot(str)
        def failed(self, message):
            self.finish_operation()
            self.busy = False
            self.status.setText("QuillSail could not start: " + message)
            self.address.setText("View logs for details. Your publication files are in the data folder.")
            self.toggle.setText("Start")
            self.toggle.setEnabled(True)
            if smoke:
                self.smoke_error, self.closing = True, True
            if self.closing:
                app.exit(1 if smoke else 0)

        def closeEvent(self, event):  # noqa: N802 - Qt virtual method
            self.closing = True
            if self.busy:
                event.ignore()
                return
            if any(runtime.server for runtime in self.runtimes.values()):
                event.ignore()
                self.stop()
            else:
                # QApplication.quit() sends another close event on macOS.
                # Accept this one once the worker has stopped instead.
                event.accept()

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("QuillSail")
    app.setOrganizationName("QuillSail")
    guard = None
    installation = installation_path() if getattr(sys, "frozen", False) else None
    if installation:
        guard = application_lock(install_lock_path(installation))
        try:
            guard.__enter__()
        except InstallError as exc:
            QMessageBox.information(None, "QuillSail is already open", str(exc))
            handler.close()
            logging.getLogger().removeHandler(handler)
            return 1
    window = Window()
    if installation and (last := last_install_result(installation)) and last.get("status") == "failed":
        window.update_status.setText("The last update could not be installed. " + last.get("error", "Try checking for updates again."))
    window.show()
    try:
        result = app.exec()
        return 1 if smoke and (window.smoke_error or window.smoke_starts != 2 or not window.logo_ready) else result
    finally:
        if window.thread is not None:
            window.thread.join()
        for runtime in window.runtimes.values():
            runtime.stop()
        # Destroy the widget tree while QApplication still exists. Leaving it
        # to Python/Qt's atexit order can double-delete child wrappers in a bundle.
        window.deleteLater()
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        logging.getLogger().removeHandler(handler)
        handler.close()
        if guard:
            guard.__exit__(None, None, None)


def main():
    parser = argparse.ArgumentParser(description="Open QuillSail on your computer.")
    parser.add_argument("--data-dir", type=Path, help="Use a separate desktop publication folder.")
    parser.add_argument("--instance", help="Open a named local publication from the shared instance list.")
    parser.add_argument("--no-browser", action="store_true", help="Start the launcher without opening the browser.")
    parser.add_argument("--smoke-test", action="store_true", help="Check a disposable local publication, then exit.")
    parser.add_argument("--smoke-test-gui", action="store_true", help="Check native window startup, stop and restart with disposable data.")
    update = parser.add_mutually_exclusive_group()
    update.add_argument("--check-updates", action="store_true", help="Check for a signed desktop update, then exit.")
    update.add_argument("--update", action="store_true", help="Download and install an available signed desktop update.")
    parser.add_argument("--channel", choices=("stable", "beta"), default="beta", help="Desktop update channel.")
    parser.add_argument("--yes", action="store_true", help="Approve a command-line update without prompting.")
    parser.add_argument("--version", action="version", version="QuillSail " + __version__)
    args = parser.parse_args()
    if args.data_dir and args.instance:
        parser.error("Choose --data-dir or --instance, rather than both.")
    if args.check_updates or args.update:
        from copydesk.cli import main as cli_main

        command = ["update", "--desktop", "--channel", args.channel]
        if args.check_updates:
            command.append("--check")
        if args.yes:
            command.append("--yes")
        cli_main(command, prog_name="QuillSail")
        return
    if (args.smoke_test or args.smoke_test_gui) and sys.stderr is not None:
        faulthandler.enable()
    if args.smoke_test:
        smoke_test()
        return
    if args.smoke_test_gui:
        with tempfile.TemporaryDirectory(prefix="quillsail-gui-smoke-") as directory:
            raise SystemExit(run(data_dir=Path(directory), open_browser=False, smoke=True))
    raise SystemExit(run(data_dir=args.data_dir, instance_id=args.instance, open_browser=not args.no_browser))


if __name__ == "__main__":
    main()
