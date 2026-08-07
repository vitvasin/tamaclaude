"""โหมด `--hook` — ต่อท้าย Claude Code hook ทุกตัว

กติกาข้อเดียวที่ห้ามพลาด: **คืน 0 และจบเร็วเสมอ แม้ daemon ไม่ทำงาน** · hook ที่พังหรือค้าง
จะไปทำให้ session ของผู้ใช้พังตาม ซึ่งแย่กว่าจอไม่ขยับมาก

**แยกไฟล์จาก `ipc.py` เพราะน้ำหนักของ import ไม่ใช่เรื่องสไตล์** · Claude Code *รอ* hook อยู่
ทุกครั้งที่ยิงเหตุการณ์ และ PreToolUse/PostToolUse ยิงตลอดเวลา · วัดบนเครื่องนี้: interpreter
เปล่า ~155ms, `+socket` ~175ms, `+json` ~250ms, `+bleak` ~700ms · ไฟล์นี้จึงแตะได้แค่ `socket`
กับ `ctypes` (ผ่าน `process_tree`) และ **ห้าม** import `json`, `bleak`, `PySide6`, `httpx`
ไม่ว่าทางตรงหรือทางอ้อม · การแกะ JSON เป็นงานของ daemon ซึ่งไม่มีใครรอ
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

from .paths import HOOK_ENDPOINT

TIMEOUT = 2.0


def send_hook_event(raw: bytes, endpoint: Path = HOOK_ENDPOINT) -> bool:
    """ยิงไบต์ดิบเข้า daemon · คืน False เมื่อส่งไม่ถึง — ผู้เรียกกลืนแล้วคืน 0 อยู่ดี

    รูปแบบบนสาย: `<token> <owner_pid> <owner_started_at>\\n<ไบต์ดิบจาก stdin>`
    หัวบรรทัดประกอบด้วย f-string ล้วน ไม่ผ่าน json
    """
    try:
        port_s, token = endpoint.read_text(encoding="utf-8").split(None, 1)
        port = int(port_s)
    except (OSError, ValueError):
        return False

    # ไต่หาเจ้าของ **ที่นี่เท่านั้น**: ตอนนี้ process ยังเป็นลูกของ Claude Code อยู่
    # พอส่งเข้าท่อแล้ว daemon อยู่คนละสายบรรพบุรุษ ไต่กลับไปไม่ได้อีก
    try:
        from .process_tree import claude_ancestor

        owner = claude_ancestor()
    except Exception:
        owner = None
    pid = owner.pid if owner else 0
    born = owner.started_at if owner else 0

    try:
        with socket.create_connection(("127.0.0.1", port), timeout=TIMEOUT) as sock:
            sock.sendall(f"{token.strip()} {pid} {born}\n".encode("utf-8") + raw)
        return True
    except OSError:
        return False


def run_hook(stdin=None, endpoint: Path = HOOK_ENDPOINT) -> int:
    """ทุกทางออกของฟังก์ชันนี้คือ 0 — ไม่มีข้อยกเว้น"""
    try:
        raw = (stdin or sys.stdin.buffer).read()
        if raw:
            send_hook_event(raw, endpoint)
    except Exception:
        pass
    return 0
