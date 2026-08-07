"""อ่าน/เขียน `~/.claude/settings.json` โดยไม่แตะของเดิม — ฐานร่วมของ hook/statusline installer

ไฟล์นี้เป็นของ Claude Code: คีย์ที่เราไม่รู้จักต้องรอดกลับออกไปครบ และสำรองไว้ก่อนเขียนเสมอ
เพราะผู้ใช้แก้เองมาแล้วแน่ๆ · แยกกฎการอ่าน/เขียนไว้ที่เดียว ไม่งั้นสอง installer จะมีสำเนากติกา
คนละใบที่วันหนึ่งจะไม่ตรงกัน
"""

from __future__ import annotations

import json
from pathlib import Path

from .paths import SETTINGS


class SettingsError(Exception):
    pass


def load(path: Path = SETTINGS) -> dict:
    """อ่าน settings.json เป็น dict · ไฟล์ไม่มี = {} · ไฟล์ว่าง = {} · ไม่ใช่ object = error"""
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        raise SettingsError("could not read ~/.claude/settings.json")
    if not raw.strip():
        return {}
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        raise SettingsError("settings.json is not valid JSON")
    if not isinstance(obj, dict):
        raise SettingsError("settings.json is not a JSON object")
    return obj


def save(root: dict, path: Path = SETTINGS) -> None:
    """สำรองของเดิม (ถ้ามี) แล้วเขียนทับ — pretty + sorted ให้ diff ของผู้ใช้อ่านง่าย"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            path.with_suffix(path.suffix + ".tamaclaude.bak").write_text(
                path.read_text(encoding="utf-8"), encoding="utf-8"
            )
        except OSError:
            pass
    text = json.dumps(root, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")
