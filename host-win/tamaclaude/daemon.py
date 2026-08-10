"""ประกอบทุกอย่างเข้าด้วยกัน + tick วินาทีละครั้ง — พอร์ตของ `Daemon.swift`"""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from . import usage_reader
from .crypto_service import CryptoService, CryptoSettings
from .crypto_service import urllib_fetch as crypto_fetch
from .ipc import HookServer
from .pages import PageHub, PageKind, PagePlan
from .paths import CRYPTO_CONFIG, STOCKS_CONFIG, TOOLS_JSON, WEATHER_CONFIG
from .process_tree import ProcessHandle, is_alive
from .protocol import HookEvent
from .session_store import SessionStore
from .stocks_service import StocksService, StockSettings, read_key
from .stocks_service import urllib_fetch as stocks_fetch
from .tool_map import ToolMap
from .weather import TempUnit
from .weather_service import WeatherService, WeatherSettings, urllib_fetch
from .wifi_provisioning import BoardEvent, NetworkList, WiFiCommand, WiFiStatus

TICK = 1.0


@dataclass(frozen=True)
class DaemonUIState:
    """สแนปช็อตข้ามเธรดสำหรับ UI — คัดลอกออกมาแล้ว ไม่ถือ reference ที่ tick แก้ต่อได้

    ยกเว้น `networks`/`wifi_status` ที่เป็น object เดียวกับ daemon — หน้าตั้งค่าอ่านตอนวาด
    เท่านั้นและ daemon แก้มันใต้ lock จึงยอมรับความเสี่ยงนั้นเพื่อไม่ต้อง deep-copy ทุกวินาที
    """

    usage: list | None
    snapshot: object | None
    connected: bool
    networks: NetworkList
    wifi_status: WiFiStatus | None


