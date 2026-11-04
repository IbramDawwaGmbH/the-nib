"""What he remembers about the two of you - counts, and nothing else.

:mod:`mood` carries one number across a restart: how the day has gone. This carries the longer
story: the day you first ran him, how many days he has seen you since, and running counts of the
things you do together - pets, prompts, focus sessions finished, treats, throws, hard landings. A
rolling window of the last few days keeps the same counts per day, plus how many minutes you were
at the machine and how many it was busy, so "yesterday was a long one" is something he can know.

**Counts only.** No app names, no times of anything, no text. The one thing close to a time is an
optional 24-slot tally of the *hour* he first saw you each day (``memory_routine_enabled``), which
is how he can know you are usually in around eight without keeping a single timestamp. Everything
here is listed, key by key, in ``docs/PRIVACY.md`` - and that page has to stay exactly true, which
is why the file holds nothing that page does not name.

**It lives in its own file**, ``memory.json`` next to ``config.json``, rather than in the config.
The config is flat scalars on purpose (``Config._coerce`` shapes every value to its default, and
does not look inside a dict), and this is structured. Keeping it separate also makes **Forget
everything** honest: it deletes one file, and your settings are not in it.

Qt-free and clock-injectable, like :mod:`mood`, so a month of days can be simulated in a test.

**It never fires anything.** Like the mood, this is read by other things - a greeting, a line in
Settings - and does nothing on its own.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import time
from dataclasses import asdict, dataclass, fields
from datetime import date, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

MEMORY_FILE = "memory.json"
#: Bumped if the shape of the file ever changes in a way an older reader would misread.
MEMORY_VERSION = 1

#: How many days the per-day window keeps: today plus the seven before it. A week is what "a heavy
#: day" is judged against, and what "the last seven days" in Settings means; anything older is
#: only in the lifetime totals.
MAX_DAYS = 8

#: Learned taste is a gentle lean on how often an antic is picked, never a switch. Bounded both
#: ways so no amount of rewarding or ignoring one can make it dominate the pool or vanish from it.
TASTE_MIN, TASTE_MAX = 0.7, 1.4
#: How far an unrewarded taste drifts back towards neutral per day.
TASTE_DECAY_PER_DAY = 0.01

#: The counters that also keep a lifetime total. The two minute counters are per-day only: "you
#: have been at your desk for 31,000 minutes since March" is not something anyone wants told.
TOTAL_KEYS = ("prompts", "pets", "focus_done", "treats", "throws", "hurts")

#: How many first-sightings the arrival tally needs before he will claim to know your routine.
#: Fewer than this and one early start would be "usually".
USUAL_HOUR_MIN_SAMPLES = 5

#: "A heavy day": yesterday's load at least this many times the typical one, and you were there
#: for at least this long. The presence floor is what stops a machine left rendering overnight -
#: busy, but with nobody at it - from counting as *your* heavy day.
HEAVY_FACTOR = 1.6
HEAVY_MIN_PRESENT_MIN = 120
#: Busy minutes are scaled to be comparable with prompts: ten minutes of the machine hard at work
#: weighs about the same as one thing asked.
BUSY_MIN_PER_POINT = 10.0
#: How many of the six days before yesterday must have been recorded before any comparison is
#: made. Two days of history is not a "usual".
HEAVY_MIN_HISTORY = 3

#: "A big week": this week's prompts per day at least this many times last week's, with at least
#: this many recorded days on each side. See :meth:`Memory.big_week` for why last week is partial.
BIG_WEEK_FACTOR = 1.5
BIG_WEEK_MIN_DAYS = 2

#: The bond: how well he knows you, 0..1. Each term saturates logarithmically at its own scale -
#: ``k`` is the count at which that term is full - so the first weeks move it most and it never
#: needs a counter to keep growing for ever. Days together weigh the most: turning up matters more
#: than any one thing done.
_BOND_TERMS = (
    ("days_seen", 0.45, 120),
    ("pets", 0.30, 600),
    ("prompts", 0.15, 300),
    ("focus_done", 0.10, 60),
)

#: Words for the bond, low to high. Never a number, for the same reason the mood has none: a
#: number invites optimising, a word just describes.
_BOND_LABELS = (
    (0.15, "just met"),
    (0.35, "warming up"),
    (0.60, "friends"),
    (0.85, "close"),
)
_BOND_TOP_LABEL = "inseparable"

#: Month names spelled out here rather than through ``strftime("%b")``, which follows the system
#: locale - a German Windows would print "Okt" in an otherwise English sentence.
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass
class DayRecord:
    """One day's counts. Every field is a plain non-negative count."""

    prompts: int = 0
    pets: int = 0
    focus_done: int = 0
    busy_min: int = 0
    present_min: int = 0
    treats: int = 0
    throws: int = 0
    hurts: int = 0


