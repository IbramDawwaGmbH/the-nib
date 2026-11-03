"""Focus sessions: the working hold, the pocket/away/focus priority order, auto-pause, and
completion vs. early cancellation."""

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
from config import Config, DEFAULTS  # noqa: E402

SCRATCH = _scratch()
fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


def fresh(tag, **overrides):
    path = SCRATCH / f"focus_{tag}.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    for k, v in overrides.items():
        cfg[k] = v
    sheet = SpriteSheet(SRC / "assets" / "nib_sheet.png", SRC / "assets" / "frames.json", 2)
    b = Behavior(cfg, sheet)
    b.set_strip(1000, 0, 4000)
    b.place_at(500)
    return cfg, b


print("\n--- starting and stopping ---")
_cfg, b = fresh("start", focus_duration_min=25)
check("starts inactive", not b.focus_active)
b.set_focus(True)
check("now active", b.focus_active)
check("remaining is the configured length",
      abs(b.focus_remaining_s - 25 * 60) < 0.5, b.focus_remaining_s)

check("starting again is a no-op - does not reset the clock", b.set_focus(True) or True)
b.update(5000.0, None)  # 5 simulated seconds
remaining_after_5s = b.focus_remaining_s
b.set_focus(True)
check("...confirmed: the clock kept running rather than resetting to 25:00",
      abs(b.focus_remaining_s - remaining_after_5s) < 0.5, b.focus_remaining_s)

b.set_focus(False)
check("stopping clears it", not b.focus_active and b.focus_remaining_s == 0.0)
check("stopping again (already inactive) is a no-op",
      b.set_focus(False) or not b.focus_active)

print("\n--- the working hold ---")
_cfg, b = fresh("hold")
b.set_focus(True)
check("holds him in the working pose", b._antic == B.FOCUS_HOLD_ANIM, b._antic)
check("held indefinitely, not for an antic's usual few seconds", b._antic_ms > 1e8, b._antic_ms)
check("wears the focus costume regardless of whatever was picked",
      b.persona_costume == B.FOCUS_COSTUME, b.persona_costume)

b._begin_antic("pet", 500.0)
check("...knocked into something else", b._antic == "pet")
b._choose_next_action(None)
check("the very next decision snaps him back to the hold",
      b._antic == B.FOCUS_HOLD_ANIM, b._antic)
check("...costume comes back with it", b.persona_costume == B.FOCUS_COSTUME)

print("\n--- press-up breaks, so a long hold is not one frozen frame ---")
_cfg, b = fresh("burst")
b.set_focus(True)
b._focus_burst_due_ms = 500.0  # force one due almost immediately
for _ in range(3):
    b.update(200.0, None)  # update() clamps dt_ms to 250ms per call - several small steps, not one big one
check("a due break switches him to the press-up loop",
      b._antic == B.FOCUS_BURST_ANIM, b._antic)
check("...still in the focus costume, not stripped for the break",
      b.persona_costume == B.FOCUS_COSTUME, b.persona_costume)
check("...still not moved and still not a new session key", b._x == 500 and b.focus_active)
check("a fresh break is scheduled rather than firing every tick",
      b._focus_burst_due_ms > 0, b._focus_burst_due_ms)

b._antic_ms = 10.0  # let the burst itself run out
b.update(20.0, None)
b._idle_timer_ms = 0.0
b._choose_next_action(None)
check("...and he settles back into the plain working pose afterwards",
      b._antic == B.FOCUS_HOLD_ANIM, b._antic)

_cfg, b = fresh("standing", wander_enabled=True)
b.set_focus(True)
entered_walk = False
for _ in range(200):
    b._choose_next_action(None)
    if b._state == State.WALK:
        entered_walk = True
check("never enters WALK during a session", not entered_walk)
check("position never moves", b._x == 500, b._x)

print("\n--- priority order: pocket > away > focus ---")
_cfg, b = fresh("priority1")
b.pocket_grab("something held")
b.set_focus(True)
b._choose_next_action(None)
check("pocket wins over focus when both are true", b._antic == B.POCKET_HOLD_ANIM, b._antic)

_cfg, b = fresh("priority2")
b.set_away(True)
b.set_focus(True)
b._choose_next_action(None)
check("away wins over focus when both are true - posture must match the tray tooltip",
      b._antic == B.AWAY_ANTIC, b._antic)

_cfg, b = fresh("priority3")
b.pocket_grab("something held")
b.set_away(True)
b.set_focus(True)
b._choose_next_action(None)
check("pocket still wins with all three true at once", b._antic == B.POCKET_HOLD_ANIM, b._antic)

print("\n--- countdown and auto-pause ---")
_cfg, b = fresh("countdown")
b.set_focus(True)
start = b.focus_remaining_s
b.update(2000.0, None)
check("ticking decrements the countdown", b.focus_remaining_s < start, b.focus_remaining_s)

