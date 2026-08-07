"""หาที่อยู่ของ repo แล้วเปิดทาง import ให้ `tools/gen/*.py`

ฝั่ง Windows ไม่ได้พอร์ต ThaiShaper/trend/pages ใหม่ — repo มีเวอร์ชัน Python ของสามอย่างนั้น
อยู่แล้ว และมันคือคู่ขนานที่ `tools/thai-golden.json` ค้ำอยู่กับฝั่ง Swift · การคัดลอกมาไว้ใน
`host-win/` จะได้สำเนาที่แก่ลงเงียบๆ ทุกครั้งที่ `tools/thai.toml` ถูกแก้แล้ว export ใหม่
"""

from __future__ import annotations

import sys
from pathlib import Path

# host-win/tamaclaude/_repo.py -> host-win/tamaclaude -> host-win -> repo
REPO_DIR = Path(__file__).resolve().parent.parent.parent
TOOLS_DIR = REPO_DIR / "tools"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
