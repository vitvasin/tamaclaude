"""UI ตรรกะล้วน — badge, quota_card, refresh_control, panel_text · พอร์ตพฤติกรรมจากฝั่ง mac"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "host-win"))

from tamaclaude.protocol import UNKNOWN, Snapshot, SessionSnap, UsageSnap, VisualState  # noqa: E402
from tamaclaude.usage_poll import Org  # noqa: E402
from tamaclaude.usage_poller import PollInterval  # noqa: E402
from tamaclaude.ui import badge, panel_text, refresh_control  # noqa: E402
from tamaclaude.ui.quota_card import CRIT_PCT, WARN_PCT, Level, QuotaCard  # noqa: E402

NOW = datetime(2026, 8, 10, 12, 0, 0)


# MARK: - MenuBadge


def test_badge_none_when_unknown_or_empty():
    assert badge.MenuBadge.from_usage(None) is None
    assert badge.MenuBadge.from_usage([]) is None
    assert badge.MenuBadge.from_usage([UsageSnap(percent=UNKNOWN, remaining=100)]) is None


def test_badge_none_when_window_rolled_over():
    # remaining == 0 = หน้าต่างตายแล้ว เปอร์เซ็นต์ที่ถืออยู่ไม่มีความหมาย
    assert badge.MenuBadge.from_usage([UsageSnap(percent=50, remaining=0)]) is None


def test_badge_alarming_only_when_over_pace():
    # SESSION_WINDOW 18000s, remaining 9000 -> เดินไปครึ่งทาง pace 50
    b = badge.MenuBadge.from_usage([UsageSnap(percent=70, remaining=9000)])
    assert b.pace == 50 and b.percent == 70 and b.is_alarming is True
    b2 = badge.MenuBadge.from_usage([UsageSnap(percent=30, remaining=9000)])
    assert b2.is_alarming is False


# MARK: - QuotaCard


def test_cards_none_when_all_unknown():
    assert QuotaCard.cards(None) is None
    assert QuotaCard.cards([UsageSnap(), UsageSnap()]) is None


def test_cards_two_windows_with_weekly_pill():
    cards = QuotaCard.cards([UsageSnap(percent=20, remaining=9000),
                             UsageSnap(percent=40, remaining=302400)], now=NOW)
    assert len(cards) == 2
    assert cards[0].title == "Session usage" and cards[0].pill is None
    assert cards[1].title == "All models" and cards[1].pill == "Weekly"


def test_level_over_pace_is_crit_even_when_low():
    assert QuotaCard.level_of(percent=30, pace=10) == Level.CRIT
    assert QuotaCard.level_of(percent=UNKNOWN, pace=10) == Level.UNKNOWN
    assert QuotaCard.level_of(percent=WARN_PCT, pace=99) == Level.WARN
    assert QuotaCard.level_of(percent=CRIT_PCT, pace=99) == Level.CRIT
    assert QuotaCard.level_of(percent=10, pace=99) == Level.GOOD


def test_reset_line_variants():
    assert QuotaCard.reset_line(UNKNOWN, NOW) == "No reset time yet"
    assert QuotaCard.reset_line(0, NOW) == "Resetting now"
    # 2h 24m -> Today 14:24 (ช่องว่างตรงกับ shortSpan ฝั่ง mac)
    assert QuotaCard.reset_line(2 * 3600 + 24 * 60, NOW) == "Resets in 2h 24m (Today 14:24)"


def test_reset_line_tomorrow_and_far():
    assert "Tomorrow" in QuotaCard.reset_line(20 * 3600, NOW)
    far = QuotaCard.reset_line(3 * 86400 + 3600, NOW)
    assert far.startswith("Resets in 3d 1h") and "Aug 13" in far


def test_short_span_sub_minute_rounds_up():
    assert QuotaCard._short_span(30) == "1m"  # ไม่ใช่ 0m


# MARK: - RefreshControl


def test_refresh_running_spins_disabled():
    st = refresh_control.state(running=True, has_key=True, finished=None, now=NOW)
    assert st.spinning is True and st.enabled is False


def test_refresh_no_key_disabled():
    st = refresh_control.state(running=False, has_key=False, finished=None, now=NOW)
    assert st.enabled is False and "session key" in st.tooltip.lower()


def test_refresh_cooldown_then_ready():
    just = NOW - timedelta(seconds=3)
    st = refresh_control.state(running=False, has_key=True, finished=just, now=NOW)
    assert st.enabled is False and "again in 7s" in st.tooltip
    old = NOW - timedelta(seconds=20)
    assert refresh_control.state(running=False, has_key=True, finished=old, now=NOW).enabled


def test_wants_poll():
    assert refresh_control.wants_poll(PollInterval.OFF, stamp=None, now=NOW) is False
    assert refresh_control.wants_poll(PollInterval.MINUTE, stamp=None, now=NOW) is True
    fresh = NOW - timedelta(seconds=5)
    assert refresh_control.wants_poll(PollInterval.MINUTE, stamp=fresh, now=NOW) is False
    stale = NOW - timedelta(seconds=120)
    assert refresh_control.wants_poll(PollInterval.MINUTE, stamp=stale, now=NOW) is True
    # polled ล่าสุดกันการยิงซ้ำแม้ stamp เก่า (รอบที่ล้มไม่ขยับ stamp)
    recent_poll = NOW - timedelta(seconds=5)
    assert refresh_control.wants_poll(
        PollInterval.MINUTE, stamp=stale, polled=recent_poll, now=NOW) is False


# MARK: - PanelText


def test_board_line():
    assert panel_text.board(True) == "Board connected"
    assert panel_text.board(False) == "Looking for the board…"


def test_heading_falls_back_to_app_name():
    orgs = [Org(id="o1", name="Acme"), Org(id="o2", name="Beta")]
    assert panel_text.heading(orgs, current="o2", has_key=True) == "Beta"
    assert panel_text.heading(orgs, current="o2", has_key=False) == "TamaClaude"
    assert panel_text.heading(orgs, current=None, has_key=True) == "TamaClaude"
    assert panel_text.heading([], current="x", has_key=True) == "TamaClaude"


def test_can_switch_org():
    two = [Org(id="a", name="A"), Org(id="b", name="B")]
    assert panel_text.can_switch_org(two, has_key=True) is True
    assert panel_text.can_switch_org(two, has_key=False) is False
    assert panel_text.can_switch_org([Org(id="a", name="A")], has_key=True) is False


def test_key_problem():
    assert panel_text.key_problem(None) is None
    assert "expired" in panel_text.key_problem(panel_text.KeyProblem.EXPIRED_KEY)
    assert "unusable" in panel_text.key_problem(panel_text.KeyProblem.UNUSABLE_KEY_FILE)


def test_updated_buckets_and_negative_clamp():
    assert panel_text.updated(None) == "No quota figures yet"
    assert panel_text.updated(NOW - timedelta(seconds=5), now=NOW) == "Updated 5s ago"
    assert panel_text.updated(NOW - timedelta(minutes=5), now=NOW) == "Updated 5m ago"
    assert panel_text.updated(NOW - timedelta(hours=3), now=NOW) == "Updated 3h ago"
    assert panel_text.updated(NOW - timedelta(days=3), now=NOW) == "Updated 3d ago"
    # นาฬิกาถอยหลัง (stamp อยู่อนาคต) ต้องไม่ประหลาด
    assert panel_text.updated(NOW + timedelta(seconds=10), now=NOW) == "Updated 0s ago"


def test_sessions_rows_and_overflow():
    assert panel_text.sessions(Snapshot(clock="12:00", date="Aug 10")) == ["No sessions"]
    snap = Snapshot(
        clock="12:00", date="Aug 10", overflow=2,
        sessions=[SessionSnap(project="app", state=VisualState.WRITING)],
    )
    rows = panel_text.sessions(snap)
    assert rows == ["app · writing", "+2 more"]
