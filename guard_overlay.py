"""Away Guard overlay windows: one frameless, topmost, capture-excluded window per monitor.

The guard's whole visual surface lives here. Each ``QScreen`` gets a ``GuardOverlay`` that covers
the *full* screen geometry (taskbar included) and paints either a fog of the real desktop or solid
black. The overlay on the monitor the pointer was on when guarding started is the **primary**: it
hosts the ``GuardScene`` (giant eyes + Nib on stage), the PIN field, the Windows Hello button, the
backoff countdown and the post-auth CHOICE panel ("Peek N min" / "Unlock"). Secondary overlays only
carry fog and, when ``guard_scene`` provides them, a small pair of eyes.

Why the fog pipeline has the shape it has (``make_fog``):

*   The full-resolution grab is the only moment real screen content exists in Nib's memory, so it
    is shrunk to at most 48 px wide **immediately** and the full-size pixmap is dropped on the very
    next line. Nothing is ever written to disk or logged. At 48 samples across a 1920 px screen one
    sample spans 40 px, so no text survives even before the blur; strength 5 goes down to 12 px.
*   Blur and haze happen on the tiny image (a box blur over at most ~48x27 pixels costs nothing in
    pure Python), then it is scaled back up in 2x smooth steps so the result is soft gradients rather
    than bilinear streaks, and a grey haze is painted on top. You see colours and movement only.

Why ``SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)``: the fog refreshes every 3 s while
the overlay is on screen, and without the affinity each grab would capture the previous fog (and
the eyes) instead of the desktop underneath. The affinity is applied before the first show and
re-applied on every show and every native window re-creation, because Qt can recreate the HWND
behind our back (``QEvent.WinIdChange``). If it does not take (pre-2004 Windows, or the offscreen
test platform) the fog stays a static snapshot taken before the overlays were shown, rather than
grabbing itself.

Why ``Qt.Tool | WindowStaysOnTopHint`` plus a 500 ms ``reassert``: Tool keeps the overlays out of
the taskbar and Alt+Tab, and topmost is not a permanent promise on Windows (see mascot_window.py),
so ``OverlaySet`` re-raises every overlay twice a second. Only the primary ever *activates* (so the
PIN field has keyboard focus) - several windows activating in turn would fight each other - and
activation is suspended while a Windows Hello prompt is open, otherwise it would steal focus from
the prompt every half second.

Why activation is *forced* (``_force_foreground``): when the guard re-arms on its own (a peek runs
out, CHOICE times out, auto-guard), Nib is a background process and Windows refuses a plain
``SetForegroundWindow`` - the fog would be painted on top while the keyboard stayed in the app the
owner used during the peek. Attaching our input queue to the foreground thread's for the duration
of the call is the documented way around that. It is best effort: the real protection is
``guard_core`` holding back every key while a foreign window has the foreground, and a click on
the fog always activates it.

The background effects (``guard_fx``: worker bots, dust, the camera scan line and REC, the
breathing glow) are painted by ``paintEvent`` right after the fog, so they sit under the scene and
the PIN panel. Each overlay ticks its own ``BackgroundFx`` on a coarse timer that only runs while the
overlay is visible, and repaints only the small rectangles the model reports as changed - see
guard_fx's docstring for the cost model. ``guard_background_fx`` off means no model and no timer.

This module never swallows keys; the keyboard hook lives in ``guard_core`` (a separate process).
Alt+F4 that does reach an overlay is ignored by ``closeEvent`` - only ``OverlaySet.close`` can
close them.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QGuiApplication,
    QImage,
    QKeyEvent,
    QPainter,
    QPixmap,
    QScreen,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger(__name__)

# --- constants ----------------------------------------------------------------------

#: ``WDA_EXCLUDEFROMCAPTURE`` (Windows 10 2004+): the window is shown normally on the monitor but
#: is absent from every screen capture, including our own ``QScreen.grabWindow``.
WDA_NONE = 0x00
WDA_EXCLUDEFROMCAPTURE = 0x11

#: Hard ceiling on how wide the grab may be once shrunk. The plan's privacy promise.
FOG_MAX_WIDTH = 48

#: Per fog strength (1-5): (tiny width in px, box-blur passes on the tiny image, haze alpha 0-255).
#: Higher strength = fewer samples, more blur, thicker haze.
_FOG_LEVELS: dict[int, tuple[int, int, int]] = {
    1: (48, 1, 70),
    2: (36, 2, 95),
    3: (28, 2, 120),
    4: (20, 3, 145),
    5: (12, 3, 165),
}
_HAZE_RGB = (118, 120, 128)  # cool grey, reads as "fog" over both light and dark desktops

FOG_REFRESH_MS = 3000
REASSERT_MS = 500
_POINTER_POLL_MS = 100
_TYPING_IDLE_MS = 1500
_BACKOFF_TICK_MS = 250
#: How long a revealed PIN panel waits, untouched, before hiding itself again.
_CONCEAL_AFTER_MS = 20000
#: How close (physical px) the pointer must come to the auth panel to count as "near the field".
_NEAR_FIELD_PX = 120

ACCENT = "#D9552E"  # Nib's body colour (same value as bubble.ACCENT; not imported to keep this lean)

MODES = ("fog", "blackout", "choice")

#: A click on a secondary overlay must not activate it: the keyboard belongs to the primary.
WM_MOUSEACTIVATE = 0x0021
MA_NOACTIVATE = 3

# --- Win32 (display affinity) ------------------------------------------------------

if sys.platform == "win32":
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
    _user32.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowDisplayAffinity.restype = wintypes.BOOL
    # A private WinDLL for the activation calls, so these prototypes can never clash with
    # argtypes another module sets on the shared ctypes.windll function objects.
    _fg_user32 = ctypes.WinDLL("user32", use_last_error=True)
    _fg_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _fg_user32.GetForegroundWindow.argtypes = []
    _fg_user32.GetForegroundWindow.restype = wintypes.HWND
    _fg_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _fg_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _fg_user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    _fg_user32.AttachThreadInput.restype = wintypes.BOOL
    _fg_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _fg_user32.SetForegroundWindow.restype = wintypes.BOOL
    _fg_user32.BringWindowToTop.argtypes = [wintypes.HWND]
    _fg_user32.BringWindowToTop.restype = wintypes.BOOL
    _fg_user32.IsWindow.argtypes = [wintypes.HWND]
    _fg_user32.IsWindow.restype = wintypes.BOOL
    _fg_user32.IsWindowEnabled.argtypes = [wintypes.HWND]
    _fg_user32.IsWindowEnabled.restype = wintypes.BOOL
    _fg_user32.EnableWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
    _fg_user32.EnableWindow.restype = wintypes.BOOL
    _fg_user32.IsHungAppWindow.argtypes = [wintypes.HWND]
    _fg_user32.IsHungAppWindow.restype = wintypes.BOOL
    _fg_kernel32.GetCurrentThreadId.argtypes = []
    _fg_kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    _fg_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _fg_kernel32.OpenProcess.restype = wintypes.HANDLE
    _fg_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _fg_kernel32.CloseHandle.restype = wintypes.BOOL
    _fg_kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    _fg_kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
else:  # pragma: no cover - the app is Windows-only, but keep imports harmless elsewhere
    _user32 = None
    _fg_user32 = None
    _fg_kernel32 = None


def set_display_affinity(hwnd: int, affinity: int = WDA_EXCLUDEFROMCAPTURE) -> bool:
    """Apply a display affinity to OUR OWN top-level window. Returns whether it took.

    Fails (returns False, logged at debug) for a window of another process, a child window, a
    non-existent HWND (the offscreen test platform) or Windows older than 10 2004.
    """
    if not hwnd or _user32 is None:
        return False
    try:
        return bool(_user32.SetWindowDisplayAffinity(wintypes.HWND(hwnd), affinity))
    except Exception:
        log.debug("SetWindowDisplayAffinity failed hwnd=%s", hwnd, exc_info=True)
        return False


def get_display_affinity(hwnd: int) -> int | None:
    """Read a window's display affinity, or None if it cannot be read."""
    if not hwnd or _user32 is None:
        return None
    try:
        value = wintypes.DWORD(0)
        if not _user32.GetWindowDisplayAffinity(wintypes.HWND(hwnd), ctypes.byref(value)):
            return None
        return int(value.value)
    except Exception:
        log.debug("GetWindowDisplayAffinity failed hwnd=%s", hwnd, exc_info=True)
        return None


