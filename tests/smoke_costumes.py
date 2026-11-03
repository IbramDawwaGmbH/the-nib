"""Every costume, on every frame, against the rules the bare sheet is held to.

``validate_sheet.py`` checks the undressed sprite. Nothing checked the *dressed* one, and a costume
is composited into the frame before the click mask and the contact shadow are derived from it - so
a scarf that hangs two pixels too low silently breaks the feet-on-the-baseline invariant the whole
app is built on, and the only symptom is him appearing to hover.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

from animator import SpriteSheet  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


SHEET = SRC / "assets" / "nib_sheet.png"
FRAMES = SRC / "assets" / "frames.json"
bare = SpriteSheet(SHEET, FRAMES, 1)
COSTUMES = bare.available_costumes()
STATES = bare.states()
#: Only the hop's mid-air frames are legitimately off the ground - the same exception the sheet
#: validator makes.
AIRBORNE = {("hop", 1), ("hop", 2), ("hop", 3)}

print(f"\n--- {len(COSTUMES)} costume(s) x {len(STATES)} states ---")
print("  " + ", ".join(COSTUMES))

print("\n--- every costume loads and is reachable ---")
categories = bare.costume_categories()
check("every costume has a category", sum(len(v) for v in categories.values()) == len(COSTUMES),
      str({k: len(v) for k, v in categories.items()}))
check("no costume is stranded under 'Other'", "Other" not in categories,
      str(categories.get("Other")))
check("names are unique", len(set(COSTUMES)) == len(COSTUMES))
check("each costume knows its own category, which is how his opinion falls back",
      bare.costume_category("cap") == "Hats"
      and all(bare.costume_category(n) == c for c, names in categories.items() for n in names))
check("...and an unknown one has none", bare.costume_category("nope") == "")

print("\n--- nothing hangs below his soles while he is standing on them ---")
# The frame's lowest opaque row must stay on the baseline, because rest_top(), foot_print() and
# the contact shadow all read it.
#
# Two states are exempt, and it took measuring to see why: on `injured` he has landed UPSIDE DOWN,
# so the frame's head anchor is at row 46 - the floor - and a hat squashed against the ground there
# is correct rather than broken. The same goes for the spin. So the rule is strict wherever he is
# on his feet, and merely bounded where he is not.
UPENDED = {"injured", "tumble"}
MAX_UPENDED_OVERHANG = 2

worst, upended = {}, {}
for costume in COSTUMES:
    sheet = SpriteSheet(SHEET, FRAMES, 1, costume=costume)
    baseline_row = sheet.frame_h - 1 - sheet.baseline_offset
    for state in STATES:
        for index in range(sheet.frame_count(state)):
            image = sheet.pixmap(state, index).toImage()
            lowest = -1
            for y in range(image.height() - 1, -1, -1):
                if any(image.pixelColor(x, y).alpha() > 0 for x in range(image.width())):
                    lowest = y
                    break
            over = lowest - baseline_row
            if over <= 0:
                continue
            if state in UPENDED:
                upended[costume] = max(upended.get(costume, 0), over)
            else:
                worst.setdefault(costume, []).append(f"{state}[{index}] +{over}")
check("no costume reaches past the baseline while he is upright", not worst,
      "; ".join(f"{k}: {v[0]} (+{len(v)-1} more)" for k, v in worst.items()))
check(f"...and never by more than {MAX_UPENDED_OVERHANG} rows when he is upside down",
      all(v <= MAX_UPENDED_OVERHANG for v in upended.values()), str(upended))

print("\n--- nothing that hangs from him covers his face ---")
# His eyes carry his entire expression, so a costume that is not FOR his eyes must stay off them.
# The medal, the bow tie, the tie and the scarf all failed this when they arrived: each had its
# anchor five rows into its own pattern, so each started at row 30 - two rows up inside his eye
# band. The medal was the obvious one because its ribbon is the tallest part, and it ran straight
# across both eyes.
#
# The convention this enforces: for anything that HANGS, the anchor is its top edge.
import json as _json  # noqa: E402

_manifest = _json.loads((SRC / "assets" / "frames.json").read_text(encoding="utf-8"))
_items = _json.loads((SRC / "assets" / "costumes.json").read_text(encoding="utf-8"))["items"]
EYE_TOP, EYE_BOTTOM = 26, 31
idle_anchors = _manifest["anchors"]["0"]

covering = {}
for costume in COSTUMES:
    item = _items[costume]
    if item["kind"] not in ("neck", "body"):
        continue        # head and eyes costumes are supposed to be up there
    if item.get("layer") == "behind":
        # A wing or a backpack strap level with his head is BESIDE it, not over it - that is what
        # the behind layer means. The first version of this check flagged all three and was wrong.
        continue
    sheet = SpriteSheet(SHEET, FRAMES, 1, costume=costume)
    bareimg = bare.pixmap("idle", 0).toImage()
    image = sheet.pixmap("idle", 0).toImage()
    # Rows where the costume added pixels the bare sprite did not have.
    added = [
        y for y in range(EYE_TOP, EYE_BOTTOM + 1)
        if any(image.pixelColor(x, y) != bareimg.pixelColor(x, y) for x in range(image.width()))
    ]
    if added:
        covering[costume] = f"rows {added[0]}..{added[-1]}"
check("no neck or body costume reaches his eye band", not covering,
      "; ".join(f"{k}: {v}" for k, v in covering.items()))

print("\n--- and they sit on him rather than dangling round his ankles ---")
BODY_BOTTOM = 41
dangling = {}
for costume in COSTUMES:
    item = _items[costume]
    if item["kind"] != "neck":
        continue
    sheet = SpriteSheet(SHEET, FRAMES, 1, costume=costume)
    image = sheet.pixmap("idle", 0).toImage()
    bareimg = bare.pixmap("idle", 0).toImage()
    added = [
        y for y in range(image.height())
        if any(image.pixelColor(x, y) != bareimg.pixelColor(x, y) for x in range(image.width()))
    ]
    if added and added[-1] > BODY_BOTTOM + 4:
        dangling[costume] = f"reaches row {added[-1]}, {added[-1] - BODY_BOTTOM} past his body"
# A scarf tail and a tie are MEANT to hang past his body; a medal is not. So this is a bounded
# check rather than a flat ban - it exists to catch the medal ending up at his feet, which is what
# happened when it was moved clear of his eyes without being shortened to fit the gap.
check("a neck costume does not hang more than 4 rows below his body", not dangling,
      "; ".join(f"{k}: {v}" for k, v in dangling.items()))

print("\n--- and nothing floats him off it either ---")
lifted = {}
for costume in COSTUMES:
    sheet = SpriteSheet(SHEET, FRAMES, 1, costume=costume)
    for state in STATES:
        for index in range(sheet.frame_count(state)):
            if (state, index) in AIRBORNE:
                continue
            _left, _right, lift = sheet.foot_print(state, index)
            if lift > 0:
                lifted.setdefault(costume, []).append(f"{state}[{index}] lift {lift}")
check("his feet still reach the ground in every dressed frame", not lifted,
      "; ".join(f"{k}: {v[0]}" for k, v in lifted.items()))

print("\n--- the click mask stays exact ---")
# The art contract bans partial alpha because the mask is thresholded from it. A costume drawn
# with a soft edge would make his hit region ragged rather than pixel-exact.
partial = {}
for costume in COSTUMES:
    sheet = SpriteSheet(SHEET, FRAMES, 1, costume=costume)
    for state in ("idle", "walk_right", "sit_dangle"):
        if not sheet.has_state(state):
            continue
        image = sheet.pixmap(state, 0).toImage()
        for y in range(image.height()):
            for x in range(image.width()):
                alpha = image.pixelColor(x, y).alpha()
                if 0 < alpha < 255:
                    partial.setdefault(costume, []).append(f"{state} ({x},{y}) a={alpha}")
check("no costume introduces partial alpha", not partial,
      "; ".join(f"{k}: {v[0]}" for k, v in partial.items()))

print("\n--- costumes stay inside the frame ---")
clipped = {}
for costume in COSTUMES:
    sheet = SpriteSheet(SHEET, FRAMES, 1, costume=costume)
    image = sheet.pixmap("idle", 0).toImage()
    edges = []
    if any(image.pixelColor(x, 0).alpha() > 0 for x in range(image.width())):
        edges.append("top")
    for x, side in ((0, "left"), (image.width() - 1, "right")):
        if any(image.pixelColor(x, y).alpha() > 0 for y in range(image.height())):
            edges.append(side)
    if edges:
        clipped[costume] = edges
# A warning rather than a failure: touching an edge is only a problem if the art is actually cut
# off, and a wide brim legitimately reaches the sides.
print(f"  note: {len(clipped)} costume(s) touch a frame edge" +
      (f" - {', '.join(f'{k}({"/".join(v)})' for k, v in clipped.items())}" if clipped else ""))

print("\n--- the wardrobe's thumbnails ---")
from PySide6.QtGui import QRegion  # noqa: E402

_thumbs = {n: bare.costume_thumbnail(n) for n in COSTUMES}
_cell = int(_json.loads((SRC / "assets" / "costumes.json").read_text(encoding="utf-8"))["cell"])
check("every costume has one", all(t is not None and not t.isNull() for t in _thumbs.values()),
      str([n for n, t in _thumbs.items() if t is None or t.isNull()]))
check("...no bigger than its cell",
      all(t.width() <= _cell and t.height() <= _cell for t in _thumbs.values() if t is not None))
check("...trimmed exactly to its opaque pixels",
      all(QRegion(t.mask()).boundingRect() == t.rect() for t in _thumbs.values() if t is not None),
      str([n for n, t in _thumbs.items()
           if t is not None and QRegion(t.mask()).boundingRect() != t.rect()]))
check("an unknown costume has none", bare.costume_thumbnail("nope") is None)
check("...and a second ask is the cached one", bare.costume_thumbnail("cap") is _thumbs["cap"])

print("\n--- he is still himself underneath ---")
dressed = SpriteSheet(SHEET, FRAMES, 1, costume=COSTUMES[0])
check("a dressed sheet has the same states", set(dressed.states()) == set(STATES))
check("...and the same frame counts",
      all(dressed.frame_count(s) == bare.frame_count(s) for s in STATES))
check("an unknown costume degrades to bare-headed",
      SpriteSheet(SHEET, FRAMES, 1, costume="nonsense").costume is None)

print("\n" + "=" * 62)
print("FAILURES: " + ", ".join(fails) if fails else "every costume behaves")
sys.exit(1 if fails else 0)
