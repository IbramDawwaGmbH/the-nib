"""Headless checks on the world-awareness layer: clock, moments, latches, mood, personas."""

import os
import random
import sys
from datetime import datetime
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
from behavior import Behavior  # noqa: E402
from config import Config  # noqa: E402

SCRATCH = _scratch()
FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"  OK   {label}" + (f"  [{detail}]" if detail else ""))
    else:
        print(f"  FAIL {label}  {detail}")
        FAILURES.append(label)


def fresh(name="w.json", **overrides):
    path = SCRATCH / name
    path.unlink(missing_ok=True)
    cfg = Config(path)
    for key, value in overrides.items():
        cfg[key] = value
    sheet = SpriteSheet(SRC / "assets" / "nib_sheet.png", SRC / "assets" / "frames.json", 2)
    return cfg, Behavior(cfg, sheet)


def at(hour, weekday=2, season=W.SEASON_SUMMER, date="2026-07-30", week="2026-W31",
       cpu=12.0, busy=False, task_just_finished=False):
    return W.WorldState(
        hour=hour, weekday=weekday, date_key=date, week_key=week,
        part_of_day=W.part_of_day(hour), season=season, cpu_percent=cpu, cpu_busy=busy,
        task_just_finished=task_just_finished,
    )


print("\n--- clock and calendar ---")
check("night before 05:00", W.part_of_day(4.99) == W.PART_NIGHT)
check("morning at 05:00", W.part_of_day(5.0) == W.PART_MORNING)
check("day at 09:30", W.part_of_day(9.5) == W.PART_DAY)
check("evening at 18:00", W.part_of_day(18.0) == W.PART_EVENING)
check("night again at 22:00", W.part_of_day(22.0) == W.PART_NIGHT)
check("december is winter", W.season_for(12) == W.SEASON_WINTER)
check("december is summer down south", W.season_for(12, southern=True) == W.SEASON_SUMMER)
check("parse 16:00", W.parse_clock("16:00") == 16.0)
check("parse bare hour", W.parse_clock("16") == 16.0)
check("parse half past", W.parse_clock("16:30") == 16.5)
check("reject empty/garbage/out-of-range",
      all(W.parse_clock(s) is None for s in ("", "  ", "nope", "25:00", "16:99", "-1:00")))

print("\n--- a live snapshot ---")
w = W.World()
s = w.read(datetime(2026, 7, 31, 16, 5))
check("friday detected", s.is_friday and not s.is_weekend, f"weekday={s.weekday}")
check("iso week key", s.week_key == "2026-W31", s.week_key)
check("saturday is weekend", w.read(datetime(2026, 8, 1, 12, 0)).is_weekend)

print("\n--- once-a-day latches ---")
cfg, b = fresh()
b.set_world(at(9.0))
check("stretch fires on first sight today", b._pick_moment()[0] == "stretch")
b._reaction_cooldown_ms = 0
check("and not a second time", (b._pick_moment() or ("", 0))[0] != "stretch")
check("latch persisted to config", cfg["last_seen_date"] == "2026-07-30", cfg["last_seen_date"])

# A brand new Behavior on the SAME config must not replay the day's moments.
b2 = Behavior(cfg, b.sheet)
b2.set_world(at(9.0))
check("survives a restart", (b2._pick_moment() or ("", 0))[0] != "stretch")

cfg, b = fresh("w2.json")
b.set_world(at(2.0))  # 02:00 - a machine left running past midnight
random.seed(5)
overnight = []
for _ in range(60):
    b._reaction_cooldown_ms = 0
    b._seasonal_cooldown_ms = 0
    overnight.append((b._pick_moment() or ("", 0))[0])
check("no stretch at 2am", "stretch" not in overnight)
check("nothing at all fires in the small hours", set(overnight) == {""}, str(set(overnight)))

print("\n--- knocking off at 16:00 ---")
cfg, b = fresh("w3.json", workday_end="16:00", last_seen_date="2026-07-30")
b.set_world(at(16.05))
check("dances at 16:03", (b._pick_moment() or ("", 0))[0] == "dance")

cfg, b = fresh("w4.json", workday_end="16:00", last_seen_date="2026-07-30")
b.set_world(at(18.0))  # launched at six - the window has closed
check("does NOT fire late at 18:00", (b._pick_moment() or ("", 0))[0] != "dance")

