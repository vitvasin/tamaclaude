"""ตาราง tool -> สถานะภาพ — พอร์ตจาก `host/Sources/TamaCore/ToolMap.swift`

การแปลอยู่ฝั่ง daemon ทั้งหมด firmware รู้จักแค่ enum คงที่
ผู้ใช้แก้ตารางได้ที่ `%USERPROFILE%\\.tamaclaude\\tools.json` โดยไม่ต้องแฟลชบอร์ดใหม่
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .protocol import VisualState

_DEFAULT_EXACT: dict[str, VisualState] = {
    "Read": VisualState.READING,
    "Grep": VisualState.READING,
    "Glob": VisualState.READING,
    "NotebookRead": VisualState.READING,

    "Edit": VisualState.WRITING,
    "Write": VisualState.WRITING,
    "MultiEdit": VisualState.WRITING,
    "NotebookEdit": VisualState.WRITING,
    "TodoWrite": VisualState.WRITING,

    "Bash": VisualState.BUILDING,
    "BashOutput": VisualState.BUILDING,
    "KillShell": VisualState.BUILDING,

    "WebSearch": VisualState.SEARCHING,
    "WebFetch": VisualState.SEARCHING,

    "Task": VisualState.THINKING,
    "Agent": VisualState.THINKING,
    "Workflow": VisualState.THINKING,
    "Skill": VisualState.THINKING,

    # คุยกับบริการนอกตัว ไม่ใช่อ่านไฟล์หรือค้นเว็บ — ควรแยกให้เห็นว่ารออีกฝั่งอยู่
    "LSP": VisualState.BEACON,
    "ListMcpResourcesTool": VisualState.BEACON,
    "ReadMcpResourceTool": VisualState.BEACON,
    "ReadMcpResourceDirTool": VisualState.BEACON,
}

_DEFAULT_PREFIXES: dict[str, VisualState] = {
    "mcp__": VisualState.BEACON,
}


@dataclass
class ToolMap:
    exact: dict[str, VisualState] = field(
        default_factory=lambda: dict(_DEFAULT_EXACT)
    )
    """ชื่อเป๊ะ เช่น "Read" """

    prefixes: dict[str, VisualState] = field(
        default_factory=lambda: dict(_DEFAULT_PREFIXES)
    )
    """ขึ้นต้นด้วย เช่น "mcp__" — ใช้เมื่อไม่เจอชื่อเป๊ะ ตัวที่ยาวกว่าชนะ"""

    fallback: VisualState = VisualState.THINKING
    """ไม่เข้าข้อไหนเลย"""

    def state(self, tool: str) -> VisualState:
        if tool in self.exact:
            return self.exact[tool]
        best: tuple[int, VisualState] | None = None
        for prefix, state in self.prefixes.items():
            if tool.startswith(prefix) and (best is None or len(prefix) > best[0]):
                best = (len(prefix), state)
        return best[1] if best else self.fallback

    # MARK: - ไฟล์คอนฟิก

    @classmethod
    def load(cls, path: Path) -> "ToolMap":
        """รูปแบบไฟล์:

        ```json
        { "fallback": "thinking",
          "tools": { "Read": "reading", "mcp__*": "searching" } }
        ```

        คีย์ที่ลงท้ายด้วย `*` คือกฎขึ้นต้นด้วย
        """
        obj = json.loads(path.read_text(encoding="utf-8"))
        out = cls()
        raw_fallback = obj.get("fallback")
        if raw_fallback in _VALUES:
            out.fallback = _VALUES[raw_fallback]
        for key, value in (obj.get("tools") or {}).items():
            state = _VALUES.get(value)
            if state is None:
                continue  # สถานะที่ firmware ไม่รู้จัก = บรรทัดที่ข้ามไป ไม่ใช่ไฟล์ที่เสีย
            if key.endswith("*"):
                out.prefixes[key[:-1]] = state
            else:
                out.exact[key] = state
        return out

    @classmethod
    def load_or_default(cls, path: Path) -> "ToolMap":
        """อ่านคอนฟิกถ้ามี ไม่มีก็ใช้ค่าเริ่มต้น — ไฟล์เสียไม่ควรทำให้ daemon ตาย"""
        try:
            return cls.load(path)
        except (OSError, ValueError):
            return cls()


_VALUES: dict[str, VisualState] = {s.value: s for s in VisualState}
