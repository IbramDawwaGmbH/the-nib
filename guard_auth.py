"""Proving you're the owner before the Away Guard lets go: Windows Hello first, a guard PIN second.

Windows Hello is the preferred path because Nib then stores nothing at all - the face/fingerprint/
PIN check happens entirely inside Windows (``UserConsentVerifier``) and Nib only learns "verified"
or "not". The catch is *where* the prompt appears. The plain WinRT call,
``UserConsentVerifier.RequestVerificationAsync``, is a UWP-era API: from a desktop app it has no
owner window, so the dialog can open *behind* a topmost full-screen overlay - which is exactly what
the guard is. ``IUserConsentVerifierInterop.RequestVerificationForWindowAsync`` takes the overlay's
HWND and parents the prompt to it, so it lands on top. pywinrt doesn't project that classic-COM
interop interface (the 3.x runtime has no raw-pointer helpers either), so it's reached here with
plain ``ctypes`` and polled through ``IAsyncInfo`` on a Qt timer. If that path fails for any reason
the plain WinRT call is used instead and ``HELLO_NEEDS_UNTOPMOST`` turns True, telling the overlay
it must drop its topmost flag while the prompt is open.

The guard PIN exists for machines where Hello is unavailable (no camera/reader, not set up,
disabled by policy). It's stored only as an scrypt hash - ``scrypt$<salt_hex>$<hash_hex>``,
n=2**14 r=8 p=1, 32-byte key, fresh 16-byte salt - and compared with ``hmac.compare_digest``. scrypt
at those parameters costs ~50-100 ms and 16 MiB, deliberately: that's what makes a stolen config
file slow to brute-force, and it's also why ``PinVerifier`` does the hashing off the UI thread so
the eyes don't freeze mid-blink on every submit.

Like ``win_integration``, nothing here takes the app down: ``hello_available()`` never raises, and
``request_hello`` always calls ``done`` exactly once, on the Qt main thread, with False on any
failure. Nothing PIN-related is ever logged - not the PIN, not the stored hash.
"""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import hmac
import logging
import os
import threading
import unicodedata
import uuid
from ctypes import wintypes
from typing import Callable

from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer, Signal, Slot

log = logging.getLogger(__name__)

# --- public flags -------------------------------------------------------------------

#: True when the Hello prompt can't be parented to the overlay (the HWND interop is unreachable),
#: so the overlay must drop ``WindowStaysOnTopHint`` while the prompt is open or the prompt opens
#: behind it. Probed by ``hello_available()`` and updated by ``request_hello`` if the interop fails
#: at request time - read it as ``guard_auth.HELLO_NEEDS_UNTOPMOST`` (module attribute, not a
#: ``from`` import, or you get a stale copy).
HELLO_NEEDS_UNTOPMOST: bool = False

# --- scrypt parameters --------------------------------------------------------------

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_SALT_BYTES = 16
#: 128 * r * n = 16 MiB is what these parameters actually need; OpenSSL's default ceiling is
#: 32 MiB, so this is explicit headroom rather than a raised cost.
_SCRYPT_MAXMEM = 64 * 1024 * 1024
_SCHEME = "scrypt"

# --- WinRT / COM plumbing (Hello interop) -------------------------------------------

_CLASS_NAME = "Windows.Security.Credentials.UI.UserConsentVerifier"

#: From UserConsentVerifierInterop.h.
_IID_IUserConsentVerifierInterop = "{39E050C3-4E74-441A-8DC0-B81104DF949C}"
_IID_IAsyncInfo = "{00000036-0000-0000-C000-000000000046}"
#: IAsyncOperation<UserConsentVerificationResult> is a parameterized interface, so its IID is not
#: in any header: WinRT derives it as a SHA-1 (UUIDv5) of the type signature under a fixed
#: namespace GUID - which is exactly ``uuid.uuid5``. The same derivation reproduces the published
#: IAsyncOperation<Boolean> IID {cdb5efb3-5788-509d-9be1-71ccb8a3362a}, which is how it was checked.
_PINTERFACE_NS = uuid.UUID("11f47ad5-7b73-42c0-abae-878b1e16adee")
_IID_IAsyncOperation_Result = "{%s}" % uuid.uuid5(
    _PINTERFACE_NS,
    "pinterface({9fc2b0bb-e446-44e2-aa61-9cab8f636af2};"
    "enum(Windows.Security.Credentials.UI.UserConsentVerificationResult;i4))",
)

