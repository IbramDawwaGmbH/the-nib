"""The snippet billboard: a standing sign showing what you've asked him to hold onto.

Modelled on ``bubble.AskBubble``: a frameless, translucent, always-on-top popup that paints its
own rounded-rect body plus a tail in ``paintEvent`` rather than through a stylesheet - a plain
QWidget's own top-level background and border do not actually render from QSS on this window flag
combination, only the custom-painted path does. The tail is the whole point here, more than it was
for the bubble: without it the sign just floats above him with no visual connection to the arm
he's holding it up with. Unlike the bubble, this one has no auto-hide timer: it stays up until
something explicit takes it down, because the whole point is to leave it visible while you work.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPolygonF
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from bubble import _theme

MAX_WIDTH = 260
RADIUS = 10
TAIL_H = 9
TAIL_W = 16
#: Where his raised hand is, as a fraction of his sprite frame - not the centre, because the arm
#: (and so the tail needs to point) sits well over to the right of his body. Matches ARM_X in
#: tools/gen_sprites.py, which puts the arm at column 36 of a 48-wide frame.
HAND_X_FRACTION = 0.85
#: How far down into the frame the raised hand sits, as a fraction of the sprite's full height -
#: the strain pose's arm tip is around row 17 of 48. Landing the tail a little above that, rather
#: than exactly on it, keeps it clear of the fingers instead of stabbing through them.
HAND_Y_FRACTION = 0.22


class Billboard(QWidget):
    """A sign he holds up. Text only - no buttons, no timer, no callback."""

    def __init__(self, ui_scale: float = 1.0) -> None:
        super().__init__(None)
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._s = max(1.0, float(ui_scale))
        self._radius = int(RADIUS * self._s)
        self._tail_h = int(TAIL_H * self._s)
        self._tail_w = int(TAIL_W * self._s)
        self._tail_x = 0  # local-coordinate x the tail points at; set in show_text
        self._theme = _theme()

        layout = QVBoxLayout(self)
        pad = int(12 * self._s)
        layout.setContentsMargins(pad, int(9 * self._s), pad, int(9 * self._s) + self._tail_h)
        self._label = QLabel("", self)
        self._label.setWordWrap(True)
        layout.addWidget(self._label)

    def show_text(self, text: str, anchor: QRect, screen: QRect) -> None:
        """(Re)draw the sign with ``text`` and plant its tail at his raised hand.

        ``anchor`` is his own on-screen rect - the whole sprite frame, not just the drawn pixels,
        which is why the hand position below is read off as a fraction of it rather than off its
        edges directly.
        """
        t = self._theme = _theme()
        s = self._s
        self._label.setStyleSheet(
            f"color: {t['text']}; font-size: {int(12 * s)}px; background: transparent;"
        )
        self._label.setText(text)
        self.setFixedWidth(int(MAX_WIDTH * s))
        self.adjustSize()

        hand_x = anchor.left() + int(anchor.width() * HAND_X_FRACTION)
        hand_y = anchor.top() + int(anchor.height() * HAND_Y_FRACTION)

        x = hand_x - self.width() // 2
        x = max(screen.left() + 6, min(screen.right() - self.width() - 6, x))
        y = max(screen.top() + 6, hand_y - self.height())
        self.move(x, y)
        # The tail points at the hand in SCREEN coordinates, but is painted in the widget's own
        # LOCAL coordinates - so it has to be re-derived from where the widget actually ended up,
        # not from hand_x directly, or clamping x against the screen edge would leave the tail
        # pointing at empty space next to him instead of at his hand.
        self._tail_x = max(self._radius + self._tail_w, min(self.width() - self._radius - self._tail_w,
                                                              hand_x - x))
        self.show()
        self.raise_()
        self.update()

    def take_down(self) -> None:
        self.hide()

    def paintEvent(self, event) -> None:  # noqa: N802
        t = self._theme
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        body = QRectF(0, 0, self.width() - 1, self.height() - self._tail_h - 1)

        path = QPainterPath()
        path.addRoundedRect(body, self._radius, self._radius)
        path.addPolygon(
            QPolygonF([
                QPointF(self._tail_x - self._tail_w / 2, body.bottom() - 1),
                QPointF(self._tail_x + self._tail_w / 2, body.bottom() - 1),
                QPointF(self._tail_x, body.bottom() + self._tail_h),
            ])
        )
        painter.setPen(QColor(t["border"]))
        painter.setBrush(QColor(t["bg"]))
        painter.drawPath(path.simplified())
