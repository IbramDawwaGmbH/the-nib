"""Generate the sprite sheet, the frame manifest, and the costume sheet.

The art is authored as integer pixel operations on 48x48 character grids - never as smoothed
vector shapes - so every pixel is deliberate, the palette stays exact, and alpha is strictly 0 or
255. That is what keeps the mascot crisp when the app upscales it with nearest-neighbour.

The walk cycle carries the anti-moonwalk contract. Each leg stays planted for three frames while
its horizontal offset steps down by exactly ``WALK_STEP_PX`` per frame. Since the app advances the
mascot by ``WALK_STEP_PX`` per frame, the planted foot holds still against the ground instead of
sliding. The other leg swings forward, lifted, over the following three frames.

Three things here exist to serve the runtime rather than the art:

*   Every frame gets a 1px dark **outline**, applied after the creature is drawn but before any
    decoration, so he reads against a light or a dark taskbar. It is never drawn below the
    baseline row, which keeps the feet-on-the-strip invariant intact.
*   Each frame records **anchors** (top-centre of the head, centre of the eye band) so costumes
    can be composited at runtime without baking a copy of all 68 frames per hat.
*   ``costumes.png`` / ``costumes.json`` hold the hats and glasses. They use a wider palette than
    the body, which is fine - only the body sheet is palette-checked.

Run:  python tools/gen_sprites.py [output_dir]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

# --- palette -------------------------------------------------------------------------

PALETTE: dict[str, tuple[int, int, int, int] | None] = {
    ".": None,                        # transparent
    "B": (0xD9, 0x55, 0x2E, 255),     # body base
    "H": (0xE8, 0x72, 0x4A, 255),     # highlight
    "S": (0xB2, 0x3F, 0x1E, 255),     # shadow
    "O": (0x2C, 0x12, 0x06, 255),     # outline / eyes / ground contact
    "F": (0x8A, 0x3D, 0x22, 255),     # feet + leg shadow
    "A": (0xC0, 0x39, 0x2B, 255),     # angry accent / hearts
    "L": (0xF5, 0xED, 0xE6, 255),     # light detail (z, dots, sparkles, ball, notes)
}

# --- geometry ------------------------------------------------------------------------

FRAME = 48
COLS, ROWS = 10, 40
BASELINE_OFFSET = 1
BASELINE_ROW = FRAME - 1 - BASELINE_OFFSET  # 46 - the row the soles occupy
WALK_STEP_PX = 3

BODY_X, BODY_W = 11, 26         # body spans x 11..36, centred on 23.5
BODY_BOTTOM = 41                # body's lowest row when not offset
BODY_H = 23                     # slightly squat, so the top sits at row 19

LEG_W = 6
LEG_A_X = 14                    # rear leg
LEG_B_X = 28                    # front leg

EYE_W, EYE_H = 5, 6
EYE_Y = 26
EYE_A_X = 16
EYE_B_X = 27
#: Horizontal centre of the eye band - where eye-anchored costumes (glasses) attach.
EYE_CENTRE_X = (EYE_A_X + EYE_B_X + EYE_W) // 2

ARM_X = BODY_X + BODY_W - 1     # overlaps the body edge by 1 px so the arm stays attached

#: Narrowest contiguous run that can count as his head rather than a raised arm, when the
#: head anchor is measured. His head is 26px across and an arm is about 5, so anything in
#: between separates them with room to spare.
MIN_HEAD_RUN = 12

#: Rows kept between the neck/body anchors and the lowest solid row, so a costume that hangs
#: from either of them cannot reach through the floor on the frames where he crouches.
HANG_CLEARANCE = 4

TOP_INSETS = (2, 1)             # just a clipped corner - blocky, not bubbly
BOTTOM_INSETS = (1,)            # near-square base

Grid = list[list[str]]
Anchors = dict[str, list[int]]


def blank() -> Grid:
    return [["." for _ in range(FRAME)] for _ in range(FRAME)]


def put(grid: Grid, x: int, y: int, ch: str) -> None:
    if 0 <= x < FRAME and 0 <= y < FRAME:
        grid[y][x] = ch


def fill_rect(grid: Grid, x: int, y: int, w: int, h: int, ch: str) -> None:
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            put(grid, xx, yy, ch)


def stamp(grid: Grid, x: int, y: int, pattern: list[str]) -> None:
    """Draw a small character pattern, treating '.' as transparent."""
    for dy, row in enumerate(pattern):
        for dx, ch in enumerate(row):
            if ch != ".":
                put(grid, x + dx, y + dy, ch)


def ring(pattern: list[str], ink: str = "O") -> list[str]:
    """Wrap a pattern in a 1px border, growing it by one pixel on every side.

    The light detail tone is very nearly white, so on a Windows 11 *light* taskbar an unbordered
    prop simply is not there - checked at ``sprite_scale=2`` over the real taskbar colour, where
    the sweat drops, the snowflakes and the calculator's operators all disappeared. This is the
    same fix ``outline_costume`` applies to hats, for the same reason.
    """
    height, width = len(pattern) + 2, max(len(r) for r in pattern) + 2
    grid = [["." for _ in range(width)] for _ in range(height)]
    for y, row in enumerate(pattern):
        for x, ch in enumerate(row):
            if ch != ".":
                grid[y + 1][x + 1] = ch
    solid = [(x, y) for y in range(height) for x in range(width) if grid[y][x] != "."]
    for x, y in solid:  # snapshot first, or the border would grow into itself
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < width and 0 <= ny < height and grid[ny][nx] == ".":
                grid[ny][nx] = ink
    return ["".join(row) for row in grid]


def stamp_prop(grid: Grid, x: int, y: int, pattern: list[str]) -> None:
    """Stamp a bordered prop at the same anchor the bare pattern would have used.

    Never paints below ``BASELINE_ROW``: a border under a prop resting on the floor would make the
    frame's lowest opaque row 47 and break the feet-on-the-baseline rule the whole app relies on.
    """
    for dy, row in enumerate(ring(pattern)):
        for dx, ch in enumerate(row):
            if ch != "." and (y - 1 + dy) <= BASELINE_ROW:
                put(grid, x - 1 + dx, y - 1 + dy, ch)


def row_inset(row_from_top: int, height: int) -> int:
    """Horizontal inset for a rounded-rectangle row."""
    if row_from_top < len(TOP_INSETS):
        return TOP_INSETS[row_from_top]
    from_bottom = height - 1 - row_from_top
    if from_bottom < len(BOTTOM_INSETS):
        return BOTTOM_INSETS[from_bottom]
    return 0


def body_cells(x0: int, y0: int, w: int, h: int) -> list[tuple[int, int, int, int]]:
    """(x, y, dx_from_left, dy_from_top) for every pixel of a rounded body."""
    cells = []
    for dy in range(h):
        inset = row_inset(dy, h)
        if inset * 2 >= w:
            continue
        for dx in range(inset, w - inset):
            cells.append((x0 + dx, y0 + dy, dx, dy))
    return cells


# --- outline + anchors ---------------------------------------------------------------


def outline(grid: Grid) -> None:
    """Wrap the silhouette in a 1px dark border.

    Never paints below ``BASELINE_ROW``: an outline under the soles would make the frame's lowest
    opaque row 47 and break the feet-rest-on-the-strip invariant the whole app relies on.
    Call this after the creature is drawn but before decorations, so balls and music notes do not
    get a heavy border.
    """
    solid = [
        (x, y)
        for y in range(FRAME)
        for x in range(FRAME)
        if grid[y][x] != "."
    ]
    for x, y in solid:  # snapshot first, or the outline would grow into itself
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < FRAME and 0 <= ny <= BASELINE_ROW and grid[ny][nx] == ".":
                grid[ny][nx] = "O"


def rotate_grid(grid: Grid, quarter_turns: int) -> Grid:
    """Rotate a frame clockwise in 90 degree steps.

    Quarter turns only, and deliberately so: they are exact permutations of the pixel grid, where
    an arbitrary angle would have to resample and would either blur the art or shred it into
    jaggies. Four of them is enough to read as a spin.
    """
    out = grid
    for _ in range(quarter_turns % 4):
        rotated = blank()
        for y in range(FRAME):
            for x in range(FRAME):
                rotated[x][FRAME - 1 - y] = out[y][x]
        out = rotated
    return out


def seat_to_baseline(grid: Grid) -> Grid:
    """Slide a frame vertically until its lowest opaque row sits on the baseline."""
    rows_used = [y for y in range(FRAME) if any(grid[y][x] != "." for x in range(FRAME))]
    if not rows_used:
        return grid
    shift = BASELINE_ROW - max(rows_used)
    if shift == 0:
        return grid
    seated = blank()
    for y in rows_used:
        new_y = y + shift
        if 0 <= new_y < FRAME:
            for x in range(FRAME):
                seated[new_y][x] = grid[y][x]
    return seated


def flip_and_seat(grid: Grid) -> tuple[Grid, int]:
    """Turn a frame upside down and drop it back onto the baseline.

    A plain vertical flip would leave the sprite floating, because the content no longer reaches
    the bottom of the cell. So this also re-seats it: whatever was the TOP of the creature (his
    head) ends up resting on the baseline row, which is exactly what landing on your head looks
    like - and it keeps the feet-on-the-baseline contract intact for the validator.

    Returns the flipped grid and the original top row, which callers need to map anchors through
    the same transform: ``new_y = BASELINE_ROW + top - y``.
    """
    rows_used = [y for y in range(FRAME) if any(grid[y][x] != "." for x in range(FRAME))]
    if not rows_used:
        return grid, 0
    top = min(rows_used)
    flipped = blank()
    for y in rows_used:
        new_y = BASELINE_ROW + top - y
        if 0 <= new_y < FRAME:
            for x in range(FRAME):
                flipped[new_y][x] = grid[y][x]
    return flipped, top


def measure_anchors(grid: Grid, *, eye_dx: int = 0, eye_dy: int = 0) -> Anchors:
    """Where costumes attach on this frame.

    ``head`` is the centre of the topmost solid row, read off the finished silhouette so it tracks
    breathing, squashing and hops automatically. ``eyes`` comes from the eye constants plus
    whatever offset the frame applied.

    ``neck`` and ``body`` are read off the silhouette in the same way, so a scarf rides up when he
    hops and a tool belt tips with him when he sits. Measured rather than assumed: he has no neck,
    so "neck" means the point a scarf would sit - a little below the eye band, on the body's own
    centre line at that height, which is the widest part of him and is not the same as the head's
    centre on frames that lean.
    """
    rows = [
        (y, [x for x in range(FRAME) if grid[y][x] != "."])
        for y in range(FRAME)
    ]
    solid = [(y, xs) for y, xs in rows if xs]
    if not solid:
        return {
            "head": [FRAME // 2, 0], "eyes": [EYE_CENTRE_X + eye_dx, EYE_Y + eye_dy],
            "neck": [FRAME // 2, EYE_Y], "body": [FRAME // 2, BODY_BOTTOM],
        }

    top_y, top_xs = solid[0]
    head = [(min(top_xs) + max(top_xs)) // 2, top_y]
    bottom_y = solid[-1][0]

    # The topmost solid row is not always his head. Whenever an arm is raised - the wave, the
    # dance, the press-ups, the strain, both new fidgets - the highest pixels belong to a hand,
    # and the naive centre-of-the-topmost-row put every hat out on his wrist. Measured across the
    # sheet: 24 of 154 frames, 16% of them.
    #
    # So: walk down until a row contains a contiguous run wide enough to BE a head, and take the
    # centre of that run. An arm is about five pixels across and his head is twenty-six, so the
    # threshold has an enormous margin either side and needs no per-frame tuning.
    for y, xs in solid:
        best_start, best_width = xs[0], 1
        run_start, run_width = xs[0], 1
        for previous, current in zip(xs, xs[1:]):
            if current == previous + 1:
                run_width += 1
            else:
                run_start, run_width = current, 1
            if run_width > best_width:
                best_start, best_width = run_start, run_width
        if best_width >= MIN_HEAD_RUN:
            head = [best_start + best_width // 2, y]
            break

    def centre_at(y: int, fallback: int) -> list[int]:
        for row_y, xs in solid:
            if row_y == y:
                return [(min(xs) + max(xs)) // 2, y]
        return [fallback, y]

    # Below the mouth line and above the legs: where a collar would sit on a creature shaped like
    # a rounded brick. ``body`` is deliberately further down than ``neck`` - they came out on the
    # same row at first, which made a belt and a scarf indistinguishable.
    #
    # Both are held clear of the floor by HANG_CLEARANCE. Costumes on these two anchors are the
    # ones that hang - a scarf's tail, an apron's hem, a cape - and on the frames where he crouches
    # or dips (the foot tap's landing beat, the hop's crouch) an anchor clamped only to the lowest
    # solid row let five of them poke a pixel through the taskbar. Reserving the space here fixes
    # every one of them at once, and any future one, instead of shortening five patterns and
    # waiting for the sixth.
    floor = bottom_y - HANG_CLEARANCE
    neck_y = min(floor, EYE_Y + EYE_H + eye_dy + 3)
    body_y = min(floor, (top_y + bottom_y) // 2 + 8)
    return {
        "head": head,
        "eyes": [EYE_CENTRE_X + eye_dx, EYE_Y + eye_dy],
        "neck": centre_at(neck_y, head[0]),
        "body": centre_at(body_y, head[0]),
    }


# --- body ----------------------------------------------------------------------------


def draw_body(
    grid: Grid,
    *,
    dx: int = 0,
    dy: int = 0,
    grow: int = 0,
    squash: int = 0,
    widen: int = 0,
) -> tuple[int, int, int, int]:
    """Draw the torso. Returns ``(x0, y0, w, h)`` of the body actually drawn.

    ``grow`` raises the top edge only, which is how breathing works - the base stays put so the
    creature inflates rather than levitates. ``squash``/``widen`` drive stretches, dips and turns.
    """
    height = BODY_H + grow - squash
    width = BODY_W + widen
    x0 = BODY_X - widen // 2 + dx
    bottom = BODY_BOTTOM + dy
    y0 = bottom - height + 1

    cells = body_cells(x0, y0, width, height)
    for x, y, _dx, _dy in cells:
        put(grid, x, y, "B")
    for x, y, cdx, cdy in cells:
        # light from the upper left
        if cdy < 3 or (cdx < 2 and cdy < height * 0.45):
            put(grid, x, y, "H")
    for x, y, cdx, cdy in cells:
        # shadow along the lower right
        if (height - 1 - cdy) < 2 or ((width - 1 - cdx) < 2 and cdy > height * 0.3):
            put(grid, x, y, "S")
    return x0, y0, width, height


def draw_legs(
    grid: Grid,
    body_bottom: int,
    *,
    dx_a: int = 0,
    dx_b: int = 0,
    lift_a: int = 0,
    lift_b: int = 0,
    length: int | None = None,
) -> None:
    """Legs run from just under the body down to the baseline, so they never detach.

    Pass ``length`` to draw legs of a fixed length instead - that is how the hop leaves the
    ground, since stretching to the baseline would look like taffy instead of a jump. The last row
    of each foot is the dark outline tone, which reads as ground contact.
    """
    for x_base, dx, lift in ((LEG_A_X, dx_a, lift_a), (LEG_B_X, dx_b, lift_b)):
        top = body_bottom + 1
        bottom = (top + length - 1) if length is not None else (BASELINE_ROW - lift)
        if length is not None:
            bottom -= lift
        if bottom < top:
            continue
        x = x_base + dx
        fill_rect(grid, x, top, LEG_W, bottom - top + 1, "S")
        foot_rows = min(2, bottom - top + 1)
        fill_rect(grid, x, bottom - foot_rows + 1, LEG_W, foot_rows, "F")
        fill_rect(grid, x, bottom, LEG_W, 1, "O")  # sole / contact shadow


HEART = [".A.A.", "AAAAA", "AAAAA", ".AAA.", "..A.."]
SPARKLE_BIG = [".L.", "LLL", ".L."]
SPARKLE_SMALL = ["L"]


def draw_eyes(
    grid: Grid,
    *,
    mode: str = "open",
    dy: int = 0,
    dx: int = 0,
    wink_side: str = "a",
) -> None:
    """Draw the face. ``dx``/``dy`` shift both eyes, which is how looking around works."""
    y = EYE_Y + dy
    for side, x_base in (("a", EYE_A_X), ("b", EYE_B_X)):
        x = x_base + dx
        if mode == "open":
            fill_rect(grid, x, y, EYE_W, EYE_H, "O")
        elif mode == "closed":
            fill_rect(grid, x, y + EYE_H // 2, EYE_W, 2, "O")
        elif mode == "angry":
            fill_rect(grid, x, y + 2, EYE_W, EYE_H - 2, "O")
        elif mode == "half":
            # Lids at half mast. Droopier than "angry", and without the red brows, so it reads as
            # tired or pleased with itself rather than cross.
            fill_rect(grid, x, y + 3, EYE_W, EYE_H - 3, "O")
        elif mode == "glint":
            fill_rect(grid, x, y, EYE_W, EYE_H, "O")
            put(grid, x + 1, y + 1, "L")
            put(grid, x + 2, y + 1, "L")
        elif mode == "heart":
            stamp(grid, x, y, HEART)
        elif mode == "wink":
            if side == wink_side:
                fill_rect(grid, x, y + EYE_H // 2, EYE_W, 2, "O")
            else:
                fill_rect(grid, x, y, EYE_W, EYE_H, "O")
                put(grid, x + 1, y + 1, "L")
        elif mode == "wide":
            fill_rect(grid, x - 1, y - 1, EYE_W + 2, EYE_H + 2, "O")
            put(grid, x, y, "L")
        elif mode == "dazed":
            # Crossed eyes: the clearest "he took a knock" read at this size.
            stamp(grid, x, y, ["O...O", ".O.O.", "..O..", ".O.O.", "O...O"])

    if mode == "angry":
        stamp(grid, EYE_A_X + dx, y - 3, ["AAA..", ".AAA.", "..AAA"])
        stamp(grid, EYE_B_X + dx, y - 3, ["..AAA", ".AAA.", "AAA.."])
        fill_rect(grid, EYE_A_X + dx - 4, y + EYE_H, 3, 2, "A")
        fill_rect(grid, EYE_B_X + dx + EYE_W + 1, y + EYE_H, 3, 2, "A")


# --- expressions ---------------------------------------------------------------------
#
# Brows and a mouth, drawn as small deltas on top of whatever the eyes are doing. Kept as a table
# rather than baked into each frame builder so the same expression can be reused across unrelated
# reactions - "determined" is the same four pixels whether he is typing, shoving at a window, or
# doing sums.
#
# He has no mouth at rest, and that is deliberate: at 48px the eyes carry everything, and a mouth
# on every frame would flatten the silhouette. It appears only where an expression asks for one.

#: Brow shapes, drawn for the LEFT eye; the right one is the mirror image. Dark outline tone, not
#: the red accent - red is reserved for anger, and using it here would make him look cross whenever
#: he was merely concentrating.
#: Drawn for the LEFT eye, whose *outer* side is the left. Which way each slopes is the whole
#: expression and it is easy to get backwards: a brow low on the INNER side reads as cross, low on
#: the OUTER side reads as tired. Rendered both ways and checked - these are the tired-and-cross
#: pair the right way round.
BROWS: dict[str, list[str]] = {
    "raised":     ["OOOOO"],                        # flat and set high - surprise
    "droop":      ["...OO", ".OO..", "OO..."],      # falls away outward - sleepy
    "determined": ["OO...", "..OO.", "...OO"],      # drives down towards the nose
}
#: Extra height above the eye band for each brow, so a raised brow really does sit higher.
BROW_LIFT = {"raised": 5, "droop": 3, "determined": 3, "smug": 3}

#: Mouths, centred under the eye band.
MOUTHS: dict[str, list[str]] = {
    "o":      [".OO.", "O..O", "O..O", ".OO."],     # a small round gape
    "smirk":  ["...OO", "OOO.."],
    "flat":   ["OOOOOO"],
    "smile":  ["O....O", ".OOOO."],
    "wavy":   ["OO..OO", "..OO.."],                 # seasick
}
MOUTH_Y = EYE_Y + EYE_H + 2          # on his lower face, clear of the eyes and of the body edge

#: name -> (eye mode, brow style or None, mouth style or None).
EXPRESSIONS: dict[str, tuple[str, str | None, str | None]] = {
    "neutral":    ("open",  None,         None),
    "surprised":  ("wide",  "raised",     "o"),
    "smug":       ("half",  "smug",       "smirk"),
    "sleepy":     ("half",  "droop",      None),
    "dizzy":      ("dazed", None,         "wavy"),
    "love":       ("heart", None,         "smile"),
    "determined": ("open",  "determined", "flat"),
}


def draw_brows(grid: Grid, style: str, *, dx: int = 0, dy: int = 0) -> None:
    y = EYE_Y + dy - BROW_LIFT.get(style, 3)
    if style == "smug":
        # Two flat brows at different heights. The asymmetry *is* the expression - a mirrored
        # pair of slanted brows just reads as a second angry face.
        stamp(grid, EYE_A_X + dx, y, ["OOOOO"])
        stamp(grid, EYE_B_X + dx, y - 3, ["OOOOO"])
        return
    pattern = BROWS.get(style)
    if not pattern:
        return
    stamp(grid, EYE_A_X + dx, y, pattern)
    stamp(grid, EYE_B_X + dx, y, [row[::-1] for row in pattern])


def draw_mouth(grid: Grid, style: str, *, dx: int = 0, dy: int = 0) -> None:
    pattern = MOUTHS.get(style)
    if not pattern:
        return
    width = max(len(r) for r in pattern)
    stamp(grid, EYE_CENTRE_X + dx - width // 2, MOUTH_Y + dy, pattern)


def draw_face(grid: Grid, expression: str = "neutral", *, dx: int = 0, dy: int = 0, **kwargs) -> None:
    """Eyes, brows and mouth for a named expression. Falls back to plain open eyes."""
    mode, brows, mouth = EXPRESSIONS.get(expression, EXPRESSIONS["neutral"])
    draw_eyes(grid, mode=mode, dx=dx, dy=dy, **kwargs)
    if brows:
        draw_brows(grid, brows, dx=dx, dy=dy)
    if mouth:
        draw_mouth(grid, mouth, dx=dx, dy=dy)


# --- arm -----------------------------------------------------------------------------

ARM_POSES: list[tuple[int, list[str]]] = [
    (13, [".BBB", "BBBB", "BBBS", ".SSS"]),
    (18, ["..BBB", ".BBBB", "BBBBS", "BBS..", ".SS.."]),
    (24, [".BBB.", "BBBBB", "BBBBS", ".BBS.", ".BBS.", ".BBS.", ".SS.."]),
    (22, ["...BBB", "..BBBB", "..BBBS", ".BBBS.", "BBBS..", ".SS..."]),
    (11, ["BBBBBB", "BBBBBB", ".SSSSS"]),  # pointing straight out - the wink's "you!"
]


def draw_arm(grid: Grid, body_bottom: int, pose: int = 0, *, dx: int = 0) -> None:
    rise, pattern = ARM_POSES[pose % len(ARM_POSES)]
    stamp(grid, ARM_X + dx, body_bottom - rise, pattern)


# --- decorations ---------------------------------------------------------------------

BIG_Z = ["LLLLL", "...LL", "..LL.", ".LL..", "LLLLL"]
SMALL_Z = ["LLL", "..L", ".L.", "LLL"]
BALL = [".LL.", "LOOL", "LOOL", ".LL."]
NOTE = [".LL", ".L.", ".L.", "LL.", "LL."]

#: Props for the world-aware states. Like the ball and the music notes these are drawn *after* the
#: outline pass, so they carry their own border where they need one rather than picking up the
#: body's heavy 1px ring.
DROP = [".L", "LL"]                       # sweat, when the machine is working hard
MUG = ["LLLL..", "LOOLLL", "LOOL.L", "LOOLLL", ".LL..."]
LEAF = [".AAA", "AAAA", "AAA.", ".A.."]   # the one prop that is not light-toned - autumn red
ENVELOPE = ["LLLLLLL", "LOLLLOL", "LLOLOLL", "LLLOLLL", "LLLLLLL"]
#: A 2x2 block of 2x2 cells. One-pixel cells read as noise at this size; these read as a grid.
GRID = [
    "LLLLLLLL",
    "LOOLOOLL",
    "LOOLOOLL",
    "LLLLLLLL",
    "LOOLOOLL",
    "LOOLOOLL",
    "LLLLLLLL",
]
#: Operators rather than digits: a legible numeral set would be a whole font, and "+ - x =" says
#: "he is doing sums" just as clearly. Drawn 5px wide - at 3px they were a scatter of specks
#: rather than symbols, which is the difference between reading as maths and reading as dust.
OPERATORS = (
    ["..L..", "..L..", "LLLLL", "..L..", "..L.."],   # plus
    ["LLLLL"],                                        # minus
    ["L...L", ".L.L.", "..L..", ".L.L.", "L...L"],   # times
    ["LLLLL", ".....", "LLLLL"],                      # equals
)


def draw_thought_dots(grid: Grid, count: int, body_top: int) -> None:
    y = body_top - 6
    for i in range(count):
        stamp_prop(grid, 19 + i * 5, y, ["LLL", "LLL", "LLL"])


def draw_zed(grid: Grid, x: int, y: int, big: bool) -> None:
    stamp_prop(grid, x, y, BIG_Z if big else SMALL_Z)


def draw_bang(grid: Grid, x: int, y: int) -> None:
    stamp(grid, x, y, ["AA", "AA", "AA", "AA", "..", "AA"])


def draw_ball(grid: Grid, x: int, y: int) -> None:
    stamp_prop(grid, x, y, BALL)


def draw_note(grid: Grid, x: int, y: int) -> None:
    stamp_prop(grid, x, y, NOTE)


def draw_sparkle(grid: Grid, x: int, y: int, big: bool) -> None:
    stamp_prop(grid, x, y, SPARKLE_BIG if big else SPARKLE_SMALL)


#: Kept as an alias: every light-tone prop now carries a border, so there is no longer a
#: distinction between the ones that do and the ones that do not.
draw_speck = draw_sparkle


def draw_heart(grid: Grid, x: int, y: int) -> None:
    stamp(grid, x, y, HEART)


def draw_bandage(grid: Grid, body_top: int, *, dx: int = 0) -> None:
    """A bandage wrapped round his head, with a knot poking out on the right.

    Drawn over the body before the outline runs, so the knot - the only part that leaves the
    silhouette - picks up the dark border and reads as a separate lump.
    """
    y = body_top + 2
    fill_rect(grid, BODY_X + 1 + dx, y, BODY_W - 2, 3, "L")
    # a couple of darker creases so it reads as wrapped cloth rather than a painted stripe
    put(grid, BODY_X + 6 + dx, y, "F")
    put(grid, BODY_X + 7 + dx, y + 1, "F")
    put(grid, BODY_X + 15 + dx, y + 1, "F")
    put(grid, BODY_X + 16 + dx, y + 2, "F")
    # the knot, just off his right temple
    stamp(grid, BODY_X + BODY_W - 2 + dx, y - 1, ["LL.", "LLL", ".LL", "LL."])


# --- frame builders ------------------------------------------------------------------
# Each returns (grid, anchors). Order matters: creature -> outline -> measure -> decorations.


def frame_idle(grow: int, *, eyes: str = "open") -> tuple[Grid, Anchors]:
    grid = blank()
    draw_body(grid, grow=grow)
    draw_legs(grid, BODY_BOTTOM)
    draw_eyes(grid, mode=eyes)
    draw_arm(grid, BODY_BOTTOM, 0)
    outline(grid)
    return grid, measure_anchors(grid)


#: (leg_a_dx, leg_b_dx, a_lifted, b_lifted, body_dy) for the six walk frames.
#: A planted leg's dx falls by WALK_STEP_PX each frame - that is the foot gripping the ground.
WALK_CYCLE = [
    (+3, -1, False, True, 0),
    (0, +1, False, True, -1),
    (-3, +3, False, True, 0),
    (-1, +3, True, False, 0),
    (+1, 0, True, False, -1),
    (+3, -3, True, False, 0),
]


def frame_walk(index: int) -> tuple[Grid, Anchors]:
    dx_a, dx_b, lift_a, lift_b, body_dy = WALK_CYCLE[index]
    grid = blank()
    draw_body(grid, dy=body_dy)
    draw_legs(grid, BODY_BOTTOM + body_dy, dx_a=dx_a, dx_b=dx_b,
              lift_a=1 if lift_a else 0, lift_b=1 if lift_b else 0)
    draw_eyes(grid, mode="open", dy=body_dy)
    draw_arm(grid, BODY_BOTTOM + body_dy, 0)
    outline(grid)
    return grid, measure_anchors(grid, eye_dy=body_dy)


WAVE_KEYS = ((1, 0), (2, 1), (3, 1), (2, 0))


def frame_wave(index: int) -> tuple[Grid, Anchors]:
    pose, grow = WAVE_KEYS[index]
    grid = blank()
    draw_body(grid, grow=grow)
    draw_legs(grid, BODY_BOTTOM)
    draw_eyes(grid, mode="open")
    draw_arm(grid, BODY_BOTTOM, pose)
    outline(grid)
    return grid, measure_anchors(grid)


def frame_working(index: int) -> tuple[Grid, Anchors]:
    grid = blank()
    body_dy = -1 if index == 1 else 0
    _x0, y0, _w, _h = draw_body(grid, dy=body_dy)
    draw_legs(grid, BODY_BOTTOM + body_dy)
    draw_eyes(grid, mode="open", dy=body_dy)
    draw_arm(grid, BODY_BOTTOM + body_dy, 0)
    outline(grid)
    anchors = measure_anchors(grid, eye_dy=body_dy)
    draw_thought_dots(grid, index + 1, y0)
    return grid, anchors


def frame_angry(index: int) -> tuple[Grid, Anchors]:
    shake = (0, 1, 0, -1)[index]
    grid = blank()
    _x0, y0, _w, _h = draw_body(grid)
    draw_legs(grid, BODY_BOTTOM, dx_a=shake, dx_b=shake)
    draw_eyes(grid, mode="angry", dx=shake)
    draw_arm(grid, BODY_BOTTOM, 1 if index % 2 else 0)
    outline(grid)
    anchors = measure_anchors(grid, eye_dx=shake)
    if index == 0:
        draw_bang(grid, 4, y0 - 1)
    return grid, anchors


def frame_sleep(index: int) -> tuple[Grid, Anchors]:
    grid = blank()
    draw_body(grid, squash=4 if index == 0 else 3)
    draw_legs(grid, BODY_BOTTOM)
    draw_eyes(grid, mode="closed", dy=3)
    draw_arm(grid, BODY_BOTTOM, 0)
    outline(grid)
    anchors = measure_anchors(grid, eye_dy=3)
    if index == 0:
        draw_zed(grid, 40, 12, False)
        draw_zed(grid, 33, 5, True)
    else:
        draw_zed(grid, 40, 9, True)
        draw_zed(grid, 34, 2, False)
    return grid, anchors


def frame_wake(index: int) -> tuple[Grid, Anchors]:
    grid = blank()
    if index == 0:
        draw_body(grid, squash=3, widen=4)
        draw_legs(grid, BODY_BOTTOM)
        draw_eyes(grid, mode="closed", dy=2)
        draw_arm(grid, BODY_BOTTOM, 0)
        eye_dy = 2
    else:
        draw_body(grid, grow=3)
        draw_legs(grid, BODY_BOTTOM)
        draw_eyes(grid, mode="open")
        draw_arm(grid, BODY_BOTTOM, 2)
        eye_dy = 0
    outline(grid)
    return grid, measure_anchors(grid, eye_dy=eye_dy)


#: Eye offsets for the 9 cursor-tracking frames, indexed ``row * 3 + col`` where row/col are
#: 0..2 for up/centre/down and left/centre/right.
TRACK_OFFSETS = [(-2, -2), (0, -2), (2, -2),
                 (-2, 0), (0, 0), (2, 0),
                 (-2, 2), (0, 2), (2, 2)]


def frame_eyes_track(index: int) -> tuple[Grid, Anchors]:
    dx, dy = TRACK_OFFSETS[index]
    grid = blank()
    draw_body(grid)
    draw_legs(grid, BODY_BOTTOM)
    draw_eyes(grid, mode="open", dx=dx, dy=dy)
    draw_arm(grid, BODY_BOTTOM, 0)
    outline(grid)
    return grid, measure_anchors(grid, eye_dx=dx, eye_dy=dy)


LOOK_AROUND_DX = (0, -2, 2, 0)


def frame_look_around(index: int) -> tuple[Grid, Anchors]:
    grid = blank()
    draw_body(grid, grow=1 if index in (1, 2) else 0)
    draw_legs(grid, BODY_BOTTOM)
    draw_eyes(grid, mode="open", dx=LOOK_AROUND_DX[index])
    draw_arm(grid, BODY_BOTTOM, 0)
    outline(grid)
    return grid, measure_anchors(grid, eye_dx=LOOK_AROUND_DX[index])


def frame_wink(index: int) -> tuple[Grid, Anchors]:
    """The wink now carries a smirk - it is the one antic that is pure smugness."""
    grid = blank()
    draw_body(grid, grow=1 if index else 0)
    draw_legs(grid, BODY_BOTTOM)
    if index == 0:
        draw_eyes(grid, mode="open")
        draw_arm(grid, BODY_BOTTOM, 0)
    else:
        draw_eyes(grid, mode="wink", wink_side="a")
        draw_mouth(grid, "smirk")
        draw_arm(grid, BODY_BOTTOM, 4)
    outline(grid)
    return grid, measure_anchors(grid)


SPARKLE_KEYS = (
    ((22, 12, True), (38, 18, False)),
    ((20, 9, False), (40, 14, True)),
    ((24, 7, True), (36, 12, False), (14, 16, False)),
    ((21, 11, False), (39, 16, True)),
)


def frame_sparkle(index: int) -> tuple[Grid, Anchors]:
    grid = blank()
    draw_body(grid, grow=1 if index % 2 else 0)
    draw_legs(grid, BODY_BOTTOM)
    draw_eyes(grid, mode="glint")
    draw_arm(grid, BODY_BOTTOM, 0)
    outline(grid)
    anchors = measure_anchors(grid)
    for x, y, big in SPARKLE_KEYS[index]:
        draw_sparkle(grid, x, y, big)
    return grid, anchors


def frame_heart_eyes(index: int) -> tuple[Grid, Anchors]:
    """Hearts for eyes, and now a small smile under them."""
    grid = blank()
    draw_body(grid, grow=1 if index in (1, 2) else 0)
    draw_legs(grid, BODY_BOTTOM)
    draw_eyes(grid, mode="heart")
    draw_mouth(grid, "smile")
    draw_arm(grid, BODY_BOTTOM, 0)
    outline(grid)
    anchors = measure_anchors(grid)
    draw_heart(grid, 37 + (index // 2), 15 - index * 3)
    return grid, anchors


FOOTBALL_KEYS = (
    (38, 16, 0, 0),
    (38, 26, 0, 0),
    (37, 34, 3, -1),
    (38, 24, 1, 0),
    (39, 14, 0, 0),
    (40, 22, 0, 0),
)


def frame_football(index: int) -> tuple[Grid, Anchors]:
    ball_x, ball_y, lift, body_dy = FOOTBALL_KEYS[index]
    grid = blank()
    draw_body(grid, dy=body_dy)
    draw_legs(grid, BODY_BOTTOM + body_dy, lift_b=lift)
    eye_dy = body_dy + (-1 if ball_y < 20 else 1)
    draw_eyes(grid, mode="open", dy=eye_dy, dx=1)
    draw_arm(grid, BODY_BOTTOM + body_dy, 1 if lift else 0)
    outline(grid)
    anchors = measure_anchors(grid, eye_dx=1, eye_dy=eye_dy)
    draw_ball(grid, ball_x, ball_y)
    return grid, anchors


DANCE_KEYS = (
    (-2, -2, -1, 0),
    (-1, -1, 0, 1),
    (0, 0, 0, 0),
    (2, 1, 2, 0),
    (1, 0, 1, 1),
    (0, 0, 0, 0),
)


def frame_dance(index: int) -> tuple[Grid, Anchors]:
    body_dx, dx_a, dx_b, grow = DANCE_KEYS[index]
    grid = blank()
    draw_body(grid, dx=body_dx, grow=grow)
    draw_legs(grid, BODY_BOTTOM, dx_a=dx_a, dx_b=dx_b,
              lift_a=1 if index in (0, 1) else 0,
              lift_b=1 if index in (3, 4) else 0)
    draw_eyes(grid, mode="open", dx=body_dx)
    draw_arm(grid, BODY_BOTTOM, (2, 3, 1, 2, 3, 1)[index], dx=body_dx)
    outline(grid)
    anchors = measure_anchors(grid, eye_dx=body_dx)
    if index % 2 == 0:
        draw_note(grid, 6 + index, 10 - index)
    else:
        draw_note(grid, 40 - index, 8 + index)
    return grid, anchors


#: Deep knee dips. A true horizontal push-up would need a whole second silhouette; this reads
#: clearly as a workout at 48px and reuses the squash the body already supports.
PUSHUP_SQUASH = (0, 4, 7, 4, -2)


def frame_pushups(index: int) -> tuple[Grid, Anchors]:
    squash = PUSHUP_SQUASH[index]
    grid = blank()
    if squash < 0:
        draw_body(grid, grow=-squash)
    else:
        draw_body(grid, squash=squash)
    draw_legs(grid, BODY_BOTTOM)
    eye_dy = 1 if squash > 4 else 0
    draw_eyes(grid, mode="closed" if index == 2 else "open", dy=eye_dy)
    draw_arm(grid, BODY_BOTTOM, 1 if squash >= 4 else 0)
    outline(grid)
    return grid, measure_anchors(grid, eye_dy=eye_dy)


#: (body dy, squash, grow, leg length or None for grounded) - frames 1..3 are airborne, which is
#: why the sheet validator exempts them from the feet-on-the-baseline rule.
HOP_KEYS = (
    (0, 5, 0, None),
    (-4, 0, 2, 4),
    (-11, 0, 3, 3),
    (-5, 0, 1, 4),
    (0, 4, 0, None),
)


def frame_hop(index: int) -> tuple[Grid, Anchors]:
    body_dy, squash, grow, leg_len = HOP_KEYS[index]
    grid = blank()
    draw_body(grid, dy=body_dy, squash=squash, grow=grow)
    draw_legs(grid, BODY_BOTTOM + body_dy, length=leg_len)
    draw_eyes(grid, mode="wide" if index == 2 else "open", dy=body_dy)
    draw_arm(grid, BODY_BOTTOM + body_dy, 2 if leg_len else 0)
    outline(grid)
    return grid, measure_anchors(grid, eye_dy=body_dy)


SIT_KEYS = ((2, 4), (3, 6), (2, 5), (1, 3))


def frame_sit_dangle(index: int) -> tuple[Grid, Anchors]:
    dx_a, dx_b = SIT_KEYS[index]
    grid = blank()
    draw_body(grid, squash=6)
    draw_legs(grid, BODY_BOTTOM, dx_a=dx_a, dx_b=dx_b)
    draw_eyes(grid, mode="open", dy=2, dx=1)
    draw_arm(grid, BODY_BOTTOM, 0)
    outline(grid)
    return grid, measure_anchors(grid, eye_dx=1, eye_dy=2)


#: (wobble, squash, star positions) - he sways on his feet with stars going round his head.
INJURED_KEYS = (
    (0, 0, ((14, 12, True), (34, 16, False))),
    (1, 1, ((18, 9, False), (36, 13, True))),
    (0, 0, ((24, 8, True), (12, 15, False))),
    (-1, 1, ((30, 10, False), (16, 14, True))),
)


def frame_injured(index: int) -> tuple[Grid, Anchors]:
    """He landed on his head: the whole creature is flipped, legs waving in the air.

    The stars are drawn *after* the flip so they still circle overhead rather than being buried
    under him, and the anchors are mapped through the same transform so a hat still tracks his
    head - which is now the part on the ground.
    """
    wobble, squash, stars = INJURED_KEYS[index]
    grid = blank()
    _x0, y0, _w, _h = draw_body(grid, dx=wobble, squash=squash)
    draw_legs(grid, BODY_BOTTOM, dx_a=wobble, dx_b=wobble)
    draw_eyes(grid, mode="dazed", dx=wobble, dy=squash)
    draw_mouth(grid, "wavy", dx=wobble, dy=squash)
    draw_arm(grid, BODY_BOTTOM, 0, dx=wobble)
    draw_bandage(grid, y0, dx=wobble)
    outline(grid)

    upright = measure_anchors(grid, eye_dx=wobble, eye_dy=squash)
    grid, top = flip_and_seat(grid)
    anchors = {
        kind: [point[0], max(0, min(FRAME - 1, BASELINE_ROW + top - point[1]))]
        for kind, point in upright.items()
    }

    for x, y, big in stars:
        draw_sparkle(grid, x, y, big)
    return grid, anchors


def frame_tumble(index: int) -> tuple[Grid, Anchors]:
    """One quarter-turn of the spin he does on the way down, eyes wide.

    Each rotation is re-seated on the baseline rather than centred in the cell, so the sprite does
    not jump when he switches into the spin on release or out of it on landing - the rotation
    alone sells the tumble, since the window is already hurtling downwards.
    """
    grid = blank()
    draw_body(grid)
    draw_legs(grid, BODY_BOTTOM)
    draw_eyes(grid, mode="wide")
    draw_arm(grid, BODY_BOTTOM, 1)
    outline(grid)
    grid = seat_to_baseline(rotate_grid(grid, index))
    return grid, measure_anchors(grid)


# --- world-aware states --------------------------------------------------------------
# Everything below is driven by the clock, the season, or which app you just switched to. They
# follow the same order as the rest: creature -> outline -> measure anchors -> decorations.


#: (grow, eyes, arm pose, puff distance or None) - he swells up on the inhale and settles back.
#: Deliberately no mouth: the whole design carries expression in the eyes, and inventing a mouth
#: for one state would read as a different character. Closed eyes plus the swell plus the puff of
#: air is enough, especially since this only ever plays first thing in the morning.
YAWN_KEYS = (
    (0, "open", 0, None),
    (3, "closed", 1, 3),
    (5, "closed", 2, 6),
    (1, "closed", 0, 9),
)


def frame_yawn(index: int) -> tuple[Grid, Anchors]:
    grow, eyes, pose, puff = YAWN_KEYS[index]
    grid = blank()
    _x0, y0, _w, _h = draw_body(grid, grow=grow)
    draw_legs(grid, BODY_BOTTOM)
    draw_eyes(grid, mode=eyes, dy=1)
    draw_arm(grid, BODY_BOTTOM, pose)
    outline(grid)
    anchors = measure_anchors(grid, eye_dy=1)
    if puff is not None:
        # A little breath drifting up and away, thinning as it goes.
        draw_speck(grid, 6 - puff // 3, y0 + 2 - puff, True)
        draw_speck(grid, 4 - puff // 4, y0 - 1 - puff, False)
    return grid, anchors


#: (grow, arm pose, leg spread, effort marks) - reaching up on waking, arm right out at the top.
STRETCH_KEYS = (
    (0, 0, 0, False),
    (4, 2, 1, False),
    (7, 4, 2, True),
    (2, 1, 0, False),
)


def frame_stretch(index: int) -> tuple[Grid, Anchors]:
    grow, pose, spread, effort = STRETCH_KEYS[index]
    grid = blank()
    _x0, y0, _w, _h = draw_body(grid, grow=grow)
    draw_legs(grid, BODY_BOTTOM, dx_a=-spread, dx_b=spread)
    draw_eyes(grid, mode="closed" if grow >= 4 else "open")
    draw_arm(grid, BODY_BOTTOM, pose)
    outline(grid)
    anchors = measure_anchors(grid)
    if effort:
        # Two short lines either side of his head, the shorthand for "nnngh".
        stamp_prop(grid, 5, y0 + 1, ["LLL"])
        stamp_prop(grid, 41, y0 + 1, ["LLL"])
    return grid, anchors


#: (body dx, eyes, arm pose, mug position, steam) - the mug starts on the floor to his right,
#: which is the side his arm is on, then comes up to his face.
COFFEE_KEYS = (
    (0, "open", 0, (39, 42), False),
    (-1, "open", 1, (39, 42), False),
    (0, "closed", 2, (37, 24), True),
    (0, "closed", 2, (37, 23), True),
    (0, "glint", 0, (39, 42), False),
)


def frame_coffee(index: int) -> tuple[Grid, Anchors]:
    body_dx, eyes, pose, (mug_x, mug_y), steam = COFFEE_KEYS[index]
    grid = blank()
    draw_body(grid, dx=body_dx)
    draw_legs(grid, BODY_BOTTOM, dx_a=body_dx, dx_b=body_dx)
    draw_eyes(grid, mode=eyes, dx=body_dx + 1)
    draw_arm(grid, BODY_BOTTOM, pose, dx=body_dx)
    outline(grid)
    anchors = measure_anchors(grid, eye_dx=body_dx + 1)
    stamp_prop(grid, mug_x, mug_y, MUG)
    if steam:
        draw_speck(grid, mug_x + 1, mug_y - 4, False)
        draw_speck(grid, mug_x + 3, mug_y - 7, False)
    return grid, anchors


#: (squash, eyes, arm pose, sweat positions) - wilting and fanning himself. Shared by "the machine
#: is pinned" and "it is summer", because they look like the same complaint.
HOT_KEYS = (
    (2, "closed", 2, ((7, 22),)),
    (3, "closed", 3, ((5, 17), (41, 24))),
    (2, "open", 2, ((43, 19),)),
    (4, "closed", 3, ((6, 14), (42, 27))),
    (2, "open", 2, ((4, 21),)),
)


def frame_hot(index: int) -> tuple[Grid, Anchors]:
    squash, eyes, pose, sweat = HOT_KEYS[index]
    grid = blank()
    draw_body(grid, squash=squash)
    draw_legs(grid, BODY_BOTTOM)
    # Sleepy rather than merely shut: the droop is what sells "wilting" over "blinking".
    draw_eyes(grid, mode="half" if eyes == "closed" else eyes, dy=squash)
    draw_brows(grid, "droop", dy=squash)
    draw_arm(grid, BODY_BOTTOM, pose)
    outline(grid)
    anchors = measure_anchors(grid, eye_dy=squash)
    for x, y in sweat:
        stamp_prop(grid, x, y, DROP)
    return grid, anchors


#: The task-just-finished celebration: a wind-up, a triumphant punch, a held sparkly beat (twinkling
#: through two sparkle layouts, not frozen on one), then a settle into a satisfied smirk. The punch
#: reuses arm pose 2 exactly as dance's own opening frame does - the tallest reach the sheet has,
#: already proven to read correctly attached at the shoulder - rather than inventing a taller one,
#: which only pushed the fist loose of the body.
TASK_FINISHED_SPARKLES_A = ((39, 6, True), (32, 10, False), (44, 15, False))
TASK_FINISHED_SPARKLES_B = ((41, 9, False), (30, 5, True), (46, 13, False))


def frame_task_finished(index: int) -> tuple[Grid, Anchors]:
    grid = blank()
    if index == 0:                    # a small coil before the jump
        draw_body(grid, squash=3)
        draw_legs(grid, BODY_BOTTOM)
        draw_eyes(grid, mode="open")
        draw_arm(grid, BODY_BOTTOM, 0)
    elif index == 1:                  # the punch - arm up, eyes wide
        draw_body(grid)
        draw_legs(grid, BODY_BOTTOM)
        draw_eyes(grid, mode="wide")
        draw_arm(grid, BODY_BOTTOM, 2)
    elif index in (2, 3):             # held a beat, grinning, sparkles twinkling round the hand
        draw_body(grid)
        draw_legs(grid, BODY_BOTTOM)
        draw_eyes(grid, mode="glint")
        draw_mouth(grid, "smile")
        draw_arm(grid, BODY_BOTTOM, 2)
    elif index == 4:                  # coming back down, still pleased
        draw_body(grid)
        draw_legs(grid, BODY_BOTTOM)
        draw_eyes(grid, mode="open")
        draw_mouth(grid, "smile")
        draw_arm(grid, BODY_BOTTOM, 1)
    else:                              # settled, smug about it
        draw_body(grid)
        draw_legs(grid, BODY_BOTTOM)
        draw_face(grid, "smug")
        draw_arm(grid, BODY_BOTTOM, 0)
    outline(grid)
    anchors = measure_anchors(grid)
    if index == 2:
        for x, y, big in TASK_FINISHED_SPARKLES_A:
            draw_sparkle(grid, x, y, big)
    elif index == 3:
        for x, y, big in TASK_FINISHED_SPARKLES_B:
            draw_sparkle(grid, x, y, big)
    return grid, anchors


#: (shake, squash, snow y offset) - hunched and trembling, with flakes drifting down BESIDE him
#: rather than over him, so they never muddle the silhouette.
SHIVER_KEYS = ((0, 3), (1, 4), (-1, 3), (1, 4))
SNOW_COLUMNS = ((5, 2), (42, 8), (7, 20))


def frame_shiver(index: int) -> tuple[Grid, Anchors]:
    shake, squash = SHIVER_KEYS[index]
    grid = blank()
    draw_body(grid, dx=shake, squash=squash)
    draw_legs(grid, BODY_BOTTOM, dx_a=shake, dx_b=shake)
    draw_eyes(grid, mode="closed" if index % 2 else "open", dx=shake, dy=squash)
    draw_arm(grid, BODY_BOTTOM, 0, dx=shake)
    outline(grid)
    anchors = measure_anchors(grid, eye_dx=shake, eye_dy=squash)
    for x, y in SNOW_COLUMNS:
        draw_speck(grid, x, (y + index * 6) % 34, index % 2 == 0)
    return grid, anchors


#: (leaf x, leaf y, eye dx, eye dy) - one leaf tumbling past while he follows it down.
#: It has to fall down the empty column to his LEFT: routed anywhere nearer the middle it lands on
#: his chest, where a small dark-red shape stops reading as a leaf and starts reading as a stain.
AUTUMN_KEYS = (
    (4, 4, -1, -1),
    (6, 15, -1, 0),
    (3, 26, -1, 1),
    (6, 36, -1, 2),
)


def frame_autumn(index: int) -> tuple[Grid, Anchors]:
    leaf_x, leaf_y, eye_dx, eye_dy = AUTUMN_KEYS[index]
    grid = blank()
    draw_body(grid)
    draw_legs(grid, BODY_BOTTOM)
    draw_eyes(grid, mode="open", dx=eye_dx, dy=eye_dy)
    draw_arm(grid, BODY_BOTTOM, 0)
    outline(grid)
    anchors = measure_anchors(grid, eye_dx=eye_dx, eye_dy=eye_dy)
    stamp_prop(grid, leaf_x, leaf_y, LEAF)
    return grid, anchors


# --- app personas ---------------------------------------------------------------------

#: (grow, leg brace, eyes, effort) - shoving upward at the underside of something above him.
#: He never wins. The window does not move, and that is the joke: nothing outside his own window
#: is touched, so this cannot displace anybody's layout however hard he tries.
STRAIN_KEYS = (
    (0, 0, "open", False),
    (3, 1, "open", True),
    (6, 2, "closed", True),
    (4, 2, "closed", True),
    (1, 0, "open", False),
)


def frame_strain(index: int) -> tuple[Grid, Anchors]:
    grow, brace, eyes, effort = STRAIN_KEYS[index]
    grid = blank()
    _x0, y0, _w, _h = draw_body(grid, grow=grow)
    draw_legs(grid, BODY_BOTTOM, dx_a=-brace, dx_b=brace)
    # Determined on the wind-up, eyes screwed shut on the big push.
    if eyes == "closed":
        draw_eyes(grid, mode="closed", dy=-1)
        draw_brows(grid, "determined", dy=-1)
    else:
        draw_face(grid, "determined", dy=-1)
    draw_arm(grid, BODY_BOTTOM, 2)     # arm straight up against the surface
    outline(grid)
    anchors = measure_anchors(grid, eye_dy=-1)
    if effort:
        # Short marks either side of his head - the shorthand for "nnngh".
        stamp_prop(grid, 4, y0 + 1 + grow // 3, ["LLL"])
        stamp_prop(grid, 41, y0 + 1 + grow // 3, ["LLL"])
    return grid, anchors


#: (arm pose, body dy, keystroke fleck positions). His arm is on the right, so the "hands" go
#: out to the right and the flecks trail off that side.
TYPING_KEYS = (
    (4, 0, ((44, 31),)),
    (1, -1, ((45, 33),)),
    (4, 0, ((44, 30), (46, 33))),
    (1, -1, ((45, 32),)),
    (4, 0, ((43, 31),)),
)


def frame_typing(index: int) -> tuple[Grid, Anchors]:
    pose, body_dy, flecks = TYPING_KEYS[index]
    grid = blank()
    draw_body(grid, dy=body_dy)
    draw_legs(grid, BODY_BOTTOM + body_dy)
    draw_face(grid, "determined", dy=body_dy)  # locked in
    draw_arm(grid, BODY_BOTTOM + body_dy, pose)
    outline(grid)
    anchors = measure_anchors(grid, eye_dy=body_dy)
    for x, y in flecks:
        draw_speck(grid, x, y, False)
    return grid, anchors


#: (operator, x, y, eye dx, arm pose) - sums drifting past while his eyes flick between them.
COUNTING_KEYS = (
    (0, 4, 10, -1, 0),
    (2, 39, 7, 1, 0),
    (1, 4, 18, -1, 1),
    (3, 39, 14, 1, 0),
)


def frame_counting(index: int) -> tuple[Grid, Anchors]:
    operator, x, y, eye_dx, pose = COUNTING_KEYS[index]
    grid = blank()
    draw_body(grid)
    draw_legs(grid, BODY_BOTTOM)
    draw_eyes(grid, mode="glint" if index == 3 else "open", dx=eye_dx, dy=-1)
    draw_arm(grid, BODY_BOTTOM, pose)
    outline(grid)
    anchors = measure_anchors(grid, eye_dx=eye_dx, eye_dy=-1)
    stamp_prop(grid, x, y, OPERATORS[operator])
    if index == 3:
        draw_speck(grid, 40, 22, False)  # the answer lands
    return grid, anchors


#: (arm pose, highlighted cell or None) - he points at the grid and walks a red cell around it.
CELL_ORIGINS = ((1, 1), (4, 1), (1, 4), (4, 4))
SPREADSHEET_KEYS = ((0, None), (4, 0), (4, 3), (1, 1))
GRID_AT = (40, 14)


def frame_spreadsheet(index: int) -> tuple[Grid, Anchors]:
    pose, cell = SPREADSHEET_KEYS[index]
    grid = blank()
    draw_body(grid)
    draw_legs(grid, BODY_BOTTOM)
    draw_eyes(grid, mode="open", dx=1, dy=-1)
    draw_arm(grid, BODY_BOTTOM, pose)
    outline(grid)
    anchors = measure_anchors(grid, eye_dx=1, eye_dy=-1)
    stamp_prop(grid, *GRID_AT, GRID)
    if cell is not None:
        cx, cy = CELL_ORIGINS[cell]
        fill_rect(grid, GRID_AT[0] + cx, GRID_AT[1] + cy, 2, 2, "A")
    return grid, anchors


#: (envelope y, arm pose, eye mode, sparkle) - post arrives and he points it out to you.
MAIL_KEYS = (
    (16, 0, "open", False),
    (13, 2, "glint", False),
    (14, 4, "glint", True),
    (13, 2, "open", False),
)
MAIL_X = 39


def frame_mail(index: int) -> tuple[Grid, Anchors]:
    envelope_y, pose, eyes, sparkle = MAIL_KEYS[index]
    grid = blank()
    draw_body(grid)
    draw_legs(grid, BODY_BOTTOM)
    draw_face(grid, "surprised" if eyes == "glint" else "neutral", dx=1, dy=-1)
    draw_arm(grid, BODY_BOTTOM, pose)
    outline(grid)
    anchors = measure_anchors(grid, eye_dx=1, eye_dy=-1)
    stamp_prop(grid, MAIL_X, envelope_y, ENVELOPE)
    if sparkle:
        draw_speck(grid, MAIL_X - 4, envelope_y - 3, False)
    return grid, anchors


# --- blinks and expressions ------------------------------------------------------------

#: Half-mast lids on the way down and back up. A slow blink is the difference between "he blinked"
#: and "he is fading" - the single 120ms frame the ordinary blink uses cannot carry that.
SLEEPY_BLINK_KEYS = ("half", "closed", "half")


def frame_blink_slow(index: int) -> tuple[Grid, Anchors]:
    grid = blank()
    draw_body(grid, grow=1)
    draw_legs(grid, BODY_BOTTOM)
    draw_eyes(grid, mode=SLEEPY_BLINK_KEYS[index], dy=1)
    draw_brows(grid, "droop", dy=1)
    draw_arm(grid, BODY_BOTTOM, 0)
    outline(grid)
    return grid, measure_anchors(grid, eye_dy=1)


#: (body dy, grow, expression) - a jolt upward with wide eyes, settling back. Played when something
#: appears in front of him.
STARTLE_KEYS = (
    (-2, 2, "surprised"),
    (-3, 3, "surprised"),
    (0, 1, "surprised"),
    (0, 0, "neutral"),
)


def frame_startle(index: int) -> tuple[Grid, Anchors]:
    body_dy, grow, expression = STARTLE_KEYS[index]
    grid = blank()
    draw_body(grid, dy=body_dy, grow=grow)
    draw_legs(grid, BODY_BOTTOM + body_dy)
    draw_face(grid, expression, dy=body_dy)
    draw_arm(grid, BODY_BOTTOM + body_dy, 1 if grow else 0)
    outline(grid)
    return grid, measure_anchors(grid, eye_dy=body_dy)


# --- being petted --------------------------------------------------------------------
#
# The only animation in the whole set that is a response to KINDNESS. Everything else he does to
# you or about you; this one is done to him. So it is deliberately the softest thing he has - no
# props flying about, no bounce, just a lean, shut eyes and a heart.

#: (body dx, grow, heart y or None). He presses UP into the hand, which is why grow rises rather
#: than the body sinking - being petted from above should look like leaning into it, not cowering.
PET_KEYS = (
    (0, 1, None),
    (1, 3, 25),
    (1, 4, 17),
    (0, 2, 9),
)
#: The heart rises up the empty left column, not over his head. Drawn centred it landed *on* the
#: silhouette and read as a lump growing out of his skull; the body spans x 11..36, so anything
#: left of 10 is guaranteed clear of him at every grow value.
PET_HEART_X = 4


def frame_pet(index: int) -> tuple[Grid, Anchors]:
    body_dx, grow, heart_y = PET_KEYS[index]
    grid = blank()
    draw_body(grid, dx=body_dx, grow=grow)
    draw_legs(grid, BODY_BOTTOM)
    # Closed eyes and a smile. "closed" is the same flat lid the blink uses: held, and with a
    # mouth under it, the identical four pixels stop reading as a blink and start reading as bliss.
    draw_eyes(grid, mode="closed", dx=body_dx)
    draw_mouth(grid, "smile", dx=body_dx)
    draw_arm(grid, BODY_BOTTOM, 0, dx=body_dx)
    outline(grid)
    anchors = measure_anchors(grid, eye_dx=body_dx)
    if heart_y is not None:
        # Bordered, unlike the heart-eyes: those sit on his face where the body backs them, and
        # this one floats over whatever the taskbar happens to be.
        stamp_prop(grid, PET_HEART_X, heart_y, HEART)
    return grid, anchors


# --- micro-fidgets -------------------------------------------------------------------
#
# The set pieces cover what he DOES; these cover the moments between them. Each is under a second
# and none of them has a prop or a pose you would notice on purpose - which is the point. They are
# what stops the gaps between activities reading as him waiting for the next cue.

#: (arm dx, arm dy, eye mode) - the hand works away at the SIDE of his head, not above it. Raised
#: clear of the crown it read as a tuft of hair rather than a limb, and was hard to tell from the
#: watch check, which uses the same pose. Pulling it back into the silhouette fixes both.
SCRATCH_KEYS = (
    (0, 1, "open"),
    (-2, 0, "half"),
    (-1, 1, "half"),
    (-2, 0, "half"),
)


def frame_scratch(index: int) -> tuple[Grid, Anchors]:
    arm_dx, arm_dy, eyes = SCRATCH_KEYS[index]
    grid = blank()
    draw_body(grid)
    draw_legs(grid, BODY_BOTTOM)
    # Squinting through it. The eyes are what carry this one - the arm movement alone is only a
    # couple of pixels, which is about right for a fidget and not enough to read on its own.
    draw_eyes(grid, mode=eyes)
    # Pose 2 is the tall reach; ``dy`` goes in through body_bottom, which is what sets its height.
    draw_arm(grid, BODY_BOTTOM + arm_dy, 2, dx=arm_dx)
    outline(grid)
    return grid, measure_anchors(grid)


#: (lifted leg, lift px, body dy) - one foot comes up and taps back down, and the body dips a
#: pixel on the beat it lands. The other sole never leaves the baseline, so this is not airborne.
FOOT_TAP_KEYS = (
    ("b", 0, 0),
    ("b", 3, 0),
    ("b", 4, 0),
    ("b", 0, 1),
)


def frame_foot_tap(index: int) -> tuple[Grid, Anchors]:
    side, lift, body_dy = FOOT_TAP_KEYS[index]
    grid = blank()
    draw_body(grid, dy=body_dy, squash=body_dy)
    draw_legs(
        grid,
        BODY_BOTTOM + body_dy,
        lift_a=lift if side == "a" else 0,
        lift_b=lift if side == "b" else 0,
    )
    draw_eyes(grid, mode="open", dy=body_dy)
    draw_arm(grid, BODY_BOTTOM + body_dy, 0)
    outline(grid)
    return grid, measure_anchors(grid, eye_dy=body_dy)


#: (arm pose, eye dx, show the watch). He has no wrist and no watch; checking one anyway is the
#: joke, and it doubles as the "how long have I been waiting" tell while he is being ignored.
WATCH_KEYS = (
    (0, 0, False),
    (2, 1, True),
    (2, 1, True),
    (0, 0, False),
)
#: Where the face of the imaginary watch sits, level with the raised hand.
WATCH_AT = (ARM_X + 2, BODY_BOTTOM - 22)


def frame_check_watch(index: int) -> tuple[Grid, Anchors]:
    pose, eye_dx, watch = WATCH_KEYS[index]
    grid = blank()
    draw_body(grid)
    draw_legs(grid, BODY_BOTTOM)
    draw_eyes(grid, mode="open", dx=eye_dx)
    draw_arm(grid, BODY_BOTTOM, pose)
    outline(grid)
    anchors = measure_anchors(grid, eye_dx=eye_dx)
    if watch:
        stamp_prop(grid, WATCH_AT[0], WATCH_AT[1], ["LL", "LL"])
    return grid, anchors


# --- involuntary things ---------------------------------------------------------------
#
# A sneeze, a stumble, a bout of hiccups. None of them is something he decides to do, and that is
# exactly why they sell him as alive: everything else in the sheet is either a reaction to you or
# an activity he chose. These just happen to him.

#: (body dy, grow, squash, eye mode, mouth, spray). The wind-up leans back and the sneeze throws
#: him forward - the recoil is the whole gag, so it is worth two frames out of four.
SNEEZE_KEYS = (
    (-1, 1, 0, "closed", "o", None),
    (-2, 3, 0, "closed", "o", None),
    (2, 0, 3, "closed", "o", (6, 30)),
    (0, 0, 1, "half", None, None),
)


def frame_sneeze(index: int) -> tuple[Grid, Anchors]:
    body_dy, grow, squash, eyes, mouth, spray = SNEEZE_KEYS[index]
    grid = blank()
    draw_body(grid, dy=body_dy, grow=grow, squash=squash)
    draw_legs(grid, BODY_BOTTOM + body_dy)
    draw_eyes(grid, mode=eyes, dy=body_dy)
    if mouth:
        draw_mouth(grid, mouth, dy=body_dy)
    draw_arm(grid, BODY_BOTTOM + body_dy, 0)
    outline(grid)
    anchors = measure_anchors(grid, eye_dy=body_dy)
    if spray:
        # Out of the empty left column, so it never lands on his own silhouette.
        stamp_prop(grid, spray[0], spray[1], ["L.L", ".L.", "L.L"])
    return grid, anchors


#: (body dx, squash, leg dx a, leg dx b, arm pose, eye mode). He pitches forward, catches himself
#: with both legs splayed, then straightens up looking mildly embarrassed.
TRIP_KEYS = (
    (3, 2, 2, -3, 4, "wide"),
    (5, 6, 4, -5, 4, "wide"),
    (1, 3, 1, -1, 2, "half"),
)


def frame_trip(index: int) -> tuple[Grid, Anchors]:
    body_dx, squash, dx_a, dx_b, arm, eyes = TRIP_KEYS[index]
    grid = blank()
    draw_body(grid, dx=body_dx, squash=squash)
    # Legs stay planted on the baseline however far he pitches - he is stumbling, not falling, and
    # a frame that leaves the ground would break the soles-on-the-strip rule the validator checks.
    draw_legs(grid, BODY_BOTTOM, dx_a=dx_a, dx_b=dx_b)
    draw_eyes(grid, mode=eyes, dx=body_dx)
    draw_arm(grid, BODY_BOTTOM, arm, dx=body_dx)
    outline(grid)
    return grid, measure_anchors(grid, eye_dx=body_dx)


#: (grow, squash, eye mode, whether the little burst shows). A hiccup is a jolt, not a jump: the
#: body snaps upward and the feet never leave the ground.
HICCUP_KEYS = (
    (0, 0, "open", False),
    (4, 0, "wide", True),
    (0, 2, "half", False),
)


def frame_hiccup(index: int) -> tuple[Grid, Anchors]:
    grow, squash, eyes, burst = HICCUP_KEYS[index]
    grid = blank()
    draw_body(grid, grow=grow, squash=squash)
    draw_legs(grid, BODY_BOTTOM)
    draw_eyes(grid, mode=eyes)
    draw_arm(grid, BODY_BOTTOM, 0)
    outline(grid)
    anchors = measure_anchors(grid)
    if burst:
        stamp_prop(grid, 5, 16, ["L"])
    return grid, anchors


# --- the Away Guard -------------------------------------------------------------------
#
# While someone types the guard PIN he puts both hands over his eyes: "I'm not looking." It is the
# only frame set in which he has TWO arms. Everywhere else the single arm on his right is enough,
# but a one-handed cover reads as rubbing an eye, and the whole point of this pose is that it is
# unmistakable from across a desk.

#: Left hand, as rectangles relative to the frame (x, y, w, h). The mitt sits over eye A with a
#: pixel to spare on every side, so the jiggle below can move it without a sliver of eye showing.
#: The forearm runs out of the mitt's lower-outer corner and the upper arm drops down the OUTSIDE
#: of the body, which is what makes it read as an arm reaching up rather than a patch on his face.
#: The right hand is the exact mirror image across the body's centre line (x -> 47 - x).
COVER_MITT = (14, 25, 8, 8)
COVER_FOREARM = (9, 30, 5, 4)
COVER_UPPER_ARM = (7, 32, 4, 5)
#: Finger grooves, as x offsets into the mitt, running down from its top edge.
COVER_GROOVES = (2, 5)
COVER_GROOVE_DEPTH = 3

#: (grow, hand dy, hands spread) - a small nervous loop, not a performance. The hands bob a pixel
#: on the breath and twitch apart once, the way someone very determined not to peek fidgets.
#: ``spread`` moves each hand OUTWARD by that much; the mitt is three pixels wider than the eye,
#: so even spread the eyes stay fully covered.
COVER_EYES_KEYS = (
    (0, 0, 0),
    (1, -1, 0),
    (0, 0, 0),
    (1, 0, 1),
)


def _cover_limb(*, dx: int, dy: int) -> dict[tuple[int, int], str]:
    """The LEFT covering limb as {(x, y): colour}, shaded; the right one is its mirror image."""
    cells: set[tuple[int, int]] = set()
    for x, y, w, h in (COVER_MITT, COVER_FOREARM, COVER_UPPER_ARM):
        cells.update((xx + dx, yy + dy) for yy in range(y, y + h) for xx in range(x, x + w))
    mx, my, mw, _mh = COVER_MITT
    mx, my = mx + dx, my + dy
    shaded: dict[tuple[int, int], str] = {}
    for x, y in cells:
        if y == my and mx <= x < mx + mw:
            shaded[(x, y)] = "H"                    # knuckles catch the light
        elif (x, y + 1) not in cells:
            shaded[(x, y)] = "S"                    # underside in shadow
        else:
            shaded[(x, y)] = "B"
    for groove in COVER_GROOVES:                    # the fingers, so it is a hand and not a lid
        for row in range(1, 1 + COVER_GROOVE_DEPTH):
            shaded[(mx + groove, my + row)] = "S"
    return shaded


def draw_cover_hands(grid: Grid, *, dy: int = 0, spread: int = 0) -> None:
    """Both hands over both eyes, each with its own dark border where it overlaps the body.

    A hand in body tones laid over the body would simply vanish, and ``outline`` only borders the
    silhouette against transparency. So each limb is bordered here wherever it touches the body;
    the parts that hang outside the body are left for ``outline`` to finish, which keeps the
    border one pixel thick everywhere instead of doubling up along the arms.
    """
    left = _cover_limb(dx=-spread, dy=dy)
    right = {(FRAME - 1 - x, y): ch for (x, y), ch in left.items()}
    for limb in (left, right):
        for (x, y), ch in limb.items():
            put(grid, x, y, ch)
        for x, y in limb:
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if (nx, ny) in limb or not (0 <= nx < FRAME and 0 <= ny <= BASELINE_ROW):
                    continue
                if grid[ny][nx] != ".":
                    grid[ny][nx] = "O"


def frame_cover_eyes(index: int) -> tuple[Grid, Anchors]:
    grow, hand_dy, spread = COVER_EYES_KEYS[index]
    grid = blank()
    draw_body(grid, grow=grow)
    draw_legs(grid, BODY_BOTTOM)
    # Eyes drawn first and then covered, so a costume or a future tweak that moves the hands can
    # never leave him eyeless - at worst an eye peeks out, which is in character.
    draw_eyes(grid, mode="closed")
    # No mouth: tried a small "o" under the hands and at 48px it only cluttered the gap between
    # them. The two mitts carry the whole expression on their own.
    draw_cover_hands(grid, dy=hand_dy, spread=spread)
    outline(grid)
    return grid, measure_anchors(grid)


def build_frames() -> dict[int, tuple[Grid, Anchors]]:
    frames: dict[int, tuple[Grid, Anchors]] = {}
    frames[0] = frame_idle(0)
    frames[1] = frame_idle(1)
    frames[2] = frame_idle(2)
    frames[3] = frame_idle(0, eyes="closed")
    for i in range(6):
        frames[10 + i] = frame_walk(i)
    for i in range(4):
        frames[20 + i] = frame_wave(i)
    for i in range(3):
        frames[30 + i] = frame_working(i)
    for i in range(4):
        frames[40 + i] = frame_angry(i)
    for i in range(2):
        frames[50 + i] = frame_sleep(i)
    for i in range(2):
        frames[60 + i] = frame_wake(i)
    for i in range(9):
        frames[70 + i] = frame_eyes_track(i)
    for i in range(4):
        frames[80 + i] = frame_look_around(i)
    for i in range(3):
        frames[90 + i] = frame_wink(i)
    for i in range(4):
        frames[100 + i] = frame_sparkle(i)
    for i in range(4):
        frames[110 + i] = frame_heart_eyes(i)
    for i in range(6):
        frames[120 + i] = frame_football(i)
    for i in range(6):
        frames[130 + i] = frame_dance(i)
    for i in range(5):
        frames[140 + i] = frame_pushups(i)
    for i in range(5):
        frames[150 + i] = frame_hop(i)
    for i in range(4):
        frames[160 + i] = frame_sit_dangle(i)
    for i in range(4):
        frames[170 + i] = frame_injured(i)
    for i in range(4):
        frames[180 + i] = frame_tumble(i)
    # world-aware states
    for i in range(4):
        frames[190 + i] = frame_yawn(i)
    for i in range(4):
        frames[200 + i] = frame_stretch(i)
    for i in range(5):
        frames[210 + i] = frame_coffee(i)
    for i in range(5):
        frames[220 + i] = frame_hot(i)
    for i in range(4):
        frames[230 + i] = frame_shiver(i)
    for i in range(4):
        frames[240 + i] = frame_autumn(i)
    # app personas
    for i in range(5):
        frames[250 + i] = frame_typing(i)
    for i in range(4):
        frames[260 + i] = frame_counting(i)
    for i in range(4):
        frames[270 + i] = frame_spreadsheet(i)
    for i in range(4):
        frames[280 + i] = frame_mail(i)
    for i in range(5):
        frames[290 + i] = frame_strain(i)
    for i in range(4):
        frames[300 + i] = frame_startle(i)
    for i in range(3):
        frames[310 + i] = frame_blink_slow(i)
    # being petted, and the micro-fidgets that fill the gaps between everything else
    for i in range(4):
        frames[320 + i] = frame_pet(i)
    for i in range(4):
        frames[330 + i] = frame_scratch(i)
    for i in range(4):
        frames[340 + i] = frame_foot_tap(i)
    for i in range(4):
        frames[350 + i] = frame_check_watch(i)
    # things that happen TO him rather than things he does
    for i in range(4):
        frames[360 + i] = frame_sneeze(i)
    for i in range(3):
        frames[370 + i] = frame_trip(i)
    for i in range(3):
        frames[380 + i] = frame_hiccup(i)
    # the CPU watcher's celebration - the last free row on the sheet
    for i in range(6):
        frames[390 + i] = frame_task_finished(i)
    # the Away Guard's "I'm not looking" - the four cells left over at the end of that row
    for i in range(4):
        frames[396 + i] = frame_cover_eyes(i)
    return frames


# --- costumes ------------------------------------------------------------------------

#: Costumes get a wider palette than the body - they live in their own PNG, so the body's
#: seven-colour rule does not apply to them.
COSTUME_PALETTE: dict[str, tuple[int, int, int, int] | None] = {
    ".": None,
    "D": (0x2C, 0x12, 0x06, 255),   # dark, matches the body outline
    "W": (0xFF, 0xFF, 0xFF, 255),   # white
    "R": (0xC0, 0x39, 0x2B, 255),   # red
    "G": (0xE9, 0xB4, 0x4C, 255),   # gold
    "B": (0x3E, 0x7C, 0xC4, 255),   # blue
    "N": (0x4E, 0x9E, 0x5F, 255),   # green
    "K": (0x9A, 0xA0, 0xA6, 255),   # grey / lens
    # Added with the shaded costumes, so new ones are not all drawn from the same seven.
    "P": (0x7E, 0x57, 0xC2, 255),   # purple
    "M": (0xE8, 0x7C, 0xA8, 255),   # pink
    "O": (0x8B, 0x5A, 0x2B, 255),   # brown - leather, wood, straw in shadow
    "A": (0x3A, 0x3A, 0x44, 255),   # charcoal - a black that still reads against his outline
    "S": (0xD8, 0xDD, 0xE3, 255),   # silver - metal, glass glint
    "F": (0xF2, 0x8C, 0x28, 255),   # flame orange
    "T": (0x2F, 0xA5, 0x9A, 255),   # teal
}

#: How strongly costumes are shaded: light from the top left, as on his body, whose highlight and
#: shadow sit about this far either side of its base colour.
COSTUME_LIGHT, COSTUME_DARK = 0.22, -0.20
#: The cast shadow a hat puts on his head: black at this alpha, drawn only over his own pixels.
COSTUME_SHADOW_ALPHA = 56

#: Wide enough for a headphone band to arc over the crown and put its cups on the *sides* of his
#: head. At 24 the cups landed on his face; at 32 they still sat up near the corners.
COSTUME_CELL = 40

#: name -> (attach kind, anchor within the sprite, pattern). ``head`` anchors sit on the top of
#: the head; ``eyes`` anchors centre on the eye band.
COSTUMES: dict[str, tuple[str, tuple[int, int], list[str]]] = {
    "santa": (
        "head",
        (7, 10),
        [
            "........WW",
            ".......WWWW",
            "......RRRWW",
            ".....RRRRW.",
            "....RRRRR..",
            "...RRRRRR..",
            "..RRRRRRR..",
            ".RRRRRRRR..",
            "WWWWWWWWWW.",
            "WWWWWWWWWW.",
        ],
    ),
    "party": (
        "head",
        (5, 11),
        [
            ".....WW.....",
            "....WWWW....",
            ".....RR.....",
            "....RRRR....",
            "....RRRR....",
            "...GGGGGG...",
            "...RRRRRR...",
            "..RRRRRRRR..",
            "..GGGGGGGG..",
            ".RRRRRRRRRR.",
            "RRRRRRRRRRRR",
            "DDDDDDDDDDDD",
        ],
    ),
    "cap": (
        "head",
        (7, 5),
        [
            "....BBBBBBBB........",
            "..BBBBBBBBBBBB......",
            ".BBBBBBBBBBBBBB.....",
            "BBBBBBBBBBBBBBBB....",
            "BBBBBBBBBBBBBBBB....",
            "DDDDDDDDDDDDDDDDDDDD",
            "..........DDDDDDDDDD",
        ],
    ),
    # The band arcs over the crown and comes down BOTH sides, so the cups sit against the sides of
    # his head at ear height rather than perching on the top corners.
    "headphones": (
        "head",
        (18, 1),
        [
            "." * 12 + "D" * 12 + "." * 12,
            "." * 9 + "DDD" + "." * 12 + "DDD" + "." * 9,
            "." * 7 + "DD" + "." * 18 + "DD" + "." * 7,
            "." * 6 + "DD" + "." * 20 + "DD" + "." * 6,
            "." * 5 + "DD" + "." * 22 + "DD" + "." * 5,
            "." * 4 + "DD" + "." * 24 + "DD" + "." * 4,
            "." * 3 + "DD" + "." * 26 + "DD" + "." * 3,
            "." + "DDDDDD" + "." * 22 + "DDDDDD" + ".",
            "." + "DKKKKD" + "." * 22 + "DKKKKD" + ".",
            "." + "DKKKKD" + "." * 22 + "DKKKKD" + ".",
            "." + "DKKKKD" + "." * 22 + "DKKKKD" + ".",
            "." + "DKKKKD" + "." * 22 + "DKKKKD" + ".",
            "." + "DDDDDD" + "." * 22 + "DDDDDD" + ".",
        ],
    ),
    "crown": (
        "head",
        (5, 5),
        [
            "G..G..G",
            "G.GGG.G",
            "GGGGGGG",
            "GGGGGGG",
            "DGGGGGD",
        ],
    ),
    # Proper lenses with a bridge and a glint, rather than a flat visor band.
    "shades": (
        "eyes",
        (10, 1),
        [
            "DDDDDDD" + "." * 6 + "DDDDDDD",
            "DKKKKKD" + "DDDDDD" + "DKKKKKD",
            "DKWKKKD" + "." * 6 + "DKWKKKD",
            "DKKKKKD" + "." * 6 + "DKKKKKD",
            "DKKKKKD" + "." * 6 + "DKKKKKD",
            "DKKKKKD" + "." * 6 + "DKKKKKD",
            ".DDDDD." + "." * 6 + ".DDDDD.",
        ],
    ),
    # Reading glasses, worn automatically while you are in a code editor. Grey wire rims rather
    # than gold - gold sits too close to the body colour to read against it. The lenses have to be
    # filled: leave them hollow and ``outline_costume`` floods the empty middle with its dark ring,
    # which turns a pair of spectacles into two black discs.
    "glasses": (
        "eyes",
        (10, 1),
        [
            "..KKK.." + "." * 6 + "..KKK..",
            ".KWWWK." + "." * 6 + ".KWWWK.",
            "KWWDWWK" + "KKKKKK" + "KWWDWWK",
            "KWWDWWK" + "." * 6 + "KWWDWWK",
            "KWWWWWK" + "." * 6 + "KWWWWWK",
            ".KWWWK." + "." * 6 + ".KWWWK.",
            "..KKK.." + "." * 6 + "..KKK..",
        ],
    ),
    # Eye-anchored, so the patch tracks his eyes wherever they look. Covers one eye and leaves the
    # other free, with the strap running up and around the back of his head.
    # The strap has to run ABOVE the eye line: routed through it, it covered his good eye too and
    # the whole thing read as a dark blob rather than a patch.
    "pirate": {
        "kind": "eyes",
        "anchor": [4, 4],
        "layer": "over",
        "category": "Eyes",
        "pattern": [
            '..AA............',
            '...AA...........',
            '....AA..........',
            '.....AAAAAAA....',
            '......AAAAAAA...',
            '......AAAAAAAAAA',
            '......AAAAAAA...',
            '......AAAAAAA...',
            '......AAAAAAA...',
            '.......AAAAA....',
            '........AAA.....',
        ],
    },
    # --- BEGIN GENERATED COSTUMES ---
    "beanie": {
        "kind": "head",
        "anchor": [11, 10],
        "layer": "over",
        "category": "Hats",
        "pattern": [
            '........WWWWWWW........',
            '.......WWWWWWWWW.......',
            '........WWWWWWW........',
            '......BBBBBBBBBBB......',
            '....BBBBBBBBBBBBBBB....',
            '...BBBBBBBBBBBBBBBBB...',
            '..BBBBBBBBBBBBBBBBBBB..',
            '.BBBBBBBBBBBBBBBBBBBBB.',
            'BBBBBBBBBBBBBBBBBBBBBBB',
            'WWWWWWWWWWWWWWWWWWWWWWW',
            'WWWWWWWWWWWWWWWWWWWWWWW',
            'WWWWWWWWWWWWWWWWWWWWWWW',
        ],
    },
    "tophat": {
        "kind": "head",
        "anchor": [10, 10],
        "layer": "over",
        "category": "Hats",
        "pattern": [
            '...AAAAAAAAAAAAAAA...',
            '...AASAAAAAAAAAAAA...',
            '....ASAAAAAAAAAAA....',
            '....ASAAAAAAAAAAA....',
            '....ASAAAAAAAAAAA....',
            '....ASAAAAAAAAAAA....',
            '....AAAAAAAAAAAAA....',
            '....RRRRRRRRRRRRR....',
            '....RRRRRRRRRRRRR....',
            'A...AAAAAAAAAAAAA...A',
            'AAAAAAAAAAAAAAAAAAAAA',
            '.AAAAAAAAAAAAAAAAAAA.',
        ],
    },
    "toque": {
        "kind": "head",
        "anchor": [10, 10],
        "layer": "over",
        "category": "Hats",
        "pattern": [
            '..WWWW..WWWWW..WWWW..',
            '.WWWWWW.WWWWW.WWWWWW.',
            'WWWWWWWWWWWWWWWWWWWWW',
            'WWWWWWWWWWWWWWWWWWWWW',
            '.WWWWWWWWWWWWWWWWWWW.',
            '..WWWWWWWWWWWWWWWWW..',
            '...WWWWWWWWWWWWWWW...',
            '...WWWWWWWWWWWWWWW...',
            '..DDDDDDDDDDDDDDDDD..',
            '...WWWWWWWWWWWWWWW...',
            '...WWWWWWWWWWWWWWW...',
            '...WWWWWWWWWWWWWWW...',
        ],
    },
    "hardhat": {
        "kind": "head",
        "anchor": [13, 8],
        "layer": "over",
        "category": "Hats",
        "pattern": [
            '.........GGDGGGDGG.........',
            '.......GGGGDGGGDGGGG.......',
            '......GGGGGDGGGDGGGGG......',
            '.....GGGGGGDGGGDGGGGGG.....',
            '....GGGGGGGDGGGDGGGGGGG....',
            '....GGGGGGGDGGGDGGGGGGG....',
            '....GGGGGGGDGGGDGGGGGGG....',
            '....DDDDDDDDDDDDDDDDDDD....',
            'GGGGGGGGGGGGGGGGGGGGGGGGGGG',
            'GGGGGGGGGGGGGGGGGGGGGGGGGGG',
        ],
    },
    "wizard": {
        "kind": "head",
        "anchor": [13, 15],
        "layer": "over",
        "category": "Hats",
        "pattern": [
            '.................PPP......',
            '...............PPPPPP.....',
            '..............PPPP.PPP....',
            '.............PPPP...PPP...',
            '.............PGPP....PP...',
            '............PGGGP....G....',
            '............PPGPPP..GGG...',
            '...........PPPPPPP...G....',
            '...........PPPPGGPP.......',
            '..........PPPPGGPPP.......',
            '..........PPPPGGPPPP......',
            '.........PPPPPPGGPPP......',
            '.........PPPPPPPPPPPP.....',
            '........GGGGGGGGGGGGGG....',
            '...PPPPPPPPPPPPPPPPPPPPPP.',
            'PPPPPPPPPPPPPPPPPPPPPPPPPP',
            '.PPPPPPPPPPPPPPPPPPPPPPPP.',
        ],
    },
    "witch": {
        "kind": "head",
        "anchor": [11, 14],
        "layer": "over",
        "category": "Hats",
        "pattern": [
            '.............PPPP.......',
            '............PPPPPPP.....',
            '...........PPPP..PPP....',
            '...........PPP.....PP...',
            '..........PPPP..........',
            '..........PPPPP.........',
            '.........PPPPPP.........',
            '.........PPPPPPP........',
            '........PPPPPPPP........',
            '.......PPPPPPPPPP.......',
            '.......PPPPPPPPPP.......',
            '......AAAAGGGAAAAA......',
            '......AAAAGAGAAAAA......',
            'P.....AAAAGGGAAAAA.....P',
            'PPPPPPPPPPPPPPPPPPPPPPPP',
            '.PPPPPPPPPPPPPPPPPPPPPP.',
        ],
    },
    "cowboy": {
        "kind": "head",
        "anchor": [15, 10],
        "layer": "over",
        "category": "Hats",
        "pattern": [
            '..........OOO..OOO............',
            '.........OOOOODDOOOOO.........',
            '........OOOOOOODOOOOOO........',
            '........OOOOOOOOOOOOOO........',
            '........OOOOOOOOOOOOOO........',
            'O.......OOOOOOOOOOOOOO.......O',
            'OO......GGGGGGGGGGGGGG......OO',
            'OO......OOOOOOOOOOOOOO......OO',
            'OOOOOOOOOOOOOOOOOOOOOOOOOOOOOO',
            '.OOOOOOOOOOOOOOOOOOOOOOOOOOOO.',
            '...OOOOOOOOOOOOOOOOOOOOOOOO...',
            '.......OOOOOOOOOOOOOOOO.......',
        ],
    },
    "tricorn": {
        "kind": "head",
        "anchor": [14, 6],
        "layer": "over",
        "category": "Hats",
        "pattern": [
            '............GGGG',
            '.GGG.......GDDDDG.......GGG',
            'GDDDG.....GDDDDDDG.....GDDDG',
            'GDDDG.....GDDDDDDG.....GDDDG',
            'GDDDG....GDDDDDDDDG....GDDDG',
            'GDDDG....GDDDDDDDDG....GDDDG',
            'GDDDDGGGGDDDDDDDDDDGGGGDDDDG',
            'GGDDDDDDDDDDDDDDDDDDDDDDDDGG',
            '..GGGGDDDDDDDDDDDDDDDDGGGG',
            '......GGGGGGGGGGGGGGGG',
        ],
    },
    "viking": {
        "kind": "head",
        "anchor": [16, 7],
        "layer": "over",
        "category": "Hats",
        "pattern": [
            'WW............................WW',
            'WWW........KKKKKKKK........WWW..',
            'WWWW......KKKKKKKKKKKK......WWWW',
            '.WWWW...KKKKKKKKKKKKKKKK...WWWW.',
            '..WWWWKKKKKKKKKKKKKKKKKKKKWWWW..',
            '.....KKKKKKKKKKKKKKKKKKKKKK.....',
            '.....KKKKKKKKKKKKKKKKKKKKKK.....',
            '.....KKKKKKKKKKKKKKKKKKKKKK.....',
            '....DDDDDDDDDDDDDDDDDDDDDDDD....',
        ],
    },
    "mortarboard": {
        "kind": "head",
        "anchor": [14, 8],
        "layer": "over",
        "category": "Hats",
        "pattern": [
            '..........AAAAAAAAA............',
            '......AAAAAAAAAAAAAAAAA........',
            '..AAAAAAAAAAAAGGAAAAAAAAAAA....',
            'AAAAAAAAAAAAAGGGGGGGGGGGGGGGG..',
            '..AAAAAAAAAAAAAAAAAAAAAAAAA.G..',
            '......AAAAAAAAAAAAAAAAA.....G..',
            '........DDAAAAAAAAADD.......G..',
            '........AADDDDDDDDDAA.......G..',
            '........AAAAAAAAAAAAA......GGG.',
            '........AAAAAAAAAAAAA......GGG.',
            '...........................GGG.',
        ],
        "frames": [
            [
                '..........AAAAAAAAA............',
                '......AAAAAAAAAAAAAAAAA........',
                '..AAAAAAAAAAAAGGAAAAAAAAAAA....',
                'AAAAAAAAAAAAAGGGGGGGGGGGGGGGG..',
                '..AAAAAAAAAAAAAAAAAAAAAAAAA.G..',
                '......AAAAAAAAAAAAAAAAA.....G..',
                '........DDAAAAAAAAADD........G.',
                '........AADDDDDDDDDAA........G.',
                '........AAAAAAAAAAAAA.......GGG',
                '........AAAAAAAAAAAAA.......GGG',
                '............................GGG',
            ],
            [
                '..........AAAAAAAAA............',
                '......AAAAAAAAAAAAAAAAA........',
                '..AAAAAAAAAAAAGGAAAAAAAAAAA....',
                'AAAAAAAAAAAAAGGGGGGGGGGGGGGGG..',
                '..AAAAAAAAAAAAAAAAAAAAAAAAA.G..',
                '......AAAAAAAAAAAAAAAAA.....G..',
                '........DDAAAAAAAAADD.......G..',
                '........AADDDDDDDDDAA.......G..',
                '........AAAAAAAAAAAAA......GGG.',
                '........AAAAAAAAAAAAA......GGG.',
                '...........................GGG.',
            ],
            [
                '..........AAAAAAAAA............',
                '......AAAAAAAAAAAAAAAAA........',
                '..AAAAAAAAAAAAGGAAAAAAAAAAA....',
                'AAAAAAAAAAAAAGGGGGGGGGGGGGGGG..',
                '..AAAAAAAAAAAAAAAAAAAAAAAAA.G..',
                '......AAAAAAAAAAAAAAAAA.....G..',
                '........DDAAAAAAAAADD......G...',
                '........AADDDDDDDDDAA......G...',
                '........AAAAAAAAAAAAA.....GGG..',
                '........AAAAAAAAAAAAA.....GGG..',
                '..........................GGG..',
            ],
        ],
    },
    "beret": {
        "kind": "head",
        "anchor": [11, 5],
        "layer": "over",
        "category": "Hats",
        "pattern": [
            '........RR............',
            '........RR............',
            '....RRRRRRRRRRR.......',
            '..RRRRRRRRRRRRRRRR....',
            '.RRRRRRRRRRRRRRRRRRR..',
            'RRRRRRRRRRRRRRRRRRRRRR',
            '.RRRRRRRRRRRRRRRRRRRRR',
            '...DDDDDDDDDDDDDDDDDDD',
        ],
    },
    "fez": {
        "kind": "head",
        "anchor": [7, 5],
        "layer": "over",
        "category": "Hats",
        "pattern": [
            '..RRRRDDDDDR....',
            '..RRRRRRRDDR....',
            '.RRRRRRRRRDDR...',
            '.RRRRRRRRRDDR...',
            'RRRRRRRRRRRDDR..',
            'RRRRRRRRRRRDDR..',
            'RRRRRRRRRRRDDR..',
            '...........DD...',
            '..........DDDD..',
            '..........DDDD..',
            '...........DD...',
        ],
    },
    "newsboy": {
        "kind": "head",
        "anchor": [10, 5],
        "layer": "over",
        "category": "Hats",
        "pattern": [
            '.....KKKKKKKKKKK.........',
            '...KKKKKKKKKKKKKKKK......',
            '.KKKKKKKKKKKKKKKKKKKK....',
            'KKKKKKKKKKKKKKKKKKKKK....',
            'KKKKKKKKKKKKKKKKKKKKK....',
            'DDDDDDDDDDDDDDDDDDDDD....',
            '............KKKKKKKKKKKKK',
            '.............KKKKKKKKKKK.',
        ],
    },
    "sombrero": {
        "kind": "head",
        "anchor": [16, 8],
        "layer": "over",
        "category": "Hats",
        "pattern": [
            '.............GGGGGGG.............',
            '............GGGGGGGGG............',
            '............GGGGGGGGG............',
            '...........GGGGGGGGGGG...........',
            '...........RRRRRRRRRRR...........',
            '..........RRRRRRRRRRRRR..........',
            '.........GGGGGGGGGGGGGGG.........',
            '.....GGGGGGGGGGGGGGGGGGGGGGG.....',
            'GGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG',
            '.RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR.',
        ],
    },
    "propeller": {
        "kind": "head",
        "anchor": [10, 10],
        "layer": "over",
        "category": "Hats",
        "pattern": [
            '..GGGGG.......GGGGG..',
            'GGGGGGGGGRRRGGGGGGGGG',
            '.....................',
            '..........K..........',
            '........BBRBB........',
            '.....BBBBRRRBBBB.....',
            '...BBBBBBRRRBBBBBB...',
            '..BBBBBBRRRRRBBBBBB..',
            '.BBBBBBBRRRRRBBBBBBB.',
            'RRRRRRRRRRRRRRRRRRRRR',
        ],
        "frames": [
            [
                '............GGGGGG...',
                '.....GGGGRRRGGGG.....',
                '...GGGGGG............',
                '..........K..........',
                '........BBRBB........',
                '.....BBBBRRRBBBB.....',
                '...BBBBBBRRRBBBBBB...',
                '..BBBBBBRRRRRBBBBBB..',
                '.BBBBBBBRRRRRBBBBBBB.',
                'RRRRRRRRRRRRRRRRRRRRR',
            ],
            [
                '.........GGG.........',
                '........GRRRG........',
                '........GGGGG........',
                '..........K..........',
                '........BBRBB........',
                '.....BBBBRRRBBBB.....',
                '...BBBBBBRRRBBBBBB...',
                '..BBBBBBRRRRRBBBBBB..',
                '.BBBBBBBRRRRRBBBBBBB.',
                'RRRRRRRRRRRRRRRRRRRRR',
            ],
            [
                '...GGGGGG............',
                '.....GGGGRRRGGGG.....',
                '............GGGGGG...',
                '..........K..........',
                '........BBRBB........',
                '.....BBBBRRRBBBB.....',
                '...BBBBBBRRRBBBBBB...',
                '..BBBBBBRRRRRBBBBBB..',
                '.BBBBBBBRRRRRBBBBBBB.',
                'RRRRRRRRRRRRRRRRRRRRR',
            ],
        ],
    },
    "cone": {
        "kind": "head",
        "anchor": [10, 12],
        "layer": "over",
        "category": "Hats",
        "pattern": [
            '.........RRR.........',
            '........RRRRR........',
            '........RRRRR........',
            '.......WWWWWWW.......',
            '.......WWWWWWW.......',
            '......RRRRRRRRR......',
            '......RRRRRRRRR......',
            '.....WWWWWWWWWWW.....',
            '.....WWWWWWWWWWW.....',
            '....RRRRRRRRRRRRR....',
            '...RRRRRRRRRRRRRRR...',
            'RRRRRRRRRRRRRRRRRRRRR',
            'RRRRRRRRRRRRRRRRRRRRR',
        ],
    },
    "halo": {
        "kind": "head",
        "anchor": [8, 9],
        "layer": "over",
        "category": "Head",
        "pattern": [
            '....GWWGGGGGG....',
            '..GG.........GG..',
            '.GG...........GG.',
            '.GGG.........GGG.',
            '..GGGGGGGGGGGGG..',
            '....GGGGGGGGG....',
            '.................',
        ],
        "frames": [
            [
                '.................',
                '....GGGGGGGGG....',
                '..GG.........GG..',
                '.GG...........GG.',
                '.GGG.........WGG.',
                '..GGGGGGGGGGWWG..',
                '....GGGGGGGGG....',
            ],
        ],
    },
    "horns": {
        "kind": "head",
        "anchor": [11, 5],
        "layer": "over",
        "category": "Head",
        "pattern": [
            'R....................R',
            'RR..................RR',
            'RR..................RR',
            'RRR................RRR',
            '.RRR..............RRR.',
            '.RRR..............RRR.',
        ],
    },
    "catears": {
        "kind": "head",
        "anchor": [11, 7],
        "layer": "over",
        "category": "Head",
        "pattern": [
            '...D..............D...',
            '..DDD............DDD..',
            '..DRD............DRD..',
            '.DDRDD..........DDRDD.',
            '.DRRRD..........DRRRD.',
            'DDRRRDD........DDRRRDD',
            'DDRRRDD........DDRRRDD',
            'DDDDDDD........DDDDDDD',
        ],
    },
    "bunnyears": {
        "kind": "head",
        "anchor": [10, 12],
        "layer": "over",
        "category": "Head",
        "pattern": [
            '...WWW.........WWW.....',
            '..WWWWW.......WWWWW....',
            '..WWMWW.......WWMWW....',
            '..WMMMW.......WMMMW....',
            '..WMMMW.......WMMMW....',
            '..WMMMW.......WMMMW....',
            '..WMMMW.......WMMMW....',
            '..WMMMW.......WMMMW....',
            '..WWMWW.......WWMWW....',
            '...WMW.........WMW.....',
            '...WMW.........WMW.....',
            '...WWW.........WWW.....',
            '...WWW.........WWW.....',
        ],
        "frames": [
            [
                '...WWW.............WWW.',
                '..WWWWW...........WWWWW',
                '..WWMWW..........WWMWW.',
                '..WMMMW..........WMMMW.',
                '..WMMMW..........WMMMW.',
                '..WMMMW.........WMMMW..',
                '..WMMMW.........WMMMW..',
                '..WMMMW.........WMMMW..',
                '..WWMWW........WWMWW...',
                '...WMW..........WMW....',
                '...WMW..........WMW....',
                '...WWW.........WWW.....',
                '...WWW.........WWW.....',
            ],
        ],
    },
    "antennae": {
        "kind": "head",
        "anchor": [11, 10],
        "layer": "over",
        "category": "Head",
        "pattern": [
            '...MMMM........MMMM...',
            '..MWWMMM......MWWMMM..',
            '..MWMMMM......MWMMMM..',
            '..MMMMMM......MMMMMM..',
            '...MMMM........MMMM...',
            '....K...........K.....',
            '.....K..........K.....',
            '......K........K......',
            '......K........K......',
            '.......K......K.......',
            '........K.....K.......',
        ],
        "frames": [
            [
                '.....MMMM........MMMM.',
                '....MWWMMM......MWWMMM',
                '....MWMMMM......MWMMMM',
                '....MMMMMM......MMMMMM',
                '.....MMMM........MMMM.',
                '......K...........K...',
                '......K..........K....',
                '.......K........K.....',
                '.......K........K.....',
                '........K......K......',
                '........K.....K.......',
            ],
        ],
    },
    "flowercrown": {
        "kind": "head",
        "anchor": [12, 3],
        "layer": "over",
        "category": "Head",
        "pattern": [
            '.MMM.......GGG.......MMM.',
            'MMGMMNNWNNGGOGGNNWNNMMGMM',
            'NMMMNNWGWNNGGGNNWGWNNMMMN',
            'NNNNDNNWNNNDNNNDNNWNNDNNN',
            'NNNDNNNNNNDNNNNNDNNNNNDNN',
        ],
    },
    "snowcap": {
        "kind": "head",
        "anchor": [14, 8],
        "layer": "over",
        "category": "Head",
        "pattern": [
            '...........WWWW.............',
            '........WWWWWWWWW...........',
            '......WWWWWWWWWWWW..WWWW....',
            '.....WWWWWWWWWWWWWWWWWWWWW..',
            '...WWWWWWWWWWWWWWWWWWWWWWWW.',
            '..WWWWWWWWWWWWWWWWWWWWWWWWWW',
            'WWWWWWWWWWWWWWWWWWWWWWWWWWWW',
            'WWWWWWWWWWWWWWWWWWWWWWWWWWWW',
            'SWWSSSSWWSSSSWWWSSSSSSWWSSWS',
            '.SWS...WS...SWWS.....SW..SW.',
            '..SS........SWS...........S.',
            '..B..........B..............',
        ],
    },
    "leaf": {
        "kind": "head",
        "anchor": [8, 11],
        "layer": "over",
        "category": "Head",
        "pattern": [
            '........R......',
            '.......RFR.....',
            '....R..FFF..R..',
            '....FF.FDF.FF..',
            '.R.FFFFDFFFF.R.',
            '.FFFDFFDFFDFFF.',
            '..FFFDFDFDFFF..',
            '...FFFDDDFFF...',
            '...FFFDFFF.....',
            '..FF..O..FF....',
            '......O........',
            '.....O.........',
        ],
    },
    "bandana": {
        "kind": "head",
        "anchor": [13, 1],
        "layer": "over",
        "category": "Head",
        "pattern": [
            '.....RRRRRRRRRRRRRRRR',
            '..RRRRRRRRRRRRRRRRRRRRRR',
            '.RRWWRRRRRRRRRRRWWRRRRRRR',
            'RRRWWRRRRRRRRRRRWWRRRRRRRRDRRR',
            'RRRRRRRRRRWWRRRRRRRRRWWRRRDRRRRR',
            'RRRRRRRRRRWWRRRRRRRRRWWRRRDRRRR',
            'RRRRRRRRRRRRRRRRRRRRRRRRRRDRRR',
            '............................RRR',
            '.............................RRR',
        ],
    },
    "earmuffs": {
        "kind": "head",
        "anchor": [18, 1],
        "layer": "over",
        "category": "Head",
        "pattern": [
            '............KKKKKKKKKKKK',
            '.........KKK............KKK',
            '.......KK..................KK',
            '......KK....................KK',
            '.....KK......................KK',
            '....KK........................KK',
            '...KK..........................KK',
            '..WWWW........................WWWW',
            '.WWWWWW......................WWWWWW',
            'WWWWWWWW....................WWWWWWWW',
            'WWWWWWWW....................WWWWWWWW',
            'WWWWWWWW....................WWWWWWWW',
            '.WWWWWW......................WWWWWW',
            '..WWWW........................WWWW',
        ],
    },
    "monocle": {
        "kind": "eyes",
        "anchor": [13, 3],
        "layer": "over",
        "category": "Eyes",
        "pattern": [
            '.....GGGGG...',
            '...GWWGGGGG..',
            '..GW.......GG',
            '..GG.......GG',
            '..GG.......GG',
            '..GG.......GG',
            '..GG.......GG',
            '..GG.......GG',
            '..GG.......GG',
            '..GG.......GG',
            '...GGGGGGGGG.',
            '..G..GGGGG...',
            '.G...........',
            '.G...........',
            '..G..........',
            '..G..........',
        ],
    },
    "glasses3d": {
        "kind": "eyes",
        "anchor": [11, 1],
        "layer": "over",
        "category": "Eyes",
        "pattern": [
            'RRRRRRRR......BBBBBBBB',
            'RRRRRRRR......BBBBBBBB',
            'RRRRRRRR......BBBBBBBB',
            'RRRDDRRRDDDDDDBBBDDBBB',
            'RRRDDRRR......BBBDDBBB',
            'RRRRRRRR......BBBBBBBB',
            'RRRRRRRR......BBBBBBBB',
        ],
    },
    "roundspecs": {
        "kind": "eyes",
        "anchor": [12, 1],
        "layer": "over",
        "category": "Eyes",
        "pattern": [
            '.....KKK........KKK.....',
            '....KWWWK......KWWWK....',
            'KKKKWWWWWKKKKKKWWWWWKKKK',
            '...KWWDWWK....KWWDWWK...',
            '...KWWDWWK....KWWDWWK...',
            '....KWWWK......KWWWK....',
            '.....KKK........KKK.....',
        ],
    },
    "skigoggles": {
        "kind": "eyes",
        "anchor": [13, 1],
        "layer": "over",
        "category": "Eyes",
        "pattern": [
            '....BBBBBBBBBBBBBBBBBB....',
            '...BBBBBBBBBBBBBBBBBBBB...',
            '...BBKKKKKKKKKKKKKKKKBB...',
            'BBBBKKKWWKKKKKKKKKKKKKBBBB',
            'BBBBKKWWKKKKKKKKKKKKKKBBBB',
            'BBBBKKKKKKKKKKKKKKKKKKBBBB',
            '...BBKKKKKKKKKKKKKKKKBB...',
            '...BBBBBBBBBBBBBBBBBBBB...',
            '....BBBBBBBBBBBBBBBBBB....',
        ],
    },
    "aviator": {
        "kind": "eyes",
        "anchor": [13, 2],
        "layer": "over",
        "category": "Eyes",
        "pattern": [
            '.....DDDDD......DDDDD.....',
            '....DDWKKDD....DDWKKDD....',
            'DDDDDWWKKKDDDDDDWWKKKDDDDD',
            'DDDDKKKKKKKDDDDKKKKKKKDDDD',
            'KKKDDKKKKKDDDDDDKKKKKDDKKK',
            'DDDDKKKKKKKDDDDKKKKKKKDDDD',
            'DDDDDKKKKKDDDDDDKKKKKDDDDD',
            '....DDKKKDD....DDKKKDD....',
            '.....DDDDD......DDDDD.....',
        ],
    },
    "heartglasses": {
        "kind": "eyes",
        "anchor": [10, 2],
        "layer": "over",
        "category": "Eyes",
        "pattern": [
            '.RR...RR....RR...RR.',
            'RRR...RRR..RRR...RRR',
            'RRRR.RRRRDDRRRR.RRRR',
            'RWWRRRRRRDDRWWRRRRRR',
            '.WRRRRRR....WRRRRRR.',
            '..RRRRR......RRRRR..',
            '...RRR........RRR...',
            '....R..........R....',
        ],
    },
    "starglasses": {
        "kind": "eyes",
        "anchor": [12, 2],
        "layer": "over",
        "category": "Eyes",
        "pattern": [
            '....GGG..........GGG....',
            '....GGG..........GGG....',
            '...GGGGG........GGGGG...',
            'GGGGGGGGGGGGGGGGGGGGGGGG',
            'GGGGGGGGGGGGGGGGGGGGGGGG',
            '.GGWWGGGGG....GGWWGGGGG.',
            '..GWWGGGG......GWWGGGG..',
            '..GGGGGGG......GGGGGGG..',
            '.GG.....GG....GG.....GG.',
            '.GG.....GG....GG.....GG.',
            'GG.......GG..GG.......GG',
        ],
    },
    "tapedglasses": {
        "kind": "eyes",
        "anchor": [10, 3],
        "layer": "over",
        "category": "Eyes",
        "pattern": [
            '........WWWW.',
            '........WWWW.',
            'KKKKKKK.WWWW.KKKKKKK',
            'KWWWWWK.WWWW.KWWWWWK',
            'KWDDDWK.WWWW.KWDDDWK',
            'KWDDDWKKWWWWKKWDDDWK',
            'KWDDDWKKWWWWKKWDDDWK',
            'KWWWWWK.WWWW.KWWWWWK',
            'KKKKKKK.WWWW.KKKKKKK',
            '........WWWW.',
            '........WWWW.',
        ],
    },
    "vrheadset": {
        "kind": "eyes",
        "anchor": [13, 5],
        "layer": "over",
        "category": "Eyes",
        "pattern": [
            '...........KKKK...........',
            '...........KKKK...........',
            '.....AAAAAAAAAAAAAAAA.....',
            '...AAAAAAAAAAAAAAAAAAAA...',
            '...AATTTTTTTTTTTTTTTTAA...',
            'KKKAATTTTTTTTTTTTTTTTAAKKK',
            'KKKAATTWWWWTTTTWWWWTTAAKKK',
            'KKKAATTWWWWTTTTWWWWTTAAKKK',
            'KKKAATTWWWWTTTTWWWWTTAAKKK',
            'KKKAATTTTTTTTTTTTTTTTAAKKK',
            '...AATTTTTTTTTTTTTTTTAA...',
            '...AAAAAAAAAAAAAAAAAAAA...',
            '.....AAAAAAAAAAAAAAAA.....',
        ],
        "frames": [
            [
                '...........KKKK...........',
                '...........KKKK...........',
                '.....AAAAAAAAAAAAAAAA.....',
                '...AAAAAAAAAAAAAAAAAAAA...',
                '...AATTTTTTTTTTTTTTTTAA...',
                'KKKAATTTTTTTTTTTTTTTTAAKKK',
                'KKKAATTWWWWTTTTWWWWTTAAKKK',
                'KKKAATTWWWWTTTTWWWWTTAAKKK',
                'KKKAATTWWWWTTTTWWWWTTAAKKK',
                'KKKAATTTTTTTTTTTTTTTTAAKKK',
                '...AATTTTTTTTTTTTTTTTAA...',
                '...AAAAAAAAAAAAAAAAAAAA...',
                '.....AAAAAAAAAAAAAAAA.....',
            ],
            [
                '...........KKKK...........',
                '...........KKKK...........',
                '.....AAAAAAAAAAAAAAAA.....',
                '...AAAAAAAAAAAAAAAAAAAA...',
                '...AATTTTTTTTTTTTTTTTAA...',
                'KKKAATTTTTTTTTTTTTTTTAAKKK',
                'KKKAATTWWWWTTTTWWWWTTAAKKK',
                'KKKAATTWWWWTTTTWWWWTTAAKKK',
                'KKKAATTWWWWTTTTWWWWTTAAKKK',
                'KKKAATTTTTTTTTTTTTTTTAAKKK',
                '...AATTTTTTTTTTTTTTTTAA...',
                '...AAAAAAAAAAAAAAAAAAAA...',
                '.....AAAAAAAAAAAAAAAA.....',
            ],
            [
                '...........KKKK...........',
                '...........KKKK...........',
                '.....AAAAAAAAAAAAAAAA.....',
                '...AAAAAAAAAAAAAAAAAAAA...',
                '...AATTTTTTTTTTTTTTTTAA...',
                'KKKAATTTTTTTTTTTTTTTTAAKKK',
                'KKKAATTTTTTTTTTTTTTTTAAKKK',
                'KKKAATTTTTTTTTTTTTTTTAAKKK',
                'KKKAATTWWWWTTTTWWWWTTAAKKK',
                'KKKAATTTTTTTTTTTTTTTTAAKKK',
                '...AATTTTTTTTTTTTTTTTAA...',
                '...AAAAAAAAAAAAAAAAAAAA...',
                '.....AAAAAAAAAAAAAAAA.....',
            ],
        ],
    },
    "scarf": {
        "kind": "neck",
        "anchor": [13, 1],
        "layer": "over",
        "category": "Neck",
        "pattern": [
            'RRRWWWRRRWWWRRRWWWRRRWWWRRRW',
            'RRRWWWRRRWWWRRRWWWRRRWWWRRRW',
            'RRRWWWRRRWWWRRRWWWRRRWWWRRRW',
            '...RRRRR....................',
            '...WWWWW....................',
            '...RRRRR....................',
            '...WWWWW....................',
            '...W.W.W....................',
        ],
        "frames": [
            [
                'RRRWWWRRRWWWRRRWWWRRRWWWRRRW',
                'RRRWWWRRRWWWRRRWWWRRRWWWRRRW',
                'RRRWWWRRRWWWRRRWWWRRRWWWRRRW',
                '...RRRRR....................',
                '...WWWWW....................',
                '....RRRRR...................',
                '....WWWWW...................',
                '.....W.W.W..................',
            ],
        ],
    },
    "bowtie": {
        "kind": "neck",
        "anchor": [6, 2],
        "layer": "over",
        "category": "Neck",
        "pattern": [
            'RR.........RR',
            'RRRR.DDD.RRRR',
            'RRRRRDDDRRRRR',
            'RRRRRDDDRRRRR',
            'RRRRRDDDRRRRR',
            'RRRR.DDD.RRRR',
            'RR.........RR',
        ],
    },
    "necktie": {
        "kind": "neck",
        "anchor": [3, 2],
        "layer": "over",
        "category": "Neck",
        "pattern": [
            '..BBB..',
            '.BBBBB.',
            '.BBBBB.',
            '.DDDDD.',
            '..BBB..',
            '.BBBBB.',
            'BBBBBBB',
            'BBBBBBB',
            'BBBBBBB',
            '.BBBBB.',
            '..BBB..',
        ],
    },
    "apron": {
        "kind": "body",
        "anchor": [13, 7],
        "layer": "over",
        "category": "Body",
        "pattern": [
            '..WW....................WW..',
            '..WW....................WW..',
            '..WW....................WW..',
            '..WW....................WW..',
            '..WW....................WW..',
            '..WWW..................WWW..',
            '....WWWWWWWWWWWWWWWWWWWW....',
            '......WWWWWWWWWWWWWWWW......',
            '......WWWWWWWWWWWWWWWW......',
            'WWWWWWWWWWWWWWWWWWWWWWWWWWWW',
            '...WWWWWWWWWWWWWWWWWWWWWW...',
            '...WWWWWWWWWWWWWWWWWWWWWW...',
            '....WWWWWWWWWWWWWWWWWWWW....',
        ],
    },
    "toolbelt": {
        "kind": "body",
        "anchor": [11, 3],
        "layer": "over",
        "category": "Body",
        "pattern": [
            '.KKKKKK........BB.S..S..',
            '.KKKKKK........BB.SSSS..',
            '...RR..........BB..SS...',
            'OOOOOOOOOOGGGGOOOOOOOOOO',
            'OOOOOOOOOOGDDGOOOOOOOOOO',
            'OOOOOOOOOOGGGGOOOOOOOOOO',
        ],
    },
    "groucho": {
        "kind": "eyes",
        "anchor": [11, 5],
        "layer": "over",
        "category": "Eyes",
        "pattern": [
            '...AAAAAAA..AAAAAAA...',
            '.AAAAAAAAA..AAAAAAAAA.',
            '.OOOOOOOOO..OOOOOOOOO.',
            'OO.......OOOO.......OO',
            'O.........OO.........O',
            'O.........OO.........O',
            'O.........OO.........O',
            'O.........OO.........O',
            'O.........OO.........O',
            'O.........OO.........O',
            'O........MMMM........O',
            'O.......MMMMMM.......O',
            'OO......MMMMMM......OO',
            '.OOOOOOOOMMMMOOOOOOOO.',
            '.....AAAAAAAAAAAA.....',
            '..AAAAAAAAAAAAAAAAAA..',
            '.AAAAAAAA....AAAAAAAA.',
            '.AAA..............AAA.',
        ],
    },
    "cape": {
        "kind": "neck",
        "anchor": [18, 14],
        "layer": "behind",
        "category": "Behind",
        "pattern": [
            '...GG............................GG...',
            '...GGRRRRRRRRRRRRRRRRRRRRRRRRRRRRGG...',
            '.....RRRRRRRRRRRRRRRRRRRRRRRRRRRR.....',
            '....RRRRRRRRRRRRRRRRRRRRRRRRRRRRR.....',
            '....RRRRRRRRRRRRRRRRRRRRRRRRRRRRR.....',
            '....RRRRRRRRRRRRRRRRRRRRRRRRRRRRR.....',
            '...RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR....',
            '...RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR....',
            '...RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR....',
            '...RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR....',
            '..RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR....',
            '..RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR....',
            '..RRDRRRRRRRRRRRRRRRRRRRRRRRRRRRRR....',
            '..RRDRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR...',
            '..RRDRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR...',
            '.RRDRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR...',
            '.RRDRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR...',
            '.RRDRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR...',
            '.RRDRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR...',
            '.RRDRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR...',
            '.RRDRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR..',
            'RRDRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR..',
            'RRDRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR..',
            'RRR.RRR.RRR.RRR.RRR.RRR.RRR.RRR.RRR...',
        ],
        "frames": [
            [
                '...GG............................GG...',
                '...GGRRRRRRRRRRRRRRRRRRRRRRRRRRRRGG...',
                '.....RRRRRRRRRRRRRRRRRRRRRRRRRRRR.....',
                '.....RRRRRRRRRRRRRRRRRRRRRRRRRRRRR....',
                '.....RRRRRRRRRRRRRRRRRRRRRRRRRRRRR....',
                '.....RRRRRRRRRRRRRRRRRRRRRRRRRRRRR....',
                '....RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR...',
                '....RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR...',
                '....RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR...',
                '....RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR...',
                '....RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR..',
                '....RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRR..',
                '....RRRRRRRRRRRRRRRRRRRRRRRRRRRRRDRR..',
                '...RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRDRR..',
                '...RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRDRR..',
                '...RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRDRR.',
                '...RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRDRR.',
                '...RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRDRR.',
                '...RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRDRR.',
                '...RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRDRR.',
                '..RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRDRR.',
                '..RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRDRR',
                '..RRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRDRR',
                '..RRR.RRR.RRR.RRR.RRR.RRR.RRR.RRR.RRR.',
            ],
        ],
    },
    "wings": {
        "kind": "neck",
        "anchor": [18, 23],
        "layer": "behind",
        "category": "Behind",
        "pattern": [
            '..WW..............................WW..',
            '.WWWWW..........................WWWWW.',
            'WWWWWWWW......................WWWWWWWW',
            'WWWWWWWWWW..................WWWWWWWWWW',
            'WWWWWWWWWWWW..............WWWWWWWWWWWW',
            'SKWWWWWWWWWWW............WWWWWWWWWWWKS',
            'SWKWWWWWWWWWW............WWWWWWWWWWKWS',
            'WSWKWWWWWWWWW............WWWWWWWWWKWSW',
            'WWSWKWWWWWWWW............WWWWWWWWKWSWW',
            'WWWSWWWWWWWWW............WWWWWWWWWSWWW',
            '.WWWSWWWWWWWW............WWWWWWWWSWWW.',
            '.WW.WWWWWWWWW............WWWWWWWWW.WW.',
            '..W.WW.WWWWWW............WWWWWW.WW.W..',
            '.......WWWWWW............WWWWWW.......',
            '......................................',
            '......................................',
            '......................................',
            '......................................',
            '......................................',
            '......................................',
        ],
        "frames": [
            [
                '......................................',
                '......................................',
                '......................................',
                '.......WW....................WW.......',
                '.....WWWWWW................WWWWWW.....',
                '...WWWWWWWWWW............WWWWWWWWWW...',
                '.WWWWWWWWWWWW............WWWWWWWWWWWW.',
                'WWWWWWWWWWWWW............WWWWWWWWWWWWW',
                'WKWWKWWWWWWWW............WWWWWWWWKWWKW',
                'WKWWKWWWW....................WWWWKWWKW',
                'WKWWKWWW......................WWWKWWKW',
                'WKWWKWW........................WWKWWKW',
                'WKWWKWW........................WWKWWKW',
                'WKWWKW..........................WKWWKW',
                'WKWWKW..........................WKWWKW',
                'WKW.KW..........................WK.WKW',
                'WKW.W............................W.WKW',
                'W.W.W............................W.W.W',
                'W...W............................W...W',
                'W....................................W',
            ],
        ],
    },
    "backpack": {
        "kind": "body",
        "anchor": [21, 25],
        "layer": "behind",
        "category": "Behind",
        "pattern": [
            '.....AAAA....',
            '....TTTTTTTTT',
            '...TTTTTTTTTT',
            '..TTTTTTTTTTT',
            '..TTTTTTTTTTT',
            '..TTTTTTTTTTT',
            '..TTTTTTTTTTT',
            '..TTTTTTTTTTT',
            '..TTTTTTTTTTT',
            '..TTTTTTTTTTT',
            '..TTTTTTTTTTT',
            '..TTTTTTTTTTT',
            '..DDDDTTTTTTT',
            '.TTTTDTTTTTTT',
            'TTTTTDTTTTTTT',
            'TGGTTDTTTTTTT',
            'TTTTTDTTTTTTT',
            'TTTTTDTTTTTTT',
            'TTTTTDTTTTTTT',
            '.TTTTDTTTTTTT',
            '..TTTTTTTTTTT',
            '..TTTTTTTTTTT',
            '...TTTTTTTTTT',
        ],
    },
    "jetpack": {
        "kind": "body",
        "anchor": [18, 19],
        "layer": "behind",
        "category": "Behind",
        "pattern": [
            '..SSSS..........................SSSS..',
            '.SSSSSSS......................SSSSSSS.',
            'SSSSSSSS......................SSSSSSSS',
            'SSSSSSSS......................SSSSSSSS',
            'RRRRRRRR......................RRRRRRRR',
            'SSSSSSSS......................SSSSSSSS',
            'SSSSSSSS......................SSSSSSSS',
            'SSSSSSSS......................SSSSSSSS',
            'SSSSSSSS......................SSSSSSSS',
            'SSSSSSSS......................SSSSSSSS',
            'SSSSSSSS......................SSSSSSSS',
            'RRRRRRRR......................RRRRRRRR',
            'SSSSSSSS......................SSSSSSSS',
            '.SSSSSSS......................SSSSSSS.',
            '..AAAA..........................AAAA..',
            '.AAAAAA........................AAAAAA.',
            '.FFFFF..........................FFFFF.',
            '.FGGGF..........................FGGGF.',
            '.FGWGF..........................FGWGF.',
            '..FGF............................FGF..',
            '..FGF............................FGF..',
            '..FF..............................FF..',
            '...R..............................R...',
        ],
        "frames": [
            [
                '..SSSS..........................SSSS..',
                '.SSSSSSS......................SSSSSSS.',
                'SSSSSSSS......................SSSSSSSS',
                'SSSSSSSS......................SSSSSSSS',
                'RRRRRRRR......................RRRRRRRR',
                'SSSSSSSS......................SSSSSSSS',
                'SSSSSSSS......................SSSSSSSS',
                'SSSSSSSS......................SSSSSSSS',
                'SSSSSSSS......................SSSSSSSS',
                'SSSSSSSS......................SSSSSSSS',
                'SSSSSSSS......................SSSSSSSS',
                'RRRRRRRR......................RRRRRRRR',
                'SSSSSSSS......................SSSSSSSS',
                '.SSSSSSS......................SSSSSSS.',
                '..AAAA..........................AAAA..',
                '.AAAAAA........................AAAAAA.',
                'FFFFFFF........................FFFFFFF',
                'FGGGGGF........................FGGGGGF',
                '.FGWGF..........................FGWGF.',
                '.FGGF............................FGGF.',
                '..RR..............................RR..',
                '......................................',
                '......................................',
            ],
            [
                '..SSSS..........................SSSS..',
                '.SSSSSSS......................SSSSSSS.',
                'SSSSSSSS......................SSSSSSSS',
                'SSSSSSSS......................SSSSSSSS',
                'RRRRRRRR......................RRRRRRRR',
                'SSSSSSSS......................SSSSSSSS',
                'SSSSSSSS......................SSSSSSSS',
                'SSSSSSSS......................SSSSSSSS',
                'SSSSSSSS......................SSSSSSSS',
                'SSSSSSSS......................SSSSSSSS',
                'SSSSSSSS......................SSSSSSSS',
                'RRRRRRRR......................RRRRRRRR',
                'SSSSSSSS......................SSSSSSSS',
                '.SSSSSSS......................SSSSSSS.',
                '..AAAA..........................AAAA..',
                '.AAAAAA........................AAAAAA.',
                '.FFFFF..........................FFFFF.',
                '.FGGGF..........................FGGGF.',
                '..FGF............................FGF..',
                '..FGF............................FGF..',
                '...FF............................FF...',
                '...F..............................F...',
                '...R..............................R...',
            ],
        ],
    },
    # --- END GENERATED COSTUMES ---
}


def outline_costume(pattern: list[str]) -> list[str]:
    """Wrap a costume in the same 1px dark border the body gets.

    Without this a white santa brim or a grey ear cup disappears against a light taskbar, and the
    hat stops looking attached to his head.
    """
    height = len(pattern) + 2
    width = max(len(r) for r in pattern) + 2
    grid = [["." for _ in range(width)] for _ in range(height)]
    for y, row in enumerate(pattern):
        for x, ch in enumerate(row):
            if ch != ".":
                grid[y + 1][x + 1] = ch
    solid = [(x, y) for y in range(height) for x in range(width) if grid[y][x] != "."]
    for x, y in solid:
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < width and 0 <= ny < height and grid[ny][nx] == ".":
                grid[ny][nx] = "D"
    return ["".join(row) for row in grid]


#: Categories for the costumes that predate the category field, so the eight originals group with
#: everything else in the tray rather than all landing under "Other".
_LEGACY_CATEGORIES = {
    "santa": "Hats", "party": "Hats", "cap": "Hats",
    "headphones": "Head", "crown": "Head",
    "shades": "Eyes", "glasses": "Eyes", "pirate": "Eyes",
}


def costume_spec(entry, name: str = "") -> tuple[str, tuple[int, int], list[str], str, str]:
    """Normalise a ``COSTUMES`` value to (kind, anchor, pattern, layer, category).

    Both shapes are accepted: the original three-tuple, and the dict the newer costumes use. That
    is deliberate rather than lazy - rewriting eight working patterns to acquire two fields they
    do not set would be eight chances to introduce a typo in art that already renders correctly.
    """
    if isinstance(entry, dict):
        return (
            entry["kind"], tuple(entry["anchor"]), entry["pattern"],
            entry.get("layer", "over"),
            entry.get("category") or _LEGACY_CATEGORIES.get(name, "Other"),
        )
    kind, anchor, pattern = entry[0], entry[1], entry[2]
    layer = entry[3] if len(entry) > 3 else "over"
    category = entry[4] if len(entry) > 4 else _LEGACY_CATEGORIES.get(name, "Other")
    return kind, tuple(anchor), pattern, layer, category


def costume_frames(entry) -> list[list[str]]:
    """Every frame of a costume: its pattern, then the ``frames`` a moving one adds (a propeller
    mid-turn, a flame mid-flicker). All share the one anchor, so they swap in place."""
    pattern = costume_spec(entry)[2]
    extra = entry.get("frames", []) if isinstance(entry, dict) else []
    return [pattern, *extra]


def costume_casts_shadow(entry, name: str = "") -> bool:
    """A hat worn on top of his head darkens the row of him just under it. Not glasses (on his
    face, nothing to cast onto), nothing behind him, nothing that hangs."""
    kind, anchor, pattern, layer, _category = costume_spec(entry, name)
    if isinstance(entry, dict) and "shadow" in entry:
        return bool(entry["shadow"])
    # One that floats - a halo - ends above the row it is anchored to: there is air under it,
    # and a shadow would land on nothing.
    lowest = max((y for y, row in enumerate(pattern) if row.strip(".")), default=-1)
    return kind == "head" and layer == "over" and lowest >= anchor[1] - 1


def _tone(colour: tuple[int, int, int, int], amount: float) -> tuple[int, int, int, int]:
    """``colour`` lighter (``amount`` > 0) or darker (< 0), in lightness, keeping its hue - and,
    like his own shading, a shade a touch more saturated than the base, a highlight a touch less."""
    import colorsys

    r, g, b, a = colour
    h, lightness, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    if amount > 0:
        lightness += (1 - lightness) * amount
        s = max(0.0, s - 0.05)
    else:
        lightness *= 1 + amount
        s = min(1.0, s + 0.08)
    r, g, b = colorsys.hls_to_rgb(h, lightness, s)
    return round(r * 255), round(g * 255), round(b * 255), a


def costume_pixels(pattern: list[str], *, shade: bool = True) -> list[list]:
    """The outlined pattern as colours (``None`` = transparent), shaded the way his body is.

    Light comes from the top left. A fill pixel with nothing of the costume above or to its left
    takes the highlight; one with nothing below or to its right takes the shade; a pixel that is
    both (a one-pixel line) keeps its base colour, or thin details turn to noise. White is only
    ever shaded, never brightened. A line of outline drawn *between* two fills - the band on a top
    hat, the seam on a hard hat - becomes a dark version of the fill beside it instead of the
    near-black outer outline, so the inside of a costume reads as one object and only its
    silhouette carries the hard edge that keeps it visible on a light taskbar.
    """
    grid = outline_costume(pattern)
    height, width = len(grid), max(len(r) for r in grid)
    rows = [row.ljust(width, ".") for row in grid]

    def is_fill(x: int, y: int) -> bool:
        return 0 <= y < height and 0 <= x < width and rows[y][x] not in ".D"

    def is_clear(x: int, y: int) -> bool:
        return not (0 <= y < height and 0 <= x < width) or rows[y][x] == "."

    out: list[list] = [[None] * width for _ in range(height)]
    for y in range(height):
        for x in range(width):
            ch = rows[y][x]
            base = COSTUME_PALETTE[ch]
            if base is None:
                continue
            if not shade:
                out[y][x] = base
                continue
            if ch == "D":
                fills = [rows[ny][nx] for nx, ny in ((x, y - 1), (x - 1, y), (x + 1, y), (x, y + 1))
                         if is_fill(nx, ny)]
                outer = any(is_clear(nx, ny)
                            for nx, ny in ((x, y - 1), (x - 1, y), (x + 1, y), (x, y + 1)))
                inner = fills and not outer and fills[0] != "W"
                out[y][x] = _tone(COSTUME_PALETTE[fills[0]], -0.55) if inner else base
                continue
            lit = not is_fill(x, y - 1) or not is_fill(x - 1, y)
            dim = not is_fill(x, y + 1) or not is_fill(x + 1, y)
            thin = (not is_fill(x, y - 1) and not is_fill(x, y + 1)) or \
                   (not is_fill(x - 1, y) and not is_fill(x + 1, y))
            if thin or (lit and dim):
                out[y][x] = base
            elif ch == "W":
                out[y][x] = _tone(base, -0.12) if dim else base
            elif lit:
                out[y][x] = _tone(base, COSTUME_LIGHT)
            elif dim:
                out[y][x] = _tone(base, COSTUME_DARK)
            else:
                out[y][x] = base
    return out


def costume_shadow(pattern: list[str]) -> list[list[bool]]:
    """Where a hat's shadow falls: the pixel just under the lowest part of each of its columns,
    in the same (outlined) coordinates. The runtime draws it only over his own pixels."""
    grid = outline_costume(pattern)
    height, width = len(grid), max(len(r) for r in grid)
    rows = [row.ljust(width, ".") for row in grid]
    cast = [[False] * width for _ in range(height + 1)]
    for x in range(width):
        lowest = max((y for y in range(height) if rows[y][x] != "."), default=None)
        if lowest is not None and lowest + 1 <= height:
            cast[lowest + 1][x] = True
    return cast


def render_costumes(out_dir: Path) -> dict:
    """Pack every costume into the sheet, one cell each, and write the manifest.

    Row 0 is the costumes themselves: every costume's first frame, in ``COSTUMES`` order, so a
    costume's ``index`` is still its column; the extra frames of the moving ones follow after
    them, listed in its ``frames``. Row 1 is the shadow each one casts on him, in the same
    column. The runtime crops by ``column * cell``.
    """
    cell = COSTUME_CELL
    cells: list[tuple[str, list[str], bool]] = []   # (name, pattern, casts a shadow)
    items = {}
    extra: list[tuple[str, list[str], bool]] = []
    for name, entry in COSTUMES.items():
        frames = costume_frames(entry)
        shadow = costume_casts_shadow(entry, name)
        cells.append((name, frames[0], shadow))
        extra.extend((name, f, shadow) for f in frames[1:])
    columns = cells + extra
    sheet = Image.new("RGBA", (cell * len(columns), cell * 2), (0, 0, 0, 0))
    pixels = sheet.load()
    over_size = []
    frames_of: dict[str, list[int]] = {}
    for column, (name, pattern, shadow) in enumerate(columns):
        frames_of.setdefault(name, []).append(column)
        entry = COSTUMES[name]
        colours = costume_pixels(pattern, shade=not (isinstance(entry, dict)
                                                      and entry.get("shade") is False))
        width, height = len(colours[0]), len(colours)
        if width > cell or height > cell:
            over_size.append(f"{name} ({width}x{height})")
        ox = column * cell
        for y, row in enumerate(colours):
            for x, colour in enumerate(row):
                if colour is not None and x < cell and y < cell:
                    pixels[ox + x, y] = colour
        if shadow:
            for y, row in enumerate(costume_shadow(pattern)):
                for x, cast in enumerate(row):
                    if cast and x < cell and y < cell:
                        pixels[ox + x, cell + y] = (0, 0, 0, COSTUME_SHADOW_ALPHA)
    for name, entry in COSTUMES.items():
        kind, anchor, _pattern, layer, category = costume_spec(entry, name)
        frames = frames_of[name]
        # the outline added a ring, so the attach point moves with it
        item = {
            "index": frames[0], "kind": kind, "anchor": [anchor[0] + 1, anchor[1] + 1],
            "layer": layer, "category": category,
        }
        if len(frames) > 1:
            item["frames"] = frames
        if costume_casts_shadow(entry, name):
            item["shadow"] = True
        items[name] = item
    if over_size:
        # Loud, because the clip above is silent: an oversized pattern simply loses its right or
        # bottom edge and looks like a drawing mistake rather than a size limit.
        raise ValueError(
            f"costume(s) larger than the {cell}x{cell} cell once outlined: {', '.join(over_size)}"
        )
    sheet.save(out_dir / "costumes.png")
    manifest = {"cell": cell, "sheet": "costumes.png", "items": items}
    (out_dir / "costumes.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


MANIFEST_STATES = {
    "idle": {"loop": True, "frames": [[0, 450], [1, 300], [2, 450], [1, 300]]},
    "blink": {"loop": False, "frames": [[3, 120]]},
    # Two blinks in quick succession. No new art at all - frame 3 is the closed eye and frame 0 is
    # the plain idle, so this is entirely a matter of timing.
    "blink_double": {"loop": False, "frames": [[3, 110], [0, 90], [3, 120]]},
    # Lids at half mast on the way down and back up, held long. This is what tired looks like.
    "blink_slow": {"loop": False, "frames": [[310, 190], [311, 430], [312, 220]]},
    "walk_right": {
        "loop": True,
        "frames": [[10, 110], [11, 110], [12, 110], [13, 110], [14, 110], [15, 110]],
    },
    "wave": {"loop": True, "frames": [[20, 140], [21, 140], [22, 140], [23, 140]]},
    "working": {"loop": True, "frames": [[30, 200], [31, 200], [32, 200]]},
    "angry": {"loop": True, "frames": [[40, 90], [41, 90], [42, 90], [43, 90]]},
    "sleep": {"loop": True, "frames": [[50, 700], [51, 700]]},
    "wake": {"loop": False, "frames": [[60, 180], [61, 180]]},
    # The app holds a specific frame of this one, chosen from the cursor direction.
    "eyes_track": {"loop": False, "frames": [[70 + i, 120] for i in range(9)]},
    "look_around": {"loop": True, "frames": [[80, 420], [81, 520], [82, 520], [83, 420]]},
    "wink": {"loop": True, "frames": [[90, 260], [91, 420], [92, 300]]},
    "sparkle": {"loop": True, "frames": [[100, 170], [101, 170], [102, 170], [103, 170]]},
    "heart_eyes": {"loop": True, "frames": [[110, 240], [111, 240], [112, 240], [113, 240]]},
    "football": {
        "loop": True,
        "frames": [[120, 130], [121, 120], [122, 130], [123, 120], [124, 140], [125, 120]],
    },
    "dance": {
        "loop": True,
        "frames": [[130, 130], [131, 130], [132, 130], [133, 130], [134, 130], [135, 130]],
    },
    "pushups": {"loop": True, "frames": [[140, 200], [141, 160], [142, 220], [143, 160], [144, 200]]},
    "hop": {"loop": True, "frames": [[150, 150], [151, 110], [152, 150], [153, 110], [154, 180]]},
    "sit_dangle": {"loop": True, "frames": [[160, 380], [161, 380], [162, 380], [163, 380]]},
    "injured": {"loop": True, "frames": [[170, 260], [171, 260], [172, 260], [173, 260]]},
    # Fast, because he is only in the air for a fraction of a second.
    "tumble": {"loop": True, "frames": [[180, 70], [181, 70], [182, 70], [183, 70]]},
    # --- world-aware. Slower than the play antics on purpose: these are moods, not stunts.
    "yawn": {"loop": True, "frames": [[190, 420], [191, 480], [192, 620], [193, 380]]},
    "stretch": {"loop": True, "frames": [[200, 260], [201, 340], [202, 620], [203, 300]]},
    "coffee": {
        "loop": True,
        "frames": [[210, 420], [211, 320], [212, 560], [213, 560], [214, 460]],
    },
    "hot": {"loop": True, "frames": [[220, 210], [221, 190], [222, 230], [223, 190], [224, 230]]},
    # A fixed celebration, not a mood - it plays through once and stops, like the involuntary
    # reactions below, rather than looping like the rest of this block. Two sparkle layouts
    # (392/393) rather than one long-held frame, so the celebration beat twinkles instead of
    # freezing, and a long satisfied hold on the smug settle (394) at the end.
    "task_finished": {
        "loop": False,
        "frames": [[390, 200], [391, 160], [392, 380], [393, 380], [394, 220], [395, 900]],
    },
    "shiver": {"loop": True, "frames": [[230, 150], [231, 150], [232, 150], [233, 150]]},
    "autumn": {"loop": True, "frames": [[240, 360], [241, 360], [242, 360], [243, 400]]},
    # --- app personas
    "typing": {
        "loop": True,
        "frames": [[250, 110], [251, 110], [252, 110], [253, 110], [254, 130]],
    },
    "counting": {"loop": True, "frames": [[260, 340], [261, 340], [262, 340], [263, 420]]},
    "spreadsheet": {"loop": True, "frames": [[270, 380], [271, 420], [272, 420], [273, 380]]},
    "mail": {"loop": True, "frames": [[280, 320], [281, 300], [282, 420], [283, 300]]},
    # Heaving at the underside of a window. Slow on the big push, quick on the recoil.
    "strain": {
        "loop": True,
        "frames": [[290, 260], [291, 220], [292, 520], [293, 180], [294, 300]],
    },
    # A jolt, then he settles. Fast, because a startle that lingers is not a startle.
    "startle": {"loop": False, "frames": [[300, 90], [301, 130], [302, 160], [303, 220]]},
    # --- being petted. The slowest loop in the set: every other frame rate here is tuned to read
    # as energy, and this one is tuned to read as the absence of it.
    "pet": {"loop": True, "frames": [[320, 300], [321, 420], [322, 520], [323, 420]]},
    # --- micro-fidgets. Short and unshowy on purpose - a fidget you notice is not a fidget.
    "scratch": {"loop": True, "frames": [[330, 190], [331, 130], [332, 130], [333, 150]]},
    "foot_tap": {"loop": True, "frames": [[340, 220], [341, 110], [342, 130], [343, 160]]},
    "check_watch": {
        "loop": True,
        "frames": [[350, 240], [351, 300], [352, 460], [353, 260]],
    },
    # --- involuntary. All three play once and stop; none of them loops, because a sneeze that
    # repeats is not a sneeze.
    "sneeze": {"loop": False, "frames": [[360, 260], [361, 220], [362, 130], [363, 300]]},
    "trip": {"loop": False, "frames": [[370, 90], [371, 140], [372, 220]]},
    # The one that does loop, briefly - hiccups come in threes.
    "hiccup": {"loop": True, "frames": [[380, 260], [381, 110], [382, 180]]},
    # --- the Away Guard. Held for as long as someone is typing the PIN, so it loops, and slowly:
    # a nervous little bob, not a dance. Appended last so no existing state's indices move.
    "cover_eyes": {"loop": True, "frames": [[396, 320], [397, 260], [398, 320], [399, 240]]},
}


def render(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    sheet = Image.new("RGBA", (FRAME * COLS, FRAME * ROWS), (0, 0, 0, 0))
    pixels = sheet.load()

    built = build_frames()
    anchors: dict[str, Anchors] = {}
    for index, (grid, frame_anchors) in built.items():
        row, col = divmod(index, COLS)
        ox, oy = col * FRAME, row * FRAME
        for y in range(FRAME):
            for x in range(FRAME):
                colour = PALETTE[grid[y][x]]
                if colour is not None:
                    pixels[ox + x, oy + y] = colour
        anchors[str(index)] = frame_anchors

    manifest = {
        "sheet": "nib_sheet.png",
        "frame_w": FRAME,
        "frame_h": FRAME,
        "cols": COLS,
        "rows": ROWS,
        "baseline_offset": BASELINE_OFFSET,
        "walk_step_px": WALK_STEP_PX,
        "states": MANIFEST_STATES,
        "anchors": anchors,
    }

    sheet.save(out_dir / "nib_sheet.png")
    (out_dir / "frames.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    costumes = render_costumes(out_dir)
    write_preview(sheet, out_dir / "preview.png")
    write_banner(sheet, anchors, costumes, out_dir)
    print(f"wrote {out_dir / 'nib_sheet.png'} ({sheet.width}x{sheet.height})")
    print(f"wrote {out_dir / 'frames.json'} "
          f"({len(MANIFEST_STATES)} states, {len(anchors)} anchored frames)")
    print(f"wrote {out_dir / 'costumes.png'} ({len(costumes['items'])} costumes)")
    print(f"wrote {out_dir / 'preview.png'}")


def write_banner(
    sheet: Image.Image,
    anchors: dict[str, Anchors],
    costumes: dict,
    out_dir: Path,
    zoom: int = 4,
) -> None:
    """A showcase for the README: him bare, then wearing each costume.

    Transparent background so it sits well on a light or a dark page. The compositing here mirrors
    what the app does at runtime - crop the frame, then stamp the costume at that frame's anchor,
    including the ``behind`` layer, which has to be painted before he is.

    Wraps into a grid rather than one strip. A single row was fine at eight costumes and is
    ridiculous at fifty: it would come out over ten thousand pixels wide and no README would ever
    show it at a readable size.
    """
    costume_sheet = Image.open(out_dir / "costumes.png").convert("RGBA")
    cell = int(costumes["cell"])
    idle = 0
    frame = sheet.crop((0, 0, FRAME, FRAME))

    tiles = [frame]
    for name, item in costumes["items"].items():
        piece = costume_sheet.crop(
            (int(item["index"]) * cell, 0, int(item["index"]) * cell + cell, cell)
        )
        target = anchors[str(idle)].get(item["kind"])
        if not target:
            continue
        ax, ay = item["anchor"]
        dest = (target[0] - ax, target[1] - ay)
        if item.get("layer") == "behind":
            dressed = Image.new("RGBA", frame.size, (0, 0, 0, 0))
            dressed.alpha_composite(piece, dest=(max(0, dest[0]), max(0, dest[1])))
            dressed.alpha_composite(frame)
        else:
            dressed = frame.copy()
            if dest[0] < 0 or dest[1] < 0:
                # alpha_composite refuses a negative destination; paste does not, and a hat
                # hanging off the top of the cell is a real thing rather than an error.
                dressed.paste(piece, dest, piece)
            else:
                dressed.alpha_composite(piece, dest=dest)
        tiles.append(dressed)

    return _write_tile_grid(tiles, out_dir / "banner.png", zoom)


def _write_tile_grid(tiles: list[Image.Image], path: Path, zoom: int, columns: int = 10) -> None:
    """Lay tiles out in a grid, transparent background, and save."""
    pad = 6
    tile_px = FRAME * zoom
    rows = (len(tiles) + columns - 1) // columns
    canvas = Image.new(
        "RGBA",
        (min(len(tiles), columns) * (tile_px + pad) + pad, rows * (tile_px + pad) + pad),
        (0, 0, 0, 0),
    )
    for i, tile in enumerate(tiles):
        row, col = divmod(i, columns)
        canvas.paste(
            tile.resize((tile_px, tile_px), Image.NEAREST),
            (pad + col * (tile_px + pad), pad + row * (tile_px + pad)),
        )
    canvas.save(path)


def write_preview(sheet: Image.Image, path: Path, zoom: int = 4) -> None:
    """Contact sheet on a checkerboard, with a marker on each cell's baseline row."""
    cell = FRAME * zoom
    pad = 4
    width = COLS * (cell + pad) + pad
    height = ROWS * (cell + pad) + pad
    canvas = Image.new("RGBA", (width, height), (54, 54, 58, 255))
    check = Image.new("RGBA", (cell, cell), (0, 0, 0, 0))
    cpx = check.load()
    for y in range(cell):
        for x in range(cell):
            light = ((x // (zoom * 2)) + (y // (zoom * 2))) % 2 == 0
            cpx[x, y] = (92, 92, 98, 255) if light else (74, 74, 80, 255)

    for index in range(COLS * ROWS):
        row, col = divmod(index, COLS)
        frame = sheet.crop(
            (col * FRAME, row * FRAME, col * FRAME + FRAME, row * FRAME + FRAME)
        ).resize((cell, cell), Image.NEAREST)
        tile = check.copy()
        tile.alpha_composite(frame)
        tpx = tile.load()
        for x in range(0, cell, 2):
            for t in range(zoom):
                tpx[x, BASELINE_ROW * zoom + t] = (0, 220, 120, 255)
        canvas.paste(tile, (pad + col * (cell + pad), pad + row * (cell + pad)))

    canvas.convert("RGB").save(path)


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent.parent / "assets"
    render(target)

