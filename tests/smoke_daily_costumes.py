"""The costume of the day: a whole calendar of them, each held to the rules every costume is.

``smoke_costumes`` walks the wardrobe's fifty-three across every frame. Doing the same for 366
would be most of a run_all on its own, so this walks each of them across the frames that
actually catch costumes out - every standing and walking frame, the arm-raised ones, sitting,
asleep, mid-hop - and checks the same invariants through the real runtime: on the baseline, off
his face, no partial alpha, feet still on the ground, and frames that genuinely move.

Then the parts that are new: the calendar has every date, February the 29th included; the month
sheets load only when a costume from that month is worn; the date outranks the season and yours
outranks the date; and the tray files 366 of them under Calendar, a month per submenu.
"""

import datetime
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

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

import animator as A  # noqa: E402
import costume_calendar as CC  # noqa: E402
import world as W  # noqa: E402
from animator import SpriteSheet  # noqa: E402
from behavior import Behavior  # noqa: E402
from config import Config  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


ASSETS = SRC / "assets"
SHEET, FRAMES = ASSETS / "nib_sheet.png", ASSETS / "frames.json"
manifest = json.loads((ASSETS / "daily_costumes.json").read_text(encoding="utf-8"))
items, days = manifest["items"], manifest["days"]

print("\n--- the calendar ---")
every_date = [f"{m:02d}-{d:02d}" for m in range(1, 13)
              for d in range(1, [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1] + 1)]
check("all 366 dates, February the 29th included", sorted(days) == every_date,
      f"{len(days)} dates; missing {sorted(set(every_date) - set(days))[:5]}")
check("366 different costumes, one a day", len(set(days.values())) == 366, len(set(days.values())))
check("every day's costume exists", all(n in items for n in days.values()))
check("...and every costume is some day's", set(items) == set(days.values()))
check("each knows its own date", all(items[n]["date"] == d for d, n in days.items()))
check("each has a title for the menus",
      all(str(i.get("title", "")).strip() and len(i["title"]) <= 32 for i in items.values()))
check("each animates, in 2 to 4 frames",
      all(2 <= len(i.get("frames", [])) <= 4 for i in items.values()),
      [n for n, i in items.items() if not 2 <= len(i.get("frames", [])) <= 4][:5])
check("each lives on its own month's sheet",
      all(i["sheet"] == f"daily/costumes-{i['date'][:2]}.png" for i in items.values()))
check("the cell matches the wardrobe's", manifest["cell"] ==
      json.loads((ASSETS / "costumes.json").read_text(encoding="utf-8"))["cell"])

bare = SpriteSheet(SHEET, FRAMES, 1)
check("no name clashes with the wardrobe's own", not set(items) & set(bare.available_costumes()),
      sorted(set(items) & set(bare.available_costumes())))
check("the sheet offers every one of them", bare.daily_costumes() == list(items))
check("...without them leaking into the wardrobe's list",
      not set(bare.available_costumes()) & set(items))
check("...or its categories", not any(n in items for v in bare.costume_categories().values()
                                       for n in v))
check("the calendar groups them a month at a time",
      list(bare.calendar_groups()) == [f"Calendar/{datetime.date(2024, m, 1):%B}" for m in range(1, 13)])
check("a daily costume's category is still what it IS, for his opinion of it",
      all(bare.costume_category(n) in {"Hats", "Head", "Eyes", "Neck", "Body", "Behind"}
          for n in items))

print("\n--- the month sheets ---")
from PySide6.QtGui import QImage  # noqa: E402

for month in range(1, 13):
    path = ASSETS / "daily" / f"costumes-{month:02d}.png"
    image = QImage(str(path))
    month_items = [i for i in items.values() if i["date"].startswith(f"{month:02d}-")]
    columns = sum(len(i["frames"]) for i in month_items)
    ok = (not image.isNull() and image.width() == columns * manifest["cell"]
          and image.height() == 2 * manifest["cell"])
    if not ok:
        check(f"costumes-{month:02d}.png is {columns} cells by 2", False,
              f"{image.width()}x{image.height()}")
check("each month's sheet is exactly its costumes' frames wide, art over shadow",
      not any("costumes-" in f for f in fails))

