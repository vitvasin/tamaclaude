"""daemon ส่งเฟรมหน้าอากาศเป็นก้อนแยก หลังบอร์ดประกาศ cap — พฤติกรรมจาก PageHub + Daemon.swift"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "host-win"))

from tamaclaude import usage_reader  # noqa: E402
from tamaclaude.daemon import Daemon  # noqa: E402
from tamaclaude.crypto import CryptoFrame, CryptoQuote  # noqa: E402
from tamaclaude.stocks import StockQuote, StocksFrame  # noqa: E402
from tamaclaude.weather import HourlyPoint, TempUnit, WeatherFrame, WeatherReading  # noqa: E402

T0 = datetime(2026, 8, 7, 12, 0, 0)


def _frame() -> WeatherFrame:
    r = WeatherReading(31, 34, 26, 61, TempUnit.CELSIUS)
    return WeatherFrame(place="Bangkok", reading=r, hour_start=13, hours=[HourlyPoint(32, 1)])


def _daemon(monkeypatch):
    # ไม่ต่อ BLE, ไม่ยิงโควตา, echo เปิดเพื่อดู payload · usage อ่านเป็น None กันคีย์ u มารบกวน
    monkeypatch.setattr(usage_reader, "read", lambda now=None, url=None: None)
    return Daemon(use_ble=False, echo=True, use_poll=False, use_pages=True)


def _lines(capsys) -> list[dict]:
    out = capsys.readouterr().out.strip().splitlines()
    return [json.loads(x) for x in out if x.strip()]


def test_weather_frame_sent_after_cap(monkeypatch, capsys):
    d = _daemon(monkeypatch)
    d._on_board_event(b'{"t":"cap","p":[0,1]}')  # บอร์ดรู้จักมาสคอต+อากาศ
    d._on_weather_frame(_frame(), T0)
    d.tick(now=T0)
    objs = _lines(capsys)
    # มีทั้ง snapshot (ไม่มี g) และเฟรมอากาศ (g=1)
    assert any("g" not in o for o in objs)
    weather = [o for o in objs if o.get("g") == 1]
    assert len(weather) == 1
    assert weather[0]["p"] == "Bangkok" and weather[0]["t"] == 31


def test_weather_frame_gated_until_cap(monkeypatch, capsys):
    d = _daemon(monkeypatch)
    d._on_weather_frame(_frame(), T0)  # ยังไม่ประกาศ cap
    d.tick(now=T0)
    objs = _lines(capsys)
    assert not any(o.get("g") == 1 for o in objs)


def test_crypto_frame_sent_after_cap(monkeypatch, capsys):
    d = _daemon(monkeypatch)
    d._on_board_event(b'{"t":"cap","p":[0,1,2]}')
    d._on_crypto_frame(CryptoFrame([CryptoQuote("BTC", 64230.0, -2.1)]), T0)
    d.tick(now=T0)
    objs = _lines(capsys)
    crypto = [o for o in objs if o.get("g") == 2]
    assert len(crypto) == 1
    assert crypto[0]["c"][0]["s"] == "BTC"


def test_stocks_frame_sent_after_cap(monkeypatch, capsys):
    d = _daemon(monkeypatch)
    d._on_board_event(b'{"t":"cap","p":[0,1,2,3,4]}')
    d._on_stocks_frame(StocksFrame([StockQuote("AAPL", 189.44, -2.1, low=184, high=194)]), T0)
    d.tick(now=T0)
    objs = _lines(capsys)
    stocks = [o for o in objs if o.get("g") == 4]
    assert len(stocks) == 1
    assert stocks[0]["c"][0]["s"] == "AAPL"


def test_plan_sent_after_cap(monkeypatch, capsys):
    d = _daemon(monkeypatch)
    d._on_board_event(b'{"t":"cap","p":[0,1]}')
    d.tick(now=T0)
    objs = _lines(capsys)
    # แผนรอบหมุนมีคีย์ pl
    assert any("pl" in o for o in objs)
