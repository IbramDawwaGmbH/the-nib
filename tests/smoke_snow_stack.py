"""The snow and the window stack: which windows get a pile, what happens to it when the window
moves, is resized, minimised, maximised or closed, what hides it, and the Win32 pass that feeds it.

``SnowField.sync`` is pure, so almost all of this runs on synthetic stacks - lists of
``(hwnd, rect, zoomed, topmost)``, front to back, exactly what ``list_window_stack`` returns - with
an injected clock and a seeded rng. The Win32 pass itself is tested twice: its filters against a
fake ``user32`` (every case, deterministically), and live, only for what cannot race with other
apps (it runs, its rectangles are sane, and a window of this process is never in it).
"""

import ctypes
import os
import random
import statistics
import sys
import time
from ctypes import wintypes
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

import snow as S  # noqa: E402
import win_integration as win  # noqa: E402
from snow import SnowField  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


PRIMARY = (0, 0, 1920, 1080)
SIDE = (1920, -249, 4480, 1191)          # the owner's second monitor, at a negative origin
FLOORS = (1020, 1143)
FRAME = SnowField.frame_ms()
SYNC_MS = 500                            # the view polls the stack at 2 Hz


def field(seed=1, scales=None, max_caps=S.MAX_CAPS):
    f = SnowField(random.Random(seed), max_caps=max_caps)
    f.set_monitors([PRIMARY, SIDE], FLOORS, scales)
    return f


def win_(hwnd, rect, zoomed=False, topmost=False):
    return (hwnd, rect, zoomed, topmost)


def fly(f, t, limit_ms=20_000):
    end = t + limit_ms
    while t < end and f.airborne:
        t += FRAME
        f.step(t)
    return t


def ledger_ok(f):
    lhs = (f.mass_in_caps + f.mass_in_air + f.ledger["floor"] + f.ledger["nib"]
           + f.ledger["faded"] + f.ledger["vanished"])
    grown = f.ledger["grown"]
    return abs(lhs - grown) <= 1e-6 * max(1.0, grown)


def fill(cap, f, frac=1.0):
    """Grow a cap to ``frac`` of full at once, icicles too, booked in the ledger as growth."""
    before = cap.mass
    for i, h in enumerate(cap.target):
        cap.heights[i] = h * frac
    for ic in cap.icicles:
        ic.length = ic.max_len * frac
    f.ledger["grown"] += cap.mass - before


def grown_cap(f, hwnd, rect, t=0.0):
    """A window that has been there a while: its cap fully grown."""
    f.sync([win_(hwnd, rect)], t)
    cap = f.caps[hwnd]
    fill(cap, f)
    return cap


W = (300, 400, 1100, 900)                # 800 px wide, top edge at y=400

print("\n--- a new window gets a bare cap, and a pass that changes nothing says nothing ---")
f = field()
res = f.sync([win_(101, W)], 0.0)
cap = f.caps.get(101)
check("the window gets a cap", cap is not None and res.added == {101})
check("...that starts bare (it grows from here)", cap is not None and cap.mass == 0.0)
check("...reported as a strip to make: in caps and moved", res.caps == {101} and res.moved == {101})
check("...and nothing was put in the air", res.chunks == 0 and not f.airborne)
v = cap.version
res = f.sync([win_(101, W)], 500.0)
check("the same stack again: falsy", not res, str(res))
check("...and no version bumped", f.caps[101].version == v)
check("...and the pass left nothing for step() either", not f.step(500.0))
res = f.sync([win_(101, (300, 400, 1100, 950))], 1000.0)
check("only the bottom edge moved: falsy, the pile does not care",
      not res and f.caps[101].rect[3] == 950)
check("a failed pass (None) changes nothing", not f.sync(None, 1500.0) and 101 in f.caps)
check("...and keeps the last pass's fullscreen monitors", f.sync(None, 1500.0).fullscreen == set())
res = f.sync([win_(7, (400, 350, 600, 450)), win_(101, W)], 2000.0, own=[7])
check("an own handle is never a window, not even an occluder", 7 not in f.caps
      and not f.caps[101].occluders)

print("\n--- who gets a pile ---")
f = field()
stack = [
    win_(1, (500, 300, 640, 340), topmost=True),       # a tooltip-sized popup: too small
    win_(2, (200, 200, 300, 500)),                     # 100 px wide: too narrow
    win_(3, (-8, -8, 1928, 1028), zoomed=True),        # maximised
    win_(4, (600, -600, 1400, 100)),                   # its top edge is above every monitor
    win_(5, (300, 400, 1100, 900)),                    # an ordinary window
]
f.sync(stack, 0.0)
check("popups, narrow windows, maximised ones and tops off-screen get none; ordinary ones do",
      set(f.caps) == {5}, str(sorted(f.caps)))
