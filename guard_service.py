"""The Away Guard, wired together: the one object ``main`` talks to.

The guard is split into pieces that each know one thing - ``guard.GuardController`` the rules,
``guard_core`` the system hooks (in a separate process), ``guard_overlay`` the windows,
``guard_scene`` the show, ``guard_auth`` Hello and the PIN. None of them knows about the others.
This module is the glue, and it is deliberately *only* glue: every decision about whether something
is suspicious, how long a backoff is, or when a peek ends is the controller's; this file just
carries facts to it and carries its mode changes out to the screen and the core.

Why the shape is what it is:

* **One listener drives everything.** The controller announces ``(old, new)`` on every real mode
  change and :meth:`GuardService._on_mode` maps each transition to what the screen, the core and
  the mascot should do. Nothing else changes the overlays' mode, so there is exactly one place to
  read to know what happens on, say, PEEK -> FOG.
* **The core is spawned lazily and supervised.** It only runs while a guard is up. If it dies
  unexpectedly mid-guard it is restarted (with a cap, so a core that crashes on start does not
  respawn forever) and the controller hears ``core_restarted`` - a dead hook is exactly what an
  attacker killing the process from Task Manager would cause, so it blacks the screens out.
* **Quitting Nib while guarded sends ``unlock`` and waits for the core to exit.** Otherwise the core
  would see its parent die while guarded and do its job: cover every monitor in black.
* **Constructing the service does nothing visible.** No process, no window, no blocking WinRT call -
  ``NibApp`` (and the smoke tests that build one) pay for a guard only when one is started. The
  Hello availability probe runs once on a daemon thread and is cached.
* **A Nib restarted after dying mid-guard resumes in BLACKOUT.** The orphaned core holds a named
  mutex while it covers the screens; seeing it at start-up means the owner never unlocked, so the
  guard comes straight back up, blacked out, and the new core's ``guard`` releases the old one.

Nothing here is ever written to disk except the PIN *hash* (``guard_pin_hash``, scrypt - see
``guard_auth``). The intrusion log lives in the controller's memory and is cleared when its card is
dismissed.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, QProcess, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

import guard_auth
from guard import FOREGROUND_KIND, GuardController, LogEntry, Mode

log = logging.getLogger(__name__)

#: Service heartbeat: peek countdown, CHOICE timeout, core liveness. The chip only shows whole
#: seconds, so four times a second is plenty and costs nothing while idle (it is stopped then).
TICK_MS = 250
#: How often the status line (minutes guarded, battery) is rebuilt.
STATUS_MS = 5000
#: "A human did it" window for a stolen foreground: input within this many seconds before.
RECENT_INPUT_S = 2.0
#: An authenticated owner who walks off without choosing Peek or Unlock leaves an Unlock button on
#: the screen for anyone. After this long in CHOICE the guard quietly goes back to fog.
CHOICE_TIMEOUT_S = 60.0
#: Core supervision: ping this often, and treat this much silence as a hung core.
PING_EVERY_S = 5.0
CORE_SILENT_S = 20.0
#: Restart a dead core at most this many times inside the window, then give up (stay guarded).
CORE_RESTART_CAP = 3
CORE_RESTART_WINDOW_S = 60.0
CORE_RESTART_DELAY_MS = 500
#: How long ``shutdown`` waits for the core to exit after ``unlock`` before killing it.
CORE_EXIT_WAIT_MS = 1500
#: Auto-guard poll. Idle time is measured in minutes, so a coarse poll is fine.
AUTO_GUARD_POLL_MS = 15000
#: Status-line battery warning: on battery and at or below this percentage.
BATTERY_WARN_PERCENT = 20
#: A foreground change this soon after a Hello prompt ended belongs to the prompt closing (Windows
#: hands the foreground back to whatever it likes), not to a person at the keyboard.
HELLO_GRACE_S = 1.5
#: Foreground owners that are the Hello prompt itself: a change to one of these during the grace is
#: not even logged (anything else - the shell, an app window - goes into the intrusion log).
#: "pid:0" is the core's name for a window already gone - the prompt that just closed.
HELLO_HOST_EXES = frozenset({"", "pid:0", "credentialuibroker.exe", "consent.exe"})
#: How long a one-off note ("Hello is busy - type your PIN") stays on the status line.
NOTE_S = 6.0
#: After a click on the guard, how long until the PIN field is checked for really taking keys, and
#: how long to wait for the core's answer about the keyboard before judging without it.
VERIFY_AFTER_MS = 300
VERIFY_REPLY_MS = 400
#: Shortest PIN the first-run setup accepts.
MIN_PIN_LEN = 4

#: What the intrusion-log card calls each kind of entry.
_LOG_LABELS = {
    "blocked_key": "blocked {detail}",
    "desktop_switch": "Ctrl+Alt+Del or a secure desktop",
    "foreground_stolen": "a window took focus: {detail}",
    "core_restarted": "the keyboard guard had to restart",
}


# --- small Win32 helpers ----------------------------------------------------------------------
# A private WinDLL so these prototypes cannot collide with win_integration's on ctypes.windll.

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.LockWorkStation.argtypes = []
_user32.LockWorkStation.restype = wintypes.BOOL
_kernel32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.OpenMutexW.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL

_SYNCHRONIZE = 0x00100000


class _SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", wintypes.DWORD),
        ("BatteryFullLifeTime", wintypes.DWORD),
    ]


_kernel32.GetSystemPowerStatus.argtypes = [ctypes.POINTER(_SYSTEM_POWER_STATUS)]
_kernel32.GetSystemPowerStatus.restype = wintypes.BOOL


def battery_warning() -> str:
    """A short status-line note when running low on battery, else "". Never raises.

    Worth saying on the guard specifically: the whole point is leaving agents running while you are
    away, and a laptop that dies at 0 % stops them just as surely as a lock screen would.
    """
    try:
        status = _SYSTEM_POWER_STATUS()
        if not _kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return ""
        percent = int(status.BatteryLifePercent)
        on_battery = status.ACLineStatus == 0
        if on_battery and percent != 255 and percent <= BATTERY_WARN_PERCENT:
            return f"on battery, {percent}% - plug in to keep things running"
    except Exception:
        log.debug("battery status unavailable", exc_info=True)
    return ""


def lock_workstation() -> bool:
    try:
        return bool(_user32.LockWorkStation())
    except Exception:
        log.warning("LockWorkStation failed", exc_info=True)
        return False


def close_nib_modals(limit: int = 10) -> list[str]:
    """Close every app-modal window of Nib's own (a "Restore defaults?" box, a file picker...).

    While one is open, Qt blocks input to every other window of the app - the overlays included -
    so a guard started over it would show a PIN field nothing can be typed or clicked into. Each is
    rejected like Escape would ("No", "Cancel"). Returns the class names of what was closed.
    """
    closed: list[str] = []
    for _ in range(limit):
        widget = QApplication.activeModalWidget()
        if widget is None:
            break
        closed.append(type(widget).__name__)
        try:
            if isinstance(widget, QDialog):
                widget.reject()
            else:
                widget.close()
            if QApplication.activeModalWidget() is widget:
                widget.hide()
        except Exception:
            log.debug("closing a modal window failed", exc_info=True)
            break
    if closed:
        log.info("guard: closed %d modal window(s) first so the guard can take input: %s",
                 len(closed), ", ".join(closed))
    return closed


def orphan_core_covering() -> bool:
    """True when a previous guard core is still blacking out the screens (its Nib died mid-guard)."""
    try:
        from guard_core import _ORPHAN_MUTEX_NAME
    except Exception:
        return False
    try:
        handle = _kernel32.OpenMutexW(_SYNCHRONIZE, False, _ORPHAN_MUTEX_NAME)
    except Exception:
        return False
    if handle:
        _kernel32.CloseHandle(handle)
        return True
    return False


def core_command(parent_pid: int) -> tuple[str, list[str]]:
    """``(program, arguments)`` that start the guard core for ``parent_pid``.

    Frozen: the same exe with ``--guard-core``. From source: ``main.py --guard-core`` under the
    windowless ``pythonw.exe`` sitting next to the running interpreter when there is one, so a Nib
    started from a console does not flash a second console window for the core.
    """
    if getattr(sys, "frozen", False):
        return sys.executable, ["--guard-core", str(parent_pid)]
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        windowless = exe.with_name("pythonw.exe")
        if windowless.is_file():
            exe = windowless
    main_py = Path(__file__).resolve().parent / "main.py"
    return str(exe), [str(main_py), "--guard-core", str(parent_pid)]


def format_log(entries: list[LogEntry]) -> list[str]:
    """The intrusion log as "14:02 - 3 wrong PINs" lines, consecutive wrong attempts collapsed."""
    lines: list[str] = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        stamp = time.strftime("%H:%M", time.localtime(entry.t))
        if entry.kind == "auth_failed":
            j = i
            while j < len(entries) and entries[j].kind == "auth_failed":
                j += 1
            count = j - i
            lines.append(f"{stamp} - {count} wrong PIN{'s' if count != 1 else ''}")
            i = j
            continue
        template = _LOG_LABELS.get(entry.kind, entry.kind.replace("_", " ") + " {detail}")
        lines.append(f"{stamp} - " + template.format(detail=entry.detail or "").strip())
        i += 1
    return lines


# --- first-run PIN setup ------------------------------------------------------------------------


class PinSetupDialog(QDialog):
    """Set the guard PIN: entered twice, at least ``MIN_PIN_LEN`` characters.

    Shown on the first guard of any PC that has no PIN yet, Windows Hello or not: Hello is an extra
    button, never the only way in. A Hello that cannot be used at the moment (reader busy, camera
    covered, the prompt opening behind the overlay) must not leave the owner facing a guard with
    no field to type into.
    """

    def __init__(self, parent: QWidget | None = None, *, reason: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Set a guard PIN")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        layout = QVBoxLayout(self)
        intro = (
            "The screen guard needs a way to let you back in.\n"
            f"{reason + chr(10) if reason else ''}"
            f"Choose a PIN of at least {MIN_PIN_LEN} characters. Only a scrypt hash of it is stored."
        )
        text = QLabel(intro, self)
        text.setWordWrap(True)
        layout.addWidget(text)
        self.first = QLineEdit(self)
        self.first.setEchoMode(QLineEdit.Password)
        self.first.setPlaceholderText("PIN")
        self.second = QLineEdit(self)
        self.second.setEchoMode(QLineEdit.Password)
        self.second.setPlaceholderText("PIN again")
        layout.addWidget(self.first)
        layout.addWidget(self.second)
        self.error = QLabel("", self)
        self.error.setStyleSheet("color: #E0483A;")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self._try_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def problem(first: str, second: str) -> str:
        """Why this pair is not acceptable, or "" if it is. Static so it can be tested alone."""
        if len(first) < MIN_PIN_LEN:
            return f"Use at least {MIN_PIN_LEN} characters."
        if first != second:
            return "The two entries don't match."
        return ""

    def _try_accept(self) -> None:
        issue = self.problem(self.first.text(), self.second.text())
        if issue:
            self.error.setText(issue)
            self.second.clear()
            self.second.setFocus()
            return
        self.accept()

    def pin(self) -> str:
        return self.first.text()


# --- the service ------------------------------------------------------------------------------------


class GuardService(QObject):
    """Owns the controller, the core process, the overlays and the peek chip.

    ``started`` / ``ended`` bracket a whole guard (IDLE -> ... -> IDLE) so ``main`` can put the
    mascot away and bring him back; a Peek in between does not end it.
    """

    started = Signal()
    ended = Signal()

    def __init__(
        self,
        config: Any,
        *,
        sheet_provider: Callable[[], Any] | None = None,
        sounds: Any = None,
        ui_scale: float = 1.0,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self._sheet_provider = sheet_provider or (lambda: None)
        self._sounds = sounds
        self._ui_scale = max(1.0, float(ui_scale))

        # Wall-clock time, so log entries format with strftime and line up with the core's own "t".
        self.controller = GuardController(
            time.time,
            peek_minutes=self._cfg_float("guard_peek_minutes", 2),
            blackout_after=self._cfg_int("guard_blackout_after_attempts", 3),
            lock_fallback=bool(self.config.get("guard_lock_fallback", False)),
        )
        self.controller.add_listener(self._on_mode)

        self._overlays = None           # guard_overlay.OverlaySet while a guard is up
        self._hooked_scene: Any = None  # the scene whose sound signal we are connected to
        self._chip = None               # guard_scene.PeekChip, built on first Peek
        self._log_card: QMessageBox | None = None
        self._verifier = guard_auth.PinVerifier(self)
        self._verifier.finished.connect(self._on_pin_checked)
        self._verifier.hashed.connect(self._on_pin_hashed)
        self._setup_open = False
        #: Whether a PIN hashed by the setup dialog starts a guard (not when set up after one).
        self._start_after_setup = True
        #: A guard resumed after a crash without a stored PIN: ask for one once it ends.
        self._setup_after_guard = False
        self._hello_pending = False
        #: monotonic() when the last Hello prompt reported; see HELLO_GRACE_S.
        self._hello_done_at = float("-inf")
        #: Bumped on every guard, so a late Hello answer can never unlock a newer guard.
        self._guard_gen = 0
        self._note_text = ""
        self._note_until = 0.0
        #: The click backstop's check: waiting for the core's status reply, and whether this click
        #: may still retry taking the keyboard once.
        self._verify_waiting = False
        self._verify_retry = False

        #: Windows Hello availability: None until the background probe answers.
        self._hello: bool | None = None
        self._hello_reason = ""
        threading.Thread(target=self._probe_hello, name="nib-guard-hello", daemon=True).start()

        # The core process and its supervision.
        self._core: QProcess | None = None
        self._core_ready = False
        self._core_queue: list[dict] = []
        self._core_buf = b""
        self._core_wanted = False       # True while a guard (including a Peek) is up
        self._core_restarting = False
        self._core_deaths: list[float] = []
        self._core_failed = False       # gave up restarting - the status line says so
        self._core_last_heard = 0.0
        self._last_ping = 0.0
        self._last_hwnds: list[int] = []

        self._last_input = 0.0
        self._choice_since = 0.0
        self._auto_armed = True

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(TICK_MS)
        self._tick_timer.timeout.connect(self._tick)
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(STATUS_MS)
        self._status_timer.timeout.connect(self._refresh_status)
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(AUTO_GUARD_POLL_MS)
        self._auto_timer.timeout.connect(self._check_auto_guard)
        self._auto_timer.start()

        if orphan_core_covering():
            # Nib died mid-guard and the old core is still holding the screens black. Come straight
            # back up in BLACKOUT; the new core's "guard" tells the orphan to stand down.
            log.warning("guard: a previous guard core is still covering - resuming in blackout")
            QTimer.singleShot(0, self._resume_after_crash)

    # --- config helpers ------------------------------------------------------------------------

    def _cfg_float(self, key: str, default: float) -> float:
        try:
            return float(self.config.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    def _cfg_int(self, key: str, default: int) -> int:
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError):
            return int(default)

    # --- public API ------------------------------------------------------------------------------

    @property
    def active(self) -> bool:
        """True from the moment a guard starts until it fully ends (Peek included)."""
        return self.controller.mode is not Mode.IDLE

    @property
    def overlays(self):
        return self._overlays

    @property
    def core_running(self) -> bool:
        return self._core is not None and self._core.state() != QProcess.NotRunning

    def request_guard(self) -> None:
        """The wheel / tray / hotkey entry point. Means "cover the screen now", whatever that takes.

        From IDLE it starts a guard (after the first-run PIN setup, if one is needed). During a Peek
        it is "Lock now"; from CHOICE it re-guards; while already guarded it does nothing.
        """
        mode = self.controller.mode
        if mode is Mode.PEEK:
            self.controller.lock_now()
            return
        if mode is Mode.CHOICE:
            self.controller.guard()
            return
        if mode is not Mode.IDLE or self._setup_open:
            return
        if not self._auth_ready(interactive=True):
            return  # the setup dialog will start the guard once the PIN is hashed
        self._start()

    def shutdown(self) -> None:
        """App is quitting. Safe to call when nothing is running, and more than once."""
        self._auto_timer.stop()
        self._tick_timer.stop()
        self._status_timer.stop()
        if self._chip is not None:
            self._chip.hide()
        if self._overlays is not None:
            self._overlays.close()
            self._overlays = None
        self._stop_core(wait=True)

    # --- auth setup --------------------------------------------------------------------------------

    def _probe_hello(self) -> None:
        try:
            reason = guard_auth.hello_availability()
        except Exception:
            reason = "missing"
        self._hello_reason = reason
        self._hello = reason == "available"
        log.info("guard: Windows Hello %s", "available" if self._hello else f"unavailable ({reason})")

    def _hello_usable(self) -> bool:
        if str(self.config.get("guard_auth", "hello")) != "hello":
            return False
        if self._hello is None:
            self._probe_hello()  # first guard raced the background probe; it is quick
        return bool(self._hello)

    def _pin_hash(self) -> str:
        """The stored hash, or "" when there is none *or it is malformed* - a hand-edited value
        must not count as "a way back in" when verify_pin would reject every PIN against it."""
        stored = str(self.config.get("guard_pin_hash", "") or "")
        parts = stored.split("$")
        return stored if len(parts) == 3 and parts[0] == "scrypt" and all(parts) else ""

    def _auth_ready(self, *, interactive: bool, start_after: bool = True) -> bool:
        """True when a guard PIN exists. Otherwise (interactive) open the PIN setup, whose PIN
        starts a guard once hashed (unless ``start_after`` is False).

        Windows Hello alone is not enough: when it cannot be used mid-guard there would be no field
        to type into at all - only Ctrl+Alt+Del and a Windows sign-in.
        """
        if self._pin_hash():
            return True
        if not interactive:
            return False
        if str(self.config.get("guard_auth", "hello")) == "pin":
            reason = ""
        elif self._hello_usable():
            reason = ("Windows Hello works on this PC too; the PIN is your way in whenever Hello "
                      "can't be used.")
        else:
            reason = "Windows Hello isn't available on this PC."
        self._setup_open = True
        self._start_after_setup = start_after
        dialog = PinSetupDialog(reason=reason)
        try:
            accepted = dialog.exec() == QDialog.Accepted
            pin = dialog.pin() if accepted else ""
        finally:
            dialog.deleteLater()
        if not accepted:
            self._setup_open = False
            log.info("guard: PIN setup cancelled - not guarding")
            return False
        self._verifier.hash(pin)  # -> _on_pin_hashed, which starts the guard
        return False

    def _on_pin_hashed(self, stored: str) -> None:
        self._setup_open = False
        if not stored:
            log.warning("guard: PIN hashing failed - not guarding")
            return
        self.config["guard_pin_hash"] = stored
        self.config.save_if_dirty()
        log.info("guard: PIN set")
        if self._start_after_setup and self.controller.mode is Mode.IDLE:
            self._start()

    def _auth_state(self) -> dict:
        return {
            "hello": self._hello_usable(),
            "pin": bool(self._pin_hash()),
            "backoff_s": self.controller.backoff_remaining(),
        }

    # --- starting and ending ------------------------------------------------------------------------

    def _start(self) -> None:
        c = self.controller
        c.peek_minutes = max(0.0, self._cfg_float("guard_peek_minutes", 2))
        c.blackout_after = max(1, self._cfg_int("guard_blackout_after_attempts", 3))
        c.lock_fallback = bool(self.config.get("guard_lock_fallback", False))
        c.guard()

    def _resume_after_crash(self) -> None:
        if self.controller.mode is not Mode.IDLE:
            return
        if not self._pin_hash():
            # A guard from before every guard had a PIN (Hello only). The orphan's black screens
            # still need taking over - and signing in to Windows ends this one, as there is no
            # PIN - but the owner gets asked for a PIN as soon as it is over.
            log.warning("guard: resuming a guard that has no PIN - asking for one once it ends")
            self._setup_after_guard = True
        self._start()
        self.controller.event("core_restarted", "Nib restarted during a guard")

    def _begin(self) -> None:
        """IDLE -> FOG: put the mascot away, cover every monitor, start the core."""
        from guard_overlay import OverlaySet

        close_nib_modals()
        self._auto_armed = False
        self._guard_gen += 1
        self.started.emit()
        self._overlays = OverlaySet(self.config, sheet=self._sheet_provider(), parent=self)
        o = self._overlays
        o.pin_submitted.connect(self._on_pin_submitted)
        o.hello_requested.connect(self._on_hello_requested)
        o.choice_peek.connect(self.controller.choose_peek)
        o.choice_unlock.connect(self.controller.choose_unlock)
        o.typing_changed.connect(self._on_typing)
        o.pointer_near_field.connect(self._on_near_field)
        o.hwnds_changed.connect(self._on_hwnds)
        o.owner_click.connect(self._on_owner_click)
        o.set_auth(**self._auth_state())
        o.show("fog")
        self._hook_scene()
        self._refresh_status()
        self._core_wanted = True
        self._core_failed = False
        self._core_deaths.clear()
        if self.core_running:
            self._send({"cmd": "guard"})
        else:
            self._start_core()
            self._send({"cmd": "guard"})
        self._tick_timer.start()
        self._status_timer.start()

    def _finish(self) -> None:
        """-> IDLE: take everything down, bring the mascot back, show what happened."""
        # A prompt still open belongs to this guard: close it (while the overlays still exist), and
        # never let it answer into the next guard - that is what _guard_gen is for, too.
        guard_auth.cancel_hello()
        self._hello_pending = False
        self._tick_timer.stop()
        self._status_timer.stop()
        # Core first, for the same reason as the Peek: closing the overlays moves the foreground.
        self._core_wanted = False
        self._stop_core(wait=False)
        if self._chip is not None:
            self._chip.hide()
        if self._overlays is not None:
            self._overlays.close()
            self._overlays.deleteLater()
            self._overlays = None
        self._hooked_scene = None
        self.ended.emit()
        self._show_log_card()
        if self._setup_after_guard:
            self._setup_after_guard = False
            if not self._pin_hash():
                QTimer.singleShot(0, self._setup_pin_after_guard)

    def _setup_pin_after_guard(self) -> None:
        if self.controller.mode is Mode.IDLE and not self._setup_open:
            self._auth_ready(interactive=True, start_after=False)

    # --- the one mode listener --------------------------------------------------------------------

    def _on_mode(self, old: Mode, new: Mode) -> None:
        o = self._overlays
        if new is Mode.FOG:
            if old is Mode.IDLE:
                self._begin()
            elif old is Mode.PEEK:
                if self._chip is not None:
                    self._chip.hide()
                if o is not None:
                    close_nib_modals()  # a dialog opened during the peek would block the overlays
                    o.set_auth(**self._auth_state())
                    o.show("fog")
                    self._hook_scene()
                    self._scene_call("set_mode", "peek_return")
                self._send({"cmd": "guard"})
            elif o is not None:  # CHOICE timed out, or re-guarded from CHOICE
                o.set_mode("fog")
                o.set_auth(**self._auth_state())
        elif new is Mode.BLACKOUT:
            if o is not None:
                o.set_mode("blackout")
        elif new is Mode.CHOICE:
            self._choice_since = time.monotonic()
            if o is not None:
                o.set_mode("choice")
        elif new is Mode.PEEK:
            # Core first: hiding the overlays hands the foreground to whatever is underneath, and a
            # still-guarding core would report that as a stolen foreground.
            self._send({"cmd": "peek"})
            if o is not None:
                o.hide()
            self._show_chip()
        elif new is Mode.IDLE:
            self._finish()
        self._refresh_status()

    # --- overlay / scene signals --------------------------------------------------------------------

    def _scene(self) -> Any:
        return self._overlays.scene if self._overlays is not None else None

    def _scene_call(self, method: str, *args) -> None:
        scene = self._scene()
        fn = getattr(scene, method, None) if scene is not None else None
        if fn is None:
            return
        try:
            fn(*args)
        except Exception:
            log.debug("scene.%s%r failed", method, args, exc_info=True)

    def _hook_scene(self) -> None:
        """Connect the scene's sound requests. The scene is rebuilt per guard, so check each time."""
        scene = self._scene()
        if scene is None or scene is self._hooked_scene:
            return
        self._hooked_scene = scene
        signal = getattr(scene, "sound_requested", None)
        if signal is not None:
            signal.connect(self._play)

    def _play(self, name: str) -> None:
        if self._sounds is None or not bool(self.config.get("guard_sounds", True)):
            return
        try:
            self._sounds.play(name)
        except Exception:
            log.debug("guard sound %s failed", name, exc_info=True)

    def _on_typing(self, typing: bool) -> None:
        self._scene_call("set_typing", bool(typing))

    def _on_near_field(self, near: bool) -> None:
        self._scene_call("set_near_field", bool(near))

    def _on_owner_click(self) -> None:
        """A click on any guard window, fog or blackout: whatever happened before, end with a PIN
        field that takes the keys. See ``_verify_typeable`` for how that is checked."""
        o = self._overlays
        if o is None or self.controller.mode not in (Mode.FOG, Mode.BLACKOUT):
            return
        if self._hello_pending:
            # A click on the fog while the prompt is open means "I'll type instead".
            log.info("guard: click on the guard while Hello was open - cancelling it")
            guard_auth.cancel_hello()
        o.take_keyboard()
        self._send({"cmd": "owner_click"})
        self._verify_retry = True
        QTimer.singleShot(VERIFY_AFTER_MS, self._verify_typeable)

    def _verify_typeable(self) -> None:
        """Ask the core how the keyboard looks, then judge (``_finish_verify``)."""
        if self._overlays is None or self.controller.mode not in (Mode.FOG, Mode.BLACKOUT):
            return
        if self._hello_pending:
            return  # a prompt opened since the click: it has the keyboard, rightly
        self._verify_waiting = True
        self._send({"cmd": "status"})
        QTimer.singleShot(VERIFY_REPLY_MS, lambda: self._finish_verify(None))

    def _finish_verify(self, status: dict | None) -> None:
        """Every condition for a typed PIN to land in the field, logged as one line; on any
        failure the keyboard is taken once more. ``None`` = could not be checked (no core reply,
        or no real window)."""
        if not self._verify_waiting:
            return
        self._verify_waiting = False
        o = self._overlays
        if o is None or self.controller.mode not in (Mode.FOG, Mode.BLACKOUT) or self._hello_pending:
            return
        state = o.keyboard_state()
        state["core_keys"] = None if status is None else bool(status.get("foreground_ok", True))
        if status is not None:
            state["held"] = status.get("held")
            state["win_down"] = status.get("win_down")
        failed = [k for k in ("field", "focus", "active", "modal", "foreground", "enabled",
                              "core_keys")
                  if state.get(k) is False]
        if failed:
            log.warning("guard: PIN not ready after a click (%s): %s", ", ".join(failed), state)
            if self._verify_retry:
                self._verify_retry = False
                o.take_keyboard()
        else:
            log.debug("guard: PIN ready after a click: %s", state)

    def _on_hwnds(self, hwnds: list) -> None:
        self._last_hwnds = [int(h) for h in hwnds if h]
        self._send({"cmd": "overlay_hwnds", "hwnds": self._last_hwnds})

    # --- authentication -------------------------------------------------------------------------------

    def _on_pin_submitted(self, pin: str) -> None:
        if self.controller.mode not in (Mode.FOG, Mode.BLACKOUT) or self._verifier.busy:
            return
        if self.controller.backoff_remaining() > 0:
            return
        stored = self._pin_hash()
        if not stored:
            return
        self._verifier.verify(pin, stored)

    def _on_pin_checked(self, ok: bool) -> None:
        if self.controller.mode not in (Mode.FOG, Mode.BLACKOUT):
            return
        if ok:
            self.controller.auth_ok()
            return
        wait = self.controller.auth_failed()
        self._scene_call("notify", "wrong")
        if self._overlays is not None:
            state = self._auth_state()
            state["backoff_s"] = wait
            self._overlays.set_auth(**state)
        if self.controller.want_lock:
            self.controller.want_lock = False
            log.warning("guard: too many wrong attempts - locking Windows")
            lock_workstation()

    def _on_hello_requested(self) -> None:
        if self._hello_pending or self._overlays is None or self._overlays.primary is None:
            return
        if self.controller.mode not in (Mode.FOG, Mode.BLACKOUT):
            return
        if guard_auth.hello_busy():
            # A cancelled unparented prompt may still be open somewhere (Nib cannot close it); a
            # second one on top would only add another window to type into by mistake.
            log.info("guard: a Windows Hello prompt is still open - not opening another")
            self._show_note("Windows Hello is busy - type your PIN")
            return
        self._hello_pending = True
        gen = self._guard_gen
        overlays = self._overlays
        # Stop re-raising / activating *before* the prompt can appear. Whether topmost must go too
        # is only known once request_hello has picked its path (the HWND interop can fail at
        # request time even when the probe said it was there), so that part waits for
        # on_fallback, which runs just before the unparented prompt opens.
        overlays.set_prompt_open(True, untopmost=False)
        self._send({"cmd": "prompt", "open": True})
        try:
            guard_auth.request_hello(
                overlays.primary.hwnd(), "Nib: confirm it's you to unlock the screen",
                lambda ok, g=gen: self._on_hello_done(ok, g),
                on_fallback=self._on_hello_fallback,
            )
        except Exception:
            log.exception("guard: Windows Hello could not start")
            self._on_hello_done(False, gen)

    def _on_hello_fallback(self) -> None:
        """The unparented prompt is about to open: it would land behind topmost overlays."""
        if self._hello_pending and self._overlays is not None:
            self._overlays.set_prompt_open(True, untopmost=True)

    def _on_hello_done(self, ok: bool, gen: int | None = None) -> None:
        if gen is not None and gen != self._guard_gen:
            log.info("guard: a Windows Hello answer from an earlier guard - ignored")
            return
        if not self._hello_pending:
            return  # already answered (cancelled, or the guard that asked has ended)
        self._hello_done_at = time.monotonic()
        self._hello_pending = False
        self._send({"cmd": "prompt", "open": False})
        if self._overlays is not None:
            self._overlays.set_prompt_open(False)
            self._overlays.ensure_enabled()  # a parented prompt may have disabled its owner
        # A cancelled or failed Hello prompt is not counted as a wrong attempt: Windows Hello does
        # its own lockout, and cancelling a dialog is the most innocent thing anyone can do.
        if ok and self.controller.mode in (Mode.FOG, Mode.BLACKOUT):
            self.controller.auth_ok()

    # --- the peek chip -----------------------------------------------------------------------------------

    def _show_chip(self) -> None:
        if self._chip is None:
            from guard_scene import PeekChip

            self._chip = PeekChip(self.config, ui_scale=self._ui_scale)
            self._chip.extend_requested.connect(self.controller.extend_peek)
            self._chip.lock_requested.connect(self.controller.lock_now)
            self._chip.warning.connect(lambda: self._play("poke"))
        self._chip.set_remaining(self.controller.peek_remaining(), self.controller.peek_total)
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            margin = int(16 * self._ui_scale)
            self._chip.adjustSize()
            self._chip.move(area.right() - self._chip.width() - margin, area.top() + margin)
        self._chip.show()
        self._chip.raise_()

    # --- heartbeat ------------------------------------------------------------------------------------------

    def _tick(self) -> None:
        c = self.controller
        c.tick()  # may end a Peek -> FOG via the listener
        now = time.monotonic()
        if c.mode is Mode.PEEK and self._chip is not None:
            self._chip.set_remaining(c.peek_remaining(), c.peek_total)
        elif c.mode is Mode.CHOICE and now - self._choice_since > CHOICE_TIMEOUT_S:
            log.info("guard: no choice made in %.0f s - back to fog", CHOICE_TIMEOUT_S)
            c.guard()
        if self.core_running and self._core_ready:
            if now - self._last_ping >= PING_EVERY_S:
                self._last_ping = now
                self._send({"cmd": "ping"})
            if now - self._core_last_heard > CORE_SILENT_S:
                log.warning("guard core silent for %.0f s - killing it", now - self._core_last_heard)
                self._core.kill()

    def _show_note(self, text: str) -> None:
        """A short-lived line on the status bar, for things the owner must do now."""
        self._note_text = text
        self._note_until = time.monotonic() + NOTE_S
        self._refresh_status()

    def _refresh_status(self) -> None:
        if self._overlays is None:
            return
        c = self.controller
        parts: list[str] = []
        if self._note_text and time.monotonic() < self._note_until:
            parts.append(self._note_text)
        if c.guarded_since is not None:
            minutes = max(0, int((time.time() - c.guarded_since) // 60))
            parts.append(f"guarding {minutes} min")
        if self._core_failed:
            parts.append("keyboard guard failed - screens stay covered")
        elif self._core_ready:
            parts.append("screen kept awake")
        else:
            parts.append("starting the keyboard guard")
        battery = battery_warning()
        if battery:
            parts.append(battery)
        self._overlays.set_status(" · ".join(parts))

    # --- auto-guard -------------------------------------------------------------------------------------------

    def _check_auto_guard(self) -> None:
        minutes = self._cfg_float("guard_auto_after_idle_min", 0)
        if minutes <= 0:
            self._auto_armed = True
            return
        try:
            import win_integration

            idle = win_integration.get_idle_seconds()
        except Exception:
            return
        threshold = minutes * 60.0
        if idle < threshold:
            self._auto_armed = True  # someone was here since the last guard: re-arm
            return
        if not self._auto_armed or self.controller.mode is not Mode.IDLE or self._setup_open:
            return
        if not self._auth_ready(interactive=False):
            # Never pop the PIN setup at an empty desk - log once per idle stretch instead.
            log.info("guard: auto-guard skipped - no guard PIN set yet")
            self._auto_armed = False
            return
        log.info("guard: idle %.0f min - guarding automatically", idle / 60.0)
        self._start()

    # --- intrusion log card --------------------------------------------------------------------------------

    def _show_log_card(self) -> None:
        lines = format_log(list(self.controller.log))
        if not lines:
            return
        if self._log_card is not None:
            self._log_card.close()
        card = QMessageBox()
        card.setWindowTitle("Nib - while you were away")
        card.setIcon(QMessageBox.Information)
        card.setText("Nib noticed this while guarding your screen:")
        card.setInformativeText("\n".join(lines[-30:]))
        card.setStandardButtons(QMessageBox.Ok)
        card.setWindowFlags(card.windowFlags() | Qt.WindowStaysOnTopHint)
        card.setModal(False)
        card.finished.connect(self._on_log_dismissed)
        self._log_card = card
        card.show()
        card.raise_()

    def _on_log_dismissed(self, *_args) -> None:
        self.controller.clear_log()
        if self._log_card is not None:
            self._log_card.deleteLater()
            self._log_card = None

    # --- the core process ----------------------------------------------------------------------------------

    def _start_core(self) -> None:
        if self.core_running:
            return
        program, args = core_command(os.getpid())
        proc = QProcess(self)
        proc.setProgram(program)
        proc.setArguments(args)
        proc.setWorkingDirectory(str(Path(__file__).resolve().parent))
        proc.readyReadStandardOutput.connect(self._on_core_stdout)
        proc.readyReadStandardError.connect(self._on_core_stderr)
        proc.finished.connect(self._on_core_finished)
        proc.errorOccurred.connect(self._on_core_error)
        self._core = proc
        self._core_ready = False
        self._core_buf = b""
        self._core_last_heard = time.monotonic()
        log.info("guard: starting core: %s %s", program, " ".join(args))
        proc.start()

    def _stop_core(self, *, wait: bool) -> None:
        proc = self._core
        if proc is None:
            return
        self._core_wanted = False
        self._core_queue.clear()
        if proc.state() != QProcess.NotRunning:
            try:
                if self._core_ready:
                    proc.write(b'{"cmd":"unlock"}\n')
                proc.closeWriteChannel()  # stdin EOF: an unguarded core exits on its own
            except Exception:
                log.debug("could not tell the core to stop", exc_info=True)
            if wait and not proc.waitForFinished(CORE_EXIT_WAIT_MS):
                log.warning("guard core did not exit - killing it")
                proc.kill()
                proc.waitForFinished(500)
        self._forget_core(proc)

    def _forget_core(self, proc: QProcess) -> None:
        """Detach ``proc`` from the service. A still-running one is left to exit on its own (it was
        told to), is killed if it has not after a few seconds, and deletes itself once it is gone -
        so a guard started straight after an unlock always gets a fresh core, never a dying one."""
        if proc.property("nib_forgotten"):
            return  # already detached (waitForFinished can deliver "finished" re-entrantly)
        proc.setProperty("nib_forgotten", True)
        if self._core is proc:
            self._core = None
            self._core_ready = False
        for signal, slot in ((proc.finished, self._on_core_finished),
                             (proc.errorOccurred, self._on_core_error),
                             (proc.readyReadStandardOutput, self._on_core_stdout),
                             (proc.readyReadStandardError, self._on_core_stderr)):
            try:
                signal.disconnect(slot)
            except Exception:
                pass
        if proc.state() == QProcess.NotRunning:
            proc.deleteLater()
            return
        def _gone(*_a, p: QProcess = proc) -> None:
            log.info("guard core exited")
            try:
                p.deleteLater()
            except RuntimeError:
                pass  # Qt already destroyed it (the app quit with the core still exiting)

        proc.finished.connect(_gone)

        def _reap(p: QProcess = proc) -> None:
            try:
                if p.state() != QProcess.NotRunning:
                    log.warning("guard core still running after unlock - killing it")
                    p.kill()
            except RuntimeError:
                pass  # already deleted: it exited

        QTimer.singleShot(3000, _reap)

    def _send(self, obj: dict) -> None:
        if not self._core_wanted and obj.get("cmd") != "unlock":
            return
        if not self._core_ready or self._core is None:
            # Only the latest mode command matters; hwnds are re-sent on ready anyway.
            if obj.get("cmd") in ("guard", "peek"):
                self._core_queue = [m for m in self._core_queue if m.get("cmd") not in ("guard", "peek")]
            if obj.get("cmd") != "ping":
                self._core_queue.append(obj)
            return
        try:
            self._core.write((json.dumps(obj) + "\n").encode("utf-8"))
        except Exception:
            log.debug("write to guard core failed", exc_info=True)

    def _on_core_stdout(self) -> None:
        proc = self.sender() if isinstance(self.sender(), QProcess) else self._core
        if proc is None or proc is not self._core:
            return
        self._core_buf += bytes(proc.readAllStandardOutput())
        while b"\n" in self._core_buf:
            line, self._core_buf = self._core_buf.split(b"\n", 1)
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except ValueError:
                log.debug("guard core said non-JSON: %.80r", text)
                continue
            if isinstance(msg, dict):
                self._core_last_heard = time.monotonic()
                self._on_core_message(msg)

    def _on_core_stderr(self) -> None:
        proc = self._core
        if proc is None:
            return
        for line in bytes(proc.readAllStandardError()).decode("utf-8", "replace").splitlines():
            if line.strip():
                log.info("core: %s", line.strip())

    def _on_core_message(self, msg: dict) -> None:
        ev = msg.get("ev")
        if ev == "ready":
            self._core_ready = True
            log.info("guard core ready (pid %s)", msg.get("pid"))
            queued, self._core_queue = self._core_queue, []
            if self._last_hwnds:
                self._send({"cmd": "overlay_hwnds", "hwnds": self._last_hwnds})
            for obj in queued:
                if obj.get("cmd") != "overlay_hwnds":
                    self._send(obj)
            if self._core_restarting:
                self._core_restarting = False
                mode = self.controller.mode
                if mode is Mode.PEEK:
                    self._send({"cmd": "peek"})
                elif mode is not Mode.IDLE:
                    self._send({"cmd": "guard"})
                # core_restarted was already judged when it died (see _core_died).
            self._refresh_status()
        elif ev == "event":
            self._on_core_event(str(msg.get("kind", "")), str(msg.get("detail", "") or ""))
        elif ev == "status":
            if self._verify_waiting:
                self._finish_verify(msg)
        elif ev == "pong":
            pass

    def _windows_unlock_ends_guard(self) -> bool:
        """``windows_unlock_clears_guard``, forced on while no valid guard PIN is stored.

        With Hello as the only way in, a Hello that breaks mid-guard (camera unplugged, reader
        busy) would otherwise leave nothing that unlocks the guard but signing out - which kills
        the very work the guard exists to protect. Proving yourself to Windows is always enough
        then.
        """
        return bool(self.config.get("windows_unlock_clears_guard", True)) or not self._pin_hash()

    def _on_core_event(self, kind: str, detail: str) -> None:
        c = self.controller
        if kind == "input":
            self._last_input = time.monotonic()
            self._scene_call("notify", "input")
            return
        if kind == "session_unlock":
            if self._windows_unlock_ends_guard() and c.mode is not Mode.IDLE:
                log.info("guard: Windows was unlocked - ending the guard")
                c.session_unlocked()
            return
        if kind == "session_lock":
            log.info("guard: Windows locked")
            return
        if kind == FOREGROUND_KIND:
            now = time.monotonic()
            recent = now - self._last_input < RECENT_INPUT_S
            if self._hello_pending or now - self._hello_done_at < HELLO_GRACE_S:
                # Someone just clicked "Windows Hello": the prompt (or its closing, which hands
                # the foreground back to whatever Windows likes) moved the foreground, and the
                # click is the "recent input". No blackout for that. But anyone can click the
                # button, so a window other than the prompt itself (the shell after a touchpad
                # swipe, an app from the taskbar) still goes into the intrusion log the owner
                # sees. Keys stay held back by the core and the screen is taken back below.
                if detail.lower() in HELLO_HOST_EXES:
                    log.info("guard: the Hello prompt moved the foreground - not counted")
                else:
                    log.info("guard: %s took the foreground during/after Hello - logged, "
                             "no blackout", detail)
                    c.event(kind, f"{detail} (during Windows Hello)", recent_input=False)
            else:
                c.event(kind, detail, recent_input=recent)
            if self._hello_pending:
                # A foreign window came up while a Hello prompt had the overlays standing down
                # (no re-raise, maybe not even topmost). Give up on the prompt - done(False) clears
                # the prompt state - so the screen is taken back now, not when it times out.
                guard_auth.cancel_hello()
            if self._overlays is not None and self._overlays.visible:
                self._overlays.reassert(force=True)  # take the screen back either way
            return
        c.event(kind, detail)

    def _on_core_error(self, error) -> None:
        log.warning("guard core process error: %s", error)
        if error == QProcess.FailedToStart:
            self._core_died(-1)

    def _on_core_finished(self, code: int, _status=None) -> None:
        self._core_died(code)

    def _core_died(self, code: int) -> None:
        proc = self._core
        if proc is not None:
            self._forget_core(proc)
        if not self._core_wanted:
            log.info("guard core exited (%s)", code)
            return
        now = time.monotonic()
        self._core_deaths = [t for t in self._core_deaths if now - t < CORE_RESTART_WINDOW_S]
        self._core_deaths.append(now)
        # Whatever happens next, a guard that just lost its keyboard hook is suspicious.
        self.controller.event("core_restarted", f"guard core exited ({code})")
        if len(self._core_deaths) > CORE_RESTART_CAP:
            log.error("guard core keeps dying - not restarting it again this guard")
            self._core_failed = True
            self._refresh_status()
            return
        log.warning("guard core exited (%s) mid-guard - restarting", code)
        self._core_restarting = True
        QTimer.singleShot(CORE_RESTART_DELAY_MS, self._restart_core)

    def _restart_core(self) -> None:
        if self._core_wanted and not self.core_running:
            self._start_core()
