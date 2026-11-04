"""Energy, affection and boredom: drift, nudges, hostile clocks, disk writes, and what they change.

The first half is Qt-free, like smoke_mood: a ``Needs`` on a fake wall clock. The second builds a
real ``Behavior`` (which needs a sprite sheet, so a ``QApplication``) to check the wiring - that
the neutral point changes nothing at all, and that the two moments fire when they should and no
more often than every 90 minutes.
"""

import dataclasses
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import scratch as _scratch  # noqa: E402

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))
SCRATCH = _scratch()

import needs as N  # noqa: E402
from config import Config  # noqa: E402
from needs import Needs  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


class FakeClock:
    """Wall time we can move by hand, so a night takes no time at all."""

    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance_h(self, hours):
        self.now += hours * 3600.0


def fresh(tag, **overrides):
    path = SCRATCH / f"needs_{tag}.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    for k, v in overrides.items():
        cfg[k] = v
    return cfg


HOUR_MS = 3_600_000.0


def run(needs, hours, *, away=False, part="day", interacting=False, step_ms=60_000.0):
    """Tick ``needs`` for ``hours`` in one-minute steps, as the behaviour clock would."""
    for _ in range(int(hours * HOUR_MS / step_ms)):
        needs.tick(step_ms, away=away, part_of_day=part, present_interacting=interacting)


print("\n--- neutral is genuinely nothing ---")
n = Needs(fresh("neutral"), FakeClock())
check("a fresh mascot is at the neutral point",
      (n.energy, n.affection, n.boredom) == (0.5, 0.5, 0.0),
      f"{n.energy} {n.affection} {n.boredom}")
check("...which means no weights at all", n.weights() == {}, str(n.weights()))
check("...no pace change", n.pace() == 1.0, str(n.pace()))
check("...no peek boost", n.peek_boost() == 1.0, str(n.peek_boost()))
check("...and no slow-blink bias", n.slow_blink_bonus() == 0.0, str(n.slow_blink_bonus()))
check("...and reads as okay, fine, busy", n.labels() == ("okay", "fine", "busy"), str(n.labels()))

# Anywhere inside every band is just as neutral as the middle of it.
inside = Needs(fresh("inside", need_energy=0.36, need_affection=0.69, need_boredom=0.59),
               FakeClock())
check("anywhere inside every band is neutral too", inside.weights() == {} and inside.pace() == 1.0
      and inside.peek_boost() == 1.0, str(inside.weights()))

import world as W  # noqa: E402

check("the night it drains faster in is world's night", N.PART_NIGHT == W.PART_NIGHT)

print("\n--- energy: rests while you are away, runs down while you are here ---")
n = Needs(fresh("energy"), FakeClock())
run(n, 1.0, away=False, part=W.PART_DAY)
check("an hour at the machine in the day costs 0.08", abs(n.energy - 0.42) < 1e-9,
      f"{n.energy:.4f}")
run(n, 1.0, away=False, part=W.PART_NIGHT)
check("...an hour at night costs 0.23", abs(n.energy - 0.19) < 1e-9, f"{n.energy:.4f}")
low = n.energy
run(n, 0.5, away=True, part=W.PART_NIGHT)
check("half an hour away gives 0.25 back, night or not", abs(n.energy - (low + 0.25)) < 1e-9,
      f"{n.energy:.4f}")
run(n, 3.0, away=True)
check("...and a long rest tops him up, and no further", n.energy == 1.0, f"{n.energy}")
check("rested reads as rested", n.labels()[0] == "rested", str(n.labels()))
run(n, 10.0, away=False, part=W.PART_DAY)
check("a ten-hour day leaves him tired", n.energy < N.TIRED_BELOW and n.labels()[0] == "tired",
      f"{n.energy:.3f}")
unknown = Needs(fresh("unknown_part"), FakeClock())
run(unknown, 1.0, away=False, part=None)
check("no world reading yet drains like the day, not the night", abs(unknown.energy - 0.42) < 1e-9,
      f"{unknown.energy:.4f}")

print("\n--- affection: rises with kindness, never pinned, drifts back ---")
n = Needs(fresh("affection"), FakeClock())
n.nudge("petted")
check("one pet raises it a little", abs(n.affection - 0.56) < 1e-9, f"{n.affection:.4f}")
for _ in range(50):
    n.nudge("petted")
