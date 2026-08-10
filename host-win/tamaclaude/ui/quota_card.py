"""การ์ดโควตาหนึ่งใบใน popover — ทุกอย่างที่ต้องวาด ยกเว้นวิธีวาด · port ของ QuotaCard.swift

กฎว่าสีไหนขึ้นเมื่อไร ขีด pace อยู่ตรงไหน และเวลารีเซ็ตอ่านว่าอะไร เป็นตรรกะล้วนที่เทสต์ได้
บนเครื่องที่ไม่มีจอ ส่วนที่เหลือ (`quota_card_view.py`) เหลือแค่ "เอาค่าพวกนี้ไปวาด"

ภาษาภาพเดียวกับแผงบนบอร์ด — เกณฑ์สีและสูตร pace ต้องตรงกับ `usage_bar_color` ใน
`tools/gen/screen.py` และ `firmware/main/ct_ui.c`
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..protocol import UNKNOWN, UsageSnap
from ..usage_reader import SESSION_WINDOW, WEEKLY_WINDOW, elapsed_percent


class Level(enum.Enum):
    """ระดับที่สีอ่านออกมาได้ — "ระดับ" คือสิ่งที่เทสต์ได้ ส่วน "สี" คือการตีความของมัน"""

    UNKNOWN = "unknown"
    GOOD = "good"
    WARN = "warn"
    CRIT = "crit"


# เกณฑ์สามขั้น — ต้องตรงกับ warn_pct / crit_pct ใน tools/layout.toml
WARN_PCT = 60
CRIT_PCT = 85


@dataclass(frozen=True)
class QuotaCard:
    title: str  # ชื่อหน้าต่าง — ตอบว่า "นี่คือโควตาก้อนไหน"
    subtitle: str  # คำอธิบายสั้น — ว่างได้เมื่อ pill บอกไปแล้ว
    percent: int  # UNKNOWN = ไม่รู้ ซึ่งไม่ใช่ศูนย์ (ADR-0001)
    pace: int  # เวลาในหน้าต่างเดินไปกี่ % = ตำแหน่งขีดบนแถบ
    level: Level
    reset: str  # ทั้งสัมพัทธ์และสัมบูรณ์ เช่น "Resets in 2h24m (Today 23:00)"
    pill: str | None = None  # ป้ายเล็กข้างชื่อ — มีเฉพาะหน้าต่างที่ชื่อไม่บอกว่ายาวแค่ไหน

    @staticmethod
    def cards(
        usage: list[UsageSnap] | None, now: datetime | None = None
    ) -> list["QuotaCard"] | None:
        """`[session, weekly]` — คืน None เมื่อไม่มีอะไรจะบอกเลยทั้งสองหน้าต่าง

        การ์ดเปล่าสองใบอ่านได้ว่าอุปกรณ์พัง ทั้งที่ความจริงคือยังไม่เคยมีตัวเลขมาถึง
        """
        if not usage or not any(u.is_known for u in usage):
            return None
        now = now or datetime.now()
        session = usage[0] if len(usage) > 0 else UsageSnap()
        weekly = usage[1] if len(usage) > 1 else UsageSnap()
        return [
            QuotaCard._card(
                session, "Session usage", "5-hour rolling window",
                window=SESSION_WINDOW, now=now),
            # ป้าย "Weekly" แทนบรรทัดคำอธิบาย — "All models" กับ "Weekly" ต่อกันเป็นประโยคแล้ว
            QuotaCard._card(
                weekly, "All models", "", window=WEEKLY_WINDOW, now=now, pill="Weekly"),
        ]

    @staticmethod
    def _card(
        snap: UsageSnap, title: str, subtitle: str, window: int, now: datetime,
        pill: str | None = None,
    ) -> "QuotaCard":
        pace = elapsed_percent(snap.remaining, window)
        return QuotaCard(
            title=title, subtitle=subtitle, pill=pill, percent=snap.percent, pace=pace,
            level=QuotaCard.level_of(snap.percent, pace),
            reset=QuotaCard.reset_line(snap.remaining, now))

    @staticmethod
    def level_of(percent: int, pace: int) -> Level:
        """แดงทันทีที่ใช้เร็วกว่า pace ไม่ต้องรอถึง 85 — "60% ตอนเหลือเวลาอีกครึ่ง" เป็นปัญหา
        คนละแบบกับ "60% ตอนหมดเวลาพอดี" ส่วนเกณฑ์เปอร์เซ็นต์ยังอยู่ครบเพราะการ์ดไล่สีสามขั้นได้
        """
        if percent == UNKNOWN:
            return Level.UNKNOWN
        if pace != UNKNOWN and percent > pace:
            return Level.CRIT
        if percent >= CRIT_PCT:
            return Level.CRIT
        if percent >= WARN_PCT:
            return Level.WARN
        return Level.GOOD

    @staticmethod
    def reset_line(remaining: int, now: datetime) -> str:
        """สัมพัทธ์ตอบ "อีกนานไหม" สัมบูรณ์ตอบ "ตอนนั้นคือเมื่อไรของวัน" — คนละคำถาม"""
        if remaining == UNKNOWN:
            return "No reset time yet"
        # ศูนย์ = หน้าต่างหมุนไปแล้วและเรายังไม่รู้ค่าใหม่ ไม่ใช่ "อีก 0 นาที"
        if remaining <= 0:
            return "Resetting now"
        at = now + timedelta(seconds=remaining)
        return f"Resets in {QuotaCard._short_span(remaining)} ({QuotaCard._clock(at, now)})"

    @staticmethod
    def _short_span(seconds: int) -> str:
        """ความละเอียดลดลงตามระยะ เหมือน fmt_remaining บนบอร์ด — วินาทีไม่เคยเปลี่ยนการตัดสินใจ"""
        days = seconds // 86_400
        hours = (seconds % 86_400) // 3600
        minutes = (seconds % 3600) // 60
        if days > 0:
            return f"{days}d {hours}h"
        if hours > 0:
            return f"{hours}h {minutes:02d}m"
        # ต่ำกว่าหนึ่งนาทีปัดขึ้นเป็น 1m ไม่ใช่ 0m — "0m" อ่านเหมือนหมดแล้ว
        return f"{max(1, minutes)}m"

    @staticmethod
    def _clock(date: datetime, now: datetime) -> str:
        """วันนี้/พรุ่งนี้เรียกด้วยชื่อ ไกลกว่านั้นเป็นวันที่ — weekly รีเซ็ตได้ไกลถึงเจ็ดวัน
        ซึ่ง Today/Tomorrow ตอบไม่ได้ · ไม่ใช้ชื่อวัน (Thu) เพราะภายในเจ็ดวันมันกำกวม
        """
        time = date.strftime("%H:%M")
        days = (date.date() - now.date()).days
        if days <= 0:
            return f"Today {time}"
        if days == 1:
            return f"Tomorrow {time}"
        # %b %-d ไม่พกพาบน Windows (ไม่มี %-d) — ตัด leading zero เอง
        return f"{date.strftime('%b')} {date.day}, {time}"
