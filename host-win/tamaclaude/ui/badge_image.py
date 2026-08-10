"""แบดจ์บน tray ในภาพเดียว — แถบวัดสั้นๆ กับเปอร์เซ็นต์ · port ของ MenuBadgeImage.swift → QPainter

ต่างจาก mac ตรง template: macOS มี isTemplate ให้ระบบกลับสีให้เอง Windows ไม่มี — tray icon
วาดลง taskbar ตรงๆ จึงต้องเลือกหมึกเองตาม color scheme ของระบบ (Qt styleHints) แล้ววาดใหม่
เมื่อ scheme เปลี่ยน · ตรรกะ isAlarming / pace อยู่ที่ `badge.py` ไฟล์นี้แค่วาด
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QIcon,
    QPainter,
    QPainterPath,
    QPixmap,
)

from .badge import MenuBadge

# ค่าคงที่หน้าตา — ตรงกับฝั่ง mac (ดูคอมเมนต์ที่นั่นว่าแต่ละค่าทำไม)
_BAR_W = 35.0
_BAR_H = 11.0
_GAP = 5.0
_HEIGHT = 16.0
_BORDER = 1.0
_RADIUS = 2.5
_PADDING = 1.5

_RED = QColor(255, 59, 48)  # systemRed โดยประมาณ


def _font() -> QFont:
    f = QFont()
    f.setPointSizeF(10.0)
    # เลขความกว้างคงที่ ไม่งั้นไอคอนขยับซ้ายขวาทุกครั้งที่เปอร์เซ็นต์เปลี่ยนหลัก
    f.setStyleHint(QFont.Monospace)
    f.setFixedPitch(True)
    return f


def make(badge: MenuBadge, dark: bool = True, scale: float = 2.0) -> QIcon:
    """คืน QIcon ของแบดจ์ · `dark` = taskbar มืด (หมึกขาว) · scale = device pixel ratio

    แดงเฉพาะขีด pace ไม่ใช่ทั้งภาพ — สิ่งที่ผิดปกติคือความสัมพันธ์ระหว่างเนื้อแถบกับขีด
    ไม่ใช่ตัวเลขหรือตัวแถบ · เนื้อ/ราง/เลข ใช้หมึกตามธีมเสมอ
    """
    ink = QColor(255, 255, 255) if dark else QColor(0, 0, 0)
    font = _font()
    fm = QFontMetricsF(font)
    text = f"{badge.percent}%"
    text_w = fm.horizontalAdvance(text)
    width = _BAR_W + _GAP + text_w

    pm = QPixmap(round(width * scale), round(_HEIGHT * scale))
    pm.setDevicePixelRatio(scale)
    pm.fill(Qt.transparent)

    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)

    # หดเข้ามาครึ่งเส้น เพราะ stroke วาดคร่อมเส้นทาง ครึ่งนอกจะถูกขอบภาพตัดทิ้ง
    shell = QRectF(
        _BORDER / 2, (_HEIGHT - _BAR_H) / 2 + _BORDER / 2,
        _BAR_W - _BORDER, _BAR_H - _BORDER)

    inset = _BORDER / 2 + _PADDING
    track = shell.adjusted(inset, inset, -inset, -inset)
    track_radius = max(0.0, _RADIUS - inset)

    # ราง: จางแต่ยังเห็น — แถบที่ไม่มีรางบอกไม่ได้ว่า 20% นี้คือ 20% ของเท่าไร
    track_path = QPainterPath()
    track_path.addRoundedRect(track, track_radius, track_radius)
    faint = QColor(ink)
    faint.setAlphaF(0.25)
    p.fillPath(track_path, faint)

    # เนื้อแถบ: clip เป็นสี่เหลี่ยมกว้างตาม % แล้วเติมทับ track_path เต็ม เพื่อให้ปลายซ้าย
    # โค้งตามราง ไม่ใช่ตามความยาวของตัวเอง
    pct = min(100, max(0, badge.percent))
    filled = track.width() * pct / 100.0
    if filled > 0:
        p.save()
        p.setClipRect(QRectF(track.left(), track.top(), filled, track.height()))
        p.fillPath(track_path, ink)
        p.restore()

    # ขีด pace: สูงเท่าแถบพอดี · แดงเมื่อ isAlarming (จมในเนื้อแถบ) ไม่งั้นเจาะร่องโปร่ง
    # ตรงที่ทับเนื้อ และวาดด้วยหมึกตรงที่อยู่บนรางว่าง
    if badge.pace != -1:
        pace = min(100, max(0, badge.pace)) / 100.0
        at = min(track.left() + track.width() * pace, shell.right() - _BORDER / 2)
        mark = QRectF(at, shell.top() - _BORDER / 2, _BORDER, _BAR_H)
        p.save()
        if badge.is_alarming:
            p.fillRect(mark, _RED)
        elif badge.pace <= badge.percent:
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            p.fillRect(mark, Qt.transparent)
        else:
            p.fillRect(mark, ink)
        p.restore()

    # ขอบทึบท้ายสุด — ต้องมาหลังขีด pace ที่พาดทับขอบบนล่าง ไม่งั้นกรอบขาดเป็นสองท่อน
    outline = QPainterPath()
    outline.addRoundedRect(shell, _RADIUS, _RADIUS)
    pen = p.pen()
    pen.setColor(ink)
    pen.setWidthF(_BORDER)
    p.setPen(pen)
    p.drawPath(outline)

    # ตัวเลข
    p.setFont(font)
    p.setPen(ink)
    p.drawText(
        QRectF(_BAR_W + _GAP, 0, text_w, _HEIGHT),
        Qt.AlignVCenter | Qt.AlignLeft, text)

    p.end()
    return QIcon(pm)


def fallback(dark: bool = True, scale: float = 2.0) -> QIcon:
    """ไอคอนตอนไม่มีอะไรจะบอก — แถบเปล่า (ราง+ขอบ ไม่มีเนื้อ ไม่มีเลข ไม่มีขีด)

    "0%" ที่เดาเอาคือคำโกหกที่ดูเหมือนค่าที่วัดมา (ADR-0001) จึงไม่วาดเนื้อแถบเลย —
    เป็นเครื่องวัดที่เข็มยังไม่ขยับ ไม่ใช่เครื่องวัดที่อ่านศูนย์ · Windows ไม่มี SF Symbol
    gauge อย่างฝั่ง mac จึงยืมโครงแถบเดียวกันแทน ผู้ใช้ไม่ต้องแปลความสัญลักษณ์ใหม่
    """
    ink = QColor(255, 255, 255) if dark else QColor(0, 0, 0)
    pm = QPixmap(round(_BAR_W * scale), round(_HEIGHT * scale))
    pm.setDevicePixelRatio(scale)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    shell = QRectF(
        _BORDER / 2, (_HEIGHT - _BAR_H) / 2 + _BORDER / 2,
        _BAR_W - _BORDER, _BAR_H - _BORDER)
    inset = _BORDER / 2 + _PADDING
    track = shell.adjusted(inset, inset, -inset, -inset)
    track_radius = max(0.0, _RADIUS - inset)
    track_path = QPainterPath()
    track_path.addRoundedRect(track, track_radius, track_radius)
    faint = QColor(ink)
    faint.setAlphaF(0.25)
    p.fillPath(track_path, faint)
    outline = QPainterPath()
    outline.addRoundedRect(shell, _RADIUS, _RADIUS)
    pen = p.pen()
    pen.setColor(ink)
    pen.setWidthF(_BORDER)
    p.setPen(pen)
    p.drawPath(outline)
    p.end()
    return QIcon(pm)


def description(badge: MenuBadge) -> str:
    used = f"{badge.percent}% of the 5 hour window used"
    if badge.pace == -1:
        return used
    return used + f", {badge.pace}% of the window elapsed"
