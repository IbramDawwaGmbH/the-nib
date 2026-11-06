"""Throw physics: flick, walls, roll-to-rest, setting him down, and what it must not break."""

import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import scratch as _scratch  # noqa: E402

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

import behavior as B  # noqa: E402
import world as W  # noqa: E402
from animator import SpriteSheet  # noqa: E402
from behavior import Behavior, State  # noqa: E402
from config import Config  # noqa: E402
from memory import Memory  # noqa: E402
from needs import NEED_NUDGES  # noqa: E402

SCRATCH = _scratch()
BASELINE, X_MIN, X_MAX = 1392, 0, 2560
TODAY = "2026-07-31"
fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


def long_memory(cfg):
    """Thirty days seen and a few hundred pets: a bond of about 0.9, as in day_rate.py."""
    mem = Memory(None, cfg)
    for back in range(29, -1, -1):
        mem.first_sight(f"2026-07-{31 - back:02d}", 9.0)
    for event, n in (("pets", 900), ("prompts", 400), ("focus_done", 80)):
        mem.record(event, "2026-07-30", n=n)
    return mem


def fresh(tag, remember=False, **overrides):
    path = SCRATCH / f"th_{tag}.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    cfg["last_seen_date"] = TODAY
    for k, v in overrides.items():
        cfg[k] = v
    sheet = SpriteSheet(SRC / "assets" / "nib_sheet.png", SRC / "assets" / "frames.json", 2)
    b = Behavior(cfg, sheet, memory=long_memory(cfg) if remember else None)
    b.set_strip(BASELINE, X_MIN, X_MAX)
    b.place_at(1200)
    b.set_world(W.WorldState(hour=14.0, weekday=4, date_key="2026-07-31", week_key="2026-W31",
                             part_of_day=W.PART_DAY, season=W.SEASON_SPRING,
                             cpu_percent=8.0, cpu_busy=False))
    return cfg, b


TICK = 16.0


def drag(b, path, tail_still_ms=0.0):
    """Drive a drag along ``path`` (list of (x, y)), one tick per point, then optionally hold."""
    b.drag_start()
    for x, y in path:
        b.drag_to(x, y)
        b.update(TICK, None)
    held = 0.0
    while held < tail_still_ms:
        b.drag_to(path[-1][0], path[-1][1])
        b.update(TICK, None)
        held += TICK


def fly(b, max_ms=20000.0):
    """Run until he stops moving. Returns (ticks, path)."""
    path, elapsed = [], 0.0
    while (b._falling or b._rolling) and elapsed < max_ms:
        b.update(TICK, None)
        path.append((b._x, b._y))
        elapsed += TICK
    return elapsed, path


print("\n--- a flick throws him; holding still does not ---")
_cfg, b = fresh("flick")
lift_y = BASELINE - 400
drag(b, [(1200 + i * 30, lift_y) for i in range(10)])
b.drag_end()
check("a fast drag gives him horizontal speed", b._vx > 0.5, f"vx={b._vx:.3f} px/ms")
start = b._x
fly(b)
check("and he travels a long way", b._x - start > 200, f"{b._x - start:.0f}px")

_cfg, b = fresh("hold")
drag(b, [(1200 + i * 30, lift_y) for i in range(10)], tail_still_ms=200.0)
b.drag_end()
check("dragging then HOLDING STILL throws nothing", abs(b._vx) < 0.02, f"vx={b._vx:.4f}")
start = b._x
fly(b)
check("he drops on the spot", abs(b._x - start) < 8, f"moved {abs(b._x - start):.1f}px")

_cfg, b = fresh("nodrag")
b.drag_start()
b.drag_to(1200, lift_y)
b.update(TICK, None)
b.drag_end()
check("a click-and-release throws nothing", abs(b._vx) < 0.01 and abs(b._vy) < 0.01)

print("\n--- direction and cap ---")
_cfg, b = fresh("left")
drag(b, [(1200 - i * 30, lift_y) for i in range(10)])
b.drag_end()
check("flicking left sends him left", b._vx < -0.5, f"vx={b._vx:.3f}")

_cfg, b = fresh("cap")
drag(b, [(300 + i * 400, lift_y) for i in range(8)])   # absurdly fast
b.drag_end()
speed = math.hypot(b._vx, b._vy)
check("launch speed is capped", speed <= B.MAX_THROW_SPEED_PX_MS + 1e-6,
      f"{speed:.2f} <= {B.MAX_THROW_SPEED_PX_MS}")

