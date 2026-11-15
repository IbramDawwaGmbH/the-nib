"""Christmas snow: the piles on your windows, and the snow you knock off them.

Snow lives in *caps* on the top edges of windows. A cap is a height map - one height per narrow
column, in physical px - with an overhang past each side of the window, a few icicles hanging from
the overhangs, and a lip that droops a few px into the window. Caps grow slowly and silently; there
is no snowfall and nothing is ever drawn falling unless you put it in the air:

*   **Brush**: a fast sideways pass of the pointer through a pile knocks off the snow above the
    pointer's line, as chunks that fly off sideways and fall down the front of the window.
*   **Flick**: a fast upward pass through a pile throws the snow it went through into the air with
    the pointer's velocity (clamped); it comes back down on whatever is underneath.
*   A slow pointer - someone heading for a button - never touches anything.

Airborne chunks fall under gravity and land on a cap (adding to it), on the floor (the work area's
bottom, where they are gone), or on Nib, whose rectangle the view hands in.

This module is only the model, the way :class:`guard_fx.BackgroundFx` is the guard's: it draws
nothing and imports no Qt. It reads no clock (every call takes ``now_ms``) and no global RNG (the
field takes an ``rng``; each cap's own shape and growth noise come from ``random.Random(hwnd)``, so
a window keeps the same look and a test replays exactly). That is what lets
``tests/smoke_snow.py`` simulate twenty minutes of growth and a hundred thrown chunks in no time.

Why it is shaped the way it is - the cost model (measured in WP0, see
``plans/CHRISTMAS-SNOW-PLAN.md`` 1.6):

    A resting cap is a strip window with a pre-rendered pixmap: it costs nothing at all until its
    shape changes, and then one re-render (~1.5 ms) and one small repaint (~0.5 ms). So the model
    reports *which caps changed* (``Changes.caps``, and ``Cap.version`` for a pixmap cache) and
    never anything per frame for a cap that did not.

    Airborne snow is painted on a temporary full-monitor flight layer, where a whole-window repaint
    costs 4-9 ms (almost all of it the layered-window flush) but a dirty box a fraction of a ms. So
    the model reports the chunks' dirty boxes per monitor, grouped by the stroke that threw them -
    a merged box over two bursts at opposite ends of a monitor would be a near full-window flush.
    The boxes cover where each chunk was *and* is, so a chunk that lands is erased exactly once.

    The animation timer only runs while something is airborne (:attr:`SnowField.airborne`); at rest
    there are zero ticks. Growth is a separate, slow call (:meth:`SnowField.grow`) that advances at
    most one step per cap per call, so it counts time the snow was *showing* - the view simply does
    not call it while snow is hidden, static or on battery - and a long gap never catches up in one
    lurch.

    The budget (``CHUNK_BUDGET`` per monitor, oldest fade first) bounds the flight layer's cost: 100
    chunks at 30 FPS measured ~8 % of one core. A chunk's *mass* is independent of its drawn size
    (a wide cap holds more snow than a hundred lumps could show), and every gram is accounted for
    in :attr:`SnowField.ledger`, so a test can check snow is neither made nor lost by accident.

Coordinates are physical px, virtual-desktop absolute (a monitor may sit at a negative origin), and
rectangles are ``(left, top, right, bottom)`` like everything in :mod:`win_integration`. Sizes
below are at 96 DPI and scaled by each cap's ``scale``. Velocities are px/s, times ms.

The desktop comes in through :meth:`SnowField.sync`, handed ``win_integration.list_window_stack()``
at 2 Hz: it gives new windows caps, makes a moved, closed, minimised or maximised window's pile
fall and a resized one's cut-off span, fills each cap's ``covered`` spans and ``occluders`` from
the windows in front, and names the monitors something covers whole. :func:`clip` turns a cap's
extent and occluders into the rectangles the view may draw.

Not here yet, by design: painting (WP3), the growth timing measurement (WP4).
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

Rect = tuple[int, int, int, int]

# --- the cap's shape (96-DPI px, scaled per cap) ----------------------------------------------

#: Width of one height-map column. A few px, well below the look's detail, so the view can draw a
#: smooth mound from it rather than steps.
COLUMN_PX = 3.0

#: Full grown height in the middle of a cap (the plan's 24-30 px).
CAP_PX = 26.0

#: Landed snow may pile above the grown height up to this, so a throw visibly adds to a cap; a
#: chunk that finds no room there keeps falling rather than vanishing.
CAP_MAX_PX = 39.0

#: Overhang past each side of the window, seeded per window.
OVERHANG_PX = (10.0, 20.0)

#: The ends of a cap round off over about its own height: quarter-circle corners.
CORNER_PX = 24.0

#: A gentle seeded unevenness along the top, as a fraction of the height.
WAVE = 0.08

#: How far the snow droops into the window below its top edge at full height, and the zone at the
#: right-hand end where it never does: three 46 px caption buttons (minimise, maximise, close).
LIP_PX = 3.0
CAPTION_ZONE_PX = 138.0

#: Icicles: per overhang, their full lengths and widths, seeded per window. Each hangs from an
#: overhang column outside the window and needs that column to hold snow (``ICICLE_SUPPORT``) or
#: it snaps off.
ICICLES_PER_SIDE = (2, 4)
ICICLE_LEN_PX = (8.0, 28.0)
ICICLE_W_PX = (3.0, 6.0)
ICICLE_STEP_PX = 2.0
ICICLE_GROW_FILL = 0.6      # the overhang above must be this full before an icicle lengthens
ICICLE_SUPPORT_PX = 2.0

# --- growth ------------------------------------------------------------------------------------

#: One growth step per cap per this, so a cap fills in ``GROWTH_STEPS`` calls: about 20 minutes of
#: showing. Each step is one pixmap re-render; never animated, no flakes. (Placeholder until WP4
#: measures it in a rate script.)
GROWTH_STEP_MS = 30_000
GROWTH_STEPS = 40
GROWTH_NOISE = 0.25          # each column's step is +-25 %, so the top fills in unevenly
ICICLE_STEP_MS = 180_000

# --- gestures ----------------------------------------------------------------------------------

#: A deliberate pass: faster than this, in px/ms (plan 1.1, "above ~0.8 px/ms sideways").
BRUSH_SPEED = 0.8
FLICK_SPEED = 0.8

#: Pointer speed is measured over at least this long a stretch of samples, and the stretch must
#: travel at least ``MIN_TRAVEL_PX``: two samples a millisecond apart and 1 px apart read as
#: 1 px/ms, which is jitter, not a swipe.
VELOCITY_WINDOW_MS = 16
MIN_SPAN_MS = 4
MIN_TRAVEL_PX = 4.0

#: Samples further apart than this are not one movement (the pointer is not polled at rest, so the
#: first sample after a pause may be anywhere): history is dropped and nothing is brushed.
MAX_SAMPLE_GAP_MS = 100

#: A brush reaches this far below the snow line (into the lip); the band right around the pointer
#: is knocked off with what is above it.
BRUSH_REACH_PX = 2.0
BRUSH_BAND_PX = 2.0

#: A flick scoops this far either side of where the pointer went up through the pile.
FLICK_HALF_W_PX = 9.0

# --- chunks ------------------------------------------------------------------------------------

#: Per monitor (plan 1.6: 100 chunks at 30 FPS cost ~8 % of one core on the flight layer).
CHUNK_BUDGET = 100

#: Drawn diameter range; the mass of a typical lump (a 12 px disc) sets how many a release makes.
CHUNK_PX = (6.0, 18.0)
TYPICAL_CHUNK_MASS = math.pi * 6.0 * 6.0
MAX_PER_RELEASE = 40

GRAVITY_PX_S2 = 2000.0
TERMINAL_PX_S = 1000.0
MAX_THROW_PX_S = 650.0       # plan 1.6: "thrown up to ~650 px/s"
BRUSH_CARRY = 0.5            # a brushed chunk leaves at this fraction of the pointer's speed
BRUSH_MAX_PX_S = 700.0
BRUSH_POP_PX_S = (60.0, 220.0)
JITTER_PX_S = 40.0
AIR_DRAG = 0.6               # per second, on the horizontal speed only
MAX_STEP_MS = 100            # a stalled timer does not teleport chunks
SUBSTEP_MS = 34
MAX_AIR_MS = 12_000          # belt and braces: nothing stays airborne forever

#: Frame rate while anything is airborne (plan 1.4/1.6); the timer is stopped otherwise.
FPS = 30
FPS_BATTERY = 20

# --- the window stack (:meth:`SnowField.sync`) -------------------------------------------------

#: A window smaller than this gets no pile (it still hides the piles behind it): menus, tooltips
#: and toasts come and go too fast to be worth snowing on, and a pile wider than its window would
#: look silly. 96-DPI px, scaled by the monitor's.
CAP_MIN_W_PX = 120.0
CAP_MIN_H_PX = 60.0

#: At most this many piles at once (plan section 5: a strip window each, ~0.8 MB). When there are
#: more windows than that, the ones whose top edge shows most go first (the pile is what shows,
#: not the window), then the largest; a window that already has a pile keeps it, unless all of its
#: top edge is behind other windows and a window whose top shows is waiting for the place.
MAX_CAPS = 30

#: A window in front hides a column of a pile when it reaches down into this band above the
#: window's top edge - the lower half of a grown pile, where the snow mostly is. One that only
#: grazes the top of a tall pile leaves it brushable; the view clips the drawing exactly anyway
#: (:attr:`Cap.occluders`, :func:`clip`), this is only what brushing and landing go by.
COVER_BAND_PX = CAP_PX / 2.0

#: A pile holding less than this fraction of its full grown snow does not fall when its window
#: moves, closes or is maximised: it simply goes. That is what keeps a window dragged across the
#: screen from spraying chunks on every 2 Hz pass - the pile it regrows at each new spot is bare
#: (``RESPAWN_HOLD_MS``), so only the first pass, the one that tears the real pile off, throws
#: anything. 0.1 is four growth steps, about two minutes of showing.
QUIET_FRACTION = 0.1

#: A pile that just moved does not grow again for this long, whatever its growth schedule was, so
#: a slow drag does not grow a little pile at each stop and throw it at the next.
RESPAWN_HOLD_MS = GROWTH_STEP_MS

#: When this many piles' windows vanish in one pass, and more than half of all piles, it is a
#: switch of virtual desktop (every window cloaked at once) or a session lock, not someone closing
#: windows: the piles go quietly instead of throwing the whole budget over the new desktop.
MASS_EXIT_CAPS = 3

_EPS = 1e-9


@dataclass
class Icicle:
    x: float                 # centre, absolute px
    width: float
    max_len: float
    length: float            # current; 0 is "not there"
    column: int              # the overhang column it hangs from
    side: str                # "left" | "right"

    @property
    def mass(self) -> float:
        return 0.5 * self.width * self.length


class Cap:
    """The snow on one window's top edge. Built by :meth:`SnowField.add_cap`.

    ``heights[i]`` is the snow over ``[x0 + i * col, x0 + (i + 1) * col)``, standing on the line
    ``y = top``; ``target[i]`` is what growth fills it to. ``covered`` is a list of absolute
    ``(x0, x1)`` spans hidden behind other windows (:meth:`SnowField.sync` fills it, by
    ``COVER_BAND_PX``): nothing there is brushed or landed on. ``occluders`` are the rectangles of
    the windows in front, cut to :meth:`extent`, for the view's clip (:func:`clip`). ``version``
    goes up on every change of shape, for the view's pixmap cache, and carries on across a move or
    resize of the window (a new :class:`Cap` under the same handle), so a cache keyed on
    ``(hwnd, version)`` never serves the old shape.
    """

    def __init__(self, hwnd: int, rect: Rect, now_ms: float, scale: float = 1.0,
                 fill: float = 0.0) -> None:
        self.hwnd = int(hwnd)
        self.rect = tuple(int(v) for v in rect)
        self.scale = float(scale)
        s = self.scale
        left, top, right, _bottom = self.rect
        self.top = float(top)
        # The window's own look comes from its handle, never the field's rng, so it is the same
        # however many other caps exist or in which order they were made.
        self.rng = random.Random(self.hwnd)
        r = self.rng
        self.overhang = (r.uniform(*OVERHANG_PX) * s, r.uniform(*OVERHANG_PX) * s)
        self.col = COLUMN_PX * s
        self.x0 = left - self.overhang[0]
        self.x1 = right + self.overhang[1]
        n = max(1, math.ceil((self.x1 - self.x0) / self.col))
        self.n = n

        phase, freq = r.uniform(0.0, math.tau), r.uniform(1.5, 3.5)
        radius = CORNER_PX * s
        span = self.x1 - self.x0
        self.target: list[float] = []
        self.lip_max: list[float] = []
        caption_from = right - CAPTION_ZONE_PX * s
        for i in range(n):
            cx = self.x0 + (i + 0.5) * self.col
            d = min(cx - self.x0, self.x1 - cx)
            taper = 1.0 if d >= radius else math.sqrt(max(0.0, 1.0 - (1.0 - d / radius) ** 2))
            wave = 1.0 + WAVE * math.sin(phase + freq * math.tau * (cx - self.x0) / max(1.0, span))
            self.target.append(CAP_PX * s * taper * wave)
            inside = left <= cx < right and cx < caption_from
            self.lip_max.append(LIP_PX * s if inside else 0.0)
        self.heights = [t * max(0.0, min(1.0, fill)) for t in self.target]

        self.icicles: list[Icicle] = []
        for side in ("left", "right"):
            over = self.overhang[0] if side == "left" else self.overhang[1]
            edge = left if side == "left" else right
            count = r.randint(*ICICLES_PER_SIDE)
            for k in range(count):
                frac = (k + 0.5 + r.uniform(-0.25, 0.25)) / count
                x = edge - over * frac if side == "left" else edge + over * frac
                if self.column_at(x) is None:
                    continue
                max_len = r.uniform(*ICICLE_LEN_PX) * s
                width = r.uniform(*ICICLE_W_PX) * s
                # The whole icicle hangs clear of the window, not just its centre: one over the
                # window's own frame would look like it grew on the title bar.
                x = min(x, edge - width / 2) if side == "left" else max(x, edge + width / 2)
                col = self.column_at(x)
                if col is None:
                    continue
                self.icicles.append(Icicle(x, width, max_len, max_len * fill, col, side))

        self.covered: list[tuple[float, float]] = []
        self.occluders: list[Rect] = []
        self.version = 0
        self.next_grow_ms = now_ms + r.uniform(0.0, GROWTH_STEP_MS)  # caps never step together
        self.next_icicle_ms = now_ms + ICICLE_STEP_MS

    # geometry

    def column_at(self, x: float) -> int | None:
        i = math.floor((x - self.x0) / self.col)
        return i if 0 <= i < self.n else None

    def column_center(self, i: int) -> float:
        return self.x0 + (i + 0.5) * self.col

    def lip(self, i: int) -> float:
        """How far column ``i`` droops into the window now (0 over the caption buttons)."""
        t = self.target[i]
        return self.lip_max[i] * min(1.0, self.heights[i] / t) if t > _EPS else 0.0

    def visible(self, x: float) -> bool:
        return not any(a <= x < b for a, b in self.covered)

    def extent(self) -> Rect:
        """Everything this cap can ever draw, fixed for its rectangle and scale: a strip window
        sized to this never has to resize as the cap grows, is piled on or grows icicles."""
        s = self.scale
        pad = 2
        below = max([LIP_PX * s] + [ic.max_len for ic in self.icicles])
        return (math.floor(self.x0) - pad, math.floor(self.top - CAP_MAX_PX * s) - pad,
                math.ceil(self.x1) + pad, math.ceil(self.top + below) + pad)

    @property
    def mass(self) -> float:
        return sum(self.heights) * self.col + sum(ic.mass for ic in self.icicles)

    @property
    def full(self) -> bool:
        return all(h >= t - _EPS for h, t in zip(self.heights, self.target))

    @property
    def full_mass(self) -> float:
        """The snow on it fully grown (no icicles): what ``QUIET_FRACTION`` is a fraction of."""
        return sum(self.target) * self.col

    def hidden(self) -> bool:
        """Is every column that holds snow behind another window?"""
        return all(h <= _EPS or not self.visible(self.column_center(i))
                   for i, h in enumerate(self.heights))


@dataclass
class Chunk:
    id: int
    x: float                 # centre, absolute px
    y: float
    vx: float                # px/s
    vy: float                # px/s, + is down
    mass: float              # px^2 of cap snow it carries; independent of its drawn size
    size: float              # drawn diameter, px
    scale: float
    born: float              # ms
    source: int              # hwnd it came off (0: none)
    lands_on_source: bool    # thrown snow may come back down on its own cap; brushed snow may not
    kind: str                # "lump" | "shard" (an icicle)
    group: int               # the stroke that released it: dirty boxes are grouped by it
    monitor: int = -1
    rect: Rect | None = None  # where it was last reported, for the dirty box


@dataclass
class Landing:
    where: str               # "cap" | "floor" | "nib"; "floor" also covers snow that left the
    #                          side of every screen, where (x, y) is the point it left, mid-air
    x: float
    y: float
    mass: float
    kind: str
    hwnd: int = 0            # the cap it landed on


@dataclass
class Gesture:
    kind: str                # "brush" | "flick"
    hwnd: int
    x: float
    y: float
    mass: float
    t: float


@dataclass
class Changes:
    """What one call changed, so the view repaints only that. Falsy when nothing did.

    ``caps`` only ever names caps that exist when it is reported: a cap that went is in
    ``removed`` (hide its strip, drop its pixmap) and nowhere else, even if it changed shape on the
    way out; one that went and came back under the same handle (its window moved) is in ``caps``
    and ``moved``, and a hwnd in ``caps`` always re-renders, whatever its ``version``."""

    caps: set[int] = field(default_factory=set)            # re-render these caps' pixmaps
    boxes: dict[int, list[Rect]] = field(default_factory=dict)  # monitor index -> dirty boxes
    landed: list[Landing] = field(default_factory=list)
    gestures: list[Gesture] = field(default_factory=list)
    faded: int = 0              # chunks dropped by the budget or after MAX_AIR_MS in the air
    removed: set[int] = field(default_factory=set)         # caps gone: hide these strips
    moved: set[int] = field(default_factory=set)           # new ``extent()``: move these strips

    def __bool__(self) -> bool:
        return bool(self.caps or self.boxes or self.landed or self.gestures or self.faded
                    or self.removed or self.moved)


@dataclass
class StackSync:
    """What one :meth:`SnowField.sync` did. Falsy when the stack changed nothing the view shows.

    ``fullscreen`` is state, not a change (it is there on every pass while a game runs), so it is
    left out of the truth test: the view compares it with the last pass's."""

    added: set[int] = field(default_factory=set)       # new caps (also in ``caps`` and ``moved``)
    caps: set[int] = field(default_factory=set)        # re-render these caps' pixmaps
    moved: set[int] = field(default_factory=set)       # new ``extent()``: move these strips
    removed: set[int] = field(default_factory=set)     # caps gone: hide these strips
    clipped: set[int] = field(default_factory=set)     # new ``occluders``: re-mask these strips
    chunks: int = 0                                    # chunks the pass put in the air
    fullscreen: set[int] = field(default_factory=set)  # monitors a window covers: hide their snow

    def __bool__(self) -> bool:
        return bool(self.added or self.caps or self.moved or self.removed or self.clipped
                    or self.chunks)


