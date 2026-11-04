"""Render candidate costumes onto real frames, without touching the sprite sheet.

Usage:
    python tools/preview_costume.py <spec.json> [out.png]

``spec.json`` is ``{name: {"kind": ..., "anchor": [x, y], "pattern": [...], "layer": ...}}`` -
exactly the shape a ``COSTUMES`` entry has. Nothing is written to ``assets/``, so this is safe to
run repeatedly while iterating on a pattern.

It composites the same way the runtime does: outline the pattern, then stamp it so its anchor
lands on the frame's own anchor point. Which means what you see here is what the app will draw,
including on the poses that move his head about.

Every costume is drawn over a light AND a dark taskbar swatch, because a costume that vanishes on
one of them is the single most common way one of these fails.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))
from gen_sprites import (COSTUME_CELL, COSTUME_PALETTE, COSTUME_SHADOW_ALPHA,  # noqa: E402
                         costume_pixels, costume_shadow, outline_costume)

ROOT = Path(__file__).parent.parent
ASSETS = ROOT / "assets"

#: Poses worth checking. Between them they cover: standing, mid-stride, genuinely airborne,
#: sitting low, lying down, and the two poses that raise an arm past his head.
POSES = [
    ("idle", 0), ("walk_right", 2), ("hop", 2), ("sit_dangle", 1),
    ("sleep", 0), ("wave", 1), ("scratch", 1),
]
ZOOM = 4
LIGHT, DARK = (243, 243, 243, 255), (32, 32, 32, 255)


def load_frames() -> tuple[Image.Image, dict]:
    manifest = json.loads((ASSETS / "frames.json").read_text(encoding="utf-8"))
    return Image.open(ASSETS / "nib_sheet.png").convert("RGBA"), manifest


def frame_image(sheet: Image.Image, manifest: dict, state: str, index: int):
    """One 48x48 frame plus its anchor table, or None if the state is missing."""
    spec = manifest["states"].get(state)
    if not spec:
        return None, None
    sheet_index = spec["frames"][index % len(spec["frames"])][0]
    fw, fh, cols = manifest["frame_w"], manifest["frame_h"], manifest["cols"]
    row, col = divmod(sheet_index, cols)
    crop = sheet.crop((col * fw, row * fh, col * fw + fw, row * fh + fh))
    return crop, manifest["anchors"].get(str(sheet_index), {})


def render_piece(pattern: list[str], shade: bool = True) -> tuple[Image.Image, tuple[int, int]]:
    """Outline and shade the pattern exactly as the sheet does. Returns the image and the anchor
    shift."""
    outlined = outline_costume(pattern)
    width, height = max(len(r) for r in outlined), len(outlined)
    if width > COSTUME_CELL or height > COSTUME_CELL:
        raise ValueError(
            f"{width}x{height} once outlined, which is over the {COSTUME_CELL}x{COSTUME_CELL} "
            f"cell. The pattern itself must fit {COSTUME_CELL - 2}x{COSTUME_CELL - 2}."
        )
    for y, row in enumerate(outlined):
        for ch in row:
            if ch not in COSTUME_PALETTE:
                raise ValueError(
                    f"unknown palette character {ch!r} at row {y}. "
                    f"Valid: {', '.join(k for k in COSTUME_PALETTE if k != '.')} and '.'"
                )
    piece = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pixels = piece.load()
    for y, row in enumerate(costume_pixels(pattern, shade=shade)):
        for x, colour in enumerate(row):
            if colour is not None:
                pixels[x, y] = colour
    return piece, (1, 1)   # the outline pushed everything one pixel right and down


def render_shadow(pattern: list[str]) -> Image.Image:
    """The shadow a hat casts on him, in the piece's own coordinates."""
    cast = costume_shadow(pattern)
    shadow = Image.new("RGBA", (len(cast[0]), len(cast)), (0, 0, 0, 0))
    for y, row in enumerate(cast):
        for x, on in enumerate(row):
            if on:
                shadow.putpixel((x, y), (0, 0, 0, COSTUME_SHADOW_ALPHA))
    return shadow


