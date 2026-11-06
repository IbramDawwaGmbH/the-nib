"""The tool wheel: fitting on screen in every corner, aiming by angle, click versus hold-and-flick,
and how right-click reaches it - or, with it off or Shift held, the menu it replaced."""

import inspect
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

import tool_wheel as W  # noqa: E402
from tool_wheel import ToolWheel, WheelItem  # noqa: E402

fails = []

SCREEN = QRect(0, 0, 1920, 1080)
BUTTON_D = 40.0
EIGHT = [WheelItem(k, k.title(), k) for k in
         ("ask", "again", "measure", "read", "focus", "pocket", "mic", "more")]


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


def all_onscreen(layout, centre):
    bounds = QRectF(SCREEN).adjusted(W.SCREEN_MARGIN, W.SCREEN_MARGIN,
                                     -W.SCREEN_MARGIN, -W.SCREEN_MARGIN)
    for i in range(len(layout.angles)):
        p = layout.point(centre, i)
        r = BUTTON_D / 2
        if not bounds.contains(QRectF(p.x() - r, p.y() - r, BUTTON_D, BUTTON_D)):
            return False
    return True


def min_gap(layout, centre):
    pts = [layout.point(centre, i) for i in range(len(layout.angles))]
    return min(((a.x() - b.x()) ** 2 + (a.y() - b.y()) ** 2) ** 0.5 for a, b in zip(pts, pts[1:]))


def mouse(wheel, kind, local, button, buttons=None, global_pos=None):
    local = QPointF(local)
    glob = QPointF(global_pos) if global_pos is not None else QPointF(wheel.mapToGlobal(local.toPoint()))
    event = QMouseEvent(kind, local, glob, button,
                        button if buttons is None else buttons, Qt.NoModifier)
    {QEvent.MouseButtonPress: wheel.mousePressEvent,
     QEvent.MouseButtonRelease: wheel.mouseReleaseEvent,
     QEvent.MouseMove: wheel.mouseMoveEvent}[kind](event)


def toward(wheel, index, fraction=1.0):
    """A point in the wheel's own coordinates, aimed at tool ``index``."""
    return wheel.layout_plan.point(wheel._centre, index, wheel.layout_plan.radius * fraction)


def fresh_wheel(centre=QPoint(960, 1040), items=EIGHT, press=True):
    wheel = ToolWheel(1.0)
    picked = []
    wheel.chosen.connect(picked.append)
    wheel.open(items, centre, SCREEN, sprite_radius=32, press_global=centre if press else None)
    app.processEvents()
    return wheel, picked


def settle():
    # chosen is emitted on a zero-length timer, after the popup's mouse grab is gone.
    for _ in range(5):
        app.processEvents()
        time.sleep(0.01)


print("--- it fits on screen wherever he happens to be ---")
for name, (x, y) in (
    ("bottom taskbar, middle", (960, 1040)),
    ("bottom taskbar, far left", (30, 1040)),
    ("bottom taskbar, far right", (1890, 1040)),
    ("top taskbar, middle", (960, 40)),
    ("top-left corner", (30, 40)),
    ("a little in from the left", (80, 1040)),
):
    centre = QPointF(x, y)
    layout = W.plan_layout(8, centre, SCREEN, 70, BUTTON_D)
    check(f"{name}: every tool fully onscreen", all_onscreen(layout, centre),
          f"facing {layout.facing:.0f}, radius {layout.radius:.0f}")
    check(f"{name}: and still a comfortable flick apart", min_gap(layout, centre) >= BUTTON_D + 2,
          f"{min_gap(layout, centre):.1f}px")

mid = W.plan_layout(8, QPointF(960, 1040), SCREEN, 70, BUTTON_D)
check("on a bottom taskbar the wheel opens straight up", abs(mid.facing - 90.0) < 1, mid.facing)
top = W.plan_layout(8, QPointF(960, 40), SCREEN, 70, BUTTON_D)
check("on a top taskbar it opens straight down", abs(top.facing - 270.0) < 1, top.facing)
left = W.plan_layout(8, QPointF(30, 1040), SCREEN, 70, BUTTON_D)
check("at the far left it tilts inward, toward the screen", 0 < left.facing < 90, left.facing)
check("a corner widens the arc rather than overlapping the tools", left.radius > mid.radius,
      f"{left.radius:.0f} vs {mid.radius:.0f}")
few = W.plan_layout(4, QPointF(960, 1040), SCREEN, 70, BUTTON_D)
check("fewer tools sit closer together, not spread round the whole arc",
      abs(few.angles[0] - few.angles[-1]) < abs(mid.angles[0] - mid.angles[-1]))
check("no tools is not a crash", W.plan_layout(0, QPointF(960, 1040), SCREEN, 70, BUTTON_D).angles == ())

print("\n--- aiming is by angle, with a dead zone over him ---")
centre = QPointF(960, 1040)
for i in range(8):
    aim = mid.point(centre, i, mid.radius * 1.6)  # well past the icon: direction is what counts
    check(f"flicking toward tool {i} picks tool {i}", W.pick(mid, centre, aim, 30) == i)
check("right on top of him is nothing", W.pick(mid, centre, QPointF(962, 1038), 30) is None)
check("straight down, away from every tool, is nothing",
      W.pick(mid, centre, QPointF(960, 1200), 30) is None)
check("miles away is nothing", W.pick(mid, centre, QPointF(5000, -3000), 30) is None)

print("\n--- a quick click leaves it open; a second click picks ---")
wheel, picked = fresh_wheel()
target = toward(wheel, 2)
mouse(wheel, QEvent.MouseButtonRelease, wheel._centre, Qt.RightButton, Qt.NoButton,
      global_pos=QPoint(960, 1040))
