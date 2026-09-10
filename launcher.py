import json
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

from PyQt6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QMessageBox, QWidget,
    QVBoxLayout, QLabel, QPushButton, QCheckBox
)
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QIcon, QAction, QPixmap, QPainter, QColor

APP_NAME = "AI System"
DASHBOARD_URL = "http://127.0.0.1:8000/dashboard"
API_URL = "http://127.0.0.1:8000/health"
OLLAMA_URL = "http://127.0.0.1:11434/api/tags"


class AISystemLauncher(QWidget):
    def __init__(self):
        super().__init__()
        self.ai_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
        self.config_file = self.ai_dir / "launcher-config.json"
        self.process = None
        self.load_config()

        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.create_icon())
        self.create_tray_menu()

        self.timer = QTimer()
        self.timer.timeout.connect(self.check_status)
        self.timer.start(5000)

        self.tray_icon.show()
        self.check_status()

        if self.config.get("auto_start", True):
            QTimer.singleShot(800, self.start_ai_system)

    def load_config(self):
        try:
            if self.config_file.exists():
                self.config = json.loads(self.config_file.read_text(encoding="utf-8"))
            else:
                self.config = {"auto_start": True, "open_dashboard": True}
        except Exception:
            self.config = {"auto_start": True, "open_dashboard": True}

    def save_config(self):
        self.config_file.write_text(json.dumps(self.config, indent=2), encoding="utf-8")

    def create_icon(self):
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(74, 144, 226))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(8, 8, 48, 48)
        painter.setBrush(QColor(255, 255, 255))
        painter.drawEllipse(24, 24, 16, 16)
        painter.end()
        return QIcon(pixmap)

    def create_tray_menu(self):
        menu = QMenu()
        self.status_action = QAction("Status: Checking...", self)
        self.status_action.setEnabled(False)
        menu.addAction(self.status_action)
        menu.addSeparator()

        self.start_action = QAction("▶ Start AI System", self)
        self.start_action.triggered.connect(self.start_ai_system)
        menu.addAction(self.start_action)

        self.stop_action = QAction("⏹ Stop AI System", self)
        self.stop_action.triggered.connect(self.stop_ai_system)
        menu.addAction(self.stop_action)

        restart_action = QAction("↻ Restart AI System", self)
        restart_action.triggered.connect(self.restart_ai_system)
        menu.addAction(restart_action)
        menu.addSeparator()

        dashboard_action = QAction("🏠 Open AI System", self)
        dashboard_action.triggered.connect(self.open_dashboard)
        menu.addAction(dashboard_action)

        docs_action = QAction("🔌 Open API Docs", self)
        docs_action.triggered.connect(lambda: webbrowser.open("http://127.0.0.1:8000/docs"))
        menu.addAction(docs_action)
        menu.addSeparator()

        settings_action = QAction("⚙ Settings", self)
        settings_action.triggered.connect(self.show_settings)
        menu.addAction(settings_action)
        menu.addSeparator()

        exit_action = QAction("❌ Exit Launcher", self)
        exit_action.triggered.connect(self.exit_app)
        menu.addAction(exit_action)

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self.on_tray_click)

    def on_tray_click(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.open_dashboard()

    def docker_command(self):
        candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker/Docker/resources/bin/docker.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker/Docker/resources/bin/com.docker.cli.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return "docker"

    def run_docker(self, args, wait=True):
        cmd = [self.docker_command(), "compose", *args]
        kwargs = {"cwd": str(self.ai_dir), "text": True}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        if wait:
            return subprocess.run(cmd, capture_output=True, **kwargs)
        return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)

    def docker_ready(self):
        try:
            cmd = [self.docker_command(), "info"]
            kwargs = {"cwd": str(self.ai_dir), "capture_output": True, "timeout": 4}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            return subprocess.run(cmd, **kwargs).returncode == 0
        except Exception:
            return False

    def ensure_docker(self):
        if self.docker_ready():
            return True
        if os.name == "nt":
            desktop = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker/Docker/Docker Desktop.exe"
            if desktop.exists():
                try:
                    subprocess.Popen([str(desktop)], creationflags=subprocess.CREATE_NO_WINDOW)
                except Exception:
                    pass
        for _ in range(36):
            if self.docker_ready():
                return True
            time.sleep(2)
        return False

    def api_ready(self):
        try:
            req = Request(API_URL, headers={"User-Agent": "AI-System-Launcher/9.0"})
            with urlopen(req, timeout=2) as response:
                return 200 <= response.status < 300
        except (URLError, OSError):
            return False

    def ollama_ready(self):
        try:
            with urlopen(Request(OLLAMA_URL), timeout=2) as response:
                return 200 <= response.status < 300
        except (URLError, OSError):
            return False

    def check_status(self):
        api = self.api_ready()
        ollama = self.ollama_ready()
        if api and ollama:
            text = "✅ Status: ONLINE"
            tip = "AI System — Online"
            self.start_action.setEnabled(False)
            self.stop_action.setEnabled(True)
        elif api:
            text = "🟡 Status: API ONLINE"
            tip = "AI System — API Online"
            self.start_action.setEnabled(False)
            self.stop_action.setEnabled(True)
        elif ollama:
            text = "🟡 Status: AI ENGINE ONLINE"
            tip = "AI System — Ollama Online"
            self.start_action.setEnabled(True)
            self.stop_action.setEnabled(False)
        else:
            text = "⭕ Status: OFFLINE"
            tip = "AI System — Offline"
            self.start_action.setEnabled(True)
            self.stop_action.setEnabled(False)
        self.status_action.setText(text)
        self.tray_icon.setToolTip(tip)

    def start_ai_system(self):
        if self.api_ready():
            if self.config.get("open_dashboard", True):
                self.open_dashboard()
            return

        self.tray_icon.showMessage(APP_NAME, "Starting the local AI system...", QSystemTrayIcon.MessageIcon.Information, 3000)
        if not self.ensure_docker():
            QMessageBox.critical(None, "AI System", "Docker Desktop is not available. Start Docker Desktop and try again.")
            return

        # Native Ollama is preferred on this Windows build. Start only the gateway;
        # docker-compose.override.yml routes the gateway to host.docker.internal:11434.
        result = self.run_docker(["up", "-d", "--build", "--no-deps", "api-gateway"])
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "Unknown Docker error").strip()[-1200:]
            QMessageBox.critical(None, "AI System", f"Could not start the API gateway.\n\n{detail}")
            return

        QTimer.singleShot(1500, self.wait_for_api)

    def wait_for_api(self, attempts=0):
        if self.api_ready():
            self.tray_icon.showMessage(APP_NAME, "AI System is online.", QSystemTrayIcon.MessageIcon.Information, 2500)
            if self.config.get("open_dashboard", True):
                self.open_dashboard()
            self.check_status()
            return
        if attempts >= 30:
            QMessageBox.warning(None, "AI System", "The gateway did not become ready yet. Check the Docker logs or try Start again.")
            return
        QTimer.singleShot(1000, lambda: self.wait_for_api(attempts + 1))

    def stop_ai_system(self):
        result = self.run_docker(["stop", "api-gateway"])
        if result.returncode != 0:
            QMessageBox.warning(None, "AI System", (result.stderr or result.stdout or "Could not stop the gateway.").strip()[-1000:])
        else:
            self.tray_icon.showMessage(APP_NAME, "AI System stopped.", QSystemTrayIcon.MessageIcon.Information, 2000)
        self.check_status()

    def restart_ai_system(self):
        self.stop_ai_system()
        QTimer.singleShot(1200, self.start_ai_system)

    def open_dashboard(self):
        webbrowser.open(DASHBOARD_URL)

    def show_settings(self):
        dialog = QWidget()
        dialog.setWindowTitle("AI System Settings")
        dialog.setFixedSize(360, 180)
        layout = QVBoxLayout(dialog)

        auto_start_cb = QCheckBox("Start AI System automatically with Windows")
        auto_start_cb.setChecked(self.config.get("auto_start", True))
        layout.addWidget(auto_start_cb)

        open_cb = QCheckBox("Open the dashboard when AI System starts")
        open_cb.setChecked(self.config.get("open_dashboard", True))
        layout.addWidget(open_cb)

        save_btn = QPushButton("Save")
        def save_settings():
            self.config["auto_start"] = auto_start_cb.isChecked()
            self.config["open_dashboard"] = open_cb.isChecked()
            self.save_config()
            dialog.close()
        save_btn.clicked.connect(save_settings)
        layout.addWidget(save_btn)
        dialog.show()
        self.settings_dialog = dialog

    def exit_app(self):
        self.tray_icon.hide()
        QApplication.quit()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    launcher = AISystemLauncher()
    sys.exit(app.exec())
