"""ทางหลักไปหาบอร์ด — เขียนใหม่ตามสัญญาบนสาย ไม่ใช่แปลจาก CoreBluetooth

`BLETransport.swift` เป็นสเปกว่า *ส่งอะไร* ไม่ใช่ *ส่งอย่างไร* — ข้อควรระวังของ CoreBluetooth
ที่คอมเมนต์ฝั่งนั้นเตือนไว้ (TCC ต่อ `.app`, callback ที่ไม่ยิงตอนปิดบลูทูธ) ไม่มีคู่บน Windows

ยืนยันกับบอร์ดจริงแล้ว (ดู `scripts/ble_spike.py`):
- MTU 517 → เขียนได้ 514 ไบต์ ซึ่งเกิน `MAX_PAYLOAD` 500 ที่ต้องการ
- `CHR_CONFIG` ต้องการลิงก์เข้ารหัส: เขียนตอนยังไม่จับคู่ได้ `Insufficient Authentication`
  ต้อง `await client.pair()` ก่อน แล้ว **pair() คืน `None` ไม่ใช่ `True`** บน backend WinRT
  จึงห้ามตัดสินผลจากค่าที่คืนมา ให้ลองเขียนซ้ำแทน
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable

from bleak import BleakClient, BleakScanner

# `UUID128_TAMA(last)` ใน ct_ble.c ใส่ `last` ลง **สองตำแหน่ง** ไม่ใช่ตำแหน่งเดียว ท้าย UUID
# จึงเปลี่ยนตามหัวเสมอ · ตาราง GATT ใน CLAUDE.md เขียนไว้แค่ "...0002" ซึ่งอ่านแล้วเข้าใจว่า
# ท้ายคงที่ ยืนยันจากบอร์ดจริงว่าไม่ใช่
SERVICE = "7a9b0001-4c1e-4b6d-9e2a-1d5c3f0a0001"
CHR_STATE = "7a9b0002-4c1e-4b6d-9e2a-1d5c3f0a0002"
CHR_CONFIG = "7a9b0003-4c1e-4b6d-9e2a-1d5c3f0a0003"
CHR_EVENT = "7a9b0004-4c1e-4b6d-9e2a-1d5c3f0a0004"

SCAN_SECONDS = 8.0
RETRY_SECONDS = 3.0


class BleTransport:
    """เธรด asyncio ตัวเดียวที่เป็นเจ้าของวิทยุ

    bleak เป็น asyncio ส่วน Qt เป็นลูปของตัวเอง — ของสองอย่างนี้ห้ามแตะกันตรงๆ ทุกอย่าง
    ข้ามด้วย `call_soon_threadsafe` ทางเดียว
    """

    def __init__(
        self,
        on_event: Callable[[bytes], None] | None = None,
        on_link: Callable[[bool], None] | None = None,
        address: str | None = None,
    ) -> None:
        self._on_event = on_event
        self._on_link = on_link
        self._address = address
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client: BleakClient | None = None
        self._pending: bytes | None = None
        self._last_sent: bytes | None = None
        self._stop = threading.Event()
        self.connected = False

    # MARK: - วงจรชีวิต

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="ble", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._link_forever())
        except Exception:
            pass
        finally:
            self._loop.close()

    # MARK: - สิ่งที่โลกภายนอกเรียก (จากเธรดอื่นเสมอ)

    def send(self, payload: bytes) -> None:
        """คิวหนึ่งช่อง ทับของเดิมโดยตั้งใจ

        snapshot ที่ใหม่กว่าแทนที่อันเก่าได้เสมอ — คิวยาวแปลว่าบอร์ดจะได้อดีตที่ช้าลงเรื่อยๆ
        ซึ่งเป็นเหตุผลเดียวกับที่ท่ามาสคอตไม่เข้าคิว
        """
        self._pending = payload

    # MARK: - ข้างใน

    async def _link_forever(self) -> None:
        while not self._stop.is_set():
            address = self._address or await self._find()
            if address is None:
                await asyncio.sleep(RETRY_SECONDS)
                continue
            try:
                await self._session(address)
            except Exception:
                pass
            self._set_connected(False)
            await asyncio.sleep(RETRY_SECONDS)

    async def _find(self) -> str | None:
        found = await BleakScanner.discover(timeout=SCAN_SECONDS, return_adv=True)
        for dev, adv in found.values():
            if SERVICE in [u.lower() for u in (adv.service_uuids or [])]:
                return dev.address
        return None

    async def _session(self, address: str) -> None:
        async with BleakClient(address) as client:
            self._client = client
            self._set_connected(True)
            # ส่งใหม่ทั้งก้อนหลังต่อติด — บอร์ดที่เพิ่งกลับมาไม่มีอะไรบนจอเลย และ
            # การกันซ้ำ (`_last_sent`) จะกลืน snapshot ที่เหมือนเดิมทิ้งไปเงียบๆ
            self._last_sent = None

            if self._on_event is not None:
                try:
                    await client.start_notify(
                        CHR_EVENT, lambda _h, data: self._on_event(bytes(data))
                    )
                except Exception:
                    pass

            while not self._stop.is_set() and client.is_connected:
                payload, self._pending = self._pending, None
                if payload is not None and payload != self._last_sent:
                    try:
                        await client.write_gatt_char(CHR_STATE, payload, response=True)
                        self._last_sent = payload
                    except Exception:
                        break
                await asyncio.sleep(0.2)
        self._client = None

    def _set_connected(self, value: bool) -> None:
        if value == self.connected:
            return
        self.connected = value
        if self._on_link is not None:
            self._on_link(value)