_RO_INIT_SINGLETHREADED = 0
_RO_INIT_MULTITHREADED = 1
_RPC_E_CHANGED_MODE = -2147417850  # 0x80010106: the thread is already in the other apartment

# IInspectable occupies vtable slots 0-5 (QueryInterface, AddRef, Release, GetIids,
# GetRuntimeClassName, GetTrustLevel); each interface's own methods follow in declaration order.
_SLOT_QUERY_INTERFACE = 0
_SLOT_RELEASE = 2
_SLOT_REQUEST_FOR_WINDOW = 6  # IUserConsentVerifierInterop::RequestVerificationForWindowAsync
_SLOT_ASYNCINFO_STATUS = 7  # IAsyncInfo::get_Status (after get_Id)
_SLOT_ASYNCINFO_CANCEL = 9  # IAsyncInfo::Cancel (after get_ErrorCode)
_SLOT_ASYNCOP_GETRESULTS = 8  # IAsyncOperation<T>::GetResults (after put_/get_Completed)

_ASYNC_STARTED, _ASYNC_COMPLETED, _ASYNC_CANCELED, _ASYNC_ERROR = 0, 1, 2, 3

#: UserConsentVerificationResult / UserConsentVerifierAvailability share these first values.
_VERIFIED = 0
_RESULT_NAMES = {
    0: "verified",
    1: "device_not_present",
    2: "not_configured_for_user",
    3: "disabled_by_policy",
    4: "device_busy",
    5: "retries_exhausted",
    6: "canceled",
}

#: How often the interop operation's status is polled, and how long before a prompt (either path)
#: is given up on and reported as False. Two minutes is ample for a face, a finger or a PIN; the cap
#: matters because the guard stops re-raising its overlays while a prompt is open (and on the
#: fallback path drops topmost), so an abandoned prompt must not keep the screen soft for long, and
#: a wedged operation must not leave the guard waiting on a callback that never comes.
_POLL_MS = 100
_HELLO_TIMEOUT_S = 2 * 60
#: hello_available() blocks its caller, so it gets a short leash: a healthy check answers in
#: well under a second.
_AVAILABILITY_TIMEOUT_S = 5.0


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(text: str) -> _GUID:
    u = uuid.UUID(text)
    g = _GUID()
    g.Data1, g.Data2, g.Data3 = u.fields[0], u.fields[1], u.fields[2]
    g.Data4[:] = u.bytes[8:]
    return g


_HSTRING = ctypes.c_void_p

_combase = None