check("releasing the opening right-click on the spot keeps the wheel open", wheel.isVisible())
check("...and picks nothing", picked == [])
mouse(wheel, QEvent.MouseMove, target, Qt.NoButton, Qt.NoButton)
check("hovering a tool highlights it", wheel._hover == 2, wheel._hover)
mouse(wheel, QEvent.MouseButtonPress, target, Qt.LeftButton)
mouse(wheel, QEvent.MouseButtonRelease, target, Qt.LeftButton, Qt.NoButton)
settle()
check("clicking it picks it", picked == ["measure"], picked)
check("...and the wheel is gone before the tool runs", not wheel.isVisible())

print("\n--- hold and flick: one motion, the game gesture ---")
wheel, picked = fresh_wheel()
target = toward(wheel, 4, 1.3)
mouse(wheel, QEvent.MouseMove, target, Qt.NoButton, Qt.RightButton)
mouse(wheel, QEvent.MouseButtonRelease, target, Qt.RightButton, Qt.NoButton)
settle()
check("press on him, flick toward a tool, let go - that tool", picked == ["focus"], picked)

wheel, picked = fresh_wheel()
wheel._opened_at -= 1.0  # held a full second, then let go back over him
mouse(wheel, QEvent.MouseButtonRelease, wheel._centre + QPointF(3, -2), Qt.RightButton, Qt.NoButton,
      global_pos=QPoint(963, 1038))
settle()
check("held, then let go back over him, cancels", picked == [] and not wheel.isVisible(), picked)

print("\n--- the other ways out, and in ---")
wheel, picked = fresh_wheel()
mouse(wheel, QEvent.MouseButtonRelease, wheel._centre, Qt.RightButton, Qt.NoButton,
      global_pos=QPoint(960, 1040))
mouse(wheel, QEvent.MouseButtonPress, toward(wheel, 1), Qt.RightButton)
check("right-clicking again while it is open closes it", not wheel.isVisible() and picked == [])

wheel, picked = fresh_wheel()
wheel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
check("Esc closes it", not wheel.isVisible())

wheel, picked = fresh_wheel()
wheel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_3, Qt.NoModifier))
settle()
check("number keys pick by position", picked == ["measure"], picked)

wheel, picked = fresh_wheel()
wheel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier))
wheel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier))
wheel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
settle()
check("arrows and Enter work too", picked == ["again"], picked)

wheel, picked = fresh_wheel()
mouse(wheel, QEvent.MouseButtonRelease, wheel._centre, Qt.RightButton, Qt.NoButton,
      global_pos=QPoint(960, 1040))
mouse(wheel, QEvent.MouseButtonPress, wheel._centre, Qt.LeftButton)
mouse(wheel, QEvent.MouseButtonRelease, wheel._centre, Qt.LeftButton, Qt.NoButton)
check("clicking over him rather than a tool closes it without picking",
      not wheel.isVisible() and picked == [])

wheel, picked = fresh_wheel()
check("it paints without raising", wheel.grab() is not None)
wheel.close()

print("\n--- every icon it can be handed exists and paints ---")
for name in ("ask", "again", "measure", "read", "focus", "pocket", "mic", "mic_muted", "more"):
    check(f"icon '{name}' is drawn in code", name in W.ICONS)
states = [WheelItem("a", "A", "focus", "active"), WheelItem("b", "B", "mic", "hot"),
          WheelItem("c", "C", "mic_muted", "muted"), WheelItem("d", "D", "nope")]
wheel, _ = fresh_wheel(items=states)
wheel._progress = 1.0
wheel._hover = 1
check("state rings and an unknown icon paint without raising", not wheel.grab().isNull())
wheel.close()

print("\n--- step scrolling: the ring turns one tool per notch ---")
from PySide6.QtCore import QPoint as _QP  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402

RING = [WheelItem(f"t{i}", f"Tool {i}", "ask", group="Tools" if i < 8 else "Him") for i in range(18)]


def scroll(wheel, dy, dx=0):
    pos = QPointF(wheel._centre)
    event = QWheelEvent(pos, QPointF(wheel.mapToGlobal(pos.toPoint())), _QP(0, 0), _QP(dx, dy),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    wheel.wheelEvent(event)


def keys(wheel):
    return [i.key for i in wheel.items]


wheel, picked = fresh_wheel(items=RING)
check("eighteen tools: eight on the arc, the rest on the ring", len(wheel.items) == W.MAX_VISIBLE
      and len(wheel.all_items) == 18, f"{len(wheel.items)} / {len(wheel.all_items)}")
check("it opens at the start of the ring", keys(wheel)[0] == "t0", keys(wheel)[:3])
check("...and says there is more to see", wheel.scrollable)

scroll(wheel, -120)
check("one notch down steps it on by exactly one tool", keys(wheel)[0] == "t1", keys(wheel)[:3])
check("...bringing the next tool in at the far end", keys(wheel)[-1] == "t8", keys(wheel)[-1])
check("...and the arc still holds the same number of slots", len(wheel.items) == W.MAX_VISIBLE)
check("the step slides rather than jumping", wheel._slide != 0 and len(wheel._departing) == 1)
wheel.grab()  # painting mid-slide, with a tool on its way out, must not raise

scroll(wheel, 120)
check("one notch up steps it back", keys(wheel)[0] == "t0", keys(wheel)[:3])

scroll(wheel, 120)
check("it wraps round rather than dead-ending", keys(wheel)[0] == "t17", keys(wheel)[:3])

wheel, picked = fresh_wheel(items=RING)
scroll(wheel, -360)
check("three notches in one event are three steps", keys(wheel)[0] == "t3", keys(wheel)[:3])

wheel, picked = fresh_wheel(items=RING)
for _ in range(3):
    scroll(wheel, -40)  # a trackpad: small deltas, adding up to one notch
check("small trackpad deltas add up to one step, not three", keys(wheel)[0] == "t1", keys(wheel)[:3])
scroll(wheel, -40)
scroll(wheel, 40)
scroll(wheel, 40)
check("a change of direction mid-notch starts counting afresh", keys(wheel)[0] == "t1", keys(wheel)[:3])

wheel, picked = fresh_wheel(items=RING)
scroll(wheel, 0, dx=-120)
check("a sideways swipe steps it too", keys(wheel)[0] == "t1", keys(wheel)[:3])

print("\n--- aiming and picking after a scroll ---")
wheel, picked = fresh_wheel(items=RING)
aim = toward(wheel, 2)
mouse(wheel, QEvent.MouseButtonRelease, wheel._centre, Qt.RightButton, Qt.NoButton,
      global_pos=QPoint(960, 1040))
mouse(wheel, QEvent.MouseMove, aim, Qt.NoButton, Qt.NoButton)
check("aiming at a slot hovers its tool", wheel.items[wheel._hover].key == "t2")
scroll(wheel, -120)
check("scrolling under a still pointer: the same slot now holds the next tool",
      wheel._hover == 2 and wheel.items[wheel._hover].key == "t3", wheel.items[wheel._hover].key)
mouse(wheel, QEvent.MouseButtonPress, aim, Qt.LeftButton)
mouse(wheel, QEvent.MouseButtonRelease, aim, Qt.LeftButton, Qt.NoButton)
settle()
check("...and clicking picks the tool that is there now", picked == ["t3"], picked)

wheel, picked = fresh_wheel(items=RING)
scroll(wheel, -120)
scroll(wheel, -120)
wheel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_1, Qt.NoModifier))
settle()
check("number keys pick by slot, so after scrolling '1' is the first tool showing",
      picked == ["t2"], picked)

