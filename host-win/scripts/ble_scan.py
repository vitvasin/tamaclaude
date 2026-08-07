"""สแกนหาบอร์ดแล้วรายงานว่าเจออะไร — ขั้นแรกของ BLE spike (Phase 2)

    python scripts/ble_scan.py [seconds]

ไม่เขียนอะไรลงบอร์ด อ่านอย่างเดียว · หน้าที่เดียวคือตอบว่า firmware ที่เพิ่งแฟลชไป
ประกาศตัวจริงไหม และ Windows มองเห็น service UUID ที่ daemon จะต้องต่อด้วยหรือเปล่า
"""

from __future__ import annotations

import asyncio
import sys

from bleak import BleakScanner

SERVICE = "7a9b0001-4c1e-4b6d-9e2a-1d5c3f0a0001"


async def main(seconds: float) -> int:
    print(f"scanning {seconds:.0f}s for {SERVICE} ...")
    found = await BleakScanner.discover(timeout=seconds, return_adv=True)

    hits = []
    for dev, adv in found.values():
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        if SERVICE in uuids:
            hits.append((dev, adv))

    print(f"{len(found)} devices seen, {len(hits)} advertising the tamaclaude service\n")
    for dev, adv in hits:
        print(f"  MATCH  {dev.address}  name={adv.local_name!r}  rssi={adv.rssi}")
        print(f"         uuids={adv.service_uuids}")

    if not hits:
        # ชื่อที่บอร์ดใช้คือสองไบต์ท้ายของ MAC (docs/hardware.md) จึงเดาชื่อล่วงหน้าไม่ได้
        # ถ้าไม่มีตัวไหนโฆษณา service เลย ให้ดูรายชื่อดิบก่อนจะสรุปว่า firmware ไม่ขึ้น
        print("no match. everything the adapter saw, for triage:")
        for dev, adv in sorted(found.values(), key=lambda x: -x[1].rssi):
            print(f"  {dev.address}  rssi={adv.rssi:4d}  name={adv.local_name!r}")
    return 0 if hits else 1


if __name__ == "__main__":
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
    sys.exit(asyncio.run(main(seconds)))
