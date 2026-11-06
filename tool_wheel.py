"""The tool wheel: right-click him and his tools fan out around him, the way a game character's
weapon wheel does.

Two ways to use it, and both have to work, because people reach for it both ways:

* **Hold and flick** - press the right button on him, move toward a tool, let go. The tool under
  the pointer when you release is the one you get, and releasing back over him cancels. This is
  the game gesture, and it is one motion.
* **Click, then click** - a quick right-click leaves the wheel open, and a second click on a tool
  picks it. Clicking anywhere else, Esc, or right-clicking again closes it.

The window is a ``Qt.Popup``, which is the single decision everything else rests on. It is what
``QMenu`` uses, and it brings three things no other flag combination gives a translucent window on
Windows: an application-wide mouse grab from the moment it shows (so the release of the press
that opened it - over a different window entirely - still arrives here), closing on a click
outside, and the keyboard. ``focus_timer``'s ``ToolTip | WA_ShowWithoutActivating`` is the right
template for how this is *painted* and precisely the wrong one for how it takes input.

Selection is by **angle**, not by hitting the icon. You flick toward a tool and get it, the way a
game wheel works, rather than having to land the pointer inside a 40px circle mid-gesture. A dead
zone over him means "nothing", so letting go without having moved away cancels instead of
picking whichever tool happened to be nearest.

**He lives on the taskbar, so a full circle would be half offscreen.** The tools sit on an arc
that faces the middle of the screen - straight up for him in the middle of a bottom taskbar,
tilted inward at either end of it - and if it still does not fit, the arc widens its radius and
tightens its spacing until it does. ``plan_layout`` is a pure function so the tests can check
that for him in every corner without ever showing a window.

This module knows nothing about what any tool *does*. It is handed items and emits the key of the
chosen one; ``main.NibApp`` maps keys to the handlers that already existed for the tray and the
hotkeys. The icons are painted in code, for the same reason the sprites and the sounds are: no
binary art is committed to this repository.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from bubble import ACCENT, _theme

#: The base geometry, in unscaled pixels - everything is multiplied by ``ui_scale``.
BUTTON_D = 40
RING_GAP = 14          # between his sprite's edge and the inside of the buttons
LABEL_GAP = 16         # between the outside of the ring and the label pill
#: The angular spacing between tools when there is room for it. Wide enough that a flick reliably
#: lands on the one you meant; any wider and eight tools would wrap most of the way round him.
BASE_STEP_DEG = 30.0
#: Never let the arc wrap further than this. Past it, the two ends start to point at each other and
#: "flick toward the tool" stops meaning anything.
MAX_SPAN_DEG = 210.0
#: Moving this far from the press, or holding this long, turns the opening release into a pick.
#: Under both, it was a click - and the wheel stays open so you can choose with a second one.
HOLD_MOVE_PX = 12
HOLD_MS = 260
OPEN_MS = 170
SCREEN_MARGIN = 4
#: How far past each end of the arc, in slots, the scroll chevrons sit.
CHEVRON_SLOT = 0.62
#: How many tools the arc shows at once. More than this and the ring *scrolls*: each notch of the
#: mouse wheel steps it round by exactly one tool, the way a game's weapon wheel cycles. Eight is
#: what fits a comfortable flick apart even in a corner - see plan_layout.
MAX_VISIBLE = 8
#: One physical notch on a standard mouse wheel. Trackpads and smooth-scrolling mice send much
#: smaller deltas, which are accumulated until they add up to a notch, so one gentle swipe is one
#: step rather than a blur of them.
SCROLL_NOTCH = 120
SLIDE_MS = 130
#: The most slots a single slide covers. A hard-spun wheel can deliver a dozen notches at once; the
#: ring still turns by all of them, but the animation runs at most this far rather than whirling
#: past tools too fast to read.
MAX_SLIDE = 3.0

HOT_COLOR = "#E0483A"     # the same red as the tray's device dot and the focus timer's last minute
MUTED_COLOR = "#F5BF4F"   # the same amber as the live-but-muted tray dot
#: The share of a button a costume thumbnail may fill. It is scaled by a whole number only, so most
#: pieces land well under it; the widest (wings, cape, earmuffs) stay at 1x and clip at the rim.
THUMB_FILL = 0.72


@dataclass(frozen=True)
class WheelItem:
    """One tool. ``state`` draws a ring: "active" (accent), "hot" (red) or "muted" (amber)."""

    key: str
    label: str
    icon: str
    state: str = ""
    #: Which part of the ring it belongs to - "Tools", "Him", "App" - shown in the idle label, so
    #: scrolling tells you where on the ring you have got to rather than just changing icons.
    group: str = ""
    #: Drawn instead of the icon when set - a costume's thumbnail in the wardrobe. Not compared:
    #: two items are the same tool whatever picture they carry.
    pixmap: QPixmap | None = field(default=None, compare=False)


@dataclass(frozen=True)
class Layout:
    """Where the tools go: the arc's radius, and each tool's angle in degrees, maths-style.

    Maths-style means 0 is to the right and 90 is straight *up* - Qt's y axis points down, so
    every conversion to a screen point below flips the sign of the vertical component.
    """

    radius: float
    angles: tuple[float, ...]
    facing: float

    def point(self, centre: QPointF, index: int, radius: float | None = None) -> QPointF:
        r = self.radius if radius is None else radius
        a = math.radians(self.angles[index])
        return QPointF(centre.x() + r * math.cos(a), centre.y() - r * math.sin(a))

    def angle_at(self, slot: float) -> float:
        """The angle of a *fractional* slot - between two tools, or one step beyond either end.

        The slots are an even arithmetic progression, so this extends it in both directions.
        That is what lets a tool slide smoothly from one slot to the next while the ring steps,
        and slide in from - or out past - the end of the arc.
        """
        if len(self.angles) < 2:
            return self.angles[0] if self.angles else self.facing
        step = self.angles[1] - self.angles[0]
        return self.angles[0] + step * slot

    def point_at(self, centre: QPointF, slot: float, radius: float | None = None) -> QPointF:
        r = self.radius if radius is None else radius
        a = math.radians(self.angle_at(slot))
        return QPointF(centre.x() + r * math.cos(a), centre.y() - r * math.sin(a))


def _angle_diff(a: float, b: float) -> float:
    """Smallest absolute difference between two angles in degrees."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


