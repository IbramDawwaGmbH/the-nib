"""Play in the snow (WP6): the time-boxed input surface, the snowball, and every way out.

Driven through a real ``QApplication`` over the real monitors, with a synthetic window stack (as
``smoke_snow_overlay.py``), and checked against Win32 where it matters: ``WindowFromPoint`` finds
a play layer while play mode is on and never a snow window once it ends, and a right-click sent
to the layer's HWND as a real window message ends it. Presses, drags and releases are otherwise
handed to the layers as Qt mouse events. The model's part (scoop, carry, throw) is checked here
too, along with the wiring in ``main`` and Nib's reaction to a snowball.
"""

import ctypes
import inspect
import os
import sys
import time
from ctypes import wintypes
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

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import behavior as Bh  # noqa: E402
import snow  # noqa: E402
import snow_overlay as SO  # noqa: E402
import tool_wheel as TW  # noqa: E402

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
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
GA_ROOT = 2
WS_EX_TRANSPARENT = 0x20
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
MK_RBUTTON = 0x0002


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


def alive(hwnd):
    """A window that could still take a click: it exists and is on screen."""
    return bool(user32.IsWindow(wintypes.HWND(hwnd))) and bool(
        user32.IsWindowVisible(wintypes.HWND(hwnd)))


# --- the overlay on a synthetic stack --------------------------------------------------------

MONS = SO.desktop_monitors()
MON = next((m for m in MONS if m.info is not None and m.info.primary), MONS[0])
MON_IDX = MONS.index(MON)
S = MON.scale
mleft, mtop, mright, mbottom = MON.rect
FAKE = 0x7FFF0021                  # a key, never a real window: the stack is ours
RECT = (mleft + int(260 * S), mtop + int(260 * S), mleft + int(860 * S), mtop + int(600 * S))
STACK = [(FAKE, RECT, False, False)]
STATE = {"fullscreen": False, "monitors": MONS}
# A transparent spot on the primary monitor, below the pile: what WindowFromPoint is asked.
PROBE = (RECT[0] + int(120 * S), RECT[3] + int(60 * S))

ov = SO.SnowOverlay(stack=lambda: list(STACK), monitors=lambda: STATE["monitors"],
                    fullscreen=lambda m: STATE["fullscreen"] and m is MON,
                    cursor=lambda: (mright - 3, mbottom - 3),
                    glance=lambda: (tuple(STACK), STATE["fullscreen"]))
ended = []
ov.play_ended.connect(ended.append)


def ready():
    """Snow showing, unfrozen, with a full pile on the fake window."""
    STATE["fullscreen"] = False
    STATE["monitors"] = MONS
    ov.set_state(active=True)
    # A window that has just come into the stack starts with only a dusting, which is not
    # enough to scoop: give the fake one a full pile.
    if FAKE not in ov.field.caps or max(ov.field.caps[FAKE].heights) < 10 * S:
        ov.field.add_cap(FAKE, RECT, ov._now_ms(), scale=S, fill=1.0)
    ov._tick_stack()


def play_layers():
    return {idx: layer for idx, layer in ov.flights.items() if layer.takes_input}


def start():
    ready()
    ended.clear()
    ok = ov.start_play()
    pump(80)
    return ok


def check_over(label, hwnds, reason=None):
    """Play mode is over, and nothing of it can take a click."""
    pump(80)
    check(f"{label}: play mode is over", not ov.playing)
    check(f"{label}: ...no play layer is left", not play_layers(), str(list(play_layers())))
    check(f"{label}: ...its timer is stopped", not ov.play_timer.isActive())
    check(f"{label}: ...no snowball is left in a hand",
          ov.field is None or ov.field.ball is None)
    check(f"{label}: ...the old layers are off the screen",
          not any(alive(h) for h in hwnds), str([hex(h) for h in hwnds if alive(h)]))
    check(f"{label}: ...WindowFromPoint never finds one", window_at(*PROBE) not in hwnds,
          hex(window_at(*PROBE)))
    still = [f.hwnd() for f in ov.flights.values()]
    check(f"{label}: ...any layer still flying snow is click-through",
          all(ex_style(h) & WS_EX_TRANSPARENT for h in still), str(still))
    if reason is not None:
        check(f"{label}: ...and it says why", ended == [reason], str(ended))


def mouse(kind, x, y, button=Qt.LeftButton):
    """A Qt mouse event on whichever play layer holds screen point (x, y)."""
    for layer in play_layers().values():
        ox, oy = layer.origin
        local = QPointF(x - ox, y - oy)
        if not layer.rect().contains(local.toPoint()):
            continue
        etype = {"press": QEvent.MouseButtonPress, "move": QEvent.MouseMove,
                 "release": QEvent.MouseButtonRelease}[kind]
        btn = Qt.NoButton if kind == "move" else button
        held = Qt.NoButton if kind == "release" else button
        QApplication.sendEvent(layer, QMouseEvent(etype, local, QPointF(x, y), btn, held,
                                                  Qt.NoModifier))
        return True
    return False


