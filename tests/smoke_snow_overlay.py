"""The snow on screen: strips, the flight layer, the timers, the hide rules, and the wiring.

Driven through a real ``QApplication`` and checked against Win32 rather than against what Qt
believes: ``WindowFromPoint`` over a painted pile, the windows' real extended styles and capture
affinity, and the Z-order read back with ``EnumWindows``. The overlay's window stack is a
synthetic one (``list_window_stack`` leaves out this process's own windows, so a window of ours
could never carry a pile) over the real monitors, so the strips land on real screens; only the
last part boots a real ``NibApp`` against the real desktop.

The snow setting's date window is checked as a table, for ``world.snow_active``.
"""

import ctypes
import os
import sys
import time
from ctypes import wintypes
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import scratch as _scratch  # noqa: E402

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
SRC = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, SRC)
os.chdir(SRC)
SCRATCH = _scratch()

import win_integration as win  # noqa: E402

win.enable_dpi_awareness()

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

import config as cfg_module  # noqa: E402
import snow  # noqa: E402
import snow_overlay as SO  # noqa: E402
import world as W  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


app = QApplication(sys.argv)


def pump(ms):
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()


def pump_until(cond, ms):
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        if cond():
            return True
        app.processEvents()
        time.sleep(0.005)
    return cond()


user32 = ctypes.windll.user32
user32.WindowFromPoint.argtypes = [wintypes.POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.IsWindow.argtypes = [wintypes.HWND]
GA_ROOT = 2
WS_EX_TRANSPARENT = 0x20
WS_EX_TOPMOST = 0x8


def ex_style(hwnd):
    getter = user32.GetWindowLongPtrW
    getter.argtypes = [wintypes.HWND, ctypes.c_int]
    getter.restype = ctypes.c_ssize_t
    return getter(wintypes.HWND(hwnd), -20)


def window_at(x, y):
    hwnd = user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
    if not hwnd:
        return 0
    return int(user32.GetAncestor(hwnd, GA_ROOT) or hwnd)


_ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, ctypes.c_ssize_t)


def z_order():
    """Every top-level window, front to back, ours included (list_window_stack leaves ours out)."""
    out = []

    def cb(hwnd, _param):
        out.append(int(hwnd))
        return True

    user32.EnumWindows(_ENUM(cb), 0)
    return out


# --- the date window -------------------------------------------------------------------------

print("--- snow_active: the date window ---")
TABLE = [
    # (date, mode, southern, expected)
    (date(2026, 11, 30), "christmas", False, False),
    (date(2026, 12, 1), "christmas", False, True),
    (date(2026, 12, 6), "christmas", False, True),
    (date(2026, 12, 25), "christmas", False, True),
    (date(2026, 12, 31), "christmas", False, True),
    (date(2027, 1, 1), "christmas", False, True),
    (date(2027, 1, 6), "christmas", False, True),
    (date(2027, 1, 7), "christmas", False, False),
    (date(2027, 7, 15), "christmas", False, False),
    # By the date everywhere: Christmas is Christmas in Sydney too.
    (date(2026, 12, 25), "christmas", True, True),
    (date(2027, 7, 15), "christmas", True, False),
    (date(2028, 2, 29), "christmas", False, False),
    # "winter" follows the meteorological season, and southern_seasons flips it.
    (date(2026, 12, 1), "winter", False, True),
    (date(2027, 2, 28), "winter", False, True),
    (date(2028, 2, 29), "winter", False, True),
    (date(2027, 3, 1), "winter", False, False),
    (date(2026, 11, 30), "winter", False, False),
    (date(2027, 7, 15), "winter", False, False),
    (date(2027, 6, 1), "winter", True, True),
    (date(2027, 8, 31), "winter", True, True),
    (date(2026, 12, 25), "winter", True, False),
    (date(2027, 9, 1), "winter", True, False),
    (date(2027, 7, 15), "always", False, True),
    (date(2027, 7, 15), "always", True, True),
    (date(2026, 12, 25), "off", False, False),
    (date(2026, 12, 25), "blizzard", False, False),
]
wrong = [(d.isoformat(), m, s, e) for d, m, s, e in TABLE if W.snow_active(d, m, s) != e]
check(f"all {len(TABLE)} rows of the date table", not wrong, str(wrong))

print("\n--- the setting ---")
check("default is christmas", cfg_module.DEFAULTS["snow"] == "christmas")
check("a hand-edited value is tidied", cfg_module._coerce("snow", " WINTER ") == "winter")
check("...and nonsense falls back to the default",
      cfg_module._coerce("snow", "blizzard") == "christmas")
