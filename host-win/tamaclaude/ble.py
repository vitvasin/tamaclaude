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


def _stream_key(payload: bytes) -> str:
    """สายของ payload — แยกเหมือนที่ firmware แยก (main.c): มีคีย์ `pl` = plan, มี `g` = เฟรม
    ของหน้านั้น (ค่า g), ไม่มีทั้งคู่ = snapshot ของมาสคอต · เครื่องหมายคำพูดใน *ค่า* ของ JSON
    ถูก escape เสมอ ลำดับไบต์ `"g":` จึงโผล่เฉพาะตรงที่เป็นคีย์จริง"""
    if b'"pl":' in payload:
        return "plan"
    i = payload.find(b'"g":')
    if i < 0:
        return "mascot"
    j = i + 4
    num = bytearray()
    while j < len(payload) and payload[j] in b"-0123456789":
        num.append(payload[j])
        j += 1
    return "page:" + (num.decode() or "?")


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
        # คิวแยกตาม *สาย* ไม่ใช่ช่องเดียว: snapshot ของมาสคอต, plan, และเฟรมของแต่ละหน้าเป็น
        # คนละสายกัน · ช่องเดียวทับกันทำให้เฟรมที่ drain พร้อมกันในหนึ่ง tick (เช่นตอนบอร์ด
        # เพิ่งประกาศ cap แล้วทุกหน้าถูกส่งพร้อมกัน) เหลือรอดแค่ตัวสุดท้าย — หน้าอากาศหาย
        # เพราะหน้าคริปโต (g มากกว่า) ทับ แล้วไม่ re-drain อีก 15 นาที · ใหม่กว่าในสายเดียวกัน
        # ยังทับได้ (บอร์ดไม่ต้องการอดีตที่ช้าลงเรื่อยๆ) แต่คนละสายอยู่ร่วมกัน
        self._pending: dict[str, bytes] = {}
        self._last_sent: dict[str, bytes] = {}
        # คำสั่ง config (Wi-Fi / LAN key) เป็นคนละสัตว์กับ snapshot: เป็น *การกระทำ* ที่ต้อง
        # ส่งครบทุกอันตามลำดับ ห้าม dedup และห้ามทับ — "scan" แล้ว "join" ต่างจาก "join" เฉยๆ
        # จึงเป็น list ไม่ใช่ dict-per-stream · ต้องผ่านลิงก์เข้ารหัส (pair) ก่อนถึงเขียนได้
        self._pending_config: list[bytes] = []
        self._paired = False
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
        """คิวแยกตามสาย ทับของเดิมเฉพาะสายเดียวกัน

        ใหม่กว่าในสายเดิมแทนที่ได้เสมอ (บอร์ดไม่ต้องการอดีตที่ช้าลง) แต่คนละสายไม่ทับกัน
        · เก็บลำดับที่ถูก send เข้ามา (dict รักษาลำดับ) เพื่อให้ plan ไปก่อนเฟรมของหน้าเหมือน
        ที่ PageHub ตั้งใจ
        """
        self._pending[_stream_key(payload)] = payload

    def send_config(self, payload: bytes) -> None:
        """คิวคำสั่ง config (Wi-Fi command / LAN key) ไปเขียนที่ CHR_CONFIG

        ต่อท้ายตามลำดับ ไม่ทับ ไม่ dedup — ต่างจาก `send` โดยสิ้นเชิง (ดูคอมเมนต์ที่
        `_pending_config`) · เขียนจริงหลัง pair สำเร็จใน `_session`
        """
        self._pending_config.append(payload)

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
            self._last_sent.clear()
            self._paired = False  # ลิงก์ใหม่ = ต้อง pair ใหม่ก่อนเขียน config รอบนี้

            if self._on_event is not None:
                try:
                    await client.start_notify(
                        CHR_EVENT, lambda _h, data: self._on_event(bytes(data))
                    )
                except Exception:
                    pass

            while not self._stop.is_set() and client.is_connected:
                # หยิบทุกสายที่ค้างอยู่รอบนี้ทีเดียว — เขียนทีละ characteristic ตามลำดับที่เข้ามา
                # (plan ก่อนเฟรมหน้า) · เฟรมที่ถูก send ระหว่าง await จะไปรอบถัดไป
                batch, self._pending = self._pending, {}
                broke = False
                for key, payload in batch.items():
                    if payload == self._last_sent.get(key):
                        continue  # กันซ้ำต่อสาย — snapshot เดิมทุก 0.2s ไม่ต้องเขียนใหม่
                    try:
                        await client.write_gatt_char(CHR_STATE, payload, response=True)
                        self._last_sent[key] = payload
                    except Exception:
                        broke = True
                        break
                if broke:
                    break
                if self._pending_config and not await self._drain_config(client):
                    break
                await asyncio.sleep(0.2)
        self._client = None

    async def _drain_config(self, client: BleakClient) -> bool:
        """เขียนคำสั่ง config ที่ค้างทั้งหมดตามลำดับ — คืน False ถ้าลิงก์ขาด (ให้ session จบ)

        CHR_CONFIG บังคับลิงก์เข้ารหัส: เขียนตอนยังไม่จับคู่ได้ `Insufficient Authentication`
        ต้อง `pair()` ก่อน · **pair() คืน None ไม่ใช่ True บน WinRT** จึงห้ามตัดสินจากค่าที่คืน
        ให้พยายามเขียนแล้วดูว่าสำเร็จไหมแทน · ถ้ายังไม่ pair รอบนี้ ลองก่อนหนึ่งครั้ง
        """
        if not self._paired:
            try:
                await client.pair()
            except Exception:
                pass  # บาง backend/บอร์ดที่ bond ไว้แล้วโยน — ไม่ใช่สัญญาณล้ม ลองเขียนต่อ
            self._paired = True
        # หยิบทั้งคิว ถ้าเขียนไม่สำเร็จค่อยคืนตัวที่เหลือกลับหน้าคิว (ลำดับต้องคง)
        batch, self._pending_config = self._pending_config, []
        for i, payload in enumerate(batch):
            try:
                await client.write_gatt_char(CHR_CONFIG, payload, response=True)
            except Exception:
                self._pending_config = batch[i:] + self._pending_config
                return False
        return True

    def _set_connected(self, value: bool) -> None:
        if value == self.connected:
            return
        self.connected = value
        if self._on_link is not None:
            self._on_link(value)
