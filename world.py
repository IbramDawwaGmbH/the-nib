"""What Nib can tell about the world without asking anyone.

Everything here is derived from the local clock or from a cheap Win32 counter. Nothing touches the
network, the filesystem, or any other application's contents - the whole point of this module is
that "he seems to know what's going on" costs a handful of microseconds and gives up no privacy.

Deliberately Qt-free, like :mod:`behavior`, so the sensing can be tested headlessly and so the
mascot's reactions can be reasoned about without a running event loop.

The polling contract: :meth:`World.read` is called on the app's slow geometry timer (~700ms), never
on the animation tick. It returns an immutable :class:`WorldState` snapshot; behaviour only ever
reads that snapshot, so a probe that fails degrades to "nothing interesting is happening" rather
than to an exception in the middle of a frame.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime

log = logging.getLogger(__name__)


# --- parts of the day ----------------------------------------------------------------
#
# Boundaries, not durations, so a clock change or a laptop resume can never leave him stuck in
# yesterday's mood: the part of day is recomputed from the wall clock every single read.

NIGHT_END = 5.0        # 05:00 - before this he is still winding down from last night
MORNING_END = 9.5      # 09:30 - yawning and coffee live in here
DAY_END = 18.0         # 18:00
EVENING_END = 22.0     # 22:00 - after this he droops

PART_NIGHT = "night"
PART_MORNING = "morning"
PART_DAY = "day"
PART_EVENING = "evening"

SEASON_WINTER = "winter"
SEASON_SPRING = "spring"
SEASON_SUMMER = "summer"
SEASON_AUTUMN = "autumn"

#: Meteorological seasons by month, northern hemisphere. ``southern_seasons`` in the config flips
#: this by six months, which is the whole of what "the other half of the planet" needs.
_SEASON_BY_MONTH = {
    12: SEASON_WINTER, 1: SEASON_WINTER, 2: SEASON_WINTER,
    3: SEASON_SPRING, 4: SEASON_SPRING, 5: SEASON_SPRING,
    6: SEASON_SUMMER, 7: SEASON_SUMMER, 8: SEASON_SUMMER,
    9: SEASON_AUTUMN, 10: SEASON_AUTUMN, 11: SEASON_AUTUMN,
}
_OPPOSITE_SEASON = {
    SEASON_WINTER: SEASON_SUMMER, SEASON_SUMMER: SEASON_WINTER,
    SEASON_SPRING: SEASON_AUTUMN, SEASON_AUTUMN: SEASON_SPRING,
}


def part_of_day(hour: float) -> str:
    if hour < NIGHT_END:
        return PART_NIGHT
    if hour < MORNING_END:
        return PART_MORNING
    if hour < DAY_END:
        return PART_DAY
    if hour < EVENING_END:
        return PART_EVENING
    return PART_NIGHT


def season_for(month: int, *, southern: bool = False) -> str:
    season = _SEASON_BY_MONTH.get(int(month), SEASON_SPRING)
    return _OPPOSITE_SEASON[season] if southern else season


def parse_clock(spec: str) -> float | None:
    """``"16:00"`` -> ``16.0``. None for an empty or unparseable value, which disables the feature.

    Accepts a bare hour too (``"16"``), because that is what people type.
    """
    text = str(spec).strip()
    if not text:
        return None
    parts = text.split(":")
    try:
        hours = int(parts[0])
        minutes = int(parts[1]) if len(parts) > 1 else 0
    except (TypeError, ValueError):
        return None
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return hours + minutes / 60.0


# --- CPU ------------------------------------------------------------------------------

_kernel32 = ctypes.windll.kernel32


class _FILETIME(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    @property
    def ticks(self) -> int:
        return (int(self.high) << 32) | int(self.low)


_kernel32.GetSystemTimes.argtypes = [
    ctypes.POINTER(_FILETIME), ctypes.POINTER(_FILETIME), ctypes.POINTER(_FILETIME)
]
_kernel32.GetSystemTimes.restype = wintypes.BOOL


def _system_times() -> tuple[int, int] | None:
    """``(busy_ticks, total_ticks)`` cumulative since boot, or None if the call fails.

    ``GetSystemTimes`` reports kernel time *including* idle, so busy is total minus idle.
    """
    idle, kernel, user = _FILETIME(), _FILETIME(), _FILETIME()
    try:
        if not _kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        ):
            return None
    except Exception:
        log.debug("GetSystemTimes failed", exc_info=True)
        return None
    total = kernel.ticks + user.ticks
    return total - idle.ticks, total


#: A sample separated from the previous one by more than this is thrown away. The counters keep
#: their own notion of elapsed time across suspend, so the first read after the lid opens shows a
#: huge idle jump that would otherwise read as a machine that has been asleep - which it has, but
#: reacting to it is meaningless.
_MAX_SAMPLE_GAP_S = 8.0
_MIN_SAMPLE_GAP_S = 0.25


class CpuSampler:
    """Rolling system-wide CPU load, and a *sustained* busy flag.

    Two things this is careful about, both of which would otherwise make the reaction look broken:

    * **It is aggregate.** One core pegged out of sixteen reads as ~6%, so a heavy single-threaded
      build will never approach a naive 80% threshold. The default is set low enough to notice real
      load and the sustain window keeps a passing spike from tripping it.
    * **Nib is in the number.** He costs ~1% himself, which is why the reaction is a brief antic
      rather than a state he sits in - reacting to load by animating harder is a feedback loop.
    """

    def __init__(
        self,
        busy_percent: float = 70.0,
        sustain_s: float = 6.0,
        *,
        task_quiet_percent: float = 35.0,
        task_min_busy_s: float = 45.0,
        task_finished_hold_s: float = 12.0,
    ) -> None:
        self.busy_percent = float(busy_percent)
        self.sustain_s = float(sustain_s)
        #: Below this, load counts as genuinely quiet again - well under ``busy_percent``, so
        #: hovering near the busy line does not flicker between the two.
        self.task_quiet_percent = float(task_quiet_percent)
        #: How long a sustained-busy stretch has to run before its end is worth celebrating - a
        #: single-digit-second spike is not a "task", it is a rendering glitch or a background scan.
        self.task_min_busy_s = float(task_min_busy_s)
        #: How long ``task_just_finished`` stays true after the drop, giving the mascot's own
        #: (much slower) decision loop a real window to notice it rather than requiring the two to
        #: land on the exact same poll.
        self.task_finished_hold_s = float(task_finished_hold_s)
        self.percent = 0.0
        self._previous: tuple[int, int] | None = None
        self._previous_at = 0.0
        self._busy_for_s = 0.0
        #: A separate, more tolerant accumulator from ``_busy_for_s`` - see ``_update_task_pattern``.
        self._task_busy_for_s = 0.0
        self._task_armed = False
        self._task_finished_remaining_s = 0.0

    @property
    def busy(self) -> bool:
        return self._busy_for_s >= self.sustain_s

    @property
    def task_just_finished(self) -> bool:
        """A sustained-busy stretch has just dropped back to quiet.

        Held true for ``task_finished_hold_s`` rather than firing for a single instant: the
        mascot's reaction loop only runs when it is about to choose a new action, not on every
        poll, so a one-tick pulse would routinely be missed. The hold gives it a real window, and
        its own cooldown (once it reacts) is what stops it firing again for the same episode.
        """
        return self._task_finished_remaining_s > 0.0

    def _update_task_pattern(self, percent: float, gap_s: float) -> None:
        """The sustained-busy-then-drop detector behind ``task_just_finished``.

        Deliberately separate from ``_busy_for_s``/``busy`` above, which resets on *any* dip below
        ``busy_percent`` - correct for "is it pinned right now", wrong here. A real build or render
        dips in and out of full load between compile units, so this only resets on a genuine return
        to quiet (``task_quiet_percent``, a distinct and lower line), not on every sub-threshold
        sample - the dead zone between the two thresholds holds the accumulator steady rather than
        clearing it.
        """
        # Aged first, using whatever remained from an *earlier* call - so the call that sets a
        # fresh hold below is not immediately docked a sample's worth of it.
        if self._task_finished_remaining_s > 0.0:
            self._task_finished_remaining_s = max(0.0, self._task_finished_remaining_s - gap_s)
        if percent >= self.busy_percent:
            self._task_busy_for_s += gap_s
            if self._task_busy_for_s >= self.task_min_busy_s:
                self._task_armed = True
        elif percent <= self.task_quiet_percent:
            if self._task_armed:
                self._task_finished_remaining_s = self.task_finished_hold_s
            self._task_armed = False
            self._task_busy_for_s = 0.0

    def _reset_task_pattern(self) -> None:
        """Discard an in-progress episode - a sample gap too big or small to trust one either way."""
        self._task_busy_for_s = 0.0
        self._task_armed = False

    def read(self) -> float:
        """Sample the counters and return the current load percentage."""
        now = time.monotonic()
        current = _system_times()
        if current is None:
            return self.percent

        previous, previous_at = self._previous, self._previous_at
        self._previous, self._previous_at = current, now
        if previous is None:
            return self.percent

        gap = now - previous_at
        if gap < _MIN_SAMPLE_GAP_S or gap > _MAX_SAMPLE_GAP_S:
            # Too close together to be meaningful, or a resume - either way, restart cleanly. A
            # sleep/resume gap in particular must never read as a task quietly finishing.
            self._busy_for_s = 0.0
            self._reset_task_pattern()
            return self.percent

        busy_delta = current[0] - previous[0]
        total_delta = current[1] - previous[1]
        if total_delta <= 0 or busy_delta < 0:
            return self.percent

        percent = 100.0 * busy_delta / total_delta
        if not (0.0 <= percent <= 100.5):
            return self.percent  # counters wrapped or lied; ignore this sample

        # Smoothed, so one busy sample between two quiet ones does not swing the number about.
        self.percent = self.percent * 0.5 + percent * 0.5
        if self.percent >= self.busy_percent:
            self._busy_for_s += gap
        else:
            self._busy_for_s = 0.0
        self._update_task_pattern(self.percent, gap)
        return self.percent


def cpu_count() -> int:
    """Logical processors, purely so the aggregate-CPU caveat can be logged at startup."""
    try:
        return max(1, int(_kernel32.GetActiveProcessorCount(0xFFFF)))
    except Exception:
        return 1


# --- the snapshot ---------------------------------------------------------------------


@dataclass(frozen=True)
class WorldState:
    """Everything the mascot is allowed to know about the world, sampled at one instant."""

    hour: float             # 0.0 - 24.0, local
    weekday: int            # 0 = Monday
    date_key: str           # "2026-07-30", the token the once-a-day latches compare against
    week_key: str           # "2026-W31", the token the once-a-week latch compares against
    part_of_day: str
    season: str
    cpu_percent: float
    cpu_busy: bool
    task_just_finished: bool = False

    @property
    def is_weekend(self) -> bool:
        return self.weekday >= 5

    @property
    def is_friday(self) -> bool:
        return self.weekday == 4


class World:
    """Polls the cheap signals and hands out immutable snapshots."""

    def __init__(self, *, busy_percent: float = 70.0, southern: bool = False) -> None:
        self._cpu = CpuSampler(busy_percent=busy_percent)
        self._southern = bool(southern)
        self.state = self.read()

    @property
    def cpu(self) -> CpuSampler:
        return self._cpu

    def read(self, now: datetime | None = None) -> WorldState:
        """Take a fresh snapshot. Cheap enough for the 700ms geometry timer."""
        moment = now or datetime.now()
        hour = moment.hour + moment.minute / 60.0 + moment.second / 3600.0
        iso_year, iso_week, _iso_day = moment.isocalendar()
        percent = self._cpu.read()
        self.state = WorldState(
            hour=hour,
            weekday=moment.weekday(),
            date_key=moment.strftime("%Y-%m-%d"),
            week_key=f"{iso_year}-W{iso_week:02d}",
            part_of_day=part_of_day(hour),
            season=season_for(moment.month, southern=self._southern),
            cpu_percent=percent,
            cpu_busy=self._cpu.busy,
            task_just_finished=self._cpu.task_just_finished,
        )
        return self.state