check("the toast latch is a hidden bool",
      cfg_module.DEFAULTS["snow_hint_seen"] is False)

# --- the overlay on synthetic stacks ---------------------------------------------------------

MONS = SO.desktop_monitors()
MON = next((m for m in MONS if m.info is not None and m.info.primary), MONS[0])
S = MON.scale
mleft, mtop, mright, mbottom = MON.rect
FAKE = 0x7FFF0011                  # a key, never a real window: the stack is ours
RECT = (mleft + int(260 * S), mtop + int(260 * S), mleft + int(860 * S), mtop + int(600 * S))
STACK = [(FAKE, RECT, False, False)]
STATE = {"fullscreen": False, "cursor": (mright - 3, mbottom - 3), "swipe": None}


def cursor():
    swipe = STATE["swipe"]
    if swipe is None:
        return STATE["cursor"]
    # Back and forth along the pile at 2.5 px/ms (the brush needs 0.8), in the snow.
    x0, x1, y, t0 = swipe
    span = x1 - x0
    d = ((time.monotonic() - t0) * 2500.0) % (2 * span)
    return (int(x0 + (d if d < span else 2 * span - d)), int(y))


# The synthetic desktop's foreground glance is its whole stack: on the real one a window opening,
# closing, moving or coming to the front changes the foreground window or its rectangle, and that
# is what lets the poll back off at rest (SO.STACK_SETTLE_PASSES) without missing it.
ov = SO.SnowOverlay(stack=lambda: list(STACK), monitors=lambda: MONS,
                    fullscreen=lambda m: STATE["fullscreen"] and m is MON, cursor=cursor,
                    glance=lambda: (tuple(STACK), STATE["fullscreen"]))
appeared = []
ov.appeared.connect(lambda: appeared.append(True))


def timers():
    return {name: getattr(ov, name).isActive()
            for name in ("stack_timer", "anim_timer", "pointer_timer", "linger_timer")}


def fill(fraction=1.0):
    """A pile on the fake window, grown to ``fraction``, as the next stack pass will find it."""
    ov.field.add_cap(FAKE, RECT, ov._now_ms(), scale=S, fill=fraction)
    ov._tick_stack()


print("\n--- off, it is nothing at all ---")
check("no timer runs", not any(timers().values()), str(timers()))
check("no window exists", not ov.strips and not ov.flights)

