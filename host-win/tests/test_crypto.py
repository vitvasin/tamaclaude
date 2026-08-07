"""หน้าคริปโต: parse CoinGecko + เฟรม + fold + service — พอร์ต Crypto.swift/CryptoService.swift"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "host-win"))

from tamaclaude import crypto  # noqa: E402
from tamaclaude.crypto import CryptoError, CryptoFrame, CryptoQuote  # noqa: E402
from tamaclaude.crypto_service import CryptoService, CryptoSettings  # noqa: E402

T0 = datetime(2026, 8, 7, 12, 0, 0)


def _obj(data: bytes) -> dict:
    return json.loads(data.decode("utf-8"))


# MARK: - price formatting


def test_price_decimals_and_grouping():
    assert crypto.price_text(64230.0) == "64,230.00"
    assert crypto.price_text(0.5) == "0.5000"
    assert crypto.price_text(0.000008) == "0.000008"


def test_price_trim_only_below_one():
    assert crypto.price_text(64230.0, trim=4) == "64,230.00"  # >=1 ตรึงสองตำแหน่ง
    assert crypto.price_text(0.5000, trim=2) == "0.50"  # <1 ลดได้


def test_change_encoded_rounds_half_up():
    q = CryptoQuote("BTC", 64230.0, -2.14)
    row = CryptoFrame([q])._rows(0, 1, False)[0]
    assert row["d"] == -21


# MARK: - sparkline


def test_spark_round_trip():
    levels = [0, 5, 10, 15, 8]
    assert crypto.spark_levels(crypto.spark_text(levels)) == levels


def test_spark_needs_16_points():
    assert crypto.spark(list(range(10)), now=5) == []


def test_spark_keeps_both_ends():
    series = [float(i) for i in range(1, 200)]  # เพิ่มขึ้นตลอด
    levels = crypto.spark(series, now=999.0)  # now สูงสุด -> จุดสุดท้ายเต็มกล่อง
    assert len(levels) == crypto.SPARK_POINTS
    assert all(0 <= v <= 15 for v in levels)
    assert levels[0] == 0 and levels[-1] == 15  # ปลายทั้งสองถูกเก็บ


def test_spark_flat_is_baseline():
    series = [100.0] * 168
    assert crypto.spark(series, now=100.0) == [7] * crypto.SPARK_POINTS


# MARK: - frame encode + squeeze


def _frame(n=1, spark=True):
    qs = [
        CryptoQuote(f"C{i}", 64230.0 + i, -2.1, [0, 5, 10, 15] * 4 if spark else [])
        for i in range(n)
    ]
    return CryptoFrame(qs)


def test_frame_encode_full():
    obj = _obj(_frame().encoded())
    assert obj["g"] == 2
    assert obj["c"][0]["s"] == "C0" and obj["c"][0]["p"] == "64,230.00" and obj["c"][0]["d"] == -21
    assert "k" in obj["c"][0]


def test_frame_squeeze_drops_spark_before_rows():
    frame = _frame(n=5)
    full = len(frame.encoded())
    obj = _obj(frame.encoded(max_bytes=full - 5))
    # ยังครบ 5 แถว แต่ sparkline หายไป (รูปถูกทิ้งก่อนแถว)
    assert len(obj["c"]) == 5
    assert all("k" not in r for r in obj["c"])


def test_frame_too_long_raises():
    try:
        _frame(n=5).encoded(max_bytes=15)
        assert False
    except CryptoError as e:
        assert str(e) == crypto.FRAME_TOO_LONG


# MARK: - parsers


def test_coin_id_first_ranked():
    data = json.dumps({"coins": [{"id": "bitcoin"}, {"id": "bitcoin-cash"}]})
    assert crypto.coin_id(data) == "bitcoin"


def test_coin_id_empty_is_no_such_coin():
    try:
        crypto.coin_id(json.dumps({"coins": []}))
        assert False
    except CryptoError as e:
        assert str(e) == crypto.NO_SUCH_COIN


def test_quotes_from():
    data = json.dumps([
        {"id": "bitcoin", "symbol": "btc", "current_price": 64230.0, "price_change_percentage_24h": -2.1}
    ])
    q = crypto.quotes_from(data)["bitcoin"]
    assert q.symbol == "BTC" and q.price == 64230.0 and q.change == -2.1


# MARK: - service


class Dispatcher:
    def __init__(self, ids: dict[str, str], quotes: list[dict]):
        self.ids = ids  # query lower -> search response
        self.quotes = quotes
        self.search_calls = 0
        self.market_calls = 0

    def __call__(self, url: str) -> bytes:
        if "search" in url:
            self.search_calls += 1
            # ดึง query ออกจาก url
            q = url.split("query=")[1]
            cid = self.ids.get(q)
            coins = [{"id": cid}] if cid else []
            return json.dumps({"coins": coins}).encode()
        self.market_calls += 1
        return json.dumps(self.quotes).encode()


def _service(fetch, coins, **kw):
    kw.setdefault("runner", lambda fn: fn())
    return CryptoService(fetch=fetch, settings=CryptoSettings(coins=coins), **kw)


def _markets(*rows):
    return [
        {"id": cid, "symbol": sym, "current_price": price, "price_change_percentage_24h": ch}
        for cid, sym, price, ch in rows
    ]


def test_service_resolves_and_orders_by_watchlist():
    d = Dispatcher(
        ids={"btc": "bitcoin", "eth": "ethereum"},
        quotes=_markets(("ethereum", "eth", 3000.0, 1.0), ("bitcoin", "btc", 64000.0, -2.0)),
    )
    got = []
    svc = _service(d, ["btc", "eth"], on_frame=lambda f, at: got.append(f))
    svc.tick(T0)
    assert len(got) == 1
    # เรียงตาม watchlist [btc, eth] ไม่ใช่ตามที่บริการคืน (eth ก่อน)
    assert [q.symbol for q in got[0].quotes] == ["BTC", "ETH"]


def test_service_caches_resolution():
    d = Dispatcher(ids={"btc": "bitcoin"}, quotes=_markets(("bitcoin", "btc", 64000.0, -2.0)))
    svc = _service(d, ["btc"], on_frame=lambda f, at: None)
    svc.tick(T0)
    svc.tick(T0 + timedelta(seconds=crypto.INTERVAL + 1))
    assert d.search_calls == 1  # แปลชื่อครั้งเดียว
    assert d.market_calls == 2


def test_service_negative_cache_no_such_coin():
    d = Dispatcher(ids={"real": "realcoin"}, quotes=_markets(("realcoin", "rc", 1.0, 0.0)))
    svc = _service(d, ["bogus", "real"], on_frame=lambda f, at: None)
    svc.tick(T0)
    svc.tick(T0 + timedelta(seconds=crypto.INTERVAL + 1))
    # "bogus" หาไม่เจอ -> จำผลลบ ไม่ยิงถามซ้ำ (2 คำ x 1 รอบแรก, รอบสองไม่ค้นเลย)
    assert d.search_calls == 2
    assert svc.status is not None and "bogus" in svc.status
