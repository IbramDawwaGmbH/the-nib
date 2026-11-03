"""He dresses himself when there is a reason to - and never over a costume you picked."""

import os
import sys
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
fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


def fresh(tag, season=W.SEASON_WINTER, busy=False, hour=14.0, weekday=2, **overrides):
    path = SCRATCH / f"dress_{tag}.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    cfg["last_seen_date"] = "2026-07-31"
    for k, v in overrides.items():
        cfg[k] = v
    sheet = SpriteSheet(SRC / "assets" / "nib_sheet.png", SRC / "assets" / "frames.json", 2)
    b = Behavior(cfg, sheet)
    b.set_strip(1392, 0, 2560)
    b.place_at(1200)
    b.set_world(W.WorldState(
        hour=hour, weekday=weekday, date_key="2026-07-31", week_key="2026-W31",
        part_of_day=W.part_of_day(hour), season=season,
        cpu_percent=95.0 if busy else 8.0, cpu_busy=busy))
    return cfg, b


print("\n--- every season dresses him, and the costume exists ---")
sheet = SpriteSheet(SRC / "assets" / "nib_sheet.png", SRC / "assets" / "frames.json", 2)
available = set(sheet.available_costumes())
for season in (W.SEASON_WINTER, W.SEASON_SPRING, W.SEASON_SUMMER, W.SEASON_AUTUMN):
    _cfg, b = fresh(f"s_{season}", season=season)
    worn = b.auto_costume
    check(f"{season:6s} -> {worn or '(nothing)'}", bool(worn) and worn in available, worn)
check("the four are not all the same", len({B.SEASON_COSTUMES[s] for s in B.SEASON_COSTUMES}) == 4,
      str(B.SEASON_COSTUMES))

print("\n--- yours always wins ---")
_cfg, b = fresh("mine", costume="tophat")
check("a costume you picked stops the seasonal one", b.auto_costume == "", b.auto_costume)
check("...and stops a moment dressing him too", not b._may_dress())

_cfg, b = fresh("mine2", costume="   ")   # whitespace is not a choice
check("whitespace is not a chosen costume", b.auto_costume != "", b.auto_costume)

print("\n--- the switches ---")
for tag, kwargs in (("off", {"auto_costumes": False}),
                    ("noworld", {"world_enabled": False}),
                    ("noseason", {"seasonal_enabled": False}),
                    ("nopersona", {"persona_costumes": False})):
    _cfg, b = fresh(tag, **kwargs)
    check(f"{list(kwargs)[0]}=false switches the seasonal costume off", b.auto_costume == "",
          b.auto_costume)

_cfg, b = fresh("noworldstate")
b._world = None
check("with no world snapshot he wears nothing", b.auto_costume == "")

print("\n--- moment costumes are keyed on the MOMENT, not the animation ---")
_cfg, b = fresh("pinned", busy=True)
moment = b._pick_moment()
check("a pinned machine fires a moment", moment is not None, str(moment))
check("...and it comes with a hard hat", moment and moment[2] == "hardhat", str(moment))

_cfg, b = fresh("clockoff", hour=16.05, weekday=4, workday_end="16:00")
found = None
for _ in range(6):
    m = b._pick_moment()
    if m and m[0] == "dance":
        found = m
        break
    b._reaction_cooldown_ms = 0.0
check("knocking off fires the dance", found is not None, str(found))
check("...wearing a party hat", found and found[2] == "party", str(found))

# The distinction the whole design rests on: the SAME animation from the ambient pool gets nothing.
_cfg, b = fresh("ambient", hour=14.0, weekday=4)
hats = set()
for _ in range(400):
    picked = b._pick_antic(None)
    if picked and picked[0] == "dance":
        hats.add(len(picked))
check("an ambient dance carries no costume at all", hats <= {2}, str(hats))

print("\n--- a moment costume is refused when you have picked one ---")
_cfg, b = fresh("momentmine", busy=True, costume="crown")
moment = b._pick_moment()
check("the moment still fires", moment is not None, str(moment))
check("...but bare-headed", moment and moment[2] == "", str(moment))

print("\n--- the momentary one outranks the seasonal one, then hands back ---")
_cfg, b = fresh("layered", busy=True, season=W.SEASON_WINTER)
check("seasonally he is in a beanie", b.auto_costume == "beanie", b.auto_costume)
moment = b._pick_moment()
b._begin_antic(*moment)
check("the moment puts the hard hat on", b.persona_costume == "hardhat", b.persona_costume)
# main prefers persona over auto, which is what makes the hard hat win while it lasts
check("...and that is what the app would show",
      (b.persona_costume or b.auto_costume) == "hardhat")
b._antic_ms = 0.0
b.update(50.0, None)
check("when it ends he goes back to the beanie, not bare-headed",
      (b.persona_costume or b.auto_costume) == "beanie",
      f"persona={b.persona_costume!r} auto={b.auto_costume!r}")

print("\n--- main wires it up that way round ---")
import inspect  # noqa: E402

import main as M  # noqa: E402

src = inspect.getsource(M.NibApp._sync_persona_costume)
check("the app prefers the momentary costume over the seasonal one",
      "persona_costume or self.behavior.auto_costume" in src)
check("Surprise me is connected", "surprise_requested" in inspect.getsource(M.NibApp._connect))
surprise = inspect.getsource(M.NibApp._on_surprise_costume)
check("...never repeats what he already wears", 'c != self.config["costume"]' in surprise)
check("...and goes through the same path as choosing one", "_on_costume_chosen" in surprise)
settings_src = inspect.getsource(M.NibApp._on_settings_changed)
check("a costume picked in Settings gets his opinion too",
      "costume_chosen" in settings_src and "skin_chosen" in settings_src)
check("...after the rebuild, like the tray",
      settings_src.index("_rebuild_appearance") < settings_src.index("costume_chosen"))
check("a colour from the tray gets the sparkle",
      "skin_chosen" in inspect.getsource(M.NibApp._on_skin_chosen))

print("\n--- and the sheet actually has every costume this promises ---")
promised = set(B.SEASON_COSTUMES.values()) | {"hardhat", "party", "glasses"}
check("all of them exist", promised <= available, str(sorted(promised - available)))

print("\n" + "=" * 62)
print("FAILURES: " + ", ".join(fails) if fails else "he dresses himself properly")
sys.exit(1 if fails else 0)
