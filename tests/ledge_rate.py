"""How often does the window enumeration actually run, on a desktop with nothing overhead?

The review measured 4564/hour - 89% of all 700ms polls - because the cooldown was only spent when
something WAS found. This reproduces that measurement against the fix.
"""
import os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import scratch as _scratch  # noqa: E402
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))
from PySide6.QtWidgets import QApplication
app = QApplication([])
import world as W
from animator import SpriteSheet
from behavior import Behavior
from config import Config

S = _scratch()

def simulate(found_something):
    p = S / "ledgerate.json"; p.unlink(missing_ok=True)
    cfg = Config(p); cfg["last_seen_date"] = "2026-07-30"
    sheet = SpriteSheet(SRC/"assets"/"nib_sheet.png", SRC/"assets"/"frames.json", 2)
    b = Behavior(cfg, sheet); b.set_strip(1392, 0, 2560); b.place_at(1200)
    POLL, TICK = 700.0, 50.0
    polls = enumerations = 0
    since_poll = 0.0
    for _ in range(int(3600_000 / TICK)):                 # one simulated hour
        b.set_world(W.WorldState(hour=14.0, weekday=2, date_key="2026-07-30",
                                 week_key="2026-W31", part_of_day=W.PART_DAY,
                                 season=W.SEASON_SPRING, cpu_percent=8.0, cpu_busy=False))
        b.update(TICK, (1240, 1340))
        since_poll += TICK
        if since_poll >= POLL:
            since_poll = 0.0
            polls += 1
            if b.wants_ledge:                              # exactly main._update_presence's gate
                enumerations += 1
                b.set_ledge((1392 - 96 - 40, 200, 600) if found_something else None)
    return polls, enumerations

for label, found in (("nothing overhead (the usual case)", False),
                     ("something overhead every time", True)):
    polls, enums = simulate(found)
    print(f"  {label:34s} {enums:5d} enumerations / {polls} polls "
          f"= {100*enums/polls:5.1f}%  ({enums}/hour)")
