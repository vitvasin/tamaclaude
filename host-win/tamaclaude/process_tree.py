"""อ่านสายพันธุ์ของ process จาก kernel — พอร์ตของ `ProcessTree.swift` ฝั่ง Windows

ใช้ตอบคำถามเดียว: เจ้าของ session ยังอยู่ไหม

**ctypes ล้วน ไม่ใช้ psutil โดยตั้งใจ** — ไฟล์นี้ถูก import บนทาง `--hook` ซึ่งวิ่งทุกครั้งที่
Claude Code ยิงเหตุการณ์และ Claude Code *รอ* มันอยู่ · psutil ลากไลบรารีที่คอมไพล์แล้วเข้ามา
ซึ่งจ่ายเป็นมิลลิวินาทีทุกครั้ง ส่วน ctypes มากับ interpreter อยู่แล้ว
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
MAX_DEPTH = 10


class _PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


@dataclass(frozen=True)
class ProcessHandle:
    """อ้างถึง process หนึ่งตัวแบบที่ PID ถูกใช้ซ้ำแล้วไม่หลอกเรา

    PID อย่างเดียวไม่พอ: Windows วนเลข PID กลับมาใช้ใหม่ได้ภายในไม่กี่นาทีบนเครื่องที่
    spawn process ถี่ๆ (ซึ่ง Claude Code ทำอยู่ตลอดผ่าน Bash tool) ถ้าเทียบแค่เลข session
    ที่ตายไปแล้วจะ "ฟื้น" ขึ้นมาบนจอเพราะมีใครมาได้เลขเดิม — จึงผูกเวลาเกิดไว้ด้วย
    """

    pid: int
    started_at: int
    """FILETIME ตอนสร้าง process หน่วย 100 นาโนวินาที — ตัวระบุตัวตน ไม่ได้เอาไปคิดอายุ

    **int ไม่ใช่ float** · ค่าจริงอยู่ราว 1.34e17 ซึ่งกินเลขนัยสำคัญ 18 หลัก เกินที่ float64
    เก็บได้ (~15-16) · เก็บเป็น float แล้ว `started_at + 1` จะเทียบว่า *เท่ากัน* คือด่านกัน
    PID ซ้ำพังเงียบๆ ทั้งที่โค้ดอ่านดูถูกทุกบรรทัด (เจอตอนสโมกเทสต์ ไม่ใช่ตอนอ่าน)
    ฝั่ง Swift ใช้ Double ได้เพราะ macOS ให้มาเป็น *วินาที* epoch ซึ่งเล็กกว่ากันสิบล้านเท่า
    """

    def encode(self) -> dict:
        return {"pid": self.pid, "startedAt": self.started_at}

    @classmethod
    def decode(cls, obj: dict | None) -> "ProcessHandle | None":
        if not obj:
            return None
        try:
            return cls(int(obj["pid"]), int(obj["startedAt"]))
        except (KeyError, TypeError, ValueError):
            return None


def created_at(pid: int) -> int | None:
    """เวลาเกิดของ process — `None` = ไม่มี process นี้แล้ว (หรือแตะไม่ได้)"""
    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        exit_t = wintypes.FILETIME()
        kernel_t = wintypes.FILETIME()
        user_t = wintypes.FILETIME()
        ok = _kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_t),
            ctypes.byref(kernel_t),
            ctypes.byref(user_t),
        )
        if not ok:
            return None
        return (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    finally:
        _kernel32.CloseHandle(handle)


def _snapshot() -> dict[int, tuple[int, str]]:
    """pid -> (ppid, ชื่อไฟล์ exe) ของทั้งเครื่อง หนึ่ง snapshot ต่อการไต่หนึ่งครั้ง

    Windows ไม่มีทางถาม "พ่อของ pid นี้คือใคร" ทีละตัว ต้องกวาดทั้งตารางอยู่ดี
    """
    snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID_HANDLE_VALUE:
        return {}
    try:
        entry = _PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32)
        out: dict[int, tuple[int, str]] = {}
        if not _kernel32.Process32First(snap, ctypes.byref(entry)):
            return out
        while True:
            out[entry.th32ProcessID] = (
                entry.th32ParentProcessID,
                entry.szExeFile.decode("mbcs", "replace"),
            )
            if not _kernel32.Process32Next(snap, ctypes.byref(entry)):
                return out
    finally:
        _kernel32.CloseHandle(snap)


def is_alive(h: ProcessHandle) -> bool:
    """process ตัวเดิมตัวนั้นยังอยู่ไหม — เลข PID ตรงแต่เวลาเกิดไม่ตรง = คนละตัว"""
    return created_at(h.pid) == h.started_at


def _is_claude(name: str) -> bool:
    """process นี้คือ Claude Code ไหม

    ตัวติดตั้งหลักวาง executable ไว้ใต้โฟลเดอร์ที่ชื่อเป็น *เลขเวอร์ชัน* ชื่อไบนารีจึงไม่นิ่ง
    บน Windows ทางที่พบบ่อยที่สุดคือติดตั้งผ่าน npm ซึ่งตัว process จริงคือ node/bun ที่รัน
    สคริปต์ ชื่อสคริปต์ไม่โผล่มาในชื่อ exe เลย
    """
    lower = name.lower()
    stem = lower[:-4] if lower.endswith(".exe") else lower
    return stem == "claude" or stem.startswith("claude-") or stem in ("node", "bun")


def claude_ancestor(start: int | None = None, max_depth: int = MAX_DEPTH) -> ProcessHandle | None:
    """ไต่สายบรรพบุรุษขึ้นไปหา process ของ Claude Code ที่เรียก hook นี้

    คืน `None` เมื่อหาไม่เจอ **โดยตั้งใจ** และเป็นกฎที่ห้ามผ่อน: hook ถูกเรียกผ่านเชลล์ และ
    ผู้ใช้ครอบ wrapper อะไรไว้ก็ได้ ถ้าเดาผิดแล้วไปคว้า wrapper ที่ตายทันทีหลัง hook จบมาเป็น
    เจ้าของ session ที่ยังทำงานอยู่ จะหายจากจอทุกครั้ง — `None` แปลว่า "ไม่รู้" แล้วตกกลับไป
    ใช้เกณฑ์เงียบครบ `evict` แบบเดิม ซึ่งช้าแต่ไม่เคยผิด
    """
    table = _snapshot()
    pid = start if start is not None else table.get(os.getpid(), (0, ""))[0]
    for _ in range(max_depth):
        if pid <= 0:
            return None
        entry = table.get(pid)
        if entry is None:
            return None
        ppid, name = entry
        if _is_claude(name):
            born = created_at(pid)
            return ProcessHandle(pid, born) if born is not None else None
        pid = ppid
    return None
