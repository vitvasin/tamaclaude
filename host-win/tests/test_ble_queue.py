"""คิวส่งของ BleTransport ต้องไม่ทำเฟรมของหน้าหาย — สายแยกกัน ไม่ทับข้ามสาย"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "host-win"))

from tamaclaude.ble import BleTransport, _stream_key  # noqa: E402

MASCOT = b'{"a":0,"c":"09:41","d":"Fri 7 Aug","m":0,"n":[],"o":0,"s":[]}'
PLAN = b'{"h":300,"j":1,"pl":[0,1,2],"r":20,"t":1}'
WEATHER = b'{"a":0,"g":1,"p":"Bangkok","t":26}'
CRYPTO = b'{"a":0,"c":[{"d":-21,"p":"64230","s":"BTC"}],"g":2}'


def test_stream_key_classifies():
    assert _stream_key(MASCOT) == "mascot"
    assert _stream_key(PLAN) == "plan"
    assert _stream_key(WEATHER) == "page:1"
    assert _stream_key(CRYPTO) == "page:2"


def test_distinct_streams_all_survive_one_tick():
    t = BleTransport()
    # จำลอง daemon.tick ที่ส่งทุกอย่างในรอบเดียว
    t.send(MASCOT)
    t.send(PLAN)
    t.send(WEATHER)
    t.send(CRYPTO)
    assert set(t._pending) == {"mascot", "plan", "page:1", "page:2"}
    # ลำดับที่ส่งเข้าถูกรักษา (plan ก่อนเฟรมหน้า)
    assert list(t._pending) == ["mascot", "plan", "page:1", "page:2"]


def test_same_stream_newer_overwrites():
    t = BleTransport()
    t.send(WEATHER)
    newer = b'{"a":5,"g":1,"p":"Bangkok","t":27}'
    t.send(newer)
    assert t._pending["page:1"] == newer
    assert len([k for k in t._pending if k == "page:1"]) == 1


def test_negative_g_never_happens_but_parses_page_number():
    # หน้าเลขสองหลักในอนาคต
    assert _stream_key(b'{"g":10,"a":0}') == "page:10"
