"""The Away Guard's plumbing: the core's key decisions, Hello's prompt state, the way back in.

These are the parts where a mistake does not look wrong, it just leaves the owner locked out or
lets keys through to the app under the fog, so each rule here is pinned down:

* ``guard_core.KeyFilter`` - Win+L must not leave the filter stuck eating every later key, and
  while a foreign window has the foreground, ordinary keys are held back silently (Win+L still
  locks). One verdict per press: a key whose down got through never has its up eaten (no stuck
  Ctrl/Shift/Win), and a press whose up was lost goes stale.
* ``GuardService`` - every guard has a PIN, Hello or not; the overlays only drop topmost once
  ``request_hello`` has actually taken the unparented path; a foreign window during a prompt
  cancels it and re-raises without blacking out a Hello the owner started; Hello state never
  outlives its guard; Windows unlock always ends the guard while no PIN is stored; a modal dialog
  of Nib's own is closed before the overlays go up, so it can never swallow their clicks and keys.
* ``OverlaySet`` - a monitor coming back after every display vanished becomes the primary again;
  a click on any monitor ends with the primary's PIN field focused and taking keys.
* ``GuardCore`` (never run, no hook) - a click re-judges the real foreground window.

Runs on the offscreen Qt platform with the core process stubbed out: nothing opens on screen and
no keyboard hook is installed.
"""

import logging
import os
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import guard_core as C  # noqa: E402

logging.basicConfig(level=logging.CRITICAL)
fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


VK_1, VK_2, VK_ENTER = 0x31, 0x32, 0x0D

print("\n--- KeyFilter: Win+L does not leave it stuck ---")
f = C.KeyFilter()
check("Win down swallowed, not reported yet", f.decide(C.VK_LWIN, False, False) == (True, None))
check("L with Win held asks for a lock", f.decide(C.VK_L, False, False) == (True, ("lock", "")))
# The key-ups happen on the lock screen; the hook never sees them. Without a reset:
stuck = f.decide(VK_1, False, False)
check("(without a reset the filter is stuck - the bug)", stuck == (True, ("key", "Win+1")), stuck)
f.decide(VK_1, True, False)
f.reset()
check("after reset a digit passes", f.decide(VK_1, False, False) == (False, None))
check("...and its key-up passes", f.decide(VK_1, True, False) == (False, None))
check("Enter passes", f.decide(VK_ENTER, False, False) == (False, None))
check("Win is guarded again", f.decide(C.VK_LWIN, False, False) == (True, None))
check("Win up alone reports 'Win'", f.decide(C.VK_LWIN, True, False) == (True, ("key", "Win")))

print("\n--- KeyFilter: foreign foreground holds keys back ---")
f = C.KeyFilter()
f.foreground_ok = False
check("a digit is swallowed silently", f.decide(VK_2, False, False) == (True, None))
check("its key-up is swallowed too", f.decide(VK_2, True, False) == (True, None))
check("Alt+Tab still reported", f.decide(C.VK_TAB, False, True) == (True, ("key", "Alt+Tab")))
f.decide(C.VK_TAB, True, True)
f.decide(C.VK_LWIN, False, False)
check("Win+L still locks", f.decide(C.VK_L, False, False) == (True, ("lock", "")))
f.reset()
f.foreground_ok = True
check("back on the overlay a digit passes", f.decide(VK_2, False, False) == (False, None))
check("reset leaves foreground_ok / swallow_all alone",
      (lambda k: (k.reset(), (k.foreground_ok, k.swallow_all))[1])(f) == (True, False))
f.foreground_ok = False
f.decide(VK_1, False, False)  # down eaten while foreign...
f.foreground_ok = True
check("a key-up whose down was eaten is eaten even after focus returns",
      f.decide(VK_1, True, False) == (True, None))
f2 = C.KeyFilter()
f2.decide(VK_1, False, False)  # down passed...
f2.foreground_ok = False
check("a key-up whose down passed is never eaten", f2.decide(VK_1, True, False) == (False, None))

print("\n--- KeyFilter: one verdict per press, so no key is left stuck down ---")
VK_LCTRL, VK_LSHIFT, VK_D = 0xA2, 0xA0, 0x44
S = C.STALE_MS
for name, vk in (("LCtrl", VK_LCTRL), ("LShift", VK_LSHIFT)):
    k = C.KeyFilter()
    check(f"{name} down passes on our window", k.decide(vk, False, False, t_ms=1000) == (False, None))
    k.foreground_ok = False  # a click-driven blackout: another window has the foreground for a bit
    check(f"...its auto-repeat is held back while a foreign window has the keyboard",
          k.decide(vk, False, False, t_ms=1500) == (True, None))
    check("...and nothing is remembered as swallowed", k.held() == [], k.held())
    k.foreground_ok = True
    check(f"...back on ours the {name} up passes (the OS saw its down)",
          k.decide(vk, True, False, t_ms=1600) == (False, None))
    check("...and a digit then passes", k.decide(VK_1, False, False, t_ms=1700) == (False, None))

