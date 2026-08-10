"""เปิด tray (= daemon ที่มีหน้าตา) ตอนล็อกอิน — คีย์ Run ของ HKCU · แทน LaunchAgent ของ macOS

daemon เป็นทั้งตัวส่ง BLE และตัวจับเวลายิงโควตา จึงต้องขึ้นเองหลังรีบูต · ใช้ HKCU\\...\\Run
(ไม่ใช่ Task Scheduler) เพราะมันรันในเซสชันของผู้ใช้ที่ล็อกอิน ซึ่งเป็นที่ที่ ~/.tamaclaude และ
สิทธิ์ BLE อยู่ · ไม่ต้องสิทธิ์ผู้ดูแล

รันด้วย `--tray` ไม่ใช่ `--daemon` เปล่าๆ: tray *เป็น* daemon อยู่แล้ว (owns BLE + tick) แต่
มีไอคอนให้เห็นและตั้งค่าได้ · รันทั้งสองพร้อมกันจะแย่ง hook socket + BLE กัน จึงเลือกอันเดียว
"""

from __future__ import annotations

import sys

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE = "TamaClaude"


def _reg():
    import winreg  # นำเข้าตรงนี้ — โมดูลนี้มีเฉพาะบน Windows

    return winreg


def command() -> str:
    """คำสั่งที่จะรันตอนล็อกอิน — pythonw เพื่อไม่ให้มีหน้าต่างคอนโซลค้าง"""
    exe = sys.executable
    # pythonw.exe รันเงียบไม่มีคอนโซล · ถ้าหาไม่เจอถอยไป python.exe
    if exe.lower().endswith("python.exe"):
        pyw = exe[:-len("python.exe")] + "pythonw.exe"
        import os

        if os.path.exists(pyw):
            exe = pyw
    return f'"{exe}" -m tamaclaude --tray'


def enable(cmd: str | None = None) -> None:
    winreg = _reg()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, _VALUE, 0, winreg.REG_SZ, cmd or command())


def disable() -> None:
    winreg = _reg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, _VALUE)
    except FileNotFoundError:
        pass


def is_enabled() -> bool:
    winreg = _reg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_QUERY_VALUE) as k:
            winreg.QueryValueEx(k, _VALUE)
            return True
    except FileNotFoundError:
        return False
