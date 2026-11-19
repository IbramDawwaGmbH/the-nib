"""The guard's dark look: plain black until someone touches the machine, then the normal page.

The dark look is only paint on the same overlay windows, so most of what is pinned down here is
that it really is *only* paint - and that nothing of the fog look leaks into the black:

* ``OverlaySet`` in the dark look starts black on every monitor: no fog grabbed, no scene, no PIN
  panel, no status line, no effects timer, the pointer hidden - and it is the same visible windows
  all along, so there is never a moment without cover.
* A mouse move past ``DARK_JITTER_PX``, a click, a wheel turn or a key wakes it into the normal page
  with the PIN panel revealed (fresh fog grabbed on the way out); a jitter does not, and nothing
  does during the first ``DARK_SETTLE_S``. The key that wakes it is not typed into the field.
* After ``DARK_IDLE_S`` without input it goes back to black - only with an empty PIN field, nobody
  typing and no Hello prompt open.
* ``GuardService`` - the setting, a dark request and the auto-guard start the right look; the
  setting is never changed by a dark request; the guard's sounds are quiet while black; waking
  takes the keyboard like a click; wrong PINs still end in BLACKOUT (which is never black-hidden);
  a Peek comes back in the look the guard had; every rule that refuses a guard refuses a dark one.
* ``main`` / ``tray`` / ``tool_wheel`` - the dark shortcut, the tray's "Guard (dark screen)" and the
  wheel's "Dark guard" all ask for the dark look.

Runs on the offscreen Qt platform with the core process stubbed out: nothing opens on screen and
no keyboard hook is installed. Time is never waited for - the clocks are rewound by hand.
"""

import inspect
import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QIcon, QKeyEvent, QMouseEvent, QPixmap, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

logging.basicConfig(level=logging.CRITICAL)
app = QApplication.instance() or QApplication([])

import config as cfg_module  # noqa: E402
import guard_auth  # noqa: E402
import guard_overlay as GO  # noqa: E402
import guard_service as GS  # noqa: E402
import settings as S  # noqa: E402
import win_integration  # noqa: E402
from guard import Mode  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


def pump(n=5):
    for _ in range(n):
        app.processEvents()


# --- stubs: the pointer, the fog grab, the core ------------------------------------------------


class FakeCursor:
    """Stands in for QCursor inside guard_overlay, so the pointer goes exactly where a test says."""

    at = QPoint(400, 300)

    @staticmethod
    def pos():
        return QPoint(FakeCursor.at)


GO.QCursor = FakeCursor
grabs = []
real_make_fog = GO.make_fog


def counting_make_fog(screen, strength):
    grabs.append(screen)
    pix = QPixmap(64, 48)
    pix.fill(QColor(118, 120, 128))
    return pix


GO.make_fog = counting_make_fog

guard_auth.hello_availability = lambda: "available"
GS.GuardService._start_core = lambda self: None
sent: list = []
GS.GuardService._send = lambda self, obj: sent.append(obj)


class Cfg(dict):
    def save_if_dirty(self):
        pass


DEFAULT_HASH = guard_auth.hash_pin("1357")


def make(**cfg):
    cfg.setdefault("guard_pin_hash", DEFAULT_HASH)
    svc = GS.GuardService(Cfg({"guard_auth": "hello", "guard_sounds": True, **cfg}))
    svc._hello = True
    return svc


class Sounds:
    def __init__(self):
        self.played = []

    def play(self, name):
        self.played.append(name)


def settled(o):
    """Skip the settle window that follows going black (the hand that started the guard)."""
    o._settle_until = 0.0


def move(o, dx, dy=0):
    if o._anchor is None:
        o._poll_dark()  # the first poll only notes where the pointer rests
    FakeCursor.at = FakeCursor.at + QPoint(dx, dy)
    o._poll_dark()


