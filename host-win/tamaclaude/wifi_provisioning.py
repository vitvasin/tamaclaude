"""สิ่งที่วิ่งบนสองท่อของ GATT ตอนตั้งค่า WiFi ให้บอร์ด — port ของ WiFiProvisioning.swift

คำสั่งออกทาง config characteristic (`...0003`) ซึ่ง firmware บังคับให้ลิงก์เข้ารหัสก่อน
(บน Windows คือ `await client.pair()` แล้วเขียน — ดู `ble.py` ฝั่ง `send_config`)
เพราะรหัส WiFi ของผู้ใช้เดินผ่านตรงนี้ · รายงานกลับมาทาง event characteristic (`...0004`)
เป็น JSON บรรทัดเดียวต่อหนึ่ง notification — decode ที่ `BoardEvent.decode`

**บอร์ดไม่เคยได้ `sessionKey`** — WiFi มีไว้ให้บอร์ดคุยกับ host ผ่าน LAN เมื่อ BLE ไกลเกินไป
ไม่ใช่ให้บอร์ดยิง claude.ai เอง

ไฟล์นี้เป็น pure logic ล้วน (ไม่ import bleak) — การเขียนจริงลง characteristic อยู่ที่ `ble.py`
"""

from __future__ import annotations

import enum
import json
from dataclasses import dataclass, field

from .pages import PageKind
from .protocol import dumps


# MARK: - คำสั่งออก (host -> board ทาง CHR_CONFIG)


@dataclass(frozen=True)
class WiFiCommand:
    """คำสั่งหนึ่งคำสั่งที่ส่งออกทาง config characteristic

    payload ใช้ sorted-keys เหมือน snapshot — คำสั่งเดิมต้องได้ไบต์เดิมเสมอ ไม่งั้นเทสต์
    เทียบผลลัพธ์ไม่ได้ และการดีบักด้วยสายตาจะขึ้นกับลำดับที่ json เลือกวันนั้น
    """

    _object: dict[str, str]

    @staticmethod
    def scan() -> "WiFiCommand":
        """สแกนแล้วรายงานผลกลับมาทีละตัว"""
        return WiFiCommand({"c": "scan"})

    @staticmethod
    def status() -> "WiFiCommand":
        """ขอสถานะปัจจุบันซ้ำ — ใช้ตอนเพิ่งเปิดหน้าตั้งค่า"""
        return WiFiCommand({"c": "status"})

    @staticmethod
    def join(ssid: str, psk: str | None) -> "WiFiCommand":
        """จำเครือข่ายแล้วต่อทันที

        `psk` ว่างคือเครือข่ายเปิด · `None` คือ **ไม่แตะรหัสที่บอร์ดจำไว้** — สองอย่างนี้
        ต่างกันบนสาย (ไม่มีคีย์ `psk` เลย) เพราะผู้ใช้ที่กลับไปต่อวงเดิมไม่ควรต้องพิมพ์รหัสใหม่
        และ `""` ที่ส่งไปแทนจะลบรหัสที่ใช้ได้อยู่ทิ้ง แล้วล้มด้วย auth fail
        """
        obj = {"c": "join", "ssid": ssid}
        if psk is not None:
            obj["psk"] = psk
        return WiFiCommand(obj)

    @staticmethod
    def forget(ssid: str) -> "WiFiCommand":
        return WiFiCommand({"c": "forget", "ssid": ssid})

    @staticmethod
    def key(hex_str: str) -> "WiFiCommand":
        """กุญแจของทาง LAN เป็น hex 64 ตัว — บอร์ดล้างตัวนับกันเล่นซ้ำทิ้งเมื่อได้รับ
        เดินทางช่องเดียวกับรหัส WiFi ด้วยเหตุผลเดียวกัน: ช่องนี้บังคับเข้ารหัส"""
        return WiFiCommand({"c": "key", "k": hex_str})

    @property
    def payload(self) -> bytes:
        return dumps(self._object)


# MARK: - รายงานเข้า (board -> host ทาง CHR_EVENT)


@dataclass(frozen=True)
class AccessPoint:
    """เครือข่ายหนึ่งวงที่บอร์ด (ไม่ใช่ host) มองเห็น

    รายการนี้มาจากวิทยุของบอร์ดโดยตั้งใจ: ESP32 รับได้แค่ 2.4GHz และตั้งอยู่คนละที่กับ host —
    รายการของ host จะมีวงที่บอร์ดเข้าไม่ได้ปนมา ซึ่งผู้ใช้จะรู้ก็ต่อเมื่อกดต่อแล้วล้ม
    """

    ssid: str
    rssi: int
    secured: bool


