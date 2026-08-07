"""ประกอบทุกอย่างเข้าด้วยกัน + tick วินาทีละครั้ง — พอร์ตของ `Daemon.swift`"""

from __future__ import annotations

import sys
import threading
from dataclasses import replace
from datetime import datetime, timezone

from . import usage_reader
from .ipc import HookServer
from .paths import TOOLS_JSON
from .process_tree import ProcessHandle, is_alive
from .protocol import HookEvent
from .session_store import SessionStore
from .tool_map import ToolMap

TICK = 1.0


class Daemon:
    def __init__(
        self,
        use_ble: bool = True,
        echo: bool = False,
        verbose: bool = False,
        use_poll: bool = True,
    ):
        self.store = SessionStore(tool_map=ToolMap.load_or_default(TOOLS_JSON))
        # เจ้าของ session มาถึงเป็น dict บนสาย — แปลงกลับเป็น handle ตรงจุดที่ใช้จริง
        self.store.is_process_alive = lambda owner: _alive(owner)
        self.echo = echo
        self.verbose = verbose
        # ตัวยิงโควตา: spawn `--usage-poll` เป็นลูก ตามรอบที่ตั้งไว้ · ไม่มี key ก็ไม่ยิง
        # (can_poll ถาม has_key เอง) จึงปล่อยให้ tick ไปเรื่อยๆ ได้โดยไม่เผาโปรเซส
        self._poller = None
        if use_poll:
            from .usage_poller import UsagePoller, subprocess_launcher

            self._poller = UsagePoller(launch=subprocess_launcher())
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._server = HookServer(self._on_event)
        self._transport = None
        if use_ble:
            from .ble import BleTransport

            self._transport = BleTransport(on_event=self._on_board_event)

    # MARK: - เข้า

    def _on_event(self, event: HookEvent) -> None:
        with self._lock:
            self.store.apply(event, datetime.now())
        if self.verbose:
            print(f"[hook] {event.hook_event_name} {event.session_id[:8]} "
                  f"{event.tool_name or ''}".rstrip(), file=sys.stderr)

    def _on_board_event(self, data: bytes) -> None:
        if self.verbose:
            print(f"[board] {data!r}", file=sys.stderr)

    # MARK: - ออก

    def tick(self, now: datetime | None = None) -> bytes:
        now = now or datetime.now()
        # ตัวยิงถูกป้อนเวลาเดียวกับ snapshot — ไม่มี timer ของตัวเอง · ลูกเขียน cache เอง
        # แล้วรอบถัดไปของ usage_reader ด้านล่างก็หยิบไปส่ง (สองทางเดินอิสระ ปลายทางเดียว)
        if self._poller is not None:
            self._poller.tick(now)
        with self._lock:
            snap = self.store.snapshot(now)
        # โควตาถูกฉีดที่นี่ ไม่ใช่ใน SessionStore: daemon เป็นที่เดียวที่แตะดิสก์ ส่วน store เป็น
        # ฟังก์ชันบริสุทธิ์ของ hook events · อ่าน cache ทุก tick เหมือน Daemon.swift — ไฟล์เล็ก
        # และค่าที่เก่าคือค่าที่ถูก (ไม่มี TTL) · `None` = ไม่เคยมีข้อมูล -> บอร์ดถอยไปเป็นนาฬิกา
        usage = usage_reader.read(datetime.now(timezone.utc))
        if usage is not None:
            snap = replace(snap, usage=usage)
        payload = snap.encoded()
        if self.echo:
            print(payload.decode("utf-8", "replace"), flush=True)
        if self._transport is not None:
            self._transport.send(payload)
        return payload

    # MARK: - วงจรชีวิต

    def run(self) -> int:
        port = self._server.start()
        if self.verbose:
            print(f"[daemon] hook endpoint on 127.0.0.1:{port}", file=sys.stderr)
        if self._transport is not None:
            self._transport.start()
        try:
            while not self._stop.wait(TICK):
                self.tick()
        except KeyboardInterrupt:
            pass
        finally:
            self._server.stop()
            if self._transport is not None:
                self._transport.stop()
        return 0

    def stop(self) -> None:
        self._stop.set()


def _alive(owner) -> bool:
    """`True` เมื่อไม่รู้ — "ไม่รู้ว่าตายไหม" ต้องไม่แปลว่า "ตายแล้ว" ไม่งั้น session ที่ยัง
    ทำงานอยู่จะหายจากจอทุกครั้งที่ไต่สายบรรพบุรุษไม่เจอ"""
    handle = ProcessHandle.decode(owner)
    return True if handle is None else is_alive(handle)
