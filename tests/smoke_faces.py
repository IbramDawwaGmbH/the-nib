"""Blink variety, the startle, and the expression table."""

import importlib.util
import json
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
fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


def fresh(tag, hour=14.0, **overrides):
    path = SCRATCH / f"fc_{tag}.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    cfg["last_seen_date"] = "2026-07-30"
    cfg["seasonal_enabled"] = False
    for k, v in overrides.items():
        cfg[k] = v
    sheet = SpriteSheet(SRC / "assets" / "nib_sheet.png", SRC / "assets" / "frames.json", 2)
    b = Behavior(cfg, sheet)
    b.set_strip(1392, 0, 2560)
    b.place_at(1200)
    b.set_world(W.WorldState(hour=hour, weekday=2, date_key="2026-07-30", week_key="2026-W31",
                             part_of_day=W.part_of_day(hour), season=W.SEASON_SPRING,
                             cpu_percent=8.0, cpu_busy=False))
    return cfg, b


print("\n--- the three blinks exist and are distinct ---")
manifest = json.loads((SRC / "assets" / "frames.json").read_text())
for name in (B.BLINK_SINGLE, B.BLINK_DOUBLE, B.BLINK_SLOW):
    check(f"{name} is in the sheet", name in manifest["states"])
single = [f[0] for f in manifest["states"][B.BLINK_SINGLE]["frames"]]
double = [f[0] for f in manifest["states"][B.BLINK_DOUBLE]["frames"]]
slow = [f[0] for f in manifest["states"][B.BLINK_SLOW]["frames"]]
check("the double really is two closes", double.count(single[0]) == 2, str(double))
check("the double costs no new art", set(double) <= {0, 1, 2, 3}, str(double))
check("the slow blink is its own frames", not (set(slow) & set(single)), str(slow))
slow_ms = sum(f[1] for f in manifest["states"][B.BLINK_SLOW]["frames"])
single_ms = sum(f[1] for f in manifest["states"][B.BLINK_SINGLE]["frames"])
check("and is genuinely slower", slow_ms > single_ms * 4, f"{slow_ms}ms vs {single_ms}ms")
for name in (B.BLINK_DOUBLE, B.BLINK_SLOW):
    check(f"{name} does not loop", manifest["states"][name]["loop"] is False)

print("\n--- which blink he picks is a mood, not a coin toss ---")
mixes = {}
for hour, label in ((2.0, "night"), (7.0, "morning"), (14.0, "day"), (20.0, "evening")):
    _cfg, b = fresh(f"blink{int(hour)}", hour=hour)
    random.seed(1234)
    counts = Counter(b._pick_blink() for _ in range(3000))
    mixes[label] = counts
    print(f"    {label:8s} " + "  ".join(f"{k}={100*v//3000}%" for k, v in counts.most_common()))
check("sleepy blinks dominate at night",
      mixes["night"][B.BLINK_SLOW] > mixes["night"][B.BLINK_SINGLE],
      f"slow={mixes['night'][B.BLINK_SLOW]} single={mixes['night'][B.BLINK_SINGLE]}")
check("and are rare in the middle of the day",
      mixes["day"][B.BLINK_SLOW] < mixes["night"][B.BLINK_SLOW] / 5,
      f"day={mixes['day'][B.BLINK_SLOW]} night={mixes['night'][B.BLINK_SLOW]}")
check("all three turn up during the day", len(mixes["day"]) == 3, str(list(mixes["day"])))

_cfg, b = fresh("blinknoworld")
b.set_world(None) if False else None
b._world = None
random.seed(7)
check("no world snapshot still blinks", {b._pick_blink() for _ in range(200)} <= {
    B.BLINK_SINGLE, B.BLINK_DOUBLE, B.BLINK_SLOW})

print("\n--- a blink actually plays end to end ---")
for kind in (B.BLINK_SINGLE, B.BLINK_DOUBLE, B.BLINK_SLOW):
    _cfg, b = fresh(f"play_{kind}")
    b._blinking = True
    b._blink_state = kind
    b.animator.set_state(kind, restart=True)
    seen, guard = set(), 0
    while b._blinking and guard < 2000:
        b.update(20.0, None)
        seen.add((b.animator.state, b.animator.frame_index))
        guard += 1
    check(f"{kind} runs and ends", not b._blinking, f"{len(seen)} distinct frames")
    check(f"{kind} returns to idle", b.animator.state == "idle", b.animator.state)

