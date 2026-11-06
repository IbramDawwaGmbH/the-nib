"""Petting, coming when called, the personality presets, the fidget switch, and the bond."""

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

SCRATCH = _scratch()
BASELINE, X_MIN, X_MAX = 1392, 0, 2560
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


def fresh(tag, pin=True, remember=False, **overrides):
    path = SCRATCH / f"pet_{tag}.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    cfg["last_seen_date"] = "2026-07-31"
    cfg["seasonal_enabled"] = False
    if pin:
        # He stays put, so a fixed cursor point keeps landing on him. Without this the pet tests
        # are really testing whether he happened to wander off mid-hold.
        cfg["wander_enabled"] = False
        cfg["antic_chance"] = 0.0
        cfg["play_with_windows"] = False
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


def on_him(b, dy=0):
    """A cursor position squarely on his sprite, recomputed as he moves.

    Useful for "is he reachable at all", but NOT for the hold: a cursor that chases him is by
    definition never still, so the stillness clock resets on every tick he walks. That is the
    correct behaviour and a wrong simulation of a hand resting on a desk - which is why the
    fixtures below pin him and the holds use a fixed screen point.
    """
    return (b.x + b.sprite_w // 2, b.top + b.sprite_h // 2 + dy)


def resting_hand(b):
    """A fixed screen point on him, captured once. This is what a hand actually is."""
    return (b.x + b.sprite_w // 2, b.top + b.sprite_h // 2)


def hold(b, ms, cursor, step=50.0):
    """Hold a cursor at a point for ``ms``. ``cursor`` may be a fixed tuple or a callable.

    Returns every state seen along the way. Sampling only the final state is not enough for the
    suppression checks: SLEEP and ANGRY both expire on their own inside a long enough window, so
    "he ended up somewhere other than PET" would pass even if he had been petted in the middle.
    """
    elapsed = 0.0
    seen = set()
    while elapsed < ms:
        b.update(step, cursor(b) if callable(cursor) else cursor)
        seen.add(b.state)
        elapsed += step
    return seen


print("\n--- a pointer resting on him is a pet ---")
_cfg, b = fresh("basic")
hold(b, 1200, resting_hand(b))
check("holding still on him pets him", b.state is State.PET, b.state.value)
check("...and it counts as noticing him", b._neglect_stage == 0 and b._neglect_ms == 0.0)
check("...and he is armed against an immediate repeat", not b._pet_armed)

print("\n--- it needs the hold; a passing cursor is not a pet ---")
_cfg, b = fresh("moving")
x = b.x
for i in range(40):
    b.update(50.0, (b.x + i * 9, b.top + 10))   # sweeping across him
check("a cursor sweeping over him does not pet him", b.state is not State.PET, b.state.value)

_cfg, b = fresh("short", pet_hold_ms=2000)
hold(b, 900, resting_hand(b))
check("a hold shorter than pet_hold_ms does not", b.state is not State.PET, b.state.value)
hold(b, 1400, resting_hand(b))
check("...and holding longer does", b.state is State.PET, b.state.value)

print("\n--- near is not on: the wave must not swallow it ---")
# hover_wave_distance_px is 120 by default, so a pointer this far out is deep inside wave range
# and squarely outside his sprite. Before petting was checked first, this case never reached it.
_cfg, b = fresh("near")
hold(b, 2000, lambda b: (b.x + b.sprite_w + 60, b.top + b.sprite_h // 2))
check("a pointer beside him waves, it does not pet", b.state is not State.PET, b.state.value)
check("...and it did wave", b.state is State.HOVER_WAVE or b._wave_cooldown_ms > 0, b.state.value)

_cfg, b = fresh("order")
hold(b, 1200, resting_hand(b))
check("a pointer ON him pets rather than waves", b.state is State.PET, b.state.value)

print("\n--- he keeps going while your hand stays, and stops when it leaves ---")
_cfg, b = fresh("duration")
hold(b, 1000, resting_hand(b))
check("petting starts", b.state is State.PET)
hold(b, 2500, resting_hand(b))
check("...and continues while you hold", b.state is State.PET, b.state.value)
for _ in range(6):
    b.update(50.0, (10, 10))    # hand gone
check("taking your hand away ends it", b.state is not State.PET, b.state.value)

_cfg, b = fresh("cap")
hold(b, B.PET_MAX_MS + 1500, resting_hand(b))
check("an abandoned pointer does not trap him there", b.state is not State.PET, b.state.value)

print("\n--- it has to be re-armed by the hand leaving ---")
_cfg, b = fresh("rearm")
hold(b, 1000, resting_hand(b))
check("first pet", b.state is State.PET)
hand = resting_hand(b)
for _ in range(int(B.PET_MAX_MS / 50) + 100):   # keep holding, well past the cap and cooldown
    b.update(50.0, hand)
check("it does not restart under the same unmoved hand", b.state is not State.PET, b.state.value)
check("...because it is still disarmed", not b._pet_armed)
for _ in range(6):
    b.update(50.0, (10, 10))
check("moving away re-arms it", b._pet_armed)
hold(b, 1200, hand)
check("...and then it works again", b.state is State.PET, b.state.value)

print("\n--- he walks into a hand that was already there ---")
# The other direction, and the one a fixed-cursor fixture would never exercise: the hand does not
# come to him, he arrives under it. Nothing special handles this - the stillness clock is in screen
# coordinates, so it is already satisfied by the time he gets there.
_cfg, b = fresh("walkinto", pin=False, antic_chance=0.0)
b.place_at(600)
hand = (1400, BASELINE - b.sprite_h // 2)
# Kept aimed at the hand rather than left to wander towards it, and re-aimed whenever he stops.
#
# Two things kept derailing this. Wander targets are random, so he did not reliably get there; and
# more interestingly, the WAVE would intercept him - the hand comes within hover_wave_distance_px
# a moment before he is actually on it, so he stops to wave and then wanders off somewhere else.
# That is correct behaviour, not a bug, so the test tolerates it instead of asserting it away.
# What is under test is "he arrives under a waiting hand and gets petted".
guard, petted = 0, False
while guard < 4000 and not petted:
    if b.state is State.IDLE and not b._cursor_is_over(hand):
        b._begin_walk(hand[0] + 120 if b.x < hand[0] else hand[0] - 120)
    b.update(50.0, hand)
    petted = b.state is State.PET
    guard += 1
check("a hand left on the taskbar catches him as he wanders past", petted, f"after {guard} ticks")

print("\n--- it is the only thing that takes annoyance back off ---")
_cfg, b = fresh("drain", annoyance_threshold=99)   # high, so poking never tips him into anger
for _ in range(3):
    b.bubble_dismissed()
before = b.annoyance
check("three dismissals annoy him", before == 3, str(before))
hold(b, 1000, resting_hand(b))
check("petting begins", b.state is State.PET)
hold(b, B.PET_DRAIN_MS * 2 + 200, resting_hand(b))
check("...and forgives a poke as it goes", b.annoyance < before, f"{before} -> {b.annoyance}")

print("\n--- and it is the only kindness the mood has ---")
_cfg, b = fresh("mood")
start = b.mood.spirits
hold(b, 1200, resting_hand(b))
check("petting lifts his spirits", b.mood.spirits > start, f"{start:+.3f} -> {b.mood.spirits:+.3f}")
_cfg, b3 = fresh("mood3")
b3.prompt_sent("hello")
check("...more than sending a prompt does", b.mood.spirits > b3.mood.spirits,
      f"pet {b.mood.spirits:+.3f} vs prompt {b3.mood.spirits:+.3f}")

_cfg, b2 = fresh("mood2")
b2.bubble_dismissed()
check("a dismissed bubble lowers them", b2.mood.spirits < 0, f"{b2.mood.spirits:+.3f}")

print("\n--- when petting would be wrong ---")
for label, setup in (
    ("asleep", lambda b: b._enter(State.SLEEP)),
    ("cross", lambda b: b._enter(State.ANGRY)),
    ("hidden", lambda b: b.set_hidden(True)),
    ("being dragged", lambda b: b.drag_start()),
    ("away", lambda b: b.set_away(True)),
    ("mid-throw", lambda b: setattr(b, "_falling", True)),
):
    _cfg, b = fresh(f"no_{label.replace(' ', '_').replace('-', '_')}")
    setup(b)
    seen = hold(b, 2500, resting_hand(b))
    check(f"he is not petted while {label}", State.PET not in seen,
          "/".join(sorted(s.value for s in seen)))

_cfg, b = fresh("off", petting_enabled=False)
seen = hold(b, 3000, resting_hand(b))
check("petting_enabled=false switches it off", State.PET not in seen,
      "/".join(sorted(s.value for s in seen)))

print("\n--- being ignored does not tick up while he is being petted ---")
_cfg, b = fresh("neglect_sup")
b._neglect_ms = 0.0
hold(b, 1000, resting_hand(b))
check("petting started", b.state is State.PET)
before_ms = b._neglect_ms
hold(b, 2000, resting_hand(b))
check("the ignored clock is frozen", b._neglect_ms == before_ms, f"{before_ms} -> {b._neglect_ms}")

print("\n--- coming when called ---")
_cfg, b = fresh("come")
b.place_at(300)
check("he sets off", b.come_here(2000), b.state.value)
check("...walking", b.state is State.WALK, b.state.value)
check("...to the right", b.facing == "right", b.facing)
guard = 0
while b.state is State.WALK and guard < 4000:
    b.update(16.0, None)
    guard += 1
check("...and arrives near where you called him",
      abs((b.x + b.sprite_w / 2) - 2000) < 40, f"centre {b.x + b.sprite_w // 2} vs 2000")

_cfg, b = fresh("come_asleep")
b._enter(State.SLEEP)
called = b.come_here(2000)
check("calling him gets him up from a nap", b.state is not State.SLEEP, b.state.value)
check("...and he comes", called and b.state is State.WALK, b.state.value)

_cfg, b = fresh("come_cross")
b._register_poke()
b._begin_anger()
check("he is cross", b.state is State.ANGRY)
b.come_here(2400)
check("being called ends a sulk", b.state is not State.ANGRY, b.state.value)
check("...and cancels the storm-off nap", not b._pending_sleep)
check("...and clears what he was cross about", b.annoyance == 0)

_cfg, b = fresh("come_drag")
b.drag_start()
check("he refuses while in your hand", not b.come_here(2000))
check("...and stays in your hand", b.state is State.DRAG, b.state.value)

_cfg, b = fresh("come_hidden")
b.set_hidden(True)
check("and while hidden there is nothing to call", not b.come_here(2000))

_cfg, b = fresh("come_here_already")
b.place_at(1200)
here = b.x + b.sprite_w / 2
check("calling him to where he already stands is a quiet no-op", not b.come_here(here))
check("...and does not disturb him", b.state is State.IDLE, b.state.value)

print("\n--- the personality presets are a lens, not a rewrite ---")
cfg, b = fresh("preset", pin=False, personality="chill")
check("the file keeps YOUR numbers", cfg["antic_chance"] == 0.5, str(cfg["antic_chance"]))
check("chill is more patient", b._annoyance_threshold() > int(cfg["annoyance_threshold"]),
      f"{cfg['annoyance_threshold']} -> {b._annoyance_threshold()}")
chill_beat = sum(b._next_wander_delay() for _ in range(200)) / 200
chill_pace = b._mood_pace()

cfg, b = fresh("preset2", pin=False, personality="hyper")
hyper_beat = sum(b._next_wander_delay() for _ in range(200)) / 200
hyper_pace = b._mood_pace()
check("hyper has much shorter beats than chill", hyper_beat < chill_beat / 3,
      f"{hyper_beat:.0f}ms vs {chill_beat:.0f}ms")
check("...and a brisker animation", hyper_pace > chill_pace, f"{hyper_pace:.2f} vs {chill_pace:.2f}")
check("hyper weights bouncing up", b._mood_weights().get("hop", 1.0) > 1.5,
      f"hop={b._mood_weights().get('hop'):.2f}")

cfg, b = fresh("preset3", pin=False, personality="sassy")
check("sassy has half the patience", b._annoyance_threshold() < int(cfg["annoyance_threshold"]),
      f"{cfg['annoyance_threshold']} -> {b._annoyance_threshold()}")
check("...and winks far more", b._mood_weights().get("wink", 1.0) > 2.0,
      f"wink={b._mood_weights().get('wink'):.2f}")

cfg, b = fresh("preset4", pin=False, personality="balanced")
# NOT "the weight table is empty": the world still has its say, and this fixture is a Friday
# afternoon, which legitimately boosts the dance. What balanced means is that the PRESET adds
# nothing on top of it.
check("balanced is an empty lens", b._preset() == {}, str(b._preset()))
check("...with every multiplier at exactly 1.0",
      all(b._preset_factor(k) == 1.0 for k in ("antic_chance", "beat", "pace", "annoyance")))
check("...so only the world is left in the weights", b._mood_weights() == {"dance": 3.0},
      str(b._mood_weights()))
check("...and the threshold is left alone",
      b._annoyance_threshold() == int(cfg["annoyance_threshold"]))

cfg, b = fresh("preset5", pin=False, personality="nonsense")
check("an unknown preset falls back to balanced", cfg["personality"] == "balanced",
      cfg["personality"])

print("\n--- the mood composes with the clock rather than replacing it ---")
_cfg, b = fresh("compose")
b.set_world(W.WorldState(hour=1.0, weekday=4, date_key="2026-07-31", week_key="2026-W31",
                         part_of_day=W.PART_NIGHT, season=W.SEASON_SPRING,
                         cpu_percent=8.0, cpu_busy=False))
night_only = b._mood_weights().get("dance", 1.0)
for _ in range(8):
    b.mood.nudge("petted")
both = b._mood_weights().get("dance", 1.0)
check("a good mood does not cancel the fact that it is 1am", both < 1.0, f"{both:.3f}")
check("...it lifts it, multiplicatively", both > night_only,
      f"night alone {night_only:.3f} -> with a good mood {both:.3f}")

print("\n--- the fidgets ---")
_cfg, b = fresh("fidget")
pool = {a[0] for a in B.ANTICS if a[4] == B.FIDGET_CATEGORY}
check("three of them", len(pool) == 3, str(sorted(pool)))
check("all three are in the sheet", all(b._sheet.has_state(n) for n in pool))
picks = {b._pick_antic(None)[0] for _ in range(600)}
check("they turn up in the ambient pool", pool & picks, str(sorted(pool & picks)))

_cfg, b = fresh("fidget_off", fidgets_enabled=False)
picks = {b._pick_antic(None)[0] for _ in range(600)}
check("fidgets_enabled=false removes them", not (pool & picks), str(sorted(pool & picks)))
check("...without leaving him with nothing to do", len(picks) >= 6, str(len(picks)))

print("\n--- the bond: somebody he knows is forgiven faster ---")


def forgiven(b, pet_ms):
    """Three pokes, then ``pet_ms`` of actual petting. Returns (pokes left, state).

    Timed from the tick the pet begins, not from when the hand arrives, so both mascots get
    exactly the same petting whatever the hold took. All of it is well inside the 15s poke window.
    """
    for _ in range(3):
        b.bubble_dismissed()
    hand = resting_hand(b)
    guard = 0
    while b.state is not State.PET and guard < 60:
        b.update(50.0, hand)
        guard += 1
    hold(b, pet_ms, hand)
    return b.annoyance, b.state


_cfg, stranger = fresh("bond_stranger", annoyance_threshold=99)
check("on a fresh memory a pet forgives one poke per 1.2s, exactly as ever",
      stranger._pet_forgive_every_ms() == B.PET_DRAIN_MS, str(stranger._pet_forgive_every_ms()))
# The 30-day party is spent up front: this is about the pet, not about a milestone in the middle.
cfg_friend, friend = fresh("bond_friend", remember=True, annoyance_threshold=99,
                           last_milestone_days="30")
bond = friend.memory.bond()
every = friend._pet_forgive_every_ms()
check("a month of days and pets is a bond near the top", bond > 0.85, f"{bond:.3f}")
check("...and his pets forgive by the bond's share faster",
      abs(every - B.PET_DRAIN_MS * (1.0 - B.FORGIVE_BOND_SPEEDUP * bond)) < 1e-9
      and every < 0.7 * B.PET_DRAIN_MS, f"{every:.0f}ms")
left_stranger, state_stranger = forgiven(stranger, 2000.0)
left_friend, state_friend = forgiven(friend, 2000.0)
check("two seconds of petting forgive the stranger one poke of three",
      left_stranger == 2 and state_stranger is State.PET, f"{left_stranger} {state_stranger.value}")
check("...and the friend two", left_friend == 1 and state_friend is State.PET,
      f"{left_friend} {state_friend.value}")
cfg_friend["memory_enabled"] = False
check("with memory off it is the stranger's 1.2s again",
      friend._pet_forgive_every_ms() == B.PET_DRAIN_MS)

print("\n--- a week of hard landings, and the warm looks thin out ---")
cfg_wary, b = fresh("wary")
mem = b.memory
mem.record("hurts", "2026-07-31", n=3)
mem.record("hurts", "2026-07-25")            # six days back: still this week
check("four hard landings in a week: not wary yet", b._mood_weights() == {"dance": 3.0},
      str(b._mood_weights()))
mem.record("hurts", "2026-07-24", n=4)       # seven days back: last week, and it does not count
check("...and older ones do not count towards it", b._mood_weights() == {"dance": 3.0},
      str(b._mood_weights()))
WARY = {"dance": 3.0, "heart_eyes": 0.5, "sparkle": 0.7}
mem.record("hurts", "2026-07-28")
check("the fifth makes him wary: heart eyes x0.5, sparkles x0.7", b._mood_weights() == WARY,
      str(b._mood_weights()))
check("...a lean, above the 0.4 floor every lean keeps", min(B.WARY_WEIGHTS.values()) >= 0.4)
mem.record("pets", "2026-07-30", n=5)
check("...but not when he has been petted as often as hurt", b._mood_weights() == {"dance": 3.0},
      str(b._mood_weights()))
mem.record("hurts", "2026-07-31")
check("...one more hurt than pets and he is wary again", b._mood_weights() == WARY,
      str(b._mood_weights()))
cfg_wary["memory_enabled"] = False
check("...and never with memory off", b._mood_weights() == {"dance": 3.0},
      str(b._mood_weights()))

print("\n" + "=" * 62)
print("FAILURES: " + ", ".join(fails) if fails else "petting, calling and presets OK")
sys.exit(1 if fails else 0)
