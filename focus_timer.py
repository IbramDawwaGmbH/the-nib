"""The focus-session readout: a small floating countdown above his head while a session runs.

Modelled on ``billboard.Billboard`` - frameless, translucent, always-on-top, custom-painted rather
than styled through Qt Style Sheets, which do not actually render a background/border on this
window flag combination. Unlike the billboard there is no tail: this is a HUD readout rather than
something he is physically holding, so it just floats above him without claiming to be held.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from bubble import ACCENT, _theme

WIDTH = 92
HEIGHT = 52
RADIUS = 12
GAP_ABOVE = 6      # px between the readout's bottom edge and his sprite's top
BAR_H = 4
BAR_MARGIN = 10
#: Under this many seconds left, the bar switches to a warmer colour - the last stretch of a
#: session reads differently from the other twenty-four minutes.
URGENT_S = 60
URGENT_COLOR = "#E0483A"


class FocusTimer(QWidget):
    """How long is left. No buttons, no way to interact with it - just a countdown and a bar."""

    def __init__(self, ui_scale: float = 1.0) -> None:
        super().__init__(None)
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._s = max(1.0, float(ui_scale))
        self.setFixedSize(int(WIDTH * self._s), int(HEIGHT * self._s))
        self._theme = _theme()
        self._remaining_s = 0
        self._fraction = 1.0

    def show_time(self, remaining_s: float, total_s: float, anchor: QRect, screen: QRect) -> None:
        """(Re)draw the countdown above ``anchor`` and make sure it's on top.

        Safe to call on every tick a session is running, not just once - his position never
        changes during a session, but re-deriving it from ``anchor`` each time costs nothing and
        means this never has to assume that stays true.
        """
        self._theme = _theme()
        self._remaining_s = max(0, int(round(remaining_s)))
        self._fraction = 0.0 if total_s <= 0 else max(0.0, min(1.0, remaining_s / total_s))

        cx = anchor.left() + anchor.width() // 2
        x = cx - self.width() // 2
        x = max(screen.left() + 6, min(screen.right() - self.width() - 6, x))
        y = max(screen.top() + 6, anchor.top() - self.height() - int(GAP_ABOVE * self._s))
        self.move(x, y)
        self.show()
        self.raise_()
        self.update()

    def take_down(self) -> None:
        self.hide()

    def paintEvent(self, event) -> None:  # noqa: N802
        t = self._theme
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        body = QRectF(0, 0, self.width() - 1, self.height() - 1)
        path = QPainterPath()
        path.addRoundedRect(body, RADIUS * self._s, RADIUS * self._s)
        painter.setPen(QColor(t["border"]))
        painter.setBrush(QColor(t["bg"]))
        painter.drawPath(path)

        label_font = QFont()
        label_font.setPixelSize(int(9 * self._s))
        painter.setFont(label_font)
        painter.setPen(QColor(t["muted"]))
        painter.drawText(
            QRectF(0, int(6 * self._s), self.width(), int(12 * self._s)),
            Qt.AlignCenter, "F O C U S",
        )

        mm, ss = divmod(self._remaining_s, 60)
        clock_font = QFont()
        clock_font.setPixelSize(int(17 * self._s))
        clock_font.setBold(True)
        painter.setFont(clock_font)
        painter.setPen(QColor(t["text"]))
        painter.drawText(
            QRectF(0, int(17 * self._s), self.width(), int(21 * self._s)),
            Qt.AlignCenter, f"{mm}:{ss:02d}",
        )

        bar_h = int(BAR_H * self._s)
        bar_x = int(BAR_MARGIN * self._s)
        bar_w = self.width() - bar_x * 2
        bar_y = self.height() - int(BAR_MARGIN * self._s / 2) - bar_h
        track = QRectF(bar_x, bar_y, bar_w, bar_h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(t["seg_bg"]))
        painter.drawRoundedRect(track, bar_h / 2, bar_h / 2)

        fill_w = bar_w * self._fraction
        if fill_w > 0:
            colour = URGENT_COLOR if self._remaining_s <= URGENT_S else ACCENT
            painter.setBrush(QColor(colour))
            painter.drawRoundedRect(QRectF(bar_x, bar_y, fill_w, bar_h), bar_h / 2, bar_h / 2)
