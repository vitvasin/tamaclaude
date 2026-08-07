"""ตัวจริงของ daemon — พอร์ตจาก `host/Sources/TamaCore/SessionStore.swift`

กินเหตุการณ์จาก hook แล้วคายภาพหน้าจอออกมา · ไม่มี timer อยู่ข้างใน เวลาถูกป้อนเข้ามาทุกครั้ง
จึงเทสต์ได้ทั้งหมดโดยไม่ต้องรอจริง
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto

from . import text
from .protocol import CardKind, CardSnap, HookEvent, SessionSnap, Snapshot, VisualState
from .tool_map import ToolMap

DISTANT_PAST = datetime.min


@dataclass
class Timings:
    """ค่าเวลาทั้งหมดของตรรกะสถานะ รวมไว้ที่เดียวเพื่อให้เทสต์ตั้งค่าได้ (หน่วยเป็นวินาที)"""

    stop_alert: float = 45
    """Stop แล้วเงียบเกินเท่านี้ = ถือว่าต้องเตือนผู้ใช้"""

    celebrate: float = 5
    """ท่าดีใจหลังงานจบ ก่อนกลับไป idle"""

    min_pose: float = 5
    """ท่าหนึ่งต้องอยู่บนจออย่างน้อยเท่านี้ ก่อนยอมให้ท่าถัดไปแทน

    Read/Edit ส่วนใหญ่จบใน ~100 มิลลิวินาที ถ้าเปลี่ยนท่าตามเหตุการณ์ทันที ท่า
    reading/writing จะโผล่สั้นกว่าหนึ่งเฟรมของบอร์ด (tick 1 วิ + ดีเลย์ BLE) ผู้ใช้จึงเห็น
    แต่ thinking ตลอด ทั้งที่ตรรกะข้างในถูกแล้ว
    """

    entering: float = 1.2
    leaving: float = 1.2

    sleep: float = 300
    """ไม่มีความเคลื่อนไหวเกินเท่านี้ = มาสคอตนอน"""

    evict: float = 3600
    """ไม่มีความเคลื่อนไหวเกินเท่านี้ = ทิ้ง session ไปเลย (กัน session ค้างจาก crash)"""

    card_ttl: float = 600
    """การ์ดหายเองเมื่อไม่มีใครสนใจ"""


class Kind(Enum):
    """สิ่งที่ session กำลังทำ — เก็บแค่นี้ ส่วนสถานะภาพคำนวณจากมันบวกเวลา"""

    IDLE = auto()
    THINKING = auto()
    TOOL = auto()
    WAITING = auto()  # Notification: Claude รอผู้ใช้ตอบ
    FAILED = auto()   # StopFailure


@dataclass
class Activity:
    kind: Kind
    tool_state: VisualState | None = None


@dataclass
class Session:
    id: str
    project: str
    """ชื่อโฟลเดอร์ดิบ ยังไม่ผ่าน `text.fit` — การตัดเกิดที่ `snapshot()` ที่เดียว

    เคยตัดตั้งแต่ตรงนี้ แต่ป้ายที่มีเลขนับต่อท้าย ("x2") ต้องรู้ว่าเหลือที่เท่าไรก่อนตัด และ
    fit ซ้ำบนข้อความที่ตัดแล้วเป็นไปไม่ได้: ร่างไทยที่ประกอบแล้วอยู่ใน PUA ซึ่ง sanitize
    ทิ้งทั้งหมด ชื่อโปรเจกต์ภาษาไทยจะกลายเป็นบรรทัดว่าง
    """
    started_at: datetime
    last_activity: datetime
    activity: Activity = field(default_factory=lambda: Activity(Kind.THINKING))
    stopped_at: datetime | None = None
    celebrate_until: datetime | None = None
    ending_at: datetime | None = None
    subagents: int = 0
    owner: dict | None = None
    posed: VisualState | None = None
    posed_at: datetime = DISTANT_PAST
    raised_hand: bool = False

    def visual_state(self, now: datetime, t: Timings) -> VisualState:
        """ท่าที่ "ควรจะเป็น" ตามสถานะจริง ณ วินาทีนี้ — ยังไม่ผ่านการหน่วง"""
        if self.ending_at is not None:
            return VisualState.LEAVING  # prune() เป็นคนเอาออกเมื่อท่าจบ
        if now < self.started_at + timedelta(seconds=t.entering):
            return VisualState.ENTERING

        kind = self.activity.kind
        if kind is Kind.FAILED:
            return VisualState.ERROR
        if kind is Kind.WAITING:
            return VisualState.WAITING
        if kind in (Kind.TOOL, Kind.THINKING):
            # การกระจายงานชนะเครื่องมือ: ตอน subagent วิ่ง tool ที่เห็นเป็นของลูกน้อง ไม่ใช่
            # ของ session หลัก (แต่แพ้ error กับ waiting ข้างบน)
            if self.subagents > 0:
                return VisualState.CONDUCTING
            if kind is Kind.TOOL and self.activity.tool_state is not None:
                return self.activity.tool_state
            return VisualState.THINKING

        # kind is IDLE
        if self.celebrate_until is not None and now < self.celebrate_until:
            return VisualState.CELEBRATE
        # นอนชนะการทวงถาม: ถ้าเงียบมาเป็นนาทีแล้ว ท่ายืนทวงตลอดกาลไม่ได้สื่ออะไรเพิ่ม
        # การเตือนยังอยู่ในรูปการ์ด ซึ่งเป็นคนละช่องทางกับท่าของมาสคอต
        if now >= self.last_activity + timedelta(seconds=t.sleep):
            return VisualState.SLEEPING
        if self.stopped_at is not None and now >= self.stopped_at + timedelta(
            seconds=t.stop_alert
        ):
            return VisualState.WAITING
        return VisualState.IDLE

    def display_state(self, now: datetime, t: Timings) -> VisualState:
        """ท่าที่แสดงจริง: ท่าดิบที่ถูกหน่วงไว้ให้อยู่ครบ `min_pose` ก่อนเปลี่ยนตัว

        ท่าที่ถูกข้ามระหว่างหน่วงจะหายไปเลย ไม่เข้าคิว — คิวจะทำให้มาสคอตเล่าอดีตช้ากว่า
        ความจริงเรื่อยๆ เมื่อเครื่องมือยิงรัว ซึ่งแย่กว่าการตกท่าไปบางท่า
        """
        raw = self.visual_state(now, t)

        def latch() -> VisualState:
            self.posed = raw
            self.posed_at = now
            return raw

        # ท่าเข้า/ออกมีนาฬิกาของตัวเองอยู่แล้ว จึงไม่หน่วง ทั้งขาเข้าและขาออกจากมัน
        # (และ prune นับเวลาท่ามุดหายจาก ending_at ไม่ใช่จากจอ ถ้าหน่วงจะโดนลบก่อนได้แสดง)
        if (
            raw in (VisualState.ENTERING, VisualState.LEAVING)
            or self.posed is VisualState.ENTERING
        ):
            return latch()
        cur = self.posed
        if cur is None:
            return latch()
        if cur == raw:
            return cur
        # เรื่องด่วนกว่าแทรกได้ทันที (พัง/ต้องการมือคน) ที่เหลือรอให้ท่าปัจจุบันอยู่ครบเวลา
        if raw.priority > cur.priority or now >= self.posed_at + timedelta(
            seconds=t.min_pose
        ):
            return latch()
        return cur


@dataclass
class SlotGroup:
    """หนึ่ง slot บนจอ = ทุก session ของโปรเจกต์เดียวกันรวมกัน"""

    project: str
    """ชื่อดิบ ใช้เทียบกับหัวการ์ด — ไม่ใช่สิ่งที่ขึ้นจอ ดู `label`"""
    state: VisualState
    last_activity: datetime
    count: int

    @property
    def label(self) -> str:
        """ป้ายใต้มาสคอต — ชื่อโปรเจกต์ ต่อท้ายด้วยเลขนับเมื่อกลุ่มมีมากกว่าหนึ่งตัว

        เลขนับกันที่ของตัวเองไว้ก่อนแล้วชื่อจึงถูกตัดให้พอดีที่เหลือ ("x2" จึงไม่มีวันหลุด
        ขอบป้าย) · ใช้ "x" ไม่ใช่ U+00D7 เพราะฟอนต์บนบอร์ดไม่มีตัวนั้น และ sanitize จะทิ้ง
        มันเงียบๆ เหลือ "tamaclaude 2" ที่อ่านได้ว่าเป็นชื่อโปรเจกต์
        """
        if self.count <= 1:
            return text.fit(self.project, text.Limit.project)
        tail = f" x{self.count}"
        return text.fit_reserving(self.project, text.Limit.project, tail) + tail


@dataclass
class StoredCard:
    session_id: str | None
    title: str
    body: str
    kind: CardKind
    created_at: datetime


# ป้ายเดือน/วันแบบตายตัว — Swift ตรึง en_US_POSIX ไว้ด้วยเหตุผลเดียวกัน: สิ่งที่ขึ้นจอเป็น
# ข้อตกลงของ *จอ* ไม่ใช่ของเครื่องที่บังเอิญรันเดมอนอยู่ และฟอนต์บนบอร์ดมีแค่ ASCII กับไทย
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class SessionStore:
    def __init__(
        self,
        tool_map: ToolMap | None = None,
        timings: Timings | None = None,
        slot_count: int = 3,
    ) -> None:
        self.tool_map = tool_map or ToolMap()
        self.timings = timings or Timings()
        self.slot_count = slot_count
        """จำนวน slot บนจอ ที่เหลือถูกนับเป็น "+N"

        ต้องตรงกับ `[slots] count` ใน tools/layout.toml เสมอ — ส่งเกินไปแล้วบอร์ดตัดทิ้ง
        เงียบๆ (ct_model.c) และ "+N" จะนับต่ำกว่าจริง คือโกหกว่ามองเห็นครบทุก session
        """
        # ถาม kernel ว่าเจ้าของ session ยังอยู่ไหม — แทนที่ได้เพื่อให้เทสต์ไม่ต้องฆ่า process จริง
        self.is_process_alive = lambda owner: True

        self._order: list[str] = []  # ลำดับซ้าย->ขวา ต้องนิ่ง = ลำดับที่ session เกิด
        self._sessions: dict[str, Session] = {}
        self._cards: list[StoredCard] = []
        # เลขเหตุการณ์ที่ต้องการคน — ขึ้นทีละหนึ่งตอนมีคน *เริ่ม* รอ ไม่ใช่ตลอดเวลาที่ยังรออยู่
        self._attention = 0

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    # MARK: - รับเหตุการณ์

    def apply(self, e: HookEvent, now: datetime) -> None:
        sid = e.session_id
        if sid not in self._sessions:
            # เหตุการณ์แรกที่เห็นเป็นตัวสร้าง session แม้จะไม่ใช่ SessionStart
            # (daemon อาจเพิ่งสตาร์ททีหลัง session)
            self._sessions[sid] = Session(
                id=sid, project=e.project, started_at=now, last_activity=now
            )
            self._order.append(sid)

        s = self._sessions[sid]
        s.last_activity = now
        s.ending_at = None
        # เขียนทับได้เสมอ ไม่ใช่เขียนครั้งเดียว: ผู้ใช้ `--resume` session เดิมได้ ซึ่งได้
        # process ตัวใหม่แต่ id เดิม ถ้ายึดตัวแรกไว้ session ที่ฟื้นมาจะถูกฆ่าทิ้งทันที
        if e.owner is not None:
            s.owner = e.owner
        if e.cwd:
            s.project = e.project

        name = e.hook_event_name
        if name == "SessionStart":
            s.started_at = now
            s.activity = Activity(Kind.IDLE)
            s.stopped_at = None
            s.subagents = 0

        elif name == "UserPromptSubmit":
            s.activity = Activity(Kind.THINKING)
            s.stopped_at = None
            s.celebrate_until = None
            # เทิร์นใหม่ = ล้างตัวนับที่ค้างจากเทิร์นก่อน (SubagentStop ที่หายไปตอน daemon
            # ไม่ได้รัน จะทำให้ session ติดท่า conducting ตลอดกาลถ้าไม่ล้าง)
            s.subagents = 0
            self._dismiss_cards(sid)

        elif name == "PreToolUse":
            s.activity = Activity(Kind.TOOL, self.tool_map.state(e.tool_name or ""))
            s.stopped_at = None
            # ขออนุญาตแล้วได้ไปต่อ = คำขอนั้นตายแล้ว การ์ดต้องไม่ค้างจนหมดอายุเอง
            self._dismiss_cards(sid)

        elif name == "PostToolUse":
            s.activity = Activity(Kind.THINKING)
            self._dismiss_cards(sid)

        elif name == "PreCompact":
            s.activity = Activity(Kind.THINKING)

        elif name == "SubagentStart":
            s.subagents += 1
            s.activity = Activity(Kind.THINKING)

        elif name == "SubagentStop":
            s.subagents = max(0, s.subagents - 1)

        elif name == "Notification":
            s.activity = Activity(Kind.WAITING)
            s.stopped_at = None
            self._push(
                StoredCard(sid, s.project, e.message or "needs your input",
                           CardKind.ALERT, now)
            )

        elif name == "Stop":
            s.activity = Activity(Kind.IDLE)
            s.stopped_at = now
            s.celebrate_until = now + timedelta(seconds=self.timings.celebrate)
            s.subagents = 0  # main loop จบแล้ว จะมี subagent ค้างจริงไม่ได้
            # เทิร์นจบแล้ว คำขออนุญาตของเทิร์นนั้นหมดความหมาย ต่อให้ผู้ใช้กดปฏิเสธ
            # (ทางนั้นไม่มี PostToolUse มาล้างให้) — การเตือนที่เหลือมาทาง _stop_alerts
            self._dismiss_cards(sid)

        elif name in ("StopFailure", "SubagentStopFailure"):
            s.activity = Activity(Kind.FAILED)
            s.stopped_at = None
            s.celebrate_until = None
            self._push(
                StoredCard(sid, s.project,
                           e.reason or e.message or "stopped with an error",
                           CardKind.ALERT, now)
            )

        elif name == "SessionEnd":
            s.ending_at = now
            s.activity = Activity(Kind.IDLE)
            s.subagents = 0
            self._dismiss_cards(sid)

    def _push(self, card: StoredCard) -> None:
        # ใบใหม่สุดอยู่บน และ session หนึ่งมีการ์ดค้างได้ใบเดียว
        self._cards = [
            c for c in self._cards
            if not (c.session_id is not None and c.session_id == card.session_id)
        ]
        self._cards.insert(0, card)

    def _dismiss_cards(self, session_id: str) -> None:
        self._cards = [c for c in self._cards if c.session_id != session_id]

    # MARK: - เวลาเดิน

    def prune(self, now: datetime) -> None:
        """ทิ้ง session ที่จบท่ามุดหายแล้ว/ค้างนานเกิน และการ์ดที่หมดอายุ"""
        for sid in list(self._sessions):
            s = self._sessions[sid]
            # ปิดหน้าต่าง terminal = process ตายทันที `SessionEnd` ไม่มีวันยิง จึงเช็คตัว
            # process เองแทนที่จะรอ hook ที่ไม่มา · ให้มุดหายเหมือนจบปกติ ไม่ใช่หายวับ
            if s.ending_at is None and s.owner is not None and not self.is_process_alive(
                s.owner
            ):
                s.ending_at = now
                s.activity = Activity(Kind.IDLE)
                s.subagents = 0
                self._dismiss_cards(sid)

            ended = (
                s.ending_at is not None
                and now >= s.ending_at + timedelta(seconds=self.timings.leaving)
            )
            stale = now >= s.last_activity + timedelta(seconds=self.timings.evict)
            if ended or stale:
                del self._sessions[sid]
                self._order = [x for x in self._order if x != sid]
                self._dismiss_cards(sid)

        ttl = timedelta(seconds=self.timings.card_ttl)
        self._cards = [c for c in self._cards if now < c.created_at + ttl]

    def _stop_alerts(self, now: datetime) -> None:
        """เติมการ์ดของ session ที่ Stop แล้วเงียบเกินเกณฑ์

        เป็น `done` ไม่ใช่ `alert` — เทิร์นจบเฉยๆ ไม่มีอะไรค้างให้ตอบ ถ้าย้อมแดงเหมือนกันหมด
        ทุกเทิร์นที่ผู้ใช้ลุกจากโต๊ะจะได้การ์ดแดง แล้วสีแดงจะเลิกแปลว่า "ต้องมือคน"
        """
        threshold = timedelta(seconds=self.timings.stop_alert)
        for sid in self._order:
            s = self._sessions.get(sid)
            if s is None or s.stopped_at is None:
                continue
            if now < s.stopped_at + threshold:
                continue
            if any(c.session_id == sid for c in self._cards):
                continue
            self._push(StoredCard(sid, s.project, "your turn", CardKind.DONE, now))

    # MARK: - ภาพหน้าจอ

    def snapshot(self, now: datetime) -> Snapshot:
        self.prune(now)
        self._stop_alerts(now)

        # เดินท่าของทุกตัวก่อน (รวมตัวที่ตกจอ) เพื่อให้นาฬิกาหน่วงท่าเดินสม่ำเสมอ
        live: list[tuple[str, VisualState, datetime]] = []
        for sid in self._order:
            s = self._sessions.get(sid)
            if s is None:
                continue
            state = s.display_state(now, self.timings)
            # นับตอนขอบขาขึ้นของ *แต่ละ session* ไม่ใช่ของทั้งจอ: ตัวที่สองที่ขอความช่วยเหลือ
            # ระหว่างที่ตัวแรกยังรออยู่ ไม่ได้เปลี่ยนภาพรวมเลย แต่เป็นเรื่องใหม่ที่ต้องได้เด้ง
            needs = state.needs_human
            if needs and not s.raised_hand:
                self._attention += 1
            s.raised_hand = needs
            live.append((s.project, state, s.last_activity))

        # ควบ session ที่อยู่โปรเจกต์เดียวกันให้เหลือป้ายเดียว · ท่าที่ขึ้นจอคือท่าที่สำคัญ
        # ที่สุดในกลุ่ม (เสมอกันแล้วเอาตัวที่ขยับล่าสุด) เพราะกลุ่มที่มีตัวหนึ่งยกมือขออนุญาต
        # ต้องอ่านออกทันทีว่ามีมือยกอยู่ ไม่ใช่ถูกกลบด้วยตัวที่กำลังไถ Read
        groups: list[SlotGroup] = []
        group_of: dict[str, int] = {}
        for project, state, last in live:
            i = group_of.get(project)
            if i is None:
                group_of[project] = len(groups)
                groups.append(SlotGroup(project, state, last, 1))
                continue
            groups[i].count += 1
            better = state.priority > groups[i].state.priority or (
                state.priority == groups[i].state.priority
                and last > groups[i].last_activity
            )
            if better:
                groups[i].state = state
            groups[i].last_activity = max(groups[i].last_activity, last)

        # เลือกตัวที่ได้ slot ตามความสำคัญ แต่ *วาด* ตามลำดับเกิดเสมอ
        # สิ่งที่ต้องนิ่งคือลำดับซ้าย->ขวา ไม่ใช่พิกัด
        if len(groups) <= self.slot_count:
            chosen = groups
        else:
            # เรียงตาม: ความสำคัญ -> ขยับล่าสุด -> เกิดทีหลัง · ข้อสุดท้ายทำให้ผลนิ่งและตัด
            # ตัวที่เก่าสุดและเงียบสุดออกก่อน ซึ่งเป็นตัวที่ผู้ใช้น่าจะสนใจน้อยที่สุด
            ranked = sorted(
                enumerate(groups),
                key=lambda p: (p[1].state.priority, p[1].last_activity, p[0]),
                reverse=True,
            )
            keep = {i for i, _ in ranked[: self.slot_count]}
            chosen = [g for i, g in enumerate(groups) if i in keep]

        snaps = [SessionSnap(g.label, g.state) for g in chosen]
        # ชื่อที่มีมาสคอตยืนอยู่บนจอเดียวกันแล้ว — หัวการ์ดที่ซ้ำกับป้ายพวกนี้ถูกตัดทิ้งก็ต่อ
        # เมื่อในเซ็ตนี้มีชื่อเดียว (ดู `_card`)
        on_screen = {g.project for g in chosen}
        # "+N" นับเป็น *session* ไม่ใช่กลุ่ม: กลุ่มที่ขึ้นจอพาสมาชิกขึ้นไปครบทุกตัวแล้ว
        seated = sum(g.count for g in chosen)

        return Snapshot(
            clock=f"{now.hour:02d}:{now.minute:02d}",
            date=f"{_WEEKDAYS[now.weekday()]} {now.day} {_MONTHS[now.month - 1]}",
            overflow=max(0, len(live) - seated),
            sessions=snaps,
            # จอวาดได้ 2 ใบ (CT_CARD_MAX ใน tools/layout.toml) — ส่งเกินมาก็ถูกทิ้ง แล้วยัง
            # กิน MTU ของสิ่งที่จอใช้จริง ที่เหลือไปโผล่เป็น "+N" แทน
            cards=[self._card(c, on_screen) for c in self._cards[:2]],
            card_overflow=max(0, len(self._cards) - 2),
            attention=self._attention,
        )

    def _card(self, c: StoredCard, on_screen: set[str]) -> CardSnap:
        """การ์ดหนึ่งใบบนสาย — ชื่อโปรเจกต์ไม่เคยได้บรรทัดของตัวเองเมื่อมาสคอตของมันอยู่บนจอ

        การอ่านชื่อเดียวกันสองครั้งไม่ได้เพิ่มอะไรในเหลือบเดียว แต่มันดันประโยคที่บอกว่า
        *เกิดอะไรขึ้น* ลงไปเป็นบรรทัดล่างตัวเล็กสีจาง ซึ่งเป็นบรรทัดเดียวที่ต้องอ่านออกจริงๆ

        **`len(on_screen) == 1` ไม่ใช่แค่ `in on_screen`** — เหตุผลนั้นเป็นจริงก็ต่อเมื่อชื่อที่
        ตัดทิ้งชี้ไปได้ที่เดียว · มีสองโปรเจกต์ยืนพร้อมกันเมื่อไร ชื่อคือตัวชี้เป้าตัวเดียวที่มี:
        ลำดับการ์ดเรียงตามใหม่สุด ไม่ใช่ตามลำดับ slot และท่ามาสคอตก็แยกให้ไม่ได้เพราะ alert
        กับ done เป็นท่า waiting ทั้งคู่
        """
        if c.title not in on_screen:
            # ไม่มีมาสคอตของโปรเจกต์นี้บนจอ — การ์ดเป็นที่เดียวที่บอกได้ว่าใครขอ
            return CardSnap(
                title=text.fit(c.title, text.Limit.card_title),
                body=text.fit(c.body, text.Limit.card_body),
                kind=c.kind,
            )
        # ป้ายใต้มาสคอตพูดชื่อนี้อยู่แล้ว — พูดซ้ำก็ต่อเมื่อมันชี้เป้าได้จริง
        if len(on_screen) <= 1:
            return CardSnap(
                title=text.fit(c.body, text.Limit.card_title), body="", kind=c.kind
            )
        # ชื่อถูกตัดก่อน ประโยคได้ที่ที่เหลือทั้งหมด — ไม่ใช่ตัดทั้งบรรทัดรวดเดียว ซึ่งทำให้
        # ชื่อโปรเจกต์ยาวๆ ตัวเดียวกินบรรทัดจนไม่เหลือคำบอกว่าเกิดอะไรขึ้นเลย
        # · `head` ไม่ใช่ `fit`: ชื่อตรงนี้ชี้ไปที่ป้ายใต้มาสคอต ไม่ได้เป็นเนื้อหาของตัวเอง
        name = text.head(c.title, text.Limit.card_name)
        said = text.fit_reserving(c.body, text.Limit.card_title, f"{name}: ")
        return CardSnap(title=f"{name}: {said}", body="", kind=c.kind)
