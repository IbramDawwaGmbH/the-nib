"""How much input region does the contact shadow actually cost?

The mask is what keeps clicks on empty pixels passing through to the taskbar, and the shadow has
to be unioned into it or it is never painted. That union is the one way this feature can regress
the click-through guarantee - the same failure mode the peek fix exists to prevent, where 26px of
input region hung below the window over the bar.

So: ground truth from GetWindowRgn, not from what Qt thinks, with the shadow on and off.
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import scratch as _scratch  # noqa: E402

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
SRC = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, SRC)
os.chdir(SRC)
SCRATCH = _scratch()

from PySide6.QtWidgets import QApplication  # noqa: E402

import behavior as B  # noqa: E402
import main as M  # noqa: E402
from behavior import State  # noqa: E402
from config import Config  # noqa: E402

u32, gdi = ctypes.windll.user32, ctypes.windll.gdi32


class RECT(ctypes.Structure):
    _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                ("r", ctypes.c_long), ("b", ctypes.c_long)]


class RGNDATAHEADER(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("iType", wintypes.DWORD),
                ("nCount", wintypes.DWORD), ("nRgnSize", wintypes.DWORD), ("rcBound", RECT)]


def region_rects(hwnd):
    """Every rectangle in the window's real input region, in window-local coordinates."""
    rgn = gdi.CreateRectRgn(0, 0, 1, 1)
    if u32.GetWindowRgn(wintypes.HWND(hwnd), rgn) == 0:
        gdi.DeleteObject(rgn)
        return None
    size = gdi.GetRegionData(rgn, 0, None)
    buf = ctypes.create_string_buffer(size)
    gdi.GetRegionData(rgn, size, buf)
    head = RGNDATAHEADER.from_buffer(buf)
    rects = (RECT * head.nCount).from_buffer(buf, ctypes.sizeof(RGNDATAHEADER))
    gdi.DeleteObject(rgn)
    return [(r.l, r.t, r.r, r.b) for r in rects]


def region_area(rects):
    return sum((r - l) * (b - t) for l, t, r, b in rects) if rects else 0


def region_box(rects):
    if not rects:
        return None
    return (min(r[0] for r in rects), min(r[1] for r in rects),
            max(r[2] for r in rects), max(r[3] for r in rects))


fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


def boot(**overrides):
    path = SCRATCH / "shadowcfg.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    cfg["last_seen_date"] = "2026-07-31"
    for k, v in overrides.items():
        cfg[k] = v
    nib = M.NibApp(app, cfg)
    app.processEvents()
    time.sleep(0.25)
    app.processEvents()
    return nib


app = QApplication(sys.argv)

print("\n--- what the shadow costs, measured against GetWindowRgn ---")
nib = boot(shadow_enabled=False)
hwnd = int(nib.window.winId())
nib.behavior.paused = True          # hold one frame so both measurements are the same pose
nib.behavior.animator.set_state("idle", restart=True)
nib._apply_frame(force=True)
app.processEvents()
bare = region_rects(hwnd)
bare_area, bare_box = region_area(bare), region_box(bare)
win_w, win_h = nib.window.width(), nib.window.height()
print(f"    window {win_w}x{win_h}")
print(f"    shadow OFF: {len(bare)} rects, {bare_area}px area, box={bare_box}")

nib.config["shadow_enabled"] = True
nib._last_frame_key = None
nib._apply_frame(force=True)
app.processEvents()
lit = region_rects(hwnd)
lit_area, lit_box = region_area(lit), region_box(lit)
print(f"    shadow ON : {len(lit)} rects, {lit_area}px area, box={lit_box}")

grew = lit_area - bare_area
print(f"\n    growth: {grew}px ({grew / max(1, bare_area) * 100:+.1f}% of his input area)")

check("the shadow never pushes input region below the window",
      lit_box is None or lit_box[3] <= win_h, f"bottom {lit_box[3]} vs window {win_h}")
check("...nor past either side", lit_box is None or (lit_box[0] >= 0 and lit_box[2] <= win_w),
      f"x {lit_box[0]}..{lit_box[2]} vs 0..{win_w}")
# The real question. The shadow is meant to be a thin band inside his own silhouette's width, so
# the extra input area should be a rounding error next to the body he already occupies.
check("the extra input area is under a tenth of what he already occupies",
      grew <= bare_area * 0.10, f"{grew}px on top of {bare_area}px")