check("fifty pets leave real headroom", n.affection < 0.95, f"{n.affection:.4f}")
check("...but have him properly loved", n.labels()[1] == "loved", f"{n.affection:.3f}")
top = n.affection
run(n, 12.0, away=True)
check("twelve hours halves the distance to the middle",
      abs((n.affection - 0.5) - (top - 0.5) / 2) < 1e-6, f"{top:.3f} -> {n.affection:.3f}")
run(n, 300.0, away=True)
check("...and it gets all the way back, exactly", n.affection == 0.5, f"{n.affection}")
lonely = Needs(fresh("lonely", need_affection=0.1), FakeClock())
check("low affection reads as lonely", lonely.labels()[1] == "lonely", str(lonely.labels()))
lonely.nudge("petted")
check("a pet when lonely lands in full", abs(lonely.affection - 0.16) < 1e-9, f"{lonely.affection}")

print("\n--- boredom: builds while ignored, cleared by anything at all ---")
n = Needs(fresh("boredom"), FakeClock())
run(n, 3.0, away=False, interacting=False)
check("three hours of being ignored builds it", abs(n.boredom - 0.3) < 1e-9, f"{n.boredom:.4f}")
run(n, 1.0, away=False, interacting=True)
check("...and it fades while you are doing things with him", n.boredom < 0.3, f"{n.boredom:.4f}")
n2 = Needs(fresh("bored", need_boredom=0.45), FakeClock())
n2.nudge("interacted")
check("one interaction clears it completely", n2.boredom == 0.0, f"{n2.boredom}")
n3 = Needs(fresh("bored2", need_boredom=0.9), FakeClock())
check("high boredom reads as bored", n3.labels()[2] == "bored", str(n3.labels()))
check("...and makes him likelier to peek", abs(n3.peek_boost() - 1.45) < 1e-9, f"{n3.peek_boost()}")
n3.nudge("world_event")
check("something happening around him helps a little", abs(n3.boredom - 0.7) < 1e-9,
      f"{n3.boredom:.3f}")
run(n3, 2.0, away=True)
check("...and two hours away halves what is left", abs(n3.boredom - 0.35) < 1e-6, f"{n3.boredom}")

print("\n--- every nudge in the table, and the taper ---")
for event, deltas in N.NEED_NUDGES.items():
    check(f"{event}: no delta bigger than half the range", all(abs(d) <= 0.5 for d in deltas))
n = Needs(fresh("unknown"), FakeClock())
n.nudge("no_such_event")
check("an unknown event is ignored", (n.energy, n.affection, n.boredom) == (0.5, 0.5, 0.0))
n.nudge("petted", 0.5)
check("a half-scale pet is half a pet", abs(n.affection - 0.53) < 1e-9, f"{n.affection}")
tired = Needs(fresh("taper", need_energy=0.05), FakeClock())
tired.nudge("hurt")
check("worn out, a knock costs almost nothing more (the taper)", 0.049 < tired.energy < 0.05,
      f"{tired.energy}")
low = tired.energy
tired.nudge("nap_woke")
check("...and a nap, back towards neutral, lands in full", abs(tired.energy - (low + 0.30)) < 1e-9,
      f"{tired.energy}")

print("\n--- what they change, at the ends of the range ---")
spent = Needs(fresh("spent", need_energy=0.0, need_affection=0.0, need_boredom=1.0), FakeClock())
w = spent.weights()
check("worn out: sit_dangle x2 and bored x1.5 compose", abs(w["sit_dangle"] - 3.0) < 1e-9, str(w))
check("...hops at 0.4", abs(w["hop"] - 0.4) < 1e-9, str(w["hop"]))
check("...lonely look_around gets 1.4 and the eyes' 1.5", abs(w["look_around"] - 2.1) < 1e-9,
      str(w["look_around"]))
check("...every eyes antic leans at you", all(abs(w[e] - 1.5) < 1e-9
                                              for e in ("sparkle", "heart_eyes", "wink")))
check("...check_watch from both tired and bored", abs(w["check_watch"] - 3.0) < 1e-9,
      str(w["check_watch"]))
check("no single need's multiplier is below 0.4",
      min(w.values()) >= 0.4, str(min(w.values())))