DAY_KEYS = tuple(f.name for f in fields(DayRecord))


def _count(value) -> int:
    """A stored count, forced to a non-negative int. Anything unreadable is a zero."""
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return max(0, number)


def _parse_day(key) -> date | None:
    """``"2026-11-04"`` -> a date, or None for anything else (a hand edit, a corrupt key)."""
    if not isinstance(key, str):
        return None
    try:
        return date.fromisoformat(key)
    except ValueError:
        return None


def _bond_term(count: int, k: int) -> float:
    return math.log1p(max(0, count)) / math.log1p(k)


class Memory:
    """The counters, the 7-day window and the arrival tally, and the file they live in.

    ``path`` is where ``memory.json`` goes; ``None`` keeps everything in memory only, which is what
    a test - or a ``Behavior`` built without one - gets. ``config`` is read for ``memory_enabled``
    and ``memory_routine_enabled`` and never written. ``clock`` is injectable like ``Mood``'s.

    Every mutator is a no-op while ``memory_enabled`` is off, and so is saving: switching it off
    means the file is not created, and an existing one is left exactly as it was. ``forget()`` is
    the exception, and works either way - wiping what is already there is not "remembering".
    """

    def __init__(self, path: Path | None, config, clock=time.time) -> None:
        self.path = Path(path) if path is not None else None
        self._config = config
        self._clock = clock
        self.dirty = False
        self._clear()
        self.load()

    def _clear(self) -> None:
        self._first_met = ""
        #: The newest day he has counted as "seen", so a restart the same day is not a new day.
        #: Kept in the file for exactly that reason; it is a date, not a time.
        self._last_seen = ""
        self._days_seen = 0
        self._totals: dict[str, int] = {key: 0 for key in TOTAL_KEYS}
        self._days: dict[str, DayRecord] = {}
        self._arrival_hours: list[int] = [0] * 24
        self._taste: dict[str, float] = {}

    # --- switches -------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self._config.get("memory_enabled", True))

    @property
    def routine_enabled(self) -> bool:
        return bool(self._config.get("memory_routine_enabled", True))

    # --- persistence ----------------------------------------------------------------

    def load(self) -> None:
        """Read the file. Missing is an empty memory; corrupt is logged and treated the same.

        Same stance as ``Config.load``: a file he cannot read must never stop him starting, and
        must never raise. Every value is re-shaped on the way in, so a hand edit that puts a
        string where a count goes costs that one count rather than the whole file.
        """
        self._clear()
        self.dirty = False
        if self.path is None:
            return
        raw: dict = {}
        try:
            if self.path.is_file():
                text = self.path.read_text(encoding="utf-8")
                parsed = json.loads(text) if text.strip() else {}
                if isinstance(parsed, dict):
                    raw = parsed
                else:
                    log.warning("memory root is %s, not an object; starting empty", type(parsed))
        except (OSError, ValueError) as exc:
            log.warning("could not read %s (%s); starting with an empty memory", self.path, exc)
            return

        if _parse_day(raw.get("first_met")) is not None:
            self._first_met = raw["first_met"]
        if _parse_day(raw.get("last_seen")) is not None:
            self._last_seen = raw["last_seen"]
        self._days_seen = _count(raw.get("days_seen", 0))

        totals = raw.get("totals")
        if isinstance(totals, dict):
            for key in TOTAL_KEYS:
                self._totals[key] = _count(totals.get(key, 0))

        days = raw.get("days")
        if isinstance(days, dict):
            for key, record in days.items():
                if _parse_day(key) is None or not isinstance(record, dict):
                    continue
                self._days[key] = DayRecord(**{f: _count(record.get(f, 0)) for f in DAY_KEYS})
            self._prune()

        hours = raw.get("arrival_hours")
        if isinstance(hours, list) and len(hours) == 24:
            self._arrival_hours = [_count(n) for n in hours]

        taste = raw.get("taste")
        if isinstance(taste, dict):
            for name, value in taste.items():
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                if isinstance(name, str) and math.isfinite(number):
                    self._taste[name] = max(TASTE_MIN, min(TASTE_MAX, number))

    def _payload(self) -> dict:
        payload = {
            "version": MEMORY_VERSION,
            "first_met": self._first_met,
            "last_seen": self._last_seen,
            "days_seen": self._days_seen,
            "totals": dict(self._totals),
            "days": {key: asdict(record) for key, record in sorted(self._days.items())},
        }
        # Only written while the routine is switched on: with it off, the file does not hold an
        # hour tally at all - not even a row of zeros - so PRIVACY.md's "if it is on" stays true.
        if self.routine_enabled:
            payload["arrival_hours"] = list(self._arrival_hours)
        # Only once there is something in it, so a file written before anything learned a taste
        # holds exactly the keys the privacy page lists.
        if self._taste:
            payload["taste"] = {name: round(value, 3) for name, value in sorted(self._taste.items())}
        return payload

    def save_if_dirty(self) -> bool:
        """Write the file if anything changed. Called from the app's 10s config flush.

        Cheap to call that often because nothing per-tick lands here: the most frequent change is
        a minute of presence, so the file is written at most about once a minute while you are at
        the machine, and not at all while nothing happens.
        """
        if not self.enabled:
            # Not even a mkdir. Off means off. Nothing new is counted while it is (every mutator
            # checks), so all this defers is whatever was already pending when it went off.
            self.dirty = False
            return True
        if not self.routine_enabled and any(self._arrival_hours):
            # Switched off since it was learned: forget the tally rather than keep it unused.
            self._arrival_hours = [0] * 24
            self.dirty = True
        if not self.dirty:
            return True
        if self.path is None:
            self.dirty = False
            return True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(self.path.parent),
                prefix=self.path.name + ".",
                suffix=".tmp",
                delete=False,
            )
            try:
                with handle:
                    json.dump(self._payload(), handle, indent=2, sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(handle.name, self.path)
            except BaseException:
                try:
                    os.unlink(handle.name)
                except OSError:
                    pass
                raise
        except OSError as exc:
            log.warning("could not save %s: %s", self.path, exc)
            return False
        self.dirty = False
        return True

    # --- recording ------------------------------------------------------------------

    def _prune(self) -> None:
        """Keep only the newest ``MAX_DAYS`` days. ISO dates sort as text, oldest first."""
        if len(self._days) > MAX_DAYS:
            for key in sorted(self._days)[:-MAX_DAYS]:
                del self._days[key]

    def record(self, event: str, date_key: str, n: int = 1) -> None:
        """Count ``n`` of ``event`` (a :class:`DayRecord` field) on ``date_key``.

        Bumps the lifetime total too, for the events that have one. An unknown event or an
        unreadable date is ignored rather than raised: a typo in a caller should cost a count, not
        the tick it was called from.
        """
        if not self.enabled or n <= 0 or event not in DAY_KEYS or _parse_day(date_key) is None:
            return
        record = self._days.get(date_key)
        if record is None:
            record = self._days[date_key] = DayRecord()
            self._prune()
        setattr(record, event, getattr(record, event) + int(n))
        if event in self._totals:
            self._totals[event] += int(n)
        self.dirty = True

    def first_sight(self, date_key: str, hour: float, *, tally: bool = True) -> bool:
        """The first time he has seen you on ``date_key``. True if it was a new day.

        Counted once per day however many times he is restarted, and only ever *forwards*: a
        clock wound back a day (a timezone change, a dual boot) does not count the same day twice.

        ``tally`` says whether this sighting was an *arrival* - he has just started, or you have
        just come back - and so belongs in the hour tally. The app passes False when the day
        merely rolled over under him while you were there all along: midnight at the desk, or a
        machine left running overnight with away detection off, would otherwise file "00:00" as
        your arrival every single day. It then reports the real arrival later through
        :meth:`tally_arrival`. The tally is only ever touched while ``memory_routine_enabled`` is on.
        """
        if not self.enabled or _parse_day(date_key) is None:
            return False
        if self._last_seen and date_key <= self._last_seen:
            return False
        self._last_seen = date_key
        self._days_seen += 1
        if not self._first_met:
            self._first_met = date_key
        if tally:
            self.tally_arrival(hour)
        self.dirty = True
        return True

    def tally_arrival(self, hour: float) -> None:
        """Add one arrival at ``hour`` to the 24-slot tally, if the routine is switched on.

        Not once-a-day by itself: :meth:`first_sight` calls it for an ordinary arrival, and the
        app calls it directly only for a day whose first sighting was a rollover (see there), the
        first time you come back that day.
        """
        if not self.enabled or not self.routine_enabled:
            return
        slot = max(0, min(23, int(hour)))
        self._arrival_hours[slot] += 1
        self.dirty = True

    # --- reading --------------------------------------------------------------------

    @property
    def days_seen(self) -> int:
        return self._days_seen

    @property
    def first_met(self) -> str:
        return self._first_met

    @property
    def totals(self) -> dict[str, int]:
        """A copy - handing out the live dict would let a caller change it without ``dirty``."""
        return dict(self._totals)

    @property
    def is_empty(self) -> bool:
        """Nothing remembered at all - what the Forget button is greyed out on."""
        return (
            not self._days_seen and not any(self._totals.values()) and not self._days
            and not any(self._arrival_hours) and not self._taste and not self._first_met
        )

    def day(self, date_key: str) -> DayRecord:
        """That day's counts, or an all-zero record. Always a copy."""
        record = self._days.get(date_key)
        return DayRecord(**asdict(record)) if record is not None else DayRecord()

    def yesterday(self, date_key: str) -> DayRecord | None:
        """The calendar day before ``date_key``, or None if he has nothing for it."""
        today = _parse_day(date_key)
        if today is None:
            return None
        key = (today - timedelta(days=1)).isoformat()
        return self.day(key) if key in self._days else None

    def usual_hour(self) -> float | None:
        """The hour he most often first sees you, once there are enough days to say so.

        The earliest hour wins a tie: "usually around eight" is the kinder guess than nine.
        """
        if not self.enabled or not self.routine_enabled:
            return None
        if sum(self._arrival_hours) < USUAL_HOUR_MIN_SAMPLES:
            return None
        best = max(self._arrival_hours)
        return float(self._arrival_hours.index(best))

    @staticmethod
    def _load_of(record: DayRecord) -> float:
        return record.prompts + record.busy_min / BUSY_MIN_PER_POINT

    def heavy_yesterday(self, date_key: str) -> bool:
        """Was yesterday a long one, compared with the six days before it?

        Yesterday's load (prompts, plus busy minutes scaled down) against the median of the six
        days before it, with two guards the bare ratio does not have. A day missing from the
        window counts as a zero - you weren't there - but at least ``HEAVY_MIN_HISTORY`` of them
        must exist, and the median must be above zero: on a fresh install every comparison is
        against nothing, and *any* day would otherwise be "1.6 times" a week of zeros.
        """
        if not self.enabled:
            return False
        today = _parse_day(date_key)
        if today is None:
            return False
        before = self.yesterday(date_key)
        if before is None or before.present_min < HEAVY_MIN_PRESENT_MIN:
            return False
        history: list[float] = []
        recorded = 0
        for back in range(2, 8):
            key = (today - timedelta(days=back)).isoformat()
            if key in self._days:
                recorded += 1
                history.append(self._load_of(self._days[key]))
            else:
                history.append(0.0)
        if recorded < HEAVY_MIN_HISTORY:
            return False
        history.sort()
        median = (history[2] + history[3]) / 2.0
        if median <= 0:
            return False
        return self._load_of(before) >= HEAVY_FACTOR * median

    def big_week(self, date_key: str) -> bool:
        """Has this week asked more of him than last week did? Asked on a Friday evening.

        Prompts per recorded day this week (Monday up to ``date_key``) against prompts per recorded
        day of last week - **as much of last week as the window still holds**, which on a Friday is
        only its Friday and weekend: the window is eight days, not fourteen, on purpose (see
        ``MAX_DAYS``), and widening it to answer this one question would mean keeping a second
        week of everything. So it is a lean judgement, and deliberately hard to trip: at least
        ``BIG_WEEK_MIN_DAYS`` recorded days on each side, last week's rate above zero, and this
        week's at least ``BIG_WEEK_FACTOR`` times it. Averages rather than sums, so five days
        against three is not "bigger" just for being longer.
        """
        if not self.enabled:
            return False
        today = _parse_day(date_key)
        if today is None:
            return False
        monday = today - timedelta(days=today.weekday())
        this_week: list[int] = []
        last_week: list[int] = []
        for key, record in self._days.items():
            when = _parse_day(key)
            if when is None or when > today:
                continue
            if when >= monday:
                this_week.append(record.prompts)
            elif when >= monday - timedelta(days=7):
                last_week.append(record.prompts)
        if len(this_week) < BIG_WEEK_MIN_DAYS or len(last_week) < BIG_WEEK_MIN_DAYS:
            return False
        before = sum(last_week) / len(last_week)
        if before <= 0:
            return False
        return sum(this_week) / len(this_week) >= BIG_WEEK_FACTOR * before

    def bond(self) -> float:
        """How well he knows you, 0..1. Grows with the counters and can never go down by itself.

        A flat 0.0 while memory is off, so everything that leans on it behaves as on day one.
        """
        if not self.enabled:
            return 0.0
        counts = {"days_seen": self._days_seen, **self._totals}
        total = sum(weight * _bond_term(counts[key], k) for key, weight, k in _BOND_TERMS)
        return max(0.0, min(1.0, total))

    def bond_label(self) -> str:
        bond = self.bond()
        for ceiling, label in _BOND_LABELS:
            if bond < ceiling:
                return label
        return _BOND_TOP_LABEL

    # --- taste ----------------------------------------------------------------------

    @property
    def taste(self) -> dict[str, float]:
        """Multipliers for antics he has learned you like or don't. A copy; 1.0 when absent."""
        return dict(self._taste) if self.enabled else {}

    def adjust_taste(self, antic: str, delta: float) -> None:
        if not self.enabled or not antic:
            return
        current = self._taste.get(antic, 1.0)
        value = max(TASTE_MIN, min(TASTE_MAX, current + float(delta)))
        if value != current:
            self._taste[antic] = value
            self.dirty = True

    def decay_taste(self, days: int) -> None:
        """Drift every taste back towards neutral, ``TASTE_DECAY_PER_DAY`` per day."""
        if not self.enabled or days <= 0 or not self._taste:
            return
        keep = (1.0 - TASTE_DECAY_PER_DAY) ** days
        faded = {}
        for name, value in self._taste.items():
            value = 1.0 + (value - 1.0) * keep
            if abs(value - 1.0) >= 0.005:   # close enough to neutral is neutral, and dropped
                faded[name] = value
        self._taste = faded
        self.dirty = True

    # --- forgetting -----------------------------------------------------------------

    def forget(self) -> None:
        """Wipe everything and delete the file. Works whether or not memory is switched on."""
        self._clear()
        self.dirty = False
        if self.path is not None:
            try:
                self.path.unlink(missing_ok=True)
            except OSError as exc:
                log.warning("could not delete %s: %s", self.path, exc)
        log.info("memory forgotten")

    # --- in words -------------------------------------------------------------------

    @staticmethod
    def _plural(n: int, one: str, many: str | None = None) -> str:
        return f"{n:,} {one if n == 1 else (many or one + 's')}"

    def summary_lines(self, name: str) -> list[str]:
        """What Settings → About him shows, in plain English. Empty when there is nothing yet."""
        lines: list[str] = []
        if self._days_seen:
            line = f"{name} has known you {self._plural(self._days_seen, 'day')}"
            met = _parse_day(self._first_met)
            if met is not None:
                line += f" (since {met.day} {_MONTHS[met.month - 1]} {met.year})"
            lines.append(line + ".")
        counts = [
            self._plural(self._totals["pets"], "pet"),
            self._plural(self._totals["prompts"], "prompt"),
            self._plural(self._totals["focus_done"], "focus session"),
        ]
        # Only once there has been one: a "0 treats" before treats exist reads like a reproach.
        if self._totals["treats"]:
            counts.append(self._plural(self._totals["treats"], "treat"))
        if any(self._totals.values()):
            lines.append(" · ".join(counts))
        hour = self.usual_hour()
        if hour is not None:
            lines.append(f"Usually sees you around {int(hour):02d}:00.")
        today = time.strftime("%Y-%m-%d", time.localtime(self._clock()))
        if self.heavy_yesterday(today):
            lines.append("Yesterday was a heavy one.")
        return lines
