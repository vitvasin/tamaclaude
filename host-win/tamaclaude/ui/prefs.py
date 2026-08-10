"""หน้าตั้งค่าสองแท็บ — General + Wi-Fi · port ของ PreferencesWindowController.swift → QDialog

macOS มี GUI ให้ตั้งทุกอย่าง Windows เดิมไม่มี (ต้องแก้ไฟล์ JSON เอง — ดู handoff) หน้านี้
ปิดช่องนั้น: key ลับสองตัว (session/finnhub) เขียนผ่าน secret_file (mode-600/ACL), ค่าหน้า
weather/crypto/stocks เขียน JSON config, autostart ที่ HKCU Run, และ Wi-Fi provisioning

**ค่า config อ่านตอน daemon เริ่มเท่านั้น** — เตือนผู้ใช้ให้ restart daemon หลังบันทึก
(เหมือนที่ handoff ย้ำ) เพราะไม่มี hot-reload
"""

from __future__ import annotations

import json
from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import autostart, session_key_file, stocks_service
from ..paths import CRYPTO_CONFIG, STOCKS_CONFIG, WEATHER_CONFIG
from ..wifi_provisioning import WiFiCommand


class PreferencesDialog(QDialog):
    def __init__(
        self,
        get_state: Callable[[], object],
        send_wifi: Callable[[WiFiCommand], None],
        autostart_command: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._get_state = get_state
        self._send_wifi = send_wifi
        self._autostart_command = autostart_command
        self.setWindowTitle("TamaClaude Settings")
        self.setMinimumWidth(420)

        tabs = QTabWidget(self)
        tabs.addTab(self._general_tab(), "General")
        tabs.addTab(self._wifi_tab(), "Wi-Fi")
        root = QVBoxLayout(self)
        root.addWidget(tabs)

    # MARK: - General

    def _general_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        # --- key ลับ: session ---
        self._session_key = QLineEdit()
        self._session_key.setEchoMode(QLineEdit.Password)
        self._session_key.setPlaceholderText(
            "usable" if session_key_file.is_usable() else "paste claude.ai sessionKey")
        save_sk = QPushButton("Save")
        save_sk.clicked.connect(self._save_session_key)
        form.addRow("Session key", _row(self._session_key, save_sk))

        # --- key ลับ: finnhub (หน้า stocks) ---
        self._finnhub_key = QLineEdit()
        self._finnhub_key.setEchoMode(QLineEdit.Password)
        self._finnhub_key.setPlaceholderText(
            "usable" if stocks_service.key_usable() else "paste Finnhub API key")
        save_fk = QPushButton("Save")
        save_fk.clicked.connect(self._save_finnhub_key)
        form.addRow("Finnhub key", _row(self._finnhub_key, save_fk))

        # --- ค่าหน้าข้อมูล (เขียน JSON config) ---
        self._weather_place = QLineEdit(_read_json(WEATHER_CONFIG, "place", ""))
        self._weather_unit = QLineEdit(_read_json(WEATHER_CONFIG, "unit", "C"))
        form.addRow("Weather place", self._weather_place)
        form.addRow("Weather unit (C/F)", self._weather_unit)

        self._crypto_coins = QLineEdit(",".join(_read_json(CRYPTO_CONFIG, "coins", [])))
        self._crypto_coins.setPlaceholderText("btc,eth (max 5)")
        form.addRow("Crypto coins", self._crypto_coins)

        self._stocks_symbols = QLineEdit(",".join(_read_json(STOCKS_CONFIG, "symbols", [])))
        self._stocks_symbols.setPlaceholderText("AAPL,MSFT (max 5)")
        form.addRow("Stock symbols", self._stocks_symbols)

        save_cfg = QPushButton("Save pages (restart daemon to apply)")
        save_cfg.clicked.connect(self._save_configs)
        form.addRow("", save_cfg)

        # --- autostart ---
        self._autostart = QCheckBox("Start TamaClaude at login")
        self._autostart.setChecked(autostart.is_enabled())
        self._autostart.toggled.connect(self._toggle_autostart)
        form.addRow("", self._autostart)

        return w

    def _save_session_key(self) -> None:
        raw = self._session_key.text().strip()
        if not raw:
            return
        try:
            session_key_file.write(raw)
        except Exception as e:  # secret_file.Refused ฯลฯ
            QMessageBox.warning(self, "Session key", str(e))
            return
        self._session_key.clear()
        self._session_key.setPlaceholderText("usable")
        QMessageBox.information(self, "Session key", "Saved.")

    def _save_finnhub_key(self) -> None:
        raw = self._finnhub_key.text().strip()
        if not raw:
            return
        try:
            stocks_service.write_key(raw)
        except Exception as e:
            QMessageBox.warning(self, "Finnhub key", str(e))
            return
        self._finnhub_key.clear()
        self._finnhub_key.setPlaceholderText("usable")
        QMessageBox.information(self, "Finnhub key", "Saved. Restart daemon to apply.")

    def _save_configs(self) -> None:
        _write_json(WEATHER_CONFIG, {
            "place": self._weather_place.text().strip(),
            "unit": "F" if self._weather_unit.text().strip().upper() == "F" else "C",
        })
        _write_json(CRYPTO_CONFIG, {"coins": _csv(self._crypto_coins.text(), 5)})
        _write_json(STOCKS_CONFIG, {"symbols": _csv(self._stocks_symbols.text(), 5)})
        QMessageBox.information(
            self, "Pages", "Saved. Restart the daemon for changes to take effect.")

    def _toggle_autostart(self, on: bool) -> None:
        try:
            if on:
                autostart.enable(self._autostart_command)
            else:
                autostart.disable()
        except Exception as e:
            QMessageBox.warning(self, "Autostart", str(e))

    # MARK: - Wi-Fi

    def _wifi_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)

        self._wifi_status = QLabel("—")
        v.addWidget(self._wifi_status)

        self._net_list = QListWidget()
        self._net_list.itemSelectionChanged.connect(self._on_net_selected)
        v.addWidget(self._net_list)

        row = QHBoxLayout()
        self._psk = QLineEdit()
        self._psk.setEchoMode(QLineEdit.Password)
        self._psk.setPlaceholderText("password (blank = open / keep saved)")
        row.addWidget(self._psk)
        v.addLayout(row)

        buttons = QHBoxLayout()
        scan = QPushButton("Scan")
        scan.clicked.connect(lambda: self._send_wifi(WiFiCommand.scan()))
        connect = QPushButton("Connect")
        connect.clicked.connect(self._connect)
        forget = QPushButton("Forget")
        forget.clicked.connect(self._forget)
        buttons.addWidget(scan)
        buttons.addWidget(connect)
        buttons.addWidget(forget)
        v.addLayout(buttons)

        note = QLabel(
            "The board's own radio does the scanning — only 2.4GHz networks it can reach "
            "appear here. The session key never leaves this PC.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray; font-size: 11px;")
        v.addWidget(note)

        self.refresh_wifi()
        return w

    def refresh_wifi(self) -> None:
        """เรียกจาก tick ของแอปเมื่อ BoardEvent Wi-Fi เข้ามา — วาดลิสต์ + สถานะใหม่"""
        state = self._get_state()
        if not hasattr(self, "_net_list"):
            return
        selected = self._selected_ssid()
        self._net_list.clear()
        for r in state.networks.rows:
            lock = "🔒" if r.secured else ""
            sig = f"{r.rssi} dBm" if r.rssi is not None else "saved"
            star = "★ " if r.saved else ""
            item = QListWidgetItem(f"{star}{r.ssid}  {lock}  {sig}")
            item.setData(Qt.UserRole, r.ssid)
            self._net_list.addItem(item)
            if r.ssid == selected:
                item.setSelected(True)
        st = state.wifi_status
        if st is not None:
            line = f"{st.state.value}"
            if st.ssid:
                line += f" · {st.ssid}"
            if st.ip:
                line += f" · {st.ip}"
            if st.needs_password:
                line += " · wrong password"
            self._wifi_status.setText(line)
        elif state.networks.scanning:
            self._wifi_status.setText("scanning…")

    def _selected_ssid(self) -> str | None:
        items = self._net_list.selectedItems()
        return items[0].data(Qt.UserRole) if items else None

    def _on_net_selected(self) -> None:
        pass  # เผื่อขยายภายหลัง — ตอนนี้ปล่อยให้ผู้ใช้พิมพ์รหัสเองอิสระ

    def _connect(self) -> None:
        ssid = self._selected_ssid()
        if not ssid:
            return
        psk = self._psk.text()
        # ช่องว่าง = วงเปิด หรือ "อย่าแตะรหัสที่จำไว้" — ส่ง None เมื่อว่างเพื่อไม่ลบรหัสเดิม
        self._send_wifi(WiFiCommand.join(ssid, psk if psk else None))
        self._psk.clear()

    def _forget(self) -> None:
        ssid = self._selected_ssid()
        if ssid:
            self._send_wifi(WiFiCommand.forget(ssid))


def _row(field: QWidget, button: QWidget) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(field)
    h.addWidget(button)
    return w


def _csv(text: str, cap: int) -> list[str]:
    items = [s.strip() for s in text.split(",") if s.strip()]
    return items[:cap]


def _read_json(path, key, default):
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj.get(key, default)
    except (OSError, json.JSONDecodeError, ValueError):
        return default


def _write_json(path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
