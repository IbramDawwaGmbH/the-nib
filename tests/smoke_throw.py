"""Throw physics: flick velocity, walls, roll-to-rest, and the things it must not break."""

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

SCRATCH = _scratch()
BASELINE, X_MIN, X_MAX = 1392, 0, 2560
fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


def fresh(tag, **overrides):
    path = SCRATCH / f"th_{tag}.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    cfg["last_seen_date"] = "2026-07-31"
    for k, v in overrides.items():
        cfg[k] = v
    sheet = SpriteSheet(SRC / "assets" / "nib_sheet.png", SRC / "assets" / "frames.json", 2)
    b = Behavior(cfg, sheet)
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

print("\n" + "=" * 58)
print("FAILURES: " + ", ".join(fails) if fails else "throw physics OK")
sys.exit(1 if fails else 0)