g = SnowField(random.Random(1))
g.set_monitors([(0, 0, 1920, 1080), (0, -1080, 1920, 0)])   # a second monitor stacked above,
g.sync([win_(9, (0, 0, 1920, 1080))], 0.0)                  # so its pile would be on screen
check("a window covering a whole monitor gets none", not g.caps, str(sorted(g.caps)))
f = field()
f.sync([win_(8, (400, 1030, 1200, 1700))], 0.0)
check("nor one whose top edge is under the taskbar (below the floor)", not f.caps)
f = field()
f.sync([win_(1, (0, 0, 960, 1020)), win_(2, (960, 5, 1920, 1020)), win_(3, (300, 20, 900, 900))],
       0.0)
check("snapped to the top of the screen (or nearly): none, its pile would be off the screen",
      set(f.caps) == {3}, str(sorted(f.caps)))
g = SnowField(random.Random(1))
g.set_monitors([(0, 0, 1920, 1080), (0, -1080, 1920, 0)])   # a second monitor stacked above
g.sync([win_(1, (0, 0, 960, 1020))], 0.0)
check("...unless another monitor carries on above it, where the pile shows", 1 in g.caps)

print("\n--- what hides a pile: covered spans and the clip ---")
f = field()
menu = (500, 380, 700, 600)                            # a context menu over the middle of the top
res = f.sync([win_(1, menu, topmost=True), win_(101, W)], 0.0)
cap = f.caps[101]
check("a popup in front marks its span covered", cap.covered == [(500.0, 700.0)], str(cap.covered))
check("...and is an occluder, cut to the strip",
      cap.occluders == [S._intersect(menu, cap.extent())])
check("...reported as a clip change", 101 in res.clipped)
check("...a covered column is not visible, an open one is",
      not cap.visible(600) and cap.visible(400))
ex = cap.extent()
vis = S.clip(ex, cap.occluders)
check("the clip leaves the strip minus the popup",
      sum(S._area(r) for r in vis) == S._area(ex) - S._area(cap.occluders[0]), str(vis))
check("...and nothing it leaves overlaps the popup",
      all(S._intersect(r, cap.occluders[0]) is None for r in vis))
res = f.sync([win_(101, W)], 500.0)
check("the popup closes: uncovered again, as a clip change",
      f.caps[101].covered == [] and f.caps[101].occluders == [] and res.clipped == {101})

# Only grazing the top of the pile: the drawing is clipped, but brushing and landing still work.
f = field()
f.sync([win_(1, (500, 100, 700, 400 - int(S.CAP_PX))), win_(101, W)], 0.0)
cap = f.caps[101]
check("a window whose bottom only grazes the pile's top clips the drawing...", cap.occluders != [])
check("...but does not cover the pile's base", cap.covered == [], str(cap.covered))
f = field()
f.sync([win_(1, (500, 400 - int(S.COVER_BAND_PX) + 2, 700, 1000)), win_(101, W)], 0.0)
check("one reaching into the lower half of the pile covers it", f.caps[101].covered != [])
f = field()
f.sync([win_(1, (500, 401, 700, 1000)), win_(101, W)], 0.0)
check("one that starts below the window's top edge covers no snow",
      f.caps[101].covered == [], str(f.caps[101].covered))

f = field()
f.sync([win_(1, (200, 200, 1300, 950)), win_(101, W)], 0.0)
cap = f.caps[101]
check("a window entirely in front covers the whole pile", cap.hidden() and cap.covered
      and cap.covered[0][0] <= cap.x0 and cap.covered[0][1] >= cap.x1)
check("...and the clip leaves nothing to draw", S.clip(cap.extent(), cap.occluders) == [])

f = field()
f.sync([win_(1, (200, 200, 700, 950)), win_(2, (650, 300, 1000, 450)), win_(101, W)], 0.0)
cap = f.caps[101]
check("two overlapping windows in front: one merged covered span",
      cap.covered == [(cap.x0, 1000.0)], str(cap.covered))
g = field()
g.sync([win_(101, W), win_(1, (200, 200, 1300, 950))], 0.0)
check("windows *behind* a window never hide its pile",
      g.caps[101].covered == [] and g.caps[101].occluders == [])

