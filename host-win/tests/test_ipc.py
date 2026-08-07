"""ท่อ hook -> daemon · พอร์ต loopback ให้ทั้งเครื่องต่อได้ โทเคนจึงเป็นด่านเดียวที่มี"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "host-win"))

from tamaclaude import hook_client, ipc  # noqa: E402

EVENT = {"hook_event_name": "PreToolUse", "session_id": "s1",
         "cwd": "D:\\w\\proj", "tool_name": "Read"}


def _frame(token: str, pid: int = 0, born: int = 0, obj=EVENT) -> bytes:
    return f"{token} {pid} {born}\n".encode() + json.dumps(obj).encode()


# MARK: - การแกะเฟรม


def test_a_good_frame_decodes():
    ev = ipc.parse_frame(_frame("tok"), "tok")
    assert ev is not None
    assert ev.tool_name == "Read"
    assert ev.project == "proj"


def test_a_wrong_token_is_refused():
    assert ipc.parse_frame(_frame("nope"), "tok") is None


def test_a_malformed_header_is_refused_before_the_body_is_touched():
    assert ipc.parse_frame(b"tok\n" + json.dumps(EVENT).encode(), "tok") is None
    assert ipc.parse_frame(b"", "tok") is None


def test_a_body_that_is_not_an_event_object_is_refused():
    assert ipc.parse_frame(_frame("tok", obj=[1, 2, 3]), "tok") is None
    assert ipc.parse_frame(b"tok 0 0\nnot json", "tok") is None


def test_owner_rides_the_header_not_the_body():
    """hook เป็นที่เดียวที่ไต่สายบรรพบุรุษได้ แต่มันแกะ JSON ไม่ได้ — เจ้าของจึงมาทางหัวบรรทัด"""
    ev = ipc.parse_frame(_frame("tok", pid=4242, born=134305553694157418), "tok")
    assert ev.owner == {"pid": 4242, "startedAt": 134305553694157418}


def test_owner_pid_zero_means_unknown_not_ownerless():
    """เดาผิดแล้วคว้า wrapper ที่ตายทันทีมาเป็นเจ้าของ = session ที่ยังทำงานหายจากจอทุกครั้ง"""
    assert ipc.parse_frame(_frame("tok", pid=0), "tok").owner is None


def test_started_at_survives_as_an_exact_integer():
    """FILETIME กินเลขนัยสำคัญ 18 หลัก ถ้าไหนสักที่แปลงเป็น float ด่านกัน PID ซ้ำจะพังเงียบๆ"""
    born = 134305553694157418
    ev = ipc.parse_frame(_frame("tok", pid=7, born=born), "tok")
    assert ev.owner["startedAt"] == born


# MARK: - ท่อจริง


def test_hook_to_daemon_round_trip(tmp_path):
    got: list = []
    server = ipc.HookServer(got.append)
    endpoint = tmp_path / "hook-endpoint"
    server.start(endpoint)
    try:
        assert hook_client.send_hook_event(json.dumps(EVENT).encode(), endpoint)
        deadline = time.time() + 3
        while not got and time.time() < deadline:
            time.sleep(0.01)
        assert got, "daemon ไม่ได้รับเหตุการณ์"
        assert got[0].session_id == "s1"
    finally:
        server.stop(endpoint)


def test_a_stranger_without_the_token_gets_nothing_through(tmp_path):
    """พอร์ต loopback ต่อได้ทั้งเครื่อง — การต่อติดต้องไม่เท่ากับการถูกเชื่อ"""
    got: list = []
    server = ipc.HookServer(got.append)
    endpoint = tmp_path / "hook-endpoint"
    port = server.start(endpoint)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as s:
            s.sendall(_frame("guessed-wrong"))
        time.sleep(0.3)
        assert got == []
    finally:
        server.stop(endpoint)


def test_the_hook_returns_zero_when_the_daemon_is_not_running(tmp_path):
    """กติกาข้อเดียวที่ห้ามพลาด — hook ที่พังไปทำให้ session ของผู้ใช้พังตาม"""
    import io

    missing = tmp_path / "no-such-endpoint"
    assert hook_client.run_hook(io.BytesIO(json.dumps(EVENT).encode()), missing) == 0


def test_the_hook_returns_zero_on_garbage_input(tmp_path):
    import io

    endpoint = tmp_path / "hook-endpoint"
    endpoint.write_text("not-a-port token\n", encoding="utf-8")
    assert hook_client.run_hook(io.BytesIO(b"\x00\xff not json"), endpoint) == 0
    assert hook_client.run_hook(io.BytesIO(b""), endpoint) == 0


def test_the_endpoint_file_is_removed_on_stop(tmp_path):
    endpoint = tmp_path / "hook-endpoint"
    server = ipc.HookServer(lambda _e: None)
    server.start(endpoint)
    assert endpoint.exists()
    server.stop(endpoint)
    assert not endpoint.exists()


def test_the_hook_path_never_imports_json_or_bleak():
    """เป็นกฎที่วัดได้ ไม่ใช่ความตั้งใจ — `import json` วัดได้ ~90ms และ Claude Code รออยู่"""
    src = (REPO / "host-win" / "tamaclaude" / "hook_client.py").read_text(encoding="utf-8")
    body = "\n".join(
        line for line in src.splitlines()
        if line.startswith(("import ", "from ")) or "    import" in line
    )
    for banned in ("json", "bleak", "PySide6", "httpx", "psutil"):
        assert banned not in body, f"hook path imports {banned}"


def test_concurrent_hooks_all_arrive(tmp_path):
    got: list = []
    lock = threading.Lock()
    server = ipc.HookServer(lambda e: (lock.acquire(), got.append(e), lock.release()))
    endpoint = tmp_path / "hook-endpoint"
    server.start(endpoint)
    try:
        threads = [
            threading.Thread(
                target=hook_client.send_hook_event,
                args=(json.dumps({**EVENT, "session_id": f"s{i}"}).encode(), endpoint),
            )
            for i in range(12)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        deadline = time.time() + 3
        while len(got) < 12 and time.time() < deadline:
            time.sleep(0.01)
        assert len(got) == 12
    finally:
        server.stop(endpoint)
