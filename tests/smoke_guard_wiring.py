"""The Away Guard's plumbing: the core's key decisions, Hello's prompt state, the way back in.

These are the parts where a mistake does not look wrong, it just leaves the owner locked out or
lets keys through to the app under the fog, so each rule here is pinned down:

* ``guard_core.KeyFilter`` - Win+L must not leave the filter stuck eating every later key, and
  while a foreign window has the foreground, ordinary keys are held back silently (Win+L still
  locks).
* ``GuardService`` - the overlays only drop topmost once ``request_hello`` has actually taken the
  unparented path; a foreign window during a prompt cancels it and re-raises; Windows unlock
  always ends the guard while no PIN is stored.
* ``OverlaySet`` - a monitor coming back after every display vanished becomes the primary again.

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


def make(**cfg):
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

print("\n" + "=" * 60)
print("FAILURES: " + ", ".join(fails) if fails else "guard wiring OK")
sys.exit(1 if fails else 0)