def pile_point():
    cap = ov.field.caps[FAKE]
    x = (RECT[0] + RECT[2]) / 2.0
    i = cap.column_at(x)
    return x, cap.top - cap.heights[i] / 2.0


# --- the model's part ------------------------------------------------------------------------

print("--- the snowball, in the model ---")
check("snow.py still imports no PySide6",
      "PySide6" not in (Path(SRC) / "snow.py").read_text(encoding="utf-8"))
f = snow.SnowField()
f.set_monitors([MON.rect], [MON.floor], [S])
f.add_cap(1, RECT, 0.0, scale=S, fill=1.0)
before = f.mass_in_caps
total = f.mass_in_caps + f.mass_in_air
cx = (RECT[0] + RECT[2]) / 2.0
check("a press on nothing scoops nothing", not f.scoop(cx, RECT[3] + 200 * S, 0.0))
check("a press on the pile scoops a snowball", f.scoop(cx, RECT[1] - 2, 0.0) and f.ball)
check("...taken from the pile", f.mass_in_caps < before, f"{f.mass_in_caps} vs {before}")
check("...and nothing is lost: it counts as in the air",
      abs(f.mass_in_caps + f.mass_in_air - total) < 1e-6)
check("...one at a time", not f.scoop(cx, RECT[1] - 2, 1.0))
for k in range(1, 6):
    f.carry(cx + 20 * k, RECT[1] - 40, 10.0 * k)
ball = f.throw(cx + 120, RECT[1] - 40, 60.0)
check("letting go throws it", ball is not None and f.ball is None and ball in f.chunks)
check("...at the pointer's speed, the way it was going", ball is not None and ball.vx > 500,
      ball.vx if ball else None)
check("...as a snowball", ball is not None and ball.kind == "ball" and ball.mode == "throw")
check("nothing in hand, nothing to throw", f.throw(0, 0, 70.0) is None)
f.scoop(cx - 100, RECT[1] - 2, 100.0)
dropped = f.drop_ball(100.0)
check("a ball still in hand when play ends is dropped, not thrown at him",
      dropped is not None and dropped.mode == "drop" and dropped.vx == 0.0)

# --- on screen -------------------------------------------------------------------------------

print("\n--- unavailable when it should be ---")
check("with no snow, it does not start", ov.play_blocker() == "off" and not ov.start_play())
ov.set_state(active=True)
ov._tick_stack()
check("with no pile on screen, it says so", ov.play_blocker() == "empty", ov.play_blocker())
ready()
ov.set_state(active=True, hidden=True)
check("hidden (snoozed, a guard), it does not start",
      ov.play_blocker() == "hidden" and not ov.start_play())
ready()
ov.set_state(active=True, frozen=True)
check("frozen (focus, paused, ghost mode), it does not start",
      ov.play_blocker() == "frozen" and not ov.start_play())
check("...and nothing was put up", not play_layers())

print("\n--- on: the whole screen takes the clicks, for a while ---")
check("it starts", start() and ov.playing)
layers = play_layers()
check("a play layer on every monitor", sorted(layers) == list(range(len(MONS))), str(layers))
hwnds = [layer.hwnd() for layer in layers.values()]
check("...none of them click-through", not any(ex_style(h) & WS_EX_TRANSPARENT for h in hwnds),
      str([hex(ex_style(h)) for h in hwnds]))
check("...none created with WindowTransparentForInput",
      not any(layer.windowFlags() & Qt.WindowTransparentForInput for layer in layers.values()))
check("...WindowFromPoint finds one over a transparent spot",
      window_at(*PROBE) in hwnds, hex(window_at(*PROBE)))
check("the time-box is running, about half a minute",
      ov.play_timer.isActive() and 20_000 <= ov.play_timer.interval() <= 40_000)
check("no brushing while you play", not ov.pointer_timer.isActive())

print("\n--- scoop, drag, throw ---")
x, y = pile_point()
mass = ov.field.caps[FAKE].mass
check("a press on the pile picks up a snowball", mouse("press", x, y) and ov.field.ball)
check("...and the pile is smaller", ov.field.caps[FAKE].mass < mass)
check("...and redrawn smaller at once", ov.strips[FAKE].version == ov.field.caps[FAKE].version)
check("the ball in hand is drawn", ov.field.ball in ov._chunks_on())
check("...as a snowball", not ov._sprite(ov.field.ball).isNull())
for k in range(1, 5):
    mouse("move", x + 15 * k * S, y - 30 * S)
    pump(12)
