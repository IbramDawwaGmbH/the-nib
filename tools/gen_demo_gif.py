"""Renders README demo GIFs straight from the sprite sheet.

Not a screen recording - it reads assets/nib_sheet.png and assets/frames.json, the same
source the app itself animates from, and composites frames onto a small drawn taskbar so the
"lives on your taskbar" idea reads in a still image. Re-run after any change to gen_sprites.py
that touches a state used here (see STATES_USED below).

    python tools/gen_demo_gif.py
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
OUT_DIR = ASSETS / "gifs"

# Every state any GIF below draws from - kept in one place so it's obvious what to check after
# an art change.
STATES_USED = ["idle", "wave", "walk_right", "football", "pushups", "dance", "hop", "task_finished",
               "strain"]

# Same font family Qt falls back to on Windows for the real billboard widget, so the demo reads
# like the actual UI rather than a mockup of it. Falls back to PIL's bitmap font if unavailable -
# e.g. re-rendering on a non-Windows machine - since a missing font must not break the build.
try:
    _SIGN_FONT = ImageFont.truetype("segoeui.ttf", 15)
except OSError:
    _SIGN_FONT = ImageFont.load_default()

# Taskbar colours: readable sitting on both GitHub's white and dark page backgrounds, since only
# the sky above the bar is transparent.
BAR_FILL = (43, 43, 48, 255)
BAR_EDGE = (72, 72, 80, 255)

# A generic "some app window" mockup for the perch/pin demo - light, so it reads as a window
# rather than another taskbar, on both GitHub page backgrounds.
WINDOW_FILL = (250, 250, 251, 255)
WINDOW_EDGE = (198, 198, 205, 255)
WINDOW_TITLE_FILL = (233, 233, 238, 255)
WINDOW_DOTS = [(237, 106, 94, 255), (245, 191, 79, 255), (97, 194, 92, 255)]
#: The same red the real tray-icon badge and the focus timer's urgent bar use - see
#: main._pin_badge and focus_timer.URGENT_COLOR - so the demo's marker reads as the same one.
PIN_BADGE_COLOR = (224, 72, 58, 255)


class Sheet:
    def __init__(self) -> None:
        manifest = json.loads((ASSETS / "frames.json").read_text(encoding="utf-8"))
        self.frame_w = int(manifest["frame_w"])
        self.frame_h = int(manifest["frame_h"])
        self.cols = int(manifest["cols"])
        self.baseline_offset = int(manifest.get("baseline_offset", 0))
        self.walk_step_px = max(1, int(manifest.get("walk_step_px", 3)))
        self.states: dict[str, list[tuple[int, int]]] = {
            name: [(int(i), max(1, int(ms))) for i, ms in spec["frames"]]
            for name, spec in manifest["states"].items()
        }
        sheet = Image.open(ASSETS / "nib_sheet.png").convert("RGBA")
        self._frames: dict[int, Image.Image] = {}
        for index in range(self.cols * (sheet.height // self.frame_h)):
            row, col = divmod(index, self.cols)
            box = (col * self.frame_w, row * self.frame_h, (col + 1) * self.frame_w, (row + 1) * self.frame_h)
            self._frames[index] = sheet.crop(box)

    def frame(self, sheet_index: int) -> Image.Image:
        return self._frames[sheet_index]


def taskbar_canvas(width: int, height: int, bar_height: int) -> Image.Image:
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    top = height - bar_height
    draw.rectangle([0, top, width, height], fill=BAR_FILL)
    draw.rectangle([0, top, width, top + 1], fill=BAR_EDGE)
    return canvas


def window_canvas(width: int, height: int, win_x: int, win_top: int, win_w: int, win_h: int) -> Image.Image:
    """A generic app window - title bar, three dots, plain body - standing in for "a browser, an
    editor, a chat", so the perch/pin demo reads as landing on a window rather than the taskbar
    every other GIF here uses."""
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    title_h = 20
    draw.rounded_rectangle(
        [win_x, win_top, win_x + win_w - 1, win_top + win_h - 1], radius=8,
        fill=WINDOW_FILL, outline=WINDOW_EDGE, width=1,
    )
    # The title bar's own rounded top corners, squared off where it meets the body below so the
    # two halves read as one continuous window rather than a capsule sitting on a box.
    draw.rounded_rectangle(
        [win_x, win_top, win_x + win_w - 1, win_top + title_h + 8], radius=8,
        fill=WINDOW_TITLE_FILL, outline=WINDOW_EDGE, width=1,
    )
    draw.rectangle([win_x + 1, win_top + title_h - 8, win_x + win_w - 2, win_top + title_h],
                   fill=WINDOW_TITLE_FILL)
    draw.line([win_x + 1, win_top + title_h, win_x + win_w - 2, win_top + title_h], fill=WINDOW_EDGE)
    for i, colour in enumerate(WINDOW_DOTS):
        cx, cy = win_x + 14 + i * 14, win_top + title_h // 2
        draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=colour)
    return canvas


class Scene:
    """One GIF's worth of frames: a fixed taskbar, a moving-or-stationary sprite."""

    def __init__(self, sheet: Sheet, width: int, height: int, bar_height: int, scale: int) -> None:
        self.sheet = sheet
        self.width, self.height, self.bar_height, self.scale = width, height, bar_height, scale
        self.backdrop = taskbar_canvas(width, height, bar_height)
        self.sprite_h = sheet.frame_h * scale
        self.sole_gap = sheet.baseline_offset * scale
        taskbar_top = height - bar_height
        self.sprite_top = taskbar_top + self.sole_gap - self.sprite_h
        self.frames: list[Image.Image] = []
        self.durations: list[int] = []

    def _paste(self, sprite_img: Image.Image, x: int) -> Image.Image:
        composite = self.backdrop.copy()
        scaled = sprite_img.resize(
            (self.sheet.frame_w * self.scale, self.sheet.frame_h * self.scale), Image.NEAREST
        )
        composite.paste(scaled, (x, self.sprite_top), scaled)
        return composite

    def hold(self, state: str, x: int, loops: int = 1, mirror: bool = False) -> None:
        for _ in range(loops):
            for sheet_index, ms in self.sheet.states[state]:
                img = self.sheet.frame(sheet_index)
                if mirror:
                    img = ImageOps.mirror(img)
                self.frames.append(self._paste(img, x))
                self.durations.append(ms)

    def walk(self, start_x: int, end_x: int, state: str = "walk_right", speed: float = 1.0) -> int:
        """Steps through the walk cycle, translating by the manifest's own stride each frame.

        ``speed`` scales only playback time, never the per-frame stride - a demo played back
        faster still covers exactly one stride per frame, the same claim the README makes about
        the real thing at any tick rate.

        Returns the x actually reached (a whole number of strides from start_x, so it lands
        exactly rather than overshooting).
        """
        mirror = end_x < start_x
        cycle = self.sheet.states[state]
        step = self.sheet.walk_step_px * self.scale * (-1 if mirror else 1)
        x = start_x
        i = 0
        while (x < end_x) if not mirror else (x > end_x):
            sheet_index, ms = cycle[i % len(cycle)]
            img = self.sheet.frame(sheet_index)
            if mirror:
                img = ImageOps.mirror(img)
            self.frames.append(self._paste(img, x))
            self.durations.append(max(20, round(ms / speed)))
            x += step
            i += 1
        return x

    def save(self, path: Path) -> None:
        # A shared palette (quantised from one frame that already contains the full cast of
        # colours used) keeps every frame mapped to the same indices, so the GIF doesn't flicker
        # as an adaptive-per-frame palette would.
        master = max(self.frames, key=lambda f: len(f.getcolors(maxcolors=1_000_000) or []))
        master_p = master.convert("RGB").convert("P", palette=Image.ADAPTIVE, colors=255)

        converted = []
        for frame in self.frames:
            alpha = frame.split()[3]
            rgb = frame.convert("RGB")
            p_frame = rgb.quantize(palette=master_p, dither=Image.Dither.NONE)
            transparent_mask = alpha.point(lambda a: 255 if a <= 0 else 0)
            p_frame.paste(255, mask=transparent_mask)
            converted.append(p_frame)

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        converted[0].save(
            path,
            save_all=True,
            append_images=converted[1:],
            duration=self.durations,
            loop=0,
            disposal=2,
            transparency=255,
            optimize=False,
        )
        print(f"{path.relative_to(ROOT)}  {len(converted)} frames, {sum(self.durations) / 1000:.1f}s")


