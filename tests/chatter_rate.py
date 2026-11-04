"""How OFTEN does he actually say something across a working day?

smoke_voice says each rule holds. This says what the rules add up to, which is the difference
between a creature who remarks on things and one you switch off by Wednesday. It walks simulated
ten-hour days minute by minute through the real ``Voice`` and ``voice.lines_for``, with the
shipped phrase table, and counts the lines.

Two kinds of day, each run 200 times with different random breaks:

* **Ordinary** - in at about half eight after a night away, a few coffee breaks and a lunch, the
  odd milestone or focus streak, nothing else.
* **Stormy** - the worst case for chatter: back from a ten-to-twenty-minute break every half hour
  all day, a heavy yesterday, an early start, a milestone, a focus streak and a big week, plus a
  needs remark (tired, fond, bored - the lines WP5 will raise) asked for every quarter of an hour.

Both assume nothing ever blocks a line - no bubble, no focus session, no fullscreen app - so the
numbers are an upper bound on what the app shows. Exits non-zero if any day goes over the cap at
its level (6 at rare, 14 at normal) or says anything at all at off.
"""

import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import scratch as _scratch  # noqa: E402

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

import voice as V  # noqa: E402
from config import Config  # noqa: E402
from phrases import PhraseBag, load_phrases  # noqa: E402

SCRATCH = _scratch()
TABLE = load_phrases(SRC / "assets", None, filename=V.NIB_PHRASES_FILE, builtin=V.BUILTIN_PHRASES)
DAYS = 200
USUAL_HOUR = 8.0


class Clock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now


def schedule(rng, stormy):
    """One day's events: (minute after 07:30, kind, data). He is launched at 07:30."""
    events = []
    arrive = 60 + rng.randint(-10, 10) if not stormy else 15  # ~08:30; stormy is in at 07:45
    events.append((arrive, "greeted", {"gone_s": 15 * 3600.0, "overnight": True,
                                       "heavy": stormy}))
    if stormy:
        minute = arrive + 30
        while minute < 600:
            events.append((minute, "greeted", {"gone_s": rng.uniform(600, 1200),
                                               "overnight": False}))
            minute += 30
        for minute in range(arrive + 7, 600, 15):
            events.append((minute, "needs", {"category": rng.choice(("tired", "fond", "bored"))}))
        events += [(150, "milestone", {"days": 30}), (450, "focus_streak", {"count": 3}),
                   (570, "big_week", {})]
    else:
        for centre in (150, 270, 420, 510):  # coffee, lunch-ish, coffee, coffee
            gone = 3600.0 if centre == 270 else rng.uniform(300, 1200)
            events.append((centre + rng.randint(-20, 20), "greeted",
                           {"gone_s": gone, "overnight": False}))
        if rng.random() < 0.1:
            events.append((rng.randint(arrive, 600), "milestone", {"days": 7}))
        if rng.random() < 0.2:
            events.append((rng.randint(400, 600), "focus_streak", {"count": 3}))
    return sorted(events, key=lambda e: e[0])


def run_day(level, seed, stormy):
    cfg_path = SCRATCH / "chatter_rate.json"
    cfg_path.unlink(missing_ok=True)
    cfg = Config(cfg_path)
    cfg["chatter_level"] = level
    rng = random.Random(seed)
    start = time.mktime((2026, 11, 4, 7, 30, 0, 0, 0, -1))
    clock = Clock(start)
    voice = V.Voice(PhraseBag(TABLE, random.Random(seed)), cfg, clock)
    said = []
    for minute, kind, data in schedule(rng, stormy):
        clock.now = start + minute * 60.0
        hour = 7.5 + minute / 60.0
        if kind == "needs":
            categories = [data["category"]]
        else:
            categories = V.lines_for(kind, data, hour=hour, usual_hour=USUAL_HOUR)
        for category in categories:
            line = voice.say(category, days=data.get("days", 30), pets=412)
            if line:
                said.append((minute, category))
                break
    return said


def measure(level, stormy):
    counts = [len(run_day(level, seed, stormy)) for seed in range(DAYS)]
    return min(counts), sum(counts) / len(counts), max(counts)


ok = True
print(f"\n{DAYS} simulated ten-hour days per row (07:30-17:30), nothing ever blocking a line\n")
print(f"    {'level':7s} {'day':9s} {'min':>4s} {'mean':>6s} {'max':>4s}   cap")
for level in ("off", "rare", "normal"):
    cap = 0 if V.LEVELS[level] is None else V.LEVELS[level][1]
    for stormy in (False, True):
        lo, mean, hi = measure(level, stormy)
        bad = hi > cap
        ok &= not bad
        print(f"    {level:7s} {'stormy' if stormy else 'ordinary':9s} {lo:4d} {mean:6.2f} {hi:4d}"
              f"   {cap:3d}{'  OVER THE CAP' if bad else ''}")

example = run_day("rare", 1, True)
print("\nOne stormy day at rare, line by line:")
for minute, category in example:
    t = 7.5 * 60 + minute
    print(f"    {int(t // 60):02d}:{int(t % 60):02d}  {category}")

print("\nchatter stays under its caps" if ok else "\nchatter went OVER a cap")
sys.exit(0 if ok else 1)
