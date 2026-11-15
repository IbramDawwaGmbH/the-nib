"""The snow model: caps that grow and stop, brushing and flicking, chunks that fall and land, the
budget, the mass ledger, and what step() reports.

Qt-free, like the module it tests. No wall time anywhere: every call is handed its ``now_ms`` and
the field its own seeded rng, so twenty minutes of growth and a hundred thrown chunks take no time
and replay exactly.
"""

import math
import random
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

import snow as S  # noqa: E402
from snow import SnowField  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


PRIMARY = (0, 0, 1920, 1080)
SIDE = (1920, -249, 4480, 1191)          # the owner's second monitor, at a negative origin
FLOORS = (1040, 1151)                    # work-area bottoms: the taskbar's top edge
FRAME = SnowField.frame_ms()


def field(seed=1, budget=S.CHUNK_BUDGET):
    f = SnowField(random.Random(seed), budget=budget)
    f.set_monitors([PRIMARY, SIDE], FLOORS)
    return f


def swipe(f, x0, x1, y0, y1=None, speed=2.0, t0=0.0, every=16.0):
    """Pointer samples from (x0, y0) to (x1, y1) at ``speed`` px/ms, one every ``every`` ms.
    Returns the time of the last sample."""
    y1 = y0 if y1 is None else y1
    dist = math.hypot(x1 - x0, y1 - y0)
    steps = max(1, math.ceil(dist / (speed * every)))
    t = t0
    for k in range(steps + 1):
        u = k / steps
        f.pointer(x0 + (x1 - x0) * u, y0 + (y1 - y0) * u, t)
        t += every
    return t - every


def fly(f, t, limit_ms=20_000):
    """Step at the frame rate until nothing is airborne; returns (end time, [Changes])."""
    seen = []
    end = t + limit_ms
    while t < end:
        t += FRAME
        seen.append(f.step(t))
        if not f.airborne:
            break
    return t, seen


def ledger_ok(f):
    lhs = (f.mass_in_caps + f.mass_in_air + f.ledger["floor"] + f.ledger["nib"]
           + f.ledger["faded"] + f.ledger["vanished"])
    grown = f.ledger["grown"]
    return abs(lhs - grown) <= 1e-6 * max(1.0, grown), lhs, grown


def budget_ok(f):
    counts = {}
    for c in f.chunks:
        counts[c.monitor] = counts.get(c.monitor, 0) + 1
    return all(n <= f.budget for n in counts.values()), counts


print("\n--- nothing Qt in here ---")
check("snow.py imports no PySide6", "PySide6" not in (SRC / "snow.py").read_text(encoding="utf-8"))
check("...and none was loaded by importing it", "PySide6" not in sys.modules)

print("\n--- a cap's shape: overhang, rounded ends, lip, icicles ---")
f = field()
W = (300, 400, 1100, 900)                     # a window 800 px wide, top edge at y=400
cap = f.add_cap(101, W, 0.0, fill=1.0)
left, top, right, _ = W
check("it overhangs both sides by 10-20 px",
      all(10.0 <= o <= 20.0 for o in cap.overhang), f"{cap.overhang}")
check("columns are a few px wide (sub-block resolution)", cap.col == S.COLUMN_PX)
mid = max(cap.target)
check("full height in the middle is the plan's 24-30 px", 24.0 <= mid <= 30.0, f"{mid:.1f}")
check("the ends round off", cap.target[0] < 0.6 * mid and cap.target[-1] < 0.6 * mid,
      f"{cap.target[0]:.1f} .. {cap.target[-1]:.1f}")
