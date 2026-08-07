"""PageHub age-diffing + capability gating — พอร์ตพฤติกรรมจาก Pages.swift"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "host-win"))

from tamaclaude.pages import PageHub, PageKind, PagePlan, PageRetire  # noqa: E402
from tamaclaude.protocol import MAX_PAYLOAD, dumps  # noqa: E402

T0 = datetime(2026, 8, 7, 12, 0, 0)


@dataclass
class FakeFrame:
    kind: PageKind
    value: int
    age: int = 0

    def encoded(self, max_bytes: int = MAX_PAYLOAD) -> bytes:
        return dumps({"g": int(self.kind), "a": self.age, "v": self.value})


def _obj(data: bytes) -> dict:
    return json.loads(data.decode("utf-8"))


# MARK: - encode contracts


def test_retire_encode():
    assert PageRetire(PageKind.WEATHER).encoded() == b'{"g":1,"x":1}'


def test_plan_encode_keys():
    plan = PagePlan(order=[PageKind.MASCOT, PageKind.WEATHER], auto_turn=True, rotation=8, hold=5, attention_jump=False)
    assert _obj(plan.encoded()) == {"pl": [0, 1], "r": 8, "h": 5, "j": 0, "t": 1}


# MARK: - capability gating


def test_frame_not_sent_until_announced():
    hub = PageHub()
    hub.submit(FakeFrame(PageKind.WEATHER, 1), T0)
    assert hub.drain(T0) == []  # ยังไม่ประกาศ -> ไม่ส่ง
    hub.announce([PageKind.WEATHER])
    frames = hub.drain(T0)
    assert len(frames) == 1
    assert _obj(frames[0])["g"] == 1


# MARK: - age diffing


def test_age_is_seconds_since_observed():
    hub = PageHub()
    hub.announce([PageKind.WEATHER])
    hub.submit(FakeFrame(PageKind.WEATHER, 1), T0)
    frames = hub.drain(T0 + timedelta(seconds=90))
    assert _obj(frames[0])["a"] == 90


def test_ticking_age_is_not_a_change():
    hub = PageHub()
    hub.announce([PageKind.WEATHER])
    hub.submit(FakeFrame(PageKind.WEATHER, 1), T0)
    hub.drain(T0)
    # ไม่มี submit ใหม่ เวลาเดินไป -> ไม่ส่งซ้ำ (อายุที่แค่เดินไม่ใช่การเปลี่ยน)
    assert hub.drain(T0 + timedelta(seconds=300)) == []


def test_reread_same_value_new_observed_resends():
    hub = PageHub()
    hub.announce([PageKind.WEATHER])
    hub.submit(FakeFrame(PageKind.WEATHER, 1), T0)
    hub.drain(T0)
    # อ่านค่าเดิมมาใหม่ (observed ใหม่) -> ส่งซ้ำ เพราะอายุคือสิ่งที่ต่างกัน
    hub.submit(FakeFrame(PageKind.WEATHER, 1), T0 + timedelta(seconds=100))
    frames = hub.drain(T0 + timedelta(seconds=100))
    assert len(frames) == 1
    assert _obj(frames[0])["a"] == 0


def test_changed_value_resends():
    hub = PageHub()
    hub.announce([PageKind.WEATHER])
    hub.submit(FakeFrame(PageKind.WEATHER, 1), T0)
    hub.drain(T0)
    hub.submit(FakeFrame(PageKind.WEATHER, 2), T0)
    assert len(hub.drain(T0)) == 1


# MARK: - plan


def test_plan_sent_once_after_announce():
    hub = PageHub()
    plan = PagePlan(order=[PageKind.WEATHER], auto_turn=True, rotation=8, hold=5, attention_jump=True)
    hub.submit_plan(plan)
    assert hub.drain(T0) == []  # ยังไม่ประกาศ -> ไม่ส่ง plan
    hub.announce([PageKind.WEATHER])
    frames = hub.drain(T0)
    assert any(b'"pl"' in f for f in frames)
    # ส่งครั้งเดียว ไม่ซ้ำ
    hub.submit(FakeFrame(PageKind.WEATHER, 9), T0)  # กันไม่ให้ drain ว่างเพราะเหตุอื่น
    frames2 = hub.drain(T0)
    assert not any(b'"pl"' in f for f in frames2)


# MARK: - drop / forget


def test_drop_emits_retire():
    hub = PageHub()
    hub.announce([PageKind.WEATHER])
    hub.drop(PageKind.WEATHER)
    frames = hub.drain(T0)
    assert frames == [b'{"g":1,"x":1}']


def test_mascot_cannot_be_dropped():
    hub = PageHub()
    hub.announce([PageKind.WEATHER])
    hub.drop(PageKind.MASCOT)
    assert hub.drain(T0) == []


def test_forget_sent_resends_everything():
    hub = PageHub()
    hub.announce([PageKind.WEATHER])
    hub.submit(FakeFrame(PageKind.WEATHER, 1), T0)
    hub.drain(T0)
    assert hub.drain(T0) == []  # ส่งแล้ว
    hub.forget_sent()
    assert len(hub.drain(T0)) == 1  # ลืมแล้ว -> ส่งใหม่
