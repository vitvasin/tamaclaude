"""tray app = daemon ที่มีหน้าตา · port ของ MenuBarApp.swift → QSystemTrayIcon

**โมเดล concurrency (Phase 6) — เขียนไว้ตรงนี้ก่อนแก้อะไร:**

- Qt event loop อยู่ **main thread** เป็นเจ้าของ QWidget ทุกตัว (tray, panel, prefs)
- `Daemon.run()` เดินใน **worker thread เดียว** (มันเปิด HookServer thread + BleTransport
  asyncio thread ในตัวเองอยู่แล้ว) — tick วินาทีละครั้งอยู่ที่นั่น ไม่บล็อก Qt
- BLE = asyncio ใน thread ของตัวเอง (ข้างใน BleTransport) — ของสองลูปนี้ห้ามแตะกันตรงๆ
- Outlook/calendar: ไม่ได้พอร์ตมา Windows จึงไม่มี STA thread เหมือนที่ plan เผื่อไว้
- ข้ามเธรด: UI อ่าน `daemon.ui_state()` ใต้ _lock ของ daemon (ปลอดภัย) · callback `on_wifi`
  ยิงจากเธรด BLE จึง marshal เข้า Qt ด้วย signal (`_WifiBridge`) ไม่แตะ widget ตรงๆ
- ไม่ใช้ qasync — BleTransport เป็นเจ้าของ asyncio อยู่แล้ว การลาก Qt เข้าลูปเดียวกันไม่ได้
  ประโยชน์อะไรที่นี่ (ดู plan: "pick one and write it down")
"""

from __future__ import annotations

import sys
import threading
from datetime import datetime

from PySide6.QtCore import QObject, QPoint, QTimer, Signal
from PySide6.QtGui import QGuiApplication, Qt
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .. import autostart, session_key_file
from ..daemon import Daemon
from . import badge_image
from .badge import MenuBadge
from .panel import Panel
from .prefs import PreferencesDialog


class _WifiBridge(QObject):
    """สะพานข้ามเธรด: BoardEvent Wi-Fi ยิงจากเธรด BLE, ส่ง signal ให้ Qt วาดลิสต์ใหม่"""

    changed = Signal()


class TrayApp:
    def __init__(self, no_ble: bool = False) -> None:
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setQuitOnLastWindowClosed(False)  # ปิดแผงแล้วแอปต้องไม่ตาย

        self._daemon = Daemon(use_ble=not no_ble, use_pages=True)
        self._bridge = _WifiBridge()
        self._bridge.changed.connect(self._on_wifi_changed)
        # on_wifi ยิงจากเธรด BLE — เด้งเข้า Qt ผ่าน signal (queued) ไม่แตะ widget ตรงๆ
        self._daemon.on_wifi = lambda: self._bridge.changed.emit()

        self._prefs: PreferencesDialog | None = None
        self._refresh_finished: datetime | None = None
        self._last_scheme = None

        self._panel = Panel(on_refresh=self._refresh_quota, on_settings=self._open_settings)

        self._tray = QSystemTrayIcon()
        self._tray.setToolTip("TamaClaude")
        menu = QMenu()
        menu.addAction("Settings…", self._open_settings)
        menu.addSeparator()
        menu.addAction("Quit", self._quit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.setIcon(badge_image.fallback(dark=self._dark()))
        self._tray.show()

        # daemon เดินใน worker thread — run() บล็อกด้วย tick loop ของมันเอง
        self._thread = threading.Thread(target=self._daemon.run, name="daemon", daemon=True)

        # timer วินาทีละครั้งบนเธรด Qt — วาดแบดจ์ใหม่ (ตามธีมด้วย) + แผงที่เปิดค้าง
        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)
        self._timer.setInterval(1000)

    # MARK: - lifecycle

    def run(self) -> int:
        self._thread.start()
        self._timer.start()
        self._tick()
        return self._app.exec()

    def _quit(self) -> None:
        self._daemon.stop()
        self._tray.hide()
        self._app.quit()

    # MARK: - per-second

    def _dark(self) -> bool:
        try:
            return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        except Exception:
            return True  # taskbar Win11 มืดโดยปริยาย

    def _tick(self) -> None:
        state = self._daemon.ui_state()
        dark = self._dark()
        badge = MenuBadge.from_usage(state.usage)
        # วาดใหม่เมื่อ badge หรือธีมเปลี่ยน — Windows ไม่มี template ให้ระบบกลับสีเอง
        if badge is None:
            self._tray.setIcon(badge_image.fallback(dark=dark))
            self._tray.setToolTip("TamaClaude — no quota figures yet")
        else:
            self._tray.setIcon(badge_image.make(badge, dark=dark))
            self._tray.setToolTip("TamaClaude — " + badge_image.description(badge))
        self._last_scheme = dark

        if self._panel.isVisible():
            self._update_panel(state)
        if self._prefs is not None and self._prefs.isVisible():
            self._prefs.refresh_wifi()

    def _update_panel(self, state) -> None:
        stamp = _usage_stamp()
        self._panel.update_view(
            state,
            stamp=stamp,
            has_key=session_key_file.is_usable(),
            refresh_running=False,
            refresh_finished=self._refresh_finished,
        )

    # MARK: - actions

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.Context):
            if reason == QSystemTrayIcon.Trigger:
                self._toggle_panel()

    def _toggle_panel(self) -> None:
        if self._panel.isVisible():
            self._panel.hide()
            return
        self._update_panel(self._daemon.ui_state())
        self._panel.adjustSize()
        # วางแผงชิดไอคอน tray (มุมล่างขวาของจอโดยประมาณ)
        geo = self._tray.geometry()
        screen = self._app.primaryScreen().availableGeometry()
        if geo.isValid():
            x = min(geo.center().x() - self._panel.width() // 2, screen.right() - self._panel.width())
            y = geo.top() - self._panel.height() if geo.top() > screen.height() // 2 else geo.bottom()
        else:
            x = screen.right() - self._panel.width() - 8
            y = screen.bottom() - self._panel.height() - 8
        self._panel.move(QPoint(max(screen.left(), x), max(screen.top(), y)))
        self._panel.show()
        self._panel.raise_()
        self._panel.activateWindow()

    def _open_settings(self) -> None:
        self._panel.hide()
        if self._prefs is None:
            self._prefs = PreferencesDialog(
                get_state=self._daemon.ui_state,
                send_wifi=self._daemon.send_wifi,
                autostart_command=autostart.command(),
            )
        self._prefs.refresh_wifi()
        self._prefs.show()
        self._prefs.raise_()
        self._prefs.activateWindow()

    def _refresh_quota(self) -> None:
        # ยิง --usage-poll หนึ่งรอบผ่าน poller ของ daemon (ถ้ามี) — เย็นตัวจัดการที่ปุ่มแล้ว
        self._refresh_finished = datetime.now()
        poller = getattr(self._daemon, "_poller", None)
        if poller is not None and hasattr(poller, "refresh_now"):
            try:
                poller.refresh_now()
            except Exception:
                pass

    def _on_wifi_changed(self) -> None:
        if self._prefs is not None and self._prefs.isVisible():
            self._prefs.refresh_wifi()


def _usage_stamp() -> datetime | None:
    """เวลาแก้ไขล่าสุดของไฟล์ cache โควตา — อายุของตัวเลขที่เห็น"""
    from ..paths import USAGE_CACHE

    try:
        return datetime.fromtimestamp(USAGE_CACHE.stat().st_mtime)
    except OSError:
        return None


def main(no_ble: bool = False) -> int:
    return TrayApp(no_ble=no_ble).run()