k = C.KeyFilter()
k.foreground_ok = False
k.decide(VK_LCTRL, False, False, t_ms=1000)  # swallowed; its up is never seen
check("a down swallowed while foreign is remembered", k.held() == [VK_LCTRL], k.held())
k.foreground_ok = True
check("a new press after STALE_MS on our window passes",
      k.decide(VK_LCTRL, False, False, t_ms=1000 + S + 1) == (False, None))
check("...and its up passes", k.decide(VK_LCTRL, True, False, t_ms=1000 + S + 50) == (False, None))

k = C.KeyFilter()
k.decide(VK_LCTRL, False, False, t_ms=1000)  # passed; the up is missed
k.foreground_ok = False
k.decide(VK_LCTRL, False, False, t_ms=1000 + S + 1)  # a "new" press judged while foreign: swallowed
check("an up after a passed then a swallowed down still passes (the OS may hold the key)",
      k.decide(VK_LCTRL, True, False, t_ms=1000 + S + 50) == (False, None))

k = C.KeyFilter()
k.decide(C.VK_LWIN, False, False, t_ms=1000)  # Win up missed (elevated window, dropped hook)
check("a digit long after a lost Win key-up passes, no Win+ report",
      k.decide(VK_1, False, False, t_ms=1000 + S + 1) == (False, None))
k = C.KeyFilter()
k.decide(C.VK_LWIN, False, False, t_ms=1000)
check("Win then D inside STALE_MS is still Win+D",
      k.decide(VK_D, False, False, t_ms=1000 + S - 1) == (True, ("key", "Win+D")))
k = C.KeyFilter()
k.decide(C.VK_LWIN, False, False, t_ms=1000)
k.decide(C.VK_LWIN, False, False, t_ms=1500)  # Win auto-repeats while held
check("Win+L still locks", k.decide(C.VK_L, False, False, t_ms=1600) == (True, ("lock", "")))
k = C.KeyFilter()
k.decide(C.VK_LWIN, False, False, t_ms=1000)
for t in range(1033, 4000, 33):  # Win held for three seconds: repeats keep it fresh
    k.decide(C.VK_LWIN, False, False, t_ms=t)
check("a Win held longer than STALE_MS (repeating) still makes Win+D",
      k.decide(VK_D, False, False, t_ms=4010) == (True, ("key", "Win+D")))

k = C.KeyFilter()
k.foreground_ok = False
k.decide(VK_2, False, False, t_ms=1000)
k.foreground_ok = True
k.soften()
check("soften keeps a swallowed down's up swallowed", k.decide(VK_2, True, False, t_ms=1010) == (True, None))
k.foreground_ok = False
k.decide(VK_1, False, False, t_ms=2000)
k.soften()
k.foreground_ok = True
check("soften makes the next press of a key fresh", k.decide(VK_1, False, False, t_ms=2010) == (False, None))
k = C.KeyFilter()
k.decide(C.VK_LWIN, False, False, t_ms=1000)
k.soften()
check("soften clears the Win state", not k.win_down
      and k.decide(VK_D, False, False, t_ms=1010) == (False, None))
check("Alt+Tab is still swallowed and reported once",
      C.KeyFilter().decide(C.VK_TAB, False, True, t_ms=10) == (True, ("key", "Alt+Tab")))

# --- the Qt side ------------------------------------------------------------------------------

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

import guard_auth  # noqa: E402
import guard_service as GS  # noqa: E402
from guard import Mode  # noqa: E402

guard_auth.hello_availability = lambda: "available"
GS.GuardService._start_core = lambda self: None
sent: list = []
GS.GuardService._send = lambda self, obj: sent.append(obj)


class Cfg(dict):
    def save_if_dirty(self):
        pass


def pump(n=5):
    for _ in range(n):
        app.processEvents()


#: Every guard has a PIN now (the first guard asks for one), so services start with one stored.
DEFAULT_HASH = guard_auth.hash_pin("1357")


def make(**cfg):
    cfg.setdefault("guard_pin_hash", DEFAULT_HASH)
    svc = GS.GuardService(Cfg({"guard_auth": "hello", "guard_sounds": False, **cfg}))
    svc._hello = True
    return svc


class Spy:
    """Records OverlaySet.set_prompt_open calls in order."""

    def __init__(self, overlays):
        self.calls = []
        real = overlays.set_prompt_open

        def wrapped(open_, *, untopmost=False):
            self.calls.append((bool(open_), bool(untopmost)))
            real(open_, untopmost=untopmost)

        overlays.set_prompt_open = wrapped