# Brushing and landing go by covered.
f = field()
cap = grown_cap(f, 101, W)
f.sync([win_(1, (500, 300, 700, 600), topmost=True), win_(101, W)], 0.0)
before = list(cap.heights)
t = 0.0
for k in range(8):
    f.pointer(520 + k * 20, 400 - 10, t)
    t += 8.0
check("a brush that stays behind a popup knocks nothing off", cap.heights == before)

print("\n--- the clip function on its own ---")
check("no occluders: the rect itself", S.clip((0, 0, 10, 10), []) == [(0, 0, 10, 10)])
check("one beside it: untouched", S.clip((0, 0, 10, 10), [(20, 0, 30, 10)]) == [(0, 0, 10, 10)])
check("all of it: nothing", S.clip((0, 0, 10, 10), [(-5, -5, 15, 15)]) == [])
rng = random.Random(5)
ok = True
for _ in range(200):
    rect = (rng.randint(-40, 0), rng.randint(-40, 0), rng.randint(1, 40), rng.randint(1, 40))
    occ = []
    for _k in range(rng.randint(0, 5)):
        a, b = rng.randint(-50, 40), rng.randint(-50, 40)
        occ.append((a, b, a + rng.randint(1, 30), b + rng.randint(1, 30)))
    pieces = S.clip(rect, occ)
    for x in range(rect[0], rect[2]):
        for y in range(rect[1], rect[3]):
            hidden = any(o[0] <= x < o[2] and o[1] <= y < o[3] for o in occ)
            shown = sum(p[0] <= x < p[2] and p[1] <= y < p[3] for p in pieces)
            if shown != (0 if hidden else 1):
                ok = False
check("200 random cases: every pixel shown exactly once unless hidden, checked pixel by pixel", ok)

print("\n--- a window covering a monitor is that monitor going fullscreen ---")
f = field()
res = f.sync([win_(9, SIDE), win_(101, W)], 0.0)
check("an unmaximised window over the whole side monitor (negative origin) is fullscreen there",
      res.fullscreen == {1} and f.fullscreen == {1}, str(res.fullscreen))
res = f.sync([win_(9, SIDE), win_(101, W)], 500.0)
check("...which is state, not a change: the next pass is falsy but still says so",
      not res and res.fullscreen == {1})
cap = f.caps[101]
fill(cap, f)
res = f.sync([win_(9, (-8, -8, 1928, 1088), zoomed=True), win_(101, W)], 1000.0)
check("a maximised window is never fullscreen, even one spilling over the edges",
      res.fullscreen == set())
check("...but it hides the grown pile behind it: covered end to end, reported as a clip change",
      cap.hidden() and cap.covered == [(cap.x0, cap.x1)] and 101 in res.clipped,
      str(cap.covered))
check("...and the clip leaves nothing of it to draw", S.clip(cap.extent(), cap.occluders) == [])
check("...the pile itself stays put, nothing thrown", f.caps.get(101) is cap and res.chunks == 0)
res = f.sync([win_(9, (0, 0, 1920, 1000)), win_(101, W)], 1500.0)
check("one short of the bottom is not", res.fullscreen == set())
res = f.sync([win_(9, (1, 1, 1919, 1079)), win_(101, W)], 2000.0)
check("1 px in on every side still is (is_fullscreen_on_monitor's slack)", res.fullscreen == {0})
res = f.sync([win_(9, (2, 0, 1920, 1080)), win_(101, W)], 2500.0)
check("...2 px in is not", res.fullscreen == set())

print("\n--- moved: the pile falls where it was and starts over at the new spot ---")
f = field()
cap = grown_cap(f, 101, W)
full = cap.mass
v = cap.version
moved = (500, 300, 1300, 800)
res = f.sync([win_(101, moved)], 1000.0)
new = f.caps[101]
check("the old pile is in the air", res.chunks > 0 and abs(f.mass_in_air - full) < 1e-6,
      f"{res.chunks} chunks")
check("...a bare cap is at the new spot", new is not cap and new.rect == moved and new.mass == 0.0)
check("...reported as one to re-render and move, not removed",
      101 in res.caps and 101 in res.moved and 101 not in res.removed)
check("...with a later version than the old pile's", new.version > v, f"{v} -> {new.version}")
check("...and the ledger balances", ledger_ok(f))
fly(f, 1000.0)
check("none of the falling snow lands on its own window's new cap", f.caps[101].mass == 0.0)
check("...all of it reaches the floor, ledger still balanced",
      ledger_ok(f) and f.ledger["floor"] > 0)

