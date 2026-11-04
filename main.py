"""Nib - a desktop mascot that launches Claude with a prefilled prompt.

Run with ``pythonw main.py`` for no console window.

Coordinate spaces: Qt's own high-DPI scaling is switched off before the application is created, so
Qt widget coordinates are *physical* pixels and line up 1:1 with what ``win_integration`` reports.
That buys exact alignment to the taskbar edge and leaves the pixel-art scale factor under our
control as an integer chosen from the monitor's DPI - a fractional scale would blur the art. Ordinary
chrome (the ask bubble, the toast) is scaled manually via ``ui_scale`` to compensate.
"""

from __future__ import annotations

import ctypes
import logging
import logging.handlers
import os
import sys
import threading
import time
from pathlib import Path

# The Away Guard's core is this same program started as ``main.py --guard-core <pid>`` (frozen:
# ``Nib.exe --guard-core <pid>``). It is dispatched here, before Qt, the DPI call and the
# single-instance mutex below: the core is ctypes-only by design (see guard_core.py), and as a
# second Nib process it would otherwise find the mutex taken and quietly exit. It keeps its own
# logging (stderr, which Nib reads) rather than sharing nib.log's rotating handler.
if __name__ == "__main__" and len(sys.argv) >= 3 and sys.argv[1] == "--guard-core":
    import guard_core

    try:
        _parent_pid = int(sys.argv[2])
    except ValueError:
        _parent_pid = 0
    sys.exit(guard_core.run(_parent_pid))

# ``main.py --after-update <pid>`` is the Nib that "Update and restart" started to replace process
# <pid>. Unlike the core it goes on to start normally - but only once the old one has exited, as
# that one holds the single-instance mutex until it does. Waited for here, before Qt loads, and the
# two arguments dropped so nothing after this sees them; main() covers the not-__main__ case.
if __name__ == "__main__" and len(sys.argv) >= 2 and sys.argv[1] == "--after-update":
    import updater

    updater.consume_after_update(sys.argv)

# Must be set before any Qt module is imported - see the module docstring.
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

import win_integration as win  # noqa: E402  (DPI awareness must precede Qt)

win.enable_dpi_awareness()

from PySide6.QtCore import QObject, QPoint, QRect, Qt, QTimer, Signal  # noqa: E402
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

import ocr  # noqa: E402
from animator import SpriteSheet, available_skins  # noqa: E402
from behavior import PERSONALITIES, Behavior, State  # noqa: E402
from billboard import Billboard  # noqa: E402
from bubble import AskBubble, Toast  # noqa: E402
from focus_timer import FocusTimer  # noqa: E402
from config import Config, config_path  # noqa: E402
from guard_service import GuardService  # noqa: E402
from launcher import (  # noqa: E402
    available_providers,
    get_provider,
    launch,
    max_chars_for,
    modes_for,
    open_download_page,
)
from mascot_window import MascotWindow  # noqa: E402
from memory import MEMORY_FILE, Memory  # noqa: E402
from mood import Mood, label_for  # noqa: E402
import devices  # noqa: E402
from ruler import RulerOverlay  # noqa: E402
from tool_wheel import ToolWheel, WheelItem  # noqa: E402
from settings import SettingsWindow  # noqa: E402
from sound import Sounds  # noqa: E402
from tray import Tray  # noqa: E402
import updater  # noqa: E402
from world import World, cpu_count  # noqa: E402

log = logging.getLogger("nib")

TICK_MS = 16              # floor: the clock never runs faster than this
IDLE_TICK_MS = 250        # ceiling: when nothing is going to change, sleep this long
                          # Between the two, behavior.next_tick_ms picks the interval from what is
                          # actually about to happen - see _update_tick_rate.
GEOMETRY_POLL_MS = 700    # taskbar, monitor, and fullscreen checks
TOPMOST_POLL_MS = 3000    # other apps steal the top-most slot; take it back
CONFIG_FLUSH_MS = 10000   # write last_x et al. at most this often
#: The most one presence poll may add to the minute counters. Polls are 700ms apart; anything
#: much longer is the machine waking from sleep, and the hours it slept were not hours you were
#: at it - nor hours it was busy.
MEMORY_MAX_POLL_S = 5.0

#: How long he has to stand on a window, undisturbed, before it actually gets pinned on top - the
#: landing itself is immediate, but the real always-on-top exception only kicks in once it is
#: clear this was a deliberate perch and not just a drop that happened to land there.
PERCH_PIN_DELAY_MS = 3000

HOTKEY_ID = 0xC1A0
COME_HOTKEY_ID = 0xC1A1
POCKET_HOTKEY_ID = 0xC1A2
MIC_HOTKEY_ID = 0xC1A3
GUARD_HOTKEY_ID = 0xC1A4

#: How long before he will mention a muted microphone a second time. Being unheard is worth one
#: nudge; a mascot that says it every few seconds through a meeting you are deliberately muted in
#: is one you turn off, so the tell fires once and then holds its tongue for a good while.
MUTED_NAG_COOLDOWN_S = 60.0

#: How far he may move from the middle of an open tool wheel before it puts itself away. He is held
#: still while it is open, but a tantrum, a fall or anything else that moves him regardless would
#: otherwise leave the wheel ringed round an empty patch of taskbar.
WHEEL_DRIFT_PX = 8

#: The quick-change's safety net: the costume goes on when this fires even if the hop never reached
#: its apex (paused, a stalled tick). The apex is ~480ms in at the slowest pace; this is ~3x that.
QUICK_CHANGE_TIMEOUT_MS = 1500
#: Wardrobe keys are "wear:<costume>"; a bare "wear:" takes it off. A prefix rather than a key per
#: costume, so _on_tool_chosen routes all fifty-three in one line.
WARDROBE_PREFIX = "wear:"
#: How many of the costumes you wore last open the wardrobe, ahead of the rest by category.
WARDROBE_RECENTS = 5

#: The one-off tour, shown on a first run. Each is (message, sub-line, how long to hold it).
#: The first four are the things that are genuinely undiscoverable - the hotkey opens the bubble at
#: your pointer without going near him, and nothing on screen suggests you can drop a file on him.
#: The last is that he has a name you can change, and a tab saying how he is.
INTRO_STEPS: tuple[tuple[str, str, int], ...] = (
    ("Hello. Click me to ask Claude something.", "I'll put a bubble above your taskbar.", 6000),
    ("Double-click sends your last prompt again.", "No typing at all.", 5500),
    ("Drop a file on me to ask about it.", "Or drop some text to fill the box.", 5500),
    ("Press %s from anywhere.", "That one's easy to miss - it's the fastest way in.", 7000),
    ("You can rename me in Settings → Personality.", "And see how I'm doing there too.", 5000),
)
INTRO_GAP_MS = 900

#: How long he looks down at something dropped on him before getting on with it. The deep link
#: has already fired by then - this delays his animation, not your launch.
DROP_GLANCE_MS = 550

#: An update's restart held up by a guard or a focus session looks again this often.
UPDATE_RESTART_RETRY_MS = 5000


def _assets_dir() -> Path:
    """Assets live next to the source, or inside the bundle when frozen by PyInstaller."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        return Path(base) / "assets" if base else Path(sys.executable).parent / "assets"
    return Path(__file__).parent / "assets"


ASSETS = _assets_dir()


def setup_logging(level: str) -> None:
    """Log to a rotating file - under pythonw there is no console to print to."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    try:
        log_path = config_path().parent / "nib.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=512_000, backupCount=2, encoding="utf-8"
        )
    except OSError:
        handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    root.addHandler(handler)


class _OcrBridge(QObject):
    """Marshals ``ocr.recognize``'s result back onto the Qt UI thread.

    ``ocr.py`` stays Qt-free by design (see its module docstring) and calls its callback from a
    background thread - a plain Python callback there cannot safely touch widgets. Emitting a
    Qt Signal from any thread and letting Qt deliver it to whichever thread the receiving
    QObject lives on is the supported way to cross back over; this class exists only to be that
    QObject. ``text``/``error`` are plain strings, not ``str | None``, because Qt signals
    typed ``str`` cannot carry ``None`` - exactly one is empty, matching ``ocr.recognize``'s own
    contract of "exactly one of text/error is set".
    """

    finished = Signal(str, str)


class _UpdateBridge(QObject):
    """The same job as ``_OcrBridge``, for ``updater``: it runs on a plain thread and calls back
    there, and these carry what it says back onto the UI thread. ``finished`` carries an
    ``updater.Outcome``."""

    progress = Signal(str)
    finished = Signal(object)
    #: A function the update thread wants run here, on the UI thread (``updater.ask_elsewhere``).
    run_here = Signal(object)


def _wardrobe_ring(sheet: SpriteSheet, wearing: str, recents: list[str]) -> list[WheelItem]:
    """Every costume as a thumbnail: the ones you wore last first, then "Take it off" while he is
    wearing something, then the rest by category - the category is the group, so the idle label
    reads "Hats · scroll for more" as you scroll through. The one he is wearing has the ring."""
    owned = sheet.available_costumes()
    recent = [c for c in dict.fromkeys(recents) if c in owned][:WARDROBE_RECENTS]

    def item(name: str, group: str) -> WheelItem:
        # The tray's own wording for a costume, so both menus call it the same thing.
        return WheelItem(WARDROBE_PREFIX + name, name.replace("_", " ").title(), "costume",
                         "active" if name == wearing else "", group=group,
                         pixmap=sheet.costume_thumbnail(name))

    ring = [item(n, "Recent") for n in recent]
    if wearing:
        ring.append(WheelItem(WARDROBE_PREFIX, "Take it off", "costume", group="Wardrobe"))
    for category, names in sheet.costume_categories().items():
        ring += [item(n, category) for n in names if n not in recent]
    return ring


