"""Energy, affection and boredom - three slow quantities that colour what he already does.

A sibling of :mod:`mood`, built the same way and for the same reasons. Where the mood is one number
about how *today* has gone, these are three about the state he is in:

* **energy** - 0 is worn out, 1 is bright-eyed. It runs down slowly while you are at the machine
  (faster late at night) and comes back while you are away or he is closed. A full working day
  leaves him tired by the evening; a night off has him fresh in the morning.
* **affection** - 0 is lonely, 1 is doted on. Pets, prompts, a finished focus session and coming
  back to him raise it; it drifts back to the middle on a twelve-hour half-life.
* **boredom** - 0 is kept busy, 1 is bored stiff. It creeps up while you are here and doing
  nothing with him, and anything you do with him knocks it straight back down.

Qt-free and clock-injectable, like :mod:`mood`, so a week of nights can be simulated in a test
without waiting a week. Persisted to the config as four scalars (``need_energy``,
``need_affection``, ``need_boredom``, ``needs_saved_at``) from the app's flush, and aged by the
wall clock across a restart exactly the way the mood is.

**It never fires anything.** Like the mood it composes with, this only leans on the weights of the
antics he already has, on how fast he plays them, on how often he blinks slowly and peeks over the
taskbar. The two moments that read it - a yawn when he is worn out in the evening, heart eyes when
he is doted on and your pointer is near - live in ``Behavior._pick_moment``, behind the same shared
cooldown as every other moment and a 90-minute one of their own.

**And it is not a Tamagotchi.** Nothing here can be failed. No need ever reaches a state that
punishes you, nags you, or demands to be topped up: a neglected mascot is lonely and a little
bored, which shows as him looking about more and checking his watch - and he gets over it on his
own, on a half-life, whether or not you do anything. No multiplier goes below 0.4, so he never
stops doing anything he does today; and at the neutral point (energy 0.5, affection
0.5, boredom 0) every one of them is exactly 1.0 and he behaves precisely as he did before this
module existed.
"""

from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

#: Where each need sits when nothing has happened. Energy and affection are mid-range quantities -
#: a fresh mascot is neither tired nor bursting, neither lonely nor doted on - while boredom is a
#: one-sided one: zero is "not bored", and there is no such thing as negative boredom.
NEUTRAL_ENERGY = 0.5
NEUTRAL_AFFECTION = 0.5
NEUTRAL_BOREDOM = 0.0

#: Within this of its neutral point a need is written off as neutral, so a value grinding towards it
#: on a half-life gets there instead of asymptotically never arriving and rewriting the config
#: forever. The same figure as ``mood.NEUTRAL_EPSILON``.
NEUTRAL_EPSILON = 0.005

#: Stored to two decimals, for the reason ``mood.STORE_QUANTUM`` gives: the config is flushed every
#: ten seconds, and a raw float that moves on every tick would be a disk write every ten seconds.
#: Energy drifts linearly while you are here, so this still writes - about once every 7.5
#: minutes at the daytime rate, 2.6 at night's - but never on every flush.
STORE_QUANTUM = 0.01
#: ...and the stamp beside them is renewed at least this often, even when no value has moved,
#: while one is somewhere closed time would move it (anything but full energy and no boredom).
#: Otherwise hours spent pinned - worn out at energy 0 at the machine, say - would leave the stamp
#: hours old, and a restart would age all of them as closed: rest he never had.
STAMP_REFRESH_S = 600.0

#: event -> (energy, affection, boredom) deltas. Nothing is bigger than half the range, and the
#: only half-range ones are boredom *falling*: anything you do with him is the cure for boredom, and
#: a cure that takes four goes is not one. Applied with the same squared outward taper as
#: ``Mood.nudge`` - see ``Needs.nudge``.
NEED_NUDGES: dict[str, tuple[float, float, float]] = {
    "petted": (0.0, 0.06, -0.5),
    "prompt_sent": (0.0, 0.02, -0.5),
    "greeted": (0.0, 0.03, -0.3),        # you came back
    "treated": (0.05, 0.08, -0.3),       # a treat (Behavior.treat): at most three a day count
    "focus_done": (-0.05, 0.04, -0.3),   # a whole session, run to the end: pleasing, and tiring
    "thrown": (-0.02, 0.0, -0.2),
    "hurt": (-0.05, 0.0, 0.0),
    "interacted": (0.0, 0.0, -0.5),      # anything at all you did with him - see Behavior.noticed
    "world_event": (0.0, 0.0, -0.2),     # something happened around him: a moment, an app reaction
    "nap_woke": (0.30, 0.0, 0.0),        # up from a nap; Behavior scales it by how long it was
}

