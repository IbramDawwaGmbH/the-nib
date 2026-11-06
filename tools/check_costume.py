"""Check costume-of-the-day specs against every rule the sheet and the app hold costumes to.

Usage:
    python tools/check_costume.py <spec.json | dir> [more ...] [--preview out.png] [--quiet]

A spec is one day's file from ``tools/daily_costumes/``::

    {"date": "03-14", "name": "pi_day", "title": "Pi Day", "theme": "...",
     "category": "Hats", "kind": "head", "anchor": [x, y], "layer": "over",
     "pattern": [...], "frames": [[...], ...]}

Exits 0 when every spec passes. Every problem is printed, so one run says everything to fix.

Nothing here needs Qt, and nothing is written to ``assets/``: the frames are cut from the real
sheet with Pillow and dressed exactly the way ``animator.SpriteSheet._apply_costume`` dresses them
- outlined, shaded, stamped anchor-to-anchor and lifted off the floor - so a costume that passes
here is placed the way the app will place it. ``tests/smoke_daily_costumes.py`` then checks the
same rules again through the real runtime, once the sheets are built.

The rules, and the failure each one exists for:

* **Fits the cell.** 38x38 before the outline, 40x40 after. Over that it is silently clipped.
* **No enclosed holes.** The outline floods a transparent pocket with dark, so hollow spectacle
  lenses render as two black discs. Fill them.
* **It moves.** 2 to 4 frames, the same size, each one different. It steps with his own frames,
  so a costume only animates while he does.
* **Off his face.** Only an ``eyes`` costume may cover the eyes; a hat that slides over them or a
  scarf that rides up into them hides his whole expression.
* **On him, not past him.** A neck costume ends within 4 rows of his body; nothing reaches the
  row his soles stand on while he is upright.
* **In the frame.** Cut off at the top on an ordinary standing frame is a failure; a tall hat
  clipped only at the peak of a hop is reported, not failed.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
from gen_sprites import COSTUME_CELL, COSTUME_PALETTE, COSTUMES  # noqa: E402
from preview_costume import frame_image, load_frames, render_piece, render_shadow  # noqa: E402

VALID_KINDS = {"head", "eyes", "neck", "body"}
VALID_LAYERS = {"over", "behind"}
VALID_CATEGORIES = {"Hats", "Head", "Eyes", "Neck", "Body", "Behind"}
MAX_PATTERN = COSTUME_CELL - 2
MIN_FRAMES, MAX_FRAMES = 2, 4

BASELINE_ROW = 46
EYE_TOP, EYE_BOTTOM = 26, 31
#: His eyes span x 16..31; one column of grace either side.
FACE_LEFT, FACE_RIGHT = 15, 32
BODY_BOTTOM = 41
HANG_CLEARANCE = 4
#: States where he is not on his feet: upside down, spinning, or in the air.
UPENDED = {"injured", "tumble"}
AIRBORNE = {("hop", 1), ("hop", 2), ("hop", 3)}
#: Most rows of a hat that may be cut off at the top of an upright frame he is in for a moment
#: (a stretch, a jump). Idle may lose none.
MAX_TRANSIENT_CLIP = 3

EXISTING = set(COSTUMES)


# --- loading -------------------------------------------------------------------------------

def load_specs(paths: list[Path]) -> list[tuple[Path, dict]]:
    out = []
    for path in paths:
        files = sorted(path.glob("[0-1][0-9]-[0-3][0-9].json")) if path.is_dir() else [path]
        for file in files:
            try:
                out.append((file, json.loads(file.read_text(encoding="utf-8"))))
            except (OSError, ValueError) as exc:
                out.append((file, {"_error": f"not valid JSON: {exc}"}))
    return out


# --- the static rules ------------------------------------------------------------------------

def _holes(pattern: list[str]) -> list[tuple[int, int]]:
    """Enclosed transparent pixels the outline would flood dark.

    The outline rings every solid pixel, inside as well as out, so a pocket the outside cannot
    reach gets a dark lining. A wide one - the middle of a halo, a monocle's lens - keeps a clear
    centre and reads as a ring, which is fine. A narrow one is lining all the way through: those
    are the black discs. So a pocket fails when fewer than four of its pixels would stay clear."""
    width = max(len(r) for r in pattern) + 2
    height = len(pattern) + 2
    grid = [["."] * width for _ in range(height)]
    for y, row in enumerate(pattern):
        for x, ch in enumerate(row):
            grid[y + 1][x + 1] = ch
    seen = {(0, 0)}
    stack = [(0, 0)]
    while stack:
        x, y = stack.pop()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < width and 0 <= ny < height and (nx, ny) not in seen \
                    and grid[ny][nx] == ".":
                seen.add((nx, ny))
                stack.append((nx, ny))
    enclosed = {(x, y) for y in range(height) for x in range(width)
                if grid[y][x] == "." and (x, y) not in seen}
    bad: list[tuple[int, int]] = []
    while enclosed:
        start = enclosed.pop()
        pocket, stack = [start], [start]
        while stack:
            x, y = stack.pop()
            for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if n in enclosed:
                    enclosed.discard(n)
                    pocket.append(n)
                    stack.append(n)
        lined = [(x, y) for x, y in pocket
                 if any(grid[ny][nx] != "." for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))]
        if len(pocket) - len(lined) < 4:
            bad += [(x - 1, y - 1) for x, y in sorted(pocket, key=lambda q: (q[1], q[0]))]
    return bad


def static_problems(spec: dict) -> list[str]:
    problems = []
    if "_error" in spec:
        return [spec["_error"]]
    name = spec.get("name", "")
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,30}", str(name)):
        problems.append(f"name {name!r}: lowercase letters, digits and underscores, 2-31 long")
    if name in EXISTING:
        problems.append(f"name {name!r} is already one of the wardrobe's costumes")
    if not re.fullmatch(r"(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])", str(spec.get("date", ""))):
        problems.append(f"date {spec.get('date')!r} is not MM-DD")
    if not str(spec.get("title", "")).strip():
        problems.append("no title")
    if spec.get("kind") not in VALID_KINDS:
        problems.append(f"kind {spec.get('kind')!r} is not one of {sorted(VALID_KINDS)}")
    if spec.get("layer", "over") not in VALID_LAYERS:
        problems.append(f"layer {spec.get('layer')!r} is not one of {sorted(VALID_LAYERS)}")
    if spec.get("category") not in VALID_CATEGORIES:
        problems.append(f"category {spec.get('category')!r} is not one of {sorted(VALID_CATEGORIES)}")
    anchor = spec.get("anchor")
    if not (isinstance(anchor, list) and len(anchor) == 2 and all(isinstance(v, int) for v in anchor)):
        problems.append(f"anchor must be [x, y] integers, got {anchor!r}")

    patterns = [spec.get("pattern")] + list(spec.get("frames") or [])
    for i, pattern in enumerate(patterns):
        label = "pattern" if i == 0 else f"frames[{i - 1}]"
        if not (isinstance(pattern, list) and pattern and all(isinstance(r, str) for r in pattern)):
            problems.append(f"{label} must be a non-empty list of strings")
            return problems
        bad = sorted({ch for row in pattern for ch in row} - set(COSTUME_PALETTE))
        if bad:
            problems.append(f"{label}: unknown palette characters {bad}")
            return problems
        width, height = max(len(r) for r in pattern), len(pattern)
        if width > MAX_PATTERN or height > MAX_PATTERN:
            problems.append(f"{label} is {width}x{height}; the most is {MAX_PATTERN}x{MAX_PATTERN}")
        if not any(ch != "." for row in pattern for ch in row):
            problems.append(f"{label} is entirely transparent")
        holes = _holes(pattern)
        if holes:
            problems.append(f"{label} has {len(holes)} narrow enclosed transparent pixel(s), "
                            f"first at x={holes[0][0]} y={holes[0][1]}: fill them, or the "
                            f"outline floods them dark")
    if problems:
        return problems

    count = len(patterns)
    if not MIN_FRAMES <= count <= MAX_FRAMES:
        problems.append(f"{count} frame(s) in all; a daily costume animates with "
                        f"{MIN_FRAMES}-{MAX_FRAMES} (the pattern plus 1-3 in 'frames')")
    size = (max(len(r) for r in patterns[0]), len(patterns[0]))
    for i, pattern in enumerate(patterns[1:]):
        if (max(len(r) for r in pattern), len(pattern)) != size:
            problems.append(f"frames[{i}] is {max(len(r) for r in pattern)}x{len(pattern)}, but "
                            f"every frame must match the pattern's {size[0]}x{size[1]}")
    padded = [[row.ljust(size[0], ".") for row in p] for p in patterns]
    for i in range(1, len(padded)):
        if padded[i] == padded[0]:
            problems.append(f"frames[{i - 1}] is identical to the pattern; it would not move")
        for j in range(1, i):
            if padded[i] == padded[j]:
                problems.append(f"frames[{i - 1}] is identical to frames[{j - 1}]")
    if isinstance(anchor, list) and len(anchor) == 2:
        ax, ay = anchor
        if not (-12 <= ax <= size[0] + 12 and -12 <= ay <= size[1] + 12):
            problems.append(f"anchor {anchor} is nowhere near the {size[0]}x{size[1]} pattern")
    return problems


# --- the placement rules ---------------------------------------------------------------------

class Stage:
    """The real frames, cut once and reused for every spec."""

    def __init__(self) -> None:
        self.sheet, self.manifest = load_frames()
        self.frames: dict[tuple[str, int], tuple[Image.Image, dict]] = {}
        for state, spec in self.manifest["states"].items():
            for index in range(len(spec["frames"])):
                frame, anchors = frame_image(self.sheet, self.manifest, state, index)
                if frame is not None:
                    self.frames[(state, index)] = (frame, anchors or {})


def _lowest(image: Image.Image) -> int:
    alpha = image.getchannel("A")
    for y in range(image.height - 1, -1, -1):
        if any(alpha.getpixel((x, y)) for x in range(image.width)):
            return y
    return -1


def _topmost(image: Image.Image) -> int:
    box = image.getchannel("A").getbbox()
    return box[1] if box else 0


def place(spec: dict, pieces: list[Image.Image], target: list[int]) -> tuple[int, int]:
    """Where the runtime would stamp the piece: anchor on anchor, then lifted off the floor."""
    ax, ay = spec["anchor"][0] + 1, spec["anchor"][1] + 1    # the outline shifts the anchor
    x, y = int(target[0]) - ax, int(target[1]) - ay
    lowest = max(_lowest(p) for p in pieces)
    overhang = y + lowest - BASELINE_ROW
    if overhang > 0:
        y -= overhang
    return x, y


def dress(spec: dict, frame: Image.Image, anchors: dict, pieces: list[Image.Image],
          step: int, shadows: list | None = None) -> tuple[Image.Image, tuple[int, int] | None]:
    target = anchors.get(spec["kind"])
    if not target:
        return frame, None
    piece = pieces[step % len(pieces)]
    x, y = place(spec, pieces, target)
    if spec.get("layer", "over") == "behind":
        canvas = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        canvas.paste(piece, (x, y), piece)
        canvas.alpha_composite(frame)
        return canvas, (x, y)
    out = frame.copy()
    shadow = shadows[step % len(shadows)] if shadows else None
    if shadow is not None:
        for sy in range(shadow.height):
            for sx in range(shadow.width):
                fx, fy = x + sx, y + sy
                if shadow.getpixel((sx, sy))[3] and 0 <= fx < 48 and 0 <= fy < 48:
                    r, g, b, a = out.getpixel((fx, fy))
                    if a:
                        k = 1 - shadow.getpixel((sx, sy))[3] / 255
                        out.putpixel((fx, fy), (round(r * k), round(g * k), round(b * k), a))
    layer = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    layer.paste(piece, (x, y), piece)
    out.alpha_composite(layer)
    return out, (x, y)


def casts_shadow(spec: dict) -> bool:
    if "shadow" in spec:
        return bool(spec["shadow"])
    pattern = spec["pattern"]
    lowest = max((y for y, row in enumerate(pattern) if row.strip(".")), default=-1)
    return spec["kind"] == "head" and spec.get("layer", "over") == "over" \
        and lowest >= spec["anchor"][1] - 1


def render(spec: dict) -> tuple[list[Image.Image], list]:
    patterns = [spec["pattern"]] + list(spec.get("frames") or [])
    width = max(len(r) for r in patterns[0])
    patterns = [[row.ljust(width, ".") for row in p] for p in patterns]
    shade = spec.get("shade", True)
    pieces = [render_piece(p, shade=shade)[0] for p in patterns]
    shadows = [render_shadow(p) for p in patterns] if casts_shadow(spec) else None
    return pieces, shadows


def placement_problems(spec: dict, stage: Stage) -> tuple[list[str], list[str]]:
    problems, notes = [], []
    pieces, shadows = render(spec)
    kind, layer = spec["kind"], spec.get("layer", "over")
    idle, idle_anchors = stage.frames[("idle", 0)]
    dressed, at = dress(spec, idle, idle_anchors, pieces, 0, shadows)
    if at is None:
        return [f"idle publishes no {kind!r} anchor"], notes

    def added_rows(a: Image.Image, b: Image.Image, x_range=range(48)) -> list[int]:
        return [y for y in range(48) if any(a.getpixel((x, y)) != b.getpixel((x, y)) for x in x_range)]

    # Off his face.
    if kind != "eyes" and layer == "over":
        rows = [y for y in added_rows(dressed, idle, range(FACE_LEFT, FACE_RIGHT + 1))
                if EYE_TOP <= y <= EYE_BOTTOM]
        if rows:
            problems.append(f"covers his eyes standing still (rows {rows[0]}..{rows[-1]}, eye band "
                            f"is {EYE_TOP}..{EYE_BOTTOM}). A {kind} costume must stay off them - "
                            f"move the anchor or shorten it")
    if kind == "eyes":
        rows = added_rows(dressed, idle)
        if rows and rows[-1] > 40:
            problems.append(f"an eyes costume reaching row {rows[-1]} covers his mouth and body")
    if kind == "neck":
        rows = added_rows(dressed, idle)
        if rows and rows[-1] > BODY_BOTTOM + HANG_CLEARANCE:
            problems.append(f"hangs to row {rows[-1]}, {rows[-1] - BODY_BOTTOM} past his body "
                            f"(at most {HANG_CLEARANCE})")

    # On him, not past him: how far the runtime had to lift it off the floor standing still.
    # Up to two rows is the hem of a cape brushing the ground; more and he is wearing it too low.
    ax, ay = spec["anchor"][0] + 1, spec["anchor"][1] + 1
    target = idle_anchors[kind]
    raw_y = int(target[1]) - ay
    overhang = raw_y + max(_lowest(p) for p in pieces) - BASELINE_ROW
    if overhang > 2:
        problems.append(f"reaches {overhang} rows below his soles when he stands; the app would "
                        f"have to hoist it {overhang} rows up his body")

    # In the frame.
    top = min(_topmost(p) for p in pieces)
    left = min(p.getchannel("A").getbbox()[0] for p in pieces)
    right = max(p.getchannel("A").getbbox()[2] for p in pieces)
    if at[1] + top < 0:
        problems.append(f"cut off {-(at[1] + top)} row(s) at the top while he stands still")
    if at[0] + left < 0 or at[0] + right > 48:
        notes.append(f"touches the side of the frame standing still "
                     f"(x {at[0] + left}..{at[0] + right - 1}); it will be clipped there")
    worst, where = 0, ""
    for (state, index), (frame, anchors) in stage.frames.items():
        if state in UPENDED or (state, index) in AIRBORNE or kind not in anchors:
            continue
        x, y = place(spec, pieces, anchors[kind])
        if -(y + top) > worst:
            worst, where = -(y + top), f"{state}[{index}]"
    if worst > MAX_TRANSIENT_CLIP:
        problems.append(f"cut off {worst} rows at the top on {where}; at most "
                        f"{MAX_TRANSIENT_CLIP} - make it shorter")
    elif worst:
        notes.append(f"loses {worst} row(s) at the top on {where}")
    return problems, notes


# --- the review sheet ------------------------------------------------------------------------

POSES = [("idle", 0, "light"), ("idle", 0, "dark"), ("walk_right", 2, "light"),
         ("wave", 1, "dark"), ("sit_dangle", 1, "light"), ("sleep", 0, "dark"),
         ("hop", 2, "light")]
LIGHT, DARK = (243, 243, 243, 255), (32, 32, 32, 255)


def preview(specs: list[dict], stage: Stage, out: Path, zoom: int = 3) -> None:
    """One row per costume: the poses on a light and a dark taskbar, then each of its frames on
    idle, side by side, so a reviewer sees both how it reads and how it moves."""
    cell = 48 * zoom
    pad, label_h = 4, 16
    columns = len(POSES) + MAX_FRAMES
    sheet = Image.new("RGBA", (pad + columns * (cell + pad),
                               pad + len(specs) * (cell + label_h + pad)), (70, 70, 76, 255))
    draw = ImageDraw.Draw(sheet)
    for row, spec in enumerate(specs):
        y0 = pad + row * (cell + label_h + pad)
        draw.text((pad, y0), f"{spec.get('date')}  {spec.get('title')}  ({spec.get('name')}, "
                             f"{spec.get('kind')}) - poses | its {1 + len(spec.get('frames') or [])}"
                             f" frames", fill=(255, 255, 255, 255))
        try:
            pieces, shadows = render(spec)
        except Exception as exc:  # noqa: BLE001 - shown on the sheet rather than aborting it
            draw.text((pad, y0 + label_h), f"cannot render: {exc}", fill=(255, 120, 120, 255))
            continue
        tiles = [(s, i, bg, i) for s, i, bg in POSES]
        tiles += [("idle", 0, "light", f) for f in range(len(pieces))]
        for col, (state, index, bg, step) in enumerate(tiles):
            if (state, index) not in stage.frames:
                continue
            frame, anchors = stage.frames[(state, index)]
            dressed, _ = dress(spec, frame, anchors, pieces, step, shadows)
            tile = Image.new("RGBA", (48, 48), LIGHT if bg == "light" else DARK)
            tile.alpha_composite(dressed)
            sheet.paste(tile.resize((cell, cell), Image.NEAREST),
                        (pad + col * (cell + pad), y0 + label_h))
    sheet.convert("RGB").save(out)


# --- the command ---------------------------------------------------------------------------

def check(specs: list[tuple[Path, dict]], stage: Stage) -> dict[str, dict]:
    report = {}
    names: dict[str, str] = {}
    for path, spec in specs:
        problems = static_problems(spec)
        notes: list[str] = []
        if not problems:
            try:
                more, notes = placement_problems(spec, stage)
                problems += more
            except Exception as exc:  # noqa: BLE001 - a broken spec is a finding, not a crash
                problems.append(f"could not be placed: {exc}")
        name = spec.get("name", "")
        if name in names:
            problems.append(f"name {name!r} is also used by {names[name]}")
        names.setdefault(name, path.name)
        if spec.get("date") and path.stem != spec.get("date"):
            problems.append(f"file is {path.name} but the spec says date {spec.get('date')}")
        report[str(path)] = {"problems": problems, "notes": notes}
    return report


def main(argv: list[str]) -> int:
    out = None
    if "--preview" in argv and argv.index("--preview") + 1 < len(argv):
        out = Path(argv[argv.index("--preview") + 1])
    # Skipped by position: the preview path is never a spec, however its slashes were written.
    args = [a for i, a in enumerate(argv)
            if not a.startswith("--") and not (i and argv[i - 1] == "--preview")]
    quiet = "--quiet" in argv
    if not args:
        print(__doc__)
        return 2
    specs = load_specs([Path(a) for a in args])
    stage = Stage()
    report = check(specs, stage)
    failed = 0
    for path, result in report.items():
        if result["problems"]:
            failed += 1
            print(f"FAIL {Path(path).name}")
            for p in result["problems"]:
                print(f"     - {p}")
        elif not quiet:
            print(f"ok   {Path(path).name}")
        if not quiet:
            for n in result["notes"]:
                print(f"     note: {n}")
    print(f"\n{len(report) - failed}/{len(report)} pass")
    if out:
        preview([s for _p, s in specs if "_error" not in s and not static_problems(s)], stage, out)
        print(f"wrote {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
