"""หน้าอากาศ: parse Open-Meteo + เฟรม + schedule — พอร์ต Weather.swift/WeatherService.swift"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "host-win"))

from tamaclaude import weather  # noqa: E402
from tamaclaude.weather import (  # noqa: E402
    HourlyPoint,
    TempUnit,
    WeatherError,
    WeatherFrame,
    WeatherReading,
)
from tamaclaude.weather_service import WeatherService, WeatherSettings  # noqa: E402

T0 = datetime(2026, 8, 7, 12, 0, 0)

GEOCODE = json.dumps({"results": [{"name": "Bangkok", "latitude": 13.75, "longitude": 100.5}]})
FORECAST = json.dumps(
    {
        "current": {"temperature_2m": 31.4, "weather_code": 61},
        "daily": {"temperature_2m_max": [34.2], "temperature_2m_min": [26.1]},
        "hourly": {
            "time": ["2026-08-07T12:00", "2026-08-07T13:00", "2026-08-07T14:00",
                     "2026-08-07T15:00", "2026-08-07T16:00", "2026-08-07T17:00"],
            "temperature_2m": [31, 32, 33, 33, 31, 30],
            "weather_code": [61, 1, 3, 61, 61, 80],
        },
    }
)


def _obj(data: bytes) -> dict:
    return json.loads(data.decode("utf-8"))


# MARK: - parsers


def test_place_from():
    p = weather.place_from(GEOCODE)
    assert p.name == "Bangkok" and p.latitude == 13.75 and p.longitude == 100.5


def test_place_no_results_is_no_such_place():
    try:
        weather.place_from(json.dumps({"results": []}))
        assert False
    except WeatherError as e:
        assert str(e) == weather.NO_SUCH_PLACE


def test_reading_from_rounds():
    r = weather.reading_from(FORECAST, TempUnit.CELSIUS)
    assert (r.temp, r.high, r.low, r.code) == (31, 34, 26, 61)


def test_hourly_skips_current_hour():
    start, hours = weather.hourly_from(FORECAST)
    assert start == 13  # ชั่วโมงของ index 1
    assert len(hours) == weather.HOUR_LIMIT
    assert hours[0] == HourlyPoint(32, 1)


def test_hourly_missing_block_is_empty_not_error():
    start, hours = weather.hourly_from(json.dumps({"current": {}}))
    assert (start, hours) == (-1, [])


# MARK: - frame encode + squeeze


def _frame(place="Bangkok", hours=True) -> WeatherFrame:
    r = WeatherReading(31, 34, 26, 61, TempUnit.CELSIUS)
    hs = [HourlyPoint(32, 1), HourlyPoint(33, 3)] if hours else []
    return WeatherFrame(place=place, reading=r, hour_start=13 if hours else -1, hours=hs)


def test_frame_encode_full():
    obj = _obj(_frame().encoded())
    assert obj["g"] == 1 and obj["p"] == "Bangkok" and obj["t"] == 31
    assert obj["n"] == 13 and obj["o"] == [32, 33] and obj["c"] == [1, 3]
    assert obj["u"] == "C"


def test_frame_squeeze_drops_hours_first():
    # เพดานที่พอสำหรับเลขล้วน แต่ไม่พอสำหรับแถบพยากรณ์ -> ทิ้งแถบ เก็บชื่อ
    small = len(_frame(hours=False).encoded()) + 2
    obj = _obj(_frame().encoded(max_bytes=small))
    assert "n" not in obj and obj["p"] == "Bangkok"


def test_frame_too_long_raises():
    try:
        _frame().encoded(max_bytes=10)
        assert False
    except WeatherError as e:
        assert str(e) == weather.FRAME_TOO_LONG


# MARK: - service


class Dispatcher:
    def __init__(self, geocode=GEOCODE, forecast=FORECAST):
        self.geocode = geocode
        self.forecast = forecast
        self.geocode_calls = 0
        self.forecast_calls = 0

    def __call__(self, url: str) -> bytes:
        if "geocoding-api" in url:
            self.geocode_calls += 1
            return self.geocode.encode()
        self.forecast_calls += 1
        return self.forecast.encode()


def _service(fetch, **kw):
    kw.setdefault("runner", lambda fn: fn())  # รันทันที ให้ผลนิ่ง
    kw.setdefault("settings", WeatherSettings(place="Bangkok"))
    return WeatherService(fetch=fetch, **kw)


def test_service_produces_frame():
    got = []
    svc = _service(Dispatcher(), on_frame=lambda f, at: got.append(f))
    svc.tick(T0)
    assert len(got) == 1
    assert got[0].place == "Bangkok"
    assert got[0].reading.temp == 31
    assert got[0].hour_start == 13


def test_service_caches_geocode():
    d = Dispatcher()
    svc = _service(d, on_frame=lambda f, at: None)
    svc.tick(T0)
    svc.tick(T0 + timedelta(seconds=weather.INTERVAL + 1))
    assert d.geocode_calls == 1  # geocode ครั้งเดียว
    assert d.forecast_calls == 2  # forecast ทุกรอบ


def test_service_not_usable_without_place():
    got = []
    svc = _service(Dispatcher(), settings=WeatherSettings(place="  "), on_frame=lambda f, at: got.append(f))
    svc.tick(T0)
    assert got == []


def test_service_failure_retries_sooner():
    def boom(url):
        raise WeatherError(weather.BAD_PAYLOAD)

    svc = _service(boom, on_frame=lambda f, at: None)
    svc.tick(T0)
    assert svc.status == weather.BAD_PAYLOAD
    # ล้มแล้ว retry 60s ไม่ต้องรอเต็ม interval
    assert not svc._schedule.start(T0 + timedelta(seconds=30))
    assert svc._schedule.start(T0 + timedelta(seconds=61))


def test_service_update_place_invalidates():
    d = Dispatcher()
    svc = _service(d, on_frame=lambda f, at: None)
    svc.tick(T0)
    svc.update(WeatherSettings(place="Tokyo"))
    # เมืองใหม่ -> ยิงเดี๋ยวนี้ ไม่รอ interval และ geocode ใหม่
    svc.tick(T0 + timedelta(seconds=1))
    assert d.geocode_calls == 2
