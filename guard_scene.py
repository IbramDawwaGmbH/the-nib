"""The Away Guard's show: giant pixel eyes, Nib on a little stage, and his running commentary.

Everything here is *painting and choreography* - no window flags, no topmost games, no hooks.
``guard_overlay`` owns the windows and hosts a :class:`GuardScene` inside the primary monitor's
overlay; this module only decides what goes on inside it. That split is deliberate: the security
half of the guard has to be boring and auditable, and the funny half is free to be as fiddly as it
likes without anyone having to re-read it for holes.

The pieces, from the inside out:

``PhraseBag`` / ``load_phrases``
    A shuffle bag per category, so a line never repeats until every other line in its category has
    been shown. Categories (``assets/guard_phrases.json``; a user file of the same name in the
    settings folder is merged in, never replacing): ``idle``, ``pointer_near``, ``poked``,
    ``clicked``, ``typing``, ``wrong_1``, ``wrong_2``, ``blackout``, ``unlock``, ``peek_return``,
    ``sleeping``, ``coffee``, ``counting``. ``{minutes}`` is the minutes guarded so far.

``EyesModel`` / ``EyesPainter``
    Pure state, then pure painting. The model is driven entirely by the ``now``/``dt`` it is handed
    and the ``random.Random`` it was given - it never reads a clock or the global RNG - which is
    what lets the smoke test replay ten minutes of blinking from a seed and count the double blinks.
    The eyes move like eyes: saccades (quick jumps on a slightly under-damped spring) between
    fixations, smooth pursuit only for a slow pointer, eased blinks that squash the eye, pupils
    that dilate with the mood, and a brow layer that does most of the acting. Saccades are also
    the cheap option - a still eye repaints nothing, where a gliding one repaints every frame.

``StageDirector``
    Picks what Nib is doing: a weighted routine every 6-15 s (never the same one twice running),
    interrupted by reactions. Same determinism rule as the eyes. It only names *states*; the scene
    turns those into frames with the existing ``Animator``.

``GuardScene``
    The composed widget. One 30 FPS timer (15 FPS after a minute without input, 10 FPS while
    everyone's asleep) drives the lot, and it repaints only the rectangles that changed - a full-widget ``update()`` would make the overlay
    re-blit the whole full-screen fog underneath thirty times a second.

``MiniEyes`` / ``PeekChip``
    The small eyes for the other monitors, and the countdown chip shown during a Peek.

Sounds are only *requested* (``sound_requested``) using names from ``sound.EVENTS``; whoever owns
the app's ``Sounds`` plays them, so the global mute is respected without this module knowing it.
"""

from __future__ import annotations

import json
import logging
import math
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from animator import _BODY, _FEET, _HIGHLIGHT, _SHADOW, Animator, SpriteSheet

log = logging.getLogger(__name__)

# --- palette --------------------------------------------------------------------------------
# Body tones come from animator (the same constants the skins remap); the rest are the sheet's own
# palette letters in tools/gen_sprites.py: O (outline/eyes), L (light detail), A (angry accent).

_OUTLINE = (0x2C, 0x12, 0x06)
_LIGHT = (0xF5, 0xED, 0xE6)
_ANGRY = (0xC0, 0x39, 0x2B)

C_BODY = QColor(*_BODY)
C_OUTLINE = QColor(*_OUTLINE)
C_SCLERA = QColor(*_LIGHT)
C_VEIN = QColor(*_ANGRY)
# Blackout mode: everything goes dark except the pupils, which glow.
C_RED_OUTLINE = QColor(0x3A, 0x06, 0x04)
C_RED_SCLERA = QColor(0x12, 0x02, 0x02)
C_RED_LID = QColor(0x24, 0x04, 0x03)
C_RED_PUPIL = QColor(0xFF, 0x2A, 0x1A)
C_RED_CORE = QColor(0xFF, 0xC8, 0xB0)
C_RED_GLOW = (QColor(0xFF, 0x20, 0x10, 70), QColor(0xFF, 0x20, 0x10, 34))

ACCENT = "#D9552E"  # same accent as bubble.py

MOODS = ("calm", "squint", "veins", "red", "happy", "stare")

# --- phrases --------------------------------------------------------------------------------

PHRASES_FILE = "guard_phrases.json"

#: Used when the asset file is missing or broken, so the scene never has an empty bag to draw from.
_BUILTIN_PHRASES: dict[str, list[str]] = {
    "idle": ["Agents at work. Please do not feed the agents."],
    "pointer_near": ["Nope."],
    "poked": ["Poking me won't unlock anything. I checked."],
    "clicked": ["The PIN goes down there. Not on me."],
    "typing": ["I'm not looking. I'm not looking."],
    "wrong_1": ["Hmm. That's not it."],
    "wrong_2": ["Strike two. I'm writing this down."],
    "blackout": ["I see you."],
    "unlock": ["Welcome back, boss!"],
    "peek_return": ["Back already?"],
    "sleeping": ["Zzz… (still guarding) …zzz"],
    "coffee": ["Guarding is thirsty work."],
    "counting": ["Guarded for {minutes} min. Not that I'm counting."],
}


class _KeepUnknown(dict):
    """``format_map`` helper: an unknown ``{placeholder}`` in a user's line stays as written.

    A hand-edited phrase file is exactly where a stray brace turns up, and a KeyError in the
    paint path would take the whole overlay down over a typo.
    """

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _format(text: str, fmt: dict[str, Any]) -> str:
    try:
        return text.format_map(_KeepUnknown(fmt))
    except (ValueError, IndexError, AttributeError):  # "{", "{0}", "{a.b}" ...
        return text


class PhraseBag:
    """A shuffle bag per category: no line repeats until the whole category has been shown.

    Refills reshuffle, and if the refill would put the line just shown first, it is swapped away -
    otherwise the bag boundary is exactly where a back-to-back repeat sneaks in.
    """

    def __init__(self, phrases: dict[str, list[str]], rng: random.Random) -> None:
        self._phrases = {k: list(v) for k, v in phrases.items() if v}
        self._rng = rng
        self._bags: dict[str, list[str]] = {}
        self._last: dict[str, str] = {}

    def categories(self) -> list[str]:
        return list(self._phrases)

    def next(self, category: str, **fmt: Any) -> str:
        """The next line for ``category``, formatted. Unknown category -> ``idle``; none -> ``""``."""
        pool = self._phrases.get(category)
        if not pool:
            category = "idle"
            pool = self._phrases.get(category)
            if not pool:
                return ""
        bag = self._bags.get(category)
        if not bag:
            bag = list(pool)
            self._rng.shuffle(bag)
            # bag.pop() takes from the end, so the end is "first out".
            if len(bag) > 1 and bag[-1] == self._last.get(category):
                bag[-1], bag[0] = bag[0], bag[-1]
            self._bags[category] = bag
        line = bag.pop()
        self._last[category] = line
        return _format(line, fmt)