print("\n--- Hello: topmost drops only once the fallback is really taken ---")
svc = make()
svc.request_guard()
pump()
check("guarding", svc.controller.mode is Mode.FOG, svc.controller.mode)
spy = Spy(svc.overlays)
guard_auth.HELLO_NEEDS_UNTOPMOST = False  # the probe said the interop is there...
pending = {}


def fake_fallback(hwnd, message, done, *, on_fallback=None):
    # ...but it fails at request time: request_hello flips the flag and opens the plain prompt.
    guard_auth.HELLO_NEEDS_UNTOPMOST = True
    if on_fallback:
        on_fallback()
    pending["done"] = done
    return True


real_request = guard_auth.request_hello
guard_auth.request_hello = fake_fallback
svc.overlays.hello_requested.emit()
check("prompt state opened first, topmost kept", spy.calls[:1] == [(True, False)], spy.calls)
check("then topmost dropped for the unparented prompt", spy.calls[1:2] == [(True, True)], spy.calls)
check("overlays know the prompt is open", all(o._prompt_open for o in svc.overlays.overlays))
pending["done"](False)
check("a failed prompt re-opens the Hello button", not svc._hello_pending)
check("...and the overlays stand back up", not any(o._prompt_open for o in svc.overlays.overlays))


def sync_fail(hwnd, message, done, *, on_fallback=None):
    done(False)  # e.g. no QApplication: reports before returning
    return False


guard_auth.request_hello = sync_fail
spy.calls.clear()
svc.overlays.hello_requested.emit()
check("a synchronous failure leaves no prompt state behind",
      not svc._hello_pending and spy.calls[-1] == (False, False), spy.calls)

print("\n--- Hello: a foreign window during the prompt cancels it ---")
cancelled = []


def hold_open(hwnd, message, done, *, on_fallback=None):
    pending["done"] = done
    return False


guard_auth.request_hello = hold_open
real_cancel = guard_auth.cancel_hello
guard_auth.cancel_hello = lambda: (cancelled.append(1), pending["done"](False))
forced = []
real_reassert = svc.overlays.reassert
svc.overlays.reassert = lambda *, force=False: (forced.append(force), real_reassert(force=force))
svc.overlays.hello_requested.emit()
check("prompt pending", svc._hello_pending)
svc._on_core_event("foreground_stolen", "notepad.exe")
check("prompt cancelled", cancelled == [1] and not svc._hello_pending)
check("overlays re-raised with force", forced and forced[-1] is True, forced)
guard_auth.request_hello = real_request
guard_auth.cancel_hello = real_cancel

print("\n--- Windows unlock is always a way back while no PIN is stored ---")
svc.config["windows_unlock_clears_guard"] = False
svc.config["guard_pin_hash"] = ""  # cleared by hand mid-guard: the only way the hash can be missing
check("still guarded before the unlock", svc.controller.mode is Mode.FOG, svc.controller.mode)
svc._on_core_event("session_unlock", "")
check("no PIN: Windows unlock ends the guard despite the setting", svc.controller.mode is Mode.IDLE,
      svc.controller.mode)
pump()
svc.shutdown()

svc = make(guard_pin_hash=guard_auth.hash_pin("2468"), windows_unlock_clears_guard=False)
svc.request_guard()
pump()
svc._on_core_event("session_unlock", "")
check("with a PIN the setting is honoured", svc.controller.mode is Mode.FOG, svc.controller.mode)
svc.controller.session_unlocked()
pump()
svc.shutdown()

print("\n--- a monitor coming back after all of them vanished is the primary ---")
from PySide6.QtGui import QGuiApplication  # noqa: E402

from guard_overlay import OverlaySet  # noqa: E402

screen = QGuiApplication.primaryScreen()
from PySide6.QtWidgets import QLabel  # noqa: E402

o = OverlaySet({"guard_scene": "eyes"}, scene_factory=lambda: QLabel("scene"), mini_factory=lambda: None)
o.show("fog", primary_screen=screen)
pump()
check("primary on show", o.primary is not None)
# Simulate "every display went away": drop the overlay and forget the primary like
# _on_screen_removed does when nothing remains.
o._destroy(screen)
o._primary_screen = None
check("no primary while no screen", o.primary is None)
o._on_screen_added(screen)
pump()
check("the returning screen hosts the auth UI again", o.primary is not None and o.primary.primary)
check("...with the scene re-hosted", o.primary is not None and o.primary.scene is not None)
o.close()
pump()

print("\n--- the Hello fallback has a time limit ---")
check("_HELLO_TIMEOUT_S is a bounded wait", 0 < guard_auth._HELLO_TIMEOUT_S <= 300, guard_auth._HELLO_TIMEOUT_S)
got: list = []
req = guard_auth._HelloRequest(lambda ok: got.append(ok))
real_thread = guard_auth.threading.Thread


