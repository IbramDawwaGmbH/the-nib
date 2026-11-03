"""How much of his day do the fidgets actually take?

Correctness says they fire. It says nothing about proportion, and proportion is the whole risk: a
fidget is meant to fill the gaps between the set pieces, not to replace them. Short antics get
picked as often as long ones and finish three times sooner, so a weight that looks modest in the
table can quietly become most of what you ever see.

Runs a real behaviour loop over a simulated hour and tallies wall-clock time per activity.
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
CATEGORY = {name: cat for name, _w, _lo, _hi, cat in B.ANTICS}
HOURS = 2.0
STEP_MS = 50.0
#: How often the simulated user does something with him - a click, a hotkey, a glance.
NOTICE_EVERY_MS = 120_000.0


def run(label, **overrides):
    path = SCRATCH / "fidget_rate.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    cfg["last_seen_date"] = "2026-07-31"
    for k, v in overrides.items():
        cfg[k] = v
    sheet = SpriteSheet(SRC / "assets" / "nib_sheet.png", SRC / "assets" / "frames.json", 2)
    b = Behavior(cfg, sheet)
    b.set_strip(1392, 0, 2560)
    b.place_at(1200)
    b.set_world(W.WorldState(hour=14.0, weekday=2, date_key="2026-07-31", week_key="2026-W31",
                             part_of_day=W.PART_DAY, season=W.SEASON_SPRING,
                             cpu_percent=8.0, cpu_busy=False))
    random.seed(20260731)

    time_by = Counter()
    starts = Counter()
    last = None
    # A user who looks at him now and then. Without this the run measures something else
    # entirely: 240s of silence starts the attention arc, and by 840s he is in the stage-3 sit,
    # which is held indefinitely and glances every 21s. The first version of this script reported
    # sit_dangle at 79% of his time and look_around at 140/h, and both of those numbers were the
    # neglect arc working exactly as designed rather than the ambient pool.
    notice_every = int(NOTICE_EVERY_MS / STEP_MS)
    for i in range(int(HOURS * 3_600_000 / STEP_MS)):
        if i % notice_every == 0:
            b.noticed()
        b.update(STEP_MS, None)
        if b.state is State.ANTIC and b._antic:
            key = b._antic
        elif b.state is State.WALK:
            key = "(walking)"
        else:
            key = "(standing)"
        time_by[key] += STEP_MS
        if key != last and key in CATEGORY:
            starts[key] += 1
        last = key

    total = sum(time_by.values())
    by_cat = Counter()
    for name, ms in time_by.items():
        by_cat[CATEGORY.get(name, name)] += ms

    print(f"\n{label}  ({HOURS:.0f} simulated hours)")
    print("    " + "-" * 62)
    for name, ms in time_by.most_common():
        cat = CATEGORY.get(name, "")
        per_h = starts[name] / HOURS if name in starts else 0
        rate = f"{per_h:5.1f}/h" if per_h else "      "
        print(f"    {name:14s} {cat:9s} {ms/total*100:5.1f}% of his time  {rate}")
    print("    " + "-" * 62)
    for cat, ms in by_cat.most_common():
        print(f"    {cat:24s} {ms/total*100:5.1f}%")
    # Share of ANTIC time, not of the day. Walking is three quarters of the day by design, so
    # measuring against the whole thing says more about walk_max_px than about the fidgets.
    antic_ms = sum(ms for name, ms in time_by.items() if name in CATEGORY)
    fid_ms = by_cat.get("fidget", 0)
    fid_starts = sum(n for name, n in starts.items() if CATEGORY.get(name) == "fidget")
    share = fid_ms / antic_ms * 100 if antic_ms else 0.0
    per_h = fid_starts / HOURS
    print(f"\n    -> fidgets: {share:.0f}% of the time he spends on antics, {per_h:.0f} an hour"
          + (f" (one every {3600/per_h:.0f}s)" if per_h else ""))
    return share, per_h


base, base_rate = run("balanced, default settings")
run("chill", personality="chill")
run("hyper", personality="hyper")
off, off_rate = run("fidgets off", fidgets_enabled=False)

print("\n" + "=" * 66)
# The judgement call this script exists to make. Too rare and they are decoration nobody ever
# sees; too frequent and the set pieces stop being what he is made of. A fidget roughly once a
# minute, taking a tenth or so of his antic time, is the shape being aimed at.
ok = 8.0 <= base <= 30.0 and 25 <= base_rate <= 100 and off_rate == 0
print(f"balanced: {base:.0f}% of antic time, {base_rate:.0f}/h   "
      f"-> {'about right' if ok else 'NEEDS RETUNING'}")
print(f"switched off: {off_rate:.0f}/h -> {'silent' if off_rate == 0 else 'STILL FIRING'}")
