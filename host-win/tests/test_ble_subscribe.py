"""BleTransport._subscribe_events — กัน cap ถูกตัดเพราะ MTU เล็ก + subscribe ที่ Windows restore

บั๊กจริง: บอร์ดส่ง cap (27B) ตอน subscribe แต่ WinRT เริ่มที่ MTU 23 (payload 20B) แล้วค่อยโต
เป็น 517 · ถ้า subscribe ตอน MTU ยังเล็ก cap ถูกตัดเหลือ 20B, decode พัง, ทุกหน้าถูก gate ทิ้ง
เหลือแต่ usage · ต้องรอ MTU โตก่อน แล้ว toggle (stop→start) ให้เกิด subscribe event สดที่ MTU ใหญ่
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "host-win"))

from tamaclaude.ble import CHR_EVENT, BleTransport  # noqa: E402


class FakeClient:
    """เลียนแบบ WinRT: mtu เริ่ม 23 แล้วโตเป็น 517 หลังถูกถามไม่กี่ครั้ง · จด start/stop"""

    def __init__(self, grow_after: int = 3) -> None:
        self._reads = 0
        self._grow_after = grow_after
        self.calls: list[str] = []

    @property
    def mtu_size(self) -> int:
        self._reads += 1
        return 517 if self._reads > self._grow_after else 23

    async def start_notify(self, uuid, cb):
        self.calls.append(f"start:{uuid}")

    async def stop_notify(self, uuid):
        self.calls.append(f"stop:{uuid}")


def test_waits_for_mtu_then_toggles():
    t = BleTransport(on_event=lambda data: None)
    client = FakeClient()
    asyncio.run(t._subscribe_events(client))
    # ต้อง start → stop → start (toggle) บน CHR_EVENT
    assert client.calls == [f"start:{CHR_EVENT}", f"stop:{CHR_EVENT}", f"start:{CHR_EVENT}"]
    # และต้องอ่าน mtu จนพ้น 23 ก่อน subscribe รอบแรก
    assert client._reads > 3


def test_survives_when_mtu_never_grows():
    # บอร์ด/แบ็กเอนด์ที่ mtu ค้าง 23 ต้องไม่ค้าง — ตัดที่ ~4s แล้ว subscribe ต่อ (cap อาจถูกตัด
    # แต่ไม่ทำให้ทั้ง session ค้าง)
    t = BleTransport(on_event=lambda data: None)
    client = FakeClient(grow_after=10_000)
    asyncio.run(t._subscribe_events(client))
    assert client.calls == [f"start:{CHR_EVENT}", f"stop:{CHR_EVENT}", f"start:{CHR_EVENT}"]
