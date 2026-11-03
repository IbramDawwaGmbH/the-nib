"""The mascot's own window: frameless, translucent, always on top, click-through where empty.

Two Windows-specific details are worth knowing about:

*   A translucent Qt window still swallows mouse events over fully transparent pixels - Qt does
    not hit-test the alpha channel. So we install a per-frame ``QRegion`` mask taken from the
    sprite's alpha. Because the art contract bans partial alpha, that mask is pixel-exact and
    costs nothing in fidelity, and it means the mascot only intercepts clicks that actually land
    on him. The rest of the taskbar stays clickable.
*   ``WindowStaysOnTopHint`` is not permanently respected - other apps take the top-most slot.
    ``reassert_topmost()`` pushes us back up via ``SetWindowPos``; the app calls it on a timer.
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

from PySide6.QtCore import (
    QDir,
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QPixmap, QRadialGradient, QRegion
from PySide6.QtWidgets import QWidget

from ocr import is_ocr_eligible

log = logging.getLogger(__name__)

#: Pointer travel (px) before a press is treated as a drag rather than a click.
DRAG_THRESHOLD_PX = 5

_WM_HOTKEY = 0x0312
_HWND_TOPMOST = -1
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_SWP_SHOWWINDOW = 0x0040


def _set_topmost(hwnd: int) -> None:
    """Re-assert top-most z-order without moving, resizing, or focusing the window."""
    if not hwnd:
        return
    try:
        user32 = ctypes.windll.user32
        user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
        user32.SetWindowPos(
            wintypes.HWND(hwnd),
            wintypes.HWND(_HWND_TOPMOST),
            0,
            0,
            0,
            0,
            _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE,
        )
    except Exception:  # pragma: no cover - defensive, must never kill the mascot
        log.debug("SetWindowPos failed", exc_info=True)


def _shadow_region(shadow: tuple[int, int, int, int, int]) -> QRegion:
    """The input/paint region the contact shadow needs.

    An ellipse rather than the bounding rectangle: the corners of that rectangle are fully
    transparent, and handing them to the mask would make him swallow clicks over four patches of
    bare taskbar for no visual benefit whatsoever.
    """
    left, top, width, height, _alpha = shadow
    return QRegion(QRect(left, top, max(1, width), max(1, height)), QRegion.Ellipse)


class MascotWindow(QWidget):
    """Draws one sprite frame and reports pointer interaction."""

    clicked = Signal()
    drag_started = Signal()
    dragged = Signal(QPoint)  # requested new top-left, in global screen coordinates
    drag_finished = Signal()
    hover_changed = Signal(bool)
    files_dropped = Signal(list)  # list[str] of local paths
    text_dropped = Signal(str)
    #: A single image, dropped with Shift held - read its text instead of discussing the file.
    #: A separate signal rather than a flag on files_dropped, so every other drop (multiple
    #: files, non-images, no modifier) reaches files_dropped completely unchanged.
    image_ocr_requested = Signal(str)
    double_clicked = Signal()
    context_menu_requested = Signal(QPoint)  # global position
    scrolled = Signal()
    hotkey_pressed = Signal(int)             # the hotkey id from RegisterHotKey

    def __init__(self, width: int, height: int) -> None:
        super().__init__(None)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool  # keeps us out of the taskbar and Alt+Tab
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_AlwaysShowToolTips, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setAcceptDrops(True)  # drop a file or some text on him to ask Claude about it
        self.resize(width, height)

        self._pixmap: QPixmap | None = None
        self._mask: QRegion | None = None
        self._masked_height = -1
        self._shadow: tuple[int, int, int, int, int] | None = None
        #: Just the ellipse's rectangle. Tracked separately so a shadow that is only *fading*
        #: repaints without rebuilding the input region - see set_frame.
        self._shadow_geometry: tuple[int, int, int, int] | None = None
        self._press_global: QPoint | None = None
        self._press_offset = QPoint(0, 0)
        self._dragging = False
        self._hovered = False
        # One reusable animation. Creating a fresh one per fade would parent another object to the
        # widget every time and never free it.
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setEasingCurve(QEasingCurve.InOutQuad)
        self._fade.finished.connect(self._on_fade_finished)
        self._fade_to_hidden = False

    # --- appearance ---------------------------------------------------------------

    def set_frame(
        self,
        pixmap: QPixmap,
        mask: QRegion | None = None,
        visible_height: int | None = None,
        shadow: tuple[int, int, int, int, int] | None = None,
    ) -> None:
        """Show a sprite frame. ``mask`` should be the frame's alpha region.

        ``visible_height`` shortens the *window* without touching the pixmap, so everything below
        it is simply never painted. That is how peeking over the taskbar works: he sinks, the
        window's bottom edge stays flush with the top of the bar, and his lower half falls outside
        the widget.

        The mask has to be clipped to that height explicitly. Qt does **not** intersect it with the
        widget rectangle - measured with ``GetWindowRgn``, a 96px sprite mask on a 68px window left
        26px of input region hanging below the window, over the taskbar, where he would have
        swallowed clicks meant for the bar. Which is precisely what the per-pixel mask exists to
        prevent, so it must not be reintroduced by the peek.

        ``shadow`` is ``(left, top, width, height, alpha)`` in window-local pixels, or None. The
        mask decides which pixels the window may *paint* as well as which it may receive clicks
        on, so the shadow's ellipse has to be unioned into it or the shadow is simply never drawn.
        That union is the one place this feature could regress the click-through guarantee, which
        is why ``shadow_mask.py`` compares the real input region reported by ``GetWindowRgn`` with
        the shadow on and off rather than taking it on trust.
        """
        height = pixmap.height()
        if visible_height is not None:
            height = max(1, min(height, int(visible_height)))
        self._pixmap = pixmap
        if self.width() != pixmap.width() or self.height() != height:
            self.resize(pixmap.width(), height)
        # Keyed on the height and the shadow's GEOMETRY as well as the source region: the same
        # frame at a new peek depth, or with the shadow spread by a landing, needs a freshly built
        # mask.
        #
        # Geometry, not the whole tuple. The shadow's alpha changes on every tick of a landing
        # while its rectangle does not, and keying on the alpha too meant a SetWindowRgn call per
        # tick for the whole 260ms - measured at 29 mask rebuilds for one landing against 13 with
        # the shadow off, which quietly undid the "repaint only when the frame changes"
        # optimisation. A fading shadow needs a repaint; it does not need a new input region.
        geometry = shadow[:4] if shadow is not None else None
        if mask is not None and (
            mask is not self._mask
            or height != self._masked_height
            or geometry != self._shadow_geometry
        ):
            self._mask = mask
            self._masked_height = height
            clipped = (
                mask if height >= pixmap.height()
                else mask.intersected(QRegion(0, 0, pixmap.width(), height))
            )
            if shadow is not None:
                clipped = clipped.united(_shadow_region(shadow))
            # Clipped AFTER the union as well as before, so the shadow can never push input region
            # past the window's own edge - the exact failure the peek fix above exists to prevent.
            clipped = clipped.intersected(QRegion(0, 0, pixmap.width(), height))
            # An empty region would make the window vanish and become unclickable.
            self.setMask(clipped if not clipped.isEmpty() else QRegion(self.rect()))
            self._shadow_geometry = geometry
        self._shadow = shadow
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self._pixmap is None:
            return
        painter = QPainter(self)
        if self._shadow is not None:
            self._paint_shadow(painter, self._shadow)
        # Everything else here is pixel art and must never be resampled or smoothed.
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        painter.drawPixmap(0, 0, self._pixmap)

    @staticmethod
    def _paint_shadow(painter: QPainter, shadow: tuple[int, int, int, int, int]) -> None:
        """A soft dark ellipse under his feet. The only thing drawn here that is not pixel art.

        Deliberately black-with-alpha rather than a colour: it has to sit on a taskbar whose
        colour is unknown and can change under him. Multiplying down whatever is behind is right
        on a light bar and correctly almost invisible on a dark one, which is also how a real
        shadow behaves on a dark floor.
        """
        left, top, width, height, alpha = shadow
        painter.setRenderHint(QPainter.Antialiasing, True)
        centre = QRectF(left, top, width, height).center()
        gradient = QRadialGradient(centre, max(1.0, width / 2.0))
        gradient.setColorAt(0.0, QColor(0, 0, 0, alpha))
        gradient.setColorAt(0.55, QColor(0, 0, 0, int(alpha * 0.62)))
        gradient.setColorAt(1.0, QColor(0, 0, 0, 0))
        # QRadialGradient is circular. Squashing the painter vertically turns the circle into the
        # ellipse we want without needing a second set of colour stops for the short axis.
        painter.save()
        painter.translate(centre)
        painter.scale(1.0, height / max(1.0, float(width)))
        painter.translate(-centre)
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(centre, width / 2.0, width / 2.0)
        painter.restore()

    # --- z-order and visibility ---------------------------------------------------

    def reassert_topmost(self) -> None:
        if self.isVisible():
            _set_topmost(int(self.winId()))

    def fade_to(self, opacity: float, duration_ms: int = 260) -> None:
        """Animate opacity; hides the window entirely once it reaches zero."""
        target = max(0.0, min(1.0, opacity))
        self._fade.stop()
        if target > 0.0 and not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
            self.reassert_topmost()
        self._fade_to_hidden = target <= 0.0
        self._fade.setDuration(max(0, duration_ms))
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(target)
        self._fade.start()

    def _on_fade_finished(self) -> None:
        if self._fade_to_hidden:
            self.hide()

    # --- pointer ------------------------------------------------------------------

    def enterEvent(self, event) -> None:  # noqa: N802
        if not self._hovered:
            self._hovered = True
            self.hover_changed.emit(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hovered:
            self._hovered = False
            self.hover_changed.emit(False)
        super().leaveEvent(event)

    def nativeEvent(self, event_type, message):  # noqa: N802
        """Catch WM_HOTKEY for hotkeys registered against this window.

        Only this window's messages come through here, unlike an application-wide native event
        filter - which is why the hotkey is bound to our HWND rather than to the thread. The
        message id is read from a fixed offset instead of building a whole MSG struct, since this
        runs for every message the window does receive.
        """
        try:
            if event_type == b"windows_generic_MSG":
                address = int(message)
                # MSG on 64-bit: hwnd(8) then message(UINT) at offset 8, wParam at 16.
                if ctypes.c_uint.from_address(address + 8).value == _WM_HOTKEY:
                    self.hotkey_pressed.emit(
                        int(ctypes.c_size_t.from_address(address + 16).value)
                    )
                    return True, 0
        except Exception:
            log.debug("nativeEvent failed", exc_info=True)
        return False, 0

    # --- drops --------------------------------------------------------------------

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        data = event.mimeData()
        if data.hasUrls() or data.hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        data = event.mimeData()
        # Local files win over text: a file drag also carries its path as text, and the path is
        # the more useful of the two.
        if data.hasUrls():
            # toLocalFile gives forward slashes on Windows; these paths end up in a prompt, so
            # hand over something that looks like a path the user would recognise.
            paths = [
                QDir.toNativeSeparators(u.toLocalFile())
                for u in data.urls()
                if u.isLocalFile()
            ]
            if paths:
                # He never moves or deletes a dropped file - only ever reads its path (and, for
                # OCR, its bytes). Windows Explorer proposes DROPEFFECT_MOVE when Shift is held,
                # and honouring that would tell Explorer the drop "moved" the file, so it deletes
                # the source afterwards. Forcing CopyAction here is what keeps every drop - Shift
                # held or not - from silently destroying the user's original file. Note this has
                # to be event.accept(), not acceptProposedAction() - the latter resets dropAction
                # back to whatever was proposed, undoing the very thing setDropAction just did.
                event.setDropAction(Qt.CopyAction)
                event.accept()
                # The modifier has to be read here, at drop time - by the time an async OCR
                # result comes back and main.py could otherwise check it, Shift is long since
                # released.
                if (
                    len(paths) == 1
                    and is_ocr_eligible(paths[0])
                    and event.modifiers() & Qt.ShiftModifier
                ):
                    self.image_ocr_requested.emit(paths[0])
                else:
                    self.files_dropped.emit(paths)
                return
        if data.hasText() and data.text().strip():
            event.acceptProposedAction()
            self.text_dropped.emit(data.text())
            return
        event.ignore()

    def wheelEvent(self, event) -> None:  # noqa: N802
        """Scrolling the wheel over him spins him. Accepted so it does not fall through.

        There is no reason for this at all - it is just funny. Accepting the event matters though:
        letting it propagate would scroll whatever is underneath him on the taskbar.
        """
        self.scrolled.emit()
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._press_global = None  # the pending single-click release must not also fire
            self._dragging = False
            self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.RightButton:
            self.context_menu_requested.emit(event.globalPosition().toPoint())
            event.accept()
            return
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        global_pos = event.globalPosition().toPoint()
        self._press_global = global_pos
        self._press_offset = global_pos - self.frameGeometry().topLeft()
        self._dragging = False
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press_global is None:
            super().mouseMoveEvent(event)
            return
        global_pos = event.globalPosition().toPoint()
        if not self._dragging:
            delta = global_pos - self._press_global
            if max(abs(delta.x()), abs(delta.y())) < DRAG_THRESHOLD_PX:
                event.accept()
                return
            self._dragging = True
            self.drag_started.emit()
        self.dragged.emit(global_pos - self._press_offset)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton or self._press_global is None:
            super().mouseReleaseEvent(event)
            return
        was_dragging = self._dragging
        self._press_global = None
        self._dragging = False
        if was_dragging:
            self.drag_finished.emit()
        else:
            self.clicked.emit()
        event.accept()
