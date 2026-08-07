"""ข้อตกลงที่ firmware พึ่งอยู่เงียบๆ — ถ้าข้อไหนพัง บอร์ดจะไม่ error มันจะวาดผิด

ไม่มี oracle ฝั่ง Swift ให้รันเทียบบน Windows ของจริงมีสี่อย่างเท่านั้น: thai-golden.json,
layout.h, ct_pages.h และตัวบอร์ด · เทสต์พวกนี้จึงเขียนมือจากการอ่าน Swift และเป็นตัวชิ้นงาน
ไม่ใช่ของแถม
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "host-win"))

from tamaclaude import text  # noqa: E402
from tamaclaude.protocol import (  # noqa: E402
    MAX_PAYLOAD,
    UNKNOWN,
    CardKind,
    CardSnap,
    HookEvent,
    SessionSnap,
    Snapshot,
    UsageSnap,
    VisualState,
    dumps,
)


def _snap(**kw) -> Snapshot:
    return Snapshot(clock="09:41", date="Thu 7 Aug", **kw)


def _sessions(n: int) -> list[SessionSnap]:
    return [SessionSnap(f"project-{i}", VisualState.WRITING) for i in range(n)]


# MARK: - งบบนสาย


def test_max_payload_is_500():
    assert MAX_PAYLOAD == 500


def test_keys_are_sorted_always():
    """ลำดับคีย์สุ่ม = การเทียบ "เปลี่ยนไหม" จริงทุกครั้ง = บอร์ดโดนยิงทุกวินาที"""
    raw = dumps({"z": 1, "a": 2, "m": 3}).decode()
    assert raw == '{"a":2,"m":3,"z":1}'
    assert " " not in raw


def test_thai_crosses_the_wire_as_utf8_not_escapes():
    assert dumps({"t": "ประชุม"}).decode() == '{"t":"ประชุม"}'


def test_small_snapshot_is_untouched():
    snap = _snap(sessions=_sessions(1))
    assert json.loads(snap.encoded()) == snap.encode()


# MARK: - ลำดับการบีบ


def test_squeeze_clips_body_before_title():
    body = "b" * 200
    title = "t" * 40
    snap = _snap(
        sessions=_sessions(2),
        cards=[CardSnap(title, body, CardKind.INFO) for _ in range(2)],
    )
    out = json.loads(snap.encoded())
    assert len(snap.encoded()) <= MAX_PAYLOAD
    # title ยังเต็มอยู่ แปลว่า body ถูกตัดจนพอก่อนที่จะแตะ title
    assert out["n"][0]["t"] == title
    assert len(out["n"][0]["b"]) < len(body)


def test_squeeze_drops_cards_from_the_bottom_and_counts_them():
    """ใบที่ถูกตัดเพราะสายส่งไม่พอก็ยังต้องนับเข้า `m` เหมือนใบที่จอวาดไม่พอ"""
    # session ยาวๆ หลายตัวกินงบจนไม่เหลือที่ให้การ์ด — นั่นคือฉากเดียวที่บังคับให้ตัดใบทิ้ง
    sessions = [
        SessionSnap(f"a-rather-long-project-{i:02d}", VisualState.WRITING)
        for i in range(8)
    ]
    snap = _snap(
        sessions=sessions,
        cards=[CardSnap("t" * 30, "b" * 120, CardKind.ALERT) for _ in range(3)],
        card_overflow=1,
    )
    out = json.loads(snap.encoded())
    assert len(snap.encoded()) <= MAX_PAYLOAD
    assert out["m"] > 1, "sessions นี้กินงบจนต้องมีใบถูกตัด — ถ้าไม่ตัด เทสต์นี้ไม่ได้ทดสอบอะไร"
    assert out["m"] == 1 + (3 - len(out["n"]))


def test_squeeze_never_touches_sessions():
    """session คือเหตุผลที่จอนี้มีอยู่ — ไม่ว่าบีบแค่ไหนก็ห้ามหาย"""
    sessions = _sessions(4)
    snap = _snap(
        sessions=sessions,
        cards=[CardSnap("t" * 40, "b" * 300, CardKind.ALERT) for _ in range(4)],
        usage=[UsageSnap(50, 3600), UsageSnap(20, 86400)],
    )
    out = json.loads(snap.encoded())
    assert out["s"] == [s.encode() for s in sessions]


def test_sessions_alone_may_overflow_the_budget_and_that_is_by_design():
    """ไม่มีอะไรเหลือให้บีบแล้ว snapshot ก็ยังใหญ่เกิน — `encoded` คืนของที่ใหญ่เกินตามจริง

    ไม่ใช่ error และไม่ใช่การหั่นเป็นชิ้น: ฝั่ง transport เป็นคนตัดสินใจทิ้งแล้วบันทึกไว้
    (BLETransport.swift) เพราะบนสายนี้ไม่มี chunking · ถ้าที่นี่แอบตัด session ทิ้งเพื่อให้
    ตัวเลขสวย จอจะเงียบหาย session ไปโดยไม่มีใครรู้
    """
    snap = _snap(
        sessions=[
            SessionSnap(f"a-rather-long-project-{i:02d}", VisualState.WRITING)
            for i in range(12)
        ],
        cards=[CardSnap("t" * 30, "b" * 120, CardKind.ALERT) for _ in range(3)],
        usage=[UsageSnap(50, 3600), UsageSnap(20, 86400)],
    )
    data = snap.encoded()
    out = json.loads(data)
    assert len(data) > MAX_PAYLOAD
    assert len(out["s"]) == 12          # ครบทุกตัว ไม่มีใครหาย
    assert out["n"] == [] and out["m"] == 3  # การ์ดหมดแล้ว และนับไว้ครบ
    assert "u" not in out               # โควตาถูกทิ้งเป็นอย่างสุดท้าย


def test_usage_is_dropped_last_not_first():
    """โควตายังดูได้จาก statusline บนจอคอม การหายไปของมันไม่ทำให้อุปกรณ์ไร้ประโยชน์"""
    snap = _snap(
        sessions=_sessions(6),
        cards=[CardSnap("t" * 40, "b" * 300, CardKind.ALERT) for _ in range(4)],
        usage=[UsageSnap(50, 3600), UsageSnap(20, 86400)],
    )
    out = json.loads(snap.encoded())
    # การ์ดต้องหมดก่อน โควตาถึงจะได้หลุด
    if "u" not in out:
        assert out["n"] == []


def test_usage_encodes_as_a_two_slot_array_not_an_object():
    """คีย์กินไบต์ในงบ 500 ที่แชร์กับ session และ card"""
    snap = _snap(usage=[UsageSnap(42, 900), UsageSnap()])
    out = json.loads(snap.encoded())
    assert out["u"] == [[42, 900], [UNKNOWN, UNKNOWN]]


def test_usage_absent_means_no_key_at_all():
    """`None` แปลว่าไม่เคยได้ข้อมูล ซึ่งบอร์ดตีความว่าถอยไปเป็นนาฬิกา ไม่ใช่วาดโครงเปล่า"""
    assert "u" not in json.loads(_snap().encoded())


def test_unknown_is_minus_one_and_zero_is_real():
    assert UNKNOWN == -1
    assert UsageSnap(0, 0).is_known
    assert not UsageSnap().is_known


def test_a_snapshot_has_no_g_key():
    """เฟรมคือ Snapshot ก็ต่อเมื่อ *ไม่มี* คีย์ `g` — นั่นคือตัวแยกมันจากเฟรมของหน้าอื่น"""
    assert "g" not in json.loads(_snap(sessions=_sessions(2)).encoded())


# MARK: - สถานะภาพ


def test_visual_state_count_and_order_match_the_firmware_contract():
    """เพิ่มหรือสลับลำดับ = ต้องแก้ ct_model.c/ct_mascot.c ด้วย"""
    assert [s.value for s in VisualState] == [
        "idle", "reading", "writing", "building", "searching", "thinking",
        "waiting", "sleeping", "alert", "celebrate", "error", "entering",
        "leaving", "conducting", "beacon",
    ]


def test_only_three_states_need_a_human():
    assert {s.value for s in VisualState if s.needs_human} == {"waiting", "alert", "error"}


def test_conducting_outranks_tool_states():
    """session ที่คุม subagent เงียบสนิท ถ้าให้เท่ากับ tool มันจะแพ้ตัวที่ไถ Read ไปเรื่อยๆ"""
    assert VisualState.CONDUCTING.priority > VisualState.READING.priority
    assert VisualState.ALERT.priority == VisualState.ERROR.priority == 40
    assert VisualState.SLEEPING.priority == 0


# MARK: - ข้อความ


def test_clip_counts_cells_not_code_points():
    """สระบนและวรรณยุกต์กว้างศูนย์ — การนับดิบๆ จะตัดไทยสั้นเกินจริงราว 40%"""
    thai_word = "ที่ปั๊มน้ำมัน"
    assert text.display_width(thai_word) < len(thai_word)
    assert text.display_width(text.clip(thai_word, 5)) == 5


def test_clip_adds_ellipsis_only_when_it_actually_cut():
    assert text.clip("abcdef", 10) == "abcdef"
    assert text.clip("abcdefghij", 6) == "abc..."
    assert text.clip("abcdef", 3) == "abc"  # <=3 ช่อง ไม่มีที่ให้ "..."
    assert text.clip("abcdef", 0) == ""


def test_sanitize_drops_what_the_board_font_cannot_draw():
    """em dash เจอจริงตอนทำ preview — ตัวที่ฟอนต์ไม่มีจะกลายเป็นกล่องสี่เหลี่ยม"""
    assert text.sanitize("a — b") == "a - b"
    assert text.sanitize("say “hi”") == 'say "hi"'
    assert text.sanitize("a\u2026") == "a..."
    assert text.sanitize("emoji 🎉 gone") == "emoji gone"
    assert text.sanitize("  spaced   out  ") == "spaced out"


def test_head_does_not_add_ellipsis():
    """ชื่อโปรเจกต์บนบรรทัดการ์ดเป็นตัวชี้ ไม่ใช่เนื้อหา — "..." กิน 3 ช่องไปบอกสิ่งที่รู้อยู่แล้ว"""
    assert text.head("an-extremely-long-name", 12) == "an-extremely"


def test_thai_golden_vectors_agree_with_the_shared_file():
    """ไฟล์เดียวกับที่ tamatest (Swift) และ tools/test_thai.py อ่าน — oracle เดียวที่มีบน Windows

    เทียบผ่าน `text.fit` ไม่ใช่ `thai.shape` ตรงๆ เพราะประตูจริงที่ข้อความใช้ออกสู่สายคือ
    ประตูนี้ ถ้า `sanitize` เผลอกิน PUA ที่ shaper เพิ่งประกอบขึ้นมา เทสต์ที่เรียก shape
    ตรงๆ จะเขียวทั้งที่บอร์ดได้ข้อความเปล่า
    """
    cases = json.loads(
        (REPO / "tools" / "thai-golden.json").read_text(encoding="utf-8")
    )["cases"]
    assert cases, "golden file is empty"
    for c in cases:
        want = "".join(chr(int(h, 16)) for h in c["out"])
        assert text.fit(c["in"], 999) == want, c["name"]
        assert text.display_width(c["in"]) == c["width"], c["name"]
        # ประกอบร่างซ้ำต้องได้ผลเดิม ไม่งั้นข้อความที่ผ่าน clip สองรอบจะเพี้ยน
        assert text.clip(want, 999) == want, c["name"]


# MARK: - hook


def test_project_is_the_last_folder_of_cwd_on_both_path_flavours():
    assert HookEvent("Stop", "s1", cwd=r"D:\work\tamaclaude").project == "tamaclaude"
    assert HookEvent("Stop", "s1", cwd="/home/u/proj").project == "proj"
    assert HookEvent("Stop", "s1", cwd=r"D:\work\tamaclaude\\").project == "tamaclaude"


def test_project_falls_back_to_claude():
    assert HookEvent("Stop", "s1").project == "claude"
    assert HookEvent("Stop", "s1", cwd="").project == "claude"


def test_hook_event_ignores_fields_the_daemon_does_not_use():
    ev = HookEvent.decode(
        {"hook_event_name": "PreToolUse", "session_id": "abc",
         "tool_name": "Read", "transcript_path": "/tmp/x", "permission_mode": "auto"}
    )
    assert ev.tool_name == "Read"
    assert "transcript_path" not in ev.encode()