cfg, b = fresh("w5.json", workday_end="16:00", last_seen_date="2026-08-01")
b.set_world(at(16.05, weekday=5, date="2026-08-01"))  # saturday
check("no knocking-off at the weekend", (b._pick_moment() or ("", 0))[0] != "dance")

cfg, b = fresh("w6.json", workday_end="", last_seen_date="2026-07-30")
b.set_world(at(16.05))
check("empty workday_end disables it", (b._pick_moment() or ("", 0))[0] != "dance")

print("\n--- the week's over flop ---")
cfg, b = fresh("w7.json", workday_end="16:00", last_seen_date="2026-07-31",
               last_clock_off_date="2026-07-31")
b.set_world(at(16.8, weekday=4, date="2026-07-31"))  # friday, 16:48
check("flops on friday evening", (b._pick_moment() or ("", 0))[0] == "sit_dangle")
b._reaction_cooldown_ms = 0
check("only once", (b._pick_moment() or ("", 0))[0] != "sit_dangle")
check("latched on the ISO week", cfg["last_week_over_week"] == "2026-W31")

cfg, b = fresh("w8.json", workday_end="16:00", last_seen_date="2026-07-30",
               last_clock_off_date="2026-07-30")
b.set_world(at(16.8, weekday=3))  # thursday
check("not on a thursday", (b._pick_moment() or ("", 0))[0] != "sit_dangle")

print("\n--- one shared reaction budget ---")
cfg, b = fresh("w9.json", workday_end="16:00")
b.set_world(at(16.05, cpu=95.0, busy=True))
first = b._pick_moment()
check("something fires", first is not None, first[0] if first else "nothing")
b._reaction_cooldown_ms = B.REACTION_COOLDOWN_MS  # as _choose_next_action would set it
check("nothing else can fire until it expires", b._pick_moment() is None)
# update() clamps dt to 250ms so a stalled process cannot teleport him, so the budget has to be
# run down in real-sized ticks rather than one big one.
#
# The LOW-WATER MARK, not the final value. Reading the value at the end asserts something subtly
# different and wrong: the budget can legitimately be spent again the instant it refills, by the
# next moment that is due - here the 16:00 knocking-off dance. This test has no walk strip, so
# every wander target resolves within 4px and he lands back at a decision point several times a
# second, which makes that re-spend near-certain rather than rare. What is actually being checked
# is that the budget comes back at all.
lowest = B.REACTION_COOLDOWN_MS
for _ in range(int(B.REACTION_COOLDOWN_MS / 200) + 2):
    b.update(200.0, None)
    lowest = min(lowest, b._reaction_cooldown_ms)
check("budget refills", lowest == 0, f"low-water mark {lowest:.0f}ms")

print("\n--- machine under load ---")
cfg, b = fresh("w10.json", last_seen_date="2026-07-30")
b.set_world(at(14.0, cpu=93.0, busy=True))
check("fans himself when pinned", (b._pick_moment() or ("", 0))[0] == "hot")
b._reaction_cooldown_ms = 0
check("then holds off", b._pick_moment() is None, "hot cooldown")

# Summer's seasonal moment is also "hot", so that has to be off for this to isolate the CPU path.
cfg, b = fresh("w11.json", last_seen_date="2026-07-30", cpu_reaction_enabled=False,
               seasonal_enabled=False)
b.set_world(at(14.0, cpu=93.0, busy=True))
check("respects cpu_reaction_enabled=false", b._pick_moment() is None)

cfg, b = fresh("w12.json", last_seen_date="2026-07-30", seasonal_enabled=False)
b.set_world(at(14.0, cpu=93.0, busy=True))
check("...and still reacts when it is on", (b._pick_moment() or ("", 0))[0] == "hot")

print("\n--- a finished task ---")
cfg, b = fresh("w13.json", last_seen_date="2026-07-30")
b.set_world(at(14.0, cpu=20.0, busy=False, task_just_finished=True))
check("celebrates once a sustained load drops back to quiet",
      (b._pick_moment() or ("", 0))[0] == "task_finished")
b._reaction_cooldown_ms = 0
check("then holds off on its own cooldown", b._pick_moment() is None, "task_finished cooldown")

cfg, b = fresh("w14.json", last_seen_date="2026-07-30", task_finished_reaction_enabled=False)
b.set_world(at(14.0, cpu=20.0, busy=False, task_just_finished=True))
check("respects task_finished_reaction_enabled=false", b._pick_moment() is None)

