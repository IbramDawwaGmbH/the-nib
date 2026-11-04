"""The lines he says: the gap, the daily cap, the launch quiet, the table, and which line for what.

Qt-free on purpose, like the module it tests: a fake wall clock walks a day in no time at all, and
the phrase table is read straight off disk. What the app does with a line (the toast, and the
moments it is never shown) is checked where a real NibApp boots, in smoke_ui.
"""

import json
import os
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import scratch as _scratch  # noqa: E402

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))
SCRATCH = _scratch() / "voice"
SCRATCH.mkdir(parents=True, exist_ok=True)

import config as C  # noqa: E402
import phrases as P  # noqa: E402
import voice as V  # noqa: E402
from config import Config  # noqa: E402
from phrases import PhraseBag, load_phrases  # noqa: E402
from voice import Voice, lines_for  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


class FakeClock:
    """Wall time we can move by hand. Starts at 09:00 local on a fixed date, so a day is a day."""

    def __init__(self):
        self.now = time.mktime((2026, 11, 4, 9, 0, 0, 0, 0, -1))

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


MIN = 60.0
ASSETS = SRC / "assets"
TABLE = load_phrases(ASSETS, None, filename=V.NIB_PHRASES_FILE, builtin=V.BUILTIN_PHRASES)


def fresh(tag, level="rare", name="Nib"):
    """A config of its own at ``level``, and a voice on the shipped table past its launch quiet."""
    path = SCRATCH / f"voice_{tag}.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    cfg["chatter_level"] = level
    cfg["name"] = name
    clock = FakeClock()
    v = Voice(PhraseBag(TABLE, random.Random(4)), cfg, clock)
    clock.advance(V.LAUNCH_QUIET_S + 1)
    return cfg, clock, v


print("\n--- nothing Qt in here ---")
for module in ("voice.py", "phrases.py"):
    text = (SRC / module).read_text(encoding="utf-8")
    check(f"{module} imports no PySide6", "PySide6" not in text)
check("...and none was loaded by importing them", "PySide6" not in sys.modules)

print("\n--- the setting ---")
check("chatter_level defaults to rare", C.DEFAULTS["chatter_level"] == "rare")
check("its values are exactly the voice's levels",
      set(C._VALID_CHATTER) == set(V.LEVELS), C._VALID_CHATTER)
check("rare is 6 a day, 45 minutes apart", V.LEVELS["rare"] == (45 * 60, 6))
check("normal is 14 a day, 20 minutes apart", V.LEVELS["normal"] == (20 * 60, 14))
check("a level is read case-blind", C._coerce("chatter_level", " Normal ") == "normal")
check("...and nonsense goes back to rare", C._coerce("chatter_level", "LOUD") == "rare")

print("\n--- off is off ---")
cfg, clock, v = fresh("off", "off")
said = []
for _ in range(40):
    said.append(v.say("greeting_short"))
    said.append(v.say("big_week"))
    clock.advance(30 * MIN)
check("off says nothing, all day, whatever the category", not any(said))
check("...and may_speak agrees", not v.may_speak("greeting_overnight"))

print("\n--- the launch quiet ---")
cfg, _c, _v = fresh("quiet")
clock = FakeClock()
v = Voice(PhraseBag(TABLE, random.Random(1)), cfg, clock)
check("nothing in the first minute", v.say("greeting_overnight") == "")
clock.advance(V.LAUNCH_QUIET_S - 1)
check("...not even at 59 s", v.say("greeting_short") == "")
clock.advance(2)
check("...and a line once it has passed", v.say("greeting_short") != "")
check("being refused used nothing up", v.spoken_today == 1, v.spoken_today)

print("\n--- the gap ---")
cfg, clock, v = fresh("gap")
check("the first line is said", v.say("big_week") != "")
clock.advance(44 * MIN)
check("rare: a second line 44 min later is refused", v.say("milestone_days") == "")
clock.advance(2 * MIN)
check("...and 46 min later it is said", v.say("milestone_days") != "")

cfg, clock, v = fresh("gap_normal", "normal")
v.say("big_week")
clock.advance(19 * MIN)
check("normal: 19 min later is refused", not v.may_speak("big_week"))
clock.advance(2 * MIN)
check("...21 min later is fine", v.may_speak("big_week"))