def plan_layout(
    count: int,
    centre: QPointF,
    screen: QRect,
    inner_radius: float,
    button_d: float,
    chevrons: bool = False,
) -> Layout:
    """Fit ``count`` tools on an arc round ``centre`` that stays inside ``screen``.

    With ``chevrons`` - the ring scrolls - the two "more this way" arrowheads past either end of
    the arc have to fit too. In a corner the arc's ends point along the screen edge, and one step
    further round is off it: the tools would all fit and the hint that there are more would not.

    Prefers an arc facing the middle of the screen at the normal radius and spacing. If that runs
    off an edge - him at the far end of the taskbar, in the corner - it tries facings further from
    the preferred one, then larger radii with proportionally tighter angular spacing, so the tools
    keep their physical spacing on a wider arc rather than overlapping on the same one. The first
    arrangement where every button is fully onscreen wins; if none is, the least-bad one does.
    """
    if count <= 0:
        return Layout(inner_radius, (), 90.0)

    sx = screen.left() + screen.width() / 2.0
    sy = screen.top() + screen.height() / 2.0
    dx, dy = sx - centre.x(), centre.y() - sy
    preferred = math.degrees(math.atan2(dy, dx)) if (dx or dy) else 90.0

    button_r = button_d / 2.0
    bounds = QRectF(screen).adjusted(SCREEN_MARGIN, SCREEN_MARGIN, -SCREEN_MARGIN, -SCREEN_MARGIN)

    def onscreen_misses(radius: float, angles: tuple[float, ...]) -> int:
        misses = 0
        for a in angles:
            r = math.radians(a)
            px = centre.x() + radius * math.cos(r)
            py = centre.y() - radius * math.sin(r)
            if not bounds.contains(QRectF(px - button_r, py - button_r, button_d, button_d)):
                misses += 1
        if chevrons and len(angles) > 1:
            step = angles[1] - angles[0]
            arrow = button_d / 8.0  # the arrowhead's size, as ToolWheel._paint_chevrons draws it
            for slot in (-CHEVRON_SLOT, len(angles) - 1 + CHEVRON_SLOT):
                r = math.radians(angles[0] + step * slot)
                px = centre.x() + radius * math.cos(r)
                py = centre.y() - radius * math.sin(r)
                if not bounds.contains(QRectF(px - arrow, py - arrow, arrow * 2, arrow * 2)):
                    misses += 1
        return misses

    def angles_for(facing: float, step: float) -> tuple[float, ...]:
        start = facing + step * (count - 1) / 2.0
        angles = tuple(start - step * i for i in range(count))
        # Slot 0 goes at whichever end of the arc is further LEFT on screen. Counting one way round
        # from the facing puts it on the left for an arc above him but on the right for one hanging
        # below him - where Left would then move the highlight right.
        if len(angles) > 1 and (math.cos(math.radians(angles[0]))
                                > math.cos(math.radians(angles[-1])) + 1e-9):
            angles = angles[::-1]
        return angles

    base_step = min(BASE_STEP_DEG, MAX_SPAN_DEG / max(1, count - 1))
    best: tuple[int, float, Layout] | None = None
    # Each step out, the arc grows and its angular spacing shrinks by the same factor, so the
    # distance between neighbouring buttons stays what it was at the normal size.
    # The last two only matter in a corner, where barely a quarter of the circle is onscreen -
    # eight tools need a wide arc to sit a comfortable flick apart in that little angle.
    for grow in (1.0, 1.3, 1.65, 2.1, 2.6, 3.2):
        radius = inner_radius * grow
        step = base_step / grow
        min_step = math.degrees((button_d + 4) / radius)
        step = max(step, min_step)
        if step * (count - 1) > MAX_SPAN_DEG:
            continue
        for offset in range(0, 181, 5):
            for facing in ((preferred,) if offset == 0 else (preferred + offset, preferred - offset)):
                angles = angles_for(facing, step)
                misses = onscreen_misses(radius, angles)
                layout = Layout(radius, angles, facing % 360.0)
                if misses == 0:
                    return layout
                score = misses * 1000 + offset
                if best is None or score < best[0] + best[1]:
                    best = (misses * 1000, offset, layout)
    return best[2] if best else Layout(inner_radius, angles_for(preferred, base_step), preferred)


