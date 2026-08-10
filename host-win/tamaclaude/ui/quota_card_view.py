"""การ์ดโควตาหนึ่งใบ วาดทั้งใบใน paintEvent เดียว · port ของ QuotaCardView.swift → QPainter

ฝั่ง mac ซอยเป็น NSTextField/NSStackView หลายตัว ที่นี่วาดทั้งการ์ดในครั้งเดียว — เหตุผล
เดียวกับที่ firmware วาดทั้งหน้าใน custom-draw object เดียว: ของหลายชิ้นที่ต้องเรียงให้ตรงกัน
เป๊ะๆ คุมง่ายกว่าเมื่ออยู่ในมือเดียว · สิ่งที่ *พูด* อยู่ที่ `quota_card.py` ที่นี่มีแต่วิธีวาด

สีสามขั้นใช้สีมาตรฐาน ไม่ใช่พาเลตต์บอร์ด (จูนมาสำหรับ TFT หรี่ไฟ บนพื้นเข้มของ popover
อ่านเป็นสีขุ่น) · สีข้อความมาจาก QPalette จึงตามธีมสว่าง/มืดเอง
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from ..protocol import UNKNOWN
from .quota_card import Level, QuotaCard

_GOOD = QColor(52, 199, 89)
_WARN = QColor(255, 149, 0)
_CRIT = QColor(255, 59, 48)

_PAD = 12
_BAR_H = 6
_MARK_OVERHANG = 3
_MARK_W = 2
_CARD_H = 92  # สูงพอสำหรับ head + subtitle + bar + reset ที่ระยะห่างด้านล่าง


def level_color(level: Level, muted: QColor) -> QColor:
    if level == Level.GOOD:
        return _GOOD
    if level == Level.WARN:
        return _WARN
    if level == Level.CRIT:
        return _CRIT
    return muted  # ไม่รู้ = สีจาง ไม่ใช่เขียว — สีปลอดภัยบนค่าที่ไม่มีคือการโกหก


class QuotaCardWidget(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._card: QuotaCard | None = None
        self.setMinimumHeight(_CARD_H)
        self.setMaximumHeight(_CARD_H)

    def show_card(self, card: QuotaCard) -> None:
        self._card = card
        self.update()

    def _muted(self, alpha: float = 1.0) -> QColor:
        c = QColor(self.palette().text().color())
        c.setAlphaF(0.55 * alpha)
        return c

    def paintEvent(self, _event) -> None:
        card = self._card
        if card is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        text_color = self.palette().text().color()
        muted = self._muted()
        faint = QColor(text_color)
        faint.setAlphaF(0.18)

        w = self.width()
        # กรอบการ์ด — เส้นขอบบางทำให้เป็น *ก้อน* ไม่ใช่ข้อความสองบล็อกที่บังเอิญอยู่ใกล้กัน
        card_rect = QRectF(0.5, 0.5, w - 1, _CARD_H - 1)
        bg = QColor(text_color)
        bg.setAlphaF(0.04)
        path = QPainterPath()
        path.addRoundedRect(card_rect, 10, 10)
        p.fillPath(path, bg)
        pen = p.pen()
        pen.setColor(faint)
        pen.setWidthF(1)
        p.setPen(pen)
        p.drawPath(path)

        color = level_color(card.level, muted)
        x = _PAD
        top = _PAD

        # แถวหัว: ชื่อ (หนา) + pill + เปอร์เซ็นต์ชิดขวา
        title_font = QFont()
        title_font.setPointSizeF(11)
        title_font.setBold(True)
        p.setFont(title_font)
        p.setPen(text_color)
        fm = QFontMetrics(title_font)
        p.drawText(x, top + fm.ascent(), card.title)
        title_w = fm.horizontalAdvance(card.title)

        pill_right = x + title_w
        if card.pill:
            pill_font = QFont()
            pill_font.setPointSizeF(8)
            pfm = QFontMetrics(pill_font)
            pw = pfm.horizontalAdvance(card.pill) + 14
            pill_rect = QRectF(x + title_w + 8, top + 1, pw, 16)
            pill_bg = QColor(text_color)
            pill_bg.setAlphaF(0.12)
            pill_path = QPainterPath()
            pill_path.addRoundedRect(pill_rect, 8, 8)
            p.fillPath(pill_path, pill_bg)
            p.setFont(pill_font)
            p.setPen(muted)
            p.drawText(pill_rect, Qt.AlignCenter, card.pill)
            pill_right = pill_rect.right()

        # เปอร์เซ็นต์: --% ไม่ใช่ 0% — ศูนย์เป็นค่าที่วัดได้จริง (ADR-0001)
        pct_font = QFont()
        pct_font.setPointSizeF(12)
        pct_font.setBold(True)
        pct_font.setStyleHint(QFont.Monospace)
        pct_font.setFixedPitch(True)
        p.setFont(pct_font)
        p.setPen(color)
        pct_text = "--%" if card.percent == UNKNOWN else f"{card.percent}%"
        pfm = QFontMetrics(pct_font)
        p.drawText(
            QRectF(pill_right, top, w - _PAD - pill_right, fm.height()),
            Qt.AlignRight | Qt.AlignVCenter, pct_text)

        y = top + fm.height() + 4

        # คำอธิบายใต้ชื่อ (ซ่อนเมื่อว่าง — บรรทัดว่างยังกินที่)
        if card.subtitle:
            sub_font = QFont()
            sub_font.setPointSizeF(8.5)
            p.setFont(sub_font)
            p.setPen(muted)
            sfm = QFontMetrics(sub_font)
            p.drawText(x, y + sfm.ascent(),
                       sfm.elidedText(card.subtitle, Qt.ElideRight, w - 2 * _PAD))
            y += sfm.height() + 4

        # แถบวัด + ขีด pace
        bar_y = y + 4
        self._draw_bar(p, card, QRectF(x, bar_y, w - 2 * _PAD, _BAR_H), color, faint, text_color)
        y = bar_y + _BAR_H + _MARK_OVERHANG + 6

        # เวลารีเซ็ต
        reset_font = QFont()
        reset_font.setPointSizeF(8.5)
        p.setFont(reset_font)
        p.setPen(muted)
        rfm = QFontMetrics(reset_font)
        p.drawText(x, y + rfm.ascent(),
                   rfm.elidedText(card.reset, Qt.ElideRight, w - 2 * _PAD))
        p.end()

    def _draw_bar(
        self, p: QPainter, card: QuotaCard, bar: QRectF, color: QColor,
        track_color: QColor, ink: QColor,
    ) -> None:
        radius = bar.height() / 2
        track = QPainterPath()
        track.addRoundedRect(bar, radius, radius)
        p.fillPath(track, track_color)  # รางยังต้องเห็นแม้ไม่มีค่า

        if card.percent != UNKNOWN:
            pct = min(100, max(0, card.percent))
            filled = bar.width() * pct / 100.0
            if filled > 0:
                p.save()
                p.setClipRect(QRectF(bar.left(), bar.top(), filled, bar.height()))
                p.fillPath(track, color)
                p.restore()

        if card.pace == UNKNOWN:
            return
        # ขีดวาดบนพื้นทึบของแผง ใช้สีข้อความทับตรงๆ ได้ ไม่ต้องเจาะร่องอย่างแถบเมนู
        pace = min(100, max(0, card.pace))
        at = bar.width() * pace / 100.0
        mx = min(max(bar.left(), bar.left() + at - _MARK_W / 2), bar.right() - _MARK_W)
        mark = QRectF(mx, bar.top() - _MARK_OVERHANG, _MARK_W, bar.height() + 2 * _MARK_OVERHANG)
        mark_path = QPainterPath()
        mark_path.addRoundedRect(mark, _MARK_W / 2, _MARK_W / 2)
        p.fillPath(mark_path, ink)
