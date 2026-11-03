"""The seven little things: costume opinions, the poke flinch, the spin, and the involuntary lot."""

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
fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


def fresh(tag, **overrides):
    path = SCRATCH / f"quirk_{tag}.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    cfg["last_seen_date"] = "2026-07-31"
    cfg["seasonal_enabled"] = False
    for k, v in overrides.items():
        cfg[k] = v
    sheet = SpriteSheet(SRC / "assets" / "nib_sheet.png", SRC / "assets" / "frames.json", 2)
    b = Behavior(cfg, sheet)
    b.set_strip(1392, 0, 2560)
    b.place_at(1200)
    b.set_world(W.WorldState(hour=14.0, weekday=2, date_key="2026-07-31", week_key="2026-W31",
                             part_of_day=W.PART_DAY, season=W.SEASON_SPRING,
                             cpu_percent=8.0, cpu_busy=False))
    return cfg, b


print("\n--- 1. he has an opinion about his hat ---")
sheet = SpriteSheet(SRC / "assets" / "nib_sheet.png", SRC / "assets" / "frames.json", 2)
_cfg, b = fresh("cost")
for costume, expected in (("cone", "startle"), ("crown", "wink"), ("catears", "heart_eyes"),
                          ("beret", B.COSTUME_OPINION_DEFAULT), ("", B.COSTUME_OPINION_DEFAULT)):
    b._enter(State.IDLE)
    b.costume_chosen(costume)
    check(f"{costume or '(none)':10s} -> {expected}", b._antic == expected,
          f"{b.state.value}/{b._antic}")

check("every opinion names an animation that exists",
      all(sheet.has_state(a) for a in B.COSTUME_OPINIONS.values()),
      str(sorted({a for a in B.COSTUME_OPINIONS.values() if not sheet.has_state(a)})))
check("...and every costume it names exists too",
      set(B.COSTUME_OPINIONS) <= set(sheet.available_costumes()),
      str(sorted(set(B.COSTUME_OPINIONS) - set(sheet.available_costumes()))))

_cfg, b = fresh("cost_off", costume_reactions=False)
b.costume_chosen("cone")
check("costume_reactions=false switches it off", b.state is not State.ANTIC, b.state.value)

_cfg, b = fresh("cost_asleep")
b._enter(State.SLEEP)
b.costume_chosen("cone")
check("...and he does not wake up to admire it", b.state is State.SLEEP, b.state.value)

print("\n--- 2. one poke gets a flinch ---")
_cfg, b = fresh("poke", annoyance_threshold=4)
b.bubble_dismissed()
check("a single dismissal gets a visible reaction", b.state is State.ANTIC, b.state.value)
check("...a brief glare", b._antic == B.POKE_FLINCH_ANIM, str(b._antic))
check("...and it is short", b._antic_ms <= B.POKE_FLINCH_MS + 1, f"{b._antic_ms:.0f}ms")
check("...and it still counts as a poke", b.annoyance == 1, str(b.annoyance))

_cfg, b = fresh("poke_anger", annoyance_threshold=2)
b.bubble_dismissed()
b.bubble_dismissed()
check("crossing the threshold still storms off rather than flinching", b.state is State.ANGRY,
      b.state.value)

print("\n--- 3. scroll to spin ---")
_cfg, b = fresh("spin")
check("scrolling spins him", b.spin(), b.state.value)
check("...using the tumble frames, not new art", b._antic == B.TUMBLE_ANIM, str(b._antic))
check("...and a second scroll straight away is swallowed", not b.spin())
for _ in range(int(B.SPIN_COOLDOWN_MS / 50) + 2):
    b.update(50.0, None)
check("...but it comes back", b.spin())

_cfg, b = fresh("spin_off", scroll_to_spin=False)
check("scroll_to_spin=false switches it off", not b.spin())
for label, setup in (("asleep", lambda x: x._enter(State.SLEEP)),
                     ("in your hand", lambda x: x.drag_start()),
                     ("hidden", lambda x: x.set_hidden(True))):
    _cfg, b = fresh(f"spin_{label.replace(' ', '_')}")
    setup(b)
    check(f"he does not spin while {label}", not b.spin(), b.state.value)

print("\n--- 4/5. the involuntary two are scheduled, not rolled ---")
_cfg, b = fresh("sched")
check("a sneeze is scheduled ahead", 0 < b._sneeze_due_ms < 3_600_000, f"{b._sneeze_due_ms:.0f}ms")
check("...roughly half an hour out",
      1_000_000 < b._sneeze_due_ms < 2_800_000, f"{b._sneeze_due_ms/60000:.1f} min")
check("a trip is scheduled too", 0 < b._trip_due_ms < 3_600_000, f"{b._trip_due_ms/60000:.1f} min")

_cfg, b = fresh("sneeze")
b._sneeze_due_ms = 0.0
before = b.x
b._choose_next_action(None)
check("a due sneeze fires", b._antic == B.SNEEZE_ANIM, str(b._antic))
check("...and knocks him backwards", b.x != before, f"{before} -> {b.x}")
check("...and reschedules", b._sneeze_due_ms > 100_000, f"{b._sneeze_due_ms/60000:.1f} min")

_cfg, b = fresh("trip")
b._trip_due_ms = 0.0
b.update(50.0, None)
check("a due trip only ARMS, it does not fire on the spot", b._trip_armed and b._antic != B.TRIP_ANIM,
      f"armed={b._trip_armed} antic={b._antic}")
b._begin_walk(2000)
b.update(50.0, None)
check("...and lands on the next walk", b._antic == B.TRIP_ANIM, str(b._antic))

