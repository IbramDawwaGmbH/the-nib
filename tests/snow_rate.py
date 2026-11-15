"""How fast does the snow grow, and how often does Nib do anything about it?

Measured on the model's injected clock, so twenty minutes of growth take a fraction of a second:

*   **Growth**: how long a fresh pile takes to show (3, 6 and 10 px in the middle), to reach 20 px
    and to fill, and when its icicles appear and finish - with ``grow()`` called every 30 s, every
    2 s (the stack poll backed off at rest) and every 0.5 s (the poll at full rate), because
    growth must count showing time whatever the poll is doing.
*   **The stack poll**: full ``list_window_stack`` passes per minute with the desktop at rest and
    with the pointer moving (the back-off, ``snow_overlay.STACK_SETTLE_PASSES``).
*   **Nib**: shivers per hour standing on a snowy window, and play moments per hour while you
    keep brushing snow next to him.

The numbers are quoted in ``plans/CHRISTMAS-SNOW-PLAN.md`` (WP4, WP5) and ``snow.py``.
"""
import os
import random
import statistics
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
import snow as S  # noqa: E402
import snow_overlay as SO  # noqa: E402
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


# --- growth -------------------------------------------------------------------------------------

WIN = (300, 400, 1100, 900)        # an 800 px window at 96 DPI
MARKS = (3.0, 6.0, 10.0, 20.0)


def grow_once(hwnd, period):
    """Minutes to each mark, to full, and to the first and last icicle length, for one pile."""
    f = S.SnowField(rng=random.Random(1))
    f.set_monitors([(0, 0, 1920, 1080)])
    cap = f.add_cap(hwnd, WIN, 0.0)
    got = {}
    t = 0.0
    while t < 90 * 60_000 and not ("full" in got and "icicles" in got):
        t += period
        f.grow(t)
        middle = statistics.median(cap.heights)
        for px in MARKS:
            if px not in got and middle >= px:
                got[px] = t / 60_000
        if "full" not in got and cap.full:
            got["full"] = t / 60_000
        if "icicle" not in got and any(ic.length >= 4.0 for ic in cap.icicles):
            got["icicle"] = t / 60_000
        if "icicles" not in got and all(ic.length >= ic.max_len for ic in cap.icicles):
            got["icicles"] = t / 60_000
    return got


print("--- growth: a fresh 800 px pile at 96 DPI, 12 windows' seeds ---")
HWNDS = [0x10000 + 977 * k for k in range(12)]
results = {}
for label, period in (("every 30 s", 30_000.0), ("every 2 s (poll at rest)", 2000.0),
                      ("every 0.5 s (poll at full rate)", 500.0)):
    runs = [grow_once(h, period) for h in HWNDS]
    row = {}
    for key in MARKS + ("full", "icicle", "icicles"):
        vals = [r[key] for r in runs if key in r]
        row[key] = (min(vals), statistics.median(vals), max(vals)) if vals else None
    results[label] = row
    print(f"  grow() {label}:")
    for key in MARKS:
        lo, med, hi = row[key]
        print(f"    middle at {key:4.0f} px   {med:5.1f} min  ({lo:.1f}-{hi:.1f})")
    for key, what in (("full", "full"), ("icicle", "first icicle 4 px"),
                      ("icicles", "every icicle full")):
        lo, med, hi = row[key]
        print(f"    {what:17s}  {med:5.1f} min  ({lo:.1f}-{hi:.1f})")

for label, row in results.items():
    check(f"{label}: snowy within a few minutes (6 px by 4 min)", row[6.0][2] <= 4.0,
          f"{row[6.0][2]:.1f} min")
    check(f"{label}: full in about 20 minutes", 17.0 <= row["full"][1] <= 23.0,
          f"{row['full'][1]:.1f} min")
    check(f"{label}: icicles over the next half hour or so", row["icicles"][2] <= 60.0,
          f"{row['icicles'][2]:.1f} min")
fulls = [row["full"][1] for row in results.values()]
check("the poll's rate does not change how fast it grows", max(fulls) - min(fulls) <= 1.0,
      f"{min(fulls):.1f}-{max(fulls):.1f} min")

# --- the stack poll ------------------------------------------------------------------------------

print("\n--- the stack poll: full passes a minute (120 ticks of STACK_POLL_MS) ---")
MON = SO.Monitor((0, 0, 1920, 1080), 1040.0, 1.0)
DESK = [(0x7FFF0101, WIN, False, False)]


