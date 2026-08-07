"""หน้าหุ้นฝั่ง host: ดึง Finnhub ทุก 60 วิ *เฉพาะตอนตลาดเปิด* — พอร์ต `StocksService.swift`

ยืมรูปของ crypto_service มาทั้งใบ ต่างสามข้อ: key ของผู้ใช้ · หนึ่งคำขอต่อหนึ่งสัญลักษณ์ ·
หน้าต่างเวลาทำการที่ปิดท่อไปเลย · key อ่านใหม่ทุกรอบ ไม่ค้างในหน่วยความจำข้ามรอบ
"""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import secret_file, stocks
from .paths import FINNHUB_KEY
from .stocks import StockQuote, StocksError, StocksFrame
from .weather_service import FetchSchedule, Runner, _thread_runner

Fetch = Callable[[str], bytes]
KeySource = Callable[[], str]
OnFrame = Callable[[StocksFrame, datetime], None]

MAX_SYMBOLS = 5

# key ของ Finnhub — ไฟล์ ACL แคบใต้ ~/.tamaclaude เหมือน sessionKey · ผู้ใช้สมัครแผนฟรีเอง
# มันคือโควตาของผู้ใช้และขโมยไปใช้ต่อได้ทันที กติกาจึงชุดเดียวกับ session key ไม่ใช่ที่ผ่อนลง
KEY_WORDING = secret_file.Wording(
    noun="Finnhub key",
    missing="get a free key at finnhub.io and paste it into ~/.tamaclaude/finnhub-key",
    empty="paste the key from finnhub.io into it",
)


def read_key(url: Path = FINNHUB_KEY) -> str:
    return secret_file.read(url, KEY_WORDING)


def write_key(raw: str, url: Path = FINNHUB_KEY) -> None:
    secret_file.write(raw, url, KEY_WORDING)


def key_usable(url: Path = FINNHUB_KEY) -> bool:
    try:
        read_key(url)
        return True
    except secret_file.Problem:
        return False


@dataclass
class StockSettings:
    """watchlist — สัญลักษณ์ตัวใหญ่เสมอ (ตลาดเรียกแบบนั้น และ "aapl"/"AAPL" ในลิสต์เดียวคือ
    watchlist ที่กินโควตาสองครั้ง) · เพดานห้าตัว = งบต่อรอบ (/quote คืนทีละสัญลักษณ์)"""

    symbols: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        seen: set[str] = set()
        out: list[str] = []
        for raw in self.symbols:
            name = raw.strip().upper()
            if not name or name in seen:
                continue
            seen.add(name)
            out.append(name)
            if len(out) == MAX_SYMBOLS:
                break
        self.symbols = out

    @property
    def is_usable(self) -> bool:
        return bool(self.symbols)