def pick(layout: Layout, centre: QPointF, point: QPointF, dead_zone: float) -> int | None:
    """Which tool ``point`` is aiming at, by angle - or None in the dead zone or off the arc."""
    if not layout.angles:
        return None
    dx, dy = point.x() - centre.x(), centre.y() - point.y()
    distance = math.hypot(dx, dy)
    if distance < dead_zone:
        return None
    if distance > layout.radius * 2.4 + 60:
        return None
    angle = math.degrees(math.atan2(dy, dx))
    index = min(range(len(layout.angles)), key=lambda i: _angle_diff(angle, layout.angles[i]))
    step = (_angle_diff(layout.angles[0], layout.angles[1]) if len(layout.angles) > 1
            else BASE_STEP_DEG)
    # Past either end of the arc by more than half a gap is "not aiming at anything" - otherwise
    # pointing straight down would still pick whichever end tool was closest.
    if _angle_diff(angle, layout.angles[index]) > max(step * 0.75, 18.0):
        return None
    return index


# --- icons --------------------------------------------------------------------------
# Drawn in a 24x24 design space and scaled into the button, as a single line weight, so they read
# as one set. Each takes the painter already translated and scaled to that space.


def _icon_ask(p: QPainter) -> None:
    path = QPainterPath()
    path.addRoundedRect(QRectF(3, 4, 18, 12.5), 4, 4)
    p.drawPath(path)
    tail = QPainterPath(QPointF(8, 16.5))
    tail.lineTo(6.5, 21)
    tail.lineTo(12, 16.5)
    p.drawPath(tail)
    for x in (8.5, 12, 15.5):
        p.drawPoint(QPointF(x, 10.3))


def _icon_again(p: QPainter) -> None:
    p.drawArc(QRectF(5, 5, 14, 14), 60 * 16, 290 * 16)
    head = QPainterPath(QPointF(16.2, 3.6))
    head.lineTo(16.6, 7.6)
    head.lineTo(12.6, 7.8)
    p.drawPath(head)


def _icon_measure(p: QPainter) -> None:
    p.save()
    p.translate(12, 12)
    p.rotate(-35)
    p.drawRoundedRect(QRectF(-10, -3.6, 20, 7.2), 1.5, 1.5)
    for i, x in enumerate((-6.5, -3.5, -0.5, 2.5, 5.5)):
        p.drawLine(QPointF(x, -3.6), QPointF(x, -3.6 + (3.0 if i % 2 == 0 else 1.8)))
    p.restore()


def _icon_read(p: QPainter) -> None:
    # Scan brackets round lines of text - "read the text out of this", not "a picture".
    for (x, y, sx, sy) in ((3, 3, 1, 1), (21, 3, -1, 1), (3, 21, 1, -1), (21, 21, -1, -1)):
        p.drawLine(QPointF(x, y), QPointF(x + 5 * sx, y))
        p.drawLine(QPointF(x, y), QPointF(x, y + 5 * sy))
    p.drawLine(QPointF(7.5, 9), QPointF(16.5, 9))
    p.drawLine(QPointF(7.5, 12.5), QPointF(16.5, 12.5))
    p.drawLine(QPointF(7.5, 16), QPointF(13, 16))


def _icon_focus(p: QPainter) -> None:
    # Headphones - the same thing he puts on for a focus session, so the icon means what it does.
    band = QPainterPath(QPointF(4.5, 15))
    band.cubicTo(4.5, 4.5, 19.5, 4.5, 19.5, 15)
    p.drawPath(band)
    p.drawRoundedRect(QRectF(3, 13.5, 4.5, 7), 1.6, 1.6)
    p.drawRoundedRect(QRectF(16.5, 13.5, 4.5, 7), 1.6, 1.6)


def _icon_pocket(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(5, 4.5, 14, 17), 2, 2)
    p.drawRoundedRect(QRectF(9, 2.5, 6, 4), 1.2, 1.2)
    p.drawLine(QPointF(8.5, 11), QPointF(15.5, 11))
    p.drawLine(QPointF(8.5, 14.5), QPointF(15.5, 14.5))
    p.drawLine(QPointF(8.5, 18), QPointF(12.5, 18))


def _icon_mic(p: QPainter, muted: bool = False) -> None:
    p.drawRoundedRect(QRectF(9, 2.5, 6, 11), 3, 3)
    p.drawArc(QRectF(5.5, 5.5, 13, 12), 180 * 16, 180 * 16)
    p.drawLine(QPointF(12, 17.5), QPointF(12, 21))
    p.drawLine(QPointF(8.5, 21), QPointF(15.5, 21))
    if muted:
        p.drawLine(QPointF(4, 3.5), QPointF(20, 20.5))


def _icon_more(p: QPainter) -> None:
    pen = p.pen()
    pen.setWidthF(pen.widthF() * 1.9)
    p.setPen(pen)
    for x in (6, 12, 18):
        p.drawPoint(QPointF(x, 12))


def _icon_sparkle(p: QPainter) -> None:
    star = QPainterPath(QPointF(12, 3))
    star.quadTo(13, 11, 21, 12)
    star.quadTo(13, 13, 12, 21)
    star.quadTo(11, 13, 3, 12)
    star.quadTo(11, 11, 12, 3)
    p.drawPath(star)
    p.drawLine(QPointF(18.5, 3.5), QPointF(18.5, 6.5))
    p.drawLine(QPointF(17, 5), QPointF(20, 5))


def _icon_treat(p: QPainter) -> None:
    # A round biscuit with a bite out of the top right and three chips - the one he eats.
    rim = QRectF(4, 4, 16, 16)
    biscuit = QPainterPath()
    biscuit.arcMoveTo(rim, 80)
    biscuit.arcTo(rim, 80, 290)                       # round to 10 degrees, leaving the bite
    biscuit.quadTo(QPointF(14.5, 9.5), QPointF(13.4, 4.1))
    p.drawPath(biscuit)
    for x, y in ((9, 10.5), (13.5, 14.5), (8.5, 16)):
        p.drawPoint(QPointF(x, y))