def passes_per_minute(pointer_moves):
    pos = [1800, 1000]

    def cursor():
        if pointer_moves:
            pos[0] = 1700 + (pos[0] + 13) % 100
        return tuple(pos)

    ov = SO.SnowOverlay(stack=lambda: list(DESK), monitors=lambda: [MON],
                        fullscreen=lambda m: False, cursor=cursor, glance=lambda: 1)
    ov.set_state(active=True, growing=False)
    ov.stack_timer.stop()
    ov.field.add_cap(DESK[0][0], WIN, ov._now_ms(), fill=1.0)
    for _ in range(20):                          # settled
        ov._on_stack_timer()
    before = ov.passes
    for _ in range(int(60_000 / SO.STACK_POLL_MS)):
        ov._on_stack_timer()
    out = ov.passes - before
    ov.shutdown()
    return out


still = passes_per_minute(False)
moving = passes_per_minute(True)
print(f"  at rest, pointer still: {still}/min    pointer moving: {moving}/min    "
      f"(before WP4: {int(60_000 / SO.STACK_POLL_MS)}/min either way)")
check("at rest it walks the stack every 2 s", still == 30, str(still))
check("with the pointer moving, every 0.5 s", moving == 120, str(moving))

# --- Nib -----------------------------------------------------------------------------------------

SHEET = SpriteSheet(SRC / "assets" / "nib_sheet.png", SRC / "assets" / "frames.json", 2)
STEP_MS = 50.0
NOTICE_EVERY_MS = 120_000.0


def mascot(tag, seed):
    path = SCRATCH / f"snow_rate_{tag}.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    cfg["last_seen_date"] = "2026-12-10"
    random.seed(seed)
    b = Behavior(cfg, SHEET)
    b.set_strip(1392, 0, 2560)
    b.place_at(1200)
    b.set_world(W.WorldState(hour=14.0, weekday=2, date_key="2026-12-10", week_key="2026-W50",
                             part_of_day=W.PART_DAY, season=W.SEASON_WINTER,
                             cpu_percent=8.0, cpu_busy=False))
    return b


print("\n--- Nib on a snowy window: shivers an hour ---")
HOURS = 4.0
rates = []
for seed in (1, 2, 3):
    b = mascot(f"perch{seed}", seed)
    b.place_at(600)
    b.perch_on((400, 300, 900, 700))
    count, last = 0, None
    notice = int(NOTICE_EVERY_MS / STEP_MS)
    for i in range(int(HOURS * 3_600_000 / STEP_MS)):
        if i % notice == 0:
            b.noticed()
        b.set_snowy_perch(True)
        b.update(STEP_MS, None)
        now = b._antic if b.state is State.ANTIC else None
        if now == B.SNOW_SHIVER_ANIM and last != now:
            count += 1
        last = now
    rates.append(count / HOURS)
    check(f"seed {seed}: still standing on the window after {HOURS:.0f} hours",
          b.perched and b.state in (State.PERCH, State.ANTIC), b.state.name)
print(f"  {', '.join(f'{r:.1f}' for r in rates)} an hour  (asked for: one every "
      f"{B.SNOW_SHIVER_EVERY_MS[0] / 60_000:.1f}-{B.SNOW_SHIVER_EVERY_MS[1] / 60_000:.0f} min)")
check("now and then: 5-15 an hour", all(5.0 <= r <= 15.0 for r in rates),
      ", ".join(f"{r:.1f}" for r in rates))

print("\n--- you brushing snow next to him all hour: play moments an hour ---")
BURST_EVERY_MS = 20_000.0          # a swipe through a pile beside him every 20 s, for an hour
rates = []
kinds = Counter()
for seed in (1, 2, 3):
    b = mascot(f"play{seed}", seed)
    puffs = 0
    every = int(BURST_EVERY_MS / STEP_MS)
    notice = int(NOTICE_EVERY_MS / STEP_MS)
    for i in range(int(3_600_000 / STEP_MS)):
        if i % notice == 0:
            b.noticed()
        if i % every < 6:                        # six samples, 50 ms apart: one burst
            x, y, w, h = b.rect
            b.snow_nearby(x + w + 60, y + h / 2)
        b.update(STEP_MS, None)
        if b.take_snow_puff():
            puffs += 1
            kinds[b._antic] += 1
    rates.append(puffs)
print(f"  {', '.join(str(r) for r in rates)} an hour  ({dict(kinds)}; "
      f"{int(3_600_000 / BURST_EVERY_MS)} bursts asked)")
check("once in a while, not every time: 10-30 an hour against 180 bursts",
      all(10 <= r <= 30 for r in rates), ", ".join(str(r) for r in rates))
check("never more often than his own cooldown allows",
      all(r <= 3_600_000 / B.SNOW_PLAY_COOLDOWN_MS + 1 for r in rates))

print("\n" + "=" * 62)
print("FAILURES: " + ", ".join(fails) if fails else "the snow's rates mean what they say")
sys.exit(1 if fails else 0)