# Canvas heights below are trimmed to just above the highest reach of the tallest state each
# GIF plays (hop's mid-air peak, sheet index with the lowest row in its frame's bbox) plus a
# small margin - not to the full 48px frame, which is mostly headroom no state here needs.
HERO_HEIGHT = 170
GALLERY_HEIGHT = 165


def build_hero(sheet: Sheet) -> None:
    scene = Scene(sheet, width=480, height=HERO_HEIGHT, bar_height=34, scale=3)
    left, right = 18, 480 - sheet.frame_w * 3 - 18
    scene.hold("wave", left, loops=1)
    reached = scene.walk(left, right, "walk_right", speed=1.8)
    scene.hold("football", reached, loops=1)
    scene.hold("pushups", reached, loops=1)
    scene.hold("dance", reached, loops=1)
    scene.hold("hop", reached, loops=1)
    scene.walk(reached, left, "walk_right", speed=1.8)
    scene.hold("wave", left, loops=1)
    scene.save(OUT_DIR / "hero.gif")


def build_gallery(sheet: Sheet) -> None:
    width, bar_height, scale = 220, 30, 3
    x = (width - sheet.frame_w * scale) // 2
    for state, loops in (("football", 3), ("pushups", 3), ("dance", 3), ("hop", 3)):
        scene = Scene(sheet, width, GALLERY_HEIGHT, bar_height, scale)
        scene.hold(state, x, loops=loops)
        scene.save(OUT_DIR / f"{state}.gif")


