"""ฝั่ง daemon ของท่อ hook — แทนที่ `SocketServer.swift`

CPython บน Windows **ไม่มี `AF_UNIX`** (ตัว OS มีตั้งแต่ 1803 แต่ interpreter ไม่ได้เปิดให้)
ท่อนี้จึงเป็น TCP บน 127.0.0.1 ซึ่ง **เปลี่ยนขอบเขตความไว้ใจ** ไปจากของเดิม: socket ที่วางใน
โฟลเดอร์โหมด 700 เห็นได้เฉพาะเจ้าของ ส่วนพอร์ต loopback ทุก process บนเครื่องต่อเข้ามาได้
จึงต้องมีโทเคนกำกับ และเทียบด้วย `hmac.compare_digest` **ก่อน** แตะเนื้อเฟรม

ฝั่งที่ยิงเข้ามาอยู่ใน `hook_client.py` ซึ่งจงใจแยกไฟล์เพราะมันห้าม import `json`
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import socket
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from .paths import HOOK_ENDPOINT, ensure_dir
from .protocol import HookEvent

TIMEOUT = 2.0
MAX_FRAME = 1 << 20  # เหตุการณ์จาก hook ไม่เคยใหญ่ขนาดนี้ — ที่เกินคือของที่ไม่ควรอ่าน


def parse_frame(frame: bytes, token: str) -> HookEvent | None:
    """แกะเฟรมหนึ่งอัน · คืน `None` เมื่อโทเคนไม่ตรงหรือเนื้อไม่ใช่เหตุการณ์ที่รู้จัก"""
    head, _, body = frame.partition(b"\n")
    parts = head.decode("utf-8", "replace").split()
    if len(parts) != 3:
        return None
    # เทียบก่อนแตะเนื้อเฟรมเสมอ และเทียบแบบเวลาคงที่
    if not hmac.compare_digest(parts[0], token):
        return None
    try:
        obj = json.loads(body)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None

    event = HookEvent.decode(obj)
    try:
        pid, born = int(parts[1]), int(parts[2])
    except ValueError:
        return event
    # pid 0 = hook ไต่ไม่เจอ ซึ่งแปลว่า "ไม่รู้" ไม่ใช่ "ไม่มีเจ้าของ" — ปล่อยว่างไว้แล้วตกกลับ
    # ไปใช้เกณฑ์เงียบครบ evict ตามเดิม ซึ่งช้าแต่ไม่เคยผิด
    if pid > 0:
        object.__setattr__(event, "owner", {"pid": pid, "startedAt": born})
    return event


class HookServer:
    """รับเหตุการณ์บน loopback แล้วส่งต่อให้ callback — หนึ่งเธรดต่อการเชื่อมต่อ

    เหตุการณ์มาถี่สุดตอน PreToolUse/PostToolUse ของหลาย session พร้อมกัน ซึ่งยังห่างจาก
    ระดับที่ thread-per-connection จะเป็นปัญหา และมันทำให้ `--hook` ไม่ต้องรอคิว
    """

    def __init__(self, on_event: Callable[[HookEvent], None]) -> None:
        self._on_event = on_event
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.token = secrets.token_urlsafe(24)
        self.port = 0

    def start(self, endpoint: Path = HOOK_ENDPOINT) -> int:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))  # พอร์ตชั่วคราว ไม่ใช่เลขตายตัวที่ใครก็เดาได้
        sock.listen(16)
        self._sock = sock
        self.port = sock.getsockname()[1]

        ensure_dir()
        endpoint.write_text(f"{self.port} {self.token}\n", encoding="utf-8")
        _lock_down(endpoint)

        self._thread = threading.Thread(target=self._serve, name="hook-server", daemon=True)
        self._thread.start()
        return self.port

    def stop(self, endpoint: Path = HOOK_ENDPOINT) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        try:
            endpoint.unlink()
        except OSError:
            pass

    def _serve(self) -> None:
        assert self._sock is not None
        while not self._stop.is_set():
            try:
                conn, peer = self._sock.accept()
            except OSError:
                return
            # ที่มาที่ไม่ใช่ loopback ไม่ต้องถึงขั้นเทียบโทเคน
            if peer[0] != "127.0.0.1":
                conn.close()
                continue
            threading.Thread(
                target=self._handle, args=(conn,), name="hook-conn", daemon=True
            ).start()

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(TIMEOUT)
            chunks: list[bytes] = []
            size = 0
            try:
                while size < MAX_FRAME:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
            except OSError:
                return
        event = parse_frame(b"".join(chunks), self.token)
        if event is not None:
            self._on_event(event)


def _lock_down(path: Path) -> None:
    """เหลือสิทธิ์เฉพาะเจ้าของ — คู่ของ `chmod 600` ฝั่ง POSIX

    ล้มเหลวแล้วปล่อยผ่านโดยตั้งใจ: ไฟล์นี้ถือแค่พอร์ตกับโทเคนของเซสชันนี้ ซึ่งตายไปพร้อม
    daemon · การทำให้ daemon ไม่ขึ้นเพราะ icacls ไม่ว่างเป็นการแลกที่ไม่คุ้ม · ของจริงที่ต้อง
    เข้มคือ session-key ซึ่งอยู่คนละไฟล์และมีกติกาของมันเอง
    """
    user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".strip("\\")
    if not user:
        return
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
            capture_output=True,
            timeout=5,
            check=False,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except (OSError, subprocess.SubprocessError):
        pass
