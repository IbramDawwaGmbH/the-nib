"""His speech bubble: his own lines and every message he shows, in a pixel bubble over his head.

The point of the bubble is that a line in it always means *he* is talking, so the split is pinned
down first: ``_say`` and ``_tell`` both go to the bubble; a message replaces a line of his.
Then the bubble itself - its shape, the typewriter, following him, staying on screen, the voice
blips and their rate limit - and every way the app has of putting him away (a guard, a snooze, a
fullscreen app, ghost mode, being picked up), each of which must take the bubble with him.

The pure helpers and the bare widget need no desktop; the wiring is driven through the real app
object, like smoke_ui, because what matters there is what the app actually does with it.
"""

import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import scratch as _scratch  # noqa: E402

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
SRC = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, SRC)
os.chdir(SRC)
SCRATCH = _scratch()

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import main as M  # noqa: E402
import speech_bubble as SB  # noqa: E402
from behavior import TALK_ANIM, State  # noqa: E402
from config import Config  # noqa: E402
from sound import BLIP_VARIANTS, EVENTS, Sounds  # noqa: E402

fails = []


def check(label, cond, detail=""):
    line = ("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else "")
    print(line.encode("ascii", "replace").decode("ascii"))
    if not cond:
        fails.append(label)


def pump(ms):
    """Let Qt actually run - the pop-in, the typewriter and the hold are all real timers."""
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()


def wait_until(cond, ms):
    """Pump until ``cond()`` holds or ``ms`` runs out. Returns whether it held - a deadline rather
    than a fixed wait, because a single-shot timer chain only ever fires late, never early."""
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.005)
    app.processEvents()
    return bool(cond())


def press(widget):
    QApplication.sendEvent(widget, QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5),
                                               QPointF(5, 5), Qt.LeftButton, Qt.LeftButton,
                                               Qt.NoModifier))


app = QApplication(sys.argv)
SCREEN = QRect(0, 0, 1920, 1080)

print("\n--- the pure helpers ---")
short, long_line = "Hi!", "I have been standing here for quite a while now, you know, just waiting."
check("typing a line takes a little per letter", SB.type_ms("abc") == 3 * SB.CHAR_MS,
      str(SB.type_ms("abc")))
check("...and a pause after a full stop", SB.type_ms("a.") > SB.type_ms("ab"))
check("a short line stays up the minimum", SB.hold_ms(short, M.SAY_MS) == M.SAY_MS,
      str(SB.hold_ms(short, M.SAY_MS)))
check("a long line stays up long enough to type and read",
      SB.hold_ms(long_line, M.SAY_MS) > SB.type_ms(long_line) + SB.READ_BASE_MS,
      str(SB.hold_ms(long_line, M.SAY_MS)))

runs = SB.pixel_rows(20, 8, 10, tail_down=True)
kinds = {k for (_y, _x, _n, k) in runs}
check("the shape is outline, fill and shade only", kinds <= {"o", "f", "s"}, str(kinds))
check("every run has a positive length", all(n > 0 for (_y, _x, n, _k) in runs))
ys = [y for (y, _x, _n, _k) in runs]
check("tail down: the tail hangs below the body", max(ys) == 8 + SB.TAIL_ROWS - 1 and min(ys) == 0,
      f"{min(ys)}..{max(ys)}")
tip = [(x, n) for (y, x, n, _k) in runs if y == max(ys)]
check("...and ends in one pixel under the tail column", tip == [(10, 1)], str(tip))
top_row = sorted((x, n) for (y, x, n, _k) in runs if y == 0)
check("the corners are stepped, not square", top_row and top_row[0][0] == SB.CORNER_INSETS[0],
      str(top_row))
up = SB.pixel_rows(20, 8, 10, tail_down=False)
up_tip = [(x, n) for (y, x, n, _k) in up if y == 0]
check("tail up: the tail tip is the top row", up_tip == [(10, 1)], str(up_tip))
squeezed = SB.pixel_rows(20, 8, 0, tail_down=True)
check("a tail asked for at the very edge is pulled clear of the corner",
      min(x for (_y, x, _n, _k) in squeezed) >= 0
      and max(x + n for (_y, x, n, _k) in squeezed) <= 20)

print("\n--- the bare bubble ---")
bubble = SB.SpeechBubble(1.0)
check("never takes focus", bubble.testAttribute(Qt.WA_ShowWithoutActivating)
      and bool(bubble.windowFlags() & Qt.WindowDoesNotAcceptFocus))
check("a tooltip-class window, like the toast", bool(bubble.windowFlags() & Qt.ToolTip))
signals = {"blip": [], "typed": 0, "finished": 0, "clicked": 0}
bubble.blip.connect(lambda: signals["blip"].append(time.monotonic()))
bubble.typed.connect(lambda: signals.__setitem__("typed", signals["typed"] + 1))
bubble.finished.connect(lambda: signals.__setitem__("finished", signals["finished"] + 1))
bubble.clicked.connect(lambda: signals.__setitem__("clicked", signals["clicked"] + 1))