def is_black(widget, step=16):
    """Every sampled pixel of a render of ``widget`` (children included) is black."""
    img = widget.grab().toImage()
    for y in range(0, img.height(), step):
        for x in range(0, img.width(), step):
            if QColor(img.pixel(x, y)).rgb() & 0xFFFFFF:
                return False
    return img.width() > 0


def overlay_set(look="dark", **cfg):
    config = {"guard_scene": "eyes", "guard_background_fx": True, **cfg}
    # A white label for a scene: if it shows through the black, the pixel check sees it.
    scene = lambda: QLabel("SCENE", styleSheet="color: white; font-size: 40px;")  # noqa: E731
    return OverlaySet(config, scene_factory=scene, mini_factory=lambda: None, look=look)


from guard_overlay import OverlaySet  # noqa: E402

# =================================================================================================
print("\n--- the dark look starts black, and only black ---")
grabs.clear()
o = overlay_set()
o.set_status("guarding 3 min · screen kept awake")
o.show("fog")
pump()
p = o.primary
check("starts asleep", o.asleep and o.look == "dark")
check("every overlay is dark", all(ov.dark for ov in o.overlays))
check("no desktop capture at all", grabs == [], len(grabs))
check("no fog kept", all(ov._fog is None for ov in o.overlays))
check("the overlays are up and covering", o.visible and all(ov.isVisible() for ov in o.overlays))
check("the scene is hidden", not p.scene.isVisible())
check("the PIN panel is hidden", not p._auth_panel.isVisible() and not p.revealed)
check("the status line is hidden", not p._status.isVisible())
check("no effects timer", p.fx is None or not p._fx_timer.isActive())
check("the pointer is hidden", p.cursor().shape() == Qt.BlankCursor, p.cursor().shape())
check("it paints plain black, children and all", is_black(p))
o.refresh_fog()
check("the 3 s fog refresh grabs nothing while black", grabs == [], len(grabs))
p.refresh_fog()
check("...nor does an overlay's own refresh", grabs == [], len(grabs))
check("the controller's mode is still fog underneath", o._mode == "fog" and p.mode == "fog")

print("\n--- the hand that started it is still settling ---")
FakeCursor.at = QPoint(400, 300)
o._anchor = None
o._poll_dark()
move(o, 60, 40)
check("a big move inside the settle time does not wake it", o.asleep)
settled(o)
o._poll_dark()
check("...and where it settled is the new resting place", o.asleep and o._anchor == FakeCursor.at,
      (o._anchor, FakeCursor.at))

print("\n--- jitter does not wake it, a real move does ---")
woke = []
o.woke.connect(lambda: woke.append(1))
windows_before = [(ov, ov.winId(), ov.windowFlags()) for ov in o.overlays]
move(o, 3, 2)
check("a 5 px jitter leaves it black", o.asleep and woke == [])
move(o, GO.DARK_JITTER_PX - 5, 0)
check(f"...still black at {GO.DARK_JITTER_PX} px from where it rested", o.asleep and woke == [],
      (o._anchor, FakeCursor.at))
p._affinity_ok = True  # a real Windows: the overlays are capture-excluded, so a grab is safe
grabs.clear()
move(o, 20, 0)
check("a real move wakes it", not o.asleep and woke == [1])
check("every overlay shows its page again", not any(ov.dark for ov in o.overlays))
check("fresh fog is grabbed on the way out", len(grabs) >= 1, len(grabs))
check("the PIN panel is revealed, as a touch reveals it",
      p.revealed and p._auth_panel.isVisible())
check("the scene is back", p.scene.isVisible())
check("the status line is back", p._status.isVisible())
check("the pointer is back", p.cursor().shape() == Qt.ArrowCursor)
check("the page is not black", not is_black(p))
check("the same windows all along: none hidden, re-created or re-flagged",
      [(ov, ov.winId(), ov.windowFlags()) for ov in o.overlays] == windows_before
      and all(ov.isVisible() for ov in o.overlays))