# --- drift, per hour -------------------------------------------------------------------------

#: Energy comes back while you are away (or he is closed): two hours away from 0 is a full tank.
ENERGY_REST_PER_H = 0.50
#: ...and runs down while you are here. Slowly: from neutral it takes just under two hours of you
#: being at the machine to reach "tired" (``TIRED_BELOW``) and about three to wear him out (the
#: yawn's and the nap's 0.25 in behavior), so an ordinary working day ends with a tired evening
#: and a lunch break visibly perks him up.
ENERGY_DRAIN_PER_H = 0.08
#: Extra drain while you are here at night. Staying up late costs him too.
ENERGY_NIGHT_DRAIN_PER_H = 0.15
#: Affection drifts back to the middle, slowly: a well-petted afternoon is still warm by evening.
AFFECTION_HALF_LIFE_H = 12.0
#: Boredom builds while you are here and doing nothing with him...
BOREDOM_PER_H = 0.10
#: ...and falls away on its own the rest of the time.
BOREDOM_HALF_LIFE_H = 2.0

#: Must equal ``world.PART_NIGHT``. Spelled out rather than imported so this module depends on
#: nothing else of his; smoke_needs pins the two together.
PART_NIGHT = "night"

# --- what they change ------------------------------------------------------------------------
#
# Each band is linear in the distance past its edge, like ``Mood.weights`` is linear in spirits:
# right at the edge every multiplier is exactly 1.0, at the far end of the range it is the table's
# value. Inside every band (energy 0.35-0.75, affection 0.3-0.7, boredom under 0.6) there is
# nothing at all, which is what keeps the rate scripts' baselines where they were.

TIRED_BELOW = 0.35
RESTED_ABOVE = 0.75
LONELY_BELOW = 0.3
LOVED_ABOVE = 0.7
BORED_ABOVE = 0.6
#: Under this, boredom reads as "busy" in the About him line - you have been doing things with him.
#: Between this and ``BORED_ABOVE`` it is just "fine". A word only; nothing is weighted by it.
BUSY_BELOW = 0.2

_TIRED_WEIGHTS: dict[str, float] = {
    "sit_dangle": 2.0, "check_watch": 1.5,
    "hop": 0.4, "dance": 0.4, "football": 0.4, "pushups": 0.4,
}
_RESTED_WEIGHTS: dict[str, float] = {"hop": 1.5, "dance": 1.4}
_LOVED_WEIGHTS: dict[str, float] = {"heart_eyes": 1.8, "sparkle": 1.4, "wink": 1.3}
#: Lonely, he looks about - and aims everything he does with his eyes at you, the same lean the
#: being-ignored arc uses. look_around is in both lists on purpose: it gets the extra 1.4.
_LONELY_WEIGHTS: dict[str, float] = {"look_around": 1.4}
#: The ambient antics in the "eyes" category of ``behavior.ANTICS``. Spelled out rather than
#: imported, because behavior imports this module; smoke_needs pins the two together.
EYES_ANTICS = ("look_around", "sparkle", "heart_eyes", "wink")
_LONELY_EYES = 1.5
_BORED_WEIGHTS: dict[str, float] = {
    "check_watch": 2.0, "foot_tap": 1.6, "scratch": 1.4, "sit_dangle": 1.5,
}

