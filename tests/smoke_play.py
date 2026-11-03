"""Peeking over the taskbar, and heaving at a window overhead.

The property that matters most here is the one that cannot be seen: neither of these ever calls
anything that moves another application's window. That is asserted directly at the end.
"""

import os
import random
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
BASELINE = 1392
fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


def fresh(tag, scale=2, **overrides):
    path = SCRATCH / f"pl_{tag}.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    # Pre-set the once-a-day latch: moments are checked BEFORE this kind of play, so a fresh
    # config would fire the first-sight-of-the-day stretch and swallow every check below it.
    cfg["last_seen_date"] = "2026-07-30"
    cfg["seasonal_enabled"] = False
    for k, v in overrides.items():
        cfg[k] = v
    sheet = SpriteSheet(SRC / "assets" / "nib_sheet.png", SRC / "assets" / "frames.json", scale)
    b = Behavior(cfg, sheet)
    b.set_strip(BASELINE, 0, 2560)
    b.place_at(1200)
    b.set_world(W.WorldState(hour=14.0, weekday=2, date_key="2026-07-30", week_key="2026-W31",
                             part_of_day=W.PART_DAY, season=W.SEASON_SUMMER,
                             cpu_percent=8.0, cpu_busy=False))
    return cfg, b


print("\n--- peeking over the taskbar ---")
for scale in (2, 3):
    cfg, b = fresh(f"peek{scale}", scale=scale)
    rest_top = b.rest_top()
    full_h = b.sprite_h
    check(f"scale={scale}: full height while standing", b.visible_height == full_h)
    check(f"scale={scale}: standing top is the rest position", b.top == rest_top)

    b._enter(State.PEEK)
    seen_h, seen_top = [], []
    total = B.PEEK_DOWN_MS + B.PEEK_HOLD_MS + B.PEEK_UP_MS
    for _ in range(int(total / 20) + 30):
        b.update(20.0, None)
        if b.state is State.PEEK:
            seen_h.append(b.visible_height)
            seen_top.append(b.top)
    lowest = min(seen_h)
    check(f"scale={scale}: he genuinely sinks", lowest < full_h * 0.75,
          f"shortest window {lowest}px of {full_h}px")
    # The window's bottom edge must stay welded to the taskbar throughout, or he would appear to
    # slide down the screen rather than sink behind the bar.
    bottoms = {t + h for t, h in zip(seen_top, seen_h)}
    check(f"scale={scale}: bottom edge stays on the taskbar", bottoms == {BASELINE},
          f"{sorted(bottoms)[:4]}")
    check(f"scale={scale}: never taller than the sprite", max(seen_h) <= full_h)
    check(f"scale={scale}: never zero height", lowest >= 1)
    check(f"scale={scale}: he comes back up",
          b.state is not State.PEEK and b.visible_height == full_h, b.state.value)
    check(f"scale={scale}: back to the exact resting height", b.top == rest_top)

# His eyes have to clear the taskbar or there is nothing to see.
cfg, b = fresh("peekeyes")
sheet = b.sheet
b._enter(State.PEEK)
for _ in range(int(B.PEEK_DOWN_MS / 20) + 4):
    b.update(20.0, None)
eyes_row = sheet.anchor("eyes", "idle", 0)[1] if hasattr(sheet, "anchor") else 26
eye_bottom = b.top + (eyes_row + 6) * sheet.scale
check("eyes still show above the taskbar at full sink", eye_bottom <= BASELINE - 2,
      f"eye bottom y={eye_bottom} vs taskbar {BASELINE}")
check("looking upward while peeking", b.animator.state == B.EYE_TRACK_STATE
      and b.animator.frame_index == B.PEEK_LOOK_FRAME, b.animator.state)

print("\n--- reaching for something overhead ---")
cfg, b = fresh("ledge")
check("wants to look, given the chance", b.wants_ledge)
check("reach is most of his height", 0 < b.ledge_reach_px < b.sprite_h,
      f"{b.ledge_reach_px}px of {b.sprite_h}px")

b.set_ledge((BASELINE - b.sprite_h - 40, 200, 600))
b._choose_next_action(None)
check("sets off toward it", b.state is State.WALK, b.state.value)
check("with the strain queued for arrival", b._pending_action == B.LEDGE_ANIM)
for _ in range(4000):
    b.update(20.0, None)
    if b._antic == B.LEDGE_ANIM:
        break
