"""A treat: he eats it, it counts, three a day, and the places a biscuit cannot be handed over.

Builds a real ``Behavior`` (which needs a sprite sheet, so a ``QApplication``), the way
smoke_attention does. Every treat here is given by hand - nothing about treats is scheduled - so
the only randomness that could get in the way is a sneeze or a trip from idle, and quirks are off
throughout. The tray and wheel wiring at the end is read off the source, as smoke_wheel does, plus
the tray's real menu, triggered.
"""

import inspect
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import scratch as _scratch  # noqa: E402

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

from PySide6.QtCore import QPoint, QRect  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

import behavior as B  # noqa: E402
import sound  # noqa: E402
import world as W  # noqa: E402
from animator import SpriteSheet  # noqa: E402
from behavior import Behavior, State  # noqa: E402
from config import Config  # noqa: E402
from memory import Memory  # noqa: E402

SCRATCH = _scratch()
SHEET = SRC / "assets" / "nib_sheet.png"
FRAMES = SRC / "assets" / "frames.json"
BASELINE = 1392
TODAY = "2026-07-31"
fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


def world_at(hour=14.0, date_key=TODAY):
    return W.WorldState(hour=hour, weekday=4, date_key=date_key, week_key="2026-W31",
                        part_of_day=W.part_of_day(hour), season=W.SEASON_SPRING,
                        cpu_percent=8.0, cpu_busy=False)


def fresh(tag, *, frames=FRAMES, memory=None, hour=14.0, **overrides):
    """A Behavior on the taskbar, mid-afternoon, quirks off (see the module docstring)."""
    path = SCRATCH / f"treat_{tag}.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    cfg["last_seen_date"] = TODAY
    cfg["seasonal_enabled"] = False
    cfg["quirks_enabled"] = False
    for k, v in overrides.items():
        cfg[k] = v
    b = Behavior(cfg, SpriteSheet(SHEET, frames, 2), memory=memory)
    b.set_strip(BASELINE, 0, 2560)
    b.place_at(1200)
    b.set_world(world_at(hour))
    return cfg, b


def wait(b, seconds, cursor=None):
    for _ in range(int(seconds * 4)):
        b.update(250.0, cursor)


def treats(b, key=TODAY):
    return b.memory.day(key).treats


def needs_of(b):
    return (b.needs.energy, b.needs.affection, b.needs.boredom)


print("--- a treat: he eats it, and it counts ---")
cfg, b = fresh("eat")
spirits, (energy, affection, _bored) = b.mood.spirits, needs_of(b)
took = b.treat()
check("he takes it", took is True)
check("...and eats it: ANTIC, playing eat", b.state is State.ANTIC and b._antic == B.EAT_ANIM
      and b.animator.state == B.EAT_ANIM, f"{b.state.value} {b._antic} {b.animator.state}")
check("today's treats go to 1", treats(b) == 1, treats(b))
check("...and the lifetime total with it", b.memory.totals["treats"] == 1, b.memory.totals)
check("his mood rose (mood 'treated')", b.mood.spirits > spirits, f"{spirits} -> {b.mood.spirits}")
check("...by mood.NUDGES['treated'] from neutral", abs(b.mood.spirits - 0.12) < 1e-9,
      b.mood.spirits)
check("affection rose (needs 'treated')", b.needs.affection > affection,
      f"{affection} -> {b.needs.affection}")
check("...and energy too", b.needs.energy > energy, f"{energy} -> {b.needs.energy}")
check("it makes the nom noise", b.take_sounds() == ["nom"])
check("...and raises a treat event, for a line", b.take_events() == [("treat", {})])

seen, ended = set(), False
for _ in range(int(4000 / 16)):  # 1.4 s of eating at any pace he can be at in the afternoon
    if b.state is State.ANTIC and b._antic == B.EAT_ANIM:
        seen.add(b.animator.frame_index)
    else:
        ended = True
        break
    b.update(16.0, None)
check("it plays all four frames once", seen == {0, 1, 2, 3}, sorted(seen))
check("...and ends by itself", ended, f"{b.state.value} {b._antic}")

print("\n--- three a day; the fourth finds him full ---")
b.treat()
for _ in range(int(1000 / 16)):  # about a second into the second treat: past the biscuit
    b.update(16.0, None)
