"""Mic mute state, and whether the mic or camera is in use right now - read-only except for one
named exception, the mute toggle you ask for.

Pure ``ctypes`` - no ``comtypes``, no ``pywin32`` - for the same reason ``win_integration`` is:
one more dependency for a mascot is one too many. That constrains the design in one place worth
naming up front, under "Why the mic is read on the tick" below.

Two entirely separate sources, because Windows keeps these two facts in different places:

* **Muted or not** is a property of the audio endpoint, through ``IAudioEndpointVolume``. This is
  the only thing here that can also be *written* - see ``set_mic_muted``.
* **In use or not** - is anything actually listening or watching this second - is not exposed by
  the audio API at all. It lives in the same ``CapabilityAccessManager`` consent store the
  Windows 11 privacy indicator itself reads.

**No camera equivalent of the mute toggle exists.** Windows has no global camera mute: the only
levers are per-app consent, which does nothing to an app already running, and disabling the device
outright, which needs admin rights and breaks the camera for everything else. So the camera here
is an indicator and nothing more, and this module deliberately exposes no way to pretend otherwise.

Why the mic is read on the tick
-------------------------------
The event-driven way to watch mute state is ``IAudioEndpointVolumeCallback``, which means
implementing a COM *server* - QueryInterface, AddRef, Release and OnNotify, as a hand-built vtable -
and then marshalling its calls off a COM thread onto the UI one. That is the riskiest possible code
to put underneath a decorative feature, in a module whose whole promise is that it cannot take the
app down. So mute is read with one vtable call on the timer the app already runs, which is a few
microseconds and allocates nothing, and the cached endpoint is re-resolved on a short TTL so a
changed default device cannot go unnoticed. The consent store, by contrast, *does* get a real
event-driven watcher - see ``_Watcher`` - because there it costs a registry handle and a thread
rather than a hand-built COM vtable.

Nothing here ever reads audio or video. It reads two boolean-ish facts and, for the in-use check,
executable paths it uses only to decide "any of them, yes or no" and then discards - the same
treatment ``world.py`` already gives the foreground app. Nothing is logged above debug level, and
no name of any app is ever logged at all.

Every function is called on the mascot's own timer, so every one of them is cheap, and every Win32
or COM call is wrapped: a failure logs at debug and returns a documented fallback rather than
taking the app down. A machine with no microphone, no camera, or no audio stack at all is a
supported configuration, not an error.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
import winreg
from ctypes import POINTER, byref, c_int, c_void_p, wintypes

log = logging.getLogger(__name__)

_ole32 = ctypes.windll.ole32
_advapi32 = ctypes.windll.advapi32
_kernel32 = ctypes.windll.kernel32

# --- COM plumbing -------------------------------------------------------------------

S_OK = 0x00000000
S_FALSE = 0x00000001
#: Someone (Qt, for its own drag-and-drop) already initialised COM on this thread in the other
#: apartment model. That is not a failure for us - we are a guest on this thread, not its owner.
RPC_E_CHANGED_MODE = 0x80010106
CLSCTX_ALL = 23

#: ``eCapture`` - the microphone side of the audio stack. ``eConsole`` is the role Windows itself
#: treats as "the default device", the one the Settings app shows you.
E_CAPTURE = 1
E_CONSOLE = 0

#: How long a resolved endpoint is trusted before being looked up again. A cached pointer stays
#: *valid* after you switch default microphone - it just answers for the wrong device, silently,
#: which is worse than an error. Re-resolving twice a second costs two COM calls and removes the
#: whole class of problem; failures re-resolve immediately regardless.
_ENDPOINT_TTL_S = 2.0


class _GUID(ctypes.Structure):
    _fields_ = [
        ("d1", ctypes.c_ulong),
        ("d2", ctypes.c_ushort),
        ("d3", ctypes.c_ushort),
        ("d4", ctypes.c_ubyte * 8),
    ]

    def __init__(self, text: str) -> None:
        super().__init__()
        _ole32.CLSIDFromString(ctypes.c_wchar_p(text), byref(self))


_CLSID_MMDeviceEnumerator = _GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
_IID_IMMDeviceEnumerator = _GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
_IID_IAudioEndpointVolume = _GUID("{5CDF2C82-841E-4546-9722-0CF74078229A}")

#: Vtable slots. 0-2 are always ``IUnknown``'s QueryInterface/AddRef/Release, so everything an
#: interface declares itself starts at 3, in the order the interface declares it. These are
#: positional and unchecked by definition - a wrong number calls the wrong method - so each one
#: is named here rather than written inline at the call site.
_SLOT_RELEASE = 2
_SLOT_ACTIVATE = 3               # IMMDevice::Activate
_SLOT_GET_DEFAULT_ENDPOINT = 4   # IMMDeviceEnumerator::GetDefaultAudioEndpoint
_SLOT_SET_MUTE = 14              # IAudioEndpointVolume::SetMute
_SLOT_GET_MUTE = 15              # IAudioEndpointVolume::GetMute


def _vcall(ptr, slot, restype, argtypes, *args):
    """Call vtable ``slot`` on the COM object at ``ptr``."""
    vtable = ctypes.cast(ptr, POINTER(POINTER(c_void_p))).contents
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return proto(vtable[slot])(ptr, *args)


def _release(ptr) -> None:
    if ptr:
        try:
            _vcall(ptr, _SLOT_RELEASE, ctypes.c_ulong, [])
        except OSError:  # pragma: no cover - a dead pointer is already what we wanted
            log.debug("releasing a COM pointer failed; it was already going away")


_com_ready = False
_enumerator = None
_endpoint_volume = None
_endpoint_at = 0.0
_lock = threading.RLock()


def _ensure_com() -> bool:
    global _com_ready
    if _com_ready:
        return True
    try:
        hr = _ole32.CoInitializeEx(None, 0x2) & 0xFFFFFFFF  # COINIT_APARTMENTTHREADED
    except OSError:
        log.debug("CoInitializeEx raised; no COM on this thread")
        return False
    if hr in (S_OK, S_FALSE, RPC_E_CHANGED_MODE):
        _com_ready = True
        return True
    log.debug("CoInitializeEx failed: 0x%08x", hr)
    return False


def _get_enumerator():
    global _enumerator
    if _enumerator:
        return _enumerator
    if not _ensure_com():
        return None
    ptr = c_void_p()
    try:
        hr = _ole32.CoCreateInstance(
            byref(_CLSID_MMDeviceEnumerator), None, CLSCTX_ALL,
            byref(_IID_IMMDeviceEnumerator), byref(ptr),
        ) & 0xFFFFFFFF
    except OSError:
        log.debug("CoCreateInstance raised; no audio stack on this machine")
        return None
    if hr != S_OK or not ptr:
        log.debug("no MMDeviceEnumerator: 0x%08x", hr)
        return None
    _enumerator = ptr
    return _enumerator


def _drop_endpoint() -> None:
    global _endpoint_volume
    _release(_endpoint_volume)
    _endpoint_volume = None


def _get_endpoint_volume():
    """The default capture device's volume interface, re-resolved when stale or broken."""
    global _endpoint_volume, _endpoint_at
    now = time.monotonic()
    if _endpoint_volume and (now - _endpoint_at) < _ENDPOINT_TTL_S:
        return _endpoint_volume
    _drop_endpoint()
    enumerator = _get_enumerator()
    if not enumerator:
        return None
    device = c_void_p()
    try:
        hr = _vcall(
            enumerator, _SLOT_GET_DEFAULT_ENDPOINT, ctypes.HRESULT,
            [c_int, c_int, POINTER(c_void_p)], E_CAPTURE, E_CONSOLE, byref(device),
        )
    except OSError:
        log.debug("GetDefaultAudioEndpoint raised")
        return None
    if hr or not device:
        log.debug("no default capture endpoint - a machine with no microphone is fine")
        return None
    volume = c_void_p()
    try:
        hr = _vcall(
            device, _SLOT_ACTIVATE, ctypes.HRESULT,
            [POINTER(_GUID), wintypes.DWORD, c_void_p, POINTER(c_void_p)],
            byref(_IID_IAudioEndpointVolume), CLSCTX_ALL, None, byref(volume),
        )
    except OSError:
        log.debug("Activate raised")
        _release(device)
        return None
    _release(device)
    if hr or not volume:
        log.debug("could not activate IAudioEndpointVolume on the capture endpoint")
        return None
    _endpoint_volume, _endpoint_at = volume, now
    return _endpoint_volume