check("pace bottoms out at 0.9", abs(spent.pace() - 0.9) < 1e-9, f"{spent.pace()}")
check("slow blink bonus tops out at 0.25", abs(spent.slow_blink_bonus() - 0.25) < 1e-9)
check("peek boost tops out at 1.6", abs(spent.peek_boost() - 1.6) < 1e-9)
glad = Needs(fresh("glad", need_energy=1.0, need_affection=1.0), FakeClock())
w = glad.weights()
check("fully rested and loved: hop x1.5, heart eyes x1.8",
      abs(w["hop"] - 1.5) < 1e-9 and abs(w["heart_eyes"] - 1.8) < 1e-9, str(w))
edge = Needs(fresh("edge", need_energy=N.TIRED_BELOW - 1e-6), FakeClock())
check("just past an edge the lean is all but nothing",
      all(abs(f - 1.0) < 1e-4 for f in edge.weights().values()), str(edge.weights()))

print("\n--- it keeps going while he is closed ---")
clock = FakeClock()
cfg = fresh("restart")
n = Needs(cfg, clock)
for _ in range(10):
    n.nudge("petted")
run(n, 4.0, away=False, part=W.PART_EVENING, interacting=False)
n.persist()
check("persist writes all three", cfg["need_energy"] < 0.5 and cfg["need_affection"] > 0.6
      and cfg["need_boredom"] > 0.3,
      f"{cfg['need_energy']} {cfg['need_affection']} {cfg['need_boredom']}")
check("...quantised to hundredths", all(abs(cfg[k] * 100 - round(cfg[k] * 100)) < 1e-6
                                        for k in ("need_energy", "need_affection", "need_boredom")))
check("...with a timestamp", cfg["needs_saved_at"] == clock.now)
stored = (cfg["need_energy"], cfg["need_affection"], cfg["need_boredom"])
clock.advance_h(12.0)
m = Needs(cfg, clock)
check("a night closed restores his energy", m.energy == 1.0, f"{stored[0]} -> {m.energy}")
check("...halves the distance from fond to neutral",
      abs((m.affection - 0.5) - (stored[1] - 0.5) / 2) < 1e-6, f"{stored[1]} -> {m.affection:.3f}")
check("...and boredom is all but gone", m.boredom < 0.01, f"{stored[2]} -> {m.boredom}")

back = FakeClock()
cfg_b = fresh("backwards", need_energy=0.2, need_affection=0.8, need_boredom=0.5,
              needs_saved_at=back.now)
back.advance_h(-48.0)
m = Needs(cfg_b, back)
check("a clock that moved backwards ages nothing",
      (m.energy, m.affection, m.boredom) == (0.2, 0.8, 0.5),
      f"{m.energy} {m.affection} {m.boredom}")

cfg_j = fresh("junk")
cfg_j["need_energy"] = 7          # clamped by config
cfg_j["needs_saved_at"] = 1.0e12  # the year 33658
m = Needs(cfg_j, FakeClock())
check("an absurd stored value is clamped into range", 0.0 <= m.energy <= 1.0, f"{m.energy}")

print("\n--- how often it touches the disk ---")


def writes_over(needs, cfg, minutes, **tick):
    needs.persist()
    cfg.save()
    writes = 0
    for _ in range(minutes * 6):        # the app's 10s flush timer
        needs.tick(10_000.0, **tick)
        needs.persist()
        if cfg.dirty:
            writes += 1
            cfg.save()
    return writes


cfg_d = fresh("disk_rest", need_energy=1.0)
rest = Needs(cfg_d, FakeClock())
w = writes_over(rest, cfg_d, 10, away=True, part_of_day=W.PART_DAY, present_interacting=False)
check("a rested mascot with you away writes nothing in ten minutes", w == 0, f"{w} writes")
cfg_d2 = fresh("disk_work")
work = Needs(cfg_d2, FakeClock())
w = writes_over(work, cfg_d2, 10, away=False, part_of_day=W.PART_DAY, present_interacting=False)
# Energy and boredom both drift while you are here, so a write is due every six minutes or so -
# the bar is smoke_mood's: a handful, never one every flush.
check("one drifting while you work writes a couple of times at most", w <= 3, f"{w} writes")

