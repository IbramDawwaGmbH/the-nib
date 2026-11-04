"""The lines he says - one sentence now and then, at most six a day, never in the way.

Everything else he does is a pose. This is the one place he puts something into words, and words
are the easiest thing for a desktop pet to overdo: a mascot that comments on every return from the
kettle is one you switch off by Wednesday. So this module is mostly *refusal*. It decides whether a
line may be said at all, and only then which one.

``Voice.may_speak(category)`` says no when any of these holds:

* ``chatter_level`` is ``"off"``.
* He was started less than ``LAUNCH_QUIET_S`` ago - the first minute belongs to the intro tips,
  an update's progress, and you finding your feet.
* He has already said ``max per day`` lines today (``LEVELS``; the day is the local calendar day).
* The last line was less than ``min gap`` ago. A greeting (``greeting_*``) is the one exception -
  "morning" is owed when you walk in, however recently he last spoke - but it still counts toward
  the day's total.
* The category is one of the once-a-day remarks (``ONCE_A_DAY``) and he has made it today.
* There is no line for the category at all (an unknown category is silence, not a crash).

Where he is and what is on screen - the bubble open, a guard up, a fullscreen game - is not this
module's business; ``main._voice_blocked`` checks that before asking. This module knows only time
and counts, which is what keeps it Qt-free and lets ``tests/chatter_rate.py`` replay a whole day in
a moment.

The count and the time of the last line live in memory only. A restart starts the day's count
again from nothing - and with ``LAUNCH_QUIET_S`` in front of it, a restart is never a way to make
him chattier than the cap by much. The words he said are never written anywhere; main.py logs
only the category of a line (docs/PRIVACY.md says the same).

The lines themselves come from ``assets/nib_phrases.json`` (plus ``nib_phrases.json`` next to
``config.json``, if you write one), through the same ``phrases.PhraseBag`` the guard uses: local
text, written by a person, picked at random without repeats. Nothing is generated or fetched.

``lines_for(kind, data, ...)`` turns an event from ``Behavior.take_events()`` into the categories
worth trying, most specific first. ``main`` says the first one that gets through.
"""

from __future__ import annotations

import time
from typing import Any

from phrases import PhraseBag

#: chatter_level -> (minimum seconds between lines, most lines in a day). "off" says nothing.
#: Rare is the default: six a day, three quarters of an hour apart, is a creature who remarks on
#: things; fourteen, twenty minutes apart, is one who is chatty but still not in your way.
LEVELS: dict[str, tuple[float, int] | None] = {
    "off": None,
    "rare": (45 * 60, 6),
    "normal": (20 * 60, 14),
}
#: Nothing for the first minute after launch, whatever happens in it.
LAUNCH_QUIET_S = 60.0

#: The shipped table in ``assets``, and the name of your own one next to ``config.json``.
NIB_PHRASES_FILE = "nib_phrases.json"

#: Greetings skip the gap (never the day's cap): you walking in is the one thing always worth a word.
GREETING_PREFIX = "greeting_"
#: Events from his needs whose category is just their name - Behavior._needs_moment raises them.
NEEDS_EVENTS = frozenset({"tired", "fond", "bored"})
#: Remarks about the day as a whole. Once each per day, however often the moment comes round - a
#: second "long one yesterday?" after lunch would be nagging.
ONCE_A_DAY = frozenset({"heavy_yesterday", "early_today", "late_today"})

#: A greeting line needs an absence of at least this long. The same ten minutes as
#: ``behavior.GREET_HOP_AFTER_S`` (a test pins the two together): under it he only waves, and a wave
#: for the kettle should not come with words.
GREET_LINE_AFTER_S = 600.0
#: How far from your usual arrival hour counts as early or late. The usual hour is a slot of the
#: tally (08 means 08:00-08:59), so the distance is taken from the middle of the slot.
EARLY_LATE_H = 1.5

#: Every category the shipped table has. The ones nothing raises yet are for later work: treats,
#: the nap, and a milestone for pets.
CATEGORIES = (
    "greeting_short", "greeting_overnight", "heavy_yesterday", "early_today", "late_today",
    "milestone_days", "milestone_pets", "focus_streak", "big_week",
    "tired", "fond", "bored", "treat", "treat_full", "nap_wake",
)

#: One line per category, used when ``assets/nib_phrases.json`` is missing, broken, or lacks the
#: category - so a category that exists never goes silent because of a bad edit.
BUILTIN_PHRASES: dict[str, list[str]] = {
    "greeting_short": ["Oh, you're back."],
    "greeting_overnight": ["Morning. I kept the desk warm."],
    "heavy_yesterday": ["Yesterday was a long one."],
    "early_today": ["You're in early."],
    "late_today": ["A slow start. Fair enough."],
    "milestone_days": ["{days} days together. That's something."],
    "milestone_pets": ["{pets} pets. Not that I count."],
    "focus_streak": ["Three focus sessions today. Look at you."],
    "big_week": ["Big week. You asked a lot of me."],
    "tired": ["I could do with a sit-down."],
    "fond": ["This is nice."],
    "bored": ["Nothing's happening. I've checked twice."],
    "treat": ["Oh! For me!"],
    "treat_full": ["Couldn't possibly. Well. Maybe later."],
    "nap_wake": ["I wasn't asleep. I was resting my eyes."],
}


