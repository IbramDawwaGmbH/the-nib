"""The Away Guard's state machine: who is allowed to see the desktop, and what counts as an intrusion.

Pure logic on purpose - no Qt, no ``ctypes``. Everything that *does* something (the overlays, the
keyboard hook in ``guard_core``, Windows Hello, the peek chip) lives elsewhere and only reports in
or listens here, so the rules themselves can be tested exhaustively with a fake clock and no
windows on screen (``tests/smoke_guard.py``).

The machine, in one picture::

    IDLE --guard--> FOG --suspicious--> BLACKOUT
                     |                     |
                     +------ auth ok ------+--> CHOICE --peek--> PEEK --timeout / lock now--> FOG
                                                       --unlock--> IDLE

Two deliberate asymmetries:

* BLACKOUT is sticky. Nothing but a successful authentication leaves it - a wrong PIN, another
  blocked key or the peek timer never bring the fog back. Whoever caused it has to be the owner
  to undo it.
* Suspicious events are only judged while the screens are actually covered (FOG/BLACKOUT). During
  PEEK the owner is at the keyboard and the core has its hook removed anyway; outside a guard
  there is nothing to guard.

The intrusion log is memory-only, like the rest of this module: it is shown once when the guard
ends and dropped on dismiss (``clear_log``). Nothing here is written to disk.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Callable

log = logging.getLogger(__name__)


class Mode(str, Enum):
    IDLE = "idle"
    FOG = "fog"
    BLACKOUT = "blackout"
    CHOICE = "choice"
    PEEK = "peek"


@dataclass
class LogEntry:
    """One line of the intrusion log. ``t`` is in the controller's clock (whatever it was given)."""

    t: float
    kind: str
    detail: str = ""


#: Event kinds that turn the fog into a blackout outright - each one means a person at the
#: keyboard tried to get past the guard (an escape key, Ctrl+Alt+Del / UAC, or the core being
#: killed and restarted).
BLACKOUT_KINDS = frozenset({"blocked_key", "desktop_switch", "core_restarted"})
#: A window grabbing the foreground is only an intrusion when a human just touched the machine;
#: with no recent input it is almost always an agent opening something, which is logged only.
FOREGROUND_KIND = "foreground_stolen"
#: Kinds that are bookkeeping rather than events: ``input`` is the core's throttled activity
#: heartbeat (the service uses it to work out ``recent_input``), and the session lock/unlock pair
#: is acted on by the service itself. None of them belong in the intrusion log.
SILENT_KINDS = frozenset({"input", "session_lock", "session_unlock"})
#: Log ``kind`` used for a wrong PIN / failed Hello, so the log card can count them.
AUTH_FAILED_KIND = "auth_failed"

BACKOFF_START_S = 1.0
BACKOFF_CAP_S = 30.0
#: With ``lock_fallback`` on, this many wrong attempts in a row ask the service to lock Windows.
LOCK_AFTER_ATTEMPTS = 10

_GUARDED = (Mode.FOG, Mode.BLACKOUT)


def backoff_for(attempts: int) -> float:
    """Seconds to wait after the ``attempts``-th wrong try: 1, 2, 4, 8 ... capped at 30."""
    if attempts <= 0:
        return 0.0
    # Clamp the exponent before raising so a very long run of attempts can't overflow.
    return min(BACKOFF_CAP_S, BACKOFF_START_S * (2 ** min(attempts - 1, 16)))


