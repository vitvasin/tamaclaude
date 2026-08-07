"""หน้าหุ้น: เฟรม + Finnhub — พอร์ตของ `Stocks.swift`

เหมือนหน้าคริปโตทุกอย่าง บวกสองคีย์: `k` = ตลาดปิดอยู่ (เหตุที่ตัวเลขไม่ขยับ อายุที่โตขึ้นไม่ได้
แปลว่าท่อพัง) และ `r` = ช่วงราคาของวันของแถวแรก ซึ่งวาดแทน sparkline ของคริปโต · ต่างจาก
CoinGecko สองข้อ: ต้องมี key ของผู้ใช้ และ /quote คืนทีละสัญลักษณ์ (เหตุของเพดานห้าตัว = งบต่อรอบ)
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse
from zoneinfo import ZoneInfo

from . import text
from .pages import PageKind
from .protocol import MAX_PAYLOAD, dumps

MAX_ROWS = 5
SYMBOL_LIMIT = 5  # สัญลักษณ์สหรัฐยาวสุดห้าตัว ("BRK.B")
INTERVAL = 60  # 60 วิ *เฉพาะตอนตลาดเปิด*


class StocksError(Exception):
    pass


BAD_PAYLOAD = "the quote service sent something we cannot read"
NO_SUCH_SYMBOL = "no symbol by that name"
KEY_REJECTED = "Finnhub refused the key — set a new one in the Stocks tab"
RATE_LIMITED = "Finnhub is rate limiting us; the next round will try again"
FRAME_TOO_LONG = "the stocks page does not fit one frame"
CLOSED_MESSAGE = "The market is closed; these figures are the last of the session."


def _round(x: float) -> int:
    return math.floor(x + 0.5) if x >= 0 else math.ceil(x - 0.5)


@dataclass(frozen=True)
class StockQuote:
    symbol: str
    price: float
    change: float  # เปอร์เซ็นต์จากราคาปิดครั้งก่อน บวก = ขึ้น
    low: float = 0  # ต่ำสุด/สูงสุดวันนี้ · ศูนย์ทั้งคู่ = "ไม่ได้ให้มา" (ก่อนตลาดเปิด) ไม่ใช่ราคาศูนย์
    high: float = 0


def _decimals() -> int:
    return 2  # หุ้นซื้อขายกันจริงที่สองตำแหน่ง — ไม่มีขั้นหกตำแหน่งแบบคริปโต


def price_text(price: float, trim: int = 0) -> str:
    # trim ไม่มีผล (ทศนิยมตรึงสองตำแหน่ง) แต่คงพารามิเตอร์ไว้เพราะลูปบีบเรียกเป็นขั้นแรก
    return text.grouped(f"{price:.{_decimals()}f}")


def _range_decimals(high: float) -> int:
    """ทศนิยมของ *ปลายช่วง* หยาบกว่าราคาโดยตั้งใจ — ต้องสั้นพอยืนคู่ HIGH ในบล็อก 84px
    ("1,204" พอดี "1,204.55" ไม่พอดี) · ต่ำกว่า 100 ยังต้องมีหนึ่งตำแหน่ง"""
    return 0 if high >= 100 else 1


def range_text(value: float, high: float, up: bool) -> str:
    """ปลายช่วงหนึ่งด้าน — ปัด *ออกนอก* ช่วงเสมอ (up=ปัดขึ้น) ไม่ใช่ปัดใกล้สุด: ราคา 189.44 ที่
    แตะจุดสูงสุดพอดี ต้องไม่ได้ป้าย HIGH ว่า "189" แล้วดูล้นช่วงของตัวเอง"""
    places = _range_decimals(high)
    scale = 10 ** places
    rounded = (math.ceil(value * scale) if up else math.floor(value * scale)) / scale
    return text.grouped(f"{rounded:.{places}f}")


@dataclass
class StocksFrame:
    """```
    {"a":42,"c":[{"d":-21,"p":"189.44","s":"AAPL"}],"g":4,"k":1,"r":{"h":"193","l":"184","t":60}}
    ```"""

    quotes: list[StockQuote]
    age: int = 0
    market_closed: bool = False

    def __post_init__(self) -> None:
        self.quotes = list(self.quotes[:MAX_ROWS])

    @property
    def kind(self) -> PageKind:
        return PageKind.STOCKS

    def day_range(self) -> dict | None:
        """ช่วงราคาของแถวแรก — None เมื่อ high<=low (Finnhub ยังไม่ให้ช่วง หรือยังไม่ซื้อขายวันนี้)"""
        if not self.quotes:
            return None
        q = self.quotes[0]
        if q.high <= q.low:
            return None
        t = (q.price - q.low) / (q.high - q.low) * 100
        return {
            "l": range_text(q.low, q.high, up=False),
            "h": range_text(q.high, q.high, up=True),
            "t": min(max(_round(t), 0), 100),  # ก่อนปัดปลายช่วง หมุดจึงอยู่ในรางเสมอ
        }

    def _rows(self, percent: bool, count: int) -> list[dict]:
        rows = []
        for q in self.quotes[:count]:
            row = {"s": text.fit(q.symbol, SYMBOL_LIMIT), "p": price_text(q.price)}
            if percent:
                row["d"] = _round(q.change * 10)  # ปัดครึ่งขึ้น — -0.04% ที่เป็น 0 ต้องไม่เป็น +0
            rows.append(row)
        return rows

    def _payload(self, percent: bool, has_range: bool, count: int) -> dict:
        out = {"g": int(PageKind.STOCKS), "a": self.age, "c": self._rows(percent, count)}
        if self.market_closed:  # ตลาดเปิดคือปกติ = "ไม่มีคีย์" ไม่ใช่ "k":0
            out["k"] = 1
        if has_range and count > 0:
            r = self.day_range()
            if r is not None:
                out["r"] = r
        return out

    def encoded(self, max_bytes: int = MAX_PAYLOAD) -> bytes:
        """ลำดับบีบที่ *ไม่* แตะสัญลักษณ์: 1. ทิ้งช่วงราคา (r, บริบทของแถวเดียว) 2. (ลดทศนิยม—
        no-op สำหรับหุ้น) 3. ทิ้งเปอร์เซ็นต์ทั้งคอลัมน์ 4. ตัดแถวท้ายทิ้ง"""
        count = len(self.quotes)
        while True:
            for percent in (True, False):
                for has_range in (True, False):
                    data = dumps(self._payload(percent, has_range, count))
                    if len(data) <= max_bytes:
                        return data
            if count <= 0:
                raise StocksError(FRAME_TOO_LONG)
            count -= 1


# MARK: - เวลาทำการของตลาดสหรัฐ

_EXCHANGE = ZoneInfo("America/New_York")
_OPEN = 9 * 60 + 30
_CLOSE = 16 * 60


def is_open(dt: datetime) -> bool:
    """09:30–16:00 จันทร์–ศุกร์ เวลาตลาด · dt ไม่มี tz ถือเป็นเวลาเครื่อง แล้วแปลงเป็นเวลาตลาด
    (naive.astimezone ถือว่าเป็น local) · วันหยุดตลาดไม่ได้จำลองไว้โดยรู้ตัว (ดู Stocks.swift)"""
    ny = dt.astimezone(_EXCHANGE)
    if ny.weekday() >= 5:  # 5=เสาร์ 6=อาทิตย์
        return False
    at = ny.hour * 60 + ny.minute
    return _OPEN <= at < _CLOSE


# MARK: - Finnhub


def quote_url(symbol: str, token: str) -> str | None:
    """key เดินทางเป็น query parameter (Finnhub รับแบบนั้น) — URL ก้อนนี้จึงเป็นความลับ
    ห้าม log ทั้งก้อน ใช้ `describe` แทน"""
    name = symbol.strip().upper()
    if not name or not token:
        return None
    return "https://finnhub.io/api/v1/quote?" + urlencode({"symbol": name, "token": token})


def describe(url: str) -> str:
    """ที่อยู่ก้อนเดียวกันโดยไม่มี key — สิ่งเดียวที่เอาไปเขียน log ได้"""
    parts = urlparse(url)
    kept = [(k, v) for k, v in parse_qsl(parts.query) if k != "token"]
    return urlunparse(parts._replace(query=urlencode(kept)))


def quote(symbol: str, data: bytes | str) -> StockQuote:
    """คำตอบ /quote หนึ่งก้อน -> ราคา · สัญลักษณ์ที่ไม่รู้จักตอบเลขศูนย์ทั้งก้อน (ไม่ใช่ 404) —
    ราคาศูนย์พร้อมราคาปิดก่อนศูนย์คือหุ้นที่ไม่มีอยู่ ไม่ใช่หุ้นที่ราคาตก"""
    try:
        obj = json.loads(data)
    except (json.JSONDecodeError, ValueError):
        raise StocksError(BAD_PAYLOAD)
    if not isinstance(obj, dict) or not isinstance(obj.get("c"), (int, float)):
        raise StocksError(BAD_PAYLOAD)
    price = float(obj["c"])
    previous = obj.get("pc")
    previous = float(previous) if isinstance(previous, (int, float)) else 0.0
    if price <= 0 and previous <= 0:
        raise StocksError(NO_SUCH_SYMBOL)
    change = obj.get("dp")  # null ได้ตอนไม่มีราคาปิดก่อนให้เทียบ (IPO วันนี้)
    change = float(change) if isinstance(change, (int, float)) else 0.0
    high = obj.get("h")
    low = obj.get("l")
    high = float(high) if isinstance(high, (int, float)) else 0.0
    low = float(low) if isinstance(low, (int, float)) else 0.0
    return StockQuote(symbol=symbol.strip().upper(), price=price, change=change, low=low, high=high)
