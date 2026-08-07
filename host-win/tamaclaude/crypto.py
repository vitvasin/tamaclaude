"""หน้าคริปโต: เฟรม + CoinGecko — พอร์ตของ `Crypto.swift`

ไม่ต้องมี API key เหมือน Open-Meteo · Mac ดึง board ไม่เคยต่อเน็ตเอง (ADR-0001) · ทุกตัว parse
เป็นฟังก์ชันบริสุทธิ์รับ bytes — เทสต์ป้อน fixture ตรงๆ
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from urllib.parse import urlencode

from . import text
from .pages import PageKind
from .protocol import MAX_PAYLOAD, dumps

MAX_ROWS = 5
SYMBOL_LIMIT = 5  # สัญลักษณ์ยาวกว่านี้ไม่มีในตลาดจริง และคอลัมน์บนจอกว้างเท่านี้
SPARK_POINTS = 16  # ต้องเท่ากับ [crypto] spark_cols ใน layout.toml
SPARK_MAX = 15  # ระดับสูงสุดที่ nibble เดียวเก็บได้ — ตรงกับ CT_TREND_SPARK_MAX
INTERVAL = 60  # ตลาดคริปโตไม่มีเวลาปิด จึงไม่มีหน้าต่างทำการให้หยุดยิงเหมือนหุ้น


class CryptoError(Exception):
    pass


BAD_PAYLOAD = "the price service sent something we cannot read"
NO_SUCH_COIN = "no coin by that name"
FRAME_TOO_LONG = "the crypto page does not fit one frame"


def _round(x: float) -> int:
    return math.floor(x + 0.5) if x >= 0 else math.ceil(x - 0.5)


@dataclass(frozen=True)
class CryptoQuote:
    symbol: str  # สัญลักษณ์ที่บริการบอก ไม่ใช่ที่ผู้ใช้พิมพ์ — คนพิมพ์ "bitcoin" ต้องเห็น BTC
    price: float
    change: float  # เปอร์เซ็นต์ 24 ชม. บวก = ขึ้น
    spark: list[int] = field(default_factory=list)  # ระดับ 0..15 · ว่าง = ไม่มีประวัติ


def decimals(price: float) -> int:
    """จำนวนทศนิยมที่ราคาขนาดนี้ควรมี · ราคา >= 1 ได้สองตำแหน่งเสมอ · ต่ำกว่า 1 ได้ความจริงเต็ม
    (0.000008 ที่ตรึงสองตำแหน่งจะอ่านว่า 0.00 ตลอดกาล ทั้งที่เปอร์เซ็นต์ข้างมันบอกว่าขยับ)"""
    v = abs(price)
    if v >= 1:
        return 2
    if v >= 0.01:
        return 4
    return 6


def price_text(price: float, trim: int = 0) -> str:
    """`trim` ลดได้เฉพาะราคา **ต่ำกว่า 1** — สองตำแหน่งของราคาที่เหลือเป็นสัญญากับผู้ใช้ ไม่ใช่
    ที่ว่างให้ตัด · ตัวคั่นหลักพันเติมที่นี่ ไม่ใช่บนบอร์ด"""
    full = decimals(price)
    places = full if abs(price) >= 1 else max(2, full - trim)
    return text.grouped(f"{price:.{places}f}")


def spark_text(levels: list[int]) -> str | None:
    """ระดับ 0..15 -> hex nibble ต่อจุด · ว่างคืน None ไม่ใช่ "" — คีย์ที่ไม่มีประหยัดกว่าคีย์ว่าง
    และบอร์ดอ่านทั้งสองแบบเป็น "ไม่มีรูป" เหมือนกัน"""
    if not levels:
        return None
    return "".join(format(min(max(v, 0), 15), "x") for v in levels)


def spark_levels(text_val: str | None) -> list[int]:
    if text_val is None:
        return []
    out: list[int] = []
    for c in text_val:
        try:
            out.append(int(c, 16))
        except ValueError:
            return []
    return out


@dataclass
class CryptoFrame:
    """```
    {"a":42,"c":[{"d":-21,"p":"64230","s":"BTC"}],"g":2}
    ```
    เปอร์เซ็นต์เดินทางเป็นจำนวนเต็มหน่วยสิบเท่า (-2.1% -> -21) — บอร์ดไม่ต้องจัดรูปทศนิยมเอง"""

    quotes: list[CryptoQuote]
    age: int = 0

    def __post_init__(self) -> None:
        self.quotes = list(self.quotes[:MAX_ROWS])

    @property
    def kind(self) -> PageKind:
        return PageKind.CRYPTO

    def _rows(self, trim: int, count: int, spark: bool) -> list[dict]:
        rows = []
        for q in self.quotes[:count]:
            row = {
                "s": text.fit(q.symbol, SYMBOL_LIMIT),
                "p": price_text(q.price, trim),
                # ปัดครึ่งขึ้น ไม่ตัดทิ้ง — -0.04% ที่กลายเป็น 0 ต้องไม่กลายเป็น +0
                "d": _round(q.change * 10),
            }
            if spark:
                s = spark_text(q.spark)
                if s is not None:
                    row["k"] = s
            rows.append(row)
        return rows

    def _payload(self, trim: int, count: int, spark: bool) -> dict:
        return {"g": int(PageKind.CRYPTO), "a": self.age, "c": self._rows(trim, count, spark)}

    def encoded(self, max_bytes: int = MAX_PAYLOAD) -> bytes:
        """ลำดับบีบที่ *ไม่* แตะสัญลักษณ์ (สัญลักษณ์คือสิ่งเดียวที่บอกว่าตัวเลขเป็นของอะไร):
        1. ลดทศนิยมราคา **ต่ำกว่า 1** ทีละขั้น 2. ทิ้ง sparkline ทุกแถว 3. ตัดแถวท้ายทิ้งทั้งแถว
        รูปถูกทิ้งก่อนแถวเสมอ: แถวที่หาย = เหรียญหายทั้งใบ ส่วนรูปที่หาย = คำถามเดียวที่ตอบไม่ได้"""
        count = len(self.quotes)
        while True:
            for spark in (True, False):
                for trim in range(5):
                    data = dumps(self._payload(trim, count, spark))
                    if len(data) <= max_bytes:
                        return data
            if count <= 0:
                raise CryptoError(FRAME_TOO_LONG)
            count -= 1


# MARK: - CoinGecko


def search_url(query: str) -> str:
    return "https://api.coingecko.com/api/v3/search?" + urlencode({"query": query})


def markets_url(ids: list[str]) -> str | None:
    """ราคาหลายเหรียญในคำขอเดียว · ใช้ /coins/markets เพราะคืน symbol มาด้วย ป้ายบนจอจึงเป็น
    สัญลักษณ์ที่บริการรู้จัก · ขอ sparkline ในคำขอเดิม ไม่ใช่คำขอที่สองต่อเหรียญ (โควตาแผนฟรี)"""
    if not ids:
        return None
    return "https://api.coingecko.com/api/v3/coins/markets?" + urlencode(
        {
            "vs_currency": "usd",
            "ids": ",".join(ids),
            "per_page": str(len(ids)),
            "sparkline": "true",
        }
    )


def _loads(data):
    try:
        return json.loads(data)
    except (json.JSONDecodeError, ValueError):
        return None


def coin_id(data: bytes | str) -> str:
    """คำที่ผู้ใช้พิมพ์ -> id ของ CoinGecko · เอาตัวแรกที่บริการจัดอันดับ ไม่ใช่ชื่อตรงเป๊ะ
    (คนพิมพ์ "btc" ต้องได้ bitcoin ไม่ใช่เหรียญล้อเลียนที่ชื่อ BTC เป๊ะกว่า)"""
    obj = _loads(data)
    if not isinstance(obj, dict):
        raise CryptoError(BAD_PAYLOAD)
    coins = obj.get("coins")
    # ไม่มีคีย์ coins = payload พัง · ลิสต์ว่าง = หาไม่เจอ — คนละเรื่องที่บอกผู้ใช้คนละอย่าง
    if not isinstance(coins, list):
        raise CryptoError(BAD_PAYLOAD)
    if not coins or not isinstance(coins[0], dict) or not coins[0].get("id"):
        raise CryptoError(NO_SUCH_COIN)
    return coins[0]["id"]


def quotes_from(data: bytes | str) -> dict[str, CryptoQuote]:
    """ราคาที่อ่านได้ ตาม id · เหรียญที่หายจากคำตอบไม่ใช่พัง (บริการอาจเลิก list) แต่คำตอบที่ไม่ใช่
    ลิสต์เลยคือพัง"""
    lst = _loads(data)
    if not isinstance(lst, list):
        raise CryptoError(BAD_PAYLOAD)
    out: dict[str, CryptoQuote] = {}
    for item in lst:
        if not isinstance(item, dict):
            continue
        cid = item.get("id")
        symbol = item.get("symbol")
        price = item.get("current_price")
        if not (isinstance(cid, str) and isinstance(symbol, str) and isinstance(price, (int, float))):
            continue
        # เหรียญที่เพิ่งขึ้น list ยังไม่มี 24 ชม. — null = "ยังไม่ขยับเท่าที่รู้" ไม่ใช่ข้อมูลหาย
        change = item.get("price_change_percentage_24h")
        change = float(change) if isinstance(change, (int, float)) else 0.0
        series = None
        s7 = item.get("sparkline_in_7d")
        if isinstance(s7, dict):
            series = s7.get("price")
        out[cid] = CryptoQuote(
            symbol=symbol.upper(), price=float(price), change=change,
            spark=spark(series, now=float(price)),
        )
    if not out:
        raise CryptoError(BAD_PAYLOAD)
    return out


def spark(series, now: float) -> list[int]:
    """อนุกรมรายชั่วโมง 7 วัน -> ระดับ 0..15 สิบหกจุดของ *24 ชั่วโมงหลัง*

    จุดสุดท้ายแทนด้วย `now` เสมอ: อนุกรมอัปเดตช้ากว่า current_price ปล่อยไว้แท่งสุดท้ายจะไม่ตรงกับ
    เปอร์เซ็นต์ข้างมัน · quantize หลัง fold ไม่ใช่ก่อน — ระดับ 0/15 ต้องอยู่ในจุดที่วาดจริง
    """
    if not isinstance(series, list) or len(series) < SPARK_POINTS:
        return []
    day = [float(v) if isinstance(v, (int, float)) else 0.0 for v in series[-24:]]
    if any((not math.isfinite(v)) or v <= 0 for v in day):
        return []
    day[-1] = now

    cols = SPARK_POINTS
    n = len(day)
    if n <= cols:
        picked = day
    else:
        # ปัดครึ่งขึ้นด้วยเลขจำนวนเต็ม ต้องได้ดัชนีชุดเดียวกับ ct_trend_fold และ gen/trend.py:fold
        # เป๊ะ — ปลายทั้งสองต้องถูกเก็บไว้เสมอ
        picked = [day[((i * (n - 1) * 2) + (cols - 1)) // (2 * (cols - 1))] for i in range(cols)]

    lo, hi = min(picked), max(picked)
    # ราคานิ่งทั้งวัน: ทุกจุดกลางกล่อง = ไม่มีแท่งถูกวาด เหลือแต่เส้นฐาน — ภาพที่ถูก ไม่ใช่พัง
    if hi <= lo:
        return [SPARK_MAX // 2 for _ in picked]
    return [_round((v - lo) / (hi - lo) * SPARK_MAX) for v in picked]