# Being pinned right now and having just finished are mutually exclusive in practice (the flag
# only turns on after load has already dropped), but the priority still has to be sane if a test
# ever hands both flags true at once.
cfg, b = fresh("w15.json", last_seen_date="2026-07-30")
b.set_world(at(14.0, cpu=93.0, busy=True, task_just_finished=True))
check("a still-pinned machine takes priority over a stale finished-flag",
      (b._pick_moment() or ("", 0))[0] == "hot")

print("\n--- seasons ---")
for season, expected in ((W.SEASON_WINTER, "shiver"), (W.SEASON_AUTUMN, "autumn")):
    cfg, b = fresh(f"ws_{season}.json", last_seen_date="2026-07-30")
    b.set_world(at(14.0, season=season))
    random.seed(7)
    got = None
    for _ in range(40):
        b._seasonal_cooldown_ms = 0
        b._reaction_cooldown_ms = 0
        moment = b._pick_moment()
        if moment and moment[0] == expected:
            got = moment[0]
            break
    check(f"{season} -> {expected}", got == expected, str(got))

def seasonal_moments(season, tag, rounds=40, **overrides):
    cfg, b = fresh(tag, last_seen_date="2026-07-30", **overrides)
    b.set_world(at(14.0, season=season))
    random.seed(3)
    seen = []
    for _ in range(rounds):
        b._seasonal_cooldown_ms = 0
        b._reaction_cooldown_ms = 0
        seen.append((b._pick_moment() or ("", 0))[0])
    return set(seen)

check("spring has no moment of its own",
      seasonal_moments(W.SEASON_SPRING, "ws_spring.json") == {""})

# Summer's seasonal animation IS the machine-under-load one. If both fired, "it is July" and
# "your machine is pinned" would be indistinguishable, so summer stands aside while the load
# reaction is on - and takes over when it is off.
check("summer defers while the load reaction is on",
      seasonal_moments(W.SEASON_SUMMER, "ws_summer_on.json") == {""})
check("...and takes over when it is off",
      "hot" in seasonal_moments(W.SEASON_SUMMER, "ws_summer_off.json",
                                cpu_reaction_enabled=False))

print("\n--- mood: weights and pace ---")
cfg, b = fresh("wm.json", workday_end="16:00")
b.set_world(at(2.0))
night = b._mood_weights()
check("dancing suppressed at 2am", night.get("dance", 1.0) < 0.3, f"x{night.get('dance')}")
check("sitting boosted at 2am", night.get("sit_dangle", 1.0) > 2.0, f"x{night.get('sit_dangle')}")
check("night pace is slower", b._mood_pace() < 0.8, f"x{b._mood_pace():.2f}")

b.set_world(at(14.0, weekday=4))  # friday afternoon
check("friday afternoon boosts dancing", b._mood_weights().get("dance", 1.0) >= 3.0)
b.set_world(at(14.0, weekday=2))
check("wednesday afternoon does not", b._mood_weights().get("dance", 1.0) < 2.0)
check("daytime pace is normal", b._mood_pace() == 1.0)

cfg, b = fresh("wm2.json", world_enabled=False)
b.set_world(at(2.0))
check("world_enabled=false neutralises mood", b._mood_weights() == {} and b._mood_pace() == 1.0)

# A mood must never zero out the whole pool.
cfg, b = fresh("wm3.json")
b.set_world(at(2.0))
random.seed(11)
picked = {b._pick_antic(None)[0] for _ in range(200)}
check("still finds something to do at 2am", len(picked) >= 3, f"{len(picked)} distinct")

print("\n--- the walk actually slows down at night ---")


def travelled(hour):
    cfg, b = fresh(f"wp_{int(hour)}.json")
    b.set_strip(1000, 0, 4000)
    b.place_at(200)
    b.set_world(at(hour))
    b._begin_walk(1800)
    start = b._x
    for _ in range(400):
        b.update(20.0, None)
    return b._x - start


day, night_dist = travelled(14.0), travelled(2.0)
check("slower at 2am than at 2pm", night_dist < day * 0.85,
      f"{night_dist:.0f}px vs {day:.0f}px = {night_dist / day:.2f}x")