def _icon_costume(p: QPainter) -> None:
    # A top hat - the costume that reads as "a costume" at 16px better than any other.
    p.drawLine(QPointF(3.5, 18), QPointF(20.5, 18))
    p.drawRoundedRect(QRectF(7, 5, 10, 13), 1.5, 1.5)
    p.drawLine(QPointF(7, 14), QPointF(17, 14))


def _icon_hide(p: QPainter) -> None:
    # A closed eye: the lid's curve and three lashes.
    p.drawArc(QRectF(3.5, 4, 17, 12), 200 * 16, 140 * 16)
    for x0, x1 in ((7, 5.5), (12, 12), (17, 18.5)):
        p.drawLine(QPointF(x0, 15.2 if x0 != 12 else 16), QPointF(x1, 19))


def _icon_wander(p: QPainter) -> None:
    # Two footprints, one ahead of the other.
    p.drawEllipse(QRectF(4.5, 10, 5.5, 8.5))
    p.drawEllipse(QRectF(14, 4.5, 5.5, 8.5))
    p.drawPoint(QPointF(7.2, 20.6))
    p.drawPoint(QPointF(16.8, 15.2))


def _icon_pause(p: QPainter) -> None:
    p.drawRoundedRect(QRectF(7, 5, 3.4, 14), 1, 1)
    p.drawRoundedRect(QRectF(13.6, 5, 3.4, 14), 1, 1)


def _icon_resume(p: QPainter) -> None:
    play = QPainterPath(QPointF(8, 5))
    play.lineTo(18.5, 12)
    play.lineTo(8, 19)
    play.closeSubpath()
    p.drawPath(play)


def _icon_ghost(p: QPainter) -> None:
    body = QPainterPath(QPointF(5, 20))
    body.lineTo(5, 11)
    body.cubicTo(5, 2.5, 19, 2.5, 19, 11)
    body.lineTo(19, 20)
    body.lineTo(16.7, 18)
    body.lineTo(14.3, 20)
    body.lineTo(12, 18)
    body.lineTo(9.7, 20)
    body.lineTo(7.3, 18)
    body.closeSubpath()
    p.drawPath(body)
    p.drawPoint(QPointF(9.5, 10.5))
    p.drawPoint(QPointF(14.5, 10.5))


def _icon_mode(p: QPainter) -> None:
    # Two arrows passing each other: switch between the modes.
    p.drawLine(QPointF(4, 8.5), QPointF(19, 8.5))
    p.drawLine(QPointF(15.5, 5), QPointF(19, 8.5))
    p.drawLine(QPointF(15.5, 12), QPointF(19, 8.5))
    p.drawLine(QPointF(20, 15.5), QPointF(5, 15.5))
    p.drawLine(QPointF(8.5, 12), QPointF(5, 15.5))
    p.drawLine(QPointF(8.5, 19), QPointF(5, 15.5))


def _icon_settings(p: QPainter) -> None:
    p.drawEllipse(QRectF(6.5, 6.5, 11, 11))
    p.drawEllipse(QRectF(10, 10, 4, 4))
    for k in range(8):
        a = math.radians(k * 45)
        p.drawLine(QPointF(12 + 5.5 * math.cos(a), 12 + 5.5 * math.sin(a)),
                   QPointF(12 + 8.5 * math.cos(a), 12 + 8.5 * math.sin(a)))


def _icon_tips(p: QPainter) -> None:
    # A light bulb.
    bulb = QPainterPath(QPointF(9.5, 15.5))
    bulb.cubicTo(5, 12.5, 5.5, 3, 12, 3)
    bulb.cubicTo(18.5, 3, 19, 12.5, 14.5, 15.5)
    p.drawPath(bulb)
    p.drawLine(QPointF(9.5, 15.5), QPointF(14.5, 15.5))
    p.drawLine(QPointF(10, 18.5), QPointF(14, 18.5))
    p.drawLine(QPointF(11, 21), QPointF(13, 21))


def _icon_home(p: QPainter) -> None:
    roof = QPainterPath(QPointF(3.5, 12))
    roof.lineTo(12, 4.5)
    roof.lineTo(20.5, 12)
    p.drawPath(roof)
    house = QPainterPath(QPointF(6.5, 10))
    house.lineTo(6.5, 20)
    house.lineTo(17.5, 20)
    house.lineTo(17.5, 10)
    p.drawPath(house)
    p.drawRect(QRectF(10.5, 14, 3, 6))


def _icon_guard(p: QPainter) -> None:
    # A shield with a keyhole: "nobody gets in", which is what the guard is for.
    shield = QPainterPath(QPointF(12, 2.8))
    shield.lineTo(19.5, 5.5)
    shield.lineTo(19.5, 11)
    shield.cubicTo(19.5, 16, 16, 19.5, 12, 21.2)
    shield.cubicTo(8, 19.5, 4.5, 16, 4.5, 11)
    shield.lineTo(4.5, 5.5)
    shield.closeSubpath()
    p.drawPath(shield)
    p.drawEllipse(QRectF(10.3, 8.6, 3.4, 3.4))
    p.drawLine(QPointF(12, 12), QPointF(12, 15.5))


