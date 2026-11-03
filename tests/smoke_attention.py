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
_cfg, b = fresh("stages")
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
# Sampling one tick made this fail about one run in six.
for _ in range(60):
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