class NoThread:
    def __init__(self, *a, **k):
        pass

    def start(self):
        pass

    def is_alive(self):
        return False  # the prompt went away with the timeout


guard_auth.threading.Thread = NoThread
try:
    req.start_fallback("test")
finally:
    guard_auth.threading.Thread = real_thread
check("fallback arms a single-shot timer", req._timer is not None and req._timer.isActive()
      and req._timer.isSingleShot())
req._timer.timeout.emit()
check("timer expiry reports False exactly once", got == [False], got)
req._finish(True)
check("a late answer is ignored", got == [False], got)

print("\n-- the hidden PIN panel: a click reveals it, a fresh guard hides it --")
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from guard_overlay import GuardOverlay  # noqa: E402

ov = GuardOverlay(QGuiApplication.primaryScreen(), primary=True, scene=QLabel("scene"), config={})
ov.show()
app.processEvents()
check("fog starts with the PIN panel hidden", not ov._auth_panel.isVisible())
press = QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5), QPointF(5, 5), Qt.LeftButton,
                    Qt.LeftButton, Qt.NoModifier)
QApplication.sendEvent(ov.scene, press)
check("a click on the scene reveals it", ov._auth_panel.isVisible())
ov.set_mode("blackout")
check("it stays up when blacking out", ov._auth_panel.isVisible())
ov._conceal_auth()
check("left untouched it hides again", not ov._auth_panel.isVisible())
ov.reveal_auth()
ov.set_mode("choice")
ov.set_mode("fog")
check("after a peek the next guard starts hidden again", not ov._auth_panel.isVisible())
ov._allow_close = True
ov.close()

print("\n--- every guard has a PIN, even where Windows Hello works ---")
svc = make(guard_pin_hash="")
check("Hello alone is not a way in (auto-guard does not start)", not svc._auth_ready(interactive=False))
opened = []
real_exec = GS.PinSetupDialog.exec


def fake_exec(dialog):
    opened.append(dialog.findChildren(QLabel)[0].text())
    return 0  # cancelled


GS.PinSetupDialog.exec = fake_exec
try:
    svc.request_guard()
finally:
    GS.PinSetupDialog.exec = real_exec
check("Ctrl+Alt+G with Hello and no PIN opens the PIN setup", len(opened) == 1, opened)
check("...which says why Hello is not enough", opened and "Hello works on this PC too" in opened[0], opened)
check("...and a cancelled setup does not guard", svc.controller.mode is Mode.IDLE, svc.controller.mode)
svc.shutdown()
svc = make()
check("with a PIN stored the guard starts", svc._auth_ready(interactive=False))
svc.shutdown()

print("\n--- a Hello the owner started is not an attack ---")
import time  # noqa: E402

real_request, real_cancel = guard_auth.request_hello, guard_auth.cancel_hello
cancelled.clear()
guard_auth.request_hello = hold_open
guard_auth.cancel_hello = lambda: (cancelled.append(1), pending["done"](False))
svc = make()
svc.request_guard()
pump()
forced = []
real_reassert = svc.overlays.reassert
svc.overlays.reassert = lambda *, force=False: (forced.append(force), real_reassert(force=force))
sent.clear()
svc.overlays.hello_requested.emit()
check("the core hears the prompt open", {"cmd": "prompt", "open": True} in sent, sent)
svc._last_input = time.monotonic()  # the click on the Hello button
svc._on_core_event("foreground_stolen", "consent.exe")
check("a foreground change during Hello, right after a click, does not black out",
      svc.controller.mode is Mode.FOG, svc.controller.mode)
check("...the prompt is cancelled and the screen taken back", cancelled == [1] and forced[-1:] == [True],
      (cancelled, forced))
check("...and the core hears the prompt closed", {"cmd": "prompt", "open": False} in sent, sent)
check("...the prompt itself moving the foreground is not even logged",
      not [e for e in svc.controller.log if e.kind == "foreground_stolen"], list(svc.controller.log))
svc._last_input = time.monotonic()
svc._on_core_event("foreground_stolen", "explorer.exe")
check("...nor does one within HELLO_GRACE_S after the prompt reported", svc.controller.mode is Mode.FOG,
      svc.controller.mode)
stolen = [e.detail for e in svc.controller.log if e.kind == "foreground_stolen"]
check("...but a window that is not the prompt still lands in the intrusion log (anyone can click Hello)",
      len(stolen) == 1 and "explorer.exe" in stolen[0], stolen)
svc._hello_done_at -= GS.HELLO_GRACE_S + 0.1
svc._last_input = time.monotonic()
svc._on_core_event("foreground_stolen", "notepad.exe")
check("without Hello a click-driven foreground change still blacks out",
      svc.controller.mode is Mode.BLACKOUT, svc.controller.mode)
