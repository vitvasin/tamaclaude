"""แปลง JSON ที่ Claude Code / claude.ai ให้เป็น cache ที่ daemon อ่านได้ — พอร์ต `UsageWriter.swift`

เขียนคีย์ชุดเดียวกับที่ Claude Usage.app ใช้ และเวลาเป็น ISO8601 เหมือนกัน เพื่อให้ statusline
เดิมของผู้ใช้อ่านไฟล์นี้ต่อได้โดยไม่ต้องแก้อะไร · `rate_limits` ให้ epoch มา จึงต้องแปลงเป็น ISO
ก่อนเขียน

ทางเข้าสองทาง (`ingest` สำหรับ statusline, `ingest_api` สำหรับ claude.ai) แต่ลง `commit`/`merge`/
`write` ตัวเดียวกัน — กฎ "ภายในหน้าต่างเดียวกัน เปอร์เซ็นต์เพิ่มอย่างเดียว" ต้องมีเจ้าของคนเดียว
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .paths import USAGE_CACHE
from .usage_reader import parse as _parse_cache
from .usage_reader import _parse_iso

_KNOWN = {
    "UTILIZATION",
    "RESETS_AT",
    "WEEKLY_UTILIZATION",
    "WEEKLY_RESETS_AT",
    "TIMESTAMP",
}


def ingest(data: bytes | str, now: datetime | None = None, url: Path = USAGE_CACHE) -> str | None:
    """อ่าน JSON ของ statusline (`rate_limits`) แล้วเขียน cache — คืนบรรทัดสั้นไว้ใช้ต่อ"""
    root = _loads(data)
    limits = root.get("rate_limits") if isinstance(root, dict) else None
    if not isinstance(limits, dict):
        return None

    fields: list[tuple[str, str]] = []
    parts: list[str] = []
    # แต่ละหน้าต่างหายอิสระต่อกัน — คีย์ที่ไม่มีข้อมูลจะไม่ถูกเขียน การเขียนค่าว่างจะทำให้ฝั่ง
    # อ่านแยก "ไม่มี" ออกจาก "เป็น 0" ไม่ได้
    for json_key, pct_key, reset_key, label in (
        ("five_hour", "UTILIZATION", "RESETS_AT", "5h"),
        ("seven_day", "WEEKLY_UTILIZATION", "WEEKLY_RESETS_AT", "7d"),
    ):
        window = limits.get(json_key)
        if not isinstance(window, dict):
            continue
        pct = _as_int(window.get("used_percentage"))
        if pct is not None:
            fields.append((pct_key, str(pct)))
            parts.append(f"{label} {pct}%")
        epoch = window.get("resets_at")
        if isinstance(epoch, (int, float)) and not isinstance(epoch, bool):
            fields.append((reset_key, _iso_epoch(float(epoch))))

    return _commit(fields, parts, now or datetime.now(timezone.utc), url)


def ingest_api(data: bytes | str, now: datetime | None = None, url: Path = USAGE_CACHE) -> str | None:
    """อ่าน payload ของ `/api/.../usage` แล้วเขียน cache เดียวกัน — ทางเข้าที่สอง ไม่ใช่ module ที่สอง"""
    root = _loads(data)
    if not isinstance(root, dict):
        return None

    fields: list[tuple[str, str]] = []
    parts: list[str] = []
    # ชื่อคีย์ weekly มีหลายแบบที่เคยเจอในสนามจริง ส่วน `weekly_scoped` เป็น limit รายโมเดล
    # ไม่ใช่ของทั้งบัญชี จึงไม่อยู่ในรายการ kind ที่ยอมรับ
    for keys, kind, pct_key, reset_key, label in (
        (["five_hour"], "session", "UTILIZATION", "RESETS_AT", "5h"),
        (
            ["seven_day", "weekly", "week", "seven_days"],
            "weekly_all",
            "WEEKLY_UTILIZATION",
            "WEEKLY_RESETS_AT",
            "7d",
        ),
    ):
        found = _window(root, keys, kind)
        if found is None:
            continue
        percent, resets_at = found
        fields.append((pct_key, str(percent)))
        # เวลาผ่าน formatter ตัวเดียวกับทางเข้าเดิมเสมอ — "…:00Z" กับ "…:00.482Z" คือหน้าต่าง
        # เดียวกันแต่คนละสตริง แล้ว merge จะเข้าใจผิดว่าหน้าต่างหมุนแล้ว
        fields.append((reset_key, _iso_dt(resets_at)))
        parts.append(f"{label} {percent}%")

    return _commit(fields, parts, now or datetime.now(timezone.utc), url)


def _window(root: dict, keys: list[str], kind: str) -> tuple[int, datetime] | None:
    """หาหน้าต่างหนึ่งบาน — ระดับบนสุดก่อน (คีย์บอกชนิดตรงตัว) แล้วค่อย `limits` array

    เปอร์เซ็นต์ที่ไม่มี `resets_at` อ่านออกถือว่าไม่มีหน้าต่าง — ทิ้งทั้งบาน เพราะกฎ "เพิ่ม
    อย่างเดียว" เทียบได้ก็ต่อเมื่อรู้ว่าเป็นหน้าต่างเดียวกัน
    """
    for key in keys:
        entry = root.get(key)
        if not isinstance(entry, dict):
            continue
        pct = _as_float(entry.get("utilization"))
        at = entry.get("resets_at")
        at = _parse_iso(at) if isinstance(at, str) else None
        if pct is not None and at is not None:
            return _round_half_up(pct), at

    limits = root.get("limits")
    if isinstance(limits, list):
        for entry in limits:
            if not isinstance(entry, dict) or entry.get("kind") != kind:
                continue
            pct = _as_float(entry.get("percent"))
            at = entry.get("resets_at")
            at = _parse_iso(at) if isinstance(at, str) else None
            if pct is not None and at is not None:
                return _round_half_up(pct), at
    return None


def _commit(
    fields: list[tuple[str, str]], parts: list[str], now: datetime, url: Path
) -> str | None:
    """จบงานของทั้งสองทางเข้า — merge, ประทับเวลา, เขียน · ต้องผ่านบรรทัดชุดนี้ชุดเดียว"""
    if not fields:
        return None

    merged = _merge(fields, url)
    merged.append(("TIMESTAMP", str(int(now.timestamp()))))

    text = "".join(f"{k}={v}\n" for k, v in merged)
    _write(text, url)
    return " · ".join(parts) if parts else None


def _merge(incoming: list[tuple[str, str]], url: Path) -> list[tuple[str, str]]:
    """รวมกับค่าที่มีอยู่แทนการเขียนทับดื้อๆ

    invariant: **ภายในหน้าต่างเดียวกัน เปอร์เซ็นต์เพิ่มอย่างเดียว** (`resets_at` ตรงกัน = หน้าต่าง
    เดียวกัน) ค่าที่ต่ำกว่าจึงเก่ากว่าเสมอ · พอหน้าต่างหมุน `resets_at` เปลี่ยน ค่าที่ลดลงกลับถูก
    """
    try:
        existing = _parse_cache(url.read_text(encoding="utf-8"))
    except OSError:
        return list(incoming)

    fresh = dict(incoming)
    out = list(incoming)
    for pct_key, reset_key in (
        ("UTILIZATION", "RESETS_AT"),
        ("WEEKLY_UTILIZATION", "WEEKLY_RESETS_AT"),
    ):
        old_pct = _as_int(existing.get(pct_key))
        new_pct = _as_int(fresh.get(pct_key))
        if (
            old_pct is not None
            and new_pct is not None
            and existing.get(reset_key) == fresh.get(reset_key)  # หน้าต่างเดียวกันเท่านั้น
            and old_pct > new_pct
        ):
            out = [(k, str(old_pct)) if k == pct_key else (k, v) for k, v in out]

    # คีย์ที่แหล่งอื่นเขียนไว้แต่เราไม่รู้จัก (PROFILE_NAME, COST_*) ต้องรอด — ไม่งั้นเรา
    # ทำลายข้อมูลของเจ้าของร่วมที่เขียนไฟล์เดียวกัน
    for k in sorted(existing):
        if k not in _KNOWN:
            out.append((k, existing[k]))
    return out


def _write(text: str, url: Path) -> None:
    """temp + rename — Claude Usage.app เขียนไฟล์เดียวกันอยู่ · rename เป็น op เดียว ไม่มีใคร
    อ่านเจอไฟล์ที่เขียนค้างครึ่งทาง (os.replace atomic บน NTFS เช่นเดียวกับ POSIX)"""
    tmp = url.with_name(f".{url.name}.tamaclaude.tmp")
    url.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, url)


# MARK: - ตัวช่วยเล็กๆ


def _loads(data: bytes | str):
    try:
        return json.loads(data)
    except (json.JSONDecodeError, ValueError):
        return None


def _as_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return _round_half_up(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _round_half_up(x: float) -> int:
    """ปัดครึ่งขึ้น (away from zero สำหรับค่าบวก) — ตรงกับ `Int(pct.rounded())` ของ Swift ·
    Python `round()` ปัดไปเลขคู่ (16.5 -> 16) แล้วจะต่างกับ mac หนึ่งจุดที่ค่าลงท้าย .5"""
    import math

    return math.floor(x + 0.5) if x >= 0 else math.ceil(x - 0.5)


def _as_float(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _iso_epoch(epoch: float) -> str:
    return _iso_dt(datetime.fromtimestamp(epoch, tz=timezone.utc))


def _iso_dt(dt: datetime) -> str:
    """ISO8601 ไม่มีเศษวินาที เขต UTC เป็น 'Z' — ตรงกับ withInternetDateTime ของ Swift"""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
