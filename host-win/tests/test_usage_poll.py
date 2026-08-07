"""ตรรกะบริสุทธิ์ของ --usage-poll — org parsing, pick, validated, url · get()/run() แตะเน็ต ไม่เทสต์"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "host-win"))

import pytest  # noqa: E402

from tamaclaude import usage_poll as up  # noqa: E402


def test_organizations_uuid_and_id_and_name():
    data = json.dumps(
        [
            {"uuid": "org-a", "name": "Acme"},
            {"id": "org-b"},  # ไม่มีชื่อ -> ใช้ id เป็นชื่อ
            {"name": "no id here"},  # ไม่มี id -> ทิ้ง
        ]
    )
    orgs = up.organizations(data)
    assert orgs == [up.Org("org-a", "Acme"), up.Org("org-b", "org-b")]


def test_organizations_bad_id_skipped_not_whole_list():
    data = json.dumps([{"uuid": "bad/slash"}, {"uuid": "good"}])
    assert up.organizations(data) == [up.Org("good", "good")]


def test_organizations_garbage_is_empty():
    assert up.organizations("not json") == []
    assert up.organizations(json.dumps({"not": "a list"})) == []


def test_pick_prefers_when_present():
    orgs = [up.Org("a", "A"), up.Org("b", "B")]
    assert up.pick(orgs, "b") == up.Org("b", "B")


def test_pick_falls_back_to_first():
    orgs = [up.Org("a", "A"), up.Org("b", "B")]
    assert up.pick(orgs, "gone") == up.Org("a", "A")
    assert up.pick(orgs, None) == up.Org("a", "A")
    assert up.pick([], "x") is None


def test_validated_rejects_traversal():
    with pytest.raises(up.Failure):
        up.validated("")
    with pytest.raises(up.Failure):
        up.validated("a/b")
    with pytest.raises(up.Failure):
        up.validated("..")
    assert up.validated("org-123") == "org-123"


def test_usage_url():
    assert up.usage_url("org-1") == "https://claude.ai/api/organizations/org-1/usage"


def test_read_key_missing_is_unusable(tmp_path):
    with pytest.raises(up.Failure) as e:
        up.read_key(tmp_path / "nope")
    assert e.value.code == up.UNUSABLE_KEY_FILE