svc.controller.session_unlocked()
pump()
svc.shutdown()

print("\n--- Hello state never outlives its guard ---")
svc = make()
svc.request_guard()
pump()
cancelled.clear()
svc.overlays.hello_requested.emit()
old_done = pending["done"]
guard_auth.cancel_hello = lambda: cancelled.append(1)  # a prompt that does not answer a cancel
svc.controller.session_unlocked()  # the guard ends while the prompt is still open
pump()
check("ending a guard cancels its prompt", cancelled == [1], cancelled)
check("...and the Hello button works again next guard", not svc._hello_pending)
svc.request_guard()
pump()
check("a new guard is up", svc.controller.mode is Mode.FOG, svc.controller.mode)
svc.overlays.hello_requested.emit()
check("the new guard can open its own prompt", svc._hello_pending)
new_done = pending["done"]
old_done(True)  # the old prompt finally says "verified" while the new one is open
check("a late Hello success from the old guard does not unlock the new one",
      svc.controller.mode is Mode.FOG, svc.controller.mode)
check("...nor close the new guard's prompt", svc._hello_pending)
new_done(True)
check("...and its own answer unlocks it", svc.controller.mode is Mode.CHOICE, svc.controller.mode)
svc.controller.choose_unlock()
pump()
svc.shutdown()


def boom(hwnd, message, done, *, on_fallback=None):
    raise OSError("WinRT went away")


guard_auth.request_hello = boom
svc = make()
svc.request_guard()
pump()
svc.overlays.hello_requested.emit()
check("request_hello raising leaves no prompt pending", not svc._hello_pending)
check("...and no overlay standing down", not any(o._prompt_open for o in svc.overlays.overlays))
svc.controller.session_unlocked()
pump()
svc.shutdown()
guard_auth.request_hello, guard_auth.cancel_hello = real_request, real_cancel

print("\n--- a fallback prompt Nib cannot close blocks a second one ---")


class FakeWorker:
    alive = True

    def __init__(self, *a, **k):
        pass

    def start(self):
        pass

    def is_alive(self):
        return FakeWorker.alive


got = []
real_thread = guard_auth.threading.Thread
guard_auth.threading.Thread = FakeWorker
try:
    req = guard_auth._HelloRequest(lambda ok: got.append(ok))
    guard_auth._pending.add(req)
    req.start_fallback("test")
finally:
    guard_auth.threading.Thread = real_thread
check("a waiting request makes Hello busy", guard_auth.hello_busy())
guard_auth.cancel_hello()
check("cancel reports False once", got == [False], got)
check("...but the open prompt still counts as busy", guard_auth.hello_busy())
calls = []
guard_auth.request_hello = lambda *a, **k: calls.append(1)
svc = make()
svc.request_guard()
pump()
svc.overlays.hello_requested.emit()
check("a second Hello click opens no second prompt", calls == [] and not svc._hello_pending, calls)
check("...and says what to do instead", "type your PIN" in svc.overlays.primary._status.text(),
      svc.overlays.primary._status.text())
abandoned = next(iter(guard_auth._abandoned))
abandoned._abandoned_at -= guard_auth._HELLO_TIMEOUT_S + 1
check("an abandoned prompt stops blocking Hello after the timeout, even with its worker alive",
      not guard_auth.hello_busy() and FakeWorker.alive, list(guard_auth._abandoned))
FakeWorker.alive = True
req2 = guard_auth._HelloRequest(lambda ok: got.append(ok))
guard_auth.threading.Thread = FakeWorker
try:
    guard_auth._pending.add(req2)
    req2.start_fallback("test")
finally:
    guard_auth.threading.Thread = real_thread
req2._fallback_timed_out()
check("a timed-out prompt that is still open counts as busy too", guard_auth.hello_busy()
      and req2 in guard_auth._abandoned, list(guard_auth._abandoned))
FakeWorker.alive = False
check("once the prompt is answered Hello is free again", not guard_auth.hello_busy())
svc.overlays.hello_requested.emit()
check("...and the button opens a prompt", calls == [1], calls)
guard_auth.request_hello = real_request
svc._hello_pending = False
svc.controller.session_unlocked()
pump()
svc.shutdown()

print("\n--- the PIN field: never a focus trap, never silently missing ---")
ov = GuardOverlay(QGuiApplication.primaryScreen(), primary=True, scene=QLabel("scene"), config={})
check("the Hello button never takes the keyboard focus", ov._hello_btn.focusPolicy() == Qt.NoFocus)
ov.set_auth(hello=True, pin=True, backoff_s=0)
ov.show()
ov.reveal_auth()
app.processEvents()
check("with a PIN the field shows, no hint", ov._pin.isVisible() and not ov._no_pin.isVisible())
ov.set_auth(hello=True, pin=False, backoff_s=0)
check("with no PIN a hint says what still works", ov._no_pin.isVisible() and not ov._pin.isVisible()
      and "Ctrl+Alt+Del" in ov._no_pin.text())
