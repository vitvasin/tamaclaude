"""JSON -> usage cache — พอร์ตของ UsageWriter.swift · เน้น invariant เพิ่มอย่างเดียว + foreign keys"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "host-win"))

from tamaclaude import usage_reader as ur  # noqa: E402
from tamaclaude import usage_writer as uw  # noqa: E402

NOW = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)
FIVE_H_EPOCH = NOW.timestamp() + 3600  # รีเซ็ตอีก 1 ชม.
WEEK_EPOCH = NOW.timestamp() + 3 * 86400


def _cache(tmp_path) -> Path:
    return tmp_path / "cache"


def _fields(url: Path) -> dict:
    return ur.parse(url.read_text(encoding="utf-8"))


def _statusline(five_pct, seven_pct=None) -> str:
    limits = {"five_hour": {"used_percentage": five_pct, "resets_at": FIVE_H_EPOCH}}
    if seven_pct is not None:
        limits["seven_day"] = {"used_percentage": seven_pct, "resets_at": WEEK_EPOCH}
    return json.dumps({"rate_limits": limits})


# MARK: - ingest (statusline)


def test_ingest_writes_known_keys(tmp_path):
    url = _cache(tmp_path)
    summary = uw.ingest(_statusline(42, 7), NOW, url)
    assert summary == "5h 42% · 7d 7%"
    f = _fields(url)
    assert f["UTILIZATION"] == "42"
    assert f["WEEKLY_UTILIZATION"] == "7"
    assert f["RESETS_AT"] == "2026-08-07T13:00:00Z"
    assert "TIMESTAMP" in f


def test_ingest_round_trips_through_reader(tmp_path):
    url = _cache(tmp_path)
    uw.ingest(_statusline(55), NOW, url)
    session, _weekly = ur.read(NOW, url)
    assert session.percent == 55
    assert session.remaining == 3600


def test_ingest_float_percent_rounded(tmp_path):
    url = _cache(tmp_path)
    body = json.dumps({"rate_limits": {"five_hour": {"used_percentage": 16.6, "resets_at": FIVE_H_EPOCH}}})
    uw.ingest(body, NOW, url)
    assert _fields(url)["UTILIZATION"] == "17"


def test_no_rate_limits_returns_none(tmp_path):
    url = _cache(tmp_path)
    assert uw.ingest(json.dumps({"foo": 1}), NOW, url) is None
    assert not url.exists()


# MARK: - invariant: เพิ่มอย่างเดียวภายในหน้าต่างเดียว


def test_lower_percent_same_window_kept_high(tmp_path):
    url = _cache(tmp_path)
    uw.ingest(_statusline(80), NOW, url)
    uw.ingest(_statusline(60), NOW, url)  # ค่าเก่ากว่ามาทีหลัง (resets_at เดิม)
    assert _fields(url)["UTILIZATION"] == "80"


def test_higher_percent_same_window_updates(tmp_path):
    url = _cache(tmp_path)
    uw.ingest(_statusline(60), NOW, url)
    uw.ingest(_statusline(80), NOW, url)
    assert _fields(url)["UTILIZATION"] == "80"


def test_lower_percent_new_window_replaces(tmp_path):
    url = _cache(tmp_path)
    uw.ingest(_statusline(80), NOW, url)
    # หน้าต่างใหม่: resets_at ต่างออกไป -> ค่าที่ลดลงกลายเป็นค่าที่ถูก
    body = json.dumps({"rate_limits": {"five_hour": {"used_percentage": 5, "resets_at": FIVE_H_EPOCH + 99999}}})
    uw.ingest(body, NOW, url)
    assert _fields(url)["UTILIZATION"] == "5"


# MARK: - foreign keys รอด


def test_foreign_keys_preserved(tmp_path):
    url = _cache(tmp_path)
    url.write_text("PROFILE_NAME=me\nCOST_TODAY=1.23\nUTILIZATION=1\nRESETS_AT=old\n", encoding="utf-8")
    uw.ingest(_statusline(50), NOW, url)
    f = _fields(url)
    assert f["PROFILE_NAME"] == "me"
    assert f["COST_TODAY"] == "1.23"
    assert f["UTILIZATION"] == "50"


# MARK: - ingest_api (claude.ai)


def test_ingest_api_top_level(tmp_path):
    url = _cache(tmp_path)
    body = json.dumps(
        {
            "five_hour": {"utilization": 33.4, "resets_at": "2026-08-07T13:00:00Z"},
            "seven_day": {"utilization": 12.0, "resets_at": "2026-08-10T12:00:00Z"},
        }
    )
    summary = uw.ingest_api(body, NOW, url)
    assert summary == "5h 33% · 7d 12%"
    f = _fields(url)
    assert f["UTILIZATION"] == "33"
    assert f["WEEKLY_UTILIZATION"] == "12"


def test_ingest_api_limits_array(tmp_path):
    url = _cache(tmp_path)
    body = json.dumps(
        {
            "limits": [
                {"kind": "session", "percent": 45, "resets_at": "2026-08-07T13:00:00Z"},
                {"kind": "weekly_scoped", "percent": 90, "resets_at": "2026-08-10T12:00:00Z"},
            ]
        }
    )
    summary = uw.ingest_api(body, NOW, url)
    # weekly_scoped ไม่นับ (รายโมเดล) เหลือแค่ session
    assert summary == "5h 45%"
    assert _fields(url)["UTILIZATION"] == "45"
    assert "WEEKLY_UTILIZATION" not in _fields(url)


def test_ingest_api_percent_without_reset_dropped(tmp_path):
    url = _cache(tmp_path)
    body = json.dumps({"five_hour": {"utilization": 50.0}})  # ไม่มี resets_at
    assert uw.ingest_api(body, NOW, url) is None