check("set up: a second treat, a second in, still being eaten", b._antic == B.EAT_ANIM
      and b.animator.frame_index > 0, f"{b._antic} frame {b.animator.frame_index}")
b.treat()
check("a third from the tray while he is still eating: from the first frame again",
      b._antic == B.EAT_ANIM and b.animator.frame_index == 0, f"frame {b.animator.frame_index}")
seen = set()
for _ in range(int(4000 / 16)):
    if not (b.state is State.ANTIC and b._antic == B.EAT_ANIM):
        break
    seen.add(b.animator.frame_index)
    b.update(16.0, None)
check("...and all four frames play again", seen == {0, 1, 2, 3}, sorted(seen))
check("three treats, three counted", treats(b) == 3, treats(b))
b.take_sounds()
b.take_events()
spirits, held = b.mood.spirits, needs_of(b)
took = b.treat()
check("the fourth is still taken - as a polite no", took is True)
check("...he sits down with it (sit_dangle)", b.state is State.ANTIC
      and b._antic == B.TREAT_FULL_ANIM, f"{b.state.value} {b._antic}")
check("...not counted", treats(b) == 3 and b.memory.totals["treats"] == 3, treats(b))
check("...no mood change", b.mood.spirits == spirits, f"{spirits} -> {b.mood.spirits}")
# Offering one at all is still noticing him (Behavior.noticed), and that knocks boredom down like
# any other interaction; what must not land is needs' "treated" - the energy and the affection.
check("...no needs change from the treat", needs_of(b)[:2] == held[:2]
      and needs_of(b)[2] <= held[2], f"{held} -> {needs_of(b)}")
check("...no nom", b.take_sounds() == [])
check("...and a treat_full event instead", b.take_events() == [("treat_full", {})])
b.set_world(world_at(date_key="2026-08-01"))
check("a new day, and he has room again", b.treat() and b._antic == B.EAT_ANIM
      and treats(b, "2026-08-01") == 1, f"{b._antic} {treats(b, '2026-08-01')}")

print("\n--- the cap holds with memory switched off ---")
cfg, b = fresh("memory_off", memory_enabled=False)
eaten = [b.treat() and b._antic for _ in range(3)]
check("three treats, all eaten", eaten == [B.EAT_ANIM] * 3, eaten)
check("...none of them written to memory", treats(b) == 0 and b.memory.is_empty, treats(b))
spirits = b.mood.spirits
check("the fourth still finds him full", b.treat() and b._antic == B.TREAT_FULL_ANIM, b._antic)
check("...and changes nothing", b.mood.spirits == spirits)
b.memory_forgotten()
check("Forget everything starts today's count again, as it does the other day counts",
      b.treat() and b._antic == B.EAT_ANIM, b._antic)

print("\n--- ...and memory's count survives a restart ---")
mem_cfg = Config(SCRATCH / "treat_restart_mem.json")
remembered = Memory(None, mem_cfg)
remembered.record("treats", TODAY, 3)
cfg, b = fresh("restart", memory=remembered)
check("three already today, from before a restart: full straight away",
      b.treat() and b._antic == B.TREAT_FULL_ANIM, b._antic)

print("\n--- where a biscuit cannot be handed over ---")
cfg, b = fresh("drag")
b.drag_start()
check("in your hand: refused", b.treat() is False and b.state is State.DRAG, b.state.value)
check("...and nothing counted", treats(b) == 0 and b.mood.spirits == 0.0)

cfg, b = fresh("hidden")
b.set_hidden(True)
check("hidden: refused", b.treat() is False and b.state is State.HIDDEN, b.state.value)
check("...nothing counted", treats(b) == 0)

cfg, b = fresh("falling")
b.drag_start()
for _ in range(3):
    b.drag_to(1200, 300)
    b.update(50.0, None)
b.drag_end()
check("set up: let go high up, he is falling", b._falling, f"falling={b._falling}")
check("mid-air: refused", b.treat() is False and treats(b) == 0, b.state.value)
for _ in range(int(6000 / 16)):  # well past the longest fall and roll the strip allows
    if not (b._falling or b._rolling):
        break
    b.update(16.0, None)
