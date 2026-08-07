"""สิ่งที่วิ่งข้ามสาย — พอร์ตจาก `host/Sources/TamaCore/Protocol.swift`

คีย์ทุกตัวในไฟล์นี้คือสัญญากับ firmware การเปลี่ยนชื่อคีย์หรือลำดับของ `VisualState`
แปลว่าต้องแก้ `ct_model.c`/`ct_mascot.c` ด้วย
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import PurePath, PureWindowsPath

from . import text

# MARK: - เหตุการณ์จาก Claude Code hook


@dataclass(frozen=True)
class HookEvent:
    """รูปแบบ JSON ที่ Claude Code ป้อนเข้า stdin ของ hook

    เก็บเฉพาะฟิลด์ที่ daemon ใช้จริง ฟิลด์อื่นถูกละทิ้งตอน decode
    """

    hook_event_name: str
    session_id: str
    cwd: str | None = None
    tool_name: str | None = None
    message: str | None = None
    prompt: str | None = None
    reason: str | None = None
    source: str | None = None
    # process ของ Claude Code ที่เป็นเจ้าของ session นี้ — `--hook` เติมเองจากสายบรรพบุรุษ
    # ของตัวเอง ไม่ได้มากับ stdin จึง optional เสมอ
    owner: dict | None = None

    @classmethod
    def decode(cls, obj: dict) -> "HookEvent":
        return cls(
            hook_event_name=obj.get("hook_event_name", ""),
            session_id=obj.get("session_id", ""),
            cwd=obj.get("cwd"),
            tool_name=obj.get("tool_name"),
            message=obj.get("message"),
            prompt=obj.get("prompt"),
            reason=obj.get("reason"),
            source=obj.get("source"),
            owner=obj.get("owner"),
        )

    def encode(self) -> dict:
        out = {"hook_event_name": self.hook_event_name, "session_id": self.session_id}
        for key in ("cwd", "tool_name", "message", "prompt", "reason", "source", "owner"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out

    @property
    def project(self) -> str:
        """ชื่อโปรเจกต์ที่จะแสดงใต้มาสคอต — ชื่อโฟลเดอร์สุดท้ายของ cwd

        รับได้ทั้งพาธ POSIX (hook ที่ยิงมาจาก WSL หรือ Git Bash) และพาธ Windows —
        `PureWindowsPath` เข้าใจทั้งสองสแลช ส่วนเครื่องที่ส่ง `/home/x/proj` มาต้องได้
        `proj` ไม่ใช่ทั้งเส้น
        """
        if not self.cwd:
            return "claude"
        raw = self.cwd.rstrip("/\\")
        name = PureWindowsPath(raw).name if raw else ""
        if not name:
            name = PurePath(raw).name
        return name or "claude"


# MARK: - สถานะภาพของมาสคอต


class VisualState(str, Enum):
    """ต้องตรงกับ `STATES` ใน tools/gen/mascot.py และ enum ฝั่ง firmware ทุกตัว"""

    IDLE = "idle"
    READING = "reading"
    WRITING = "writing"
    BUILDING = "building"
    SEARCHING = "searching"
    THINKING = "thinking"
    WAITING = "waiting"
    SLEEPING = "sleeping"
    ALERT = "alert"
    CELEBRATE = "celebrate"
    ERROR = "error"
    ENTERING = "entering"
    LEAVING = "leaving"
    CONDUCTING = "conducting"
    BEACON = "beacon"

    @property
    def priority(self) -> int:
        """ลำดับความสำคัญตอนเลือกว่า session ไหนได้ slot เมื่อมีเกิน 4 ตัว — สูง = ได้ก่อน"""
        return _PRIORITY[self]

    @property
    def needs_human(self) -> bool:
        """เดินต่อเองไม่ได้ถ้าไม่มีมือคน — เกณฑ์เดียวที่ตัดสินว่าจอควรถูกดึงกลับมาหามาสคอต

        `celebrate`/`idle` ไม่อยู่ในนี้ทั้งที่เทิร์นจบแล้ว: งานที่จบเรียบร้อยไม่ได้ขออะไร
        """
        return self in (VisualState.WAITING, VisualState.ALERT, VisualState.ERROR)


_PRIORITY: dict[VisualState, int] = {
    VisualState.ALERT: 40,
    VisualState.ERROR: 40,
    VisualState.WAITING: 30,
    VisualState.ENTERING: 25,
    VisualState.LEAVING: 25,
    # สูงกว่า tool: session ที่คุม subagent อยู่ *เงียบสนิท* ไม่มี hook ยิงเป็นนาทีๆ
    # ตัวตัดสินอันดับสองคือ lastActivity ล่าสุดชนะ ถ้าให้เท่ากับ tool มันจะแพ้ session
    # ที่ไถ Read ไปเรื่อยๆ แล้วหลุดจอ ทั้งที่เป็นตัวที่น่าสนใจที่สุด
    VisualState.CONDUCTING: 22,
    VisualState.READING: 20,
    VisualState.WRITING: 20,
    VisualState.BUILDING: 20,
    VisualState.SEARCHING: 20,
    VisualState.THINKING: 20,
    VisualState.BEACON: 20,
    VisualState.CELEBRATE: 15,
    VisualState.IDLE: 10,
    VisualState.SLEEPING: 0,
}


# MARK: - snapshot ที่ส่งข้ามสาย


class CardKind(str, Enum):
    INFO = "info"
    ALERT = "alert"
    DONE = "done"


@dataclass(frozen=True)
class CardSnap:
    title: str
    body: str
    kind: CardKind

    def encode(self) -> dict:
        return {"t": self.title, "b": self.body, "k": self.kind.value}


@dataclass(frozen=True)
class SessionSnap:
    project: str
    state: VisualState

    def encode(self) -> dict:
        return {"p": self.project, "s": self.state.value}


UNKNOWN = -1
"""ค่าที่แปลว่า "ไม่รู้" — ศูนย์เป็นค่าจริง จึงใช้เป็น sentinel ไม่ได้"""


@dataclass(frozen=True)
class UsageSnap:
    """โควตาหนึ่งหน้าต่าง เข้ารหัสเป็น array 2 ช่อง `[percent, secondsRemaining]` ไม่ใช่ object
    เพราะคีย์กินไบต์ในงบ 500 ที่แชร์กับ session และ card

    **ส่งวินาทีที่เหลือ ไม่ใช่เวลารีเซ็ตสัมบูรณ์** — บอร์ดนับถอยลงเอง countdown จึงยังเดินถูก
    ตอน BLE หลุด และ daemon ไม่ต้องยิงใหม่ทุกนาทีเพียงเพื่ออัปเดตตัวเลข
    """

    percent: int = UNKNOWN
    remaining: int = UNKNOWN

    @property
    def is_known(self) -> bool:
        return self.percent != UNKNOWN or self.remaining != UNKNOWN

    def encode(self) -> list[int]:
        return [self.percent, self.remaining]


MAX_PAYLOAD = 500
"""ขนาดสูงสุดที่เขียนลง GATT characteristic ได้ในครั้งเดียว — MTU 517 หัก ATT header 3 ไบต์
แล้วเผื่อไว้อีกหน่อย"""


def dumps(obj) -> bytes:
    """JSON บนสาย — คีย์เรียงเสมอ ไม่มีช่องว่าง ไทยไปดิบๆ เป็น UTF-8

    `sort_keys` ไม่ใช่เรื่องความสวยงาม: ถ้าลำดับคีย์สุ่มไปเรื่อยๆ การเทียบว่า "snapshot
    เปลี่ยนไหม" จะจริงทุกครั้ง แล้วบอร์ดโดนยิงทุกวินาที
    """
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


@dataclass(frozen=True)
class Snapshot:
    """ก้อนเดียวที่อธิบายทั้งหน้าจอ — firmware วาดจากสิ่งนี้อย่างเดียว ไม่เก็บสถานะเอง"""

    clock: str
    date: str
    overflow: int = 0
    sessions: list[SessionSnap] = field(default_factory=list)
    cards: list[CardSnap] = field(default_factory=list)
    # จำนวน card ที่มีอยู่จริงแต่ไม่ได้ส่ง — จอวาดได้แค่ 2 ใบ การ์ดที่หายไปเงียบๆ
    # คือการเตือนที่หายไป ต้องเหลือร่องรอยว่ายังมีอีก
    card_overflow: int = 0
    # `[session, weekly]` เสมอเมื่อมี — `None` แปลว่าไม่เคยได้ข้อมูลเลย ซึ่งบอร์ดตีความว่า
    # "ถอยไปเป็นนาฬิกาตั้งโต๊ะ" ไม่ใช่ "วาดโครงเปล่า"
    usage: list[UsageSnap] | None = None
    # จำนวนครั้งที่มี session *เข้าสู่* สถานะที่ต้องการคน นับตั้งแต่ daemon เริ่มทำงาน
    # เป็น id ของเหตุการณ์ ไม่ใช่สถานะ: บอร์ดเด้งกลับหน้ามาสคอตเมื่อเลขนี้โตขึ้นเท่านั้น
    attention: int = 0

    def encode(self) -> dict:
        out = {
            "c": self.clock,
            "d": self.date,
            "o": self.overflow,
            "s": [x.encode() for x in self.sessions],
            "n": [x.encode() for x in self.cards],
            "m": self.card_overflow,
            "a": self.attention,
        }
        if self.usage is not None:
            out["u"] = [x.encode() for x in self.usage]
        return out

    def encoded(self, max_bytes: int = MAX_PAYLOAD) -> bytes:
        """encode แล้วบีบข้อความให้พอดี `max_bytes` — ไม่มี chunking บนสาย

        ลำดับการตัด: body ของ card -> title ของ card -> ตัด card ทิ้งจากใบล่างสุด -> โควตา
        sessions ไม่เคยถูกตัดทิ้ง เพราะมันคือสิ่งที่จอนี้มีไว้แสดง
        """
        copy = self
        data = dumps(copy.encode())
        if len(data) <= max_bytes:
            return data

        for limit in (40, 28, 18, 10, 0):
            copy = replace(
                copy,
                cards=[
                    CardSnap(c.title, text.clip(c.body, limit), c.kind) for c in copy.cards
                ],
            )
            data = dumps(copy.encode())
            if len(data) <= max_bytes:
                return data

        for limit in (24, 16, 10):
            copy = replace(
                copy,
                cards=[
                    CardSnap(text.clip(c.title, limit), c.body, c.kind) for c in copy.cards
                ],
            )
            data = dumps(copy.encode())
            if len(data) <= max_bytes:
                return data

        while copy.cards:
            # ใบที่ถูกตัดเพราะ MTU ล้นก็ยังต้องนับ — จอต้องบอกได้ว่ามีอีกกี่ใบ ไม่ว่ามันหายไป
            # เพราะจอวาดไม่พอหรือเพราะสายส่งไม่พอ
            copy = replace(
                copy, cards=copy.cards[:-1], card_overflow=copy.card_overflow + 1
            )
            data = dumps(copy.encode())
            if len(data) <= max_bytes:
                return data

        # โควตาตกก่อน session — session คือเหตุผลที่จอนี้มีอยู่ ส่วนโควตายังดูได้จาก
        # statusline บนจอคอม การหายไปของมันจึงไม่ทำให้อุปกรณ์ไร้ประโยชน์
        if copy.usage is not None:
            copy = replace(copy, usage=None)
            data = dumps(copy.encode())
            if len(data) <= max_bytes:
                return data

        return data