print("\n--- he stays on the strip, at every tick rate ---")
for tick in (16.0, 50.0, 120.0, 250.0):
    _cfg, b = fresh(f"bounds{int(tick)}")
    drag(b, [(1200 + i * 100, lift_y) for i in range(9)])
    b.drag_end()
    worst_lo, worst_hi = b._x, b._x
    guard = 0
    while (b._falling or b._rolling) and guard < 4000:
        b.update(tick, None)
        worst_lo, worst_hi = min(worst_lo, b._x), max(worst_hi, b._x)
        guard += 1
    check(f"tick={tick:5.0f}ms never leaves the strip",
          worst_lo >= X_MIN - 0.001 and worst_hi <= b.x_max + 0.001,
          f"x range {worst_lo:.1f}..{worst_hi:.1f} vs {X_MIN}..{b.x_max}")

print("\n--- he bounces off the ends rather than sticking ---")
_cfg, b = fresh("wall", injury_enabled=False)
b.place_at(300)
drag(b, [(300 + i * 100, lift_y) for i in range(9)])   # fast, but never past the end
b.drag_end()
_elapsed, path = fly(b)
xs = [x for x, _y in path]
# The reflection happens inside a single tick, so the sampled position is already on the way back
# - he is never recorded exactly touching the end however fine the tick.
check("he reaches the far end", max(xs) >= b.x_max - 12, f"max x {max(xs):.1f} vs {b.x_max}")
check("and comes back off it", min(xs[len(xs) // 2:]) < max(xs) - 20,
      f"rebounded {max(xs) - min(xs[len(xs)//2:]):.0f}px")
check("he does not end up pinned to the edge", b._x < b.x_max - 5, f"rest x={b._x:.0f}")

print("\n--- roll to a stop ---")
_cfg, b = fresh("roll", injury_enabled=False)
drag(b, [(600 + i * 60, BASELINE - 120) for i in range(10)])
b.drag_end()
saw_roll = False
speeds, guard = [], 0
while (b._falling or b._rolling) and guard < 4000:
    b.update(TICK, None)
    if b._rolling:
        saw_roll = True
        speeds.append(abs(b._vx))
    guard += 1
check("he rolls along the ground after landing", saw_roll, f"{len(speeds)} rolling ticks")
check("rolling uses the tumble frames, not new art",
      B.TUMBLE_ANIM in str(b._sheet.has_state(B.TUMBLE_ANIM)) or b._sheet.has_state(B.TUMBLE_ANIM))
if speeds:
    check("the roll slows monotonically", all(a >= c - 1e-9 for a, c in zip(speeds, speeds[1:])))
check("he comes to a complete stop", not b._falling and not b._rolling
      and b._vx == 0.0 and b._vy == 0.0, f"vx={b._vx} vy={b._vy}")
check("and ends up standing on the strip", b.top == b.rest_top(), f"{b.top} vs {b.rest_top()}")

print("\n--- what the throw must not break ---")
_cfg, b = fresh("injury")
b.drag_start()
b.drag_to(1200, BASELINE - 500)
b.update(TICK, None)
b.drag_end()
fly(b)
check("a plain high drop still injures him", b.state is State.INJURED, b.state.value)

_cfg, b = fresh("shuffle")
b.drag_start()
b.drag_to(1210, BASELINE - 10)
b.update(TICK, None)
b.drag_end()
fly(b)
check("a small shuffle still does not", b.state is not State.INJURED, b.state.value)

_cfg, b = fresh("nap")
b._enter(State.SLEEP)
b._sleep_ms = 30000.0
drag(b, [(1200 + i * 40, lift_y) for i in range(10)])
b.drag_end()
fly(b)
check("thrown mid-nap he goes back to sleep", b.state is State.SLEEP, b.state.value)
check("...and is not hurt", b._injured_ms == 0.0)
check("...with the nap time preserved", 0 < b._sleep_ms <= 30000.0, f"{b._sleep_ms:.0f}ms")

# A hard throw should skid to a halt and THEN sit up dazed, not freeze the instant it lands.
# Gently enough sideways that he lands in open strip rather than slamming into the end - a wall
# impact makes him crumple, which is a different (and separately tested) outcome.
_cfg, b = fresh("hurtrolls", injury_enabled=True)
drag(b, [(400 + i * 16, BASELINE - 500) for i in range(10)])
b.drag_end()
rolled = False
guard = 0
while (b._falling or b._rolling) and guard < 4000:
    b.update(TICK, None)
    rolled = rolled or b._rolling
    guard += 1
check("a hard throw still rolls before it hurts", rolled)
check("...and ends up injured", b.state is State.INJURED, b.state.value)
check("...having stopped moving", b._vx == 0.0 and not b._rolling and not b._falling)

_cfg, b = fresh("straightdrop", injury_enabled=True)
b.drag_start(); b.drag_to(1200, BASELINE - 500); b.update(TICK, None); b.drag_end()
fly(b)
check("a straight drop is hurt the moment it lands", b.state is State.INJURED, b.state.value)

print("\n--- hitting the end of the strip hard ---")
_cfg, b = fresh("wallhurt", injury_enabled=True)
b.place_at(b.x_max - 700)
drag(b, [(b.x_max - 700 + i * 60, BASELINE - 500) for i in range(10)])
b.drag_end()
fly(b)
check("a hard sideways impact hurts him too", b.state is State.INJURED, b.state.value)
check("...and he crumples rather than bouncing off", b._vx == 0.0, f"vx={b._vx}")

_cfg, b = fresh("wallsoft", injury_enabled=True)
b.place_at(b.x_max - 300)
drag(b, [(b.x_max - 300 + i * 6, BASELINE - 30) for i in range(10)])
b.drag_end()
fly(b)
check("a gentle nudge into the end does not", b.state is not State.INJURED, b.state.value)

print("\n--- the tick rate while he is in your hand ---")
_cfg, b = fresh("tickrate")
b.drag_start()
check("dragging runs at the floor rate", b.next_tick_ms(16.0, 250.0) == 16.0,
      str(b.next_tick_ms(16.0, 250.0)))
b.drag_end()
check("and flight still runs fast", b.next_tick_ms(16.0, 250.0) <= B.MOVE_TICK_MS)

print("\n--- set down gently, he sparkles; once per 30 seconds of his own clock ---")
# Everything that could start an antic of its own is off, so whatever is playing after a landing
# is the landing's own answer. And that answer starts on the tick he settles - inside fly() - so
# none of this waits on a timer or a roll of the dice.
CALM = dict(wander_enabled=False, antic_chance=0.0, play_with_windows=False,
            quirks_enabled=False, seasonal_enabled=False)


def set_down(b, lift_px=30.0, dx=0.0, ticks=1):
    """Lift him ``lift_px`` above the strip, move ``dx`` px a tick for ``ticks``, let go, land.

    One tick and no ``dx`` is a release at zero speed: the first drag tick only seeds the
    velocity sampler, so there is nothing to average yet. Returns how long the landing took.
    """
    top = b.rest_top() - lift_px
    x0 = b._x
    drag(b, [(x0 + i * dx, top) for i in range(ticks)])
    b.drag_end()
    took, _path = fly(b)
    return took


def sparkling(b):
    """The landing's own 900ms sparkle, just begun - never an ambient one (1.2-1.9s long)."""
    return b.state is State.ANTIC and b._antic == B.GENTLE_ANIM and b._antic_ms <= B.GENTLE_MS


_cfg, b = fresh("gentle", **CALM)
fond = b.needs.affection
took = set_down(b)
check("set down gently he sparkles, the moment he lands", sparkling(b) and took < 2000.0,
      f"{b.state.value} {b._antic} after {took:.0f}ms")
check("...unhurt", b._injured_ms == 0.0 and b.state is not State.INJURED)
# At the starting point the taper is 1, so a pet would be worth its full +0.06 here.
pet = NEED_NUDGES["petted"][1]
check("...and a little fonder of you for it: about a third of a pet",
      0.25 * pet < b.needs.affection - fond < 0.35 * pet,
      f"{fond:.3f} -> {b.needs.affection:.3f}, a pet is +{pet}")
check("...which is neither a pet nor a throw in what he remembers",
      b.memory.day(TODAY).pets == 0 and b.memory.day(TODAY).throws == 0, str(b.memory.day(TODAY)))
first_at = b._clock_ms
for _ in range(40):                       # two seconds: the sparkle plays out
    b.update(50.0, None)
fond = b.needs.affection
set_down(b)
check("a second gentle set-down inside 30s gets no sparkle", b.state is State.IDLE, b.state.value)
check("...and no more affection (it only drifts back towards the middle)",
      b.needs.affection <= fond, f"{fond:.4f} -> {b.needs.affection:.4f}")
# Thirty seconds of HIS clock, which is over in a fraction of a second of the test's: if the
# cooldown were on the wall clock, this would fail.
while b._clock_ms - first_at < B.GENTLE_COOLDOWN_MS + 50.0:
    b.update(250.0, None)
set_down(b)
check("...and once 30s of his own clock have passed, it is a sparkle again", sparkling(b),
      f"{b.state.value} {b._antic}")

_cfg, b = fresh("gentle_high", need_affection=0.9, **CALM)
set_down(b, lift_px=300.0)
check("let go slowly from high up, he is hurt, not sparkling", b.state is State.INJURED,
      b.state.value)
# The same drop with injuries off. Nothing hurts him now, so the injury no longer returns first:
# only the set-down's own height clause stands between this and a sparkle.
_cfg, b = fresh("gentle_high_unhurt", injury_enabled=False, **CALM)
fond = b.needs.affection
set_down(b, lift_px=300.0)
check("...and with injuries off, a drop that high is still no gentle set-down",
      b.state is State.IDLE and b.needs.affection <= fond,
      f"{b.state.value} {b._antic}, {fond:.4f} -> {b.needs.affection:.4f}")
# Straight down, fast, from under the drop that hurts: nothing sideways, so not a throw, and low
# enough not to hurt - only the set-down's speed clause makes it no set-down.
_cfg, b = fresh("gentle_tossed", **CALM)
fond = b.needs.affection
low = b.rest_top() - 40
drag(b, [(b._x, low - 110 + i * 10) for i in range(12)])   # 10px a 16ms tick: 0.625px/ms down
speed, above = math.hypot(*b._throw_velocity()), b.rest_top() - b._y
b.drag_end()
fly(b)
check("let go fast straight down from low, he just gets up - too quick to be set down",
      speed > B.PERCH_MAX_RELEASE_SPEED_PX_MS and above < B.INJURY_FALL_PX
      and b.state is State.IDLE and b.needs.affection <= fond
      and b.memory.day(TODAY).throws == 0,
      f"{speed:.3f}px/ms from {above:.0f}px, {b.state.value} {b._antic}")

_cfg, b = fresh("gentle_hard", need_affection=0.9, **CALM)
drag(b, [(400 + i * 16, BASELINE - 500) for i in range(10)])
b.drag_end()
fly(b)
check("a hard throw still hurts, however fond he is", b.state is State.INJURED, b.state.value)

_cfg, b = fresh("gentle_perch", **CALM)
fond = b.needs.affection
b.drag_start()
b.drag_to(1200, 500)
b.update(TICK, None)
b.drag_end(perch_rect=(1000, 700, 1600, 1000))
check("set down on a window he stands on it, with no sparkle",
      b.state is State.PERCH and b.needs.affection == fond, b.state.value)
b.update(TICK, None)
set_down(b)
check("...and lifting him off and setting him down is a first gentle set-down, not a second",
      sparkling(b), f"{b.state.value} {b._antic}")

_cfg, b = fresh("gentle_nap", **CALM)
b._enter(State.SLEEP)
b._sleep_ms = 30000.0
fond = b.needs.affection
set_down(b)
check("set down gently mid-nap he just goes back to sleep", b.state is State.SLEEP, b.state.value)
check("...with nothing nudged", b.needs.affection <= fond, f"{fond} -> {b.needs.affection}")

_cfg, b = fresh("gentle_paused", **CALM)
b.drag_start()
b.drag_to(int(b._x), b.rest_top() - 30)
b.update(TICK, None)
b.drag_end()
b.paused = True
fly(b)
check("landing while paused, he lands - and starts nothing to sit frozen on its first frame",
      not b._falling and not b._rolling and b.state is State.IDLE, b.state.value)

_cfg, b = fresh("gentle_clicked", **CALM)
fond = b.needs.affection
drag(b, [(b._x, b.rest_top() - 50)])
b.drag_end()
b.update(TICK, None)
clicked = b.click()                       # mid-drop: the bubble opens round him as he falls
fly(b)
check("clicked mid-drop, the bubble stays open - the landing does not replace it",
      clicked and b.state is State.ASK and b.needs.affection <= fond,
      f"{clicked} {b.state.value}, {fond:.4f} -> {b.needs.affection:.4f}")

_cfg, b = fresh("gentle_hidden", **CALM)
fond = b.needs.affection
drag(b, [(b._x, b.rest_top() - 50)])
b.drag_end()
b.update(TICK, None)
b.set_hidden(True)                        # a fullscreen app, say, with him still in the air
for _ in range(40):                       # ten seconds of his clock, and he does not move
    b.update(250.0, None)
b.set_hidden(False)
fly(b)
check("hidden mid-drop, he finishes the fall when he is back - with no sparkle to answer it",
      not b._falling and b.state is State.IDLE and b.needs.affection <= fond,
      f"{b.state.value} {b._antic}, {fond:.4f} -> {b.needs.affection:.4f}")

print("\n--- thrown, and fond of you: \"again!\" ---")
_cfg, b = fresh("gentle_flick", **CALM)
b.drag_start()
top = b.rest_top() - 20
for i in range(12):                       # 7.2px a 16ms tick: 0.45px/ms sideways
    b.drag_to(1200 + i * 7.2, top)
    b.update(TICK, None)
speed = math.hypot(*b._throw_velocity())
b.drag_end()
fly(b)
check("a release slow enough to perch but over 0.4px/ms sideways is a throw, not a set-down",
      B.THROWN_ABOVE_PX_MS < speed <= B.PERCH_MAX_RELEASE_SPEED_PX_MS
      and b.memory.day(TODAY).throws == 1 and b.state is State.IDLE,
      f"{speed:.3f}px/ms, {b.state.value}, throws={b.memory.day(TODAY).throws}")

_cfg, b = fresh("play", need_affection=0.8, **CALM)
set_down(b, lift_px=20.0, dx=15.0, ticks=12)
check("thrown and landed unhurt, a fond mascot sparkles for another go", sparkling(b),
      f"{b.state.value} {b._antic}")
check("...and it is counted as one throw", b.memory.day(TODAY).throws == 1,
      str(b.memory.day(TODAY)))
for _ in range(40):
    b.update(50.0, None)
set_down(b, lift_px=20.0, dx=-15.0, ticks=12)
check("...every time - a throw is never on a cooldown", sparkling(b),
      f"{b.state.value} {b._antic}")

_cfg, b = fresh("play_meh", **CALM)
set_down(b, lift_px=20.0, dx=15.0, ticks=12)
check("at ordinary affection he just gets up", b.state is State.IDLE, b.state.value)

print("\n--- held by someone he knows, he squirms less ---")


def squirm(b, ms=3000.0):
    """The widest he wriggles in your hand, held still in mid-air for ``ms``."""
    b.drag_start()
    worst, held = 0.0, 0.0
    while held < ms:
        b.drag_to(1200, b.rest_top() - 200)
        b.update(TICK, None)
        worst = max(worst, abs(b._struggle_dx), abs(b._struggle_dy))
        held += TICK
    return worst


_cfg, stranger = fresh("squirm_stranger", **CALM)
check("a stranger squirms exactly as hard as he always has",
      stranger._struggle_amplitude_px() == B.STRUGGLE_AMPLITUDE_PX * stranger._sheet.scale,
      f"{stranger._struggle_amplitude_px()}")
_cfg, friend = fresh("squirm_friend", remember=True, **CALM)
bond = friend.memory.bond()
check("a month of days and pets is a bond near the top", bond > 0.85, f"{bond:.3f}")
s0, s1 = squirm(stranger), squirm(friend)
check("...and in his friend's hand he wriggles far less", s1 < 0.6 * s0,
      f"{s0:.2f}px vs {s1:.2f}px")
check("...by exactly the bond's share", abs(s1 / s0 - (1.0 - B.SQUIRM_BOND_CALM * bond)) < 1e-9,
      f"{s1 / s0:.4f}")
cfg_off, forgetful = fresh("squirm_off", remember=True, **CALM)
cfg_off["memory_enabled"] = False         # the month is still in there; he just does not use it
check("...but with memory off he squirms as hard as a stranger",
      forgetful._struggle_amplitude_px() == stranger._struggle_amplitude_px())

print("\n" + "=" * 58)
print("FAILURES: " + ", ".join(fails) if fails else "throw physics OK")
sys.exit(1 if fails else 0)