class SnowField:
    """Every cap and every airborne chunk, across all monitors."""

    def __init__(self, rng: random.Random | None = None, budget: int = CHUNK_BUDGET,
                 max_caps: int = MAX_CAPS) -> None:
        self.rng = rng if rng is not None else random.Random()
        self.budget = int(budget)
        self.max_caps = int(max_caps)
        self.caps: dict[int, Cap] = {}
        self.chunks: list[Chunk] = []
        self.monitors: list[Rect] = []
        self.floors: list[float] = []
        self.scales: list[float] = []
        self.fullscreen: set[int] = set()   # as of the last sync()
        self.nib: Rect | None = None
        #: Where all the snow went. ``grown`` is every px^2 that ever appeared (growth, icicles,
        #: ``fill``); the rest are the exits. ``mass_in_caps + mass_in_air + floor + nib + faded
        #: + vanished == grown`` always (up to float rounding).
        self.ledger = {"grown": 0.0, "floor": 0.0, "nib": 0.0, "faded": 0.0, "vanished": 0.0}
        self._pending = Changes()
        self._last_step: float | None = None
        self._samples: list[tuple[float, float, float]] = []
        self._next_id = 1
        self._stroke = 0
        self._stroke_hit = False

    # --- the world it lives in ---------------------------------------------------------------

    def set_monitors(self, rects: Sequence[Rect], floors: Sequence[float] | None = None,
                     scales: Sequence[float] | None = None) -> None:
        """The monitors, as ``(left, top, right, bottom)``; ``floors`` (default: each bottom) is
        where falling snow is gone - pass the work area's bottom so it lands on the taskbar;
        ``scales`` (default: all 1.0) is each monitor's DPI / 96, which sizes the piles
        :meth:`sync` makes on it."""
        self.monitors = [tuple(int(v) for v in r) for r in rects]
        self.floors = ([float(f) for f in floors] if floors is not None
                       else [float(r[3]) for r in self.monitors])
        self.scales = ([float(s) for s in scales] if scales is not None
                       else [1.0] * len(self.monitors))

    def set_nib(self, rect: Rect | None) -> None:
        """Nib's rectangle, or ``None`` while he is hidden: snow crossing it lands on him."""
        self.nib = tuple(int(v) for v in rect) if rect is not None else None

    # --- caps --------------------------------------------------------------------------------

    def add_cap(self, hwnd: int, rect: Rect, now_ms: float, scale: float = 1.0,
                fill: float = 0.0) -> Cap:
        """A cap for a window (replacing any it had, whose snow vanishes). ``fill`` starts it part
        or fully grown (0..1), which counts as grown snow in the ledger."""
        prev = self.caps.get(hwnd)
        if prev is not None:
            self.remove_cap(hwnd)
        cap = Cap(hwnd, rect, now_ms, scale, fill)
        if prev is not None:
            cap.version = prev.version + 1
        self.caps[cap.hwnd] = cap
        self.ledger["grown"] += cap.mass
        self._pending.removed.discard(cap.hwnd)
        self._pending.caps.add(cap.hwnd)
        self._pending.moved.add(cap.hwnd)
        return cap

    def remove_cap(self, hwnd: int) -> None:
        """Forget a cap; its snow simply vanishes (the window went where nothing can fall from).
        Reported in ``Changes.removed`` - and no longer in ``Changes.caps``, which only ever names
        caps that exist."""
        cap = self.caps.pop(hwnd, None)
        if cap is not None:
            self.ledger["vanished"] += cap.mass
            self._pending.caps.discard(cap.hwnd)
            self._pending.moved.discard(cap.hwnd)
            self._pending.removed.add(cap.hwnd)

    def drop_cap(self, hwnd: int, now_ms: float) -> int:
        """The whole pile falls (its window moved, was minimised or closed), and the cap goes.
        Returns how many chunks it made."""
        cap = self.caps.get(hwnd)
        if cap is None:
            return 0
        made = self.release_span(hwnd, cap.x0, cap.x1, now_ms)
        made += self._snap_icicles(cap, cap.icicles, now_ms, (0.0, 0.0), "drop", self._new_stroke())
        self.remove_cap(hwnd)
        return made

    def release_span(self, hwnd: int, x0: float, x1: float, now_ms: float, *,
                     keep_px: float = 0.0, velocity: tuple[float, float] = (0.0, 0.0),
                     lands_on_source: bool = False) -> int:
        """Turn the snow of ``[x0, x1)`` above ``keep_px`` into chunks leaving at ``velocity``
        (px/s, jittered). The one primitive under brushing, flicking and a window moving, resizing
        or closing. Covered columns are released too: this is the window's own snow. Returns how
        many chunks it made."""
        cap = self.caps.get(hwnd)
        if cap is None or x1 <= x0:
            return 0
        i0 = max(0, math.floor((x0 - cap.x0) / cap.col))
        i1 = min(cap.n, math.ceil((x1 - cap.x0) / cap.col))
        keeps = {i: max(0.0, keep_px) for i in range(i0, i1)}
        mode = "drop" if velocity == (0.0, 0.0) else "throw"
        return self._release(cap, keeps, now_ms, velocity, mode, lands_on_source,
                             self._new_stroke())

    def grow(self, now_ms: float) -> set[int]:
        """One growth step for every cap that is due, at most one per cap per call; returns the
        caps that changed. Call it on a timer of any period up to ``GROWTH_STEP_MS``, only while
        snow is showing and allowed to grow (not on battery, not static).

        The schedule keeps its cadence (the next step is one period after the last was *due*, not
        after the call), and a step is due a quarter-period early, so a timer of exactly
        ``GROWTH_STEP_MS`` that fires a few ms early or late still steps every time instead of
        every other time. But a call more than a period late resets it: a long gap never catches
        up, so growth counts showing time.
        """
        changed: set[int] = set()
        for cap in self.caps.values():
            # A clock that went backwards must not stall growth for however far it jumped. (The
            # cadence alone never puts the next step more than a period and a quarter away.)
            if cap.next_grow_ms > now_ms + 2 * GROWTH_STEP_MS:
                cap.next_grow_ms = now_ms + GROWTH_STEP_MS
            if cap.next_icicle_ms > now_ms + 2 * ICICLE_STEP_MS:
                cap.next_icicle_ms = now_ms + ICICLE_STEP_MS
            before = cap.mass
            if now_ms >= cap.next_grow_ms - GROWTH_STEP_MS / 4:
                due = cap.next_grow_ms + GROWTH_STEP_MS
                cap.next_grow_ms = due if due > now_ms else now_ms + GROWTH_STEP_MS
                for i, t in enumerate(cap.target):
                    h = cap.heights[i]
                    if h < t - _EPS:
                        step = t / GROWTH_STEPS * (1.0 + cap.rng.uniform(-GROWTH_NOISE,
                                                                         GROWTH_NOISE))
                        cap.heights[i] = min(t, h + step)
            if now_ms >= cap.next_icicle_ms - GROWTH_STEP_MS / 4:
                due = cap.next_icicle_ms + ICICLE_STEP_MS
                cap.next_icicle_ms = due if due > now_ms else now_ms + ICICLE_STEP_MS
                for ic in cap.icicles:
                    support = cap.heights[ic.column]
                    if ic.length < ic.max_len and support >= ICICLE_GROW_FILL * cap.target[
                            ic.column]:
                        ic.length = min(ic.max_len, ic.length + ICICLE_STEP_PX * cap.scale)
            grown = cap.mass - before
            if grown > _EPS:
                self.ledger["grown"] += grown
                cap.version += 1
                changed.add(cap.hwnd)
        return changed

    def near(self, x: float, y: float, margin: float = 0.0) -> int | None:
        """The cap whose snow (or icicles) is within ``margin`` px of a point, or ``None``: lets
        the view poll the pointer quickly only while it is somewhere it could brush."""
        for cap in self.caps.values():
            left, top, right, bottom = cap.extent()
            if left - margin <= x < right + margin and top - margin <= y < bottom + margin:
                return cap.hwnd
        return None

    # --- the window stack --------------------------------------------------------------------

    def sync(self, stack: Iterable[tuple[int, Rect, bool, bool]] | None, now_ms: float,
             own: Iterable[int] = ()) -> StackSync:
        """Follow the desktop. ``stack`` is ``win_integration.list_window_stack()``: every window
        you can see, front to back, as ``(hwnd, rect, zoomed, topmost)``. Called at 2 Hz on its
        own timer, never per frame; pure, and silent - falsy, no ``version`` bumped - when the
        stack is what it was.

        *   A window big enough (``CAP_MIN_*_PX``), not maximised, not covering a whole monitor,
            with its top edge on a monitor above that monitor's floor (not under the taskbar) and
            the band above it (``COVER_BAND_PX``) on one too - not a window snapped to the top of
            the screen, whose pile would be all off it - gets a bare cap at that monitor's scale
            while there is room. Past ``max_caps`` windows, they are ranked by how much of their
            top edge shows (by the ``covered`` rule), then by area; a window with a cap keeps it
            unless its top edge is all covered and a window whose top shows has none.
        *   **Moved** (its top, both its sides, or its monitor's scale changed): the pile falls
            where it was, and a bare one starts at the new spot that does not grow for
            ``RESPAWN_HOLD_MS``. A top-edge resize counts as a move: the line the snow stood on
            went.
        *   **Resized** by one side: only the snow past the new edge falls, with that side's
            icicles and whatever stands above the new rounded end; the rest keeps its heights
            and the other side keeps its icicles. A new bottom alone changes nothing.
        *   **Closed, minimised, hidden, maximised**, grown too small or onto no monitor: the whole
            pile falls.
        *   A pile goes *quietly* instead (no chunks) when it holds less than ``QUIET_FRACTION``
            of a full one - so a window dragged across several passes throws its snow once, not
            every pass - when every bit of it was behind other windows, when its monitor is
            fullscreen (the view is hiding that monitor's snow), when it is one of many going at
            once (``MASS_EXIT_CAPS``), or when it only lost its place to ``max_caps`` (it was all
            covered). A resize's cut-off span goes quietly when the pile is hidden or its monitor
            fullscreen too.
        *   Every cap's ``covered`` and ``occluders`` are recomputed from the windows in front.
        *   ``fullscreen`` names each monitor that some unmaximised window covers whole:
            ``win_integration.is_fullscreen_on_monitor`` asks only about the foreground window.

        ``stack`` ``None`` (the pass failed) changes nothing. ``own`` are handles never to treat
        as windows (the stack helper already leaves out this process's). Chunks made here fly from
        the next :meth:`step`; every cap change pending - this pass's and any before it - is in
        the return value, not the next ``step``'s, because at rest nothing calls ``step``.
        """
        out = StackSync()
        if stack is None:
            out.fullscreen = set(self.fullscreen)
            return out
        skip = {int(h) for h in own}
        entries: list[tuple[int, Rect, bool]] = []
        for hwnd, rect, zoomed, _topmost in stack:
            h = int(hwnd)
            r = tuple(int(v) for v in rect)
            if h not in skip and r[2] > r[0] and r[3] > r[1]:
                entries.append((h, r, bool(zoomed)))
        self.fullscreen = {idx for idx, mon in enumerate(self.monitors)
                           if any(not z and _covers(r, mon) for _h, r, z in entries)}
        out.fullscreen = set(self.fullscreen)

        # Who gets a pile.
        eligible: dict[int, tuple[Rect, float]] = {}
        for h, r, z in entries:
            if z or any(_covers(r, mon) for mon in self.monitors):
                continue
            idx = self._monitor_of_edge(r)
            if idx < 0:
                continue
            s = self.scales[idx] if idx < len(self.scales) else 1.0
            if r[2] - r[0] < CAP_MIN_W_PX * s or r[3] - r[1] < CAP_MIN_H_PX * s:
                continue
            if idx < len(self.floors) and r[1] >= self.floors[idx]:
                continue   # its top edge is under the taskbar
            if not self._on_screen(r[0], r[2], r[1] - COVER_BAND_PX * s):
                continue   # snapped to the top of the screen: its pile would be all off it
            eligible[h] = (r, s)

        order = {h: k for k, (h, _r, _z) in enumerate(entries)}
        if len(eligible) <= self.max_caps:
            # Room for all (the usual case): nothing to rank, so no per-window occlusion pass.
            keep = [h for h in eligible if h in self.caps]
            fresh = [h for h in eligible if h not in self.caps]
        else:
            shows = {h: _shown_width(r, COVER_BAND_PX * s, entries[:order[h]])
                     for h, (r, s) in eligible.items()}

            def rank(h: int) -> tuple[float, int, int]:
                r = eligible[h][0]
                return (-shows[h], -(r[2] - r[0]) * (r[3] - r[1]), h)

            keep = sorted((h for h in eligible if h in self.caps), key=rank)[:self.max_caps]
            new = sorted((h for h in eligible if h not in self.caps), key=rank)
            room = self.max_caps - len(keep)
            fresh, waiting = new[:room], [h for h in new[room:] if shows[h] > 0.0]
            # A pile whose top edge is all covered gives its place up to a window whose top shows
            # (smallest first); it goes quietly below, being still eligible. Only an all-covered
            # one: evicting by rank would swap two similar windows back and forth every pass.
            hidden = [h for h in reversed(keep) if shows[h] <= 0.0][:len(waiting)]
            keep = [h for h in keep if h not in hidden]
            fresh += waiting[:len(hidden)]

        # Piles whose window went.
        present = {h for h, _r, _z in entries}
        kept = set(keep)
        gone = [h for h in self.caps if h not in kept]
        vanished = [h for h in gone if h not in present]
        mass_exit = len(vanished) >= MASS_EXIT_CAPS and 2 * len(vanished) > len(self.caps)
        for h in gone:
            quiet = h in eligible or (mass_exit and h not in present)
            out.chunks += self._let_go(self.caps[h], now_ms, quiet)

        # Piles whose window moved or changed size.
        for h in keep:
            r, s = eligible[h]
            cap = self.caps[h]
            if r == cap.rect and s == cap.scale:
                continue
            (l0, t0, r0, _b0), (l1, t1, r1, _b1) = cap.rect, r
            if s != cap.scale or t1 != t0 or (l1 != l0 and r1 != r0):
                out.chunks += self._move(cap, r, s, now_ms)
            elif l1 == l0 and r1 == r0:
                cap.rect = r   # only the bottom: nothing the pile stands on changed
            else:
                out.chunks += self._resize(cap, r, now_ms)
        for h in fresh:
            r, s = eligible[h]
            self.add_cap(h, r, now_ms, scale=s)
            out.added.add(h)

        # What hides what. EnumWindows is front to back: everything before a window is over it.
        for h, cap in self.caps.items():
            ext = cap.extent()
            band = cap.top - COVER_BAND_PX * cap.scale
            occluders: list[Rect] = []
            spans: list[tuple[float, float]] = []
            for _h, r, _z in entries[:order[h]]:
                cut = _intersect(r, ext)
                if cut is None:
                    continue
                occluders.append(cut)
                if r[1] < cap.top and r[3] > band:
                    spans.append((max(float(r[0]), cap.x0), min(float(r[2]), cap.x1)))
            covered = _merge_spans(spans)
            if occluders != cap.occluders or covered != cap.covered:
                cap.occluders, cap.covered = occluders, covered
                out.clipped.add(h)

        pending = self._pending
        out.caps = pending.caps & self.caps.keys()
        out.moved = pending.moved & self.caps.keys()
        out.removed = pending.removed - self.caps.keys()
        pending.caps, pending.moved, pending.removed = set(), set(), set()
        return out

    def _monitor_of_edge(self, rect: Rect) -> int:
        """The monitor a window's top edge is on (the one holding most of it), or -1."""
        left, top, right, _bottom = rect
        best, best_w = -1, 0
        for idx, (m_left, m_top, m_right, m_bottom) in enumerate(self.monitors):
            if m_top <= top < m_bottom:
                width = min(right, m_right) - max(left, m_left)
                if width > best_w:
                    best, best_w = idx, width
        return best

    def _on_screen(self, left: float, right: float, y: float) -> bool:
        """Is any of the row ``y`` between ``left`` and ``right`` on a monitor?"""
        return any(m_top <= y < m_bottom and min(right, m_right) > max(left, m_left)
                   for m_left, m_top, m_right, m_bottom in self.monitors)

    def _quiet(self, cap: Cap) -> bool:
        """Should this pile just go rather than fall? (See :meth:`sync`.)"""
        return (cap.mass < QUIET_FRACTION * cap.full_mass or cap.hidden()
                or self._monitor_of_edge(cap.rect) in self.fullscreen)

    def _let_go(self, cap: Cap, now_ms: float, quiet: bool) -> int:
        if quiet or self._quiet(cap):
            self.remove_cap(cap.hwnd)
            return 0
        return self.drop_cap(cap.hwnd, now_ms)

    def _move(self, cap: Cap, rect: Rect, scale: float, now_ms: float) -> int:
        made = self._let_go(cap, now_ms, False)
        new = self.add_cap(cap.hwnd, rect, now_ms, scale=scale)
        new.version = cap.version + 1
        # ``grow`` steps a quarter-period early; the hold is meant in full.
        new.next_grow_ms = now_ms + RESPAWN_HOLD_MS + GROWTH_STEP_MS / 4
        return made

    def _resize(self, cap: Cap, rect: Rect, now_ms: float) -> int:
        """One side of the window moved: cut the pile to the new width and carry the rest over.

        The new :class:`Cap` (same handle, so the same seeded overhangs and icicle spots) is made
        bare and filled from the old one by absolute x. What lies past the new cap's ends falls,
        as does anything above the new rounded end's profile (scaled up to ``CAP_MAX_PX``, so
        snow piled on by throws survives in the middle). When the left edge moved the column grid
        shifts by a fraction of a column, so the carry is overlap-weighted: mass is conserved,
        and the sliver of a column that straddled the cut goes to ``vanished``."""
        side = "left" if rect[0] != cap.rect[0] else "right"
        s = cap.scale
        new = Cap(cap.hwnd, rect, now_ms, s)
        moved_edge = float(rect[0] if side == "left" else rect[2])
        middle = (rect[0] + rect[2]) / 2.0
        near: dict[int, float] = {}
        far: dict[int, float] = {}
        for i, h in enumerate(cap.heights):
            if h <= _EPS:
                continue
            cx = cap.column_center(i)
            j = new.column_at(cx)
            if j is None or not new.x0 <= cx < new.x1:
                limit = 0.0
            else:
                limit = min(CAP_MAX_PX * s, new.target[j] * CAP_MAX_PX / CAP_PX)
            if h > limit + _EPS:
                on_moved_side = (cx < middle) == (moved_edge < middle)
                (near if on_moved_side else far)[i] = limit
        if cap.hidden() or self._monitor_of_edge(cap.rect) in self.fullscreen:
            # ``_let_go``'s rule: chunks would fly unclipped on the flight layer, over the window
            # hiding the pile or the fullscreen app. The cut-off snow just goes.
            made, before = 0, cap.mass
            for i, k in {**near, **far}.items():
                cap.heights[i] = k
            for ic in cap.icicles:
                if ic.side == side or cap.heights[ic.column] < ICICLE_SUPPORT_PX * s:
                    ic.length = 0.0
            self.ledger["vanished"] += before - cap.mass
        else:
            # Two strokes, so the two ends of a wide window never share one dirty box.
            stroke = self._new_stroke()
            made = self._release(cap, near, now_ms, (0.0, 0.0), "drop", False, stroke)
            made += self._snap_icicles(cap, [ic for ic in cap.icicles if ic.side == side],
                                       now_ms, (0.0, 0.0), "drop", stroke)
            made += self._release(cap, far, now_ms, (0.0, 0.0), "drop", False,
                                  self._new_stroke())

        for i, h in enumerate(cap.heights):
            if h <= _EPS:
                continue
            a = cap.x0 + i * cap.col
            b = a + cap.col
            j0 = math.floor((a - new.x0) / new.col)
            for j in (j0, j0 + 1):
                if 0 <= j < new.n:
                    lo, hi = max(a, new.x0 + j * new.col), min(b, new.x0 + (j + 1) * new.col)
                    if hi > lo:
                        new.heights[j] += h * (hi - lo) / new.col
        other = "right" if side == "left" else "left"
        olds = [ic for ic in cap.icicles if ic.side == other]
        news = [ic for ic in new.icicles if ic.side == other]
        if len(olds) == len(news):
            for old_ic, new_ic in zip(olds, news):
                if abs(old_ic.x - new_ic.x) < 0.5:
                    new_ic.length = min(new_ic.max_len, old_ic.length)
        lost = cap.mass - new.mass
        if lost >= 0.0:
            self.ledger["vanished"] += lost
        else:   # float rounding only
            self.ledger["grown"] -= lost

        new.version = cap.version + 1
        new.next_grow_ms, new.next_icicle_ms = cap.next_grow_ms, cap.next_icicle_ms
        new.covered, new.occluders = cap.covered, cap.occluders
        self.caps[cap.hwnd] = new
        self._pending.caps.add(cap.hwnd)
        self._pending.moved.add(cap.hwnd)
        return made

    # --- the pointer -------------------------------------------------------------------------

    def pointer(self, x: float, y: float, t_ms: float) -> bool:
        """One pointer sample. Returns True when it knocked or threw snow into the air (start the
        animation timer). What it changed is reported by the next :meth:`step`."""
        samples = self._samples
        if samples:
            gap = t_ms - samples[-1][2]
            if -MAX_SAMPLE_GAP_MS <= gap <= 0:
                return False       # a duplicate or out-of-order sample: ignore it
            if abs(gap) > MAX_SAMPLE_GAP_MS:
                samples.clear()    # a pause or a clock jump: not one movement
        samples.append((float(x), float(y), float(t_ms)))
        while len(samples) > 2 and t_ms - samples[1][2] >= VELOCITY_WINDOW_MS:
            samples.pop(0)
        if len(samples) < 2:
            self._stroke_hit = False
            return False
        ref = samples[0]
        span = t_ms - ref[2]
        travel = math.hypot(x - ref[0], y - ref[1])
        px, py, _pt = samples[-2]
        if span < MIN_SPAN_MS:
            return False
        vx, vy = (x - ref[0]) / span, (y - ref[1]) / span   # px/ms
        hit = False
        for cap in list(self.caps.values()):
            s = cap.scale
            if travel < MIN_TRAVEL_PX * s:
                continue
            if abs(vx) >= BRUSH_SPEED * s and abs(vx) >= abs(vy):
                hit |= self._brush(cap, (px, py), (x, y), vx, t_ms)
            elif -vy >= FLICK_SPEED * s and abs(vy) >= abs(vx):
                hit |= self._flick(cap, (px, py), (x, y), vx, vy, t_ms)
        if not hit:
            self._stroke_hit = False
        return hit

    def _stroke_id(self) -> int:
        """Releases along one continuous swipe share a group, so their dirty box is one."""
        if not self._stroke_hit:
            self._stroke += 1
            self._stroke_hit = True
        return self._stroke

    def _new_stroke(self) -> int:
        self._stroke += 1
        self._stroke_hit = False
        return self._stroke

    def _brush(self, cap: Cap, a: tuple[float, float], b: tuple[float, float], vx: float,
               t_ms: float) -> bool:
        s = cap.scale
        lo, hi = min(a[0], b[0]), max(a[0], b[0])
        if hi < cap.x0 or lo >= cap.x1:
            return False
        keeps: dict[int, float] = {}
        i0 = max(0, math.floor((lo - cap.x0) / cap.col))
        i1 = min(cap.n - 1, math.floor((hi - cap.x0) / cap.col))
        for i in range(i0, i1 + 1):
            h = cap.heights[i]
            cx = cap.column_center(i)
            if h <= _EPS or not cap.visible(cx):
                continue
            y = _y_on_segment(a, b, min(max(cx, lo), hi))
            if y < cap.top - h or y > cap.top + cap.lip(i) + BRUSH_REACH_PX * s:
                continue   # passed above the snow, or too far down into the window
            keep = max(0.0, cap.top - y - BRUSH_BAND_PX * s)
            if keep < h - _EPS:
                keeps[i] = keep
        # Icicles the pass went through snap off, as do any whose overhang it just cleared.
        snapped = []
        for ic in cap.icicles:
            if ic.length <= _EPS or not (lo - ic.width / 2 <= ic.x <= hi + ic.width / 2):
                continue
            y = _y_on_segment(a, b, min(max(ic.x, lo), hi))
            if cap.top <= y <= cap.top + ic.length + BRUSH_REACH_PX * s:
                snapped.append(ic)
        if not keeps and not snapped:
            return False
        speed = max(-BRUSH_MAX_PX_S * s, min(BRUSH_MAX_PX_S * s, vx * 1000.0 * BRUSH_CARRY))
        group = self._stroke_id()
        mass = sum(cap.heights[i] - k for i, k in keeps.items()) * cap.col
        mass += sum(ic.mass for ic in snapped)
        self._release(cap, keeps, t_ms, (speed, 0.0), "brush", False, group)
        self._snap_icicles(cap, snapped, t_ms, (speed, 0.0), "brush", group)
        self._pending.gestures.append(Gesture("brush", cap.hwnd, b[0], b[1], mass, t_ms))
        return True

    def _flick(self, cap: Cap, a: tuple[float, float], b: tuple[float, float], vx: float,
               vy: float, t_ms: float) -> bool:
        s = cap.scale
        # The pass goes up from ``a`` (its low end) to ``b``. It scoops the column where it comes
        # up through the window's top edge (or where it starts, if it starts higher than that).
        y_low, y_high = a[1], b[1]
        cross_y = min(y_low, cap.top)
        x = _x_on_segment(a, b, cross_y)
        col = cap.column_at(x)
        if col is None or cap.heights[col] <= _EPS or not cap.visible(x):
            return False
        if y_low < cap.top - cap.heights[col]:
            return False   # it started above the snow: went through none of it
        if y_high > cap.top + cap.lip(col) + BRUSH_REACH_PX * s:
            return False   # it never came up out of the window
        keep = max(0.0, cap.top - y_low)   # what is under the start of the pass stays
        half = FLICK_HALF_W_PX * s
        keeps: dict[int, float] = {}
        for i in range(cap.n):
            cx = cap.column_center(i)
            if abs(cx - x) > half or not cap.visible(cx):
                continue
            if cap.heights[i] > keep + _EPS:
                keeps[i] = keep
        if not keeps:
            return False
        vx_s, vy_s = vx * 1000.0, vy * 1000.0
        speed = math.hypot(vx_s, vy_s)
        cap_speed = MAX_THROW_PX_S * s
        if speed > cap_speed:
            vx_s, vy_s = vx_s * cap_speed / speed, vy_s * cap_speed / speed
        mass = sum(cap.heights[i] - k for i, k in keeps.items()) * cap.col
        self._release(cap, keeps, t_ms, (vx_s, vy_s), "flick", True, self._stroke_id())
        self._pending.gestures.append(Gesture("flick", cap.hwnd, x, cross_y, mass, t_ms))
        return True

    # --- releasing snow into the air ---------------------------------------------------------

    def _release(self, cap: Cap, keeps: dict[int, float], now_ms: float,
                 velocity: tuple[float, float], mode: str, lands_on_source: bool,
                 group: int) -> int:
        """Cut columns down to their ``keeps`` height and send what came off up as chunks.

        The removed snow is split, left to right, into runs of about equal mass, one chunk each,
        so mass is conserved exactly and the chunks come from where the snow was."""
        removed: list[tuple[int, float, float]] = []   # (column, mass, band centre y)
        for i in sorted(keeps):
            h = cap.heights[i]
            k = min(h, keeps[i])
            if h - k <= _EPS:
                continue
            removed.append((i, (h - k) * cap.col, cap.top - (h + k) / 2.0))
            cap.heights[i] = k
        if not removed:
            return 0
        cap.version += 1
        self._pending.caps.add(cap.hwnd)
        total = sum(m for _i, m, _y in removed)
        n = max(1, min(MAX_PER_RELEASE, math.ceil(total / (TYPICAL_CHUNK_MASS * cap.scale ** 2))))
        per = total / n
        made = 0
        acc_m = acc_x = acc_y = 0.0
        for idx, (i, m, y) in enumerate(removed):
            acc_m += m
            acc_x += cap.column_center(i) * m
            acc_y += y * m
            last = idx == len(removed) - 1
            if acc_m >= per - _EPS or last:
                self._spawn(cap, acc_x / acc_m, acc_y / acc_m, acc_m, now_ms, velocity, mode,
                            lands_on_source, group, "lump")
                made += 1
                acc_m = acc_x = acc_y = 0.0
        # Icicles lose their hold when the overhang above them goes.
        loose = [ic for ic in cap.icicles if ic.length > _EPS
                 and cap.heights[ic.column] < ICICLE_SUPPORT_PX * cap.scale]
        made += self._snap_icicles(cap, loose, now_ms, velocity, mode, group)
        return made

    def _snap_icicles(self, cap: Cap, icicles: Iterable[Icicle], now_ms: float,
                      velocity: tuple[float, float], mode: str, group: int) -> int:
        made = 0
        for ic in icicles:
            if ic.length <= _EPS:
                continue
            self._spawn(cap, ic.x, cap.top + ic.length / 2.0, ic.mass, now_ms, velocity, mode,
                        False, group, "shard")
            ic.length = 0.0
            made += 1
        if made:
            cap.version += 1
            self._pending.caps.add(cap.hwnd)
        return made

    def _spawn(self, cap: Cap, x: float, y: float, mass: float, now_ms: float,
               velocity: tuple[float, float], mode: str, lands_on_source: bool, group: int,
               kind: str) -> None:
        r, s = self.rng, cap.scale
        vx, vy = velocity
        if mode == "brush":
            vx = vx * r.uniform(0.6, 1.1) + r.uniform(-1.0, 1.0) * JITTER_PX_S * s
            vy = -r.uniform(*BRUSH_POP_PX_S) * s
        elif mode == "flick":
            vx = vx * r.uniform(0.85, 1.05) + r.uniform(-1.0, 1.0) * JITTER_PX_S * s
            vy = vy * r.uniform(0.85, 1.05)
            speed, top = math.hypot(vx, vy), MAX_THROW_PX_S * s
            if speed > top:
                vx, vy = vx * top / speed, vy * top / speed
        else:   # dropped, or released by the window stack with a velocity of its own
            vx += r.uniform(-1.0, 1.0) * JITTER_PX_S * s * 0.75
            vy += r.uniform(-0.5, 1.0) * JITTER_PX_S * s
        x += r.uniform(-0.5, 0.5) * cap.col
        size = max(CHUNK_PX[0] * s, min(CHUNK_PX[1] * s, 2.0 * math.sqrt(mass / math.pi)))
        if self._last_step is None or not self.chunks:
            self._last_step = now_ms   # the flight starts now, not at the last flight's end
        chunk = Chunk(self._next_id, x, y, vx, vy, mass, size, s, now_ms, cap.hwnd,
                      lands_on_source, kind, group)
        self._next_id += 1
        chunk.monitor = self._monitor_of(chunk.x, chunk.y)
        self.chunks.append(chunk)
        self._enforce_budget(self._pending)

    # --- the flight --------------------------------------------------------------------------

    @property
    def airborne(self) -> bool:
        """True while anything is in the air: run the animation timer only then."""
        return bool(self.chunks)

    @staticmethod
    def frame_ms(on_battery: bool = False) -> int:
        return 1000 // (FPS_BATTERY if on_battery else FPS)

    @property
    def mass_in_caps(self) -> float:
        return sum(cap.mass for cap in self.caps.values())

    @property
    def mass_in_air(self) -> float:
        return sum(c.mass for c in self.chunks)

    def step(self, now_ms: float, pointer: tuple[float, float] | None = None) -> Changes:
        """Advance the airborne snow to ``now_ms`` (feeding one pointer sample first, if given);
        return what changed since the last call. Empty, and falsy, when nothing moved."""
        if pointer is not None:
            self.pointer(pointer[0], pointer[1], now_ms)
        out, self._pending = self._pending, Changes()
        out.caps.intersection_update(self.caps)   # belt and braces: remove_cap sees to it
        out.moved.intersection_update(self.caps)
        if not self.chunks:
            self._last_step = now_ms
            return out
        last = self._last_step if self._last_step is not None else now_ms
        elapsed = max(0.0, min(float(MAX_STEP_MS), now_ms - last))
        self._last_step = now_ms
        dirty: dict[tuple[int, int], list[Rect]] = {}

        def mark(chunk: Chunk, rect: Rect | None) -> None:
            if rect is not None:
                dirty.setdefault((chunk.monitor, chunk.group), []).append(rect)

        # Every chunk's last reported spot is dirty whatever happens to it now.
        for c in self.chunks:
            mark(c, c.rect)
        substeps = max(1, math.ceil(elapsed / SUBSTEP_MS))
        dt = elapsed / substeps / 1000.0
        for _ in range(substeps):
            for c in list(self.chunks):
                self._advance(c, dt, now_ms, out)
        for c in list(self.chunks):
            if now_ms - c.born > MAX_AIR_MS:
                self._fade(c, out)
        self._enforce_budget(out)
        for c in self.chunks:
            c.rect = _chunk_rect(c)
            mark(c, c.rect)
        grouped = [r for rs in out.boxes.values() for r in rs]   # faded at spawn, from the pointer
        for rects in dirty.values():
            box = rects[0]
            for r in rects[1:]:
                box = _union(box, r)
            grouped.append(box)
        # Each monitor's flight layer repaints every box that touches it: a chunk crossing from
        # one monitor to the other is erased on the first and drawn on the second.
        out.boxes = {}
        for box in grouped:
            for idx, mon in enumerate(self.monitors):
                if box[0] < mon[2] and mon[0] < box[2] and box[1] < mon[3] and mon[1] < box[3]:
                    out.boxes.setdefault(idx, []).append(box)
        for monitor in out.boxes:
            out.boxes[monitor] = _merge_boxes(out.boxes[monitor])
        return out

    def _advance(self, c: Chunk, dt: float, now_ms: float, out: Changes) -> None:
        s = c.scale
        x0, y0 = c.x, c.y
        c.vy = min(TERMINAL_PX_S * s, c.vy + GRAVITY_PX_S2 * s * dt)
        c.vx *= math.exp(-AIR_DRAG * dt)
        c.x += c.vx * dt
        c.y += c.vy * dt
        c.monitor = self._monitor_of(c.x, c.y)
        if self.nib is not None and _segment_hits_rect((x0, y0), (c.x, c.y), self.nib,
                                                       c.size / 2.0):
            self._land(c, "nib", out)
            return
        if c.vy > 0 and self._land_on_cap(c, y0, out):
            return
        if c.monitor < 0:
            self._land(c, "floor", out)   # off every screen: gone
            return
        # A floor is landed on from above, so snow knocked off a window just under a taskbar (on
        # the monitor stacked above) is not lifted onto it. And a screen's bottom edge is no floor
        # where another monitor carries on below it: the snow keeps falling down that one.
        r, floor = c.size / 2.0, self.floors[c.monitor]
        if y0 + r < floor <= c.y + r and not self._continues_below(c.monitor, floor, c.x):
            c.y = floor - r
            self._land(c, "floor", out)

    def _land_on_cap(self, c: Chunk, y_before: float, out: Changes) -> bool:
        """A falling chunk whose bottom crossed a cap's surface this substep settles into it."""
        best: tuple[float, Cap, int] | None = None
        sink = c.size / 4.0
        for cap in self.caps.values():
            if cap.hwnd == c.source and not c.lands_on_source:
                continue
            i = cap.column_at(c.x)
            if i is None or not cap.visible(c.x):
                continue
            surface = cap.top - cap.heights[i]
            if y_before + sink <= surface < c.y + sink and (best is None or surface < best[0]):
                best = (surface, cap, i)
        if best is None:
            return False
        surface, cap, i = best
        if not self._deposit(cap, i, c.mass):
            return False   # no room left on that pile: it keeps falling past
        c.y = surface - sink
        self._remove(c)
        out.landed.append(Landing("cap", c.x, surface, c.mass, c.kind, cap.hwnd))
        cap.version += 1
        out.caps.add(cap.hwnd)
        return True

    def _deposit(self, cap: Cap, i: int, mass: float) -> bool:
        """Spread ``mass`` over the columns around ``i`` (a little mound, 1-2-3-2-1), never past
        ``CAP_MAX_PX``. False, and nothing changed, if there is not room for all of it."""
        cols = [j for j in range(i - 2, i + 3)
                if 0 <= j < cap.n and cap.visible(cap.column_center(j))]
        limit = CAP_MAX_PX * cap.scale
        room = {j: max(0.0, limit - cap.heights[j]) * cap.col for j in cols}
        if sum(room.values()) < mass - _EPS:
            return False
        weight = {j: 3 - abs(j - i) for j in cols}
        left = mass
        while left > _EPS:
            open_cols = [j for j in cols if room[j] > _EPS]
            if not open_cols:
                break
            total_w = sum(weight[j] for j in open_cols)
            share = left
            for j in open_cols:
                give = min(room[j], share * weight[j] / total_w)
                room[j] -= give
                cap.heights[j] += give / cap.col
                left -= give
        return True

    def _land(self, c: Chunk, where: str, out: Changes) -> None:
        self._remove(c)
        self.ledger[where] += c.mass
        out.landed.append(Landing(where, c.x, c.y, c.mass, c.kind))

    def _fade(self, c: Chunk, out: Changes) -> None:
        self._remove(c)
        self.ledger["faded"] += c.mass
        out.faded += 1

    def _remove(self, c: Chunk) -> None:
        self.chunks.remove(c)

    def _enforce_budget(self, out: Changes) -> None:
        """At most ``budget`` chunks per monitor; the oldest fade first."""
        counts: dict[int, int] = {}
        for c in self.chunks:
            counts[c.monitor] = counts.get(c.monitor, 0) + 1
        for monitor, count in counts.items():
            if count <= self.budget:
                continue
            on = sorted((c for c in self.chunks if c.monitor == monitor),
                        key=lambda c: (c.born, c.id))
            for c in on[:count - self.budget]:
                if c.rect is not None:   # it was drawn: erase it
                    out.boxes.setdefault(c.monitor, []).append(c.rect)
                self._fade(c, out)

    def _monitor_of(self, x: float, y: float) -> int:
        """The monitor under a point; above the top of the screens (thrown high), the one whose
        columns it is over; -1 when it is beside every screen."""
        for idx, (left, top, right, bottom) in enumerate(self.monitors):
            if left <= x < right and top <= y < bottom:
                return idx
        for idx, (left, top, right, _bottom) in enumerate(self.monitors):
            if left <= x < right and y < top:
                return idx
        return -1

    def _continues_below(self, idx: int, floor: float, x: float) -> bool:
        """Is ``floor`` monitor ``idx``'s own bottom edge, with another monitor right under it at
        ``x``?"""
        bottom = self.monitors[idx][3]
        return floor >= bottom and any(
            j != idx and left <= x < right and top <= bottom < bot
            for j, (left, top, right, bot) in enumerate(self.monitors))


