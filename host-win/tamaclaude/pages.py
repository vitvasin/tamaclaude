"""หน้าอื่นที่ไม่ใช่มาสคอต: ใครส่งได้ อันไหนควรส่งซ้ำ — พอร์ตของ `Pages.swift` + `PagePlan`

`Snapshot` ของหน้ามาสคอตไม่ทำตาม protocol นี้โดยตั้งใจ (ADR-0003): มันมีอยู่ก่อนและต้องเหมือนเดิม
ทุกไบต์ · ตัวแยกบนสายคือ *การมีคีย์ `g`* ไม่ใช่ค่าของมัน — เฟรมที่ไม่มีคีย์นี้คือหน้ามาสคอต
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from .protocol import MAX_PAYLOAD, dumps


class PageKind(enum.IntEnum):
    """ตัวเลขเดินทางบนสาย = สัญญากับ firmware แบบเดียวกับ VisualState · เพิ่มค่าที่นี่ต้องแก้
    `ct_page_kind_t` (firmware/main/ct_pages.h) และ `PAGES` (tools/gen/pages.py) พร้อมกัน"""

    MASCOT = 0
    WEATHER = 1
    CRYPTO = 2
    CALENDAR = 3
    STOCKS = 4

    @property
    def title(self) -> str:
        return {0: "Mascot", 1: "Weather", 2: "Crypto", 3: "Calendar", 4: "Stocks"}[int(self)]


@runtime_checkable
class PageFrame(Protocol):
    """เฟรมของหนึ่งหน้าที่เดินทางเป็นก้อนของตัวเอง (ADR-0003) · `age` = วินาทีนับจากตอนที่ Mac
    ได้ข้อมูลก้อนนี้มา board นับต่อเอง · `encoded` ต้องพอดี maxBytes โดยลำพัง ไม่มี chunking"""

    kind: PageKind
    age: int

    def encoded(self, max_bytes: int = MAX_PAYLOAD) -> bytes: ...


@dataclass
class PageRetire:
    """"ลืมหน้านี้ทิ้ง" — ผู้ใช้ปิดมันบน Mac · จำเป็นเพราะบอร์ดเก็บทุกหน้าใน RAM (ADR-0002):
    ไม่บอก = หน้าที่ปิดยังหมุนมาพร้อมตัวเลขเมื่อวานตลอดไป"""

    kind: PageKind
    age: int = 0  # ไม่มีข้อมูล จึงไม่มีอายุ — มีให้ครบตามสัญญา PageFrame เท่านั้น

    def encoded(self, max_bytes: int = MAX_PAYLOAD) -> bytes:
        # สั้นจนไม่มีอะไรให้บีบ — ประกอบตรงๆ · คีย์เรียงเองอยู่แล้ว (g < x)
        return dumps({"g": int(self.kind), "x": 1})


@dataclass(frozen=True)
class PagePlan:
    """ค่าตั้งของจอที่ผู้ใช้เลือก — ตัวจับเวลาอยู่บนบอร์ด นี่คือกติกาที่มันใช้จับ

    ตัวแยกจากเฟรมบนช่องเดียวกันคือคีย์ `pl` ซึ่งไม่มีในเฟรมของใครเลย (board `on_state`)
    """

    order: list[PageKind]  # เฉพาะหน้าที่เปิด เรียงตามที่ผู้ใช้จัด — ที่ไม่อยู่ในลิสต์หายจากรอบ
    auto_turn: bool
    rotation: int
    hold: int
    attention_jump: bool

    def encode(self) -> dict:
        return {
            "pl": [int(k) for k in self.order],
            "r": self.rotation,
            "h": self.hold,
            # 0/1 ไม่ใช่ true/false — บอร์ดอ่านตัวเลขทุกคีย์ที่เหลืออยู่แล้ว
            "j": 1 if self.attention_jump else 0,
            "t": 1 if self.auto_turn else 0,
        }

    def encoded(self) -> bytes:
        return dumps(self.encode())


class PageHub:
    """เก็บ *ค่าที่อ่านได้* กับ *เวลาที่อ่านมา* แยกกัน แล้วประกอบเฟรมตอนส่งจริง

    data age เปลี่ยนทุกวินาที ถ้าเทียบ "เปลี่ยนไหม" จากไบต์ที่มี age อยู่ด้วย บอร์ดจะโดนเขียน
    ทุกวินาทีตลอดไป — กฎเดิมห้ามไว้แล้ว
    """

    def __init__(self) -> None:
        # หน้าที่บอร์ดประกาศว่ารู้จัก (ADR-0006) — ว่าง = firmware เก่า มีแต่หน้ามาสคอต
        self.capability: set[PageKind] = set()
        self._plan: PagePlan | None = None
        self._sent_plan: bytes | None = None
        self._frames: dict[PageKind, PageFrame] = {}
        self._observed: dict[PageKind, datetime | None] = {}
        self._sent_body: dict[PageKind, bytes] = {}
        self._sent_observed: dict[PageKind, datetime | None] = {}

    def announce(self, kinds: list[PageKind]) -> None:
        """บอร์ดประกาศความสามารถ — รายการใหม่ทับของเดิมทั้งชุด · บอร์ดคนละตัว/เพิ่งแฟลชไม่ได้
        ถือเฟรมเก่าของเราไว้"""
        self.capability = set(kinds)
        self._sent_body.clear()
        self._sent_observed.clear()
        self._sent_plan = None

    def submit_plan(self, plan: PagePlan) -> None:
        self._plan = plan

    def allows(self, kind: PageKind) -> bool:
        """หน้ามาสคอตส่งได้เสมอ มันไม่เคยเป็นของใหม่สำหรับบอร์ดตัวไหน"""
        return kind == PageKind.MASCOT or kind in self.capability

    def submit(self, frame: PageFrame, observed_at: datetime | None) -> None:
        """มีข้อมูลใหม่ของหน้าหนึ่ง — `observed_at` คือตอนที่ Mac ได้ค่านี้ ไม่ใช่ตอนนี้"""
        self._frames[frame.kind] = frame
        self._observed[frame.kind] = observed_at

    def drop(self, kind: PageKind) -> None:
        """หน้าที่ผู้ใช้เพิ่งปิด — บอกบอร์ดให้ลืม ไม่ใช่แค่เลิกส่งของใหม่ (ADR-0002)"""
        if kind == PageKind.MASCOT:
            return  # หน้ามาสคอตปิดไม่ได้
        self._frames[kind] = PageRetire(kind=kind)
        self._observed[kind] = None

    def forget_sent(self) -> None:
        """บอร์ดกลับมา — มันไม่จำอะไรเลย ทุกหน้าในมือต้องส่งใหม่"""
        self._sent_body.clear()
        self._sent_observed.clear()
        self._sent_plan = None

    def drain(self, now: datetime, max_bytes: int = MAX_PAYLOAD) -> list[bytes]:
        """เฟรมที่ควรส่งเดี๋ยวนี้ เรียงตาม PageKind เพื่อให้ผลซ้ำได้ในเทสต์"""
        out: list[bytes] = []

        # ค่าตั้งไปก่อนเนื้อหาเสมอ: บอร์ดที่ได้เฟรมของหน้าที่ถูกปิดก่อนรู้ว่าปิด จะแสดงมันหนึ่ง
        # รอบก่อนหาย ซึ่งผู้ใช้อ่านว่าสวิตช์ไม่ทำงาน · ส่งเฉพาะบอร์ดที่ประกาศตัวแล้ว
        if self._plan is not None and self.capability:
            data = self._plan.encoded()
            if data != self._sent_plan:
                self._sent_plan = data
                out.append(data)

        for kind in sorted(PageKind, key=lambda k: int(k)):
            frame = self._frames.get(kind)
            if frame is None or not self.allows(kind):
                continue
            frame.age = 0
            body = frame.encoded(max_bytes)
            read_at = self._observed.get(kind)
            if body == self._sent_body.get(kind) and read_at == self._sent_observed.get(kind):
                continue
            self._sent_body[kind] = body
            self._sent_observed[kind] = read_at
            since = max(0, int((now - read_at).total_seconds())) if read_at is not None else 0
            frame.age = since
            out.append(frame.encoded(max_bytes))
        return out