class GuardController:
    """The guard's rules. Callers drive it; listeners hear every mode change as ``(old, new)``."""

    def __init__(self, clock: Callable[[], float], *, peek_minutes: float = 3,
                 blackout_after: int = 3, lock_fallback: bool = False) -> None:
        self._clock = clock
        self.peek_minutes = max(0.0, float(peek_minutes))
        self.blackout_after = max(1, int(blackout_after))
        self.lock_fallback = bool(lock_fallback)

        self._mode = Mode.IDLE
        self._attempts = 0
        self._backoff_until = 0.0
        self._peek_until = 0.0
        self._peek_total = 0.0
        self._guarded_since: float | None = None
        self._log: list[LogEntry] = []
        self._listeners: list[Callable[[Mode, Mode], None]] = []
        #: Set when ``lock_fallback`` is on and the attempts reached ``LOCK_AFTER_ATTEMPTS``. The
        #: service reads it after each failed attempt, calls ``LockWorkStation`` and clears it.
        self.want_lock = False

    # --- read-only state ------------------------------------------------------------------

    @property
    def mode(self) -> Mode:
        return self._mode

    @property
    def attempts(self) -> int:
        """Wrong attempts since the last success (or since the guard started)."""
        return self._attempts

    @property
    def log(self) -> list[LogEntry]:
        return self._log

    @property
    def guarded_since(self) -> float | None:
        """Clock time the current guard started (``None`` when idle). Feeds ``{minutes}``."""
        return self._guarded_since

    @property
    def peek_total(self) -> float:
        """Length of the current peek in seconds, extensions included - the chip's ring total."""
        return self._peek_total if self._mode is Mode.PEEK else 0.0

    def add_listener(self, fn: Callable[[Mode, Mode], None]) -> None:
        self._listeners.append(fn)

    # --- transitions ----------------------------------------------------------------------

    def guard(self) -> None:
        """Cover the screens. From IDLE this starts a fresh guard; from PEEK/CHOICE it re-guards."""
        if self._mode in _GUARDED:
            return
        if self._mode is Mode.IDLE:
            self._guarded_since = self._clock()
            self._attempts = 0
            self._backoff_until = 0.0
            self.want_lock = False
        self._set_mode(Mode.FOG)

    def auth_ok(self) -> None:
        """The owner proved who they are: offer Peek / Unlock. Clears attempts and any backoff."""
        if self._mode not in _GUARDED:
            log.debug("auth_ok ignored in %s", self._mode.value)
            return
        self._attempts = 0
        self._backoff_until = 0.0
        self.want_lock = False
        self._set_mode(Mode.CHOICE)

    def auth_failed(self) -> float:
        """Count a wrong attempt and return how long the PIN/Hello controls should stay disabled.

        Reaching ``blackout_after`` turns FOG into BLACKOUT; the backoff keeps doubling past it.
        Outside FOG/BLACKOUT there's nothing to authenticate against, so it is ignored (0 s).
        """
        if self._mode not in _GUARDED:
            log.debug("auth_failed ignored in %s", self._mode.value)
            return 0.0
        now = self._clock()
        self._attempts += 1
        wait = backoff_for(self._attempts)
        self._backoff_until = now + wait
        self._log.append(LogEntry(now, AUTH_FAILED_KIND, f"attempt {self._attempts}"))
        log.info("guard: wrong attempt %d (backoff %.0f s)", self._attempts, wait)
        if self.lock_fallback and self._attempts >= LOCK_AFTER_ATTEMPTS:
            self.want_lock = True
        if self._attempts >= self.blackout_after and self._mode is Mode.FOG:
            self._set_mode(Mode.BLACKOUT)
        return wait

    def backoff_remaining(self) -> float:
        return max(0.0, self._backoff_until - self._clock())

    def choose_peek(self) -> None:
        """CHOICE -> PEEK for ``peek_minutes``. The tripwires pause; the timer re-guards."""
        if self._mode is not Mode.CHOICE:
            log.debug("choose_peek ignored in %s", self._mode.value)
            return
        self._peek_total = self.peek_minutes * 60.0
        self._peek_until = self._clock() + self._peek_total
        self._set_mode(Mode.PEEK)

    def choose_unlock(self) -> None:
        """CHOICE -> IDLE: the guard is over. The intrusion log is kept until ``clear_log``."""
        if self._mode is not Mode.CHOICE:
            log.debug("choose_unlock ignored in %s", self._mode.value)
            return
        self._end()

    def session_unlocked(self) -> None:
        """The owner unlocked Windows itself (``windows_unlock_clears_guard``): end from any mode.

        Equivalent to ``auth_ok()`` + ``choose_unlock()`` - the Windows password is the stronger
        proof - and also works from PEEK, where the service would otherwise have to re-guard first.
        """
        if self._mode is Mode.IDLE:
            return
        self._attempts = 0
        self._backoff_until = 0.0
        self.want_lock = False
        self._end()

    def extend_peek(self) -> None:
        """Another ``peek_minutes`` on the clock. No re-auth: the owner is evidently still here."""
        if self._mode is not Mode.PEEK:
            return
        extra = self.peek_minutes * 60.0
        # Extend from whichever is later, so a tap right at expiry (before the tick ran) still
        # gives the full extra time rather than a fraction of it.
        self._peek_until = max(self._peek_until, self._clock()) + extra
        self._peek_total += extra

    def lock_now(self) -> None:
        """PEEK -> FOG straight away (the chip's "Lock now")."""
        if self._mode is Mode.PEEK:
            self._set_mode(Mode.FOG)

    def peek_remaining(self) -> float:
        if self._mode is not Mode.PEEK:
            return 0.0
        return max(0.0, self._peek_until - self._clock())

    def tick(self) -> None:
        """Called on a timer by the service: an expired peek goes back to FOG."""
        if self._mode is Mode.PEEK and self._clock() >= self._peek_until:
            log.info("guard: peek over, re-guarding")
            self._set_mode(Mode.FOG)

    def event(self, kind: str, detail: str = "", *, recent_input: bool = False) -> None:
        """Judge something the core (or the service) noticed, per the suspicious-events table."""
        if self._mode not in _GUARDED or kind in SILENT_KINDS:
            return
        now = self._clock()
        self._log.append(LogEntry(now, kind, detail))
        if kind in BLACKOUT_KINDS or (kind == FOREGROUND_KIND and recent_input):
            log.info("guard: %s (%s) -> blackout", kind, detail or "-")
            if self._mode is Mode.FOG:
                self._set_mode(Mode.BLACKOUT)
        else:
            log.info("guard: %s (%s) logged", kind, detail or "-")

    def clear_log(self) -> None:
        self._log.clear()

    # --- internals ------------------------------------------------------------------------

    def _end(self) -> None:
        self._guarded_since = None
        self._peek_until = 0.0
        self._peek_total = 0.0
        self._set_mode(Mode.IDLE)

    def _set_mode(self, new: Mode) -> None:
        old = self._mode
        if new is old:
            return
        self._mode = new
        log.info("guard: %s -> %s", old.value, new.value)
        for fn in list(self._listeners):
            # One broken listener (an overlay mid-teardown, say) must not stop the others from
            # hearing about a blackout.
            try:
                fn(old, new)
            except Exception:
                log.exception("guard listener failed on %s -> %s", old.value, new.value)
