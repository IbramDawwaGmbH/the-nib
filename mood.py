"""How the day has gone - the one thing about him that carries over.

Everything else he feels is *derived*. The time of day, the season, whether the machine is pinned:
all of it is recomputed from scratch on every launch, which means two sessions an hour apart are
identical in every respect that matters. Nothing you did in the first one reaches the second.

This is the exception. One number, ``spirits``, running from -1 to +1, moved by the things you do
to him and fading back to neutral on a half-life. It is written to the config with a wall-clock
stamp, so the fade keeps running while he is closed: leave him sulking, come back tomorrow, and he
has got over it.

Qt-free and clock-injectable, like :mod:`world`, so a week of moods can be simulated in a test
without waiting a week.

**It never fires anything.** Exactly like the time-of-day mood it composes with, this only leans on
the weights of the antics he already has and on how fast he plays them. A mascot with a *new*
behaviour unlocked by an invisible counter is one you cannot reason about; a mascot who is
noticeably bouncier after a good morning is one you can.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

SPIRITS_MIN, SPIRITS_MAX = -1.0, 1.0

#: Below this he is written off as neutral, so a value grinding towards zero eventually gets there
#: instead of asymptotically never arriving and rewriting the config forever.
NEUTRAL_EPSILON = 0.005

#: Stored to two decimals. The config's dirty flag is set by *any* change, and the flush timer
#: fires every 10s - so persisting a raw float that moves on every tick would mean a disk write
#: every ten seconds for as long as he is running.
STORE_QUANTUM = 0.01

#: Nothing here is bigger than a third of the range. One event should be a lean, not a verdict;
#: a mood you can slam to either end with a single click is just a flag with extra arithmetic.
NUDGES: dict[str, float] = {
    "petted": 0.22,         # the big one, and deliberately so - the kindness he can have all day
    "treated": 0.12,        # a treat; smaller than a pet, and at most three a day count
    "focus_done": 0.15,     # a focus session run all the way to completion, not cancelled early
    "prompt_sent": 0.10,    # you used him for what he is for
    "greeted": 0.05,        # you came back
    "poked": -0.09,         # a bubble opened and dismissed without sending
    "angered": -0.30,       # you crossed the annoyance threshold
    "hurt": -0.18,          # dropped from a height, or slammed into the end of the strip
    "ignored": -0.12,       # he gave up waiting and sat down
}

#: Multipliers on the ambient antic pool at full +1 and full -1. Interpolated linearly from
#: ``spirits``, so at 0 every one of them is exactly 1.0 and the mood is genuinely absent rather
#: than merely quiet.
_HIGH_WEIGHTS: dict[str, float] = {
    "hop": 1.9, "dance": 1.8, "sparkle": 1.6, "football": 1.5, "heart_eyes": 1.4,
    "pushups": 1.3, "sit_dangle": 0.4, "check_watch": 0.5,
}
_LOW_WEIGHTS: dict[str, float] = {
    "sit_dangle": 2.2, "look_around": 1.5, "scratch": 1.4, "check_watch": 1.6,
    "dance": 0.25, "hop": 0.4, "football": 0.4, "pushups": 0.3, "sparkle": 0.6,
}

#: How much the animation clock speeds up or slows down at the extremes. Small on purpose: the
#: part-of-day pace already goes down to 0.7 at night, and stacking two big factors would have him
#: crawling. Measured together they bottom out at 0.7 * 0.9 = 0.63.
_PACE_HIGH, _PACE_LOW = 1.08, 0.9

#: Extra chance of a slow, heavy blink when he is flat, added to whatever the clock already asks
#: for. Being tired and being fed up look much the same around the eyes.
_LOW_SLOW_BLINK = 0.25


@dataclass(frozen=True)
class MoodState:
    """A snapshot, for anything that wants to show or test it."""

    spirits: float
    label: str


def label_for(spirits: float) -> str:
    """A phrase for the settings window and the tray tooltip. Never a number."""
    if spirits >= 0.55:
        return "delighted"
    if spirits >= 0.2:
        return "in good spirits"
    if spirits > -0.2:
        return "fine"
    if spirits > -0.55:
        return "a bit flat"
    return "thoroughly fed up"


class Mood:
    """The persistent half of how he feels.

    ``config`` is read for ``mood_enabled`` and ``mood_half_life_h`` and written for
    ``mood_spirits`` / ``mood_saved_at``. ``clock`` is injectable so a test can run a week in a
    millisecond.
    """

    def __init__(self, config, clock=time.time) -> None:
        self._config = config
        self._clock = clock
        self._spirits = 0.0
        self._stored = 0.0
        self._load()

    # --- persistence ----------------------------------------------------------------

    def _load(self) -> None:
        """Restore the stored value and age it by however long he has been shut."""
        try:
            spirits = float(self._config.get("mood_spirits", 0.0))
            saved_at = float(self._config.get("mood_saved_at", 0.0))
        except (TypeError, ValueError):
            spirits, saved_at = 0.0, 0.0

        elapsed_h = 0.0
        if saved_at > 0:
            elapsed_s = self._clock() - saved_at
            # A clock that has moved *backwards* - a timezone change, an NTP correction, a dual
            # boot - would otherwise age the mood by a negative amount and amplify it instead of
            # fading it. Same class of guard as CpuSampler's resume check.
            if elapsed_s < 0:
                log.info("mood timestamp is in the future by %.0fs; treating it as now", -elapsed_s)
                elapsed_s = 0.0
            elapsed_h = elapsed_s / 3600.0

        self._spirits = self._decayed(spirits, elapsed_h)
        self._stored = self._quantised(self._spirits)
        if abs(self._spirits) >= NEUTRAL_EPSILON:
            log.info(
                "mood restored: %+.2f (%s) after %.1fh away",
                self._spirits, label_for(self._spirits), elapsed_h,
            )

    def _half_life_h(self) -> float:
        try:
            return max(0.1, float(self._config.get("mood_half_life_h", 6.0)))
        except (TypeError, ValueError):
            return 6.0

    def _decayed(self, spirits: float, elapsed_h: float) -> float:
        spirits = max(SPIRITS_MIN, min(SPIRITS_MAX, spirits))
        if elapsed_h <= 0 or abs(spirits) < NEUTRAL_EPSILON:
            return 0.0 if abs(spirits) < NEUTRAL_EPSILON else spirits
        faded = spirits * (0.5 ** (elapsed_h / self._half_life_h()))
        return 0.0 if abs(faded) < NEUTRAL_EPSILON else faded

    @staticmethod
    def _quantised(spirits: float) -> float:
        return round(spirits / STORE_QUANTUM) * STORE_QUANTUM

    def persist(self) -> None:
        """Write the current value, but only when the stored one would actually differ.

        Called from the app's config flush. The quantum is what makes that safe to call every ten
        seconds forever: below one hundredth of the range nothing is written, so an untouched
        mascot stops dirtying the file within a couple of minutes of the last event.
        """
        current = self._quantised(self._spirits)
        if current == self._stored:
            return
        self._stored = current
        self._config["mood_spirits"] = current
        self._config["mood_saved_at"] = float(self._clock())

    # --- the mood itself ------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self._config.get("mood_enabled", True))

    @property
    def spirits(self) -> float:
        """-1..+1, or a flat 0.0 while the whole thing is switched off."""
        return self._spirits if self.enabled else 0.0

    @property
    def state(self) -> MoodState:
        return MoodState(spirits=self.spirits, label=label_for(self.spirits))

    def tick(self, dt_ms: float) -> None:
        """Fade towards neutral. Driven from the behaviour clock, not from wall time.

        Wall time is only consulted across a *restart* - see ``_load``. Using it here as well
        would make a paused or hidden mascot keep getting over things while frozen, and would
        make the mood untestable without either sleeping or monkey-patching the clock.
        """
        if dt_ms <= 0 or self._spirits == 0.0:
            return
        self._spirits = self._decayed(self._spirits, dt_ms / 3_600_000.0)

    def nudge(self, event: str, scale: float = 1.0) -> float:
        """Move him by a named event. Returns the new value.

        Nudges that push him *further out* are tapered by the headroom left, so no amount of
        petting pins him at +1 and no amount of poking pins him at -1. Nudges that pull him back
        towards neutral land at full strength - which is the asymmetry that matters: being nice to
        a delighted mascot does very little, and being unpleasant to one always lands.

        The taper is *squared*, and that is not a detail. Tapering linearly makes the remaining
        headroom shrink geometrically: fifty pets left him at 0.99999, which satisfies "never
        pinned" arithmetically and not at all in spirit. Squared makes the approach hyperbolic
        instead - headroom after n events is 1/(1 + n * delta) - so the last stretch of the range
        stays genuinely out of reach and only an exceptional day gets near it.
        """
        delta = NUDGES.get(event)
        if delta is None or not self.enabled:
            return self._spirits
        delta *= scale
        outward = (delta > 0) == (self._spirits >= 0)
        if outward:
            delta *= (1.0 - abs(self._spirits)) ** 2
        before = self._spirits
        self._spirits = max(SPIRITS_MIN, min(SPIRITS_MAX, self._spirits + delta))
        if abs(self._spirits) < NEUTRAL_EPSILON:
            self._spirits = 0.0
        if abs(self._spirits - before) >= STORE_QUANTUM:
            log.debug("mood %+.2f -> %+.2f (%s)", before, self._spirits, event)
        return self._spirits

    def reset(self) -> None:
        self._spirits = 0.0

    # --- what it actually changes ---------------------------------------------------

    def weights(self) -> dict[str, float]:
        """Multipliers for the ambient antic pool.

        Returned for *composition*: the caller multiplies these into whatever the clock, the
        season and the attention arc have already asked for. Replacing those would mean a good
        mood cancelled the fact that it is two in the morning.
        """
        spirits = self.spirits
        if abs(spirits) < NEUTRAL_EPSILON:
            return {}
        table = _HIGH_WEIGHTS if spirits > 0 else _LOW_WEIGHTS
        strength = abs(spirits)
        # Linear towards the table's value, so the multiplier is exactly 1.0 at neutral.
        return {name: 1.0 + (factor - 1.0) * strength for name, factor in table.items()}

    def pace(self) -> float:
        """Animation speed multiplier - brisk when he is up, draggy when he is down."""
        spirits = self.spirits
        if abs(spirits) < NEUTRAL_EPSILON:
            return 1.0
        end = _PACE_HIGH if spirits > 0 else _PACE_LOW
        return 1.0 + (end - 1.0) * abs(spirits)

    def slow_blink_bonus(self) -> float:
        """Extra chance of a heavy blink. Only ever added when he is actually flat."""
        spirits = self.spirits
        return _LOW_SLOW_BLINK * -spirits if spirits < 0 else 0.0
