from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from .api import Api
from .workers import Worker
from .widgets import PhoneGrid


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, base_url: str) -> None:
        super().__init__()

        self.setWindowTitle("Android Phone Farm")
        self.resize(1200, 800)

        self.base_url = base_url.rstrip("/")
        self.api = Api(self.base_url)
        self.thread_pool = QtCore.QThreadPool.globalInstance()

        self._devices: List[Dict[str, Any]] = []
        self._selection: List[str] = []

        self._build_ui()
        self._connect_signals()

        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setInterval(2000)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start()

        self._log_timer = QtCore.QTimer(self)
        self._log_timer.setInterval(3000)
        self._log_timer.timeout.connect(self.refresh_logs)
        self._log_timer.start()

        # Initial load.
        self.refresh()
        self.refresh_logs()

    # --- UI ---

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # Top bar
        top = QtWidgets.QHBoxLayout()
        self.backend_label = QtWidgets.QLabel(f"Backend: {self.base_url}")
        self.backend_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.count_label = QtWidgets.QLabel("Devices: 0 | Selected: 0")
        self.refresh_btn = QtWidgets.QPushButton("Refresh Now")
        top.addWidget(self.backend_label, 1)
        top.addWidget(self.count_label, 0)
        top.addWidget(self.refresh_btn, 0)
        root.addLayout(top)

        body = QtWidgets.QHBoxLayout()
        root.addLayout(body, 1)

        # Left: grid of phone tiles inside a scroll area
        self.grid = PhoneGrid()
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.grid)
        body.addWidget(scroll, 3)

        # Right: actions
        side = QtWidgets.QVBoxLayout()
        side.setSpacing(8)
        body.addLayout(side, 1)

        self.help_label = QtWidgets.QLabel(
            "Select phones (click / shift-click / drag).\nThen use a big button.\n\nDouble-click a phone to open scrcpy."
        )
        self.help_label.setWordWrap(True)
        side.addWidget(self.help_label)

        self.btn_instagram = self._big_button("Start Instagram", "Queues the 'Open Instagram' preset on selected phones.")
        self.btn_tiktok = self._big_button("Start TikTok", "Queues the 'Open TikTok' preset on selected phones.")
        self.btn_x = self._big_button("Start X", "Queues the 'Open X (Twitter)' preset on selected phones.")
        self.btn_facebook = self._big_button("Start Facebook", "Queues the 'Open Facebook' preset on selected phones.")
        self.btn_youtube = self._big_button("Start YouTube", "Queues the 'Open YouTube' preset on selected phones.")
        self.btn_web = self._big_button("Open Website", "Prompts for a URL and opens it on selected phones.")
        self.btn_screens = self._big_button("See Screens (scrcpy)", "Opens scrcpy windows for selected phones.")
        self.btn_screenshot = self._big_button("Take Screenshot", "Captures screenshots into ./data/devices/<serial>/screenshots")

        side.addWidget(self.btn_instagram)
        side.addWidget(self.btn_tiktok)
        side.addWidget(self.btn_x)
        side.addWidget(self.btn_facebook)
        side.addWidget(self.btn_youtube)
        side.addWidget(self.btn_web)
        side.addWidget(self.btn_screens)
        side.addWidget(self.btn_screenshot)

        side.addSpacing(10)

        self.btn_cooldown = self._big_button("Let Phones Cool Down", "Marks selected phones as COOLING for 60 seconds.")
        self.btn_cooldown_stop = self._big_button("Cancel Cooldown", "Returns selected phones to READY immediately.")
        self.btn_restart_phones = self._big_button("Restart Phones", "Reboots selected phones via ADB.")
        self.btn_restart_adb = self._big_button("Restart ADB on Mac", "Restarts the ADB server on this Mac.")
        self.btn_stop = self._big_button("Stop Everything", "Emergency stop: cancels running/queued work.")

        # Make Stop Everything visually louder.
        self.btn_stop.setStyleSheet("font-size: 18px; font-weight: 800; padding: 14px; background: #B71C1C; color: white;")

        side.addWidget(self.btn_cooldown)
        side.addWidget(self.btn_cooldown_stop)
        side.addWidget(self.btn_restart_phones)
        side.addWidget(self.btn_restart_adb)
        side.addWidget(self.btn_stop)
        side.addStretch(1)

        # Bottom: logs
        self.logs = QtWidgets.QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setPlaceholderText("Live backend log tail...")
        self.logs.setMaximumBlockCount(500)
        root.addWidget(self.logs, 0)

        self.statusBar().showMessage("Ready")

    def _big_button(self, text: str, tooltip: str) -> QtWidgets.QPushButton:
        btn = QtWidgets.QPushButton(text)
        btn.setToolTip(tooltip)
        btn.setMinimumHeight(48)
        btn.setStyleSheet("font-size: 16px; font-weight: 650; padding: 10px;")
        return btn

    def _connect_signals(self) -> None:
        self.refresh_btn.clicked.connect(self.refresh)
        self.grid.selection_changed.connect(self._on_selection_changed)
        self.grid.scrcpy_requested.connect(self._scrcpy_single)

        self.btn_instagram.clicked.connect(lambda: self._run_preset("instagram_open"))
        self.btn_tiktok.clicked.connect(lambda: self._run_preset("tiktok_open"))
        self.btn_x.clicked.connect(lambda: self._run_preset("x_open"))
        self.btn_facebook.clicked.connect(lambda: self._run_preset("facebook_open"))
        self.btn_youtube.clicked.connect(lambda: self._run_preset("youtube_open"))
        self.btn_web.clicked.connect(self._open_website)
        self.btn_screens.clicked.connect(self._scrcpy_selected)
        self.btn_screenshot.clicked.connect(self._screenshot_selected)

        self.btn_cooldown.clicked.connect(lambda: self._cooldown_selected(60))
        self.btn_cooldown_stop.clicked.connect(self._cooldown_stop_selected)
        self.btn_restart_phones.clicked.connect(self._reboot_selected)
        self.btn_restart_adb.clicked.connect(self._restart_adb)
        self.btn_stop.clicked.connect(self._emergency_stop)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        try:
            self.api.close()
        finally:
            super().closeEvent(event)

    # --- Refresh ---

    def refresh(self) -> None:
        worker = Worker(self.api.get_snapshot)
        worker.signals.result.connect(self._on_snapshot)
        worker.signals.error.connect(self._on_error)
        self.thread_pool.start(worker)

    def refresh_logs(self) -> None:
        worker = Worker(lambda: self.api.logs_tail(200))
        worker.signals.result.connect(self._on_logs)
        worker.signals.error.connect(lambda _: None)
        self.thread_pool.start(worker)

    def _on_snapshot(self, result: Tuple[List[Dict[str, Any]], List[str]]) -> None:
        devices, selection = result
        self._devices = devices
        self._selection = selection
        self.grid.set_devices(devices, selection)
        self._update_counts()

    def _on_logs(self, result: Dict[str, Any]) -> None:
        lines = result.get("lines", []) if isinstance(result, dict) else []
        self.logs.setPlainText("\n".join(lines))

    def _update_counts(self) -> None:
        self.count_label.setText(f"Devices: {len(self._devices)} | Selected: {len(self.grid.selected_serials())}")

    def _on_error(self, tb: str) -> None:
        # Keep UI calm: show a short status bar message; details in dialog.
        self.statusBar().showMessage("Backend error. Check connection.")
        dlg = QtWidgets.QMessageBox(self)
        dlg.setWindowTitle("Backend Error")
        dlg.setIcon(QtWidgets.QMessageBox.Warning)
        dlg.setText("The GUI couldn't reach the backend.")
        dlg.setInformativeText("Make sure the backend is running: ./bin/farm start")
        dlg.setDetailedText(tb)
        dlg.exec()

    # --- Selection sync ---

    def _on_selection_changed(self, serials: List[str]) -> None:
        self._update_counts()
        worker = Worker(lambda: self.api.set_selection(serials))
        worker.signals.error.connect(lambda _: None)
        self.thread_pool.start(worker)

    # --- Helpers ---

    def _selected_or_warn(self) -> List[str]:
        serials = self.grid.selected_serials()
        if not serials:
            QtWidgets.QMessageBox.information(self, "No phones selected", "Select one or more phones first.")
        return serials

    def _confirm(self, title: str, message: str) -> bool:
        return QtWidgets.QMessageBox.question(
            self,
            title,
            message,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        ) == QtWidgets.QMessageBox.Yes

    def _toast(self, msg: str) -> None:
        self.statusBar().showMessage(msg, 5000)

    # --- Actions ---

    def _run_preset(self, campaign_id: str) -> None:
        serials = self._selected_or_warn()
        if not serials:
            return

        def fn() -> Dict[str, Any]:
            return self.api.run_campaign(campaign_id, serials)

        worker = Worker(fn)
        worker.signals.result.connect(lambda r: self._toast((r or {}).get("message", "Queued")))
        worker.signals.error.connect(self._on_error)
        self.thread_pool.start(worker)

    def _open_website(self) -> None:
        serials = self._selected_or_warn()
        if not serials:
            return

        url, ok = QtWidgets.QInputDialog.getText(
            self,
            "Open Website",
            "Enter a URL to open on the selected phones:",
            QtWidgets.QLineEdit.Normal,
            "https://example.com",
        )
        if not ok or not url.strip():
            return
        url = url.strip()

        def fn() -> str:
            for s in serials:
                self.api.open_url(s, url)
            return f"Opened URL on {len(serials)} phone(s)."

        worker = Worker(fn)
        worker.signals.result.connect(self._toast)
        worker.signals.error.connect(self._on_error)
        self.thread_pool.start(worker)

    def _scrcpy_single(self, serial: str) -> None:
        def fn() -> str:
            self.api.start_scrcpy(serial)
            return f"scrcpy started for {serial}"

        worker = Worker(fn)
        worker.signals.result.connect(self._toast)
        worker.signals.error.connect(lambda _: None)
        self.thread_pool.start(worker)

    def _scrcpy_selected(self) -> None:
        serials = self._selected_or_warn()
        if not serials:
            return

        def fn() -> str:
            for s in serials:
                self.api.start_scrcpy(s)
            return f"Opened scrcpy for {len(serials)} phone(s)."

        worker = Worker(fn)
        worker.signals.result.connect(self._toast)
        worker.signals.error.connect(self._on_error)
        self.thread_pool.start(worker)

    def _screenshot_selected(self) -> None:
        serials = self._selected_or_warn()
        if not serials:
            return

        def fn() -> str:
            ok = 0
            for s in serials:
                resp = self.api.screenshot(s)
                if resp.get("ok"):
                    ok += 1
            return f"Screenshots captured: {ok}/{len(serials)}"

        worker = Worker(fn)
        worker.signals.result.connect(self._toast)
        worker.signals.error.connect(self._on_error)
        self.thread_pool.start(worker)

    def _cooldown_selected(self, seconds: int) -> None:
        serials = self._selected_or_warn()
        if not serials:
            return
        def fn() -> str:
            self.api.cooldown_start(serials, seconds)
            return f"Cooling {len(serials)} phone(s) for {seconds}s"
        worker = Worker(fn)
        worker.signals.result.connect(self._toast)
        worker.signals.error.connect(self._on_error)
        self.thread_pool.start(worker)

    def _cooldown_stop_selected(self) -> None:
        serials = self._selected_or_warn()
        if not serials:
            return
        def fn() -> str:
            self.api.cooldown_stop(serials)
            return "Cooldown cancelled"
        worker = Worker(fn)
        worker.signals.result.connect(self._toast)
        worker.signals.error.connect(self._on_error)
        self.thread_pool.start(worker)

    def _reboot_selected(self) -> None:
        serials = self._selected_or_warn()
        if not serials:
            return
        if not self._confirm("Restart Phones", f"Reboot {len(serials)} selected phone(s)?"):
            return

        def fn() -> str:
            for s in serials:
                self.api.reboot(s)
            return "Reboot requested"

        worker = Worker(fn)
        worker.signals.result.connect(self._toast)
        worker.signals.error.connect(self._on_error)
        self.thread_pool.start(worker)

    def _restart_adb(self) -> None:
        if not self._confirm("Restart ADB", "Restart the ADB server on this Mac now?"):
            return

        worker = Worker(lambda: self.api.restart_adb().get("message", "ADB restarted"))
        worker.signals.result.connect(self._toast)
        worker.signals.error.connect(self._on_error)
        self.thread_pool.start(worker)

    def _emergency_stop(self) -> None:
        if not self._confirm("STOP EVERYTHING", "Emergency stop cancels all work. Do it now?"):
            return
        worker = Worker(lambda: self.api.emergency_stop().get("message", "Emergency stop"))
        worker.signals.result.connect(self._toast)
        worker.signals.error.connect(self._on_error)
        self.thread_pool.start(worker)


def main() -> None:
    host = os.environ.get("PHONE_FARM_HOST", "127.0.0.1")
    port = os.environ.get("PHONE_FARM_PORT", "8765")
    base_url = os.environ.get("PHONE_FARM_BASE_URL", f"http://{host}:{port}")

    app = QtWidgets.QApplication([])
    app.setApplicationName("Android Phone Farm")

    win = MainWindow(base_url=base_url)
    win.show()
    app.exec()