print("\n--- loaded lazily ---")
A._EXTRA_SHEETS.clear()
SpriteSheet(SHEET, FRAMES, 2)
check("building the sheet decodes no month at all", not A._EXTRA_SHEETS, list(A._EXTRA_SHEETS))
march = days["03-14"]
dressed = SpriteSheet(SHEET, FRAMES, 2, costume=march)
dressed.pixmap("idle", 0)
check("wearing a March costume decodes March and nothing else",
      [Path(p).name for p in A._EXTRA_SHEETS] == ["costumes-03.png"], list(A._EXTRA_SHEETS))
started = time.perf_counter()
SpriteSheet(SHEET, FRAMES, 2, costume=days["03-15"]).pixmap("idle", 0)
check("a second March costume reuses it", len(A._EXTRA_SHEETS) == 1)
check("...and dresses him quickly", time.perf_counter() - started < 0.25,
      f"{(time.perf_counter() - started) * 1000:.0f}ms")
check("its thumbnail is cut from that sheet", bare.costume_thumbnail(march) is not None)
check("a menu calls it by date and title", bare.costume_menu_labels()[march]
      == f"14 · {items[march]['title']}", bare.costume_menu_labels()[march])

print("\n--- costume_calendar ---")
check("a date picks its costume", CC.costume_for(datetime.date(2031, 3, 14)) == march)
check("...so does the world's date key", CC.costume_for("2026-03-14") == march)
check("the 29th of February has one of its own", CC.costume_for(datetime.date(2028, 2, 29))
      == days["02-29"] and days["02-29"] not in (days["02-28"], days["03-01"]))
check("the title comes along", CC.title_for(march) == items[march]["title"])
check("...and nothing for a wardrobe costume", CC.title_for("tophat") == "")
check("no calendar at all is no costume, not a crash",
      CC.costume_for("2026-03-14", Path(_scratch()) / "no-such.json") == "")

print("\n--- he wears it ---")
SCRATCH = _scratch()


def fresh(tag, date_key="2026-03-14", season=W.SEASON_SPRING, **overrides):
    path = SCRATCH / f"daily_{tag}.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    cfg["last_seen_date"] = date_key
    for k, v in overrides.items():
        cfg[k] = v
    b = Behavior(cfg, SpriteSheet(SHEET, FRAMES, 2))
    b.set_strip(1392, 0, 2560)
    b.place_at(1200)
    b.set_world(W.WorldState(hour=14.0, weekday=5, date_key=date_key, week_key="2026-W11",
                             part_of_day=W.part_of_day(14.0), season=season,
                             cpu_percent=8.0, cpu_busy=False))
    return cfg, b


_cfg, b = fresh("today")
check("the date's costume, ahead of the season's", b.auto_costume == march, b.auto_costume)
_cfg, b = fresh("leap", date_key="2028-02-29", season=W.SEASON_WINTER)
check("on the leap day, the leap day's", b.auto_costume == days["02-29"], b.auto_costume)
_cfg, b = fresh("mine", costume="tophat")
check("yours still wins", b.auto_costume == "", b.auto_costume)
_cfg, b = fresh("off", daily_costumes=False)
check("daily_costumes off falls back to the season", b.auto_costume == "flowercrown",
      b.auto_costume)
_cfg, b = fresh("alloff", auto_costumes=False)
check("auto_costumes off takes the calendar off too", b.auto_costume == "")
_cfg, b = fresh("noseason", seasonal_enabled=False)
check("the calendar does not hang off the seasonal switch", b.auto_costume == march,
      b.auto_costume)
_cfg, b = fresh("nopersona", persona_costumes=False)
check("...but persona_costumes off is 'do not dress him', and that holds", b.auto_costume == "")
_cfg, b = fresh("picked", costume=days["07-04"])
check("a costume of the day picked from the tray is just yours", b.auto_costume == "")

print("\n--- the tray ---")
from tray import Tray  # noqa: E402
from PySide6.QtGui import QIcon, QPixmap  # noqa: E402

_pm = QPixmap(16, 16)
tray = Tray(QIcon(_pm), costumes={**bare.costume_categories(), **bare.calendar_groups()},
            skins=["default"], personalities=["calm"], costume_labels=bare.costume_menu_labels())
# Walked up from an action rather than down from the tray: the submenus belong to Qt, and a
# wrapper fetched through QAction.menu() can outlive the object it wraps.
month_menu = tray._costume_actions[march].parent()
calendar_menu = month_menu.parent()
costume_menu = calendar_menu.parent()
check("one Calendar submenu, beside the wardrobe's categories",
      calendar_menu.title() == "Calendar" and costume_menu.title() == "Costume"
      and [m.title() for m in costume_menu.findChildren(type(calendar_menu))
           if m.parent() is costume_menu].count("Calendar") == 1)