def mic_muted() -> bool | None:
    """Is the default microphone muted? ``None`` when there is nothing to ask.

    ``None`` is a real answer rather than an error: no capture device, no audio stack, no COM.
    Callers show no indicator at all in that case rather than guessing a state.
    """
    with _lock:
        volume = _get_endpoint_volume()
        if not volume:
            return None
        muted = wintypes.BOOL()
        try:
            hr = _vcall(volume, _SLOT_GET_MUTE, ctypes.HRESULT,
                        [POINTER(wintypes.BOOL)], byref(muted))
        except OSError:
            _drop_endpoint()
            return None
        if hr:
            log.debug("GetMute failed; dropping the cached endpoint so the next read re-resolves")
            _drop_endpoint()
            return None
        return bool(muted.value)


def set_mic_muted(muted: bool) -> bool:
    """Mute or unmute the default microphone. Returns whether it took.

    **The only thing in this module that changes anything on your system**, and it happens only
    when you ask for it - a hotkey or a tray entry, never a timer, never a behaviour, never as a
    side effect of anything he does on his own.
    """
    with _lock:
        volume = _get_endpoint_volume()
        if not volume:
            return False
        try:
            hr = _vcall(volume, _SLOT_SET_MUTE, ctypes.HRESULT,
                        [wintypes.BOOL, c_void_p], wintypes.BOOL(bool(muted)), None)
        except OSError:
            _drop_endpoint()
            return False
        if hr:
            log.debug("SetMute failed; dropping the cached endpoint")
            _drop_endpoint()
            return False
        return True


