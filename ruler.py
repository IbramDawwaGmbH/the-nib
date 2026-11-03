"""Pixel Ruler: a full-monitor overlay for measuring an on-screen distance.

``RulerOverlay`` is mouse-only, on purpose. It carries ``Qt.WindowDoesNotAcceptFocus``, which
means it can never steal keyboard focus - or, in practice, foreground activation - from whatever
you're measuring, so a dropdown or tooltip you had open to check its size stays open underneath
it. The tradeoff is that a window which never takes focus never receives key events either, so
Escape/Enter can't be the accept/cancel gesture here. The whole interaction is mouse-driven
instead: drag to measure, release to freeze the reading, click anywhere (not just on the reading
itself - any plain click while frozen) to copy it and close, right-click at any point (dragging or
frozen) to close without copying. Starting a new *drag* from a frozen reading abandons it and
measures again instead, so several distances in a row don't need the tray menu reopened each time.

The overlay is otherwise unpainted everywhere the line, crosshair, and hint pill aren't. On
Windows, ``WA_TranslucentBackground`` is implemented as a layered window, and hit-testing follows
the composited pixel alpha rather than the widget's geometry - a fully transparent region is
click-through at the OS level, confirmed empirically (a blank overlay with this flag combination
received zero mouse events anywhere, including where nothing was ever painted). ``paintEvent``
therefore always fills the whole rect with alpha 1 first: invisible, but enough that the very
first press - before any line exists to paint - still lands on the overlay instead of whatever's
behind it.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from bubble import ACCENT
from calculator import format_number

_STATE_IDLE = "idle"
_STATE_DRAGGING = "dragging"
_STATE_FROZEN = "frozen"

#: How far the mouse has to move between press and release to count as a real measurement
#: rather than a stray click - keeps an accidental twitch from freezing a near-zero reading, and
#: (while already frozen) from being read as a fresh drag instead of "accept this one".
CLICK_THRESHOLD_PX = 4

_HITTEST_ALPHA = 1
_HINT_BG = QColor(20, 20, 20, 230)
_HINT_TEXT = QColor("#F2EDE9")


def _reading_text(dx: int, dy: int, dpi: int) -> str:
    """The label text for a drag of ``dx``, ``dy`` pixels at ``dpi``.

    A pure function so the measurement math can be exercised without a real window - see
    tests/smoke_ruler.py. Inches and centimetres are rounded before formatting, not after:
    pixel/DPI precision doesn't support more than a couple of decimal places, and
    ``format_number``'s own decimal budget would otherwise print several digits of fake
    precision off a raw division.
    """
    px = math.hypot(dx, dy)
    inches = px / dpi
    cm = inches * 2.54
    return (
        f"{format_number(round(px))} px  ·  "
        f"{format_number(round(inches, 2))} in  ·  "
        f"{format_number(round(cm, 2))} cm"
    )


class RulerOverlay(QWidget):
    """One monitor's worth of measuring surface. Closes itself when it's done."""

    closed = Signal()

    def __init__(self, monitor_rect: QRect, dpi: int, start_pos: QPoint | None = None) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setGeometry(monitor_rect)

        self._dpi = max(1, int(dpi))
        self._state = _STATE_IDLE
        self._anchor: QPoint | None = None
        self._current: QPoint | None = self._clamp(start_pos) if start_pos is not None else None
        self._press_started_frozen = False
        #: The reading text as of the moment a drag froze - captured then, not recomputed from
        #: ``_anchor``/``_current`` later, because accepting it starts with a fresh press that
        #: immediately overwrites both with the new press position (see mousePressEvent) before
        #: _copy_and_close ever runs.
        self._frozen_reading: str | None = None

    def _clamp(self, pos: QPoint) -> QPoint:
        return QPoint(
            max(0, min(self.width() - 1, pos.x())),
            max(0, min(self.height() - 1, pos.y())),
        )

    # --- mouse state machine ----------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.RightButton:
            self.close()
            return
        if event.button() != Qt.LeftButton:
            return
        pos = self._clamp(event.position().toPoint())
        self._press_started_frozen = self._state == _STATE_FROZEN
        self._anchor = pos
        self._current = pos
        self._state = _STATE_DRAGGING
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._state == _STATE_FROZEN:
            return
        self._current = self._clamp(event.position().toPoint())
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton or self._state != _STATE_DRAGGING:
            return
        moved = (
            self._anchor is not None
            and (self._anchor - self._current).manhattanLength() >= CLICK_THRESHOLD_PX
        )
        if moved:
            self._state = _STATE_FROZEN
            dx = self._current.x() - self._anchor.x()
            dy = self._current.y() - self._anchor.y()
            self._frozen_reading = _reading_text(dx, dy, self._dpi)
        elif self._press_started_frozen:
            self._copy_and_close()
            return
        else:
            self._state = _STATE_IDLE
            self._anchor = None
        self.update()

    def _copy_and_close(self) -> None:
        if self._frozen_reading is not None:
            QApplication.clipboard().setText(self._frozen_reading)
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.closed.emit()
        super().closeEvent(event)

    # --- paint --------------------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(0, 0, 0, _HITTEST_ALPHA))

        if self._current is None:
            return

        if self._state == _STATE_IDLE:
            self._paint_crosshair(painter)
            self._paint_hint(painter, self._current, "drag to measure  ·  right-click to close")
            return

        if self._anchor is None:
            return
        self._paint_ruler_line(painter)
        if self._state == _STATE_DRAGGING:
            dx = self._current.x() - self._anchor.x()
            dy = self._current.y() - self._anchor.y()
            reading = _reading_text(dx, dy, self._dpi)
            hint = "release to lock"
        else:
            reading = self._frozen_reading or ""
            hint = "click to copy  ·  right-click to close"
        self._paint_hint(painter, self._current, f"{reading}\n{hint}")

    def _paint_crosshair(self, painter: QPainter) -> None:
        pen = QPen(QColor(ACCENT))
        pen.setWidthF(1.0)
        painter.setPen(pen)
        painter.setOpacity(0.35)
        painter.drawLine(0, self._current.y(), self.width(), self._current.y())
        painter.drawLine(self._current.x(), 0, self._current.x(), self.height())
        painter.setOpacity(1.0)

    def _paint_ruler_line(self, painter: QPainter) -> None:
        pen = QPen(QColor(ACCENT))
        pen.setWidthF(2.0)
        painter.setPen(pen)
        painter.setBrush(QColor(ACCENT))
        painter.drawLine(self._anchor, self._current)
        painter.drawEllipse(self._anchor, 4, 4)
        painter.drawEllipse(self._current, 4, 4)

    def _paint_hint(self, painter: QPainter, near: QPoint, text: str) -> None:
        # A fixed dark pill regardless of light/dark theme: it sits over an unpredictable mix of
        # whatever's on screen underneath, not over Nib's own chrome, so the theme this app
        # otherwise follows isn't a meaningful signal for it.
        lines = text.split("\n")
        metrics = painter.fontMetrics()
        width = max(metrics.horizontalAdvance(line) for line in lines) + 20
        height = metrics.height() * len(lines) + 14
        x = near.x() + 18
        y = near.y() + 18
        if x + width > self.width():
            x = near.x() - width - 18
        if y + height > self.height():
            y = near.y() - height - 18
        rect = QRect(x, y, width, height)
        painter.setPen(Qt.NoPen)
        painter.setBrush(_HINT_BG)
        painter.drawRoundedRect(rect, 6, 6)
        painter.setPen(_HINT_TEXT)
        for i, line in enumerate(lines):
            painter.drawText(
                rect.left() + 10, rect.top() + 10 + metrics.ascent() + i * metrics.height(), line
            )
