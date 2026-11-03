"""Does "twice an hour" actually mean twice an hour?

The rates were asked for by number, so they get measured rather than asserted. Six simulated hours
of a real behaviour loop, counting each occurrence, across the settings most likely to skew it:
a hyper mascot reaches decision points far more often than a chill one, and a scheduled quirk is
supposed to be immune to that where a per-roll chance would not have been.
"""
import os
import random
import sys
from collections import Counter
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
HOURS = 6.0
STEP_MS = 50.0
#: A user who looks at him now and then - without this the attention arc dominates the run, which
#: is the mistake fidget_rate.py made first time round.
NOTICE_EVERY_MS = 120_000.0
fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


def run(label, seed=20260731, **overrides):
    path = SCRATCH / "quirk_rate.json"
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
    random.seed(seed)

    counts = Counter()
    last = None
    notice_every = int(NOTICE_EVERY_MS / STEP_MS)
    for i in range(int(HOURS * 3_600_000 / STEP_MS)):
        if i % notice_every == 0:
            b.noticed()
        b.update(STEP_MS, None)
        current = b._antic if b.state is State.ANTIC else None
        if current != last and current in (B.SNEEZE_ANIM, B.TRIP_ANIM, B.HICCUP_ANIM):
            counts[current] += 1
        last = current
    return {k: v / HOURS for k, v in counts.items()}


print(f"\n--- {HOURS:.0f} simulated hours each ---")
rows = []
for label, kw in (("balanced", {}), ("hyper", {"personality": "hyper"}),
                  ("chill", {"personality": "chill"}),
                  ("no antics", {"antics_enabled": False})):
    rate = run(label, **kw)
    rows.append((label, rate))
    print(f"  {label:12s} sneeze {rate.get(B.SNEEZE_ANIM, 0):4.1f}/h   "
          f"trip {rate.get(B.TRIP_ANIM, 0):4.1f}/h")

print(f"\n  asked for: sneeze {B.SNEEZES_PER_HOUR:.0f}/h, trip {B.TRIPS_PER_HOUR:.0f}/h")

for label, rate in rows:
    s, t = rate.get(B.SNEEZE_ANIM, 0), rate.get(B.TRIP_ANIM, 0)
    # Generous bands: six hours of a random schedule is a small sample, and the point is that the
    # rate does not DRIFT with the preset, not that it is exact.
    check(f"{label:12s} sneezes about twice an hour", 1.2 <= s <= 3.0, f"{s:.2f}/h")
    check(f"{label:12s} trips about three times an hour", 1.8 <= t <= 4.5, f"{t:.2f}/h")

print("\n--- the rate must not drift with how busy he is ---")
sneezes = [r.get(B.SNEEZE_ANIM, 0) for _l, r in rows]
trips = [r.get(B.TRIP_ANIM, 0) for _l, r in rows]
check("sneeze rate is the same whatever the preset", max(sneezes) - min(sneezes) <= 1.2,
      f"{min(sneezes):.1f}..{max(sneezes):.1f}/h")
check("trip rate is the same whatever the preset", max(trips) - min(trips) <= 1.6,
      f"{min(trips):.1f}..{max(trips):.1f}/h")

print("\n--- and they can be switched off ---")
off = run("off", quirks_enabled=False)
check("quirks_enabled=false silences all of them", not off, str(off))

print("\n" + "=" * 62)
print("FAILURES: " + ", ".join(fails) if fails else "the rates mean what they say")
sys.exit(1 if fails else 0)