#: Animation pace at energy 0, from 1.0 at the tired edge. Small on purpose: the night pace and a
#: flat mood already slow him, and the three multiply.
_PACE_EXHAUSTED = 0.90
#: Slow-blink chance added at energy 0, from nothing at this energy.
SLOW_BLINK_BELOW = 0.4
_SLOW_BLINK_EXHAUSTED = 0.25
#: Peek chance multiplier at boredom 1, from 1.0 at the bored edge. A bored mascot goes looking.
_PEEK_BORED = 1.6


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _toward(value: float, target: float, half_life_h: float, elapsed_h: float) -> float:
    """``value`` moved towards ``target`` on a half-life, snapped onto it once close enough."""
    if elapsed_h > 0:
        value = target + (value - target) * (0.5 ** (elapsed_h / half_life_h))
    return target if abs(value - target) < NEUTRAL_EPSILON else value


def _band(table: dict[str, float], strength: float, into: dict[str, float]) -> None:
    """Multiply ``table``, scaled linearly by ``strength`` (0..1), into ``into``."""
    strength = _clamp01(strength)
    for name, factor in table.items():
        into[name] = into.get(name, 1.0) * (1.0 + (factor - 1.0) * strength)


class Needs:
    """Energy, affection and boredom. See the module docstring.

    ``config`` is read for ``needs_enabled`` and written for ``need_energy`` /
    ``need_affection`` / ``need_boredom`` / ``needs_saved_at``. ``clock`` (wall seconds) is
    injectable, and only ever consulted across a restart - the running drift is driven by
    ``tick``, on the behaviour clock, for the reasons ``Mood.tick`` gives.
    """

    def __init__(self, config, clock=time.time) -> None:
        self._config = config
        self._clock = clock
        self._energy = NEUTRAL_ENERGY
        self._affection = NEUTRAL_AFFECTION
        self._boredom = NEUTRAL_BOREDOM
        self._stored = self._quantised_all()
        self._saved_at = 0.0
        self._load()

    # --- persistence ----------------------------------------------------------------

    def _load(self) -> None:
        """Restore the stored values and age them by however long he has been shut.

        Closed counts as away: he has been resting, nobody has petted him, and nothing has bored
        him. A timestamp in the future - a clock that has jumped backwards since - is treated as
        no time at all, the same guard ``Mood._load`` has.
        """
        try:
            energy = float(self._config.get("need_energy", NEUTRAL_ENERGY))
            affection = float(self._config.get("need_affection", NEUTRAL_AFFECTION))
            boredom = float(self._config.get("need_boredom", NEUTRAL_BOREDOM))
            saved_at = float(self._config.get("needs_saved_at", 0.0))
        except (TypeError, ValueError):
            energy, affection, boredom, saved_at = (
                NEUTRAL_ENERGY, NEUTRAL_AFFECTION, NEUTRAL_BOREDOM, 0.0)
        self._energy = _clamp01(energy)
        self._affection = _clamp01(affection)
        self._boredom = _clamp01(boredom)

        elapsed_h = 0.0
        if saved_at > 0:
            elapsed_s = self._clock() - saved_at
            if elapsed_s < 0:
                log.info("needs timestamp is in the future by %.0fs; treating it as now",
                         -elapsed_s)
                elapsed_s = 0.0
            elapsed_h = elapsed_s / 3600.0
        self._drift(elapsed_h, away=True, part_of_day=None, interacting=False)
        # What is in the config now, so persist() writes only once something moves past it.
        self._stored = self._quantised_all()
        self._saved_at = saved_at
        if saved_at > 0:
            log.info("needs restored after %.1fh closed: %s", elapsed_h, ", ".join(self.labels()))

    @staticmethod
    def _quantised(value: float) -> float:
        return round(value / STORE_QUANTUM) * STORE_QUANTUM

    def _quantised_all(self) -> tuple[float, float, float]:
        return (self._quantised(self._energy), self._quantised(self._affection),
                self._quantised(self._boredom))

    def persist(self) -> None:
        """Write the three values, but only when a stored one would actually differ - or when
        the stamp beside them is ``STAMP_REFRESH_S`` old and closed time would move one.

        Called from the app's config flush. Switched off, it writes nothing at all - the values
        last stored stay exactly as they were, so switching back on carries on from them.
        """
        if not self.enabled:
            return
        current = self._quantised_all()
        now = float(self._clock())
        stale = (self._saved_at > 0 and now - self._saved_at >= STAMP_REFRESH_S
                 and (current[0] < 1.0 or current[2] > NEUTRAL_BOREDOM))
        if current == self._stored and not stale:
            return
        self._stored = current
        self._saved_at = now
        self._config["need_energy"] = current[0]
        self._config["need_affection"] = current[1]
        self._config["need_boredom"] = current[2]
        self._config["needs_saved_at"] = now

    # --- the needs themselves -------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self._config.get("needs_enabled", True))

    @property
    def energy(self) -> float:
        """0..1, or a flat neutral 0.5 while the whole thing is switched off."""
        return self._energy if self.enabled else NEUTRAL_ENERGY

    @property
    def affection(self) -> float:
        return self._affection if self.enabled else NEUTRAL_AFFECTION

    @property
    def boredom(self) -> float:
        return self._boredom if self.enabled else NEUTRAL_BOREDOM

    def _drift(self, elapsed_h: float, *, away: bool, part_of_day: str | None,
               interacting: bool) -> None:
        if elapsed_h <= 0:
            return
        if away:
            self._energy = _clamp01(self._energy + ENERGY_REST_PER_H * elapsed_h)
        else:
            drain = ENERGY_DRAIN_PER_H
            if part_of_day == PART_NIGHT:
                drain += ENERGY_NIGHT_DRAIN_PER_H
            self._energy = _clamp01(self._energy - drain * elapsed_h)
        self._affection = _toward(self._affection, NEUTRAL_AFFECTION, AFFECTION_HALF_LIFE_H,
                                  elapsed_h)
        if not away and not interacting:
            self._boredom = _clamp01(self._boredom + BOREDOM_PER_H * elapsed_h)
        else:
            self._boredom = _toward(self._boredom, NEUTRAL_BOREDOM, BOREDOM_HALF_LIFE_H, elapsed_h)

    def tick(self, dt_ms: float, *, away: bool, part_of_day: str | None,
             present_interacting: bool) -> None:
        """Drift by ``dt_ms`` of the behaviour clock.

        ``away`` is whether you are away from the machine; ``part_of_day`` is the world's
        (``None`` before the first reading, or with the world switched off - then there is no
        night drain); ``present_interacting`` is whether you are doing something with him right
        now, which is what stops boredom building. Switched off, nothing moves.
        """
        if dt_ms <= 0 or not self.enabled:
            return
        self._drift(dt_ms / 3_600_000.0, away=away, part_of_day=part_of_day,
                    interacting=present_interacting)

    def nudge(self, event: str, scale: float = 1.0) -> None:
        """Move all three by a named event (``NEED_NUDGES``); unknown events are ignored.

        The taper is ``Mood.nudge``'s, applied per need about its own neutral point: a nudge that
        pushes a need *further* from neutral is scaled by the square of the headroom left on that
        side, so no amount of petting pins affection at 1; a nudge back *towards* neutral lands in
        full. Boredom's neutral point is its floor, so every boredom cure lands in full - which is
        what makes doing anything with him clear it.
        """
        deltas = NEED_NUDGES.get(event)
        if deltas is None or not self.enabled:
            return
        de, da, db = (d * scale for d in deltas)
        self._energy = self._nudged(self._energy, de, NEUTRAL_ENERGY)
        self._affection = self._nudged(self._affection, da, NEUTRAL_AFFECTION)
        self._boredom = self._nudged(self._boredom, db, NEUTRAL_BOREDOM)

    @staticmethod
    def _nudged(value: float, delta: float, neutral: float) -> float:
        if delta == 0:
            return value
        outward = (delta > 0) == (value >= neutral)
        if outward:
            # Headroom on the side he is moving towards, as a fraction of that side's width.
            edge = 1.0 if delta > 0 else 0.0
            width = abs(edge - neutral)
            headroom = abs(edge - value) / width if width > 0 else 0.0
            delta *= headroom ** 2
        value = _clamp01(value + delta)
        return neutral if abs(value - neutral) < NEUTRAL_EPSILON else value

    def reset(self) -> None:
        """Back to neutral - Forget everything.

        Written straight into the config (the next flush saves it) rather than left to
        ``persist``, which does nothing while needs are switched off: otherwise Forget with needs
        off would leave the old values stored, and the next launch would bring them back. This is
        the one write that happens with needs off, and only because you asked for it.
        """
        self._energy = NEUTRAL_ENERGY
        self._affection = NEUTRAL_AFFECTION
        self._boredom = NEUTRAL_BOREDOM
        self._config["need_energy"] = NEUTRAL_ENERGY
        self._config["need_affection"] = NEUTRAL_AFFECTION
        self._config["need_boredom"] = NEUTRAL_BOREDOM
        # No timestamp: neutral has nothing to age, and 0 is what a fresh config holds.
        self._config["needs_saved_at"] = 0.0
        self._stored = self._quantised_all()
        self._saved_at = 0.0

    # --- what they actually change --------------------------------------------------

    def weights(self) -> dict[str, float]:
        """Multipliers for the ambient antic pool, for composition with everything else's.

        ``{}`` inside every band, which includes the neutral point - see the band notes above.
        """
        weights: dict[str, float] = {}
        energy, affection, boredom = self.energy, self.affection, self.boredom
        if energy < TIRED_BELOW:
            _band(_TIRED_WEIGHTS, (TIRED_BELOW - energy) / TIRED_BELOW, weights)
        elif energy > RESTED_ABOVE:
            _band(_RESTED_WEIGHTS, (energy - RESTED_ABOVE) / (1.0 - RESTED_ABOVE), weights)
        if affection > LOVED_ABOVE:
            _band(_LOVED_WEIGHTS, (affection - LOVED_ABOVE) / (1.0 - LOVED_ABOVE), weights)
        elif affection < LONELY_BELOW:
            strength = (LONELY_BELOW - affection) / LONELY_BELOW
            _band(_LONELY_WEIGHTS, strength, weights)
            _band({name: _LONELY_EYES for name in EYES_ANTICS}, strength, weights)
        if boredom > BORED_ABOVE:
            _band(_BORED_WEIGHTS, (boredom - BORED_ABOVE) / (1.0 - BORED_ABOVE), weights)
        return weights

    def pace(self) -> float:
        """Animation speed multiplier: 1.0 until he is tired, down to 0.9 when he is spent."""
        energy = self.energy
        if energy >= TIRED_BELOW:
            return 1.0
        return 1.0 + (_PACE_EXHAUSTED - 1.0) * (TIRED_BELOW - energy) / TIRED_BELOW

    def slow_blink_bonus(self) -> float:
        """Extra chance of the heavy slow blink, added to the clock's and the mood's."""
        energy = self.energy
        if energy >= SLOW_BLINK_BELOW:
            return 0.0
        return _SLOW_BLINK_EXHAUSTED * (SLOW_BLINK_BELOW - energy) / SLOW_BLINK_BELOW

    def peek_boost(self) -> float:
        """Multiplier on the taskbar-peek chance: 1.0, rising to 1.6 as he gets properly bored."""
        past = max(0.0, self.boredom - BORED_ABOVE) / (1.0 - BORED_ABOVE)
        return 1.0 + (_PEEK_BORED - 1.0) * past

    def labels(self) -> tuple[str, str, str]:
        """A word for each, for Settings → About him. Never a number.

        The same band edges as the weights, so the word always agrees with what he is doing:
        "tired" exactly when the tired weights are leaning on him, and so on.
        """
        energy, affection, boredom = self.energy, self.affection, self.boredom
        energy_word = (
            "tired" if energy < TIRED_BELOW else "rested" if energy > RESTED_ABOVE else "okay")
        affection_word = (
            "lonely" if affection < LONELY_BELOW
            else "loved" if affection > LOVED_ABOVE else "fine")
        if boredom > BORED_ABOVE:
            boredom_word = "bored"
        elif boredom < BUSY_BELOW:
            boredom_word = "busy"
        else:
            boredom_word = "fine"
        return energy_word, affection_word, boredom_word