#: Where his raised hand is, as a fraction of the sprite frame - must match HAND_X_FRACTION /
#: HAND_Y_FRACTION in billboard.py, since this is standing in for that real widget.
BILLBOARD_HAND_X = 0.85
BILLBOARD_HAND_Y = 0.22
SIGN_BG = (255, 255, 255, 255)
SIGN_BORDER = (228, 218, 211, 255)
SIGN_TEXT = (44, 18, 6, 255)


def render_sign(text: str, width: int) -> tuple[Image.Image, int]:
    """The sign + its tail, tight-cropped. Returns the image and the tail's x within it, so the
    caller can align that point to the hand instead of the box's centre."""
    pad, radius, tail_w, tail_h = 12, 10, 16, 9
    surface = Image.new("RGBA", (width, 200), (0, 0, 0, 0))
    draw = ImageDraw.Draw(surface)

    lines: list[str] = []
    for raw in text.split("\n"):
        words, line = raw.split(), ""
        for word in words:
            trial = f"{line} {word}".strip()
            if line and draw.textlength(trial, font=_SIGN_FONT) > width - pad * 2:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
    line_h = _SIGN_FONT.size + 4
    height = pad * 2 + line_h * len(lines)
    tail_x = width // 2

    draw.rounded_rectangle([0, 0, width - 1, height - 1], radius=radius,
                           fill=SIGN_BG, outline=SIGN_BORDER, width=1)
    draw.polygon(
        [(tail_x - tail_w // 2, height - 1), (tail_x + tail_w // 2, height - 1),
         (tail_x, height - 1 + tail_h)],
        fill=SIGN_BG, outline=SIGN_BORDER,
    )
    for i, line in enumerate(lines):
        draw.text((pad, pad + i * line_h), line, font=_SIGN_FONT, fill=SIGN_TEXT)

    return surface.crop((0, 0, width, height + tail_h)), tail_x


def build_billboard(sheet: Sheet) -> None:
    """The snippet billboard: the strain-braced hold, with a sign standing in for the real
    (Qt-rendered, so unreachable from this Qt-free tool) billboard widget attached at his hand."""
    width, height, bar_height, scale = 240, 235, 30, 3
    scene = Scene(sheet, width, height, bar_height, scale)
    x = (width - sheet.frame_w * scale) // 2
    scene.hold("strain", x, loops=2)

    hand_x = x + int(sheet.frame_w * scale * BILLBOARD_HAND_X)
    hand_y = scene.sprite_top + int(sheet.frame_h * scale * BILLBOARD_HAND_Y)
    sign, tail_x = render_sign("refactor the auth\nmodule before Friday", 200)
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    layer.paste(sign, (hand_x - tail_x, hand_y - sign.height), sign)

    scene.frames = [Image.alpha_composite(frame, layer) for frame in scene.frames]
    scene.save(OUT_DIR / "billboard.gif")


def build_task_finished(sheet: Sheet) -> None:
    """A dedicated clip for the CPU watcher's celebration - a one-shot narrative, not a loop, so
    it gets its own two-play-through demo rather than a slot in the generic behaviour gallery."""
    width, bar_height, scale = 220, 30, 3
    x = (width - sheet.frame_w * scale) // 2
    scene = Scene(sheet, width, GALLERY_HEIGHT, bar_height, scale)
    scene.hold("task_finished", x, loops=2)
    scene.save(OUT_DIR / "task_finished.gif")


def build_perch(sheet: Sheet) -> None:
    """Standing on a window instead of the taskbar, with the tray's own red pin marker fading in
    partway through - the beat where PERCH_PIN_DELAY_MS (main.py) has just run out and the perch
    stops being "he happened to land here" and becomes a real, pinned-on-top one."""
    width, height, scale = 220, 190, 3
    win_w, win_h = 158, 96
    win_x = (width - win_w) // 2
    win_top = height - win_h - 24  # a sliver of desktop below, so the window reads as floating
    scene = Scene(sheet, width, height, bar_height=height - win_top, scale=scale)
    scene.backdrop = window_canvas(width, height, win_x, win_top, win_w, win_h)
    x = win_x + (win_w - sheet.frame_w * scale) // 2

    scene.hold("idle", x, loops=3)  # just landed - undisturbed, but not pinned yet
    pinned_from = len(scene.frames)
    scene.hold("idle", x, loops=4)  # the dwell has now run out

    badge = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(badge)
    bx, by = win_x + win_w - 11, win_top + 10
    d.ellipse([bx - 6, by - 6, bx + 6, by + 6], fill=PIN_BADGE_COLOR, outline=(255, 255, 255, 255))
    scene.frames = [
        Image.alpha_composite(frame, badge) if i >= pinned_from else frame
        for i, frame in enumerate(scene.frames)
    ]
    scene.save(OUT_DIR / "perch.gif")


def main() -> None:
    sheet = Sheet()
    build_hero(sheet)
    build_gallery(sheet)
    build_task_finished(sheet)
    build_billboard(sheet)
    build_perch(sheet)


if __name__ == "__main__":
    main()