f = field()
grown_cap(f, 101, W)
check("a top-edge resize is a move: the whole pile falls",
      f.sync([win_(101, (300, 350, 1100, 900))], 0.0).chunks > 0 and f.caps[101].mass == 0.0)

print("\n--- dragged across the screen: it throws its snow once, not on every pass ---")
f = field()
grown_cap(f, 101, W)
t, x, sprays = 0.0, 300, []
for k in range(120):                                   # a minute of dragging, polled at 2 Hz
    t += SYNC_MS
    x += 7
    sprays.append(f.sync([win_(101, (x, 400, x + 800, 900))], t).chunks)
    f.grow(t)                                          # the growth timer keeps running meanwhile
    f.step(t)
check("the first pass tears the pile off", sprays[0] > 0, str(sprays[:3]))
check("...and no later pass throws anything", not any(sprays[1:]), str(sum(sprays[1:])))
check("...the respawned pile never got to grow mid-drag", f.caps[101].mass == 0.0)
check("...ledger balanced", ledger_ok(f))
# It stops: after the hold, the pile at the new spot grows again.
t_stop = t
while t < t_stop + S.RESPAWN_HOLD_MS + 2 * S.GROWTH_STEP_MS:
    t += 1000.0
    f.grow(t)
    f.sync([win_(101, (x, 400, x + 800, 900))], t)
check("once the window is left alone, its pile grows again", f.caps[101].mass > 0.0)
held = S.RESPAWN_HOLD_MS
g = field()
grown_cap(g, 101, W)
g.sync([win_(101, (310, 400, 1110, 900))], 0.0)
first = next((tt for tt in range(0, 3 * held, 250) if g.grow(float(tt))), None)
check("...and not before the hold is up", first is not None and first >= held, str(first))

print("\n--- resized: only the cut-off span falls ---")
f = field()
cap = grown_cap(f, 101, W)
left_icicles = [(ic.x, ic.length) for ic in cap.icicles if ic.side == "left"]
before = {round(cap.column_center(i), 6): h for i, h in enumerate(cap.heights)}
total = cap.mass
v = cap.version
narrow = (300, 400, 800, 900)                         # the right edge comes in by 300 px
res = f.sync([win_(101, narrow)], 0.0)
new = f.caps[101]
check("the cut-off span falls", res.chunks > 0 and f.mass_in_air > 0)
check("...all of it from past the new right edge (and the new rounded end)",
      all(c.x > 800 - S.CORNER_PX - 4 for c in f.chunks), f"{min(c.x for c in f.chunks):.0f}")
kept = [i for i in range(new.n) if new.x0 + S.CORNER_PX < new.column_center(i) < 800 - S.CORNER_PX]
same = all(abs(new.heights[i] - before.get(round(new.column_center(i), 6), -1)) < 1e-9
           for i in kept)
check("...the surviving heights are kept exactly (same left edge: same column grid)", same)
check("...the left icicles are kept, the right ones fell",
      [(ic.x, ic.length) for ic in new.icicles if ic.side == "left"] == left_icicles
      and all(ic.length == 0.0 for ic in new.icicles if ic.side == "right")
      and any(c.kind == "shard" for c in f.chunks))
check("...the new rounded end is no cliff", new.heights[-1] < 0.6 * max(new.heights),
      f"{new.heights[-1]:.1f}")
check("...reported as re-render and move, version carried on",
      101 in res.caps and 101 in res.moved and new.version > v)
check("...nothing made or lost", ledger_ok(f) and abs(new.mass + f.mass_in_air - total)
      < 1e-6 * total)

f = field()
cap = grown_cap(f, 101, W)
right_icicles = [(ic.x, ic.length) for ic in cap.icicles if ic.side == "right"]
total = cap.mass
res = f.sync([win_(101, (551, 400, 1100, 900))], 0.0)   # the left edge in by 251: not a column
new = f.caps[101]
check("from the left: the chunks come off the left end only",
      res.chunks > 0 and all(c.x < 551 + S.CORNER_PX + 4 for c in f.chunks))
check("...the right icicles are kept",
      [(ic.x, ic.length) for ic in new.icicles if ic.side == "right"] == right_icicles)
