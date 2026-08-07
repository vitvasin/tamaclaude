"""ตัวเขียนไฟล์ session key — แอปเป็นคนเขียน ผู้ใช้แค่วางค่า · พอร์ตของ `SessionKeyFile.swift`

เหตุที่ไม่ปล่อยให้ผู้ใช้สร้างไฟล์เอง: สิทธิ์ของไฟล์เป็นส่วนหนึ่งของความปลอดภัยของ credential
เต็มบัญชี · อ่านกลับทาง `usage_poll.read_key` เสมอ — กฎว่าอะไรคือไฟล์ที่ใช้ได้มีสำเนาเดียว
"""

from __future__ import annotations

from pathlib import Path

from . import secret_file, usage_poll
from .paths import SESSION_KEY


def write(raw: str, url: Path = SESSION_KEY) -> None:
    """เขียน key ทับของเดิม แล้วล็อก ACL ให้เจ้าของคนเดียวตั้งแต่ไฟล์เพิ่งเกิด"""
    try:
        secret_file.write(raw, url, usage_poll.KEY_WORDING)
    except secret_file.Problem as p:
        raise usage_poll.Failure(p.message, usage_poll.UNUSABLE_KEY_FILE)


def is_usable(url: Path = SESSION_KEY) -> bool:
    """มี key ที่ยิงได้จริงไหม — ไม่ใช่แค่ "ไฟล์มีอยู่" · ผู้เรียกใช้ตัดสินใจว่าจะยิงไหม"""
    try:
        usage_poll.read_key(url)
        return True
    except usage_poll.Failure:
        return False
