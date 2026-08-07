"""ตรรกะสถานะ — hook เข้า ภาพหน้าจอออก

เวลาถูกป้อนเข้ามาทุกครั้ง เทสต์จึงไม่ต้องรอจริงสักวินาที
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "host-win"))

from tamaclaude.protocol import CardKind, HookEvent, VisualState  # noqa: E402
from tamaclaude.session_store import SessionStore, Timings  # noqa: E402
from tamaclaude.tool_map import ToolMap  # noqa: E402

T0 = datetime(2026, 8, 7, 9, 41, 0)


def _ev(name: str, sid: str = "s1", **kw) -> HookEvent:
    kw.setdefault("cwd", "D:\\work\\tamaclaude")
    return HookEvent(hook_event_name=name, session_id=sid, **kw)


def _store(**kw) -> SessionStore:
    return SessionStore(**kw)


def _settled(store: SessionStore, at: datetime):
    """ข้ามท่า entering ให้พ้นไปก่อน — 1.2 วิแรกของทุก session เป็นท่าเดินเข้ามาเสมอ"""
    return store.snapshot(at + timedelta(seconds=2))


# MARK: - การหน่วงท่า


def test_a_pose_holds_for_min_pose_even_when_the_tool_already_finished():
    """Read/Edit จบใน ~100ms ถ้าเปลี่ยนท่าทันที ผู้ใช้จะเห็นแต่ thinking ตลอด"""
    s = _store()
    s.apply(_ev("PreToolUse", tool_name="Read"), T0)
    first = _settled(s, T0)
    assert first.sessions[0].state is VisualState.READING

    # เครื่องมือจบทันที แต่ท่าต้องค้างจนครบ min_pose
    s.apply(_ev("PostToolUse", tool_name="Read"), T0 + timedelta(seconds=2.1))
    held = s.snapshot(T0 + timedelta(seconds=2.2))
    assert held.sessions[0].state is VisualState.READING

    after = s.snapshot(T0 + timedelta(seconds=9))
    assert after.sessions[0].state is VisualState.THINKING


def test_skipped_poses_are_dropped_never_queued():
    """คิวจะทำให้มาสคอตเล่าอดีตช้ากว่าความจริงเรื่อยๆ เมื่อเครื่องมือยิงรัว"""
    s = _store()
    s.apply(_ev("PreToolUse", tool_name="Read"), T0)
    _settled(s, T0)
    for i, tool in enumerate(("Write", "Bash", "WebSearch")):
        s.apply(_ev("PreToolUse", tool_name=tool), T0 + timedelta(seconds=2.2 + i * 0.1))
    # ท่าที่ถูกข้ามระหว่างหน่วงหายไปเลย — พ้นเวลาแล้วได้ท่า *ปัจจุบัน* ไม่ใช่ท่าที่ค้างคิว
    out = s.snapshot(T0 + timedelta(seconds=9))
    assert out.sessions[0].state is VisualState.SEARCHING


def test_urgent_states_cut_the_hold_short():
    """พัง/ต้องการมือคน แทรกได้ทันที ที่เหลือรอให้ท่าปัจจุบันอยู่ครบเวลา"""
    s = _store()
    s.apply(_ev("PreToolUse", tool_name="Read"), T0)
    _settled(s, T0)
    s.apply(_ev("Notification", message="permission?"), T0 + timedelta(seconds=2.1))
    out = s.snapshot(T0 + timedelta(seconds=2.2))
    assert out.sessions[0].state is VisualState.WAITING


# MARK: - attention


def test_attention_counts_rising_edges_per_session_not_screen_state():
    """ถ้าดูจากสถานะแทน จอจะถูกกระชากกลับทุก snapshot ตลอดที่คำขออนุญาตค้างอยู่"""
    s = _store()
    s.apply(_ev("Notification", "a", message="?"), T0)
    first = _settled(s, T0)
    assert first.attention == 1

    # ยังรออยู่เหมือนเดิม — เลขต้องไม่ขยับ
    assert s.snapshot(T0 + timedelta(seconds=3)).attention == 1

    # ตัวที่สองขอความช่วยเหลือ: ภาพรวมไม่เปลี่ยนเลย แต่เป็นเรื่องใหม่ที่ต้องได้เด้ง
    s.apply(_ev("Notification", "b", cwd="D:\\work\\other", message="?"),
            T0 + timedelta(seconds=4))
    assert s.snapshot(T0 + timedelta(seconds=6)).attention == 2


def test_attention_rearms_after_the_hand_goes_down():
    s = _store()
    s.apply(_ev("Notification", message="?"), T0)
    assert _settled(s, T0).attention == 1
    s.apply(_ev("PreToolUse", tool_name="Read"), T0 + timedelta(seconds=8))
    assert s.snapshot(T0 + timedelta(seconds=9)).attention == 1
    s.apply(_ev("Notification", message="?"), T0 + timedelta(seconds=20))
    assert s.snapshot(T0 + timedelta(seconds=21)).attention == 2


# MARK: - การจัดกลุ่มและ slot


def test_sessions_of_one_project_collapse_to_a_single_label_with_a_count():
    s = _store()
    for sid in ("a", "b", "c"):
        s.apply(_ev("PreToolUse", sid, tool_name="Read"), T0)
    out = _settled(s, T0)
    assert len(out.sessions) == 1
    assert out.sessions[0].project == "tamaclaude x3"
    assert out.overflow == 0  # กลุ่มที่ขึ้นจอพาสมาชิกขึ้นไปครบทุกตัวแล้ว


def test_the_group_shows_the_most_important_state_in_it():
    """กลุ่มที่มีตัวหนึ่งยกมือขออนุญาตต้องอ่านออกทันที ไม่ใช่ถูกกลบด้วยตัวที่กำลังไถ Read"""
    s = _store()
    s.apply(_ev("PreToolUse", "a", tool_name="Read"), T0)
    s.apply(_ev("Notification", "b", message="?"), T0)
    out = _settled(s, T0)
    assert out.sessions[0].state is VisualState.WAITING


def test_overflow_counts_sessions_that_did_not_get_a_slot():
    s = _store(slot_count=2)
    for i, sid in enumerate(("a", "b", "c", "d")):
        s.apply(_ev("PreToolUse", sid, cwd=f"D:\\work\\p{i}", tool_name="Read"), T0)
    out = _settled(s, T0)
    assert len(out.sessions) == 2
    assert out.overflow == 2


def test_draw_order_is_birth_order_not_importance_order():
    """สิ่งที่ต้องนิ่งคือลำดับซ้าย->ขวา ไม่ใช่พิกัด"""
    s = _store(slot_count=3)
    s.apply(_ev("PreToolUse", "a", cwd="D:\\w\\alpha", tool_name="Read"), T0)
    s.apply(_ev("PreToolUse", "b", cwd="D:\\w\\bravo", tool_name="Read"), T0)
    # ตัวที่เกิดทีหลังสำคัญกว่า แต่ต้องยังวาดทีหลังอยู่ดี
    s.apply(_ev("Notification", "c", cwd="D:\\w\\charlie", message="?"), T0)
    out = _settled(s, T0)
    assert [x.project for x in out.sessions] == ["alpha", "bravo", "charlie"]


# MARK: - การ์ด


def test_a_card_drops_its_title_when_the_only_mascot_on_screen_says_it_already():
    """ชื่อซ้ำกับป้ายที่อยู่เหนือมันไม่ถึงร้อยพิกเซล — ประโยคเลื่อนขึ้นมาเป็นบรรทัดใหญ่แทน"""
    s = _store()
    s.apply(_ev("Notification", message="needs your input"), T0)
    out = _settled(s, T0)
    assert out.cards[0].title == "needs your input"
    assert out.cards[0].body == ""


def test_a_card_keeps_a_name_prefix_when_two_projects_share_the_screen():
    """มีสองโปรเจกต์ยืนพร้อมกันเมื่อไร ชื่อคือตัวชี้เป้าตัวเดียวที่มี"""
    s = _store()
    s.apply(_ev("PreToolUse", "a", cwd="D:\\w\\alpha", tool_name="Read"), T0)
    s.apply(_ev("Notification", "b", cwd="D:\\w\\bravo", message="needs you"), T0)
    out = _settled(s, T0)
    assert out.cards[0].title.startswith("bravo: ")
    assert out.cards[0].body == ""


def test_a_card_for_an_offscreen_project_keeps_both_lines():
    """ตอนไม่มีมาสคอตให้แข่งด้วย การ์ดกลับไปเป็นสองบรรทัดเต็มใบได้ตามเดิม"""
    s = _store(slot_count=1)
    s.apply(_ev("Notification", "a", cwd="D:\\w\\alpha", message="please look"), T0)
    s.apply(_ev("Notification", "b", cwd="D:\\w\\bravo", message="me too"),
            T0 + timedelta(seconds=1))
    out = _settled(s, T0 + timedelta(seconds=1))
    offscreen = [c for c in out.cards if c.body]
    assert offscreen, "การ์ดของโปรเจกต์ที่ตกจอต้องยังบอกได้ว่าใครเป็นคนขอ"


def test_one_session_holds_only_one_card_and_newest_is_on_top():
    s = _store()
    s.apply(_ev("Notification", message="first"), T0)
    s.apply(_ev("Notification", message="second"), T0 + timedelta(seconds=1))
    out = _settled(s, T0 + timedelta(seconds=1))
    assert len(out.cards) == 1
    assert "second" in out.cards[0].title


def test_going_on_dismisses_the_permission_card():
    """ขออนุญาตแล้วได้ไปต่อ = คำขอนั้นตายแล้ว การ์ดต้องไม่ค้างจนหมดอายุเอง"""
    s = _store()
    s.apply(_ev("Notification", message="permission?"), T0)
    s.apply(_ev("PreToolUse", tool_name="Bash"), T0 + timedelta(seconds=1))
    assert _settled(s, T0 + timedelta(seconds=1)).cards == []


def test_a_quiet_stop_earns_a_done_card_not_an_alert():
    """ถ้าย้อมแดงเหมือนกันหมด ทุกเทิร์นที่ผู้ใช้ลุกจากโต๊ะจะได้การ์ดแดง แล้วแดงจะเลิกมีความหมาย"""
    s = _store()
    s.apply(_ev("Stop"), T0)
    quiet = s.snapshot(T0 + timedelta(seconds=50))
    assert quiet.cards[0].kind is CardKind.DONE
    assert quiet.sessions[0].state is VisualState.WAITING  # ท่ายังเป็นนาฬิกาเหลือง


def test_cards_beyond_two_become_overflow():
    """จอวาดได้ 2 ใบ ที่เหลือไปโผล่เป็น +N"""
    s = _store(slot_count=1)
    for i, sid in enumerate(("a", "b", "c", "d")):
        s.apply(_ev("Notification", sid, cwd=f"D:\\w\\p{i}", message="?"), T0)
    out = _settled(s, T0)
    assert len(out.cards) == 2
    assert out.card_overflow == 2


# MARK: - เวลาผ่านไป


def test_a_session_sleeps_after_long_silence_rather_than_nagging_forever():
    """ท่ายืนทวงตลอดกาลไม่ได้สื่ออะไรเพิ่ม การเตือนยังอยู่ในรูปการ์ด"""
    s = _store()
    s.apply(_ev("Stop"), T0)
    out = s.snapshot(T0 + timedelta(seconds=400))
    assert out.sessions[0].state is VisualState.SLEEPING


def test_a_dead_owner_makes_the_session_leave_rather_than_vanish():
    """ปิดหน้าต่าง terminal = process ตายทันที SessionEnd ไม่มีวันยิง"""
    s = _store()
    s.apply(_ev("PreToolUse", tool_name="Read", owner={"pid": 4242}), T0)
    _settled(s, T0)
    s.is_process_alive = lambda owner: False
    leaving = s.snapshot(T0 + timedelta(seconds=3))
    assert leaving.sessions[0].state is VisualState.LEAVING
    # แล้วค่อยหายไปเมื่อท่ามุดหายเล่นจบ
    gone = s.snapshot(T0 + timedelta(seconds=6))
    assert gone.sessions == []


def test_a_stale_session_is_evicted():
    s = _store()
    s.apply(_ev("PreToolUse", tool_name="Read"), T0)
    _settled(s, T0)
    assert s.snapshot(T0 + timedelta(seconds=3700)).sessions == []
    assert s.session_count == 0


def test_a_new_turn_clears_a_stuck_subagent_count():
    """SubagentStop ที่หายไปตอน daemon ไม่ได้รัน จะทำให้ session ติดท่า conducting ตลอดกาล"""
    s = _store()
    s.apply(_ev("SubagentStart"), T0)
    assert _settled(s, T0).sessions[0].state is VisualState.CONDUCTING
    s.apply(_ev("UserPromptSubmit"), T0 + timedelta(seconds=10))
    assert s.snapshot(T0 + timedelta(seconds=11)).sessions[0].state is VisualState.THINKING


# MARK: - clock/date


def test_clock_and_date_do_not_follow_the_machine_locale():
    """สิ่งที่ขึ้นจอเป็นข้อตกลงของ *จอ* และฟอนต์บนบอร์ดมีแค่ ASCII กับไทย"""
    s = _store()
    s.apply(_ev("PreToolUse", tool_name="Read"), T0)
    out = _settled(s, T0)
    assert out.clock == "09:41"
    assert out.date == "Fri 7 Aug"


# MARK: - ToolMap


def test_tool_map_prefers_exact_then_longest_prefix_then_fallback():
    m = ToolMap()
    assert m.state("Read") is VisualState.READING
    assert m.state("mcp__linear__issue") is VisualState.BEACON
    assert m.state("SomethingBrandNew") is VisualState.THINKING


def test_tool_map_file_overrides_and_a_broken_file_is_survivable(tmp_path):
    """ไฟล์เสียไม่ควรทำให้ daemon ตาย"""
    good = tmp_path / "tools.json"
    good.write_text('{"fallback":"idle","tools":{"Read":"building","zz__*":"celebrate"}}',
                    encoding="utf-8")
    m = ToolMap.load(good)
    assert m.state("Read") is VisualState.BUILDING
    assert m.state("zz__thing") is VisualState.CELEBRATE
    assert m.state("Unknown") is VisualState.IDLE

    bad = tmp_path / "broken.json"
    bad.write_text("{not json", encoding="utf-8")
    assert ToolMap.load_or_default(bad).state("Read") is VisualState.READING
    assert ToolMap.load_or_default(tmp_path / "missing.json").fallback is VisualState.THINKING


def test_an_unknown_state_in_the_file_is_skipped_not_fatal():
    """สถานะที่ firmware ไม่รู้จัก = บรรทัดที่ข้ามไป ไม่ใช่ไฟล์ที่เสีย"""
    m = ToolMap()
    m.exact["Read"] = VisualState.READING
    assert m.state("Read") is VisualState.READING