check("dragging carries it", ov.field.ball is not None and ov.field.ball.x > x)
mouse("release", x + 75 * S, y - 30 * S)
check("letting go throws it", ov.field.ball is None and ov.field.airborne)
check("...and it flies", ov.anim_timer.isActive())
check("...the layers stay up while it does", play_layers() and ov.playing)
pump_until(lambda: not ov.field.airborne, 6000)
pump(SO.FLIGHT_LINGER_MS + 200)
check("once it has landed, play mode carries on (the layers do not linger out)",
      ov.playing and play_layers())

print("\n--- a snowball can hit Nib ---")
# Nib a little way off the pile; let go above him with the pointer at rest, and it falls on him.
NIB_RECT = (RECT[2] + int(200 * S), RECT[1] + int(80 * S), RECT[2] + int(300 * S),
            RECT[1] + int(180 * S))
ov.set_nib(NIB_RECT)
ov.take_landed()
x, y = pile_point()
check("scooped another", mouse("press", x, y) and ov.field.ball)
above = ((NIB_RECT[0] + NIB_RECT[2]) / 2.0, NIB_RECT[1] - 40 * S)
mouse("move", *above)
pump(snow.THROW_WINDOW_MS + 60)
mouse("move", *above)          # still: the samples that set the throw's speed are all at rest
pump(30)
mouse("move", *above)
mouse("release", *above)
hit = []
pump_until(lambda: hit.extend(ov.take_landed()) or any(h.where == "nib" for h in hit), 12000)
nib_hits = [h for h in hit if h.where == "nib"]
check("it lands on him", bool(nib_hits), str([(h.where, h.kind) for h in hit]))
check("...as a snowball, thrown", bool(nib_hits) and nib_hits[0].kind == "ball"
      and nib_hits[0].mode in Bh.SNOW_HIT_MODES, str(nib_hits[:1]))
ov.set_nib(None)

print("\n--- every way out ---")
check_over_hwnds = lambda: [layer.hwnd() for layer in play_layers().values()]  # noqa: E731

hw = check_over_hwnds()
mouse("press", *PROBE, button=Qt.RightButton)
check_over("right-click", hw, "right-click")
check("...and a press after it scoops nothing", ov._play_input("press", Qt.LeftButton,
                                                               *pile_point()) is None
      and ov.field.ball is None)

# The same, as a real WM_RBUTTONDOWN sent to the layer's HWND: Qt has to deliver it.
start()
layer = play_layers()[MON_IDX]
lx, ly = PROBE[0] - layer.origin[0], PROBE[1] - layer.origin[1]
lparam = (ly & 0xFFFF) << 16 | (lx & 0xFFFF)
user32.SendMessageW(wintypes.HWND(layer.hwnd()), WM_RBUTTONDOWN, MK_RBUTTON, lparam)
user32.SendMessageW(wintypes.HWND(layer.hwnd()), WM_RBUTTONUP, 0, lparam)
check("a right-click sent as a real window message reaches the layer and ends it",
      pump_until(lambda: not ov.playing, 1000))
ov.end_play()

# The time-box.
ov.play_timer.setInterval(300)
start()
hw = check_over_hwnds()
pump_until(lambda: not ov.playing, 2000)
check_over("the time running out", hw, "time's up")
ov.play_timer.setInterval(SO.PLAY_MS)

# The poll's backstop, in case the timer never fired.
start()
ov.play_timer.stop()
ov._play_deadline = ov._now_ms() - SO.PLAY_GRACE_MS - 1
hw = check_over_hwnds()
ov._on_stack_timer()
check_over("the stack poll past the deadline", hw, "time's up")

# With a snowball in hand.
start()
mouse("press", *pile_point())
held = ov.field.ball
hw = check_over_hwnds()
ov.end_play("stopped from the wheel")
check_over("stopped from the wheel, a ball in hand", hw, "stopped from the wheel")
check("...the ball fell, a drop", held is not None and held in ov.field.chunks
      and held.mode == "drop", held.mode if held else None)
pump_until(lambda: not ov.field.airborne, 6000)

for label, reason, act in (
        ("snoozed or a guard (hidden)", "snow hidden",
         lambda: ov.set_state(active=True, hidden=True)),
        ("focus, paused or ghost mode (frozen)", "snow frozen",
         lambda: ov.set_state(active=True, frozen=True)),
        ("snow off (the date, the setting, off for today)", "snow hidden",
         lambda: ov.set_state(active=False)),
):
    start()
    hw = check_over_hwnds()
    act()
    check_over(label, hw, reason)

start()
hw = check_over_hwnds()
STATE["fullscreen"] = True
ov._tick_stack()
check_over("a fullscreen app", hw, "fullscreen")
STATE["fullscreen"] = False

start()
hw = check_over_hwnds()
STATE["monitors"] = MONS[:-1] if len(MONS) > 1 else [SO.Monitor(
    (MON.rect[0], MON.rect[1], MON.rect[2] - 1, MON.rect[3]), MON.floor, MON.scale, MON.info)]