class WiFiState(str, enum.Enum):
    OFF = "off"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    FAILED = "failed"


@dataclass(frozen=True)
class WiFiStatus:
    """สถานะ WiFi ของบอร์ด พร้อมรายชื่อที่มันจำไว้

    รายชื่อที่จำไว้เดินทางมากับสถานะเสมอ ไม่ใช่ข้อความแยก — หน้าตั้งค่าต้องการทั้งคู่พร้อมกัน
    และสองข้อความที่มาไม่พร้อมกันแปลว่ามีจังหวะที่หน้าจอแสดงของครึ่งเดียว
    """

    state: WiFiState
    ssid: str
    ip: str
    # เหตุผลที่รอบล่าสุดล้ม — แยก "รหัสผิด" (ผู้ใช้ต้องพิมพ์ใหม่) ออกจาก "ไม่เจอ" (รอ)
    error: str | None
    saved: list[str] = field(default_factory=list)
    # ลายนิ้วมือกุญแจ LAN ที่บอร์ดถืออยู่ (8 hex) — ว่างคือยังไม่เคยตั้ง
    #
    # มีไว้ตอบคำถามเดียว: กุญแจสองฝั่งตรงกันไหม ถ้าไม่ตรง เฟรมบน LAN จะถูกทิ้งทุกก้อน
    # โดยไม่มีอาการอื่นให้เห็นเลยนอกจากจอที่ค้าง
    key_fingerprint: str = ""

    @property
    def needs_password(self) -> bool:
        """รอบนี้ล้มเพราะสิ่งที่บอร์ดลองเองอีกกี่ครั้งก็ไม่ดีขึ้นไหม

        สตริง `"wrong password"` ผูกกับ `wifi_event` ใน `firmware/main/ct_wifi.c` ซึ่งเป็นที่
        เดียวที่แปลง `WIFI_REASON_*` เป็นคำ — สองไฟล์นี้อ้างถึงกันตอนรันไม่ได้ จึงต้องมีคอมเมนต์
        ชี้กลับทั้งสองฝั่ง แก้ข้างหนึ่งแล้วอีกข้างจะเงียบ ไม่ใช่พัง
        """
        return self.state == WiFiState.FAILED and self.error == "wrong password"


@dataclass(frozen=True)
class BoardEvent:
    """สิ่งที่บอร์ดแจ้งกลับมาทาง event characteristic

    เป็น union แบบง่าย: เช็ก field ที่ไม่ None เพื่อรู้ชนิด · `decode` คืน None เมื่อไม่ใช่
    ข้อความที่เรารู้จัก — firmware รุ่นใหม่กว่าต้องไม่ทำให้ host พัง
    """

    access_point: AccessPoint | None = None
    scan_finished: bool = False
    wifi: WiFiStatus | None = None
    # รายการ PageKind ที่บอร์ดตัวนี้รู้จัก ประกาศตอนเชื่อมต่อ (ADR-0006) — เป็น *ความสามารถ*
    # ไม่ใช่เลขเวอร์ชัน: แอปใหม่กับ firmware เก่าเป็นสภาพปกติ
    capability: list[PageKind] | None = None

    @staticmethod
    def decode(data: bytes) -> "BoardEvent | None":
        try:
            obj = json.loads(data)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(obj, dict):
            return None
        kind = obj.get("t")

        if kind == "ap":
            ssid = obj.get("s")
            if not isinstance(ssid, str) or not ssid:
                return None
            rssi = obj.get("r")
            secured = obj.get("e", 1)
            return BoardEvent(access_point=AccessPoint(
                ssid=ssid,
                rssi=int(rssi) if isinstance(rssi, (int, float)) else 0,
                secured=(int(secured) if isinstance(secured, (int, float)) else 1) != 0,
            ))

        if kind == "ap_end":
            return BoardEvent(scan_finished=True)

        if kind == "cap":
            # ค่าที่ยังไม่มีใน enum ฝั่งนี้ถูกทิ้ง ไม่ใช่ทำให้ทั้งข้อความใช้ไม่ได้ —
            # firmware ที่ใหม่กว่าแอปคือสภาพที่มีจริงพอๆ กับทางกลับกัน
            raw = obj.get("p") if isinstance(obj.get("p"), list) else []
            kinds: list[PageKind] = []
            for v in raw:
                try:
                    kinds.append(PageKind(v))
                except (ValueError, TypeError):
                    continue
            return BoardEvent(capability=kinds)

        if kind == "wifi":
            try:
                state = WiFiState(obj.get("st", ""))
            except ValueError:
                state = WiFiState.OFF
            err = obj.get("er")
            error = err if isinstance(err, str) and err else None
            saved = obj.get("nets")
            return BoardEvent(wifi=WiFiStatus(
                state=state,
                ssid=obj.get("s", "") if isinstance(obj.get("s"), str) else "",
                ip=obj.get("ip", "") if isinstance(obj.get("ip"), str) else "",
                error=error,
                saved=[s for s in saved if isinstance(s, str)] if isinstance(saved, list) else [],
                key_fingerprint=obj.get("kf", "") if isinstance(obj.get("kf"), str) else "",
            ))

        return None


