"""The snippet billboard: grab, accumulate, take down - and standing still while it's up."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import scratch as _scratch  # noqa: E402

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

import behavior as B  # noqa: E402
from animator import SpriteSheet  # noqa: E402
from behavior import Behavior, State  # noqa: E402
from billboard import Billboard  # noqa: E402
from config import Config  # noqa: E402

SCRATCH = _scratch()
fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


def fresh(tag, **overrides):
    path = SCRATCH / f"pocket_{tag}.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    for k, v in overrides.items():
        cfg[k] = v
    sheet = SpriteSheet(SRC / "assets" / "nib_sheet.png", SRC / "assets" / "frames.json", 2)
    return cfg, Behavior(cfg, sheet)


print("\n--- grabbing, accumulating, taking it down ---")
_cfg, b = fresh("basic")
check("empty to start", not b.pocket_full)
check("...and shows nothing", b.pocket_text == "")
check("grabbing blank/whitespace text is refused", not b.pocket_grab("   "))
check("still empty", not b.pocket_full)

check("grabbing real text is accepted", b.pocket_grab("first snippet"))
check("now holding something", b.pocket_full)
check("the sign shows it", b.pocket_text == "first snippet", b.pocket_text)

check("a second grab ADDS to the sign, it does not replace it", b.pocket_grab("second snippet"))
check("both are on there, in order",
      b.pocket_text == "first snippet\n\nsecond snippet", b.pocket_text)

taken = b.pocket_take()
check("taking it down returns everything that was on it",
      taken == "first snippet\n\nsecond snippet", taken)
check("...and empties it", not b.pocket_full)
check("the sign is blank again", b.pocket_text == "")
check("taking an already-empty sign down is just \"\"", b.pocket_take() == "")

print("\n--- grabbing puts him into a braced, gripping-it-hard pose ---")
_cfg, b = fresh("brace")
b.set_strip(1000, 0, 4000)
b.place_at(500)
b.pocket_grab("hold this hard")
check("he braces (the strain pose) rather than idling beside it",
      b._antic == B.POCKET_HOLD_ANIM, b._antic)
check("held indefinitely, not for an antic's usual few seconds", b._antic_ms > 1e8, b._antic_ms)
for _ in range(300):
    b.update(50.0, None)
check("still braced 15 simulated seconds later", b._antic == B.POCKET_HOLD_ANIM, b._antic)
check("...and has not moved", b._x == 500, b._x)

print("\n--- and snaps back to it if something else interrupts him ---")
_cfg, b = fresh("interrupt")
b.pocket_grab("keep gripping")
check("braced immediately", b._antic == B.POCKET_HOLD_ANIM)
# Petting (or a persona costume, or anything else that calls _begin_antic directly) is allowed to
# interrupt an antic - this is what makes sure the brace is what he goes BACK to, rather than
# whatever ordinary idle antic the scheduler would otherwise have picked.
b._begin_antic("pet", 500.0)
check("...knocked into something else", b._antic == "pet")
b._choose_next_action(None)
check("the very next decision snaps him back to the brace",
      b._antic == B.POCKET_HOLD_ANIM, b._antic)

b.pocket_take()
check("taking it down ends the antic's budget", b._antic_ms <= 0)
b.update(50.0, None)
check("...and one more tick actually returns him to idle",
      b._antic is None and b._state == State.IDLE, f"antic={b._antic!r} state={b._state}")

print("\n--- holding the sign keeps him standing ---")
_cfg, b = fresh("standing", wander_enabled=True, antics_enabled=False, world_enabled=False)
b.set_strip(1000, 0, 4000)
b.place_at(500)
b.pocket_grab("hold still")
entered_walk = False
for _ in range(200):
    b._choose_next_action(None)
    if b._state == State.WALK:
        entered_walk = True
check("never enters WALK while the sign is up", not entered_walk)
check("position never moves", b._x == 500, b._x)

_cfg, b2 = fresh("wandering", wander_enabled=True, antics_enabled=False, world_enabled=False)
b2.set_strip(1000, 0, 4000)
b2.place_at(500)
entered_walk2 = False
for _ in range(50):
    b2._choose_next_action(None)
    if b2._state == State.WALK:
        entered_walk2 = True
        break
check("...but wanders normally once nothing is held", entered_walk2)

print("\n--- pinning an empty clipboard: he has a look round for it ---")
_cfg, b = fresh("empty")
b._neglect_stage = 1
b.pocket_empty()
check("he looks around", b._antic == B.POCKET_EMPTY_ANIM, b._antic)
check("...and nothing goes up", not b.pocket_full)
check("...and it counts as you noticing him", b._neglect_stage == 0, b._neglect_stage)

_cfg, b = fresh("empty_sign_up")
b.pocket_grab("already up")
b.pocket_empty()
check("with the sign already up he keeps holding it", b._antic == B.POCKET_HOLD_ANIM, b._antic)

_cfg, b = fresh("empty_paused")
b.paused = True
b.pocket_empty()
check("paused, no look round - it would freeze on its first frame", b.state is State.IDLE,
      b.state)

print("\n--- the pocket never touches costumes ---")
_cfg, b = fresh("nocostume")
b.pocket_grab("snippet")
check("auto_costume is untouched by the pocket - it is a separate widget now", b.auto_costume == "")

print("\n--- main wires it up: grab shows the sign, using it takes the sign down ---")
import inspect  # noqa: E402

import main as M  # noqa: E402

src_pocket = inspect.getsource(M.NibApp._on_pocket_hotkey)
check("reads the real clipboard", "QApplication.clipboard()" in src_pocket)
check("goes through behavior.pocket_grab", "self.behavior.pocket_grab" in src_pocket)
check("puts the text up on the billboard", "self.billboard.show_text" in src_pocket)
check("an empty clipboard gets his look round", "pocket_empty()" in src_pocket)
check("...and a toast saying why nothing went up", "toast.show_message" in src_pocket)

src_bubble = inspect.getsource(M.NibApp._open_bubble_at)
check("an explicit prefill is never overridden by the pocket",
      "if not prefill and self.behavior.pocket_full" in src_bubble)
check("popping the pocket takes the sign down", "self.billboard.take_down()" in src_bubble)

print("\n--- the billboard widget itself ---")
board = Billboard(1.0)
check("always-on-top and frameless, like the toast",
      bool(board.windowFlags() & Qt.WindowStaysOnTopHint)
      and bool(board.windowFlags() & Qt.FramelessWindowHint))
board.show_text("a snippet on the sign", QRect(100, 100, 40, 40), QRect(0, 0, 1920, 1080))
check("visible once shown", board.isVisible())
board.take_down()
check("hidden once taken down", not board.isVisible())

print("\n" + "=" * 62)
print("FAILURES: " + ", ".join(fails) if fails else "the billboard behaves")
sys.exit(1 if fails else 0)
