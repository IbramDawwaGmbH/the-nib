"""Nib and the snow: hit by it, playing in it, standing on it (plan 1.5, WP5).

Three parts. The model's side (``snow.SnowField``): the dent his feet press into a pile, the puff he
kicks up, how full a pile is. His side (``behavior.Behavior``, Qt-free): a jump and the snowcap for
a minute when snow lands on him, a hop or a dance when you brush snow near him, a shiver now and
then on a snowy window - and none of it when he is asleep, paused, held, hidden or focusing, or
over a costume you picked. Last, that a mascot the snow never reached rolls exactly the random
numbers he always did: that is what keeps ``fidget_rate`` and ``quirk_rate`` byte-identical.
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
import snow as S  # noqa: E402
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


# --- the model's side -----------------------------------------------------------------------

MON = (0, 0, 1920, 1080)
FLOOR = 1040
WIN = (300, 400, 1100, 900)


def field():
    f = S.SnowField(rng=random.Random(1))
    f.set_monitors([MON], [FLOOR])
    return f


def ledger_ok(f):
    lhs = (f.mass_in_caps + f.mass_in_air + f.ledger["floor"] + f.ledger["nib"]
           + f.ledger["faded"] + f.ledger["vanished"])
    return abs(lhs - f.ledger["grown"]) <= 1e-6 * max(1.0, f.ledger["grown"])


print("--- the dent under his feet ---")
f = field()
cap = f.add_cap(101, WIN, 0.0, fill=1.0)
SPAN = (600.0, 640.0)
under = [i for i in range(cap.n) if SPAN[0] <= cap.column_center(i) < SPAN[1]]
rim = [under[0] - 1, under[-1] + 1]
before = list(cap.heights)
v = cap.version
changed = f.set_dent(101, SPAN)
check("standing on a pile presses the snow under his feet down to DENT_PX",
      changed == {101} and all(cap.heights[i] <= S.DENT_PX + 1e-9 for i in under),
      str([round(cap.heights[i], 1) for i in under]))
check("...pushing it out into a rim either side", all(cap.heights[i] > before[i] for i in rim))
check("...and bumps the version, for the view to re-render", cap.version == v + 1)
check("...with every bit of snow accounted for", ledger_ok(f))
check("the same feet again: nothing changes (it is called every tick)",
      f.set_dent(101, SPAN) == set() and cap.version == v + 1)
check("a pile he is not on is left alone",
      f.set_dent(101, SPAN) == set() and 202 not in f.caps)

f = field()
cap = f.add_cap(101, WIN, 0.0)
f.set_dent(101, SPAN)
t = 0.0
while t < 40 * 60_000:
    t += 2000.0
    f.grow(t)
check("growth never fills the dent while he stands there",
      all(cap.heights[i] <= S.DENT_PX + 1e-9 for i in under)
      and cap.heights[under[0] - 10] >= cap.target[under[0] - 10] - 1e-9)
f.set_dent(0, None)
while t < 70 * 60_000:
    t += 2000.0
    f.grow(t)
check("once he has gone, it grows back", all(cap.heights[i] >= cap.target[i] - 1e-9
                                              for i in under))
check("...and the ledger still balances", ledger_ok(f))

f = field()
f.sync([(101, WIN, False, False)], 0.0)
f.caps[101].heights = list(f.caps[101].target)
f.ledger["grown"] += f.caps[101].mass
f.set_dent(101, SPAN)
moved = (WIN[0] + 50, WIN[1] + 30, WIN[2] + 50, WIN[3] + 30)
f.sync([(101, moved, False, False)], 1000.0)
check("a window that moves takes no dent with it", f.caps[101].dent is None)
new_span = (SPAN[0] + 50, SPAN[1] + 50)
check("...and the next tick presses the new pile (once it has snow)",
      f.set_dent(101, new_span) == set() and f.caps[101].dent == new_span)

print("\n--- how full a pile is ---")
f = field()
check("no pile: 0", f.fullness(101) == 0.0)
f.add_cap(101, WIN, 0.0, fill=1.0)
f.add_cap(202, (1200, 300, 1700, 600), 0.0, fill=0.2)
# Icicles count as snow too, so a pile with them is a little over its grown height's worth.
check("a full one: 1, and a little", 1.0 <= f.fullness(101) < 1.1, f"{f.fullness(101):.3f}")
check("a fifth of one: 0.2, and a little", 0.2 <= f.fullness(202) < 0.22,
      f"{f.fullness(202):.3f}")

print("\n--- the puff he kicks up ---")
f = field()
NIB = (900, 940, 996, 1040)                      # standing on the floor
f.set_nib(NIB)
made = f.puff(948.0, 1040.0, 0.0, half_width=40.0)   # from the line his soles stand on
check("a few lumps go up", made == S.PUFF_CHUNKS and f.airborne and all(c.vy < 0 for c in f.chunks))
check("...from beside him, not behind him (the flight layer is under his window)",
      all(abs(c.x - 948.0) >= 28.0 for c in f.chunks)
      and {c.x < 948.0 for c in f.chunks} == {True, False})
landings = []
t = 0.0
while f.airborne and t < 10_000:
    t += 33.0
    landings += f.step(t).landed
check("...and come down again, none of it on him",
      not f.airborne and landings and all(l.where != "nib" for l in landings),
      str(sorted({l.where for l in landings})))
check("...booked as new snow: the ledger balances", ledger_ok(f) and f.ledger["nib"] == 0.0)
g = field()
g.set_nib(NIB)
g.add_cap(101, (850, 700, 1100, 900), 0.0, fill=1.0)
g.release_span(101, 860.0, 1090.0, 0.0)
hit = []
t = 0.0
while g.airborne and t < 10_000:
    t += 33.0
    hit += [l for l in g.step(t).landed if l.where == "nib"]
check("(snow knocked off the window above him still does land on him)", bool(hit))
check("...marked as dropped, which he does not react to (the app reacts to brush, flick, throw)",
      all(l.mode == "drop" for l in hit) and "drop" not in B.SNOW_HIT_MODES)
g = field()
g.set_nib(NIB)
cap = g.add_cap(101, (850, 700, 1100, 900), 0.0, fill=1.0)
t = 0.0
for k in range(12):                              # a swipe along the pile at 2 px/ms
    t += 8.0
    g.pointer(860.0 + 16.0 * k, cap.top - 4.0, t)
landed = []
while g.airborne and t < 12_000:
    t += 33.0
    landed += g.step(t).landed
modes = {l.mode for l in landed}
check("snow you brush off is marked brushed, wherever it lands", modes == {"brush"}, str(modes))

# --- his side ---------------------------------------------------------------------------------

SHEET = SpriteSheet(SRC / "assets" / "nib_sheet.png", SRC / "assets" / "frames.json", 2)


def fresh(tag, **overrides):
    path = SCRATCH / f"snow_nib_{tag}.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    cfg["last_seen_date"] = "2026-07-31"
    cfg["seasonal_enabled"] = False
    cfg["quirks_enabled"] = False                # a sneeze due at the decision would go first
    for k, v in overrides.items():
        cfg[k] = v
    b = Behavior(cfg, SHEET)
    b.set_strip(1392, 0, 2560)
    b.place_at(1200)
    b.set_world(W.WorldState(hour=14.0, weekday=2, date_key="2026-07-31", week_key="2026-W31",
                             part_of_day=W.PART_DAY, season=W.SEASON_WINTER,
                             cpu_percent=8.0, cpu_busy=False))
    b._enter(State.IDLE)
    return cfg, b


def run(b, ms, step=50.0, until=None):
    t = 0.0
    while t < ms:
        b.update(step, None)
        t += step
        if until is not None and until():
            return True
    return until() if until is not None else None


def centre(b):
    x, y, w, h = b.rect
    return x + w / 2.0, y + h / 2.0


print("\n--- snow landing on him: a jump, and the snowcap for a minute ---")
check("the art is there: the jump, the snowcap, the shiver, the hop and the dance",
      all(SHEET.has_state(s) for s in (B.SNOW_HIT_ANIM, B.SNOW_SHIVER_ANIM, "hop", "dance"))
      and SHEET.has_costume(B.SNOW_HIT_COSTUME))
_cfg, b = fresh("hit")
check("he reacts", b.snow_landed())
check("...with the jump, the snowcap on for it",
      b.state == State.ANTIC and b._antic == B.SNOW_HIT_ANIM
      and b.persona_costume == B.SNOW_HIT_COSTUME, f"{b._antic} {b.persona_costume!r}")
hat = b._snow_hat_ms
check("a second lump of the same shower: no second jump, and the minute is not restarted",
      not b.snow_landed() and b._snow_hat_ms == hat)
run(b, 2000)
check("the jump is over, the hat stays on", b._antic != B.SNOW_HIT_ANIM
      and b._snow_hat_ms > 0 and (b._persona_costume or b.persona_costume == "snowcap"))
run(b, B.SNOW_HAT_MS - 4000)                     # 58 s after the hit
check("...for about a minute", b._snow_hat_ms > 0)
run(b, 3000)
check("...and then it comes off", b._snow_hat_ms == 0
      and b.persona_costume != B.SNOW_HIT_COSTUME, repr(b.persona_costume))
check("after the shower, the next one gets its jump", b.snow_landed())

_cfg, b = fresh("hit_mine", costume="tophat")
check("a costume you picked: he still jumps", b.snow_landed() and b._antic == B.SNOW_HIT_ANIM)
check("...but no snowcap, then or after", b.persona_costume == "" and b._snow_hat_ms == 0)
_cfg, b = fresh("hit_pick")
b.snow_landed()
run(b, 1500)
b.clear_persona_costume()                        # what the app does when you pick one
_cfg["costume"] = "tophat"
check("picking a costume during the minute takes the snowcap off at once",
      b.persona_costume == "" and b._snow_hat_ms == 0)
_cfg, b = fresh("hit_paused_hat")
b.snow_landed()
run(b, 1500)
b.paused = True
hat = b._snow_hat_ms
run(b, 5000)
check("paused, the minute is held", b._snow_hat_ms == hat)
b.paused = False

for label, setup in (
        ("paused", lambda b: setattr(b, "paused", True)),
        ("asleep (or napping)", lambda b: b._enter(State.SLEEP)),
        ("in your hand", lambda b: b.drag_start()),
        ("hidden (snoozed, the guard)", lambda b: b.set_hidden(True)),
        ("focusing", lambda b: b.set_focus(True)),
        ("you away", lambda b: b.set_away(True))):
    _cfg, b = fresh("hit_" + label.split()[0])
    setup(b)
    check(f"{label}: no reaction", not b.snow_landed() and b._snow_hat_ms == 0
          and b._antic != B.SNOW_HIT_ANIM)

print("\n--- snow brushed near him: once in a while, a hop or a dance ---")
_real_chance = B.SNOW_PLAY_CHANCE
B.SNOW_PLAY_CHANCE = 1.0                         # the chance is snow_rate's to measure
_cfg, b = fresh("play")
cx, cy = centre(b)
b.snow_nearby(cx - 2000, cy)
check("far away: nothing asked", b._snow_play_ms == 0)
b.snow_nearby(cx + 150, cy - 100)
check("near him: an ask to play is open", b._snow_play_ms > 0)
played = run(b, 1500, until=lambda: b._antic in ("hop", "dance"))
check("an idle mascot answers it within a moment, with a hop or a dance", played, str(b._antic))
check("...and the app is asked for a puff at his feet, once",
      b.take_snow_puff() and not b.take_snow_puff())
check("...through the shared moment cooldown", b._reaction_cooldown_ms > 0)
b._reaction_cooldown_ms = 0.0
b.snow_nearby(cx + 150, cy - 100)
check("more brushing in the same burst asks nothing more", b._snow_play_ms == 0)
run(b, B.SNOW_BURST_GAP_MS + 500)
b._reaction_cooldown_ms = 0.0
b.snow_nearby(cx + 150, cy - 100)
check("a new burst inside his own cooldown asks nothing either", b._snow_play_ms == 0)
_cfg, b = fresh("play_lapse")
b._reaction_cooldown_ms = 30_000.0               # he has just had a moment
b.snow_nearby(cx + 150, cy - 100)
run(b, B.SNOW_PLAY_WINDOW_MS + 1000)
check("an ask he cannot get to lapses: no hop half a minute later", b._snow_play_ms == 0)
b._reaction_cooldown_ms = 0.0
run(b, 60_000, until=lambda: b._antic in ("hop", "dance") and b.take_snow_puff())
check("...and never fires late", not b._snow_puff_due and b._snow_play_cooldown_ms == 0)
for label, setup in (("paused", lambda b: setattr(b, "paused", True)),
                     ("asleep", lambda b: b._enter(State.SLEEP)),
                     ("focusing", lambda b: b.set_focus(True))):
    _cfg, b = fresh("play_" + label)
    setup(b)
    b.snow_nearby(cx + 150, cy - 100)
    check(f"{label}: nothing asked", b._snow_play_ms == 0)
B.SNOW_PLAY_CHANCE = 0.0
_cfg, b = fresh("play_never")
b.snow_nearby(cx + 150, cy - 100)
run(b, 3000)
check("a roll that says no spends the ask quietly", b._snow_play_ms == 0
      and not b.take_snow_puff() and b._snow_play_cooldown_ms == 0)
B.SNOW_PLAY_CHANCE = _real_chance

print("\n--- standing on a snowy window: a shiver now and then ---")
WINDOW = (400, 300, 900, 700)
_cfg, b = fresh("shiver")
b.place_at(600)
b.perch_on(WINDOW)
feet = b.top
b.set_snowy_perch(True)
check("the shiver's clock is drawn the first time he stands on snow",
      b._shiver_due_ms is not None
      and B.SNOW_SHIVER_EVERY_MS[0] <= b._shiver_due_ms <= B.SNOW_SHIVER_EVERY_MS[1])
b._shiver_due_ms = 200.0
check("he shivers when it comes round",
      run(b, 1000, until=lambda: b._antic == B.SNOW_SHIVER_ANIM))
check("...standing on the window all the while", b.perched and b.top == feet)
states = []
run(b, 4000, until=lambda: states.append(b.state) or b.state == State.PERCH)
check("...and goes straight back to standing there, not via an idle beat",
      b.state == State.PERCH and State.IDLE not in states, str(sorted({s.name for s in states})))
b.update_perch((WINDOW[0] + 80, WINDOW[1] - 50, WINDOW[2] + 80, WINDOW[3] - 50))
check("...still following the window", b.top == feet - 50)
check("...through the shared moment cooldown", b._reaction_cooldown_ms > 0)
_cfg, b = fresh("shiver_bare")
b.place_at(600)
b.perch_on(WINDOW)
b.set_snowy_perch(True)
b.set_snowy_perch(False)
b._shiver_due_ms = 0.0
check("a window without snow: no shiver",
      not run(b, 5000, until=lambda: b._antic == B.SNOW_SHIVER_ANIM))
_cfg, b = fresh("shiver_off", antics_enabled=False)
b.place_at(600)
b.perch_on(WINDOW)
b.set_snowy_perch(True)
b._shiver_due_ms = 0.0
check("antics switched off: no shiver",
      not run(b, 5000, until=lambda: b._antic == B.SNOW_SHIVER_ANIM))

print("\n--- the snow draws nothing until it reaches him ---")


def day(touch):
    random.seed(4242)
    _cfg, b = fresh("same_" + str(touch), quirks_enabled=True, seasonal_enabled=True)
    seen = []
    for k in range(int(20 * 60_000 / 50)):
        if touch:
            x, y = centre(b)
            if k % 20 == 0:
                b.snow_nearby(x - 3000, y)       # brushing far across the screen
            b.set_snowy_perch(False)             # what the app says every tick he is not perched
            b.take_snow_puff()
        b.update(50.0, None)
        if b._antic and (not seen or seen[-1] != b._antic):
            seen.append(b._antic)
    return seen, random.random()


plain = day(False)
touched = day(True)
check("twenty minutes of him, with snow about but never near him: the same antics and the same "
      "random numbers", plain == touched and len(plain[0]) > 3, f"{len(plain[0])} antics")

print("\n" + "=" * 60)
print("FAILURES: " + ", ".join(fails) if fails else "snow and Nib OK")
sys.exit(1 if fails else 0)