check("...and taken once he is down - on his head from that height, which a biscuit cuts short",
      not (b._falling or b._rolling) and b.state is State.INJURED and b.treat()
      and b._antic == B.EAT_ANIM, f"falling={b._falling} rolling={b._rolling} {b.state.value}")

cfg, b = fresh("rolling", injury_enabled=False)
b.drag_start()
for i in range(10):  # a low, fast flick along the strip, as smoke_throw's roll-to-a-stop
    b.drag_to(600 + i * 60, BASELINE - 120)
    b.update(16.0, None)
b.drag_end()
for _ in range(int(6000 / 16)):
    if b._rolling and not b._falling:
        break
    b.update(16.0, None)
check("set up: down from a flick, and rolling along the ground", b._rolling and not b._falling,
      f"falling={b._falling} rolling={b._rolling}")
check("rolling: refused", b.treat() is False and treats(b) == 0, b.state.value)

cfg, b = fresh("paused")
b.paused = True
check("paused: refused - he would freeze holding it", b.treat() is False and treats(b) == 0)

cfg, b = fresh("angry")
b._begin_anger()
check("mid-tantrum: refused, as a click is", b.state is State.ANGRY and b.treat() is False
      and treats(b) == 0, b.state.value)
for _ in range(int(6000 / 16)):
    if b.state is not State.ANGRY:
        break
    b.update(16.0, None)
check("set up: the tantrum over, he is storming off to sleep it off",
      b.state is State.WALK and b._pending_sleep, f"{b.state.value} {b._pending_sleep}")
check("storming off: refused too", b.treat() is False and treats(b) == 0, b.state.value)
check("...and the sulk is still coming", b.state is State.WALK and b._pending_sleep,
      f"{b.state.value} {b._pending_sleep}")

print("\n--- asleep: the sulk grumbles, the nap wakes ---")
cfg, b = fresh("sulk")
b._enter(State.SLEEP)
check("set up: asleep, and not a nap", b.state is State.SLEEP and not b.napping)
spirits = b.mood.spirits
check("a sulk: refused", b.treat() is False)
check("...with a grumble, exactly as at a click", b._grumble_ms == B.GRUMBLE_MS, b._grumble_ms)
check("...which counts as a click, keeping the hover wake off him for a moment",
      b._last_click_ms == b._clock_ms, f"{b._last_click_ms} vs {b._clock_ms}")
check("...still asleep, nothing counted, mood untouched",
      b.state is State.SLEEP and treats(b) == 0 and b.mood.spirits == spirits, b.state.value)

def napping(tag):
    """Worn out, late, and you gone eleven minutes: he has dozed off (see smoke_attention)."""
    cfg, b = fresh(tag, hour=23.5, need_energy=0.1)
    b.set_away(True)
    wait(b, 11 * 60)
    return cfg, b


cfg, b = napping("nap")
check("set up: worn out, late, gone eleven minutes - napping", b.napping, b.state.value)
took = b.treat()
check("a nap: he wakes for it", took is True and not b._napping, b.state.value)
check("...with no grumble", b._grumble_ms == 0, b._grumble_ms)
check("...and eats it straight away", b.state is State.ANTIC and b._antic == B.EAT_ANIM
      and treats(b) == 1, f"{b.state.value} {b._antic}")
wait(b, 60.0)
check("you still read as away: he sits back down to wait, and does not nod straight off",
      not b.napping and b._antic == B.AWAY_ANTIC, f"{b.state.value} {b._antic}")

cfg, b = napping("nap_waking")
b.click()
check("set up: clicked awake from a nap, and still getting up", b._waking_from_nap,
      b.state.value)
check("getting up from a nap: left to finish, as a second click is", b.treat() is False
      and treats(b) == 0 and b._waking_from_nap, b.state.value)
check("...and still no grumble - that is the sulk's", b._grumble_ms == 0, b._grumble_ms)

print("\n--- a treat is noticing him ---")
cfg, b = fresh("noticed")
b._neglect_ms, b._neglect_stage = 400_000.0, 2
b.treat()
check("it resets the being-ignored clock", b._neglect_ms == 0.0 and b._neglect_stage == 0,
      f"{b._neglect_ms} {b._neglect_stage}")