wheel, picked = fresh_wheel(items=RING)
target = toward(wheel, 5, 1.3)
mouse(wheel, QEvent.MouseMove, target, Qt.NoButton, Qt.RightButton)
scroll(wheel, -120)  # still holding the right button: scroll mid-gesture...
mouse(wheel, QEvent.MouseButtonRelease, target, Qt.RightButton, Qt.NoButton)
settle()
check("hold, scroll, flick, let go - the gesture survives a scroll in the middle",
      picked == ["t6"], picked)

print("\n--- the keyboard and the edges ---")
wheel, picked = fresh_wheel(items=RING)
wheel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_PageDown, Qt.NoModifier))
check("PageDown steps forward", keys(wheel)[0] == "t1")
wheel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_BracketLeft, Qt.NoModifier))
check("[ steps back", keys(wheel)[0] == "t0")

wheel, picked = fresh_wheel(items=EIGHT)
scroll(wheel, -120)
check("with everything already on the arc, scrolling does nothing",
      not wheel.scrollable and keys(wheel)[0] == "ask")
check("...and step() says so", wheel.step(1) is False)

wheel, picked = fresh_wheel(items=RING)
for _ in range(5):
    scroll(wheel, -120)
wheel.close()
wheel.open(RING, QPoint(960, 1040), SCREEN, sprite_radius=32, press_global=QPoint(960, 1040))
check("reopening starts back at home, so the flick is muscle memory", keys(wheel)[0] == "t0",
      keys(wheel)[:3])
wheel.close()

check("a slot past either end of the arc fades out", W.ToolWheel._edge_fade(-0.5, 7) == 0.5
      and W.ToolWheel._edge_fade(7.5, 7) == 0.5 and W.ToolWheel._edge_fade(3.0, 7) == 1.0)
mid = W.plan_layout(8, QPointF(960, 1040), SCREEN, 70, BUTTON_D)
check("fractional slots sit exactly on the integer ones",
      abs(mid.angle_at(3) - mid.angles[3]) < 1e-9 and abs(mid.angle_at(0) - mid.angles[0]) < 1e-9)
check("...and halfway between neighbours, halfway between their angles",
      abs(mid.angle_at(2.5) - (mid.angles[2] + mid.angles[3]) / 2) < 1e-9)

for name in ("sparkle", "treat", "costume", "wardrobe", "hide", "wander", "pause", "resume",
             "ghost", "mode", "settings", "tips", "home"):
    check(f"new icon '{name}' is drawn in code", name in W.ICONS)
painted = [WheelItem(n, n, n, group="Him") for n in ("sparkle", "treat", "costume", "wardrobe",
                                                     "hide", "wander", "pause", "resume", "ghost",
                                                     "mode", "settings", "tips", "home")]
wheel, _ = fresh_wheel(items=painted)
wheel._progress = 1.0
check("all of them paint without raising", not wheel.grab().isNull())
wheel.close()

print("\n--- costume thumbnails in place of icons ---")
from animator import SpriteSheet as _Sheet  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402

_dressing = _Sheet(SRC / "assets" / "nib_sheet.png", SRC / "assets" / "frames.json", 2)
_names = _dressing.available_costumes()[:12]
thumbs = [WheelItem(f"wear:{n}", n, "costume", "active" if i == 3 else "", group="Hats",
                    pixmap=_dressing.costume_thumbnail(n)) for i, n in enumerate(_names)]
thumbs.append(WheelItem("plain", "Plain", "costume", pixmap=None))
wheel, _ = fresh_wheel(items=thumbs)
wheel._progress = 1.0
wheel._hover = 3
check("a ring of thumbnails paints without raising", not wheel.grab().isNull())
wheel.close()
p1, p2 = QPixmap(4, 4), QPixmap(8, 8)
check("the picture is not part of what makes two items the same tool",
      WheelItem("a", "A", "x", pixmap=p1) == WheelItem("a", "A", "x", pixmap=p2))
check("...and an item carrying one is still hashable",
      hash(WheelItem("a", "A", "x", pixmap=p1)) == hash(WheelItem("a", "A", "x")))

