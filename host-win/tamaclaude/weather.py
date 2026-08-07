"""หน้าอากาศ: เฟรม + Open-Meteo — พอร์ตของ `Weather.swift`

เลือก Open-Meteo เป็นหน้าแรกของรอบ multi-page เพราะไม่ต้องมี API key ท่อทั้งเส้นจึงพิสูจน์ได้
โดยไม่มีตัวแปร credential ปน · ทุกตัว parse เป็นฟังก์ชันบริสุทธิ์รับ bytes — เทสต์ป้อน fixture ตรงๆ
Mac ส่ง *ข้อมูล* ไม่ส่งภาพ: รหัส WMO เดินทางดิบๆ board แปลเป็นสัญลักษณ์เอง (ADR-0004)
"""

from __future__ import annotations

import enum
import json
import math
from dataclasses import dataclass, field
from urllib.parse import urlencode

from . import text
from .pages import PageKind
from .protocol import MAX_PAYLOAD, dumps

PLACE_LIMIT = 18  # ชื่อยาวเกินความกว้างจอไม่มีประโยชน์ — วัดจาก [weather] ใน layout.toml
HOUR_LIMIT = 5  # ต้องตรงกับ [weather] fc_cols ใน layout.toml
INTERVAL = 15 * 60  # รอบดึง — อากาศเปลี่ยนช้ากว่านั้นมาก และบริการฟรีบนพื้นฐานไม่ยิงถี่


class WeatherError(Exception):
    """สิ่งที่อาจผิดพลาดระหว่างทางไป Open-Meteo"""


BAD_PAYLOAD = "the weather service sent something we cannot read"
NO_SUCH_PLACE = "no place by that name"
FRAME_TOO_LONG = "the weather page does not fit one frame"


class TempUnit(enum.Enum):
    CELSIUS = "C"
    FAHRENHEIT = "F"

    @property
    def api_name(self) -> str:
        return "celsius" if self is TempUnit.CELSIUS else "fahrenheit"


def _round(x: float) -> int:
    """ปัดครึ่งออกจากศูนย์ — ตรงกับ `Int(x.rounded())` ของ Swift · temp ติดลบได้ จึงไม่ใช่ +0.5 เฉยๆ"""
    return math.floor(x + 0.5) if x >= 0 else math.ceil(x - 0.5)


@dataclass(frozen=True)
class WeatherReading:
    temp: int
    high: int
    low: int
    code: int  # รหัส WMO ดิบ
    unit: TempUnit


@dataclass(frozen=True)
class HourlyPoint:
    temp: int
    code: int


@dataclass(frozen=True)
class GeoPlace:
    name: str
    latitude: float
    longitude: float


@dataclass
class WeatherFrame:
    """```
    {"a":420,"c":[0,1,3,61,61],"g":1,"h":34,"l":26,"n":15,
     "o":[32,33,33,31,30],"p":"Bangkok","t":31,"u":"C","w":61}
    ```"""

    place: str
    reading: WeatherReading
    age: int = 0
    # ชั่วโมงของช่องแรก (0..23) — -1 = ไม่มีพยากรณ์ · **สัมบูรณ์** ไม่ใช่ offset: ป้าย "17:00"
    # ที่นับต่อไม่ได้จะกลายเป็นคำโกหกเมื่อเฟรมค้าง ส่วนชั่วโมงสัมบูรณ์ที่ค้างยังเป็นชั่วโมงนั้นจริง
    hour_start: int = -1
    hours: list[HourlyPoint] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.hours = list(self.hours[:HOUR_LIMIT])

    @property
    def kind(self) -> PageKind:
        return PageKind.WEATHER

    @property
    def has_hours(self) -> bool:
        return 0 <= self.hour_start < 24 and bool(self.hours)

    def _encode(self, place: str, hours: list[HourlyPoint], hour_start: int) -> dict:
        out = {
            "g": int(PageKind.WEATHER),
            "a": self.age,
            "p": place,
            "t": self.reading.temp,
            "h": self.reading.high,
            "l": self.reading.low,
            "w": self.reading.code,
            "u": self.reading.unit.value,
        }
        if 0 <= hour_start < 24 and hours:
            out["n"] = hour_start
            out["o"] = [h.temp for h in hours]
            out["c"] = [h.code for h in hours]
        return out

    def encoded(self, max_bytes: int = MAX_PAYLOAD) -> bytes:
        """สองสิ่งที่บีบได้: แถบพยากรณ์ทั้งชุด แล้วจึงชื่อสถานที่ · ลำดับนี้ไม่บังเอิญ — ชื่อเมือง
        บอกว่าตัวเลขทั้งหน้าพูดถึงที่ไหน · การทิ้งพยากรณ์ทีละคอลัมน์ไม่ช่วย (สามคอลัมน์อ่านเป็น
        ข้อมูลหาย ไม่ใช่ย่อ)"""
        place = text.fit(self.place, PLACE_LIMIT)
        data = dumps(self._encode(place, self.hours, self.hour_start))
        if len(data) <= max_bytes:
            return data

        data = dumps(self._encode(place, [], -1))  # ทิ้งพยากรณ์ทั้งชุด
        if len(data) <= max_bytes:
            return data

        limit = text.display_width(place)
        while limit > 0 and len(data) > max_bytes:
            limit -= 1
            place = text.clip(place, limit)
            data = dumps(self._encode(place, [], -1))
        # ตัวเลขล้วนยังไม่พอ = เพดานที่ให้มาเล็กกว่าเฟรมที่เล็กที่สุด · เฟรมยาวเกินถูกบอร์ดทิ้ง
        # อยู่แล้ว คืนมันเงียบๆ แค่ย้ายความล้มเหลวไปให้ไกลตา
        if len(data) > max_bytes:
            raise WeatherError(FRAME_TOO_LONG)
        return data