print("\n--- a sheet with no eat frames ---")
manifest = json.loads(FRAMES.read_text(encoding="utf-8"))
del manifest["states"]["eat"]
no_eat = SCRATCH / "treat_frames_no_eat.json"
no_eat.write_text(json.dumps(manifest), encoding="utf-8")
cfg, b = fresh("fallback", frames=no_eat)
check("set up: this sheet has no eat", not b.sheet.has_state(B.EAT_ANIM))
check("he eats it with heart eyes instead", b.treat() and b._antic == B.EAT_FALLBACK_ANIM
      and b.animator.state == B.EAT_FALLBACK_ANIM, f"{b._antic} {b.animator.state}")
check("...and it still counts", treats(b) == 1, treats(b))

print("\n--- never on his own ---")
check("eat is not an ambient antic", B.EAT_ANIM not in [a[0] for a in B.ANTICS])
check("...nor one Do something can pick", B.EAT_ANIM not in [a[0] for a in Behavior.ON_DEMAND])
check("...nor a wheel beat on top of the treat", "treat" not in B.TOOL_BEATS)

print("\n--- the sound ---")
sys.path.insert(0, str(SRC / "tools"))
import gen_sounds  # noqa: E402

check("nom is a sound event", "nom" in sound.EVENTS)
check("...and gen_sounds makes it", "nom" in gen_sounds.SOUNDS)
check("every sound event has a sound, and every sound an event",
      set(gen_sounds.SOUNDS) == set(sound.EVENTS),
      str(set(gen_sounds.SOUNDS) ^ set(sound.EVENTS)))
nom_ms = len(gen_sounds.SOUNDS["nom"]) / gen_sounds.RATE * 1000
check("...a short one", nom_ms <= 200, f"{nom_ms:.0f}ms")

print("\n--- the wheel and the tray ---")
import main as M  # noqa: E402
import tool_wheel as TW  # noqa: E402
from tool_wheel import WheelItem  # noqa: E402
from tray import Tray  # noqa: E402

src_items = inspect.getsource(M.NibApp._wheel_items)
src_chosen = inspect.getsource(M.NibApp._on_tool_chosen)
check("the wheel offers a treat", 'WheelItem("treat"' in src_items)
check("...in the Him group, right after Do something",
      all(f'"{k}"' in src_items for k in ("do_something", "treat", "costume"))
      and src_items.index('"do_something"') < src_items.index('"treat"')
      < src_items.index('"costume"'))
branch = src_chosen.split('key == "treat"', 1)[1].split("elif", 1)[0] if 'key == "treat"' \
    in src_chosen else ""
check("...and picking it gives one", "self._on_treat()" in branch, branch.strip())
check("its icon is drawn in code", "treat" in TW.ICONS)
wheel = TW.ToolWheel(1.0)
wheel.open([WheelItem("treat", "Give him a treat", "treat", group="Him")],
           QPoint(960, 1040), QRect(0, 0, 1920, 1080), sprite_radius=32, press_global=None)
wheel._progress = 1.0
wheel._hover = 0
check("...and paints without raising", not wheel.grab().isNull())
wheel.close()

src_treat = inspect.getsource(M.NibApp._on_treat)
check("the handler goes through Behavior.treat", "self.behavior.treat()" in src_treat)
check("...without walking him out of a sulk first, as Do something does",
      "come_here" not in src_treat and "State.SLEEP" not in src_treat)
check("the tray's treat signal is wired", "self.tray.treat_requested.connect(self._on_treat)"
      in inspect.getsource(M.NibApp._connect))

tray = Tray(QIcon())
actions = [a for a in tray._menu.actions() if not a.isSeparator()]
texts = [a.text() for a in actions]
check("the tray menu has Give a treat", "Give a treat" in texts, texts[:4])
check("...next to Do something", "Do something" in texts and "Give a treat" in texts
      and texts.index("Give a treat") == texts.index("Do something") + 1, texts[:4])
emitted = []
tray.treat_requested.connect(lambda: emitted.append(True))
if "Give a treat" in texts:
    actions[texts.index("Give a treat")].trigger()
check("...and choosing it asks for a treat", emitted == [True], emitted)

print("\n" + "=" * 62)
print("FAILURES: " + ", ".join(fails) if fails else "treats behave")
sys.exit(1 if fails else 0)