def dress(frame: Image.Image, anchors: dict, item: dict, piece: Image.Image,
          shift: tuple[int, int], shadow: Image.Image | None = None) -> Image.Image:
    target = anchors.get(item.get("kind", "head"))
    if not target:
        return frame
    ax, ay = item["anchor"][0] + shift[0], item["anchor"][1] + shift[1]
    x, y = int(target[0]) - ax, int(target[1]) - ay
    if item.get("layer") == "behind":
        canvas = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        canvas.alpha_composite(piece, dest=(max(0, x), max(0, y)))
        canvas.alpha_composite(frame)
        return canvas
    out = frame.copy()
    if shadow is not None:
        # Only over his own pixels, as the runtime's SourceAtop does: darken, keep his alpha.
        for sy in range(shadow.height):
            for sx in range(shadow.width):
                a = shadow.getpixel((sx, sy))[3]
                fx, fy = x + sx, y + sy
                if a and 0 <= fx < out.width and 0 <= fy < out.height:
                    r, g, b, fa = out.getpixel((fx, fy))
                    if fa:
                        k = 1 - a / 255
                        out.putpixel((fx, fy), (round(r * k), round(g * k), round(b * k), fa))
    # alpha_composite refuses negative destinations, so pad rather than clip - a costume hanging
    # off the top of the cell is a real thing to see, not something to silently crop.
    if x < 0 or y < 0:
        pad = Image.new("RGBA", frame.size, (0, 0, 0, 0))
        pad.paste(piece, (x, y), piece)
        out.alpha_composite(pad)
        return out
    out.alpha_composite(piece, dest=(x, y))
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("costume_preview.png")
    sheet, manifest = load_frames()

    poses = [(s, i) for s, i in POSES if s in manifest["states"]]
    cell = 48 * ZOOM
    pad, label_w = 6, 150
    rows = sum(2 + (1 if item.get("frames") else 0) for item in spec.values())
    canvas = Image.new(
        "RGBA",
        (label_w + len(poses) * (cell + pad) + pad, pad + rows * (cell + pad)),
        (70, 70, 76, 255),
    )

    problems = []
    r = 0
    for name, item in spec.items():
        patterns = [item.get("pattern")] + list(item.get("frames") or [])
        try:
            pieces = [render_piece(p, shade=item.get("shade", True)) for p in patterns]
        except (ValueError, KeyError, TypeError) as exc:
            problems.append(f"{name}: {exc}")
            continue
        casts = item.get("shadow", item.get("kind") == "head" and item.get("layer", "over") == "over")
        shadows = [render_shadow(p) if casts else None for p in patterns]
        if any(pc[0].size != pieces[0][0].size for pc in pieces):
            problems.append(f"{name}: every frame must be the same size as the first")
        # Light and dark rows show each pose at its own step, as the runtime does; a moving
        # costume gets a third row: idle's frames, then the walk's, one step each.
        steps = [(s, i) for s, i in POSES]
        rows_here = [("light", LIGHT, steps), ("dark", DARK, steps)]
        if len(patterns) > 1:
            anim = [("idle", i) for i in range(4)] + [("walk_right", i) for i in range(6)]
            rows_here.append(("frames", LIGHT, anim[:len(poses)]))
        for _label, bg, row_poses in rows_here:
            y = pad + r * (cell + pad)
            r += 1
            for c, (state, index) in enumerate(row_poses):
                frame, anchors = frame_image(sheet, manifest, state, index)
                if frame is None:
                    continue
                piece, shift = pieces[index % len(pieces)]
                dressed = dress(frame, anchors, item, piece, shift, shadows[index % len(pieces)])
                tile = Image.new("RGBA", (48, 48), bg)
                tile.alpha_composite(dressed)
                canvas.paste(
                    tile.resize((cell, cell), Image.NEAREST),
                    (label_w + c * (cell + pad), y),
                )

    canvas.convert("RGB").save(out_path)
    print(f"wrote {out_path}")
    print("poses, left to right: " + ", ".join(f"{s}[{i}]" for s, i in poses))
    print("each costume gets TWO rows: light taskbar, then dark")
    for name, item in spec.items():
        pattern = item.get("pattern") or [""]
        print(f"  {name:16s} {item.get('kind','head'):5s} anchor={item.get('anchor')} "
              f"{max(len(r) for r in pattern)}x{len(pattern)}")
    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print("  " + p)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
