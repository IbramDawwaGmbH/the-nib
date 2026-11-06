"""Being ignored: the three stages, everything that resets them, and everything that suppresses them."""

import inspect
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
import needs as N  # noqa: E402
import world as W  # noqa: E402
from animator import SpriteSheet  # noqa: E402
from behavior import Behavior, State  # noqa: E402
from config import Config  # noqa: E402

SCRATCH = _scratch()
BASELINE = 1392
STEP_S = 240.0
fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


def world_at(busy=False):
    return W.WorldState(hour=14.0, weekday=4, date_key="2026-07-31", week_key="2026-W31",
                        part_of_day=W.PART_DAY, season=W.SEASON_SPRING,
                        cpu_percent=95.0 if busy else 8.0, cpu_busy=busy)


def fresh(tag, **overrides):
    path = SCRATCH / f"at_{tag}.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    cfg["last_seen_date"] = "2026-07-31"
    cfg["seasonal_enabled"] = False
    for k, v in overrides.items():
        cfg[k] = v
    sheet = SpriteSheet(SRC / "assets" / "nib_sheet.png", SRC / "assets" / "frames.json", 2)
    b = Behavior(cfg, sheet)
    b.set_strip(BASELINE, 0, 2560)
    b.place_at(1200)
    b.set_world(world_at())
    return cfg, b


def ignore_for(b, seconds, cursor=None):
    """Run the clock with nothing aimed at him. Returns the stages that fired, in order."""
    seen, elapsed = [], 0.0
    while elapsed < seconds * 1000.0:
        b.update(100.0, cursor)
        if b._neglect_stage and (not seen or seen[-1] != b._neglect_stage):
            seen.append(b._neglect_stage)
        elapsed += 100.0
    return seen


print("\n--- the three stages, in order, and then it stops ---")
# Quirks off: a sneeze or a trip is involuntary and deliberately still fires while he is ignored
# (see Behavior._tick_quirks), so one landing in the sample below would fail "does nothing else"
# at random. That check is about what he chooses to do, not about his reflexes.
_cfg, b = fresh("stages", quirks_enabled=False)
seen = ignore_for(b, STEP_S * 4.2)
check("all three stages fire", seen == [1, 2, 3], str(seen))
check("and it never goes further", b._neglect_stage == 3, str(b._neglect_stage))
# Sampled over a couple of seconds rather than at one instant: after each glance he is briefly
# idle before settling back down, so a single sample legitimately catches him between the two.
settled = set()
for _ in range(30):
    b.update(100.0, None)
    settled.add(b._antic)
check("he ends up sitting", B.NEGLECT_SIT_ANTIC in settled, str(sorted(map(str, settled))))
check("and does nothing else while he waits",
      settled <= {B.NEGLECT_SIT_ANTIC, B.NEGLECT_GLANCE_ANTIC, None},
      str(sorted(map(str, settled))))

_cfg, b = fresh("timing")
ignore_for(b, STEP_S * 0.9)
check("nothing before the first threshold", b._neglect_stage == 0, str(b._neglect_stage))
ignore_for(b, STEP_S * 0.3)
check("stage 1 at ~4 min", b._neglect_stage == 1, str(b._neglect_stage))
ignore_for(b, STEP_S)
check("stage 2 at ~8 min", b._neglect_stage == 2, str(b._neglect_stage))
ignore_for(b, STEP_S * 1.7)
check("stage 3 at ~14 min", b._neglect_stage == 3, str(b._neglect_stage))

print("\n--- stage 1 is invisible; it only changes the weights ---")
_cfg, b = fresh("weights")
plain = b._mood_weights().get("wink", 1.0)
ignore_for(b, STEP_S * 1.2)
boosted = b._mood_weights().get("wink", 1.0)
check("attention-seeking antics get weighted up", boosted > plain, f"{plain} -> {boosted}")
check("...and the activity ones do not",
      b._mood_weights().get("pushups", 1.0) == 1.0, str(b._mood_weights().get("pushups")))

print("\n--- stage 3 keeps glancing up, so it is not the away-sit ---")
_cfg, b = fresh("glance")
ignore_for(b, STEP_S * 3.6)
glances = 0
for _ in range(int(B.NEGLECT_GLANCE_EVERY_MS * 3 / 100)):
    b.update(100.0, None)
    if b._antic == B.NEGLECT_GLANCE_ANTIC:
        glances += 1
        b._antic_ms = 0.0     # end it early so the next one can be counted