def _read_phrase_file(path: Path) -> dict[str, list[str]]:
    """One phrase file -> {category: [lines]}. Keys starting with ``_`` are comments."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("top level must be an object")
    out: dict[str, list[str]] = {}
    for key, value in data.items():
        if not isinstance(key, str) or key.startswith("_"):
            continue
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            log.warning("%s: category %r is not a list; skipped", path.name, key)
            continue
        out[key] = [s.strip() for s in value if isinstance(s, str) and s.strip()]
    return out


def load_phrases(assets_dir: Path, user_dir: Path | None) -> dict[str, list[str]]:
    """Built-in lines plus the user's own, merged per category (deduplicated, order kept).

    A missing or malformed file is logged and skipped - the guard must come up even if someone's
    custom phrase file has a trailing comma in it.
    """
    merged: dict[str, list[str]] = {}

    def merge(src: dict[str, list[str]]) -> None:
        for category, lines in src.items():
            bucket = merged.setdefault(category, [])
            for line in lines:
                if line not in bucket:
                    bucket.append(line)

    builtin = Path(assets_dir) / PHRASES_FILE
    try:
        merge(_read_phrase_file(builtin))
    except (OSError, ValueError) as exc:
        log.warning("could not read %s (%s); using the built-in fallback lines", builtin, exc)
    # Any category the asset lacks still gets its fallback line, so no reaction is ever silent.
    for category, lines in _BUILTIN_PHRASES.items():
        if not merged.get(category):
            merged[category] = list(lines)

    if user_dir is not None:
        custom = Path(user_dir) / PHRASES_FILE
        if custom.is_file():
            try:
                merge(_read_phrase_file(custom))
                log.info("merged custom guard phrases from %s", custom)
            except (OSError, ValueError) as exc:
                log.warning("ignoring %s: %s", custom, exc)
    return {k: v for k, v in merged.items() if v}


# --- the eyes: model ------------------------------------------------------------------------

#: Lid opening at rest. Below 1.0 so "wide" (+20 %) has somewhere to go.
LID_REST = 0.82
LID_WIDE = 1.0
LID_SLEEPY = 0.45
LID_SQUINT = 0.6          # wrong attempt 1
LID_SLITS = 0.45          # wrong attempt 2 (with the lower lid up a row, a slit)
#: Pupil travel: a pointer this many px from the eyes' centre is "as far as they can look".
TRACK_RANGE_PX = 520.0
#: Smooth pursuit: fraction of the remaining distance covered per 30 FPS frame (plan: 15 %).
#: Only used while the pointer creeps along slowly - anything quicker gets a saccade.
EASE_TRACK = 0.15
EASE_RED = 0.05           # blackout: slow, heavy, no saccades - a predator, not a person
EASE_IDLE = 0.08          # asleep: the eyes roll back down under the lids
EASE_SIDE_EYE = 0.035     # the slow, deliberate side-eye is a glide on purpose
EASE_LID = 0.25
EASE_BROW = 0.35
EASE_DILATE = 0.12        # pupils respond in ~0.3 s, like real ones
IDLE_GAZE_S = 2.0
SLEEPY_S = 60.0
SLEEP_S = 300.0
FAST_POINTER_PX_S = 2400.0
WIDEN_S = 1.0
GLANCE_S = 0.7
STARTLE_S = 0.45
PEEK_EVERY_S = (32.0, 48.0)   # "every ~40 s one eye pops open"
PEEK_S = 2.6
BLINK_EVERY_S = (2.0, 6.0)
P_SLOW_BLINK = 0.05
P_DOUBLE_BLINK = 0.10
#: Half-blinks are counted apart from ``blink_counts`` (see ``half_blink_count``), so the
#: 10 % double / 5 % slow shares the plan asks for stay shares of the *full* blinks.
P_HALF_BLINK = 0.08

# Saccades. Real eyes do not glide after a target: they jump (a saccade, 40-70 ms), hold still
# (a fixation, with a tiny drift), and jump again. The jump is a slightly under-damped spring, so
# a big one lands a hair past the target and settles back - the overshoot that reads as "alive".
SACCADE_MIN = 0.08            # a gaze error below this never triggers a jump
SACCADE_SMALL = 0.2           # ...below this one waits for the fixation to run out first
SACCADE_REFRACTORY_S = 0.15   # eyes cannot fire saccades back to back
FIXATION_S = (0.3, 2.0)       # free looking (idle gaze)
TRACK_FIXATION_S = (0.3, 0.7) # watching the pointer: corrections come sooner
PURSUIT_PX_S = (25.0, 320.0)  # pointer speeds the eyes follow smoothly instead of jumping
PURSUIT_MAX_ERR = 0.3         # ...as long as they are not already too far behind
SACCADE_ZETA = 0.55           # damping ratio: ~12 % overshoot, settled within ~4 frames
SACCADE_OMEGA = (70.0, 48.0)  # rad/s for a tiny / a full-width jump: ~40 ms .. ~70 ms
DRIFT_AMP = 0.045             # fixational drift, in pupil-travel units
DRIFT_EVERY_S = (0.35, 0.9)
EASE_DRIFT = 0.05
BALL_EASE = 0.3               # pursuit gain on Nib's football - it is slow and predictable
BALL_CATCHUP = 0.25           # ...but if it gets this far ahead, a catch-up saccade
BALL_AFTER_INPUT_S = 0.6      # a moving pointer outranks the ball
BALL_BOB = 0.12               # px of bounce per unit of gaze, as a fraction of the eye span

# Cross-eyed: the pointer parked in the gap between the eyes, close up.
CROSS_ZONE = (0.3, 0.28)      # half-width / half-height of the zone, as fractions of the eye span
CROSS_DWELL_S = 0.25
CROSS_X = 0.85
EASE_CROSS = 0.3
WINCE_S = 0.4
REALLY_S = 1.4

#: Pupil size target per mood, 0 (pin-prick) .. 1 (wide open, curious).
DILATION = {"calm": 0.5, "squint": 0.3, "veins": 0.1, "red": 0.0, "happy": 0.5, "stare": 1.0}

# Blink profiles. Closing is the quick part (ease-in, so the lid accelerates shut), opening the
# slow one (ease-out): a symmetric triangle reads as mechanical.
_SINGLE_S = 0.2
_HALF_S = 0.18
_HALF_DEPTH = 0.55
_DOUBLE_GAP_S = 0.1
_SLOW_S = 0.95
_LAZY_S = 1.6             # the sleepy version of the slow blink
_LAG_S = 1.0 / 30.0       # one frame: the "other" eye is sometimes a beat late
P_LAG = 0.3
SQUASH_ROWS = 2           # a closing eye squashes this many rows at full closure


def _blink_curve(t: float, length: float, *, depth: float = 1.0, close: float = 0.32,
                 hold: float = 0.12) -> float:
    """Closure (0 open .. ``depth``) ``t`` seconds into a blink of ``length``.

    ``close`` and ``hold`` are fractions of ``length``; the rest is the (slower) opening.
    """
    if t <= 0.0 or t >= length:
        return 0.0
    c, h = length * close, length * hold
    if t < c:
        u = t / c
        return depth * u * u
    if t < c + h:
        return depth
    u = (t - c - h) / max(1e-6, length - c - h)
    return depth * (1.0 - u) * (1.0 - u)


def _clamp_disc(x: float, y: float) -> tuple[float, float]:
    d = math.hypot(x, y)
    if d > 1.0:
        return x / d, y / d
    return x, y


def _ease(f: float, dt: float) -> float:
    """Per-frame easing fraction ``f`` (at 30 FPS) turned into a frame-rate-independent one."""
    return 1.0 - (1.0 - f) ** max(0.0, dt * 30.0)


def _spring(x: float, v: float, target: float, omega: float, zeta: float,
            dt: float) -> tuple[float, float]:
    """One exact step of an under-damped spring pulling ``x`` (velocity ``v``) to ``target``.

    Closed form rather than Euler: at 30 FPS a 70 rad/s spring is ``omega * dt`` > 2, where any
    explicit integrator explodes, and a 0.5 s hitch (system wake) must land, not fling.
    """
    if dt <= 0.0:
        return x, v
    a = x - target
    zo = zeta * omega
    wd = omega * math.sqrt(1.0 - zeta * zeta)
    b = (v + zo * a) / wd
    e = math.exp(-zo * dt)
    c, s = math.cos(wd * dt), math.sin(wd * dt)
    return target + e * (a * c + b * s), e * ((b * wd - zo * a) * c - (a * wd + zo * b) * s)


#: Brow poses: (lift rows, tilt rows - inner end relative to outer, arch rows) for the left and
#: right eye. Positive lift is up; a negative tilt drops the inner end - the cross "V".
BROWS: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    "calm": ((-1.0, 0.0, 0.0), (-1.0, 0.0, 0.0)),
    "suspicious": ((1.0, -2.0, 0.0), (2.5, 0.0, 1.0)),
    "surprised": ((3.0, 0.5, 1.0), (3.0, 0.5, 1.0)),
    "angry": ((3.0, -4.0, 0.0), (3.0, -4.0, 0.0)),
    "sleepy": ((-1.0, 1.0, 0.0), (-1.0, 1.0, 0.0)),
    "squeezed": ((1.5, -3.0, 0.0), (1.5, -3.0, 0.0)),
    "wince": ((2.5, -4.0, 0.0), (2.5, -4.0, 0.0)),
    "happy": ((2.0, 0.0, 1.5), (2.0, 0.0, 1.5)),
    "confused": ((0.0, 2.0, 0.0), (0.0, 2.0, 0.0)),
}
#: "Really?": one brow way up after a wrong attempt, the other pressed down. Index = raised eye.
_REALLY = (((3.5, 0.5, 1.5), (1.0, -2.0, 0.0)), ((1.0, -2.0, 0.0), (3.5, 0.5, 1.5)))


class EyesModel:
    """What the eyes are doing. Pure state - :class:`EyesPainter` turns it into pixels.

    Exposed for the painter and the tests: ``pupil`` (x, y in -1..1, inside the unit disc - the
    shared gaze), ``pupil_l`` / ``pupil_r`` (per eye, which differ only when ``cross`` > 0),
    ``pupil_speed`` (units/s: spikes during a saccade, ~0 in a fixation), ``lid_open`` (0..1, the
    leading eye) with ``lids`` (left, right), ``squash`` (left, right; 0..1 vertical squash of a
    blinking eye), ``dilation`` (0..1), ``brows`` ((lift, tilt, arch) per eye) with ``brow_pose``,
    ``blinking``, ``sleeping``, ``sleepy``, ``squeezed`` (typing: "not looking!"), ``wincing``,
    ``crossed`` / ``cross``, ``following_ball``, ``one_eye_peek`` with ``peek_eye`` (-1 left / +1
    right) and ``peek_lid``, ``glancing``, ``widened``, ``shake`` (-1..1), ``z_phase`` and ``mood``.
    """

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng
        self.mood = "calm"
        self.pupil_x = 0.0
        self.pupil_y = 0.0
        self.pupil_l = (0.0, 0.0)
        self.pupil_r = (0.0, 0.0)
        self.pupil_speed = 0.0
        self.lid_open = LID_REST
        self.lids = (LID_REST, LID_REST)
        self.squash = (0.0, 0.0)
        self.dilation = DILATION["calm"]
        self.brows = BROWS["calm"]
        self.brow_pose = "calm"
        self.blinking = False
        self.blink_kind = ""
        self.sleeping = False
        self.sleepy = False
        self.squeezed = False
        self.wincing = False
        self.crossed = False
        self.cross = 0.0
        self.following_ball = False
        self.one_eye_peek = False
        self.peek_eye = 1
        self.peek_lid = 0.0
        self.glancing = False
        self.widened = False
        self.shake = 0.0
        self.z_phase = 0.0
        #: Blinks started, by kind - read by the smoke test to check the 10 % / 5 % split.
        self.blink_counts = {"single": 0, "double": 0, "slow": 0}
        self.half_blink_count = 0
        self.saccade_count = 0
        self.peek_count = 0

        self._base = LID_REST
        self._started = False
        self._next_blink = 0.0
        self._blink_start = -1.0
        self._blink_len = 0.0
        self._blink_lag = 0           # 0, or the eye (-1 / +1) that trails by a frame
        self._gaze = (0.0, 0.0)
        self._gaze_glide = False
        self._next_gaze = 0.0
        self._widen_until = -1.0
        self._glance_until = -1.0
        self._shake_until = -1.0
        self._near_prev = False
        self._next_peek = 0.0
        self._peek_until = -1.0
        self._peek_gaze = (0.0, 0.0)
        self._next_peek_gaze = 0.0
        self._forced_blink = False
        # Gaze dynamics: the fixation point the spring pulls to, plus a little drift around it.
        self._fix = (0.0, 0.0)
        self._vel = (0.0, 0.0)
        self._omega = SACCADE_OMEGA[0]
        self._fix_until = 0.0
        self._last_saccade = -1e9
        self._drift = (0.0, 0.0)
        self._drift_to = (0.0, 0.0)
        self._next_drift = 0.0
        self._cross_since: float | None = None
        self._ball_ref: tuple[float, float] | None = None
        self._wince_pending = False
        self._wince_until = -1.0
        self._really_pending = False
        self._really_until = -1.0
        self._really_eye = 1
        self._really_flip = False

    # --- public -------------------------------------------------------------------

    @property
    def pupil(self) -> tuple[float, float]:
        return self.pupil_x, self.pupil_y

    def set_mood(self, mood: str) -> None:
        if mood not in MOODS:
            log.debug("unknown eye mood %r; using calm", mood)
            mood = "calm"
        if mood == self.mood:
            return
        self.mood = mood
        if mood in ("red", "happy", "stare"):
            # These moods own the lids outright; a blink in progress would fight them.
            self._blink_start = -1.0
            self.blinking = False
        if mood == "stare":
            self._base = LID_WIDE

    def bump(self) -> None:
        """A forced blink on the next update - the football bouncing off a lid."""
        self._forced_blink = True

    def wince(self) -> None:
        """Someone clicked the eyes themselves: squeeze shut, brows down, for ~0.4 s."""
        self._wince_pending = True

    def really(self) -> None:
        """A wrong attempt: one brow goes up - "really?" - for a second or so."""
        self._really_pending = True

    # --- blinking -----------------------------------------------------------------

    def _blinks_allowed(self, typing: bool) -> bool:
        return not (self.sleeping or typing or self.wincing or self.mood in ("red", "happy", "stare"))

    def _start_blink(self, now: float, kind: str) -> None:
        self.blink_kind = kind
        if kind == "half":
            self.half_blink_count += 1
        else:
            self.blink_counts[kind] += 1
        self._blink_start = now
        self._blink_len = {
            "single": _SINGLE_S,
            "half": _HALF_S,
            "double": _SINGLE_S * 2 + _DOUBLE_GAP_S,
            "slow": _LAZY_S if self.sleepy else _SLOW_S,
        }[kind]
        # Mostly perfectly in step; now and then one eye trails by a single frame.
        self._blink_lag = self._rng.choice((-1, 1)) if self._rng.random() < P_LAG else 0
        self._next_blink = now + self._blink_len + self._rng.uniform(*BLINK_EVERY_S)

    def _closure_at(self, t: float) -> float:
        """How shut a blink has the lids ``t`` seconds after it started (0 open .. 1 shut)."""
        n = self._blink_len
        if self.blink_kind == "double":
            if t < _SINGLE_S:
                return _blink_curve(t, _SINGLE_S)
            return _blink_curve(t - _SINGLE_S - _DOUBLE_GAP_S, _SINGLE_S)
        if self.blink_kind == "half":
            return _blink_curve(t, n, depth=_HALF_DEPTH)
        if self.blink_kind == "slow":
            # Close, hold, open: the "suspicious" one; drawn out and droopy when sleepy.
            return _blink_curve(t, n, close=0.3, hold=0.3 if n == _SLOW_S else 0.2)
        return _blink_curve(t, n)

    def _closures(self, now: float) -> tuple[float, float]:
        """(left, right) blink closure right now; ends the blink once both eyes are open."""
        if self._blink_start < 0:
            return 0.0, 0.0
        t = now - self._blink_start
        if t >= self._blink_len + _LAG_S:
            self._blink_start = -1.0
            return 0.0, 0.0
        left = self._closure_at(t - (_LAG_S if self._blink_lag == -1 else 0.0))
        right = self._closure_at(t - (_LAG_S if self._blink_lag == 1 else 0.0))
        return left, right

    # --- gaze ---------------------------------------------------------------------

    def _saccade(self, now: float, target: tuple[float, float], fixation: tuple[float, float]) -> None:
        amp = min(1.0, math.hypot(target[0] - self._fix[0], target[1] - self._fix[1]) / 1.4)
        # Bigger jumps take longer (the "main sequence"): 70 rad/s for a twitch, 48 for a sweep.
        self._omega = SACCADE_OMEGA[0] + (SACCADE_OMEGA[1] - SACCADE_OMEGA[0]) * amp
        self._fix = target
        self._last_saccade = now
        self._fix_until = now + self._rng.uniform(*fixation)
        self.saccade_count += 1

    def _move_gaze(self, dt: float, now: float, target: tuple[float, float], how: str, *,
                   ease: float = EASE_TRACK, pointer_speed: float = 0.0) -> None:
        """Move the pupils toward ``target`` the way ``how`` says.

        ``glide``: plain exponential easing (blackout, side-eye, sleep). ``track``: the pointer -
        smooth pursuit while it creeps, saccades when it jumps. ``ball``: pursuit with catch-up
        saccades. ``look``: free looking - a saccade to every new target, then a fixation.
        """
        x, y = self.pupil_x, self.pupil_y
        if how == "glide":
            k = _ease(ease, dt)
            nx, ny = x + (target[0] - x) * k, y + (target[1] - y) * k
            self._vel = ((nx - x) / dt, (ny - y) / dt) if dt > 0 else (0.0, 0.0)
            self._fix = (nx, ny)
            self._drift = self._drift_to = (0.0, 0.0)
            self.pupil_x, self.pupil_y = _clamp_disc(nx, ny)
            self.pupil_speed = math.hypot(*self._vel)
            return

        err = math.hypot(target[0] - self._fix[0], target[1] - self._fix[1])
        ready = now - self._last_saccade >= SACCADE_REFRACTORY_S
        if how == "ball":
            if err >= BALL_CATCHUP and ready:
                self._saccade(now, target, TRACK_FIXATION_S)
            else:
                k = _ease(BALL_EASE, dt)
                self._fix = (self._fix[0] + (target[0] - self._fix[0]) * k,
                             self._fix[1] + (target[1] - self._fix[1]) * k)
        else:
            lo, hi = PURSUIT_PX_S
            pursuing = how == "track" and lo <= pointer_speed < hi and err < PURSUIT_MAX_ERR
            if pursuing:
                k = _ease(ease, dt)
                self._fix = (self._fix[0] + (target[0] - self._fix[0]) * k,
                             self._fix[1] + (target[1] - self._fix[1]) * k)
            elif err >= SACCADE_MIN and ready and (err >= SACCADE_SMALL or now >= self._fix_until):
                self._saccade(now, target, TRACK_FIXATION_S if how == "track" else FIXATION_S)

        # Fixational drift: a slow wander of a fraction of a block around the fixation point.
        if now >= self._next_drift:
            self._next_drift = now + self._rng.uniform(*DRIFT_EVERY_S)
            self._drift_to = (self._rng.uniform(-DRIFT_AMP, DRIFT_AMP),
                              self._rng.uniform(-DRIFT_AMP, DRIFT_AMP) * 0.6)
        k = _ease(EASE_DRIFT, dt)
        self._drift = (self._drift[0] + (self._drift_to[0] - self._drift[0]) * k,
                       self._drift[1] + (self._drift_to[1] - self._drift[1]) * k)

        gx, gy = _clamp_disc(self._fix[0] + self._drift[0], self._fix[1] + self._drift[1])
        nx, vx = _spring(x, self._vel[0], gx, self._omega, SACCADE_ZETA, dt)
        ny, vy = _spring(y, self._vel[1], gy, self._omega, SACCADE_ZETA, dt)
        if abs(nx - gx) < 1e-3 and abs(ny - gy) < 1e-3 and abs(vx) + abs(vy) < 0.05:
            # Settled: snap, so a spring that never quite stops can't keep forcing repaints.
            nx, ny, vx, vy = gx, gy, 0.0, 0.0
        self._vel = (vx, vy)
        self.pupil_x, self.pupil_y = _clamp_disc(nx, ny)
        self.pupil_speed = math.hypot(vx, vy)

    def _ball_target(self, dt: float, ball: QPointF, centre: QPointF,
                     eye_span_px: float) -> tuple[float, float]:
        """Gaze target for the football: "down at Nib", plus the bob, exaggerated.

        Nib is far below the eyes, so the plain direction to the ball pins the pupils at the
        bottom of their travel and the keepy-uppies vanish. Instead the eyes aim at where the ball
        hangs about on average and add its bounce on top, scaled to the size the eyes are drawn.
        """
        if self._ball_ref is None:
            self._ball_ref = (ball.x(), ball.y())
        k = _ease(0.02, dt)
        rx = self._ball_ref[0] + (ball.x() - self._ball_ref[0]) * k
        ry = self._ball_ref[1] + (ball.y() - self._ball_ref[1]) * k
        self._ball_ref = (rx, ry)
        bx, by = _clamp_disc((rx - centre.x()) / TRACK_RANGE_PX, (ry - centre.y()) / TRACK_RANGE_PX)
        bob_px = max(30.0, eye_span_px * BALL_BOB) if eye_span_px > 0 else 80.0
        return _clamp_disc(bx * 0.75 + (ball.x() - rx) / bob_px * 0.5,
                           by * 0.6 + (ball.y() - ry) / bob_px)

    # --- the per-frame step -------------------------------------------------------

    def update(
        self,
        dt: float,
        now: float,
        pointer: QPointF,
        centre: QPointF,
        *,
        pointer_speed: float,
        idle_s: float,
        typing: bool,
        near_field: bool,
        ball: QPointF | None = None,
        eye_span_px: float = 0.0,
    ) -> None:
        """Advance to ``now``.

        ``ball`` is where Nib's football is (same coordinates as ``pointer``) while he is playing,
        else None. ``eye_span_px`` is the distance between the two eye centres in those
        coordinates; 0 turns the cross-eyed trick off (the model can't know how big it is drawn).
        """
        dt = max(0.0, min(dt, 0.5))  # a wake from system sleep must not teleport anything
        if not self._started:
            self._started = True
            self._next_blink = now + self._rng.uniform(*BLINK_EVERY_S)
            self._next_gaze = now
        self.z_phase = now

        # Sleep: 60 s sleepy, 5 min asleep. Red/happy/stare never doze - a blackout is not restful.
        dozy_allowed = not typing and self.mood not in ("red", "happy", "stare")
        asleep = dozy_allowed and idle_s >= SLEEP_S
        if self.sleeping and not asleep:
            # Any input while sleeping: snap open with a little shake.
            self._shake_until = now + STARTLE_S
            self._widen_until = now + WIDEN_S
            self._base = LID_WIDE
            self._peek_until = -1.0
        if asleep and not self.sleeping:
            self._next_peek = now + self._rng.uniform(*PEEK_EVERY_S)
        self.sleeping = asleep
        self.sleepy = dozy_allowed and SLEEPY_S <= idle_s < SLEEP_S

        if pointer_speed >= FAST_POINTER_PX_S and idle_s < 0.5:
            self._widen_until = now + WIDEN_S
        if near_field and not self._near_prev:
            self._glance_until = now + GLANCE_S
        self._near_prev = near_field
        if self._wince_pending:
            self._wince_pending = False
            self._wince_until = now + WINCE_S
            self._blink_start = -1.0
        if self._really_pending:
            self._really_pending = False
            self._really_until = now + REALLY_S
            # Alternate sides, starting on a random one: the same brow every time looks canned.
            if not self._really_flip:
                self._really_flip = True
                self._really_eye = self._rng.choice((0, 1))
            else:
                self._really_eye = 1 - self._really_eye
        self.widened = now < self._widen_until
        self.glancing = now < self._glance_until and not typing
        self.squeezed = typing and self.mood not in ("red", "happy")
        self.wincing = now < self._wince_until and self.mood not in ("red", "happy")

        dx = pointer.x() - centre.x()
        dy = pointer.y() - centre.y()

        # --- cross-eyed: pointer parked in the gap between the eyes, close up
        crossable = (eye_span_px > 0 and not (self.sleeping or self.squeezed or self.wincing)
                     and self.mood not in ("red", "happy"))
        in_zone = (crossable and abs(dx) < CROSS_ZONE[0] * eye_span_px
                   and abs(dy) < CROSS_ZONE[1] * eye_span_px and pointer_speed < 600.0)
        if in_zone:
            if self._cross_since is None:
                self._cross_since = now
        else:
            self._cross_since = None
        self.crossed = in_zone and now - (self._cross_since or now) >= CROSS_DWELL_S
        self.cross += ((1.0 if self.crossed else 0.0) - self.cross) * _ease(EASE_CROSS, dt)
        if self.cross < 0.01:
            self.cross = 0.0

        # --- where the pupils want to be, and how they get there
        self.following_ball = False
        if self.sleeping:
            if now >= self._next_peek and self._peek_until < now:
                self._peek_until = now + PEEK_S
                self._next_peek = now + PEEK_S + self._rng.uniform(*PEEK_EVERY_S)
                self.peek_eye = self._rng.choice((-1, 1))
                self.peek_count += 1
                self._next_peek_gaze = now
            self.one_eye_peek = now < self._peek_until
            if self.one_eye_peek:
                if now >= self._next_peek_gaze:  # looks around, quickly
                    self._peek_gaze = _clamp_disc(self._rng.uniform(-1, 1), self._rng.uniform(-0.6, 0.6))
                    self._next_peek_gaze = now + self._rng.uniform(0.5, 0.9)
                self._move_gaze(dt, now, self._peek_gaze, "look")
            else:
                self._move_gaze(dt, now, (0.0, 0.3), "glide", ease=EASE_IDLE)
        else:
            self.one_eye_peek = False
            track = _clamp_disc(dx / TRACK_RANGE_PX, dy / TRACK_RANGE_PX)
            if self.squeezed or self.wincing:
                self._move_gaze(dt, now, (0.0, 0.0), "glide", ease=0.3)
            elif self.glancing:
                self._move_gaze(dt, now, (0.0, 0.9), "look")  # down at the PIN field
            elif self.mood == "red":
                self._move_gaze(dt, now, track, "glide", ease=EASE_RED)
            elif (ball is not None and self.mood != "stare" and idle_s >= BALL_AFTER_INPUT_S
                  and not self.crossed):
                self.following_ball = True
                self._move_gaze(dt, now, self._ball_target(dt, ball, centre, eye_span_px), "ball")
            elif self.mood == "stare" or idle_s < IDLE_GAZE_S or self.crossed:
                self._move_gaze(dt, now, track, "track", pointer_speed=pointer_speed)
            else:
                if now >= self._next_gaze:
                    if self._rng.random() < 0.2:  # the slow side-eye
                        self._gaze = (self._rng.choice((-0.95, 0.95)), self._rng.uniform(-0.1, 0.15))
                        self._gaze_glide = True
                    else:
                        self._gaze = _clamp_disc(self._rng.uniform(-0.8, 0.8), self._rng.uniform(-0.5, 0.5))
                        self._gaze_glide = False
                    self._next_gaze = now + self._rng.uniform(1.5, 4.0)
                if self._gaze_glide:
                    self._move_gaze(dt, now, self._gaze, "glide", ease=EASE_SIDE_EYE)
                else:
                    self._move_gaze(dt, now, self._gaze, "look")
        if not self.following_ball:
            self._ball_ref = None
        # Per-eye pupils: identical unless crossing, when each eye turns in toward the nose.
        c = self.cross
        y = self.pupil_y * (1.0 - c) + 0.2 * c
        self.pupil_l = _clamp_disc(self.pupil_x * (1.0 - c) + CROSS_X * c, y)
        self.pupil_r = _clamp_disc(self.pupil_x * (1.0 - c) - CROSS_X * c, y)

        # --- lids
        if self.sleeping or self.squeezed:
            base = 0.0
        elif self.mood == "happy":
            base = LID_WIDE
        elif self.mood == "stare" or self.widened:
            base = LID_WIDE
        elif self.mood == "red":
            base = 0.9
        elif self.sleepy:
            base = LID_SLEEPY
        else:
            base = LID_REST
        if self.mood == "squint":
            base = min(base, LID_SQUINT)
        elif self.mood == "veins":
            base = min(base, LID_SLITS)
        self._base += (base - self._base) * _ease(EASE_LID, dt)
        if self.squeezed or self.sleeping:
            self._base = base  # shutting is a snap, not a fade

        if self._blinks_allowed(typing):
            if self._forced_blink and self._blink_start < 0:
                self._start_blink(now, "single")
            elif now >= self._next_blink and self._blink_start < 0:
                roll = self._rng.random()
                if self.sleepy:
                    kind = "slow"  # sleepy eyes only do the slow (lazy) one
                elif roll < P_SLOW_BLINK:
                    kind = "slow"
                elif roll < P_SLOW_BLINK + P_DOUBLE_BLINK:
                    kind = "double"
                elif roll < P_SLOW_BLINK + P_DOUBLE_BLINK + P_HALF_BLINK:
                    kind = "half"
                else:
                    kind = "single"
                self._start_blink(now, kind)
            cl, cr = self._closures(now)
        else:
            cl = cr = 0.0
            self._blink_start = -1.0
            if now >= self._next_blink:
                self._next_blink = now + self._rng.uniform(*BLINK_EVERY_S)
        self._forced_blink = False
        if self.wincing:
            cl = cr = 1.0
        self.blinking = max(cl, cr) > 0.0 and not self.wincing
        lead = cr if self._blink_lag == -1 else cl
        self.lid_open = max(0.0, min(1.0, self._base * (1.0 - lead)))
        if self.sleeping:
            self.lids = (self.peek_lid if self.one_eye_peek and self.peek_eye == -1 else 0.0,
                         self.peek_lid if self.one_eye_peek and self.peek_eye == 1 else 0.0)
        else:
            self.lids = (max(0.0, min(1.0, self._base * (1.0 - cl))),
                         max(0.0, min(1.0, self._base * (1.0 - cr))))
        self.squash = (cl, cr) if not self.sleeping else (0.0, 0.0)

        # Peeking eye: pops open fast, shuts at the end.
        if self.one_eye_peek:
            left = self._peek_until - now
            self.peek_lid = min(1.0, (PEEK_S - left) / 0.15, left / 0.2) * LID_REST
            self.lids = (self.peek_lid if self.peek_eye == -1 else 0.0,
                         self.peek_lid if self.peek_eye == 1 else 0.0)
        else:
            self.peek_lid = 0.0

        if now < self._shake_until:
            remaining = (self._shake_until - now) / STARTLE_S
            self.shake = math.sin(now * 70.0) * remaining
        else:
            self.shake = 0.0

        # --- pupils dilate with the mood; brows follow it
        if self.mood in ("calm", "squint", "veins") and (self.widened or self.crossed):
            want = 0.85  # curious
        elif self.following_ball:
            want = 0.7
        elif self.sleepy:
            want = 0.65
        else:
            want = DILATION.get(self.mood, 0.5)
        self.dilation += (want - self.dilation) * _ease(EASE_DILATE, dt)
        self._update_brows(now, dt)

    def _update_brows(self, now: float, dt: float) -> None:
        if self.wincing:
            pose = "wince"
        elif self.mood == "happy":
            pose = "happy"
        elif self.mood == "red":
            pose = "angry"
        elif self.squeezed:
            pose = "squeezed"
        elif now < self._really_until:
            pose = "really"     # after every wrong attempt, even the second: "...really?"
        elif self.mood == "veins":
            pose = "angry"
        elif self.mood == "stare" or self.widened:
            pose = "surprised"
        elif self.mood == "squint":
            pose = "suspicious"
        elif self.crossed:
            pose = "confused"
        elif self.sleeping or self.sleepy:
            pose = "sleepy"
        else:
            pose = "calm"
        self.brow_pose = pose
        target = _REALLY[self._really_eye] if pose == "really" else BROWS[pose]
        k = 1.0 if pose == "wince" else _ease(EASE_BROW, dt)
        # Brows dip a touch while the lids are down - they're attached to the same face.
        dips = (self.squash[0] * 0.8, self.squash[1] * 0.8) if self.blinking else (0.0, 0.0)
        self.brows = tuple(
            (cur[0] + (want[0] - dip - cur[0]) * k, cur[1] + (want[1] - cur[1]) * k,
             cur[2] + (want[2] - cur[2]) * k)
            for cur, want, dip in zip(self.brows, target, dips)
        )

    def signature(self) -> tuple:
        """A coarse fingerprint: when it has not changed, a repaint would draw the same pixels.

        Rounded to about the painter's block resolution, so sub-block drift and a spring's last
        hundredths don't trigger repaints that would draw exactly what is already there.
        """
        q = 2 * _SPAN_X  # half-block steps of pupil travel
        return (
            round(self.pupil_l[0] * q), round(self.pupil_l[1] * q),
            round(self.pupil_r[0] * q), round(self.pupil_r[1] * q),
            round(self.lids[0] * (EYE_H - 2)), round(self.lids[1] * (EYE_H - 2)),
            round(self.squash[0] * SQUASH_ROWS), round(self.squash[1] * SQUASH_ROWS),
            round(self.dilation * (PUPIL_D[1] - PUPIL_D[0]) * 2),
            tuple(round(v * 2) for brow in self.brows for v in brow),
            self.sleeping, self.squeezed, self.wincing, self.mood,
            self.one_eye_peek and self.peek_eye, round(self.shake * 6),
            int(self.z_phase * 6) if self.sleeping else 0,
        )


# --- the eyes: painter ----------------------------------------------------------------------

EYE_W = 32        # blocks
EYE_H = 22
EYE_GAP = 12
BROW_ROWS = 8     # room above for brows (raised ones climb right to the top)
ZZZ_COLS = 10     # room to the right for the floating Z z z
GRID_W = EYE_W * 2 + EYE_GAP
GRID_H = BROW_ROWS + EYE_H
IRIS_D = 12.0
_DISC_FULL = 0.3
#: Pupil diameter range in blocks: pin-prick (angry, blackout) .. wide (curious, staring).
PUPIL_D = (3.4, 8.2)
#: How far the iris centre can travel from the eye's centre, in blocks.
_SPAN_X = (EYE_W - 2) / 2 - IRIS_D / 2 - 1.5
_SPAN_Y = (EYE_H - 2) / 2 - IRIS_D / 2 + 1.0
#: The specular highlight: (dx, dy, w, h) blocks from the eye's resting centre. It is a reflection
#: of the room's light on the bulging cornea, so it travels only part of the way with the pupil
#: (_GLINT_FOLLOW) - the pupil slides a little under it, the cheapest trick there is for "glassy".
#: It is clipped to the iris: a white fleck out on the white of the eye reads as a smudge.
_GLINT = ((-3, -4, 2, 2), (-1, -2, 1, 1), (3, 2, 1, 1))
_GLINT_FOLLOW = 0.85  # any lower and the big fleck falls off the iris when he looks hard aside
_BROW_COLS = (4, EYE_W - 4)
_BROW_REST = BROW_ROWS - 4     # top row of a resting brow (two rows thick, two clear above the eye)

# Palette beyond the base four: the body ramp from animator for the iris and lid shading, plus a
# couple of sclera shades. All module-level so painting allocates nothing per frame.
C_IRIS_RING = QColor(*_FEET)
C_IRIS = QColor(*_SHADOW)
C_IRIS_LOW = QColor(*_BODY)
C_LID_HI = QColor(*_HIGHLIGHT)
C_LID_EDGE = QColor(*_SHADOW)
C_GLINT = QColor(0xFF, 0xFF, 0xFF)
C_SCLERA_SHADE = QColor(0xE6, 0xD9, 0xCF)
C_LID_SHADOW = (QColor(0x2C, 0x12, 0x06, 72), QColor(0x2C, 0x12, 0x06, 30))
C_BLUSH = QColor(_ANGRY[0], _ANGRY[1], _ANGRY[2], 110)
C_RED_IRIS = QColor(0x4A, 0x08, 0x05)
C_RED_RING = QColor(0x6A, 0x0C, 0x08)
C_RED_BROW = QColor(0x5A, 0x0A, 0x06)


def _spans(w: int, h: int, power: float = 2.6) -> list[tuple[int, int]]:
    """Row spans [left, right) of a chunky superellipse - a pixel-art eye outline."""
    out = []
    for r in range(h):
        yy = abs((r + 0.5) / h * 2.0 - 1.0)
        half = (w / 2.0) * (1.0 - yy ** power) ** (1.0 / power)
        left = int(round(w / 2.0 - half))
        out.append((left, w - left))
    return out


#: Eye outline/interior per height, for the squash of a blink: {h: (outer, inner)}. ``inner`` is
#: indexed by outer row, with empty spans on the outline rows. Built once at import.
_GEOM: dict[int, tuple[list[tuple[int, int]], list[tuple[int, int]]]] = {}
for _h in range(EYE_H - SQUASH_ROWS - 1, EYE_H + 1):
    _GEOM[_h] = (_spans(EYE_W, _h),
                 [(0, 0)] + [(l + 1, r + 1) for l, r in _spans(EYE_W - 2, _h - 2)] + [(0, 0)])
del _h


def _line_blocks(points: tuple[tuple[int, int], ...]) -> list[tuple[int, int]]:
    """The blocks of a polyline, stepped one block at a time (a pixel-art line)."""
    out: list[tuple[int, int]] = []
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        n = max(abs(x1 - x0), abs(y1 - y0), 1)
        for i in range(n + 1):
            block = (round(x0 + (x1 - x0) * i / n), round(y0 + (y1 - y0) * i / n))
            if block not in out:
                out.append(block)
    return out


# Vein paths for the left eye, from its outer corner inward; the right eye's are mirrored.
_VEIN_LINES = (((2, 10), (5, 10), (7, 11), (9, 11)), ((5, 10), (7, 8)),
               ((3, 15), (6, 14), (8, 15)), ((4, 6), (6, 7)))
_VEINS_L = [b for line in _VEIN_LINES for b in _line_blocks(line)]
_VEINS = (_VEINS_L, [(EYE_W - 1 - c, r) for c, r in _VEINS_L])
#: Z glyph, 4x4 blocks.
#: A 5x5 Z: on a 4x4 grid the diagonal lands in the two middle columns and it reads as an I-beam.
_Z = ((0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (3, 1), (2, 2), (1, 3),
      (0, 4), (1, 4), (2, 4), (3, 4), (4, 4))


def _disc_rows(cx: float, cy: float, d: float):
    """Row spans (row, left, right) of a chunky disc of diameter ``d`` centred at (cx, cy).

    A block is in when its centre is inside the circle, with a little slack (``_DISC_FULL``) so
    small discs come out round rather than as diamonds.
    """
    r = d / 2.0 + _DISC_FULL
    for row in range(math.floor(cy - r), math.ceil(cy + r)):
        yc = row + 0.5 - cy
        if abs(yc) >= r:
            continue
        half = math.sqrt(r * r - yc * yc)
        left, right = math.ceil(cx - half - 0.5), math.floor(cx + half - 0.5) + 1
        if right > left:
            yield row, left, right


class EyesPainter:
    """Draws an :class:`EyesModel` as two pixel-art eyes in Nib's palette.

    Everything is filled as horizontal *row spans* rather than block by block: an eye is a few
    dozen ``fillRect`` calls instead of ~700, which is the difference between the scene costing a
    rounding error of one core at 30 FPS and costing a noticeable slice of it.

    Layers, back to front, per eye: (red glow) - outline - sclera with a shaded bottom row -
    veins - iris ring, iris, its lit lower crescent - pupil - the fixed glint - the upper lid with
    its highlight, shadowed edge and the soft shadow it throws on the eye - lower lid - brow.
    """

    @staticmethod
    def fit_scale(rect: QRectF | QRect, *, extra_cols: int = ZZZ_COLS) -> int:
        """Largest integer scale whose grid (2px blocks x scale) fits ``rect``."""
        b = min(rect.width() / (GRID_W + extra_cols * 2), rect.height() / (GRID_H + 2))
        return max(1, int(b // 2))

    @staticmethod
    def bounds(rect: QRectF | QRect, *, scale: int) -> QRect:
        """The pixels :meth:`paint` can touch for this rect and scale - the repaint region."""
        b = 2 * scale
        ox, oy = EyesPainter._origin(rect, b)
        return QRect(ox - 3 * b, oy - 2 * b, (GRID_W + ZZZ_COLS + 6) * b, (GRID_H + 4) * b)

    @staticmethod
    def eye_rects(rect: QRectF | QRect, *, scale: int) -> tuple[QRect, QRect]:
        """The (left, right) eye boxes in ``rect``'s coordinates - for hit-testing clicks."""
        b = 2 * max(1, int(scale))
        ox, oy = EyesPainter._origin(rect, b)
        top = oy + BROW_ROWS * b
        return (QRect(ox, top, EYE_W * b, EYE_H * b),
                QRect(ox + (EYE_W + EYE_GAP) * b, top, EYE_W * b, EYE_H * b))

    @staticmethod
    def eye_span_px(scale: int) -> float:
        """Distance between the two eye centres at ``scale``, in pixels."""
        return float((EYE_W + EYE_GAP) * 2 * max(1, int(scale)))

    @staticmethod
    def _origin(rect: QRectF | QRect, b: int) -> tuple[int, int]:
        ox = int(rect.x() + (rect.width() - GRID_W * b) / 2)
        oy = int(rect.y() + (rect.height() - GRID_H * b) / 2)
        return ox, oy

    def paint(self, p: QPainter, rect: QRectF, model: EyesModel, *, scale: int) -> None:
        b = 2 * max(1, int(scale))
        ox, oy = self._origin(rect, b)
        ox += int(round(model.shake * b))
        red = model.mood == "red"

        if model.mood == "happy":
            for i in range(2):
                ex = ox + i * (EYE_W + EYE_GAP) * b
                self._happy(p, ex, oy + BROW_ROWS * b, b, i)
                self._brow(p, ex, oy, b, i, model.brows[i], C_OUTLINE)
            return

        for i in range(2):
            ex = ox + i * (EYE_W + EYE_GAP) * b
            ey = oy + BROW_ROWS * b
            self._eye(p, ex, ey, b, model, i, red)
            self._brow(p, ex, oy, b, i, model.brows[i], C_RED_BROW if red else C_OUTLINE)
        if model.sleeping:
            self._zzz(p, ox + GRID_W * b, oy, b, model.z_phase)

    # --- pieces ---------------------------------------------------------------------

    @staticmethod
    def _fill_rows(p: QPainter, x: int, y: int, b: int, spans, rows, color: QColor) -> None:
        for r in rows:
            left, right = spans[r]
            if right > left:
                p.fillRect(x + left * b, y + r * b, (right - left) * b, b, color)

    def _eye(self, p: QPainter, ex: int, ey: int, b: int, m: EyesModel, index: int,
             red: bool) -> None:
        lid = m.lids[index]
        squash = int(round(m.squash[index] * SQUASH_ROWS))
        h = EYE_H - squash
        outer, inner = _GEOM[h]
        # A squashing eye gives up its rows from the top first - the lid is what's coming down.
        top = ey + ((squash + 1) // 2) * b
        if red:
            # Glow: the outline shape again, one block bigger all round, twice, fading.
            for grow, glow in ((2, C_RED_GLOW[1]), (1, C_RED_GLOW[0])):
                for r in range(-grow, h + grow):
                    left, right = outer[min(h - 1, max(0, r))]
                    p.fillRect(ex + (left - grow) * b, top + r * b,
                               (right - left + 2 * grow) * b, b, glow)
        outline = C_RED_OUTLINE if red else C_OUTLINE
        sclera = C_RED_SCLERA if red else C_SCLERA
        lid_colour = C_RED_LID if red else C_BODY

        self._fill_rows(p, ex, top, b, outer, range(h), outline)
        rows = range(1, h - 1)
        self._fill_rows(p, ex, top, b, inner, rows, sclera)
        if not red:
            self._fill_rows(p, ex, top, b, inner, (h - 2,), C_SCLERA_SHADE)

        if m.mood == "veins" and lid > 0.05:
            for col, row in _VEINS[index]:
                if 1 <= row < h - 1 and inner[row][0] <= col < inner[row][1]:
                    p.fillRect(ex + col * b, top + row * b, b, b, C_VEIN)

        # Iris and pupil: concentric chunky discs, clipped row by row to the sclera.
        px, py = m.pupil_l if index == 0 else m.pupil_r
        # Snapped to whole blocks: a disc centred off-grid comes out lopsided, and the pupil only
        # ever moves in blocks on screen anyway.
        cx = float(round(EYE_W / 2 + px * _SPAN_X))
        cy = float(round(h / 2 + py * _SPAN_Y))
        pupil_d = PUPIL_D[0] + (PUPIL_D[1] - PUPIL_D[0]) * max(0.0, min(1.0, m.dilation))

        def disc(d: float, colour: QColor, *, lower_only: float | None = None) -> None:
            for row, left, right in _disc_rows(cx, cy, d):
                if not 1 <= row < h - 1 or (lower_only is not None and row + 0.5 < lower_only):
                    continue
                a, z = max(left, inner[row][0]), min(right, inner[row][1])
                if z > a:
                    p.fillRect(ex + a * b, top + row * b, (z - a) * b, b, colour)

        if red:
            disc(IRIS_D, C_RED_RING)
            disc(IRIS_D - 2, C_RED_IRIS)
            disc(pupil_d + 2.5, C_RED_GLOW[0])
            disc(pupil_d, C_RED_PUPIL)
            disc(max(1.2, pupil_d - 2.6), C_RED_CORE)
        else:
            disc(IRIS_D, C_IRIS_RING)
            disc(IRIS_D - 2, C_IRIS)
            # Light pools in the lower half of the iris, opposite the glint: the "wet" look.
            disc(IRIS_D - 2, C_IRIS_LOW, lower_only=cy + 1.5)
            disc(pupil_d, C_OUTLINE)
        if not red:  # in the dark there's no light to reflect - only the glow
            gx = EYE_W // 2 + round(px * _SPAN_X * _GLINT_FOLLOW)
            gy = h // 2 + round(py * _SPAN_Y * _GLINT_FOLLOW)
            reach = (IRIS_D / 2 - 0.5) ** 2
            for dx, dy, w, gh in _GLINT:
                for row in range(gy + dy, gy + dy + gh):
                    if not 1 <= row < h - 1:
                        continue
                    for col in range(gx + dx, gx + dx + w):
                        if (inner[row][0] <= col < inner[row][1]
                                and (col + 0.5 - cx) ** 2 + (row + 0.5 - cy) ** 2 <= reach):
                            p.fillRect(ex + col * b, top + row * b, b, b, C_GLINT)

        # Upper lid, coming down to (1 - lid) of the inner height.
        inner_h = h - 2
        covered = int(round((1.0 - lid) * inner_h))
        lower = 0
        if not m.sleeping and lid > 0.05:
            lower = {"squint": 2, "veins": 3}.get(m.mood, 0)
        if covered >= inner_h:
            self._fill_rows(p, ex, top, b, inner, rows, lid_colour)
            if not red:
                self._lid_highlight(p, ex, top, b, inner, inner_h)
            if m.squeezed or m.wincing:
                self._chevron(p, ex, top, b, h, index, outline)
            else:
                self._lash(p, ex, top, b, h, inner, outline)
            return
        if covered > 0:
            self._fill_rows(p, ex, top, b, inner, range(1, 1 + covered), lid_colour)
            if not red:
                self._lid_highlight(p, ex, top, b, inner, covered)
                if covered >= 3:  # the lid's own thickness, just above the lash line
                    self._fill_rows(p, ex, top, b, inner, (covered,), C_LID_EDGE)
        edge = 1 + covered
        if edge < h - 1:
            self._fill_rows(p, ex, top, b, inner, (edge,), outline)
            # The soft shadow the lid throws on the eyeball, two rows fading out.
            if not red:
                for k, shade in enumerate(C_LID_SHADOW):
                    r = edge + 1 + k
                    if r < h - 1 - lower:
                        self._fill_rows(p, ex, top, b, inner, (r,), shade)
        if lower:
            self._fill_rows(p, ex, top, b, inner, range(h - 1 - lower, h - 1), lid_colour)
            edge = h - 2 - lower
            if edge > covered + 1:
                self._fill_rows(p, ex, top, b, inner, (edge,), outline)

    @staticmethod
    def _lash(p: QPainter, ex: int, top: int, b: int, h: int, inner, colour: QColor) -> None:
        """Closed: a lash line slightly below centre, sagging in the middle like a resting lid."""
        base = h // 2 + 2
        left, right = inner[base]
        half = (right - left - 2) / 2.0
        run_start, run_row = left + 1, None
        for c in range(left + 1, right):
            row = None
            if c < right - 1:
                x = (c + 0.5 - (left + 1) - half) / half
                row = base - math.floor(2.0 * x * x + 0.5)
            if row != run_row:
                if run_row is not None:
                    p.fillRect(ex + run_start * b, top + run_row * b, (c - run_start) * b, b, colour)
                run_start, run_row = c, row

    @staticmethod
    def _lid_highlight(p: QPainter, ex: int, top: int, b: int, inner, covered: int) -> None:
        """A short light streak across the upper lid - Nib's body is lit from the top left."""
        if covered < 4:
            return
        left, right = inner[2]
        p.fillRect(ex + (left + 2) * b, top + 2 * b, max(1, (right - left) // 3) * b, b, C_LID_HI)

    @staticmethod
    def _chevron(p: QPainter, ex: int, top: int, b: int, h: int, index: int, colour: QColor) -> None:
        """Squeezed shut: '>' on the left eye, '<' on the right - the universal "not looking"."""
        mid = h // 2
        arm = 5
        for k in range(arm + 1):
            dx = k if index == 0 else arm - k
            col = EYE_W // 2 - 3 + dx
            p.fillRect(ex + col * b, top + (mid - arm + k) * b, 3 * b, b, colour)
            p.fillRect(ex + col * b, top + (mid + arm - k) * b, 3 * b, b, colour)

    @staticmethod
    def _brow(p: QPainter, ex: int, oy: int, b: int, index: int,
              pose: tuple[float, float, float], colour: QColor) -> None:
        """A chunky brow from (lift, tilt, arch): two rows thick, one at the tips.

        The one-row tips sit on the lower row, which rounds the ends off - without them a brow is
        a plank. Drawn as runs of equal height, so a flat brow is three rects, a steep one a few.
        """
        lift, tilt, arch = pose
        c0, c1 = _BROW_COLS
        n = c1 - c0
        run_start, run_key = c0, None
        for c in range(c0, c1 + 1):
            key = None
            if c < c1:
                t = (c - c0) / (n - 1)
                t = t if index == 0 else 1.0 - t       # 0 at the outer end, 1 at the nose
                y = _BROW_REST - lift - tilt * t - arch * 4.0 * t * (1.0 - t)
                tip = c - c0 < 2 or c1 - c <= 2
                row = max(-2, math.floor(y + 0.5))
                key = (row + 1, 1) if tip else (row, 2)
            if key != run_key:
                if run_key is not None:
                    row, thick = run_key
                    p.fillRect(ex + run_start * b, oy + row * b, (c - run_start) * b, thick * b, colour)
                run_start, run_key = c, key

    @staticmethod
    def _happy(p: QPainter, ex: int, ey: int, b: int, index: int) -> None:
        """^ ^ - two thick arcs of blocks, and a little blush under each."""
        half = EYE_W / 2
        for col in range(3, EYE_W - 3):
            x = (col + 0.5 - half) / (half - 3)
            rise = 1.0 - x * x * (0.6 + 0.4 * abs(x))   # rounded top, straight-ish legs
            row = int(round(EYE_H * 0.66 - rise * 8))
            p.fillRect(ex + col * b, ey + row * b, b, 3 * b, C_OUTLINE)
        cheek = 3 if index == 0 else EYE_W - 9
        p.fillRect(ex + cheek * b, ey + (EYE_H - 4) * b, 6 * b, 2 * b, C_BLUSH)

    @staticmethod
    def _zzz(p: QPainter, x: int, y: int, b: int, phase: float) -> None:
        """Floating Z z z beside the right eye, drifting up and looping."""
        for i, size in enumerate((5, 4, 3)):
            t = (phase * 0.35 + i / 3.0) % 1.0
            bx = x + (1 + i * 3) * b + int(math.sin((phase + i) * 2.0) * b)
            by = y + int((GRID_H - 5 - t * (GRID_H - 3)) * b)
            unit = max(1, b * size // 5)
            colour = QColor(C_SCLERA)
            colour.setAlpha(int(255 * (1.0 - t * 0.8)))
            for cx, cy in _Z:
                p.fillRect(bx + cx * unit, by + cy * unit, unit, unit, colour)


# --- Nib on stage ---------------------------------------------------------------------------

#: name -> weight (plan section 3). ``tired`` only after 20 min.
ROUTINE_WEIGHTS: dict[str, int] = {
    "patrol": 30, "stand_guard": 20, "coffee": 10, "keep_fit": 10,
    "football": 10, "counting": 10, "dance": 5, "tired": 5,
}
TIRED_AFTER_MIN = 20.0
ROUTINE_EVERY_S = (6.0, 15.0)
IDLE_LINE_EVERY_S = (9.0, 14.0)
WALK_SPEED = 0.16          # stage widths per second
RUN_SPEED = 0.7
STAGE_MIN, STAGE_MAX = 0.12, 0.88
POINTER_NEAR_COOLDOWN_S = 5.0
POKE_WINDOW_S = 8.0
POKES_TO_ANGER = 3

#: Fallbacks for states the sheet might not have: state -> (fallback state, held frame or None).
STATE_FALLBACKS: dict[str, tuple[str, int | None]] = {
    "cover_eyes": ("sleep", 0),
    "walk_left": ("walk_right", None),
}


@dataclass
class _Step:
    kind: str                 # "do" | "walk" | "say" | "sound" | "vanish"
    state: str = ""
    seconds: float = 0.0
    target: float = 0.0
    speed: float = WALK_SPEED
    category: str = ""


class StageDirector:
    """Chooses Nib's state and position on the stage. Deterministic for a given ``rng``.

    Outputs, read by the scene each frame: ``state`` (an existing sheet state, already checked),
    ``hold_frame`` (non-None when a missing state was replaced by one pinned frame - e.g. no
    ``cover_eyes`` art yet -> ``sleep`` frame 0), ``x`` (0..1 across the stage; <0 or >1 is
    off-stage), ``facing`` (+1 right / -1 left), ``visible``, ``sleeping``.

    ``phrase`` ((category, text) or None) and ``sound`` (an event name from ``sound.EVENTS`` or
    None) are *pending* outputs: they stay set until taken with :meth:`take_phrase` /
    :meth:`take_sound`, which return and clear them. ``eye_bump`` likewise (:meth:`take_bump`).
    """

    def __init__(self, sheet_states: set[str], rng: random.Random, *,
                 phrases: PhraseBag | None = None) -> None:
        self._states = set(sheet_states)
        self._rng = rng
        self._phrases = phrases
        self.state = "idle"
        self.hold_frame: int | None = None
        self.x = 0.5
        self.facing = 1
        self.visible = True
        self.sleeping = False
        self.phrase: tuple[str, str] | None = None
        self.sound: str | None = None
        self.eye_bump = False
        self.routine: str | None = None
        #: Every routine started, in order - the smoke test checks for back-to-back repeats.
        self.history: list[str] = []
        self.mode = "fog"          # "fog" | "blackout" | "unlock"
        self.held = False          # covering his eyes while you type
        self.wrongs = 0
        self.started_at: float | None = None

        self._now = 0.0
        self._steps: list[_Step] = []
        self._step_started = 0.0
        self._step_live = False
        self._reacting = False
        self._next_pick = 0.0
        self._next_line = 0.0
        self._next_bump = 0.0
        self._last_pointer_near = -1e9
        self._last_typing_line = -1e9
        self._pokes: list[float] = []

    # --- outputs ------------------------------------------------------------------

    def take_phrase(self) -> tuple[str, str] | None:
        phrase, self.phrase = self.phrase, None
        return phrase

    def take_sound(self) -> str | None:
        sound, self.sound = self.sound, None
        return sound

    def take_bump(self) -> bool:
        bump, self.eye_bump = self.eye_bump, False
        return bump

    @property
    def ball_active(self) -> bool:
        """True while Nib is actually juggling - the scene then tells the eyes where the ball is."""
        return self.visible and self.routine == "football" and self.state == "football"

    def start_routine(self, name: str) -> None:
        """Start routine ``name`` now, as if the weighted pick had chosen it (tests, demos)."""
        if name not in ROUTINE_WEIGHTS or self.mode != "fog":
            return
        self.sleeping = False
        self.held = False
        self.routine = name
        self.history.append(name)
        self._run(self._routine_steps(name), reaction=False)
        self._next_pick = self._now + self._rng.uniform(*ROUTINE_EVERY_S)
        self._advance(0.0)

    @property
    def minutes(self) -> int:
        if self.started_at is None:
            return 0
        return int(max(0.0, self._now - self.started_at) // 60)

    # --- helpers ------------------------------------------------------------------

    def _set_state(self, state: str) -> None:
        """Every state assignment comes through here, so nothing unknown reaches the Animator."""
        if state in self._states:
            self.state, self.hold_frame = state, None
            return
        fallback, frame = STATE_FALLBACKS.get(state, ("idle", None))
        if fallback not in self._states:
            fallback, frame = "idle", None
        self.state, self.hold_frame = fallback, frame

    def _say(self, category: str) -> None:
        text = self._phrases.next(category, minutes=self.minutes) if self._phrases else ""
        self.phrase = (category, text)
        # A reaction line resets the idle-line clock, so they never talk over each other.
        self._next_line = self._now + self._rng.uniform(*IDLE_LINE_EVERY_S)

    def _run(self, steps: list[_Step], *, reaction: bool) -> None:
        self._steps = list(steps)
        self._step_live = False
        self._reacting = reaction
        if reaction:
            self.routine = None

    # --- routines -----------------------------------------------------------------

    def _routine_steps(self, name: str) -> list[_Step]:
        r = self._rng
        if name == "patrol":
            home = self.x
            far = r.uniform(0.7, STAGE_MAX) if self.x < 0.5 else r.uniform(STAGE_MIN, 0.3)
            return [_Step("walk", target=far), _Step("do", "check_watch", 2.0),
                    _Step("walk", target=home), _Step("do", "look_around", 2.2)]
        if name == "stand_guard":
            steps = [_Step("do", "idle", r.uniform(2.0, 3.5)), _Step("do", "blink_slow", 0.9),
                     _Step("do", "idle", r.uniform(1.5, 3.0))]
            if r.random() < 0.5:
                steps.append(_Step("do", "foot_tap", 2.0))
            return steps
        if name == "coffee":
            return [_Step("do", "sit_dangle", 1.6), _Step("say", category="coffee"),
                    _Step("do", "coffee", 4.0)]
        if name == "keep_fit":
            return [_Step("do", "pushups", 3.6), _Step("do", "stretch", 2.0)]
        if name == "football":
            return [_Step("do", "football", r.uniform(5.0, 8.0))]
        if name == "counting":
            return [_Step("say", category="counting"), _Step("do", "counting", 4.0)]
        if name == "dance":
            return [_Step("do", "dance", r.uniform(3.0, 4.5))]
        if name == "tired":
            return [_Step("do", "yawn", 2.4), _Step("do", "stretch", 2.0)]
        return [_Step("do", "idle", 2.0)]

    def _pick_routine(self) -> str:
        names = [n for n in ROUTINE_WEIGHTS
                 if n != self.routine and n != (self.history[-1] if self.history else None)]
        if self.minutes < TIRED_AFTER_MIN:
            names = [n for n in names if n != "tired"]
        weights = [ROUTINE_WEIGHTS[n] for n in names]
        return self._rng.choices(names, weights=weights, k=1)[0]

    # --- the per-frame step -------------------------------------------------------

    def update(self, now: float, idle_s: float) -> None:
        if self.started_at is None:
            self.started_at = now
            self._next_pick = now + self._rng.uniform(1.5, 3.0)
            self._next_line = now + self._rng.uniform(4.0, 7.0)
        gap = now - self._now if self._now else 0.0
        if gap > 1.0:
            # Nobody updated him for a while (the scene was hidden for a Peek): time did not pass
            # on stage, so push his schedule along rather than firing everything at once.
            self._next_line += gap
            self._next_pick += gap
            self._next_bump += gap
        dt = max(0.0, min(gap, 0.5))
        self._now = now

        if self.mode == "blackout":
            self._advance(dt)
            return

        # Asleep after 5 min of nothing, in sync with the eyes; any input wakes both.
        if self.mode == "fog" and not self.held and not self._reacting:
            if idle_s >= SLEEP_S and not self.sleeping:
                self.sleeping = True
                self._steps = []
                self.routine = None
                self._set_state("sleep")
                self.sound = "sleep"
            elif self.sleeping and idle_s < SLEEP_S:
                self.sleeping = False
                self._run([_Step("do", "wake", 0.8)], reaction=True)
                self.sound = "wake"
        if self.sleeping:
            if now >= self._next_line:
                self._say("sleeping")
            return
        if self.held:
            return

        self._advance(dt)
        if self._reacting and not self._steps:
            # A reaction just ended: back to the routines soon, not after a full 6-15 s wait.
            self._reacting = False
            self._next_pick = min(self._next_pick, now + self._rng.uniform(2.0, 4.0))

        if self.mode != "fog":
            return
        if not self._steps:
            if self.routine is not None:
                self.routine = None
            if self.state != "idle" or self.hold_frame is not None:
                self._set_state("idle")
            if now >= self._next_pick:
                name = self._pick_routine()
                self.routine = name
                self.history.append(name)
                self._run(self._routine_steps(name), reaction=False)
                self._next_pick = now + self._rng.uniform(*ROUTINE_EVERY_S)
                self._advance(0.0)
        if self.routine == "football" and now >= self._next_bump:
            self._next_bump = now + self._rng.uniform(2.0, 3.5)
            if self._rng.random() < 0.5:
                self.eye_bump = True  # the ball clips a lower lid; the eyes blink
        if now >= self._next_line and not self._reacting:
            self._say("idle")

    def _advance(self, dt: float) -> None:
        """Run the current step list forward by ``dt``."""
        while self._steps:
            step = self._steps[0]
            if not self._step_live:
                self._step_live = True
                self._step_started = self._now
                if step.kind == "do":
                    self._set_state(step.state)
                elif step.kind == "say":
                    self._say(step.category)
                elif step.kind == "sound":
                    self.sound = step.state
                elif step.kind == "vanish":
                    self.visible = False
                elif step.kind == "walk":
                    self.facing = 1 if step.target >= self.x else -1
                    self._set_state("walk_right" if self.facing > 0 else "walk_left")
            if step.kind == "do":
                if self._now - self._step_started < step.seconds:
                    return
            elif step.kind == "walk":
                gap = step.target - self.x
                move = step.speed * dt
                if abs(gap) > move:
                    self.x += math.copysign(move, gap)
                    return
                self.x = step.target
            self._steps.pop(0)
            self._step_live = False
            dt = 0.0

    def reset(self) -> None:
        """Back on stage, guarding, nothing pending - for a fresh guard after an unlock."""
        self.mode = "fog"
        self.visible = True
        self.held = False
        self.sleeping = False
        self.wrongs = 0
        self.x = 0.5
        self.facing = 1
        self._steps = []
        self._reacting = False
        self.routine = None
        self._set_state("idle")

    # --- reactions ----------------------------------------------------------------

    def react(self, kind: str) -> None:
        """Interrupt the routine. Kinds: pointer_near, poked, clicked, typing_start, typing_stop,
        wrong, blackout, unlock, peek_return. Unknown kinds are ignored."""
        now = self._now
        if kind == "blackout":
            self.mode = "blackout"
            self.held = False
            self.sleeping = False
            self.visible = True
            # "Runs for help": a quick dash off the right of the stage, then gone.
            self._run([_Step("walk", target=1.3, speed=RUN_SPEED), _Step("vanish")], reaction=True)
            self._advance(0.0)
            self.sound = "grump"
            self._say("blackout")
            return
        if kind == "unlock":
            self.mode = "unlock"
            self.held = False
            self.sleeping = False
            self.visible = True
            if self.x < 0 or self.x > 1:
                self.x = 0.5
            self.wrongs = 0
            self._run([_Step("do", "task_finished", 1.2), _Step("do", "heart_eyes", 2.4)], reaction=True)
            self._advance(0.0)
            self.sound = "cheer"
            self._say("unlock")
            return
        if kind == "peek_return":
            self.mode = "fog"
            self.held = False
            self.sleeping = False
            self.visible = True
            self.wrongs = 0
            self.x = -0.15
            self._run([_Step("walk", target=0.5), _Step("do", "check_watch", 2.0)], reaction=True)
            self._advance(0.0)
            self.sound = "wake"
            self._say("peek_return")
            return
        if self.mode == "blackout":
            return  # he's gone for help; nothing else plays until the guard is resolved
        if kind == "typing_start":
            if self.held:
                return
            self.held = True
            self.sleeping = False
            self._steps = []
            self.routine = None
            self._set_state("cover_eyes")
            if now - self._last_typing_line >= 6.0:
                self._last_typing_line = now
                self._say("typing")
            return
        if kind == "typing_stop":
            if not self.held:
                return
            self.held = False
            self._set_state("idle")
            self._next_pick = now + self._rng.uniform(2.0, 4.0)
            return
        if kind == "wrong":
            self.wrongs += 1
            self.held = False
            self.sleeping = False
            self._run([_Step("do", "angry", 1.2), _Step("do", "foot_tap", 1.6)], reaction=True)
            self._advance(0.0)
            self.sound = "grump"
            self._say("wrong_1" if self.wrongs <= 1 else "wrong_2")
            return
        if self.held or self.sleeping:
            return  # covering his eyes / asleep: the pointer games don't reach him
        if kind == "pointer_near":
            if now - self._last_pointer_near < POINTER_NEAR_COOLDOWN_S or self._reacting:
                return
            self._last_pointer_near = now
            self._run([_Step("do", "startle", 0.6), _Step("do", "wave", 1.4)], reaction=True)
            self._advance(0.0)
            self.sound = "poke"
            self._say("pointer_near")
            return
        if kind == "clicked":
            self._pokes = [t for t in self._pokes if now - t < POKE_WINDOW_S] + [now]
            if len(self._pokes) >= POKES_TO_ANGER:
                self._pokes = []
                kind = "poked"
            else:
                # Waves, pointing down at the PIN field.
                self._run([_Step("do", "wave", 1.6)], reaction=True)
                self._advance(0.0)
                self.sound = "poke"
                if len(self._pokes) == 1:
                    self._say("clicked")
                return
        if kind == "poked":
            self._run([_Step("do", "angry", 1.8)], reaction=True)
            self._advance(0.0)
            self.sound = "grump"
            self._say("poked")
            return
        log.debug("stage director: ignoring reaction %r", kind)


# --- the composed scene ---------------------------------------------------------------------

FRAME_MS = 33        # 30 FPS
IDLE_FRAME_MS = 66   # 15 FPS once nobody has touched anything for a minute (the "sleepy" stretch)
SLEEP_FRAME_MS = 100 # 10 FPS while asleep
#: Where the ball is in each ``football`` frame: its centre in unscaled 48x48 sprite pixels.
#: Mirrors ``FOOTBALL_KEYS`` in tools/gen_sprites.py (top-left of the 4x4 ball, +2), so the eyes
#: can follow the ball Nib is actually drawn juggling rather than a made-up arc.
FOOTBALL_BALL = ((40, 18), (40, 28), (39, 36), (40, 26), (41, 16), (42, 24))
TYPE_MS_PER_CHAR = 35
BUBBLE_HOLD_S = (4.0, 6.0)
BUBBLE_FADE_S = 0.5
POINTER_NEAR_PX = 120
STARE_S = 4.0
HAPPY_S = 0.6
UNLOCK_FADE_S = 0.5


class GuardScene(QWidget):
    """Eyes + Nib on his stage + a typewriter speech bubble, on a transparent background.

    Hosted by the primary ``GuardOverlay``. Inputs:

    * :meth:`set_mode` - ``"fog"`` | ``"blackout"`` | ``"peek_return"`` | ``"unlock"``.
    * :meth:`notify` - ``"input"`` (any key/mouse activity, e.g. from the guard core),
      ``"typing_start"`` / ``"typing_stop"``, ``"near_field"`` / ``"far_field"`` (pointer near the
      PIN field), ``"wrong"``, ``"poked"``, ``"clicked"``, ``"pointer_near"``, ``"eyes_clicked"``
      (the eyes wince; a click on them does this by itself). Unknown -> ignored.

    Outputs: ``sound_requested(name)`` (gated on ``guard_sounds``) and ``phrase_shown(category,
    text)`` for an overlay that wants to show the line itself (the ``"eyes"`` scene setting, where
    there is no Nib to speak it; the scene also draws it as a banner in that case).
    """

    sound_requested = Signal(str)
    phrase_shown = Signal(str, str)

    def __init__(self, sheet: SpriteSheet, config, *, show_eyes: bool, show_nib: bool,
                 rng: random.Random | None = None, parent: QWidget | None = None,
                 phrases: dict[str, list[str]] | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setMouseTracking(True)
        self._sheet = sheet
        self._config = config
        self._rng = rng or random.Random()
        self.show_eyes = bool(show_eyes)
        self.show_nib = bool(show_nib)

        if phrases is None:
            user_dir = None
            path = getattr(config, "path", None)
            if path is not None:
                user_dir = Path(path).parent
            phrases = load_phrases(sheet.sheet_path.parent, user_dir)
        self.phrases = PhraseBag(phrases, self._rng)
        self.eyes = EyesModel(self._rng)
        self.director = StageDirector(set(sheet.states()), self._rng, phrases=self.phrases)
        self._painter = EyesPainter()
        self._anim = Animator(sheet, "idle")

        self.mode = "fog"
        self._wrongs = 0
        self._typing = False
        self._near_field = False
        self._near_nib = False
        self._last_input = 0.0
        self._last_now: float | None = None
        self._last_cursor: QPointF | None = None
        self._pointer = QPointF(-1e6, -1e6)
        self._pointer_speed = 0.0
        self._stare_until = -1.0
        self._mood_until = -1.0
        self._fade_start: float | None = None
        self.opacity = 1.0

        # Bubble state.
        self._bubble_text = ""
        self._bubble_category = ""
        self._bubble_start = 0.0
        self._bubble_hold = 0.0
        self._bubble_alpha = 0.0
        self._bubble_chars = 0
        self._bubble_layout: tuple[str, QRect, QFont] | None = None

        # What was painted last, for the dirty-rect updates.
        self._eyes_sig: tuple | None = None
        self._nib_rect = QRect()
        self._nib_key: tuple | None = None
        self._bubble_rect = QRect()
        self._bubble_key: tuple | None = None

        #: Average/last paintEvent cost in ms - checked by the smoke test, handy when tuning.
        self.paint_ms_total = 0.0
        self.paint_count = 0

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(lambda: self.step(time.monotonic()))

    # --- lifecycle ----------------------------------------------------------------

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._timer.isActive():
            self._timer.start(FRAME_MS)

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._timer.stop()  # a hidden scene (Peek) costs nothing
        # Forget the frozen clock: a Peek lasts minutes, and a set_mode("peek_return") arriving
        # while hidden must stamp its timers with the real time, not the last tick before hiding -
        # otherwise the stare expires instantly and three minutes of "idle" make the eyes sleepy.
        self._last_now = None
        self._last_cursor = None

    # --- inputs -------------------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        now = self._last_now if self._last_now is not None else time.monotonic()
        self.mode = mode
        self._last_input = now
        if mode == "blackout":
            self.eyes.set_mood("red")
            self.director.react("blackout")
        elif mode == "unlock":
            self._wrongs = 0
            self.eyes.set_mood("happy")
            self._mood_until = now + HAPPY_S
            self._fade_start = now + HAPPY_S
            self.director.react("unlock")
        elif mode == "peek_return":
            self._wrongs = 0
            self.opacity = 1.0
            self._fade_start = None
            self.eyes.set_mood("stare")
            self._stare_until = now + STARE_S
            self.director.react("peek_return")
            self.mode = "fog"
        else:  # "fog"
            self.opacity = 1.0
            self._fade_start = None
            self.eyes.set_mood(self._fog_mood())
            if self.director.mode != "fog":
                self.director.reset()
        self.update()

    def notify(self, kind: str) -> None:
        now = self._last_now if self._last_now is not None else time.monotonic()
        if kind == "input":
            self._last_input = now
        elif kind == "typing_start":
            self._typing = True
            self._last_input = now
            self.director.react("typing_start")
        elif kind == "typing_stop":
            self._typing = False
            self._last_input = now
            self.director.react("typing_stop")
        elif kind in ("near_field", "field_near"):
            self._near_field = True
        elif kind in ("far_field", "field_far"):
            self._near_field = False
        elif kind == "wrong":
            self._wrongs += 1
            self._typing = False
            self._last_input = now
            if self.mode != "blackout":
                self.eyes.set_mood(self._fog_mood())
                self.eyes.really()
            self.director.react("wrong")
        elif kind in ("poked", "clicked", "pointer_near"):
            self._last_input = now
            self.director.react(kind)
        elif kind == "eyes_clicked":
            self._last_input = now
            self.eyes.wince()
        else:
            log.debug("guard scene: ignoring notify(%r)", kind)

    def set_typing(self, typing: bool) -> None:
        if typing != self._typing:
            self.notify("typing_start" if typing else "typing_stop")

    def set_near_field(self, near: bool) -> None:
        self.notify("near_field" if near else "far_field")

    def _fog_mood(self) -> str:
        if self._wrongs >= 2:
            return "veins"
        if self._wrongs == 1:
            return "squint"
        return "calm"

    # --- geometry -----------------------------------------------------------------

    def _eyes_rect(self) -> QRect:
        w, h = self.width(), self.height()
        if not self.show_nib:
            return QRect(0, int(h * 0.05), w, int(h * 0.62))
        return QRect(0, int(h * 0.02), w, int(h * 0.48))

    def _eyes_scale(self) -> int:
        return EyesPainter.fit_scale(self._eyes_rect())

    def _stage(self) -> tuple[int, int, int]:
        """(left, right, floor y) of the stage, in widget pixels."""
        w, h = self.width(), self.height()
        return int(w * 0.18), int(w * 0.82), int(h * 0.97) if self.show_eyes else int(h * 0.8)

    def _current_nib_rect(self) -> QRect:
        if not (self.show_nib and self.director.visible):
            return QRect()
        left, right, floor = self._stage()
        sw, sh = self._sheet.sprite_w, self._sheet.sprite_h
        cx = left + self.director.x * (right - left)
        return QRect(int(cx - sw / 2), floor - sh + self._sheet.sole_gap, sw, sh)

    def _font(self, banner: bool) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(max(14, self.height() // (20 if banner else 38)))
        font.setBold(banner)
        return font

    def _layout_bubble(self) -> tuple[QRect, QFont, bool]:
        """Bubble box for the *full* text, so it doesn't grow while the typewriter types."""
        banner = (not self.show_nib) or self.mode == "blackout" or not self.director.visible
        cached = self._bubble_layout
        key = (self._bubble_text, banner, self.width(), self.height(),
               None if banner else self._current_nib_rect().center().x())
        if cached is not None and cached[0] == key:
            return cached[1], cached[2], banner
        font = self._font(banner)
        fm = QFontMetrics(font)
        pad = max(8, font.pixelSize() // 2)
        max_w = int(self.width() * (0.7 if banner else 0.42))
        text_rect = fm.boundingRect(QRect(0, 0, max_w, 10_000), Qt.TextWordWrap, self._bubble_text)
        bw, bh = text_rect.width() + 2 * pad, text_rect.height() + 2 * pad
        if banner:
            eyes = self._eyes_rect()
            y = eyes.bottom() + max(8, self.height() // 40) if self.show_eyes else int(self.height() * 0.35)
            box = QRect(int((self.width() - bw) / 2), y, bw, bh)
        else:
            nib = self._current_nib_rect()
            tail = max(10, font.pixelSize() // 2)
            x = int(nib.center().x() - bw / 2)
            x = max(8, min(self.width() - bw - 8, x))
            # The frame has ~a third of empty rows above his head; aim the tail at the head itself.
            box = QRect(x, nib.top() + int(self._sheet.sprite_h * 0.33) - bh - tail, bw, bh)
        self._bubble_layout = (key, box, font)
        return box, font, banner

    # --- the frame ----------------------------------------------------------------

    def step(self, now: float) -> None:
        """Advance everything to ``now`` (seconds, monotonic) and queue the repaints that changed.

        Public so a test can drive the scene without a running timer.
        """
        dt = 0.0 if self._last_now is None else max(0.0, min(now - self._last_now, 0.5))
        if self._last_now is None:
            self._last_input = now
        self._last_now = now

        # Pointer, in our coordinates, and its speed. Read from the cursor rather than mouse
        # events, because the overlay's widgets (PIN field, buttons) eat the moves over them.
        cursor = QPointF(self.mapFromGlobal(QCursor.pos()))
        if self._last_cursor is not None and dt > 0:
            moved = math.hypot(cursor.x() - self._last_cursor.x(), cursor.y() - self._last_cursor.y())
            self._pointer_speed = moved / dt
            if moved > 1.0:
                self._last_input = now
        self._last_cursor = cursor
        self._pointer = cursor
        idle_s = max(0.0, now - self._last_input)

        # Mood timers.
        if self._stare_until > 0 and now >= self._stare_until:
            self._stare_until = -1.0
            if self.eyes.mood == "stare":
                self.eyes.set_mood(self._fog_mood())
        if self._fade_start is not None:
            self.opacity = max(0.0, 1.0 - (now - self._fade_start) / UNLOCK_FADE_S)

        eyes_rect = self._eyes_rect()
        centre = QPointF(eyes_rect.center())
        self.eyes.update(dt, now, cursor, centre, pointer_speed=self._pointer_speed,
                         idle_s=idle_s, typing=self._typing, near_field=self._near_field,
                         ball=self.ball_pos(),
                         eye_span_px=EyesPainter.eye_span_px(self._eyes_scale()))

        # Pointer close to Nib -> he notices (the director rate-limits it).
        nib_rect = self._current_nib_rect()
        if not nib_rect.isNull():
            near = nib_rect.adjusted(-POINTER_NEAR_PX, -POINTER_NEAR_PX,
                                     POINTER_NEAR_PX, POINTER_NEAR_PX).contains(cursor.toPoint())
            if near and not self._near_nib and idle_s < 0.5:
                self.director.react("pointer_near")
            self._near_nib = near

        self.director.update(now, idle_s)
        if self.director.take_bump():
            self.eyes.bump()
        sound = self.director.take_sound()
        if sound and self._config.get("guard_sounds", True):
            self.sound_requested.emit(sound)
        phrase = self.director.take_phrase()
        if phrase is not None and phrase[1]:
            self._show_phrase(now, *phrase)

        # Nib's animation.
        d = self.director
        if d.hold_frame is not None:
            self._anim.hold(d.state, d.hold_frame)
        elif self._anim.state != d.state:
            self._anim.set_state(d.state)
        else:
            self._anim.advance(dt * 1000.0)

        self._update_bubble(now)
        self._queue_repaints()

        # 10 FPS while both are asleep and nothing is being said; 15 FPS through the sleepy minute
        # before that. Nobody is watching closely then, and the agents underneath want the CPU.
        slow = self.eyes.sleeping and d.sleeping and self._bubble_alpha <= 0.0
        if slow:
            want = SLEEP_FRAME_MS
        elif idle_s >= SLEEPY_S and not self._typing and self._fade_start is None:
            want = IDLE_FRAME_MS
        else:
            want = FRAME_MS
        if self._timer.isActive() and self._timer.interval() != want:
            self._timer.setInterval(want)

    def _show_phrase(self, now: float, category: str, text: str) -> None:
        self._bubble_text = text
        self._bubble_category = category
        self._bubble_start = now
        self._bubble_hold = self._rng.uniform(*BUBBLE_HOLD_S)
        self._bubble_layout = None
        self.phrase_shown.emit(category, text)

    def _update_bubble(self, now: float) -> None:
        if not self._bubble_text:
            self._bubble_alpha = 0.0
            return
        t = now - self._bubble_start
        typed = int(t * 1000 / TYPE_MS_PER_CHAR)
        self._bubble_chars = min(len(self._bubble_text), typed)
        full_at = len(self._bubble_text) * TYPE_MS_PER_CHAR / 1000
        fade_at = full_at + self._bubble_hold
        if t < fade_at:
            self._bubble_alpha = 1.0
        else:
            self._bubble_alpha = max(0.0, 1.0 - (t - fade_at) / BUBBLE_FADE_S)
            if self._bubble_alpha <= 0.0:
                self._bubble_text = ""

    def _queue_repaints(self) -> None:
        """``update(rect)`` only what changed - see the module docstring for why it matters."""
        if self._fade_start is not None and 0.0 < self.opacity < 1.0:
            self.update()
            return
        if self.show_eyes:
            sig = self.eyes.signature() + (self.width(), self.height())
            if sig != self._eyes_sig:
                self._eyes_sig = sig
                self.update(EyesPainter.bounds(self._eyes_rect(), scale=self._eyes_scale()))
        rect = self._current_nib_rect()
        key = (rect.x(), rect.y(), self._anim.state, self._anim.frame_index, rect.isNull())
        if key != self._nib_key:
            self.update(self._nib_rect.united(rect))
            self._nib_rect = rect
            self._nib_key = key
        if self._bubble_text or not self._bubble_rect.isNull():
            box = self._layout_bubble()[0] if self._bubble_text else QRect()
            box = box.adjusted(-4, -4, 4, 40) if not box.isNull() else box
            bkey = (self._bubble_chars, round(self._bubble_alpha * 20), box.x(), box.y(), box.width())
            if bkey != self._bubble_key:
                self.update(self._bubble_rect.united(box))
                self._bubble_rect = box
                self._bubble_key = bkey

    # --- painting -----------------------------------------------------------------

    def ball_pos(self) -> QPointF | None:
        """Where Nib's football is right now, in widget pixels - None unless he's juggling.

        Interpolated between the sprite's key positions by how far through the frame the animator
        is, so the eyes pursue a moving ball rather than one that teleports every 130 ms.
        """
        if not (self.show_nib and self.director.ball_active and self._anim.state == "football"):
            return None
        nib = self._current_nib_rect()
        if nib.isNull():
            return None
        n = len(FOOTBALL_BALL)
        i = self._anim.frame_index % n
        duration = max(1, self._anim.frame_duration)
        f = max(0.0, min(1.0, 1.0 - self._anim.remaining_ms / duration))
        (x0, y0), (x1, y1) = FOOTBALL_BALL[i], FOOTBALL_BALL[(i + 1) % n]
        s = self._sheet.scale
        return QPointF(nib.x() + (x0 + (x1 - x0) * f) * s, nib.y() + (y0 + (y1 - y0) * f) * s)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        if self._current_nib_rect().contains(pos):
            self.notify("clicked")
            event.accept()
            return
        if self.show_eyes and self.mode != "blackout":
            rects = EyesPainter.eye_rects(self._eyes_rect(), scale=self._eyes_scale())
            if any(r.contains(pos) for r in rects):
                # Poked right in the eye: a wince. No sound - the eyes are the quiet half.
                self.notify("eyes_clicked")
                event.accept()
                return
        event.ignore()  # let the overlay have it

    def paintEvent(self, event) -> None:  # noqa: N802
        started = time.perf_counter()
        p = QPainter(self)
        try:
            if self.opacity < 1.0:
                p.setOpacity(self.opacity)
            dirty = event.rect()
            if self.show_eyes:
                eyes_rect = self._eyes_rect()
                scale = self._eyes_scale()
                if dirty.intersects(EyesPainter.bounds(eyes_rect, scale=scale)):
                    self._painter.paint(p, QRectF(eyes_rect), self.eyes, scale=scale)
            if self.show_nib:
                left, right, floor = self._stage()
                if self.mode != "blackout":
                    stage = QColor(C_SCLERA)
                    stage.setAlpha(60)
                    p.fillRect(QRect(left, floor, right - left, max(2, self._sheet.scale)), stage)
                nib = self._current_nib_rect()
                if not nib.isNull() and dirty.intersects(nib):
                    # Only the walk is mirrored in the sheet; every other state faces front.
                    p.drawPixmap(nib.topLeft(), self._anim.pixmap)
            if self._bubble_text and self._bubble_alpha > 0:
                self._paint_bubble(p, dirty)
        finally:
            p.end()
        self.paint_ms_total += (time.perf_counter() - started) * 1000.0
        self.paint_count += 1

    def _paint_bubble(self, p: QPainter, dirty: QRect) -> None:
        box, font, banner = self._layout_bubble()
        if not dirty.intersects(box.adjusted(-4, -4, 4, 40)):
            return
        red = self.mode == "blackout"
        p.save()
        p.setOpacity(p.opacity() * self._bubble_alpha)
        p.setRenderHint(QPainter.Antialiasing, True)
        radius = max(8, font.pixelSize() * 0.7)
        path = QPainterPath()
        path.addRoundedRect(QRectF(box), radius, radius)
        if not banner:
            tail = max(10, font.pixelSize() // 2)
            nib = self._current_nib_rect()
            tx = max(box.left() + radius + tail, min(box.right() - radius - tail, nib.center().x()))
            tri = QPainterPath()
            tri.moveTo(tx - tail * 0.7, box.bottom() - 1)
            tri.lineTo(tx + tail * 0.7, box.bottom() - 1)
            tri.lineTo(tx, box.bottom() + tail)
            tri.closeSubpath()
            path = path.united(tri)
        if red:
            p.setPen(QPen(QColor(0x5A, 0x0A, 0x06), 2))
            p.setBrush(QColor(0x10, 0x02, 0x02, 220))
        else:
            p.setPen(QPen(QColor("#E4DAD3"), 1.5))
            p.setBrush(QColor(255, 255, 255, 240))
        p.drawPath(path)
        p.setFont(font)
        p.setPen(QColor(0xFF, 0x3A, 0x2A) if red else QColor("#2C1206"))
        pad = max(8, font.pixelSize() // 2)
        p.drawText(box.adjusted(pad, pad, -pad, -pad), Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop,
                   self._bubble_text[: self._bubble_chars])
        p.restore()


# --- small eyes for the other monitors -------------------------------------------------------


class MiniEyes(QWidget):
    """A smaller pair of eyes for a secondary monitor's overlay, tracking the *global* cursor.

    So every pair on every screen looks at you, including across monitors. Same model and painter
    as the giant ones; no Nib, no bubble. Sleeps on its own idle clock (cursor movement).
    """

    def __init__(self, parent: QWidget | None = None, *, rng: random.Random | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.eyes = EyesModel(rng or random.Random())
        self._painter = EyesPainter()
        self._last_now: float | None = None
        self._last_cursor: QPointF | None = None
        self._last_input = 0.0
        self._sig: tuple | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(lambda: self.step(time.monotonic()))

    def set_mode(self, mode: str) -> None:
        """``fog`` | ``blackout`` | ``peek_return`` | ``unlock`` - same words as the big scene."""
        self.eyes.set_mood({"blackout": "red", "unlock": "happy", "peek_return": "stare"}.get(mode, "calm"))
        if self._last_now is not None:
            self._last_input = self._last_now
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._timer.start(FRAME_MS)

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._timer.stop()
        self._last_now = None  # see GuardScene.hideEvent
        self._last_cursor = None

    def step(self, now: float) -> None:
        dt = 0.0 if self._last_now is None else max(0.0, min(now - self._last_now, 0.5))
        if self._last_now is None:
            self._last_input = now
        self._last_now = now
        cursor = QPointF(QCursor.pos())
        speed = 0.0
        if self._last_cursor is not None and dt > 0:
            moved = math.hypot(cursor.x() - self._last_cursor.x(), cursor.y() - self._last_cursor.y())
            speed = moved / dt
            if moved > 1.0:
                self._last_input = now
        self._last_cursor = cursor
        centre = QPointF(self.mapToGlobal(self.rect().center()))
        idle_s = now - self._last_input
        scale = EyesPainter.fit_scale(QRectF(self.rect()))
        self.eyes.update(dt, now, cursor, centre, pointer_speed=speed, idle_s=idle_s,
                         typing=False, near_field=False, eye_span_px=EyesPainter.eye_span_px(scale))
        sig = self.eyes.signature()
        if sig != self._sig:
            self._sig = sig
            self.update()
        if self.eyes.sleeping:
            want = SLEEP_FRAME_MS
        elif idle_s >= SLEEPY_S:
            want = IDLE_FRAME_MS
        else:
            want = FRAME_MS
        if self._timer.isActive() and self._timer.interval() != want:
            self._timer.setInterval(want)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        try:
            rect = QRectF(self.rect())
            self._painter.paint(p, rect, self.eyes, scale=EyesPainter.fit_scale(rect))
        finally:
            p.end()


# --- the Peek countdown chip -----------------------------------------------------------------


class PeekChip(QWidget):
    """Small topmost chip during a Peek: ``m:ss`` with a progress ring, "+N min", "Lock now".

    ``warning`` fires once when the countdown crosses 10 s, so the service can play a sound there;
    it re-arms whenever the remaining time goes back above 10 s (after "+N min").
    """

    extend_requested = Signal()
    lock_requested = Signal()
    warning = Signal()

    def __init__(self, config=None, *, ui_scale: float = 1.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._s = max(1.0, float(ui_scale))
        self._remaining = 0.0
        self._total = 1.0
        self._warned = False
        minutes = 3
        if config is not None:
            try:
                minutes = int(config.get("guard_peek_minutes", 2))
            except (TypeError, ValueError):
                minutes = 3

        s = self._s
        layout = QHBoxLayout(self)
        ring_room = int(118 * s)
        layout.setContentsMargins(ring_room, int(8 * s), int(10 * s), int(8 * s))
        layout.setSpacing(int(6 * s))
        self._extend = QPushButton(f"+{minutes} min", self)
        self._lock = QPushButton("Lock now", self)
        for button in (self._extend, self._lock):
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            layout.addWidget(button)
        self._extend.clicked.connect(self.extend_requested.emit)
        self._lock.clicked.connect(self.lock_requested.emit)
        self.setStyleSheet(
            f"""
            QPushButton {{
                background: #3A3532; color: #F2EDE9; border: 1px solid #4A443F;
                border-radius: {int(8 * s)}px; padding: {int(5 * s)}px {int(11 * s)}px;
                font-size: {int(12 * s)}px; font-weight: 600;
            }}
            QPushButton:hover {{ border-color: {ACCENT}; }}
            """
        )
        self.adjustSize()

    def set_remaining(self, seconds: float, total: float) -> None:
        self._remaining = max(0.0, float(seconds))
        self._total = max(1.0, float(total))
        if self._remaining > 10.0:
            self._warned = False
        elif not self._warned and self._remaining > 0.0:
            self._warned = True
            self.warning.emit()
        self.update()

    def text(self) -> str:
        whole = int(math.ceil(self._remaining))
        return f"{whole // 60}:{whole % 60:02d}"

    def paintEvent(self, event) -> None:  # noqa: N802
        s = self._s
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            body = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            p.setPen(QPen(QColor("#4A443F"), 1))
            p.setBrush(QColor(0x2A, 0x27, 0x25, 238))
            p.drawRoundedRect(body, 12 * s, 12 * s)

            d = min(self.height() - 16 * s, 34 * s)
            ring = QRectF(10 * s, (self.height() - d) / 2, d, d)
            width = max(3.0, 3.5 * s)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor("#4A443F"), width))
            p.drawEllipse(ring)
            frac = self._remaining / self._total
            colour = QColor("#E04A3A") if self._remaining <= 10.0 else QColor(ACCENT)
            pen = QPen(colour, width)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.drawArc(ring, 90 * 16, int(-360 * 16 * frac))

            font = QFont(self.font())
            font.setPixelSize(int(15 * s))
            font.setBold(True)
            p.setFont(font)
            p.setPen(QColor("#F2EDE9"))
            text_rect = QRectF(ring.right() + 8 * s, 0, 64 * s, self.height())
            p.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, self.text())
        finally:
            p.end()
