"""หน้าอากาศฝั่ง host: ดึง Open-Meteo ตามรอบ แล้ววาง page frame ให้ PageHub — พอร์ต
`WeatherService.swift`

การยิงจริงเข้ามาทาง callable (`fetch`) — เทสต์ป้อน fixture แทนได้ ไม่มีเทสต์ตัวไหนแตะเครือข่าย ·
งานยิงรันใน runner แยก (ดีฟอลต์เป็น thread) เพื่อไม่บล็อกลูป tick วินาทีละครั้งของ daemon;
เทสต์ส่ง runner แบบรันทันทีเข้ามาให้ผลนิ่ง
"""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from . import weather
from .weather import GeoPlace, TempUnit, WeatherError, WeatherFrame

Fetch = Callable[[str], bytes]
Runner = Callable[[Callable[[], None]], None]
OnFrame = Callable[[WeatherFrame, datetime], None]


@dataclass
class WeatherSettings:
    """ชื่อเมืองที่ผู้ใช้พิมพ์ + หน่วย · "เปิด/ปิดหน้านี้" ไม่ได้อยู่ที่นี่ — เป็นข้อเดียวกับทุกหน้า
    อยู่ใน page plan ที่เดียว"""

    place: str = ""
    unit: TempUnit = TempUnit.CELSIUS

    @property
    def is_usable(self) -> bool:
        return bool(self.place.strip())


class FetchSchedule:
    """เมื่อไรถึงยิงรอบถัดไป — ตรรกะล้วน · รอบที่ล้มไม่รอเต็มรอบ (retry) แต่ก็ไม่ยิงรัว"""

    def __init__(self, interval: float, retry: float = 60):
        self.interval = interval
        self.retry = retry
        self.running = False
        self.last_started: datetime | None = None
        self.last_failed = False

    def start(self, now: datetime) -> bool:
        """ถึงเวลายิงหรือยัง — คืน True แค่ครั้งเดียวต่อรอบ และทำเครื่องหมายว่าเริ่มแล้ว"""
        if self.running:
            return False
        wait = self.retry if self.last_failed else self.interval
        if self.last_started is not None and (now - self.last_started).total_seconds() < wait:
            return False
        self.running = True
        self.last_started = now
        return True

    def finished(self, ok: bool) -> None:
        self.running = False
        self.last_failed = not ok

    def invalidate(self) -> None:
        """ค่าตั้งเปลี่ยน — รอบถัดไปต้องเกิดเดี๋ยวนี้ ไม่ใช่เมื่อครบ interval นับจากรอบก่อน"""
        self.last_started = None
        self.last_failed = False


def _thread_runner(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, daemon=True).start()


class WeatherService:
    def __init__(
        self,
        fetch: Fetch,
        settings: WeatherSettings | None = None,
        interval: float = weather.INTERVAL,
        on_frame: OnFrame | None = None,
        runner: Runner = _thread_runner,
        clock: Callable[[], datetime] = datetime.now,
    ):
        self.settings = settings or WeatherSettings()
        self._schedule = FetchSchedule(interval)
        self._fetch = fetch
        self._runner = runner
        self._clock = clock
        self.on_frame = on_frame
        self.status: str | None = None
        # พิกัดของชื่อเมือง — แปลครั้งเดียวแล้วจำไว้จนชื่อเปลี่ยน (เมืองไม่ย้ายที่ การยิง
        # geocoding ทุก 15 นาทีคือคำขอที่ไม่มีใครต้องการ)
        self._located: tuple[str, GeoPlace] | None = None
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._schedule.running

    def update(self, nxt: WeatherSettings) -> None:
        """ผู้ใช้เปลี่ยนค่าตั้ง — เมือง/หน่วยที่เปลี่ยนทำให้ของที่ถืออยู่หมดความหมายทันที"""
        changed = nxt.place != self.settings.place or nxt.unit != self.settings.unit
        self.settings = nxt
        if not changed:
            return
        if self._located is None or nxt.place != self._located[0]:
            self._located = None
        self.status = None
        self._schedule.invalidate()

    def restart(self) -> None:
        """หน้าเพิ่งถูกเปิดกลับ — เฟรมเก่าถูกบอร์ดลืมไปตอนปิด (ADR-0002) ไม่งั้นเห็นหน้าเปล่า
        ได้นานถึง interval หลังกดสวิตช์"""
        self.status = None
        self._schedule.invalidate()

    def tick(self, now: datetime | None = None) -> None:
        now = now or self._clock()
        if not self.settings.is_usable:
            return
        if not self._schedule.start(now):
            return
        self._runner(lambda: self._step(now))

    def _step(self, now: datetime) -> None:
        query = self.settings.place.strip()
        try:
            if self._located is not None and self._located[0] == query:
                place = self._located[1]
            else:
                place = weather.place_from(self._fetch(weather.geocode_url(query)))
                self._located = (query, place)
            self._forecast(place)
        except (WeatherError, urllib.error.URLError, OSError) as e:
            self._finish(None, _explain(e))

    def _forecast(self, place: GeoPlace) -> None:
        unit = self.settings.unit
        payload = self._fetch(weather.forecast_url(place, unit))
        # แถบพยากรณ์อ่านจาก payload ก้อนเดียวกัน และอ่านไม่ได้ก็ไม่ล้มทั้งรอบ
        try:
            reading = weather.reading_from(payload, unit)
        except (WeatherError, ValueError) as e:
            self._finish(None, _explain(e))
            return
        hour_start, hours = weather.hourly_from(payload)
        # ป้ายบนจอเป็นชื่อที่ *บริการ* คืนมา ไม่ใช่ที่ผู้ใช้พิมพ์ — คนพิมพ์ "bkk" ต้องเห็นเมืองจริง
        label = place.name or self.settings.place
        self._finish(
            WeatherFrame(place=label, reading=reading, hour_start=hour_start, hours=hours), None
        )

    def _finish(self, frame: WeatherFrame | None, problem: str | None) -> None:
        with self._lock:
            self._schedule.finished(frame is not None)
            self.status = problem
        if frame is not None and self.on_frame is not None:
            self.on_frame(frame, self._clock())


def _explain(error: Exception) -> str:
    return str(error) if str(error) else error.__class__.__name__


def urllib_fetch(timeout: float = 15) -> Fetch:
    """ตัวยิงจริง — บางจนไม่มีตรรกะให้เทสต์ · non-2xx/พัง = โยน WeatherError"""

    def fetch(url: str) -> bytes:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                if not (200 <= resp.status < 300):
                    raise WeatherError(weather.BAD_PAYLOAD)
                return resp.read()
        except urllib.error.HTTPError:
            raise WeatherError(weather.BAD_PAYLOAD)

    return fetch
