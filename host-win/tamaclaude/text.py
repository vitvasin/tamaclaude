"""การเตรียมข้อความก่อนส่งบน BLE — พอร์ตจาก `host/Sources/TamaCore/Text.swift`

ฟอนต์บนบอร์ดมี ASCII พิมพ์ได้ (0x20..0x7E) กับอักขระไทยตาม `gen.thai.BLOCK_RANGES`
ตัวอักษรนอกสองชุดนี้จะกลายเป็นกล่องสี่เหลี่ยมบนจอ daemon จึงต้องกรองเอง

การแตกคลัสเตอร์ไม่ได้เขียนใหม่ที่นี่ — เรียก `gen.thai` ซึ่งเป็นคู่ขนานของ ThaiShaper.swift
ที่ `tools/thai-golden.json` ค้ำอยู่ทั้งสองฝั่ง (ADR-0008)
"""

from __future__ import annotations

from dataclasses import dataclass

from . import _repo  # noqa: F401  (ต้อง import ก่อน gen.* เพื่อเปิดทาง sys.path)

from gen import thai  # noqa: E402

# ตัวที่แทนได้ด้วย ASCII แบบไม่เสียความหมาย
SUBSTITUTIONS: dict[str, str] = {
    "—": "-",  # em dash
    "–": "-",  # en dash
    "−": "-",  # minus
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "…": "...",
    " ": " ",
    " ": " ",
    " ": " ",
    "→": "->",
    "←": "<-",
    "•": "*",
    "·": "*",
    "✓": "ok",
    "✗": "x",
}


def sanitize(s: str) -> str:
    """เหลือเฉพาะอักขระที่ฟอนต์บนบอร์ดมีจริง และยุบช่องว่างซ้อนให้เหลือช่องเดียว

    เดินทีละ code point ไม่ใช่ทีละคลัสเตอร์ เพราะสระบนกับวรรณยุกต์ไทยอยู่ใน grapheme
    เดียวกับพยัญชนะ การตัดสินทั้งคลัสเตอร์ด้วยตัวแรกจะทิ้งวรรณยุกต์ที่ฟอนต์มีจริง
    """
    out: list[str] = []
    last_was_space = False
    for ch in s:
        cp = ord(ch)
        if ch in SUBSTITUTIONS:
            piece = SUBSTITUTIONS[ch]
        elif 0x20 <= cp <= 0x7E:
            piece = ch
        elif thai.in_block(cp):
            piece = ch
        elif ch.isspace():
            piece = " "
        else:
            continue  # ทิ้ง ไม่แทนด้วย '?' เพราะจะกลายเป็นขยะเต็มบรรทัด
        for p in piece:
            if p == " ":
                if last_was_space:
                    continue
                last_was_space = True
            else:
                last_was_space = False
            out.append(p)
    return "".join(out).strip(" \t")


def clip(s: str, limit: int) -> str:
    """ตัดให้ยาวไม่เกิน `limit` ช่องบนจอ โดยเติม "..." เมื่อถูกตัดจริง

    นับเป็น "ช่อง" ไม่ใช่ code point — สระบนและวรรณยุกต์ไทยกว้างศูนย์ การนับดิบๆ
    จะตัดข้อความไทยสั้นเกินจริงราว 40%
    """
    if limit <= 0:
        return ""
    cells = thai.clusters(s)
    if len(cells) <= limit:
        return "".join(cells)
    if limit <= 3:
        return "".join(cells[:limit])
    return "".join(cells[: limit - 3]) + "..."


@dataclass(frozen=True)
class Cells:
    """เพดานของป้ายหนึ่ง แยกตามภาษา เพราะช่องไทยกว้างกว่าตัวอักษรอังกฤษเฉลี่ยเสมอ

    เลขเดียวที่ใช้ได้ทั้งสองภาษาต้องเป็นเลขของไทย ซึ่งแปลว่าบรรทัดอังกฤษถูกตัดสั้นกว่าที่
    จอรับได้จริงราวหนึ่งในสี่ ทั้งที่ไม่มีเหตุผล
    """

    ascii: int
    thai: int


