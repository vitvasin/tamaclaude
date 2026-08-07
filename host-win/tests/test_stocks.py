"""หน้าหุ้น: parse Finnhub + เฟรม + market hours + service — พอร์ต Stocks.swift/StocksService.swift"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "host-win"))

from tamaclaude import stocks  # noqa: E402
from tamaclaude.stocks import StockQuote, StocksError, StocksFrame  # noqa: E402
from tamaclaude.stocks_service import StockSettings, StocksService  # noqa: E402

ET = ZoneInfo("America/New_York")
OPEN = datetime(2026, 8, 7, 10, 0, tzinfo=ET)   # ศุกร์ 10:00 -> เปิด
CLOSED = datetime(2026, 8, 8, 10, 0, tzinfo=ET)  # เสาร์ -> ปิด


def _obj(data: bytes) -> dict:
    return json.loads(data.decode("utf-8"))


# MARK: - market hours


def test_is_open():
    assert stocks.is_open(OPEN)
    assert not stocks.is_open(CLOSED)
    assert not stocks.is_open(datetime(2026, 8, 7, 9, 0, tzinfo=ET))   # ก่อน 9:30
    assert not stocks.is_open(datetime(2026, 8, 7, 16, 0, tzinfo=ET))  # 16:00 ปิดพอดี
    assert stocks.is_open(datetime(2026, 8, 7, 15, 59, tzinfo=ET))


# MARK: - formatting


def test_range_text_rounds_outward():
    assert stocks.range_text(184.2, high=193.0, up=False) == "184"   # >=100 -> 0 ตำแหน่ง, ปัดลง
    assert stocks.range_text(192.1, high=193.0, up=True) == "193"    # ปัดขึ้น
    assert stocks.range_text(8.24, high=9.0, up=False) == "8.2"      # <100 -> 1 ตำแหน่ง
    assert stocks.range_text(8.71, high=9.0, up=True) == "8.8"


def test_day_range_position():
    q = StockQuote("AAPL", 189.0, -1.0, low=184.0, high=194.0)
    r = StocksFrame([q]).day_range()
    assert r == {"l": "184", "h": "194", "t": 50}


def test_day_range_none_when_no_range():
    q = StockQuote("AAPL", 189.0, -1.0, low=0, high=0)
    assert StocksFrame([q]).day_range() is None


# MARK: - frame encode + squeeze


def _frame(n=1, closed=False):
    qs = [StockQuote(f"S{i}", 189.44 + i, -2.1, low=184.0, high=194.0) for i in range(n)]
    return StocksFrame(qs, market_closed=closed)


def test_frame_encode_full():
    obj = _obj(_frame().encoded())
    assert obj["g"] == 4
    assert obj["c"][0] == {"s": "S0", "p": "189.44", "d": -21}
    assert obj["r"] == {"l": "184", "h": "194", "t": 54}
    assert "k" not in obj  # ตลาดเปิด = ไม่มีคีย์


def test_frame_closed_has_k():
    assert _obj(_frame(closed=True).encoded())["k"] == 1


def test_frame_squeeze_drops_range_first():
    frame = _frame(n=5)
    full = len(frame.encoded())
    obj = _obj(frame.encoded(max_bytes=full - 5))
    assert "r" not in obj  # ช่วงราคาไปก่อน
    assert len(obj["c"]) == 5
    assert "d" in obj["c"][0]  # เปอร์เซ็นต์ยังอยู่


# MARK: - quote parser


def _quote_json(c, pc=None, dp=None, h=None, l=None):
    d = {"c": c}
    if pc is not None:
        d["pc"] = pc
    if dp is not None:
        d["dp"] = dp
    if h is not None:
        d["h"] = h
    if l is not None:
        d["l"] = l
    return json.dumps(d)


def test_quote_parses():
    q = stocks.quote("aapl", _quote_json(189.44, pc=193.0, dp=-2.1, h=194.0, l=184.0))
    assert q.symbol == "AAPL" and q.price == 189.44 and q.change == -2.1
    assert q.high == 194.0 and q.low == 184.0


def test_quote_all_zero_is_no_such_symbol():
    try:
        stocks.quote("BOGUS", _quote_json(0, pc=0))
        assert False
    except StocksError as e:
        assert str(e) == stocks.NO_SUCH_SYMBOL


def test_quote_missing_c_is_bad_payload():
    try:
        stocks.quote("AAPL", json.dumps({"pc": 100}))
        assert False
    except StocksError as e:
        assert str(e) == stocks.BAD_PAYLOAD


def test_describe_strips_token():
    url = stocks.quote_url("AAPL", "secrettoken")
    d = stocks.describe(url)
    assert "secrettoken" not in d and "symbol=AAPL" in d


# MARK: - service


class Dispatcher:
    def __init__(self, quotes: dict[str, dict], unknown: set[str] = frozenset(), key_bad=False):
        self.quotes = quotes
        self.unknown = set(unknown)
        self.key_bad = key_bad
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        if self.key_bad:
            raise StocksError(stocks.KEY_REJECTED)
        sym = url.split("symbol=")[1].split("&")[0]
        self.calls.append(sym)
        if sym in self.unknown:
            return _quote_json(0, pc=0).encode()
        return json.dumps(self.quotes[sym]).encode()


def _service(fetch, symbols, **kw):
    kw.setdefault("runner", lambda fn: fn())
    kw.setdefault("key", lambda: "tok")
    return StocksService(fetch=fetch, settings=StockSettings(symbols=symbols), **kw)


def test_service_open_produces_frame_ordered():
    d = Dispatcher({"AAPL": {"c": 189.0, "pc": 193.0, "dp": -2.0, "h": 194, "l": 184},
                    "MSFT": {"c": 400.0, "pc": 396.0, "dp": 1.0, "h": 401, "l": 395}})
    got = []
    svc = _service(d, ["MSFT", "AAPL"], on_frame=lambda f, at: got.append(f))
    svc.tick(OPEN)
    assert len(got) == 1
    assert [q.symbol for q in got[0].quotes] == ["MSFT", "AAPL"]
    assert got[0].market_closed is False


def test_service_closed_replays_last_as_closed():
    d = Dispatcher({"AAPL": {"c": 189.0, "pc": 193.0, "dp": -2.0, "h": 194, "l": 184}})
    got = []
    svc = _service(d, ["AAPL"], on_frame=lambda f, at: got.append(f))
    # ตลาดปิด ยังไม่มี last -> ยิงหนึ่งรอบ /quote ยังคืนราคาปิดล่าสุด
    svc.tick(CLOSED)
    assert len(got) == 1 and got[0].market_closed is True
    # tick ปิดซ้ำ -> ไม่ส่งเฟรมใหม่ (บอกครั้งเดียวต่อช่วงปิด)
    svc.tick(CLOSED + timedelta(seconds=1))
    assert len(got) == 1


def test_service_key_rejected_latches():
    d = Dispatcher({}, key_bad=True)
    svc = _service(d, ["AAPL"], on_frame=lambda f, at: None)
    svc.tick(OPEN)
    assert svc.key_rejected
    assert svc.status == stocks.KEY_REJECTED
    # ล็อกแล้ว — ไม่ยิงอีก
    d.key_bad = False
    svc.tick(OPEN + timedelta(seconds=61))
    assert d.calls == []


def test_service_unknown_symbol_cached():
    d = Dispatcher({"AAPL": {"c": 189.0, "pc": 193.0, "dp": -2.0, "h": 194, "l": 184}},
                   unknown={"BOGUS"})
    svc = _service(d, ["BOGUS", "AAPL"], on_frame=lambda f, at: None)
    svc.tick(OPEN)
    svc.tick(OPEN + timedelta(seconds=61))
    # BOGUS ถามครั้งเดียว (รอบแรก) แล้วจำว่าไม่มี — รอบสองข้าม
    assert d.calls.count("BOGUS") == 1
    assert svc.status is not None and "BOGUS" in svc.status