ov._tick_stack()
check_over("a monitor change", hw, "monitors changed")
STATE["monitors"] = MONS

start()
hw = check_over_hwnds()
ov.shutdown()
check_over("quitting", hw, "snow off")
check("...and nothing of the snow is left", ov.field is None and not ov.flights
      and not ov.strips)

print("\n--- a fullscreen monitor at the start ---")
if len(MONS) > 1:
    # A pile on another monitor too: the one on the fullscreen monitor is hidden.
    OTHER = MONS[(MON_IDX + 1) % len(MONS)]
    o_left, o_top = OTHER.rect[0], OTHER.rect[1]
    FAKE2 = FAKE + 1
    RECT2 = (o_left + int(200 * OTHER.scale), o_top + int(260 * OTHER.scale),
             o_left + int(700 * OTHER.scale), o_top + int(600 * OTHER.scale))
    STACK.append((FAKE2, RECT2, False, False))
    ready()
    ov.field.add_cap(FAKE2, RECT2, ov._now_ms(), scale=OTHER.scale, fill=1.0)
    ov._tick_stack()
    STATE["fullscreen"] = True
    ov._tick_stack()
    check("set up: a pile still shows on the other monitor", ov.play_blocker() == "",
          ov.play_blocker())
    ov.start_play()
    pump(50)
    check("no play layer over the fullscreen app", MON_IDX not in play_layers()
          and play_layers(), str(list(play_layers())))
    ov._tick_stack()
    check("...and play does not end because of it", ov.playing)
    ov.end_play()
    STATE["fullscreen"] = False
    STACK.pop()
    ov._tick_stack()
else:
    ready()
    STATE["fullscreen"] = True
    ov._tick_stack()
    check("one monitor, fullscreen: it does not start",
          ov.play_blocker() == "fullscreen" and not ov.start_play())
    STATE["fullscreen"] = False
check("...over either way", not ov.playing and not play_layers())

# --- Nib and the wiring ----------------------------------------------------------------------

print("\n--- Nib: a snowball is startle, then football ---")
import world as W  # noqa: E402
from animator import SpriteSheet  # noqa: E402
from config import Config  # noqa: E402

SHEET = SpriteSheet(Path(SRC) / "assets" / "nib_sheet.png", Path(SRC) / "assets" / "frames.json",
                    2)


def fresh(tag):
    path = SCRATCH / f"snow_play_{tag}.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    cfg["last_seen_date"] = "2026-07-31"
    cfg["seasonal_enabled"] = False
    cfg["quirks_enabled"] = False
    b = Bh.Behavior(cfg, SHEET)
    b.set_strip(1392, 0, 2560)
    b.place_at(1200)
    b.set_world(W.WorldState(hour=14.0, weekday=2, date_key="2026-07-31", week_key="2026-W31",
                             part_of_day=W.PART_DAY, season=W.SEASON_WINTER,
                             cpu_percent=8.0, cpu_busy=False))
    b._enter(Bh.State.IDLE)
    return b


b = fresh("hit")
check("a snowball makes him jump", b.snow_landed(snowball=True)
      and b._antic == Bh.SNOW_HIT_ANIM, b._antic)
check("...then play football", [step[0] for step in b._antic_queue] == [Bh.SNOWBALL_ANIM],
      str(b._antic_queue))
c = fresh("plain")
c.snow_landed()
check("plain snow is just the jump", c._antic_queue == [], str(c._antic_queue))
check("football is not added to the ambient pool for this",
      sum(1 for a in Bh.ANTICS if a[0] == "football") == 1)

print("\n--- the wheel and main ---")
check("the wheel has the snowball icon", "snowball" in TW.ICONS)
import main as M  # noqa: E402

src_items = inspect.getsource(M.NibApp._wheel_items)
src_chosen = inspect.getsource(M.NibApp._on_tool_chosen)
check("the ring offers 'Play in the snow'", '"snow_play"' in src_items
      and "Play in the snow" in src_items)
check("...only while there is snow", "overlay.active" in src_items)
check("...and picking it plays", 'key == "snow_play"' in src_chosen
      and "self._on_snow_play()" in src_chosen)
src_play = inspect.getsource(M.NibApp._on_snow_play)
check("unavailable, it says why", "play_blocker()" in src_play and "self._tell(" in src_play)
check("picked again while playing, it stops", "end_play(" in src_play)
src_tick = inspect.getsource(M.NibApp._tick_snow)
check("a snowball hit reaches him as one", "snowball=" in src_tick and '"ball"' in src_tick)
check("'snow_play' has a wheel beat", "snow_play" in Bh.TOOL_BEATS)

print("\n" + "=" * 60)
print("FAILURES: " + ", ".join(fails) if fails else "snow play OK")
sys.exit(1 if fails else 0)