mid_old = sum(h for i, h in enumerate(cap.heights) if 700 <= cap.column_center(i) < 1000)
mid_new = sum(h for i, h in enumerate(new.heights) if 700 <= new.column_center(i) < 1000)
check("...the middle keeps its snow, carried across the shifted column grid",
      abs(mid_new - mid_old) < 0.02 * mid_old, f"{mid_old:.1f} vs {mid_new:.1f}")
check("...and the ledger balances (the straddling sliver is 'vanished')",
      ledger_ok(f) and f.ledger["vanished"] < S.CAP_MAX_PX * S.COLUMN_PX,
      f"{f.ledger['vanished']:.3f}")

f = field()
cap = grown_cap(f, 101, W)
total = cap.mass
res = f.sync([win_(101, (300, 400, 1400, 900))], 0.0)   # wider to the right
new = f.caps[101]
check("made wider: no snow falls, only the icicles that are over the window now",
      all(c.kind == "shard" for c in f.chunks) and res.chunks == len(f.chunks))
check("...the new span starts bare, the old one keeps its snow",
      new.heights[-5] == 0.0 and abs(new.mass + f.mass_in_air - total) < 1e-6 * total)
check("...ledger balanced", ledger_ok(f))

# Piled above full height by throws, then made far narrower: the new rounded ends are lower than
# what stands there at *both* ends, so the far end sheds too, in a stroke of its own.
f = field()
cap = grown_cap(f, 101, W)
limit = S.CAP_MAX_PX * cap.scale
before = cap.mass
for i in range(cap.n):
    cap.heights[i] = limit
f.ledger["grown"] += cap.mass - before
res = f.sync([win_(101, (300, 400, 450, 900))], 0.0)   # the right edge in by 650 px
new = f.caps[101]
middle = 375.0
check("squeezed narrow under an over-full pile: chunks from both halves",
      any(c.x < middle for c in f.chunks) and any(c.x > middle for c in f.chunks),
      f"{res.chunks} chunks")
check("...two strokes, so the two ends never share a dirty box",
      len({c.group for c in f.chunks if c.kind == "lump"}) == 2)
check("...no column is left above its new limit",
      all(h <= min(limit, t * S.CAP_MAX_PX / S.CAP_PX) + 1e-9
          for h, t in zip(new.heights, new.target)))
check("...ledger balanced", ledger_ok(f))

print("\n--- minimised or closed: the whole pile falls; maximised: it falls and gets none ---")
for label, stack in (("closed (or minimised: gone from the stack)", []),
                     ("maximised", [win_(101, (-8, -8, 1928, 1028), zoomed=True)])):
    f = field()
    cap = grown_cap(f, 101, W)
    full = cap.mass
    res = f.sync(stack, 0.0)
    check(f"{label}: all of it in the air, the cap gone",
          101 not in f.caps and res.removed == {101} and abs(f.mass_in_air - full) < 1e-6
          and res.chunks > 0)
    check("...not in caps, nothing to re-render", 101 not in res.caps and 101 not in res.moved)
    fly(f, 0.0)
    check("...and it falls to the floor, ledger balanced", ledger_ok(f) and f.ledger["floor"] > 0)
f = field()
grown_cap(f, 101, W)
f.sync([win_(101, (0, 0, 1920, 1080))], 0.0)
check("grown over a whole monitor (a game going fullscreen): it goes quietly",
      101 not in f.caps and not f.airborne and ledger_ok(f))

print("\n--- when a pile goes quietly ---")
f = field()
f.sync([win_(101, W)], 0.0)
fill(f.caps[101], f, 0.05)
res = f.sync([], 0.0)
check("a small pile (a few growth steps) just goes", res.removed == {101} and res.chunks == 0
      and not f.airborne and ledger_ok(f))
f = field()
grown_cap(f, 101, W)
f.sync([win_(1, (200, 200, 1300, 950)), win_(101, W)], 0.0)
res = f.sync([win_(1, (200, 200, 1300, 950))], 500.0)
check("a pile entirely behind another window just goes", res.chunks == 0 and not f.airborne)
f = field()
grown_cap(f, 101, W)
f.sync([win_(1, (200, 200, 1300, 950)), win_(101, W)], 0.0)
res = f.sync([win_(1, (200, 200, 1300, 950)), win_(101, (400, 400, 1200, 900))], 500.0)
check("...and moved out from behind it too", res.chunks == 0 and f.caps[101].mass == 0.0)
f = field()
grown_cap(f, 101, W)
f.sync([win_(1, (200, 200, 1300, 950)), win_(101, W)], 0.0)
res = f.sync([win_(1, (200, 200, 1300, 950)), win_(101, (300, 400, 800, 900))], 500.0)
check("...and resized behind it: the cut-off end just goes", res.chunks == 0 and not f.airborne
      and f.caps[101].mass > 0.0 and ledger_ok(f))