class StocksService:
    def __init__(
        self,
        fetch: Fetch,
        settings: StockSettings | None = None,
        interval: float = stocks.INTERVAL,
        key: KeySource = read_key,
        on_frame: OnFrame | None = None,
        runner: Runner = _thread_runner,
        clock: Callable[[], datetime] = datetime.now,
    ):
        self.settings = settings or StockSettings()
        self._schedule = FetchSchedule(interval)
        self._fetch = fetch
        self._key = key
        self._runner = runner
        self._clock = clock
        self.on_frame = on_frame
        self.status: str | None = None
        self.key_rejected = False
        self._unknown: set[str] = set()  # สัญลักษณ์ที่ถามแล้วไม่มีจริง — ไม่ถามซ้ำ
        self._last: list[StockQuote] = []  # ราคาชุดล่าสุด — ใช้ตอนตลาดปิด
        self._told_closed = False
        self._observed_at: datetime | None = None
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        return self._schedule.running

    def update(self, nxt: StockSettings) -> None:
        if nxt == self.settings:
            return
        self.settings = nxt
        self.status = None
        # ลืมสัญลักษณ์ที่เคยหาไม่เจอ + ราคาชุดเก่า (เป็นของ watchlist ที่ไม่มีแล้ว) แล้วยิงรอบใหม่
        self._unknown.clear()
        self._last.clear()
        self._told_closed = False
        self._schedule.invalidate()

    def key_was_set(self) -> None:
        self.key_rejected = False
        self.status = None
        self._schedule.invalidate()

    def restart(self) -> None:
        self.status = None
        self._told_closed = False
        self._schedule.invalidate()

    def tick(self, now: datetime | None = None) -> None:
        now = now or self._clock()
        if not self.settings.is_usable:
            return
        # key ที่ถูกปฏิเสธจะถูกปฏิเสธเหมือนเดิมทุกรอบ — ยิงต่อคือเผาโควตาเพื่อคำตอบที่รู้แล้ว
        if self.key_rejected:
            return
        if not stocks.is_open(now):
            self._closed(now)
            return
        self._told_closed = False
        if not self._schedule.start(now):
            return
        self._runner(lambda: self._round(now))

    def _closed(self, now: datetime) -> None:
        # ยังไม่เคยมีตัวเลข (เปิดเครื่องคืนเสาร์) — ยิงหนึ่งรอบ /quote คืนราคาปิดครั้งล่าสุดอยู่แล้ว
        if not self._last:
            if not self._schedule.start(now):
                return
            self._runner(lambda: self._round(now))
            return
        if self._told_closed:
            return
        self._told_closed = True
        self.status = stocks.CLOSED_MESSAGE
        # อายุนับต่อจากตอนที่ราคาถูกอ่านมาจริง ไม่ใช่ตอนนี้ — observed_at จึงเป็นเวลาเดิม
        if self.on_frame is not None:
            self.on_frame(StocksFrame(list(self._last), market_closed=True), self._observed_at or now)

    def _round(self, now: datetime) -> None:
        try:
            token = self._key()
        except secret_file.Problem as p:
            self._finish(None, p.message, now)
            return
        collected: dict[str, StockQuote] = {}
        for symbol in self.settings.symbols:
            if symbol in self._unknown:
                continue
            url = stocks.quote_url(symbol, token)
            if url is None:
                self._finish(None, f"could not ask about {symbol}", now)
                return
            try:
                collected[symbol] = stocks.quote(symbol, self._fetch(url))
            except StocksError as e:
                msg = str(e)
                if msg == stocks.NO_SUCH_SYMBOL:
                    self._unknown.add(symbol)  # จำไว้แล้วเดินต่อ ไม่ให้ตัวเดียวกันทั้ง watchlist
                    continue
                if msg == stocks.KEY_REJECTED:
                    self.key_rejected = True  # ถูกปฏิเสธทั้งห้าคำขอเหมือนกัน — หยุดทั้งรอบ
                    self._finish(None, stocks.KEY_REJECTED, now)
                    return
                self._finish(None, msg, now)
                return
            except (urllib.error.URLError, OSError) as e:
                self._finish(None, _explain(e), now)
                return
        self._deliver(collected, now)

    def _deliver(self, quotes: dict[str, StockQuote], now: datetime) -> None:
        rows = [quotes[s] for s in self.settings.symbols if s in quotes]
        if not rows:
            self._finish(None, self._unlisted(set()) or "the quote service knows none of these symbols", now)
            return
        closed = not stocks.is_open(now)
        with self._lock:
            self._last = rows
            self._observed_at = now
            self._schedule.finished(True)
            self._told_closed = closed
            self.status = self._unlisted({q.symbol for q in rows}) or (
                stocks.CLOSED_MESSAGE if closed else None
            )
        if self.on_frame is not None:
            self.on_frame(StocksFrame(rows, market_closed=closed), now)

    def _unlisted(self, shown: set[str]) -> str | None:
        lost = [s for s in self.settings.symbols if s not in shown]
        return f"not showing: {', '.join(lost)}" if lost else None

    def _finish(self, frame: StocksFrame | None, problem: str | None, now: datetime) -> None:
        with self._lock:
            self._schedule.finished(frame is not None)
            self.status = problem
        if frame is not None and self.on_frame is not None:
            self._observed_at = now
            self.on_frame(frame, now)


def _explain(error: Exception) -> str:
    if isinstance(error, secret_file.Problem):
        return error.message
    return str(error) if str(error) else error.__class__.__name__


def urllib_fetch(timeout: float = 15) -> Fetch:
    """แปลรหัสสถานะที่บอกว่า *key* ผิด ให้ต่างจากรหัสที่บอกว่า *คำขอ* ผิด · URL พก key อยู่ใน
    query — ห้าม log ทั้งก้อน"""

    def fetch(url: str) -> bytes:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                if not (200 <= resp.status < 300):
                    raise StocksError(stocks.BAD_PAYLOAD)
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise StocksError(stocks.KEY_REJECTED)
            if e.code == 429:
                raise StocksError(stocks.RATE_LIMITED)
            raise StocksError(stocks.BAD_PAYLOAD)

    return fetch
