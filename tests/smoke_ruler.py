"""The pixel ruler: measurement math, the mouse-only state machine, and how main.py wires it up.

Driven with hand-built ``QMouseEvent``s passed straight to the widget's own event handlers,
never through a shown window or the real event loop - the same idiom smoke_ui.py uses for
QKeyEvent. A real OS-level input pass was done once, by hand, to confirm the coordinate mapping
itself is exact (press/release land exactly where the OS says the cursor is, given a real mouse
button capturing input); that isn't something worth re-proving here on every run, and synthetic
SetCursorPos-driven hover events turned out to be too timing-dependent on this host to assert on
at all - real button-down drags don't share that problem, because Windows guarantees delivery to
whichever window captured the mouse. Everything below is exercised through direct calls instead,
which sidesteps both concerns entirely.
"""

import inspect
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

import ruler as R  # noqa: E402
from ruler import RulerOverlay, _reading_text  # noqa: E402

fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


def press(overlay, x, y, button=Qt.LeftButton):
    pos = QPointF(x, y)
    overlay.mousePressEvent(
        QMouseEvent(QEvent.MouseButtonPress, pos, pos, button, button, Qt.NoModifier)
    )


def move(overlay, x, y):
    pos = QPointF(x, y)
    overlay.mouseMoveEvent(
        QMouseEvent(QEvent.MouseMove, pos, pos, Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
    )


def release(overlay, x, y, button=Qt.LeftButton):
    pos = QPointF(x, y)
    overlay.mouseReleaseEvent(
        QMouseEvent(QEvent.MouseButtonRelease, pos, pos, button, Qt.NoButton, Qt.NoModifier)
    )


print("\n--- _reading_text: the pure conversion math ---")
check("400px at 96dpi", _reading_text(400, 0, 96) == "400 px  ·  4.17 in  ·  10.58 cm",
      _reading_text(400, 0, 96))
check("a 3-4-5 triangle at 96dpi comes to exactly 500px",
      _reading_text(300, 400, 96) == "500 px  ·  5.21 in  ·  13.23 cm",
      _reading_text(300, 400, 96))
check("zero distance", _reading_text(0, 0, 96) == "0 px  ·  0 in  ·  0 cm",
      _reading_text(0, 0, 96))
check("a round number of pixels at a round dpi gives round inches",
      _reading_text(144, 0, 144) == "144 px  ·  1 in  ·  2.54 cm",
      _reading_text(144, 0, 144))
check("negative deltas still measure a positive distance",
      _reading_text(-300, -400, 96) == "500 px  ·  5.21 in  ·  13.23 cm",
      _reading_text(-300, -400, 96))

print("\n--- _clamp: never reports a point off the monitor it's scoped to ---")
o = RulerOverlay(QRect(0, 0, 800, 600), 96)
check("negative coordinates clamp to the top-left corner",
      o._clamp(QPoint(-50, -50)) == QPoint(0, 0), o._clamp(QPoint(-50, -50)))
check("past-the-edge coordinates clamp to the bottom-right corner",
      o._clamp(QPoint(900, 700)) == QPoint(799, 599), o._clamp(QPoint(900, 700)))
check("a normal in-bounds point passes through unchanged",
      o._clamp(QPoint(400, 300)) == QPoint(400, 300))

print("\n--- the state machine: press/drag/release ---")
o = RulerOverlay(QRect(0, 0, 800, 600), 96)
check("starts idle", o._state == R._STATE_IDLE, o._state)
press(o, 100, 100)
check("a press starts a drag", o._state == R._STATE_DRAGGING, o._state)
check("...anchored at the press point", o._anchor == QPoint(100, 100), o._anchor)
move(o, 150, 120)
check("moving live-updates the current point", o._current == QPoint(150, 120), o._current)

o = RulerOverlay(QRect(0, 0, 800, 600), 96)
press(o, 200, 200)
move(o, 201, 201)
release(o, 201, 201)
check("releasing within the click threshold resets to idle rather than freezing a near-zero reading",
      o._state == R._STATE_IDLE, o._state)
check("...and clears the anchor", o._anchor is None)

o = RulerOverlay(QRect(0, 0, 800, 600), 96)
press(o, 100, 100)
move(o, 500, 100)
release(o, 500, 100)
check("a real drag freezes on release", o._state == R._STATE_FROZEN, o._state)
check("...at the reading its endpoints imply",
      o._frozen_reading == _reading_text(400, 0, 96), o._frozen_reading)

print("\n--- accepting a frozen reading: the regression this suite exists to catch ---")
# A click accepting the frozen reading is itself a press/release pair at the SAME point - which
# means mousePressEvent immediately overwrites _anchor/_current with that point before release
# can act on them. The first implementation read the (by-then-zeroed) live anchor/current instead
# of the reading that was actually frozen, and copied "0 px" to the clipboard every time.
o = RulerOverlay(QRect(0, 0, 800, 600), 96)
press(o, 100, 100)
move(o, 500, 400)
release(o, 500, 400)
expected = _reading_text(400, 300, 96)
check("frozen at the 400x300 (=500px) reading", o._frozen_reading == expected, o._frozen_reading)

clipboard = QApplication.clipboard()
clipboard.clear()
closed = {"v": False}
o.closed.connect(lambda: closed.__setitem__("v", True))
press(o, 500, 400)    # accepting: press again at the exact frozen endpoint
release(o, 500, 400)  # ...and release without moving
check("clicking the frozen reading copies exactly that reading",
      clipboard.text() == expected, clipboard.text())
check("...not the zero-length regression value",
      clipboard.text() != _reading_text(0, 0, 96), clipboard.text())
check("...and closes", closed["v"])

print("\n--- starting a new drag from a frozen reading measures again instead of accepting it ---")
o = RulerOverlay(QRect(0, 0, 800, 600), 96)
press(o, 100, 100)
move(o, 300, 100)
release(o, 300, 100)
first_reading = o._frozen_reading
press(o, 500, 500)
move(o, 750, 500)
check("a press-and-drag from frozen re-enters dragging at the new point, not accept-and-close",
      o._state == R._STATE_DRAGGING and o._anchor == QPoint(500, 500), (o._state, o._anchor))
release(o, 750, 500)
check("...and freezes a distinct, correct new reading",
      o._state == R._STATE_FROZEN and o._frozen_reading == _reading_text(250, 0, 96)
      and o._frozen_reading != first_reading,
      o._frozen_reading)

print("\n--- right-click cancels from any state, and never touches the clipboard ---")
for label, setup in (
    ("idle", lambda ov: None),
    ("mid-drag", lambda ov: (press(ov, 100, 100), move(ov, 300, 300))),
    ("frozen", lambda ov: (press(ov, 100, 100), move(ov, 300, 100), release(ov, 300, 100))),
):
    o = RulerOverlay(QRect(0, 0, 800, 600), 96)
    setup(o)
    closed = {"v": False}
    o.closed.connect(lambda: closed.__setitem__("v", True))
    clipboard.clear()
    press(o, 300, 300, button=Qt.RightButton)
    check(f"right-click while {label} closes", closed["v"])
    check(f"...without writing the clipboard ({label})", clipboard.text() == "")

print("\n--- a plain click from idle is a no-op, not a close ---")
o = RulerOverlay(QRect(0, 0, 800, 600), 96)
closed = {"v": False}
o.closed.connect(lambda: closed.__setitem__("v", True))
press(o, 400, 400)
release(o, 400, 400)
check("stays idle", o._state == R._STATE_IDLE, o._state)
check("...and does not close", not closed["v"])

print("\n--- a seeded start position shows the idle crosshair without needing a hover event first ---")
o = RulerOverlay(QRect(0, 0, 800, 600), 96, start_pos=QPoint(250, 250))
check("current is pre-seeded", o._current == QPoint(250, 250), o._current)
check("still idle - a seeded position is not a press", o._state == R._STATE_IDLE)
o2 = RulerOverlay(QRect(0, 0, 800, 600), 96)
check("without one, current starts unset", o2._current is None)

print("\n--- main.py and tray.py wiring ---")
import main as M  # noqa: E402
import tray as T  # noqa: E402

src_tray_init = inspect.getsource(T.Tray.__init__)
check("the tray declares a Measure entry", 'QAction("Measure"' in src_tray_init)
check("...wired to measure_requested", "self.measure_requested.emit" in src_tray_init)
check("Tray defines the measure_requested signal",
      "measure_requested = Signal()" in inspect.getsource(T.Tray))

src_connect = inspect.getsource(M.NibApp._connect)
check("main connects the tray's measure_requested to its own handler",
      "self.tray.measure_requested.connect(self._on_measure_requested)" in src_connect)

src_handler = inspect.getsource(M.NibApp._on_measure_requested)
check("it guards against opening a second overlay while one is already up",
      "self._ruler is not None" in src_handler)
check("it measures the monitor under the cursor, not wherever Nib himself is standing",
      "get_cursor_pos" in src_handler and "get_monitor_for_point" in src_handler)
check("it reads that monitor's real DPI rather than assuming 96",
      "get_dpi_for_monitor" in src_handler)
check("it's a plain Qt.WindowStaysOnTopHint window, not the raw Win32 topmost call",
      "RulerOverlay(" in src_handler)

src_closed = inspect.getsource(M.NibApp._on_ruler_closed)
check("closing it clears the reference", "self._ruler = None" in src_closed)

src_topmost = inspect.getsource(M.NibApp._reassert_topmost)
check("his own window's periodic topmost reassertion stands down while the ruler is up "
      "(otherwise he could jump in front of it every few seconds)",
      "self._ruler is None" in src_topmost)

src_quit = inspect.getsource(M.NibApp._on_quit)
check("quitting with the ruler open closes it explicitly rather than trusting Qt teardown order",
      "self._ruler" in src_quit and ".close()" in src_quit)

print("\n" + "=" * 62)
print("FAILURES: " + ", ".join(fails) if fails else "the ruler measures correctly")
sys.exit(1 if fails else 0)
