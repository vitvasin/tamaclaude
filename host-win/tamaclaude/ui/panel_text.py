"""ข้อความท้าย popover — สถานะบอร์ดกับรายการ session · port ของ PanelText.swift

ตรรกะล้วน เทสต์ได้บนเครื่องไม่มีจอ · เส้นแบ่งระหว่าง "สิ่งที่พูด" กับ "วิธีวาด"

**ตัดจากฝั่ง mac ตามที่ยังไม่ได้พอร์ตมา Windows:** LAN failover (Phase 7 เลื่อน) จึงไม่มี
LanRoute — สถานะบอร์ดเป็นแค่ ต่อ/กำลังหา · session-starter/spawn ไม่ได้พอร์ต จึงไม่มี
startProblem · org-switching ยังอยู่เพราะ usage_poll มี Org ครบ
"""

from __future__ import annotations

import enum
from datetime import datetime

from ..protocol import Snapshot
from ..usage_poll import Org

APP_NAME = "TamaClaude"

# ข้อความ *คือ* ปลายทาง ไม่ใช่คำว่า "GitHub" ที่ซ่อนพาธไว้ — ไม่มี scheme เพราะ https://
# ไม่ได้บอกอะไรที่ปลายทางอื่นไม่มีเหมือนกัน
PROJECT_LINK = "github.com/thaitop/tamaclaude"
PROJECT_URL = "https://" + PROJECT_LINK


class KeyProblem(enum.Enum):
    """ท่อโควตาพัง — map จาก exit code ของ --usage-poll (2 = expired, 3 = ไฟล์ key ใช้ไม่ได้)"""

    EXPIRED_KEY = "expired"
    UNUSABLE_KEY_FILE = "unusable"


def board(connected: bool) -> str:
    """ไม่มีคำว่า disconnected: บอร์ดที่ยังหาไม่เจอกับบอร์ดที่หลุดเป็นสภาพเดียวกันสำหรับผู้ใช้ —
    แอปกำลังสแกนอยู่และจะกลับมาต่อเอง"""
    return "Board connected" if connected else "Looking for the board…"


def heading(orgs: list[Org], current: str | None, has_key: bool) -> str:
    """หัว popover — ชื่อ org ที่ตัวเลขบนแผงนี้มาจาก

    ยังไม่ได้ตั้ง key แปลว่ายังไม่เคยถามใครว่าบัญชีมี org อะไรบ้าง รายการที่ค้างจาก key ตัวก่อน
    จึงเป็นของเก่าที่ไม่มีอะไรรับรอง — ชื่อแอปจริงกว่า
    """
    if not has_key or current is None:
        return APP_NAME
    for org in orgs:
        if org.id == current:
            return org.name
    return APP_NAME


def can_switch_org(orgs: list[Org], has_key: bool) -> bool:
    """ลูกศรสลับ org โผล่ต่อเมื่อมีอะไรให้สลับ — บัญชีที่มี org เดียวไม่มีอะไรให้ตัดสินใจ"""
    return has_key and len(orgs) > 1


def key_problem(blocked: KeyProblem | None) -> str | None:
    """บรรทัด "ท่อพัง" — มีก็ต่อเมื่อผู้ใช้ต้องลงมือ ไม่ใช่ตอนเน็ตสะดุด"""
    if blocked == KeyProblem.EXPIRED_KEY:
        return "Session key expired — click to paste a new one"
    if blocked == KeyProblem.UNUSABLE_KEY_FILE:
        return "Session key file unusable — click to paste a new one"
    return None


def updated(stamp: datetime | None, now: datetime | None = None) -> str:
    """บรรทัด "ค่านี้อายุเท่าไร" — ความเก่าของตัวเลขที่เห็น ไม่ใช่สถานะของท่อ

    มีวินาทีจริงๆ: ทั้งฟีเจอร์เกิดจากคำถาม "เลขนี้ค้างหรือเปล่า" และแผงที่เปิดค้างวาดใหม่ทุก
    วินาทีอยู่แล้ว ตัวเลขที่เดินจึงเป็นหลักฐานว่าแผงยังมีชีวิต
    """
    if stamp is None:
        return "No quota figures yet"
    now = now or datetime.now()
    # นาฬิกาเครื่องเดินถอยหลังได้ (sleep, NTP) — อายุติดลบต้องไม่กลายเป็นข้อความประหลาด
    age = int(max(0, (now - stamp).total_seconds()))
    if age < 60:
        return f"Updated {age}s ago"
    minutes = age // 60
    if minutes < 60:
        return f"Updated {minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"Updated {hours}h ago"
    return f"Updated {hours // 24}d ago"


def sessions(snapshot: Snapshot) -> list[str]:
    """หนึ่งแถวต่อหนึ่ง session แล้วปิดท้ายด้วยจำนวนที่ล้นออกจาก slot ของบอร์ด

    `+N more` เป็นแถวสุดท้ายเสมอ ถ้าอยู่ข้างบนมันจะอ่านเหมือนหัวข้อของแถวที่ตามมา
    """
    if not snapshot.sessions:
        return ["No sessions"]
    rows = [f"{s.project} · {s.state.value}" for s in snapshot.sessions]
    if snapshot.overflow > 0:
        rows.append(f"+{snapshot.overflow} more")
    return rows