f = field()
cap = grown_cap(f, 101, W)
total = cap.mass
# The game is *behind* the window, so the pile is not hidden: only the fullscreen rule quiets it.
res = f.sync([win_(101, (300, 400, 800, 900)), win_(9, PRIMARY)], 0.0)
new = f.caps[101]
check("resized on a monitor gone fullscreen: the cut-off end just goes",
      res.fullscreen == {0} and res.chunks == 0 and not f.airborne and new.covered == [])
check("...the rest is kept, the moved side's icicles are gone, the ledger balances",
      0.0 < new.mass < total and all(ic.length == 0.0 for ic in new.icicles if ic.side == "right")
      and ledger_ok(f))
FOUR = [win_(101 + k, (100 + k * 400, 300 + k * 50, 450 + k * 400, 900)) for k in range(4)]
f = field()
f.sync(FOUR, 0.0)
for c in f.caps.values():
    fill(c, f)
check("four grown piles", len(f.caps) == 4)
res = f.sync([], 0.0)
check("all four windows gone at once (a virtual-desktop switch): quietly, no budget sprayed",
      res.removed == {101, 102, 103, 104} and res.chunks == 0 and ledger_ok(f))
f = field()
f.sync(FOUR, 0.0)
for c in f.caps.values():
    fill(c, f)
res = f.sync(FOUR[2:], 0.0)
check("two of four closed is someone closing windows: they fall", res.chunks > 0)

print("\n--- at most max_caps piles: the ones that show first, a shown pile is never evicted ---")
f = field(max_caps=5)
small = [win_(10 + k, (100 + k * 300, 300, 300 + k * 300, 500)) for k in range(5)]
f.sync(small, 0.0)
check("five small windows, five caps", set(f.caps) == {10, 11, 12, 13, 14})
bigger = [win_(50, (0, 100, 1900, 1000))]
res = f.sync(small + bigger, 500.0)                    # behind them: every pile still shows
check("a bigger sixth window does not evict a pile that shows",
      set(f.caps) == {10, 11, 12, 13, 14} and not res.added and not res.removed)
res = f.sync(small[1:] + bigger, 1000.0)
check("...it gets the place that frees up", 50 in f.caps and 10 not in f.caps and 50 in res.added)
f = field(max_caps=2)
f.sync([win_(1, (0, 300, 200, 500)), win_(2, (300, 300, 1200, 900)),
        win_(3, (1300, 300, 1800, 700))], 0.0)
check("when many arrive at once, all showing, the largest win", set(f.caps) == {2, 3},
      str(sorted(f.caps)))

# Plan section 5: the largest *visible* windows. A maximised window hides thirty on the primary
# monitor; the one window on the side monitor must still get a pile.
MAXED = win_(900, (-8, -8, 1928, 1028), zoomed=True)
BEHIND = [win_(600 + k, (40 + k * 20, 100 + k * 10, 1200 + k * 20, 900)) for k in range(30)]
ASIDE = win_(700, (2300, 300, 2700, 600))                # smaller than any of the thirty
f = field()
f.sync([MAXED] + BEHIND + [ASIDE], 0.0)
check("thirty hidden windows and one small visible one arrive at once: the visible one gets a pile",
      700 in f.caps and len(f.caps) == S.MAX_CAPS, str(len(f.caps)))
f = field()
f.sync(BEHIND, 0.0)
for c in f.caps.values():
    fill(c, f)
check("thirty grown piles fill the pool", len(f.caps) == S.MAX_CAPS)
res = f.sync([MAXED] + BEHIND + [ASIDE], 500.0)
check("...a window maximised over them, and a new one showing: a hidden pile gives up its place",
      700 in f.caps and len(res.removed) == 1 and len(f.caps) == S.MAX_CAPS, str(res.removed))
check("...the smallest of them (all hidden alike)", res.removed == {629}, str(res.removed))
check("...quietly: it lost its place, nobody knocked it off (and it was hidden)",
      res.chunks == 0 and not f.airborne and ledger_ok(f))