print("\n--- review fixes: a notch mid-slide carries on rather than snapping back ---")


def drawn(wheel):
    """Where every tool is drawn right now, by key: on-arc tools and departing ones alike."""
    out = {item.key: i + wheel._slide for i, item in enumerate(wheel.items)}
    out.update({item.key: slot + wheel._slide for item, slot in wheel._departing})
    return out


wheel, _ = fresh_wheel(items=RING)
wheel.step(1)
wheel._slide_anim.stop()
wheel._on_slide(0.4)                      # forty percent of the way through the first slide
before = drawn(wheel)
wheel.step(1)                             # ...and a second notch lands
wheel._slide_anim.stop()
after = drawn(wheel)
shared = set(before) & set(after)
check("every tool on screen stays exactly where it was drawn",
      all(abs(before[k] - after[k]) < 1e-9 for k in shared),
      {k: (round(before[k], 2), round(after[k], 2)) for k in sorted(shared)
       if abs(before[k] - after[k]) > 1e-9})
check("...including the one that was already on its way out",
      "t0" in after and abs(after["t0"] - before["t0"]) < 1e-9, after.get("t0"))
check("...and the newly departing one joins it rather than replacing it",
      {"t0", "t1"} <= {item.key for item, _ in wheel._departing})

wheel, _ = fresh_wheel(items=RING)
scroll(wheel, -240)                       # two notches in ONE event: a hard-spun wheel
wheel._slide_anim.stop()
check("two notches in one event turn the ring two tools...", keys(wheel)[0] == "t2", keys(wheel)[:3])
check("...and animate two slots of travel, not one", abs(wheel._slide - 2.0) < 1e-9, wheel._slide)
check("...so the far-end newcomer starts off the arc, not at full strength on it",
      W.ToolWheel._edge_fade(len(wheel.items) - 1 + wheel._slide, len(wheel.items) - 1) == 0.0)

wheel, _ = fresh_wheel(items=RING)
scroll(wheel, -120 * 12)
wheel._slide_anim.stop()
check("a dozen notches at once still turns by all twelve", keys(wheel)[0] == "t12", keys(wheel)[:3])
check("...but the animation is capped rather than whirling", abs(wheel._slide) <= W.MAX_SLIDE)

wheel, _ = fresh_wheel(items=RING)
wheel.step(1)
wheel.step(-1)                            # out and straight back in, mid-slide
wheel._slide_anim.stop()
check("a tool scrolled away and straight back is drawn once, not twice",
      "t0" not in {item.key for item, _ in wheel._departing} and keys(wheel)[0] == "t0")
wheel._on_slide_done()
check("when the slide ends, nothing is left departing", wheel._departing == [])
wheel.close()

print("\n--- review fixes: the label never sits on a tool, whichever way the wheel faces ---")
from PySide6.QtGui import QFont, QFontMetrics  # noqa: E402

LONG = [WheelItem(f"t{i}", "Pin the clipboard to his sign" if i % 3 == 0 else f"Tool {i}", "ask",
                  group="Tools") for i in range(18)]


def label_overlap(wheel, text):
    s = wheel._s
    font = QFont()
    font.setPixelSize(int(12 * s))
    font.setBold(True)
    w = QFontMetrics(font).horizontalAdvance(text) + 22 * s
    h = 24 * s
    a = W.math.radians(wheel._layout.facing)
    ca, sa = W.math.cos(a), W.math.sin(a)
    distance = (wheel._layout.radius + wheel.button_d / 2 + W.LABEL_GAP * s
                + abs(w / 2 * ca) + abs(h / 2 * sa))
    cx = wheel._centre.x() + distance * ca
    cy = wheel._centre.y() - distance * sa
    clamped = (max(w / 2 + 2, min(wheel.width() - w / 2 - 2, cx)),
               max(h / 2 + 2, min(wheel.height() - h / 2 - 2, cy)))
    rect = QRectF(clamped[0] - w / 2, clamped[1] - h / 2, w, h)
    worst = -1e9
    for i in range(len(wheel.items)):
        c = wheel._layout.point(wheel._centre, i)
        nx = max(rect.left(), min(c.x(), rect.right()))
        ny = max(rect.top(), min(c.y(), rect.bottom()))
        worst = max(worst, wheel.button_d / 2 - ((c.x() - nx) ** 2 + (c.y() - ny) ** 2) ** 0.5)
    return worst, (cx, cy) != clamped


for scale, sprite_r, screen in ((1.0, 48, QRect(0, 0, 1920, 1080)), (2.0, 96, QRect(0, 0, 3840, 2160))):
    for x in (sprite_r, screen.width() // 6, screen.width() // 2, screen.width() - sprite_r):
        for y in (screen.height() - sprite_r, sprite_r):
            wh = ToolWheel(scale)
            wh.open(LONG, QPoint(x, y), screen, sprite_radius=sprite_r)
            for text in ("Pin the clipboard to his sign", "Tools · scroll for more"):
                overlap, clamped = label_overlap(wh, text)
                where = f"scale {scale}, x={x}, {'bottom' if y > sprite_r else 'top'}"
                check(f"{where}: '{text[:14]}…' clears every tool", overlap <= 0, f"{overlap:.1f}px")
                check(f"{where}: ...and fits without being pushed back", not clamped)
            wh.close()

print("\n--- review fixes: slot 0 is on the left, so the arrow keys match the screen ---")
for name, y in (("bottom taskbar", 1040), ("top taskbar", 40)):
    for x in (30, 400, 960, 1890):
        layout = W.plan_layout(8, QPointF(x, y), SCREEN, 91, BUTTON_D)
        c = QPointF(x, y)
        check(f"{name}, x={x}: the first slot is the leftmost",
              layout.point(c, 0).x() <= layout.point(c, 7).x() + 1e-6,
              f"{layout.point(c, 0).x():.0f} vs {layout.point(c, 7).x():.0f}")

wheel, _ = fresh_wheel(centre=QPoint(960, 40), items=EIGHT)
wheel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier))
first = wheel._hover
wheel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier))
c = wheel._centre
moved_right = wheel.layout_plan.point(c, wheel._hover).x() > wheel.layout_plan.point(c, first).x()
check("on a top taskbar, Right moves the highlight to the right", moved_right)
wheel.close()