# --- geometry helpers ----------------------------------------------------------------------------


def _y_on_segment(a: tuple[float, float], b: tuple[float, float], x: float) -> float:
    if abs(b[0] - a[0]) < _EPS:
        return b[1]
    t = (x - a[0]) / (b[0] - a[0])
    return a[1] + (b[1] - a[1]) * max(0.0, min(1.0, t))


def _x_on_segment(a: tuple[float, float], b: tuple[float, float], y: float) -> float:
    if abs(b[1] - a[1]) < _EPS:
        return b[0]
    t = (y - a[1]) / (b[1] - a[1])
    return a[0] + (b[0] - a[0]) * max(0.0, min(1.0, t))


def _segment_hits_rect(a: tuple[float, float], b: tuple[float, float], rect: Rect,
                       pad: float) -> bool:
    """Liang-Barsky: does the segment ``a``-``b`` touch ``rect`` grown by ``pad``? A segment, not
    the end point, so a fast chunk cannot step over him between two frames."""
    left, top, right, bottom = rect[0] - pad, rect[1] - pad, rect[2] + pad, rect[3] + pad
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - left), (dx, right - a[0]), (-dy, a[1] - top), (dy, bottom - a[1])):
        if abs(p) < _EPS:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return False
    return True


def _chunk_rect(c: Chunk) -> Rect:
    r = c.size / 2.0
    return (math.floor(c.x - r) - 1, math.floor(c.y - r) - 1,
            math.ceil(c.x + r) + 1, math.ceil(c.y + r) + 1)


