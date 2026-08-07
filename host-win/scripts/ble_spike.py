"""BLE spike — สองคำถามที่ต้องรู้คำตอบก่อนจะพอร์ตอะไรจริงจัง (Phase 2)

    python scripts/ble_spike.py [address]

1. Windows เจรจา MTU ได้ถึง 500 ไบต์ที่ `Wire.maxPayload` ต้องการไหม
   ถ้าไม่ถึง snapshot ที่ยาวจะถูกทิ้งทั้งก้อน (ไม่มี chunking บนสายนี้) และงบต้องหดตาม
2. `BleakClient.pair()` ทำให้ลิงก์เข้ารหัสพอที่ CHR_CONFIG (BLE_GATT_CHR_F_WRITE_ENC)
   จะยอมรับการเขียนไหม — ถ้าไม่ ทาง Wi-Fi provisioning กับการส่งกุญแจ LAN ต้องหาทางอื่น

เขียน CHR_STATE ด้วย snapshot จริงหนึ่งก้อน (มาสคอตควรขึ้นจอ) และเขียน CHR_CONFIG ด้วย
*ค่าความสว่างเดิมที่อ่านมา* — พิสูจน์ทางเข้ารหัสโดยไม่เปลี่ยนอะไรที่ตาเห็น
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bleak import BleakClient, BleakScanner  # noqa: E402

from tamaclaude.protocol import (  # noqa: E402
    MAX_PAYLOAD,
    CardKind,
    CardSnap,
    SessionSnap,
    Snapshot,
    UsageSnap,
    VisualState,
)

# `UUID128_TAMA(last)` ใน ct_ble.c ใส่ `last` ลงสองตำแหน่ง ไม่ใช่ตำแหน่งเดียว — ท้าย UUID
# เปลี่ยนตามหัวเสมอ · ตาราง GATT ใน CLAUDE.md เขียนไว้แค่ "...0002" ซึ่งอ่านแล้วเข้าใจว่า
# ท้ายคงที่ ยืนยันจากบอร์ดจริงว่าไม่ใช่ (bleak หา characteristic ไม่เจอ)
SERVICE = "7a9b0001-4c1e-4b6d-9e2a-1d5c3f0a0001"
CHR_STATE = "7a9b0002-4c1e-4b6d-9e2a-1d5c3f0a0002"
CHR_CONFIG = "7a9b0003-4c1e-4b6d-9e2a-1d5c3f0a0003"
CHR_EVENT = "7a9b0004-4c1e-4b6d-9e2a-1d5c3f0a0004"


def _on_event(_handle, data: bytearray) -> None:
    print(f"  [event] {bytes(data)!r}")


async def find() -> str | None:
    print(f"scanning for {SERVICE} ...")
    found = await BleakScanner.discover(timeout=10.0, return_adv=True)
    for dev, adv in found.values():
        if SERVICE in [u.lower() for u in (adv.service_uuids or [])]:
            print(f"found {dev.address} name={adv.local_name!r} rssi={adv.rssi}")
            return dev.address
    return None


async def main(address: str | None) -> int:
    address = address or await find()
    if not address:
        print("FAIL: no board advertising the service")
        return 1

    async with BleakClient(address) as client:
        print(f"\nconnected={client.is_connected}")

        # --- คำถามที่ 1: MTU ---
        try:
            mtu = client.mtu_size
        except Exception as exc:  # backend ที่ไม่รองรับจะโยน ไม่ใช่คืน None
            print(f"mtu_size unavailable: {exc!r}")
            mtu = -1
        usable = mtu - 3  # ATT header
        print(f"mtu_size={mtu}  usable={usable}  needed={MAX_PAYLOAD}  "
              f"{'OK' if usable >= MAX_PAYLOAD else 'TOO SMALL'}")

        for svc in client.services:
            if svc.uuid.lower() != SERVICE:
                continue
            for chr_ in svc.characteristics:
                print(f"  char {chr_.uuid}  {chr_.properties}")

        # --- เขียน snapshot จริงหนึ่งก้อน ---
        snap = Snapshot(
            clock="09:41",
            date="Fri 7 Aug",
            sessions=[SessionSnap("tamaclaude", VisualState.CELEBRATE)],
            cards=[CardSnap("windows host", "BLE spike reached the board", CardKind.DONE)],
            usage=[UsageSnap(42, 3600), UsageSnap(17, 86400)],
        )
        payload = snap.encoded()
        print(f"\nwriting snapshot ({len(payload)} bytes) to CHR_STATE ...")
        await client.write_gatt_char(CHR_STATE, payload, response=True)
        print("  wrote OK — the board should be showing a celebrating mascot")

        try:
            await client.start_notify(CHR_EVENT, _on_event)
            print("subscribed to CHR_EVENT")
        except Exception as exc:
            print(f"subscribe failed: {exc!r}")

        # --- คำถามที่ 2: การเขียนที่ต้องการลิงก์เข้ารหัส ---
        current = await client.read_gatt_char(CHR_CONFIG)
        print(f"\nCHR_CONFIG reads {bytes(current)!r}")
        level = json.loads(bytes(current).decode()).get("b")
        same = json.dumps({"b": level}, separators=(",", ":")).encode()

        print(f"writing the same brightness back ({same!r}) before pairing ...")
        try:
            await client.write_gatt_char(CHR_CONFIG, same, response=True)
            print("  wrote OK without pairing — link was already encrypted")
        except Exception as exc:
            print(f"  refused (expected): {exc!r}")
            print("pairing ...")
            try:
                paired = await client.pair()
                print(f"  pair() -> {paired}")
            except Exception as pexc:
                print(f"  PAIR FAILED: {pexc!r}")
                return 2
            try:
                await client.write_gatt_char(CHR_CONFIG, same, response=True)
                print("  wrote OK after pairing — encrypted path works")
            except Exception as wexc:
                print(f"  STILL REFUSED AFTER PAIRING: {wexc!r}")
                return 3

        await asyncio.sleep(2.0)  # เผื่อ event ที่บอร์ดจะยิงตามมา
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None)))