# A stand-in for Nib: topmost, like his window, bubble, wheel and focus timer.
nib = QWidget(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
nib.setGeometry(mright - 140, mbottom - 220, 60, 60)
nib.show()
pump(100)
NIB = int(nib.winId())

print("\n--- on, at rest ---")
ov.set_state(active=True)
check("the stack poll runs", ov.stack_timer.isActive())
check("a bare pile is not shown", not any(s.isVisible() for s in ov.strips.values()))
fill(1.0)
strip = ov.strips.get(FAKE)
check("a grown pile gets a strip", strip is not None and strip.isVisible())
cap = ov.field.caps[FAKE]
ext = cap.extent()
check("...sized to the cap's extent",
      strip.geometry() == QRect(ext[0], ext[1], ext[2] - ext[0], ext[3] - ext[1]),
      f"{strip.geometry()} vs {ext}")
check("'appeared' fires once", appeared == [True], str(appeared))
pump(1200)
t = timers()
check("at rest only the stack poll runs (no animation, no pointer sampling)",
      t["stack_timer"] and not t["anim_timer"] and not t["pointer_timer"]
      and not t["linger_timer"], str(t))
check("no flight layer while nothing is in the air", not ov.flights)
check("'appeared' still fired only once", appeared == [True])

print("\n--- never takes a click ---")
SH = strip.hwnd()
check("the strip is WS_EX_TRANSPARENT", bool(ex_style(SH) & WS_EX_TRANSPARENT), hex(ex_style(SH)))
check("...and not topmost", not ex_style(SH) & WS_EX_TOPMOST, hex(ex_style(SH)))
from guard_overlay import WDA_EXCLUDEFROMCAPTURE, get_display_affinity  # noqa: E402

check("...and hidden from capture", get_display_affinity(SH) == WDA_EXCLUDEFROMCAPTURE,
      str(get_display_affinity(SH)))
img = strip.pixmap.toImage()
painted = [(x, y) for x in range(0, img.width(), 7) for y in range(img.height())
           if img.pixelColor(x, y).alpha() >= 200 and strip.region.contains(QRect(x, y, 1, 1))]
check("the pile has painted pixels to test", len(painted) > 50, str(len(painted)))


def snow_hwnds():
    return {s.hwnd() for s in ov.strips.values()} | {f.hwnd() for f in ov.flights.values()}


hits = [(x, y) for x, y in painted[::9][:40] if window_at(ext[0] + x, ext[1] + y) in snow_hwnds()]
check("WindowFromPoint never returns a snow window over a painted pile", not hits, str(hits[:3]))

# Re-created behind our back: the click-through and the capture exclusion must come back.
strip.hide()
strip.destroy()
strip.create()
ov._refresh(FAKE, render=False)
pump(100)
SH2 = strip.hwnd()
check("a re-created strip is click-through again", bool(ex_style(SH2) & WS_EX_TRANSPARENT))
check("...and hidden from capture again", get_display_affinity(SH2) == WDA_EXCLUDEFROMCAPTURE,
      str(get_display_affinity(SH2)))

print("\n--- below Nib ---")
z = z_order()
check("the strip is below every topmost window of Nib's", z.index(NIB) < z.index(SH2),
      f"nib {z.index(NIB)} strip {z.index(SH2)}")
ov._raise_strips()
pump(50)
z = z_order()
check("...and still is after the strips are raised", z.index(NIB) < z.index(SH2))

print("\n--- his own windows hide the snow, and the strips are raised back up ---")
# An ordinary window of Nib's (Settings, a message box) over part of the pile, shown after the
# strip, so it starts above it. list_window_stack never lists it: the overlay must add it. The
# stack poll is held off meanwhile, so the order before its next pass can be seen.
ov.stack_timer.stop()
mine = QWidget(None, Qt.FramelessWindowHint | Qt.Tool)
mine.setGeometry(ext[0] + 60, ext[1] - 10, 120, 80)
mine.show()
pump(100)
MINE = int(mine.winId())
z = z_order()
check("an own window shown later starts above the strip", z.index(MINE) < z.index(SH2),
      f"mine {z.index(MINE)} strip {z.index(SH2)}")
UNDER = QRect(70, 20, 1, 1)        # strip-local, inside the own window's rectangle
check("before the next pass the strip still draws there", strip.region.contains(UNDER))
ov._tick_stack()
pump(50)
check("it hides the part of the pile under it (the strip's clip leaves it out)",
      strip.isVisible() and not strip.region.contains(UNDER), str(strip.region.boundingRect()))
check("...and never carries a pile itself", MINE not in ov.field.caps)
z = z_order()
check("the front window changed, so the strips were raised back above it",
      z.index(SH2) < z.index(MINE), f"mine {z.index(MINE)} strip {z.index(SH2)}")
check("...and still below Nib", z.index(NIB) < z.index(SH2))
# With it still open, other apps coming to the front in turn must each raise the strips: the
# front window is read from the real stack, never from his own windows heading it.
OTHER = 0x7FFF0033
ORECT = (RECT[0], RECT[3] + int(60 * S), RECT[0] + int(200 * S), RECT[3] + int(160 * S))
raises = []
_real_raise = ov._raise_strips
ov._raise_strips = lambda: (raises.append(True), _real_raise())
for order in ([(OTHER, ORECT, False, False), (FAKE, RECT, False, False)],
              [(FAKE, RECT, False, False), (OTHER, ORECT, False, False)]) * 2:
    STACK[:] = order
    ov._tick_stack()
del ov._raise_strips
STACK[:] = [(FAKE, RECT, False, False)]
ov._tick_stack()
check("with his own window open, each app coming to the front raises the strips",
      len(raises) == 4, f"{len(raises)} raises")
mine.close()
pump(50)
ov._tick_stack()
ov.stack_timer.start()
check("with it gone, the whole pile shows again", strip.region.contains(UNDER))

print("\n--- the pointer, sampled only near a pile ---")
STATE["cursor"] = (ext[0] + 40, ext[1] - 30)
check("the pointer near a pile starts the fast sampling within a stack tick",
      pump_until(lambda: ov.pointer_timer.isActive(), 1500))
STATE["cursor"] = (mright - 3, mbottom - 3)
check("...and it stops once the pointer has been away a while",
      pump_until(lambda: not ov.pointer_timer.isActive(), SO.NEAR_LINGER_MS + 1500))
ov.set_state(active=True, frozen=True)
STATE["cursor"] = (ext[0] + 40, ext[1] - 30)
pump(1200)
check("frozen (focus, paused, ghost), the pointer is never sampled",
      not ov.pointer_timer.isActive())
ov.set_state(active=True)

print("\n--- a pile is rendered once per version, never per pass ---")
renders = []
_real_render = SO.render_cap
SO.render_cap = lambda c: (renders.append(c.hwnd), _real_render(c))[1]
ov.set_state(active=True, growing=False)       # growth steps are renders of their own
pump(1200)
check("none at rest, over two stack passes", not renders, str(renders))
OCC = 0x7FFF0044                                # a window in front, over part of the pile
STACK.insert(0, (OCC, (ext[0] + 100, ext[1] - 40, ext[0] + 260, ext[3] + 200), False, False))
ov._tick_stack()
clipped = not strip.region.contains(QRect(150, 2, 1, 1))
STACK.pop(0)
ov._tick_stack()
check("none for a clip-only change (a window over the pile, then gone)",
      clipped and not renders, f"clipped={clipped} renders={renders}")
ov.set_state(active=True, hidden=True)
ov.set_state(active=True, growing=False)
check("none for a refit (back from hidden)", not renders and strip.isVisible(), str(renders))
ov.set_state(active=True)

print("\n--- a swipe knocks snow off, and the flight layer comes and goes ---")
ov.take_gestures()
line_y = cap.top - 6 * S
# First through a window of Nib's lying over the pile: what is under him is not there to brush.
nib.setGeometry(int(cap.rect[0]), int(line_y - 40 * S), int(500 * S), int(80 * S))
pump(50)
STATE["swipe"] = (cap.rect[0] + 20 * S, cap.rect[0] + 480 * S, line_y, time.monotonic())
STATE["cursor"] = (int(cap.rect[0] + 20 * S), int(line_y))
ov._near_until = ov._now_ms() + 2000
ov.pointer_timer.start()                          # as if it had come from beside him
pump(1500)
STATE["swipe"] = None
STATE["cursor"] = (mright - 3, mbottom - 3)
check("a swipe over Nib's window never brushes the snow under it",
      not any(g.kind == "brush" for g in ov.gestures) and not ov.field.airborne,
      str([g.kind for g in ov.gestures]))
nib.setGeometry(mright - 140, mbottom - 220, 60, 60)
pump(50)
renders.clear()
t_brush = time.monotonic()
STATE["swipe"] = (cap.rect[0] + 20 * S, cap.rect[2] - 160 * S, line_y, time.monotonic())
STATE["cursor"] = (int(cap.rect[0] + 20 * S), int(line_y))
got = pump_until(lambda: any(g.kind == "brush" for g in ov.gestures), 4000)
STATE["swipe"] = None
STATE["cursor"] = (mright - 3, mbottom - 3)
check("a fast swipe through the pile brushes snow off", got,
      str([g.kind for g in ov.gestures]))
check("the flight layer exists while snow is in the air",
      pump_until(lambda: bool(ov.flights) or not ov.field.airborne, 500) and bool(ov.flights))
check("...with the animation running", ov.anim_timer.isActive() or not ov.field.airborne)
layer = next(iter(ov.flights.values()), None)
if layer is not None:
    LH = layer.hwnd()
    check("the flight layer is click-through and not topmost",
          bool(ex_style(LH) & WS_EX_TRANSPARENT) and not ex_style(LH) & WS_EX_TOPMOST,
          hex(ex_style(LH)))
    z = z_order()
    check("...below Nib", z.index(NIB) < z.index(LH), f"nib {z.index(NIB)} layer {z.index(LH)}")
    pump(40)
    chunk_hits = [c for c in list(ov.field.chunks)[:20] if window_at(c.x, c.y) in snow_hwnds()]
    check("WindowFromPoint never returns the flight layer over a flying chunk", not chunk_hits)
landed = pump_until(lambda: not ov.field.airborne, snow.MAX_AIR_MS + 1000)
check("everything lands", landed)
bound = (time.monotonic() - t_brush) * 1000.0 / SO.RENDER_GAP_MS + 2
check("re-rendered at most every RENDER_GAP_MS while it flew",
      renders.count(FAKE) <= bound, f"{renders.count(FAKE)} renders, bound {bound:.0f}")
SO.render_cap = _real_render
pump(100)
check("then the animation stops", not ov.anim_timer.isActive())
check("...and the layer lingers a moment", bool(ov.flights) and ov.linger_timer.isActive())
check("...and is destroyed about 2 s after the last chunk lands",
      pump_until(lambda: not ov.flights, SO.FLIGHT_LINGER_MS + 1500))
pump(100)
if layer is not None:
    check("...its window really gone", not user32.IsWindow(wintypes.HWND(LH)))
check("the landings are there for Nib to react to",
      any(l.where in ("floor", "cap", "nib") for l in ov.take_landed()))

print("\n--- a drop from the model flies the same way ---")
fill(1.0)
n = ov.field.release_span(FAKE, cap.x0, cap.x1, ov._now_ms())
pump(700)
check("released snow starts the animation and a layer", n > 0 and (bool(ov.flights)
                                                                   or not ov.field.airborne))
pump_until(lambda: not ov.field.airborne and not ov.flights, snow.MAX_AIR_MS + 3000)
check("...and both are gone once it has landed", not ov.flights and not ov.anim_timer.isActive())

print("\n--- hidden: snooze and the guard ---")
fill(1.0)
strip = ov.strips[FAKE]
check("shown before", strip.isVisible())
ov.set_state(active=True, hidden=True)
check("hidden: no strip on screen", not any(s.isVisible() for s in ov.strips.values()))
check("...and no timer at all", not any(timers().values()), str(timers()))
check("...but the snow is kept (hidden, not empty)",
      ov.field is not None and ov.field.caps[FAKE].mass > 0)
ov.set_state(active=True)
check("back with nothing changed meanwhile: the strip shows again", ov.strips[FAKE].isVisible())
ov.set_state(active=True, hidden=True)
ov.field.release_span(FAKE, cap.x0, cap.x0 + 100, ov._now_ms())
pump(700)
check("...and nothing flies while hidden", not ov.flights and not ov.anim_timer.isActive())
ov.set_state(active=True)
check("back: the strip shows again", ov.strips[FAKE].isVisible())
pump_until(lambda: not ov.field.airborne and not ov.flights, snow.MAX_AIR_MS + 3000)

print("\n--- hidden behind a fullscreen app ---")
fill(1.0)
STATE["fullscreen"] = True
pump(700)
check("the foreground fullscreen test hides that monitor's snow",
      not ov.strips[FAKE].isVisible() and MONS.index(MON) in ov.fullscreen)
hid = ov.field.caps[FAKE]
hx0, hy0 = hid.extent()[:2]
STATE["cursor"] = (hx0 + 40, hy0 - 30)
pump(1200)
check("...the pointer near its hidden pile is never sampled", not ov.pointer_timer.isActive())
STATE["cursor"] = (mright - 3, mbottom - 3)
ov.field.add_cap(FAKE, RECT, ov._now_ms(), scale=S, fill=0.5)
hid = ov.field.caps[FAKE]
hid.next_grow_ms = hid.next_icicle_ms = ov._now_ms() - 1.0      # due now
before = hid.mass
ov._tick_stack()
check("...and it does not grow", hid.mass == before, f"{before} -> {hid.mass}")
fill(1.0)
ov.field.release_span(FAKE, cap.x0, cap.x1, ov._now_ms())
pump(500)
check("...and no flight layer is ever made over it", MONS.index(MON) not in ov.flights)
STATE["fullscreen"] = False
pump_until(lambda: not ov.field.airborne, snow.MAX_AIR_MS + 1000)
pump(700)
fill(1.0)
check("it comes back when the app goes", ov.strips[FAKE].isVisible())
# A window covering the monitor that is not the foreground one: the stack pass's own test.
COVER = 0x7FFF0022
STACK.append((COVER, MON.rect, False, False))     # behind the pile's window, covering it all
pump(700)
check("a window covering the monitor anywhere in the stack hides it too",
      not ov.strips[FAKE].isVisible() and MONS.index(MON) in ov.fullscreen)
STACK.pop()
pump(700)
fill(1.0)
check("...and it comes back with that window gone", ov.strips[FAKE].isVisible())

print("\n--- at rest the stack poll backs off, and snaps back ---")
# Its own overlay, ticked by hand (the timer stopped), so the count is exact.
BO = {"stack": [(FAKE, RECT, False, False)], "glance": 1, "cursor": (mright - 3, mbottom - 3)}
walks = []


def bo_stack():
    walks.append(1)
    return list(BO["stack"])


bo = SO.SnowOverlay(stack=bo_stack, monitors=lambda: MONS, fullscreen=lambda m: False,
                    cursor=lambda: BO["cursor"], glance=lambda: BO["glance"])
bo.set_state(active=True)
bo.stack_timer.stop()
# Under QUIET_FRACTION, so the move below drops it quietly: nothing in the air to keep it polling.
bo.field.add_cap(FAKE, RECT, bo._now_ms(), scale=S, fill=0.1)


def ticks(n):
    before = len(walks)
    for _ in range(n):
        bo._on_stack_timer()
    return len(walks) - before


ticks(SO.STACK_SETTLE_PASSES + 2)
still = ticks(40)
check("pointer still, nothing changing: a full pass every STACK_IDLE_EVERY-th tick",
      still == 40 // SO.STACK_IDLE_EVERY, f"{still} passes in 40 ticks")
BO["cursor"] = (BO["cursor"][0] - 50, BO["cursor"][1])
check("the pointer moving: a full pass on that very tick", ticks(1) == 1)
ticks(SO.STACK_SETTLE_PASSES + 2)
BO["glance"] += 1
check("another window at the front (or the front one moved): a full pass at once", ticks(1) == 1)
moved = (RECT[0] + 40, RECT[1], RECT[2] + 40, RECT[3])
ticks(SO.STACK_SETTLE_PASSES + 2)
BO["stack"] = [(FAKE, moved, False, False)]
got = 0
for k in range(SO.STACK_IDLE_EVERY):
    ticks(1)
    if bo.field.caps.get(FAKE) is not None and bo.field.caps[FAKE].rect == moved:
        got = k + 1
        break
check("a change neither glance sees is still found within STACK_IDLE_EVERY ticks",
      got and got <= SO.STACK_IDLE_EVERY, str(got))
check("...and the poll is back at every tick after it",
      ticks(SO.STACK_SETTLE_PASSES) == SO.STACK_SETTLE_PASSES)
ticks(SO.STACK_SETTLE_PASSES + 2)
cap_b = bo.field.caps[FAKE]
cap_b.next_grow_ms = bo._now_ms() - 1.0
mass_b = cap_b.mass
passes = ticks(SO.STACK_IDLE_EVERY)
check("piles still grow while it is backed off", passes == 1 and cap_b.mass > mass_b,
      f"{passes} passes, {mass_b:.0f} -> {cap_b.mass:.0f}")
# WP4's acceptance: a growth step is one re-render on the poll, never an animation tick.
check("...and a growth step starts no animation and no pointer sampling",
      not bo.anim_timer.isActive() and not bo.pointer_timer.isActive() and not bo.flights
      and not bo.field.airborne,
      f"anim={bo.anim_timer.isActive()} pointer={bo.pointer_timer.isActive()}")
before = len(walks)
for _ in range(20):
    bo._tick_stack()
check("called directly, a pass always walks the stack", len(walks) - before == 20)
bext = bo.field.caps[FAKE].extent()
BO["cursor"] = (bext[0] + 40, bext[1] - 20)          # near the pile, and then resting there
ticks(1)
check("the pointer coming to rest near a pile starts the fast sampling",
      bo.pointer_timer.isActive())
check("...which stops once it has rested there NEAR_LINGER_MS",
      pump_until(lambda: not bo.pointer_timer.isActive(), SO.NEAR_LINGER_MS + 1500))
ticks(SO.STACK_SETTLE_PASSES + 2)
resting = ticks(40)
check("...so a pointer resting by a window's top edge does not keep the poll at full rate",
      resting == 40 // SO.STACK_IDLE_EVERY and not bo.pointer_timer.isActive(),
      f"{resting} passes in 40 ticks")
BO["cursor"] = (bext[0] + 60, bext[1] - 20)
ticks(1)
check("...and moving it again starts the sampling at once", bo.pointer_timer.isActive())
bo.shutdown()

print("\n--- off again ---")
ov.set_state(active=False)
pump(100)
check("off: every timer stopped", not any(timers().values()), str(timers()))
check("...every window gone", not ov.strips and not ov.flights and ov.field is None)
nib.close()

# --- wired into the app ----------------------------------------------------------------------

print("\n--- the wiring in main ---")
import main as M  # noqa: E402
from config import Config  # noqa: E402

path = SCRATCH / "snow_overlay_app.json"
path.unlink(missing_ok=True)
cfg = Config(path)
cfg["intro_seen"] = True
cfg["last_seen_date"] = "2026-07-31"
cfg["mic_indicator_enabled"] = False
cfg["camera_indicator_enabled"] = False
cfg["snow"] = "off"
nibapp = M.NibApp(app, cfg)
sn = nibapp.snow
# No real window ever carries a pile here: one grown on the real desktop would emit 'appeared'
# (and latch the toast) whenever the desktop happened to allow it.
sn._stack_source = lambda: []
nibapp.config["snow"] = "always"
nibapp._update_snow()
pump(300)
check("snow 'always' turns the overlay on", sn.active and sn.showing)
check("...and offers 'Snow: off for today' in the tray", nibapp.tray._snow_off.isVisible())
pump(200)
check("his rectangle is handed to the snow", sn.field is not None and sn.field.nib is not None,
      str(sn.field.nib if sn.field else None))
nibapp._snooze(600)
check("snoozed: hidden, timers stopped", sn.hidden and not sn.stack_timer.isActive())
nibapp._wake_from_snooze()
check("awake: showing again", sn.showing and sn.stack_timer.isActive())
nibapp._set_guarding(True)
check("a guard: hidden", sn.hidden and not sn.stack_timer.isActive())
nibapp._set_guarding(False)
check("...and back after it", sn.showing)
nibapp.behavior.paused = True
nibapp._update_snow()
check("paused: frozen (shown, not growing)", sn.frozen and not sn.growing and sn.showing)
nibapp.behavior.paused = False
nibapp.behavior._focus_active = True            # a property; what a focus session sets
nibapp._update_snow()
check("focusing: frozen", sn.frozen and not sn.growing and sn.showing)
nibapp.behavior._focus_active = False
nibapp.config["ghost_mode"] = True
nibapp._update_snow()
check("ghost mode: frozen", sn.frozen)
nibapp.config["ghost_mode"] = False
nibapp._update_snow()
check("...and not after", not sn.frozen and sn.growing)
nibapp.config["world_enabled"] = False
nibapp._update_snow()
check("world_enabled off: no snow", not sn.active and sn.field is None)
nibapp.config["world_enabled"] = True
nibapp._update_snow()
nibapp.tray.snow_off_requested.emit()
pump(50)
check("'Snow: off for today' puts it away", not sn.active and sn.field is None)
check("...and the entry goes with it", not nibapp.tray._snow_off.isVisible())
nibapp._on_settings_changed({"snow"})
check("choosing the snow in Settings brings it back the same day",
      sn.active and nibapp.tray._snow_off.isVisible())
nibapp.config["snow"] = "off"
nibapp._update_snow()
check("snow 'off': none", not sn.active)
nibapp.config["snow"] = "always"
nibapp._update_snow()
check("...and back on", sn.active)

print("\n--- on battery ---")
_real_battery = M.win.on_battery
check("win.on_battery answers", isinstance(_real_battery(), bool))
M.win.on_battery = lambda: True
nibapp._update_snow()
check("on battery: the piles stay, but do not grow, and snow flies at 20 FPS",
      sn.showing and not sn.growing and sn.on_battery
      and sn.anim_timer.interval() == snow.SnowField.frame_ms(True),
      f"growing={sn.growing} interval={sn.anim_timer.interval()}")
M.win.on_battery = lambda: False
nibapp._update_snow()
check("...and on mains they grow again, at 30 FPS",
      sn.growing and sn.anim_timer.interval() == snow.SnowField.frame_ms(False))
M.win.on_battery = _real_battery

print("\n--- the news reaches him (WP5) ---")
heard = []
_b = nibapp.behavior
_real_landed, _real_nearby = _b.snow_landed, _b.snow_nearby
_b.snow_landed = lambda snowball=False: heard.append("landed")
_b.snow_nearby = lambda x, y: heard.append(("near", x, y))
sn.landed.append(snow.Landing("floor", 0.0, 0.0, 1.0, "lump", mode="brush"))
nibapp._tick_snow()
check("snow landing elsewhere is not news to him", heard == [], str(heard))
sn.landed.append(snow.Landing("nib", 10.0, 10.0, 1.0, "lump", mode="drop"))
sn.landed.append(snow.Landing("nib", 10.0, 10.0, 1.0, "lump", mode="puff"))
nibapp._tick_snow()
check("...nor a pile falling off a window that moved above him, nor his own puff", heard == [],
      str(heard))
sn.landed.append(snow.Landing("nib", 10.0, 10.0, 1.0, "lump", mode="brush"))
sn.landed.append(snow.Landing("nib", 12.0, 10.0, 1.0, "lump", mode="flick"))
sn.gestures.append(snow.Gesture("brush", 1, 300.0, 200.0, 50.0, 0.0))
nibapp._tick_snow()
check("snow landing on him and a brush are handed to Behavior, once each tick",
      heard == ["landed", ("near", 300.0, 200.0)], str(heard))
heard.clear()
nibapp._tick_snow()
check("...and the queues were drained", heard == [] and not sn.landed and not sn.gestures)
_b.snow_landed, _b.snow_nearby = _real_landed, _real_nearby


class SnowStub:
    """What _tick_snow asks of the overlay, recorded: the dent, the snowy check, the puff."""

    showing = True

    def __init__(self):
        self.calls = []

    def take_landed(self):
        return []

    def take_gestures(self):
        return []

    def set_perch(self, hwnd, span):
        self.calls.append(("perch", hwnd, span))

    def fullness(self, hwnd):
        return 0.5

    def puff(self, x, y, half_width=0.0):
        self.calls.append(("puff", x, y, half_width))


stub, real_snow = SnowStub(), nibapp.snow
nibapp.snow = stub
PERCHED = 0x7FFF0055
_b.perch_on((mleft + 300, mtop + 400, mleft + 900, mtop + 800))
nibapp._perched_hwnd = PERCHED
nibapp._tick_snow()
bx, _by, bw, _bh = _b.rect
check("perched: the dent goes under his feet on that window's pile",
      stub.calls == [("perch", PERCHED, (bx + bw * 0.3, bx + bw * 0.7))], str(stub.calls))
check("...and half a pile is snowy enough to shiver about", _b._snowy_perch)
stub.calls.clear()
_b._snow_puff_due = True
nibapp._tick_snow()
puffs = [c for c in stub.calls if c[0] == "puff"]
check("a puff is kicked up from the line his soles stand on, beside him",
      puffs == [("puff", bx + bw / 2.0, _b.feet_y, bw * 0.45)]
      and _b.feet_y == mtop + 400, str(puffs))
_b._release_perch()
_b._enter(M.State.IDLE)
nibapp._perched_hwnd = 0
stub.calls.clear()
nibapp._tick_snow()
check("off the window: no dent, nothing snowy",
      stub.calls == [("perch", 0, None)] and not _b._snowy_perch, str(stub.calls))
nibapp.snow = real_snow

# The toast, with the polls stopped so nothing but these calls moves it on.
nibapp._geometry_timer.stop()
nibapp._tick_timer.stop()
nibapp.speech.dismiss()
nibapp.config["snow_hint_seen"] = False
sn._appeared = False
nibapp._snow_hint_due = False
nibapp.behavior._away = True
sn.appeared.emit()
pump(50)
check("you away, the first snow's message waits", not nibapp.speech.isVisible()
      and not nibapp.config["snow_hint_seen"] and nibapp._snow_hint_due)
nibapp.behavior._away = False
nibapp.config["snow"] = "off"
nibapp._update_snow()
pump(50)
check("...and with no snow on screen too", not nibapp.speech.isVisible()
      and not nibapp.config["snow_hint_seen"] and nibapp._snow_hint_due)
nibapp.config["snow"] = "always"
nibapp._update_snow()
pump(50)
check("the first snow brings the one-time message, once it can be seen",
      nibapp.speech.isVisible(), str({"away": nibapp.behavior.away,
                                     "state": nibapp.behavior.state.name}))
check("...and latches it", bool(nibapp.config["snow_hint_seen"]))
nibapp.speech.dismiss()
sn.appeared.emit()
pump(50)
check("...which never shows twice", not nibapp.speech.isVisible())
nibapp._on_quit()
pump(100)
nibapp._update_snow()                               # what the geometry poll would do next
check("quitting takes the snow down, for good", not sn.active and sn.field is None)

print("\n--- invisible windows of other programs carry no snow ---")
# A click-through overlay (some other tool's transparent strip above the taskbar, say) reports
# itself visible, but there is nothing there: no pile, and nothing for him to stand on.
see_through = QWidget(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowTransparentForInput)
see_through.setGeometry(300, 300, 340, 60)
see_through.show()
solid = QWidget(None, Qt.Tool | Qt.FramelessWindowHint)
solid.setGeometry(300, 400, 340, 60)
solid.show()
pump(100)
check("a click-through window counts as see-through", win._is_see_through(int(see_through.winId())))
check("...an ordinary one does not", not win._is_see_through(int(solid.winId())))
see_through.close()
solid.close()

print("\n" + "=" * 60)
print("FAILURES: " + ", ".join(fails) if fails else "snow overlay OK")
sys.exit(1 if fails else 0)
