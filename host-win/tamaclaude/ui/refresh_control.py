"""วินัยของปุ่ม refresh ที่หัว popover — ตรรกะล้วน · port ของ RefreshControl.swift

endpoint นี้ไม่มีเอกสาร ปุ่มที่กดรัวได้จะยิงถี่เกินจำเป็น — ปุ่มจึงเป็นทางออกฉุกเฉิน ไม่ใช่ทางปกติ
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..usage_poller import PollInterval

COOLDOWN = 10.0  # เย็นตัวหลังยิงเสร็จ — สั้นพอไม่รู้สึกถูกลงโทษ ยาวพอกันการกดรัว


@dataclass(frozen=True)
class RefreshState:
    enabled: bool
    spinning: bool
    tooltip: str


def state(
    running: bool, has_key: bool, finished: datetime | None, now: datetime | None = None
) -> RefreshState:
    now = now or datetime.now()
    if running:
        return RefreshState(False, True, "Checking your quota…")
    # ไม่มี key ก็ไม่มีคำถามจะยิง — ปุ่มที่กดแล้วไม่เกิดอะไรแย่กว่าปุ่มที่กดไม่ได้
    if not has_key:
        return RefreshState(False, False, "Set a session key before checking your quota")
    left = _seconds_left(finished, now)
    if left is not None:
        return RefreshState(False, False, f"Just checked · you can check again in {left}s")
    return RefreshState(True, False, "Check your quota now")


def _seconds_left(finished: datetime | None, now: datetime) -> int | None:
    """เหลืออีกกี่วินาทีถึงกดได้ · None = กดได้แล้ว

    นาฬิกาเครื่องเดินถอยหลังได้ (sleep, NTP) เวลาที่ประทับในอนาคตจึงต้องไม่กลายเป็น
    การเย็นตัวชั่วนิรันดร์ — ตัดที่ COOLDOWN เต็มจำนวนแล้วมันคลายเองภายในสิบวินาที
    """
    if finished is None:
        return None
    elapsed = (now - finished).total_seconds()
    left = min(COOLDOWN, COOLDOWN - elapsed)
    if left <= 0:
        return None
    return int(math.ceil(left))


def wants_poll(
    interval: PollInterval, stamp: datetime | None, polled: datetime | None = None,
    now: datetime | None = None,
) -> bool:
    """เปิดแผงแล้วควรยิงเองไหม — ยิงเมื่อค่าที่มีเก่ากว่ารอบที่ผู้ใช้ตั้งไว้

    `polled` (รอบล่าสุดที่*ออกไป*) ไม่ใช่ของซ้ำกับ `stamp` (ค่าล่าสุดที่*กลับมา*) —
    รอบที่ล้มไม่เคยขยับ stamp ถ้าดูแต่ stamp การเปิดปิดแผงตอนเน็ตล่มจะยิงทุกครั้งที่ชำเลืองดู
    """
    if interval == PollInterval.OFF:
        return False
    now = now or datetime.now()
    round_s = timedelta(seconds=interval.seconds)
    if polled is not None and now - polled < round_s:
        return False
    if stamp is None:
        return True
    return now - stamp >= round_s
