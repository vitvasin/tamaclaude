"""WiFiCommand payloads + BoardEvent.decode + NetworkList — พอร์ตพฤติกรรมจาก WiFiProvisioning.swift"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "host-win"))

from tamaclaude.pages import PageKind  # noqa: E402
from tamaclaude.wifi_provisioning import (  # noqa: E402
    AccessPoint,
    BoardEvent,
    NetworkList,
    WiFiCommand,
    WiFiState,
)


# MARK: - คำสั่งออก


def test_command_payloads_sorted_keys():
    assert WiFiCommand.scan().payload == b'{"c":"scan"}'
    assert WiFiCommand.status().payload == b'{"c":"status"}'
    assert WiFiCommand.forget("home").payload == b'{"c":"forget","ssid":"home"}'
    assert WiFiCommand.key("ab" * 32).payload == b'{"c":"key","k":"%s"}' % (b"ab" * 32)


def test_join_open_vs_untouched_psk_differ_on_wire():
    # psk="" คือวงเปิด (มีคีย์ psk ว่าง) · psk=None คือไม่แตะรหัสที่จำไว้ (ไม่มีคีย์ psk เลย)
    assert WiFiCommand.join("net", "").payload == b'{"c":"join","psk":"","ssid":"net"}'
    assert WiFiCommand.join("net", None).payload == b'{"c":"join","ssid":"net"}'
    assert WiFiCommand.join("net", "pw").payload == b'{"c":"join","psk":"pw","ssid":"net"}'


# MARK: - decode


def test_decode_access_point():
    ev = BoardEvent.decode(b'{"t":"ap","s":"MyWiFi","r":-40,"e":1}')
    assert ev.access_point == AccessPoint(ssid="MyWiFi", rssi=-40, secured=True)


def test_decode_ap_open_and_missing_rssi():
    ev = BoardEvent.decode(b'{"t":"ap","s":"Open","e":0}')
    assert ev.access_point == AccessPoint(ssid="Open", rssi=0, secured=False)


def test_decode_ap_empty_ssid_is_none():
    assert BoardEvent.decode(b'{"t":"ap","s":""}') is None


def test_decode_scan_finished():
    assert BoardEvent.decode(b'{"t":"ap_end"}').scan_finished is True


def test_decode_capability_drops_unknown():
    ev = BoardEvent.decode(b'{"t":"cap","p":[0,1,2,99]}')
    assert ev.capability == [PageKind(0), PageKind(1), PageKind(2)]


def test_decode_wifi_status_and_needs_password():
    ev = BoardEvent.decode(
        b'{"t":"wifi","st":"failed","s":"Home","ip":"","er":"wrong password",'
        b'"nets":["Home","Cafe"],"kf":"deadbeef"}'
    )
    st = ev.wifi
    assert st.state == WiFiState.FAILED
    assert st.saved == ["Home", "Cafe"]
    assert st.key_fingerprint == "deadbeef"
    assert st.needs_password is True


def test_decode_wifi_not_found_is_not_needs_password():
    ev = BoardEvent.decode(b'{"t":"wifi","st":"failed","er":"not found"}')
    assert ev.wifi.needs_password is False


def test_decode_unknown_and_garbage_is_none():
    assert BoardEvent.decode(b'{"t":"whatever"}') is None
    assert BoardEvent.decode(b"not json") is None


# MARK: - NetworkList


def test_rows_merge_found_and_saved():
    nl = NetworkList()
    nl.apply(BoardEvent.decode(b'{"t":"wifi","st":"off","nets":["Home","Away"]}'))
    nl.begin_scan()  # begin_scan ล้าง found แต่ saved คงอยู่
    nl.apply(BoardEvent(access_point=AccessPoint("Home", -50, True)))
    nl.apply(BoardEvent(access_point=AccessPoint("Cafe", -30, True)))
    rows = nl.rows
    # เห็นเรียงตามความแรงก่อน (Cafe -30 > Home -50), แล้ว saved-ที่ไม่เห็น (Away) ต่อท้าย
    assert [r.ssid for r in rows] == ["Cafe", "Home", "Away"]
    assert rows[0].saved is False and rows[1].saved is True
    away = rows[2]
    assert away.rssi is None and away.in_range is False and away.saved is True


def test_duplicate_ap_keeps_stronger():
    nl = NetworkList()
    nl.apply(BoardEvent(access_point=AccessPoint("X", -70, True)))
    nl.apply(BoardEvent(access_point=AccessPoint("X", -40, True)))
    assert [(r.ssid, r.rssi) for r in nl.rows] == [("X", -40)]


def test_scan_finished_and_link_lost_clear_scanning():
    nl = NetworkList()
    nl.begin_scan()
    assert nl.scanning is True
    nl.apply(BoardEvent(scan_finished=True))
    assert nl.scanning is False
    nl.begin_scan()
    nl.link_lost()
    assert nl.scanning is False
