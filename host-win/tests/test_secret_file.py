"""secret_file — กันบั๊ก _others_can_read ที่จับพาธผิดเป็น principal กว้าง

บั๊กเดิม: icacls ขึ้นต้นด้วยพาธไฟล์ (C:\\Users\\...) ซึ่งมี "\\Users\\" อยู่แล้ว การสแกนหา
ชื่อ principal ทั้งก้อนจึงเป็น false positive กับความลับทุกใบใต้ %USERPROFILE% —
finnhub-key/session-key ถูกปฏิเสธทั้งที่ ACL แคบถูกต้อง → หน้า stocks ไม่ขึ้น
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "host-win"))

from tamaclaude import secret_file  # noqa: E402


def _fake_icacls(monkeypatch, stdout: str):
    class R:
        def __init__(self): self.stdout = stdout
    monkeypatch.setattr(secret_file.subprocess, "run", lambda *a, **k: R())
    monkeypatch.setattr(secret_file.os, "name", "nt")


def test_clean_acl_under_users_profile_is_not_broad(monkeypatch):
    # พาธมี \Users\ แต่ ACE มีแค่เจ้าของ — ต้องไม่ถือว่ากว้าง (นี่คือบั๊กเดิม)
    p = Path(r"C:\Users\005514\.tamaclaude\finnhub-key")
    _fake_icacls(monkeypatch, f"{p} NSTDA\\005514:(F)\n\nSuccessfully processed 1 files")
    assert secret_file._others_can_read(p) is False


def test_everyone_ace_is_broad(monkeypatch):
    p = Path(r"C:\Users\005514\.tamaclaude\finnhub-key")
    _fake_icacls(monkeypatch, f"{p} NSTDA\\005514:(F)\n{p} Everyone:(R)\n")
    assert secret_file._others_can_read(p) is True


def test_authenticated_users_ace_is_broad(monkeypatch):
    p = Path(r"C:\Users\005514\.tamaclaude\session-key")
    _fake_icacls(monkeypatch, f"{p} NT AUTHORITY\\Authenticated Users:(R)\n")
    assert secret_file._others_can_read(p) is True


def test_builtin_users_ace_is_broad(monkeypatch):
    # ACE ของ BUILTIN\\Users (คนละอันกับ \Users\ ในพาธ) ต้องยังจับได้
    p = Path(r"C:\Users\005514\.tamaclaude\finnhub-key")
    _fake_icacls(monkeypatch, f"{p} BUILTIN\\Users:(RX)\n")
    assert secret_file._others_can_read(p) is True


def test_round_trip_write_then_read(tmp_path, monkeypatch):
    # เขียนแล้วอ่านกลับได้ · บังคับ path นอก Windows-check ให้ผ่าน (_others_can_read False)
    monkeypatch.setattr(secret_file, "_others_can_read", lambda p: False)
    monkeypatch.setattr(secret_file, "_lock_acl", lambda p: None)
    w = secret_file.Wording(noun="test key", missing="make it", empty="fill it")
    target = tmp_path / "k"
    secret_file.write("  hunter2  ", target, w)
    assert secret_file.read(target, w) == "hunter2"