print("\n--- back to black after the quiet spell, only with an empty field ---")
o._last_touch -= GO.DARK_IDLE_S - 2
o._poll_dark()
check("not before the idle time", not o.asleep)
o._last_touch -= 3
o._poll_dark()
check(f"after {GO.DARK_IDLE_S:.0f} s without input it goes black again", o.asleep)
check("...dropping the fog it had", all(ov._fog is None for ov in o.overlays))
settled(o)
move(o, 30, 0)
check("woken again", not o.asleep)
p._pin.setText("12")
o._last_touch -= GO.DARK_IDLE_S + 5
o._poll_dark()
check("a half-typed PIN keeps the page up", not o.asleep)
p._pin.clear()
p._set_typing(True)
o._poll_dark()
check("someone typing keeps the page up", not o.asleep)
p._set_typing(False)
o.set_prompt_open(True)
o._poll_dark()
check("an open Windows Hello prompt keeps the page up", not o.asleep)
o.set_prompt_open(False)
o._poll_dark()
check("a Hello prompt that just closed starts the quiet spell again", not o.asleep)
o._last_touch -= GO.DARK_IDLE_S + 1
o._poll_dark()
check("with all of that gone it goes black", o.asleep)

print("\n--- clicks, wheel turns and keys wake it too ---")
settled(o)
woke.clear()
QApplication.sendEvent(p, QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                                      Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
pump()
check("a click wakes it", not o.asleep and woke == [1])
check("...with the panel revealed", p.revealed)
o._last_touch -= GO.DARK_IDLE_S + 1
o._poll_dark()
settled(o)
woke.clear()
QApplication.sendEvent(p, QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120),
                                      Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False))