# MARK: - Open-Meteo


def geocode_url(name: str) -> str:
    q = urlencode({"name": name, "count": "1", "format": "json"})
    return f"https://geocoding-api.open-meteo.com/v1/search?{q}"


def forecast_url(place: GeoPlace, unit: TempUnit) -> str:
    items = {
        "latitude": f"{place.latitude:.4f}",
        "longitude": f"{place.longitude:.4f}",
        "current": "temperature_2m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min",
        "forecast_days": "1",
        # แถวรายชั่วโมงเริ่มที่ชั่วโมงปัจจุบัน ดัชนี 0 จึงผ่านไปแล้วบางส่วน (current ตอบไปแล้ว)
        # ขอมา HOUR_LIMIT+1 เพื่อใช้ 1..HOUR_LIMIT
        "hourly": "temperature_2m,weather_code",
        "forecast_hours": str(HOUR_LIMIT + 1),
        "timezone": "auto",  # สูงสุด/ต่ำสุด "วันนี้" ต้องเป็นวันของสถานที่ ไม่ใช่ของ Mac
    }
    if unit is not TempUnit.CELSIUS:
        items["temperature_unit"] = unit.api_name
    return "https://api.open-meteo.com/v1/forecast?" + urlencode(items)


def _loads(data: bytes | str):
    try:
        return json.loads(data)
    except (json.JSONDecodeError, ValueError):
        return None


def place_from(data: bytes | str) -> GeoPlace:
    obj = _loads(data)
    if not isinstance(obj, dict):
        raise WeatherError(BAD_PAYLOAD)
    results = obj.get("results")
    # ไม่มีคีย์ results = "หาไม่เจอ" ตามสัญญาของบริการ ไม่ใช่ payload พัง
    if not isinstance(results, list) or not results:
        raise WeatherError(NO_SUCH_PLACE)
    first = results[0]
    lat = first.get("latitude")
    lon = first.get("longitude")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        raise WeatherError(BAD_PAYLOAD)
    return GeoPlace(name=first.get("name") or "", latitude=float(lat), longitude=float(lon))


def reading_from(data: bytes | str, unit: TempUnit) -> WeatherReading:
    obj = _loads(data)
    try:
        current = obj["current"]
        daily = obj["daily"]
        temp = current["temperature_2m"]
        code = current["weather_code"]
        high = daily["temperature_2m_max"][0]
        low = daily["temperature_2m_min"][0]
    except (TypeError, KeyError, IndexError):
        raise WeatherError(BAD_PAYLOAD)
    if not all(isinstance(v, (int, float)) for v in (temp, code, high, low)):
        raise WeatherError(BAD_PAYLOAD)
    return WeatherReading(
        temp=_round(temp), high=_round(high), low=_round(low), code=int(code), unit=unit
    )


def hourly_from(data: bytes | str) -> tuple[int, list[HourlyPoint]]:
    """ห้าชั่วโมงข้างหน้าจากคำตอบก้อนเดียวกับ reading — คืน (-1, []) เมื่อใช้ไม่ได้

    **ไม่ raise** ต่างจากทุกอย่างในไฟล์นี้ และตั้งใจ: แถบพยากรณ์เป็นส่วนเสริม · บล็อก hourly หาย
    ควรทำให้แถบล่างว่าง ไม่ใช่ทั้งหน้าหยุดอัปเดต · ชั่วโมงมาจากสตริงเวลาของบริการ (timezone=auto
    = เวลาของสถานที่นั้น) ไม่ใช่นาฬิกาของ Mac
    """
    obj = _loads(data)
    if not isinstance(obj, dict):
        return (-1, [])
    block = obj.get("hourly")
    if not isinstance(block, dict):
        return (-1, [])
    times = block.get("time")
    temps = block.get("temperature_2m")
    codes = block.get("weather_code")
    if not all(isinstance(v, list) for v in (times, temps, codes)):
        return (-1, [])
    n = min(len(times), len(temps), len(codes))
    if n <= 1:
        return (-1, [])
    start = _hour_of(times[1])  # ข้ามดัชนี 0 — ชั่วโมงปัจจุบันคือสิ่งที่เลขใหญ่บอกอยู่แล้ว
    if start is None:
        return (-1, [])
    out: list[HourlyPoint] = []
    for i in range(1, min(n, HOUR_LIMIT + 1)):
        t, c = temps[i], codes[i]
        if not isinstance(t, (int, float)) or not isinstance(c, (int, float)):
            return (-1, [])  # ช่องที่หายกลางแถบทำให้ทั้งแถบอ่านผิด ไม่ใช่แค่สั้นลง
        out.append(HourlyPoint(temp=_round(t), code=int(c)))
    return (start, out) if out else (-1, [])


def _hour_of(value) -> int | None:
    """"2026-08-03T13:00" -> 13 · None เมื่อไม่ใช่รูปนั้น"""
    if not isinstance(value, str) or len(value) < 13 or value[10] != "T":
        return None
    try:
        h = int(value[11:13])
    except ValueError:
        return None
    return h if 0 <= h < 24 else None
