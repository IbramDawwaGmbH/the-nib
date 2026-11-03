"""Windows integration: taskbar geometry, monitors, DPI, fullscreen detection, startup entry.

Pure ``ctypes`` - no pywin32 dependency. Everything here is called on a timer while the mascot is
running, so every function is cheap and every Win32 call is wrapped: a failure logs at debug level
and returns a documented fallback rather than taking the app down.

A note on coordinate spaces: everything returned here is in **physical** virtual-screen pixels.
``main`` disables Qt's own high-DPI scaling so Qt widget coordinates are physical pixels too, which
keeps this module's numbers directly usable and keeps the pixel-art scale factor under our control.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import winreg
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# --- Win32 constants ----------------------------------------------------------------

ABM_GETSTATE = 0x0004
ABM_GETTASKBARPOS = 0x0005
ABS_AUTOHIDE = 0x0001

ABE_LEFT, ABE_TOP, ABE_RIGHT, ABE_BOTTOM = 0, 1, 2, 3
_EDGE_NAMES = {ABE_LEFT: "left", ABE_TOP: "top", ABE_RIGHT: "right", ABE_BOTTOM: "bottom"}

MONITOR_DEFAULTTONEAREST = 0x00000002
MONITORINFOF_PRIMARY = 0x00000001

MDT_EFFECTIVE_DPI = 0
DEFAULT_DPI = 96

DWMWA_CLOAKED = 14

_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
_PROCESS_PER_MONITOR_DPI_AWARE = 2

#: Window classes that belong to the shell itself, or are screen-covering overlays rather than
#: real fullscreen apps. A "fullscreen" desktop is not fullscreen. Observed on the target machine:
#: Windows.UI.Core.CoreWindow (Windows Input Experience) and CEF-OSC-WIDGET (NVIDIA GeForce
#: Overlay) both span the whole monitor while unzoomed and captionless, so they would otherwise
#: pass the fullscreen test and hide the mascot for no reason.
_SHELL_CLASSES = frozenset(
    {
        "Progman",
        "WorkerW",
        "Shell_TrayWnd",
        "Shell_SecondaryTrayWnd",
        "DV2ControlHost",
        "MultitaskingViewFrame",
        "XamlExplorerHostIslandWindow",
        "ForegroundStaging",
        "Windows.UI.Core.CoreWindow",
        "CEF-OSC-WIDGET",
    }
)

STRIP_MARGIN_PX = 8

_user32 = ctypes.windll.user32
_shell32 = ctypes.windll.shell32


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (int(self.left), int(self.top), int(self.right), int(self.bottom))


class _APPBARDATA(ctypes.Structure):
    """Field order and types matter - ctypes handles the 64-bit padding after ``cbSize``."""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uCallbackMessage", wintypes.UINT),
        ("uEdge", wintypes.UINT),
        ("rc", _RECT),
        ("lParam", ctypes.c_ssize_t),  # LPARAM is pointer-sized
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(_RECT), ctypes.c_ssize_t
)

_shell32.SHAppBarMessage.argtypes = [wintypes.DWORD, ctypes.POINTER(_APPBARDATA)]
_shell32.SHAppBarMessage.restype = ctypes.c_size_t  # UINT_PTR: ABM_GETSTATE returns flags

_user32.EnumDisplayMonitors.argtypes = [
    wintypes.HDC,
    ctypes.POINTER(_RECT),
    _MONITORENUMPROC,
    ctypes.c_ssize_t,
]
_user32.EnumDisplayMonitors.restype = wintypes.BOOL
_user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFO)]
_user32.GetMonitorInfoW.restype = wintypes.BOOL
_user32.MonitorFromPoint.argtypes = [_POINT, wintypes.DWORD]
_user32.MonitorFromPoint.restype = wintypes.HANDLE
_user32.MonitorFromRect.argtypes = [ctypes.POINTER(_RECT), wintypes.DWORD]
_user32.MonitorFromRect.restype = wintypes.HANDLE
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_user32.GetClassNameW.restype = ctypes.c_int
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.IsIconic.restype = wintypes.BOOL
_user32.IsZoomed.argtypes = [wintypes.HWND]
_user32.IsZoomed.restype = wintypes.BOOL
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
_user32.GetCursorPos.restype = wintypes.BOOL
_user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_user32.FindWindowW.restype = wintypes.HWND


# --- data ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MonitorInfo:
    handle: int
    rect: tuple[int, int, int, int]
    work_rect: tuple[int, int, int, int]
    primary: bool

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    def contains_x(self, x: int) -> bool:
        return self.rect[0] <= x < self.rect[2]


@dataclass(frozen=True)
class TaskbarInfo:
    rect: tuple[int, int, int, int]
    edge: str
    auto_hide: bool
    visible: bool


# --- DPI ----------------------------------------------------------------------------


_dpi_awareness_set = False


def enable_dpi_awareness() -> None:
    """Opt into per-monitor DPI awareness. Idempotent, never raises.

    Must run before the Qt application is constructed. If Qt (or the manifest) already set an
    awareness mode, these calls fail harmlessly.
    """
    global _dpi_awareness_set
    if _dpi_awareness_set:
        return
    _dpi_awareness_set = True
    try:
        _user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_ssize_t]
        _user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        if _user32.SetProcessDpiAwarenessContext(
            _DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        ):
            return
    except Exception:
        log.debug("SetProcessDpiAwarenessContext unavailable", exc_info=True)
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(_PROCESS_PER_MONITOR_DPI_AWARE) == 0:
            return
    except Exception:
        log.debug("SetProcessDpiAwareness unavailable", exc_info=True)
    try:
        _user32.SetProcessDPIAware()
    except Exception:
        log.debug("SetProcessDPIAware unavailable", exc_info=True)


def get_dpi_for_monitor(mon: MonitorInfo) -> int:
    """Effective DPI for ``mon``, or 96 if the platform cannot say."""
    try:
        dpi_x = wintypes.UINT()
        dpi_y = wintypes.UINT()
        hresult = ctypes.windll.shcore.GetDpiForMonitor(
            wintypes.HANDLE(mon.handle),
            MDT_EFFECTIVE_DPI,
            ctypes.byref(dpi_x),
            ctypes.byref(dpi_y),
        )
        if hresult == 0 and dpi_x.value:
            return int(dpi_x.value)
    except Exception:
        log.debug("GetDpiForMonitor failed", exc_info=True)
    return DEFAULT_DPI


def suggest_sprite_scale(dpi: int, target_height_px: int = 92, frame_h: int = 48) -> int:
    """Integer nearest-neighbour scale whose result is closest to ``target_height_px``.

    Integer-only, because a fractional scale would blur the pixel art.
    """
    scaled_target = target_height_px * max(dpi, 1) / DEFAULT_DPI
    return max(2, min(10, round(scaled_target / max(frame_h, 1))))


# --- monitors -----------------------------------------------------------------------


def list_monitors() -> list[MonitorInfo]:
    """All active monitors. Falls back to a single primary-screen entry on failure."""
    found: list[MonitorInfo] = []

    def _callback(handle, _hdc, _rect_ptr, _data):  # pragma: no cover - Win32 callback
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if _user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            found.append(
                MonitorInfo(
                    handle=int(handle),
                    rect=info.rcMonitor.as_tuple(),
                    work_rect=info.rcWork.as_tuple(),
                    primary=bool(info.dwFlags & MONITORINFOF_PRIMARY),
                )
            )
        return True

    try:
        # The callback object must outlive the call, hence the local reference.
        proc = _MONITORENUMPROC(_callback)
        _user32.EnumDisplayMonitors(None, None, proc, 0)
    except Exception:
        log.debug("EnumDisplayMonitors failed", exc_info=True)

    if not found:
        try:
            width = _user32.GetSystemMetrics(0)
            height = _user32.GetSystemMetrics(1)
            found.append(
                MonitorInfo(0, (0, 0, width, height), (0, 0, width, height), True)
            )
        except Exception:
            log.debug("GetSystemMetrics fallback failed", exc_info=True)
    return found


def _monitor_from_handle(handle: int) -> MonitorInfo | None:
    for mon in list_monitors():
        if mon.handle == handle:
            return mon
    return None


def get_monitor_for_point(x: int, y: int) -> MonitorInfo | None:
    try:
        handle = _user32.MonitorFromPoint(_POINT(int(x), int(y)), MONITOR_DEFAULTTONEAREST)
        found = _monitor_from_handle(int(handle))
        if found is not None:
            return found
    except Exception:
        log.debug("MonitorFromPoint failed", exc_info=True)
    monitors = list_monitors()
    for mon in monitors:
        if mon.contains_x(int(x)):
            return mon
    return next((m for m in monitors if m.primary), monitors[0] if monitors else None)


def get_monitor_for_rect(rect: tuple[int, int, int, int]) -> MonitorInfo | None:
    try:
        native = _RECT(*(int(v) for v in rect))
        handle = _user32.MonitorFromRect(ctypes.byref(native), MONITOR_DEFAULTTONEAREST)
        found = _monitor_from_handle(int(handle))
        if found is not None:
            return found
    except Exception:
        log.debug("MonitorFromRect failed", exc_info=True)
    centre_x = (int(rect[0]) + int(rect[2])) // 2
    centre_y = (int(rect[1]) + int(rect[3])) // 2
    return get_monitor_for_point(centre_x, centre_y)


def get_primary_monitor() -> MonitorInfo | None:
    monitors = list_monitors()
    return next((m for m in monitors if m.primary), monitors[0] if monitors else None)


# --- taskbar ------------------------------------------------------------------------


def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    if not hwnd:
        return None
    rect = _RECT()
    if _user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return rect.as_tuple()
    return None


def get_taskbar_info() -> TaskbarInfo | None:
    """Primary taskbar geometry and state, or ``None`` if the shell will not say.

    ``visible`` is ``False`` when an auto-hide taskbar is currently slid off screen. Windows has no
    direct query for that, so we compare the live ``Shell_TrayWnd`` rectangle against its monitor:
    a hidden bar leaves only a two-or-three pixel sliver on screen.
    """
    data = _APPBARDATA()
    data.cbSize = ctypes.sizeof(_APPBARDATA)
    try:
        if not _shell32.SHAppBarMessage(ABM_GETTASKBARPOS, ctypes.byref(data)):
            log.debug("ABM_GETTASKBARPOS returned false")
            return None
    except Exception:
        log.debug("SHAppBarMessage(ABM_GETTASKBARPOS) failed", exc_info=True)
        return None

    rect = data.rc.as_tuple()
    edge = _EDGE_NAMES.get(int(data.uEdge), "unknown")

    auto_hide = False
    try:
        state = _APPBARDATA()
        state.cbSize = ctypes.sizeof(_APPBARDATA)
        auto_hide = bool(_shell32.SHAppBarMessage(ABM_GETSTATE, ctypes.byref(state)) & ABS_AUTOHIDE)
    except Exception:
        log.debug("SHAppBarMessage(ABM_GETSTATE) failed", exc_info=True)

    visible = True
    if auto_hide:
        live = _window_rect(int(_user32.FindWindowW("Shell_TrayWnd", None) or 0))
        if live is not None:
            rect = live
            mon = get_monitor_for_rect(live)
            if mon is not None:
                m_l, m_t, m_r, m_b = mon.rect
                sliver = 6
                if edge == "bottom":
                    visible = live[1] < m_b - sliver
                elif edge == "top":
                    visible = live[3] > m_t + sliver
                elif edge == "left":
                    visible = live[2] > m_l + sliver
                elif edge == "right":
                    visible = live[0] < m_r - sliver
    return TaskbarInfo(rect=rect, edge=edge, auto_hide=auto_hide, visible=visible)


def get_walk_strip(mon: MonitorInfo, tb: TaskbarInfo | None) -> tuple[int, int, int]:
    """``(baseline_y, x_min, x_max)`` for ``mon`` - the line the mascot's feet rest on.

    A bottom taskbar overlapping this monitor gives its top edge. Anything else (a taskbar on
    another edge, on another monitor, or currently auto-hidden) falls back to the monitor's work
    area, which already accounts for a secondary monitor's own taskbar.
    """
    left, top, right, bottom = mon.rect
    w_left, _w_top, w_right, w_bottom = mon.work_rect
    if w_right - w_left <= 0:  # a degenerate work area would strand him in a zero-width strip
        w_left, w_right = left, right
    baseline = w_bottom

    if tb is not None and tb.edge == "bottom":
        t_left, t_top, t_right, t_bottom = tb.rect
        overlaps = t_right > left and t_left < right and t_bottom > top and t_top < bottom
        if overlaps:
            baseline = t_top if tb.visible else w_bottom

    baseline = max(top + 1, min(bottom, baseline))
    # Horizontal bounds come from the WORK area, not the monitor: that is what keeps him out from
    # behind a left- or right-docked taskbar. For a bottom taskbar the two are identical.
    margin = min(STRIP_MARGIN_PX, max(0, (w_right - w_left) // 4))
    return int(baseline), int(w_left + margin), int(w_right - margin)


# --- foreground / fullscreen --------------------------------------------------------


def _class_name(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    if _user32.GetClassNameW(wintypes.HWND(hwnd), buffer, 256):
        return buffer.value
    return ""


def _is_cloaked(hwnd: int) -> bool:
    try:
        cloaked = wintypes.DWORD(0)
        hresult = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            DWMWA_CLOAKED,
            ctypes.byref(cloaked),
            ctypes.sizeof(cloaked),
        )
        return hresult == 0 and bool(cloaked.value)
    except Exception:
        return False


def is_fullscreen_on_monitor(mon: MonitorInfo) -> bool:
    """True when a genuine fullscreen window (game, video, slideshow) covers ``mon``."""
    try:
        hwnd = int(_user32.GetForegroundWindow() or 0)
        if not hwnd or not _user32.IsWindowVisible(wintypes.HWND(hwnd)):
            return False
        if _user32.IsIconic(wintypes.HWND(hwnd)):
            return False
        if _class_name(hwnd) in _SHELL_CLASSES:
            return False

        # A maximized window is by definition not fullscreen, and rejecting it outright matters:
        # Windows reports a maximized window's rect as its WORK AREA inflated by the frame border
        # (measured here: 8 px on all four sides, e.g. work (0,0,2560,1392) -> (-8,-8,2568,1400)).
        # So on any monitor whose work area equals its full rect - an auto-hiding taskbar, or a
        # display carrying no taskbar - a merely maximized window satisfies the covering test and
        # the mascot would vanish for as long as anything is maximized.
        #
        # IsZoomed alone is the discriminator, deliberately not combined with a "has a title bar"
        # test: a maximized frameless app would defeat that pair. Erring this way is correct
        # because the costs are lopsided - a false positive hides the mascot during ordinary use,
        # whereas a false negative merely leaves him visible over a game, which the tray can fix.
        if _user32.IsZoomed(wintypes.HWND(hwnd)):
            return False

        pid = wintypes.DWORD(0)
        _user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        if int(pid.value) == os.getpid():
            return False  # never hide because of our own window

        if _is_cloaked(hwnd):
            return False

        rect = _window_rect(hwnd)
        if rect is None:
            return False

        m_left, m_top, m_right, m_bottom = mon.rect
        slack = 1
        return (
            rect[0] <= m_left + slack
            and rect[1] <= m_top + slack
            and rect[2] >= m_right - slack
            and rect[3] >= m_bottom - slack
        )
    except Exception:
        log.debug("fullscreen probe failed", exc_info=True)
        return False


_ENUMWINDOWSPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, ctypes.c_ssize_t)
_user32.EnumWindows.argtypes = [_ENUMWINDOWSPROC, ctypes.c_ssize_t]
_user32.EnumWindows.restype = wintypes.BOOL

#: A window has to be at least this big to be something he would notice hanging over him.
_LEDGE_MIN_W, _LEDGE_MIN_H = 160, 100


def find_ledge(
    baseline_y: int, x_min: int, x_max: int, sprite_h: int, reach_px: int
) -> tuple[int, int, int] | None:
    """The lowest window bottom edge hanging within reach above the mascot's strip.

    Returns ``(bottom_y, left, right)`` in physical screen pixels, or ``None`` when there is
    nothing overhead - which, measured on a real desktop, is most of the time. Of eight visible
    windows only two hung in the useful band; the rest were maximized, or on another monitor.

    **Read-only, and only rectangles.** No title is ever fetched and nothing is ever moved. This
    is also the most expensive probe in the app - a Python callback per top-level window - so it
    is called on demand from behaviour that is behind a long cooldown, never on a timer.

    Maximized windows are excluded on purpose rather than as a safety measure: their bottom edge
    sits *behind* the taskbar, so there is no visible underside to look up at.
    """
    head_y = baseline_y - sprite_h
    best: tuple[int, int, int] | None = None

    def _callback(hwnd, _param):  # pragma: no cover - Win32 callback
        nonlocal best
        try:
            handle = int(hwnd)
            if not _user32.IsWindowVisible(wintypes.HWND(handle)):
                return True
            if _user32.IsIconic(wintypes.HWND(handle)):
                return True
            if _user32.IsZoomed(wintypes.HWND(handle)):
                return True
            # Class name only - the shell's own windows are not scenery he can react to.
            if _class_name(handle) in _SHELL_CLASSES or _is_cloaked(handle):
                return True
            if _pid_of_window(handle) == os.getpid():
                return True
            rect = _window_rect(handle)
            if rect is None:
                return True
            left, top, right, bottom = rect
            if right - left < _LEDGE_MIN_W or bottom - top < _LEDGE_MIN_H:
                return True
            if right <= x_min or left >= x_max:  # not over his strip at all
                return True
            if not (head_y - reach_px <= bottom <= head_y):
                return True
            # The lowest one wins: that is the one he could most plausibly reach.
            if best is None or bottom > best[0]:
                best = (bottom, max(left, x_min), min(right, x_max))
        except Exception:
            log.debug("ledge probe failed for one window", exc_info=True)
        return True

    try:
        proc = _ENUMWINDOWSPROC(_callback)  # must outlive the call
        _user32.EnumWindows(proc, 0)
    except Exception:
        log.debug("EnumWindows failed", exc_info=True)
        return None
    return best


#: A dropped-on window has to be at least this big, or a stray tooltip or tiny utility popup
#: sitting under the drop point would count. Smaller than the ledge minimum on purpose - a normal
#: dialog is a perfectly good thing to stand on and some are narrower than 160px.
_PERCH_MIN_W, _PERCH_MIN_H = 80, 40


def find_window_at(x: int, y: int) -> tuple[int, tuple[int, int, int, int]] | None:
    """The topmost real window under a screen point, or ``None``.

    Returns ``(hwnd, rect)`` - the handle is kept only so the caller can re-check the *same*
    window later (``track_window``), never to read anything about it beyond what this function
    already reads. **Read-only, and only rectangles**, the same guarantee ``find_ledge`` makes:
    no title, no content, nothing recorded.

    ``EnumWindows`` already walks in Z-order, front to back, so the first match is the topmost one
    - exactly "whatever he'd actually land on", not an arbitrary one of however many windows happen
    to overlap that point.
    """
    found: tuple[int, tuple[int, int, int, int]] | None = None

    def _callback(hwnd, _param):  # pragma: no cover - Win32 callback
        nonlocal found
        try:
            handle = int(hwnd)
            if not _user32.IsWindowVisible(wintypes.HWND(handle)):
                return True
            if _user32.IsIconic(wintypes.HWND(handle)):
                return True
            if _class_name(handle) in _SHELL_CLASSES or _is_cloaked(handle):
                return True
            if _pid_of_window(handle) == os.getpid():
                return True  # never his own window
            rect = _window_rect(handle)
            if rect is None:
                return True
            left, top, right, bottom = rect
            if right - left < _PERCH_MIN_W or bottom - top < _PERCH_MIN_H:
                return True
            if not (left <= x < right and top <= y < bottom):
                return True
            found = (handle, rect)
            return False  # topmost match - stop walking the z-order
        except Exception:
            log.debug("window-at-point probe failed for one window", exc_info=True)
            return True

    try:
        proc = _ENUMWINDOWSPROC(_callback)  # must outlive the call
        _user32.EnumWindows(proc, 0)
    except Exception:
        log.debug("EnumWindows failed", exc_info=True)
        return None
    return found


def track_window(hwnd: int) -> tuple[int, int, int, int] | None:
    """``hwnd``'s current rectangle, or ``None`` once it is gone, minimized, or hidden.

    Used to follow a window a mascot has been dropped onto - polled on an ordinary timer rather
    than from ``find_window_at``'s one-off enumeration, since by then there is exactly one window
    to ask about rather than every top-level window on the desktop.
    """
    if not hwnd:
        return None
    try:
        if not _user32.IsWindowVisible(wintypes.HWND(hwnd)) or _user32.IsIconic(wintypes.HWND(hwnd)):
            return None
    except Exception:
        return None
    return _window_rect(hwnd)


_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010

_user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.UINT,
]
_user32.SetWindowPos.restype = wintypes.BOOL


def set_topmost(hwnd: int, topmost: bool) -> bool:
    """Toggle ``hwnd``'s always-on-top flag. Returns whether it actually took.

    This is the **one** deliberate exception to this app's "never touch another window" rule -
    see docs/BEHAVIOUR.md#he-never-moves-your-windows - and it exists only here, scoped to exactly
    this one call: no move, no resize (``SWP_NOMOVE | SWP_NOSIZE``), no stealing focus
    (``SWP_NOACTIVATE``), and only ever on the single window a mascot was deliberately dropped
    onto, never one merely walked past or read for its rectangle. Silently returns False on any
    failure - a window that has since closed, or one that refuses for its own reasons - the same
    as every other Win32 call in this module.
    """
    if not hwnd:
        return False
    try:
        insert_after = wintypes.HWND(_HWND_TOPMOST if topmost else _HWND_NOTOPMOST)
        return bool(_user32.SetWindowPos(
            wintypes.HWND(hwnd), insert_after, 0, 0, 0, 0,
            _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE,
        ))
    except Exception:
        log.debug("SetWindowPos call failed - topmost=%s hwnd=%s", topmost, hwnd, exc_info=True)
        return False


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def get_idle_seconds() -> float:
    """Seconds since the user last touched the keyboard or mouse. 0.0 if it cannot be read.

    This is a system-wide idle timer, not input monitoring: it reports *how long ago* the last
    input happened and nothing about what was typed or clicked.
    """
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not _user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        now = ctypes.windll.kernel32.GetTickCount()
        elapsed = (now - info.dwTime) & 0xFFFFFFFF  # the tick counter wraps every ~49 days
        return elapsed / 1000.0
    except Exception:
        log.debug("GetLastInputInfo failed", exc_info=True)
        return 0.0


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

#: Store apps do not own their own top-level window. The shell hosts them in an
#: ``ApplicationFrameWindow`` belonging to this process, so asking who owns the foreground window
#: names the host rather than the app - which would make Calculator, Mail, Photos and Settings all
#: look like the same program. Measured on the target machine: Calculator's frame reports
#: applicationframehost.exe (pid 28852) while its ``Windows.UI.Core.CoreWindow`` child reports
#: calculatorapp.exe (pid 27908).
_UWP_HOST = "applicationframehost.exe"

_ENUMCHILDPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, ctypes.c_ssize_t)
_user32.EnumChildWindows.argtypes = [wintypes.HWND, _ENUMCHILDPROC, ctypes.c_ssize_t]
_user32.EnumChildWindows.restype = wintypes.BOOL


def _pid_of_window(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    _user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return int(pid.value)


def _exe_of_pid(pid: int) -> str:
    """Lower-cased executable name for ``pid``, or "" if it cannot be opened."""
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION, False, wintypes.DWORD(pid)
    )
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(260)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return Path(buffer.value).name.lower()
    finally:
        kernel32.CloseHandle(handle)
    return ""


def _real_uwp_pid(hwnd: int, host_pid: int) -> int:
    """The PID of the app actually living inside a shell-hosted frame window.

    Only the window *hierarchy* and its owning process are inspected - never a window title.
    """
    found: list[int] = []

    def _callback(child, _param):  # pragma: no cover - Win32 callback
        child_pid = _pid_of_window(int(child))
        if child_pid and child_pid != host_pid:
            found.append(child_pid)
            return False  # the first foreign child is the hosted app; stop walking
        return True

    try:
        proc = _ENUMCHILDPROC(_callback)  # must outlive the call
        _user32.EnumChildWindows(wintypes.HWND(hwnd), proc, 0)
    except Exception:
        log.debug("EnumChildWindows failed", exc_info=True)
    return found[0] if found else 0


def get_foreground_process() -> str:
    """Lower-cased executable name of the foreground window, e.g. ``"chrome.exe"``.

    Deliberately the executable only. Window *titles* routinely contain document names, message
    subjects and URLs, and the mascot has no business reading those just to decide whether to wave.
    Resolving a Store app through its host window keeps to the same rule: the walk looks at the
    window tree and at process IDs, never at what any of those windows say.
    """
    try:
        hwnd = int(_user32.GetForegroundWindow() or 0)
        if not hwnd:
            return ""
        pid = _pid_of_window(hwnd)
        if not pid or pid == os.getpid():
            return ""
        name = _exe_of_pid(pid)
        if name == _UWP_HOST:
            real_pid = _real_uwp_pid(hwnd, pid)
            if real_pid and real_pid != os.getpid():
                return _exe_of_pid(real_pid) or name
        return name
    except Exception:
        log.debug("foreground process probe failed", exc_info=True)
    return ""


# --- global hotkey ------------------------------------------------------------------

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x0001, 0x0002, 0x0004, 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

_MODIFIER_NAMES = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT, "shift": MOD_SHIFT,
    "win": MOD_WIN, "super": MOD_WIN, "meta": MOD_WIN,
}
_NAMED_KEYS = {"space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D, "esc": 0x1B}


def parse_hotkey(spec: str) -> tuple[int, int] | None:
    """``"ctrl+alt+c"`` -> ``(modifiers, virtual_key)``. None if it cannot be parsed."""
    parts = [p.strip().lower() for p in str(spec).split("+") if p.strip()]
    if not parts:
        return None
    modifiers = 0
    key = None
    for part in parts:
        if part in _MODIFIER_NAMES:
            modifiers |= _MODIFIER_NAMES[part]
        elif part in _NAMED_KEYS:
            key = _NAMED_KEYS[part]
        elif len(part) == 1 and (part.isalpha() or part.isdigit()):
            key = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            key = 0x70 + int(part[1:]) - 1
        else:
            return None
    if key is None or not modifiers:
        return None  # a bare key would swallow normal typing system-wide
    return modifiers, key


def register_hotkey(hotkey_id: int, spec: str, hwnd: int = 0) -> bool:
    """Register a global hotkey, delivered as WM_HOTKEY to ``hwnd``.

    Binding it to a specific window matters for cost: a thread-level registration (hwnd 0) has to
    be caught with an application-wide native event filter, which means a Python call for *every*
    Windows message the app pumps. Targeting our own window lets Qt route it to that widget alone.
    """
    parsed = parse_hotkey(spec)
    if parsed is None:
        log.warning("could not parse hotkey %r", spec)
        return False
    modifiers, key = parsed
    try:
        _user32.RegisterHotKey.argtypes = [
            wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT
        ]
        _user32.RegisterHotKey.restype = wintypes.BOOL
        target = wintypes.HWND(hwnd) if hwnd else None
        if _user32.RegisterHotKey(target, hotkey_id, modifiers | MOD_NOREPEAT, key):
            log.info("registered global hotkey %s", spec)
            return True
        log.warning("hotkey %s is already taken by another app", spec)
    except Exception:
        log.debug("RegisterHotKey failed", exc_info=True)
    return False


def unregister_hotkey(hotkey_id: int, hwnd: int = 0) -> None:
    try:
        _user32.UnregisterHotKey(wintypes.HWND(hwnd) if hwnd else None, hotkey_id)
    except Exception:
        log.debug("UnregisterHotKey failed", exc_info=True)


def get_cursor_pos() -> tuple[int, int]:
    point = _POINT()
    try:
        if _user32.GetCursorPos(ctypes.byref(point)):
            return int(point.x), int(point.y)
    except Exception:
        log.debug("GetCursorPos failed", exc_info=True)
    return 0, 0


# --- ghost mode ---------------------------------------------------------------------

_GWL_EXSTYLE = -20
_WS_EX_TRANSPARENT = 0x00000020


def set_click_through(hwnd: int, enabled: bool) -> bool:
    """Make a window ignore the mouse entirely, or stop doing so.

    ``WS_EX_TRANSPARENT`` makes hit-testing skip the window: clicks, hovers and drags all land on
    whatever is behind it. That is a stronger and cheaper thing than an empty click mask - the mask
    still has to be recomputed per frame and still catches the pixels he occupies, whereas this is
    one flag and it covers every pixel including the contact shadow.

    Only ever applied to OUR OWN window. It cannot affect anything else on the desktop, and the
    global hotkey keeps working because keyboard shortcuts are message-based and never hit-tested.
    """
    if not hwnd:
        return False
    try:
        # GetWindowLongPtrW / SetWindowLongPtrW on 64-bit: the 32-bit variants truncate the style
        # word's pointer-sized return on Win64, which is the classic way to corrupt a window.
        getter = getattr(_user32, "GetWindowLongPtrW", _user32.GetWindowLongW)
        setter = getattr(_user32, "SetWindowLongPtrW", _user32.SetWindowLongW)
        getter.argtypes = [wintypes.HWND, ctypes.c_int]
        getter.restype = ctypes.c_ssize_t
        setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        setter.restype = ctypes.c_ssize_t

        handle = wintypes.HWND(hwnd)
        style = getter(handle, _GWL_EXSTYLE)
        wanted = style | _WS_EX_TRANSPARENT if enabled else style & ~_WS_EX_TRANSPARENT
        if wanted == style:
            return True
        setter(handle, _GWL_EXSTYLE, wanted)
        return True
    except Exception:
        log.debug("set_click_through failed", exc_info=True)
        return False


# --- run-on-login -------------------------------------------------------------------

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = "NibMascot"


def default_startup_command() -> str:
    """The command Windows should run at login, correctly quoted."""
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    executable = Path(sys.executable)
    # pythonw.exe keeps the console window from flashing at login.
    windowless = executable.with_name(executable.name.replace("python.exe", "pythonw.exe"))
    interpreter = windowless if windowless.is_file() else executable
    entry = (Path(__file__).parent / "main.py").resolve()
    return f'"{interpreter}" "{entry}"'


_instance_mutex: int | None = None
_ERROR_ALREADY_EXISTS = 183


def acquire_single_instance(name: str = "NibMascot") -> bool:
    """True if this process may run. False means another copy already holds the name.

    Two mascots would fight over ``config.json`` and the log file. The mutex is deliberately never
    released - Windows drops it when the process exits. If the check itself fails we allow the
    launch, since refusing to start is the worse failure.
    """
    global _instance_mutex
    if _instance_mutex is not None:
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, True, f"Local\\{name}")
        last_error = kernel32.GetLastError()
        if not handle:
            return True
        if last_error == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        _instance_mutex = int(handle)
        return True
    except Exception:
        log.debug("single-instance check failed", exc_info=True)
        return True


def get_launch_on_startup() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _kind = winreg.QueryValueEx(key, _RUN_VALUE)
            return bool(str(value).strip())
    except FileNotFoundError:
        return False
    except OSError:
        log.debug("could not read Run key", exc_info=True)
        return False


def set_launch_on_startup(enabled: bool, target_cmd: str | None = None) -> bool:
    """Add or remove the login entry. Returns True on success (absent counts as removed)."""
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                command = target_cmd or default_startup_command()
                winreg.SetValueEx(key, _RUN_VALUE, 0, winreg.REG_SZ, command)
                log.info("registered login entry: %s", command)
            else:
                try:
                    winreg.DeleteValue(key, _RUN_VALUE)
                    log.info("removed login entry")
                except FileNotFoundError:
                    pass  # already absent
        return True
    except OSError:
        log.warning("could not update Run key", exc_info=True)
        return False