class Limit:
    project = Cells(ascii=14, thai=12)
    """ป้ายใต้มาสคอต"""

    card_title = Cells(ascii=32, thai=30)
    """หัวการ์ด — กว้าง 278px ฟอนต์บอร์ด 14"""

    card_body = Cells(ascii=44, thai=34)
    """เนื้อการ์ด — กว้างเท่ากัน ฟอนต์บอร์ด 12"""

    card_name = Cells(ascii=12, thai=10)
    """ชื่อโปรเจกต์ตอนมันต้องแชร์บรรทัดเดียวกับประโยค — เป็น *ส่วนแบ่ง* ไม่ใช่เพดานของชื่อ"""


def _ceiling(clean: str, limit: int | Cells) -> int:
    """บรรทัดที่มีอักขระไทยแม้ตัวเดียวใช้เพดานของไทยทั้งบรรทัด

    ช่องไทยกว้างคงที่และกว้างกว่าอังกฤษเฉลี่ย การเดาว่าอีกกี่ช่องจะเป็นไทยแปลว่าเพดาน
    เปลี่ยนตามเนื้อหา ซึ่งไม่ใช่เพดาน
    """
    if isinstance(limit, int):
        return limit
    return limit.thai if any(thai.in_block(ord(ch)) for ch in clean) else limit.ascii


def fit(s: str, limit: int | Cells) -> str:
    """ประตูเดียวที่ข้อความจะออกสู่สายได้ — กรองแล้วประกอบร่างไทยแล้วค่อยตัด"""
    clean = sanitize(s)
    return clip(clean, _ceiling(clean, limit))


def fit_reserving(s: str, limit: int | Cells, suffix: str) -> str:
    """เหมือน `fit` แต่กันที่ไว้ให้ส่วนท้ายที่ผู้เรียกจะต่อเองทีหลัง

    สิ่งที่ต้องพอดีเพดานคือ *ป้ายทั้งป้าย* ไม่ใช่ชื่อโปรเจกต์อย่างเดียว · `suffix` วัดดิบๆ
    ไม่ผ่าน `sanitize` ซึ่งจะกินช่องว่างนำหน้าทิ้งแล้วกันที่ขาดไปหนึ่งช่อง
    ห้าม fit ซ้ำบนข้อความที่ผ่านทางนี้แล้ว: ร่างไทยที่ประกอบแล้วอยู่ใน PUA ซึ่ง sanitize ทิ้งหมด
    """
    clean = sanitize(s)
    return clip(clean, _ceiling(clean, limit) - display_width(suffix))


def head(s: str, limit: int | Cells) -> str:
    """ส่วนหน้าเท่าที่ใส่ได้ — **ไม่มี "..." ต่อท้าย** ต่างจาก `fit`

    ใช้กับชื่อโปรเจกต์บนบรรทัดการ์ดเท่านั้น ซึ่งเป็นตัวชี้ไปที่ป้ายใต้มาสคอต ไม่ใช่เนื้อหา
    ห้ามใช้กับประโยค: ที่นั่นการตัดคือการหายไปของข้อมูล ซึ่งต้องเห็น
    """
    clean = sanitize(s)
    return "".join(thai.clusters(clean)[: max(0, _ceiling(clean, limit))])


def display_width(s: str) -> int:
    """ความยาวที่ตาเห็น หน่วยเป็นช่อง — ตัวเดียวกับที่ `clip` ใช้ตัดสิน"""
    return thai.display_width(s)


def grouped(number: str) -> str:
    """ใส่ตัวคั่นหลักพัน — ตัวคั่นบนจอนี้เป็นข้อตกลงของ *จอ* ไม่ใช่ของเครื่องที่รันเดมอน

    ทำงานบนสตริงที่จัดรูปแล้ว ไม่ใช่ Double — จำนวนทศนิยมถูกตัดสินไปแล้วตามขนาดราคา
    (`decimals` ของแต่ละหน้า) ขั้นนี้ต้องไม่ไปยุ่งกับมัน
    """
    sign = "-" if number.startswith("-") else ""
    body = number[1:] if sign else number
    dot = body.find(".")
    whole, rest = (body, "") if dot < 0 else (body[:dot], body[dot:])
    if len(whole) <= 3 or not whole.isdigit():
        return sign + whole + rest
    out = []
    n = len(whole)
    for i, c in enumerate(whole):
        if i > 0 and (n - i) % 3 == 0:
            out.append(",")
        out.append(c)
    return sign + "".join(out) + rest
