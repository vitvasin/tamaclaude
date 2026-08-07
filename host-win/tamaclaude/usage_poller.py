"""รอบการยิงโควตา + ตัวจับเวลา — พอร์ตของ `UsagePoller.swift`

จับเวลาที่นี่ที่เดียว ไม่มี timer ของตัวเอง: ถูกป้อน `tick(now)` จากนาฬิกาวินาทีละครั้งของ daemon
ตรรกะทั้งอันจึงเป็นฟังก์ชันของเวลาที่ส่งเข้ามา เทสต์ได้โดยไม่ต้องรอของจริง · ลูกยิงรอบเดียวแล้ว
ตาย ตัวนี้ตัดสินว่าเมื่อไรมีลูกตัวถัดไป
"""

from __future__ import annotations

import enum
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from . import session_key_file, usage_poll


class PollInterval(enum.IntEnum):
    OFF = 0
    MINUTE = 60
    FIVE_MINUTES = 300

    @property
    def seconds(self) -> int:
        return int(self.value)

    @property
    def title(self) -> str:
        return {0: "Off", 60: "60s", 300: "5 min"}[int(self.value)]

    @classmethod
    def fallback(cls) -> "PollInterval":
        # 60 วินาทีคือจังหวะที่แอปอื่นใช้จริง และเร็วเท่าที่ค่าจะขยับอยู่แล้ว (หน้าต่าง 5 ชม.
        # ขยับราวหนึ่งเปอร์เซ็นต์ต่อสามนาที) ตัวเลือกที่เร็วกว่านั้นคือยิงถี่ขึ้นเพื่อได้ค่าเดิม
        return cls.MINUTE

    @classmethod
    def stored(cls, value: int | None) -> "PollInterval":
        # `Off` เป็นค่าที่เลือกได้จริง ต้องแยกจาก "ยังไม่เคยเลือก" — คืน 0 ให้คีย์ที่ไม่มีอยู่จะ
        # เปลี่ยนแอปที่เพิ่งติดตั้งให้เป็น Off เงียบๆ
        if value is None:
            return cls.fallback()
        try:
            return cls(value)
        except ValueError:
            return cls.fallback()


class PollBlock(enum.Enum):
    EXPIRED_KEY = "expired_key"  # 401/403 — ล็อกการยิงไว้ ยิงต่อก็ได้ 401 เดิม
    UNUSABLE_KEY_FILE = "unusable_key_file"  # ป้ายบอกอาการ ไม่ใช่ล็อก


@dataclass(frozen=True)
class Outcome:
    code: int
    output: str


# spawn หนึ่งตัว แล้วคืน callable สำหรับฆ่ามัน
Launcher = Callable[[str | None, Callable[[Outcome], None]], Callable[[], None]]

_ORG_PREFIX = "org "


def render_output(report: usage_poll.Report) -> str:
    lines = [f"{_ORG_PREFIX}{o.id} {o.name}" for o in report.orgs]
    lines.append(report.summary)
    return "\n".join(lines)


def parse_output(text: str) -> tuple[list[usage_poll.Org], str | None]:
    """`org <id> <ชื่อ>` = รายการ org · บรรทัดอื่นบรรทัดสุดท้าย = สถานะ · id ยัง validate ซ้ำ
    เพราะทางเดินจบที่ URL เหมือนตอนมาจากเน็ต"""
    orgs: list[usage_poll.Org] = []
    summary: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if not line.startswith(_ORG_PREFIX):
            summary = line
            continue
        rest = line[len(_ORG_PREFIX):].strip()
        cut = rest.find(" ")
        oid = rest if cut < 0 else rest[:cut]
        name = "" if cut < 0 else rest[cut + 1:].strip()
        try:
            oid = usage_poll.validated(oid)
        except usage_poll.Failure:
            continue
        orgs.append(usage_poll.Org(id=oid, name=name if name else oid))
    return orgs, summary


