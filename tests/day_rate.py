"""How OFTEN do the moments actually fire across a whole day?

Correctness tests say each one fires and does not repeat. They say nothing about rate, which is
the difference between charming and irritating. This walks a simulated Friday minute by minute.
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
from behavior import Behavior
from config import Config

SCRATCH = _scratch()

def simulate(season, weekday, busy_from=None, busy_to=None, label=""):
    p = SCRATCH / "rate.json"; p.unlink(missing_ok=True)
    cfg = Config(p); cfg["workday_end"] = "16:00"
    sheet = SpriteSheet(SRC/"assets"/"nib_sheet.png", SRC/"assets"/"frames.json", 2)
    b = Behavior(cfg, sheet)
    random.seed(20260730)
    fired = Counter()
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
        # advance every cooldown by one beat, as update() would
        for attr in ("_reaction_cooldown_ms", "_hot_cooldown_ms",
                     "_morning_cooldown_ms", "_seasonal_cooldown_ms"):
            setattr(b, attr, max(0.0, getattr(b, attr) - STEP_MS))
    total = sum(fired.values())
    print(f"\n{label}")
    for name, n in fired.most_common():
        print(f"    {name:12s} {n:4d} times  (~every {24*60/n:5.1f} min of the day)")
    print(f"    {'TOTAL':12s} {total:4d}")
    return fired

simulate(W.SEASON_SUMMER, 4, 10.0, 10.5, "A Friday in summer, machine pinned 10:00-10:30")
simulate(W.SEASON_WINTER, 2, None, None, "A Wednesday in winter, machine quiet")