def _icon_wardrobe(p: QPainter) -> None:
    # A coat hanger: the hook, then the shoulders - "the whole rail", where the top hat is one hat.
    hook = QPainterPath(QPointF(10, 7))
    hook.cubicTo(10, 4, 14, 4, 14, 7)
    hook.cubicTo(14, 9, 12, 9, 12, 11)
    p.drawPath(hook)
    body = QPainterPath(QPointF(12, 11))
    body.lineTo(3.5, 17)
    body.lineTo(20.5, 17)
    body.closeSubpath()
    p.drawPath(body)



def _icon_update(p: QPainter) -> None:
    # A down arrow inside a turning one: fetch the new version, then start again.
    p.drawArc(QRectF(3.5, 3.5, 17, 17), 70 * 16, -300 * 16)
    head = QPainterPath(QPointF(2.8, 5.2))
    head.lineTo(6.5, 5.5)
    head.lineTo(6.2, 9.3)
    p.drawPath(head)
    p.drawLine(QPointF(12, 7.5), QPointF(12, 15.5))
    tip = QPainterPath(QPointF(9, 12.6))
    tip.lineTo(12, 15.6)
    tip.lineTo(15, 12.6)
    p.drawPath(tip)


ICONS = {
    "guard": _icon_guard,
    "wardrobe": _icon_wardrobe,
    "sparkle": _icon_sparkle,
    "treat": _icon_treat,
    "costume": _icon_costume,
    "hide": _icon_hide,
    "wander": _icon_wander,
    "pause": _icon_pause,
    "resume": _icon_resume,
    "ghost": _icon_ghost,
    "mode": _icon_mode,
    "settings": _icon_settings,
    "tips": _icon_tips,
    "home": _icon_home,
    "update": _icon_update,
    "ask": _icon_ask,
    "again": _icon_again,
    "measure": _icon_measure,
    "read": _icon_read,
    "focus": _icon_focus,
    "pocket": _icon_pocket,
    "mic": _icon_mic,
    "mic_muted": lambda p: _icon_mic(p, muted=True),
    "more": _icon_more,
}