print("\n--- the startle ---")
_cfg, b = fresh("startle")
b._startle_cooldown_ms = 0
random.seed(2)
got = None
for _ in range(60):
    b._foreground_cooldown_ms = 0
    b._startle_cooldown_ms = 0
    b.foreground_changed("someunknownapp.exe")
    if b._antic == B.STARTLE_ANIM:
        got = b._antic
        break
check("an unfamiliar app can startle him", got == B.STARTLE_ANIM, str(b._antic))

# It must NOT swallow a persona - that was the bug when it was checked first.
for exe, expected in (("code.exe", "typing"), ("excel.exe", "spreadsheet"),
                      ("outlook.exe", "mail"), ("calculatorapp.exe", "counting")):
    ok = True
    for seed in range(12):
        _cfg, b = fresh(f"st_{exe}_{seed}")
        random.seed(seed)
        b._startle_cooldown_ms = 0
        b.foreground_changed(exe)
        if b._antic != expected:
            ok = False
            break
    check(f"{exe} always still gets its persona", ok, str(b._antic))

_cfg, b = fresh("stcool")
b._startle_cooldown_ms = 0
random.seed(3)
for _ in range(30):
    b._foreground_cooldown_ms = 0
    b.foreground_changed("randomthing.exe")
check("one jump per burst of alt-tabbing", b._startle_cooldown_ms > 0)

print("\n--- the expression table ---")
spec = importlib.util.spec_from_file_location("gs", SRC / "tools" / "gen_sprites.py")
gs = importlib.util.module_from_spec(spec)
sys.modules["gs"] = gs
spec.loader.exec_module(gs)

wanted = {"surprised", "smug", "sleepy", "dizzy", "love", "determined"}
check("all six expressions exist", wanted <= set(gs.EXPRESSIONS),
      str(sorted(wanted - set(gs.EXPRESSIONS))))

# Every expression must actually draw something different from neutral.
def face_pixels(name):
    grid = gs.blank()
    gs.draw_body(grid)
    gs.draw_legs(grid, gs.BODY_BOTTOM)
    gs.draw_face(grid, name)
    return frozenset((x, y, grid[y][x]) for y in range(gs.FRAME) for x in range(gs.FRAME)
                     if grid[y][x] != ".")

neutral = face_pixels("neutral")
seen_faces = {}
for name in gs.EXPRESSIONS:
    pix = face_pixels(name)
    if name != "neutral":
        check(f"{name} differs from neutral", pix != neutral)
    for other, other_pix in seen_faces.items():
        if pix == other_pix:
            check(f"{name} differs from {other}", False)
    seen_faces[name] = pix
check("all seven faces are distinct", len(set(seen_faces.values())) == len(gs.EXPRESSIONS))

# Sleepy and determined are mirror-image mistakes waiting to happen: assert the brows
# actually slope opposite ways.
def brow_slope(style):
    """+1 if the brow falls towards the nose (cross), -1 if it falls outward (tired)."""
    grid = gs.blank()
    gs.draw_brows(grid, style)
    pts = [(x, y) for y in range(gs.FRAME) for x in range(gs.FRAME)
           if grid[y][x] != "." and x < gs.EYE_CENTRE_X]      # left brow only
    if not pts:
        return 0
    lo = max(y for x, y in pts)                                # lowest row used
    left_low = min(x for x, y in pts if y == lo)
    right_low = max(x for x, y in pts if y == lo)
    return 1 if (left_low + right_low) / 2 > gs.EYE_A_X + gs.EYE_W / 2 - 1 else -1

check("determined brows drive towards the nose", brow_slope("determined") > 0)
check("sleepy brows fall away outward", brow_slope("droop") < 0)
check("they are not the same shape", gs.BROWS["droop"] != gs.BROWS["determined"])

print("\n--- every light-tone prop now has a border ---")
for fn in ("draw_zed", "draw_note", "draw_ball", "draw_sparkle", "draw_thought_dots"):
    src = importlib.import_module("gs").__dict__[fn]
    import inspect
    body = inspect.getsource(src)
    check(f"{fn} uses stamp_prop", "stamp_prop" in body)

print("\n" + "=" * 60)
print("FAILURES: " + ", ".join(fails) if fails else "blinks, startle and expressions OK")
sys.exit(1 if fails else 0)