caption = [i for i in range(cap.n) if right - S.CAPTION_ZONE_PX <= cap.column_center(i) < right]
over = [i for i in range(cap.n) if not left <= cap.column_center(i) < right]
check("no lip over the caption buttons", all(cap.lip(i) == 0.0 for i in caption), str(len(caption)))
check("...nor over the overhangs", all(cap.lip(i) == 0.0 for i in over))
check("...but a few px of it elsewhere", 0 < cap.lip(cap.n // 3) <= S.LIP_PX)
per_side = {s: [ic for ic in cap.icicles if ic.side == s] for s in ("left", "right")}
check("2-4 icicles per overhang", all(2 <= len(v) <= 4 for v in per_side.values()),
      str({k: len(v) for k, v in per_side.items()}))

def clear_of_frame(c):
    """Every icicle hangs wholly outside the window's edges, not just its centre."""
    lft, _t, rgt, _b = c.rect
    return all(ic.x + ic.width / 2 <= lft + 1e-9 if ic.side == "left"
               else ic.x - ic.width / 2 >= rgt - 1e-9 for ic in c.icicles)


check("icicles hang only outside the window's edges, all of each one",
      clear_of_frame(cap) and all(clear_of_frame(field().add_cap(h, W, 0.0, fill=1.0))
                                  for h in range(1, 300)))
ex = cap.extent()
check("the extent holds the tallest pile and the longest icicle",
      ex[1] <= top - S.CAP_MAX_PX and ex[3] >= top + max(ic.max_len for ic in cap.icicles)
      and ex[0] <= cap.x0 and ex[2] >= cap.x1, str(ex))
check("...and is the strip size the plan expects (about 90 px tall)", 60 <= ex[3] - ex[1] <= 100,
      str(ex[3] - ex[1]))
twin = field(seed=99).add_cap(101, W, 0.0, fill=1.0)
check("the same window gets the same cap whatever the field's rng",
      twin.target == cap.target and [(i.x, i.max_len) for i in twin.icicles]
      == [(i.x, i.max_len) for i in cap.icicles])
other = field().add_cap(202, W, 0.0, fill=1.0)
check("another window gets another one", other.target != cap.target)
big = field().add_cap(101, W, 0.0, scale=1.5, fill=1.0)
check("sizes scale with DPI", big.col == 1.5 * cap.col
      and big.overhang == tuple(1.5 * o for o in cap.overhang)
      and abs(max(big.target) / (1.5 * mid) - 1.0) < 0.05, f"{max(big.target):.1f}")

print("\n--- growth reaches the cap, slowly, and stops ---")
f = field()
cap = f.add_cap(101, W, 0.0)
check("a new cap starts bare", cap.mass == 0.0 and f.grow(0.0) == set())
steps, t, at10 = 0, 0.0, None
while t <= 90 * 60_000:
    t += 1000.0
    if f.grow(t):
        steps += 1
    if at10 is None and t >= 10 * 60_000:
        at10 = sum(cap.heights) / sum(cap.target)
    if cap.full and all(ic.length == ic.max_len for ic in cap.icicles):
        break
full_at = t
# Eased out (snow.GROWTH_STEPS): quick to show, slow to finish, so past half at ten minutes.
check("about two-thirds grown at ten minutes (about 20 to full)", 0.55 <= at10 <= 0.8,
      f"{at10:.2f}")


def time_to_full(period, jitter=0.0, bias=0.0):
    """Minutes until a fresh cap is full, with grow() called every ``period`` ms (+-jitter)."""
    g = field()
    c2 = g.add_cap(101, W, 0.0)
    jr = random.Random(9)
    tt = 0.0
    while tt <= 60 * 60_000:
        tt += period + bias + jr.uniform(-jitter, jitter)
        g.grow(tt)
        if c2.full:
            return tt / 60_000
    return None


for label, args in (("every second", (1000.0,)), ("every 10 s", (10_000.0,)),
                    ("every 30 s, a few ms either side", (30_000.0, 5.0)),
                    ("every 30 s, always 3 ms early", (30_000.0, 0.0, -3.0))):
    mins = time_to_full(*args)
    check(f"full in about 20 minutes of showing, with the timer {label}",
          mins is not None and 18 <= mins <= 25, f"{mins or 0:.1f} min")
check("never above its grown height", all(h <= tgt + 1e-9 for h, tgt in zip(cap.heights,
                                                                        cap.target)))
check("icicles grow to their full length too",
      all(ic.length == ic.max_len for ic in cap.icicles), f"by {full_at / 60_000:.0f} min")
quiet = [f.grow(t + k * S.GROWTH_STEP_MS) for k in range(1, 40)]
check("...and then growth stops: nothing reported", not any(quiet))
check("it fills in small steps, not a few big ones", steps >= S.GROWTH_STEPS * 0.8, str(steps))

f = field()
cap = f.add_cap(101, W, 0.0)
f.grow(S.GROWTH_STEP_MS + 1.0)
one = cap.mass
t = S.GROWTH_STEP_MS + 2 * 3_600_000.0             # two hours hidden, then one call
f.grow(t)
check("a long gap never catches up: one call is one step", cap.mass <= 2.4 * one,
      f"{cap.mass / one:.2f} steps")
for k in range(1, 121):                            # ...then two minutes of a 1 s timer
    f.grow(t + k * 1000.0)
check("...nor in the calls after it: back to one step per period",
      cap.mass <= (2 + 120_000 / S.GROWTH_STEP_MS + 1.5) * one, f"{cap.mass / one:.2f} steps")
before = cap.mass
f.grow(-86_400_000.0)                              # the clock jumped back a day
f.grow(-86_400_000.0 + S.GROWTH_STEP_MS + 1.0)
check("a clock that jumps back does not stall it", cap.mass > before)

print("\n--- a slow pointer never touches it ---")
f = field()
cap = f.add_cap(101, W, 0.0, fill=1.0)
full = cap.mass
f.step(0.0)
swipe(f, 200, 1200, top - 5, speed=0.3, t0=10.0)
check("a slow pass through the pile brushes nothing", cap.mass == full and not f.airborne)
check("...and step() has nothing to report", not f.step(20_000.0))
r = random.Random(5)
t = 30_000.0
for k in range(400):                                # a shaky hand resting in the pile
    f.pointer(600 + r.choice((-1, 0, 1)), top - 5 + r.choice((-1, 0, 1)), t)
    t += 1.0
check("jitter, sampled every millisecond, brushes nothing", cap.mass == full)
for k in range(4):                                  # after a pause: 5 px a ms, for 3 ms
    f.pointer(600 + 5 * k, top - 5, 40_000.0 + k)
check("a few samples a ms apart are too short a stretch to time a swipe by", cap.mass == full)
f.pointer(600, top - 5, 41_000.0)
f.pointer(603.5, top - 5, 41_004.0)                 # 0.9 px/ms, but only 3.5 px of it
check("...and a stretch that barely moves is not a swipe, however fast", cap.mass == full)
f.pointer(600, top - 5, 42_000.0)
f.pointer(600, top - 5, 42_020.0)
f.pointer(1500, top - 5, 42_010.0)                  # a late sample from before the last one
check("a sample out of order is ignored, not read as a leap", cap.mass == full)
f.pointer(320, top - 5, 50_000.0)
f.pointer(1080, top - 5, 50_400.0)                  # 400 ms later, across the window
check("a jump after a pause is not a swipe", cap.mass == full)
swipe(f, 200, 1200, top - 40, speed=3.0, t0=60_000.0)
check("a fast pass above the snow brushes nothing", cap.mass == full)
swipe(f, 330, 1070, top + 15, speed=3.0, t0=70_000.0)
check("a fast pass along the title bar brushes nothing", cap.mass == full)
swipe(f, 700, 700, top + 30, top - 60, speed=0.3, t0=80_000.0)
check("a slow upward pass throws nothing", cap.mass == full and not f.step(90_000.0))

print("\n--- a fast sideways pass knocks that span off, mass and all ---")
f = field()
cap = f.add_cap(101, W, 0.0, fill=1.0)
full = cap.mass
f.step(0.0)
end = swipe(f, 500, 800, top + 1, speed=2.0, t0=100.0)
gone = [i for i in range(cap.n) if 505 <= cap.column_center(i) <= 795]
kept = [i for i in range(cap.n) if cap.column_center(i) < 480 or cap.column_center(i) > 820]
check("the span it passed through is cleared", all(cap.heights[i] == 0.0 for i in gone))
check("the rest of the pile is untouched",
      all(cap.heights[i] == cap.target[i] for i in kept))
check("it is now in the air", f.airborne and len(f.chunks) > 3, str(len(f.chunks)))
check("mass conserved: what left the cap is what is airborne",
      abs((full - cap.mass) - f.mass_in_air) < 1e-6,
      f"{full - cap.mass:.1f} vs {f.mass_in_air:.1f}")
check("chunk sizes are the plan's 6-18 px",
      all(S.CHUNK_PX[0] <= c.size <= S.CHUNK_PX[1] for c in f.chunks))
check("they fly off the way the pointer went",
      sum(c.vx > 0 for c in f.chunks) > 0.8 * len(f.chunks))
ch = f.step(end + FRAME)
check("step() reports the cap and the gesture", 101 in ch.caps and ch.gestures
      and all(gs.kind == "brush" for gs in ch.gestures))
check("...and dirty boxes, on the primary monitor only", set(ch.boxes) == {0}, str(ch.boxes.keys()))
_t, seen = fly(f, end + FRAME)
landed = [ld for c in seen for ld in c.landed]
check("brushed snow falls down the front of its window, never back onto it",
      not any(ld.where == "cap" and ld.hwnd == 101 for ld in landed))
check("...and lands on the floor (the taskbar's edge)",
      landed and all(ld.where == "floor" and ld.y <= FLOORS[0] for ld in landed))
ok, lhs, grown = ledger_ok(f)
check("the ledger balances", ok, f"{lhs:.3f} vs {grown:.3f}")

f = field()
cap = f.add_cap(101, W, 0.0, fill=1.0)
swipe(f, 500, 800, top - 10, speed=2.0, t0=100.0)
i = cap.column_at(650)
check("a pass through the middle of the pile takes only what is above the pointer",
      0 < cap.heights[i] <= 10.0 and cap.heights[i] >= 10.0 - S.BRUSH_BAND_PX - 1e-9,
      f"{cap.heights[i]:.1f}")

f = field()
cap = f.add_cap(101, W, 0.0, fill=1.0)
ic = cap.icicles[0]
swipe(f, ic.x - 40, ic.x + 40, top + ic.length / 2, speed=2.0, t0=100.0)
check("a pass through an icicle snaps it off", ic.length == 0.0
      and any(c.kind == "shard" for c in f.chunks))

f = field()
cap = f.add_cap(101, W, 0.0, fill=1.0)
hanging = [ic for ic in cap.icicles if ic.side == "left"]
swipe(f, cap.x0 - 40, left + 20, top - 1, speed=2.0, t0=100.0)   # in the snow, above the icicles
check("brushing the overhang away lets its icicles fall",
      all(ic.length == 0.0 for ic in hanging)
      and sum(c.kind == "shard" for c in f.chunks) == len(hanging), f"{len(hanging)} icicles")

print("\n--- an upward flick throws it, and it comes back down ---")
f = field()
cap = f.add_cap(101, W, 0.0, fill=1.0)
full = cap.mass
end = swipe(f, 700, 705, top + 20, top - 60, speed=1.5, t0=100.0)
check("the scoop leaves the cap", cap.mass < full and f.airborne, f"{len(f.chunks)} chunks")
check("...as much as is in the air", abs((full - cap.mass) - f.mass_in_air) < 1e-6)
check("it goes up", all(c.vy < 0 for c in f.chunks))
check("at no more than the clamp", all(math.hypot(c.vx, c.vy) <= S.MAX_THROW_PX_S + 1e-6
                                       for c in f.chunks))
start_y = min(c.y for c in f.chunks)
scooped = cap.mass
low = {c.id: c.y for c in f.chunks}
t = end
for _ in range(600):
    t += FRAME
    f.step(t)
    for c in f.chunks:
        low[c.id] = min(low[c.id], c.y)
    if not f.airborne:
        break
check("it rose above where it started", min(low.values()) < start_y - 40,
      f"{start_y - min(low.values()):.0f} px up")
check("and came back down: nothing is left in the air", not f.airborne)
check("...some of it back onto the pile it came from", cap.mass > scooped + 1e-6,
      f"{cap.mass - scooped:.0f} of {full - scooped:.0f} px^2 came back")
ok, lhs, grown = ledger_ok(f)
check("the ledger balances", ok, f"{lhs:.3f} vs {grown:.3f}")

f = field()
cap = f.add_cap(101, W, 0.0, fill=1.0)
swipe(f, 700, 702, top + 30, top - 200, speed=8.0, t0=100.0)
check("a violent flick is clamped to the plan's ~650 px/s",
      f.chunks and max(math.hypot(c.vx, c.vy) for c in f.chunks) <= S.MAX_THROW_PX_S + 1e-6,
      f"{max((math.hypot(c.vx, c.vy) for c in f.chunks), default=0):.0f}")

print("\n--- landing: on another cap, on Nib, never on a covered span ---")
f = field()
upper = f.add_cap(101, (600, 300, 900, 700), 0.0, fill=1.0)
lower = f.add_cap(202, (200, 600, 1500, 1000), 0.0, fill=0.3)
before = lower.mass
t = swipe(f, 620, 880, 301, speed=1.0, t0=100.0)
_t, seen = fly(f, t)
on_lower = [ld for c in seen for ld in c.landed if ld.where == "cap" and ld.hwnd == 202]
check("brushed snow lands on the window below", on_lower and lower.mass > before,
      f"{len(on_lower)} chunks, +{lower.mass - before:.0f} px^2")
check("...which is reported as a changed cap", any(202 in c.caps for c in seen))
check("landed snow never piles past the cap's limit",
      all(h <= S.CAP_MAX_PX + 1e-9 for h in lower.heights))
ok, lhs, grown = ledger_ok(f)
check("the ledger balances", ok, f"{lhs:.3f} vs {grown:.3f}")

f = field()
cap = f.add_cap(101, (600, 300, 900, 700), 0.0, fill=1.0)
f.set_nib((640, 700, 900, 900))
t = swipe(f, 620, 880, 301, speed=1.0, t0=100.0)
_t, seen = fly(f, t)
on_nib = [ld for c in seen for ld in c.landed if ld.where == "nib"]
check("snow falling through Nib's rectangle lands on him", on_nib and f.ledger["nib"] > 0,
      f"{len(on_nib)} chunks")

f = field()
cap = f.add_cap(101, W, 0.0, fill=1.0)
cap.covered = [(600.0, 900.0)]
full = cap.mass
swipe(f, 650, 850, top + 1, speed=2.0, t0=100.0)
check("a covered span cannot be brushed", cap.mass == full and not f.airborne)

f = field()
f.add_cap(101, (600, 300, 900, 700), 0.0, fill=1.0)
lower = f.add_cap(202, (200, 600, 1500, 1000), 0.0, fill=0.3)
lower.covered = [(0.0, 2000.0)]
before = lower.mass
t = swipe(f, 620, 880, 301, speed=1.0, t0=100.0)
_t, seen = fly(f, t)
check("snow falls past a covered span instead of landing on it",
      lower.mass == before and f.ledger["floor"] > 0
      and not any(ld.where == "cap" for c in seen for ld in c.landed))

f = field()
f.add_cap(101, (600, 300, 900, 700), 0.0, fill=1.0)
lower = f.add_cap(202, (200, 600, 1500, 1000), 0.0, fill=1.0)
extra = sum(S.CAP_MAX_PX - h for h in lower.heights) * lower.col
lower.heights = [S.CAP_MAX_PX] * lower.n
f.ledger["grown"] += extra                       # piled to the limit by hand, so book it
t = swipe(f, 620, 880, 301, speed=1.0, t0=100.0)
_t, seen = fly(f, t)
check("a pile at its limit takes no more: the snow falls on past it",
      all(h == S.CAP_MAX_PX for h in lower.heights) and f.ledger["floor"] > 0
      and not any(ld.where == "cap" for c in seen for ld in c.landed))
ok, lhs, grown = ledger_ok(f)
check("...and the ledger balances", ok, f"{lhs:.3f} vs {grown:.3f}")

print("\n--- the pointer sampled at the real frame rates ---")
for every in (33.0, 50.0):
    f = field()
    cap = f.add_cap(101, W, 0.0, fill=1.0)
    full = cap.mass
    swipe(f, 500, 800, top + 1, speed=2.0, t0=100.0, every=every)
    gone = [i for i in range(cap.n) if 510 <= cap.column_center(i) <= 790]
    check(f"a brush sampled every {every:.0f} ms clears its span",
          all(cap.heights[i] == 0.0 for i in gone) and f.airborne)
    f = field()
    cap = f.add_cap(101, W, 0.0, fill=1.0)
    swipe(f, 700, 705, top + 20, top - 60, speed=1.5, t0=100.0, every=every)
    check(f"a flick sampled every {every:.0f} ms throws snow up",
          f.airborne and cap.mass < full and all(c.vy < 0 for c in f.chunks))
    f = field()
    cap = f.add_cap(101, W, 0.0, fill=1.0)
    swipe(f, 200, 1200, top - 5, speed=0.5, t0=100.0, every=every)
    check(f"a slow pass sampled every {every:.0f} ms still brushes nothing", cap.mass == full)

print("\n--- nothing stays up forever ---")
f = SnowField(random.Random(4))
f.set_monitors([(0, 0, 1920, 400_000)])        # a floor too far away to ever reach
f.add_cap(101, W, 0.0, fill=1.0)
made = f.drop_cap(101, 0.0)
t, faded_at = 0.0, []
while f.airborne and t < 2 * S.MAX_AIR_MS:
    t += FRAME
    ch = f.step(t)
    if ch.faded:
        faded_at.append((t, ch.faded))
check("chunks still airborne after the limit all fade, in one step",
      not f.airborne and len(faded_at) == 1 and faded_at[0][1] == made
      and S.MAX_AIR_MS < faded_at[0][0] <= S.MAX_AIR_MS + FRAME, str(faded_at))
ok, lhs, grown = ledger_ok(f)
check("...and the ledger has them", ok and f.ledger["faded"] > 0)

print("\n--- a window that goes takes its snow with it ---")
f = field()
cap = f.add_cap(101, W, 0.0, fill=1.0)
full = cap.mass
made = f.drop_cap(101, 0.0)
check("dropping a cap puts all of it in the air", 101 not in f.caps
      and abs(f.mass_in_air - full) < 1e-6 and made == len(f.chunks), f"{made} chunks")
check("...a release is capped, so a wide cap makes bigger chunks, not hundreds",
      made <= S.MAX_PER_RELEASE + 8, str(made))
check("...though never bigger than the plan's 18 px drawn (their mass is what grows)",
      all(S.CHUNK_PX[0] <= c.size <= S.CHUNK_PX[1] for c in f.chunks)
      and max(c.mass for c in f.chunks) > math.pi * (S.CHUNK_PX[1] / 2) ** 2,
      f"{max(c.size for c in f.chunks):.1f} px")
check("...and its icicles fall as shards", any(c.kind == "shard" for c in f.chunks))
f.step(0.0)
_t, seen = fly(f, 0.0)
check("...and it all falls to the floor", not f.airborne and f.ledger["floor"] > 0)
f = field()
f.add_cap(101, W, 0.0, fill=1.0)
f.remove_cap(101)
ok, lhs, grown = ledger_ok(f)
check("removing a cap outright is in the ledger as vanished", ok and f.ledger["vanished"] > 0)
ch = f.step(0.0)
check("...and step() reports it removed, never as a cap to re-render",
      ch.removed == {101} and not ch.caps and not ch.moved, f"{ch.caps} {ch.removed}")

print("\n--- the budget: at most 100 per monitor, the oldest fade first ---")
f = field(seed=3)
for k in range(8):
    f.add_cap(1000 + k, (100 + k * 220, 300 + (k % 3) * 120, 300 + k * 220, 900), 0.0, fill=1.0)
t, worst, faded = 0.0, 0, 0
f.step(t)
for k in range(8):
    made = f.drop_cap(1000 + k, t)
    for _ in range(3):
        t += FRAME
        ch = f.step(t)
        faded += ch.faded
        ok, counts = budget_ok(f)
        worst = max([worst] + list(counts.values()))
check("never more than the budget on a monitor", worst <= S.CHUNK_BUDGET, str(worst))
check("...and the budget was actually reached", faded > 0, f"{faded} faded")
f2 = field(seed=3, budget=10)
f2.add_cap(1, W, 0.0, fill=1.0)
f2.drop_cap(1, 0.0)
alive = sorted(c.id for c in f2.chunks)
check("a smaller budget holds too", len(alive) == 10, str(len(alive)))
check("...and what survives is the newest", alive == list(range(f2._next_id - 10, f2._next_id)),
      str(alive))
ok, lhs, grown = ledger_ok(f2)
check("faded snow is in the ledger", ok and f2.ledger["faded"] > 0)


def covers(box, r):
    return box[0] <= r[0] and box[1] <= r[1] and box[2] >= r[2] and box[3] >= r[3]


f = field(seed=5, budget=10)
f.add_cap(1, (100, 300, 500, 900), 0.0, fill=1.0)
f.drop_cap(1, 0.0)
t = 0.0
for _ in range(2):
    t += FRAME
    f.step(t)
drawn = {c.id: (c.monitor, c.rect) for c in f.chunks}
f.add_cap(2, (1400, 300, 1800, 900), t, fill=1.0)
f.drop_cap(2, t)                                    # far away, and over the budget at once
alive = {c.id for c in f.chunks}
gone = [mr for cid, mr in drawn.items() if cid not in alive]
ch = f.step(t + FRAME)
check("chunks already drawn that the budget fades as new snow flies are erased",
      gone and all(any(covers(bx, r) for bx in ch.boxes.get(m, [])) for m, r in gone),
      f"{len(gone)} faded")

print("\n--- step() reports nothing when nothing moves ---")
f = field()
f.add_cap(101, W, 0.0, fill=1.0)
f.step(0.0)
check("caps at rest: nothing", not f.step(1000.0) and not f.step(2000.0))
check("...and no timer wanted", not f.airborne)
check("the frame rate is 30 FPS, 20 on battery",
      SnowField.frame_ms() == 33 and SnowField.frame_ms(True) == 50)
t = swipe(f, 500, 800, top + 1, speed=2.0, t0=3000.0)
t, seen = fly(f, t)
last = seen[-1]
check("the step that lands the last chunk still reports its box, to erase it",
      bool(last) and last.boxes, str(last.boxes))
check("...and after that, nothing", not f.step(t + FRAME) and not f.step(t + 10 * FRAME))

print("\n--- Cap.version: up on every change of shape, never at rest ---")
f = field()
cap = f.add_cap(101, W, 0.0)
v = cap.version
f.grow(S.GROWTH_STEP_MS + 1.0)
check("a growth step bumps it (the view's pixmap cache key)", cap.version > v)
f = field()
cap = f.add_cap(101, W, 0.0, fill=1.0)
lower = f.add_cap(202, (200, 700, 1500, 1000), 0.0, fill=0.3)
still = f.add_cap(303, (2200, 100, 3000, 500), 0.0, fill=1.0)
v, v_still = cap.version, still.version
for k in range(4):
    f.step(k * 1000.0)
    f.grow(k * S.GROWTH_STEP_MS)
check("a full cap at rest keeps it, however often grow() and step() run", cap.version == v)
t = swipe(f, 500, 800, top + 1, speed=2.0, t0=200_000.0)
check("a brush bumps it", cap.version > v)
v = cap.version
t = swipe(f, 950, 953, top + 20, top - 60, speed=1.5, t0=t + 500.0)
check("a flick bumps it", cap.version > v)
v, heights = cap.version, list(cap.heights)
ic = max(cap.icicles, key=lambda i: i.length)
t = swipe(f, ic.x - 40, ic.x + 40, top + ic.length, speed=2.0, t0=t + 500.0)
check("an icicle snapping off bumps it", cap.version > v and cap.heights == heights
      and ic.length == 0.0)
v_lower = lower.version
fly(f, t)
check("snow landing on a cap bumps that cap's", lower.version > v_lower)
check("...and a cap nothing happened to keeps its own", still.version == v_still)

print("\n--- dirty boxes: every chunk inside one, per monitor, per stroke ---")
f = field(seed=7)
a = f.add_cap(1, (100, 300, 500, 900), 0.0, fill=1.0)
b = f.add_cap(2, (1400, 300, 1800, 900), 0.0, fill=1.0)
t = swipe(f, 150, 450, 301, speed=2.0, t0=0.0)
t = swipe(f, 1450, 1750, 301, speed=2.0, t0=t + 200.0)
inside, erased, boxes_seen, far_apart = True, True, 0, False
widest, spanned = 0, False
for _ in range(200):
    t += FRAME
    before = [c.rect for c in f.chunks if c.rect is not None]
    ch = f.step(t)
    every = [bx for rects in ch.boxes.values() for bx in rects]
    for r in before:
        if not any(bx[0] <= r[0] and bx[1] <= r[1] and bx[2] >= r[2] and bx[3] >= r[3]
                   for bx in every):
            erased = False
    for c in f.chunks:
        r = S._chunk_rect(c)
        mine = ch.boxes.get(c.monitor, [])
        if not any(bx[0] <= r[0] and bx[1] <= r[1] and bx[2] >= r[2] and bx[3] >= r[3]
                   for bx in mine):
            inside = False
    boxes_seen = max(boxes_seen, len(ch.boxes.get(0, [])))
    if len(ch.boxes.get(0, [])) >= 2:
        far_apart = True
    widest = max([widest] + [bx[2] - bx[0] for bx in ch.boxes.get(0, [])])
    if any(bx[0] < 500 and bx[2] > 1400 for bx in ch.boxes.get(0, [])):
        spanned = True
    if not f.airborne:
        break
check("every airborne chunk is inside a dirty box of its monitor", inside)
check("...and where each one was last frame is too, so it is erased", erased)
check("two bursts at opposite ends stay two boxes, not one near-full-screen box",
      far_apart and not spanned, f"up to {boxes_seen} boxes, the widest {widest} px")

print("\n--- a monitor at a negative origin ---")
f = field()
cap = f.add_cap(303, (2200, -100, 3000, 500), 0.0, fill=1.0)
t = swipe(f, 2300, 2600, -99, speed=2.0, t0=0.0)
check("brushing works above y=0", f.airborne and all(c.monitor == 1 for c in f.chunks))
t, seen = fly(f, t)
lands = [ld for c in seen for ld in c.landed]
check("...and it lands on that monitor's floor",
      lands and all(ld.where == "floor" and FLOORS[1] - 20 <= ld.y <= FLOORS[1]
                    for ld in lands), str(sorted({round(ld.y) for ld in lands})))
check("...with its boxes on that monitor's layer", all(set(c.boxes) <= {1} for c in seen))

f = field()
f.add_cap(304, (3800, 200, 4400, 800), 0.0, fill=1.0)   # near the desktop's right-hand edge
t = swipe(f, 3900, 4400, 201, speed=2.0, t0=0.0)
t, seen = fly(f, t)
off = [ld for c in seen for ld in c.landed if ld.x >= SIDE[2]]
check("snow thrown off the side of the desktop is gone where it left, not on some floor",
      off and all(ld.where == "floor" for ld in off)
      and sum(ld.y < 900 for ld in off) > len(off) / 2,
      f"{sum(ld.y < 900 for ld in off)} of {len(off)} mid-air, above y=900")

print("\n--- a monitor stacked above ---")
LOWER, UPPER = (0, 0, 1920, 1080), (0, -1080, 1920, 0)
for floors, label, flick in (((1040, 0), "no taskbar", True),
                             ((1040, -40), "its taskbar at the bottom", False)):
    f = SnowField(random.Random(6))
    f.set_monitors([LOWER, UPPER], floors)
    f.add_cap(101, (300, 0, 1100, 800), 0.0, fill=1.0)    # its snow is on the upper monitor
    t = swipe(f, 500, 800, 1, speed=2.0, t0=100.0)
    if flick:
        t = swipe(f, 950, 953, 20, -60, speed=1.5, t0=t + 200.0)
    _t, seen = fly(f, t)
    lands = [ld for c in seen for ld in c.landed]
    check(f"under a monitor above ({label}), knocked-off snow falls down the window to the floor",
          lands and all(ld.where == "cap" or (ld.where == "floor" and ld.y > 1000) for ld in lands),
          str(sorted({round(ld.y) for ld in lands if ld.where == "floor"})))
    ok, lhs, grown = ledger_ok(f)
    check("...and the ledger balances", ok, f"{lhs:.3f} vs {grown:.3f}")

print("\n--- the small guards ---")
f = field()
f.add_cap(101, W, 0.0, fill=1.0)
f.drop_cap(101, 0.0)
f.step(0.0)
f.step(FRAME)
ys = {c.id: c.y for c in f.chunks}
f.step(FRAME + 5000.0)                              # the timer stalled for five seconds
fell = max((c.y - ys[c.id] for c in f.chunks), default=0.0)
check("a stalled timer does not teleport the snow: one step is at most MAX_STEP_MS",
      f.airborne and fell <= S.TERMINAL_PX_S * S.MAX_STEP_MS / 1000.0 + 1.0, f"{fell:.0f} px")
f = field()
f.add_cap(101, W, 0.0, fill=1.0)
f.set_nib((0, 950, 1920, 952))                      # a 2 px sliver, under snow at full speed
f.drop_cap(101, 0.0)
f.step(0.0)
fly(f, 0.0)
check("snow at terminal speed cannot step over a thin Nib between two frames",
      f.ledger["nib"] > 0 and f.ledger["floor"] == 0, f"{f.ledger['floor']:.0f} px^2 got past")

print("\n--- deterministic under a seed ---")


def scenario(seed):
    f = field(seed)
    f.add_cap(101, W, 0.0, fill=1.0)
    f.add_cap(202, (200, 700, 1500, 1000), 0.0, fill=0.5)
    f.set_nib((900, 900, 1000, 1040))
    trace = []
    t = swipe(f, 400, 900, top - 3, speed=2.5, t0=0.0)
    t = swipe(f, 1000, 1004, top + 10, top - 50, speed=1.5, t0=t + 5)
    for _ in range(200):
        t += FRAME
        ch = f.step(t)
        trace.append((tuple((c.id, round(c.x, 6), round(c.y, 6)) for c in f.chunks),
                      tuple(sorted(ch.caps)), tuple((ld.where, round(ld.mass, 6))
                                                   for ld in ch.landed)))
        if not f.airborne:
            break
    return trace


one, two, three = scenario(11), scenario(11), scenario(12)
check("the same seed replays exactly", one == two, f"{len(one)} frames")
check("another seed differs", one != three)

print("\n--- a long random afternoon: the ledger and the budget always hold ---")
f = field(seed=21, budget=60)
r = random.Random(21)
wins = [(1 + k, (r.randint(0, 1500), r.randint(100, 700))) for k in range(6)]
for h, (x, y) in wins:
    f.add_cap(h, (x, y, x + r.randint(200, 900), y + 300), 0.0, fill=r.random())
f.add_cap(77, (2400, 0, 3600, 600), 0.0, fill=1.0)
t, broke, over = 0.0, False, False
for k in range(400):
    t += r.choice((FRAME, FRAME, 500.0, 30_000.0))
    if r.random() < 0.08:
        f.grow(t)
    if r.random() < 0.15:
        h, (x, y) = r.choice(wins)
        if h in f.caps:
            t = swipe(f, x - 50, x + r.randint(100, 600), y + r.uniform(-20, 2),
                      speed=r.uniform(0.2, 4.0), t0=t)
    if r.random() < 0.05:
        t = swipe(f, 2900, 2905, 20, -80, speed=2.0, t0=t)
    f.step(t)
    ok, _l, _g = ledger_ok(f)
    broke |= not ok
    over |= not budget_ok(f)[0]
check("mass is never made or lost by accident", not broke, str(f.ledger))
check("the budget is never exceeded", not over)

print("\n" + "=" * 60)
print("FAILURES: " + ", ".join(fails) if fails else "snow OK")
sys.exit(1 if fails else 0)