class Daemon:
    def __init__(
        self,
        use_ble: bool = True,
        echo: bool = False,
        verbose: bool = False,
        use_poll: bool = True,
        use_pages: bool = True,
    ):
        self.store = SessionStore(tool_map=ToolMap.load_or_default(TOOLS_JSON))
        # เจ้าของ session มาถึงเป็น dict บนสาย — แปลงกลับเป็น handle ตรงจุดที่ใช้จริง
        self.store.is_process_alive = lambda owner: _alive(owner)
        self.echo = echo
        self.verbose = verbose
        # ตัวยิงโควตา: spawn `--usage-poll` เป็นลูก ตามรอบที่ตั้งไว้ · ไม่มี key ก็ไม่ยิง
        # (can_poll ถาม has_key เอง) จึงปล่อยให้ tick ไปเรื่อยๆ ได้โดยไม่เผาโปรเซส
        self._poller = None
        if use_poll:
            from .usage_poller import UsagePoller, subprocess_launcher

            self._poller = UsagePoller(launch=subprocess_launcher())

        # หน้าอื่นนอกจากมาสคอต: hub เก็บเฟรมล่าสุดของแต่ละหน้าและตัดสินว่าอันไหนควรส่งซ้ำ
        # weather service ดึง Open-Meteo ตามรอบแล้ววางเฟรมไว้ที่ hub · ค่าตั้งอ่านจากไฟล์
        # (Windows ไม่มี GUI แบบ macOS) — ไม่มีเมือง = ไม่ยิง หน้าอากาศเป็นแค่ช่องว่างในรอบ
        self._hub = None
        self._weather = None
        self._crypto = None
        self._stocks = None
        if use_pages:
            self._hub = PageHub()
            place, unit = _weather_config()
            self._weather = WeatherService(
                fetch=urllib_fetch(),
                settings=WeatherSettings(place=place, unit=unit),
                on_frame=self._on_weather_frame,
            )
            coins = _crypto_config()
            self._crypto = CryptoService(
                fetch=crypto_fetch(),
                settings=CryptoSettings(coins=coins),
                on_frame=self._on_crypto_frame,
            )
            symbols = _stocks_config()
            self._stocks = StocksService(
                fetch=stocks_fetch(),
                settings=StockSettings(symbols=symbols),
                key=read_key,
                on_frame=self._on_stocks_frame,
            )
            # แผนรอบหมุน: มาสคอต + หน้าที่ตั้งค่าไว้ · ค่าเริ่มตรงกับ [rotation] ใน layout.toml
            # (rotation 20, hold 300) เพื่อให้ตรงกับที่บอร์ดใช้ก่อนได้รับแผน
            order = [PageKind.MASCOT]
            if place.strip():
                order.append(PageKind.WEATHER)
            if coins:
                order.append(PageKind.CRYPTO)
            if symbols:
                order.append(PageKind.STOCKS)
            self._hub.submit_plan(
                PagePlan(order=order, auto_turn=True, rotation=20, hold=300, attention_jump=True)
            )
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._server = HookServer(self._on_event)
        # สถานะ Wi-Fi ที่หน้าตั้งค่า (Phase 6) อ่าน — ลิสต์เครือข่าย + สถานะล่าสุดของบอร์ด
        # อัปเดตจาก BoardEvent ทุกครั้งที่บอร์ดแจ้งเข้ามา · UI ฟังผ่าน on_wifi callback
        self.networks = NetworkList()
        self.wifi_status: WiFiStatus | None = None
        self.on_wifi: Callable[[], None] | None = None
        # ค่าล่าสุดที่ tick คำนวณไว้ ให้ UI (Phase 6) อ่านข้ามเธรดได้โดยไม่ต้องคำนวณซ้ำ —
        # tray timer เดินบนเธรด Qt ส่วน tick เดินบนเธรด daemon จึงอ่านใต้ _lock เดียวกัน
        self._latest_usage: list | None = None
        self._latest_snapshot = None
        self._transport = None
        if use_ble:
            from .ble import BleTransport

            self._transport = BleTransport(
                on_event=self._on_board_event, on_link=self._on_link
            )

    # MARK: - เข้า

    def _on_event(self, event: HookEvent) -> None:
        with self._lock:
            self.store.apply(event, datetime.now())
        if self.verbose:
            print(f"[hook] {event.hook_event_name} {event.session_id[:8]} "
                  f"{event.tool_name or ''}".rstrip(), file=sys.stderr)

    def _on_board_event(self, data: bytes) -> None:
        if self.verbose:
            print(f"[board] {data!r}", file=sys.stderr)
        event = BoardEvent.decode(data)
        if event is None:
            return  # firmware รุ่นใหม่กว่าที่เราไม่รู้จัก — ต้องไม่ทำให้ host พัง

        # cap: บอร์ดประกาศหน้าที่มันรู้จักตอน subscribe CHR_EVENT ({"t":"cap","p":[0,1,..]})
        # announce() ล้างสถานะที่ส่งไปแล้วในตัว จึงครอบคลุมทั้งบอร์ดใหม่และการต่อกลับ
        if event.capability is not None:
            if self._hub is None:
                return
            with self._lock:
                self._hub.announce(event.capability)
            if self.verbose:
                names = [k.name for k in event.capability]
                print(f"[pages] board knows {names}", file=sys.stderr)
            return

        # ap / ap_end / wifi: เลี้ยงลิสต์เครือข่าย + สถานะให้หน้าตั้งค่า แล้วปลุก UI
        with self._lock:
            self.networks.apply(event)
            if event.wifi is not None:
                self.wifi_status = event.wifi
        if self.on_wifi is not None:
            self.on_wifi()

    def _on_link(self, up: bool) -> None:
        # ลิงก์ขาด: สปินเนอร์สแกนที่ค้างคือคำโกหก — ล้างทิ้งแล้วปลุก UI ให้วาดใหม่
        if not up:
            with self._lock:
                self.networks.link_lost()
            if self.on_wifi is not None:
                self.on_wifi()

    def send_wifi(self, command: WiFiCommand) -> None:
        """ส่งคำสั่ง Wi-Fi (หรือ LAN key) ไปบอร์ดทาง CHR_CONFIG — ไม่มีลิงก์ก็เงียบ

        `scan` เริ่มรอบใหม่ที่ฝั่งเราด้วย (begin_scan) เพื่อให้ลิสต์ล้างของเก่าทันทีที่กด
        ไม่ต้องรอ ap ใบแรก · เขียนจริงเมื่อ transport ต่อติดและ pair สำเร็จ
        """
        if command._object.get("c") == "scan":
            with self._lock:
                self.networks.begin_scan()
        if self._transport is not None:
            self._transport.send_config(command.payload)

    def _on_weather_frame(self, frame, observed_at: datetime) -> None:
        # เรียกจาก worker thread ของ WeatherService — เข้า hub ใต้ lock เดียวกับ tick
        if self._hub is None:
            return
        with self._lock:
            self._hub.submit(frame, observed_at)

    def _on_crypto_frame(self, frame, observed_at: datetime) -> None:
        if self._hub is None:
            return
        with self._lock:
            self._hub.submit(frame, observed_at)

    def _on_stocks_frame(self, frame, observed_at: datetime) -> None:
        if self._hub is None:
            return
        with self._lock:
            self._hub.submit(frame, observed_at)

    # MARK: - ออก

    def tick(self, now: datetime | None = None) -> bytes:
        now = now or datetime.now()
        # ตัวยิงถูกป้อนเวลาเดียวกับ snapshot — ไม่มี timer ของตัวเอง · ลูกเขียน cache เอง
        # แล้วรอบถัดไปของ usage_reader ด้านล่างก็หยิบไปส่ง (สองทางเดินอิสระ ปลายทางเดียว)
        if self._poller is not None:
            self._poller.tick(now)
        if self._weather is not None:
            self._weather.tick(now)
        if self._crypto is not None:
            self._crypto.tick(now)
        if self._stocks is not None:
            self._stocks.tick(now)
        with self._lock:
            snap = self.store.snapshot(now)
        # โควตาถูกฉีดที่นี่ ไม่ใช่ใน SessionStore: daemon เป็นที่เดียวที่แตะดิสก์ ส่วน store เป็น
        # ฟังก์ชันบริสุทธิ์ของ hook events · อ่าน cache ทุก tick เหมือน Daemon.swift — ไฟล์เล็ก
        # และค่าที่เก่าคือค่าที่ถูก (ไม่มี TTL) · `None` = ไม่เคยมีข้อมูล -> บอร์ดถอยไปเป็นนาฬิกา
        usage = usage_reader.read(datetime.now(timezone.utc))
        if usage is not None:
            snap = replace(snap, usage=usage)
        with self._lock:
            self._latest_usage = usage
            self._latest_snapshot = snap
        payload = snap.encoded()
        if self.echo:
            print(payload.decode("utf-8", "replace"), flush=True)
        if self._transport is not None:
            self._transport.send(payload)

        # เฟรมของหน้าอื่นเดินทางเป็นก้อนแยกบนช่องเดียวกับ snapshot (ADR-0003) · drain คืนเฉพาะ
        # เฟรมที่เปลี่ยนจริง เรียงตาม PageKind — ค่าตั้งไปก่อนเนื้อหาเสมอ
        if self._hub is not None:
            with self._lock:
                frames = self._hub.drain(now)
            for frame in frames:
                if self.echo:
                    print(frame.decode("utf-8", "replace"), flush=True)
                if self._transport is not None:
                    self._transport.send(frame)
        return payload

    # MARK: - วงจรชีวิต

    def run(self) -> int:
        port = self._server.start()
        if self.verbose:
            print(f"[daemon] hook endpoint on 127.0.0.1:{port}", file=sys.stderr)
        if self._transport is not None:
            self._transport.start()
        try:
            while not self._stop.wait(TICK):
                self.tick()
        except KeyboardInterrupt:
            pass
        finally:
            self._server.stop()
            if self._transport is not None:
                self._transport.stop()
        return 0

    def stop(self) -> None:
        self._stop.set()

    # MARK: - สำหรับ UI (Phase 6) — อ่านข้ามเธรดใต้ lock เดียวกับ tick

    def ui_state(self) -> "DaemonUIState":
        """ภาพรวมที่ tray/popover ต้องวาด — usage ล่าสุด, snapshot, สถานะบอร์ด, Wi-Fi

        อ่านค่าที่ tick คำนวณไว้แล้ว ไม่คำนวณซ้ำและไม่แตะดิสก์ — timer ของ UI เดินทุกวินาที
        การอ่านไฟล์รอบสองทุกวินาทีเปล่าประโยชน์ (tick อ่านให้แล้ว)
        """
        with self._lock:
            return DaemonUIState(
                usage=list(self._latest_usage) if self._latest_usage is not None else None,
                snapshot=self._latest_snapshot,
                connected=bool(self._transport and self._transport.connected),
                networks=self.networks,
                wifi_status=self.wifi_status,
            )