check("strains at it on arrival", b._antic == B.LEDGE_ANIM, str(b._antic))
under = 200 + (600 - 200) / 2 - b.sprite_w / 2
check("standing under the middle of it", abs(b._x - under) < 30, f"x={b._x:.0f} vs {under:.0f}")
check("will not go looking again for a while", not b.wants_ledge)

# Already underneath it: no pointless walk first.
cfg, b = fresh("ledge2")
b.place_at(300)
b.set_ledge((BASELINE - b.sprite_h - 30, 280, 420))
b._choose_next_action(None)
check("strains straight away when already under it", b._antic == B.LEDGE_ANIM, str(b._antic))

# A queued action must not survive an interruption.
cfg, b = fresh("ledge3")
b.set_ledge((BASELINE - b.sprite_h - 40, 200, 600))
b._choose_next_action(None)
check("queued before the interruption", b._pending_action == B.LEDGE_ANIM)
b.drag_start()
b.drag_end()
check("dragging him drops the queued action", b._pending_action is None)

cfg, b = fresh("ledge4", play_with_windows=False)
check("play_with_windows=false stops him looking", not b.wants_ledge)
b.set_ledge((BASELINE - b.sprite_h - 40, 200, 600))
random.seed(4)
states = set()
for _ in range(60):
    b._reaction_cooldown_ms = 0
    b._peek_cooldown_ms = 0
    b._choose_next_action(None)
    states.add(b.state)
check("...and stops him peeking", State.PEEK not in states, str(states))

print("\n--- the guarantee: nothing outside his own window is touched, with one named exception ---")
import inspect  # noqa: E402
import win_integration as win  # noqa: E402

sources = {name: inspect.getsource(mod)
           for name, mod in (("behavior.py", B), ("win_integration.py", win))}
MOVERS = ("SetWindowPos", "MoveWindow", "SetWindowPlacement", "DeferWindowPos", "ShowWindow")
for mover in MOVERS:
    check(f"behavior.py never calls {mover}", mover not in sources["behavior.py"])
check("find_ledge is read-only",
      not any(m in inspect.getsource(win.find_ledge) for m in MOVERS))
check("find_ledge never reads a window title",
      "GetWindowTextW" not in inspect.getsource(win.find_ledge))
check("behaviour is handed rectangles only",
      "tuple[int, int, int]" in inspect.getsource(B.Behavior.set_ledge))

# The same read-only guarantee for finding and following what he perches on - only pinning it
# (below) is the named exception, and it never does either of these things itself.
check("find_window_at is read-only",
      not any(m in inspect.getsource(win.find_window_at) for m in MOVERS))
check("find_window_at never reads a window title",
      "GetWindowTextW" not in inspect.getsource(win.find_window_at))
check("track_window is read-only",
      not any(m in inspect.getsource(win.track_window) for m in MOVERS))
check("perch_on is handed a rectangle only",
      "tuple[int, int, int, int]" in inspect.getsource(B.Behavior.perch_on))
check("update_perch is handed a rectangle (or None) only",
      "tuple[int, int, int, int] | None" in inspect.getsource(B.Behavior.update_perch))

# set_topmost is the one deliberate, narrow exception: pinning the single window he was dropped
# on. Checked here rather than just trusted, so the exception cannot quietly widen without a test
# noticing - it must stay the *only* place any mover appears in win_integration.py, it must never
# itself go looking for a window (an explicit hwnd in, nothing enumerated), and it must ask
# Windows not to move, resize or activate the window it touches.
occurrences = sum(sources["win_integration.py"].count(f"{mover}(") for mover in MOVERS)
check("SetWindowPos appears exactly once in win_integration.py - inside set_topmost itself",
      occurrences == 1, occurrences)
src_topmost = inspect.getsource(win.set_topmost)
check("set_topmost takes an explicit hwnd rather than searching for one",
      "def set_topmost(hwnd: int" in src_topmost)
check("...and never enumerates windows to find one",
      "EnumWindows" not in src_topmost)
check("...asks Windows not to move or resize the window",
      "_SWP_NOSIZE" in src_topmost and "_SWP_NOMOVE" in src_topmost)
check("...and not to steal focus either",
      "_SWP_NOACTIVATE" in src_topmost)

print("\n" + "=" * 60)
print("FAILURES: " + ", ".join(fails) if fails else "peek and strain OK")
sys.exit(1 if fails else 0)
