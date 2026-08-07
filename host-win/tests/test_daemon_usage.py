"""daemon ฉีดโควตาเข้า snapshot ทุก tick — พอร์ตพฤติกรรมจาก Daemon.swift"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "host-win"))

from tamaclaude import usage_reader  # noqa: E402
from tamaclaude.daemon import Daemon  # noqa: E402
from tamaclaude.protocol import UsageSnap  # noqa: E402


def _payload(monkeypatch, usage):
    monkeypatch.setattr(usage_reader, "read", lambda now=None, url=None: usage)
    d = Daemon(use_ble=False, echo=False, use_poll=False, use_pages=False)
    return json.loads(d.tick().decode("utf-8"))


def test_usage_injected_when_present(monkeypatch):
    obj = _payload(monkeypatch, [UsageSnap(42, 3600), UsageSnap(7, 172800)])
    assert obj["u"] == [[42, 3600], [7, 172800]]


def test_no_u_key_when_absent(monkeypatch):
    # cache หาย -> ไม่มีคีย์ 'u' เลย -> บอร์ดถอยไปเป็นนาฬิกา
    obj = _payload(monkeypatch, None)
    assert "u" not in obj