class NibApp:
    """Owns the mascot, the widgets, and the timers that drive them."""

    def __init__(self, app: QApplication, config: Config) -> None:
        self.app = app
        self.config = config

        monitor = win.get_primary_monitor()
        dpi = win.get_dpi_for_monitor(monitor) if monitor else win.DEFAULT_DPI
        configured = int(config["sprite_scale"])
        scale = configured if configured > 0 else win.suggest_sprite_scale(dpi)
        self.ui_scale = max(1.0, dpi / win.DEFAULT_DPI)
        log.info("dpi=%s sprite_scale=%s ui_scale=%.2f", dpi, scale, self.ui_scale)

        self.sheet = self._load_sheet(scale)
        # How the day has gone. Built here rather than inside Behavior so the app owns when it is
        # written back - it goes out on the same flush timer as everything else.
        self.mood = Mood(config)
        # What he remembers about you two, in its own file next to the config. Next to *this*
        # config rather than at config_path(): the same place in the app, and a scratch directory
        # under the tests, which boot a real NibApp and must never write the user's real memory.
        self.memory = Memory(config.path.parent / MEMORY_FILE, config)
        self.behavior = Behavior(config, self.sheet, self.mood, memory=self.memory)
        self.behavior.paused = bool(config["paused"])
        # Silent unless the WAVs have been generated AND sound is switched on. Both halves fail
        # softly: no directory, no backend, no file - all of them end in nothing happening.
        self.sounds = Sounds(ASSETS / "sounds", config)

        # What he can tell about the world without asking anyone: the clock, the calendar, and how
        # hard the machine is working. Sampled on the slow geometry timer, never on the tick.
        self.world = World(
            busy_percent=float(config["cpu_busy_percent"]),
            southern=bool(config["southern_seasons"]),
        )
        self.behavior.set_world(self.world.state)
        #: Headwear a persona has put on him, separate from the costume he actually owns.
        self._persona_costume = ""
        #: A costume already written to the config but not yet visible - he is hopping into it.
        #: See _on_costume_chosen; the timer is the safety net if the hop never reaches its apex.
        self._costume_pending = False
        self._costume_timer = QTimer(self.app)
        self._costume_timer.setSingleShot(True)
        self._costume_timer.setInterval(QUICK_CHANGE_TIMEOUT_MS)
        self._costume_timer.timeout.connect(self._apply_pending_costume)
        log.info(
            "world: %d logical cores, busy threshold %.0f%% (aggregate - one pegged core is ~%.0f%%)",
            cpu_count(), float(config["cpu_busy_percent"]), 100.0 / max(1, cpu_count()),
        )

        self.window = MascotWindow(self.sheet.sprite_w, self.sheet.sprite_h)
        self.bubble = AskBubble(self.ui_scale)
        self.toast = Toast(self.ui_scale)
        self.billboard = Billboard(self.ui_scale)
        self.focus_timer = FocusTimer(self.ui_scale)
        self._make_tool_wheel()

        self.tray = Tray(
            self._make_icon(),
            costumes=self.sheet.costume_categories(),
            skins=available_skins(),
            personalities=list(PERSONALITIES),
        )
        self._refresh_tray()
        self.tray.show()

        self._monitor = None
        self._hidden_by_fullscreen = False
        self._last_saved_x: int | None = None
        self._last_frame_key: tuple | None = None
        self._last_tick = time.monotonic()
        self._snoozed = False
        #: Wall-clock moment of your last input before an away, so the greeting can be sized by how
        #: long you were gone. time.time() because the behaviour clock caps each tick at 250ms, and
        #: so loses a night the machine slept through.
        self._away_since: float | None = None
        #: The day memory last recorded a first sighting for, so the poll does not ask every 700ms.
        self._memory_day = ""
        #: Seconds of presence and of a busy machine not yet a whole minute. One record() per
        #: full minute, never per poll - see MEMORY_MAX_POLL_S for why the step is capped.
        self._present_acc = 0.0
        self._busy_acc = 0.0
        self._memory_polled = time.monotonic()
        #: Whether you were present at the previous poll - None before the first one. A sighting
        #: that does not follow an absence (or a start) is the day rolling over under you, not an
        #: arrival, and stays out of the hour tally; see Memory.first_sight.
        self._memory_was_present: bool | None = None
        #: A day first seen by rollover, whose real arrival is still to be tallied the first time
        #: you come back to the machine that day. "" when there is none.
        self._arrival_pending = ""
        self._last_foreground = ""
        self._hotkey_hwnd = 0
        self._come_hotkey_hwnd = 0
        self._pocket_hotkey_hwnd = 0
        self._mic_hotkey_hwnd = 0
        self._guard_hotkey_hwnd = 0
        #: Last mic/camera state shown, so the tray icon is only repainted when it actually
        #: changes rather than every 700ms - a QIcon rebuild is four scaled pixmaps.
        self._device_state: tuple[bool | None, bool, bool] | None = None
        #: How long you have been talking into a muted mic, and how long until he may say so
        #: again. Both here rather than in Behavior: they are about your hardware, not his mood.
        self._muted_since: float | None = None
        self._muted_nag_until = 0.0
        #: The window he is perched on, or 0. Owned here rather than in behavior.py because
        #: finding and re-checking it needs Win32 - behavior.py only ever gets the rectangle.
        self._perched_hwnd = 0
        #: Whether that window is actually pinned on top yet - separate from _perched_hwnd because
        #: the landing is immediate but the pin waits out PERCH_PIN_DELAY_MS, below.
        self._perch_pinned = False
        self._perch_dwell_ms = 0.0
        self._settings: SettingsWindow | None = None
        self._intro_step = -1
        #: The pixel ruler overlay, or None when it isn't up. Checked by _reassert_topmost so
        #: his own window doesn't jump in front of it every TOPMOST_POLL_MS.
        self._ruler: RulerOverlay | None = None
        self._ocr_bridge = _OcrBridge()
        self._ocr_bridge.finished.connect(self._on_ocr_finished)
        #: Update and restart. Set from the click until the outcome is back, so a second pick
        #: while one is running is refused rather than racing it over the same files.
        self._updating = False
        #: A checkout updated on disk whose restart is waiting for a guard or focus session to
        #: end. This process is still the old code, so it starts no guard of its own meanwhile.
        self._restart_pending = False
        #: Set (on the UI thread, at the update thread's asking) the moment a merge is allowed:
        #: from then on the files on disk may be newer than this process, whatever the outcome
        #: says later.
        self._files_changing = False
        #: A downloaded release waiting for its restart (an ``updater.Outcome`` of kind HANDOFF).
        #: Thrown away if Nib quits some other way first, so it doesn't sit in %TEMP% for good.
        self._staged = None
        self._update_bridge = _UpdateBridge()
        self._update_bridge.progress.connect(self._on_update_progress)
        self._update_bridge.finished.connect(self._on_update_finished)
        self._update_bridge.run_here.connect(self._run_from_update)
        # Created in _start_timers, which runs after the first geometry pass - and that pass can
        # already want to throttle the clock if a fullscreen app is up at launch.
        self._tick_timer: QTimer | None = None
        #: The Away Guard. Inert until a guard is actually started - no process, no windows. The
        #: sheet is handed over as a getter because a costume or DPI change replaces self.sheet.
        self._guarding = False
        self.guard = GuardService(
            config, sheet_provider=lambda: self.sheet, sounds=self.sounds, ui_scale=self.ui_scale,
        )
        self.guard.start_blocker = self._guard_start_blocker

        self._connect()
        self._refresh_geometry(initial=True)
        self._apply_frame(force=True)
        self.window.show()
        self.window.reassert_topmost()
        self._apply_ghost_mode()
        self._start_timers()
        if not bool(config["intro_seen"]):
            # After the timers, so he is already walking about while the first tip appears - the
            # tour is meant to introduce a mascot that is visibly alive, not a still image.
            QTimer.singleShot(2500, self.show_intro)

    # --- setup --------------------------------------------------------------------

    def _load_sheet(self, scale: int, costume: str = "") -> SpriteSheet:
        """Build the sheet. ``costume`` overrides the configured one - that is persona headwear."""
        chosen = costume or str(self.config["costume"])
        return SpriteSheet(
            ASSETS / "nib_sheet.png",
            ASSETS / "frames.json",
            scale,
            costume=chosen or None,
            skin=str(self.config["skin"]),
        )

    def _refresh_tray(self) -> None:
        self.tray.refresh(
            wander=bool(self.config["wander_enabled"]),
            paused=bool(self.config["paused"]),
            startup=self._reconcile_startup_entry(),
            costume=self.sheet.costume or "",
            skin=self.sheet.skin,
            ghost=bool(self.config["ghost_mode"]),
            personality=str(self.config["personality"]),
            focus=self.behavior.focus_active,
        )

    def _rebuild_appearance(self) -> None:
        """Rebuild the sprites after a costume or colour change, preserving the animation."""
        try:
            sheet = self._load_sheet(self.sheet.scale)
        except Exception:
            log.exception("could not rebuild sprites; keeping the current look")
            return
        # Any rebuild shows the config's costume, so a quick-change still in the air is settled.
        self._costume_pending = False
        self._costume_timer.stop()
        # The user just chose. Both trackers have to be cleared: zeroing only ours left behaviour
        # still asking for the persona's hat, and the next tick put it straight back on over the
        # one they picked.
        self._persona_costume = ""
        self.behavior.clear_persona_costume()
        self.sheet = sheet
        self.behavior.set_sheet(sheet)
        self._last_frame_key = None
        self.window.resize(sheet.sprite_w, sheet.sprite_h)
        self._apply_frame(force=True)
        self._refresh_tray()

    def _reconcile_startup_entry(self) -> bool:
        """Make ``launch_on_startup`` in the config actually mean something.

        The registry is the real mechanism, so it wins when it says the entry exists. But if the
        config asks for the entry and it is missing - the user set it by hand, or something removed
        it - restore it, so editing config.json works as the settings table implies.
        """
        wanted = bool(self.config["launch_on_startup"])
        registered = win.get_launch_on_startup()
        if wanted and not registered:
            log.info("config asks for a login entry but none exists - restoring it")
            if win.set_launch_on_startup(True):
                registered = win.get_launch_on_startup()
        self.config["launch_on_startup"] = registered
        return registered

    def _make_icon(self, *, pinned: bool = False, device: str = "") -> QIcon:
        icon = QIcon()
        base = self.sheet.pixmap("idle", 0)
        for size in (16, 24, 32, 48):
            pixmap = base.scaled(size, size, Qt.KeepAspectRatio, Qt.FastTransformation)
            if pinned:
                pixmap = self._pin_badge(pixmap)
            if device:
                pixmap = self._device_badge(pixmap, device)
            icon.addPixmap(pixmap)
        return icon

    @staticmethod
    def _device_badge(pixmap: QPixmap, kind: str) -> QPixmap:
        """A dot in the icon's top corner while something is listening or watching.

        Opposite corner from the pin badge so the two can be true at once and still both read -
        being on a call and having pinned the call window on top is not a rare combination.

        Red means you are being heard or seen. Amber means the microphone is live but muted, which
        is a genuinely different fact and the one worth its own colour: the difference between
        "careful, this is going out" and "nobody can hear you".
        """
        out = QPixmap(pixmap)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.Antialiasing)
        d = max(6, round(out.width() * 0.44))
        rect = QRect(out.width() - d, 0, d, d)
        painter.setBrush(QColor("#F5BF4F" if kind == "muted" else "#E0483A"))
        painter.setPen(QColor("#ffffff"))
        painter.drawEllipse(rect)
        painter.end()
        return out

    @staticmethod
    def _pin_badge(pixmap: QPixmap) -> QPixmap:
        """A small marker over the tray icon's own corner, so a pinned window still shows even
        while he is hidden behind the very (possibly fullscreen) thing he pinned on top of
        everything else - the tray icon is the one part of him that stays reachable regardless."""
        out = QPixmap(pixmap)
        painter = QPainter(out)
        painter.setRenderHint(QPainter.Antialiasing)
        d = max(6, round(out.width() * 0.44))
        rect = QRect(out.width() - d, out.height() - d, d, d)
        painter.setBrush(QColor("#E0483A"))
        painter.setPen(QColor("#ffffff"))
        painter.drawEllipse(rect)
        painter.end()
        return out

    def _refresh_pin_badge(self) -> None:
        self.tray.set_icon(self._make_icon(pinned=self._perch_pinned, device=self._device_badge_kind()))

    def _device_badge_kind(self) -> str:
        """Which dot the tray icon should carry, from the last state _tick_devices read."""
        if self._device_state is None:
            return ""
        muted, mic_live, camera_live = self._device_state
        if camera_live or (mic_live and not muted):
            return "hot"
        if mic_live and muted:
            return "muted"
        return ""

    def _connect(self) -> None:
        self.window.clicked.connect(self._on_click)
        self.window.drag_started.connect(self.behavior.drag_start)
        self.window.dragged.connect(self._on_dragged)
        self.window.drag_finished.connect(self._on_drag_finished)
        self.window.files_dropped.connect(self._on_files_dropped)
        self.window.text_dropped.connect(self._on_text_dropped)
        self.window.image_ocr_requested.connect(self._on_image_ocr_requested)
        self.window.double_clicked.connect(self._on_double_clicked)
        self.window.context_menu_requested.connect(self._on_context_menu)
        self.window.scrolled.connect(self.behavior.spin)

        self.tray.costume_chosen.connect(self._on_costume_chosen)
        self.tray.skin_chosen.connect(self._on_skin_chosen)

        self.bubble.submitted.connect(self._on_submitted)
        self.bubble.calculated.connect(self._on_calculated)
        self.bubble.dismissed.connect(self._on_bubble_dismissed)

        self.tray.wander_toggled.connect(self._on_wander_toggled)
        self.tray.pause_toggled.connect(self._on_pause_toggled)
        self.tray.startup_toggled.connect(self._on_startup_toggled)
        self.tray.reset_position_requested.connect(self._on_reset_position)
        self.tray.ghost_toggled.connect(self._on_ghost_toggled)
        self.tray.focus_toggled.connect(self._on_focus_toggled)
        self.tray.come_here_requested.connect(self._on_come_here)
        self.tray.settings_requested.connect(self._on_settings)
        self.tray.tips_requested.connect(self.show_intro)
        self.tray.personality_chosen.connect(self._on_personality_chosen)
        self.tray.surprise_requested.connect(self._on_surprise_costume)
        self.tray.do_something_requested.connect(self._on_do_something)
        self.tray.measure_requested.connect(self._on_measure_requested)
        self.tray.mic_toggle_requested.connect(self._on_mic_toggle)
        self.tray.guard_requested.connect(self._on_guard_requested)
        self.tray.quit_requested.connect(self._on_quit)
        self.guard.started.connect(lambda: self._set_guarding(True))
        self.guard.ended.connect(lambda: self._set_guarding(False))
        self.app.aboutToQuit.connect(self._flush_config)
        # Belt and braces for a quit that does not go through _on_quit: a core left guarding after
        # Nib is gone would black out every screen (that is its job when Nib dies mid-guard).
        self.app.aboutToQuit.connect(self.guard.shutdown)

    def _start_timers(self) -> None:
        self._tick_timer = QTimer(self.app)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(TICK_MS)

        self._geometry_timer = QTimer(self.app)
        self._geometry_timer.timeout.connect(self._refresh_geometry)
        self._geometry_timer.start(GEOMETRY_POLL_MS)

        self._topmost_timer = QTimer(self.app)
        self._topmost_timer.timeout.connect(self._reassert_topmost)
        self._topmost_timer.start(TOPMOST_POLL_MS)

        self._flush_timer = QTimer(self.app)
        self._flush_timer.timeout.connect(self._flush_config)
        self._flush_timer.start(CONFIG_FLUSH_MS)

        # Apply the right rate now, in case we launched paused or behind a fullscreen app.
        self._update_tick_rate()
        self._install_hotkey()

    def _install_hotkey(self) -> None:
        """Register both shortcuts against our own window. Either may be disabled or unavailable."""
        hwnd = int(self.window.winId())
        connected = False
        # Bound to the mascot's own window, so Qt delivers it to that widget's nativeEvent
        # instead of every message in the app passing through a Python filter.
        for key_id, setting, attribute in (
            (HOTKEY_ID, "hotkey", "_hotkey_hwnd"),
            (COME_HOTKEY_ID, "come_hotkey", "_come_hotkey_hwnd"),
            (POCKET_HOTKEY_ID, "pocket_hotkey", "_pocket_hotkey_hwnd"),
            (MIC_HOTKEY_ID, "mic_toggle_hotkey", "_mic_hotkey_hwnd"),
            (GUARD_HOTKEY_ID, "guard_hotkey", "_guard_hotkey_hwnd"),
        ):
            spec = str(self.config[setting]).strip()
            if not spec:
                continue
            if win.register_hotkey(key_id, spec, hwnd):
                setattr(self, attribute, hwnd)
                connected = True
            else:
                self.tray.notify(
                    "Nib", f"Couldn't register the {spec} shortcut (already in use?)."
                )
        if connected:
            self.window.hotkey_pressed.connect(self._on_hotkey_pressed)

    def _on_hotkey_pressed(self, hotkey_id: int) -> None:
        if hotkey_id == GUARD_HOTKEY_ID:
            self._on_guard_requested()
            return
        if self._guarding:
            # Nothing of his may open over the guard's fog - the bubble in particular is topmost
            # and would sit above it, with whatever you typed last in its history.
            return
        if hotkey_id == HOTKEY_ID:
            self._on_hotkey()
        elif hotkey_id == COME_HOTKEY_ID:
            self._on_come_here()
        elif hotkey_id == POCKET_HOTKEY_ID:
            self._on_pocket_hotkey()
        elif hotkey_id == MIC_HOTKEY_ID:
            self._on_mic_toggle()

    # --- geometry -----------------------------------------------------------------

    def _refresh_geometry(self, *, initial: bool = False) -> None:
        """Re-derive the walk strip and decide whether a fullscreen app should hide us."""
        taskbar = win.get_taskbar_info()
        monitors = win.list_monitors()
        if not monitors:
            return

        if initial:
            saved_monitor = self.config["last_monitor"]
            monitor = None
            if saved_monitor is not None:
                monitor = next(
                    (m for m in monitors if m.contains_x(int(saved_monitor))), None
                )
            monitor = monitor or next((m for m in monitors if m.primary), monitors[0])
        else:
            centre = self.behavior.x + self.behavior.sprite_w // 2
            monitor = next(
                (m for m in monitors if m.contains_x(centre)),
                self._monitor or monitors[0],
            )

        if monitor != self._monitor:
            self._rescale_for(monitor)

        baseline, x_min, x_max = win.get_walk_strip(monitor, taskbar)
        if bool(self.config["roam_all_monitors"]) and len(monitors) > 1:
            # Let him cross between screens: the horizontal bounds span every monitor, while the
            # baseline stays the one under his feet right now, so he steps up or down onto each
            # screen's own taskbar as he crosses.
            for other in monitors:
                _b, o_min, o_max = win.get_walk_strip(other, taskbar)
                x_min, x_max = min(x_min, o_min), max(x_max, o_max)
        strip = (baseline, x_min, x_max)
        changed = monitor != self._monitor or strip != (
            self.behavior.baseline_y,
            self.behavior.x_min,
            self.behavior.x_max + self.behavior.sprite_w,
        )
        self._monitor = monitor
        self.behavior.set_strip(*strip)

        if initial:
            self.behavior.place_at(self.config["last_x"])
        elif changed:
            log.debug("strip updated: baseline=%s x=%s..%s", *strip)

        if self.config["hide_on_fullscreen"]:
            self._update_fullscreen(monitor)
        self._update_presence()

    def _update_presence(self) -> None:
        """Notice when you step away, come back, or switch app - and what the world is doing."""
        state = self.world.read()
        self.behavior.set_world(state)

        # Only when he is actually in the mood to look. Enumerating top-level windows is the
        # priciest probe here, and behind its 15-minute cooldown this runs a few times an hour.
        if self.behavior.wants_ledge:
            self.behavior.set_ledge(
                win.find_ledge(
                    self.behavior.baseline_y,
                    self.behavior.x_min,
                    self.behavior.x_max + self.behavior.sprite_w,
                    self.behavior.sprite_h,
                    self.behavior.ledge_reach_px,
                )
            )

        self._tick_devices()

        away_after = float(self.config["away_after_s"])
        if away_after > 0 and not self._snoozed:
            idle = win.get_idle_seconds()
            away = idle >= away_after
            gone_s = None
            if away and self._away_since is None:
                self._away_since = time.time() - idle
            elif not away and self._away_since is not None:
                gone_s = max(0.0, time.time() - self._away_since)
                self._away_since = None
            self.behavior.set_away(away, gone_s=gone_s)

        self._update_memory(state)

        if bool(self.config["react_to_apps"]):
            process = win.get_foreground_process()
            if process and process != self._last_foreground:
                self._last_foreground = process
                self.behavior.foreground_changed(process)
                # Right here rather than waiting for the next tick: a persona is decided on this
                # 700ms timer, and at the 250ms idle tick rate he would otherwise play the first
                # frame or two of it bare-headed before the glasses appeared.
                self._sync_persona_costume()

        # His name, not the app's: this is the line that is about *him*. Read on every poll rather
        # than cached, so a rename in Settings shows here on the next 700ms tick with no restart.
        name = str(self.config["name"])
        if self.behavior.focus_active:
            remaining = int(self.behavior.focus_remaining_s)
            label = "paused (away)" if self.behavior.away else "focusing"
            self.tray.set_tooltip(f"{name} - {label}, {remaining // 60}:{remaining % 60:02d} left")
        elif self._device_state is not None and self._device_badge_kind():
            muted, mic_live, camera_live = self._device_state
            if mic_live and muted:
                said = "microphone is live but muted - nobody can hear you"
            elif mic_live and camera_live:
                said = "microphone and camera are live"
            elif camera_live:
                said = "camera is live"
            else:
                said = "microphone is live"
            self.tray.set_tooltip(f"{name} - {said}")
        elif self._perch_pinned:
            # The one case worth a tooltip of its own even without a countdown: a fullscreen
            # window can cover him entirely once it is the thing pinned on top, so the tray - icon
            # badge and all - is the only place left this is visible from.
            self.tray.set_tooltip(f"{name} - pinned a window on top; grab him to let it go")
        else:
            # Nothing more pressing to say, so say how he is - the one place the mood is
            # readable at a glance without opening anything. The same words as Settings → About
            # him, and never a number. Tray.DEFAULT_TOOLTIP stays as what the icon shows before
            # the first poll gets here.
            self.tray.set_tooltip(f"{name} — {label_for(self.mood.spirits)}")

    def _update_memory(self, state) -> None:
        """First sighting of the day, and the minutes you were here and the machine was busy.

        Read off the behaviour's ``away`` rather than the idle check above, which is skipped
        entirely while snoozed or with ``away_after_s`` at 0 - and in both of those he treats you
        as present, so memory should too.
        """
        now = time.monotonic()
        step = min(max(0.0, now - self._memory_polled), MEMORY_MAX_POLL_S)
        self._memory_polled = now
        present = not self.behavior.away
        # An arrival is the first poll of the session or the first one back from away - not a
        # date that changed while you sat there, nor a Forget or a switch-on mid-session.
        arrived = present and self._memory_was_present is not True
        self._memory_was_present = present
        if not self.memory.enabled:
            # Nothing banked while off, so switching it back on does not pay out minutes from
            # before; and the day is left unmarked, so it is counted the moment it is back on.
            self._present_acc = self._busy_acc = 0.0
            self._memory_day = ""
            self._arrival_pending = ""
            return
        if present and state.date_key != self._memory_day:
            new_day = self.memory.first_sight(state.date_key, state.hour, tally=arrived)
            self._memory_day = state.date_key
            self._arrival_pending = state.date_key if new_day and not arrived else ""
        elif arrived and self._arrival_pending == state.date_key:
            self.memory.tally_arrival(state.hour)
            self._arrival_pending = ""
        if present:
            self._present_acc += step
            if self._present_acc >= 60.0:
                self._present_acc -= 60.0
                self.memory.record("present_min", state.date_key)
        if state.cpu_busy:
            self._busy_acc += step
            if self._busy_acc >= 60.0:
                self._busy_acc -= 60.0
                self.memory.record("busy_min", state.date_key)

    def _update_fullscreen(self, monitor) -> None:
        fullscreen = win.is_fullscreen_on_monitor(monitor)
        if fullscreen and not self._hidden_by_fullscreen:
            log.info("fullscreen detected - hiding")
            self._hidden_by_fullscreen = True
            self.behavior.set_hidden(True)
            # Silently: we are closing it, not the user, so it must not count as a poke.
            self.bubble.close_silently()
            self.window.fade_to(0.0)
            self._update_tick_rate()
        elif not fullscreen and self._hidden_by_fullscreen:
            log.info("fullscreen ended - returning")
            self._hidden_by_fullscreen = False
            if not self._snoozed and not self._guarding:  # a snooze or guard outlasts it
                self.behavior.set_hidden(False, greet=True)
                self.window.fade_to(1.0)
            self._update_tick_rate()

    def _rescale_for(self, monitor) -> None:
        """Re-derive the pixel-art scale for ``monitor``'s DPI and rebuild if it changed.

        Only relevant on a mixed-DPI setup: walking to a 150% monitor should make him physically
        the same size, which means a different *integer* sprite scale. A pinned ``sprite_scale``
        in the config is respected and never overridden.
        """
        if int(self.config["sprite_scale"]) > 0:
            return
        dpi = win.get_dpi_for_monitor(monitor)
        scale = win.suggest_sprite_scale(dpi)
        if scale == self.sheet.scale:
            return
        try:
            # Through _load_sheet, so walking onto a monitor with another DPI does not strip off
            # his costume and colour along with the old scale. It reads the config, so a costume
            # he was hopping into goes on now rather than at the apex.
            self._costume_pending = False
            self._costume_timer.stop()
            sheet = self._load_sheet(scale, self._persona_costume)
        except Exception:
            log.exception("could not rebuild sprites at scale %s; keeping %s", scale, self.sheet.scale)
            return

        log.info("dpi changed to %s - sprite scale %s -> %s", dpi, self.sheet.scale, scale)
        self.sheet = sheet
        self.behavior.set_sheet(sheet)
        self.window.resize(sheet.sprite_w, sheet.sprite_h)
        self._last_frame_key = None  # force a repaint with the new pixmaps

        # The bubble, toast and billboard size themselves from DPI too, so rebuild them at the new
        # scale. The billboard keeps whatever it was showing - a DPI change is not a reason to
        # forget what you asked him to hold.
        self.ui_scale = max(1.0, dpi / win.DEFAULT_DPI)
        was_visible = self.bubble.isVisible()
        self.bubble.close_silently()
        self.bubble.deleteLater()
        self.toast.hide()
        self.toast.deleteLater()
        billboard_text = self.behavior.pocket_text
        self.billboard.take_down()
        self.billboard.deleteLater()
        focus_was_up = self.focus_timer.isVisible()
        self.focus_timer.take_down()
        self.focus_timer.deleteLater()
        self.bubble = AskBubble(self.ui_scale)
        self.toast = Toast(self.ui_scale)
        self.billboard = Billboard(self.ui_scale)
        self.focus_timer = FocusTimer(self.ui_scale)
        self.tool_wheel.hide()
        self.tool_wheel.deleteLater()
        self._make_tool_wheel()
        self.bubble.submitted.connect(self._on_submitted)
        self.bubble.calculated.connect(self._on_calculated)
        self.bubble.dismissed.connect(self._on_bubble_dismissed)
        if billboard_text:
            x, y, w, _h = self.behavior.rect
            self.billboard.show_text(
                billboard_text, QRect(x, y, w, self.behavior.sprite_h), self._screen_rect()
            )
        if focus_was_up:
            x, y, w, _h = self.behavior.rect
            self.focus_timer.show_time(
                self.behavior.focus_remaining_s, self.behavior.focus_total_s,
                QRect(x, y, w, self.behavior.sprite_h), self._screen_rect(),
            )
        if was_visible:
            x, y, w, _h = self.behavior.rect
            self.bubble.show_for(
                QRect(x, y, w, self.behavior.sprite_h),
                self._screen_rect(),
                str(self.config["default_mode"]),
            )

    def _update_tick_rate(self) -> None:
        """Sleep until something is actually going to change.

        A fixed 33fps clock spends most of its budget recomputing a frame nobody can see - an idle
        breathing frame lasts 450ms. Walking still gets the full rate; standing, sitting and
        sleeping drop to a few ticks a second, which is where most of the idle cost went.
        """
        if self._tick_timer is None:
            return
        if self._hidden_by_fullscreen or self._snoozed or self.behavior.paused:
            wanted = IDLE_TICK_MS
        else:
            wanted = int(self.behavior.next_tick_ms(TICK_MS, IDLE_TICK_MS))
        # Hysteresis: nudging the interval every tick would restart the countdown constantly.
        if abs(self._tick_timer.interval() - wanted) > 4:
            self._tick_timer.setInterval(wanted)

    def _tick_devices(self) -> None:
        """Read mic and camera state, show it, and notice a muted conversation.

        On the 700ms presence timer rather than the movement tick: none of this changes fast
        enough to be worth reading twenty times a second, and the whole point of the consent
        store's watcher is that a read costs nothing at all unless something actually changed.
        """
        mic_on = bool(self.config["mic_indicator_enabled"])
        camera_on = bool(self.config["camera_indicator_enabled"])
        if not mic_on and not camera_on:
            if self._device_state is not None:
                self._device_state = None
                self.tray.set_mic_state(None)
                self._refresh_pin_badge()
            self._muted_since = None
            return

        muted = devices.mic_muted() if mic_on else None
        mic_live = devices.mic_in_use() if mic_on else False
        camera_live = devices.camera_in_use() if camera_on else False

        state = (muted, mic_live, camera_live)
        if state != self._device_state:
            self._device_state = state
            self.tray.set_mic_state(muted)
            # Only on a real change: rebuilding a QIcon is four scaled and repainted pixmaps, and
            # this timer fires all day.
            self._refresh_pin_badge()

        self._tick_muted_talk(bool(mic_live and muted))

    def _tick_muted_talk(self, unheard: bool) -> None:
        """Talking into a muted microphone, for long enough that it was not just a passing blip."""
        now = time.monotonic()
        if not unheard or self._guarding:
            self._muted_since = None
            return
        if self._muted_since is None:
            self._muted_since = now
            return
        held_s = now - self._muted_since
        if held_s < float(self.config["talking_while_muted_after_s"]) or now < self._muted_nag_until:
            return
        self._muted_nag_until = now + MUTED_NAG_COOLDOWN_S
        self.behavior.talking_while_muted()
        # The toast goes up whether or not he was in a state to perform the animation - asleep or
        # in your hand, the message is the point and he is the decoration.
        x, y, w, _h = self.behavior.rect
        self.toast.show_message(
            "You're muted",
            QRect(x, y, w, self.behavior.sprite_h),
            self._screen_rect(),
            sub="Click to unmute",
            on_click=self._on_mic_toggle,
        )

    def _on_mic_toggle(self) -> None:
        """Flip the microphone, from the tray, the shortcut, or the muted-talk toast."""
        result = devices.toggle_mic()
        if result is None:
            self.tray.notify("Nib", "No microphone to mute.")
            return
        self.tray.set_mic_state(result)
        # Straight away rather than on the next presence tick, so the menu and the icon agree with
        # what you just did instead of lagging most of a second behind it.
        self._device_state = None
        self._muted_since = None
        self._tick_devices()

    def _reassert_topmost(self) -> None:
        # Not while the tool wheel is up: both are topmost, and re-asserting his would raise him
        # above it - his sprite painted over the very tools ringed round him.
        # Nor while the guard is up: he is hidden then, and must never be raised over the fog.
        if (not self._hidden_by_fullscreen and self._ruler is None
                and not self.tool_wheel.isVisible() and not self._guarding):
            self.window.reassert_topmost()

    def _screen_rect(self) -> QRect:
        if self._monitor is None:
            return QRect(0, 0, 1920, 1080)
        left, top, right, bottom = self._monitor.rect
        return QRect(left, top, right - left, bottom - top)

    # --- the tick -----------------------------------------------------------------

    def _tick(self) -> None:
        now = time.monotonic()
        dt_ms = (now - self._last_tick) * 1000.0
        self._last_tick = now

        cursor = win.get_cursor_pos()
        self.behavior.update(dt_ms, cursor)
        if self.behavior.take_costume_swap():
            # Before _apply_frame, so the apex paints already dressed.
            self._apply_pending_costume()
        self._check_wheel_drift()

        # Crossing between screens has to be noticed promptly: waiting for the 700ms geometry
        # poll would leave him at the wrong height for most of a second after he steps across.
        if self._monitor is not None and not self._hidden_by_fullscreen:
            centre = self.behavior.x + self.behavior.sprite_w // 2
            if not self._monitor.contains_x(centre):
                self._refresh_geometry()

        if self._perched_hwnd:
            if self.behavior.perched:
                self.behavior.update_perch(win.track_window(self._perched_hwnd))
                if not self.behavior.perched:
                    # update_perch just released him - the window is already gone, so there is
                    # nothing left to un-pin; a closed window does not keep its topmost flag.
                    self._perched_hwnd = 0
                    self._perch_pinned = False
                elif not self._perch_pinned:
                    # Still standing there, undisturbed, since the drop - once that has held for
                    # PERCH_PIN_DELAY_MS this stops being "he happened to land here" and becomes a
                    # real perch, so only now does the one named exception to never touching
                    # another window's geometry actually engage.
                    self._perch_dwell_ms += dt_ms
                    if self._perch_dwell_ms >= PERCH_PIN_DELAY_MS:
                        win.set_topmost(self._perched_hwnd, True)
                        self._perch_pinned = True
                        self._refresh_pin_badge()
            else:
                # He left the perch some other way - grabbed again, angered off, called over.
                # Only unpin if the pin actually happened - the dwell may never have finished.
                if self._perch_pinned:
                    win.set_topmost(self._perched_hwnd, False)
                    self._perch_pinned = False
                    self._refresh_pin_badge()
                self._perched_hwnd = 0

        self._sync_persona_costume()
        self._apply_frame()
        self.sounds.tick(dt_ms)
        for event in self.behavior.take_sounds():
            self.sounds.play(event)
        if self.behavior.take_focus_finished():
            # A toast rather than relying on the on-screen celebration alone: he might be hidden
            # behind a fullscreen app when a session completes, and the toast is the one part of
            # this that still reaches you either way.
            self.tray.notify(str(self.config["name"]), "Focus session complete - nice work.")
            self._refresh_tray()
        # Reconciled here every tick, rather than only from the tray toggle, so every way a session
        # can end - it finishing on its own, anger cancelling it, being called over - takes the
        # readout down too. Anything that leaves it up after focus_active goes False would be
        # exactly the tooltip-contradicts-posture bug this feature already had to fix once.
        if self.behavior.focus_active and not self._guarding:
            x, y, w, _h = self.behavior.rect
            self.focus_timer.show_time(
                self.behavior.focus_remaining_s, self.behavior.focus_total_s,
                QRect(x, y, w, self.behavior.sprite_h), self._screen_rect(),
            )
        elif self.focus_timer.isVisible():
            self.focus_timer.take_down()
        self._update_tick_rate()

    def _sync_persona_costume(self) -> None:
        """Put on (or take off) whatever headwear the current activity calls for.

        A comparison every tick and a rebuild only when it changes, which at ~7ms measured against
        a 12s per-app cooldown is far cheaper than keeping a second dressed sheet in memory.

        Two sources, and the order matters. The persona/moment costume is *momentary* - reading
        glasses for as long as he is typing, a hard hat for as long as he is fanning himself - and
        it wins while it is set. Underneath it sits the seasonal one, which is ambient and has no
        expiry, so when a moment ends he goes back to his beanie rather than to bare-headed.

        Not while a quick-change is in the air: the config already holds your new costume, which
        takes the beanie off auto_costume - rebuilding for that would show the new one before the
        hop. The beanie stays on through the crouch and comes off at the apex.
        """
        if self._costume_pending:
            return
        wanted = self.behavior.persona_costume or self.behavior.auto_costume
        if wanted == self._persona_costume:
            return
        if wanted and wanted not in self.sheet.available_costumes():
            return  # a sheet without that costume simply does not get the gag
        try:
            sheet = self._load_sheet(self.sheet.scale, wanted)
        except Exception:
            log.exception("could not put on %r; staying as he is", wanted)
            return
        self._persona_costume = wanted
        self.sheet = sheet
        self.behavior.set_sheet(sheet)
        self._last_frame_key = None

    def _apply_frame(self, *, force: bool = False) -> None:
        animator = self.behavior.animator
        x, y, _w, height = self.behavior.rect
        shadow = self.behavior.shadow
        # Repaint only when the sprite frame actually changes - a few times a second rather than
        # sixty. Movement alone just needs a move(), which does not re-render the pixmap. The
        # height is part of the key because peeking changes it while the frame stays put, and the
        # shadow is because it spreads on landing while the frame does not.
        key = (animator.state, animator.frame_index, height, shadow)
        if force or key != self._last_frame_key:
            self._last_frame_key = key
            self.window.set_frame(
                animator.pixmap, animator.mask_region, visible_height=height, shadow=shadow
            )
        if force or self.window.pos().x() != x or self.window.pos().y() != y:
            self.window.move(x, y)

    # --- interaction --------------------------------------------------------------

    def _on_click(self) -> None:
        if not self.behavior.click():
            return
        if self.bubble.isVisible():
            self.bubble.close()
            return
        x, y, w, _h = self.behavior.rect
        self._open_bubble_at(QRect(x, y, w, self.behavior.sprite_h))

    def _on_dragged(self, top_left) -> None:
        self.behavior.drag_to(top_left.x(), top_left.y())
        self._apply_frame(force=True)

    def _on_drag_finished(self) -> None:
        """Let go. Lands on whatever real window is under him, if any - but only a gentle release
        counts; behavior.drag_end judges that by the same release speed a throw already measures,
        so a flick that happens to end over a window flies through it instead of sticking.

        Finding the window is read-only - the same rectangle-only guarantee find_ledge already
        makes. Actually pinning it is the one deliberate exception to that guarantee, and does not
        happen here: _tick delays it by PERCH_PIN_DELAY_MS so a moment's stop is not mistaken for
        a real perch. See win.set_topmost's own docstring for exactly how narrowly that is scoped.
        """
        x, y, w, h = self.behavior.rect
        found = win.find_window_at(x + w // 2, y + h // 2)
        self.behavior.drag_end(perch_rect=found[1] if found is not None else None)
        if found is not None and self.behavior.perched:
            self._perched_hwnd = found[0]
            self._perch_pinned = False
            self._perch_dwell_ms = 0.0
        else:
            self._perched_hwnd = 0

    def _on_bubble_dismissed(self) -> None:
        self.behavior.bubble_dismissed()

    def _on_submitted(self, mode: str, text: str) -> None:
        self.config["default_mode"] = mode
        self._send(mode, text)

    def _on_calculated(self, expression: str, result: str, ok: bool) -> None:
        """The bubble answered a sum or conversion itself - nothing was launched, nothing sent.

        A toast rather than the billboard sign: the sign already means something else (the
        clipboard pocket, held for as long as you leave it up) and reusing it here would mean an
        answer silently overwriting whatever you had grabbed onto it. The toast is unclaimed,
        already built for exactly this "a small message near him, then gone" shape, and a click
        offers the one thing a calculator answer is actually for - see docs/USING-HIM.md.
        """
        self.behavior.calculated(ok)
        x, y, w, _h = self.behavior.rect
        self.toast.show_message(
            result,
            QRect(x, y, w, self.behavior.sprite_h),
            self._screen_rect(),
            sub="Click to copy" if ok else "",
            on_click=(lambda r=result: self._copy_calculation(r)) if ok else None,
        )

    def _copy_calculation(self, result: str) -> None:
        value = result[2:] if result.startswith("= ") else result
        QApplication.clipboard().setText(value)

    def _send(self, mode: str, text: str, *, after_ms: int = 0) -> None:
        """Fire a deep link and give the appropriate feedback. Shared by the bubble and by drops.

        ``after_ms`` delays only his *reaction*, never the launch. A drop makes him glance down at
        what landed on him, and jumping straight to the working animation would cut that off after
        a single frame - but making you wait for Claude to open would be a real cost for a purely
        cosmetic beat.
        """
        provider = str(self.config["provider"])
        text = text[:max_chars_for(provider)]
        self.config["last_prompt"] = text
        # Through the helper, never by mutating the stored list: an in-place append would not set
        # the dirty flag and the entry would be lost at the next save.
        self.config.remember_prompt(text)
        ok, reason = launch(mode, text, provider)
        # Not a poke either way. Only a launch that went through gets "on it"; the text goes with
        # it so he can react to its shape - matched against a keyword table and discarded.
        react = (lambda t=text: self.behavior.prompt_sent(t)) if ok else self.behavior.launch_failed
        if after_ms > 0:
            QTimer.singleShot(after_ms, react)
        else:
            react()
        if ok:
            return

        log.warning("launch failed: %s", reason)
        x, y, w, _h = self.behavior.rect
        self.toast.show_message(
            reason or FAILURE_MESSAGE,
            QRect(x, y, w, self.behavior.sprite_h),
            self._screen_rect(),
            sub="Click here to open the download page.",
            on_click=lambda p=provider: open_download_page(p),
        )

    def _open_bubble_at(self, anchor: QRect, prefill: str = "") -> None:
        # Deliberately NOT blocked by ghost mode. Ghost mode is about him being out of the way of
        # the *mouse*; the hotkey is the whole reason the app exists, and a launcher you have
        # switched off by accident is worse than a mascot in the way.
        #
        # An explicit prefill (a dropped file, a quick prompt) always wins; the pocket is only a
        # fallback for the plain click/hotkey paths, which otherwise open empty. Popped here rather
        # than at each call site, so any future way of opening the bubble gets this for free.
        if not prefill and self.behavior.pocket_full:
            prefill = self.behavior.pocket_take()
            self.billboard.take_down()
        provider = str(self.config["provider"])
        # A mode the current provider does not have - because you switched from Claude to ChatGPT
        # while "cowork" was remembered - would leave the picker with nothing selected.
        mode = str(self.config["default_mode"])
        if mode not in modes_for(provider):
            mode = get_provider(provider).default_mode
        self.bubble.show_for(
            anchor,
            self._screen_rect(),
            mode,
            prefill=prefill,
            history=list(self.config["prompt_history"]),
            provider=provider,
            calculator_enabled=bool(self.config.get("calculator_enabled", True)),
        )

    def _on_hotkey(self) -> None:
        """Global hotkey: put the bubble at the pointer rather than making you hunt for him."""
        if self._hidden_by_fullscreen:
            return
        if self.bubble.isVisible():
            self.bubble.close_silently()
            return
        cx, cy = win.get_cursor_pos()
        log.info("hotkey pressed")
        # Counts as noticing him even though it never goes near his window - otherwise the person
        # using the shortcut all day is exactly the person he decides is ignoring him.
        self.behavior.noticed()
        self._open_bubble_at(QRect(cx - 8, cy - 8, 16, 16))

    def _on_pocket_hotkey(self) -> None:
        """Grab whatever is on the clipboard right now and put it up on his sign.

        Reads the clipboard rather than being handed one, so this works no matter what you had
        selected it from - a browser, a terminal, another app entirely. Adds to the sign rather
        than replacing it, so a second grab builds on the first instead of losing it.
        """
        text = QApplication.clipboard().text()
        if self.behavior.pocket_grab(text):
            x, y, w, _h = self.behavior.rect
            self.billboard.show_text(
                self.behavior.pocket_text, QRect(x, y, w, self.behavior.sprite_h),
                self._screen_rect(),
            )
        else:
            self.behavior.pocket_empty()
            # Not over the fog, and not for a mascot nobody can currently see.
            if not (self._guarding or self._hidden_by_fullscreen or self._snoozed):
                x, y, w, _h = self.behavior.rect
                self.toast.show_message(
                    "Nothing to pin - the clipboard has no text.",
                    QRect(x, y, w, self.behavior.sprite_h), self._screen_rect(),
                    sub="Copy some text, then pin it again.",
                )

    def _on_double_clicked(self) -> None:
        """Double-click fires your quick prompt, or repeats the last one."""
        self.behavior.noticed()
        quick = str(self.config["quick_prompt"]).strip()
        repeat = str(self.config["last_prompt"]).strip()
        prompt = quick or repeat
        if not prompt:
            # Nothing to repeat yet, so fall back to just asking.
            self._on_click()
            return
        self.bubble.close_silently()
        log.info("double-click: sending the %s prompt", "quick" if quick else "last")
        self._send(str(self.config["default_mode"]), prompt)

    def _on_context_menu(self, position: QPoint) -> None:
        """Right-click him: the tool wheel, or - with it switched off, or Shift held - the menu.

        The menu is the couple of things that only make sense right here, pinned above the exact
        same menu the tray icon shows, so the whole thing (costumes, settings, quit, all of it) is
        one right-click away instead of a hunt for an icon Windows might have tucked into the
        overflow tray. See ``Tray.popup_for_mascot``. The wheel's More... tool opens that same
        menu, so turning the wheel on never puts anything out of reach.
        """
        shift = bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)
        if bool(self.config["tool_wheel_enabled"]) and not shift:
            self._open_tool_wheel(position)  # Behavior.wheel_opened notices him itself
            return
        self.behavior.noticed()
        self.tray.popup_for_mascot(position, self._mascot_menu_extras())

    def _mascot_menu_extras(self) -> list[QAction]:
        """What the right-click menu pins above the tray's own - built fresh each time it opens."""
        extras: list[QAction] = []

        ask = QAction("Ask Claude…", self.tray)
        ask.triggered.connect(self._on_click)
        extras.append(ask)

        last = str(self.config["last_prompt"]).strip()
        if last:
            label = last if len(last) <= 40 else last[:37] + "…"
            again = QAction(f'Send again: "{label}"', self.tray)
            again.triggered.connect(
                lambda: self._send(str(self.config["default_mode"]), last)
            )
            extras.append(again)

        provider = get_provider(str(self.config["provider"]))
        if len(provider.modes) > 1:
            group = QActionGroup(self.tray)
            group.setExclusive(True)
            for mode in provider.modes:
                action = QAction(provider.label_for(mode), self.tray, checkable=True)
                action.setChecked(self.config["default_mode"] == mode)
                action.triggered.connect(
                    lambda _c=False, m=mode: self.config.__setitem__("default_mode", m)
                )
                group.addAction(action)
                extras.append(action)

        snooze = QAction("Hide for 10 minutes", self.tray)
        snooze.triggered.connect(lambda: self._snooze(600))
        extras.append(snooze)
        return extras

    # --- the tool wheel -----------------------------------------------------------

    def _make_tool_wheel(self) -> None:
        self.tool_wheel = ToolWheel(self.ui_scale)
        self.tool_wheel.chosen.connect(self._on_tool_chosen)
        self.tool_wheel.closed.connect(self.behavior.wheel_closed)
        self.tool_wheel.aimed.connect(self._on_wheel_aimed)
        self._wheel_position = QPoint()
        self._wheel_centre = QPoint()

    def _wheel_items(self) -> list[WheelItem]:
        """The whole ring, in order, each tool labelled with what it will do *right now*.

        Three groups, scrolled through in this order: his **tools**, then things to do **to him**,
        then the **app**. The tools come first so they - and More..., the way into the full menu -
        are all on the arc the moment it opens; everything after them is a scroll away.

        Conditional tools are left off rather than greyed out: a wheel is aimed by direction, and
        a dead slot is a direction that does nothing - worse than a slightly shorter ring. A ring
        marks a toggle that is not how he normally is: focusing, paused, not wandering.
        """
        tools, him, app = "Tools", "Him", "App"
        provider = get_provider(str(self.config["provider"]))
        items = [WheelItem("ask", f"Ask {provider.label}", "ask", group=tools)]
        if str(self.config["last_prompt"]).strip():
            items.append(WheelItem("again", "Send your last prompt again", "again", group=tools))
        items.append(WheelItem("measure", "Measure the screen", "measure", group=tools))
        items.append(WheelItem("read", "Read text from an image", "read", group=tools))
        focusing = self.behavior.focus_active
        items.append(WheelItem("focus", "Stop focus" if focusing else "Start focus", "focus",
                               "active" if focusing else "", group=tools))
        items.append(WheelItem("pocket", "Pin the clipboard to his sign", "pocket", group=tools))
        if bool(self.config["mic_indicator_enabled"]):
            muted = devices.mic_muted()
            if muted is not None:
                items.append(WheelItem("mic", "Unmute microphone" if muted else "Mute microphone",
                                       "mic_muted" if muted else "mic", self._device_badge_kind(),
                                       group=tools))
        items.append(WheelItem("more", "More…", "more", group=tools))

        items.append(WheelItem("do_something", "Do something", "sparkle", group=him))
        items.append(WheelItem("costume", "Surprise costume", "costume", group=him))
        items.append(WheelItem("wardrobe", "Wardrobe…", "wardrobe", group=him))
        items.append(WheelItem("hide", "Hide for 10 minutes", "hide", group=him))
        # Next to Hide: the same "go away for a while" thought, for the whole screen rather than
        # just him. Not with the tools - a ninth tool there would push More... off the opening arc.
        items.append(WheelItem("guard", "Guard the screen", "guard", group=him))
        wandering = bool(self.config["wander_enabled"])
        items.append(WheelItem("wander", "Stop wandering" if wandering else "Let him wander",
                               "wander", "" if wandering else "active", group=him))
        paused = bool(self.config["paused"])
        items.append(WheelItem("pause", "Resume" if paused else "Pause him",
                               "resume" if paused else "pause", "active" if paused else "",
                               group=him))
        # Only ever offered to switch ON: in ghost mode he is click-through, so nobody can be
        # right-clicking him to switch it off - that is the tray's job, and the label says so.
        items.append(WheelItem("ghost", "Ghost mode (undo from the tray)", "ghost", group=him))

        if len(provider.modes) > 1:
            modes = list(provider.modes)
            current = str(self.config["default_mode"])
            following = modes[(modes.index(current) + 1) % len(modes)] if current in modes else modes[0]
            items.append(WheelItem("mode", f"Switch to {provider.label_for(following)}", "mode",
                                   group=app))
        items.append(WheelItem("settings", "Settings", "settings", group=app))
        items.append(WheelItem("tips", "Show tips", "tips", group=app))
        items.append(WheelItem("reset", "Send him home", "home", group=app))
        # Last on the ring: the furthest a stray flick can be from it, as it ends in a restart.
        items.append(WheelItem("update", "Update and restart", "update", group=app))
        return items

    def _open_tool_wheel(self, position: QPoint) -> None:
        self.bubble.close_silently()
        self._wheel_position = QPoint(position)
        # The items first: their labels describe what he is doing now, before he is held still.
        items = self._wheel_items()
        self.behavior.wheel_opened()
        x, y, w, _h = self.behavior.rect
        h = self.behavior.sprite_h
        centre = QPoint(x + w // 2, y + h // 2)
        self._wheel_centre = QPoint(centre)
        self.tool_wheel.open(
            items, centre, self._screen_rect(),
            sprite_radius=max(w, h) / 2.0, press_global=QPoint(position),
        )

    def _open_wardrobe(self) -> None:
        """The wheel again, with his costumes on it - see _wardrobe_ring.

        Picked from the wheel, so that wheel has already closed and let him go: hold him again,
        and re-centre, or the drift check would measure against where he stood last time. Click
        mode - the press that opened the first wheel is long gone.
        """
        items = _wardrobe_ring(self.sheet, str(self.config["costume"]),
                               list(self.config["costume_recents"]))
        self.behavior.wheel_opened()
        x, y, w, _h = self.behavior.rect
        h = self.behavior.sprite_h
        centre = QPoint(x + w // 2, y + h // 2)
        self._wheel_centre = QPoint(centre)
        self.tool_wheel.open(
            items, centre, self._screen_rect(),
            sprite_radius=max(w, h) / 2.0, press_global=None,
        )

    def _check_wheel_drift(self) -> None:
        """Put the wheel away if he has moved out from under it - see WHEEL_DRIFT_PX."""
        if not self.tool_wheel.isVisible():
            return
        x, y, w, _h = self.behavior.rect
        cx, cy = x + w // 2, y + self.behavior.sprite_h // 2
        drift = abs(cx - self._wheel_centre.x()) + abs(cy - self._wheel_centre.y())
        if drift > WHEEL_DRIFT_PX:
            log.debug("he moved %dpx from under the tool wheel - closing it", drift)
            self.tool_wheel.close()

    def _on_wheel_aimed(self, point) -> None:
        """His eyes on the tool you are aiming at - painted now, not on the next (<=250ms) tick."""
        self.behavior.wheel_aimed(point)
        self._apply_frame()

    def _on_tool_chosen(self, key: str) -> None:
        """Every tool is a handler that already existed for the tray or a hotkey - none is new."""
        if key.startswith(WARDROBE_PREFIX):
            self._on_costume_chosen(key[len(WARDROBE_PREFIX):])
            self._refresh_tray()
        elif key == "ask":
            self._on_click()
        elif key == "again":
            last = str(self.config["last_prompt"]).strip()
            if last:
                self._send(str(self.config["default_mode"]), last)
        elif key == "measure":
            self._on_measure_requested()
        elif key == "read":
            self._pick_image_to_read()
        elif key == "focus":
            self._on_focus_toggled(not self.behavior.focus_active)
        elif key == "pocket":
            self._on_pocket_hotkey()
        elif key == "mic":
            self._on_mic_toggle()
        elif key == "more":
            self.tray.popup_for_mascot(self._wheel_position, self._mascot_menu_extras())
        elif key == "do_something":
            self._on_do_something()
        elif key == "costume":
            self._on_surprise_costume()
            self._refresh_tray()
        elif key == "wardrobe":
            self._open_wardrobe()
        elif key == "hide":
            self._snooze(600)
        elif key == "guard":
            self._on_guard_requested()
        # The three toggles below are the tray's own handlers, which assume the tray flipped its
        # checkbox itself before calling them. From here nothing did, so the tray is refreshed
        # afterwards - otherwise its menu would go on saying "Pause" about a paused mascot.
        elif key == "wander":
            self._on_wander_toggled(not bool(self.config["wander_enabled"]))
            self._refresh_tray()
        elif key == "pause":
            self._on_pause_toggled(not bool(self.config["paused"]))
            self._refresh_tray()
        elif key == "ghost":
            self._on_ghost_toggled(True)
            self._refresh_tray()
        elif key == "mode":
            self._cycle_mode()
        elif key == "settings":
            self._on_settings()
        elif key == "tips":
            self.show_intro()
        elif key == "reset":
            self._on_reset_position()
        elif key == "update":
            self._on_update_requested()
        # Last, after the handler: a tool that gave him a job keeps it (see Behavior.tool_picked).
        if self.behavior.tool_picked(key):
            self._apply_frame()

    def _cycle_mode(self) -> None:
        """Move default_mode on to the provider's next mode - the wheel's Chat/Cowork switch."""
        modes = list(get_provider(str(self.config["provider"])).modes)
        if len(modes) < 2:
            return
        current = str(self.config["default_mode"])
        following = modes[(modes.index(current) + 1) % len(modes)] if current in modes else modes[0]
        self.config["default_mode"] = following

    def _pick_image_to_read(self) -> None:
        """The wheel's way into drop-to-OCR, for when there is nothing handy to drag onto him."""
        patterns = " ".join(f"*{suffix}" for suffix in sorted(ocr.IMAGE_SUFFIXES))
        path, _ = QFileDialog.getOpenFileName(
            None, "Read text from an image", str(Path.home() / "Pictures"),
            f"Images ({patterns})",
        )
        if path and ocr.is_ocr_eligible(path):
            self._on_image_ocr_requested(path)

    def _snooze(self, seconds: int) -> None:
        """Send him away for a bit without pausing him - he comes back on his own."""
        log.info("snoozing for %ds", seconds)
        self.behavior.set_hidden(True)
        self.bubble.close_silently()
        self.window.fade_to(0.0)
        self._snoozed = True
        self._update_tick_rate()
        QTimer.singleShot(max(1, seconds) * 1000, self._wake_from_snooze)

    def _wake_from_snooze(self) -> None:
        if not self._snoozed:
            return
        self._snoozed = False
        log.info("snooze over")
        if not self._hidden_by_fullscreen and not self._guarding:
            self.behavior.set_hidden(False, greet=True)
            self.window.fade_to(1.0)
        self._update_tick_rate()

    # --- update and restart --------------------------------------------------------

    def _tell(self, text: str, sub: str = "", duration_ms: int = 6000) -> None:
        """Say something in the toast above him - how every tool without a window of its own talks.

        Not while a guard is up: the fog is there to hide everything of his, toasts included.
        """
        if self._guarding or self.guard.active:
            log.info("not shown during a guard: %s", text)
            return
        x, y, w, _h = self.behavior.rect
        self.toast.show_message(text, QRect(x, y, w, self.behavior.sprite_h), self._screen_rect(),
                                sub=sub, duration_ms=duration_ms)

    def _guard_busy(self) -> bool:
        """A guard is up, or about to be: its PIN setup is open."""
        return self._guarding or self.guard.active or self.guard.setting_up

    def _update_blocker(self) -> str:
        """Why an update may not start right now, or ""."""
        return updater.refusal(running=self._updating, guarding=self._guard_busy(),
                               focusing=self.behavior.focus_active)

    def _on_update_requested(self) -> None:
        """The wheel's Update and restart. Decides how this copy updates, then hands the work to a
        thread - the wheel has already closed, and nothing here waits on git or the network."""
        why = self._update_blocker()
        if why:
            self._tell(why)
            return
        mode = updater.detect_mode()
        log.info("update requested (%s)", mode.kind)
        if mode.kind == updater.MODE_NONE:
            self._tell(mode.reason, sub="Nothing was changed.")
            return
        if mode.kind == updater.MODE_GIT:
            # GuardService imports these the first time it needs them. Now, while the files on
            # disk are still the ones this process started from, so a guard that does go up
            # before the restart runs old code throughout, not old code with new parts.
            import guard_overlay  # noqa: F401
            import guard_scene  # noqa: F401
        self._updating = True
        self._tell("Checking for updates…", duration_ms=60000)
        bridge = self._update_bridge
        updater.start(mode, progress=bridge.progress.emit, done=bridge.finished.emit,
                      log_path=config_path().parent / "update.log",
                      blocked=lambda: updater.ask_elsewhere(bridge.run_here.emit, self._merge_blocker))

    def _run_from_update(self, fn) -> None:
        """``_UpdateBridge.run_here``: run what the update thread sent, here on the UI thread."""
        fn()

    def _merge_blocker(self) -> str:
        """Just before a checkout's files change: is a restart still all right? A guard or focus
        session that began during the fetch says no. A yes also marks the files as changing, so no
        new guard starts from here on - not during pip, not in the moment before the restart, not
        by the idle timer.

        Runs on the UI thread (the update thread asks through ``updater.ask_elsewhere``), the
        thread every guard starts on, so the check and the mark are one step as far as a guard is
        concerned: it starts before them and is seen, or after them and sees the mark."""
        why = updater.refusal(running=False, guarding=self._guard_busy(),
                              focusing=self.behavior.focus_active)
        if not why:
            self._files_changing = True
        return why

    def _guard_start_blocker(self) -> str:
        """GuardService.start_blocker: a checkout that is new on disk but not restarted yet."""
        if self._files_changing or self._restart_pending:
            return "an update is waiting for its restart"
        return ""

    def _on_update_progress(self, text: str) -> None:
        self._tell(text, duration_ms=60000)

    def _on_update_finished(self, outcome) -> None:
        """Up to date or failed: say so, and that is all. Updated: say so, then restart.

        An update that is about to restart still counts as running until it does (or doesn't), so a
        second pick in the moment before the restart is refused rather than started alongside it.
        """
        log.info("update: %s - %s", outcome.kind, outcome.message)
        if outcome.kind not in (updater.RESTART, updater.HANDOFF):
            self._updating = False
            self._files_changing = False   # only a fast-forward that went through changes them
            self._tell(outcome.message, sub=outcome.sub)
            return
        if outcome.kind == updater.HANDOFF:
            self._staged = outcome
        self._tell(outcome.message, sub=outcome.sub)
        # Long enough to read the line; longer when there is small print (pip failing) to read too.
        QTimer.singleShot(4500 if outcome.sub else 1500, lambda o=outcome: self._restart_after_update(o))

    def _restart_after_update(self, outcome, waited: bool = False) -> None:
        """Start the replacement, then quit through the tray's own Quit, config saved and all.

        Checked again here, not only at the click: a guard or a focus session can have started in
        the moment between the files changing and now, and quitting would end either one. So the
        restart waits for it to end instead - checking every few seconds, still counting as a
        running update - and then goes ahead, with a word first so he doesn't just vanish.
        Waiting rather than giving up, because a checkout is already new on disk: this process
        must not carry on as the old code for longer than it has to.
        """
        guarding, focusing = self._guard_busy(), self.behavior.focus_active
        if guarding or focusing:
            if outcome.kind == updater.RESTART:
                self._restart_pending = True
            if not waited:
                busy = "the screen guard is down" if guarding else "your focus session is over"
                self._tell(f"Updated. I'll restart once {busy}.")
            QTimer.singleShot(UPDATE_RESTART_RETRY_MS,
                              lambda o=outcome: self._restart_after_update(o, waited=True))
            return
        if waited:
            self._tell("Restarting into the update…")
            QTimer.singleShot(1500, lambda o=outcome: self._restart_after_update(o))
            return
        try:
            updater.launch_restart(outcome)
        except OSError:
            log.exception("could not start the updated Nib")
            self._updating = False
            if outcome.kind == updater.HANDOFF:
                self._staged = None
                updater.discard(outcome)
            else:
                self._restart_pending = True   # the files are new all the same
            self._tell("Updated, but I couldn't start again by myself.",
                       sub="Quit me from the tray and start me again.")
            return
        log.info("restarting after the update")
        self._staged = None   # the helper has it now: it swaps it in, then deletes it
        self._on_quit()

    # --- the away guard -------------------------------------------------------------

    def _on_guard_requested(self) -> None:
        """Wheel, tray or hotkey: cover the screen. Everything else is GuardService's business -
        including what "guard" means mid-Peek (lock now) or while already guarded (nothing)."""
        log.info("guard requested")
        self.tool_wheel.close()
        if self._guard_start_blocker() and not self.guard.active:
            # A new guard from this process would be the old GuardService driving a guard core
            # started from the new files on disk. "Lock now" in a Peek is the running guard's.
            log.info("guard refused: an update is waiting for its restart")
            self._tell("I've updated - restart me first, then I'll guard.",
                       sub="Quit me from the tray and start me again." if not self._updating
                       else "I'll restart by myself as soon as I can.")
            return
        self.guard.request_guard()

    def _set_guarding(self, active: bool) -> None:
        """Put him (and everything of his) away for the length of a guard, and bring him back.

        Hidden outright rather than faded or ghosted: his window is topmost, and anything of his
        left on screen - the bubble with your prompt history, the clipboard sign - is exactly what
        the fog is there to hide. The tick keeps running, so he resumes where he was.
        """
        if active == self._guarding:
            return
        self._guarding = active
        if active:
            self.bubble.close_silently()
            self.tool_wheel.close()
            self.toast.hide()
            self.billboard.take_down()
            self.focus_timer.take_down()
            if self._ruler is not None:
                self._ruler.close()
            self.behavior.set_hidden(True)
            self.window.fade_to(0.0, 0)
        else:
            if not self._hidden_by_fullscreen and not self._snoozed:
                self.behavior.set_hidden(False)
                ghost = bool(self.config["ghost_mode"])
                self.window.fade_to(float(self.config["ghost_opacity"]) if ghost else 1.0)
            if self.behavior.pocket_text:
                x, y, w, _h = self.behavior.rect
                self.billboard.show_text(
                    self.behavior.pocket_text, QRect(x, y, w, self.behavior.sprite_h),
                    self._screen_rect(),
                )
        self._update_tick_rate()

    def _on_files_dropped(self, paths: list[str]) -> None:
        """Dropped files: the intent is clear enough to launch straight away."""
        self.behavior.noticed()
        paths = [p for p in paths if p]
        if not paths:
            return
        self._glance_at_drop()
        if len(paths) == 1:
            prompt = f"Take a look at this file and tell me what's in it:\n{paths[0]}"
        else:
            listed = "\n".join(paths[:20])
            prompt = f"Take a look at these {len(paths)} files and summarise them:\n{listed}"
        log.info("%d file(s) dropped on the mascot", len(paths))
        self._send(str(self.config["default_mode"]), prompt, after_ms=DROP_GLANCE_MS)

    def _glance_at_drop(self) -> None:
        """He looks down at whatever you just dropped on him.

        At the pointer, because that is where you let go. Purely cosmetic: the launch is NOT
        delayed by it - see ``_on_files_dropped``, which fires the deep link immediately and only
        sequences his animation afterwards.
        """
        cx, cy = win.get_cursor_pos()
        self.behavior.look_at(cx, cy)

    def _on_text_dropped(self, text: str) -> None:
        """Dropped text is ambiguous - he has the content but not the instruction.

        So this opens the bubble prefilled instead of guessing, letting you say what to do with it.
        """
        self.behavior.noticed()
        text = text.strip()[: max_chars_for(str(self.config["provider"]))]
        if not text:
            return
        self._glance_at_drop()
        log.info("%d chars of text dropped on the mascot", len(text))
        x, y, w, _h = self.behavior.rect
        self._open_bubble_at(QRect(x, y, w, self.behavior.sprite_h), prefill=text)

    def _on_image_ocr_requested(self, path: str) -> None:
        """Shift-dropping a single image: read its text instead of discussing the file.

        Fires and forgets - ``ocr.recognize`` returns immediately and reports back later,
        possibly a good deal later than the ~100ms typical case, through ``_ocr_bridge``.
        """
        self.behavior.noticed()
        self._glance_at_drop()
        log.info("OCR requested for a dropped image")
        ocr.recognize(
            path,
            lambda text, error: self._ocr_bridge.finished.emit(text or "", error or ""),
        )

    def _on_ocr_finished(self, text: str, error: str) -> None:
        """The background OCR call is back - text is a plain-English reason it failed instead."""
        self.behavior.read_image(ok=bool(text))
        x, y, w, _h = self.behavior.rect
        if not text:
            self.toast.show_message(
                error or "couldn't read that image",
                QRect(x, y, w, self.behavior.sprite_h),
                self._screen_rect(),
            )
            return
        clamped = text.strip()[: max_chars_for(str(self.config["provider"]))]
        self._open_bubble_at(QRect(x, y, w, self.behavior.sprite_h), prefill=clamped)

    # --- tray ---------------------------------------------------------------------

    # --- ghost mode, calling him, settings, tips ------------------------------------

    def _apply_ghost_mode(self) -> None:
        """Fade him down and make the whole window ignore the mouse - or put both back.

        The escape hatch, and the reason it exists: on the day a mascot is in the way, the
        alternatives are pausing him (he freezes mid-stride, which looks broken) or uninstalling
        him. This leaves him there, doing everything he normally does, and completely inert.

        Opacity is clamped away from zero in the config, because a mascot you can neither see nor
        click is indistinguishable from a crashed one.
        """
        ghost = bool(self.config["ghost_mode"])
        hwnd = int(self.window.winId())
        if not win.set_click_through(hwnd, ghost):
            log.warning("could not change the click-through style; ghost mode may not work")
        if ghost:
            self.bubble.close_silently()
        # Not fade_to(0): hiding the window entirely would take the mascot away, and the point is
        # that he is still there.
        target = float(self.config["ghost_opacity"]) if ghost else 1.0
        if not self._hidden_by_fullscreen and not self._snoozed:
            self.window.fade_to(target)
        log.info("ghost mode %s", "on" if ghost else "off")

    def _on_ghost_toggled(self, enabled: bool) -> None:
        self.config["ghost_mode"] = enabled
        self._apply_ghost_mode()

    def _on_focus_toggled(self, active: bool) -> None:
        self.behavior.set_focus(active)
        if active:
            # Instant feedback, the same reason set_focus itself begins the antic directly rather
            # than waiting for the next tick - a toggle in the tray deserves to show up right away,
            # not up to a tick-rate's worth of a beat later.
            x, y, w, _h = self.behavior.rect
            self.focus_timer.show_time(
                self.behavior.focus_remaining_s, self.behavior.focus_total_s,
                QRect(x, y, w, self.behavior.sprite_h), self._screen_rect(),
            )
        else:
            self.focus_timer.take_down()
        self._refresh_tray()

    def _on_come_here(self) -> None:
        """Call him over to the pointer. The useful half of "where did he go"."""
        if self._hidden_by_fullscreen:
            return
        if self._snoozed:
            self._wake_from_snooze()   # you asked for him; that outranks the snooze
        cx, _cy = win.get_cursor_pos()
        log.info("called over to x=%d", cx)
        if not self.behavior.come_here(cx):
            log.debug("...he was already there, or in no state to come")

    def _on_personality_chosen(self, personality: str) -> None:
        self.config["personality"] = personality
        log.info("personality -> %r", personality)
        self._refresh_tray()
        if self._settings is not None:
            self._settings.reload()

    def _on_settings(self) -> None:
        """Open the settings window, or raise the one already open."""
        if self._settings is None:
            self._settings = SettingsWindow(
                self.config,
                costumes=self.sheet.available_costumes(),
                skins=available_skins(),
                status=self._status_text,
                forget=self._forget_memory,
            )
            self._settings.changed.connect(self._on_settings_changed)
            self._settings.history_cleared.connect(lambda: self.behavior.noticed())
        else:
            self._settings.reload()
        self._settings.show()
        self._settings.raise_()
        self._settings.activateWindow()

    def _status_text(self) -> dict:
        """How he is, and what he remembers, for Settings → About him. Words only, never a score.

        ``memory`` is the list of plain-English lines from memory.py, or one line saying it is off;
        ``can_forget`` greys the Forget button out when there is nothing to forget.
        """
        name = str(self.config["name"])
        if self.memory.enabled:
            lines = self.memory.summary_lines(name)
        else:
            lines = [f"{name} isn't keeping count - memory is switched off on the Personality tab."]
            if not self.memory.is_empty:
                lines.append("What he already knew is kept until you forget it.")
        return {
            "line": f"{name} is {label_for(self.mood.spirits)}.",
            "memory": lines,
            "can_forget": not self.memory.is_empty,
        }

    def _forget_memory(self) -> None:
        """Settings → About him → Forget everything: back to the day you met.

        The file goes, and so does how the day has gone - a mood built on the days just forgotten
        would otherwise outlive them. Settings, costume and prompt history are untouched; those
        have their own controls.
        """
        self.memory.forget()
        needs = getattr(self, "needs", None)
        if needs is not None:
            needs.reset()
        self.mood.reset()
        # Today counts again from the next poll - a fresh start, met today, rather than nothing
        # until tomorrow - and the part-minutes banked so far belonged to what was wiped. Not
        # polled from here: the panel should show the empty memory you just asked for first.
        self._memory_day = ""
        self._arrival_pending = ""
        self._present_acc = self._busy_acc = 0.0
        self.behavior.noticed()

    def _on_settings_changed(self, keys: set) -> None:
        """React to the handful of settings that need more than a config write.

        Everything else is read live off the config on the tick, so it is already in effect by the
        time this runs - which is what keeps this method three branches long instead of sixty.
        """
        if keys & {"costume", "skin"}:
            self._rebuild_appearance()
            # After the rebuild, so he reacts while actually wearing it - same order as the tray.
            # Both at once gets the costume's: only one antic can play.
            if "costume" in keys:
                # A choice like any other, so it heads the wardrobe too. No hop: the window
                # already rebuilt him, and he is behind it.
                self.config.remember_costume(str(self.config["costume"]))
                self.behavior.costume_chosen(str(self.config["costume"]))
            else:
                self.behavior.skin_chosen()
        if keys & {"ghost_mode", "ghost_opacity"}:
            self._apply_ghost_mode()
        if "cpu_busy_percent" in keys or "southern_seasons" in keys:
            self.world = World(
                busy_percent=float(self.config["cpu_busy_percent"]),
                southern=bool(self.config["southern_seasons"]),
            )
        if "shadow_enabled" in keys:
            self._last_frame_key = None
            self._apply_frame(force=True)
        if "name" in keys:
            # The presence timer would get there within 700ms anyway; this just means the tooltip
            # is already right by the time you look down from the Apply button.
            self._update_presence()
        self._refresh_tray()

    def show_intro(self) -> None:
        """Run the one-off tour of the things you would otherwise never find.

        Chained through the existing toast rather than a new widget: it is already anchored to
        him, already themed, and already knows how to get out of the way.
        """
        self.config["intro_seen"] = True
        self._intro_step = 0
        self._next_intro_step()

    def _next_intro_step(self) -> None:
        if self._intro_step < 0 or self._intro_step >= len(INTRO_STEPS):
            self._intro_step = -1
            return
        text, sub, hold = INTRO_STEPS[self._intro_step]
        self._intro_step += 1
        if "%s" in text:
            spec = str(self.config["hotkey"]).strip()
            if not spec or not self._hotkey_hwnd:
                # No shortcut registered, so do not teach one that does nothing.
                self._next_intro_step()
                return
            text = text % spec.replace("+", " + ").title()
        x, y, w, _h = self.behavior.rect
        self.toast.show_message(
            text, QRect(x, y, w, self.behavior.sprite_h), self._screen_rect(),
            sub=sub, duration_ms=hold,
        )
        QTimer.singleShot(hold + INTRO_GAP_MS, self._next_intro_step)

    # --- tray -----------------------------------------------------------------------

    def _on_costume_chosen(self, costume: str) -> None:
        """The tray, the wheel's Surprise and the wardrobe all land here. He does a quick-change:
        the choice is saved now, and only the visible swap waits for the top of his hop."""
        previous = str(self.config["costume"])
        self.config["costume"] = costume
        self.config.remember_costume(costume)
        log.info("costume -> %r", costume or "none")
        # Shown at the apex (the take_costume_swap drain in _tick), or by the timer if the hop
        # stalls. Anything that knocks him out of it shows it at once. Re-picking the one he
        # already wears skips the hop, and so does any state quick_change refuses.
        if costume != previous and self.behavior.quick_change(costume):
            self._costume_pending = True
            self._costume_timer.start()
            return
        self._rebuild_appearance()
        # After the rebuild, so the reaction plays while he is actually wearing the thing.
        self.behavior.costume_chosen(costume)

    def _apply_pending_costume(self) -> None:
        """Show the costume a quick-change is hopping into - at its apex, or when the timer fires."""
        if not self._costume_pending:
            return
        # Cleared first, so a rebuild that fails cannot leave the persona sync blocked for good.
        self._costume_pending = False
        self._costume_timer.stop()
        self._rebuild_appearance()

    def _on_do_something(self) -> None:
        """Tray: play one of his rarer animations now.

        Wakes him first if he is napping - being asked to perform is attention, and refusing while
        asleep would make the button look broken at exactly the moment somebody tries it.
        """
        if self._snoozed:
            self._wake_from_snooze()
        if self.behavior.state in (State.SLEEP, State.WAKE):
            self.behavior.come_here(self.behavior.x + self.behavior.sprite_w // 2)
        did = self.behavior.do_something()
        if not did:
            log.debug("asked him to do something, but he is busy")

    def _on_surprise_costume(self) -> None:
        """Put a random costume on him. The fastest way to actually see fifty-three of them.

        Never the one he is already wearing - a "surprise me" that does nothing half the time is
        worse than no button. Treated exactly like picking one from the menu, so it persists and
        it outranks anything he would put on by himself.
        """
        available = [c for c in self.sheet.available_costumes() if c != self.config["costume"]]
        if not available:
            return
        import random

        chosen = random.choice(available)
        log.info("surprise costume -> %r", chosen)
        self._on_costume_chosen(chosen)
        self.behavior.noticed()

    def _on_skin_chosen(self, skin: str) -> None:
        self.config["skin"] = skin
        log.info("skin -> %r", skin)
        self._rebuild_appearance()
        self.behavior.skin_chosen()

    def _on_wander_toggled(self, enabled: bool) -> None:
        self.config["wander_enabled"] = enabled
        log.info("wander %s", "enabled" if enabled else "disabled")

    def _on_pause_toggled(self, paused: bool) -> None:
        self.behavior.paused = paused
        self.config["paused"] = paused
        self._update_tick_rate()
        log.info("mascot %s", "paused" if paused else "resumed")

    def _on_startup_toggled(self, enabled: bool) -> None:
        ok = win.set_launch_on_startup(enabled)
        actual = win.get_launch_on_startup()
        self.config["launch_on_startup"] = actual
        self.tray.refresh(
            wander=bool(self.config["wander_enabled"]),
            paused=bool(self.config["paused"]),
            startup=actual,
        )
        if not ok:
            self.tray.notify("Nib", "Couldn't update the Windows startup entry.")

    def _on_reset_position(self) -> None:
        self.config.reset_position()
        self._monitor = None
        self._refresh_geometry(initial=True)
        self._apply_frame(force=True)
        log.info("position reset")

    def _on_measure_requested(self) -> None:
        if self._ruler is not None:
            return
        cursor_x, cursor_y = win.get_cursor_pos()
        monitor = win.get_monitor_for_point(cursor_x, cursor_y)
        if monitor is None:
            return
        left, top, right, bottom = monitor.rect
        rect = QRect(left, top, right - left, bottom - top)
        dpi = win.get_dpi_for_monitor(monitor)
        start_pos = QPoint(cursor_x - left, cursor_y - top)
        self._ruler = RulerOverlay(rect, dpi, start_pos=start_pos)
        self._ruler.closed.connect(self._on_ruler_closed)
        self._ruler.show()
        self._ruler.raise_()

    def _on_ruler_closed(self) -> None:
        self._ruler = None

    def _drop_staged(self) -> None:
        """Quitting while a downloaded release still waits for its restart (a guard or a focus
        session held it up, and you quit from the tray instead): it is not going to be used, so
        it goes. The next Update and restart fetches it again."""
        if self._staged is not None:
            log.info("quitting before the update's restart - throwing the download away")
            updater.discard(self._staged)
            self._staged = None

    def _on_quit(self) -> None:
        self._drop_staged()
        self.bubble.close_silently()
        self.billboard.take_down()
        self.focus_timer.take_down()
        if self._ruler is not None:
            self._ruler.close()
        if self._perched_hwnd:
            # Quitting mid-perch must not leave someone else's window stuck on top with nothing
            # left tracking it - the one thing worse than never having pinned it at all. Only
            # actually unpins if the dwell ever finished and it was really pinned in the first
            # place.
            if self._perch_pinned:
                win.set_topmost(self._perched_hwnd, False)
                self._perch_pinned = False
            self._perched_hwnd = 0
        devices.shutdown()
        # Before app.quit(): the core is told to unlock and waited for, so it does not outlive Nib
        # and mistake a normal quit for a crash mid-guard.
        self.guard.shutdown()
        if self._settings is not None:
            self._settings.close()
        self.sounds.stop_all()
        for key_id, attribute in ((HOTKEY_ID, "_hotkey_hwnd"),
                                  (COME_HOTKEY_ID, "_come_hotkey_hwnd"),
                                  (POCKET_HOTKEY_ID, "_pocket_hotkey_hwnd"),
                                  (MIC_HOTKEY_ID, "_mic_hotkey_hwnd"),
                                  (GUARD_HOTKEY_ID, "_guard_hotkey_hwnd")):
            hwnd = getattr(self, attribute)
            if hwnd:
                win.unregister_hotkey(key_id, hwnd)
                setattr(self, attribute, 0)
        self._flush_config()
        self.tray.hide()
        self.app.quit()

    # --- persistence --------------------------------------------------------------

    def _flush_config(self) -> None:
        # Quantised inside Mood, so this is a no-op within a hundredth of the range rather than a
        # disk write every ten seconds for as long as he is running.
        self.mood.persist()
        # Dirty only when a count moved, and the most frequent count is a minute of presence - so
        # this writes memory.json at most about once a minute, and never while nothing happens.
        self.memory.save_if_dirty()
        if self.behavior.state not in (State.DRAG,):
            x = self.behavior.x
            if self._last_saved_x is None or abs(x - self._last_saved_x) > 2:
                self.config["last_x"] = x
                # The monitor's left edge identifies it stably, even if last_x later gets clamped.
                if self._monitor is not None:
                    self.config["last_monitor"] = self._monitor.rect[0]
                self._last_saved_x = x
        self.config.save_if_dirty()


def main() -> int:
    # Normally caught at the top of this file, before Qt is even imported; this covers main()
    # being called from somewhere other than ``__main__``.
    if len(sys.argv) >= 3 and sys.argv[1] == "--guard-core":
        import guard_core

        return guard_core.run(int(sys.argv[2]) if sys.argv[2].isdigit() else 0)
    # Before the mutex - see the top of this file. A no-op if that already dealt with it.
    updater.consume_after_update(sys.argv)
    # Logging first, so warnings raised while *reading* the config are not thrown away.
    setup_logging("INFO")
    config = Config()
    logging.getLogger().setLevel(
        getattr(logging, str(config["log_level"]).upper(), logging.INFO)
    )
    log.info("starting Nib (config: %s)", config.path)

    if not win.acquire_single_instance():
        log.warning("another Nib is already running - exiting")
        return 0
    # Downloads a crashed or killed Nib staged and never used. Off the UI thread: a whole unpacked
    # release takes a moment to delete, and nothing waits on it.
    threading.Thread(target=updater.sweep_stale, name="nib-sweep", daemon=True).start()

    app = QApplication(sys.argv)
    app.setApplicationName("Nib")
    app.setQuitOnLastWindowClosed(False)  # the tray keeps us alive

    try:
        nib = NibApp(app, config)
    except Exception as exc:
        log.exception("failed to start")
        QMessageBox.critical(
            None,
            "Nib",
            f"Nib couldn't start.\n\n{exc}\n\n"
            f"If the sprite sheet is missing, run:\n    python tools/gen_sprites.py",
        )
        return 1

    # Keep a reference so the app object is not garbage collected.
    app._nib = nib  # type: ignore[attr-defined]
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
