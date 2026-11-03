"""The seven little things: costume opinions, the poke flinch, the spin, and the involuntary lot -
plus how any antic ends."""

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
# The beret used to get the one default; it is a hat, so now it gets the hats' scratch. Each of the
# others is the first unnamed costume of its category.
for costume, expected in (("cone", "startle"), ("crown", "wink"), ("catears", "heart_eyes"),
                          ("beret", "scratch"), ("horns", "sparkle"), ("shades", "look_around"),
                          ("bowtie", "wink"), ("apron", "pushups"), ("jetpack", "stretch"),
                          ("", B.COSTUME_OPINION_DEFAULT)):
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
check("every category has an opinion of its own",
      set(sheet.costume_categories()) <= set(B.COSTUME_CATEGORY_OPINIONS),
      str(sorted(set(sheet.costume_categories()) - set(B.COSTUME_CATEGORY_OPINIONS))))
check("...each one an animation that exists",
      all(sheet.has_state(a) for a in B.COSTUME_CATEGORY_OPINIONS.values()))
check("...and none of either table is the hop, which is the quick-change itself",
      "hop" not in B.COSTUME_CATEGORY_OPINIONS.values() and "hop" not in B.COSTUME_OPINIONS.values())
from collections import Counter  # noqa: E402

spread = Counter(b._costume_opinion(n) for n in sheet.available_costumes())
print(f"  opinions: {dict(spread.most_common())}")
check("no one reaction covers more than 10 costumes (32 shared look_around before)",
      spread.most_common(1)[0][1] <= 10, str(spread.most_common(1)))

_cfg, b = fresh("cost_off", costume_reactions=False)
check("costume_reactions=false switches it off",
      not b.costume_chosen("cone") and b.state is not State.ANTIC, b.state.value)

_cfg, b = fresh("cost_asleep")
b._enter(State.SLEEP)
check("...and he does not wake up to admire it",
      not b.costume_chosen("cone") and b.state is State.SLEEP, b.state.value)
_cfg, b = fresh("cost_true")
check("costume_chosen says when he did react", b.costume_chosen("cone") and b.state is State.ANTIC)
_cfg, b = fresh("cost_paused")
b.paused = True
check("...and paused, he keeps it to himself - it would freeze on its first frame",
      not b.costume_chosen("cone") and b.state is State.IDLE, b.state.value)

_cfg, b = fresh("skin")
check("(the colour reaction has its art)", b._sheet.has_state(B.SKIN_REACTION_ANIM))
b.skin_chosen()
check("a new colour gets a sparkle", b._antic == B.SKIN_REACTION_ANIM, str(b._antic))
_cfg, b = fresh("skin_off", costume_reactions=False)
b.skin_chosen()
check("...costume_reactions=false switches that off too", b.state is not State.ANTIC,
      b.state.value)
_cfg, b = fresh("skin_asleep")
b._enter(State.SLEEP)
b.skin_chosen()
check("...and it does not wake him", b.state is State.SLEEP, b.state.value)
_cfg, b = fresh("skin_paused")
b.paused = True
b.skin_chosen()
check("...nor sparkle while paused", b.state is State.IDLE, b.state.value)

print("\n--- 1b. the quick-change ---")


def hop_until(b, until, bound_ms=3000.0, step=20.0):
    """Tick until ``until(b)`` (checked after each tick); returns the ms it took, or None."""
    spent = 0.0
    while spent < bound_ms:
        b.update(step, None)
        spent += step
        if until(b):
            return spent
    return None


lifts = [sheet.foot_print(B.QUICK_CHANGE_ANIM, i)[2]
         for i in range(sheet.frame_count(B.QUICK_CHANGE_ANIM))]
check("the swap frame is the top of the hop",
      lifts[B.QUICK_CHANGE_SWAP_FRAME] == max(lifts) > 0, str(lifts))

_cfg, b = fresh("qc", quirks_enabled=False)
check("a quick-change starts", b.quick_change("cone"))
check("...as the hop, from the crouch", b.state is State.ANTIC and b._antic == "hop"
      and b.animator.frame_index == 0, f"{b.state.value}/{b._antic}/{b.animator.frame_index}")
check("...and nothing is due to be shown yet", not b.take_costume_swap())
swapped_at = []


def _swap_seen(x):
    if x.take_costume_swap():
        swapped_at.append(x.animator.frame_index)
    return x._antic != "hop"


took = hop_until(b, _swap_seen)
check("the costume is shown exactly once, on the apex frame",
      swapped_at == [B.QUICK_CHANGE_SWAP_FRAME], str(swapped_at))
check("...and not again afterwards", not b.take_costume_swap())
check("then he lands and gives his opinion of it", b._antic == "startle", str(b._antic))
check("...well before the safety net", took is not None and took < B.QUICK_CHANGE_MAX_MS,
      f"{took}ms")