months = [m.title() for m in calendar_menu.findChildren(type(calendar_menu))
          if m.parent() is calendar_menu]
check("...holding the twelve months in order", months == [f"{datetime.date(2024, m, 1):%B}"
                                                           for m in range(1, 13)], months)
check("every costume is reachable from it", len(tray._costume_actions) ==
      1 + len(bare.available_costumes()) + 366, len(tray._costume_actions))
check("...labelled by its date", tray._costume_actions[march].text().startswith("14 · "))

print("\n--- every costume, on the frames that catch them out ---")
#: Standing, walking, both arm-raised poses, a stretch, sitting, napping, the hop's take-off and
#: landing, and eating a treat (his hand comes up to his mouth, past a scarf). Upside down (injured, tumble) and mid-air are exempt, as smoke_costumes makes them.
SAMPLE = [("idle", i) for i in range(4)] + [("walk_right", i) for i in range(6)] + [
    ("wave", 1), ("wave", 2), ("scratch", 1), ("stretch", 2), ("sit_dangle", 1), ("sleep", 0),
    ("hop", 0), ("hop", 4), ("dance", 1), ("pushups", 2)] + [("eat", i) for i in range(4)]
SAMPLE = [(s, i) for s, i in SAMPLE if bare.has_state(s) and i < bare.frame_count(s)]
EYE_TOP, EYE_BOTTOM, FACE = 26, 31, range(15, 33)
BODY_BOTTOM, HANG = 41, 4


def rows_of(image):
    """(alpha rows, the image) - a frame as bytes rather than 2304 pixelColor calls."""
    image = image.convertToFormat(QImage.Format_ARGB32)
    data = bytes(image.constBits())
    stride = image.bytesPerLine()
    return [[data[y * stride + x * 4 + 3] for x in range(image.width())]
            for y in range(image.height())], data, stride


bare_idle = rows_of(bare.pixmap("idle", 0).toImage())
baseline_row = bare.frame_h - 1 - bare.baseline_offset
problems: dict[str, list[str]] = {}
started = time.perf_counter()
for name, item in items.items():
    sheet = SpriteSheet(SHEET, FRAMES, 1, costume=name)
    issues = []
    seen_frames = set()
    for state, index in SAMPLE:
        alpha, data, stride = rows_of(sheet.pixmap(state, index).toImage())
        lowest = max((y for y, row in enumerate(alpha) if any(row)), default=-1)
        if lowest > baseline_row:
            issues.append(f"below his soles on {state}[{index}]")
        if any(0 < a < 255 for row in alpha for a in row):
            issues.append(f"partial alpha on {state}[{index}]")
        if state != "hop":
            _l, _r, lift = sheet.foot_print(state, index)
            if lift > 0:
                issues.append(f"lifts his feet on {state}[{index}]")
        if state == "idle":
            seen_frames.add(data)
    if len(seen_frames) < 2:
        issues.append("does not move over his idle sway")
    alpha, data, stride = rows_of(sheet.pixmap("idle", 0).toImage())
    _ba, bdata, _bs = bare_idle
    changed = [y for y in range(48) if any(data[y * stride + x * 4: y * stride + x * 4 + 4]
                                           != bdata[y * stride + x * 4: y * stride + x * 4 + 4]
                                           for x in FACE)]
    if item["kind"] != "eyes" and item.get("layer") != "behind" and \
            any(EYE_TOP <= y <= EYE_BOTTOM for y in changed):
        issues.append("covers his eyes")
    if item["kind"] == "neck":
        added = [y for y in range(48) if any(data[y * stride + x * 4: y * stride + x * 4 + 4]
                                             != bdata[y * stride + x * 4: y * stride + x * 4 + 4]
                                             for x in range(48))]
        if added and added[-1] > BODY_BOTTOM + HANG:
            issues.append(f"hangs to row {added[-1]}")
    if issues:
        problems[name] = issues
elapsed = time.perf_counter() - started
check(f"all 366 hold every rule on {len(SAMPLE)} frames each", not problems,
      "; ".join(f"{n}: {v[0]}" for n, v in list(problems.items())[:6]))
print(f"  ({elapsed:.1f}s)")

if fails:
    print(f"\n{len(fails)} check(s) FAILED")
    sys.exit(1)
print("\nall checks passed")