print("\n--- the daily cap ---")
cfg, clock, v = fresh("cap")
for i in range(6):
    check(f"line {i + 1} of the day is said", v.say("fond") != "")
    clock.advance(46 * MIN)
check("the 7th line of the day is refused", v.say("fond") == "")
check("...greeting or not", v.say("greeting_short") == "")
check("six said", v.spoken_today == 6, v.spoken_today)
clock.now = time.mktime((2026, 11, 5, 9, 0, 0, 0, 0, -1))
check("tomorrow the count starts again", v.spoken_today == 0 and v.say("fond") != "")

cfg, clock, v = fresh("cap_normal", "normal")
n = 0
for _ in range(20):
    n += bool(v.say("bored"))
    clock.advance(21 * MIN)
check("normal stops at 14", n == 14, n)

print("\n--- greetings skip the gap, not the cap ---")
cfg, clock, v = fresh("greet")
v.say("big_week")
clock.advance(1 * MIN)
check("a remark a minute after a line is refused", v.say("bored") == "")
check("...but a greeting is not", v.say("greeting_short") != "")
check("...nor a second one straight after", v.say("greeting_overnight") != "")
check("both greetings counted toward the day", v.spoken_today == 3, v.spoken_today)
check("...and they restart the gap for everything else", v.say("fond") == "")
for _ in range(3):
    v.say("greeting_short")
check("six lines in, a greeting is refused too", v.spoken_today == 6 and not v.say("greeting_short"))

print("\n--- once a day ---")
cfg, clock, v = fresh("once", "normal")
check("a heavy yesterday is mentioned", v.say("heavy_yesterday") != "")
clock.advance(3 * 60 * MIN)
check("...once: not again that day", v.say("heavy_yesterday") == "")
check("...though other lines still are", v.say("fond") != "")
clock.now = time.mktime((2026, 11, 5, 9, 0, 0, 0, 0, -1))
check("the next day it may be again", v.may_speak("heavy_yesterday"))

print("\n--- unknown categories, and formatting ---")
cfg, clock, v = fresh("fmt", name="Biscuit")
check("an unknown category is silence, not a crash", v.say("no_such_thing") == "")
check("...and uses nothing up", v.spoken_today == 0 and v.may_speak("fond"))
weird = Voice(PhraseBag({"fond": ["{name} and {nope} and { and {days}"]}, random.Random(0)),
              cfg, clock)
clock.advance(V.LAUNCH_QUIET_S + 1)
line = weird.say("fond", days=7)
check("a broken brace leaves the line as written", line == "{name} and {nope} and { and {days}",
      line)
tidy = Voice(PhraseBag({"fond": ["{name}: {days} days, {nope}"]}, random.Random(0)), cfg, clock)
clock.advance(V.LAUNCH_QUIET_S + 1)
line = tidy.say("fond", days=30)
check("{name} is his name, {days} is filled, an unknown placeholder kept",
      line == "Biscuit: 30 days, {nope}", line)
check("_KeepUnknown keeps a stray placeholder", P._format("a {b} c", {}) == "a {b} c")

print("\n--- the table ---")
raw = json.loads((ASSETS / V.NIB_PHRASES_FILE).read_text(encoding="utf-8"))
shipped = {k: v for k, v in raw.items() if not k.startswith("_")}
check("the shipped table has exactly the voice's categories",
      set(shipped) == set(V.CATEGORIES), sorted(set(shipped) ^ set(V.CATEGORIES)))
check("every category has at least six lines", min(len(x) for x in shipped.values()) >= 6)
check("every category has a built-in fallback", set(V.BUILTIN_PHRASES) == set(V.CATEGORIES))
every = [s for lines in list(shipped.values()) + list(V.BUILTIN_PHRASES.values()) for s in lines]
check("no line is a question", not [s for s in every if "?" in s],
      [s for s in every if "?" in s])
check("every line is short enough for one small toast", max(len(s) for s in every) <= 60,
      max(every, key=len))
placeholders = {m for s in every for m in re.findall(r"\{(\w*)\}", s)}
check("only {name}, {days} and {pets} are used", placeholders <= {"name", "days", "pets"},
      placeholders)

user_dir = SCRATCH / "user"
user_dir.mkdir(exist_ok=True)
(user_dir / V.NIB_PHRASES_FILE).write_text(
    '{"fond": ["My own fond line.", "This is nice."], "brand_new": ["A whole new category."]}',
    encoding="utf-8")