print("\n--- switched off ---")
cfg_off = fresh("off", needs_enabled=False, need_energy=0.1, need_affection=0.9,
                need_boredom=0.8, needs_saved_at=123.0)
off = Needs(cfg_off, FakeClock())
check("off reports the neutral point", (off.energy, off.affection, off.boredom) == (0.5, 0.5, 0.0))
check("...and changes nothing", off.weights() == {} and off.pace() == 1.0
      and off.peek_boost() == 1.0 and off.slow_blink_bonus() == 0.0)
for _ in range(5):
    off.nudge("petted")
run(off, 2.0, away=False)
off.persist()
check("...and never rewrites what is stored",
      (cfg_off["need_energy"], cfg_off["need_affection"], cfg_off["need_boredom"],
       cfg_off["needs_saved_at"]) == (0.1, 0.9, 0.8, 123.0))

print("\n--- reset ---")
cfg_r = fresh("reset", need_energy=0.1, need_affection=0.9, need_boredom=0.8)
r = Needs(cfg_r, FakeClock())
r.reset()
check("reset is the neutral point", (r.energy, r.affection, r.boredom) == (0.5, 0.5, 0.0))
r.persist()
check("...and the next flush writes it", (cfg_r["need_energy"], cfg_r["need_affection"],
                                          cfg_r["need_boredom"]) == (0.5, 0.5, 0.0))
# Forget everything with needs switched off: persist() writes nothing then, so if reset left the
# old values in the config they would simply come back on the next launch.
cfg_ro = fresh("reset_off", needs_enabled=False, need_energy=0.1, need_affection=0.9,
               need_boredom=0.8, needs_saved_at=123.0)
Needs(cfg_ro, FakeClock()).reset()
check("reset with needs off still clears what is stored",
      (cfg_ro["need_energy"], cfg_ro["need_affection"], cfg_ro["need_boredom"],
       cfg_ro["needs_saved_at"]) == (0.5, 0.5, 0.0, 0.0),
      f"{cfg_ro['need_energy']} {cfg_ro['need_affection']} {cfg_ro['need_boredom']}")
cfg_ro["needs_enabled"] = True
again = Needs(cfg_ro, FakeClock())
check("...so switching back on after a restart starts from neutral",
      (again.energy, again.affection, again.boredom) == (0.5, 0.5, 0.0))

# --- the Behavior wiring ---------------------------------------------------------------------

print("\n--- in Behavior: neutral changes nothing ---")
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

import behavior as B  # noqa: E402
from animator import SpriteSheet  # noqa: E402
from behavior import Behavior, State  # noqa: E402

check("the four eyes antics are behavior's eyes category",
      set(N.EYES_ANTICS) == {name for name, _w, _lo, _hi, cat in B.ANTICS if cat == "eyes"})
check("the bored line and the bored word share an edge", B.BORED_ABOVE == N.BORED_ABOVE)

sheet = SpriteSheet(SRC / "assets" / "nib_sheet.png", SRC / "assets" / "frames.json", 2)
DATE = "2026-11-04"


def world(hour, weekday=2):
    return W.WorldState(hour=hour, weekday=weekday, date_key=DATE, week_key="2026-W45",
                        part_of_day=W.part_of_day(hour), season=W.SEASON_AUTUMN,
                        cpu_percent=8.0, cpu_busy=False)


def make(tag, clock=None, **overrides):
    base = dict(wander_enabled=False, play_with_windows=False, seasonal_enabled=False,
                quirks_enabled=False, last_seen_date=DATE)
    base.update(overrides)
    cfg = fresh(tag, **base)
    needs = Needs(cfg, clock or FakeClock())
    b = Behavior(cfg, sheet, needs=needs)
    b.set_strip(1392, 0, 2560)
    b.place_at(1200)
    return b, needs, cfg


b_on, n_on, _ = make("neutral_on")
check("a Behavior keeps the needs it is given", b_on.needs is n_on)
check("...and makes its own when it is not", isinstance(Behavior(fresh("own"), sheet).needs, Needs))
b_off, _, _ = make("neutral_off", needs_enabled=False)
for hour in (3.0, 8.0, 14.0, 20.0):
    b_on.set_world(world(hour))
    b_off.set_world(world(hour))
    check(f"at {hour:04.1f}h neutral weights equal needs switched off",
          b_on._mood_weights() == b_off._mood_weights(), str(b_on._mood_weights()))
    check(f"...and so does the pace", b_on._mood_pace() == b_off._mood_pace())