def toggle_mic() -> bool | None:
    """Flip the mute state. Returns the new state, or ``None`` if there was nothing to flip."""
    with _lock:
        current = mic_muted()
        if current is None:
            return None
        return (not current) if set_mic_muted(not current) else current


# --- Is anything actually listening or watching -------------------------------------

MIC = "microphone"
CAMERA = "webcam"

_CONSENT_ROOT = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"
)

#: Store apps sit directly under the capability key; **everything else** - Chrome, Teams, Zoom,
#: Discord, every desktop app anyone actually takes a call in - sits under this subkey instead,
#: with backslashes in its path rewritten as ``#``. Reading only the top level finds packaged apps
#: and misses the entire desktop, which was verified rather than assumed: on the machine this was
#: written on, every webcam user was here and none were above.
_NON_PACKAGED = "NonPackaged"

#: An app that is using the device right now has a start time and no stop time. Windows writes the
#: stop time when it releases the device, so a zero there is the live signal, not a missing value.
_START, _STOP = "LastUsedTimeStart", "LastUsedTimeStop"

KEY_READ = 0x20019
KEY_NOTIFY = 0x0010
_REG_FILTER = 0x00000001 | 0x00000004  # CHANGE_NAME | CHANGE_LAST_SET
_WAIT_TIMEOUT_MS = 500


def _key_says_in_use(key) -> bool:
    """Does any immediate subkey of ``key`` hold a live start-without-stop pair?"""
    try:
        count = winreg.QueryInfoKey(key)[0]
    except OSError:
        return False
    for i in range(count):
        try:
            name = winreg.EnumKey(key, i)
        except OSError:
            break
        if name == _NON_PACKAGED:
            continue  # walked separately by the caller; not an app entry of its own
        try:
            with winreg.OpenKey(key, name) as sub:
                start, _ = winreg.QueryValueEx(sub, _START)
                stop, _ = winreg.QueryValueEx(sub, _STOP)
        except OSError:
            continue  # an app with no recorded usage at all - not in use, by definition
        if start and not stop:
            return True
    return False