_cfg, b = fresh("qc_slow", quirks_enabled=False)
b._mood_pace = lambda: 0.54
b.quick_change("cone")
swapped_at = []
took = hop_until(b, _swap_seen)
check("at the slowest pace it still swaps at the apex",
      swapped_at == [B.QUICK_CHANGE_SWAP_FRAME], str(swapped_at))
check("...and lands before the safety net", took is not None and took < B.QUICK_CHANGE_MAX_MS,
      f"{took}ms")


def _perched(x):
    x.drag_start()
    for _ in range(3):
        x.drag_to(1200, 300)
        x.update(50.0, None)
    x.drag_end(perch_rect=(1100, 300, 1600, 700))


for label, setup, overrides in (
    ("asleep", lambda x: x._enter(State.SLEEP), {}),
    ("in your hand", lambda x: x.drag_start(), {}),
    ("hidden", lambda x: x.set_hidden(True), {}),
    ("paused", lambda x: setattr(x, "paused", True), {}),
    ("with reactions off", lambda x: None, {"costume_reactions": False}),
    ("on a window", _perched, {}),
):
    _cfg, b = fresh(f"qc_no_{label.replace(' ', '_')}", quirks_enabled=False, **overrides)
    setup(b)
    before = b.state
    check(f"{label}: no hop - it just goes on", not b.quick_change("cone") and b.state is before,
          f"{before.value} -> {b.state.value}")

for label, interrupt in (("grabbed", lambda x: x.drag_start()),
                         ("hidden", lambda x: x.set_hidden(True))):
    _cfg, b = fresh(f"qc_cut_{label}", quirks_enabled=False)
    b.quick_change("cone")
    b.update(20.0, None)
    interrupt(b)
    check(f"{label} mid-hop: the costume shows at once",
          b.take_costume_swap() and b._quick_change is None, str(b._quick_change))

_cfg, b = fresh("qc_twice", quirks_enabled=False)
b.quick_change("cone")
for _ in range(4):
    b.update(20.0, None)
b.quick_change("crown")
check("a second pick mid-hop leaves nothing stale to show", not b.take_costume_swap())
check("...and hops again from the crouch", b.animator.frame_index == 0 and b._antic == "hop",
      f"{b._antic}/{b.animator.frame_index}")
hop_until(b, lambda x: x._antic != "hop")
check("...into the newer one's opinion", b._antic == "wink", str(b._antic))

_cfg, b = fresh("qc_greet", quirks_enabled=False)
b._begin_antic("hop", 1500.0)
b.animator.hold("hop", 3)
b.quick_change("cone")
check("a greeting hop already in the air restarts from the crouch",
      b.animator.frame_index == 0 and not b.take_costume_swap(), str(b.animator.frame_index))

# The wardrobe's "Take it off" hops into nothing: "" is a costume to change into, not "no hop".
_cfg, b = fresh("qc_off", quirks_enabled=False)
check("taking it off is a quick-change too", b.quick_change(""))
swapped_at = []
hop_until(b, _swap_seen)
check("...shown at the apex like any other", swapped_at == [B.QUICK_CHANGE_SWAP_FRAME],
      str(swapped_at))
check("...then the bare-headed look about", b._antic == B.COSTUME_OPINION_DEFAULT, str(b._antic))

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

print("\n--- 8. antics end on a clean frame ---")


def watch_endings(b):
    """Record (state, frame on screen, finished) every time _antic_settled lets an antic go."""
    seen = []
    inner = b._antic_settled

    def wrapped(dt_ms, shown):
        done = inner(dt_ms, shown)
        if done:
            seen.append((b.animator.state, shown, b.animator.finished))
        return done

    b._antic_settled = wrapped
    return seen


def run_out(b, step=50.0, limit=20000.0):
    """Tick until he leaves ANTIC; returns how long it took."""
    spent = 0.0
    while b.state is State.ANTIC and spent < limit:
        b.update(step, None)
        spent += step
    return spent


_cfg, b = fresh("clean_hop")
seen = watch_endings(b)
b._begin_antic("hop", 1500.0)
b.update(1.0, None)                 # let _sync_animation put the hop on screen
b.animator.hold("hop", 2)           # the top of the jump
b._antic_ms = 0.0
b.update(50.0, None)
check("a hop whose time runs out mid-air does not end that tick", b.state is State.ANTIC,
      b.state.value)
spent = run_out(b)
check("...it lands within one more cycle", b.state is State.IDLE and spent <= 700, f"{spent:.0f}ms")
check("...and the frame it ends on has his feet down",
      bool(seen) and b._sheet.foot_print(*seen[-1][:2])[2] == 0, str(seen[-1:]))

