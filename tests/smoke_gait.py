"""The feet must still grip after the night-time pacing change.

The contract: he advances exactly ``walk_step_px * scale`` per ANIMATION frame, regardless of tick
rate or mood. Slowing him at night has to slow the animation and the travel together - scale only
one of them and his feet start sliding.

Sampling x at each frame flip has a boundary artifact: the whole tick that crosses the boundary
gets attributed to the frame that just ended, which at a 120ms tick is most of a stride and looks
like a large error when nothing is wrong. So this measures the invariant itself - total travel
divided by total animation frames advanced.
"""

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

import world as W  # noqa: E402
from animator import SpriteSheet  # noqa: E402
from behavior import Behavior, State  # noqa: E402
from config import Config  # noqa: E402

SCRATCH = _scratch()
fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


def world_at(hour):
    return W.WorldState(
        hour=hour, weekday=2, date_key="2026-07-30", week_key="2026-W31",
        part_of_day=W.part_of_day(hour), season=W.SEASON_SUMMER,
        cpu_percent=5.0, cpu_busy=False,
    )


def walker(scale, hour, tag, personality="balanced", spirits=0.0):
    path = SCRATCH / f"g_{tag}.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    cfg["walk_speed_px_s"] = 0  # derive from the sheet, which is what keeps the feet gripping
    cfg["personality"] = personality
    sheet = SpriteSheet(SRC / "assets" / "nib_sheet.png", SRC / "assets" / "frames.json", scale)
    b = Behavior(cfg, sheet)
    if spirits:
        # Straight into the scalar rather than through nudge(), so the test can sit at an extreme
        # the taper deliberately makes hard to reach.
        b.mood._spirits = spirits
    b.set_strip(1000, 0, 20000)
    b.place_at(100)
    b.set_world(world_at(hour))
    return b, sheet


def mean_stride(scale, tick_ms, hour, personality="balanced", spirits=0.0):
    """Mean pixels travelled per animation frame over a long walk.

    Frames are counted from the time actually handed to the animator, not by watching
    ``frame_index`` change: one 120ms tick crosses two 110ms walk frames, so an index watcher
    silently counts one of them and reports a ~5% error for a gait that is in fact exact. Summing
    the animation time and dividing by the frame duration has no such blind spot, and it is the
    invariant itself - _walk feeds the same scaled delta to both the animation and the distance.
    """
    b, _sheet = walker(scale, hour, f"{scale}_{int(tick_ms)}_{int(hour)}_{personality}_{spirits}",
                       personality, spirits)
    animator = b.animator
    consumed = []
    real_advance = animator.advance
    animator.advance = lambda dt, _sink=consumed, _f=real_advance: (_sink.append(dt), _f(dt))[1]

    b._begin_walk(9000)
    # AFTER _begin_walk: read while he is still idle and this is the 450ms idle frame, not the
    # 110ms walk frame, and every number below comes out 4.09x wrong.
    frame_ms = animator.frame_duration  # every walk frame is the same length
    start_x = b._x
    for _ in range(200000):
        if b.state is not State.WALK:
            break
        b.update(tick_ms, None)
    frames = sum(consumed) / frame_ms
    return abs(b._x - start_x) / max(1e-9, frames), frames


print("\n--- distance per animation frame must equal one stride ---")
for scale in (2, 3):
    expected = 3 * scale  # walk_step_px is 3
    for tick in (16.0, 50.0, 120.0):
        for hour, label in ((14.0, "day  "), (2.0, "night")):
            mean, n = mean_stride(scale, tick, hour)
            # No tolerance worth the name: both sides come from the same scaled delta, so this
            # is float noise or it is a bug.
            check(
                f"scale={scale} tick={tick:5.0f}ms {label}",
                abs(mean - expected) <= 1e-9,
                f"{mean:.9f}px vs {expected}px stride over {n:.1f} frames",
            )

# The pace multiplier is no longer just the clock: the personality preset and the persistent mood
# multiply into it too, and _speed_multiplier feeds both the distance AND the animation. So the
# invariant has to be re-proved at the extremes those two can now reach, not only at 1.0 - and
# through the max(0.05, ...) clamp at the slow end.
print("\n--- ...including under every new pace multiplier ---")
CASES = (
    ("hyper, midday",           14.0, "hyper",    0.0,  "1.18x"),
    ("chill, midday",           14.0, "chill",    0.0,  "0.85x"),
    ("delighted, midday",       14.0, "balanced", 1.0,  "1.08x"),
    ("fed up, midday",          14.0, "balanced", -1.0, "0.90x"),
    # The slowest he can be: chill 0.85 x night 0.7 x thoroughly fed up 0.9 = 0.535
    ("chill + night + fed up",   2.0, "chill",    -1.0, "0.535x"),
    # And the briskest: hyper 1.18 x day 1.0 x delighted 1.08 = 1.274
    ("hyper + delighted",       14.0, "hyper",    1.0,  "1.274x"),
)
for label, hour, personality, spirits, pace in CASES:
    for tick in (16.0, 120.0):
        mean, n = mean_stride(2, tick, hour, personality, spirits)
        check(f"{label:23s} tick={tick:5.0f}ms (pace ~{pace})",
              abs(mean - 6) <= 1e-9, f"{mean:.9f}px vs 6px over {n:.1f} frames")

print("\n--- and he really does travel less at night ---")


def distance(hour):
    b, _ = walker(2, hour, f"d{int(hour)}")
    b._begin_walk(9000)
    start = b._x
    for _ in range(500):
        b.update(20.0, None)
    return b._x - start


day, night = distance(14.0), distance(2.0)
check("night is ~0.7x day", 0.6 <= night / day <= 0.8,
      f"{night:.0f}px vs {day:.0f}px = {night / day:.2f}x")

print("\n" + "=" * 58)
print("FAILURES: " + ", ".join(fails) if fails else "gait invariant intact")
sys.exit(1 if fails else 0)
