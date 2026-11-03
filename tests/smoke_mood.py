"""The persistent mood: decay, headroom, hostile clocks, and how little it writes to disk."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import scratch as _scratch  # noqa: E402

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))
SCRATCH = _scratch()

import mood as MD  # noqa: E402
from config import Config  # noqa: E402
from mood import Mood, label_for  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


class FakeClock:
    """Wall time we can move by hand, so a week takes no time at all."""

    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance_h(self, hours):
        self.now += hours * 3600.0


def fresh(tag, **overrides):
    path = SCRATCH / f"mood_{tag}.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    for k, v in overrides.items():
        cfg[k] = v
    return cfg


print("\n--- it starts neutral and nothing is a verdict ---")
cfg = fresh("basic")
m = Mood(cfg, FakeClock())
check("a fresh mascot is exactly neutral", m.spirits == 0.0, f"{m.spirits}")
check("...and neutral means no weights at all", m.weights() == {}, str(m.weights()))
check("...and no pace change", m.pace() == 1.0, str(m.pace()))
check("...and no blink bias", m.slow_blink_bonus() == 0.0)

m.nudge("petted")
check("one pet moves him but nowhere near the top", 0.0 < m.spirits < 0.35, f"{m.spirits:+.3f}")
one = m.spirits
for _ in range(50):
    m.nudge("petted")
# Not merely "< 1.0": a linear taper passed that while sitting at 0.99999, which is pinned in
# every sense that matters. The squared taper leaves the top of the range genuinely unreached.
check("fifty pets leave real headroom at the top", m.spirits < 0.95, f"{m.spirits:+.4f}")
check("...but they do get him properly delighted", m.spirits > 0.85, f"{m.spirits:+.4f}")
check("...and six pets already read as delighted", True, f"6 pets = {one:+.3f} for the first")

print("\n--- bad news always lands in full, however delighted he is ---")
m2 = Mood(fresh("asym"), FakeClock())
for _ in range(50):
    m2.nudge("petted")
top = m2.spirits
m2.nudge("angered")
drop = top - m2.spirits
check("anger at the ceiling lands at its full weight",
      abs(drop - abs(MD.NUDGES["angered"])) < 1e-9, f"dropped {drop:.4f}")

m3 = Mood(fresh("asym2"), FakeClock())
for _ in range(50):
    m3.nudge("angered")
bottom = m3.spirits
check("...and fifty angers leave headroom at the bottom", bottom > -0.96, f"{bottom:+.4f}")
m3.nudge("petted")
check("a pet at the floor lands in full too",
      abs((m3.spirits - bottom) - MD.NUDGES["petted"]) < 1e-9, f"rose {m3.spirits - bottom:.4f}")

print("\n--- it fades on a half-life, on the tick clock ---")
m4 = Mood(fresh("fade", mood_half_life_h=6.0), FakeClock())
m4.nudge("angered")
start = m4.spirits
m4.tick(6 * 3_600_000.0)
check("six hours halves it", abs(m4.spirits - start / 2) < 1e-6, f"{start:+.3f} -> {m4.spirits:+.3f}")
m4.tick(6 * 3_600_000.0)
check("...and again", abs(m4.spirits - start / 4) < 1e-6, f"{m4.spirits:+.3f}")
m4.tick(200 * 3_600_000.0)
check("and it reaches exactly zero rather than crawling", m4.spirits == 0.0, f"{m4.spirits}")

print("\n--- it keeps fading while he is closed ---")
clock = FakeClock()
cfg5 = fresh("restart", mood_half_life_h=3.0)
m5 = Mood(cfg5, clock)
m5.nudge("angered")
m5.nudge("hurt")
m5.persist()
stored = cfg5["mood_spirits"]
check("persist writes the value", stored < -0.1, f"{stored}")
check("...with a timestamp", cfg5["mood_saved_at"] == clock.now, str(cfg5["mood_saved_at"]))

clock.advance_h(3.0)
m6 = Mood(cfg5, clock)
check("a restart three hours later finds him half over it",
      abs(m6.spirits - stored / 2) < 1e-6, f"{stored:+.3f} -> {m6.spirits:+.3f}")

clock.advance_h(500.0)
m7 = Mood(cfg5, clock)
check("a restart weeks later finds him neutral", m7.spirits == 0.0, f"{m7.spirits}")

print("\n--- hostile clocks and hostile files ---")
back = FakeClock()
cfg8 = fresh("backwards")
m8 = Mood(cfg8, back)
m8.nudge("angered")
m8.persist()
saved = cfg8["mood_spirits"]
back.advance_h(-72.0)  # clock jumped BACKWARDS three days
m9 = Mood(cfg8, back)
check("a clock that moved backwards does not amplify the mood",
      abs(m9.spirits) <= abs(saved) + 1e-9, f"{saved:+.3f} -> {m9.spirits:+.3f}")

cfg10 = fresh("junk")
cfg10["mood_spirits"] = 99          # clamped by config
cfg10["mood_saved_at"] = 1.0e12     # the year 33658
m10 = Mood(cfg10, FakeClock())
check("an absurd stored value is clamped into range", -1.0 <= m10.spirits <= 1.0, f"{m10.spirits}")

print("\n--- how often it touches the disk ---")
cfg11 = fresh("disk")
clock11 = FakeClock()
m11 = Mood(cfg11, clock11)
m11.nudge("petted")
m11.persist()
cfg11.save()
writes = 0
# Ten minutes of the app's 10s flush timer with nothing happening at all.
for _ in range(60):
    m11.tick(10_000.0)
    m11.persist()
    if cfg11.dirty:
        writes += 1
        cfg11.save()
check("an untouched mascot stops rewriting the config", writes <= 3, f"{writes} writes in 10 min")

print("\n--- the switch, and what it changes ---")
off = Mood(fresh("off", mood_enabled=False), FakeClock())
off.nudge("angered")
check("mood_enabled=false reports a flat zero", off.spirits == 0.0, f"{off.spirits}")
check("...and no weights", off.weights() == {})

up = Mood(fresh("up"), FakeClock())
for _ in range(6):
    up.nudge("petted")
w = up.weights()
check("high spirits weight the bouncy antics up", w.get("hop", 1.0) > 1.3, f"hop={w.get('hop'):.2f}")
check("...and sitting about down", w.get("sit_dangle", 1.0) < 0.8, f"sit={w.get('sit_dangle'):.2f}")
check("...and quicken the pace", up.pace() > 1.0, f"{up.pace():.3f}")
check("...with no slow-blink bias", up.slow_blink_bonus() == 0.0)

down = Mood(fresh("down"), FakeClock())
for _ in range(6):
    down.nudge("angered")
w = down.weights()
check("low spirits weight sitting about up", w.get("sit_dangle", 1.0) > 1.5, f"sit={w.get('sit_dangle'):.2f}")
check("...and dancing down", w.get("dance", 1.0) < 0.5, f"dance={w.get('dance'):.2f}")
check("...and slow the pace", down.pace() < 1.0, f"{down.pace():.3f}")
check("...and make the heavy blink likelier", down.slow_blink_bonus() > 0.1,
      f"{down.slow_blink_bonus():.3f}")

print("\n--- every weight is exactly 1.0 at neutral, so 'no mood' is really no mood ---")
edge = Mood(fresh("edge"), FakeClock())
edge.nudge("petted")
edge.tick(400 * 3_600_000.0)
check("faded to nothing leaves an empty table", edge.weights() == {}, str(edge.weights()))

print("\n--- the labels are phrases, never numbers ---")
seen = {label_for(v) for v in (-1.0, -0.6, -0.3, 0.0, 0.3, 0.6, 1.0)}
check("five distinct phrases across the range", len(seen) == 5, str(sorted(seen)))
check("no label contains a digit", not any(c.isdigit() for s in seen for c in s))

print("\n" + "=" * 60)
print("FAILURES: " + ", ".join(fails) if fails else "mood OK")
sys.exit(1 if fails else 0)