merged = load_phrases(ASSETS, user_dir, filename=V.NIB_PHRASES_FILE, builtin=V.BUILTIN_PHRASES)
check("your own line is merged in", "My own fond line." in merged["fond"])
check("...without duplicating a shipped one", merged["fond"].count("This is nice.") == 1)
check("...or replacing the shipped ones", len(merged["fond"]) == len(shipped["fond"]) + 1)
check("a new category of yours is kept", merged.get("brand_new") == ["A whole new category."])
(user_dir / V.NIB_PHRASES_FILE).write_text("{ not json", encoding="utf-8")
broken = load_phrases(ASSETS, user_dir, filename=V.NIB_PHRASES_FILE, builtin=V.BUILTIN_PHRASES)
check("a broken file of yours is skipped, not fatal", broken["fond"] == shipped["fond"])
bare = load_phrases(SCRATCH / "no-such-dir", None, filename=V.NIB_PHRASES_FILE,
                    builtin=V.BUILTIN_PHRASES)
check("with no table at all, every category still has its fallback",
      all(bare.get(c) for c in V.CATEGORIES))

print("\n--- which line for what ---")
check("the greeting threshold is behaviour's hop threshold",
      re.search(r"^GREET_HOP_AFTER_S = 600\.0", (SRC / "behavior.py").read_text(encoding="utf-8"),
                re.M) is not None and V.GREET_LINE_AFTER_S == 600.0)
check("a wave for the kettle says nothing",
      lines_for("greeted", {"gone_s": 300.0, "overnight": False}) == [])
check("a hop with no known absence says nothing",
      lines_for("greeted", {"gone_s": None, "overnight": False}) == [])
check("ten minutes away is a short greeting",
      lines_for("greeted", {"gone_s": 600.0, "overnight": False}) == ["greeting_short"])
check("overnight is the overnight greeting",
      lines_for("greeted", {"gone_s": 9e4, "overnight": True}) == ["greeting_overnight"])
check("...a heavy yesterday first",
      lines_for("greeted", {"gone_s": 9e4, "overnight": True, "heavy": True})
      == ["heavy_yesterday", "greeting_overnight"])
check("usually at 08, in at 06:50: early",
      lines_for("greeted", {"gone_s": 9e4, "overnight": True}, hour=6.84, usual_hour=8.0)
      == ["early_today", "greeting_overnight"])
check("usually at 08, in at 08:20: just a greeting",
      lines_for("greeted", {"gone_s": 9e4, "overnight": True}, hour=8.33, usual_hour=8.0)
      == ["greeting_overnight"])
check("usually at 08, in at 10:05: late",
      lines_for("greeted", {"gone_s": 9e4, "overnight": True}, hour=10.08, usual_hour=8.0)
      == ["late_today", "greeting_overnight"])
check("no usual hour yet: neither",
      lines_for("greeted", {"gone_s": 9e4, "overnight": True}, hour=5.0, usual_hour=None)
      == ["greeting_overnight"])
check("early and late are only about the first arrival",
      lines_for("greeted", {"gone_s": 3600.0, "overnight": False}, hour=13.0, usual_hour=8.0)
      == ["greeting_short"])
check("a milestone", lines_for("milestone", {"days": 30}) == ["milestone_days"])
check("a focus streak", lines_for("focus_streak", {"count": 3}) == ["focus_streak"])
check("a big week", lines_for("big_week", {}) == ["big_week"])
check("an event nobody wrote lines for says nothing", lines_for("tick", {"n": 1}) == [])

print("\n--- a refused remark falls back to the greeting ---")
cfg, clock, v = fresh("fallback")
v.say("fond")
clock.advance(2 * MIN)
said = ""
for category in lines_for("greeted", {"gone_s": 9e4, "overnight": True, "heavy": True}):
    said = v.say(category)
    if said:
        break
check("the gap refuses the remark, and the greeting is said instead",
      said in TABLE["greeting_overnight"], said)
check("...so the heavy yesterday is still owed", v.may_speak("heavy_yesterday") is False
      and "heavy_yesterday" not in v._made)

print()
if fails:
    print(f"{len(fails)} voice check(s) FAILED")
    sys.exit(1)
print("all voice checks passed")