print("\n--- review fixes: chevrons stay on screen at 200% in either corner ---")
big = QRect(0, 0, 3840, 2160)
for x in (96, 3744):
    wh = ToolWheel(2.0)
    wh.open(LONG, QPoint(x, 2160 - 96), big, sprite_radius=96)
    last = len(wh.items) - 1
    for slot in (-0.62, last + 0.62):
        tip = wh.mapToGlobal(wh.layout_plan.point_at(wh._centre, slot, wh.layout_plan.radius).toPoint())
        arrow = 5 * 2.0
        check(f"x={x}: the chevron at slot {slot:.2f} is fully on screen",
              big.adjusted(int(arrow), int(arrow), -int(arrow), -int(arrow)).contains(tip), tip)
    wh.close()

print("\n--- it says whether you picked or cancelled, and what you are aiming at ---")
settle()  # let the last section's popups finish closing, or this one can open already dismissed
wheel, _ = fresh_wheel()
closes = []
wheel.closed.connect(closes.append)
check("set up: the wheel is open", wheel.isVisible())
wheel._choose(0)
settle()
check("a pick closes with True", closes == [True], closes)

wheel, _ = fresh_wheel()
closes = []
wheel.closed.connect(closes.append)
wheel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
check("Esc closes with False", closes == [False], closes)

wheel, _ = fresh_wheel()
closes = []
wheel.closed.connect(closes.append)
wheel._opened_at -= 1.0  # held, then let go back over him
mouse(wheel, QEvent.MouseButtonRelease, wheel._centre + QPointF(3, -2), Qt.RightButton, Qt.NoButton,
      global_pos=QPoint(963, 1038))
check("letting go back over him closes with False", closes == [False], closes)

wheel, _ = fresh_wheel(items=RING)
aims = []
wheel.aimed.connect(aims.append)
mouse(wheel, QEvent.MouseMove, toward(wheel, 2), Qt.NoButton, Qt.NoButton)
expected = wheel.mapToGlobal(toward(wheel, 2).toPoint())
check("aiming at a tool says where that tool is, on screen",
      aims == [(expected.x(), expected.y())], (aims, expected))
mouse(wheel, QEvent.MouseMove, toward(wheel, 2, 1.3), Qt.NoButton, Qt.NoButton)
check("...and moving about on the same tool says nothing new", len(aims) == 1, aims)
mouse(wheel, QEvent.MouseMove, wheel._centre, Qt.NoButton, Qt.NoButton)
check("back over him is None", len(aims) == 2 and aims[-1] is None, aims)
wheel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier))
first = wheel.mapToGlobal(wheel.layout_plan.point(wheel._centre, 0).toPoint())
check("the arrow keys aim too", len(aims) == 3 and aims[-1] == (first.x(), first.y()), aims)
check("set up: this ring scrolls", wheel.scrollable)
wheel.step(1)
check("turning the ring keeps the slot, so the direction - and his eyes - stay put", len(aims) == 3,
      aims)
wheel.close()

print("\n--- how main.py wires it ---")
import main as M  # noqa: E402

src_ctx = inspect.getsource(M.NibApp._on_context_menu)
check("right-click opens the wheel when it is enabled", "self._open_tool_wheel(position)" in src_ctx)
check("...unless Shift is held", "Qt.ShiftModifier" in src_ctx)
check("...and otherwise still falls back to the classic menu",
      "self.tray.popup_for_mascot(position, self._mascot_menu_extras())" in src_ctx)

src_chosen = inspect.getsource(M.NibApp._on_tool_chosen)
for key, handler in (
    ("ask", "self._on_click()"),
    ("measure", "self._on_measure_requested()"),
    ("read", "self._pick_image_to_read()"),
    ("focus", "self._on_focus_toggled(not self.behavior.focus_active)"),
    ("pocket", "self._on_pocket_hotkey()"),
    ("mic", "self._on_mic_toggle()"),
):
    check(f"'{key}' runs the handler that already existed for it", handler in src_chosen)
check("More... opens the full menu, extras and all, so nothing is out of reach",
      "self.tray.popup_for_mascot(self._wheel_position, self._mascot_menu_extras())" in src_chosen)

src_items = inspect.getsource(M.NibApp._wheel_items)
check("Send again only appears when there is a last prompt", 'self.config["last_prompt"]' in src_items)
check("the mic only appears when there is a microphone", "if muted is not None:" in src_items)
check("...and when the mic indicator is on at all", 'self.config["mic_indicator_enabled"]' in src_items)
check("focus is labelled with what it will do now", '"Stop focus" if focusing else "Start focus"'
      in src_items)

for key in ("do_something", "treat", "costume", "wardrobe", "hide", "wander", "pause", "ghost",
            "mode", "settings", "tips", "reset"):
    check(f"the ring offers '{key}'", f'"{key}"' in src_items)
    check(f"...and picking '{key}' does something", f'key == "{key}"' in src_chosen)
check("the tools come before everything else, so they are all on the arc at open",
      src_items.index('"more"') < src_items.index('"do_something"'))

for key, handler in (("wander", "self._on_wander_toggled("), ("pause", "self._on_pause_toggled("),
                     ("ghost", "self._on_ghost_toggled(")):
    branch = src_chosen.split(f'key == "{key}"', 1)[1].split("elif", 1)[0]
    check(f"'{key}' goes through the tray's own handler", handler in branch)
    check(f"...then refreshes the tray, so its checkbox does not go stale",
          "self._refresh_tray()" in branch)
