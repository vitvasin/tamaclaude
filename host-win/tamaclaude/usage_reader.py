"""อ่าน utilization ของโควตาจาก cache ที่ statusline เขียนไว้ — พอร์ตของ `UsageReader.swift`

daemon ไม่เคยยิงเน็ตเองและไม่เคยถือ credential — ตัวเลขทั้งหมดมาจาก `rate_limits` ที่ Claude Code
ป้อนเข้า stdin ของ statusline แล้ว statusline เขียนลงไฟล์นี้ (หรือ `--usage-poll` เติมให้)

รูปแบบไฟล์เป็น KEY=VALUE บรรทัดละคีย์ ตรงกับที่ Claude Usage.app ใช้อยู่เดิม ทั้งสองฝ่ายจึง
เขียนไฟล์เดียวกันได้โดยไม่ขัดกัน — ใครเขียนทีหลังก็ถูก

**ไม่มี TTL** — เปอร์เซ็นต์ขยับได้ก็ต่อเมื่อผู้ใช้เรียก Claude ค่าที่เก่า *คือ* ค่าที่ถูก สิ่งเดียว
ที่ทำให้มันหมดอายุคือ `resets_at` ที่ผ่านไปแล้ว ซึ่งเป็นเส้นตายจริงในโดเมนนี้
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .paths import USAGE_CACHE
from .protocol import UNKNOWN, UsageSnap

# ความยาวหน้าต่างเป็นวินาที — ต้องตรงกับ `[usage]` ใน tools/layout.toml
SESSION_WINDOW = 18_000  # 5 ชั่วโมง
WEEKLY_WINDOW = 604_800  # 7 วัน


def read(now: datetime | None = None, url: Path = USAGE_CACHE) -> list[UsageSnap] | None:
    """อ่านแล้วแปลงเป็น `[session, weekly]` พร้อมส่งขึ้นบอร์ด

    คืน `None` เมื่อไม่มีอะไรจะบอกเลย (ไฟล์หาย/อ่านไม่ได้/ไม่มีคีย์ที่รู้จักสักตัว) ซึ่งบอร์ด
    ตีความว่าให้กลับไปเป็นนาฬิกา — โครงเปล่าดูเหมือนอุปกรณ์พัง
    """
    now = now or datetime.now(timezone.utc)
    try:
        text = url.read_text(encoding="utf-8")
    except OSError:
        return None
    fields = parse(text)

    session = _snap(fields.get("UTILIZATION"), fields.get("RESETS_AT"), now)
    weekly = _snap(fields.get("WEEKLY_UTILIZATION"), fields.get("WEEKLY_RESETS_AT"), now)

    if not (session.is_known or weekly.is_known):
        return None
    return [session, weekly]


def elapsed_percent(remaining: int, window: int) -> int:
    """เวลาในหน้าต่างเดินไปกี่เปอร์เซ็นต์แล้ว — ตำแหน่งของขีด pace

    อยู่ที่นี่เพราะที่นี่เป็นเจ้าของความยาวหน้าต่าง สองสูตรที่เขียนแยกกันจะเพี้ยนกันวันหนึ่ง
    """
    if remaining == UNKNOWN or window <= 0:
        return UNKNOWN
    # countdown ที่ยาวกว่าหน้าต่างแปลว่านาฬิกาสองฝั่งไม่ตรงกัน ไม่ใช่ว่าเวลาเดินถอยหลัง
    elapsed = min(window, max(0, window - remaining))
    return elapsed * 100 // window


def stamp(url: Path = USAGE_CACHE) -> datetime | None:
    """เวลาที่ cache ถูกเขียนครั้งล่าสุด — คนละเรื่องกับ `resets_at` ของหน้าต่าง

    ตอบคำถาม "ค่านี้อายุเท่าไร" ค่าที่เก่ายังถูกได้ แต่ผู้ใช้ควรรู้ว่าเก่าแค่ไหน
    """
    try:
        raw = parse(url.read_text(encoding="utf-8")).get("TIMESTAMP")
    except OSError:
        return None
    if raw is None:
        return None
    try:
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    except ValueError:
        return None


def parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        eq = line.find("=")
        if eq < 0:
            continue
        key = line[:eq]
        value = line[eq + 1 :].strip()
        # ค่าว่างนับเป็น "ไม่มีคีย์" — ไฟล์ที่เขียนครึ่งๆ ต้องไม่กลายเป็น 0%
        if value:
            out[key] = value
    return out


def _snap(percent: str | None, resets: str | None, now: datetime) -> UsageSnap:
    """เปอร์เซ็นต์และเวลาหายไปทีละตัวได้ — แต่ละหน้าต่างอาจไม่มีมาอิสระกัน"""
    pct = UNKNOWN
    remaining = UNKNOWN
    if percent is not None:
        try:
            value = int(percent)
        except ValueError:
            value = None
        if value is not None and 0 <= value <= 100:
            pct = value
    if resets is not None:
        date = _parse_iso(resets)
        if date is not None:
            raw = max(0, _ceil_seconds(date - now))
            # ปัดลงเป็นนาที — บอร์ดนับถอยลงเอง ค่าที่ส่งจึงไม่ควรเปลี่ยนทุกวินาที ไม่งั้น
            # snapshot ต่างกันทุก tick แล้ว BLE โดนยิงวินาทีละครั้ง · ที่ความละเอียดนาที มัน
            # เปลี่ยนพร้อมนาฬิกา `c` ซึ่งยิงอยู่แล้ว = ไม่มีของเพิ่ม
            remaining = max(60, raw // 60 * 60) if raw > 0 else 0
    # หน้าต่างหมุนไปแล้วโดยยังไม่มีใครอัปเดต cache: เปอร์เซ็นต์ที่ถืออยู่ผิดแน่ ตัวเลขที่ถูกคือ
    # "ไม่รู้" ไม่ใช่ 0 — ห้ามเดา
    if remaining == 0:
        pct = UNKNOWN
    return UsageSnap(percent=pct, remaining=remaining)


def _ceil_seconds(delta) -> int:
    s = delta.total_seconds()
    return int(-(-s // 1))  # ceil ทางบวก/ลบ ให้ตรงกับ `.rounded(.up)` ของ Swift


def _parse_iso(s: str) -> datetime | None:
    raw = s.strip()
    # Python 3.11 `fromisoformat` เข้าใจ 'Z' และ fractional seconds แล้ว
    try:
        d = datetime.fromisoformat(raw)
    except ValueError:
        return None
    # cache ที่เราเขียนใส่ offset เสมอ แต่ไฟล์จากมือคนอาจไม่มี — ถือว่าเป็น UTC ตามที่ mac เขียน 'Z'
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d
