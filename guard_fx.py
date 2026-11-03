"""Away Guard background effects: the life going on *behind* the eyes, in the fog.

The fog on its own is a still grey photograph, and a guard that never moves reads as a frozen app.
This layer sits between the fog and the scene widgets (``GuardOverlay.paintEvent`` paints it right
after the fog) and adds a little ambient life, all in Nib's own pixel style and palette:

*   **Worker bots** - small, dim, orange robots that walk along the bottom of the screen carrying
    boxes, or fly across the top under a propeller with a box or a gear dangling. "Agents at work",
    made literal. A few at a time, a new one every several seconds.
*   **Dust motes** drifting through the fog, nudged out of the way by the pointer.
*   **A security-camera look**: a scan line sweeping top to bottom every few seconds, and a
    blinking red ``REC`` with the time guarded in the top-left corner (top-right is the faint
    "unlock" link's corner in CHOICE mode, so nothing of ours goes there).
*   **A slow ambient glow** breathing behind everything, tinted by the time of day (warmer in the
    evening, deep blue with a few twinkling pixel stars at night - ``world.part_of_day`` decides,
    so "evening" means the same thing here as everywhere else in Nib). In blackout all of it goes
    except a faint slow red pulse and the REC: blackout has to stay overwhelmingly black.

Why it is shaped the way it is - the cost model:

    The guard runs *because* agents are working underneath, so it must stay cheap. A full repaint
    of a big overlay costs a few ms (fog, glow, the whole transparent ``GuardScene`` child on top,
    then the blit to the screen) - fine once a second, not twenty times. So :class:`BackgroundFx`
    asks for a full repaint only when the glow's brightness steps (it is quantised to a few
    levels, ~1 Hz) or the time of day changes, and otherwise reports small rectangles. The
    fog, the tint and the glow at its current level are composed once into a cached *base*
    (:meth:`BackgroundFx.paint_base`), so a partial repaint is one opaque blit plus a few sprites
    instead of re-blending a glow over the area every time (~4x cheaper per strip).

    The less obvious half: on Windows a repaint is pushed to the screen as *one* blit of the
    bounding box of everything repainted together, so a mote in each corner repainted as one
    region costs nearly a full-screen blit (measured: ~2 ms at 1440p, against ~0.2 ms for a strip
    along the bottom). So dirt is reported in a few compact *groups* - the walkers' strip, the
    flyers', the scan line, the REC, the dust around the pointer, and one horizontal strip of the
    remaining dust - and the overlay repaints each group on its own. The far-away dust takes its
    turn strip by strip (each mote refreshes ~2 times a second, plenty for something drifting a
    few px a second); bots step at 10 FPS, the classic pixel-art walk-cycle rate. When nobody has
    touched anything for a minute the tick drops to 8 FPS, and the overlay stops the timer outright
    while it is hidden (Peek).

The model is pure apart from painting: it reads no clock (``now`` is passed in; the REC start is
handed over), no global RNG (``rng``), and the local wall clock only through ``local_time``. That
is what lets ``tests/smoke_guard_fx.py`` replay minutes of bots and dust from a seed.

Nothing here touches the desktop image: the fog is painted by the overlay before we are called,
and we only ever paint *over* it.
"""

from __future__ import annotations

import logging
import math
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QRadialGradient, QTransform

from animator import _BODY, _FEET, _HIGHLIGHT, _SHADOW

try:  # world is tiny and Qt-free; guarded only so a broken import can never take the guard down
    from world import PART_DAY, PART_EVENING, PART_MORNING, PART_NIGHT, part_of_day
except Exception:  # pragma: no cover - defensive
    PART_NIGHT, PART_MORNING, PART_DAY, PART_EVENING = "night", "morning", "day", "evening"

    def part_of_day(hour: float) -> str:
        return PART_DAY

log = logging.getLogger(__name__)

# --- palette --------------------------------------------------------------------------------
# Nib's body tones come from animator (same constants the skins remap); outline / light / angry are
# the sheet's palette letters O / L / A in tools/gen_sprites.py (guard_scene uses the same three).
# The cardboard and gear greys are the only colours of our own; the greys are the "charcoal" skin.

_OUTLINE = (0x2C, 0x12, 0x06)
_LIGHT = (0xF5, 0xED, 0xE6)
_ANGRY = (0xC0, 0x39, 0x2B)
_REC_RED = (0xE8, 0x2E, 0x22)
_STAR = (0xFF, 0xF1, 0xC8)  # the light tone, warmed a touch so stars never read as dust

#: Sprite palette letters -> RGB. "." is transparent.
_PALETTE: dict[str, tuple[int, int, int]] = {
    "o": _OUTLINE, "B": _BODY, "H": _HIGHLIGHT, "S": _SHADOW, "F": _FEET, "L": _LIGHT,
    "R": _ANGRY,
    "K": (0xC8, 0x93, 0x4F), "k": (0x9A, 0x6A, 0x35), "t": (0xE6, 0xC8, 0x8A),  # cardboard
    "G": (0x8C, 0x8C, 0x94), "g": (0x4E, 0x4E, 0x56),                          # gear
}

# --- tuning ---------------------------------------------------------------------------------

#: Tick intervals. 20 FPS while someone is around, 8 FPS once input has been quiet for
#: ``IDLE_SLOW_S`` (the same 60 s after which guard_scene's eyes turn sleepy - ``SLEEPY_S`` there;
#: not imported, so a half-edited guard_scene can never take this layer down with it).
FRAME_MS = 50
IDLE_FRAME_MS = 125
IDLE_SLOW_S = 60.0

