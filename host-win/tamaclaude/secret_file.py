"""กติกาของ "ไฟล์ที่เก็บความลับหนึ่งบรรทัด" — พอร์ตของ `SecretFile.swift`

macOS ใช้ mode 600 กัน group/other อ่าน · Windows ไม่มี POSIX bits ที่มีความหมาย ตัวคุมสิทธิ์
คือ NTFS ACL · การป้องกันจริงจึงเกิดตอน **เขียน**: ตัด inheritance แล้ว grant เฉพาะเจ้าของ
(ผ่าน `icacls`) ตั้งแต่ไฟล์เพิ่งเกิด · ตอนอ่านตรวจซ้ำแบบ best-effort ว่าไม่มี principal กว้าง
(Everyone/Users/Authenticated Users) ถืออยู่

sessionKey ของ claude.ai และ key ของ Finnhub ใช้กติกานี้ร่วมกัน — ต่างกันแค่ *ประโยค* ที่บอก
ผู้ใช้ว่าต้องทำอะไรต่อ · กติกาความปลอดภัยที่มีสองสำเนาคือกติกาที่วันหนึ่งจะเหลือสำเนาเดียวที่ถูก
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

# principal กว้างที่ถืออยู่ = ความลับนี้ไม่ใช่ของเจ้าของไฟล์คนเดียวแล้ว · เทียบด้วย SID (ไม่ขึ้น
# กับภาษาของ Windows) — ชื่อบัญชีถูกแปลตามภาษา แต่ SID เหล่านี้คงที่ทุกเครื่อง
_BROAD_SIDS = ("S-1-1-0", "S-1-5-11", "S-1-5-32-545")


class Problem(Exception):
    """ไฟล์นี้ใช้ไม่ได้ พร้อมประโยคที่บอกวิธีแก้ — ข้อความไม่เคยพาความลับติดออกมา"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Wording:
    """ประโยคที่ต่างกันของความลับแต่ละใบ — ที่เหลือเหมือนกันหมด"""

    noun: str  # ชื่อที่ใช้เรียกความลับใบนี้ เช่น "session key"
    missing: str  # ยังไม่มีไฟล์เลย — ทำอย่างไรถึงจะมี
    empty: str  # มีไฟล์แต่ว่าง — ต้องเอาอะไรใส่


def write(raw: str, url: Path, wording: Wording) -> None:
    """เขียนความลับทับของเดิม แล้วล็อก ACL ให้เจ้าของคนเดียวตั้งแต่วินาทีแรกที่ไฟล์มีตัวตน"""
    secret = raw.strip()
    if not secret:
        raise Problem(f"the {wording.noun} is empty — {wording.empty}")
    url.parent.mkdir(parents=True, exist_ok=True)

    tmp = url.with_name(f".{url.name}.tamaclaude.tmp")
    try:
        tmp.unlink()
    except OSError:
        pass
    tmp.write_text(secret, encoding="utf-8")
    _lock_acl(tmp)
    # ลบของเดิมก่อน rename — os.replace รักษาเนื้อไฟล์ใหม่ แต่เราล็อก ACL ที่ tmp มาแล้ว
    os.replace(tmp, url)


def read(url: Path, wording: Wording) -> str:
    """อ่านความลับกลับมา — ปฏิเสธไฟล์ที่ principal กว้างเปิดได้ · อ่านใหม่ทุกรอบ ไม่ cache"""
    target = url.resolve()
    if not target.exists():
        raise Problem(f"no {wording.noun} at {url} — {wording.missing}")
    if _others_can_read(target):
        raise Problem(
            f"{url} is readable by other users; remove inheritance and grant only your "
            f"account (icacls {url} /inheritance:r /grant:r %USERNAME%:F)"
        )
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        raise Problem(f"{url} is not readable text")
    secret = text.strip()
    if not secret:
        raise Problem(f"{url} is empty — {wording.empty}")
    return secret


def _lock_acl(path: Path) -> None:
    """ตัด inheritance แล้ว grant เฉพาะเจ้าของ — best-effort · ล้มเหลวไม่โยน เพราะการอ่านมี
    ด่านตรวจซ้ำของตัวเอง และไฟล์ใต้ %USERPROFILE% ก็แคบอยู่แล้วโดยปริยาย"""
    user = os.environ.get("USERNAME")
    if not user or os.name != "nt":
        return
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
            check=False, capture_output=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _others_can_read(path: Path) -> bool:
    """`True` เมื่อ principal กว้าง (Everyone/Users/Authenticated Users) ถือ ACE อยู่

    ใช้ `icacls /save` ที่พ่น SID string ออกมา (ไม่ใช่ชื่อบัญชีที่แปลตามภาษา) · หา SID ไม่ได้
    (icacls ไม่มี, ไม่ใช่ Windows) = fail-open: ด่านจริงคือการล็อก ACL ตอนเขียน ตัวนี้เป็นการ
    ตรวจซ้ำ ไม่ใช่ด่านเดียว"""
    if os.name != "nt":
        return False
    try:
        out = subprocess.run(
            ["icacls", str(path)], check=False, capture_output=True, timeout=10, text=True
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    # icacls ขึ้นต้นด้วย *พาธของไฟล์* (เช่น C:\Users\005514\...) ก่อนรายการ ACE · พาธใต้
    # โปรไฟล์ปกติมี "\Users\" อยู่แล้ว การสแกนหาชื่อ principal ในพาธจึงเป็น false positive
    # กับความลับทุกใบใต้ %USERPROFILE% (บั๊กนี้ทำให้ finnhub-key/session-key ถูกปฏิเสธทั้งที่
    # ACL แคบถูกต้อง) — ตัด token พาธทิ้งก่อนแล้วค่อยจับคู่เฉพาะบรรทัด ACE
    haystack = out.lower().replace(str(path).lower(), "")
    names = ("everyone", "authenticated users", "\\users", "builtin\\users")
    if any(sid.lower() in haystack for sid in _BROAD_SIDS):
        return True
    return any(name in haystack for name in names)