def _read_in_use(capability: str) -> bool:
    path = _CONSENT_ROOT + "\\" + capability
    for sub_path in (path, path + "\\" + _NON_PACKAGED):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub_path) as key:
                if _key_says_in_use(key):
                    return True
        except OSError:
            continue  # capability absent on this machine - no camera, no mic, fine
    return False


class _Watcher:
    """Recomputes an in-use answer only when Windows says the consent store changed.

    ``RegNotifyChangeKeyValue`` in its asynchronous form signals an event handle and nothing else,
    so this is a thread that sleeps in ``WaitForSingleObject`` and sets a flag - no polling loop,
    no timer, and no cost at all while nothing is happening. The bounded wait exists purely so the
    thread can notice ``stop()`` rather than outliving the app.

    The flag is only ever set here and cleared in ``in_use``, both under the same lock, so the
    worst a race can do is recompute one extra time - which is a registry read of a handful of
    keys, not something worth a heavier guarantee.
    """

    def __init__(self, capability: str, path: str | None = None, read=None) -> None:
        self.capability = capability
        #: Both injectable purely so the tests can point a real watcher at a scratch key of their
        #: own and prove the notification actually fires, rather than asserting that the arming
        #: call returned zero and hoping. Nothing in the app passes either.
        self._path = path if path is not None else _CONSENT_ROOT + "\\" + capability
        self._read = read if read is not None else _read_in_use
        self._dirty = True
        self._value = False
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name=f"nib-{self.capability}-watch", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def value(self) -> bool:
        with self._lock:
            if self._dirty:
                self._dirty = False
                self._value = self._read(self.capability)
            return self._value

    def _mark_dirty(self) -> None:
        with self._lock:
            self._dirty = True

    def _run(self) -> None:
        event = _kernel32.CreateEventW(None, True, False, None)
        if not event:
            log.debug("no event handle for the %s watcher; falling back to re-reads",
                      self.capability)
            return
        try:
            while not self._stop.is_set():
                try:
                    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._path, 0,
                                         KEY_READ | KEY_NOTIFY)
                except OSError:
                    return  # capability absent; nothing will ever change here
                with key:
                    _kernel32.ResetEvent(event)
                    status = _advapi32.RegNotifyChangeKeyValue(
                        wintypes.HANDLE(int(key)), True, _REG_FILTER,
                        wintypes.HANDLE(event), True,
                    )
                    if status != 0:
                        log.debug("RegNotifyChangeKeyValue failed for %s", self.capability)
                        return
                    while not self._stop.is_set():
                        if _kernel32.WaitForSingleObject(event, _WAIT_TIMEOUT_MS) == 0:
                            self._mark_dirty()
                            break
        finally:
            _kernel32.CloseHandle(event)


_watchers: dict[str, _Watcher] = {}


def _watcher(capability: str) -> _Watcher:
    watcher = _watchers.get(capability)
    if watcher is None:
        watcher = _Watcher(capability)
        watcher.start()
        _watchers[capability] = watcher
    return watcher


def in_use(capability: str) -> bool:
    """Is anything using ``capability`` (``MIC`` or ``CAMERA``) right now?"""
    return _watcher(capability).value()


def mic_in_use() -> bool:
    return in_use(MIC)


def camera_in_use() -> bool:
    return in_use(CAMERA)


def shutdown() -> None:
    """Stop the watchers and let go of the COM pointers. Safe to call more than once."""
    global _enumerator
    for watcher in _watchers.values():
        watcher.stop()
    _watchers.clear()
    with _lock:
        _drop_endpoint()
        _release(_enumerator)
        _enumerator = None