_cfg, b = fresh("trip_storm")
b._trip_armed = True
b._pending_sleep = True
b._begin_walk(2000)
b.update(50.0, None)
check("he does not trip while storming off", b._antic != B.TRIP_ANIM, str(b._antic))

print("\n--- 6. hiccups, earned by landing hard ---")
_cfg, b = fresh("hic", injury_enabled=False)
b.drag_start()
b.drag_to(b.x, b.rest_top() - 400)
b.update(16.0, None)
b.drag_end()
guard = 0
while (b._falling or b._rolling) and guard < 3000:
    b.update(16.0, None)
    guard += 1
check("a hard landing gives him the hiccups", b._hiccups_left > 0, str(b._hiccups_left))
check("...in threes or so", B.HICCUP_MIN <= b._hiccups_left + 1 <= B.HICCUP_MAX + 1,
      str(b._hiccups_left))
seen = 0
for _ in range(600):
    b.update(50.0, None)
    if b._antic == B.HICCUP_ANIM and b._antic_ms > B.HICCUP_MS - 60:
        seen += 1
check("and they actually play", seen >= 1, f"{seen} hiccups seen")
for _ in range(2000):
    b.update(50.0, None)
check("...and then stop", b._hiccups_left == 0, str(b._hiccups_left))

_cfg, b = fresh("hic_soft", injury_enabled=False)
b.drag_start()
b.drag_to(b.x + 4, b.rest_top() - 8)
b.update(16.0, None)
b.drag_end()
for _ in range(200):
    b.update(16.0, None)
check("a gentle shuffle does not", b._hiccups_left == 0, str(b._hiccups_left))

print("\n--- 7. he looks at what you dropped ---")
_cfg, b = fresh("look")
b.look_at(b.x + 200, b.top + 300)   # down and to the right
check("he glances at it", b._antic == B.EYE_TRACK_STATE, str(b._antic))
check("...at the point, not at your cursor", b._forced_look is not None, str(b._forced_look))
b.update(50.0, None)                 # the first tick is what applies the held frame
looked = b.animator.frame_index
check("...and it really is aimed down-right at it", looked == 8, str(looked))
b.update(50.0, (0, 0))               # cursor somewhere else entirely
check("...and the cursor does not steal it back", b.animator.frame_index == looked,
      f"{looked} -> {b.animator.frame_index}")
check("...and he does not give up early because the pointer is far away",
      b._antic_ms > 300, f"{b._antic_ms:.0f}ms")
for _ in range(40):
    b.update(50.0, None)
check("the forced look is cleared afterwards", b._forced_look is None, str(b._forced_look))

print("\n--- the quirks switch ---")
_cfg, b = fresh("off", quirks_enabled=False)
b._sneeze_due_ms = 0.0
b._choose_next_action(None)
check("quirks_enabled=false stops the sneeze", b._antic != B.SNEEZE_ANIM, str(b._antic))
b.start_hiccups()
check("...and the hiccups", b._hiccups_left == 0, str(b._hiccups_left))

print("\n--- 'Do something' reaches them on demand ---")
import random as _random  # noqa: E402

_cfg, b = fresh("demand")
_random.seed(7)
picked = [b.do_something() for _ in range(400)]
rare = {B.SNEEZE_ANIM, B.TRIP_ANIM, B.HICCUP_ANIM, B.TUMBLE_ANIM}
hits = sum(1 for x in picked if x in rare)
check("every rare animation is reachable", rare <= set(picked), str(sorted(rare - set(picked))))
# The button exists to show the ones you would otherwise wait half an hour for, so it has to land
# on them most of the time - but not always, or it stops being "do something" and becomes a test
# harness with the rest of his repertoire hidden behind it.
check("most presses land on one of the rare four", 55 <= hits / 4 <= 90, f"{hits / 4:.0f}%")
check("...and the ambient pool still turns up", len(set(picked) - rare) >= 4,
      str(sorted(set(picked) - rare)[:5]))
check("it never leaves a trip armed behind it", not b._trip_armed)
check("...and it does not consume the next natural sneeze", b._sneeze_due_ms > 100_000,
      f"{b._sneeze_due_ms / 60000:.1f} min")

_cfg, b = fresh("demand_drag")
b.drag_start()
check("he refuses while in your hand", b.do_something() == "", b.state.value)
_cfg, b = fresh("demand_hidden")
b.set_hidden(True)
check("...and while hidden", b.do_something() == "", b.state.value)

print("\n--- main wires the last two up ---")
import inspect  # noqa: E402

import main as M  # noqa: E402

check("the wheel is connected to the spin",
      "scrolled.connect(self.behavior.spin)" in inspect.getsource(M.NibApp._connect))
check("choosing a costume asks his opinion",
      "costume_chosen" in inspect.getsource(M.NibApp._on_costume_chosen))
_src = inspect.getsource(M.NibApp._on_costume_chosen)
check("...after the sprites are rebuilt, so he is wearing it",
      _src.index("_rebuild_appearance") < _src.index("self.behavior.costume_chosen"))
send = inspect.getsource(M.NibApp._send)
# The launch call itself, whatever its arguments - the point is that it is NOT behind the
# QTimer that sequences his animation.
check("a dropped file launches immediately",
      "launch(mode, text" in send and send.index("launch(mode, text") < send.index("singleShot"))
check("...and only his animation waits for the glance", "after_ms" in send and "singleShot" in send)
check("the tray can ask him to do something",
      "do_something_requested" in inspect.getsource(M.NibApp._connect))
check("...and it wakes him rather than looking broken",
      "SLEEP" in inspect.getsource(M.NibApp._on_do_something))

print("\n" + "=" * 62)
print("FAILURES: " + ", ".join(fails) if fails else "all seven behave")
sys.exit(1 if fails else 0)