def _union(a: Rect, b: Rect) -> Rect:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _area(r: Rect) -> int:
    return max(0, r[2] - r[0]) * max(0, r[3] - r[1])


def _intersect(a: Rect, b: Rect) -> Rect | None:
    r = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return r if r[0] < r[2] and r[1] < r[3] else None


def _covers(r: Rect, monitor: Rect) -> bool:
    """Does a window cover a whole monitor? With ``is_fullscreen_on_monitor``'s 1 px of slack."""
    slack = 1
    return (r[0] <= monitor[0] + slack and r[1] <= monitor[1] + slack
            and r[2] >= monitor[2] - slack and r[3] >= monitor[3] - slack)


def _merge_spans(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for a, b in sorted(s for s in spans if s[1] > s[0]):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _shown_width(rect: Rect, band: float, front: Iterable[tuple[int, Rect, bool]]) -> float:
    """How much of a window's top edge the windows ``front`` of it leave showing, by the rule
    ``Cap.covered`` goes by: one covers the columns it spans if it reaches into the ``band`` px
    above the edge. What the pool ranks windows by - it is where the pile would be."""
    left, top, right, _bottom = rect
    spans = [(float(max(r[0], left)), float(min(r[2], right))) for _h, r, _z in front
             if r[1] < top and r[3] > top - band]
    return (right - left) - sum(b - a for a, b in _merge_spans(spans))


def clip(rect: Rect, occluders: Iterable[Rect]) -> list[Rect]:
    """What of ``rect`` the ``occluders`` leave showing, as rectangles that do not overlap: the
    view's clip for a cap strip (``rect`` its :meth:`Cap.extent`, ``occluders`` its
    :attr:`Cap.occluders`), as a ``QRegion`` or a mask. Empty when it is all hidden. Pure, and
    small: a strip has a few windows in front of it at most."""
    pieces = [tuple(rect)] if _area(rect) > 0 else []
    for o in occluders:
        out: list[Rect] = []
        for p in pieces:
            if _intersect(p, o) is None:
                out.append(p)
                continue
            if p[1] < o[1]:
                out.append((p[0], p[1], p[2], o[1]))     # the band above it
            if o[3] < p[3]:
                out.append((p[0], o[3], p[2], p[3]))     # the band below it
            top, bottom = max(p[1], o[1]), min(p[3], o[3])
            if p[0] < o[0]:
                out.append((p[0], top, o[0], bottom))    # beside it, left
            if o[2] < p[2]:
                out.append((o[2], top, p[2], bottom))    # beside it, right
        pieces = out
    return pieces


def _merge_boxes(boxes: list[Rect]) -> list[Rect]:
    """Merge any two boxes whose union is barely bigger than the pair (``guard_fx._Dirty.rects``'s
    rule): every separate repaint has a fixed cost, but a union of two far-apart boxes is a near
    full-window flush. There are only ever a few, one per stroke."""
    out = list(boxes)
    merged = True
    while merged and len(out) > 1:
        merged = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                u = _union(out[i], out[j])
                if _area(u) <= (_area(out[i]) + _area(out[j])) * 1.25 + 4096:
                    out[i] = u
                    del out[j]
                    merged = True
                    break
            if merged:
                break
    return out
