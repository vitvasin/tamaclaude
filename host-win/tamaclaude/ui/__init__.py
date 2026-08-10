"""Phase 6 UI — tray, popover with quota cards, สองแท็บ settings

แบ่งสองชั้นชัดเจนเหมือนฝั่ง mac: ตรรกะล้วน (`badge`, `quota_card`, `refresh_control`,
`panel_text`) เทสต์ได้บนเครื่องไม่มีจอ · ชั้นวาด (`badge_image`, `quota_card_view`,
`panel`, `prefs`, `tray`, `app`) เป็น PySide6 (QPainter) — import Qt เฉพาะชั้นนี้

ทาง --hook และ --daemon แบบ headless ต้อง **ไม่** แตะ package นี้เลย — งบเวลา hook ~175ms
และ PySide6 เป็น extra ([ui]) ไม่ใช่ dependency หลัก
"""