print("\n--- app personas ---")
EXPECTED = {
    "code.exe": ("typing", "glasses"),
    "cursor.exe": ("typing", "glasses"),
    "calculatorapp.exe": ("counting", ""),
    "excel.exe": ("spreadsheet", ""),
    "outlook.exe": ("mail", ""),
    "olk.exe": ("mail", ""),
    "thunderbird.exe": ("mail", ""),
}
for exe, (antic, costume) in EXPECTED.items():
    cfg, b = fresh(f"wpr_{exe}.json")
    b.foreground_changed(exe)
    check(f"{exe} -> {antic}", b._antic == antic, str(b._antic))
    check(f"  ...wearing {costume or 'nothing'}", b.persona_costume == costume, b.persona_costume)

cfg, b = fresh("wpc.json", costume="pirate")
b.foreground_changed("code.exe")
check("a chosen costume beats the persona hat", b.persona_costume == "", b.persona_costume)

cfg, b = fresh("wpc2.json", persona_costumes=False)
b.foreground_changed("code.exe")
check("persona_costumes=false means no hat", b.persona_costume == "")
check("but the antic still plays", b._antic == "typing")

cfg, b = fresh("wpc3.json", persona_apps={"blender.exe": "football"})
b.foreground_changed("blender.exe")
check("persona_apps extends the table", b._antic == "football", str(b._antic))

cfg, b = fresh("wpc4.json", persona_apps={"code.exe": "dance"})
b.foreground_changed("code.exe")
check("and overrides a built-in", b._antic == "dance", str(b._antic))

cfg, b = fresh("wpc5.json", persona_apps={"thing.exe": "not_a_real_state"})
b.foreground_changed("thing.exe")
check("an unknown state name is ignored, not fatal", b._antic != "not_a_real_state", str(b._antic))

print("\n--- the hat comes off again ---")
cfg, b = fresh("wh.json")
b.foreground_changed("code.exe")
check("hat on during the antic", b.persona_costume == "glasses")
b._begin_antic("dance", 500)
check("hat off for an unrelated antic", b.persona_costume == "", b.persona_costume)
b.foreground_changed("code.exe")
b._foreground_cooldown_ms = 0
for _ in range(400):
    b.update(20.0, None)
check("hat off once he stops", b.persona_costume == "", b.persona_costume)

print("\n--- cpu sampler guards ---")
c = W.CpuSampler(busy_percent=50.0)
check("first read is not a spike", c.read() == 0.0)
c._previous, c._previous_at = (0, 0), -10_000.0  # pretend a resume: an enormous gap
c._busy_for_s = 99.0
c.read()
check("a resume-sized gap resets the sustain", c._busy_for_s == 0.0)

print("\n--- the task-finished pattern ---")
c = W.CpuSampler(busy_percent=70.0, task_quiet_percent=35.0, task_min_busy_s=45.0,
                 task_finished_hold_s=12.0)
for _ in range(50):
    c._update_task_pattern(80.0, 1.0)  # 50s sustained
check("not finished mid-task", not c.task_just_finished)
c._update_task_pattern(20.0, 1.0)  # the drop
check("fires once genuinely quiet after a long stretch", c.task_just_finished)
for _ in range(11):
    c._update_task_pattern(20.0, 1.0)
check("stays true through the hold window", c.task_just_finished)
c._update_task_pattern(20.0, 2.0)
check("clears once the hold window runs out", not c.task_just_finished)

c = W.CpuSampler(busy_percent=70.0, task_quiet_percent=35.0, task_min_busy_s=45.0)
for _ in range(50):
    c._update_task_pattern(80.0, 1.0)
c._update_task_pattern(50.0, 1.0)  # dips into the dead zone - not busy, not quiet either
check("a dip into the dead zone does not fire early", not c.task_just_finished)
c._update_task_pattern(20.0, 1.0)  # now genuinely quiet
check("...but it still fires once load actually goes quiet", c.task_just_finished)

c = W.CpuSampler(busy_percent=70.0, task_quiet_percent=35.0, task_min_busy_s=45.0)
for _ in range(10):
    c._update_task_pattern(80.0, 1.0)  # only 10s busy - a spike, not a task
c._update_task_pattern(20.0, 1.0)
check("a short spike is not a finished task", not c.task_just_finished)

c = W.CpuSampler(busy_percent=70.0)
c._task_busy_for_s = 99.0
c._task_armed = True
c._reset_task_pattern()
check("a resume-sized gap discards an in-progress episode",
      not c._task_armed and c._task_busy_for_s == 0.0)

print("\n" + ("=" * 60))
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): " + ", ".join(FAILURES))
    sys.exit(1)
print("all world checks passed")