# And the same as before needs existed: just the mood's and the clock's, composed.
b_on.mood.nudge("petted")
b_on.set_world(world(14.0))
expected = dict(b_on.mood.weights())
check("neutral needs add nothing to the mood's own weights", b_on._mood_weights() == expected,
      f"{b_on._mood_weights()} vs {expected}")

print("\n--- in Behavior: the nudges are wired ---")
b, n, _ = make("wiring", need_boredom=0.4)
b.set_world(world(14.0))
b.noticed()
check("noticing him clears boredom", n.boredom == 0.0, f"{n.boredom}")
before = n.affection
b.prompt_sent("hello")
check("a prompt warms him a little", n.affection > before, f"{before} -> {n.affection}")
for _ in range(200):
    b.update(50.0, None)
before = n.affection
b.set_away(True)
b.set_away(False, gone_s=900.0)
check("coming back warms him too", n.affection > before, f"{before} -> {n.affection}")
hand = (b.x + b.sprite_w // 2, b.top + b.sprite_h // 2)
before = n.affection
for _ in range(80):
    b.update(50.0, hand)
    if b.state == State.PET:
        break
check("a pet warms him most", b.state == State.PET and n.affection - before > 0.04,
      f"{b.state} {before:.3f} -> {n.affection:.3f}")
for _ in range(100):
    b.update(50.0, None)

# A session about to end on its own, the way smoke_memory drives one.
before = (n.energy, n.affection)
b.set_focus(True)
b._focus_remaining_ms = 50.0
b.update(100.0, None)
check("a finished focus session tires him a little and pleases him",
      n.energy < before[0] - 0.04 and n.affection > before[1],
      f"{before} -> {n.energy:.3f}, {n.affection:.3f}")
for _ in range(200):
    b.update(50.0, None)

# A fast sideways flick, the shape smoke_throw drives.
before = n.energy
b.drag_start()
lift_y = int(b._y) - 200
for step in range(12):
    b.drag_to(1000 + 40 * step, lift_y)
    b.update(16, None)
b.drag_end()
check("a throw costs a little energy (tapered below neutral)", n.energy < before - 0.01, f"{before} -> {n.energy}")
for _ in range(400):
    b.update(16, None)
before = n.energy
b._pending_injury = True
b._settle()
check("a hard landing costs more", n.energy < before - 0.025, f"{before} -> {n.energy}")
for _ in range(400):
    b.update(16, None)

# Switching to Claude starts an antic: something happening around him, so a little less bored.
b2, n2, _ = make("foreground", need_boredom=0.5)
b2.set_world(world(14.0))
b2.foreground_changed("claude.exe")
check("an app reaction takes 0.2 off boredom", abs(n2.boredom - 0.3) < 1e-9 and b2.state == State.ANTIC,
      f"{n2.boredom} {b2.state}")

print("\n--- in Behavior: the clock drives them ---")
b, n, _ = make("ticking", attention_enabled=True)
b.set_world(world(14.0))
for _ in range(int(0.5 * HOUR_MS / 250.0)):
    b.update(250.0, None)
check("half an hour here and ignored: a little tired, a little bored",
      n.energy < 0.5 and n.boredom > 0.0, f"energy {n.energy:.3f} boredom {n.boredom:.3f}")
here = n.energy
b.set_away(True)
for _ in range(int(0.5 * HOUR_MS / 250.0)):
    b.update(250.0, None)
check("half an hour away and he is rested again", n.energy > here + 0.2,
      f"{here:.3f} -> {n.energy:.3f}")

# With being ignored on hold, the clock is frozen at whatever it reached - which is not "just
# noticed", so a check on its value alone would keep boredom climbing all through a pinned build.
for tag, overrides, busy in (("pinned", {"antics_enabled": False}, True),
                              ("arc_off", {"attention_enabled": False}, False)):
    bh, nh, _ = make(f"hold_{tag}", need_boredom=0.3, **overrides)
    bh.set_world(dataclasses.replace(world(14.0), cpu_busy=busy))
    bh._neglect_ms = 600_000.0   # ten minutes ignored, then the hold began
    for _ in range(int(0.5 * HOUR_MS / 250.0)):
        bh.update(250.0, None)
    check(f"boredom does not build while being ignored is on hold ({tag})", nh.boredom < 0.3,
          f"{nh.boredom:.3f}")

b.paused = True
frozen = (n.energy, n.affection, n.boredom)
for _ in range(400):
    b.update(250.0, None)
check("paused, nothing drifts", (n.energy, n.affection, n.boredom) == frozen)

print("\n--- in Behavior: worn out in the evening ---")
b, n, cfg = make("tired", need_energy=0.1, need_boredom=0.3)
b.set_world(world(20.0))
b._choose_next_action(None)
check("worn out at eight in the evening, he yawns", b.state == State.ANTIC and b._antic == "yawn",
      f"{b.state} {b._antic}")
check("...with a heavy blink queued after it", b._antic_queue == [(B.BLINK_SLOW, 800.0)],
      str(b._antic_queue))
check("...and says so (an event for voice.py)", ("tired", {}) in b.take_events())
check("...and the moment took the edge off his boredom", abs(n.boredom - 0.1) < 1e-9,
      f"{n.boredom}")
b2, _, _ = make("tired_day", need_energy=0.1)
b2.set_world(world(14.0))
check("the same energy in the afternoon is no moment", b2._pick_moment() is None
      and not b2.take_events())
b3, _, _ = make("tired_off", need_energy=0.1, needs_enabled=False)
b3.set_world(world(20.0))
check("with needs off, no yawn however low the stored energy", b3._pick_moment() is None)

# A whole night, beat by beat the way day_rate walks a day: how often does he yawn?
b, n, _ = make("night", need_energy=0.05)
STEP = 800.0
yawns = []
t = 0.0
while t < 6 * HOUR_MS:
    b.set_world(world(20.0 + t / HOUR_MS if t / HOUR_MS < 4 else (t / HOUR_MS - 4.0)))
    moment = b._pick_moment()
    if moment is not None:
        b._reaction_cooldown_ms = B.REACTION_COOLDOWN_MS
    yawns += [t for kind, _d in b.take_events() if kind == "tired"]
    for attr in ("_reaction_cooldown_ms", "_needs_moment_cooldown_ms"):
        setattr(b, attr, max(0.0, getattr(b, attr) - STEP))
    t += STEP
gaps = [later - earlier for earlier, later in zip(yawns, yawns[1:])]
check("six hours of a worn-out night: a yawn every 90 minutes, no more",
      len(yawns) == 4 and all(g >= B.NEEDS_MOMENT_COOLDOWN_MS for g in gaps),
      f"{len(yawns)} yawns, gaps {[round(g / 60000) for g in gaps]} min")

print("\n--- in Behavior: fond, and bored ---")
b, n, _ = make("fond", need_affection=0.95)
b.set_world(world(14.0))
check("doted on, but your pointer is far away: nothing", b._pick_moment((0, 0)) is None)
near = (int(b.x + b.sprite_w // 2), int(b.top + b.sprite_h // 2) - 40)
moment = b._pick_moment(near)
check("doted on and your pointer is near: heart eyes",
      moment is not None and moment[0] == "heart_eyes" and 1400 <= moment[1] <= 2100, str(moment))
check("...and an event for it", ("fond", {}) in b.take_events())
b._reaction_cooldown_ms = 0.0
check("...once per 90 minutes", b._pick_moment(near) is None)

b, n, _ = make("bored", need_boredom=0.8)
b.set_world(world(14.0))
check("bored: nothing to see", b._pick_moment() is None)
check("...but an event, for a line", ("bored", {}) in b.take_events())
check("...and not again for 90 minutes", b._pick_moment() is None and not b.take_events())
b._needs_moment_cooldown_ms = 0.0
b._reaction_cooldown_ms = 0.0
b.update(250.0, None)  # makes sure the cooldown is in update's loop
b._needs_moment_cooldown_ms = 1000.0
b.update(250.0, None)
check("update counts the needs cooldown down", b._needs_moment_cooldown_ms == 750.0,
      f"{b._needs_moment_cooldown_ms}")

print("\n" + ("FAILURES: " + ", ".join(fails) if fails else "all needs checks passed"))
sys.exit(1 if fails else 0)