res = f.sync([MAXED] + BEHIND + [ASIDE], 1000.0)
check("...and the next pass is falsy: nobody swaps back", not res and 700 in f.caps, str(res))
f = field(max_caps=2)
f.sync([win_(1, (300, 300, 1200, 900)), win_(2, (1300, 300, 1800, 700))], 0.0)
res = f.sync([win_(1, (300, 300, 1200, 900)), win_(2, (1300, 300, 1800, 700)),
              win_(3, (200, 200, 1250, 950))], 500.0)   # behind them, its own top showing
check("a pile that still shows is not evicted for a bigger window that shows",
      set(f.caps) == {1, 2} and not res.removed, str(sorted(f.caps)))

print("\n--- a negative monitor origin, and DPI ---")
f = field(scales=(1.0, 1.5))
up = (2200, -200, 3200, 400)                           # on the side monitor, above y=0
res = f.sync([win_(201, up)], 0.0)
cap = f.caps.get(201)
check("a window above y=0 on the side monitor gets a cap", cap is not None)
check("...at that monitor's scale",
      cap is not None and cap.scale == 1.5 and cap.col == 1.5 * S.COLUMN_PX)
f.sync([win_(202, (2200, -260, 3200, 400))], 500.0)
check("one whose top is above that monitor's (-249) gets none", 202 not in f.caps)
f.sync([win_(203, (2200, 100, 2370, 400))], 1000.0)
g = field(scales=(1.0, 1.5))
g.sync([win_(203, (200, 100, 370, 400))], 0.0)
check("170 px wide is enough at 96 DPI but not at 144 (minimum scaled)",
      203 not in f.caps and 203 in g.caps)
straddle = (1500, 500, 2500, 900)                       # mostly on the side monitor
f.sync([win_(204, straddle)], 1500.0)
check("a window across both monitors takes the scale of the one holding most of its top",
      f.caps[204].scale == 1.5)
f = field(scales=(1.0, 1.5))
grown_cap(f, 205, (600, 300, 1400, 900))
res = f.sync([win_(205, (2600, 300, 3400, 900))], 0.0)
check("dragged to the other monitor: a move (the scale changed), the pile falls",
      res.chunks > 0 and f.caps[205].scale == 1.5 and f.caps[205].mass == 0.0)
fly(f, 0.0)
check("...and the chunks fall, ledger balanced", ledger_ok(f) and not f.airborne)
f = field(scales=(1.0, 1.5))
grown_cap(f, 207, W)
f.set_monitors([PRIMARY, SIDE], FLOORS, (1.25, 1.5))     # the display scale changed under it
res = f.sync([win_(207, W)], 500.0)
check("same rectangle, new monitor DPI: a move, the pile falls and starts over at the new scale",
      res.chunks > 0 and f.caps[207].scale == 1.25 and f.caps[207].mass == 0.0
      and 207 in res.moved, f"{res.chunks} chunks")
f = field(scales=(1.0, 1.5))
res = f.sync([win_(9, (1920, -249, 4480, 1191)), win_(206, up)], 0.0)
check("the fullscreen test works at the negative origin too", res.fullscreen == {1})

print("\n--- cost: it runs twice a second ---")
rng = random.Random(7)
stack = []
for k in range(100):
    mon = PRIMARY if k % 2 else SIDE
    x = rng.randint(mon[0], mon[2] - 400)
    y = rng.randint(mon[1], mon[3] - 300)
    stack.append(win_(5000 + k, (x, y, x + rng.randint(150, 1200), y + rng.randint(100, 800)),
                      topmost=k < 5))
f = field()
f.sync(stack, 0.0)
check("100 windows: the pool caps it at 30 piles", len(f.caps) == S.MAX_CAPS)
times = []
for k in range(60):
    stack = stack[1:] + stack[:1] if k % 2 else stack   # restack every other pass
    t0 = time.perf_counter()
    f.sync(stack, 500.0 * (k + 1))
    times.append((time.perf_counter() - t0) * 1000.0)
med = statistics.median(times)
print(f"       sync, 100 windows / 30 caps: {med:.2f} ms median, {max(times):.2f} ms worst")
check("...a pass takes a few ms at most", med < 25.0, f"{med:.2f} ms")

print("\n--- the Win32 pass: its filters, against a fake user32 ---")


class FakeUser32:
    def __init__(self, order, visible=(), iconic=(), zoomed=(), fail=False):
        self.order, self.visible, self.iconic, self.zoomed = order, visible, iconic, zoomed
        self.fail = fail

    def EnumWindows(self, proc, _param):
        if self.fail:
            return False
        for h in self.order:
            if not proc(h, 0):
                break
        return True

    def IsWindowVisible(self, h):
        return h.value in self.visible

    def IsIconic(self, h):
        return h.value in self.iconic

    def IsZoomed(self, h):
        return h.value in self.zoomed