def _set_topmost(hwnd: int) -> None:
    """Re-assert topmost on one of our overlays (no move, no size, no activation)."""
    try:
        import win_integration  # lazy: keeps this module importable in isolation for tests

        win_integration.set_topmost(hwnd, True)
    except Exception:
        log.debug("set_topmost failed hwnd=%s", hwnd, exc_info=True)


def _exe_of_window(hwnd: Any) -> str:
    """The exe name owning ``hwnd``, for log lines only. Never raises."""
    try:
        pid = wintypes.DWORD(0)
        _fg_user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = _fg_kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return f"pid:{pid.value}"
        try:
            size = wintypes.DWORD(520)
            buf = ctypes.create_unicode_buffer(size.value)
            if _fg_kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value.rsplit("\\", 1)[-1]
            return f"pid:{pid.value}"
        finally:
            _fg_kernel32.CloseHandle(h)
    except Exception:
        return "?"


#: The last foreground window an ``AttachThreadInput`` was refused for (logged once per window).
_attach_refused_for = 0


def _force_foreground(hwnd: int) -> bool:
    """Make OUR OWN ``hwnd`` the foreground window even when Nib is a background process.

    ``AttachThreadInput`` to the current foreground thread lets ``SetForegroundWindow`` through
    (see the module docstring); detached again straight away. Returns whether ``hwnd`` is now
    the foreground window. Never raises.

    Not attached to a hung window's thread (``IsHungAppWindow``): attaching to a queue nobody is
    pumping can freeze Nib's own GUI thread with it. An elevated foreground window refuses the
    attach (UIPI); that is logged once per window - a click on the fog recovers either way.
    """
    global _attach_refused_for
    if not hwnd or _fg_user32 is None:
        return False
    try:
        fg = _fg_user32.GetForegroundWindow()
        if fg and int(fg) == int(hwnd):
            return True
        me = _fg_kernel32.GetCurrentThreadId()
        fg_tid = _fg_user32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = False
        if fg_tid and fg_tid != me:
            if _fg_user32.IsHungAppWindow(fg):
                log.debug("foreground window is hung - not attaching to it")
            else:
                attached = bool(_fg_user32.AttachThreadInput(me, fg_tid, True))
                if not attached and int(fg) != _attach_refused_for:
                    _attach_refused_for = int(fg)
                    log.debug("attach refused (elevated/UIPI?) fg=%s", _exe_of_window(fg))
        try:
            _fg_user32.BringWindowToTop(wintypes.HWND(hwnd))
            _fg_user32.SetForegroundWindow(wintypes.HWND(hwnd))
        finally:
            if attached:
                _fg_user32.AttachThreadInput(me, fg_tid, False)
        now = _fg_user32.GetForegroundWindow()
        return bool(now) and int(now) == int(hwnd)
    except Exception:
        log.debug("forcing the foreground failed hwnd=%s", hwnd, exc_info=True)
        return False


def _is_real_window(hwnd: int) -> bool:
    """True for a live native window (never on the offscreen test platform)."""
    if not hwnd or _fg_user32 is None:
        return False
    try:
        return bool(_fg_user32.IsWindow(wintypes.HWND(hwnd)))
    except Exception:
        return False


def _ensure_enabled(hwnd: int) -> bool:
    """Re-enable OUR OWN ``hwnd`` if something disabled it. Returns True if it had to.

    A Hello prompt parented to the overlay may disable its owner the way a modal dialog does; if
    the prompt goes away without re-enabling it (cancelled, crashed), the overlay takes no input
    at all - no click, no key - and the PIN field cannot be typed into.
    """
    if not _is_real_window(hwnd):
        return False
    try:
        if _fg_user32.IsWindowEnabled(wintypes.HWND(hwnd)):
            return False
        _fg_user32.EnableWindow(wintypes.HWND(hwnd), True)
        return True
    except Exception:
        log.debug("EnableWindow failed hwnd=%s", hwnd, exc_info=True)
        return False


def _cfg(config: Any, key: str, default: Any) -> Any:
    """Read a setting from a ``Config``, a plain dict, or nothing (tests)."""
    if config is None:
        return default
    try:
        value = config.get(key, default)
    except Exception:
        log.debug("config read failed for %s", key, exc_info=True)
        return default
    return default if value is None else value


def _clamp_strength(strength: Any) -> int:
    try:
        return max(1, min(5, int(strength)))
    except (TypeError, ValueError):
        return 5


# --- fog --------------------------------------------------------------------------------


