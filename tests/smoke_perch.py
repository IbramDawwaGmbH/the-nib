"""Perching on a window: drop him on it and he stands there, follows it, and pins it on top -
the one deliberate, narrowly-scoped exception to never touching another window's geometry."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import scratch as _scratch  # noqa: E402

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

import win_integration as win  # noqa: E402
from animator import SpriteSheet  # noqa: E402
from behavior import Behavior, State  # noqa: E402
from config import Config  # noqa: E402

SCRATCH = _scratch()
fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


def fresh(tag, **overrides):
    path = SCRATCH / f"perch_{tag}.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    for k, v in overrides.items():
        cfg[k] = v
    sheet = SpriteSheet(SRC / "assets" / "nib_sheet.png", SRC / "assets" / "frames.json", 2)
    b = Behavior(cfg, sheet)
    b.set_strip(1000, 0, 4000)
    b.place_at(500)
    return cfg, b


WINDOW = (400, 300, 900, 700)  # left, top, right, bottom - a plausible app window

print("\n--- a still release still perches ---")
_cfg, b = fresh("gentle")
b.place_at(440)
b.drag_start()
for _ in range(3):
    b.drag_to(440, 300)  # not moving between samples - a real placement, not a flick
    b.update(50.0, None)
b.drag_end(perch_rect=WINDOW)
check("perches on a still release", b.perched)
check("in State.PERCH", b.state == State.PERCH, b.state)

print("\n--- a flick over a window flies through it instead of sticking ---")
_cfg, b = fresh("flick")
b.place_at(440)
b.drag_start()
x = 440
for _ in range(4):
    x += 40  # fast movement each sample - a real flick, well past the perch's speed gate
    b.drag_to(x, 300)
    b.update(16.0, None)
b.drag_end(perch_rect=WINDOW)
check("does not perch on a fast release", not b.perched)
check("flies/falls instead, same as anywhere else", b._falling)
check("left DRAG for IDLE, not PERCH", b.state != State.PERCH, b.state)

print("\n--- landing on a window ---")
_cfg, b = fresh("land")
check("not perched to start", not b.perched)
b.place_at(440)  # drop point near the window's left edge
b.perch_on(WINDOW)
check("perched now", b.perched)
check("in State.PERCH", b.state == State.PERCH, b.state)
check("feet welded to the window's top edge",
      b.top == WINDOW[1] - (b.sheet.frame_h - b.sheet.baseline_offset) * b.sheet.scale, b.top)
check("standing within the window's width",
      WINDOW[0] <= b.x <= WINDOW[2] - b.sprite_w, b.x)
check("roughly where he was dropped, not snapped to centre", b.x < 500, b.x)

print("\n--- never wanders while perched ---")
_cfg, b = fresh("stays", wander_enabled=True)
b.perch_on(WINDOW)
entered_walk = False
for _ in range(200):
    b._choose_next_action(None)
    if b.state == State.WALK:
        entered_walk = True
check("never enters WALK", not entered_walk)
check("still perched after 200 decisions", b.perched)
check("position never drifts off the window",
      WINDOW[0] <= b.x <= WINDOW[2] - b.sprite_w, b.x)

print("\n--- interruption and reassertion ---")
_cfg, b = fresh("interrupt")
b.perch_on(WINDOW)
b._begin_antic("pet", 500.0)
check("...knocked into an antic", b.state == State.ANTIC)
check("...but the rectangle is still held, so top stays on the window",
      b.top == WINDOW[1] - (b.sheet.frame_h - b.sheet.baseline_offset) * b.sheet.scale, b.top)
check("...and perched still reads True - it is keyed on the rectangle, not the state", b.perched)
b._choose_next_action(None)
check("the very next decision snaps him back to State.PERCH", b.state == State.PERCH, b.state)

print("\n--- following a move or a resize ---")
_cfg, b = fresh("follow")
b.place_at(650)  # dropped near the right-hand side of the window
b.perch_on(WINDOW)
frac_x = b.x
moved = (700, 300, 1200, 700)  # slid 300px right, same size
b.update_perch(moved)
check("follows the window right", b.x > frac_x, b.x)
check("keeps the same relative spot along its width",
      abs((b.x - moved[0]) - (frac_x - WINDOW[0])) < 1.0, (b.x, frac_x))

resized = (700, 300, 900, 700)  # narrowed to a quarter of the width
b.update_perch(resized)
check("stays within a narrower window too",
      resized[0] <= b.x <= resized[2] - b.sprite_w, b.x)

print("\n--- the window disappearing ---")
_cfg, b = fresh("gone")
b.perch_on(WINDOW)
b.update_perch(None)
check("no longer perched", not b.perched)
check("steps down by falling, not by teleporting", b._falling)
check("state left PERCH", b.state != State.PERCH, b.state)

print("\n--- grabbing him again ends it ---")
_cfg, b = fresh("grabbed")
b.perch_on(WINDOW)
b.drag_start()
check("perch cleared the instant he's picked back up", not b.perched)
check("now dragging", b.state == State.DRAG)

print("\n--- getting angry releases the perch honestly ---")
_cfg, b = fresh("angry", annoyance_threshold=1)
b.perch_on(WINDOW)
b.bubble_dismissed()  # one poke is enough to cross the (lowered) threshold
check("no longer perched", not b.perched)
check("the storm-off proceeds as normal", b.state == State.ANGRY, b.state)

print("\n--- being called over releases the perch honestly ---")
_cfg, b = fresh("comehere")
b.perch_on(WINDOW)
b.come_here(2000)
check("no longer perched", not b.perched)
check("he actually walks", b.state == State.WALK, b.state)

print("\n--- resuming after a fullscreen hide elsewhere ---")
_cfg, b = fresh("hidden")
b.perch_on(WINDOW)
b.set_hidden(True)
check("hidden", b.state == State.HIDDEN)
check("...but the rectangle survives the hide", b.perched)
b.set_hidden(False)
check("comes back standing on the window, not reset to idle", b.state == State.PERCH, b.state)

print("\n--- win_integration's window lookup does not crash ---")
try:
    result = win.find_window_at(-99999, -99999)
    check("an absurd point finds nothing rather than raising", result is None, result)
except Exception as exc:  # pragma: no cover - this is the failure itself
    check("find_window_at did not raise", False, repr(exc))
try:
    result = win.track_window(0)
    check("tracking hwnd 0 returns None rather than raising", result is None, result)
except Exception as exc:  # pragma: no cover - this is the failure itself
    check("track_window did not raise", False, repr(exc))

print("\n--- set_topmost, against a real window ---")
from PySide6.QtWidgets import QWidget  # noqa: E402

probe = QWidget()
probe.resize(200, 100)
probe.move(50, 50)
probe.show()
app.processEvents()
probe_hwnd = int(probe.winId())
check("pins a real window", win.set_topmost(probe_hwnd, True))
check("unpins it again", win.set_topmost(probe_hwnd, False))
check("hwnd 0 fails safely rather than raising", win.set_topmost(0, True) is False)
check("a bogus hwnd fails safely rather than raising", win.set_topmost(999_999_999, True) is False)
probe.close()

print("\n--- main.py delays the pin, and wires it only to a real, held perch ---")
import inspect  # noqa: E402

import main as M  # noqa: E402

src_finished = inspect.getsource(M.NibApp._on_drag_finished)
check("landing on a window does not pin it immediately - _tick's dwell decides that",
      "win.set_topmost(" not in src_finished)
check("...found through the read-only lookup, not a fresh search of its own",
      "win.find_window_at(" in src_finished)

src_tick = inspect.getsource(M.NibApp._tick)
check("standing on it undisturbed long enough pins it",
      "win.set_topmost(self._perched_hwnd, True)" in src_tick)
check("...gated by the dwell delay, not the drop itself",
      "PERCH_PIN_DELAY_MS" in src_tick)
check("leaving the perch some other way unpins it, if it was ever actually pinned",
      "win.set_topmost(self._perched_hwnd, False)" in src_tick)

src_quit = inspect.getsource(M.NibApp._on_quit)
check("quitting mid-perch unpins rather than abandoning it",
      "win.set_topmost(self._perched_hwnd, False)" in src_quit)

print("\n--- a pinned window shows on the tray icon, since he can end up hidden behind it ---")
src_badge = inspect.getsource(M.NibApp._refresh_pin_badge)
check("the tray icon is redrawn to reflect the pin state", "self.tray.set_icon" in src_badge)
check("...called once the dwell actually pins it", "self._refresh_pin_badge()" in src_tick)

print("\n" + "=" * 62)
print("FAILURES: " + ", ".join(fails) if fails else "perching behaves")
sys.exit(1 if fails else 0)
