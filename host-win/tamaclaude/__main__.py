"""ทางเข้าเดียวของทุกโหมด — คู่ของ `Sources/tamaclaude/main.swift`

    python -m tamaclaude --hook                      ต่อท้าย Claude Code hook (คืน 0 เสมอ)
    python -m tamaclaude --daemon --print --no-ble -v  เดมอนแบบไม่ใช้บลูทูธ พิมพ์ snapshot
    python -m tamaclaude --send '<json>'             ยิงเหตุการณ์ที่เขียนมือหนึ่งอัน
    python -m tamaclaude --usage-poll                ยิงโควตาจาก claude.ai หนึ่งรอบ -> cache
    python -m tamaclaude --usage-cache < line.json   ท่อ statusline ด้วยมือ (stdin -> cache)
    python -m tamaclaude --install                   ติดตั้ง hook + statusline + autostart
    python -m tamaclaude --install-hook | --remove-hook
    python -m tamaclaude --install-statusline | --remove-statusline
    python -m tamaclaude --autostart | --no-autostart

**`--hook` ต้องมาก่อนทุกอย่าง** และห้ามลาก import อะไรเพิ่ม — Claude Code รอทางนี้อยู่ทุกครั้ง
ที่ยิงเหตุการณ์ · `argparse` เองก็หนักพอที่จะไม่คุ้ม จึงคัดด้วย `sys.argv` ดิบๆ ก่อน
"""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    # ทางด่วน: ตัดสินจาก argv ตรงๆ แล้วออกก่อนที่ import อื่นจะได้เกิด
    if "--hook" in args:
        from .hook_client import run_hook

        return run_hook()

    if "--usage-poll" in args:
        # โปรเซสอายุสั้น: อ่าน key จากไฟล์ ACL แคบ ยิง claude.ai เขียน cache แล้วออก
        # exit code แยกชนิดความล้มเหลว (ดู usage_poll.main)
        from .usage_poll import main as usage_poll_main

        return usage_poll_main(args)

    if "--usage-cache" in args:
        # ท่อ statusline: JSON ของ Claude Code เข้า stdin -> cache · สำหรับทดสอบด้วยมือ ตัว
        # statusline.ps1 เรียกทางนี้เหมือนกัน
        from .usage_writer import ingest

        summary = ingest(sys.stdin.buffer.read())
        if summary:
            print(summary)
        return 0

    if "--send" in args:
        i = args.index("--send")
        if i + 1 >= len(args):
            print("--send needs one json argument", file=sys.stderr)
            return 1
        return _send(args[i + 1])

    # ติดตั้ง/ถอน hook + statusline + autostart · ต้องรันหลัง `pip install -e host-win` เพื่อให้
    # `-m tamaclaude` resolve ได้จาก cwd ใดๆ ที่ Claude Code ยิง hook มา
    if any(
        f in args
        for f in (
            "--install-hook",
            "--remove-hook",
            "--install-statusline",
            "--remove-statusline",
            "--autostart",
            "--no-autostart",
            "--install",
        )
    ):
        return _install(args)

    if "--daemon" in args:
        from .daemon import Daemon

        return Daemon(
            use_ble="--no-ble" not in args,
            echo="--print" in args,
            verbose="-v" in args or "--verbose" in args,
            use_poll="--no-poll" not in args,
        ).run()

    print(__doc__, file=sys.stderr)
    return 1


def _install(args: list[str]) -> int:
    """ติดตั้ง/ถอนตัวเชื่อมกับ Claude Code · `--install` = hook + statusline + autostart ทีเดียว"""
    base = [sys.executable, "-m", "tamaclaude"]
    hook_command = f'"{sys.executable}" -m tamaclaude --hook'
    do_all = "--install" in args

    if do_all or "--install-hook" in args:
        from . import hook_installer

        hook_installer.install(hook_command)
        print("installed hook into ~/.claude/settings.json")
    if "--remove-hook" in args:
        from . import hook_installer

        hook_installer.uninstall()
        print("removed hook")

    if do_all or "--install-statusline" in args:
        from . import statusline_installer

        statusline_installer.install(base)
        print("installed statusline (quota pipe)")
    if "--remove-statusline" in args:
        from . import statusline_installer

        statusline_installer.uninstall()
        print("removed statusline")

    if do_all or "--autostart" in args:
        from . import autostart

        autostart.enable()
        print("enabled autostart at login")
    if "--no-autostart" in args:
        from . import autostart

        autostart.disable()
        print("disabled autostart")
    return 0


def _send(raw: str) -> int:
    """ยิงเหตุการณ์ที่เขียนมือเข้า daemon ที่กำลังรันอยู่ — ทางเดียวกับที่ hook ใช้"""
    from .hook_client import send_hook_event

    if not send_hook_event(raw.encode("utf-8")):
        print("daemon not running", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