check("ghost mode can only be switched ON from the wheel - off is the tray's job",
      "self._on_ghost_toggled(True)" in src_chosen)
check("the mode switch only appears with more than one mode",
      "if len(provider.modes) > 1:" in src_items)
check("Quit is not on the ring - one stray flick should never close the app",
      '"quit"' not in src_items)
check("the wardrobe sits next to Surprise costume",
      src_items.index('"costume"') < src_items.index('"wardrobe"') < src_items.index('"hide"'))

print("\n--- the wardrobe: the wheel again, with his costumes on it ---")
_ring = M._wardrobe_ring(_dressing, "crown", ["cap", "nope", "cap", "crown"])
_keys = [i.key for i in _ring]
_owned = _dressing.available_costumes()
check("your recents first, unknown ones dropped, repeats folded, then Take it off",
      _keys[:3] == ["wear:cap", "wear:crown", "wear:"], _keys[:4])
check("every key is a wear: key", all(k.startswith(M.WARDROBE_PREFIX) for k in _keys))
check("every costume once, plus Take it off", len(_ring) == len(_owned) + 1
      and len(set(_keys)) == len(_keys), len(_ring))
check("the one he is wearing has the ring",
      [i.key for i in _ring if i.state == "active"] == ["wear:crown"])
check("every costume carries its thumbnail",
      all(i.pixmap is not None and not i.pixmap.isNull() for i in _ring if i.key != "wear:"))
check("the groups read Recent, then Wardrobe, then the categories in the sheet's order",
      list(dict.fromkeys(i.group for i in _ring))
      == ["Recent", "Wardrobe", *_dressing.costume_categories()],
      list(dict.fromkeys(i.group for i in _ring)))
check("labels are the tray's wording", next(i for i in _ring if i.key == "wear:tophat").label
      == "Tophat")
_bare = M._wardrobe_ring(_dressing, "", [])
check("bare-headed: no Take it off, and the ring starts on the first category",
      "wear:" not in [i.key for i in _bare] and len(_bare) == len(_owned)
      and _bare[0].group == next(iter(_dressing.costume_categories())))
_many = M._wardrobe_ring(_dressing, "", list(_owned))
check("no more than WARDROBE_RECENTS recents",
      sum(i.group == "Recent" for i in _many) == M.WARDROBE_RECENTS)
wheel, _ = fresh_wheel(items=_ring)
wheel._progress = 1.0
check("the whole wardrobe paints without raising", not wheel.grab().isNull())
wheel.close()

check("a wear: pick goes through the same path as the tray",
      src_chosen.index("key.startswith(WARDROBE_PREFIX)")
      < src_chosen.index("self._on_costume_chosen(key[len(WARDROBE_PREFIX):])")
      < src_chosen.index('elif key == "ask"'))
check("...and refreshes the tray's check mark",
      "self._refresh_tray()" in src_chosen.split("key.startswith(WARDROBE_PREFIX)", 1)[1]
      .split("elif", 1)[0])
check("Wardrobe... opens the wardrobe", "self._open_wardrobe()" in
      src_chosen.split('key == "wardrobe"', 1)[1].split("elif", 1)[0])
src_wardrobe = inspect.getsource(M.NibApp._open_wardrobe)
check("...which holds him again - the first wheel closing let him go",
      "self.behavior.wheel_opened()" in src_wardrobe)
check("...re-centres the drift check on where he is now", "self._wheel_centre =" in src_wardrobe)
check("...and opens in click mode, the first press being long gone",
      "press_global=None" in src_wardrobe)
check("...with your recents", 'self.config["costume_recents"]' in src_wardrobe)

print("\n--- review fixes: he holds still under the wheel, and it never ends up over nothing ---")
src_open = inspect.getsource(M.NibApp._open_tool_wheel)
check("opening the wheel holds him still", "self.behavior.wheel_opened()" in src_open)
check("...after the items are built, so their labels describe him before he was held",
      src_open.index("self._wheel_items()") < src_open.index("self.behavior.wheel_opened()"))
src_make = inspect.getsource(M.NibApp._make_tool_wheel)
check("closing the wheel lets him go - and is wired again whenever the wheel is rebuilt",
      "self.tool_wheel.closed.connect(self.behavior.wheel_closed)" in src_make)
check("his eyes are wired to the tool you are aiming at",
      "self.tool_wheel.aimed.connect(self._on_wheel_aimed)" in src_make)
src_aimed = inspect.getsource(M.NibApp._on_wheel_aimed)
check("...and painted at once, not a tick later",
      "wheel_aimed(" in src_aimed and "_apply_frame(" in src_aimed)
check("every pick goes past Behavior.tool_picked for its beat", "self.behavior.tool_picked(key)"
      in src_chosen)
check("...after the whole chain of handlers, so a tool that gave him a job keeps it",
      src_chosen.index("self.behavior.tool_picked(key)") > src_chosen.rindex("elif key =="))
check("the topmost timer leaves the wheel above him",
      "not self.tool_wheel.isVisible()" in inspect.getsource(M.NibApp._reassert_topmost))
check("every movement tick checks he is still under the wheel",
      "self._check_wheel_drift()" in inspect.getsource(M.NibApp._tick))

import behavior as Bh  # noqa: E402
from animator import SpriteSheet  # noqa: E402
from config import Config  # noqa: E402
from _helpers import scratch as _scratch  # noqa: E402

cfg_path = _scratch() / "wheel_hold.json"
cfg_path.unlink(missing_ok=True)
cfg = Config(cfg_path)
cfg["wander_enabled"] = True
sheet = SpriteSheet(SRC / "assets" / "nib_sheet.png", SRC / "assets" / "frames.json", 2)
b = Bh.Behavior(cfg, sheet)
b.set_strip(1000, 0, 4000)
b.place_at(600)
for _ in range(10):
    b.update(50.0, None)
