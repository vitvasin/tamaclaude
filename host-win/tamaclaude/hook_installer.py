"""เขียน hook ของเราเข้า `~/.claude/settings.json` โดยไม่แตะของเดิม — พอร์ตของ `HookInstaller.swift`

คีย์ที่เราไม่รู้จักต้องรอดกลับออกไปครบ (ดู settings_file) · คำสั่ง hook เรียก `--hook` ผ่าน
console script `tamaclaude` (หรือ python -m tamaclaude) ที่ชี้ด้วยพาธสัมบูรณ์ — Claude Code รัน
มันโดยไม่ผ่าน shell ของเรา
"""

from __future__ import annotations

from pathlib import Path

from . import settings_file
from .paths import SETTINGS

# hook ที่ daemon ใช้จริง — ตรงกับ switch ใน session_store.apply
EVENTS = [
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "PreCompact",
    "Notification",
    "Stop",
    # ต้องมีคู่กับ SubagentStop เสมอ — ติดตั้งแต่ Stop แล้วตัวนับ subagent จะค้างที่ศูนย์
    "SubagentStart",
    "SubagentStop",
    "SessionEnd",
]


def _is_ours(command: str) -> bool:
    return "--hook" in command and "tamaclaude" in command


def install(command: str, path: Path = SETTINGS) -> None:
    """เพิ่ม/อัปเดต hook ทุก event ให้ชี้ที่ `command` · ติดตั้งซ้ำอัปเดตพาธ ไม่เพิ่มซ้ำ

    `command` คือสิ่งที่ Claude Code จะรัน เช่น `"C:\\...\\tamaclaude.exe" --hook`
    """
    root = settings_file.load(path)
    hooks = root.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}

    for event in EVENTS:
        entries = hooks.get(event)
        if not isinstance(entries, list):
            entries = []
        already = any(
            isinstance(e, dict)
            and any(
                isinstance(h, dict) and _is_ours(str(h.get("command", "")))
                for h in (e.get("hooks") or [])
            )
            for e in entries
        )
        if already:
            # อัปเดตพาธให้ตรง binary ปัจจุบัน แทนเพิ่มซ้ำ
            new_entries = []
            for e in entries:
                if isinstance(e, dict) and isinstance(e.get("hooks"), list):
                    e = dict(e)
                    e["hooks"] = [
                        {**h, "command": command}
                        if isinstance(h, dict) and _is_ours(str(h.get("command", "")))
                        else h
                        for h in e["hooks"]
                    ]
                new_entries.append(e)
            entries = new_entries
        else:
            entries.append({"hooks": [{"type": "command", "command": command}]})
        hooks[event] = entries

    root["hooks"] = hooks
    settings_file.save(root, path)


def uninstall(path: Path = SETTINGS) -> None:
    """ถอน hook ของเราออกจากทุก event · ของผู้ใช้ในไฟล์เดียวกันต้องไม่ถูกแตะ"""
    root = settings_file.load(path)
    hooks = root.get("hooks")
    if not isinstance(hooks, dict):
        return
    for event in list(hooks.keys()):
        entries = hooks.get(event)
        if not isinstance(entries, list):
            continue
        kept = []
        for e in entries:
            if not isinstance(e, dict):
                kept.append(e)
                continue
            inner = [
                h
                for h in (e.get("hooks") or [])
                if not (isinstance(h, dict) and _is_ours(str(h.get("command", ""))))
            ]
            if inner:
                kept.append({**e, "hooks": inner})
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if hooks:
        root["hooks"] = hooks
    else:
        root.pop("hooks", None)
    settings_file.save(root, path)