ov._pin.clearFocus()  # Tab or a click on a button: the field lost the focus
ov.set_auth(hello=True, pin=True, backoff_s=0)
ov.reveal_auth()
ov._pin.clearFocus()
ov.reassert()
check("reassert puts the focus back in the field", QApplication.focusWidget() is ov._pin,
      QApplication.focusWidget())
ov._allow_close = True
ov.close()

print("\n--- a rebuilt primary shows what is left of a backoff, not all of it ---")
o = OverlaySet({"guard_scene": "eyes"}, scene_factory=lambda: QLabel("scene"), mini_factory=lambda: None)
o.show("fog", primary_screen=screen)
o.set_auth(hello=False, pin=True, backoff_s=8)
o._backoff_until -= 5  # five seconds later the monitor is unplugged and plugged back in
o._destroy(screen)
o._primary_screen = None
o._on_screen_added(screen)
left = o.primary._backoff_until - time.monotonic()
check("about 3 s left, not 8", 2.0 < left < 3.5, round(left, 2))
o.close()
pump()

print("\n--- a click on any monitor ends with a PIN field that takes keys ---")
from PySide6.QtGui import QKeyEvent  # noqa: E402

import guard_overlay as GO  # noqa: E402

svc = make()
svc.request_guard()
pump()
o = svc.overlays
primary = o.primary
# A second monitor: offscreen has one screen, so host a secondary overlay on it by hand.
sec = GO.GuardOverlay(screen, primary=False, scene=None, config=svc.config)
sec.set_key_sink(o._forward_key)
o._overlays["second monitor"] = sec
sec.show()
sec.activateWindow()  # the click on that monitor activated it (WM_MOUSEACTIVATE said no, but say it did)
pump()
check("(the secondary has the keyboard before the click)", sec.isActiveWindow(), QApplication.activeWindow())
sent.clear()
QApplication.sendEvent(sec, QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
pump()
check("the core is told about the click", {"cmd": "owner_click"} in sent, sent)
check("the PIN field is revealed on the primary", primary._auth_panel.isVisible())
check("the primary is the active window", primary.isActiveWindow(), QApplication.activeWindow())
check("the PIN field has the focus", QApplication.focusWidget() is primary._pin, QApplication.focusWidget())
sec.activateWindow()
pump()
QApplication.sendEvent(sec, QKeyEvent(QEvent.KeyPress, Qt.Key_7, Qt.NoModifier, "7"))
pump()
check("a key typed while the secondary had the keyboard lands in the field", primary._pin.text() == "7",
      primary._pin.text())
check("...and the primary takes the keyboard back", primary.isActiveWindow())
primary._pin.clear()
msg = GO.wintypes.MSG()
msg.message = GO.WM_MOUSEACTIVATE
check("a secondary answers WM_MOUSEACTIVATE with MA_NOACTIVATE",
      sec.nativeEvent(b"windows_generic_MSG", GO.ctypes.addressof(msg)) == (True, GO.MA_NOACTIVATE))
check("...the primary does not", primary.nativeEvent(b"windows_generic_MSG",
                                                    GO.ctypes.addressof(msg)) != (True, GO.MA_NOACTIVATE))

o.set_prompt_open(True)  # a Hello prompt is marked open: the click still wins
primary._pin.clearFocus()
sec.activateWindow()
pump()
QApplication.sendEvent(sec, QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
pump()
check("with a prompt marked open a click still gives the field the keyboard",
      primary.isActiveWindow() and QApplication.focusWidget() is primary._pin, QApplication.focusWidget())
o.set_prompt_open(False)

warnings = []


class Grab(logging.Handler):
    def emit(self, record):
        warnings.append(record.getMessage())


grab = Grab(level=logging.WARNING)
GS.log.addHandler(grab)
GS.log.setLevel(logging.DEBUG)
retakes = []
real_take = o.take_keyboard
o.take_keyboard = lambda: (retakes.append(1), real_take())
svc._verify_retry = True
svc._verify_waiting = True
svc._finish_verify({"ev": "status", "foreground_ok": True, "held": [], "win_down": False})
check("all good: no warning, no retake", warnings == [] and retakes == [], (warnings, retakes))
svc._verify_retry = True
svc._verify_waiting = True
svc._finish_verify({"ev": "status", "foreground_ok": False, "held": [0xA2], "win_down": False})
check("core still holding keys back: one warning line naming it", len(warnings) == 1
      and "core_keys" in warnings[0] and "PIN not ready" in warnings[0], warnings)
check("...and the keyboard is taken once more", retakes == [1], retakes)
svc._verify_waiting = True
svc._finish_verify({"ev": "status", "foreground_ok": False})
check("...but only once per click", retakes == [1], retakes)
warnings.clear()
svc._verify_waiting = True
svc._finish_verify(None)
check("no core reply is not counted as a failure", warnings == [], warnings)
svc._verify_waiting = True
svc._on_core_message({"ev": "status", "foreground_ok": True, "held": [], "win_down": False})
check("a status reply completes a pending check", not svc._verify_waiting)
GS.log.removeHandler(grab)
GS.log.setLevel(logging.NOTSET)
o.take_keyboard = real_take

sent.clear()
QApplication.sendEvent(primary._hello_btn, QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                                                       Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
pump()
check("a press on the Hello button is not a take-the-keyboard click (its prompt needs the keys)",
      {"cmd": "owner_click"} not in sent, sent)

hello_cancels = []
guard_auth.request_hello = hold_open
guard_auth.cancel_hello = lambda: (hello_cancels.append(1), pending["done"](False))
o.hello_requested.emit()
QApplication.sendEvent(primary.scene, QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                                                  Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
pump()
check("a click on the fog while Hello is open cancels it (typing instead)",
      hello_cancels == [1] and not svc._hello_pending, hello_cancels)
guard_auth.request_hello, guard_auth.cancel_hello = real_request, real_cancel
del o._overlays["second monitor"]
sec._allow_close = True
sec.close()
svc.controller.session_unlocked()
pump()
svc.shutdown()

print("\n--- a guard started over one of Nib's own modal dialogs still takes clicks and keys ---")
from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402

import win_integration  # noqa: E402


def modal_box(text):
    box = QMessageBox(QMessageBox.Question, "Nib", text, QMessageBox.Yes | QMessageBox.No)
    box.setWindowModality(Qt.ApplicationModal)  # what QMessageBox.question() / exec() gives
    box.show()  # never exec() here: it would block the test
    pump()
    return box


for how in ("hotkey", "auto-guard", "peek ends"):
    svc = make(guard_auto_after_idle_min=1)
    if how == "peek ends":
        svc.request_guard()
        pump()
        svc.controller._set_mode(Mode.PEEK)  # the owner is peeking...
        pump()
    box = modal_box("Restore defaults?")  # ...or not guarded yet, and opens a Nib dialog
    check(f"[{how}] (the box is app-modal)", QApplication.activeModalWidget() is box,
          QApplication.activeModalWidget())
    if how == "hotkey":
        svc.request_guard()
    elif how == "auto-guard":
        real_idle = win_integration.get_idle_seconds
        win_integration.get_idle_seconds = lambda: 3600.0
        try:
            svc._auto_armed = True
            svc._check_auto_guard()
        finally:
            win_integration.get_idle_seconds = real_idle
    else:
        svc.controller._set_mode(Mode.FOG)  # the peek ran out
    pump()
    check(f"[{how}] guarding", svc.controller.mode is Mode.FOG, svc.controller.mode)
    check(f"[{how}] the box is closed first", not box.isVisible() and QApplication.activeModalWidget() is None,
          QApplication.activeModalWidget())
    check(f"[{how}] ...answered like Escape, not Yes", box.clickedButton() is not box.button(QMessageBox.Yes))
    primary = svc.overlays.primary
    check(f"[{how}] the overlay reports no modal in the way", primary.keyboard_state()["modal"] is True,
          primary.keyboard_state())
    # Through the window, like real input: Qt's modal blocking sits there, not in sendEvent.
    win = primary.windowHandle()
    sent.clear()
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(20, 20))
    pump()
    check(f"[{how}] a click reaches the guard", {"cmd": "owner_click"} in sent, sent)
    for ch in "1234":
        QTest.keyClick(win, ch)
    pump()
    check(f"[{how}] typed keys land in the PIN field", primary._pin.text() == "1234", primary._pin.text())
    primary._pin.clear()
    svc.controller.session_unlocked()
    pump()
    svc.shutdown()
    box.deleteLater()
pump()

print("\n--- the self-check names a modal dialog in the way ---")
svc = make()
svc.request_guard()
pump()
box = modal_box("Clear history?")  # opened after the guard started (nothing of Nib's should)
check("keyboard_state flags it", svc.overlays.keyboard_state()["modal"] is False, svc.overlays.keyboard_state())
warnings.clear()
GS.log.addHandler(grab)
GS.log.setLevel(logging.DEBUG)
svc._verify_retry = False
svc._verify_waiting = True
svc._finish_verify({"ev": "status", "foreground_ok": True, "held": [], "win_down": False})
GS.log.removeHandler(grab)
GS.log.setLevel(logging.NOTSET)
check("...and the PIN-not-ready warning names it", warnings and "modal" in warnings[0], warnings)
box.close()
box.deleteLater()
svc.controller.session_unlocked()
pump()
svc.shutdown()

print("\n--- a guard resumed after a crash without a PIN asks for one when it ends ---")
for hello in (False, True):
    svc = make(guard_pin_hash="", windows_unlock_clears_guard=False)
    svc._hello = hello
    svc._resume_after_crash()
    pump()
    check(f"[hello={hello}] the orphan's screens are taken over, blacked out",
          svc.controller.mode is Mode.BLACKOUT, svc.controller.mode)
    hint = svc.overlays.primary._no_pin.text()
    check(f"[hello={hello}] the no-PIN hint mentions Hello only when Hello can be used",
          ("Windows Hello" in hint) is hello and "Ctrl+Alt+Del" in hint, hint)
    opened.clear()
    GS.PinSetupDialog.exec = fake_exec
    try:
        svc._on_core_event("session_unlock", "")  # no PIN: signing in to Windows ends it
        pump()
    finally:
        GS.PinSetupDialog.exec = real_exec
    check(f"[hello={hello}] signing in ends it", svc.controller.mode is Mode.IDLE, svc.controller.mode)
    check(f"[hello={hello}] ...and the PIN setup opens once", len(opened) == 1, opened)
    check(f"[hello={hello}] ...without starting another guard", svc.controller.mode is Mode.IDLE)
    svc.shutdown()

svc = make(guard_pin_hash="")
svc._start_after_setup = False  # what the after-guard setup sets
svc._on_pin_hashed(guard_auth.hash_pin("8642"))
check("a PIN set after a guard is stored but starts no guard",
      svc._pin_hash() and svc.controller.mode is Mode.IDLE, svc.controller.mode)
svc.shutdown()
svc = make()
svc._resume_after_crash()
pump()
check("with a PIN stored a resumed guard asks for nothing afterwards", svc._setup_after_guard is False)
opened.clear()
GS.PinSetupDialog.exec = fake_exec
try:
    svc.controller.session_unlocked()
    pump()
finally:
    GS.PinSetupDialog.exec = real_exec
check("...and opens no setup", opened == [], opened)
svc.shutdown()

print("\n--- the core re-judges the real foreground on a click ---")
core = C.GuardCore(0)  # never run(): no hook, no window, no cover - only its bookkeeping
try:
    core.guarded = True
    fg = C._user32.GetForegroundWindow()
    real_ok = core._is_whitelisted(fg)[0] if fg else True
    core.filter.foreground_ok = False
    core.filter.decide(VK_2, False, False)  # a swallowed press whose up gets lost
    core.filter.foreground_ok = not real_ok
    core.handle({"cmd": "owner_click"})
    check("owner_click takes foreground_ok from the real foreground window, never blindly",
          core.filter.foreground_ok == real_ok, (core.filter.foreground_ok, real_ok))
    check("...and lets held presses go stale", core.filter._held.get(VK_2, [0, 0])[1] == float("-inf"))
    if fg:
        core.overlay_hwnds = {int(fg)}
        core.handle({"cmd": "owner_click"})
        check("a foreground window that is ours lets keys pass", core.filter.foreground_ok is True)
    core.handle({"cmd": "prompt", "open": True})
    check("prompt open is remembered", core.prompt_open is True)
    st = core._status()
    check("status reports held keys and the Win state", st["held"] == ["+1 other"] and st["win_down"] is False
          and st["prompt_open"] is True, st)
    check("...naming modifiers but never a key that could be a PIN digit",
          C.describe_held([0xA2, VK_2, 0x5B, 0x41]) == ["LCtrl", "LWin", "+2 other"],
          C.describe_held([0xA2, VK_2, 0x5B, 0x41]))
    logged = []

    class Keep(logging.Handler):
        def emit(self, record):
            logged.append(record.getMessage())

    keep = Keep(level=logging.DEBUG)
    C.log.addHandler(keep)
    C.log.setLevel(logging.DEBUG)
    core.filter.foreground_ok = False
    core.filter.decide(VK_2, False, False)
    core.handle({"cmd": "owner_click"})
    C.log.removeHandler(keep)
    C.log.setLevel(logging.NOTSET)
    clicks = [m for m in logged if m.startswith("owner_click")]
    check("the owner_click log line never names the held digit",
          clicks and all("0x32" not in m and "+1 other" in m for m in clicks), clicks)
    core.handle({"cmd": "prompt", "open": False})
    check("prompt closed is remembered", core.prompt_open is False)
finally:
    core.guarded = False
    if core._handoff_event:
        C._kernel32.CloseHandle(core._handoff_event)
        core._handoff_event = 0

print("\n" + "=" * 60)
print("FAILURES: " + ", ".join(fails) if fails else "guard wiring OK")
sys.exit(1 if fails else 0)