check("...and it is confined to the bottom of the window",
      lit_box is None or bare_box is None or lit_box[1] >= bare_box[1],
      f"top {lit_box[1]} vs {bare_box[1]}")

print("\n--- where the extra region actually is ---")
rows_bare = {}
for l, t, r, bt in bare:
    for y in range(t, bt):
        rows_bare[y] = rows_bare.get(y, 0) + (r - l)
rows_lit = {}
for l, t, r, bt in lit:
    for y in range(t, bt):
        rows_lit[y] = rows_lit.get(y, 0) + (r - l)
changed = sorted(y for y in set(rows_bare) | set(rows_lit)
                 if rows_bare.get(y, 0) != rows_lit.get(y, 0))
print(f"    rows affected: {len(changed)} of {win_h}"
      + (f"  (rows {changed[0]}..{changed[-1]})" if changed else ""))
for y in changed:
    print(f"      row {y:3d}: {rows_bare.get(y, 0):3d}px -> {rows_lit.get(y, 0):3d}px")
check("only the last few rows change", len(changed) <= B.SHADOW_H_PX * nib.sheet.scale + 2,
      f"{len(changed)} rows")
check("...all of them at his feet",
      not changed or changed[0] >= win_h - (B.SHADOW_H_PX + 2) * nib.sheet.scale,
      f"highest affected row {changed[0] if changed else '-'} of {win_h}")

print("\n--- and it is gone entirely where a shadow would be a lie ---")
b = nib.behavior
b.paused = False
for label, setup in (
    ("peeking", lambda: b._enter(State.PEEK)),
    ("in your hand", lambda: b.drag_start()),
    ("in the air", lambda: setattr(b, "_falling", True)),
    ("hidden", lambda: b.set_hidden(True)),
):
    b._enter(State.IDLE)
    b._falling = False
    b.set_hidden(False)
    setup()
    check(f"no shadow while {label}", b.shadow is None, str(b.shadow))

b.set_hidden(False)
b._falling = False
b._enter(State.IDLE)
check("and it is back once he is standing again", b.shadow is not None, str(b.shadow))

print("\n--- the hop lifts off, and the shadow knows without being told ---")
sheet = nib.sheet
grounded = sheet.foot_print("hop", 0)
airborne = sheet.foot_print("hop", 2)
print(f"    hop frame 0: lift={grounded[2]}px   frame 2: lift={airborne[2]}px")
check("the sheet can see which hop frames are off the ground", airborne[2] > grounded[2],
      f"{grounded[2]} -> {airborne[2]}")
b._enter(State.ANTIC)
b._antic = "hop"
b.animator.hold("hop", 0)
low = b.shadow
b.animator.hold("hop", 2)
high = b.shadow
print(f"    grounded shadow: {low}\n    airborne shadow: {high}")
check("mid-hop the shadow is fainter or gone",
      high is None or (low is not None and high[4] < low[4]),
      f"alpha {low[4] if low else '-'} -> {high[4] if high else 'none'}")

print("\n--- a landing spreads it ---")
b._enter(State.IDLE)
b.animator.set_state("idle", restart=True)
b._land_squash_ms = 0.0
rest = b.shadow
b._land_squash_ms = B.LAND_SQUASH_MS
landed = b.shadow
print(f"    at rest: w={rest[2]} alpha={rest[4]}    on impact: w={landed[2]} alpha={landed[4]}")
check("impact widens it", landed[2] > rest[2], f"{rest[2]} -> {landed[2]}")
check("...and thins it out rather than darkening it", landed[4] < rest[4],
      f"{rest[4]} -> {landed[4]}")

print("\n--- switched off it costs literally nothing ---")
nib.config["shadow_enabled"] = False
check("shadow_enabled=false means no shadow at all", b.shadow is None, str(b.shadow))
nib._last_frame_key = None
nib._apply_frame(force=True)
app.processEvents()
off_again = region_rects(hwnd)
check("...and the input region goes back to exactly what it was",
      region_area(off_again) == bare_area, f"{region_area(off_again)}px vs {bare_area}px")

print("\n" + "=" * 62)
print("FAILURES: " + ", ".join(fails) if fails else "contact shadow OK")
nib._on_quit()
sys.exit(1 if fails else 0)