#: Bots. Speeds are in blocks per second so they read the same on every monitor size. They are
#: redrawn at most every BOT_FRAME_S (10 FPS: pixel-art walk-cycle pace, and half the repaints).
BOT_FRAME_S = 0.1
SPAWN_EVERY_S = (3.5, 8.0)
MAX_BOTS_PRIMARY = 3
MAX_BOTS_SECONDARY = 1
P_FLYER = 0.4
WALK_SPEED = (12.0, 18.0)
FLY_SPEED = (18.0, 28.0)
BOT_OPACITY = 0.46
#: The middle of the screen is where the eyes, Nib and the PIN panel live: bots fade to this share
#: of their opacity inside the centre column, ramping back to full by ``CENTRE_FADE_OUT``.
CENTRE_FADE = 0.3
CENTRE_FADE_IN = 0.20   # |x - centre| / width below this: fully faded
CENTRE_FADE_OUT = 0.34  # ...above this: full opacity

#: Dust. Count per 1920x1080 of screen; secondaries get ``SECONDARY_SHARE`` of it.
MOTES_PER_1080P = 34
SECONDARY_SHARE = 0.55
MOTE_SPEED = (1.5, 4.0)       # blocks/s of base drift
MOTE_SWAY = (0.6, 2.0)        # blocks of side-to-side sway
PUSH_RADIUS_BLOCKS = 42.0     # pointer influence radius
PUSH_ACCEL_BLOCKS = 260.0     # blocks/s^2 at the pointer, falling off to 0 at the radius
PUSH_DAMPING = 1.6            # 1/s: how fast a pushed mote settles back into its drift
PUSH_MAX_BLOCKS = 60.0        # cap on the pushed speed
#: Dust away from the pointer is redrawn one horizontal strip per tick, round robin.
DUST_STRIPS = 8

#: The camera.
SCAN_PERIOD_S = 7.0
SCAN_SWEEP_S = 2.6
SCAN_TAIL_BANDS = 6
REC_BLINK_S = 1.0

#: The ambient glow breathes between GLOW_MIN and 1.0 opacity, in GLOW_LEVELS steps; every step is
#: one full repaint, so the level count *is* the cost knob.
GLOW_PERIOD_S = 16.0
GLOW_LEVELS = 7
GLOW_MIN = 0.45
#: Blackout's red pulse: faster, and much fainter (it sits on solid black).
PULSE_PERIOD_S = 6.0
PULSE_LEVELS = 5
PULSE_MIN = 0.25

#: How often the local clock is consulted for the time-of-day tint.
TOD_CHECK_S = 30.0
STARS_PRIMARY = 14
STARS_SECONDARY = 8

MODES = ("fog", "blackout", "choice")

# --- sprites ----------------------------------------------------------------------------------
# Facing right; mirrored for left. One letter = one block. Kept tiny on purpose: these are extras
# in the background, Nib is the star.

_WALKER = (
    [
        "...R...",
        "...o...",
        ".ooooo.",
        "oHHHHBo",
        "oBBLBLo",
        "oBBBBBo",
        "oSSSSSo",
        ".o...o.",
        ".F...F.",
    ],
    [
        "...R...",
        "...o...",
        ".ooooo.",
        "oHHHHBo",
        "oBBLBLo",
        "oBBBBBo",
        "oSSSSSo",
        "..o.o..",
        "..F.F..",
    ],
)

_WALKER_BOX = (
    [
        ".ooooo.",
        ".KtKKK.",
        ".KtKKK.",
        ".kkkkk.",
        ".o...o.",
        ".ooooo.",
        "oHHHHBo",
        "oBBLBLo",
        "oBBBBBo",
        "oSSSSSo",
        ".o...o.",
        ".F...F.",
    ],
    [
        ".ooooo.",
        ".KtKKK.",
        ".KtKKK.",
        ".kkkkk.",
        ".o...o.",
        ".ooooo.",
        "oHHHHBo",
        "oBBLBLo",
        "oBBBBBo",
        "oSSSSSo",
        "..o.o..",
        "..F.F..",
    ],
)

_PROP = (["ooooooo", "...o..."], ["..ooo..", "...o..."])
_FLYER_BODY = [
    ".ooooo.",
    "oHHHHBo",
    "oBBLBLo",
    "oBBBBBo",
    "oSSSSSo",
    ".ooooo.",
    "...o...",
    "...o...",
]
_CARGO = {
    "box": (
        [".ooooo.", ".KtKKK.", ".KtKKK.", ".kkkkk."],
        [".ooooo.", ".KtKKK.", ".KtKKK.", ".kkkkk."],
    ),
    "gear": (
        ["..g.g..", ".gGGGg.", "..GgG..", ".gGGGg.", "..g.g.."],
        [".g...g.", "..GGG..", ".gGgGg.", "..GGG..", ".g...g."],
    ),
}

# A 3x5 pixel font: just what "REC", "CAM" and a clock need.
_GLYPHS: dict[str, tuple[str, ...]] = {
    "0": ("###", "#.#", "#.#", "#.#", "###"), "1": (".#.", "##.", ".#.", ".#.", "###"),
    "2": ("###", "..#", "###", "#..", "###"), "3": ("###", "..#", ".##", "..#", "###"),
    "4": ("#.#", "#.#", "###", "..#", "..#"), "5": ("###", "#..", "###", "..#", "###"),
    "6": ("###", "#..", "###", "#.#", "###"), "7": ("###", "..#", ".#.", ".#.", ".#."),
    "8": ("###", "#.#", "###", "#.#", "###"), "9": ("###", "#.#", "###", "..#", "###"),
    ":": ("...", ".#.", "...", ".#.", "..."), " ": ("...", "...", "...", "...", "..."),
    "R": ("##.", "#.#", "##.", "#.#", "#.#"), "E": ("###", "#..", "##.", "#..", "###"),
    "C": ("###", "#..", "#..", "#..", "###"), "A": (".#.", "#.#", "###", "#.#", "#.#"),
    "M": ("#.#", "###", "###", "#.#", "#.#"),
}
_DOT = (".##.", "####", "####", ".##.")