def _today(clock_s: float) -> str:
    """The local calendar day of a wall-clock time - what "a day" means for the cap."""
    return time.strftime("%Y-%m-%d", time.localtime(clock_s))


def lines_for(kind: str, data: dict[str, Any], *, hour: float | None = None,
              usual_hour: float | None = None) -> list[str]:
    """The categories worth trying for one event, most specific first.

    The caller says the first one that gets through and stops - so a remark about the day is
    preferred, and the plain greeting is the fallback when the remark has been made already or
    the gap refuses it (the greeting is exempt from the gap; the remark is not).

    * ``greeted`` - only after an absence of ``GREET_LINE_AFTER_S`` or more; a hop with no known
      absence (back from behind a fullscreen app) says nothing. After an overnight absence: a
      heavy yesterday (Behavior has already decided that, in ``data["heavy"]``), then early or
      late against ``usual_hour`` if it is known, then the overnight greeting. Otherwise the short
      one. Early and late are only ever about the first arrival of the day, so only overnight.
    * ``milestone``, ``focus_streak``, ``big_week`` - one category each.
    * ``tired``, ``fond``, ``bored`` - from his needs (see needs.py), each its own category.

    Anything else is an empty list: a new kind of event says nothing until someone writes its lines.
    """
    if kind == "greeted":
        gone_s = data.get("gone_s")
        if gone_s is None or gone_s < GREET_LINE_AFTER_S:
            return []
        if not data.get("overnight"):
            return ["greeting_short"]
        out = []
        if data.get("heavy"):
            out.append("heavy_yesterday")
        if hour is not None and usual_hour is not None:
            # usual_hour is the start of its tally slot; measure from the slot's middle.
            off = hour - (usual_hour + 0.5)
            if off <= -EARLY_LATE_H:
                out.append("early_today")
            elif off >= EARLY_LATE_H:
                out.append("late_today")
        out.append("greeting_overnight")
        return out
    if kind == "milestone":
        return ["milestone_days"]
    if kind == "focus_streak":
        return ["focus_streak"]
    if kind == "big_week":
        return ["big_week"]
    if kind in NEEDS_EVENTS:
        return [kind]
    return []


class Voice:
    """Whether he may say a line now, and which one. See the module docstring for the rules."""

    def __init__(self, bag: PhraseBag, config, clock=time.time) -> None:
        self._bag = bag
        self._config = config
        self._clock = clock
        self._launched = clock()
        #: Wall-clock second of the last line, or None before the first.
        self._last: float | None = None
        #: The day the count below belongs to, and how many lines have been said in it.
        self._day = ""
        self._count = 0
        #: The once-a-day remarks made today (cleared with the count when the day changes).
        self._made: set[str] = set()

    @property
    def level(self) -> str:
        """``chatter_level``, read on every call so Settings → Apply needs no restart."""
        return str(self._config["chatter_level"])

    @property
    def spoken_today(self) -> int:
        """Lines said so far today - for the tests and the rate script."""
        self._roll(self._clock())
        return self._count

    def _roll(self, now: float) -> None:
        today = _today(now)
        if today != self._day:
            self._day = today
            self._count = 0
            self._made = set()

    def may_speak(self, category: str) -> bool:
        """Whether a line in ``category`` may be said right now. Asks; changes nothing."""
        limits = LEVELS.get(self.level)
        if limits is None:
            return False
        gap_s, per_day = limits
        if category not in self._bag.categories():
            return False
        now = self._clock()
        # A clock set backwards reads as "just launched" or "just spoke" until it catches up -
        # which errs on the side of saying nothing, the right side to err on here.
        if now - self._launched < LAUNCH_QUIET_S:
            return False
        self._roll(now)
        if self._count >= per_day:
            return False
        if category in ONCE_A_DAY and category in self._made:
            return False
        if (
            self._last is not None
            and now - self._last < gap_s
            and not category.startswith(GREETING_PREFIX)
        ):
            return False
        return True

    def say(self, category: str, **fmt: Any) -> str:
        """The line to say, or ``""`` if he may not (or has nothing to say for ``category``).

        Only a line actually handed back counts toward the gap and the day's cap, so asking about
        an unknown category - or being refused - never uses anything up. ``{name}`` defaults to his
        ``name`` setting; anything else in a line's braces comes from ``fmt``, and a placeholder
        nobody supplied is left as written rather than breaking the line.
        """
        if not self.may_speak(category):
            return ""
        fmt.setdefault("name", str(self._config["name"]))
        line = self._bag.next(category, **fmt)
        if not line:
            return ""
        self._last = self._clock()
        self._count += 1
        if category in ONCE_A_DAY:
            self._made.add(category)
        return line