def _box_blur(img: QImage, passes: int) -> QImage:
    """3x3 box blur, ``passes`` times, on a tiny RGB32 image. Pure Python; only ever <= 48 px wide."""
    w, h = img.width(), img.height()
    if w == 0 or h == 0 or passes <= 0:
        return img
    px = [[QColor(img.pixel(x, y)) for x in range(w)] for y in range(h)]
    rgb = [[(c.red(), c.green(), c.blue()) for c in row] for row in px]
    for _ in range(passes):
        out = []
        for y in range(h):
            row = []
            for x in range(w):
                r = g = b = n = 0
                for yy in (y - 1, y, y + 1):
                    if 0 <= yy < h:
                        src = rgb[yy]
                        for xx in (x - 1, x, x + 1):
                            if 0 <= xx < w:
                                pr, pg, pb = src[xx]
                                r += pr
                                g += pg
                                b += pb
                                n += 1
                row.append((r // n, g // n, b // n))
            out.append(row)
        rgb = out
    result = QImage(w, h, QImage.Format_RGB32)
    for y in range(h):
        for x in range(w):
            r, g, b = rgb[y][x]
            result.setPixel(x, y, QColor(r, g, b).rgb())
    return result


def _shrink(image: QImage, strength: int) -> QImage:
    """Area-average ``image`` down to the strength's tiny width (never more than FOG_MAX_WIDTH)."""
    tiny_w = min(FOG_MAX_WIDTH, _FOG_LEVELS[strength][0])
    tiny_h = max(1, round(image.height() * tiny_w / max(1, image.width())))
    # SmoothTransformation on a large downscale averages whole source areas, which is exactly the
    # "colours only" sampling we want; FastTransformation would point-sample and alias.
    return image.scaled(tiny_w, tiny_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).convertToFormat(
        QImage.Format_RGB32
    )


def _haze_pixmap(size: QSize, strength: int) -> QPixmap:
    """A plain grey fog for when there is nothing (or nothing safe) to grab."""
    pix = QPixmap(size if size.isValid() and not size.isEmpty() else QSize(1, 1))
    pix.fill(QColor(*_HAZE_RGB))
    return pix


def _fog_from_tiny(tiny: QImage, size: QSize, strength: int) -> QPixmap:
    """Blur the tiny image, scale it up softly to ``size`` and lay the grey haze over it."""
    _, passes, haze_alpha = _FOG_LEVELS[strength]
    tiny = _box_blur(tiny, passes)
    # Progressive 2x smooth upscaling: one huge bilinear jump from 12 px leaves visible cross-shaped
    # streaks; doubling step by step lets each pass interpolate the previous one into a gradient.
    img = tiny
    while img.width() * 2 < size.width() and img.height() * 2 < size.height():
        img = img.scaled(img.width() * 2, img.height() * 2, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    img = img.scaled(size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    pix = QPixmap.fromImage(img)
    painter = QPainter(pix)
    try:
        painter.fillRect(pix.rect(), QColor(*_HAZE_RGB, haze_alpha))
    finally:
        painter.end()
    return pix


def fog_from_pixmap(pixmap: QPixmap | QImage, size: QSize, strength: int) -> QPixmap:
    """Turn any pixmap into fog of ``size``. The pure half of ``make_fog`` (used by tests).

    The caller should drop its reference to ``pixmap`` afterwards; this function only ever keeps
    the shrunk copy.
    """
    strength = _clamp_strength(strength)
    if pixmap is None or pixmap.isNull():
        return _haze_pixmap(size, strength)
    image = pixmap.toImage() if isinstance(pixmap, QPixmap) else pixmap
    tiny = _shrink(image, strength)
    del image
    return _fog_from_tiny(tiny, size, strength)


def make_fog(screen: QScreen, strength: int) -> QPixmap:
    """Grab ``screen``, shrink to <= 48 px wide at once, discard the grab, return blurred hazy fog.

    Never raises and never touches disk: any failure (offscreen platform, a screen that vanished
    mid-grab) yields a plain grey haze of the screen's size.
    """
    strength = _clamp_strength(strength)
    size = screen.geometry().size() if screen is not None else QSize(1, 1)
    try:
        grab = screen.grabWindow(0)
        if grab.isNull():
            return _haze_pixmap(size, strength)
        image = grab.toImage()
        del grab  # the full-resolution desktop: gone as soon as we have something to shrink
        tiny = _shrink(image, strength)
        del image  # ...and the full-resolution QImage copy too
    except Exception:
        log.debug("fog grab failed", exc_info=True)
        return _haze_pixmap(size, strength)
    return _fog_from_tiny(tiny, size, strength)


# --- scene hosting --------------------------------------------------------------------


class _PlaceholderScene(QLabel):
    """Stands in for ``guard_scene.GuardScene`` when it is missing or fails to build."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Nib is guarding this screen.", parent)
        self.setAlignment(Qt.AlignCenter)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setStyleSheet("color: rgba(255,255,255,200); background: transparent;")

    def set_mode(self, mode: str) -> None:
        self.setStyleSheet(
            "color: #E0302A; background: transparent;"
            if mode == "blackout"
            else "color: rgba(255,255,255,200); background: transparent;"
        )

    def notify(self, kind: str) -> None:
        pass


def build_scene(sheet: Any, config: Any) -> QWidget:
    """Build the primary monitor's ``GuardScene``, or a placeholder if that is not possible.

    ``guard_scene`` is imported lazily so the overlay works (and can be tested) without it.
    """
    which = str(_cfg(config, "guard_scene", "both")).lower()
    show_eyes = which in ("both", "eyes")
    show_nib = which in ("both", "nib")
    if sheet is None:
        show_nib = False
    try:
        from guard_scene import GuardScene

        return GuardScene(sheet, config, show_eyes=show_eyes, show_nib=show_nib)
    except Exception:
        log.info("GuardScene unavailable - using placeholder", exc_info=True)
        return _PlaceholderScene()


def build_fx(screen: QScreen, *, primary: bool, config: Any, label: str = "") -> Any | None:
    """The background effects for one overlay, or None when ``guard_background_fx`` is off (or the
    module cannot load - the guard must never depend on its decorations)."""
    if not _cfg(config, "guard_background_fx", True):
        return None
    try:
        from guard_fx import BackgroundFx

        geo = screen.geometry() if screen is not None else QRect()
        return BackgroundFx(geo.width(), geo.height(), primary=primary,
                            started=time.monotonic(), label=label)
    except Exception:
        log.info("guard background fx unavailable", exc_info=True)
        return None


def build_mini_eyes(config: Any) -> QWidget | None:
    """Small eyes for a secondary monitor, or None (fog only) when unavailable or turned off."""
    if str(_cfg(config, "guard_scene", "both")).lower() == "nib":
        return None
    try:
        from guard_scene import MiniEyes

        return MiniEyes()
    except Exception:
        log.debug("MiniEyes unavailable - secondary overlay shows fog only", exc_info=True)
        return None


# --- one overlay ------------------------------------------------------------------------


class GuardOverlay(QWidget):
    """Covers one monitor. See the module docstring for the why of each flag."""

    pin_submitted = Signal(str)
    hello_requested = Signal()
    choice_peek = Signal()
    choice_unlock = Signal()
    typing_changed = Signal(bool)
    pointer_near_field = Signal(bool)
    #: A mouse press on any guard window in fog or blackout (seen by the primary's app-wide filter).
    owner_click = Signal()

    def __init__(
        self,
        screen: QScreen,
        *,
        primary: bool,
        scene: QWidget | None,
        config: Any,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        # Flags are set once, here: changing window flags later makes Qt recreate the native window.
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setWindowTitle("Nib Guard")
        if not primary:
            self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAutoFillBackground(False)
        self.setCursor(Qt.ArrowCursor)

        self._screen = screen
        self.primary = primary
        self._config = config
        self._mode = "fog"
        self._fog: QPixmap | None = None
        self._allow_close = False
        self._prompt_open = False
        self._affinity_ok = False
        self._last_hwnd = 0
        self._typing = False
        self._near = False
        self._backoff_until = 0.0
        self._hello_enabled = True
        self._pin_enabled = True
        self._scene: QWidget | None = None
        self._revealed = False
        #: Secondaries only: where a key typed on this monitor goes (``OverlaySet._forward_key``).
        self._key_sink: Callable[[QKeyEvent], None] | None = None

        h = max(480, screen.geometry().height())
        self._ui_px = max(14, h // 55)  # base font size in physical px

        self._build_ui(scene)
        self.setScreen(screen)
        self.setGeometry(screen.geometry())

        # Create the native window now so the affinity is in place before the first frame shows.
        self._apply_affinity()

        self._typing_timer = QTimer(self)
        self._typing_timer.setSingleShot(True)
        self._typing_timer.setInterval(_TYPING_IDLE_MS)
        self._typing_timer.timeout.connect(lambda: self._set_typing(False))

        self._backoff_timer = QTimer(self)
        self._backoff_timer.setInterval(_BACKOFF_TICK_MS)
        self._backoff_timer.timeout.connect(self._tick_backoff)

        self._pointer_timer = QTimer(self)
        self._pointer_timer.setInterval(_POINTER_POLL_MS)
        self._pointer_timer.timeout.connect(self._poll_pointer)

        self._conceal_timer = QTimer(self)
        self._conceal_timer.setSingleShot(True)
        self._conceal_timer.setInterval(_CONCEAL_AFTER_MS)
        self._conceal_timer.timeout.connect(self._conceal_auth)

        # Background effects: built (or not) once; ticked only while visible, see _tick_fx.
        self._fx = build_fx(screen, primary=primary, config=config, label="CAM 1" if primary else "")
        self._fx_timer = QTimer(self)
        # Precise, like the scene's: a coarse 50 ms timer fires every 62.5 ms on Windows' 15.6 ms
        # tick (16 FPS, not 20), and the scene has already raised the timer resolution anyway.
        self._fx_timer.setTimerType(Qt.PreciseTimer)
        self._fx_timer.timeout.connect(self._tick_fx)

        self.set_mode("fog")

    # --- construction ---------------------------------------------------------------

    def _build_ui(self, scene: QWidget | None) -> None:
        px = self._ui_px
        root = QVBoxLayout(self)
        root.setContentsMargins(px * 2, px * 2, px * 2, px * 2)
        root.setSpacing(px)

        if scene is not None:
            self.attach_scene(scene, root)

        if not self.primary:
            # Secondary monitor: fog (painted) plus, at most, the mini eyes centred by the layout.
            return

        self.setStyleSheet(
            f"""
            QWidget#guardPanel {{ background: rgba(20, 20, 24, 170); border-radius: {px}px; }}
            QLineEdit#guardPin {{
                background: rgba(0,0,0,120); color: white; border: 2px solid rgba(255,255,255,90);
                border-radius: {px // 2}px; padding: {px // 3}px {px // 2}px; font-size: {px * 2}px;
                selection-background-color: {ACCENT};
            }}
            QLineEdit#guardPin:focus {{ border-color: {ACCENT}; }}
            QLineEdit#guardPin:disabled {{ color: rgba(255,255,255,90); }}
            QPushButton {{
                background: {ACCENT}; color: white; border: none; border-radius: {px // 2}px;
                padding: {px // 2}px {px}px; font-size: {px + 2}px; font-weight: 600;
            }}
            QPushButton:hover {{ background: #E8683F; }}
            QPushButton:disabled {{ background: rgba(217,85,46,90); color: rgba(255,255,255,140); }}
            QPushButton#guardSecondary {{ background: rgba(255,255,255,40); }}
            QPushButton#guardSecondary:hover {{ background: rgba(255,255,255,70); }}
            QPushButton#guardPeek {{
                font-size: {px * 4}px; font-weight: 800; border-radius: {px}px;
                padding: {px}px {px * 4}px;
            }}
            QPushButton#guardUnlock {{
                background: transparent; color: rgba(255,255,255,55); font-size: {max(10, px - 4)}px;
                font-weight: 400; padding: {px // 4}px {px // 2}px;
            }}
            QPushButton#guardUnlock:hover {{ background: transparent; color: rgba(255,255,255,150); }}
            QLabel#guardCountdown {{ color: #FFB199; font-size: {px}px; background: transparent; }}
            QLabel#guardStatus {{ color: rgba(255,255,255,170); font-size: {px - 2}px; background: transparent; }}
            """
        )

        # Auth panel: PIN field + Hello button + backoff countdown.
        self._auth_panel = QWidget(self)
        self._auth_panel.setObjectName("guardPanel")
        auth = QVBoxLayout(self._auth_panel)
        auth.setContentsMargins(px, px, px, px)
        auth.setSpacing(px // 2)
        row = QHBoxLayout()
        row.setSpacing(px)
        self._pin = QLineEdit(self._auth_panel)
        self._pin.setObjectName("guardPin")
        self._pin.setEchoMode(QLineEdit.Password)
        self._pin.setMaxLength(64)
        self._pin.setPlaceholderText("PIN")
        self._pin.setAlignment(Qt.AlignCenter)
        self._pin.setMinimumWidth(px * 14)
        self._pin.setContextMenuPolicy(Qt.NoContextMenu)  # no paste-from-menu, no "show password"
        self._pin.returnPressed.connect(self._submit_pin)
        self._pin.textEdited.connect(self._on_text_edited)
        row.addWidget(self._pin)
        self._hello_btn = QPushButton("Windows Hello", self._auth_panel)
        self._hello_btn.setCursor(Qt.PointingHandCursor)
        # Like the unlock link: a click works, but the keyboard focus never lands here, so the
        # keys of a PIN can never go to a button that ignores them.
        self._hello_btn.setFocusPolicy(Qt.NoFocus)
        self._hello_btn.clicked.connect(self.hello_requested.emit)
        row.addWidget(self._hello_btn)
        auth.addLayout(row)
        # Only when there is no stored PIN hash (cleared by hand mid-guard, or a guard resumed after
        # a crash from before every guard had a PIN): say what still works. See set_auth.
        self._no_pin = QLabel("No guard PIN set: use Windows Hello, or Ctrl+Alt+Del then Lock",
                              self._auth_panel)
        self._no_pin.setObjectName("guardStatus")
        self._no_pin.setAlignment(Qt.AlignCenter)
        self._no_pin.hide()
        auth.addWidget(self._no_pin)
        self._countdown = QLabel("", self._auth_panel)
        self._countdown.setObjectName("guardCountdown")
        self._countdown.setAlignment(Qt.AlignCenter)
        self._countdown.hide()
        auth.addWidget(self._countdown)
        root.addWidget(self._auth_panel, 0, Qt.AlignHCenter)

        # Choice panel: shown after a successful auth. Peek is the big, obvious answer; a full
        # Unlock is deliberately a faint little link up in the corner, so someone watching over a
        # shoulder sees only "Peek" - and the owner, who knows it is there, still has it.
        self._choice_panel = QWidget(self)
        self._choice_panel.setObjectName("guardPanel")
        choice = QHBoxLayout(self._choice_panel)
        choice.setContentsMargins(px, px, px, px)
        self._peek_btn = QPushButton(self._peek_label(), self._choice_panel)
        self._peek_btn.setObjectName("guardPeek")
        self._peek_btn.setCursor(Qt.PointingHandCursor)
        self._peek_btn.setDefault(True)
        self._peek_btn.clicked.connect(self.choice_peek.emit)
        choice.addWidget(self._peek_btn)
        self._choice_panel.hide()
        root.addWidget(self._choice_panel, 0, Qt.AlignHCenter)

        # Not in the layout: pinned to the top-right corner by _place_unlock.
        self._unlock_btn = QPushButton("unlock", self)
        self._unlock_btn.setObjectName("guardUnlock")
        self._unlock_btn.setCursor(Qt.PointingHandCursor)
        self._unlock_btn.setFocusPolicy(Qt.NoFocus)  # Enter/Space must never pick it by accident
        self._unlock_btn.clicked.connect(self.choice_unlock.emit)
        self._unlock_btn.hide()

        self._status = QLabel("", self)
        self._status.setObjectName("guardStatus")
        self._status.setAlignment(Qt.AlignCenter)
        root.addWidget(self._status, 0, Qt.AlignHCenter)

    def _peek_label(self) -> str:
        minutes = _cfg(self._config, "guard_peek_minutes", 2)
        try:
            minutes = float(minutes)
        except (TypeError, ValueError):
            minutes = 3.0
        shown = str(int(minutes)) if minutes == int(minutes) else f"{minutes:g}"
        return f"Peek {shown} min"

    def _place_unlock(self) -> None:
        """Pin the small Unlock link to the top-right corner."""
        btn = self._unlock_btn
        btn.adjustSize()
        margin = self._ui_px
        btn.move(self.width() - btn.width() - margin, margin)
        btn.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        fx = getattr(self, "_fx", None)  # Qt may deliver a resize before __init__ is done
        if fx is not None:
            fx.set_size(self.width(), self.height())
        if self.primary:
            self._place_unlock()

    def attach_scene(self, scene: QWidget, layout: QVBoxLayout | None = None) -> None:
        """Host ``scene`` (GuardScene on the primary, MiniEyes on a secondary) in the centre."""
        layout = layout or self.layout()
        self._scene = scene
        scene.setParent(self)
        if self.primary:
            layout.insertWidget(0, scene, 1)
        else:
            # Mini eyes: a fixed share of the monitor, centred.
            side = max(80, self._screen.geometry().height() // 5)
            scene.setMinimumSize(side * 2, side)
            layout.addStretch(1)
            layout.addWidget(scene, 0, Qt.AlignCenter)
            layout.addStretch(1)
        scene.show()

    def take_scene(self) -> QWidget | None:
        """Detach the hosted scene (before this overlay is destroyed) so it can be re-hosted."""
        scene, self._scene = self._scene, None
        if scene is not None:
            layout = self.layout()
            if layout is not None:
                layout.removeWidget(scene)
            scene.setParent(None)
        return scene

    @property
    def scene(self) -> QWidget | None:
        return self._scene

    @property
    def screen_ref(self) -> QScreen:
        return self._screen

    # --- contract API ----------------------------------------------------------------

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """"fog" | "blackout" | "choice". Blackout drops the fog and paints solid black."""
        if mode not in MODES:
            log.warning("unknown overlay mode %r", mode)
            return
        old, self._mode = self._mode, mode
        if self._fx is not None:
            self._fx.set_mode(mode)
        if mode == "blackout":
            self._fog = None  # nothing derived from the desktop is kept while blacked out
        if self.primary:
            auth_visible = mode in ("fog", "blackout")
            if not auth_visible or old == "choice":
                self._revealed = False  # every fresh guard starts with the PIN panel hidden
            self._auth_panel.setVisible(auth_visible and self._revealed)
            self._choice_panel.setVisible(mode == "choice")
            self._unlock_btn.setVisible(mode == "choice")
            if mode == "choice":
                self._peek_btn.setText(self._peek_label())
                self._place_unlock()
                self._set_typing(False)
                self._pin.clear()
                self._peek_btn.setFocus()
            elif auth_visible and self.isVisible():
                self._focus_pin()
        # The scene's own modes are "fog" | "blackout" | "peek_return" | "unlock"; reaching CHOICE
        # means auth just succeeded, which is the scene's "unlock" (happy eyes, cheering Nib).
        scene_mode = {"fog": "fog", "blackout": "blackout", "choice": "unlock"}[mode]
        if self._scene is not None and old != mode and hasattr(self._scene, "set_mode"):
            try:
                self._scene.set_mode(scene_mode)
            except Exception:
                log.debug("scene.set_mode(%s) failed", scene_mode, exc_info=True)
        self.update()

    def set_status(self, text: str) -> None:
        if self.primary:
            self._status.setText(text)

    def set_auth(self, *, hello: bool, pin: bool, backoff_s: float) -> None:
        """Which auth methods are offered, and how long they stay disabled after a wrong attempt."""
        if not self.primary:
            return
        self._hello_enabled = bool(hello)
        self._pin_enabled = bool(pin)
        self._hello_btn.setVisible(self._hello_enabled)
        self._pin.setVisible(self._pin_enabled)
        self._no_pin.setText(
            "No guard PIN set: use Windows Hello, or Ctrl+Alt+Del then Lock" if self._hello_enabled
            else "No guard PIN set: Ctrl+Alt+Del, Lock, then sign in to Windows")
        self._no_pin.setVisible(not self._pin_enabled)
        self._backoff_until = time.monotonic() + max(0.0, float(backoff_s))
        self._tick_backoff()
        if backoff_s > 0:
            self._backoff_timer.start()

    def refresh_fog(self) -> None:
        """Re-grab this monitor into fog. No-op in blackout, and while visible without the affinity
        (the grab would be of our own fog)."""
        if self._mode == "blackout":
            return
        if self.isVisible() and not self._affinity_ok:
            return
        strength = _clamp_strength(_cfg(self._config, "guard_fog_strength", 5))
        self.set_fog(make_fog(self._screen, strength))

    def set_fog(self, fog: QPixmap | None) -> None:
        """Install a pre-made fog (``OverlaySet`` grabs every screen before showing any overlay)."""
        if self._mode == "blackout":
            return
        self._fog = fog
        self.update()

    def reassert(self, *, force: bool = False) -> None:
        """Topmost again, geometry again, display affinity again; the primary also takes focus.

        While a Hello prompt is open this normally does nothing beyond the affinity/geometry
        upkeep. ``force`` (a foreign window just took the foreground) re-topmosts anyway, without
        activating anything, so that window cannot sit above the fog for as long as the prompt
        stays open. On the unparented-prompt path that can cover the prompt too; the service
        cancels the pending prompt in the same breath, so nothing is left waiting behind us.
        """
        if not self.isVisible():
            return
        if self.hwnd() != self._last_hwnd or not self._affinity_ok:
            self._apply_affinity()
        geo = self._screen.geometry()
        if self.geometry() != geo:
            self.setGeometry(geo)
        if self._prompt_open:
            if force:
                _set_topmost(self.hwnd())  # SWP_NOACTIVATE: the prompt keeps the keyboard
            return  # a Hello prompt is up: no raising over it, no focus stealing
        self.ensure_enabled()
        self.raise_()
        _set_topmost(self.hwnd())
        if self.primary and not self.isActiveWindow():
            _force_foreground(self.hwnd())
            self.activateWindow()
            if self._mode in ("fog", "blackout"):
                self._focus_pin()
        if (self.primary and self._revealed and self._mode in ("fog", "blackout")
                and QApplication.focusWidget() is not self._pin):
            # Active, but the focus wandered off the field (Tab, a click on a button): typing
            # would go nowhere. Put it back.
            self._focus_pin()

    def hwnd(self) -> int:
        try:
            return int(self.winId())
        except Exception:
            return 0

    def take_keyboard(self) -> None:
        """Primary, after a click on any guard window: end with a PIN field that takes keys.

        Done even while a Hello prompt is marked open - the click on the fog means "I'll type
        instead" (the service cancels the prompt first).
        """
        if not self.primary or not self.isVisible() or self._mode not in ("fog", "blackout"):
            return
        if _ensure_enabled(self.hwnd()):
            log.warning("guard: primary overlay was disabled (Hello owner?) - re-enabled")
        self.raise_()
        _set_topmost(self.hwnd())
        _force_foreground(self.hwnd())
        self.activateWindow()
        self.reveal_auth()
        if QApplication.focusWidget() is not self._pin and self._pin.isVisible() \
                and self._pin.isEnabled():
            self._pin.setFocus(Qt.MouseFocusReason)

    def keyboard_state(self) -> dict[str, Any]:
        """Everything that must hold for a typed PIN to land in the field (diagnostics).

        Native checks read ``None`` where there is no real window (the offscreen test platform).
        """
        hwnd = self.hwnd()
        real = _is_real_window(hwnd)
        state: dict[str, Any] = {
            "field": bool(self.primary and ((self._pin.isVisible() and self._pin.isEnabled())
                                            or self._in_backoff())),
            "focus": bool(self.primary) and QApplication.focusWidget() is self._pin,
            "active": self.isActiveWindow(),
            # An app-modal dialog of Nib's own blocks every input to the overlays.
            "modal": QApplication.activeModalWidget() is None,
            "foreground": None,
            "enabled": None,
        }
        if real:
            try:
                fg = _fg_user32.GetForegroundWindow()
                state["foreground"] = bool(fg) and int(fg) == hwnd
                if not state["foreground"] and fg:
                    state["fg_exe"] = _exe_of_window(fg)
                state["enabled"] = bool(_fg_user32.IsWindowEnabled(wintypes.HWND(hwnd)))
            except Exception:
                log.debug("keyboard_state native checks failed", exc_info=True)
        return state

    def set_key_sink(self, sink: Callable[[QKeyEvent], None] | None) -> None:
        self._key_sink = sink

    @property
    def revealed(self) -> bool:
        return self._revealed

    # --- extensions beyond the contract ----------------------------------------------

    def set_prompt_open(self, open_: bool, *, untopmost: bool = False) -> None:
        """While a Windows Hello prompt is open, stop re-raising/activating; optionally drop
        topmost too (``guard_auth.HELLO_NEEDS_UNTOPMOST``)."""
        self._prompt_open = bool(open_)
        if open_ and untopmost:
            try:
                import win_integration

                win_integration.set_topmost(self.hwnd(), False)
            except Exception:
                log.debug("drop topmost failed", exc_info=True)
        elif not open_:
            self.reassert()

    def ensure_enabled(self) -> None:
        """See ``_ensure_enabled``. Only while no prompt is open: an open one may disable us."""
        if not self._prompt_open and _ensure_enabled(self.hwnd()):
            log.warning("guard: %s overlay was disabled (Hello owner?) - re-enabled",
                        "primary" if self.primary else "secondary")

    def allow_close(self) -> None:
        self._allow_close = True

    @property
    def fx(self) -> Any | None:
        """The ``guard_fx.BackgroundFx`` painted under the scene, or None (setting off)."""
        return self._fx

    def set_fx_info(self, *, started: float, label: str) -> None:
        """When the guard began (the REC clock, shared by every monitor) and this camera's name."""
        if self._fx is not None:
            self._fx.started = started
            self._fx.set_label(label)

    @property
    def affinity_ok(self) -> bool:
        return self._affinity_ok

    # --- internals ---------------------------------------------------------------------

    def _apply_affinity(self) -> None:
        hwnd = self.hwnd()
        self._last_hwnd = hwnd
        self._affinity_ok = set_display_affinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
        if not self._affinity_ok:
            log.debug("display affinity not applied (hwnd=%s); fog stays a static snapshot", hwnd)

    def _tick_fx(self) -> None:
        """Advance the background effects and repaint only what they changed."""
        fx = self._fx
        if fx is None:
            self._fx_timer.stop()
            return
        now = time.monotonic()
        rects, full = fx.step(now, QPointF(self.mapFromGlobal(QCursor.pos())))
        if full:
            self.update()
        else:
            # One synchronous repaint per group, deliberately not update(): Qt would merge the
            # groups into one region, and Windows blits a region's whole bounding box - a strip
            # at the top plus one at the bottom would cost a full-screen blit. See guard_fx.
            for rect in rects:
                self.repaint(rect)
        want = fx.frame_ms(now)
        if self._fx_timer.interval() != want:
            self._fx_timer.setInterval(want)

    def _note_input(self) -> None:
        if self._fx is not None:
            self._fx.note_input(time.monotonic())

    def _focus_pin(self) -> None:
        if self.primary and self._pin.isVisible() and self._pin.isEnabled():
            self._pin.setFocus(Qt.OtherFocusReason)

    # The PIN panel stays hidden until a mouse click anywhere on a guard window, so a passer-by sees
    # no field to try, and hides again after a quiet spell. The click also takes the keyboard back
    # (``owner_click`` -> ``take_keyboard``), on whichever monitor it lands.

    def reveal_auth(self) -> None:
        if not self.primary or self._mode not in ("fog", "blackout"):
            return
        self._revealed = True
        self._auth_panel.show()
        self._focus_pin()
        self._conceal_timer.start()

    def _conceal_auth(self) -> None:
        if self._typing or self._pin.text() or self._mode not in ("fog", "blackout"):
            self._conceal_timer.start()  # someone is mid-PIN: look again later
            return
        self._revealed = False
        self._auth_panel.hide()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        # App-wide, so clicks on the scene (which handles its own presses) count too.
        if event.type() == QEvent.MouseButtonPress and isinstance(obj, QWidget):
            win = obj.window()
            if isinstance(win, GuardOverlay):
                self._note_input()
                self.reveal_auth()
                # Not for the Hello button: that click opens a prompt, which must keep the keyboard.
                if (win.mode in ("fog", "blackout") and self._mode in ("fog", "blackout")
                        and obj is not getattr(self, "_hello_btn", None)):
                    self.owner_click.emit()
        return False

    def _submit_pin(self) -> None:
        text = self._pin.text()
        self._pin.clear()
        self._set_typing(False)
        if text and self._pin_enabled and not self._in_backoff():
            self.pin_submitted.emit(text)

    def _on_text_edited(self, text: str) -> None:
        self._note_input()
        self._conceal_timer.start()
        if text:
            self._set_typing(True)
            self._typing_timer.start()
        else:
            self._set_typing(False)

    def _set_typing(self, typing: bool) -> None:
        if typing == self._typing:
            return
        self._typing = typing
        if not typing:
            self._typing_timer.stop()
        self.typing_changed.emit(typing)

    def _in_backoff(self) -> bool:
        return time.monotonic() < self._backoff_until

    def _tick_backoff(self) -> None:
        remaining = self._backoff_until - time.monotonic()
        locked = remaining > 0
        self._pin.setEnabled(not locked)
        self._hello_btn.setEnabled(not locked)
        if locked:
            secs = int(remaining + 0.999)
            self._countdown.setText(f"Too many tries. Wait {secs} s.")
            self._countdown.show()
        else:
            self._backoff_timer.stop()
            self._countdown.hide()
            self._countdown.setText("")
            if self.isVisible() and self._mode in ("fog", "blackout"):
                self._focus_pin()

    def _poll_pointer(self) -> None:
        if not self.primary or not self._auth_panel.isVisible():
            near = False
        else:
            top_left = self._auth_panel.mapToGlobal(QPoint(0, 0))
            rect = QRect(top_left, self._auth_panel.size()).adjusted(
                -_NEAR_FIELD_PX, -_NEAR_FIELD_PX, _NEAR_FIELD_PX, _NEAR_FIELD_PX
            )
            near = rect.contains(QCursor.pos())
        if near != self._near:
            self._near = near
            self.pointer_near_field.emit(near)

    # --- Qt overrides ----------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        try:
            fx = self._fx
            fog = None if self._mode == "blackout" else self._fog
            # With effects on, the fx layer paints the fog itself, composed with its glow and tint
            # into one cached picture (see guard_fx) - then the bots, dust and camera on top.
            if fx is None or not fx.paint_base(p, event.rect(), fog):
                if self._mode == "blackout":
                    p.fillRect(self.rect(), Qt.black)
                elif self._fog is not None and not self._fog.isNull():
                    p.drawPixmap(self.rect(), self._fog)
                else:
                    p.fillRect(self.rect(), QColor(*_HAZE_RGB))
            if fx is not None:
                fx.paint(p, event.rect())
        finally:
            p.end()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._apply_affinity()
        if self._fx is not None:
            # Fresh clock after a Peek, not a leap. Only the model is stepped here: the expose
            # that is about to happen paints everything, so no repaint() from inside showEvent.
            now = time.monotonic()
            self._fx.pause()
            self._fx.step(now, QPointF(self.mapFromGlobal(QCursor.pos())))
            self._fx_timer.start(self._fx.frame_ms(now))
        if self.primary:
            self._pointer_timer.start()
            QApplication.instance().installEventFilter(self)
            if self._mode in ("fog", "blackout"):
                self._focus_pin()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._pointer_timer.stop()
        self._fx_timer.stop()  # a hidden overlay (Peek) spends nothing on decorations
        if self.primary:
            QApplication.instance().removeEventFilter(self)
            self._conceal_timer.stop()
        self._set_typing(False)
        if self._near:
            self._near = False
            self.pointer_near_field.emit(False)

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.WinIdChange:
            # Qt recreated the native window: the new HWND has no affinity yet.
            self._apply_affinity()
        return super().event(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._allow_close:
            super().closeEvent(event)
        else:
            event.ignore()  # Alt+F4 that slipped past the hook does nothing

    def keyPressEvent(self, event) -> None:  # noqa: N802
        self._note_input()
        event.accept()  # Esc and friends do nothing on the overlay itself
        if not self.primary and self._key_sink is not None:
            # This monitor got the keyboard anyway (activated before WM_MOUSEACTIVATE could say
            # no): hand the key to the PIN field rather than drop it.
            try:
                self._key_sink(event)
            except Exception:
                log.debug("forwarding a key to the primary failed", exc_info=True)

    def nativeEvent(self, event_type, message):  # noqa: N802
        if not self.primary and sys.platform == "win32":
            try:
                if bytes(event_type) == b"windows_generic_MSG":
                    msg = wintypes.MSG.from_address(int(message))
                    if msg.message == WM_MOUSEACTIVATE:
                        # The click is still delivered to Qt (it reveals the field and takes the
                        # keyboard for the primary); this window just never becomes active.
                        return True, MA_NOACTIVATE
            except Exception:
                pass
        try:
            return super().nativeEvent(event_type, message)
        except Exception:
            return False, 0


# --- all monitors ------------------------------------------------------------------------


class OverlaySet(QObject):
    """One ``GuardOverlay`` per monitor, kept in sync with hot-plugged screens.

    Re-emits the primary overlay's signals, so the service connects once. Remembers mode, status
    and auth state so an overlay created for a newly plugged-in monitor (or a primary rebuilt after
    its monitor was unplugged) comes up in the same state.
    """

    pin_submitted = Signal(str)
    hello_requested = Signal()
    choice_peek = Signal()
    choice_unlock = Signal()
    typing_changed = Signal(bool)
    pointer_near_field = Signal(bool)
    #: The set of overlay HWNDs changed (show / hot-plug); resend ``overlay_hwnds`` to the core.
    hwnds_changed = Signal(list)
    #: A click on any guard window in fog or blackout: the service takes the keyboard back.
    owner_click = Signal()

    def __init__(
        self,
        config: Any,
        *,
        sheet: Any = None,
        scene: QWidget | None = None,
        scene_factory: Callable[[], QWidget] | None = None,
        mini_factory: Callable[[], QWidget | None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._sheet = sheet
        self._scene = scene
        self._scene_factory = scene_factory or (lambda: build_scene(self._sheet, self._config))
        self._mini_factory = mini_factory or (lambda: build_mini_eyes(self._config))
        self._overlays: dict[QScreen, GuardOverlay] = {}
        self._primary_screen: QScreen | None = None
        self._mode = "fog"
        self._status = ""
        self._auth: dict[str, Any] = {"hello": True, "pin": True}
        #: monotonic() when the current backoff ends, so a rebuilt primary shows what is left.
        self._backoff_until = 0.0
        self._prompt: tuple[bool, bool] = (False, False)
        self._visible = False
        #: When this guard began (monotonic): the REC clock in every monitor's corner. Survives
        #: Peek (the overlays are only hidden), reset by ``close``.
        self._started: float | None = None

        self._fog_timer = QTimer(self)
        self._fog_timer.setInterval(FOG_REFRESH_MS)
        self._fog_timer.timeout.connect(self.refresh_fog)
        self._reassert_timer = QTimer(self)
        self._reassert_timer.setInterval(REASSERT_MS)
        self._reassert_timer.timeout.connect(self.reassert)

        app = QGuiApplication.instance()
        if app is not None:
            app.screenAdded.connect(self._on_screen_added)
            app.screenRemoved.connect(self._on_screen_removed)

    # --- lifecycle -------------------------------------------------------------------

    @property
    def scene(self) -> QWidget | None:
        """The GuardScene (or placeholder) on the primary monitor, built on first ``show``."""
        return self._scene

    @property
    def primary(self) -> GuardOverlay | None:
        return self._overlays.get(self._primary_screen) if self._primary_screen else None

    @property
    def overlays(self) -> list[GuardOverlay]:
        return list(self._overlays.values())

    @property
    def visible(self) -> bool:
        return self._visible

    def show(self, mode: str = "fog", primary_screen: QScreen | None = None) -> None:
        """Cover every monitor. Fog is grabbed for all screens *before* any overlay appears."""
        self._mode = mode
        if self._started is None:
            self._started = time.monotonic()
        if not self._overlays:
            self._primary_screen = primary_screen or self._pointer_screen()
            for screen in QGuiApplication.screens():
                self._create(screen)
        elif primary_screen is not None and primary_screen in self._overlays \
                and primary_screen is not self._primary_screen:
            self._move_primary(primary_screen)
        if mode != "blackout":
            strength = _clamp_strength(_cfg(self._config, "guard_fog_strength", 5))
            fogs = {s: make_fog(s, strength) for s in self._overlays}  # nothing of ours is visible yet
            for s, overlay in self._overlays.items():
                overlay.set_fog(fogs[s])
        for overlay in self._overlays.values():
            overlay.set_mode(mode)
            overlay.show()  # plain show, not showFullScreen: the geometry is already exact
        primary = self.primary
        if primary is not None:
            primary.raise_()
            _force_foreground(primary.hwnd())
            primary.activateWindow()
        self._visible = True
        self._fog_timer.start()
        self._reassert_timer.start()
        self.reassert()
        self.hwnds_changed.emit(self.hwnds())

    def hide(self) -> None:
        """PEEK: hide every overlay and stop the timers; the windows are kept for the re-guard."""
        self._visible = False
        self._fog_timer.stop()
        self._reassert_timer.stop()
        for overlay in self._overlays.values():
            overlay.hide()

    def close(self) -> None:
        """UNLOCK: destroy every overlay (and the scene with the primary)."""
        self.hide()
        for overlay in list(self._overlays.values()):
            overlay.allow_close()
            overlay.close()
            overlay.deleteLater()
        self._overlays.clear()
        self._primary_screen = None
        self._scene = None
        self._started = None

    # --- broadcast ---------------------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        self._mode = mode
        for overlay in self._overlays.values():
            overlay.set_mode(mode)

    def set_status(self, text: str) -> None:
        self._status = text
        if self.primary is not None:
            self.primary.set_status(text)

    def set_auth(self, *, hello: bool, pin: bool, backoff_s: float) -> None:
        self._auth = {"hello": hello, "pin": pin}
        self._backoff_until = time.monotonic() + max(0.0, float(backoff_s))
        if self.primary is not None:
            self.primary.set_auth(hello=hello, pin=pin, backoff_s=backoff_s)

    def take_keyboard(self) -> None:
        """After Qt's own click handling: make the primary's PIN field take the keys."""
        QTimer.singleShot(0, self._take_keyboard_now)

    def _take_keyboard_now(self) -> None:
        primary = self.primary
        if primary is not None and self._visible:
            primary.take_keyboard()

    def keyboard_state(self) -> dict[str, Any]:
        primary = self.primary
        if primary is None:
            return {"field": False, "focus": False, "active": False, "primary": False}
        return primary.keyboard_state()

    def _forward_key(self, event: QKeyEvent) -> None:
        """A key typed while a secondary overlay had the keyboard: give it to the PIN field."""
        primary = self.primary
        if primary is None or not primary.revealed or primary.mode not in ("fog", "blackout"):
            return  # no field showing yet: a click reveals it, keys alone never do
        pin = primary._pin
        if pin.isVisible() and pin.isEnabled():
            copy = QKeyEvent(event.type(), event.key(), event.modifiers(), event.text(),
                             event.isAutoRepeat(), event.count())
            QApplication.sendEvent(pin, copy)
        self.take_keyboard()

    def set_prompt_open(self, open_: bool, *, untopmost: bool = False) -> None:
        """Call with True while a Windows Hello prompt is up, False when it is done."""
        self._prompt = (bool(open_), bool(untopmost))
        for overlay in self._overlays.values():
            overlay.set_prompt_open(open_, untopmost=untopmost)

    def ensure_enabled(self) -> None:
        for overlay in self._overlays.values():
            overlay.ensure_enabled()

    def refresh_fog(self) -> None:
        if not self._visible:
            return
        for overlay in self._overlays.values():
            overlay.refresh_fog()

    def reassert(self, *, force: bool = False) -> None:
        """Re-raise every overlay. ``force``: see ``GuardOverlay.reassert``."""
        if not self._visible:
            return
        # Secondaries first, primary last, so the primary ends up activated and on top.
        for overlay in self._overlays.values():
            if not overlay.primary:
                overlay.reassert(force=force)
        if self.primary is not None:
            self.primary.reassert(force=force)

    def hwnds(self) -> list[int]:
        return [o.hwnd() for o in self._overlays.values() if o.hwnd()]

    # --- internals ---------------------------------------------------------------------

    @staticmethod
    def _pointer_screen() -> QScreen | None:
        return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()

    def _create(self, screen: QScreen) -> GuardOverlay:
        is_primary = screen is self._primary_screen
        if is_primary:
            if self._scene is None:
                self._scene = self._scene_factory()
            scene = self._scene
        else:
            scene = None
            try:
                scene = self._mini_factory()
            except Exception:
                log.debug("mini eyes factory failed", exc_info=True)
        overlay = GuardOverlay(screen, primary=is_primary, scene=scene, config=self._config)
        others = [s for s in QGuiApplication.screens() if s is not self._primary_screen]
        cam = 1 if is_primary else 2 + (others.index(screen) if screen in others else len(others))
        overlay.set_fx_info(started=self._started if self._started is not None else time.monotonic(),
                            label=f"CAM {cam}")
        if is_primary:
            overlay.pin_submitted.connect(self.pin_submitted)
            overlay.hello_requested.connect(self.hello_requested)
            overlay.choice_peek.connect(self.choice_peek)
            overlay.choice_unlock.connect(self.choice_unlock)
            overlay.typing_changed.connect(self.typing_changed)
            overlay.pointer_near_field.connect(self.pointer_near_field)
            overlay.owner_click.connect(self.owner_click)
            overlay.set_status(self._status)
            # What is left of the backoff, not the whole of it again.
            overlay.set_auth(**self._auth,
                             backoff_s=max(0.0, self._backoff_until - time.monotonic()))
        else:
            overlay.set_key_sink(self._forward_key)
        overlay.set_mode(self._mode)
        if self._prompt[0]:
            overlay.set_prompt_open(True, untopmost=self._prompt[1])
        self._overlays[screen] = overlay
        return overlay

    def _destroy(self, screen: QScreen) -> None:
        overlay = self._overlays.pop(screen, None)
        if overlay is None:
            return
        if overlay.primary:
            overlay.take_scene()  # keep the scene alive for the new primary
        overlay.allow_close()
        overlay.close()
        overlay.deleteLater()

    def _move_primary(self, screen: QScreen) -> None:
        """Rebuild so that ``screen`` hosts the scene and the auth UI."""
        old = self._primary_screen
        if old is not None:
            self._destroy(old)
        self._destroy(screen)
        self._primary_screen = screen
        self._create(screen)
        if old is not None and old in QGuiApplication.screens():
            self._create(old)

    def _show_one(self, overlay: GuardOverlay) -> None:
        if not self._visible:
            return
        if self._mode != "blackout":
            overlay.set_fog(make_fog(overlay.screen_ref, _clamp_strength(
                _cfg(self._config, "guard_fog_strength", 5))))
        overlay.show()
        overlay.reassert()

    def _on_screen_added(self, screen: QScreen) -> None:
        if not self._overlays and not self._visible:
            return  # not guarding; show() will pick it up
        log.info("guard: screen added %s", screen.name())
        if self._primary_screen is None or self._primary_screen not in self._overlays:
            # Every display went away for a moment (dock, KVM): without a primary there is no
            # scene, no PIN field and no Hello button anywhere. The first screen back hosts them.
            log.info("guard: no primary overlay - %s becomes primary", screen.name())
            self._primary_screen = screen
        self._show_one(self._create(screen))
        self.hwnds_changed.emit(self.hwnds())

    def _on_screen_removed(self, screen: QScreen) -> None:
        if screen not in self._overlays:
            return
        log.info("guard: screen removed %s", screen.name())
        was_primary = screen is self._primary_screen
        self._destroy(screen)
        if was_primary:
            remaining = [s for s in QGuiApplication.screens() if s is not screen]
            if remaining:
                new_primary = QGuiApplication.primaryScreen()
                if new_primary is None or new_primary is screen:
                    new_primary = remaining[0]
                self._destroy(new_primary)
                self._primary_screen = new_primary
                self._show_one(self._create(new_primary))
            else:
                self._primary_screen = None
        self.hwnds_changed.emit(self.hwnds())