class ToolWheel(QWidget):
    """The wheel itself. ``chosen`` carries the picked item's key, emitted after it has closed.

    ``closed`` carries whether a tool was picked; ``aimed`` the screen point (x, y) of the tool
    being aimed at, or None.
    """

    chosen = Signal(str)
    closed = Signal(bool)
    aimed = Signal(object)

    def __init__(self, ui_scale: float = 1.0) -> None:
        super().__init__(None)
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self._s = max(1.0, float(ui_scale))
        self._theme = _theme()
        self._items: list[WheelItem] = []
        self._layout = Layout(0.0, (), 90.0)
        self._centre = QPointF()          # his centre, in this widget's coordinates
        self._dead_zone = 0.0
        self._hover: int | None = None
        self._pressed: int | None = None
        self._awaiting_release = False    # the release of the right-press that opened us
        self._press_global = QPoint()
        self._opened_at = 0.0
        self._progress = 1.0
        self._picked = False
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(OPEN_MS)
        self._anim.setEasingCurve(QEasingCurve.OutBack)
        self._anim.valueChanged.connect(self._on_anim)

        # Scrolling. ``_all`` is the whole ring; ``_items`` is only the window of it on the arc
        # right now, so everything that aims, picks and paints keeps working on slots exactly as it
        # did before the ring could move - a slot is a direction, and the tool in it is what changes.
        self._all: list[WheelItem] = []
        self._offset = 0
        self._scroll_accum = 0
        self._slide = 0.0               # fractional slots every tool still has to travel
        #: Tools sliding off the ends of the arc, each with the slot it is drawn at once the slide
        #: reaches zero. A list, because a second notch can arrive while the first tool is still on
        #: its way out, and that one must keep going rather than vanish.
        self._departing: list[tuple[WheelItem, float]] = []
        self._slide_anim = QVariantAnimation(self)
        self._slide_anim.setDuration(SLIDE_MS)
        self._slide_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._slide_anim.valueChanged.connect(self._on_slide)
        self._slide_anim.finished.connect(self._on_slide_done)

    # --- opening ------------------------------------------------------------------

    @property
    def button_d(self) -> float:
        return BUTTON_D * self._s

    @property
    def items(self) -> list[WheelItem]:
        """The tools on the arc right now, slot by slot."""
        return list(self._items)

    @property
    def all_items(self) -> list[WheelItem]:
        """Every tool on the ring, including the ones scrolled out of sight."""
        return list(self._all)

    @property
    def scrollable(self) -> bool:
        return len(self._all) > len(self._items)

    @property
    def layout_plan(self) -> Layout:
        return self._layout

    def open(
        self,
        items: list[WheelItem],
        centre: QPoint,
        screen: QRect,
        sprite_radius: float,
        press_global: QPoint | None = None,
    ) -> None:
        """Fan ``items`` out round ``centre`` (global coordinates), kept inside ``screen``.

        ``press_global`` is where the opening right-press happened, if one did - it is what tells
        a hold-and-flick apart from a click when that press is released.

        More than ``MAX_VISIBLE`` items and the first ones go on the arc and the rest wait on the
        ring, a scroll away. It always opens at the start of the ring rather than wherever you last
        left it: the hold-and-flick is muscle memory, and muscle memory needs the same tool in the
        same direction every time you reach for it.
        """
        self._theme = _theme()
        self._all = list(items)
        self._offset = 0
        self._scroll_accum = 0
        self._slide_anim.stop()
        self._slide = 0.0
        self._departing = []
        self._items = self._window()
        inner = sprite_radius + RING_GAP * self._s + self.button_d / 2.0
        self._layout = plan_layout(len(self._items), QPointF(centre), screen, inner, self.button_d,
                                   chevrons=self.scrollable)
        self._dead_zone = max(sprite_radius * 0.85, self._layout.radius * 0.42)

        # Big enough for the widest label the ring can show, pushed out as far as _paint_label
        # pushes it - so the clamp there is a safety net, never what decides where a label goes.
        extent = int(self._layout.radius + self.button_d * 1.2 + LABEL_GAP * self._s
                     + self._widest_label() + 24 * self._s + 8)
        self.setGeometry(QRect(centre.x() - extent, centre.y() - extent, extent * 2, extent * 2))
        self._centre = QPointF(extent, extent)

        self._hover = None
        self._pressed = None
        self._picked = False
        self._press_global = press_global if press_global is not None else QPoint(centre)
        self._awaiting_release = press_global is not None
        self._opened_at = time.monotonic()
        self._progress = 0.0
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.PopupFocusReason)
        self._anim.stop()
        self._anim.start()

    def _on_anim(self, value) -> None:
        self._progress = float(value)
        self.update()

    def _widest_label(self) -> float:
        font = QFont()
        font.setPixelSize(int(12 * self._s))
        font.setBold(True)
        metrics = QFontMetrics(font)
        texts = [item.label for item in self._all] + ["Choose a tool", "Scroll for more"]
        texts += [f"{item.group} · scroll for more" for item in self._all if item.group]
        return max(metrics.horizontalAdvance(t) for t in texts) + 22 * self._s

    # --- scrolling ------------------------------------------------------------------

    def _window(self) -> list[WheelItem]:
        count = len(self._all)
        visible = min(count, MAX_VISIBLE)
        return [self._all[(self._offset + i) % count] for i in range(visible)]

    def step(self, direction: int) -> bool:
        """Turn the ring one tool. +1 brings in the next tool at the far end; -1 the previous one.

        It wraps rather than stopping, so there is never a dead end to scroll back out of. The
        slots do not move - only what is in them - so whatever the pointer is aiming at simply
        becomes the next tool along, which is how a game wheel feels to scroll through.
        Returns whether it turned: with everything already on the arc there is nothing to show.
        """
        if not self.scrollable or direction == 0 or self._picked:
            return False
        direction = 1 if direction > 0 else -1
        # Stopped before anything below is rebuilt: stopping an animation that is at its end emits
        # finished(), and _on_slide_done would then wipe the slide and departing tools set up here.
        self._slide_anim.stop()
        before = self._items
        self._offset = (self._offset + direction) % len(self._all)
        self._items = self._window()
        # Every tool is drawn at (its slot + _slide). Adding ``direction`` to _slide as the slots
        # shift by ``direction`` leaves every drawn position exactly where it was this frame, so a
        # notch arriving mid-slide - or several in one event, from a fast-spun wheel - carries on
        # from wherever things are instead of snapping back. The tool leaving the arc joins the
        # departing list one slot past the end it left by, and the ones already leaving shift
        # along with everything else.
        shift = -float(direction)
        self._departing = [(item, slot + shift) for item, slot in self._departing]
        if direction > 0:
            self._departing.append((before[0], -1.0))
        else:
            self._departing.append((before[-1], float(len(before))))
        visible = {id(item) for item in self._items}
        last = len(self._items) - 1
        self._departing = [
            (item, slot) for item, slot in self._departing
            # Back on the arc (scrolled away and straight back again) it is drawn there instead;
            # past the end of every possible slide it can never be seen again.
            if id(item) not in visible and -MAX_SLIDE - 1 <= slot <= last + MAX_SLIDE + 1
        ]
        self._slide = max(-MAX_SLIDE, min(MAX_SLIDE, self._slide + direction))
        self._slide_anim.setStartValue(self._slide)
        self._slide_anim.setEndValue(0.0)
        self._slide_anim.start()
        self.update()
        return True

    def _on_slide(self, value) -> None:
        self._slide = float(value)
        self.update()

    def _on_slide_done(self) -> None:
        self._slide = 0.0
        self._departing = []
        self.update()

    def wheelEvent(self, event) -> None:  # noqa: N802
        if not self.scrollable:
            event.accept()  # swallowed, not passed on: the wheel is open, so it owns the scroll
            return
        delta = event.angleDelta()
        # Vertical first; a tilt wheel or a sideways trackpad swipe steps it too. Qt reports both
        # axes negative for "onward" - scrolling down, scrolling right - so they share a sign.
        amount = delta.y() if delta.y() else delta.x()
        if self._scroll_accum and (amount > 0) != (self._scroll_accum > 0):
            self._scroll_accum = 0  # changed direction mid-notch: start counting afresh
        self._scroll_accum += amount
        while abs(self._scroll_accum) >= SCROLL_NOTCH:
            # Scrolling *down* moves on to the next tools, the same way scrolling a list does.
            self.step(1 if self._scroll_accum < 0 else -1)
            self._scroll_accum += SCROLL_NOTCH if self._scroll_accum < 0 else -SCROLL_NOTCH
        event.accept()

    # --- picking --------------------------------------------------------------------

    def aim_at(self, local: QPointF) -> int | None:
        """The item ``local`` (widget coordinates) is aiming at. Public so the tests can drive it."""
        return pick(self._layout, self._centre, local, self._dead_zone)

    def _set_hover(self, hover: int | None) -> None:
        """Highlight slot ``hover`` (or nothing), and say where that tool is, so he can look."""
        if hover == self._hover:
            return
        self._hover = hover
        self.update()
        point = None
        if hover is not None:
            p = self.mapToGlobal(self._layout.point(self._centre, hover).toPoint())
            point = (p.x(), p.y())
        self.aimed.emit(point)

    def _choose(self, index: int) -> None:
        if self._picked or not (0 <= index < len(self._items)):
            return
        self._picked = True
        key = self._items[index].key
        self.hide()
        # After the popup's grab is gone: a file dialog, the no-focus ruler overlay or the ask
        # bubble opening while a popup still holds the mouse would all misbehave in their own ways.
        QTimer.singleShot(0, lambda: self.chosen.emit(key))

    def hideEvent(self, event) -> None:  # noqa: N802
        self._anim.stop()
        self.closed.emit(self._picked)
        super().hideEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self._set_hover(self.aim_at(event.position()))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if not self.rect().contains(event.position().toPoint()):
            self.close()  # Qt.Popup already does this; explicit so behaviour never depends on it
            return
        if event.button() == Qt.RightButton and not self._awaiting_release:
            self.close()  # right-click again: put the wheel away
            return
        self._pressed = self.aim_at(event.position())
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        target = self.aim_at(event.position())
        if self._awaiting_release:
            self._awaiting_release = False
            moved = (event.globalPosition().toPoint() - self._press_global).manhattanLength()
            held_ms = (time.monotonic() - self._opened_at) * 1000.0
            if moved <= HOLD_MOVE_PX and held_ms <= HOLD_MS:
                return  # a click: stay open and let the next click choose
            if target is None:
                self.close()  # let go back over him, or off the arc: the game-wheel cancel
            else:
                self._choose(target)
            return
        if target is not None and target == self._pressed:
            self._choose(target)
        elif target is None:
            self.close()
        self._pressed = None

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        count = len(self._items)
        if key == Qt.Key_Escape:
            self.close()
        elif key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and self._hover is not None:
            self._choose(self._hover)
        elif key in (Qt.Key_Left, Qt.Key_Up) and count:
            self._set_hover(0 if self._hover is None else (self._hover - 1) % count)
        elif key in (Qt.Key_Right, Qt.Key_Down, Qt.Key_Tab) and count:
            self._set_hover(0 if self._hover is None else (self._hover + 1) % count)
        elif key in (Qt.Key_PageDown, Qt.Key_BracketRight):
            self.step(1)
        elif key in (Qt.Key_PageUp, Qt.Key_BracketLeft):
            self.step(-1)
        elif Qt.Key_1 <= key <= Qt.Key_9 and key - Qt.Key_1 < count:
            self._choose(key - Qt.Key_1)
        else:
            super().keyPressEvent(event)

    # --- painting -------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        t = self._theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        # Alpha 1 over the whole window, invisible but not *transparent*: a layered window lets
        # clicks fall straight through fully transparent pixels, and a click between two tools has
        # to land here so it can close the wheel instead of reaching the desktop underneath.
        p.fillRect(self.rect(), QColor(0, 0, 0, 1))

        if not self._items:
            return
        progress = max(0.0, self._progress)
        fade = min(1.0, progress * 1.6)
        radius = self._layout.radius * progress
        bd = self.button_d

        # The track: one soft band the buttons sit on, so it reads as a wheel and not a scatter.
        if len(self._items) > 1:
            first, last = self._layout.angles[0], self._layout.angles[-1]
            track = QColor(t["bg"])
            track.setAlphaF(0.55 * fade)
            pen = QPen(track, bd + 12 * self._s, Qt.SolidLine, Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            box = QRectF(self._centre.x() - radius, self._centre.y() - radius, radius * 2, radius * 2)
            p.drawArc(box, int(last * 16), int((first - last) * 16))

        if self.scrollable:
            self._paint_chevrons(p, fade, radius)

        last_slot = len(self._items) - 1
        # Tools sliding off the ends first, so the ones sliding into their places draw over them.
        for item, base in self._departing:
            slot = base + self._slide
            self._paint_button(p, item, slot, radius, fade * self._edge_fade(slot, last_slot),
                               hovered=False, grow=1.0)

        for i, item in enumerate(self._items):
            # A slight stagger on opening: each tool leaves him a moment after the one before it.
            local = max(0.0, min(1.2, progress * 1.15 - i * 0.025))
            slot = i + self._slide
            self._paint_button(p, item, slot, self._layout.radius * local,
                               fade * self._edge_fade(slot, last_slot),
                               hovered=i == self._hover, grow=min(1.0, 0.55 + local * 0.45))
        p.setOpacity(1.0)

        self._paint_label(p, fade)

    @staticmethod
    def _edge_fade(slot: float, last_slot: int) -> float:
        """Full strength on the arc; fading out over the one slot past either end of it."""
        if slot < 0:
            return max(0.0, 1.0 + slot)
        if slot > last_slot:
            return max(0.0, 1.0 - (slot - last_slot))
        return 1.0

    def _paint_button(self, p: QPainter, item: WheelItem, slot: float, radius: float,
                      opacity: float, *, hovered: bool, grow: float) -> None:
        if opacity <= 0.0:
            return
        t = self._theme
        centre = self._layout.point_at(self._centre, slot, radius)
        size = self.button_d * (1.14 if hovered else 1.0) * grow
        rect = QRectF(centre.x() - size / 2, centre.y() - size / 2, size, size)

        p.setOpacity(opacity)
        p.setPen(QPen(QColor(t["border"]), 1.0))
        p.setBrush(QColor(ACCENT) if hovered else QColor(t["bg"]))
        p.drawEllipse(rect)

        thumbnail = item.pixmap is not None and not item.pixmap.isNull()
        if thumbnail:
            # Under the ring rather than over it: a wide piece reaches the rim, and the ring is how
            # the wardrobe says which one he is wearing.
            self._paint_thumbnail(p, item.pixmap, centre, size, rect)

        ring = {"active": ACCENT, "hot": HOT_COLOR, "muted": MUTED_COLOR}.get(item.state)
        if ring:
            inset = 2.5 * self._s
            p.setPen(QPen(QColor("#ffffff" if hovered else ring), 2.4 * self._s))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(rect.adjusted(inset, inset, -inset, -inset))

        draw = None if thumbnail else ICONS.get(item.icon)
        if draw is not None:
            p.save()
            icon = size * 0.52
            p.translate(centre.x() - icon / 2, centre.y() - icon / 2)
            p.scale(icon / 24.0, icon / 24.0)
            colour = QColor("#ffffff") if hovered else QColor(t["text"])
            p.setPen(QPen(colour, 1.9, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            draw(p)
            p.restore()

    @staticmethod
    def _paint_thumbnail(p: QPainter, pixmap: QPixmap, centre: QPointF, size: float,
                         button: QRectF) -> None:
        """A costume's own pixels, at a whole-number scale and on whole pixels (CONTRIBUTING rule 6),
        clipped to the button so the widest pieces lose only their corners at the rim."""
        w, h = pixmap.width(), pixmap.height()
        k = max(1, int(size * THUMB_FILL // max(w, h)))
        tw, th = w * k, h * k
        target = QRect(round(centre.x() - tw / 2), round(centre.y() - th / 2), tw, th)
        p.save()
        clip = QPainterPath()
        clip.addEllipse(button)
        p.setClipPath(clip)
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        p.drawPixmap(target, pixmap)
        p.restore()

    def _paint_chevrons(self, p: QPainter, fade: float, radius: float) -> None:
        """A small arrowhead beyond each end of the arc: there is more of the ring that way."""
        t = self._theme
        colour = QColor(t["muted"])
        last_slot = len(self._items) - 1
        size = self.button_d / 8.0  # plan_layout keeps an arrowhead this size on screen
        p.setOpacity(fade)
        p.setPen(Qt.NoPen)
        p.setBrush(colour)
        for slot, outward in ((-CHEVRON_SLOT, -1.0), (last_slot + CHEVRON_SLOT, 1.0)):
            tip = self._layout.point_at(self._centre, slot, radius)
            back = self._layout.point_at(self._centre, slot - outward * 0.12, radius)
            dx, dy = tip.x() - back.x(), tip.y() - back.y()
            length = math.hypot(dx, dy) or 1.0
            ux, uy = dx / length, dy / length      # along the arc, pointing outward
            nx, ny = -uy, ux                        # across it
            arrow = QPainterPath(QPointF(tip.x() + ux * size, tip.y() + uy * size))
            arrow.lineTo(tip.x() - ux * size * 0.4 + nx * size, tip.y() - uy * size * 0.4 + ny * size)
            arrow.lineTo(tip.x() - ux * size * 0.4 - nx * size, tip.y() - uy * size * 0.4 - ny * size)
            arrow.closeSubpath()
            p.drawPath(arrow)
        p.setOpacity(1.0)

    def _paint_label(self, p: QPainter, fade: float) -> None:
        """The hovered tool's name, in a pill just beyond the ring on the side the wheel opens to."""
        t = self._theme
        if self._hover is not None:
            text = self._items[self._hover].label
        elif self.scrollable:
            # Where on the ring you are, so scrolling reads as moving through it: the group of the
            # tool in the middle of the arc.
            middle = self._items[len(self._items) // 2].group
            text = f"{middle} · scroll for more" if middle else "Scroll for more"
        else:
            text = "Choose a tool"
        font = QFont()
        font.setPixelSize(int(12 * self._s))
        font.setBold(self._hover is not None)
        p.setFont(font)
        metrics = QFontMetrics(font)
        w = metrics.horizontalAdvance(text) + 22 * self._s
        h = 24 * self._s

        # Pushed out along the facing by the pill's own extent in that direction, not just its
        # half-height. Every button lies within radius + button/2 of the centre in any direction,
        # so a pill wholly beyond that line along the facing cannot touch one - whereas on a tilted
        # wheel a half-height push left the pill's wide side lying across two of them.
        a = math.radians(self._layout.facing)
        ca, sa = math.cos(a), math.sin(a)
        support = abs(w / 2 * ca) + abs(h / 2 * sa)
        distance = self._layout.radius + self.button_d / 2 + LABEL_GAP * self._s + support
        cx = self._centre.x() + distance * ca
        cy = self._centre.y() - distance * sa
        # Kept inside the window, so a corner layout cannot push its own label off the edge.
        cx = max(w / 2 + 2, min(self.width() - w / 2 - 2, cx))
        cy = max(h / 2 + 2, min(self.height() - h / 2 - 2, cy))
        rect = QRectF(cx - w / 2, cy - h / 2, w, h)

        p.setOpacity(fade)
        p.setPen(QPen(QColor(t["border"]), 1.0))
        p.setBrush(QColor(t["bg"]))
        p.drawRoundedRect(rect, h / 2, h / 2)
        p.setPen(QColor(t["text"] if self._hover is not None else t["muted"]))
        p.drawText(rect, Qt.AlignCenter, text)
        p.setOpacity(1.0)