b.set_away(True)
held = b.focus_remaining_s
for _ in range(20):
    b.update(200.0, None)
check("away freezes the countdown", b.focus_remaining_s == held, b.focus_remaining_s)

b.set_away(False)
b.update(1000.0, None)
check("coming back resumes it", b.focus_remaining_s < held, b.focus_remaining_s)

_cfg, b = fresh("hiddenpause")
b.set_focus(True)
start = b.focus_remaining_s
b._state = State.HIDDEN
b.update(1000.0, None)
check("still decrements while HIDDEN - a committed session is not \"behaviour\" to freeze",
      b.focus_remaining_s < start, b.focus_remaining_s)

_cfg, b = fresh("pausedpause")
b.set_focus(True)
start = b.focus_remaining_s
b.paused = True
b.update(1000.0, None)
check("still decrements while paused, for the same reason", b.focus_remaining_s < start,
      b.focus_remaining_s)

print("\n--- completion vs. cancellation ---")
_cfg, b = fresh("complete", focus_duration_min=1)
b.set_focus(True)
b._focus_remaining_ms = 50.0  # about to end
before_mood = b.mood.spirits
b.update(100.0, None)
check("session ends on its own", not b.focus_active)
check("take_focus_finished is true exactly once", b.take_focus_finished())
check("...then false", not b.take_focus_finished())
check("mood moved by the focus_done nudge", b.mood.spirits > before_mood, b.mood.spirits)
check("its own completion sound is queued - distinct from the CPU-driven cheer",
      "focus_done" in b.take_sounds())
check("the task-finished celebration plays", b._antic == B.TASK_FINISHED_ANIM, b._antic)
check("its cooldown is charged - a CPU-drop moments later must not echo it",
      b._task_finished_cooldown_ms > 0)

b.set_world(W.WorldState(
    hour=14.0, weekday=2, date_key="2026-07-31", week_key="2026-W31",
    part_of_day=W.PART_DAY, season=W.SEASON_SUMMER,
    cpu_percent=93.0, cpu_busy=True,
))
echoed = b._pick_moment()
check("...confirmed: a CPU-driven moment does not also fire task_finished right after",
      echoed is None or echoed[0] != B.TASK_FINISHED_ANIM, str(echoed))

_cfg, b = fresh("cancel")
b.set_focus(True)
before_mood = b.mood.spirits
b.set_focus(False)
check("cancelling early earns nothing: no completion flag",
      not b.take_focus_finished())
check("...no mood change", b.mood.spirits == before_mood)
check("...no completion sound queued", "focus_done" not in b.take_sounds())

print("\n--- completing while in an unsafe state earns nothing either ---")
for unsafe_state in (State.SLEEP, State.WAKE, State.DRAG, State.HIDDEN, State.ANGRY):
    _cfg, b = fresh(f"unsafe_{unsafe_state.value}", focus_duration_min=1)
    b.set_focus(True)
    b._focus_remaining_ms = 50.0
    b._state = unsafe_state
    before_mood = b.mood.spirits
    b.update(100.0, None)
    check(f"session still ends while {unsafe_state.value}", not b.focus_active)
    check("...take_focus_finished is still true - it did genuinely complete",
          b.take_focus_finished())
    check("...but mood does not move - he was not visibly celebrating",
          b.mood.spirits == before_mood, b.mood.spirits)
    check("...no completion sound either - it would contradict what is on screen",
          "focus_done" not in b.take_sounds())
    check("...and no celebration antic queued",
          b._antic != B.TASK_FINISHED_ANIM, b._antic)

print("\n--- anger and being called end a session honestly rather than lying about it ---")
_cfg, b = fresh("anger", annoyance_threshold=1)
b.set_focus(True)
b.bubble_dismissed()  # one poke is enough to cross the (lowered) threshold
check("crossing the annoyance threshold cancels the session outright",
      not b.focus_active)
check("...not a completion - no reward for a session you got angry through",
      not b.take_focus_finished())
check("the storm-off itself still proceeds as normal", b._state == State.ANGRY, b._state)

_cfg, b = fresh("comehere")
b.set_focus(True)
check("holding the working pose beforehand", b._antic == B.FOCUS_HOLD_ANIM)
b.come_here(900)
check("being called across the screen cancels the session too",
      not b.focus_active)
check("...the call itself is not silently swallowed - he actually walks",
      b._state == State.WALK, b._state)

print("\n--- nothing here touches config ---")
_cfg, b = fresh("noconfig", focus_duration_min=25)
b.set_focus(True)
b.update(5000.0, None)
b.set_focus(False)
extra_keys = set(_cfg._values) - set(DEFAULTS)
check("no new keys leaked into config", not extra_keys, str(extra_keys))
check("focus_duration_min itself is untouched", _cfg["focus_duration_min"] == 25)

print("\n" + "=" * 62)
print("FAILURES: " + ", ".join(fails) if fails else "focus sessions behave")
sys.exit(1 if fails else 0)