held = b.wheel_opened()
x0 = b.x
for _ in range(80):  # four seconds of ticks with wandering on
    b.update(50.0, None)
check("while the wheel is open he really does stay put", held and b.x == x0, f"{x0} -> {b.x}")
check("...in the same held state the ask bubble uses", b.state == Bh.State.ASK, b.state)
b.wheel_closed()
check("closing it lets him go back to what he was doing", b.state == Bh.State.IDLE, b.state)
pokes = len(b._pokes)
b.wheel_opened()
b.wheel_closed(False)
check("putting the wheel away without choosing is not a poke", len(b._pokes) == pokes)
check("...but it does get a head scratch, so a cancel looks different from a pick",
      b.state == Bh.State.ANTIC and b._antic == Bh.WHEEL_CANCEL_ANIM, (b.state, b._antic))

print("\n--- review fixes: right-clicking him on a window does not knock him off it ---")
cfg_path = _scratch() / "wheel_perch.json"
cfg_path.unlink(missing_ok=True)
b = Bh.Behavior(Config(cfg_path), sheet)
b.set_strip(1000, 0, 4000)
b.place_at(440)
b.drag_start()
for _ in range(3):
    b.drag_to(440, 300)
    b.update(50.0, None)
b.drag_end(perch_rect=(400, 300, 900, 700))
check("set up: he is standing on a window", b.perched and b.state == Bh.State.PERCH, b.state)
check("opening the wheel does not hold him - he is already still up there", b.wheel_opened() is False)
check("...so he is still perched, in the perch state", b.perched and b.state == Bh.State.PERCH, b.state)
b.wheel_closed()
check("...and closing it leaves the perch exactly as it was",
      b.perched and b.state == Bh.State.PERCH, b.state)
for _ in range(40):
    b.update(50.0, None)
check("two seconds later he is still standing on the window", b.perched and b.state == Bh.State.PERCH,
      b.state)

print("\n--- review fixes: only a hold the wheel took is the wheel's to release ---")
cfg_path = _scratch() / "wheel_ask.json"
cfg_path.unlink(missing_ok=True)
b = Bh.Behavior(Config(cfg_path), sheet)
b.set_strip(1000, 0, 4000)
b.place_at(600)
b.click()                       # the ask bubble put him in ASK, not the wheel
b.wheel_closed()                # a stray close from a wheel that never held him
check("a wheel closing does not end someone else's ASK", b.state == Bh.State.ASK, b.state)

print("\n--- review fixes: a step never loses its slide to the animation finishing ---")
wheel, _ = fresh_wheel(items=RING)
wheel.step(1)
wheel._slide_anim.setCurrentTime(wheel._slide_anim.duration())  # at its very end, not yet finished
wheel.step(1)
check("stepping as the previous slide ends still slides", wheel._slide != 0 and wheel._departing,
      (wheel._slide, len(wheel._departing)))
wheel.close()

print("\n--- one blink clock, shared by every state that stands about ---")
for tick in ("_tick_idle", "_tick_perch"):
    check(f"{tick} blinks through the shared clock",
          "_tick_blink(" in inspect.getsource(getattr(Bh.Behavior, tick)))

def wheel_nib(name):
    path = _scratch() / f"{name}.json"
    path.unlink(missing_ok=True)
    nib = Bh.Behavior(Config(path), sheet)
    nib.set_strip(1000, 0, 4000)
    nib.place_at(600)
    for _ in range(5):
        nib.update(50.0, None)
    return nib


BLINKS = {Bh.BLINK_SINGLE, "blink", "blink_double", "blink_slow"}

