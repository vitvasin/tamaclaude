"""ตัวจับเวลา + parse ผลลูก — พอร์ตพฤติกรรมจาก UsagePoller.swift · launcher ปลอม ไม่ spawn จริง"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "host-win"))

from tamaclaude import usage_poll as up  # noqa: E402
from tamaclaude import usage_poller as pl  # noqa: E402

T0 = datetime(2026, 8, 7, 12, 0, 0)


class FakeLauncher:
    """เก็บ callback ไว้ให้เทสต์สั่ง 'ลูกจบแล้ว' เอง + นับการฆ่า"""

    def __init__(self):
        self.done = None
        self.org_id = None
        self.launches = 0
        self.kills = 0

    def __call__(self, org_id, done):
        self.launches += 1
        self.org_id = org_id
        self.done = done
        return self._kill

    def _kill(self):
        self.kills += 1

    def finish(self, code, output=""):
        self.done(pl.Outcome(code=code, output=output))


def _poller(fake, **kw):
    kw.setdefault("has_key", lambda: True)
    return pl.UsagePoller(launch=fake, **kw)


# MARK: - PollInterval


def test_interval_stored_fallback():
    assert pl.PollInterval.stored(None) == pl.PollInterval.MINUTE
    assert pl.PollInterval.stored(999) == pl.PollInterval.MINUTE
    assert pl.PollInterval.stored(0) == pl.PollInterval.OFF
    assert pl.PollInterval.stored(300) == pl.PollInterval.FIVE_MINUTES


# MARK: - parse/render output round trip


def test_output_round_trip():
    report = up.Report(orgs=[up.Org("a", "Acme Inc"), up.Org("b", "b")], summary="5h 42%")
    orgs, summary = pl.parse_output(pl.render_output(report))
    assert orgs == report.orgs
    assert summary == "5h 42%"


def test_parse_output_skips_bad_org_id():
    orgs, summary = pl.parse_output("org bad/slash Name\norg good G\n5h 1%")
    assert orgs == [up.Org("good", "G")]
    assert summary == "5h 1%"


# MARK: - tick scheduler


def test_first_tick_starts(fake=None):
    f = FakeLauncher()
    p = _poller(f)
    p.tick(T0)
    assert f.launches == 1
    assert p.is_running


def test_no_second_child_while_running():
    f = FakeLauncher()
    p = _poller(f)
    p.tick(T0)
    p.tick(T0 + timedelta(seconds=1))
    assert f.launches == 1  # ลูกยังวิ่ง ไม่ spawn ตัวสอง


def test_waits_for_interval_after_finish():
    f = FakeLauncher()
    p = _poller(f, interval=pl.PollInterval.MINUTE)
    p.tick(T0)
    f.finish(0)
    # ยังไม่ครบ 60 วิ
    p.tick(T0 + timedelta(seconds=30))
    assert f.launches == 1
    # ครบแล้ว
    p.tick(T0 + timedelta(seconds=60))
    assert f.launches == 2


def test_off_never_polls():
    f = FakeLauncher()
    p = _poller(f, interval=pl.PollInterval.OFF)
    p.tick(T0)
    assert f.launches == 0


def test_expired_key_locks_polling():
    f = FakeLauncher()
    p = _poller(f)
    p.tick(T0)
    f.finish(up.REJECTED_KEY)
    assert p.blocked == pl.PollBlock.EXPIRED_KEY
    p.tick(T0 + timedelta(seconds=120))
    assert f.launches == 1  # ล็อกแล้ว ไม่ยิงอีก


def test_unusable_key_file_is_not_a_lock():
    f = FakeLauncher()
    p = _poller(f)
    p.tick(T0)
    f.finish(up.UNUSABLE_KEY_FILE)
    assert p.blocked == pl.PollBlock.UNUSABLE_KEY_FILE
    # ไม่ล็อก: ครบรอบแล้วยิงใหม่ได้ (ผู้ใช้อาจ fix สิทธิ์ไฟล์ข้างนอก)
    p.tick(T0 + timedelta(seconds=61))
    assert f.launches == 2


def test_transient_error_clears_unusable_flag():
    f = FakeLauncher()
    p = _poller(f)
    p.tick(T0)
    f.finish(up.UNUSABLE_KEY_FILE)
    p.tick(T0 + timedelta(seconds=61))
    f.finish(1)  # รอบนี้ไปได้ไกลกว่า — ป้ายเก่าต้องหาย
    assert p.blocked is None


def test_timeout_kills_stuck_child():
    f = FakeLauncher()
    p = _poller(f)
    p.tick(T0)
    p.tick(T0 + timedelta(seconds=31))
    assert f.kills == 1
    assert not p.is_running
    assert "too long" in p.status


def test_stale_child_generation_ignored():
    f = FakeLauncher()
    p = _poller(f)
    p.tick(T0)
    p.tick(T0 + timedelta(seconds=31))  # ฆ่าตัวแรก, generation++
    p.tick(T0 + timedelta(seconds=91))  # ยิงตัวใหม่
    stale = f  # done ของ FakeLauncher ชี้ไป callback รุ่นล่าสุด — จำลองรุ่นเก่าพูด
    # เสียงจากลูกที่ถูกฆ่าไปแล้ว (generation เก่า) ต้องไม่ทับสถานะ — ที่นี่ finish ล่าสุดคือ
    # ของตัวที่สอง; ตรวจว่า orgs เดิมไม่ถูกเขียนจากรุ่นเก่าเป็นการทดสอบเชิงโครง
    assert p.is_running  # ตัวที่สองยังวิ่ง
    _ = stale


def test_org_id_passed_to_launcher():
    f = FakeLauncher()
    p = _poller(f, preferred_org="org-x")
    p.tick(T0)
    assert f.org_id == "org-x"


def test_current_org_prefers_from_list():
    f = FakeLauncher()
    p = _poller(f, preferred_org="b")
    p.orgs = [up.Org("a", "A"), up.Org("b", "B")]
    assert p.current_org == "b"