def _api():
    """The combase entry points, bound on first use so importing this module never touches COM."""
    global _combase
    if _combase is None:
        lib = ctypes.WinDLL("combase")
        lib.RoInitialize.argtypes = [ctypes.c_int]
        lib.RoInitialize.restype = ctypes.c_long
        lib.RoUninitialize.argtypes = []
        lib.RoUninitialize.restype = None
        lib.RoGetActivationFactory.argtypes = [
            _HSTRING,
            ctypes.POINTER(_GUID),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        lib.RoGetActivationFactory.restype = ctypes.c_long
        lib.WindowsCreateString.argtypes = [
            wintypes.LPCWSTR,
            ctypes.c_uint32,
            ctypes.POINTER(_HSTRING),
        ]
        lib.WindowsCreateString.restype = ctypes.c_long
        lib.WindowsDeleteString.argtypes = [_HSTRING]
        lib.WindowsDeleteString.restype = ctypes.c_long
        _combase = lib
    return _combase


def _hr_check(hr: int, what: str) -> None:
    if hr < 0:
        raise OSError(f"{what} failed: HRESULT 0x{hr & 0xFFFFFFFF:08X}")


def _vcall(ptr: int, slot: int, argtypes: list, *args) -> int:
    """Call vtable slot ``slot`` of COM object ``ptr``; returns the raw HRESULT."""
    vtbl = ctypes.cast(ctypes.c_void_p(ptr), ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    return proto(vtbl[slot])(ptr, *args)


def _release(ptr: int | None) -> None:
    if ptr:
        try:
            _vcall(ptr, _SLOT_RELEASE, [])
        except Exception:
            log.debug("Release failed", exc_info=True)


def _hstring(text: str) -> int:
    h = _HSTRING()
    _hr_check(_api().WindowsCreateString(text, len(text), ctypes.byref(h)), "WindowsCreateString")
    return h.value or 0  # an empty string is a legitimate NULL HSTRING


def _interop_factory() -> int:
    """IUserConsentVerifierInterop from the class's activation factory. Caller releases it."""
    api = _api()
    name = _hstring(_CLASS_NAME)
    try:
        factory = ctypes.c_void_p()
        iid = _guid(_IID_IUserConsentVerifierInterop)
        hr = api.RoGetActivationFactory(name, ctypes.byref(iid), ctypes.byref(factory))
        _hr_check(hr, "RoGetActivationFactory(IUserConsentVerifierInterop)")
        return factory.value
    finally:
        api.WindowsDeleteString(name)


def _ensure_apartment(kind: int) -> bool:
    """RoInitialize this thread; True if the caller now owns a matching RoUninitialize.

    Already-initialised threads (Qt's GUI thread is an OLE STA) answer S_FALSE or
    RPC_E_CHANGED_MODE - both mean "COM is usable here", and neither must be balanced by us.
    """
    hr = _api().RoInitialize(kind)
    if hr == 0:
        return True
    if hr == 1:  # S_FALSE: already initialised in this mode; still counted, so balance it
        return True
    if hr == _RPC_E_CHANGED_MODE:
        return False
    _hr_check(hr, "RoInitialize")
    return False


# --- availability -------------------------------------------------------------------


def _probe() -> tuple[str, bool]:
    """(availability name, interop reachable). Runs on a worker thread; may raise."""
    interop_ok = False
    owned = False
    try:
        owned = _ensure_apartment(_RO_INIT_MULTITHREADED)
        _release(_interop_factory())
        interop_ok = True
    except Exception as exc:
        log.info("Hello window interop unreachable (%s) - prompt can't be parented", exc)
    finally:
        if owned:
            _api().RoUninitialize()

    from winrt.windows.security.credentials.ui import UserConsentVerifier

    availability = asyncio.run(_check_availability(UserConsentVerifier))
    return _RESULT_NAMES.get(int(availability), f"unknown_{int(availability)}"), interop_ok


async def _check_availability(verifier) -> int:
    return await verifier.check_availability_async()


def hello_availability() -> str:
    """Why Hello is or isn't usable: "available", "device_not_present", "not_configured_for_user",
    "disabled_by_policy", "device_busy", or "missing" (package/WinRT unusable) / "timeout".

    Never raises. Blocks for the length of one WinRT availability check (typically well under a
    second, capped at ``_AVAILABILITY_TIMEOUT_S``); the work runs on a short-lived worker thread
    so the caller's COM apartment is never touched. Also refreshes ``HELLO_NEEDS_UNTOPMOST``.
    """
    global HELLO_NEEDS_UNTOPMOST
    box: dict[str, object] = {}

    def work() -> None:
        try:
            box["result"] = _probe()
        except Exception as exc:
            box["error"] = exc

    t = threading.Thread(target=work, name="nib-hello-probe", daemon=True)
    t.start()
    t.join(_AVAILABILITY_TIMEOUT_S)
    if t.is_alive():
        log.warning("Hello availability check timed out")
        return "timeout"
    if "error" in box:
        log.info("Hello unavailable: %s", type(box["error"]).__name__)
        return "missing"
    name, interop_ok = box["result"]  # type: ignore[misc]
    HELLO_NEEDS_UNTOPMOST = not interop_ok
    log.debug("Hello availability: %s (window interop %s)", name, "ok" if interop_ok else "no")
    return name


def hello_available() -> bool:
    """True when Windows Hello can verify the user right now. Never raises."""
    try:
        return hello_availability() == "available"
    except Exception:
        log.debug("hello_available failed", exc_info=True)
        return False


# --- Hello request ------------------------------------------------------------------


class _HelloRequest(QObject):
    """One in-flight Hello prompt. Lives on the Qt main thread; reports exactly once."""

    _from_worker = Signal(bool)

    def __init__(self, done: Callable[[bool], None]) -> None:
        super().__init__()
        self._done: Callable[[bool], None] | None = done
        self._op = 0
        self._info = 0
        self._elapsed_ms = 0
        self._timer: QTimer | None = None
        # Queued because the receiver (self, main thread) differs from the emitting worker
        # thread - this is what lands the fallback's answer back on the main thread.
        self._from_worker.connect(self._finish)

    # -- interop path (preferred) --

    def start_interop(self, hwnd: int, message: str) -> None:
        api = _api()
        _ensure_apartment(_RO_INIT_SINGLETHREADED)  # GUI thread: normally already an OLE STA
        factory = _interop_factory()
        msg = _hstring(message)
        try:
            op = ctypes.c_void_p()
            iid = _guid(_IID_IAsyncOperation_Result)
            hr = _vcall(
                factory,
                _SLOT_REQUEST_FOR_WINDOW,
                [wintypes.HWND, _HSTRING, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)],
                hwnd,
                msg,
                ctypes.byref(iid),
                ctypes.byref(op),
            )
            _hr_check(hr, "RequestVerificationForWindowAsync")
            self._op = op.value or 0
        finally:
            api.WindowsDeleteString(msg)
            _release(factory)

        info = ctypes.c_void_p()
        iid_info = _guid(_IID_IAsyncInfo)
        hr = _vcall(
            self._op,
            _SLOT_QUERY_INTERFACE,
            [ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)],
            ctypes.byref(iid_info),
            ctypes.byref(info),
        )
        if hr < 0:
            self._cleanup()
            _hr_check(hr, "QueryInterface(IAsyncInfo)")
        self._info = info.value or 0

        # Polled rather than given a Completed delegate: implementing a COM callback object in
        # ctypes is far more code (and a far worse crash surface) than checking a status flag ten
        # times a second, and polling on a main-thread timer delivers the result where the
        # contract wants it for free.
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    def _poll(self) -> None:
        try:
            status = wintypes.DWORD()  # AsyncStatus is an int32 enum
            hr = _vcall(
                self._info, _SLOT_ASYNCINFO_STATUS, [ctypes.POINTER(wintypes.DWORD)],
                ctypes.byref(status),
            )
            _hr_check(hr, "IAsyncInfo::get_Status")
            if status.value == _ASYNC_STARTED:
                self._elapsed_ms += _POLL_MS
                if self._elapsed_ms >= _HELLO_TIMEOUT_S * 1000:
                    log.warning("Hello prompt timed out - cancelling")
                    _vcall(self._info, _SLOT_ASYNCINFO_CANCEL, [])
                    self._finish(False)
                return
            if status.value != _ASYNC_COMPLETED:
                log.info("Hello prompt ended with async status %d", status.value)
                self._finish(False)
                return
            result = ctypes.c_int32()
            hr = _vcall(
                self._op, _SLOT_ASYNCOP_GETRESULTS, [ctypes.POINTER(ctypes.c_int32)],
                ctypes.byref(result),
            )
            _hr_check(hr, "IAsyncOperation::GetResults")
            log.info("Hello result: %s", _RESULT_NAMES.get(result.value, result.value))
            self._finish(result.value == _VERIFIED)
        except Exception:
            log.warning("Hello poll failed", exc_info=True)
            self._finish(False)

    # -- plain WinRT path (fallback) --

    def start_fallback(self, message: str) -> None:
        # The worker's asyncio call cannot be cancelled from here, so the cap is a plain timer:
        # when it fires the request reports False and the worker's late answer is ignored by
        # _finish. Only on the main thread - a timer made elsewhere would have no event loop.
        app = QCoreApplication.instance()
        if app is not None and QThread.currentThread() == app.thread():
            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.setInterval(int(_HELLO_TIMEOUT_S * 1000))
            self._timer.timeout.connect(self._fallback_timed_out)
            self._timer.start()

        def work() -> None:
            ok = False
            try:
                from winrt.windows.security.credentials.ui import UserConsentVerifier

                async def ask() -> int:
                    return await UserConsentVerifier.request_verification_async(message)

                result = int(asyncio.run(ask()))
                log.info("Hello result (fallback): %s", _RESULT_NAMES.get(result, result))
                ok = result == _VERIFIED
            except Exception as exc:
                log.warning("Hello fallback failed (%s)", type(exc).__name__)
            self._from_worker.emit(ok)

        threading.Thread(target=work, name="nib-hello", daemon=True).start()

    def _fallback_timed_out(self) -> None:
        log.warning("Hello prompt (fallback) timed out - giving up on it")
        self._finish(False)

    # -- shared --

    def cancel(self) -> None:
        """Give up on this prompt now: cancel the operation if we can, report False."""
        if self._info:
            try:
                _vcall(self._info, _SLOT_ASYNCINFO_CANCEL, [])
            except Exception:
                log.debug("IAsyncInfo::Cancel failed", exc_info=True)
        self._finish(False)

    def _cleanup(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        _release(self._info)
        _release(self._op)
        self._info = self._op = 0

    @Slot(bool)
    def _finish(self, ok: bool) -> None:
        done, self._done = self._done, None
        self._cleanup()
        _pending.discard(self)
        if done is None:
            return  # already reported
        try:
            done(bool(ok))
        except Exception:
            log.exception("Hello done callback raised")


#: Keeps in-flight requests alive until they report (nothing else holds a reference).
_pending: set[_HelloRequest] = set()


def request_hello(
    hwnd: int,
    message: str,
    done: Callable[[bool], None],
    *,
    on_fallback: Callable[[], None] | None = None,
) -> bool:
    """Show the Windows Hello prompt for ``hwnd``; ``done(verified)`` fires once, on the Qt main
    thread, False on any failure, cancel or after ``_HELLO_TIMEOUT_S``.

    Call from the Qt main thread with a running QApplication. Prefers the HWND-parented interop
    (prompt appears above the overlay); if that can't be reached, falls back to the plain WinRT
    call and sets ``HELLO_NEEDS_UNTOPMOST``. Which path is taken is only known *here* (the probe
    can't tell whether the call itself will succeed), so the overlay must drop topmost *after*
    this decision: ``on_fallback()`` is called just before the unparented prompt opens, and the
    return value says whether the fallback was used (contract callers may ignore it).

    ``done`` may run synchronously (no QApplication), before this returns.
    """
    global HELLO_NEEDS_UNTOPMOST
    app = QCoreApplication.instance()
    if app is None:
        log.warning("request_hello called without a QApplication")
        done(False)
        return False
    req = _HelloRequest(done)
    _pending.add(req)
    if QThread.currentThread() != app.thread():
        # The interop path polls on a QTimer owned by the request, which must live on the main
        # thread; rather than half-support that from elsewhere, hand the request to the main
        # thread and take the worker-thread fallback, which reports there via a queued signal.
        log.warning("request_hello called off the main thread - using the unparented prompt")
        req.moveToThread(app.thread())
    else:
        try:
            req.start_interop(int(hwnd), message)
            return False
        except Exception as exc:
            log.info("Hello window interop failed (%s) - using the unparented prompt", exc)
            req._cleanup()
    HELLO_NEEDS_UNTOPMOST = True
    if on_fallback is not None:
        try:
            on_fallback()
        except Exception:
            log.exception("Hello on_fallback callback raised")
    req.start_fallback(message)
    return True


def cancel_hello() -> None:
    """Give up on every in-flight Hello prompt; each reports ``done(False)``.

    The guard calls this when a foreign window takes the foreground while a prompt is open, so the
    overlays can be re-raised at once instead of staying soft until the prompt times out.
    """
    for req in list(_pending):
        req.cancel()


# --- guard PIN ----------------------------------------------------------------------


def _normalise(pin: str) -> bytes:
    # NFC so the same PIN typed via a different input method (composed vs decomposed accents,
    # if anyone uses non-digits) hashes identically.
    return unicodedata.normalize("NFC", pin).encode("utf-8")


def _scrypt(pin: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        _normalise(pin),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
        maxmem=_SCRYPT_MAXMEM,
    )


def hash_pin(pin: str) -> str:
    """``scrypt$<salt_hex>$<hash_hex>`` for ``pin`` with a fresh random salt.

    Raises ValueError on an empty PIN; length/charset rules are the setup UI's job. Slow on
    purpose (~50-100 ms) - call it off the UI thread (``PinVerifier.hash``).
    """
    if not pin:
        raise ValueError("empty PIN")
    salt = os.urandom(_SALT_BYTES)
    return f"{_SCHEME}${salt.hex()}${_scrypt(pin, salt).hex()}"


def verify_pin(pin: str, stored: str) -> bool:
    """True if ``pin`` matches ``stored``. False - never an exception - on a malformed or empty
    stored value, so a hand-edited config can only lock the PIN path, not crash the guard."""
    try:
        scheme, salt_hex, hash_hex = stored.split("$")
        if scheme != _SCHEME:
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        if len(salt) != _SALT_BYTES or len(expected) != _SCRYPT_DKLEN:
            return False
        return hmac.compare_digest(_scrypt(pin, salt), expected)
    except Exception:
        return False


class PinVerifier(QObject):
    """Runs the scrypt work on a worker thread and reports back on this object's (main) thread.

    ``finished(ok)`` answers ``verify``; ``hashed(stored)`` answers ``hash`` (first-run PIN setup;
    an empty string means hashing failed). Results arrive in completion order - callers should
    disable the PIN field while a check is in flight (``busy``).
    """

    finished = Signal(bool)
    hashed = Signal(str)

    # Worker -> self hops: emitted on the worker thread, received by a slot of this main-thread
    # object, so the connection is queued and the public signals fire on the main thread no
    # matter what the caller connected them to.
    _verified = Signal(bool)
    _hashed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._in_flight = 0
        self._verified.connect(self._on_verified)
        self._hashed.connect(self._on_hashed)

    @property
    def busy(self) -> bool:
        return self._in_flight > 0

    def verify(self, pin: str, stored: str) -> None:
        self._in_flight += 1
        threading.Thread(
            target=lambda: self._verified.emit(verify_pin(pin, stored)),
            name="nib-pin-verify",
            daemon=True,
        ).start()

    def hash(self, pin: str) -> None:
        def work() -> None:
            try:
                out = hash_pin(pin)
            except Exception as exc:
                log.warning("PIN hashing failed (%s)", type(exc).__name__)
                out = ""
            self._hashed.emit(out)

        self._in_flight += 1
        threading.Thread(target=work, name="nib-pin-hash", daemon=True).start()

    @Slot(bool)
    def _on_verified(self, ok: bool) -> None:
        self._in_flight -= 1
        self.finished.emit(ok)

    @Slot(str)
    def _on_hashed(self, stored: str) -> None:
        self._in_flight -= 1
        self.hashed.emit(stored)