class UsagePoller:
    timeout = timedelta(seconds=30)  # ลูกที่ไม่จบใน 30 วิ ถูกฆ่า ไม่งั้นลูกค้างกองกันทุกนาที

    def __init__(
        self,
        launch: Launcher,
        interval: PollInterval = PollInterval.MINUTE,
        preferred_org: str | None = None,
        has_key: Callable[[], bool] | None = None,
        on_change: Callable[[], None] | None = None,
    ):
        self.interval = interval
        self.preferred_org = preferred_org
        self._has_key = has_key or session_key_file.is_usable
        self._launch = launch
        self.on_change = on_change

        self.blocked: PollBlock | None = None
        self.orgs: list[usage_poll.Org] = []
        self.status: str | None = None
        self.last_started: datetime | None = None
        self._started_at: datetime | None = None
        self._kill: Callable[[], None] | None = None
        # ลูกที่ถูกฆ่าแล้วยังพูดทีหลังได้ — รุ่นไม่ตรงคือเสียงจากอดีต ต้องไม่ทับของใหม่
        self._generation = 0

    @property
    def is_running(self) -> bool:
        return self._started_at is not None

    @property
    def current_org(self) -> str | None:
        chosen = usage_poll.pick(self.orgs, self.preferred_org)
        return chosen.id if chosen else self.preferred_org

    @property
    def can_poll(self) -> bool:
        # has_key ท้ายสุดเสมอ เพราะมันแตะดิสก์ (และไฟล์ credential) — เรียกทุกวินาทีเพื่อตอบ
        # คำถามที่ตัดจบด้วย Off ไปแล้วคืองานที่ไม่มีใครขอ
        return (
            self.interval != PollInterval.OFF
            and self.blocked != PollBlock.EXPIRED_KEY
            and not self.is_running
            and self._has_key()
        )

    def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now()
        if self._started_at is not None:
            if now - self._started_at >= self.timeout:
                self._kill_running("the quota check took too long and was stopped")
            return
        if (
            self.last_started is not None
            and now - self.last_started < timedelta(seconds=self.interval.seconds)
        ):
            return
        if not self.can_poll:
            return
        self._start(now)

    def poll_now(self, now: datetime | None = None) -> None:
        """ยิงเดี๋ยวนี้โดยไม่รอครบรอบ — ตอนตื่นจาก sleep และตอนเพิ่งตั้ง key"""
        if self.can_poll:
            self._start(now or datetime.now())

    def refresh_now(self, now: datetime | None = None) -> None:
        """ยิงเพราะผู้ใช้กดปุ่ม — ข้าม Off และข้ามล็อก key หมดอายุ · ยังกันลูกที่วิ่งอยู่กับ
        ไฟล์ key ที่ไม่มี/ใช้ไม่ได้"""
        if not self.is_running and self._has_key():
            self._start(now or datetime.now())

    def key_was_set(self, now: datetime | None = None) -> None:
        """ตั้ง key ใหม่แล้วกลับมายิงตามรอบเอง — ลูกที่วิ่งอยู่อ่าน key เก่า ผลของมันเป็นอดีต"""
        self.stop()
        self.blocked = None
        self.status = None
        self.poll_now(now)
        self._notify()

    def stop(self) -> None:
        if self._kill:
            self._kill()
        self._generation += 1
        self._kill = None
        self._started_at = None

    def _start(self, now: datetime) -> None:
        self._generation += 1
        gen = self._generation
        self._started_at = now
        self.last_started = now
        self._kill = self._launch(
            self.current_org, lambda outcome: self._finished(gen, outcome)
        )
        self._notify()

    def _kill_running(self, status: str) -> None:
        self.stop()
        self.status = status
        self._notify()

    def _finished(self, gen: int, outcome: Outcome) -> None:
        if gen != self._generation:
            return
        self._started_at = None
        self._kill = None

        orgs, summary = parse_output(outcome.output)
        # รายการว่าง = รอบนี้ถามไม่สำเร็จ ไม่ใช่ว่าบัญชีไม่มี org — ของเดิมยังจริงกว่า
        if orgs:
            self.orgs = orgs
        self.status = summary

        if outcome.code == 0:
            self.blocked = None
        elif outcome.code == usage_poll.REJECTED_KEY:
            self.blocked = PollBlock.EXPIRED_KEY
        elif outcome.code == usage_poll.UNUSABLE_KEY_FILE:
            self.blocked = PollBlock.UNUSABLE_KEY_FILE
        else:
            # 5xx/เน็ตหลุด/timeout — รอบหน้าหายเอง · ป้าย "ไฟล์ key ใช้ไม่ได้" ที่ค้างต้องหายด้วย
            # เพราะรอบนี้ไปได้ไกลกว่านั้นแล้ว
            if self.blocked == PollBlock.UNUSABLE_KEY_FILE:
                self.blocked = None
        self._notify()

    def _notify(self) -> None:
        if self.on_change:
            self.on_change()


def subprocess_launcher(executable: list[str] | None = None) -> Launcher:
    """spawn `--usage-poll` เป็น subprocess จริง — บางจนไม่มี logic ให้เทสต์ ตรรกะอยู่ใน UsagePoller

    key ไม่เคยผ่าน env/argv — ลูกอ่านไฟล์ ACL แคบเอง · org id ไม่ใช่ความลับ ส่งทาง env ได้ และ
    ลูกยัง validate ซ้ำ
    """
    cmd = executable or [sys.executable, "-m", "tamaclaude", "--usage-poll"]

    def launch(org_id: str | None, done: Callable[[Outcome], None]) -> Callable[[], None]:
        import os

        env = dict(os.environ)
        if org_id:
            env["TAMACLAUDE_ORG_ID"] = org_id
        else:
            env.pop("TAMACLAUDE_ORG_ID", None)

        try:
            proc = subprocess.Popen(
                cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
            )
        except OSError:
            done(Outcome(code=1, output="could not run the quota check"))
            return lambda: None

        def wait() -> None:
            out, _ = proc.communicate()
            done(Outcome(code=proc.returncode or 0, output=out or ""))

        threading.Thread(target=wait, daemon=True).start()

        def kill() -> None:
            if proc.poll() is None:
                proc.terminate()

        return kill

    return launch