anchor = QRect(800, 700, 96, 96)
text = "Hello there, nice to see you again."
typing_ms = bubble.say(text, anchor, SCREEN, pixel=2, minimum_ms=600)
check("say shows it at once", bubble.isVisible() and bubble.text() == text)
check("...typing, with letters still to come", bubble.typing)
check("...and reports the typing time, pop-in included",
      typing_ms == SB.type_ms(text) + SB.POP_STEP_MS * len(SB.POP_IN), str(typing_ms))
check("it sits above his head", bubble.geometry().bottom() < anchor.top() + anchor.height() // 2,
      f"{bubble.geometry()} vs {anchor}")
check("...centred over him", abs(bubble.geometry().center().x() - anchor.center().x()) <= 2,
      f"{bubble.geometry().center().x()} vs {anchor.center().x()}")
mask = bubble.mask()
check("masked to its pixels: a corner lets clicks through",
      not mask.isEmpty() and not mask.contains(QPoint(0, 0)))
check("...and the space beside the tail does too",
      not mask.contains(QPoint(0, bubble.height() - 1)))
check("the whole bubble is in whole sprite pixels",
      bubble.width() % 2 == 0 and bubble.height() % 2 == 0, f"{bubble.width()}x{bubble.height()}")

check("the typewriter finishes", wait_until(lambda: signals["typed"] == 1, typing_ms * 3 + 1000),
      str(signals))
check("...and then it is no longer typing", not bubble.typing)
check("...and it is still up to be read", bubble.isVisible())
voiced = sum(1 for ch in text if ch.isalnum())
check("blips while it types: a few, not one a letter",
      2 <= len(signals["blip"]) <= voiced // SB.BLIP_EVERY + 1,
      f"{len(signals['blip'])} blips for {voiced} letters")
gaps = [b - a for a, b in zip(signals["blip"], signals["blip"][1:])]
check("...never closer than the minimum gap (allowing timer slack)",
      all(g >= (SB.BLIP_MIN_GAP_MS - 15) / 1000.0 for g in gaps),
      f"min gap {min(gaps) * 1000:.0f}ms" if gaps else "no gaps")

check("after its hold it pops out by itself",
      wait_until(lambda: not bubble.isVisible(), SB.hold_ms(text, 600) + 1500))
check("...and says it is gone, once", signals["finished"] == 1, str(signals))
check("...with no timer left running", not (bubble._type_timer.isActive()
                                            or bubble._pop_timer.isActive()
                                            or bubble._hold_timer.isActive()))

print("\n--- following him, and staying on screen ---")
bubble.say("Walking along.", anchor, SCREEN, pixel=2, minimum_ms=5000)
before = bubble.pos()
bubble.follow(anchor.translated(150, 0), SCREEN)
check("follows him as he walks", bubble.pos() == before + QPoint(150, 0),
      f"{before} -> {bubble.pos()}")
