"""ยึดช่อง `statusLine.command` ใน `~/.claude/settings.json` เพื่อดักข้อมูลโควตา — พอร์ต
`StatuslineInstaller.swift` เป็น PowerShell

Claude Code ป้อน JSON เข้า stdin ของ statusline ทุกครั้งที่ render และใน JSON นั้นมี `rate_limits`
(used_percentage + resets_at เป็น epoch) นี่คือแหล่งโควตาที่ไม่ต้องใช้ credential ไม่ต้องยิงเน็ต

สคริปต์ที่ติดตั้ง **ไม่เปลี่ยนสิ่งที่ผู้ใช้เห็น**: เขียน cache แล้วส่ง JSON ก้อนเดิมต่อให้คำสั่ง
statusline เดิม แล้วพิมพ์ผลของคำสั่งนั้นออกไป · ผู้ใช้ที่ไม่เคยมี statusline คือข้อยกเว้นเดียว —
ไม่มีอะไรให้ส่งต่อ จึงพิมพ์บรรทัดสรุปสั้นๆ ที่ `--usage-cache` คืนมาแทน
"""

from __future__ import annotations

from pathlib import Path

from . import settings_file
from .paths import SETTINGS, STATUSLINE

# คำสั่งที่ Claude Code จะเรียก — รัน ps1 โดยไม่โหลดโปรไฟล์ ไม่ติด ExecutionPolicy ของเครื่อง
_COMMAND = 'powershell -NoProfile -ExecutionPolicy Bypass -File "{path}"'


def command_line(path: Path = STATUSLINE) -> str:
    return _COMMAND.format(path=path)


def _psq(s: str) -> str:
    """ครอบด้วย single quote แบบ PowerShell — เดี่ยวข้างในกลายเป็นคู่"""
    return "'" + s.replace("'", "''") + "'"


def script(invocation: list[str], previous: str | None) -> str:
    """สร้างเนื้อ ps1 · `invocation` = คำสั่งฐานเรียก tamaclaude เช่น [python.exe, -m, tamaclaude]

    กติกาข้อเดียวที่ห้ามพลาด: statusline ของผู้ใช้ต้องไม่หายไม่ว่า binary จะหาย พัง หรือช้า —
    ทุกทางจึงมี try/catch และ exit 0 เสมอ
    """
    exe = _psq(invocation[0])
    args = ", ".join(_psq(a) for a in invocation[1:])
    prev = _psq(previous or "")
    return f"""# สร้างโดย tamaclaude statusline install — แก้ที่ statusline_installer.py
# ดัก JSON ของ Claude Code เก็บ rate_limits ลง cache แล้วส่งงานวาดต่อให้คำสั่งเดิมของผู้ใช้
$PREV = {prev}
$exe  = {exe}
$tamaArgs = @({args})

$stdin = [Console]::In.ReadToEnd()

$fallback = ''
try {{ $fallback = ($stdin | & $exe @tamaArgs --usage-cache 2>$null | Out-String).Trim() }} catch {{ $fallback = '' }}

if ($PREV -ne '') {{
    try {{ $stdin | & cmd /c $PREV }} catch {{ }}
}} elseif ($fallback -ne '') {{
    $fallback
}}
exit 0
"""


def previous_command(path: Path = SETTINGS, script_path: Path = STATUSLINE) -> str | None:
    """คำสั่ง statusline เดิมของผู้ใช้ที่จะส่งงานต่อ — `None` ถ้าไม่เคยมี หรือถ้าเป็นของเราเอง

    "ของเราเอง" ดูจากพาธสคริปต์จริงที่กำลังติดตั้ง ไม่ใช่ค่า global — ไม่งั้นติดตั้งซ้ำด้วยพาธ
    อื่น (เช่นในเทสต์) จะไม่รู้จักคำสั่งของตัวเองแล้วเก็บมันเป็น "คำสั่งเดิม" วนไม่จบ
    """
    try:
        root = settings_file.load(path)
    except settings_file.SettingsError:
        return None
    line = root.get("statusLine")
    if not isinstance(line, dict):
        return None
    command = line.get("command")
    if not isinstance(command, str) or str(script_path) in command:
        return None  # ของเราเอง อย่าเรียกวน
    return command


def is_installed(path: Path = SETTINGS, script_path: Path = STATUSLINE) -> bool:
    try:
        root = settings_file.load(path)
    except settings_file.SettingsError:
        return False
    line = root.get("statusLine")
    return isinstance(line, dict) and str(script_path) in str(line.get("command", ""))


def delegated_command_in_script(script_path: Path = STATUSLINE) -> str | None:
    """ดึงคำสั่งเดิมกลับจากสคริปต์ที่ติดตั้งไว้ — สคริปต์เป็นที่เก็บ ไม่ต้องมีไฟล์สำรองแยก"""
    try:
        text = script_path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("$PREV = "):
            raw = line[len("$PREV = ") :].strip()
            if len(raw) >= 2 and raw.startswith("'") and raw.endswith("'"):
                inner = raw[1:-1].replace("''", "'")
                return inner or None
    return None


def install(
    invocation: list[str], settings: Path = SETTINGS, script_path: Path = STATUSLINE
) -> None:
    # อ่านคำสั่งเดิม *ก่อน* เขียนทับ ไม่งั้นได้สคริปต์ที่เรียกตัวเองไม่รู้จบ · ติดตั้งซ้ำจะเจอ
    # statusLine ที่ชี้มาที่เราแล้ว (previous_command คืน None ถูกต้อง) — คำสั่งเดิมของผู้ใช้
    # เก็บอยู่ในสคริปต์เก่า ต้องกู้จากตรงนั้น ไม่งั้นติดตั้งซ้ำ = ลบ statusline ของเขาทิ้ง
    previous = previous_command(settings, script_path) or delegated_command_in_script(script_path)

    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script(invocation, previous), encoding="utf-8")

    root = settings_file.load(settings)
    # refreshInterval 10: statusline event-driven ล้วน และ trigger เงียบตอน session ว่าง ซึ่ง
    # เป็นสภาพที่จอนี้เจอเกือบตลอด · 10 วิเร็วพอให้ตัวเลขตามทัน ช้าพอไม่ให้กินซีพียู
    root["statusLine"] = {
        "type": "command",
        "command": command_line(script_path),
        "refreshInterval": 10,
    }
    settings_file.save(root, settings)


def uninstall(settings: Path = SETTINGS, script_path: Path = STATUSLINE) -> None:
    """คืนช่องให้คำสั่งเดิมที่สคริปต์เก็บไว้ — ถอนโดยไม่ต้องจำอะไรเอง"""
    root = settings_file.load(settings)
    restored = delegated_command_in_script(script_path)
    if restored:
        root["statusLine"] = {"type": "command", "command": restored}
    else:
        root.pop("statusLine", None)
    settings_file.save(root, settings)
    try:
        script_path.unlink()
    except OSError:
        pass