def _weather_config() -> tuple[str, TempUnit]:
    """อ่าน {"place":..,"unit":"C"|"F"} จาก ~/.tamaclaude/weather.json · ไม่มีไฟล์ = ไม่มีเมือง
    (หน้าอากาศเงียบจนกว่าผู้ใช้จะสร้างไฟล์) ไม่ใช่ error"""
    try:
        obj = json.loads(WEATHER_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return "", TempUnit.CELSIUS
    if not isinstance(obj, dict):
        return "", TempUnit.CELSIUS
    place = obj.get("place") or ""
    unit = TempUnit.FAHRENHEIT if str(obj.get("unit", "")).upper() == "F" else TempUnit.CELSIUS
    return (place if isinstance(place, str) else ""), unit


def _crypto_config() -> list[str]:
    """อ่าน {"coins":[...]} จาก ~/.tamaclaude/crypto.json · ไม่มีไฟล์ = ไม่มีเหรียญ (หน้าคริปโต
    เงียบ) ไม่ใช่ error"""
    try:
        obj = json.loads(CRYPTO_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    coins = obj.get("coins") if isinstance(obj, dict) else None
    if not isinstance(coins, list):
        return []
    return [c for c in coins if isinstance(c, str)]


def _stocks_config() -> list[str]:
    """อ่าน {"symbols":[...]} จาก ~/.tamaclaude/stocks.json · ไม่มีไฟล์ = ไม่มีสัญลักษณ์"""
    try:
        obj = json.loads(STOCKS_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    symbols = obj.get("symbols") if isinstance(obj, dict) else None
    if not isinstance(symbols, list):
        return []
    return [s for s in symbols if isinstance(s, str)]


def _alive(owner) -> bool:
    """`True` เมื่อไม่รู้ — "ไม่รู้ว่าตายไหม" ต้องไม่แปลว่า "ตายแล้ว" ไม่งั้น session ที่ยัง
    ทำงานอยู่จะหายจากจอทุกครั้งที่ไต่สายบรรพบุรุษไม่เจอ"""
    handle = ProcessHandle.decode(owner)
    return True if handle is None else is_alive(handle)
