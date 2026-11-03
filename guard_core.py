"""Away Guard core: the small separate process that owns the system-level hooks.

Why a separate process (``Nib.exe --guard-core <parent_pid>``) and why ctypes only:

* Windows silently drops a ``WH_KEYBOARD_LL`` hook whose callback is slow (``LowLevelHooksTimeout``,
  ~1 s, and after a few misses the hook is removed without notice). Nib's Qt main thread paints a
  30 FPS scene, grabs screens for the fog and hashes PINs; a hook living there would eventually
  die. Here the hook gets its own thread with its own ``GetMessageW`` pump, its callback does a
  handful of integer comparisons and posts anything interesting to a queue, and it is re-installed
  every 30 s as self-heal in case Windows dropped it anyway.
* If Nib crashes (or is killed from Task Manager) while guarding, the overlays vanish with it. This
  process waits on Nib's process handle and, if that fires while guarded, puts up plain black
  topmost Win32 windows on every monitor and swallows the keyboard until the user proves
  ownership to *Windows* (Win+L / Ctrl+Alt+Del -> lock -> unlock), or a new Nib takes over.
* No Qt import keeps the process tiny and fast to start, and means nothing here can contend with
  Qt's own message handling.

Protocol (stdin/stdout, UTF-8 JSON, one object per line, flushed after each write):

* Nib -> core: ``guard``, ``peek``, ``unlock``, ``ping``, ``overlay_hwnds`` (``hwnds: [...]``),
  ``cover``, ``uncover``, and the extra diagnostic ``status``.
* core -> Nib: ``ready``, ``pong``, ``status`` and ``event`` (``kind``, ``detail``, ``t`` = Unix time).
  Event kinds: ``blocked_key``, ``foreground_stolen``, ``desktop_switch``, ``input`` (throttled to at
  most one per 500 ms, physical keyboard/mouse only), ``session_lock``, ``session_unlock``.

Threads: the *main* thread owns a hidden window (commands, timers, WTS notifications, WinEvent
callbacks, cover windows and the power request - ``SetThreadExecutionState`` is per-thread, so it
must always be the same thread). The *hook* thread owns the LL keyboard + mouse hooks. Two small
blocking threads read stdin and wait on the parent / hand-off handles; they only ever talk to the
main thread with ``PostMessageW``. stdin/stdout use raw ``ReadFile``/``WriteFile`` on the standard
handles, so this also works in the windowed (no console, ``sys.stdin is None``) frozen build and a
blocked read never holds a Python I/O lock at interpreter shutdown.
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
import queue
import sys
import threading
import time
from ctypes import wintypes
from typing import Any

log = logging.getLogger(__name__)

# --- pointer-sized Win32 types (64-bit correct) ------------------------------------------------

LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
UINT_PTR = ctypes.c_size_t
ULONG_PTR = ctypes.c_size_t
HANDLE = wintypes.HANDLE
HWND = wintypes.HWND

# --- Win32 constants -----------------------------------------------------------------------------

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
HC_ACTION = 0
LLKHF_INJECTED = 0x10
LLKHF_ALTDOWN = 0x20
LLKHF_UP = 0x80
LLMHF_INJECTED = 0x01

VK_TAB = 0x09
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_ESCAPE = 0x1B
VK_F4 = 0x73
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_L = 0x4C
_WIN_KEYS = frozenset({VK_LWIN, VK_RWIN})

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUIT = 0x0012
WM_DISPLAYCHANGE = 0x007E
WM_TIMER = 0x0113
WM_WTSSESSION_CHANGE = 0x02B1
WM_APP = 0x8000
WM_APP_CMD = WM_APP + 1  # a parsed stdin command is waiting in the queue
WM_APP_KEY = WM_APP + 2  # the hook thread queued a blocked key / Win+L
WM_APP_STDIN_EOF = WM_APP + 3
WM_APP_PARENT_DIED = WM_APP + 4
WM_APP_HANDOFF = WM_APP + 5  # a newer core took over while we were covering as an orphan
WM_APP_RESET_FILTER = WM_APP + 6  # (hook thread) forget held keys: a desktop switch ate their key-ups

WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
NOTIFY_FOR_THIS_SESSION = 0

EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_SYSTEM_DESKTOPSWITCH = 0x0020
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SW_SHOW = 5
BLACK_BRUSH = 4
IDC_ARROW = 32512
GA_ROOT = 2

SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
INFINITE = 0xFFFFFFFF
WAIT_OBJECT_0 = 0
STD_INPUT_HANDLE = -10
STD_OUTPUT_HANDLE = -11
ERROR_BROKEN_PIPE = 109
_INVALID_HANDLE = ctypes.c_void_p(-1).value

_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4

#: Main-thread timer: drains queues, throttles ``input`` events, re-asserts cover windows.
_TICK_TIMER_ID = 1
_TICK_MS = 100
_COVER_REASSERT_S = 0.5
#: How often the hook thread re-installs its hooks (Windows may have dropped them silently).
HOOK_REINSTALL_MS = 30_000
#: Minimum spacing of ``input`` events.
INPUT_THROTTLE_S = 0.5

#: Processes whose windows may take the foreground while guarded without it being suspicious:
#: the Windows Hello / credential prompt Nib itself opens, and the lock screen.
FOREGROUND_WHITELIST_EXES = frozenset({"credentialuibroker.exe", "lockapp.exe"})

#: Named kernel objects for orphan hand-off. ``Local\\`` = per session, which is what we want.
_ORPHAN_MUTEX_NAME = "Local\\NibGuardCoreOrphanCovering"
_HANDOFF_EVENT_NAME = "Local\\NibGuardCoreHandoff"

_MAIN_CLASS = "NibGuardCoreMsg"
_COVER_CLASS = "NibGuardCoreCover"

# --- private DLL handles --------------------------------------------------------------------------
# Separate WinDLL instances (not ``ctypes.windll``) so the prototypes set below can never clash
# with argtypes another module sets on the shared function objects.

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
_wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", HWND),
        ("message", wintypes.UINT),
        ("wParam", WPARAM),
        ("lParam", LPARAM),
        ("time", wintypes.DWORD),
        ("pt", _POINT),
        ("lPrivate", wintypes.DWORD),
    ]


class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", _POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


_HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, WPARAM, LPARAM)
_WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HWND, wintypes.UINT, WPARAM, LPARAM)
_WINEVENTPROC = ctypes.WINFUNCTYPE(
    None,
    HANDLE,  # HWINEVENTHOOK
    wintypes.DWORD,  # event
    HWND,
    wintypes.LONG,  # idObject
    wintypes.LONG,  # idChild
    wintypes.DWORD,  # idEventThread
    wintypes.DWORD,  # dwmsEventTime
)
_MONITORENUMPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, HANDLE, wintypes.HDC, ctypes.POINTER(_RECT), LPARAM
)


class _WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", HANDLE),
        ("hbrBackground", HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


def _proto(fn: Any, restype: Any, *argtypes: Any) -> None:
    fn.restype = restype
    fn.argtypes = list(argtypes)


# user32
_proto(_user32.SetWindowsHookExW, HANDLE, ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
_proto(_user32.UnhookWindowsHookEx, wintypes.BOOL, HANDLE)
_proto(_user32.CallNextHookEx, LRESULT, HANDLE, ctypes.c_int, WPARAM, LPARAM)
_proto(_user32.GetMessageW, wintypes.BOOL, ctypes.POINTER(_MSG), HWND, wintypes.UINT, wintypes.UINT)
_proto(_user32.PeekMessageW, wintypes.BOOL, ctypes.POINTER(_MSG), HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT)
_proto(_user32.TranslateMessage, wintypes.BOOL, ctypes.POINTER(_MSG))
_proto(_user32.DispatchMessageW, LRESULT, ctypes.POINTER(_MSG))
_proto(_user32.PostMessageW, wintypes.BOOL, HWND, wintypes.UINT, WPARAM, LPARAM)
_proto(_user32.PostThreadMessageW, wintypes.BOOL, wintypes.DWORD, wintypes.UINT, WPARAM, LPARAM)
_proto(_user32.PostQuitMessage, None, ctypes.c_int)
_proto(_user32.SetTimer, UINT_PTR, HWND, UINT_PTR, wintypes.UINT, ctypes.c_void_p)
_proto(_user32.KillTimer, wintypes.BOOL, HWND, UINT_PTR)
_proto(_user32.DefWindowProcW, LRESULT, HWND, wintypes.UINT, WPARAM, LPARAM)
_proto(_user32.RegisterClassExW, wintypes.ATOM, ctypes.POINTER(_WNDCLASSEXW))
_proto(
    _user32.CreateWindowExW,
    HWND,
    wintypes.DWORD,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.DWORD,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    HWND,
    wintypes.HMENU,
    wintypes.HINSTANCE,
    ctypes.c_void_p,
)
_proto(_user32.DestroyWindow, wintypes.BOOL, HWND)
_proto(_user32.ShowWindow, wintypes.BOOL, HWND, ctypes.c_int)
_proto(
    _user32.SetWindowPos,
    wintypes.BOOL,
    HWND,
    ctypes.c_ssize_t,  # hWndInsertAfter: HWND_TOPMOST is (HWND)-1, so pass it as a signed int
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.UINT,
)
_proto(_user32.SetForegroundWindow, wintypes.BOOL, HWND)
_proto(_user32.GetForegroundWindow, HWND)
_proto(_user32.LoadCursorW, HANDLE, wintypes.HINSTANCE, ctypes.c_void_p)
_proto(_user32.EnumDisplayMonitors, wintypes.BOOL, wintypes.HDC, ctypes.POINTER(_RECT), _MONITORENUMPROC, LPARAM)
_proto(_user32.SetWinEventHook, HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, _WINEVENTPROC, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD)
_proto(_user32.UnhookWinEvent, wintypes.BOOL, HANDLE)
_proto(_user32.GetWindowThreadProcessId, wintypes.DWORD, HWND, ctypes.POINTER(wintypes.DWORD))
_proto(_user32.GetAncestor, HWND, HWND, wintypes.UINT)
_proto(_user32.GetAsyncKeyState, ctypes.c_short, ctypes.c_int)
_proto(_user32.LockWorkStation, wintypes.BOOL)
_proto(_user32.SetProcessDpiAwarenessContext, wintypes.BOOL, ctypes.c_ssize_t)

# kernel32
_proto(_kernel32.GetModuleHandleW, wintypes.HMODULE, wintypes.LPCWSTR)
_proto(_kernel32.GetCurrentThreadId, wintypes.DWORD)
_proto(_kernel32.SetThreadExecutionState, wintypes.DWORD, wintypes.DWORD)
_proto(_kernel32.OpenProcess, HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_proto(_kernel32.CloseHandle, wintypes.BOOL, HANDLE)
_proto(_kernel32.WaitForSingleObject, wintypes.DWORD, HANDLE, wintypes.DWORD)
_proto(_kernel32.QueryFullProcessImageNameW, wintypes.BOOL, HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
_proto(_kernel32.GetStdHandle, HANDLE, wintypes.DWORD)
_proto(_kernel32.ReadFile, wintypes.BOOL, HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
_proto(_kernel32.WriteFile, wintypes.BOOL, HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
_proto(_kernel32.CreateEventW, HANDLE, ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
_proto(_kernel32.SetEvent, wintypes.BOOL, HANDLE)
_proto(_kernel32.ResetEvent, wintypes.BOOL, HANDLE)
_proto(_kernel32.CreateMutexW, HANDLE, ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
_proto(_kernel32.OpenMutexW, HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)

# gdi32 / wtsapi32
_proto(_gdi32.GetStockObject, HANDLE, ctypes.c_int)
_proto(_wtsapi32.WTSRegisterSessionNotification, wintypes.BOOL, HWND, wintypes.DWORD)
_proto(_wtsapi32.WTSUnRegisterSessionNotification, wintypes.BOOL, HWND)


# --- key filter (pure, unit-testable) ----------------------------------------------------------------

_KEY_NAMES = {
    VK_TAB: "Tab",
    VK_ESCAPE: "Esc",
    0x0D: "Enter",
    0x20: "Space",
    0x25: "Left",
    0x26: "Up",
    0x27: "Right",
    0x28: "Down",
    0x2C: "PrintScreen",
    0xBB: "Plus",
    0xBD: "Minus",
    0xBE: "Period",
}


def key_name(vk: int) -> str:
    """Human-readable name for a virtual-key code, for the ``blocked_key`` detail."""
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk)
    if 0x70 <= vk <= 0x87:
        return f"F{vk - 0x6F}"
    return _KEY_NAMES.get(vk, f"0x{vk:02X}")


class KeyFilter:
    """The swallow/report decisions of the keyboard hook, kept free of any Win32 call.

    ``decide`` runs inside the LL hook callback, so it is a few set lookups and comparisons. It
    returns ``(swallow, report)`` where ``report`` is ``None``, ``("key", name)`` for a blocked
    escape attempt, or ``("lock", "")`` for Win+L.

    Rules while guarded:

    * Win (either) is swallowed, so Start / Win+anything never reaches the shell. The report is
      deferred to the key-up (``"Win"``) or the first combo key (``"Win+D"`` ...), so that **Win+L
      never counts as an attack**: it asks the main thread to ``LockWorkStation()`` instead, which
      is exactly what Win+L would have done (we cannot know whether Windows still sees the
      combination once we ate the Win key-down, so we do it ourselves).
    * Alt+Tab, Alt+Esc, Alt+F4, Ctrl+Esc (and Ctrl+Shift+Esc) are swallowed and reported.
    * ``swallow_all`` (orphan cover mode - Nib is gone, black windows are up) eats every key
      except Win+L, so nobody can type blind into the apps underneath the black.
    * ``foreground_ok`` False (the main thread saw a window that is neither an overlay, Nib, nor the
      Hello prompt / lock screen take the foreground) eats every other key too, *silently*. Nib is
      a background process when the guard re-arms by itself (peek ran out, CHOICE timed out,
      auto-guard), and Windows may refuse it the foreground: the fog is painted on top but the
      keyboard still goes to the terminal the owner used during the peek. Without this, a PIN typed
      at the fog - or anything typed blind by someone else - would land in that hidden app. Not
      reported: the owner typing before clicking the fog must not blackout per keystroke, and the
      ``input`` event already says "someone is here". A click on the fog activates it and lifts this.
    * A key-up is swallowed only if its key-down was: eating an up whose down got through would
      leave that key logically stuck for the rest of the system.
    * Auto-repeat downs of a swallowed key are swallowed silently (one report per press).
    """

    def __init__(self) -> None:
        self.swallow_all = False
        #: Set by the main thread from foreground events; read in the hook callback (a bool store
        #: and load are atomic under the GIL, so no lock is needed).
        self.foreground_ok = True
        self._swallowed: set[int] = set()
        self._win_down = False
        self._win_used = False

    def reset(self) -> None:
        """Forget held keys (``swallow_all`` / ``foreground_ok`` are left alone).

        Needed after Win+L and any other desktop switch: the key-ups happen on the secure desktop,
        which a ``WH_KEYBOARD_LL`` hook never sees, so without a reset ``_win_down`` would stay set
        and every later key would be eaten and reported as ``Win+<key>`` - the owner could never
        type the PIN again. (Checking ``GetAsyncKeyState(VK_LWIN)`` in the callback instead does
        not work: a key-down the hook swallowed never reaches the async key state, so a Win that
        really is held would read as up.)
        """
        self._swallowed.clear()
        self._win_down = False
        self._win_used = False

    def decide(
        self, vk: int, up: bool, alt: bool, ctrl: bool = False, shift: bool = False
    ) -> tuple[bool, tuple[str, str] | None]:
        if up:
            if vk not in self._swallowed:
                return False, None
            self._swallowed.discard(vk)
            if vk in _WIN_KEYS:
                report = None
                if self._win_down and not self._win_used:
                    report = ("key", "Win")
                if not (self._swallowed & _WIN_KEYS):
                    self._win_down = False
                    self._win_used = False
                return True, report
            return True, None

        repeat = vk in self._swallowed
        if vk in _WIN_KEYS:
            self._swallowed.add(vk)
            if not self._win_down:
                self._win_down = True
                self._win_used = False
            return True, None
        if self._win_down:
            self._swallowed.add(vk)
            if repeat:
                return True, None
            self._win_used = True
            if vk == VK_L:
                return True, ("lock", "")
            return True, ("key", "Win+" + key_name(vk))

        name = None
        if alt and vk == VK_TAB:
            name = "Alt+Tab"
        elif alt and vk == VK_ESCAPE:
            name = "Alt+Esc"
        elif alt and vk == VK_F4:
            name = "Alt+F4"
        elif ctrl and vk == VK_ESCAPE:
            name = "Ctrl+Shift+Esc" if shift else "Ctrl+Esc"
        if name is not None:
            self._swallowed.add(vk)
            return True, (None if repeat else ("key", name))
        if self.swallow_all or not self.foreground_ok:
            self._swallowed.add(vk)
            return True, None
        return False, None


# --- hook thread -------------------------------------------------------------------------------------


class _HookThread(threading.Thread):
    """Owns ``WH_KEYBOARD_LL`` + ``WH_MOUSE_LL`` and their message pump.

    The callbacks only: read the event struct, update ``last_input`` (physical input only -
    injected events from agents/automation don't make a "human is here"), run ``KeyFilter.decide``
    and, if there is something to report, put it on ``reports`` and poke the main window with
    ``PostMessageW``. Everything else happens on the main thread.

    The mouse hook exists only to know about *physical* mouse activity for the throttled
    ``input`` event; it never swallows anything.
    """

    def __init__(self, notify_hwnd: int, key_filter: KeyFilter) -> None:
        super().__init__(name="guard-core-hooks", daemon=True)
        self.notify_hwnd = notify_hwnd
        self.filter = key_filter
        self.reports: queue.SimpleQueue[tuple[str, str]] = queue.SimpleQueue()
        self.last_input = 0.0  # time.monotonic() of the last physical key/mouse event
        self.installed = threading.Event()
        self.ok = False
        self.reinstalls = 0
        self._tid = 0
        self._kb_hook: int | None = None
        self._ms_hook: int | None = None
        # Keep the ctypes thunks alive for as long as the hooks may call them.
        self._kb_cb = _HOOKPROC(self._kb_proc)
        self._ms_cb = _HOOKPROC(self._ms_proc)

    # --- callbacks: keep trivial -------------------------------------------------------------
    def _kb_proc(self, n_code: int, wparam: int, lparam: int) -> int:
        try:
            if n_code == HC_ACTION:
                kb = _KBDLLHOOKSTRUCT.from_address(lparam)
                flags = kb.flags
                if not flags & LLKHF_INJECTED:
                    self.last_input = time.monotonic()
                    vk = kb.vkCode
                    ctrl = shift = False
                    if vk == VK_ESCAPE:
                        ctrl = bool(_user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
                        shift = bool(_user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
                    swallow, report = self.filter.decide(
                        vk, bool(flags & LLKHF_UP), bool(flags & LLKHF_ALTDOWN), ctrl, shift
                    )
                    if report is not None:
                        self.reports.put(report)
                        _user32.PostMessageW(self.notify_hwnd, WM_APP_KEY, 0, 0)
                    if swallow:
                        return 1
        except Exception:  # never let an exception decide the key's fate
            pass
        return _user32.CallNextHookEx(None, n_code, wparam, lparam)

    def _ms_proc(self, n_code: int, wparam: int, lparam: int) -> int:
        try:
            if n_code == HC_ACTION:
                if not _MSLLHOOKSTRUCT.from_address(lparam).flags & LLMHF_INJECTED:
                    self.last_input = time.monotonic()
        except Exception:
            pass
        return _user32.CallNextHookEx(None, n_code, wparam, lparam)

    # --- install / pump -----------------------------------------------------------------------
    def _install(self) -> bool:
        """Install fresh hooks *before* removing the old ones, so there is no unguarded gap."""
        hmod = _kernel32.GetModuleHandleW(None)
        kb = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kb_cb, hmod, 0)
        ms = _user32.SetWindowsHookExW(WH_MOUSE_LL, self._ms_cb, hmod, 0)
        if not kb:
            log.error("SetWindowsHookExW(WH_KEYBOARD_LL) failed: %d", ctypes.get_last_error())
        if not ms:
            log.warning("SetWindowsHookExW(WH_MOUSE_LL) failed: %d", ctypes.get_last_error())
        old_kb, old_ms = self._kb_hook, self._ms_hook
        if kb:
            self._kb_hook = kb
            if old_kb:
                _user32.UnhookWindowsHookEx(old_kb)
        if ms:
            self._ms_hook = ms
            if old_ms:
                _user32.UnhookWindowsHookEx(old_ms)
        return bool(self._kb_hook)

    def _uninstall(self) -> None:
        for h in (self._kb_hook, self._ms_hook):
            if h:
                _user32.UnhookWindowsHookEx(h)
        self._kb_hook = self._ms_hook = None

    def run(self) -> None:
        msg = _MSG()
        # Force creation of this thread's message queue so PostThreadMessageW(WM_QUIT) works.
        _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        self._tid = _kernel32.GetCurrentThreadId()
        self.ok = self._install()
        self.installed.set()
        timer = _user32.SetTimer(None, 0, HOOK_REINSTALL_MS, None)
        try:
            while True:
                r = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if r == 0 or r == -1:
                    break
                if msg.message == WM_TIMER and msg.hwnd is None and msg.wParam == timer:
                    self._install()
                    self.reinstalls += 1
                    continue
                if msg.message == WM_APP_RESET_FILTER and msg.hwnd is None:
                    # Done here, on the thread the callback runs on, so it can never interleave
                    # with a half-finished decide().
                    self.filter.reset()
                    continue
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if timer:
                _user32.KillTimer(None, timer)
            self._uninstall()
            self.filter.reset()
            log.debug("hook thread stopped")

    def reset_filter(self) -> None:
        """Ask the hook thread to ``KeyFilter.reset()`` (see there for why)."""
        if self._tid:
            _user32.PostThreadMessageW(self._tid, WM_APP_RESET_FILTER, 0, 0)
        else:
            self.filter.reset()

    def stop(self, timeout: float = 2.0) -> None:
        if self._tid:
            _user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
        self.join(timeout)
        if self.is_alive():
            log.warning("hook thread did not stop within %.1f s", timeout)


# --- helpers --------------------------------------------------------------------------------------------


def _exe_name_for_pid(pid: int) -> str:
    h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return f"pid:{pid}"
    try:
        size = wintypes.DWORD(520)
        buf = ctypes.create_unicode_buffer(size.value)
        if _kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return f"pid:{pid}"
    finally:
        _kernel32.CloseHandle(h)


def _monitor_rects() -> list[tuple[int, int, int, int]]:
    """Every monitor's full rectangle (physical virtual-screen pixels; we are per-monitor DPI aware)."""
    rects: list[tuple[int, int, int, int]] = []

    def _cb(_hmon: int, _hdc: int, lprc: Any, _data: int) -> bool:
        r = lprc.contents
        rects.append((int(r.left), int(r.top), int(r.right), int(r.bottom)))
        return True

    _user32.EnumDisplayMonitors(None, None, _MONITORENUMPROC(_cb), 0)
    return rects


def _wait_thread(handle: int, hwnd: int, message: int, name: str) -> threading.Thread:
    """Block on ``handle`` in a daemon thread and post ``message`` to ``hwnd`` when it is signalled."""

    def _run() -> None:
        r = _kernel32.WaitForSingleObject(handle, INFINITE)
        if r == WAIT_OBJECT_0:
            _user32.PostMessageW(hwnd, message, 0, 0)

    t = threading.Thread(target=_run, name=name, daemon=True)
    t.start()
    return t


# --- the core ----------------------------------------------------------------------------------------


class GuardCore:
    """State and main-thread message handling. One instance per process, driven by ``run``."""

    def __init__(self, parent_pid: int) -> None:
        self.parent_pid = parent_pid
        self.pid = os.getpid()
        self.guarded = False  # hook + tripwires active (FOG/BLACKOUT on Nib's side)
        self.peeking = False
        self.power_held = False
        self.orphan = False  # Nib died while guarded; we are covering on our own
        self.stdin_closed = False
        self.overlay_hwnds: set[int] = set()
        self.filter = KeyFilter()
        self.hooks: _HookThread | None = None
        self.hwnd = 0
        self.cover_hwnds: list[int] = []
        self._win_event_hooks: list[int] = []
        self._cmds: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
        self._out_lock = threading.Lock()
        self._out = _kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        self._in = _kernel32.GetStdHandle(STD_INPUT_HANDLE)
        self._input_emitted_for = 0.0
        self._last_input_emit = 0.0
        self._last_reassert = 0.0
        self._exit_code = 0
        self._handoff_event = _kernel32.CreateEventW(None, False, False, _HANDOFF_EVENT_NAME)
        self._orphan_mutex = 0
        self._handoff_waiter: threading.Thread | None = None
        # ctypes thunks must outlive every window / hook that can call them.
        self._wndproc = _WNDPROC(self._wnd_proc)
        self._wineventproc = _WINEVENTPROC(self._win_event)

    # --- output ---------------------------------------------------------------------------------
    def emit(self, obj: dict[str, Any]) -> None:
        """Write one JSON line to stdout. Never raises: after Nib dies the pipe is simply gone."""
        data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
        with self._out_lock:
            if not self._out or self._out == _INVALID_HANDLE:
                return
            written = wintypes.DWORD(0)
            buf = ctypes.create_string_buffer(data, len(data))
            if not _kernel32.WriteFile(self._out, buf, len(data), ctypes.byref(written), None):
                err = ctypes.get_last_error()
                log.debug("stdout write failed (%d); further output dropped", err)
                self._out = None

    def emit_event(self, kind: str, detail: str = "") -> None:
        log.info("event %s %s", kind, detail)
        self.emit({"ev": "event", "kind": kind, "detail": detail, "t": time.time()})

    # --- windows ------------------------------------------------------------------------------------
    def _register_classes(self) -> None:
        hinst = _kernel32.GetModuleHandleW(None)
        for name, brush in ((_MAIN_CLASS, None), (_COVER_CLASS, _gdi32.GetStockObject(BLACK_BRUSH))):
            wc = _WNDCLASSEXW()
            wc.cbSize = ctypes.sizeof(_WNDCLASSEXW)
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = hinst
            wc.hCursor = _user32.LoadCursorW(None, ctypes.c_void_p(IDC_ARROW))
            wc.hbrBackground = brush
            wc.lpszClassName = name
            if not _user32.RegisterClassExW(ctypes.byref(wc)):
                log.debug("RegisterClassExW(%s) failed: %d", name, ctypes.get_last_error())

    def _create_main_window(self) -> None:
        # A real (never shown) top-level popup rather than HWND_MESSAGE: it also receives the
        # WM_DISPLAYCHANGE broadcast, and WTS notifications are documented for normal windows.
        self.hwnd = _user32.CreateWindowExW(
            WS_EX_TOOLWINDOW, _MAIN_CLASS, "Nib guard core", WS_POPUP, 0, 0, 0, 0,
            None, None, _kernel32.GetModuleHandleW(None), None,
        ) or 0
        if not self.hwnd:
            raise OSError(f"CreateWindowExW failed: {ctypes.get_last_error()}")

    def cover(self) -> None:
        """Plain black topmost windows over every monitor. Idempotent (rebuilds on re-call)."""
        self._destroy_covers()
        hinst = _kernel32.GetModuleHandleW(None)
        for left, top, right, bottom in _monitor_rects():
            h = _user32.CreateWindowExW(
                WS_EX_TOPMOST | WS_EX_TOOLWINDOW, _COVER_CLASS, "Nib guard", WS_POPUP | WS_VISIBLE,
                left, top, right - left, bottom - top, None, None, hinst, None,
            )
            if not h:
                log.error("cover window failed: %d", ctypes.get_last_error())
                continue
            _user32.SetWindowPos(h, HWND_TOPMOST, left, top, right - left, bottom - top, SWP_SHOWWINDOW)
            self.cover_hwnds.append(h)
        if self.cover_hwnds:
            # Best effort: Windows may refuse a background process the foreground. In orphan mode
            # the keyboard is swallowed anyway, and the windows are topmost for the mouse.
            _user32.SetForegroundWindow(self.cover_hwnds[0])
        log.info("covering %d monitor(s)", len(self.cover_hwnds))

    def _destroy_covers(self) -> None:
        for h in self.cover_hwnds:
            _user32.DestroyWindow(h)
        self.cover_hwnds = []

    def uncover(self) -> None:
        had = bool(self.cover_hwnds)
        self._destroy_covers()
        self.filter.swallow_all = False
        if self.orphan:
            self._end_orphan()
        if had:
            log.info("uncovered")

    def _reassert_covers(self) -> None:
        for h in self.cover_hwnds:
            _user32.SetWindowPos(h, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)

    # --- power -------------------------------------------------------------------------------------
    def _take_power(self) -> None:
        if not self.power_held:
            prev = _kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
            self.power_held = prev != 0
            log.info("power request %s", "held" if self.power_held else "FAILED")

    def _release_power(self) -> None:
        if self.power_held:
            _kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            self.power_held = False
            log.info("power request released")

    # --- tripwires -----------------------------------------------------------------------------------
    def _start_tripwires(self) -> None:
        if self.hooks is None or not self.hooks.is_alive():
            self.hooks = _HookThread(self.hwnd, self.filter)
            self.hooks.start()
            if not self.hooks.installed.wait(2.0) or not self.hooks.ok:
                log.error("keyboard hook not installed")
        if not self._win_event_hooks:
            flags = WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS
            for ev in (EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_DESKTOPSWITCH):
                h = _user32.SetWinEventHook(ev, ev, None, self._wineventproc, 0, 0, flags)
                if h:
                    self._win_event_hooks.append(h)
                else:
                    log.error("SetWinEventHook(0x%x) failed: %d", ev, ctypes.get_last_error())

    def _stop_tripwires(self) -> None:
        for h in self._win_event_hooks:
            _user32.UnhookWinEvent(h)
        self._win_event_hooks = []
        if self.hooks is not None:
            self.hooks.stop()
            self._drain_keys()
            self.hooks = None
        self.filter.reset()
        self.filter.swallow_all = False
        self.filter.foreground_ok = True

    def _reset_filter(self) -> None:
        if self.hooks is not None and self.hooks.is_alive():
            self.hooks.reset_filter()
        else:
            self.filter.reset()

    def _sample_foreground(self) -> None:
        """Re-judge the current foreground window for ``KeyFilter.foreground_ok``.

        Foreground *events* only arrive on a change and only while guarded, so the state is also
        sampled when guarding starts and whenever the overlay HWND list changes.
        """
        if not self.guarded:
            self.filter.foreground_ok = True
            return
        fg = _user32.GetForegroundWindow()
        if not fg:
            return  # mid-switch: keep the last verdict
        ok, exe = self._is_whitelisted(fg)
        if ok != self.filter.foreground_ok:
            log.info("foreground %s -> keys %s", exe or "ours", "pass" if ok else "held back")
        self.filter.foreground_ok = ok

    def _is_whitelisted(self, hwnd: int) -> tuple[bool, str]:
        root = _user32.GetAncestor(hwnd, GA_ROOT) or hwnd
        if hwnd in self.overlay_hwnds or root in self.overlay_hwnds:
            return True, ""
        pid = wintypes.DWORD(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in (self.pid, self.parent_pid):
            return True, ""
        exe = _exe_name_for_pid(pid.value)
        return exe.lower() in FOREGROUND_WHITELIST_EXES, exe

    def _win_event(self, _hook: int, event: int, hwnd: int | None, *_rest: Any) -> None:
        try:
            if not self.guarded:
                return
            if event == EVENT_SYSTEM_DESKTOPSWITCH:
                self._reset_filter()  # key-ups on the other desktop never reach our hook
                self.emit_event("desktop_switch")
            elif event == EVENT_SYSTEM_FOREGROUND and hwnd:
                ok, exe = self._is_whitelisted(hwnd)
                self.filter.foreground_ok = ok
                if not ok:
                    self.emit_event("foreground_stolen", exe)
        except Exception:
            log.exception("WinEvent callback failed")

    def _drain_keys(self) -> None:
        hooks = self.hooks
        if hooks is None:
            return
        while True:
            try:
                kind, detail = hooks.reports.get_nowait()
            except queue.Empty:
                return
            if kind == "lock":
                log.info("Win+L while guarded -> LockWorkStation")
                _user32.LockWorkStation()
                # Win and L are still held, and their key-ups will happen on the lock screen.
                self._reset_filter()
            else:
                self.emit_event("blocked_key", detail)

    def _tick(self) -> None:
        self._drain_keys()
        now = time.monotonic()
        hooks = self.hooks
        if self.guarded and hooks is not None:
            li = hooks.last_input
            if li > self._input_emitted_for and now - self._last_input_emit >= INPUT_THROTTLE_S:
                self._input_emitted_for = li
                self._last_input_emit = now
                self.emit({"ev": "event", "kind": "input", "detail": "", "t": time.time()})
        if self.cover_hwnds and now - self._last_reassert >= _COVER_REASSERT_S:
            self._last_reassert = now
            self._reassert_covers()

    # --- orphan mode --------------------------------------------------------------------------------
    def _become_orphan(self) -> None:
        """Nib died while guarded: black out every monitor and eat the keyboard (except Win+L)."""
        log.warning("parent %d died while guarded - covering", self.parent_pid)
        self.orphan = True
        self.filter.swallow_all = True
        self.cover()
        self._orphan_mutex = _kernel32.CreateMutexW(None, False, _ORPHAN_MUTEX_NAME) or 0
        if self._handoff_event:
            # Clear any stale hand-off signal before waiting; a new core sets it once it guards.
            _kernel32.ResetEvent(self._handoff_event)
            self._handoff_waiter = _wait_thread(
                self._handoff_event, self.hwnd, WM_APP_HANDOFF, "guard-core-handoff"
            )

    def _end_orphan(self) -> None:
        self.orphan = False
        if self._orphan_mutex:
            _kernel32.CloseHandle(self._orphan_mutex)
            self._orphan_mutex = 0

    # --- commands -----------------------------------------------------------------------------------
    def _status(self) -> dict[str, Any]:
        return {
            "ev": "status",
            "guarded": self.guarded,
            "peeking": self.peeking,
            "hooked": bool(self.hooks and self.hooks.is_alive() and self.hooks.ok),
            "power": self.power_held,
            "covering": len(self.cover_hwnds),
            "orphan": self.orphan,
            "foreground_ok": self.filter.foreground_ok,
            "reinstalls": self.hooks.reinstalls if self.hooks else 0,
        }

    def handle(self, cmd: dict[str, Any]) -> None:
        name = cmd.get("cmd")
        if name == "ping":
            self.emit({"ev": "pong"})
        elif name == "guard":
            self._take_power()
            self.guarded = True
            self.peeking = False
            self._start_tripwires()
            self._sample_foreground()
            if self._handoff_event:
                # Tell an orphaned previous core (if any) that a live guard is up again.
                _kernel32.SetEvent(self._handoff_event)
            log.info("guarding")
        elif name == "peek":
            self.guarded = False
            self.peeking = True
            self._stop_tripwires()
            self._take_power()  # keep (or take) the power request through the peek
            log.info("peeking")
        elif name == "unlock":
            log.info("unlock -> exiting")
            self.quit(0)
        elif name == "overlay_hwnds":
            try:
                self.overlay_hwnds = {int(h) for h in cmd.get("hwnds") or []}
            except (TypeError, ValueError):
                log.warning("bad overlay_hwnds: %r", cmd.get("hwnds"))
            self._sample_foreground()
        elif name == "cover":
            self.cover()
        elif name == "uncover":
            self.uncover()
        elif name == "status":
            self.emit(self._status())
        else:
            log.warning("unknown command %r", name)

    def quit(self, code: int) -> None:
        self._exit_code = code
        self.guarded = False
        _user32.PostQuitMessage(code)

    # --- main window procedure ---------------------------------------------------------------------
    def _wnd_proc(self, hwnd: int | None, msg: int, wparam: int, lparam: int) -> int:
        try:
            if hwnd != self.hwnd or not hwnd:
                if msg == WM_CLOSE:  # cover windows ignore close requests
                    return 0
                return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)
            if msg == WM_APP_CMD:
                while True:
                    try:
                        cmd = self._cmds.get_nowait()
                    except queue.Empty:
                        break
                    self.handle(cmd)
                return 0
            if msg == WM_APP_KEY:
                self._drain_keys()
                return 0
            if msg == WM_TIMER and wparam == _TICK_TIMER_ID:
                self._tick()
                return 0
            if msg == WM_APP_STDIN_EOF:
                self.stdin_closed = True
                if not self.guarded and not self.orphan:
                    log.info("stdin closed and not guarded -> exiting")
                    self.quit(0)
                else:
                    log.warning("stdin closed while guarded; staying up")
                return 0
            if msg == WM_APP_PARENT_DIED:
                if self.guarded and not self.orphan:
                    self._become_orphan()
                elif not self.guarded:
                    log.info("parent died while not guarded -> exiting")
                    self.quit(0)
                return 0
            if msg == WM_APP_HANDOFF:
                if self.orphan:
                    log.info("a new guard core took over -> exiting")
                    self.quit(0)
                return 0
            if msg == WM_WTSSESSION_CHANGE:
                if wparam in (WTS_SESSION_LOCK, WTS_SESSION_UNLOCK):
                    self._reset_filter()  # Win+L's key-ups happened where our hook can't see
                if wparam == WTS_SESSION_LOCK:
                    self.emit_event("session_lock")
                elif wparam == WTS_SESSION_UNLOCK:
                    self.emit_event("session_unlock")
                    if self.orphan or self.stdin_closed:
                        # The user proved ownership to Windows and no Nib is listening: done.
                        log.info("Windows unlocked with no Nib attached -> exiting")
                        self.quit(0)
                return 0
            if msg == WM_DISPLAYCHANGE:
                if self.cover_hwnds:
                    self.cover()
                return 0
            return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        except Exception:
            log.exception("window procedure failed (msg 0x%x)", msg)
            return 0

    # --- stdin ------------------------------------------------------------------------------------------
    def _stdin_loop(self) -> None:
        handle = self._in
        buf = b""
        chunk = ctypes.create_string_buffer(4096)
        got = wintypes.DWORD(0)
        try:
            while handle and handle != _INVALID_HANDLE:
                ok = _kernel32.ReadFile(handle, chunk, len(chunk), ctypes.byref(got), None)
                if not ok or got.value == 0:
                    err = ctypes.get_last_error() if not ok else 0
                    if err not in (0, ERROR_BROKEN_PIPE):
                        log.debug("stdin ReadFile error %d", err)
                    break
                buf += chunk.raw[: got.value]
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode("utf-8", "replace").strip()
                    if not text:
                        continue
                    try:
                        cmd = json.loads(text)
                    except ValueError:
                        log.warning("ignoring non-JSON line: %.80r", text)
                        continue
                    if isinstance(cmd, dict):
                        self._cmds.put(cmd)
                        _user32.PostMessageW(self.hwnd, WM_APP_CMD, 0, 0)
        finally:
            _user32.PostMessageW(self.hwnd, WM_APP_STDIN_EOF, 0, 0)

    # --- run --------------------------------------------------------------------------------------------
    def run(self) -> int:
        try:
            _user32.SetProcessDpiAwarenessContext(_DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        except Exception:
            log.debug("SetProcessDpiAwarenessContext unavailable", exc_info=True)
        self._register_classes()
        self._create_main_window()
        if not _wtsapi32.WTSRegisterSessionNotification(self.hwnd, NOTIFY_FOR_THIS_SESSION):
            log.warning("WTSRegisterSessionNotification failed: %d", ctypes.get_last_error())
        _user32.SetTimer(self.hwnd, _TICK_TIMER_ID, _TICK_MS, None)

        parent = 0
        if self.parent_pid > 0:
            parent = _kernel32.OpenProcess(SYNCHRONIZE, False, self.parent_pid) or 0
            if parent:
                _wait_thread(parent, self.hwnd, WM_APP_PARENT_DIED, "guard-core-parent")
            else:
                log.warning("cannot open parent pid %d (%d)", self.parent_pid, ctypes.get_last_error())

        orphan_mutex = _kernel32.OpenMutexW(SYNCHRONIZE, False, _ORPHAN_MUTEX_NAME)
        orphan_covering = bool(orphan_mutex)
        if orphan_mutex:
            _kernel32.CloseHandle(orphan_mutex)
        threading.Thread(target=self._stdin_loop, name="guard-core-stdin", daemon=True).start()
        # orphan_covering: an earlier core is still blacking out the screens because its Nib
        # died mid-guard. Nib should resume straight into BLACKOUT; our "guard" releases it.
        self.emit({"ev": "ready", "pid": self.pid, "orphan_covering": orphan_covering})
        log.info("guard core ready (parent %d)", self.parent_pid)

        msg = _MSG()
        try:
            while True:
                r = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if r == 0 or r == -1:
                    break
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._shutdown()
            if parent:
                _kernel32.CloseHandle(parent)
        return self._exit_code

    def _shutdown(self) -> None:
        self.guarded = False
        try:
            self._stop_tripwires()
        except Exception:
            log.exception("stopping tripwires failed")
        self._destroy_covers()
        self._end_orphan()
        self._release_power()
        if self.hwnd:
            _user32.KillTimer(self.hwnd, _TICK_TIMER_ID)
            _wtsapi32.WTSUnRegisterSessionNotification(self.hwnd)
            _user32.DestroyWindow(self.hwnd)
        if self._handoff_event:
            _kernel32.CloseHandle(self._handoff_event)
            self._handoff_event = 0
        log.info("guard core stopped")


def run(parent_pid: int) -> int:
    """Entry point for ``--guard-core <parent_pid>``. Returns the process exit code."""
    root = logging.getLogger()
    if not root.handlers:
        if sys.stderr is not None:
            logging.basicConfig(
                level=logging.INFO, stream=sys.stderr,
                format="%(asctime)s %(levelname)-7s guard_core: %(message)s",
            )
        else:
            root.addHandler(logging.NullHandler())
    try:
        return GuardCore(int(parent_pid)).run()
    except Exception:
        log.exception("guard core crashed")
        # Never leave the power request behind (process exit clears it too; belt and braces).
        _kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        return 1


if __name__ == "__main__":  # handy for manual testing: python guard_core.py <parent_pid>
    sys.exit(run(int(sys.argv[1]) if len(sys.argv) > 1 else 0))