check("he glances up while waiting", glances >= 1, f"{glances} glances")

print("\n--- everything that resets it ---")
def reset_by(tag, action, **overrides):
    _cfg, b = fresh(tag, **overrides)
    ignore_for(b, STEP_S * 1.2)
    before = b._neglect_stage
    action(b)
    return before, b._neglect_stage

for label, action in (
    ("click", lambda b: b.click()),
    ("drag", lambda b: b.drag_start()),
    ("a prompt sent", lambda b: b.prompt_sent()),
    ("the bubble dismissed", lambda b: b.bubble_dismissed()),
    ("the hotkey / tray (noticed)", lambda b: b.noticed()),
    ("coming back from away", lambda b: (b.set_away(True), b.set_away(False))),
):
    before, after = reset_by(label.replace(" ", "_").replace("/", ""), action)
    check(f"{label} resets him", before >= 1 and after == 0, f"{before} -> {after}")

_cfg, b = fresh("wave")
ignore_for(b, STEP_S * 1.2)
before = b._neglect_stage
b._wave_cooldown_ms = 0.0
# Driven through update() rather than by calling _maybe_wave directly, because the reset no
# longer lives there: the wave now stands aside for a pending pet, so a pointer landing ON him
# would have deferred the wave and reset nothing. It is handled in _track_cursor instead, which
# only update() reaches - and which is the path a real pointer takes anyway.
# He wanders while being ignored, so aim at where he actually is rather than where he started.
b.update(50.0, (int(b._x + b.sprite_w / 2), b.top + b.sprite_h // 2))
check("your cursor coming to him resets it", before >= 1 and b._neglect_stage == 0,
      f"{before} -> {b._neglect_stage}")

# ...and from a distance, where it really is the wave that does it.
_cfg, b = fresh("wave_near")
ignore_for(b, STEP_S * 1.2)
before = b._neglect_stage
b._wave_cooldown_ms = 0.0
# Over a window rather than a single tick: the wave fires from _tick_idle and _tick_walk, so if
# this lands while he happens to be mid-antic the check never reaches the code it is testing.
# Sampling one tick made this fail about one run in six; a 3s window still failed about one in
# fifty, because football, dance, press-ups and sit_dangle can all run past 3s (up to 4.4s).
# 10s outlasts any antic he can be in (even the 8s Friday flop) with the wave still to come.
for _ in range(200):
    b.update(50.0, (int(b._x + b.sprite_w + 40), b.top + b.sprite_h // 2))
    if b._neglect_stage == 0:
        break
check("...and a cursor merely coming near him does too", before >= 1 and b._neglect_stage == 0,
      f"{before} -> {b._neglect_stage}")

print("\n--- everything that suppresses it ---")
# SLEEP lasts sleep_cooldown_s and ANGRY only 2.5s, so those two are checked over a window the
# state actually outlasts - the point is that the clock does not tick *while he is in them*.
for label, setup, window_s in (
    ("away", lambda b: b.set_away(True), STEP_S * 4),
    ("paused", lambda b: setattr(b, "paused", True), STEP_S * 4),
    ("asleep", lambda b: b._enter(State.SLEEP), 30.0),
    ("cross", lambda b: b._enter(State.ANGRY), 2.0),
    ("hidden", lambda b: b.set_hidden(True), STEP_S * 4),
    ("being dragged", lambda b: b.drag_start(), STEP_S * 4),
    ("machine pinned", lambda b: b.set_world(world_at(busy=True)), STEP_S * 4),
):
    _cfg, b = fresh(f"sup_{label.replace(' ', '_')}")
    setup(b)
    b.noticed()
    for _ in range(int(window_s * 10)):
        b.update(100.0, None)
    check(f"nothing accumulates while {label}", b._neglect_stage == 0 and b._neglect_ms == 0.0,
          f"stage={b._neglect_stage} ms={b._neglect_ms:.0f}")

_cfg, b = fresh("off", attention_enabled=False)
ignore_for(b, STEP_S * 5)
check("attention_enabled=false switches it off entirely", b._neglect_stage == 0)

_cfg, b = fresh("zero", attention_after_s=0)
ignore_for(b, STEP_S * 5)
check("attention_after_s=0 switches it off too", b._neglect_stage == 0)

# Stages fire at the next decision point *after* the threshold, not exactly on it - he finishes
# whatever he was doing first - so this allows a few seconds of slack.
_cfg, b = fresh("short", attention_after_s=60)
ignore_for(b, 55.0)
early = b._neglect_stage
ignore_for(b, 30.0)
check("attention_after_s is honoured", early == 0 and b._neglect_stage == 1,
      f"{early} at 55s -> {b._neglect_stage} at 85s")

print("\n--- coming back: the greeting is sized by how long you were gone ---")
for label, gone_s, expected in (
    ("no length given (the old hop)", None, B.GREET_ANTIC),
    ("five minutes", 300.0, B.GREET_WAVE_ANTIC),
    ("an hour", 3600.0, B.GREET_ANTIC),
):
    _cfg, b = fresh(f"greet_{int(gone_s or 0)}")
    b.set_away(True)
    b.set_away(False, gone_s=gone_s)
    check(f"{label}: {expected}", b.state is State.ANTIC and b._antic == expected, str(b._antic))

cfg, b = fresh("greet_night")
cfg["last_seen_date"] = "2026-07-30"     # today's first sight of you is still to come
b.set_away(True)
b.set_away(False, gone_s=8 * 3600.0)
check("a night away: a stretch first", b._antic == B.GREET_OVERNIGHT[0][0], str(b._antic))
check("...then a yawn and the hop, queued",
      [name for name, _ms in b._antic_queue] == [s[0] for s in B.GREET_OVERNIGHT[1:]],
      str(b._antic_queue))
played = [b._antic]
for _ in range(400):
    b.update(50.0, None)
    if b.state is not State.ANTIC:
        break
    if b._antic != played[-1]:
        played.append(b._antic)
check("...played in that order, then back to idle",
      played == [s[0] for s in B.GREET_OVERNIGHT] and b.state is not State.ANTIC,
      f"{played} then {b.state.value}")
check("...and it counts as today's first sight, so no second stretch",
      cfg["last_seen_date"] == "2026-07-31" and (b._pick_moment() or ("",))[0] != "stretch",
      cfg["last_seen_date"])

_cfg, b = fresh("greet_grabbed")
b.set_away(True)
b.set_away(False, gone_s=8 * 3600.0)
b.drag_start()
check("grabbing him mid-greeting drops the rest of it", b._antic_queue == [], str(b._antic_queue))

_cfg, b = fresh("away_no_glance")
b.set_away(True)
glanced = False
for _ in range(int(STEP_S * 4 * 10)):
    b.update(100.0, None)
    glanced = glanced or b._antic == B.NEGLECT_GLANCE_ANTIC
check("the away-sit still never glances up - that is the ignored sit", not glanced
      and b._antic == B.AWAY_ANTIC, str(b._antic))

print("\n--- back from a snooze or a fullscreen app: a wave ---")


def hidden_for(b, seconds, *, greet=True):
    b.set_hidden(True)
    for _ in range(int(seconds * 4)):
        b.update(250.0, None)   # the app's idle tick while he is hidden
    b.set_hidden(False, greet=greet)


_cfg, b = fresh("back")
b._neglect_stage = 1
hidden_for(b, 30.0)
check("half a minute hidden, then back: he waves", b._antic == B.BACK_ANTIC, str(b._antic))
check("...without counting as you noticing him", b._neglect_stage == 1, str(b._neglect_stage))
for _ in range(40):
    b.update(50.0, None)
hidden_for(b, 30.0)
check("...but a second return inside the minute does not", b._antic != B.BACK_ANTIC,
      f"{b.state.value} {b._antic}")

_cfg, b = fresh("back_quiet")
hidden_for(b, 30.0, greet=False)
check("a return that does not ask for it (the guard) gets nothing", b.state is State.IDLE,
      b.state.value)

_cfg, b = fresh("back_flicker")
hidden_for(b, 5.0)
check("a five-second flicker of fullscreen gets nothing", b.state is State.IDLE, b.state.value)

_cfg, b = fresh("back_perch")
b._enter(State.PERCH)
hidden_for(b, 30.0)
check("back onto a perch: no wave to pull him off it", b.state is State.PERCH, b.state.value)

_cfg, b = fresh("back_away")
b.set_away(True)
hidden_for(b, 30.0)
check("back while you are still away: no wave", b._antic != B.BACK_ANTIC, str(b._antic))

_cfg, b = fresh("back_paused")
b.paused = True
hidden_for(b, 30.0)
check("back while paused: no wave - it would freeze on its first frame", b.state is State.IDLE,
      f"{b.state.value} {b._antic}")

print("\n--- a launch that failed gets a double-take, not \"on it\" ---")
_cfg, b = fresh("launch_failed")
b._register_poke()
pokes = len(b._pokes)
b._neglect_stage = 1
b._enter(State.ASK)          # where a bubble submit leaves him
b._post_prompt = ("wink", 900.0, "")
b.take_sounds()
b.launch_failed()
check("he does a double-take", b._antic == B.LAUNCH_FAILED_ANIM, str(b._antic))
seen_working = False
for _ in range(60):
    b.update(50.0, None)
    seen_working = seen_working or b.state is State.WORKING
check("...and never plays the working animation", not seen_working)
check("...or a reaction to a prompt that went nowhere", b._post_prompt is None)
check("...with no send sound", "send" not in b.take_sounds())
check("...and it is not a poke, nor does it take the edge off", len(b._pokes) == pokes,
      f"{pokes} -> {len(b._pokes)}")
check("...but it does count as you noticing him", b._neglect_stage == 0, str(b._neglect_stage))

_cfg, b = fresh("launch_failed_paused")
b._enter(State.ASK)
b.paused = True
b.launch_failed()
check("paused, no double-take, but the bubble's ASK still ends", b.state is State.IDLE,
      f"{b.state.value} {b._antic}")

print("\n--- worn out, late, and you have gone: he naps ---")


def night_at(hour=23.5):
    return W.WorldState(hour=hour, weekday=4, date_key="2026-07-31", week_key="2026-W31",
                        part_of_day=W.part_of_day(hour), season=W.SEASON_SPRING,
                        cpu_percent=8.0, cpu_busy=False)


def napper(tag, *, hour=23.5, energy=0.1, **overrides):
    """Worn out at half eleven at night. Quirks off: a sneeze is the one thing that could knock
    him out of the away-sit he naps from, and it would land at random. Away is set by hand
    through set_away - never the machine's own idle clock, which an unattended runner fills."""
    cfg, b = fresh(tag, need_energy=energy, quirks_enabled=False, **overrides)
    b.set_world(night_at(hour))
    return cfg, b


def wait(b, seconds, cursor=None):
    for _ in range(int(seconds * 4)):
        b.update(250.0, cursor)


def until(b, done, seconds=5.0, step=16.0):
    """Tick until ``done(b)``, for at most ``seconds`` - longer than WAKE and a landing take."""
    for _ in range(int(seconds * 1000 / step)):
        if done(b):
            return True
        b.update(step, None)
    return done(b)


def napping_after_away(tag, **kw):
    cfg, b = napper(tag, **kw)
    b.set_away(True)
    wait(b, 11 * 60)
    return cfg, b


cfg, b = napper("nap")
b.set_away(True)
wait(b, 9.5 * 60)
check("nine and a half minutes away he is still sitting up for you",
      b.state is State.ANTIC and b._antic == B.AWAY_ANTIC and not b.napping,
      f"{b.state.value} {b._antic}")
wait(b, 90.0)
check("eleven minutes away at half eleven, worn out (energy 0.1): asleep, and napping",
      b.state is State.SLEEP and b.napping, f"{b.state.value} napping={b.napping}")
check("...the same SLEEP as the sulk, not a new state", b.animator.state == "sleep",
      b.animator.state)
near = (int(b._x + b.sprite_w + 40), b.top + b.sprite_h // 2)
wait(b, 2 * cfg["sleep_cooldown_s"], near)
check("a pointer hovering by him for twice the sulk's length does not wake him, nor any timer",
      b.napping, f"{b.state.value}")
b.take_events()
rested = b.needs.energy
napped_h = (b._clock_ms - b._nap_since_ms) / 3_600_000.0
b.set_away(False, gone_s=700.0)
check("coming back wakes him: WAKE, and the nap is over",
      b.state is State.WAKE and not b._napping, b.state.value)
check("...with no grumble", b._grumble_ms == 0 and b.animator.state == "wake", b.animator.state)
# A few minutes' nap, so a share of nap_woke - well clear of the energy cap - and the sum exact:
# below neutral, a nudge back up lands in full (Needs.nudge).
gain, owed = b.needs.energy - rested, N.NEED_NUDGES["nap_woke"][0] * min(1.0, napped_h / 2.0)
check("...rested for the nap in proportion to its length (nap_woke, whole at two hours)",
      0 < gain < 0.05 and abs(gain - owed) < 1e-3,
      f"{napped_h * 60:.1f} min: {rested:.4f} -> {b.needs.energy:.4f}, owed {owed:.4f}")
check("...and nothing to say yet - he is still getting up", b.take_events() == [])
until(b, lambda x: x.state is not State.WAKE)
check("...then, on his feet, the greeting the absence earned",
      b.state is State.ANTIC and b._antic == B.GREET_ANTIC, f"{b.state.value} {b._antic}")
events = b.take_events()
check("...with the nap's line raised ahead of the greeting's",
      [kind for kind, _data in events] == ["nap_wake", "greeted"], str(events))
check("...and the greeting sized by how long you were gone", events[-1][1].get("gone_s") == 700.0,
      str(events[-1:]))

cfg, b = napping_after_away("nap_overnight")
b.set_away(False, gone_s=8 * 3600.0)
until(b, lambda x: x.state is not State.WAKE)
events = dict(b.take_events())
check("back after a night away: up from the nap into the overnight greeting, stretch first",
      b._antic == B.GREET_OVERNIGHT[0][0] and events.get("greeted", {}).get("overnight") is True,
      f"{b.state.value} {b._antic} {events}")

# The sulk's sleep, by contrast, ends on its clock and early to a hovering pointer - so the hover
# above really did meet a sleep that would have woken for it.
cfg, b = fresh("sulk_hover", quirks_enabled=False)
b._enter(State.SLEEP)
wait(b, cfg["sleep_cooldown_s"] * 0.9, (int(b._x + b.sprite_w + 40), b.top + b.sprite_h // 2))
check("(an ordinary sleep does wake for that hover)", b.state is not State.SLEEP, b.state.value)

cfg, b = napping_after_away("nap_click")
opened = b.click()
check("a click wakes him from a nap", b.state is State.WAKE and not b._napping, b.state.value)
check("...with no grumble and no bubble", b._grumble_ms == 0 and not opened
      and b.animator.state == "wake", f"grumble={b._grumble_ms} opened={opened}")
b.click()
check("...nor a scowl for a second click while he gets up", b._grumble_ms == 0,
      str(b._grumble_ms))
b.set_away(False, gone_s=700.0)  # the app sees you back a poll later, while he is getting up
until(b, lambda x: x.state is not State.WAKE)
check("...and once he is up, the greeting", b.state is State.ANTIC
      and b._antic == B.GREET_ANTIC, f"{b.state.value} {b._antic}")

cfg, b = napping_after_away("nap_wheel")
b.wheel_opened()
check("a right-click wakes him from a nap too, with no grumble - the wheel still opens",
      b.state is State.WAKE and not b._napping and b._grumble_ms == 0,
      f"{b.state.value} napping={b._napping} grumble={b._grumble_ms}")

cfg, b = napping_after_away("nap_click_away")
b.click()
wait(b, 60.0)
check("clicked awake while you still read as away, he does not nod straight off again",
      not b.napping and b._antic == B.AWAY_ANTIC, f"{b.state.value} {b._antic}")

cfg, b = napper("sulk", annoyance_threshold=1)
b.bubble_dismissed()
until(b, lambda x: x.state is State.SLEEP, seconds=20.0)
check("a sulk at the same hour, just as worn out, is asleep but not napping",
      b.state is State.SLEEP and not b.napping, f"{b.state.value} napping={b.napping}")
b.click()
check("...still grumbles at a click, exactly as before", b._grumble_ms == B.GRUMBLE_MS
      and b.state is State.SLEEP, f"{b._grumble_ms} {b.state.value}")
wait(b, 1.0)  # the grumble passes
b.wheel_opened()
check("...and at a right-click", b._grumble_ms == B.GRUMBLE_MS and b.state is State.SLEEP,
      f"{b._grumble_ms} {b.state.value}")
wait(b, cfg["sleep_cooldown_s"] + 1.0)
check("...and still ends on its own clock", b.state is not State.SLEEP, b.state.value)

PERCH_RECT = (900, 1100, 1700, 1300)
for label, overrides, setup in (
    ("with nap_enabled off", {"nap_enabled": False}, None),
    ("with needs switched off", {"needs_enabled": False}, None),
    ("in the day", {"hour": 14.0}, None),
    ("at energy 0.3", {"energy": 0.3}, None),
    ("in a focus session", {}, lambda b: b.set_focus(True)),
    ("standing on a window", {}, lambda b: b.perch_on(PERCH_RECT)),
):
    cfg, b = napper("nonap_" + label.replace(" ", "_"), **overrides)
    if setup is not None:
        setup(b)
    b.set_away(True)
    wait(b, 11 * 60)
    # Still in the pose he naps from, so it is the one thing changed that kept him awake.
    check(f"no nap {label}", not b.napping and b.state is State.ANTIC
          and b._antic == B.AWAY_ANTIC, f"{b.state.value} {b._antic}")
check("...and on the window he is still standing on it", b.perched)

cfg, b = napping_after_away("nap_throw")
lift_y = int(b.rest_top()) - 150
b.drag_start()
check("picked up mid-nap, the nap goes with him", b._napping and b.state is State.DRAG,
      b.state.value)
for i in range(10):
    b.drag_to(1200 + i * 40, lift_y)
    b.update(16.0, None)
b.drag_end()
until(b, lambda x: not (x._falling or x._rolling))
check("thrown mid-nap he lands back in it, unhurt", b.napping, f"{b.state.value}")
wait(b, 2 * cfg["sleep_cooldown_s"])
check("...still with no clock on it, the throw has not made it a sulk", b.napping,
      b.state.value)

cfg, b = napping_after_away("nap_carried")
b.drag_start()
for _ in range(5):
    b.drag_to(1200, int(b.rest_top()) - 30)
    b.update(16.0, None)
b.set_away(False, gone_s=900.0)  # you are back while he is in your hand
check("you coming back cannot wake him in your hand", b._napping and b.state is State.DRAG,
      b.state.value)
b.drag_end()
until(b, lambda x: x.state is State.ANTIC)
check("...so he wakes as he lands, and greets you - not left asleep with you sat there",
      not b._napping and b._antic == B.GREET_ANTIC, f"{b.state.value} {b._antic}")

# Set down on a window instead, the nap is over - and the cooldown drag_start owed a carried sleep
# must go with it, or the next ordinary landing puts him to sleep for a sulk's length.
cfg, b = napping_after_away("nap_perched")
b.drag_start()
for _ in range(5):
    b.drag_to(1200, int(b.rest_top()) - 300)  # held still, so let go slowly enough to perch
    b.update(16.0, None)
b.drag_end(PERCH_RECT)
check("set down asleep on a window, he wakes standing on it",
      b.state is State.PERCH and not b._napping, b.state.value)
check("...owing no sleep for later", b._resume_sleep_ms == 0, str(b._resume_sleep_ms))
b.set_away(False, gone_s=700.0)
for _ in range(int(10_000 / 16)):
    if not b.perched and not (b._falling or b._rolling):
        break
    b.update_perch(None)  # the window has closed, as main.py's poll would say
    b.update(16.0, None)
check("...so when the window goes and he steps down, he lands awake",
      not b.perched and b.state is not State.SLEEP and not b._napping, b.state.value)

# The sulk's twin: drag_start owes a carried sulk the rest of its cooldown too, and every end of
# the carry but a drop must drop it - or a landing minutes later is a sulk-length sleep, with the
# gentle set-down's sparkle and a hard landing's hurt both lost to it.
for route, how in (("window", "set down on a window"), ("hidden", "hidden"),
                   ("focus", "a focus session started")):
    cfg, b = fresh(f"sulk_carried_{route}", annoyance_threshold=1, quirks_enabled=False)
    b.bubble_dismissed()
    sulked = until(b, lambda x: x.state is State.SLEEP, seconds=20.0)
    b.drag_start()
    for _ in range(5):
        b.drag_to(1200, int(b.rest_top()) - 300)
        b.update(16.0, None)
    if route == "window":
        b.drag_end(PERCH_RECT)
    elif route == "hidden":
        b.set_hidden(True)
        b.set_hidden(False)
    else:
        b.set_focus(True)
    check(f"carried mid-sulk, then {how}, he owes no sleep for later",
          sulked and b.state is not State.SLEEP and b._resume_sleep_ms == 0,
          f"sulked={sulked} {b.state.value} {b._resume_sleep_ms}")
    if route == "window":
        wait(b, 60.0)
        for _ in range(int(10_000 / 16)):
            if not b.perched and not (b._falling or b._rolling):
                break
            b.update_perch(None)
            b.update(16.0, None)
        check("...so when the window goes and he steps down, he lands awake",
              not b.perched and b.state is not State.SLEEP, b.state.value)

# The greeting a nap owes lasts through the WAKE after it and no further: picked up mid-wake it
# is dropped, and a later sulk's wake-up must not find it and greet you for nothing.
cfg, b = napping_after_away("nap_greeting_dropped")
b.set_away(False, gone_s=700.0)
b.drag_start()
check("picked up while he gets up from a nap, the greeting it owed is dropped",
      b._nap_greeting is None, str(b._nap_greeting))
b.drag_end()
until(b, lambda x: not (x._falling or x._rolling))
b.take_events()
cfg["annoyance_threshold"] = 1
b.bubble_dismissed()
sulked = until(b, lambda x: x.state is State.SLEEP and not x._napping, seconds=20.0)
wait(b, cfg["sleep_cooldown_s"] + 1.0)
until(b, lambda x: x.state is not State.WAKE)
kinds = [kind for kind, _data in b.take_events()]
check("...so a sulk later wakes to no stale nap greeting", sulked and "nap_wake" not in kinds
      and "greeted" not in kinds and b._nap_greeting is None, f"sulked={sulked} {kinds}")

cfg, b = napping_after_away("nap_hidden")
b.set_hidden(True)
wait(b, 30.0)
b.set_hidden(False, greet=True)
check("hidden mid-nap (fullscreen, the snooze, the guard), he comes back awake",
      not b._napping and b.state is not State.SLEEP, b.state.value)

cfg, b = napper("nap_world_off", world_enabled=False)
b.set_away(True)
wait(b, 11 * 60)
check("with world awareness off there is no night, so no nap", not b.napping, b.state.value)

# Exactly how worn out he has to be: energy rests at +0.5 an hour while you are away, so at the
# ten-minute mark it must still be under 0.25. The edge, measured - BEHAVIOUR.md quotes it.
edge = []
for start in (0.16, 0.17):
    cfg, b = napper(f"nap_edge_{int(start * 100)}", energy=start)
    b.set_away(True)
    wait(b, 20 * 60)
    edge.append(b.napping)
check("left at energy 0.16 he naps; at 0.17 he never does", edge == [True, False], str(edge))

print("\n--- the reset list is complete in main.py too ---")
import main as M  # noqa: E402
for fn in ("_on_hotkey", "_on_context_menu", "_on_double_clicked",
           "_on_files_dropped", "_on_text_dropped"):
    src = inspect.getsource(getattr(M.NibApp, fn))
    check(f"main.{fn} calls noticed()", "noticed()" in src)

check("main measures how long you were away and tells him",
      "gone_s" in inspect.getsource(M.NibApp._update_presence))
for fn in ("_wake_from_snooze", "_update_fullscreen"):
    check(f"main.{fn} asks for the back wave",
          "greet=True" in inspect.getsource(getattr(M.NibApp, fn)))
check("...and the guard's return does not",
      "greet=True" not in inspect.getsource(M.NibApp._set_guarding))
_send = inspect.getsource(M.NibApp._send)
check("main._send only plays \"on it\" when the launch went through",
      "if ok" in _send and "launch_failed" in _send and _send.index("if ok") < _send.index("react()"))

print("\n" + "=" * 60)
print("FAILURES: " + ", ".join(fails) if fails else "attention arc OK")
sys.exit(1 if fails else 0)