_random.seed(1806)
airborne, worst = 0, -1e9
for _ in range(200):
    _cfg, b = fresh("clean_hops")
    seen = watch_endings(b)
    asked = _random.uniform(1300, 2100)
    b._begin_antic("hop", asked)
    spent = run_out(b)
    if not seen or b._sheet.foot_print(*seen[-1][:2])[2] > 0:
        airborne += 1
    worst = max(worst, spent - asked - b._sheet.total_duration("hop") / b._mood_pace())
check("200 hops of random length: none ends in mid-air", airborne == 0, f"{airborne} airborne")
check("...and none overruns by more than a cycle", worst <= 50.0, f"worst {worst:+.0f}ms")

_cfg, b = fresh("clean_look")
b._begin_antic("look_around", 1000.0)
b.update(1.0, None)
b._antic_ms = 0.0
b.update(50.0, None)
check("a grounded loop ends on its timer with no wait", b.state is State.IDLE, b.state.value)

_cfg, b = fresh("clean_night", personality="chill")
seen = watch_endings(b)
b.set_world(W.WorldState(hour=1.0, weekday=2, date_key="2026-07-31", week_key="2026-W31",
                         part_of_day=W.PART_NIGHT, season=W.SEASON_SPRING,
                         cpu_percent=8.0, cpu_busy=False))
check("(night on chill really is slow)", b._mood_pace() < 0.7, f"pace {b._mood_pace():.2f}")
b._begin_antic(B.TASK_FINISHED_ANIM, B.TASK_FINISHED_MS)
last_frame, finished = -1, False
while b.state is State.ANTIC:
    last_frame, finished = b.animator.frame_index, b.animator.finished
    b.update(50.0, None)
check("at night the knocking-off dance reaches its settle frame",
      last_frame == b._sheet.frame_count(B.TASK_FINISHED_ANIM) - 1, f"frame {last_frame}")
check("...and plays it through before he moves on", bool(seen) and seen[-1][2], str(seen[-1:]))

_cfg, b = fresh("clean_hold")
b._begin_antic(B.AWAY_ANTIC, 1e9)
b.update(1.0, None)
b._antic_ms = 0.0
b.update(50.0, None)
check("a hold ended by zeroing its clock ends on the next tick", b.state is State.IDLE,
      b.state.value)

_cfg, b = fresh("clean_eyes")
b.look_at(1200, 1000)
spent = run_out(b)
check("eye tracking ends on its own timer, never an overrun",
      b.state is State.IDLE and spent <= 700 and b._antic_overrun_ms is None, f"{spent:.0f}ms")

_cfg, b = fresh("clean_tick")
b._begin_antic("hop", 1500.0)
b.update(1.0, None)
b.animator.hold("hop", 2)
b._antic_ms = 0.0
b.update(10.0, None)
check("waiting to land does not spin the app at the tick floor",
      b.state is State.ANTIC and b.next_tick_ms(16, 250) > 16, f"{b.next_tick_ms(16, 250):.0f}ms")

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
check("the quick-change writes the config first and defers only the visible swap",
      _src.index('self.config["costume"] = costume') < _src.index("self.behavior.quick_change(")
      < _src.index("_rebuild_appearance"))
check("...remembers it for the wardrobe", "self.config.remember_costume(costume)" in _src)
check("...and starts the safety net", "self._costume_timer.start()" in _src)
_tick = inspect.getsource(M.NibApp._tick)
check("the swap is drained before the persona sync and the paint, so the apex shows it",
      _tick.index("take_costume_swap()") < _tick.index("self._sync_persona_costume()")
      < _tick.index("self._apply_frame()"))
_sync = inspect.getsource(M.NibApp._sync_persona_costume)
check("the persona sync waits while a quick-change is in the air (else the beanie came off early)",
      _sync.index("if self._costume_pending:") < _sync.index("wanted ="))
check("any rebuild settles a pending quick-change",
      "self._costume_pending = False" in inspect.getsource(M.NibApp._rebuild_appearance)
      and "self._costume_pending = False" in inspect.getsource(M.NibApp._rescale_for))
_apply = inspect.getsource(M.NibApp._apply_pending_costume)
check("...and the pending flag is cleared before rebuilding, so a failed one cannot wedge it",
      _apply.index("self._costume_pending = False") < _apply.index("self._rebuild_appearance()"))
check("the safety net comes after the apex at the slowest pace, and before the hop's own cap",
      481 < M.QUICK_CHANGE_TIMEOUT_MS < B.QUICK_CHANGE_MAX_MS,
      f"{M.QUICK_CHANGE_TIMEOUT_MS} vs {B.QUICK_CHANGE_MAX_MS}")
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
