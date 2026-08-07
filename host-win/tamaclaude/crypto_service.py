"""หน้าคริปโตฝั่ง host: ดึง CoinGecko ทุก 60 วิ แล้ววาง page frame ให้ PageHub — พอร์ต
`CryptoService.swift`

รูปร่างเดียวกับ weather_service ทุกส่วน · ใบนี้เป็นตัวตั้งรูปของ "หน้าที่มี watchlist" ซึ่งหน้าหุ้น
จะยืมไปใช้ต่อ · แปลชื่อ->id ครั้งเดียวแล้วจำ (รวมผลลบ) ไม่งั้นคำพิมพ์ผิดถูกยิงถามใหม่ทุก 60 วิ
"""

from __future__ import annotations

import threading
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from . import crypto
from .crypto import CryptoError, CryptoFrame, CryptoQuote
from .weather_service import FetchSchedule, Runner, _thread_runner

Fetch = Callable[[str], bytes]
OnFrame = Callable[[CryptoFrame, datetime], None]

MAX_COINS = 5


@dataclass
class CryptoSettings:
    """watchlist — คำที่ผู้ใช้พิมพ์ ("btc"/"bitcoin") ยังไม่ใช่ id · เพดานห้าตัวบังคับที่นี่
    (ทำให้เฟรมพอดี 500 ไบต์แน่นอน และจำนวนคำขอต่อรอบเป็นค่าคงที่)"""

    coins: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        seen: set[str] = set()
        out: list[str] = []
        for raw in self.coins:
            name = raw.strip()
            key = name.lower()
            if not name or key in seen:
                continue
            seen.add(key)
            out.append(name)
            if len(out) == MAX_COINS:
                break
        self.coins = out

    @property
    def is_usable(self) -> bool:
        return bool(self.coins)


class CryptoService:
    def __init__(
        self,
        fetch: Fetch,
        settings: CryptoSettings | None = None,
        interval: float = crypto.INTERVAL,
        on_frame: OnFrame | None = None,
        runner: Runner = _thread_runner,
        clock: Callable[[], datetime] = datetime.now,
    ):
        self.settings = settings or CryptoSettings()
        self._schedule = FetchSchedule(interval)
        self._fetch = fetch
        self._runner = runner
        self._clock = clock
        self.on_frame = on_frame
        self.status: str | None = None
        # คำที่ผู้ใช้พิมพ์ -> id · "" = "ถามแล้ว ไม่มีเหรียญนี้" (จำผลลบด้วย ไม่งั้นยิงซ้ำทุกรอบ)
        self._resolved: dict[str, str] = {}
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._schedule.running

    def update(self, nxt: CryptoSettings) -> None:
        if nxt == self.settings:
            return
        self.settings = nxt
        self.status = None
        # ผลการแปลงชื่อไม่ล้าง: "btc" ยังเป็น bitcoin การล้างคือยิงถามซ้ำเรื่องที่รู้คำตอบแล้ว
        self._schedule.invalidate()

    def restart(self) -> None:
        self.status = None
        self._schedule.invalidate()

    def tick(self, now: datetime | None = None) -> None:
        now = now or self._clock()
        if not self.settings.is_usable:
            return
        if not self._schedule.start(now):
            return
        self._runner(self._run)

    def _run(self) -> None:
        # แปลชื่อที่ยังไม่รู้ id ให้ครบก่อน แล้วค่อยถามราคาทีเดียว (คำขอเดียวสำหรับทั้ง watchlist)
        for name in self.settings.coins:
            key = name.lower()
            if key in self._resolved:
                continue
            try:
                self._resolved[key] = crypto.coin_id(self._fetch(crypto.search_url(name)))
            except CryptoError as e:
                # "ไม่มีเหรียญนี้" คือคำตอบที่จบแล้ว — จำไว้แล้วเดินต่อ ไม่งั้นคำพิมพ์ผิดหนึ่งคำ
                # กันทั้ง watchlist ไว้ตลอดกาล
                if str(e) == crypto.NO_SUCH_COIN:
                    self._resolved[key] = ""
                    continue
                self._finish(None, str(e))
                return
            except (urllib.error.URLError, OSError) as e:
                self._finish(None, _explain(e))
                return
        self._prices()

    def _prices(self) -> None:
        ids = [self._resolved[n.lower()] for n in self.settings.coins if self._resolved.get(n.lower())]
        url = crypto.markets_url(ids)
        if url is None:
            # ทุกคำหาไม่เจอเลย — ไม่ใช่ความล้มเหลว และไม่มีอะไรให้ยิง
            with self._lock:
                self._schedule.finished(True)
                self.status = self._unlisted(set())
            return
        try:
            by_id = crypto.quotes_from(self._fetch(url))
        except (CryptoError, urllib.error.URLError, OSError) as e:
            self._finish(None, _explain(e))
            return
        # เรียงตาม watchlist ของผู้ใช้ ไม่ใช่ตามที่บริการคืน — ลำดับที่เขาจัดคือลำดับที่เขากวาดตาหา
        quotes: list[CryptoQuote] = []
        for name in self.settings.coins:
            cid = self._resolved.get(name.lower())
            if cid and cid in by_id:
                quotes.append(by_id[cid])
        if not quotes:
            self._finish(None, "the price service knows none of these coins")
            return
        self._finish(CryptoFrame(quotes=quotes), self._unlisted(set(by_id.keys())))

    def _unlisted(self, shown: set[str]) -> str | None:
        """เหรียญที่ผู้ใช้ใส่ไว้แต่ไม่ได้เห็น — คำนวณใหม่ทุกรอบ ไม่สะสมข้อความ (ข้อความค้างจะบอกว่า
        หน้าพังทั้งที่จอแสดงราคาสดอยู่)"""
        lost = []
        for name in self.settings.coins:
            cid = self._resolved.get(name.lower())
            if cid == "":
                lost.append(name)
            elif cid and cid not in shown:
                lost.append(name)
        return f"not showing: {', '.join(lost)}" if lost else None

    def _finish(self, frame: CryptoFrame | None, problem: str | None) -> None:
        with self._lock:
            self._schedule.finished(frame is not None)
            # เขียนทับเสมอ รวมทั้งด้วย None — รอบที่สำเร็จต้องลบคำบ่นของรอบก่อน
            self.status = problem
        if frame is not None and self.on_frame is not None:
            self.on_frame(frame, self._clock())


def _explain(error: Exception) -> str:
    return str(error) if str(error) else error.__class__.__name__


def urllib_fetch(timeout: float = 15) -> Fetch:
    import urllib.request

    def fetch(url: str) -> bytes:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                if not (200 <= resp.status < 300):
                    raise CryptoError(crypto.BAD_PAYLOAD)
                return resp.read()
        except urllib.error.HTTPError:
            raise CryptoError(crypto.BAD_PAYLOAD)

    return fetch