saved = {name: getattr(win, name) for name in
         ("_user32", "_class_name", "_is_cloaked", "_pid_of_window", "_window_rect",
          "_frame_bounds", "_is_topmost")}
try:
    order = [11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
    win._user32 = FakeUser32(order, visible={11, 12, 13, 14, 15, 16, 17, 19, 20},
                             iconic={13}, zoomed={17})
    win._class_name = lambda h: "Shell_TrayWnd" if h == 15 else "Some.App"
    win._is_cloaked = lambda h: h == 14
    win._pid_of_window = lambda h: os.getpid() if h == 16 else 4242
    outer = {h: (100 * h - 7, 50, 100 * h + 207, 457) for h in order}
    outer[19] = (300, 300, 300, 400)                      # empty, and no frame either
    win._window_rect = lambda h: outer.get(h)
    win._frame_bounds = lambda h: None if h in (19, 20) else (100 * h, 51, 100 * h + 200, 450)
    win._is_topmost = lambda h: h == 12
    got = win.list_window_stack()
    check("visible, not minimised, not cloaked, not the shell's, not ours, not empty",
          [e[0] for e in got] == [11, 12, 17, 20], str([e[0] for e in got]))
    check("...front to back, as EnumWindows walks",
          [e[0] for e in got] == sorted(e[0] for e in got))
    check("...top from GetWindowRect, sides and bottom from the visible frame",
          got[0][1] == (1100, 50, 1300, 450), str(got[0][1]))
    check("...the window rect throughout when DWM will not say", got[3][1] == outer[20])
    check("...zoomed and topmost passed on", got[2][2] is True and got[1][3] is True
          and got[0][2] is False and got[0][3] is False)
    win._user32 = FakeUser32(order, fail=True)
    check("a failed pass is None, not an empty desktop", win.list_window_stack() is None)
    win._user32 = FakeUser32([])
    check("an empty desktop is an empty list", win.list_window_stack() == [])
finally:
    for name, value in saved.items():
        setattr(win, name, value)

print("\n--- the Win32 pass, live ---")
win.enable_dpi_awareness()
user32 = ctypes.windll.user32
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE,
                                   wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DestroyWindow.argtypes = [wintypes.HWND]
WS_POPUP, WS_VISIBLE = 0x80000000, 0x10000000
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x00000080, 0x08000000
# A 2x2 px window of our own, visible, never activated, gone again at once.
mine = user32.CreateWindowExW(WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE, "STATIC", None,
                              WS_POPUP | WS_VISIBLE, 0, 0, 2, 2, None, None, None, None)
try:
    live = win.list_window_stack()
    check("it runs", live is not None)
    live = live or []
    check("...every entry is (hwnd, 4-int rect, bool, bool) with an area",
          all(isinstance(h, int) and len(r) == 4 and all(isinstance(v, int) for v in r)
              and r[2] > r[0] and r[3] > r[1] and isinstance(z, bool) and isinstance(tm, bool)
              for h, r, z, tm in live))
    check("...no window of this process is in it", all(win._pid_of_window(h) != os.getpid()
                                                        for h, _r, _z, _t in live))
    check("...not even one that is visible right now",
          bool(mine) and bool(user32.IsWindowVisible(mine))
          and int(mine) not in {h for h, _r, _z, _t in live})
    check("...and no hwnd twice", len({h for h, *_ in live}) == len(live))
    # How many piles the live desktop gets depends on what is open (none on a CI runner), so this
    # only checks the real stack goes through sync without error and throws nothing on a first
    # pass; the rules themselves are all checked on the synthetic stacks above.
    mons = win.list_monitors()
    f = field()
    f.set_monitors([m.rect for m in mons], [m.work_rect[3] for m in mons],
                   [win.get_dpi_for_monitor(m) / 96.0 for m in mons])
    res = f.sync(live, 0.0)
    check("the live stack goes through sync: piles only for windows in it, nothing thrown",
          set(f.caps) <= {h for h, *_ in live} and res.chunks == 0 and not f.airborne,
          f"{len(f.caps)} caps")
finally:
    if mine:
        user32.DestroyWindow(mine)

print("\n" + "=" * 60)
print("FAILURES: " + ", ".join(fails) if fails else "snow stack OK")
sys.exit(1 if fails else 0)
