"""Validate a Nib sprite sheet + manifest against the project's art contract.

Usage:
    python tools/validate_sheet.py <sheet.png> <frames.json>

Exits 0 when the sheet is conformant, 1 otherwise. Every violation is printed, so a
failing run tells you everything that needs fixing in one pass.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

PALETTE = {
    (0xD9, 0x55, 0x2E): "body",
    (0xE8, 0x72, 0x4A): "highlight",
    (0xB2, 0x3F, 0x1E): "shadow",
    (0x2C, 0x12, 0x06): "outline",
    (0x8A, 0x3D, 0x22): "feet-shadow",
    (0xC0, 0x39, 0x2B): "angry",
    (0xF5, 0xED, 0xE6): "light-detail",
}

# state -> frame indices that must be non-empty, per the contract
REQUIRED_STATES = {
    "idle": [0, 1, 2],
    "blink": [3],
    "walk_right": [10, 11, 12, 13, 14, 15],
    "wave": [20, 21, 22, 23],
    "working": [30, 31, 32],
    "angry": [40, 41, 42, 43],
    "sleep": [50, 51],
    "wake": [60, 61],
    "eyes_track": list(range(70, 79)),
    "look_around": [80, 81, 82, 83],
    "wink": [90, 91, 92],
    "sparkle": [100, 101, 102, 103],
    "heart_eyes": [110, 111, 112, 113],
    "football": [120, 121, 122, 123, 124, 125],
    "dance": [130, 131, 132, 133, 134, 135],
    "pushups": [140, 141, 142, 143, 144],
    "hop": [150, 151, 152, 153, 154],
    "sit_dangle": [160, 161, 162, 163],
    "injured": [170, 171, 172, 173],
    "tumble": [180, 181, 182, 183],
    "yawn": [190, 191, 192, 193],
    "stretch": [200, 201, 202, 203],
    "coffee": [210, 211, 212, 213, 214],
    "hot": [220, 221, 222, 223, 224],
    "shiver": [230, 231, 232, 233],
    "autumn": [240, 241, 242, 243],
    "typing": [250, 251, 252, 253, 254],
    "counting": [260, 261, 262, 263],
    "spreadsheet": [270, 271, 272, 273],
    "mail": [280, 281, 282, 283],
    "strain": [290, 291, 292, 293, 294],
    "startle": [300, 301, 302, 303],
    "blink_slow": [310, 311, 312],
    "pet": [320, 321, 322, 323],
    "eat": [324, 325, 326, 327],
    "scratch": [330, 331, 332, 333],
    "foot_tap": [340, 341, 342, 343],
    "check_watch": [350, 351, 352, 353],
    "sneeze": [360, 361, 362, 363],
    "trip": [370, 371, 372],
    "hiccup": [380, 381, 382],
    "task_finished": [390, 391, 392, 393, 394, 395],
}

#: Frames that are legitimately off the ground, so the feet-on-the-baseline rule cannot apply.
#: Only the hop's mid-air frames qualify - everything else must keep its soles planted, or the
#: mascot would visibly jitter up and down as states change.
AIRBORNE_FRAMES = frozenset({151, 152, 153})

EXPECTED_GEOMETRY = (48, 48, 10, 40)


def frame_box(idx: int, fw: int, fh: int, cols: int) -> tuple[int, int, int, int]:
    row, col = divmod(idx, cols)
    return (col * fw, row * fh, col * fw + fw, row * fh + fh)


def opaque_rows(img: Image.Image) -> list[int]:
    """Row indices (0 = top) that contain at least one opaque pixel."""
    px = img.load()
    w, h = img.size
    return [y for y in range(h) if any(px[x, y][3] != 0 for x in range(w))]


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    sheet_path, manifest_path = Path(sys.argv[1]), Path(sys.argv[2])
    errors: list[str] = []
    warnings: list[str] = []

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"FAIL: cannot read manifest: {exc}")
        return 1

    fw = manifest.get("frame_w")
    fh = manifest.get("frame_h")
    cols = manifest.get("cols")
    rows = manifest.get("rows")
    baseline_offset = manifest.get("baseline_offset")

    if (fw, fh, cols, rows) != EXPECTED_GEOMETRY:
        errors.append(
            f"geometry must be frame_w/h/cols/rows = {EXPECTED_GEOMETRY}, got "
            f"{fw}x{fh} cols={cols} rows={rows}"
        )
    if baseline_offset != 1:
        errors.append(f"baseline_offset must be 1, got {baseline_offset!r}")
    if not isinstance(manifest.get("walk_step_px"), int) or manifest["walk_step_px"] <= 0:
        errors.append(f"walk_step_px must be a positive int, got {manifest.get('walk_step_px')!r}")

    for state, idxs in REQUIRED_STATES.items():
        spec = manifest.get("states", {}).get(state)
        if not spec:
            errors.append(f"manifest missing state {state!r}")
            continue
        got = [f[0] for f in spec.get("frames", [])]
        if sorted(set(got)) != sorted(set(idxs)):
            errors.append(f"state {state!r} frame indices {got} != contract {idxs}")
        for entry in spec.get("frames", []):
            if len(entry) != 2 or not all(isinstance(v, int) for v in entry):
                errors.append(f"state {state!r} has malformed frame entry {entry!r}")
            elif entry[1] <= 0:
                errors.append(f"state {state!r} frame {entry[0]} has non-positive duration")
    if "walk_left" in manifest.get("states", {}):
        errors.append("walk_left must NOT be in the manifest (it is mirrored at load time)")

    # Costumes are composited at these anchors, so a frame without them could not wear a hat.
    anchors = manifest.get("anchors")
    if not isinstance(anchors, dict):
        errors.append("manifest is missing the per-frame 'anchors' map")
    else:
        for state, idxs in REQUIRED_STATES.items():
            for idx in idxs:
                entry = anchors.get(str(idx))
                if not isinstance(entry, dict):
                    errors.append(f"frame {idx} ({state}) has no anchors entry")
                    continue
                for kind in ("head", "eyes"):
                    point = entry.get(kind)
                    if (
                        not isinstance(point, list)
                        or len(point) != 2
                        or not all(isinstance(v, int) for v in point)
                        or not (0 <= point[0] < 48 and 0 <= point[1] < 48)
                    ):
                        errors.append(
                            f"frame {idx} ({state}) has a bad {kind!r} anchor: {point!r}"
                        )

    if errors and (fw, fh, cols, rows) != EXPECTED_GEOMETRY:
        # geometry is wrong, per-frame checks would be meaningless
        for e in errors:
            print(f"FAIL: {e}")
        return 1

    try:
        img = Image.open(sheet_path).convert("RGBA")
    except Exception as exc:
        print(f"FAIL: cannot read sheet: {exc}")
        return 1

    if img.size != (fw * cols, fh * rows):
        errors.append(f"sheet must be {fw * cols}x{fh * rows}, got {img.width}x{img.height}")
        for e in errors:
            print(f"FAIL: {e}")
        return 1

    # --- palette + alpha purity over the whole sheet ---
    bad_colors: Counter = Counter()
    partial_alpha = 0
    for (count, pixel) in img.getcolors(maxcolors=1 << 24) or []:
        r, g, b, a = pixel
        if a not in (0, 255):
            partial_alpha += count
            continue
        if a == 255 and (r, g, b) not in PALETTE:
            bad_colors[(r, g, b)] += count
    if partial_alpha:
        errors.append(
            f"{partial_alpha} px have partial alpha; alpha must be exactly 0 or 255 "
            "(no anti-aliasing)"
        )
    if bad_colors:
        listed = ", ".join(
            f"#{r:02X}{g:02X}{b:02X}({n}px)" for (r, g, b), n in bad_colors.most_common(8)
        )
        errors.append(f"{len(bad_colors)} off-palette colour(s): {listed}")

    # --- per-frame content + baseline discipline ---
    baseline_row = fh - 1 - int(baseline_offset or 0)
    all_required = sorted({i for idxs in REQUIRED_STATES.values() for i in idxs})
    for idx in all_required:
        frame = img.crop(frame_box(idx, fw, fh, cols))
        rows_used = opaque_rows(frame)
        if not rows_used:
            errors.append(f"frame {idx} is empty but is required")
            continue
        bottom = max(rows_used)
        if idx in AIRBORNE_FRAMES:
            if bottom >= baseline_row:
                errors.append(
                    f"frame {idx} is meant to be airborne but its lowest opaque row is {bottom} "
                    f"(baseline is {baseline_row})"
                )
        elif bottom != baseline_row:
            errors.append(
                f"frame {idx}: lowest opaque row is {bottom}, expected {baseline_row} "
                f"(baseline_offset={baseline_offset}) - feet must rest on the baseline"
            )
        # Sleep draws its eyes as flat lines and heart_eyes replaces them with red hearts, so
        # neither has the dark eye block this check looks for.
        if idx in (50, 51) or idx in REQUIRED_STATES["heart_eyes"]:
            continue
        px = frame.load()
        dark = sum(
            1
            for y in range(fh)
            for x in range(fw)
            if px[x, y][3] == 255 and px[x, y][:3] == (0x2C, 0x12, 0x06)
        )
        if dark < 4:
            warnings.append(f"frame {idx}: only {dark} outline/eye px - eyes may be missing")

    # --- cells outside the contract must be empty ---
    for idx in range(cols * rows):
        if idx in all_required:
            continue
        frame = img.crop(frame_box(idx, fw, fh, cols))
        if frame.getbbox() is not None:
            warnings.append(f"frame {idx} is outside the contract but is not empty")

    for w in warnings:
        print(f"WARN: {w}")
    for e in errors:
        print(f"FAIL: {e}")
    if errors:
        print(f"\n{len(errors)} error(s), {len(warnings)} warning(s)")
        return 1
    print(f"OK: sheet conforms to contract ({len(warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())


