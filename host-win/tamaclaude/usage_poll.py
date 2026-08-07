"""ยิงโควตาจาก claude.ai หนึ่งรอบแล้วจบ — พอร์ตของ `UsagePoll.swift` · โปรเซสอายุสั้น ไม่ใช่ daemon

**นี่คือจุดที่ credential เต็มบัญชีเดินทาง** — `sessionKey` ทำได้ทุกอย่างที่เจ้าของบัญชีทำได้ ห้าม
log request/header ทุกกรณี (cookie อยู่ในนั้น) และห้ามให้ key ผ่าน argv หรือ env · key อ่านจาก
ไฟล์ ACL แคบใหม่ทุกรอบ อยู่ใน RAM แค่ช่วงยิง
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import secret_file, usage_writer
from .paths import SESSION_KEY, USAGE_CACHE

ORGANIZATIONS_URL = "https://claude.ai/api/organizations"

# ประโยคของ session key — กติกาว่าอะไรคือไฟล์ที่ใช้ได้อยู่ที่ secret_file ที่เดียว
KEY_WORDING = secret_file.Wording(
    noun="session key",
    missing="paste the claude.ai sessionKey cookie into that file",
    empty="paste the claude.ai sessionKey cookie into it",
)

# exit code ที่ผู้เรียกแยกแยะได้โดยไม่ต้องอ่านข้อความ
REJECTED_KEY = 2  # key ถูกปฏิเสธ — หมดอายุ ต้องไปเอาอันใหม่จากเบราว์เซอร์
UNUSABLE_KEY_FILE = 3  # ไฟล์ key ใช้ไม่ได้ — ไม่มี ว่าง หรือคนอื่นอ่านได้


class Failure(Exception):
    def __init__(self, message: str, code: int = 1, orgs: list["Org"] | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.orgs = orgs or []


@dataclass(frozen=True)
class Org:
    id: str
    name: str


@dataclass
class Report:
    orgs: list[Org] = field(default_factory=list)
    summary: str = ""


def read_key(url: Path = SESSION_KEY) -> str:
    """อ่าน session key จากไฟล์ — ทุกอาการของไฟล์ที่ใช้ไม่ได้เป็น exit code เดียวกัน"""
    try:
        return secret_file.read(url, KEY_WORDING)
    except secret_file.Problem as p:
        raise Failure(p.message, UNUSABLE_KEY_FILE)


def validated(org_id: str) -> str:
    """ตรวจ org id ก่อนต่อเข้า URL เสมอ แม้จะมาจาก response ของเราเอง — `/` หรือ `..` เปลี่ยน
    path ที่ยิงได้ทั้งเส้น ปลายทางเป็นของคนอื่น ถือว่าทุกอย่างที่กลับมาเป็นข้อมูลภายนอก"""
    if not org_id or "/" in org_id or ".." in org_id:
        raise Failure("organization id must not be empty or contain '/' or '..'")
    return org_id


def organizations(data: bytes | str) -> list[Org]:
    """แกะ org ทั้งรายการ — อันที่ id ใช้ไม่ได้ถูกทิ้งเงียบ ไม่ใช่ทำให้ทั้งรายการล้ม"""
    try:
        rows = json.loads(data)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    out: list[Org] = []
    for entry in rows:
        if not isinstance(entry, dict):
            continue
        raw = entry.get("uuid") or entry.get("id") or ""
        try:
            oid = validated(raw)
        except Failure:
            continue
        name = entry.get("name") or ""
        out.append(Org(id=oid, name=name if name else oid))
    return out


def pick(orgs: list[Org], preferred: str | None) -> Org | None:
    """org ที่จะยิงจริง: ตัวที่เลือกไว้ถ้ายังอยู่ในรายการ ไม่งั้นตัวแรก"""
    if preferred:
        for o in orgs:
            if o.id == preferred:
                return o
    return orgs[0] if orgs else None


def usage_url(org_id: str) -> str:
    return f"https://claude.ai/api/organizations/{validated(org_id)}/usage"


def get(url: str, key: str, timeout: float = 20) -> bytes:
    """GET หนึ่งครั้งพร้อม cookie — ไม่มี cookie jar บนดิสก์ · error สร้างจาก status/urlerror
    เท่านั้น ไม่เคยพก request/header ติดไปด้วย (cookie อยู่ในนั้น)"""
    req = urllib.request.Request(
        url,
        headers={
            "Cookie": f"sessionKey={key}",
            "Accept": "application/json",
            "User-Agent": "tamaclaude",
        },
        method="GET",
    )
    # opener ไม่มี HTTPCookieProcessor — Set-Cookie ที่ claude.ai ส่งกลับ (รวม sessionKey ตัวใหม่)
    # จะไม่ถูกเก็บลงดิสก์ที่ไหน · credential ต้องอยู่ใน RAM แค่ช่วงยิง
    opener = urllib.request.build_opener()
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        status = e.code
        if status in (401, 403):
            raise Failure(
                f"claude.ai rejected the session key (HTTP {status}); it has most likely "
                "expired — set a fresh one",
                REJECTED_KEY,
            )
        raise Failure(f"claude.ai returned HTTP {status}")
    except (urllib.error.URLError, OSError) as e:
        raise Failure(f"cannot reach claude.ai ({e.reason if hasattr(e, 'reason') else e})")


def run(
    key_file: Path = SESSION_KEY,
    cache: Path = USAGE_CACHE,
    org_override: str | None = None,
    now: datetime | None = None,
) -> Report:
    """ยิงหนึ่งรอบ: อ่าน key -> หา org -> ยิง -> เขียน cache -> คืนบรรทัดสรุป

    ล้มเหลวแบบไหนก็ไม่แตะ cache เดิม: ค่าที่ค้างอยู่ยังจริงกว่าการไม่มีค่าเลย
    """
    now = now or datetime.now(timezone.utc)
    if org_override is None:
        org_override = os.environ.get("TAMACLAUDE_ORG_ID")
    orgs: list[Org] = []
    try:
        if org_override:
            # พินคือพิน — ค่าที่ env กำหนดต้องไม่ถูกแทนเงียบด้วยตัวแรก
            org_id = validated(org_override)
            try:
                orgs = organizations(get(ORGANIZATIONS_URL, read_key(key_file)))
            except Failure:
                orgs = []
        else:
            orgs = organizations(get(ORGANIZATIONS_URL, read_key(key_file)))
            first = pick(orgs, None)
            if first is None:
                raise Failure("no organizations on this account")
            org_id = first.id

        payload = get(usage_url(org_id), read_key(key_file))
        line = usage_writer.ingest_api(payload, now=now, url=cache)
        if line is None:
            raise Failure(
                "no window we recognise in the usage payload; a field was probably renamed"
            )
        return Report(orgs=orgs, summary=line)
    except Failure as f:
        f.orgs = orgs
        raise


def main(argv: list[str] | None = None) -> int:
    """`--usage-poll`: ยิงรอบเดียวแล้วออก · 0=เขียน cache · 2=key ถูกปฏิเสธ · 3=ไฟล์ key ใช้ไม่ได้
    · 1=อื่นๆ · รายการ org + สรุปเดินทางกลับทาง stdout (ลูกตายทุกรอบ ต้องพูดตอนยังมีชีวิต)"""
    try:
        report = run()
    except Failure as f:
        for o in f.orgs:
            print(f"org {o.id} {o.name}")
        print(f.message)
        return f.code
    for o in report.orgs:
        print(f"org {o.id} {o.name}")
    print(report.summary)
    return 0
