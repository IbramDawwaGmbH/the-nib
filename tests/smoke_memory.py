"""What he remembers: the counters, the 7-day window, the bond, forgetting, and the file itself."""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import scratch as _scratch  # noqa: E402

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))
SCRATCH = _scratch() / "memory"
SCRATCH.mkdir(parents=True, exist_ok=True)

import memory as MM  # noqa: E402
from config import Config  # noqa: E402
from memory import DayRecord, Memory  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


class FakeClock:
    """Wall time we can move by hand, so a month takes no time at all."""

    def __init__(self, now=1_790_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def fresh(tag, **overrides):
    """A config and a memory file of their own, both gone before the case starts."""
    cfg_path = SCRATCH / f"mem_{tag}.json"
    cfg_path.unlink(missing_ok=True)
    cfg = Config(cfg_path)
    for k, v in overrides.items():
        cfg[k] = v
    mem_path = SCRATCH / f"mem_{tag}.memory.json"
    mem_path.unlink(missing_ok=True)
    return cfg, mem_path


def day(n):
    """Day ``n`` of November 2026 as a date key - the fixtures read as a calendar."""
    return f"2026-11-{n:02d}"


print("\n--- counting, and what a count is filed under ---")
cfg, path = fresh("basic")
m = Memory(path, cfg, FakeClock())
check("a fresh memory is empty", m.is_empty and m.days_seen == 0 and m.first_met == "")
check("...with every lifetime total at zero", set(m.totals.values()) == {0}, str(m.totals))
check("...and a bond of nothing", m.bond() == 0.0 and m.bond_label() == "just met")
m.record("pets", day(4))
m.record("pets", day(4))
m.record("prompts", day(4), n=3)
m.record("present_min", day(4))
check("a day counts what happened on it",
      m.day(day(4)) == DayRecord(prompts=3, pets=2, present_min=1), str(m.day(day(4))))
check("...and the lifetime totals keep up", m.totals["pets"] == 2 and m.totals["prompts"] == 3,
      str(m.totals))
check("...but minutes are per-day only", "present_min" not in m.totals and "busy_min" not in m.totals)
check("a day with nothing in it is all zeros, not missing", m.day(day(1)) == DayRecord())
check("day() hands out a copy", (m.day(day(4)).__setattr__("pets", 99), m.day(day(4)).pets)[1] == 2)
totals = m.totals
totals["pets"] = 1000
check("...and so does totals", m.totals["pets"] == 2)
m.record("nonsense", day(4))
m.record("pets", "not a date")
m.record("pets", day(4), n=0)
check("unknown events, bad dates and zero counts are ignored, not raised",
      m.day(day(4)).pets == 2 and m.totals["pets"] == 2)
check("recording marks it dirty", m.dirty)

print("\n--- the window keeps eight days, the totals keep everything ---")
cfg, path = fresh("window")
m = Memory(path, cfg, FakeClock())
for n in range(1, 13):
    m.record("pets", day(n))
check("only the newest eight days are kept", sorted(m._days) == [day(n) for n in range(5, 13)],
      str(sorted(m._days)))
check("...while the total still has all twelve", m.totals["pets"] == 12)
check("yesterday is the calendar day before", m.yesterday(day(12)) == DayRecord(pets=1))
check("...and None when he has nothing for it", m.yesterday(day(5)) is None)

print("\n--- first sight: once a day, forwards only ---")
cfg, path = fresh("sight")
m = Memory(path, cfg, FakeClock())
check("the first sighting is a new day", m.first_sight(day(4), 8.5) is True)
check("...and it is the day you met", m.first_met == day(4) and m.days_seen == 1)
check("seeing you again the same day is not", m.first_sight(day(4), 14.0) is False)
check("...so days_seen is still one", m.days_seen == 1)
m.save_if_dirty()
again = Memory(path, cfg, FakeClock())
check("a restart the same day does not count it twice",
      again.first_sight(day(4), 15.0) is False and again.days_seen == 1)
check("the next day does", again.first_sight(day(5), 9.0) is True and again.days_seen == 2)
check("a clock wound back a day does not recount", again.first_sight(day(4), 9.0) is False
      and again.days_seen == 2)
check("...and first_met stays the first day", again.first_met == day(4))
check("the arrival hour went into its slot",
      again._arrival_hours[8] == 1 and again._arrival_hours[9] == 1, str(again._arrival_hours))
check("an hour past the end of the clock is clamped, not an IndexError",
      again.first_sight(day(6), 24.7) and again._arrival_hours[23] == 1)
before = list(again._arrival_hours)
check("a day that merely rolled over still counts", again.first_sight(day(7), 0.1, tally=False)
      and again.days_seen == 4)
check("...but is not an arrival in the tally", again._arrival_hours == before,
      str(again._arrival_hours))
again.tally_arrival(8.7)
check("the real arrival is tallied when the app reports it", again._arrival_hours[8] == 2,
      str(again._arrival_hours))
cfg["memory_routine_enabled"] = False
again.tally_arrival(8.7)
check("...and never with the routine switched off", again._arrival_hours[8] == 2)
cfg["memory_routine_enabled"] = True

print("\n--- usual hour: not before five sightings ---")
cfg, path = fresh("usual")
m = Memory(path, cfg, FakeClock())
for n, hour in zip(range(1, 5), (8.2, 8.9, 9.5, 8.0)):
    m.first_sight(day(n), hour)
check("four sightings is not a routine", m.usual_hour() is None)
m.first_sight(day(5), 7.5)
check("five is, and it is the commonest hour", m.usual_hour() == 8.0, str(m.usual_hour()))
check("...which Settings puts in words", any("around 08:00" in line for line in m.summary_lines("Nib")),
      str(m.summary_lines("Nib")))
cfg["memory_routine_enabled"] = False
check("with the routine switched off he claims no usual hour", m.usual_hour() is None)
before = list(m._arrival_hours)
m.first_sight(day(6), 8.0)
check("...and the tally is not touched", m._arrival_hours == before)
check("...but the day still counts", m.days_seen == 6)
m.save_if_dirty()
written = json.loads(path.read_text(encoding="utf-8"))
check("...and the file holds no hour tally at all", "arrival_hours" not in written, str(written.keys()))
check("...and the learned tally is dropped, not kept for later", not any(m._arrival_hours))

print("\n--- a heavy yesterday, judged against the week before it ---")


#: Midday on day 8, local time - "today" for the Settings line, which reads the clock.
NOON_ON_8TH = time.mktime((2026, 11, 8, 12, 0, 0, 0, 0, -1))


def week(tag, loads, yesterday_prompts, yesterday_present=300):
    """Days 1..6 at ``loads`` prompts each, day 7 the one under test, asked on day 8."""
    cfg, path = fresh(tag)
    m = Memory(path, cfg, FakeClock(NOON_ON_8TH))
    for n, load in zip(range(1, 7), loads):
        if load is not None:
            m.record("prompts", day(n), n=load) if load else m.record("present_min", day(n))
    m.record("prompts", day(7), n=yesterday_prompts)
    m.record("present_min", day(7), n=yesterday_present)
    return m


check("twice an ordinary week is heavy", week("heavy", [10] * 6, 20).heavy_yesterday(day(8)))
check("an ordinary day is not", not week("normal", [10] * 6, 12).heavy_yesterday(day(8)))
check("busy minutes count too, ten to a prompt",
      (lambda m: (m.record("busy_min", day(7), n=100), m.heavy_yesterday(day(8)))[1])(
          week("busy", [10] * 6, 10)))
check("a heavy load nobody was there for is not your heavy day",
      not week("absent", [10] * 6, 40, yesterday_present=60).heavy_yesterday(day(8)))
check("two days of history is not a usual to compare against",
      not week("young", [None, None, None, None, 10, 10], 40).heavy_yesterday(day(8)))
check("a week of zeros is not a baseline either",
      not week("zeros", [0] * 6, 40).heavy_yesterday(day(8)))
check("missing days count as zeros, which pulls the median down",
      week("gaps", [None, None, 10, 10, 10, 10], 16).heavy_yesterday(day(8)))
check("no yesterday at all is not heavy", not week("noy", [10] * 6, 20).heavy_yesterday(day(9)))
check("...and Settings says so when it was",
      "Yesterday was a heavy one." in week("line", [10] * 6, 20).summary_lines("Nib"))
check("...and not when it wasn't",
      "Yesterday was a heavy one." not in week("noline", [10] * 6, 12).summary_lines("Nib"))

print("\n--- the bond: grows with what you share, and only grows ---")
cfg, path = fresh("bond")
m = Memory(path, cfg, FakeClock())
seen, previous, monotone = [], -1.0, True
for n in range(1, 400):
    m._days_seen += 1
    m._totals["pets"] += 3
    if n % 2:
        m._totals["prompts"] += 1
    if n % 10 == 0:
        m._totals["focus_done"] += 1
    bond = m.bond()
    monotone &= bond >= previous
    previous = bond
    seen.append(m.bond_label())
check("the bond never goes down while the counters grow", monotone)
check("...and stays inside 0..1", 0.0 <= m.bond() <= 1.0, f"{m.bond():.3f}")
check("...and gets all the way to the top word", m.bond_label() == "inseparable", m.bond_label())
check("...through every word in order", list(dict.fromkeys(seen)) ==
      ["just met", "warming up", "friends", "close", "inseparable"], str(list(dict.fromkeys(seen))))
for value, word in ((0.0, "just met"), (0.1499, "just met"), (0.15, "warming up"),
                    (0.3499, "warming up"), (0.35, "friends"), (0.5999, "friends"),
                    (0.6, "close"), (0.8499, "close"), (0.85, "inseparable"), (1.0, "inseparable")):
    m.bond = lambda v=value: v
    check(f"bond {value} reads as {word!r}", m.bond_label() == word, m.bond_label())
del m.bond
cfg["memory_enabled"] = False
check("with memory off the bond is day one's", m.bond() == 0.0)

print("\n--- the file: atomic, written only when something changed ---")
cfg, path = fresh("file")
m = Memory(path, cfg, FakeClock())
check("nothing recorded, nothing written", m.save_if_dirty() and not path.exists())
m.first_sight(day(4), 8.0)
m.record("pets", day(4))
m.record("prompts", day(4))
check("a save writes the file", m.save_if_dirty() and path.is_file())
written = json.loads(path.read_text(encoding="utf-8"))
check("...holding the counts", written["days"][day(4)]["pets"] == 1
      and written["days"][day(4)]["prompts"] == 1 and written["totals"]["pets"] == 1, str(written))
check("...and exactly the keys PRIVACY.md lists",
      set(written) == {"version", "first_met", "last_seen", "days_seen", "totals", "days",
                       "arrival_hours"}, str(sorted(written)))
check("...and no taste key before there is a taste", "taste" not in written)
check("...and no temporary file left behind", not list(SCRATCH.glob(path.name + ".*.tmp")))
mtime = path.stat().st_mtime_ns
check("a second save with nothing new does not rewrite it",
      m.save_if_dirty() and path.stat().st_mtime_ns == mtime)
back = Memory(path, cfg, FakeClock())
check("it reads back the same", back.totals == m.totals and back.day(day(4)) == m.day(day(4))
      and back.first_met == day(4) and back.days_seen == 1)
check("...and reading it does not mark it dirty", not back.dirty)

print("\n--- a bad file costs nothing but itself ---")
for tag, text in (("corrupt", "{not json"), ("list", "[1, 2, 3]"), ("empty", ""),
                  ("weird", json.dumps({"days_seen": "lots", "totals": {"pets": -5},
                                        "days": {"yesterday": {"pets": 1},
                                                 day(4): {"pets": "x", "prompts": 2}},
                                        "arrival_hours": [1, 2], "first_met": 7}))):
    cfg, path = fresh(tag)
    path.write_text(text, encoding="utf-8")
    try:
        m = Memory(path, cfg, FakeClock())
        ok = True
    except Exception as exc:  # noqa: BLE001 - the point is that nothing escapes
        ok, m = False, None
        print("    raised:", exc)
    check(f"a {tag} file loads without raising", ok)
    if m is not None and tag != "weird":
        check(f"...and is an empty memory", m.is_empty)
weird = m
check("unreadable values become zeros, readable ones survive",
      weird.days_seen == 0 and weird.totals["pets"] == 0 and weird.day(day(4)).prompts == 2
      and weird.day(day(4)).pets == 0, f"{weird.totals} {weird.day(day(4))}")
check("...and a key that isn't a date is dropped", "yesterday" not in weird._days)
check("...as is a tally with the wrong number of hours", weird._arrival_hours == [0] * 24)

print("\n--- switched off: not a byte on disk ---")
cfg, path = fresh("off", memory_enabled=False)
m = Memory(path, cfg, FakeClock())
m.first_sight(day(4), 8.0)
m.record("pets", day(4))
m.adjust_taste("hop", 0.1)
check("nothing is counted", m.is_empty and m.totals["pets"] == 0)
check("...and saving creates no file", m.save_if_dirty() and not path.exists())
cfg, path = fresh("off-later")
m = Memory(path, cfg, FakeClock())
m.record("pets", day(4))
m.save_if_dirty()
stamp = path.read_bytes()
cfg["memory_enabled"] = False
m.record("pets", day(4))
m.save_if_dirty()
check("switching it off leaves an existing file exactly as it was", path.read_bytes() == stamp)

print("\n--- forgetting ---")
cfg, path = fresh("forget")
m = Memory(path, cfg, FakeClock())
m.first_sight(day(4), 8.0)
m.record("pets", day(4), n=5)
m.adjust_taste("hop", 0.2)
m.save_if_dirty()
check("there was something to forget", path.exists() and not m.is_empty)
m.forget()
check("forget() deletes the file", not path.exists())
check("...and empties the memory", m.is_empty and m.days_seen == 0 and m.taste == {})
check("...and has nothing left to write", not m.dirty and m.save_if_dirty() and not path.exists())
check("...and Settings has nothing to say", m.summary_lines("Nib") == [])
cfg, path = fresh("forget-off")
m = Memory(path, cfg, FakeClock())
m.record("pets", day(4))
m.save_if_dirty()
cfg["memory_enabled"] = False
m.forget()
check("forget() works with memory switched off too", not path.exists() and m.is_empty)
m.forget()
check("...and forgetting twice is harmless", not path.exists())

print("\n--- taste: a bounded lean, fading back ---")
cfg, path = fresh("taste")
m = Memory(path, cfg, FakeClock())
for _ in range(1000):
    m.adjust_taste("hop", 0.03)
    m.adjust_taste("dance", -0.03)
check("a thousand rewards still stop at the ceiling", m.taste["hop"] == MM.TASTE_MAX)
check("...and a thousand snubs at the floor", m.taste["dance"] == MM.TASTE_MIN)
m.decay_taste(30)
check("a month without any drifts both back towards 1.0",
      1.0 < m.taste["hop"] < MM.TASTE_MAX and MM.TASTE_MIN < m.taste["dance"] < 1.0, str(m.taste))
m.save_if_dirty()
check("a taste, once there is one, is written", "taste" in json.loads(path.read_text("utf-8")))

print("\n--- in words ---")
cfg, path = fresh("words")
m = Memory(path, cfg, FakeClock())
m.first_sight(day(4), 8.0)
m.record("pets", day(4), n=412)
m.record("prompts", day(4), n=87)
m.record("focus_done", day(4))
lines = m.summary_lines("Pip")
check("it says how long he's known you, with his name", lines[0] == "Pip has known you 1 day "
      "(since 4 Nov 2026).", lines[0])
check("...and the counts, singular where it should be",
      lines[1] == "412 pets · 87 prompts · 1 focus session", lines[1])
check("...with no treats line before there has been a treat", "treat" not in lines[1])
m.record("treats", day(4), n=2)
check("...and one after", m.summary_lines("Pip")[1].endswith("· 2 treats"), m.summary_lines("Pip")[1])

print("\n--- he counts what you do together ---")
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

import world as W  # noqa: E402
from animator import SpriteSheet  # noqa: E402
from behavior import Behavior, State  # noqa: E402

cfg, path = fresh("behavior", wander_enabled=False, antic_chance=0.0, play_with_windows=False,
                  seasonal_enabled=False, last_seen_date=day(4))
mem = Memory(path, cfg, FakeClock())
sheet = SpriteSheet(SRC / "assets" / "nib_sheet.png", SRC / "assets" / "frames.json", 2)
b = Behavior(cfg, sheet, memory=mem)
b.set_strip(1392, 0, 2560)
b.place_at(1200)
b.set_world(W.WorldState(hour=14.0, weekday=2, date_key=day(4), week_key="2026-W45",
                         part_of_day=W.PART_DAY, season=W.SEASON_AUTUMN,
                         cpu_percent=8.0, cpu_busy=False))
check("a Behavior keeps the memory it is given", b.memory is mem)
check("...and makes an in-memory one when it is not", Behavior(cfg, sheet).memory.path is None)

# Pet him first, while he is standing still: a fixed point on him, held like a resting hand.
hand = (b.x + b.sprite_w // 2, b.top + b.sprite_h // 2)
for _ in range(40):
    b.update(50.0, hand)
    if b.state == State.PET:
        break
check("a pet is counted under the world's day", b.state == State.PET and mem.day(day(4)).pets == 1,
      f"{b.state} {mem.day(day(4))}")
for _ in range(100):
    b.update(50.0, None)

# A session about to end on its own, the way smoke_focus drives one.
b.set_focus(True)
b._focus_remaining_ms = 50.0
b.update(100.0, None)
check("a focus session run to the end is counted", mem.day(day(4)).focus_done == 1,
      str(mem.day(day(4))))
b.set_focus(True)
b.set_focus(False)
check("...and one stopped early is not", mem.day(day(4)).focus_done == 1)

b.prompt_sent("hello")
check("a prompt is counted", mem.day(day(4)).prompts == 1, str(mem.day(day(4))))
for _ in range(100):
    b.update(50.0, None)

# A fast sideways flick, the same shape smoke_throw drives: one tick per point along the path.
b.drag_start()
lift_y = int(b._y) - 200
for step in range(12):
    b.drag_to(1000 + 40 * step, lift_y)
    b.update(16, None)
b.drag_end()
check("a throw is counted", mem.day(day(4)).throws == 1, str(mem.day(day(4))))
for _ in range(400):
    b.update(16, None)
b.drag_start()
b.drag_to(1200, lift_y)
b.update(16, None)
b.drag_end()
check("...and a plain let-go is not", mem.day(day(4)).throws == 1, str(mem.day(day(4))))

# Settled by hand rather than by flying: whether the flick above happened to hurt depends on where
# it landed, and this is about the count, not the physics smoke_throw already covers.
for _ in range(400):
    b.update(16, None)
hurts = mem.day(day(4)).hurts
b._pending_injury = True
b._settle()
check("a hard landing is counted", mem.day(day(4)).hurts == hurts + 1 and b.state == State.INJURED,
      str(mem.day(day(4))))
check("...in the lifetime total too", mem.totals["hurts"] == hurts + 1, str(mem.totals))
check("...and none of it wrote a file on its own", not path.exists())

print("\n" + "=" * 62)
print("FAILURES: " + ", ".join(fails) if fails else "counters, window, bond, forget OK")
sys.exit(1 if fails else 0)
