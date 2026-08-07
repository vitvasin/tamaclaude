"""ที่อยู่ของทุกอย่างบนดิสก์ — พอร์ตของ `Paths.swift`

`~/.tamaclaude` ของฝั่ง macOS คือ `%USERPROFILE%\\.tamaclaude` ที่นี่ · `~/.claude/settings.json`
อยู่ที่เดิมทั้งสองระบบ (Claude Code เป็นคนกำหนด ไม่ใช่เรา)
"""

from __future__ import annotations

import os
from pathlib import Path

HOME = Path(os.path.expanduser("~"))

TAMA_DIR = HOME / ".tamaclaude"
CLAUDE_DIR = HOME / ".claude"

SETTINGS = CLAUDE_DIR / "settings.json"
"""ไฟล์ที่ hook กับ statusline ถูกติดตั้งลงไป — เป็นของ Claude Code เราแค่เขียนคีย์ของเรา"""

USAGE_CACHE = CLAUDE_DIR / ".statusline-usage-cache"
"""แชร์กับเครื่องมืออื่น — คีย์แปลกหน้าในไฟล์นี้ต้องรอดจากการเขียนของเราเสมอ"""

TOOLS_JSON = TAMA_DIR / "tools.json"
WEATHER_CONFIG = TAMA_DIR / "weather.json"
"""ค่าตั้งหน้าอากาศบน Windows — ไม่มี GUI แบบ macOS จึงอ่านจากไฟล์ {"place":..,"unit":"C"|"F"}"""
SESSION_KEY = TAMA_DIR / "session-key"
FINNHUB_KEY = TAMA_DIR / "finnhub-key"
LAN_KEY = TAMA_DIR / "lan-key"

HOOK_ENDPOINT = TAMA_DIR / "hook-endpoint"
"""พอร์ต + โทเคนของ IPC — แทนที่ Unix socket ที่ CPython บน Windows ไม่มีให้ใช้"""

STATUSLINE = TAMA_DIR / "statusline.ps1"
"""สคริปต์ที่ยึดช่อง statusLine.command — PowerShell แทน sh ของ macOS"""

LOG = TAMA_DIR / "tamaclaude.log"


def ensure_dir() -> Path:
    TAMA_DIR.mkdir(parents=True, exist_ok=True)
    return TAMA_DIR