print("\n--- he watches the tool you are aiming at ---")
b = wheel_nib("wheel_eyes")
b.wheel_opened()
x0 = b.x
b.wheel_aimed((b.x + b.sprite_w // 2, b.top - 200))
check("aiming straight up turns his eyes up at once",
      b.animator.state == Bh.EYE_TRACK_STATE and b.animator.frame_index == 1,
      (b.animator.state, b.animator.frame_index))
for _ in range(20):
    b.update(50.0, None)
check("...and the tick holds them there rather than resetting them",
      b.animator.state == Bh.EYE_TRACK_STATE and b.animator.frame_index == 1,
      (b.animator.state, b.animator.frame_index))
check("...still held for the wheel, and still in the same spot",
      b.state == Bh.State.ASK and b.x == x0, (b.state, x0, b.x))
b.wheel_aimed((b.x + b.sprite_w + 400, b.top + b.sprite_h + 400))
check("aiming down and to the right looks down and to the right", b.animator.frame_index == 8,
      b.animator.frame_index)
b.wheel_aimed(None)
b.update(50.0, None)
check("off every tool, he looks ahead again", b.animator.state == "idle", b.animator.state)
b.wheel_aimed((b.x + b.sprite_w // 2, b.top - 200))
b.wheel_closed(True)
check("closing the wheel forgets where he was looking", b._wheel_look is None)
check("...and a pick leaves him standing about, not scratching", b.state == Bh.State.IDLE, b.state)

cfg_path = _scratch() / "wheel_perch_eyes.json"
cfg_path.unlink(missing_ok=True)
b = Bh.Behavior(Config(cfg_path), sheet)
b.set_strip(1000, 0, 4000)
b.place_at(440)
b.drag_start()
for _ in range(3):
    b.drag_to(440, 300)
    b.update(50.0, None)
b.drag_end(perch_rect=(400, 300, 900, 700))
b.wheel_opened()
b.wheel_aimed((b.x + b.sprite_w // 2, b.top - 200))
b.update(50.0, None)
check("perched, the wheel does not hold him, so he does not watch it",
      b.perched and b.animator.state != Bh.EYE_TRACK_STATE, (b.perched, b.animator.state))

print("\n--- asleep, the wheel still opens, and he grumbles about it ---")
b = wheel_nib("wheel_sleep")
b._enter(Bh.State.SLEEP)
check("right-clicking him asleep does not hold him", b.wheel_opened() is False)
check("...he stays asleep", b.state == Bh.State.SLEEP, b.state)
check("...but grumbles, the same as a click would", b._grumble_ms == Bh.GRUMBLE_MS, b._grumble_ms)
b.update(50.0, None)
check("...and looks it", b.animator.state == "angry", b.animator.state)
b.wheel_closed(False)
check("cancelling that wheel does not wake him with a scratch", b.state == Bh.State.SLEEP, b.state)

print("\n--- he blinks while the bubble or the wheel holds him ---")
for how in ("bubble", "wheel"):
    b = wheel_nib(f"ask_blink_{how}")
    b.click() if how == "bubble" else b.wheel_opened()
    check(f"{how}: entering ASK winds a fresh blink clock",
          Bh.BLINK_MIN_MS <= b._blink_timer_ms <= Bh.BLINK_MAX_MS, b._blink_timer_ms)
    check(f"{how}: ...and the app's tick waits no longer than the next blink",
          b.next_tick_ms(16, 250) <= b._blink_timer_ms)
    b._blink_timer_ms = 1
    b.update(50.0, None)
    check(f"{how}: when it runs out, he blinks", b._blinking and b.animator.state in BLINKS,
          (b._blinking, b.animator.state))
    for _ in range(100):
        if not b._blinking:
            break
        b.update(50.0, None)
    check(f"{how}: ...and the blink ends, still held, eyes open again",
          not b._blinking and b.state == Bh.State.ASK and b.animator.state == "idle",
          (b._blinking, b.state, b.animator.state))
check("ASK ticks through the shared blink clock",
      "_tick_blink(" in inspect.getsource(Bh.Behavior._tick_ask))

b = wheel_nib("ask_blink_aim")
b.wheel_opened()
b._blink_timer_ms = 1
b.update(50.0, None)
check("set up: a blink is under way", b._blinking)
b.wheel_aimed((b.x + b.sprite_w // 2, b.top - 200))
check("aiming mid-blink takes the frame over",
      not b._blinking and b.animator.state == Bh.EYE_TRACK_STATE)
b.wheel_aimed(None)
started = 0
for _ in range(200):  # ten seconds, longer than BLINK_MAX_MS
    was = b._blinking
    b.update(50.0, None)
    started += b._blinking and not was
check("...and he goes on blinking afterwards rather than getting stuck", started >= 1, started)

b = wheel_nib("wheel_paused_cancel")
b.wheel_opened()
b.paused = True
b.wheel_closed(False)
check("cancelling while paused skips the scratch - it would freeze mid-pose",
      b.state == Bh.State.IDLE, b.state)

print("\n--- every pick gets a reaction ---")
import re  # noqa: E402

for name in [anim for anim, _ms in Bh.TOOL_BEATS.values()] + [Bh.WHEEL_CANCEL_ANIM]:
    check(f"the sheet has '{name}'", sheet.has_state(name))
# Tools whose handlers already give him something to do, or take him away: no beat on top.
EXEMPT = {"ask", "again", "read", "focus", "pocket", "do_something", "treat", "costume",
          "wardrobe", "more", "hide", "guard", "update"}
ring = re.findall(r'WheelItem\("(\w+)"', inspect.getsource(M.NibApp._wheel_items))
check("set up: the ring's keys were found", len(ring) >= 18, ring)
for key in ring:
    check(f"'{key}' either has a beat or is exempt on purpose - not both, not neither",
          (key in Bh.TOOL_BEATS) != (key in EXEMPT))
for key, (anim, _ms) in Bh.TOOL_BEATS.items():
    b = wheel_nib(f"beat_{key}")
    check(f"picking '{key}' gets a beat", b.tool_picked(key) and b.state == Bh.State.ANTIC
          and b._antic == anim, (b.state, b._antic))
for key in sorted(EXEMPT):
    b = wheel_nib(f"beat_exempt_{key}")
    check(f"picking '{key}' adds nothing of its own", not b.tool_picked(key)
          and b.state == Bh.State.IDLE, b.state)

b = wheel_nib("beat_pause")
b.paused = True
check("pausing gets no beat - it would freeze halfway through",
      not b.tool_picked("pause") and b.state == Bh.State.IDLE, b.state)
b.paused = False
check("...resuming gets a stretch", b.tool_picked("pause") and b._antic == "stretch", b._antic)

b = wheel_nib("beat_mic")
b.tool_picked("mic")
check("the mic beat is not the muted-mic tell",
      b._antic != Bh.MUTED_ANIM and b.persona_costume == "", (b._antic, b.persona_costume))

b = wheel_nib("beat_sleep")
b._enter(Bh.State.SLEEP)
check("asleep, no pick wakes him with a beat",
      not any(b.tool_picked(k) for k in ring) and b.state == Bh.State.SLEEP, b.state)
cfg_path = _scratch() / "beat_perch.json"
cfg_path.unlink(missing_ok=True)
b = Bh.Behavior(Config(cfg_path), sheet)
b.set_strip(1000, 0, 4000)
b.place_at(440)
b.drag_start()
for _ in range(3):
    b.drag_to(440, 300)
    b.update(50.0, None)
b.drag_end(perch_rect=(400, 300, 900, 700))
check("perched, no pick knocks him off the window with a beat",
      not any(b.tool_picked(k) for k in ring) and b.perched and b.state == Bh.State.PERCH, b.state)

print("\n" + "=" * 62)
print("FAILURES: " + ", ".join(fails) if fails else "the tool wheel behaves")
sys.exit(1 if fails else 0)