def _sprite_pixmap(rows: list[str], b: int, mirrored: bool) -> QPixmap:
    """Render a letter grid to a pixmap at ``b`` px per block."""
    w = max(len(r) for r in rows)
    img = QImage(w * b, len(rows) * b, QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    try:
        for y, row in enumerate(rows):
            for x, ch in enumerate(row):
                rgb = _PALETTE.get(ch)
                if rgb is not None:
                    p.fillRect(x * b, y * b, b, b, QColor(*rgb))
    finally:
        p.end()
    if mirrored:
        img = img.transformed(QTransform().scale(-1, 1))
    return QPixmap.fromImage(img)


def block_for(height: int) -> int:
    """Pixel-art block size for a screen ``height``: Nib's 2 px block times a screen scale.
    Dust, stars, the scan line and the REC text use it (4 px at 1080p)."""
    return 2 * max(1, round(height / 540))


def bot_block_for(height: int) -> int:
    """The bots' block: a size up from :func:`block_for` (6 px at 1080p), so a 7-block robot is
    big enough to read as a robot from across the room and still clearly smaller than Nib."""
    return 2 * max(1, round(height / 360))


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = max(0.0, min(1.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


def format_elapsed(seconds: float) -> str:
    """``MM:SS`` under an hour, ``H:MM:SS`` after."""
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


# --- model pieces -----------------------------------------------------------------------------


class _Dirty:
    """Dirty rectangles collected per group; each group is merged into one rectangle.

    The groups are repainted separately (see the module docstring: a group's cost is its bounding
    box), so a group must only ever hold things that sit close together.
    """

    def __init__(self) -> None:
        self.groups: dict[str, QRect] = {}
        self.loose: list[QRect] = []

    def add(self, group: str, rect: QRect) -> None:
        if rect.isNull():
            return
        cur = self.groups.get(group)
        self.groups[group] = QRect(rect) if cur is None else cur.united(rect)

    def add_loose(self, rect: QRect) -> None:
        if not rect.isNull():
            self.loose.append(QRect(rect))

    def moved(self, group: str, old: QRect, new: QRect, width: int, height: int) -> None:
        """``old`` -> ``new`` in ``group``, unless it wrapped around the screen: a union of the two
        ends would span the whole screen, so the old spot gets a repaint of its own."""
        if not old.isNull() and not new.isNull() and (
                abs(old.x() - new.x()) > width // 2 or abs(old.y() - new.y()) > height // 2):
            self.add_loose(old)
        else:
            self.add(group, old)
        self.add(group, new)

    def rects(self) -> list[QRect]:
        """The groups, with any two merged whose union is barely bigger than the pair (the REC and
        a flyer both up top, say): every separate repaint has a fixed cost too."""
        out = list(self.groups.values()) + self.loose
        merged = True
        while merged and len(out) > 1:
            merged = False
            for i in range(len(out)):
                for j in range(i + 1, len(out)):
                    a, b = out[i], out[j]
                    u = a.united(b)
                    if _area(u) <= (_area(a) + _area(b)) * 1.25 + 4096:
                        out[i] = u
                        del out[j]
                        merged = True
                        break
                if merged:
                    break
        return out


def _area(r: QRect) -> int:
    return max(0, r.width()) * max(0, r.height())


@dataclass
class Bot:
    kind: str            # "walker" | "flyer"
    cargo: str | None    # None | "box" | "gear"
    x: float             # left edge, px
    y: float             # top edge, px (flyers bob around it)
    speed: float         # px/s, signed: + moves right
    born: float
    phase: float = 0.0
    rect: QRect = field(default_factory=QRect)   # last reported (snapped) bounds
    key: tuple = ()                              # what was last drawn there

    @property
    def facing(self) -> int:
        return 1 if self.speed >= 0 else -1


@dataclass
class Mote:
    x: float
    y: float
    vx: float            # base drift, px/s
    vy: float
    sway: float          # px amplitude
    sway_hz: float
    phase: float
    alpha: int
    size: int = 1        # blocks
    px: float = 0.0      # pushed velocity, px/s (decays to 0)
    py: float = 0.0
    rect: QRect = field(default_factory=QRect)


@dataclass
class Star:
    x: int
    y: int
    period: float
    phase: float
    big: bool
    level: int = -1


# --- the effects ------------------------------------------------------------------------------


class BackgroundFx:
    """Model + painter for one overlay's background effects. See the module docstring.

    Driving it: call :meth:`step` once per tick with the clock and the pointer (in overlay
    coordinates); it returns ``(rects, full)`` - the rectangles to ``update()``, or ``full=True``
    when the whole overlay must repaint. Paint with :meth:`paint` from the overlay's
    ``paintEvent``, after the fog. :meth:`frame_ms` says how soon the next tick is wanted.
    """

    def __init__(
        self,
        width: int,
        height: int,
        *,
        rng: random.Random | None = None,
        primary: bool = True,
        started: float = 0.0,
        local_time: Callable[[], datetime] = datetime.now,
        label: str = "",
    ) -> None:
        self._rng = rng or random.Random()
        self.primary = primary
        self.started = started
        self._local_time = local_time
        self.label = label
        self.mode = "fog"
        self.max_bots = MAX_BOTS_PRIMARY if primary else MAX_BOTS_SECONDARY
        self.bots: list[Bot] = []
        self.motes: list[Mote] = []
        self.stars: list[Star] = []
        self.part = PART_DAY
        self.last_input = started
        self._last_now: float | None = None
        self._last_pointer: QPointF | None = None
        self._next_spawn: float | None = None
        self._tick = 0
        self._bots_at = -1e9  # set on the first step: the first one comes soon
        self._next_tod = -1e9
        self.glow_level = -1
        self.scan_rect = QRect()
        self._rec_key: tuple = ()
        self.rec_rect = QRect()
        self._sprites: dict[tuple, QPixmap] = {}
        self._glow: QPixmap | None = None
        self._glow_key: tuple = ()
        self._base: QPixmap | None = None
        self._base_key: tuple = ()
        self.width = self.height = 0
        self.b = 4
        self.bb = 6
        self.set_size(width, height)

    # --- configuration ----------------------------------------------------------------

    def set_size(self, width: int, height: int) -> None:
        width, height = max(0, int(width)), max(0, int(height))
        if (width, height) == (self.width, self.height):
            return
        self.width, self.height = width, height
        self.b = block_for(max(1, height))
        self.bb = bot_block_for(max(1, height))
        self._sprites.clear()
        self._glow = None
        self._base = None
        self.bots.clear()
        self._seed_motes()
        self._seed_stars()

    def set_mode(self, mode: str) -> None:
        if mode in MODES:
            self.mode = mode
            if mode == "blackout":
                # The base holds a copy of the fog; the overlay drops its fog on blackout so that
                # nothing derived from the desktop is kept, and so must we.
                self._base = None

    def set_label(self, label: str) -> None:
        """Rename the camera ("CAM 2"); the stars are re-placed around the wider REC line."""
        if label != self.label:
            self.label = label
            self._seed_stars()

    def pause(self) -> None:
        """The overlay was hidden (Peek): the next step starts a fresh clock instead of a 0.5 s leap."""
        self._last_now = None
        self._last_pointer = None

    def note_input(self, now: float) -> None:
        """Something happened (a key, a click): back to the full frame rate."""
        self.last_input = max(self.last_input, now)

    @property
    def blackout(self) -> bool:
        return self.mode == "blackout"

    @property
    def is_night(self) -> bool:
        return self.part == PART_NIGHT

    def idle_s(self, now: float) -> float:
        return max(0.0, now - self.last_input)

    def frame_ms(self, now: float) -> int:
        """The tick interval wanted right now: 20 FPS when someone is around, 8 FPS otherwise."""
        if self.blackout or self.idle_s(now) >= IDLE_SLOW_S:
            return IDLE_FRAME_MS
        return FRAME_MS

    # --- seeding ----------------------------------------------------------------------

    def _seed_motes(self) -> None:
        r, b = self._rng, self.b
        area = self.width * self.height / (1920 * 1080)
        n = MOTES_PER_1080P * area * (1.0 if self.primary else SECONDARY_SHARE)
        n = 0 if self.width < 8 or self.height < 8 else max(8, min(80, round(n)))
        self.motes = []
        for _ in range(n):
            speed = r.uniform(*MOTE_SPEED) * b
            ang = r.uniform(-math.pi * 0.85, -math.pi * 0.15)  # mostly upward, a little sideways
            self.motes.append(Mote(
                x=r.uniform(0, self.width), y=r.uniform(0, self.height),
                vx=math.cos(ang) * speed, vy=math.sin(ang) * speed * 0.6,
                sway=r.uniform(*MOTE_SWAY) * b, sway_hz=r.uniform(0.08, 0.2),
                phase=r.uniform(0, math.tau), alpha=r.randint(40, 110),
                size=2 if r.random() < 0.3 else 1,
            ))

    def _seed_stars(self) -> None:
        r, b = self._rng, self.b
        n = STARS_PRIMARY if self.primary else STARS_SECONDARY
        self.stars = []
        if self.width < 8 or self.height < 8:
            return
        # Not by the REC line or the unlock link, and not in the face: a star twinkling between
        # the eyes reads as a glint *on* them and pulls the gaze off the pupils.
        avoid = [self._rec_box(), QRect(self.width - self.width // 6, 0, self.width // 6, self.height // 8),
                 QRect(self.width // 4, self.height // 10, self.width // 2, self.height)]
        tries = 0
        while len(self.stars) < n and tries < n * 20:
            tries += 1
            x = int(r.uniform(0.03, 0.97) * self.width) // b * b
            y = int(r.uniform(0.03, 0.42) * self.height) // b * b
            box = QRect(x - 2 * b, y - 2 * b, 5 * b, 5 * b)
            if any(box.intersects(a) for a in avoid):
                continue
            if any(abs(s.x - x) < 12 * b and abs(s.y - y) < 12 * b for s in self.stars):
                continue  # spread them out: clumps look like noise, not sky
            self.stars.append(Star(x, y, period=r.uniform(2.5, 6.0), phase=r.uniform(0, 1),
                                   big=r.random() < 0.35))

    # --- geometry helpers ---------------------------------------------------------------

    def centre_factor(self, cx: float) -> float:
        """Opacity multiplier for something centred at ``cx``: faint in the centre column."""
        if self.width <= 0:
            return 1.0
        d = abs(cx - self.width / 2) / self.width
        return CENTRE_FADE + (1 - CENTRE_FADE) * _smoothstep(CENTRE_FADE_IN, CENTRE_FADE_OUT, d)

    def _margin(self) -> int:
        # Same corner inset family as the overlay's UI (its base unit is height // 55).
        return max(3 * self.b, self.height // 55)

    def _rec_box(self) -> QRect:
        g = max(2, self.b // 2 + 1)  # REC font pixel
        m = self._margin()
        # Dot + "REC  CAM 2  9:59:59" at 4 font pixels a character, plus the drop shadow.
        cols = 6 + 4 * (len("REC  ") + (len(self.label) + 2 if self.label else 0) + len("9:59:59")) + 1
        return QRect(m - g, m - g, cols * g + 2 * g, 8 * g)

    def _sprite_rows(self, bot: Bot, frame: int) -> list[str]:
        if bot.kind == "walker":
            return (_WALKER_BOX if bot.cargo == "box" else _WALKER)[frame]
        rows = list(_PROP[frame]) + _FLYER_BODY
        if bot.cargo:
            rows += _CARGO[bot.cargo][frame if bot.cargo == "gear" else 0]
        return rows

    def _sprite(self, bot: Bot, frame: int) -> QPixmap:
        key = (bot.kind, bot.cargo, frame, bot.facing < 0, self.bb)
        pix = self._sprites.get(key)
        if pix is None:
            pix = _sprite_pixmap(self._sprite_rows(bot, frame), self.bb, bot.facing < 0)
            self._sprites[key] = pix
        return pix

    def _bot_size(self, bot: Bot) -> tuple[int, int]:
        rows = self._sprite_rows(bot, 0)
        return 7 * self.bb, len(rows) * self.bb

    # --- the tick ---------------------------------------------------------------------

    def step(self, now: float, pointer: QPointF | None = None) -> tuple[list[QRect], bool]:
        """Advance to ``now``; return ``(dirty rects, full repaint wanted)``.

        Each returned rectangle is one compact group; repaint them one by one (``repaint(rect)``),
        not merged, or the screen blit grows to their combined bounding box.
        """
        if self._last_now is None:
            self.last_input = max(self.last_input, now)  # being shown counts as activity
        dt = 0.0 if self._last_now is None else max(0.0, min(now - self._last_now, 0.5))
        self._last_now = now
        if pointer is not None:
            if self._last_pointer is not None and (
                    abs(pointer.x() - self._last_pointer.x()) + abs(pointer.y() - self._last_pointer.y()) > 1.0):
                self.last_input = max(self.last_input, now)
            self._last_pointer = QPointF(pointer)
        if self.width < 8 or self.height < 8:
            return [], False

        dirty = _Dirty()
        full = False

        if now >= self._next_tod:
            self._next_tod = now + TOD_CHECK_S
            try:
                t = self._local_time()
                part = part_of_day(t.hour + t.minute / 60.0)
            except Exception:
                log.debug("local time unavailable", exc_info=True)
                part = self.part
            if part != self.part:
                self.part = part
                self._glow = None
                full = True

        level = self._glow_level_at(now)
        if level != self.glow_level:
            self.glow_level = level
            full = True

        if self.blackout:
            # Nothing moves in the dark: drop the bots, forget where the dust was drawn. (Going
            # dark is a full repaint anyway - the overlay's set_mode - these are belt and braces.)
            for bot in self.bots:
                dirty.add_loose(bot.rect)
            self.bots.clear()
            for m in self.motes:
                dirty.add_loose(m.rect)
                m.rect = QRect()
            dirty.add_loose(self.scan_rect)
            self.scan_rect = QRect()
            for s in self.stars:
                s.level = -1
        else:
            strip = self._tick % DUST_STRIPS
            self._step_bots(now, dt, dirty)
            self._step_motes(now, dt, pointer, dirty, strip)
            self._step_scan(now, dirty)
            if self.is_night:
                self._step_stars(now, dirty, strip)

        self._step_rec(now, dirty)
        self._tick += 1
        return dirty.rects(), full

    def _glow_level_at(self, now: float) -> int:
        period, levels = (PULSE_PERIOD_S, PULSE_LEVELS) if self.blackout else (GLOW_PERIOD_S, GLOW_LEVELS)
        wave = 0.5 - 0.5 * math.cos(math.tau * ((now - self.started) / period))
        return min(levels - 1, int(wave * levels))

    def glow_opacity(self) -> float:
        lo, levels = (PULSE_MIN, PULSE_LEVELS) if self.blackout else (GLOW_MIN, GLOW_LEVELS)
        level = max(0, self.glow_level)
        return lo + (1.0 - lo) * level / max(1, levels - 1)

    # bots

    def spawn_bot(self, now: float) -> Bot | None:
        """Add one bot now (if there is room). Public for tests."""
        if len(self.bots) >= self.max_bots or self.blackout:
            return None
        r, b, w, h = self._rng, self.bb, self.width, self.height
        flyer = r.random() < P_FLYER
        direction = 1 if r.random() < 0.5 else -1
        if flyer:
            cargo = r.choice(("box", "gear", None))
            bot = Bot("flyer", cargo, 0, 0, direction * r.uniform(*FLY_SPEED) * b, now,
                      phase=r.uniform(0, math.tau))
            bw, bh = self._bot_size(bot)
            # The top band, under the REC line and above where the eyes usually start.
            top = max(self._rec_box().bottom() + 2 * self.b, int(h * 0.05))
            bot.y = r.uniform(top, max(top, h * 0.15))
        else:
            cargo = "box" if r.random() < 0.5 else None
            bot = Bot("walker", cargo, 0, 0, direction * r.uniform(*WALK_SPEED) * b, now)
            bw, bh = self._bot_size(bot)
            bot.y = h - bh - max(b, h // 120)  # feet just above the bottom edge
        self._keep_spacing(bot, bw)
        bot.x = -bw if bot.speed > 0 else w
        self.bots.append(bot)
        return bot

    def _keep_spacing(self, bot: Bot, bw: int) -> None:
        """Stop a new bot from walking *through* one of its own kind: two walkers merged into one
        eight-legged blob read as a glitch, not as busy workers. Crossing in opposite directions
        is fine (it is brief and reads as passing), so: enter from the other side when the near
        entrance is still occupied, and never move faster than a same-way bot ahead - a convoy."""
        def same_way(speed: float) -> list[Bot]:
            return [o for o in self.bots if o.kind == bot.kind and (o.speed > 0) == (speed > 0)]

        def crowded(speed: float) -> bool:
            entry = -bw if speed > 0 else self.width
            return any(abs(o.x - entry) < 3 * bw for o in same_way(speed))

        if crowded(bot.speed) and not crowded(-bot.speed):
            bot.speed = -bot.speed
        ahead = same_way(bot.speed)
        if ahead:
            slowest = min(abs(o.speed) for o in ahead)
            bot.speed = math.copysign(min(abs(bot.speed), slowest), bot.speed)

    def _step_bots(self, now: float, dt: float, dirty: _Dirty) -> None:
        if self._next_spawn is None:
            self._next_spawn = now + self._rng.uniform(0.6, 2.0)
        if now >= self._next_spawn:
            self._next_spawn = now + self._rng.uniform(*SPAWN_EVERY_S)
            self.spawn_bot(now)
        b = self.bb
        redraw = now - self._bots_at >= BOT_FRAME_S - 1e-6
        if redraw:
            self._bots_at = now
        alive: list[Bot] = []
        for bot in self.bots:
            bot.x += bot.speed * dt
            bw, bh = self._bot_size(bot)
            group = "walk" if bot.kind == "walker" else "fly"
            if bot.x > self.width + b or bot.x + bw < -b:
                dirty.add(group, bot.rect)
                continue
            if redraw:
                rect, key = self._bot_rect(bot, now)
                if key != bot.key:
                    dirty.add(group, bot.rect)
                    dirty.add(group, rect)
                    bot.rect, bot.key = rect, key
            alive.append(bot)
        self.bots = alive

    def _bot_rect(self, bot: Bot, now: float) -> tuple[QRect, tuple]:
        b = self.bb
        bw, bh = self._bot_size(bot)
        x = int(bot.x) // b * b
        y = int(bot.y)
        if bot.kind == "walker":
            frame = int(abs(bot.x - (-bw if bot.speed > 0 else self.width)) / (3 * b)) % 2
            bob = 0
        else:
            frame = int((now - bot.born) * 10) % 2   # propeller at 10 changes a second
            bob = round(math.sin((now - bot.born) * 2.2 + bot.phase) * 1.5) * b
        y = y // b * b + bob
        # The opacity bucket is part of the key, so the centre fade repaints as it changes.
        fade = round(self.centre_factor(x + bw / 2) * 8)
        return QRect(x, y, bw, bh), (x, y, frame, fade)

    # dust

    def _step_motes(self, now: float, dt: float, pointer: QPointF | None, dirty: _Dirty,
                    strip: int) -> None:
        """Move every mote; redraw the ones near the pointer (or still flying from a push) every
        tick, and the rest only when their strip's turn comes round."""
        b, w, h = self.b, self.width, self.height
        radius = PUSH_RADIUS_BLOCKS * b
        accel = PUSH_ACCEL_BLOCKS * b
        cap = PUSH_MAX_BLOCKS * b
        damp = math.exp(-PUSH_DAMPING * dt)
        px = py = None
        if pointer is not None and 0 <= pointer.x() <= w and 0 <= pointer.y() <= h:
            px, py = pointer.x(), pointer.y()
        strip_h = -(-h // DUST_STRIPS)
        for m in self.motes:
            close = False
            if px is not None:
                dx, dy = m.x - px, m.y - py
                d = math.hypot(dx, dy)
                close = d < radius * 1.2
                if d < radius:
                    if d < 1e-3:
                        dx, dy, d = 1.0, 0.0, 1.0
                    push = accel * (1.0 - d / radius) * dt
                    m.px += dx / d * push
                    m.py += dy / d * push
                    sp = math.hypot(m.px, m.py)
                    if sp > cap:
                        m.px, m.py = m.px / sp * cap, m.py / sp * cap
            m.px *= damp
            m.py *= damp
            m.x = (m.x + (m.vx + m.px) * dt) % w
            m.y = (m.y + (m.vy + m.py) * dt) % h
            flying = abs(m.px) + abs(m.py) > 2 * b
            if close or flying:
                group = "pointer"
            elif int(m.y) // strip_h == strip:
                group = "dust"
            else:
                continue
            rect = self._mote_rect(m, now)
            if rect != m.rect:
                dirty.moved(group, m.rect, rect, w, h)
                m.rect = rect

    def _mote_rect(self, m: Mote, now: float) -> QRect:
        b = self.b
        sx = m.x + m.sway * math.sin(m.phase + math.tau * m.sway_hz * now)
        x = int(sx) // b * b
        y = int(m.y) // b * b
        return QRect(x, y, b, b) if m.size == 1 else QRect(x, y, 3 * b, 3 * b)

    # camera

    def scan_y(self, now: float) -> int | None:
        """Top of the scan line at ``now``, or None between sweeps."""
        t = (now - self.started) % SCAN_PERIOD_S
        if t >= SCAN_SWEEP_S:
            return None
        travel = self.height + SCAN_TAIL_BANDS * 2 * self.b
        return int(t / SCAN_SWEEP_S * travel) // self.b * self.b

    def _scan_box(self, y: int) -> QRect:
        tail = SCAN_TAIL_BANDS * 2 * self.b
        return QRect(0, y - tail, self.width, tail + self.b)

    def _step_scan(self, now: float, dirty: _Dirty) -> None:
        y = self.scan_y(now)
        box = QRect() if y is None else self._scan_box(y)
        if box != self.scan_rect:
            dirty.moved("scan", self.scan_rect, box, self.width, self.height)
            self.scan_rect = box

    def rec_on(self, now: float) -> bool:
        return ((now - self.started) % REC_BLINK_S) < REC_BLINK_S * 0.6

    def rec_text(self, now: float) -> str:
        cam = f"{self.label}  " if self.label else ""
        return f"REC  {cam}{format_elapsed(now - self.started)}"

    def _step_rec(self, now: float, dirty: _Dirty) -> None:
        key = (self.rec_on(now), self.rec_text(now))
        if key != self._rec_key:
            self._rec_key = key
            self.rec_rect = self._rec_box()
            dirty.add("rec", self.rec_rect)

    # stars

    def _star_level(self, s: Star, now: float) -> int:
        t = ((now / s.period) + s.phase) % 1.0
        wave = 0.5 - 0.5 * math.cos(math.tau * t)
        return 0 if wave < 0.3 else 1 if wave < 0.8 else 2

    def _star_rect(self, s: Star) -> QRect:
        b = self.b
        return QRect(s.x - 2 * b, s.y - 2 * b, 5 * b, 5 * b)

    def _step_stars(self, now: float, dirty: _Dirty, strip: int) -> None:
        # Stars twinkle on the dust's strip schedule: same compact repaint, no extra blits.
        strip_h = -(-self.height // DUST_STRIPS)
        for s in self.stars:
            if s.y // strip_h != strip and s.level >= 0:
                continue
            level = self._star_level(s, now)
            if level != s.level:
                dirty.add("dust" if s.level >= 0 else "stars", self._star_rect(s))
                s.level = level

    # --- painting -------------------------------------------------------------------------

    def _tint(self) -> QColor | None:
        if self.blackout:
            return None
        return {
            PART_MORNING: QColor(255, 226, 170, 14),
            PART_EVENING: QColor(255, 128, 60, 30),
            PART_NIGHT: QColor(8, 14, 48, 92),
        }.get(self.part)

    def _glow_pixmap(self) -> QPixmap:
        key = (self.width, self.height, self.part, self.blackout)
        if self._glow is not None and self._glow_key == key:
            return self._glow
        self._glow_key = key
        self._glow = self._build_glow()
        return self._glow

    def _build_glow(self) -> QPixmap:
        """The glow at full brightness, at half resolution (it is nothing but soft gradients, and
        it is drawn smooth-scaled into the base). Built once per (size, time of day, blackout)."""
        w, h = self.width, self.height
        img = QImage(max(1, w // 2), max(1, h // 2), QImage.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        try:
            p.setPen(Qt.NoPen)
            p.scale(img.width() / w, img.height() / h)

            def blob(cx: float, cy: float, rx: float, ry: float, rgba: tuple[int, int, int, int]) -> None:
                # An elliptical radial gradient: a circle squashed by the painter transform.
                p.save()
                p.translate(cx, cy)
                p.scale(1.0, ry / rx)
                g = QRadialGradient(QPointF(0, 0), rx)
                # A smooth (1 - t^2)^2 falloff in eight stops: two linear stops leave a visible
                # ring where the slope changes, which on black (blackout) is all you would see.
                for i in range(9):
                    t = i / 8
                    g.setColorAt(t, QColor(rgba[0], rgba[1], rgba[2], round(rgba[3] * (1 - t * t) ** 2)))
                p.setBrush(g)
                p.drawEllipse(QRectF(-rx, -rx, 2 * rx, 2 * rx))
                p.restore()

            if self.blackout:
                # Low and wide, behind where the red eyes sit: a heartbeat in the dark.
                blob(w * 0.5, h * 0.36, w * 0.42, h * 0.38, (190, 14, 8, 40))
                blob(w * 0.5, h * 0.36, w * 0.16, h * 0.14, (255, 30, 16, 26))
            else:
                bottom, top_a, top_b = {
                    PART_MORNING: ((246, 172, 92, 70), (150, 200, 235, 50), (255, 214, 150, 40)),
                    PART_DAY: ((217, 85, 46, 72), (70, 180, 190, 46), (126, 96, 210, 42)),
                    PART_EVENING: ((236, 108, 46, 92), (214, 78, 132, 56), (128, 70, 180, 48)),
                    PART_NIGHT: ((217, 85, 46, 44), (40, 160, 150, 58), (88, 70, 200, 60)),
                }[self.part if self.part in (PART_MORNING, PART_DAY, PART_EVENING, PART_NIGHT) else PART_DAY]
                blob(w * 0.5, h * 1.02, w * 0.62, h * 0.42, bottom)    # warm floor glow under the stage
                blob(w * 0.24, h * 0.06, w * 0.40, h * 0.16, top_a)    # the aurora: two bands up top
                blob(w * 0.74, h * 0.10, w * 0.42, h * 0.15, top_b)
        finally:
            p.end()
        return QPixmap.fromImage(img)

    def _base_pixmap(self, fog: QPixmap | None) -> QPixmap:
        """Fog (or black) + time-of-day tint + glow at its current level, composed once."""
        key = (None if self.blackout or fog is None else fog.cacheKey(), self.blackout,
               max(0, self.glow_level), self.part, self.width, self.height)
        if self._base is not None and self._base_key == key:
            return self._base
        base = QPixmap(self.width, self.height)
        p = QPainter(base)
        try:
            if self.blackout:
                base.fill(Qt.black)
            elif fog is not None and not fog.isNull():
                p.drawPixmap(base.rect(), fog)
            else:
                base.fill(QColor(118, 120, 128))  # guard_overlay's plain haze
            tint = self._tint()
            if tint is not None:
                p.fillRect(base.rect(), tint)
            p.setOpacity(self.glow_opacity())
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.drawPixmap(base.rect(), self._glow_pixmap())
        finally:
            p.end()
        self._base, self._base_key = base, key
        return base

    def paint_base(self, p: QPainter, dirty: QRect, fog: QPixmap | None) -> bool:
        """Paint the background under ``dirty``: ``fog`` (None in blackout, or when there is no fog
        yet) with the tint and the glow composed in. Replaces the overlay's own fog/black fill;
        returns False when there is nothing to paint with (a zero-size screen), so the caller
        falls back to its plain fill."""
        if self.width < 8 or self.height < 8:
            return False
        dirty = dirty.intersected(QRect(0, 0, self.width, self.height))
        if not dirty.isEmpty():
            p.drawPixmap(dirty, self._base_pixmap(fog), dirty)
        return True

    def paint(self, p: QPainter, dirty: QRect, now: float | None = None) -> None:
        """Paint the moving things that touch ``dirty``. Call after :meth:`paint_base`.

        ``now`` defaults to the last :meth:`step`'s time, and the overlay must leave it at that:
        the dirty rectangles were computed for that instant, so painting the scan line or a star
        for any other instant would leave stale pixels outside the rectangles that get repainted.
        """
        if self.width < 8 or self.height < 8:
            return
        if now is None:
            now = self._last_now if self._last_now is not None else self.started
        dirty = dirty.intersected(QRect(0, 0, self.width, self.height))
        if dirty.isEmpty():
            return
        p.save()
        try:
            if not self.blackout:
                if self.is_night:
                    self._paint_stars(p, dirty, now)
                self._paint_scan(p, dirty, now)
                self._paint_motes(p, dirty)
                self._paint_bots(p, dirty, now)
            self._paint_rec(p, dirty, now)
        finally:
            p.restore()

    def _paint_stars(self, p: QPainter, dirty: QRect, now: float) -> None:
        b = self.b
        for s in self.stars:
            if not self._star_rect(s).intersects(dirty):
                continue
            # A four-point twinkle: the arms grow and shrink with the level (0 = just a glint).
            # The level the last step settled on, not one for "now": a partial repaint must draw
            # exactly what the last full repaint of this star's rectangle drew.
            level = s.level
            if level < 0:
                continue
            p.fillRect(s.x, s.y, b, b, QColor(*_STAR, (110, 190, 245)[level]))
            arms = level + (1 if s.big else 0) - 1
            for i in range(1, min(2, arms) + 1):
                arm = QColor(*_STAR, (120 if i == 1 else 55) if level == 2 else 70)
                p.fillRect(s.x - i * b, s.y, b, b, arm)
                p.fillRect(s.x + i * b, s.y, b, b, arm)
                p.fillRect(s.x, s.y - i * b, b, b, arm)
                p.fillRect(s.x, s.y + i * b, b, b, arm)

    def _paint_scan(self, p: QPainter, dirty: QRect, now: float) -> None:
        y = self.scan_y(now)
        if y is None or not self._scan_box(y).intersects(dirty):
            return
        b = self.b
        # Stepped bands rather than a smooth gradient: it's pixel art, and it's cheaper.
        for i in range(SCAN_TAIL_BANDS):
            alpha = int(4 + 16 * (i + 1) / SCAN_TAIL_BANDS)
            top = y - (SCAN_TAIL_BANDS - i) * 2 * b
            p.fillRect(QRect(0, top, self.width, 2 * b), QColor(*_LIGHT, alpha))
        p.fillRect(QRect(0, y, self.width, b), QColor(*_LIGHT, 58))

    def _paint_motes(self, p: QPainter, dirty: QRect) -> None:
        b = self.b
        for m in self.motes:
            r = m.rect
            if r.isNull() or not r.intersects(dirty):
                continue
            if m.size == 1:
                p.fillRect(r, QColor(*_LIGHT, m.alpha))
            else:
                # A bigger fleck: a bright block with a faint halo cross, 3x3 blocks in all.
                halo = QColor(*_LIGHT, m.alpha // 3)
                cx, cy = r.x() + b, r.y() + b
                p.fillRect(cx, cy, b, b, QColor(*_LIGHT, m.alpha))
                p.fillRect(cx - b, cy, b, b, halo)
                p.fillRect(cx + b, cy, b, b, halo)
                p.fillRect(cx, cy - b, b, b, halo)
                p.fillRect(cx, cy + b, b, b, halo)

    def _paint_bots(self, p: QPainter, dirty: QRect, now: float) -> None:
        b = self.bb
        for bot in self.bots:
            if bot.rect.isNull() or not bot.rect.intersects(dirty):
                continue
            frame = bot.key[2] if bot.key else 0
            pix = self._sprite(bot, frame)
            p.setOpacity(BOT_OPACITY * self.centre_factor(bot.rect.center().x()))
            # A soft contact shadow under walkers, one block tall: they stand *on* something.
            if bot.kind == "walker":
                p.fillRect(QRect(bot.rect.x() + b, bot.rect.bottom() + 1, bot.rect.width() - 2 * b,
                                 max(1, b // 2)), QColor(0, 0, 0, 60))
            p.drawPixmap(bot.rect.topLeft(), pix)
        p.setOpacity(1.0)

    def _paint_rec(self, p: QPainter, dirty: QRect, now: float) -> None:
        box = self._rec_box()
        if not box.intersects(dirty):
            return
        g = max(2, self.b // 2 + 1)
        m = self._margin()
        x, y = m, m
        shadow = QColor(0, 0, 0, 110)
        if self.rec_on(now):
            red = QColor(*_REC_RED, 235)
            for ry, row in enumerate(_DOT):
                for rx, ch in enumerate(row):
                    if ch == "#":
                        p.fillRect(x + rx * g + g // 2, y + ry * g + g // 2 + 1, g, g, shadow)
                        p.fillRect(x + rx * g, y + ry * g + g // 2, g, g, red)
        x += 6 * g
        # In blackout the caption goes dim red with the eyes: a grey label would be the only
        # neutral light on an otherwise black-and-red screen.
        colour = QColor(*_LIGHT, 170) if not self.blackout else QColor(*_REC_RED, 120)
        for ch in self.rec_text(now):
            glyph = _GLYPHS.get(ch)
            if glyph is None:
                continue
            for gy, row in enumerate(glyph):
                for gx, c in enumerate(row):
                    if c == "#":
                        p.fillRect(x + gx * g + g // 2, y + gy * g + g // 2, g, g, shadow)
                        p.fillRect(x + gx * g, y + gy * g, g, g, colour)
            x += 4 * g
