"""popover ที่เด้งจาก tray — หัว (ชื่อ org + ปุ่ม refresh), การ์ดโควตา, ท้าย (สถานะบอร์ด +
รายการ session + อายุตัวเลข) · port ของ PanelViewController.swift → QWidget

frameless popup วางใกล้ tray · วาดใหม่ทุกวินาทีจาก DaemonUIState (ตัวเลขที่เดินคือหลักฐาน
ว่าแผงยังมีชีวิต) · สิ่งที่ *พูด* อยู่ที่ `panel_text.py`/`quota_card.py` ที่นี่คือการวาง
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import panel_text, refresh_control
from .quota_card import QuotaCard
from .quota_card_view import QuotaCardWidget

_WIDTH = 300


class Panel(QWidget):
    def __init__(
        self,
        on_refresh: Callable[[], None] | None = None,
        on_settings: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        # Popup = ปิดเองเมื่อคลิกนอกแผง เหมือน NSPopover .transient · frameless ไม่มีแถบหัว
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self._on_refresh = on_refresh
        self._on_settings = on_settings
        self.setFixedWidth(_WIDTH)
        self.setObjectName("panel")

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # หัว: ชื่อ org ซ้าย, refresh + เฟือง ขวา
        head = QHBoxLayout()
        self._heading = QLabel(panel_text.APP_NAME)
        hf = self._heading.font()
        hf.setPointSizeF(13)
        hf.setBold(True)
        self._heading.setFont(hf)
        head.addWidget(self._heading)
        head.addStretch(1)
        self._refresh = QPushButton("⟳")
        self._refresh.setFixedSize(24, 24)
        self._refresh.setFlat(True)
        self._refresh.clicked.connect(lambda: self._on_refresh and self._on_refresh())
        head.addWidget(self._refresh)
        gear = QPushButton("⚙")
        gear.setFixedSize(24, 24)
        gear.setFlat(True)
        gear.clicked.connect(lambda: self._on_settings and self._on_settings())
        head.addWidget(gear)
        root.addLayout(head)

        # การ์ดโควตา (สูงสุดสองใบ: session + weekly)
        self._cards = [QuotaCardWidget(self), QuotaCardWidget(self)]
        for c in self._cards:
            root.addWidget(c)

        # ข้อความ "ยังไม่มีตัวเลข" แทนการ์ดเปล่า
        self._empty = QLabel("No quota figures yet")
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setStyleSheet("color: gray;")
        root.addWidget(self._empty)

        # เส้นคั่นก่อนท้ายแผง
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: rgba(128,128,128,80);")
        root.addWidget(line)

        # ท้าย: สถานะบอร์ด, อายุตัวเลข, รายการ session
        self._board = QLabel("Looking for the board…")
        self._board.setStyleSheet("color: gray;")
        root.addWidget(self._board)
        self._updated = QLabel("")
        self._updated.setStyleSheet("color: gray; font-size: 11px;")
        root.addWidget(self._updated)
        self._sessions = QLabel("")
        self._sessions.setStyleSheet("font-size: 11px;")
        self._sessions.setWordWrap(True)
        root.addWidget(self._sessions)

    def update_view(
        self,
        state,
        stamp: datetime | None,
        has_key: bool,
        orgs: list | None = None,
        current_org: str | None = None,
        refresh_running: bool = False,
        refresh_finished: datetime | None = None,
        now: datetime | None = None,
    ) -> None:
        now = now or datetime.now()
        orgs = orgs or []

        self._heading.setText(panel_text.heading(orgs, current_org, has_key))
        self._board.setText(panel_text.board(state.connected))
        self._updated.setText(panel_text.updated(stamp, now))

        rs = refresh_control.state(refresh_running, has_key, refresh_finished, now)
        self._refresh.setEnabled(rs.enabled)
        self._refresh.setToolTip(rs.tooltip)

        cards = QuotaCard.cards(state.usage, now=now)
        if cards is None:
            for c in self._cards:
                c.hide()
            self._empty.show()
        else:
            self._empty.hide()
            for i, c in enumerate(self._cards):
                if i < len(cards):
                    c.show_card(cards[i])
                    c.show()
                else:
                    c.hide()

        if state.snapshot is not None:
            self._sessions.setText("\n".join(panel_text.sessions(state.snapshot)))
        else:
            self._sessions.setText("No sessions")
        self.adjustSize()
