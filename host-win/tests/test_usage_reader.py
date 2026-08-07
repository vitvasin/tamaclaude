"""usage cache -> [session, weekly] — พอร์ตของ UsageReader.swift อ่านมือเทียบ Swift"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "host-win"))

from tamaclaude import usage_reader as ur  # noqa: E402
from tamaclaude.protocol import UNKNOWN  # noqa: E402

NOW = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "cache"
    p.write_text(body, encoding="utf-8")
    return p


def _iso(minutes: int) -> str:
    return (NOW + timedelta(minutes=minutes)).isoformat()


# MARK: - ไม่มีอะไรจะบอก -> None (บอร์ดถอยไปเป็นนาฬิกา)


def test_missing_file_is_none(tmp_path):
    assert ur.read(NOW, tmp_path / "nope") is None


def test_no_known_keys_is_none(tmp_path):
    url = _write(tmp_path, "PROFILE_NAME=me\nCOST_TODAY=1.23\n")
    assert ur.read(NOW, url) is None


def test_empty_value_is_not_zero(tmp_path):
    # ไฟล์ที่เขียนครึ่งๆ ต้องไม่กลายเป็น 0% แต่เป็น "ไม่รู้"
    url = _write(tmp_path, "UTILIZATION=\nRESETS_AT=\n")
    assert ur.read(NOW, url) is None


# MARK: - session + weekly


def test_session_and_weekly(tmp_path):
    url = _write(
        tmp_path,
        f"UTILIZATION=42\nRESETS_AT={_iso(90)}\n"
        f"WEEKLY_UTILIZATION=7\nWEEKLY_RESETS_AT={_iso(60 * 48)}\n",
    )
    session, weekly = ur.read(NOW, url)
    assert session.percent == 42
    assert session.remaining == 90 * 60
    assert weekly.percent == 7
    assert weekly.remaining == 48 * 60 * 60


def test_remaining_floored_to_minute(tmp_path):
    # 90 วินาที -> ปัดลงเป็น 60 (ไม่ต่ำกว่า 60 เมื่อยังเหลือ)
    url = _write(tmp_path, f"UTILIZATION=10\nRESETS_AT={(NOW + timedelta(seconds=90)).isoformat()}\n")
    (session, _weekly) = ur.read(NOW, url)
    assert session.remaining == 60


def test_window_expired_percent_unknown(tmp_path):
    # resets_at ผ่านไปแล้ว: remaining=0 และ percent ที่ถืออยู่ผิด -> UNKNOWN
    url = _write(tmp_path, f"UTILIZATION=99\nRESETS_AT={_iso(-5)}\n")
    (session, _weekly) = ur.read(NOW, url)
    assert session.remaining == 0
    assert session.percent == UNKNOWN


def test_percent_out_of_range_ignored(tmp_path):
    url = _write(tmp_path, f"UTILIZATION=250\nRESETS_AT={_iso(30)}\n")
    (session, _weekly) = ur.read(NOW, url)
    assert session.percent == UNKNOWN
    assert session.remaining == 30 * 60


def test_z_suffix_iso_parsed(tmp_path):
    z = (NOW + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = _write(tmp_path, f"UTILIZATION=5\nRESETS_AT={z}\n")
    (session, _weekly) = ur.read(NOW, url)
    assert session.remaining == 30 * 60


# MARK: - elapsed_percent


def test_elapsed_percent():
    assert ur.elapsed_percent(ur.SESSION_WINDOW, ur.SESSION_WINDOW) == 0
    assert ur.elapsed_percent(0, ur.SESSION_WINDOW) == 100
    assert ur.elapsed_percent(ur.SESSION_WINDOW // 2, ur.SESSION_WINDOW) == 50
    assert ur.elapsed_percent(UNKNOWN, ur.SESSION_WINDOW) == UNKNOWN


# MARK: - stamp


def test_stamp(tmp_path):
    url = _write(tmp_path, "UTILIZATION=1\nTIMESTAMP=1000000000\n")
    assert ur.stamp(url) == datetime.fromtimestamp(1000000000, tz=timezone.utc)


def test_stamp_missing(tmp_path):
    url = _write(tmp_path, "UTILIZATION=1\n")
    assert ur.stamp(url) is None