bubble.follow(anchor.translated(150, 0), SCREEN)
check("...and stays put when he does", bubble.pos() == before + QPoint(150, 0))
bubble.follow(QRect(-40, 700, 96, 96), SCREEN)
check("at the left edge it stays on screen", bubble.x() >= SCREEN.left(), str(bubble.pos()))
check("...and its tail slides along to stay over him, off the middle",
      bubble._tail_col < bubble._cols // 2, f"tail col {bubble._tail_col} of {bubble._cols}")
bubble.follow(QRect(1880, 700, 96, 96), SCREEN)
check("at the right edge too", bubble.geometry().right() <= SCREEN.right(),
      str(bubble.geometry()))
bubble.follow(QRect(800, -30, 96, 96), SCREEN)
check("held up at the top: it flips below him, tail up",
      not bubble._tail_down and bubble.y() > -30 and bubble.y() >= SCREEN.top(),
      str(bubble.geometry()))
bubble.dismiss()
check("dismiss hides it at once", not bubble.isVisible() and not bubble.typing)
check("...with every timer stopped", not (bubble._type_timer.isActive()
                                          or bubble._pop_timer.isActive()
                                          or bubble._hold_timer.isActive()))
bubble.say("Hidden mid-sentence.", anchor, SCREEN, pixel=2, minimum_ms=5000)
bubble.hide()  # not through dismiss: whatever hides it, typing must end
check("hidden some other way, it is not left 'typing'", not bubble.typing)

clicks = signals["clicked"]
bubble.say("Click me.", anchor, SCREEN, pixel=2, minimum_ms=5000)
press(bubble)
check("a click is reported", signals["clicked"] == clicks + 1)
press(bubble)
check("...once, however many times it is clicked on the way out", signals["clicked"] == clicks + 1)
check("...and it pops out", wait_until(lambda: not bubble.isVisible(), 600))
bubble.deleteLater()

print("\n--- the voice blips ---")


class StubEffect:
    """Stands in for QSoundEffect: records what it was asked to do and makes no noise."""

    def __init__(self, name):
        self.name, self.plays, self.stops, self.volume, self.playing = name, 0, 0, None, False

    def setVolume(self, v):  # noqa: N802
        self.volume = v

    def play(self):
        self.plays += 1
        self.playing = True

    def isPlaying(self):  # noqa: N802
        return self.playing

    def stop(self):
        self.stops += 1
        self.playing = False


check("every variant is a sound event", set(BLIP_VARIANTS) <= set(EVENTS))
sound_dir = SCRATCH / "speech_sounds"
sound_dir.mkdir(exist_ok=True)
sound_cfg = {"sound_enabled": True, "sound_volume": 0.3}
sounds = Sounds(sound_dir, sound_cfg)
stubs = {name: StubEffect(name) for name in BLIP_VARIANTS}
sounds._effects.update(stubs)
check("a blip plays", sounds.play("blip") is True and stubs["blip"].plays == 1)
check("...at the volume setting", stubs["blip"].volume == 0.3, str(stubs["blip"].volume))
check("...and one straight after is held back", sounds.play("blip") is False)
sounds.tick(30)
check("...still too soon 30ms later", sounds.play("blip") is False)
sounds.tick(40)
check("...but fine 70ms later", sounds.play("blip") is True)
check("the next one is a different pitch", stubs["blip2"].plays == 1, str(
    {k: v.plays for k, v in stubs.items()}))
check("...and the one still ringing was cut off first, so they never stack",
      stubs["blip"].stops == 1)
sounds.tick(70)
sounds.play("blip")
sounds.tick(70)
sounds.play("blip")
sounds.tick(70)
sounds.play("blip")
check("the four pitches are taken in turn", [stubs[n].plays for n in BLIP_VARIANTS] == [2, 1, 1, 1],
      str([stubs[n].plays for n in BLIP_VARIANTS]))
check("blips keep one shared guard, and no other sound's clock is touched",
      set(sounds._last_played) == {"blip"}, str(set(sounds._last_played)))
state = random.getstate()
sounds.tick(70)
sounds.play("blip")
check("...and picking the pitch draws no random number", random.getstate() == state)
sound_cfg["sound_enabled"] = False
plays = sum(s.plays for s in stubs.values())
sounds.tick(500)
check("with sound off, no blip", sounds.play("blip") is False
      and sum(s.plays for s in stubs.values()) == plays)

print("\n--- the app: his lines and his messages, all in the bubble ---")
path = SCRATCH / "speech_app.json"
path.unlink(missing_ok=True)
cfg = Config(path)
cfg["intro_seen"] = True
cfg["last_seen_date"] = "2026-07-31"
cfg["mic_indicator_enabled"] = False
cfg["camera_indicator_enabled"] = False
cfg["snow"] = "off"
cfg["sound_enabled"] = False
nib = M.NibApp(app, cfg)
pump(300)
# Only these calls move him on from here, so nothing walks him off or starts a nap mid-check.
nib._geometry_timer.stop()
nib._tick_timer.stop()
b = nib.behavior
nib.toast.hide()
nib.bubble.close_silently()
nib.voice._launched -= 120.0  # past the launch quiet
nib.voice._last = None

check("the bubble starts hidden", not nib.speech.isVisible())
check("an ordinary moment does not block a line", not nib._voice_blocked(),
      str({"state": b.state.name, "guard": nib._guard_busy(), "intro": nib._intro_step,
           "focus": b.focus_active, "away": b.away, "paused": b.paused}))
line = nib._say("greeting_short")
check("a line he says goes in his speech bubble", line != "" and nib.speech.isVisible()
      and nib.speech.text() == line, repr(line))
check("...not in the toast", not nib.toast.isVisible())
check("...and his mouth starts moving with it", b.talking)
check("a visible speech bubble counts as already talking", nib._voice_blocked())
check("...so nothing is said over it", nib._say("fond") == "" and nib.speech.text() == line)
check("the tick is quick enough for the voice while it types",
      # +4: _update_tick_rate leaves an interval alone within 4ms of the one it wants.
      nib._tick_timer.interval() <= M.SPEECH_TICK_MS + 4, f"{nib._tick_timer.interval()}ms")

clicked = []
nib._tell("Checking for updates...", sub="Nothing was changed.", on_click=lambda: clicked.append(1))
check("a message goes in his speech bubble too, not the plain toast",
      nib.speech.isVisible() and not nib.toast.isVisible())
check("...and replaces his line, not the other way round",
      nib.speech.text() == "Checking for updates..." and not nib._speech_is_line)
nib._on_speech_clicked()
check("...and a click on it runs the message's own action", clicked == [1])
nib.speech.dismiss()
nib._stop_talking()

# Straight through _speak from here: the voice's gap would refuse a second line, and what is under
# test is the bubble, not the voice.
blips = []
real_play = nib.sounds.play


def counting_play(event):
    result = real_play(event)
    if event == "blip":
        blips.append(result)
    return result


nib.sounds.play = counting_play
nib._speak("Hello again, it is good to see you.")
check("the typewriter finishes in the app", wait_until(lambda: not nib.speech.typing, 4000))
check("...and his mouth stops on the last letter", not b.talking)
check("...it blipped while it typed", len(blips) >= 2, str(len(blips)))
check("...through the sound layer, so with sound off every blip is silent",
      not any(blips), str(blips))
nib.sounds.play = real_play
nib.speech.dismiss()

print("\n--- following him ---")
# Placed by the screen the bubble is kept on, well clear of both edges, so neither he nor the
# bubble is clamped and the two moves can be compared directly.
b.place_at(nib._screen_rect().center().x() - 300)
nib._speak("Watch me go.")
x0, bx0 = b.rect[0], nib.speech.x()
b.place_at(b.x + 150)
nib._sync_speech()
moved = b.rect[0] - x0
check("the bubble follows his rect", moved != 0 and nib.speech.x() - bx0 == moved,
      f"he moved {moved}, the bubble {nib.speech.x() - bx0}")
nib.speech.dismiss()

print("\n--- the talking animation ---")
b._enter(State.IDLE)
b._blinking = False
b._grumble_ms = 0.0
state = random.getstate()
check("start_talking moves his mouth", b.start_talking(1000.0) and b.animator.state == TALK_ANIM,
      b.animator.state)
b.stop_talking()
check("stop_talking shuts it", not b.talking and b.animator.state != TALK_ANIM, b.animator.state)
check("...and neither drew a random number", random.getstate() == state)
check("a line with no time is refused", not b.start_talking(0) and not b.talking)
b.start_talking(200.0)
b.update(250.0, None)
check("a line runs out on its own too", not b.talking and b.animator.state != TALK_ANIM,
      b.animator.state)

print("\n--- the click ---")
nib._speak("Click me if you like.")
heard = []
nib.speech.clicked.connect(lambda: heard.append(True))
spirits = nib.mood.spirits
press(nib.speech)
check("clicking his line is reported", heard == [True])
check("...and counts as noticing him: it lifts his mood", nib.mood.spirits > spirits,
      f"{spirits:.3f} -> {nib.mood.spirits:.3f}")
check("...and the bubble pops out", wait_until(lambda: not nib.speech.isVisible(), 600))
check("...and his mouth stops", not b.talking)

print("\n--- every way he is put away takes the bubble too ---")


def spoken():
    nib._speak("Still here.")
    return nib.speech.isVisible()


up = spoken()
nib._set_guarding(True)
check("a guard takes it down", up and not nib.speech.isVisible() and not b.talking)
nib._speak("Not now.")
check("...and nothing is said during one", not nib.speech.isVisible())
nib._set_guarding(False)
pump(100)
nib.behavior.take_events()

up = spoken()
nib._snooze(600)
check("a snooze takes it down", up and not nib.speech.isVisible())
nib._wake_from_snooze()
pump(100)
nib.behavior.take_events()

fullscreen = M.win.is_fullscreen_on_monitor
try:
    up = spoken()
    M.win.is_fullscreen_on_monitor = lambda monitor: True
    nib._update_fullscreen(nib._monitor)
    check("a fullscreen app takes it down", up and not nib.speech.isVisible())
    M.win.is_fullscreen_on_monitor = lambda monitor: False
    nib._update_fullscreen(nib._monitor)
finally:
    M.win.is_fullscreen_on_monitor = fullscreen
pump(100)
nib.behavior.take_events()

up = spoken()
nib._on_ghost_toggled(True)
check("ghost mode takes it down", up and not nib.speech.isVisible())
nib._on_ghost_toggled(False)
pump(100)

# A way in that does not take the bubble down itself: the tick's own check has to catch it.
up = spoken()
nib._snoozed = True
nib._sync_speech()
check("the tick catches the rest", up and not nib.speech.isVisible())
nib._snoozed = False

up = spoken()
nib._on_drag_started()
check("being picked up takes it down", up and not nib.speech.isVisible()
      and b.state == State.DRAG, b.state.name)
b.drag_end()
for _ in range(240):
    if not b.airborne:
        break
    b.update(16.0, None)

nib._on_quit()
pump(100)
print("\n" + "=" * 62)
print("FAILURES: " + ", ".join(fails) if fails else "speech bubble OK")
sys.exit(1 if fails else 0)