pump()
check("a wheel turn wakes it", not o.asleep and woke == [1])
o._last_touch -= GO.DARK_IDLE_S + 1
o._poll_dark()
settled(o)
woke.clear()
QApplication.sendEvent(p, QKeyEvent(QEvent.KeyPress, Qt.Key_7, Qt.NoModifier, "7"))
pump()
check("a key wakes it", not o.asleep and woke == [1])
check("...and that key is not typed into the field", p._pin.text() == "", p._pin.text())
QApplication.sendEvent(p._pin, QKeyEvent(QEvent.KeyPress, Qt.Key_8, Qt.NoModifier, "8"))
check("the next key types as usual", p._pin.text() == "8", p._pin.text())
p._pin.clear()
p._set_typing(False)
o._last_touch -= GO.DARK_IDLE_S + 1
o._poll_dark()
woke.clear()
QApplication.sendEvent(p, QKeyEvent(QEvent.KeyPress, Qt.Key_7, Qt.NoModifier, "7", True))
check("an auto-repeat (a held shortcut) does not wake it", o.asleep and woke == [])
o._settle_until = GO.time.monotonic() + 5
QApplication.sendEvent(p, QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                                      Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
pump()
check("a click inside the settle time does not wake it", o.asleep and woke == [])
settled(o)

print("\n--- a monitor plugged in while black comes up black ---")
screen = p.screen_ref
grabs.clear()
o._destroy(screen)
o._primary_screen = None
o._on_screen_added(screen)
pump()
check("the rebuilt primary is dark", o.primary is not None and o.primary.dark)
check("...without grabbing the desktop", grabs == [], len(grabs))
check("...and its scene stays hidden", not o.primary.scene.isVisible())
o.close()
pump()

print("\n--- a blackout is never hidden behind black ---")
o = overlay_set()
o.show("fog")
pump()
check("(asleep)", o.asleep)
grabs.clear()
o.set_mode("blackout")
pump()
check("a blackout ends the dark look's black", not o.asleep and not any(ov.dark for ov in o.overlays))
check("...shows its scene", o.primary.scene.isVisible())
check("...grabs nothing (a blackout has no fog)", grabs == [], len(grabs))
o._last_touch -= GO.DARK_IDLE_S + 1
o._poll_dark()
check("...and never goes back to plain black", not o.asleep)
o.close()
pump()

print("\n--- the fog look never goes black ---")
o = overlay_set(look="fog")
grabs.clear()
o.show("fog")
pump()
check("the fog look grabs the desktop and shows its page", not o.asleep and grabs, len(grabs))
o._last_touch -= GO.DARK_IDLE_S + 1
o._poll_dark()
check("...and stays that way however long nobody touches it",
      not o.asleep and not o.primary.dark)
o.close()
pump()

# =================================================================================================
print("\n--- the service: which look a guard starts with ---")
svc = make()
svc.request_guard()
pump()
check("guard_look's default is fog", svc.look == "fog" and not svc.overlays.asleep)
svc.controller.session_unlocked()
pump()
svc.request_guard(look="dark")
pump()
check("a dark request starts the dark look", svc.look == "dark" and svc.overlays.asleep)
check("...without changing the setting", svc.config.get("guard_look", "fog") == "fog")
svc.controller.session_unlocked()
pump()
svc.request_guard()
pump()
check("the next plain request is fog again", svc.look == "fog" and not svc.overlays.asleep)
svc.shutdown()
pump()

svc = make(guard_look="dark")
svc.request_guard()
pump()
check("guard_look dark: a plain request is dark", svc.look == "dark" and svc.overlays.asleep)
svc.config["guard_look"] = "nonsense"
check("a look that is not a look reads as fog", svc.look_setting() == "fog")
svc.config["guard_look"] = "dark"

print("\n--- the service: quiet while black, keyboard taken on waking ---")
sounds = Sounds()
svc._sounds = sounds
svc._play("poke")
check("no guard sounds while black", sounds.played == [])
o = svc.overlays
settled(o)
sent.clear()
move(o, 40, 0)
pump()
check("waking tells the core, as a click does", {"cmd": "owner_click"} in sent, sent)
check("...and the PIN field is revealed", o.primary.revealed)
sounds.played.clear()
svc._play("poke")
check("on the page the sounds are back", "poke" in sounds.played, sounds.played)

print("\n--- the service: wrong PINs still end in BLACKOUT ---")
for _ in range(svc.controller.blackout_after):
    svc._on_pin_checked(False)
pump()
check("BLACKOUT, as with the fog", svc.controller.mode is Mode.BLACKOUT, svc.controller.mode)
check("...shown, not hidden behind black", o._mode == "blackout" and not o.asleep)
check("...attempts counted as ever", svc.controller.attempts == svc.controller.blackout_after)
svc.controller.session_unlocked()
pump()
svc.shutdown()
pump()

svc = make(guard_look="dark")
svc.request_guard()
pump()
check("(asleep)", svc.overlays.asleep)
svc._on_core_event("blocked_key", "Win")
pump()
check("a blocked key while black blacks out as ever", svc.controller.mode is Mode.BLACKOUT
      and not svc.overlays.asleep and svc.overlays.primary.scene.isVisible())
svc.controller.session_unlocked()
pump()

print("\n--- the service: Peek comes back in the look the guard had ---")
svc.request_guard()
pump()
svc.controller.auth_ok()
pump()
check("CHOICE shows its panel, not black", svc.overlays._mode == "choice" and not svc.overlays.asleep)
svc.controller.choose_peek()
pump()
check("(peeking)", svc.controller.mode is Mode.PEEK and not svc.overlays.visible)
svc.controller.lock_now()
pump()
check("back from the Peek in the dark look, black", svc.overlays.visible and svc.overlays.asleep
      and svc.overlays.look == "dark")
svc.controller.auth_ok()
pump()
svc.controller.guard()  # CHOICE timed out / re-guarded
pump()
check("re-guarded from CHOICE: black straight away", svc.overlays.asleep)
svc.controller.session_unlocked()
pump()
svc.shutdown()
pump()

svc = make()
svc.request_guard()
pump()
svc.controller.auth_ok()
svc.controller.choose_peek()
pump()
svc.request_guard()  # a plain request mid-Peek is Lock now, in the guard's own look
pump()
check("a fog guard comes back from a Peek as fog", svc.controller.mode is Mode.FOG
      and not svc.overlays.asleep and svc.overlays.look == "fog")
svc.controller.auth_ok()
svc.controller.choose_peek()
pump()
svc.request_guard(look="dark")
pump()
check("a dark request mid-Peek locks now, dark", svc.controller.mode is Mode.FOG
      and svc.overlays.asleep and svc.look == "dark")
svc.controller.session_unlocked()
pump()
svc.shutdown()
pump()

print("\n--- the service: every rule that refuses a guard refuses a dark one ---")
svc = make(guard_look="dark", guard_auto_after_idle_min=1)
svc.start_blocker = lambda: "an update is waiting for its restart"
svc.request_guard(look="dark")
pump()
check("a start blocker stops a dark request", svc.controller.mode is Mode.IDLE)
svc.start_blocker = lambda: ""
real_idle = win_integration.get_idle_seconds
win_integration.get_idle_seconds = lambda: 3600.0
try:
    svc._auto_armed = True
    svc._check_auto_guard()
    pump()
    check("the auto-guard uses the setting: dark", svc.controller.mode is Mode.FOG
          and svc.look == "dark" and svc.overlays.asleep)
    svc.controller.session_unlocked()
    pump()
    svc.config["guard_look"] = "fog"
    svc._auto_armed = True
    svc._check_auto_guard()
    pump()
    check("...and fog when the setting says fog", svc.look == "fog" and not svc.overlays.asleep)
    svc.controller.session_unlocked()
    pump()
finally:
    win_integration.get_idle_seconds = real_idle
svc.shutdown()
pump()


class FakeSetup:
    """The first-run PIN setup, answered at once."""

    answer = False

    def __init__(self, *a, **k):
        pass

    def exec(self):
        return GS.QDialog.Accepted if FakeSetup.answer else GS.QDialog.Rejected

    def pin(self):
        return "2468"

    def deleteLater(self):
        pass


real_setup = GS.PinSetupDialog
GS.PinSetupDialog = FakeSetup
try:
    svc = make(guard_pin_hash="")
    svc._verifier.hash = lambda pin: svc._on_pin_hashed(guard_auth.hash_pin(pin))
    svc.request_guard(look="dark")
    pump()
    check("no PIN and the setup cancelled: no dark guard either", svc.controller.mode is Mode.IDLE)
    FakeSetup.answer = True
    svc.request_guard(look="dark")
    pump()
    check("a PIN set up first: the guard that follows is the dark one asked for",
          svc.controller.mode is Mode.FOG and svc.look == "dark" and svc.overlays.asleep)
    svc.controller.session_unlocked()
    pump()
    svc.shutdown()
finally:
    GS.PinSetupDialog = real_setup
pump()

# =================================================================================================
print("\n--- the setting ---")
check("guard_look defaults to fog", cfg_module.DEFAULTS["guard_look"] == "fog")
check("guard_look accepts dark", cfg_module._coerce("guard_look", " DARK ") == "dark")
check("...and nothing else", cfg_module._coerce("guard_look", "night") == "fog")
check("guard_dark_hotkey defaults to Ctrl+Alt+Shift+G",
      cfg_module.DEFAULTS["guard_dark_hotkey"] == "ctrl+alt+shift+g")
check("...and can be turned off", cfg_module._coerce("guard_dark_hotkey", "") == "")
check("Settings offers guard_look as a choice", S.CHOICES.get("guard_look") == ("fog", "dark"))
guard_group = dict(S.GROUPS)["Guard"]
check("both live on Settings -> Guard", {"guard_look", "guard_dark_hotkey"} <= set(guard_group))
check("both have help text", "guard_look" in S.HELP and "guard_dark_hotkey" in S.HELP)
check("the shortcut, like the others, takes effect next launch",
      "guard_dark_hotkey" in S.NEEDS_RESTART)

print("\n--- the shortcut, the tray and the wheel ask for the dark look ---")
import main as M  # noqa: E402
import tool_wheel as TW  # noqa: E402
from tray import Tray  # noqa: E402

check("the dark shortcut has its own id", M.GUARD_DARK_HOTKEY_ID not in (
    M.HOTKEY_ID, M.COME_HOTKEY_ID, M.POCKET_HOTKEY_ID, M.MIC_HOTKEY_ID, M.GUARD_HOTKEY_ID))
src_install = inspect.getsource(M.NibApp._install_hotkey)
check("it is registered like guard_hotkey",
      '(GUARD_DARK_HOTKEY_ID, "guard_dark_hotkey", "_guard_dark_hotkey_hwnd")' in src_install)
check("...and unregistered on quit", '(GUARD_DARK_HOTKEY_ID, "_guard_dark_hotkey_hwnd")'
      in inspect.getsource(M.NibApp._on_quit))

asked = []
holder = SimpleNamespace(_on_guard_requested=lambda look=None: asked.append(look), _guarding=True,
                         behavior=SimpleNamespace(tool_picked=lambda key: False))
M.NibApp._on_hotkey_pressed(holder, M.GUARD_DARK_HOTKEY_ID)
M.NibApp._on_hotkey_pressed(holder, M.GUARD_HOTKEY_ID)
check("the dark shortcut asks for dark, Ctrl+Alt+G for the setting's look (even mid-guard)",
      asked == ["dark", None], asked)
asked.clear()
M.NibApp._on_tool_chosen(holder, "guard_dark")
M.NibApp._on_tool_chosen(holder, "guard")
check("the wheel's Dark guard asks for dark, its Guard for the setting's look",
      asked == ["dark", None], asked)

requests = []
blocker = [""]
told = []
holder = SimpleNamespace(
    tool_wheel=SimpleNamespace(close=lambda: None), _guard_start_blocker=lambda: blocker[0],
    guard=SimpleNamespace(active=False, request_guard=lambda **kw: requests.append(kw)),
    _tell=lambda *a, **k: told.append(a), _updating=False)
M.NibApp._on_guard_requested(holder, "dark")
M.NibApp._on_guard_requested(holder)
check("main hands the look on to the service", requests == [{"look": "dark"}, {}], requests)
blocker[0] = "an update is waiting for its restart"
requests.clear()
M.NibApp._on_guard_requested(holder, "dark")
check("an update waiting for its restart refuses a dark guard too", requests == [] and told)

src_items = inspect.getsource(M.NibApp._wheel_items)
check("the wheel offers Dark guard", 'WheelItem("guard_dark", "Dark guard", "guard_dark"' in src_items)
check("...right after Guard", src_items.index('"guard_dark"') > src_items.index('"guard"'))
check("its icon is drawn in code", "guard_dark" in TW.ICONS)
pix = QPixmap(24, 24)
pix.fill(Qt.transparent)
from PySide6.QtGui import QPainter  # noqa: E402

painter = QPainter(pix)
TW.ICONS["guard_dark"](painter)
painter.end()
check("...and paints without raising", not pix.isNull())

tray = Tray(QIcon())
dark_actions = [a for a in tray._menu.actions() if a.text() == "Guard (dark screen)"]
check("the tray has Guard (dark screen)", len(dark_actions) == 1)
texts = [a.text() for a in tray._menu.actions()]
check("...right after Guard the screen",
      texts.index("Guard (dark screen)") == texts.index("Guard the screen") + 1, texts)
emitted = []
tray.guard_dark_requested.connect(lambda: emitted.append(1))
if dark_actions:
    dark_actions[0].trigger()
check("...which asks for the dark guard", emitted == [1])
main_src = inspect.getsource(M.NibApp)
check("main connects it to a dark request",
      'self.tray.guard_dark_requested.connect(lambda: self._on_guard_requested("dark"))' in main_src)

GO.make_fog = real_make_fog

print("\n" + "=" * 60)
print("FAILURES: " + ", ".join(fails) if fails else "guard dark look OK")
sys.exit(1 if fails else 0)