# MARK: - รายการที่หน้าตั้งค่าเอาไปวาด


@dataclass(frozen=True)
class NetworkRow:
    """หนึ่งแถวในลิสต์เครือข่าย — ที่เห็นตอนนี้ กับที่บอร์ดจำไว้ อยู่ลิสต์เดียวกัน

    เครือข่ายที่จำไว้ต้องมีแถวของตัวเองแม้รอบสแกนล่าสุดจะไม่เห็นมัน ไม่งั้นผู้ใช้ที่พิมพ์รหัสผิด
    ไปแล้วจะเลือกมันไม่ได้ และปุ่ม Connect/Forget จะค้างเป็นสีเทาตลอด — ทางเดียวที่เหลือคือรอให้
    สแกนเจอ ซึ่งเป็นสิ่งที่ล้มอยู่พอดีตอนบอร์ดกำลังวนต่อด้วยรหัสเดิม
    """

    ssid: str
    rssi: int | None  # None คือ "จำไว้แต่รอบนี้ไม่เห็น" ไม่ใช่ "สัญญาณศูนย์"
    secured: bool
    saved: bool

    @property
    def in_range(self) -> bool:
        return self.rssi is not None


class NetworkList:
    """รายการที่หน้าตั้งค่าเอาไปวาด — ผลสแกนผสมกับสิ่งที่บอร์ดจำไว้

    เก็บสถานะการสแกนไว้ที่เดียว ไม่ใช่กระจายอยู่ในตัว view: บอร์ดส่งผลมาทีละใบและอาจไม่ส่ง
    `ap_end` เลยถ้าหลุดกลางคัน หน้าตั้งค่าจึงต้องมีที่ให้ถามว่า "ตอนนี้รู้อะไรแล้ว"
    """

    def __init__(self) -> None:
        self.scanning = False
        self.found: list[AccessPoint] = []
        self.saved: list[str] = []

    @property
    def rows(self) -> list[NetworkRow]:
        """สิ่งที่ลิสต์วาดจริง: ที่เห็นเรียงตามความแรง แล้วต่อท้ายด้วยที่จำไว้แต่ยังไม่เห็นรอบนี้"""
        out = [
            NetworkRow(ssid=ap.ssid, rssi=ap.rssi, secured=ap.secured,
                       saved=ap.ssid in self.saved)
            for ap in self.found
        ]
        seen = {ap.ssid for ap in self.found}
        for ssid in self.saved:
            if ssid in seen:
                continue
            # ที่จำไว้ย่อมมีรหัส เว้นแต่ตอนบันทึกมันเป็นวงเปิด — เดาว่ามีรหัสไว้ก่อนดีกว่า
            # เพราะช่องรหัสที่โผล่มาเกินไม่ทำให้ใครต่อไม่ได้ ส่วนช่องที่หายไปทำ
            out.append(NetworkRow(ssid=ssid, rssi=None, secured=True, saved=True))
        return out

    def begin_scan(self) -> None:
        self.scanning = True
        self.found = []

    def apply(self, event: BoardEvent) -> None:
        if event.access_point is not None:
            ap = event.access_point
            # AP เดียวกันตอบสองครั้งได้ตอนสแกนซ้อนรอบ — เก็บใบที่แรงกว่าไว้ใบเดียว
            for i, existing in enumerate(self.found):
                if existing.ssid == ap.ssid:
                    if ap.rssi > existing.rssi:
                        self.found[i] = ap
                    break
            else:
                self.found.append(ap)
            self.found.sort(key=lambda a: a.rssi, reverse=True)
        elif event.scan_finished:
            self.scanning = False
        elif event.wifi is not None:
            self.saved = list(event.wifi.saved)
        # capability: หน้า Wi-Fi ไม่รู้จักเรื่อง page — คนที่ฟังคือ PageHub

    def link_lost(self) -> None:
        """ล้างสถานะกำลังสแกนเมื่อลิงก์ BLE หลุด — สปินเนอร์ที่หมุนค้างคือคำโกหก"""
        self.scanning = False
