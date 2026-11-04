"""How OFTEN do the moments actually fire across a whole day?

Correctness tests say each one fires and does not repeat. They say nothing about rate, which is
the difference between charming and irritating. This walks a simulated Friday minute by minute.

The third run is the same quiet Wednesday with a long memory behind it - thirty days seen and a
bond near the top - to show that what he remembers adds exactly one moment (the 30-day milestone)
and changes the rate of nothing else. It exits non-zero if the milestone fires any other number of
times, or if the other moments differ from the fresh-install run (a yawn and a coffee count as
one kind: see below).

The fourth is that Wednesday again with him worn out all day (energy held at 0.1 - nothing ticks
here, so it stays there): the tired yawn may fire only in the evening and at night, at most once
every 90 minutes. It exits non-zero if a tired yawn lands in the day or two land closer together.
The first three hold his needs at neutral, where the tired yawn cannot fire at all.
"""
import os, sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import scratch as _scratch  # noqa: E402
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))
from PySide6.QtWidgets import QApplication
app = QApplication([])
import random
import world as W
from animator import SpriteSheet
from behavior import TASK_FINISHED_ANIM, Behavior
from config import Config
from memory import Memory
from needs import Needs
import behavior as B

SCRATCH = _scratch()

def long_memory(cfg):
    """Thirty days seen, and enough pets, prompts and focus sessions for a bond of about 0.9."""
    mem = Memory(None, cfg)
    for back in range(29, -1, -1):
        mem.first_sight(f"2026-07-{31 - back:02d}", 9.0)
    for event, n in (("pets", 900), ("prompts", 400), ("focus_done", 80)):
        mem.record(event, "2026-07-30", n=n)
    return mem


def simulate(season, weekday, busy_from=None, busy_to=None, label="", remembering=False,
             energy=None):
    p = SCRATCH / "rate.json"; p.unlink(missing_ok=True)
    cfg = Config(p); cfg["workday_end"] = "16:00"
    if energy is not None:
        cfg["need_energy"] = energy
    sheet = SpriteSheet(SRC/"assets"/"nib_sheet.png", SRC/"assets"/"frames.json", 2)
    mem = long_memory(cfg) if remembering else None
    b = Behavior(cfg, sheet, memory=mem, needs=Needs(cfg))
    if mem is not None:
        print(f"\n(memory: {mem.days_seen} days seen, bond {mem.bond():.2f} - {mem.bond_label()})")
    random.seed(20260730)
    fired = Counter()
    tired_at = []            # the hour of each tired yawn, read off the event queue
    STEP_MS = 800.0          # one wander beat, i.e. how often _choose_next_action runs
    minutes = int(24 * 60 * 60_000 / STEP_MS)
    for i in range(minutes):
        hour = (i * STEP_MS / 3_600_000.0) % 24.0
        busy = busy_from is not None and busy_from <= hour <= busy_to
        b.set_world(W.WorldState(
            hour=hour, weekday=weekday, date_key="2026-07-31", week_key="2026-W31",
            part_of_day=W.part_of_day(hour), season=season,
            cpu_percent=92.0 if busy else 10.0, cpu_busy=busy))
        moment = b._pick_moment()
        if moment is not None:
            fired[moment[0]] += 1
            b._reaction_cooldown_ms = __import__("behavior").REACTION_COOLDOWN_MS
        tired_at += [hour for kind, _data in b.take_events() if kind == "tired"]
        # advance every cooldown by one beat, as update() would
        for attr in ("_reaction_cooldown_ms", "_hot_cooldown_ms",
                     "_morning_cooldown_ms", "_seasonal_cooldown_ms",
                     "_task_finished_cooldown_ms", "_needs_moment_cooldown_ms"):
            setattr(b, attr, max(0.0, getattr(b, attr) - STEP_MS))
    total = sum(fired.values())
    print(f"\n{label}")
    for name, n in fired.most_common():
        print(f"    {name:12s} {n:4d} times  (~every {24*60/n:5.1f} min of the day)")
    print(f"    {'TOTAL':12s} {total:4d}")
    if tired_at:
        print(f"    (of which tired yawns: {len(tired_at)}, at "
              + ", ".join(f"{int(h):02d}:{int(h * 60 % 60):02d}" for h in tired_at) + ")")
    simulate.tired_at = tired_at
    return fired

simulate(W.SEASON_SUMMER, 4, 10.0, 10.5, "A Friday in summer, machine pinned 10:00-10:30")
fresh = simulate(W.SEASON_WINTER, 2, None, None, "A Wednesday in winter, machine quiet")
known = simulate(W.SEASON_WINTER, 2, None, None,
                 "The same Wednesday, thirty days and a bond near the top remembered", remembering=True)

# The milestone is the same party as a finished task, so it shows up as task_finished: once. Its
# duration is drawn from the shared random stream, which shifts every later draw by one - so the
# morning moment's coin flip between a yawn and a coffee can land the other way. That is the same
# morning moment as often, just a different one, so the two are compared as one.
MORNING = ("yawn", "coffee")


def by_kind(fired):
    kinds = Counter()
    for name, n in fired.items():
        if name != TASK_FINISHED_ANIM:
            kinds["yawn/coffee" if name in MORNING else name] += n
    return kinds


milestones = known[TASK_FINISHED_ANIM] - fresh[TASK_FINISHED_ANIM]
others_same = by_kind(known) == by_kind(fresh)
print(f"\n    30-day milestone fired {milestones} time(s) in the day (want 1); "
      f"everything else {'unchanged' if others_same else 'CHANGED'}")

simulate(W.SEASON_WINTER, 2, None, None,
         "The same Wednesday, worn out all day (energy held at 0.1)", energy=0.1)
tired_at = simulate.tired_at
late = all(W.part_of_day(h) in (W.PART_EVENING, W.PART_NIGHT) for h in tired_at)
gap_h = B.NEEDS_MOMENT_COOLDOWN_MS / 3_600_000.0
spaced = all(later - earlier >= gap_h - 1e-9 for earlier, later in zip(tired_at, tired_at[1:]))
tired_ok = bool(tired_at) and late and spaced
print(f"\n    tired yawn fired {len(tired_at)} time(s), "
      f"{'only in the evening and at night' if late else 'IN THE DAY'}, "
      f"{'at least 90 min apart' if spaced else 'CLOSER THAN 90 MIN'}")
sys.exit(0 if milestones == 1 and others_same and tired_ok else 1)
