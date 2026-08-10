"""สิ่งเดียวที่ไอคอนบนแถบเมนูต้องรู้: เลขของหน้าต่าง 5 ชม. กับ pace · port ของ MenuBadge.swift

แยกจากโค้ดวาดเพราะกฎว่าเมื่อไรควรเตือนเป็นตรรกะล้วน ทดสอบได้โดยไม่ต้องมีหน้าจอ
"""

from __future__ import annotations

from dataclasses import dataclass

from ..protocol import UNKNOWN, UsageSnap
from ..usage_reader import SESSION_WINDOW, elapsed_percent


@dataclass(frozen=True)
class MenuBadge:
    percent: int
    pace: int  # เวลาในหน้าต่างเดินไปกี่ % — ตำแหน่งของขีด pace บนแถบ

    @property
    def is_alarming(self) -> bool:
        """แดง — ไม่มีเหลือง ไม่มีเขียว บนแถบเมนู · ไอคอน 16px มีคำถามเดียว "ต้องช้าลงไหม"

        ไม่รู้ว่าเวลาเดินไปถึงไหน = เทียบ pace ไม่ได้ = ไม่มีเหตุให้เตือน
        """
        return self.pace != UNKNOWN and self.percent > self.pace

    @staticmethod
    def from_usage(usage: list[UsageSnap] | None) -> "MenuBadge | None":
        """None แปลว่าไม่มีอะไรจะบอก ให้กลับไปเป็นไอคอนเดิม

        "ไม่รู้" กับ "ศูนย์" คนละเรื่อง (ADR-0001) — 0% ที่เดาเอาบนแถบเมนูคือคำโกหกที่
        ผู้ใช้เชื่อทันที เพราะมันดูเหมือนค่าที่วัดมา ส่วนแถบเปล่าดูเหมือนแอปพัง
        """
        if not usage:
            return None
        session = usage[0]
        if session.percent == UNKNOWN:
            return None
        # remaining == 0 = หน้าต่างหมุนไปแล้ว เปอร์เซ็นต์ที่ถืออยู่เป็นของหน้าต่างที่ตายแล้ว
        if session.remaining == 0:
            return None
        pace = elapsed_percent(session.remaining, SESSION_WINDOW)
        return MenuBadge(percent=session.percent, pace=pace)
