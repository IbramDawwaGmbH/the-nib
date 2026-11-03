"""The inline calculator: safe parsing, unit conversion, and never dispatching a real calculation."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import scratch as _scratch  # noqa: E402

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

import behavior as B  # noqa: E402
import calculator as C  # noqa: E402
from animator import SpriteSheet  # noqa: E402
from behavior import Behavior, State  # noqa: E402
from bubble import AskBubble  # noqa: E402
from config import Config  # noqa: E402

SCRATCH = _scratch()
fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


def fresh(tag, **overrides):
    path = SCRATCH / f"calc_{tag}.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    for k, v in overrides.items():
        cfg[k] = v
    sheet = SpriteSheet(SRC / "assets" / "nib_sheet.png", SRC / "assets" / "frames.json", 2)
    return cfg, Behavior(cfg, sheet)


def ok(text, expected_result=None):
    r = C.evaluate(text)
    if r is None or not r.ok:
        check(f"{text!r} answers cleanly", False, repr(r))
        return
    if expected_result is not None:
        check(f"{text!r} -> {expected_result!r}", r.result == expected_result, r.result)
    else:
        check(f"{text!r} answers cleanly", True, r.result)


def errors(text, expected_substring=None):
    r = C.evaluate(text)
    if r is None or r.ok:
        check(f"{text!r} is a recognised-but-broken calculation", False, repr(r))
        return
    if expected_substring is not None:
        check(f"{text!r} explains what's wrong", expected_substring in r.result, r.result)
    else:
        check(f"{text!r} explains what's wrong", True, r.result)


def passthrough(text):
    r = C.evaluate(text)
    check(f"{text!r} is left for the assistant", r is None, repr(r))


print("\n--- plain arithmetic ---")
ok("12 * 7", "= 84")
ok("12*7", "= 84")
ok("(1 + 2) * 3", "= 9")
ok("2 ** 10", "= 1,024")
ok("2^10", "= 1,024")          # ^ is accepted as an alias for ** - never as bitwise xor
ok("10 / 4", "= 2.5")
ok("10 // 3", "= 3")
ok("10 % 3", "= 1")
ok("-5 + 3", "= -2")
ok("1,000 + 2,000", "= 3,000")  # thousands separators in the INPUT are understood
ok("2 * pi")
ok("sqrt(16)", "= 4")
ok("min(1,2)", "= 1")           # a function-call comma must survive separator stripping
ok("max(1000,2)", "= 1,000")    # ...even right next to a number that itself has a separator
ok("round(3.14159, 2)", "= 3.14")

print("\n--- broken, but still unmistakably a sum - answered, not forwarded ---")
errors("12 / 0", "divide by zero")
errors("sqrt(-1)")
errors("2**2000", "large")          # exponent guard - refused before Python builds the number
errors("((2**500)**500)**500", "big")  # chained pow guard - a tower, not a single huge exponent

print("\n--- not a calculation at all: every one of these must fall through untouched ---")
passthrough("")
passthrough("   ")
passthrough("42")                       # a bare number is a note, not a request
passthrough("-42")
passthrough("pi")                       # a bare constant, same reasoning
passthrough("why is the sky blue")
passthrough("please multiply 12 by 7")  # arithmetic in ENGLISH is not arithmetic in Python
passthrough("call John in 5 min")
passthrough("y = mx + b")
passthrough("2x + 3 = 7")
passthrough("not sure")
passthrough("True")
passthrough("a" * 500)                  # far past MAX_INPUT_CHARS

print("\n--- refuses to execute anything, however it is dressed up as a sum ---")
passthrough("os.system('rm -rf /')")
passthrough("__import__('os')")
passthrough("().__class__")
passthrough("[x for x in range(10)]")
passthrough("lambda: 1")
passthrough("1 if True else 2")
passthrough("open('config.json')")

print("\n--- adversarial input never hangs or blows up memory ---")
import time  # noqa: E402

adversarial = [
    "9**9**9",
    "((((2**999)**999)**999)**999)**999",
    "(" * 90 + "1" + ")" * 90,
    "1" * 200,
]
for text in adversarial:
    started = time.perf_counter()
    C.evaluate(text)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    check(f"{text[:24]!r}... resolves in well under a second", elapsed_ms < 500, f"{elapsed_ms:.1f}ms")

print("\n--- unit conversion: both sides must be real, known units ---")
ok("3 miles in km", "= 4.82803 km")
ok("10 kg to lb", "= 22.0462 lb")
ok("500 ml to cups", "= 2.11338 cups")
ok("1 gb to mb", "= 1,000 mb")
ok("98.6 f to c", "= 37 c")
ok("100 c to f", "= 212 f")
ok("0 c to k", "= 273.15 k")

print("\n--- 'in' is both a unit and the connector word - the grammar must not be ambiguous ---")
ok("3 in in cm", "= 7.62 cm")     # 3 INCHES in centimetres
ok("5 m in in", "= 196.85 in")    # 5 METRES in inches

print("\n--- dimension mismatches are an error, not silence - both sides were real units ---")
errors("3 miles in kg", "length")
errors("3 miles in kg", "weight")

print("\n--- one side not a real unit at all: this is a sentence, not a mistake ---")
passthrough("10 minutes in advance")
passthrough("3 dollars in euros")    # currency is deliberately unsupported, not just unlisted
passthrough("count me in")

print("\n--- more dimensions: pressure, energy, power, angle, binary data, more area, a year ---")
ok("1 bar to psi", "= 14.5038 psi")
ok("1 atm to kpa", "= 101.325 kpa")
ok("100 kpa to psi")
ok("1 kcal to kj", "= 4.184 kj")
ok("1 wh to j", "= 3,600 j")
ok("1 hp to w", "= 745.7 w")
ok("1 kw to hp")
ok("180 deg to rad")
ok("1 rad to deg")
ok("1 sqkm to sqmi")
ok("1 year to days", "= 365.25 days")

print("\n--- binary data storage gets its own names, not a silent redefinition of kb/mb/... ---")
ok("1 gib to mb")
ok("1 gb to gib")
check("gib is bigger than gb (1024 vs 1000 - a real, deliberate difference)",
      "1,073.74" in C.evaluate("1 gib to mb").result, C.evaluate("1 gib to mb").result)

print("\n--- volume <-> mass: only ever with a named ingredient - see calculator.py's docstring ---")
ok("1 cup sugar in g", "= 200 g of sugar")            # round-trips to the cited 200g/cup exactly
ok("1 cup flour to grams", "= 125 grams of flour")
ok("1 cup butter to grams", "= 227 grams of butter")
ok("1 cup brown sugar to g", "= 220 g of brown sugar")
ok("1 cup powdered sugar to g", "= 120 g of powdered sugar")
ok("1 cup cocoa powder to g", "= 86 g of cocoa powder")
ok("1 cup rice to g", "= 185 g of rice")
ok("200 ml of flour to grams")           # "of" is optional phrasing, not required
ok("100 g flour in ml")                  # the reverse direction works too
ok("100 ml honey to g", "= 142 g of honey")
ok("100 ml olive oil to grams", "= 91 grams of olive oil")
ok("100 ml vegetable oil to grams", "= 92 grams of vegetable oil")
ok("1 cup water to grams", "= 236.588 grams of water")
ok("100 g salt to ml")

print("\n--- ...and bare volume<->mass with NO ingredient stays quiet rather than guessing water ---")
passthrough("200 ml to grams")
passthrough("200 grams to ml")
passthrough("1 cup to g")
passthrough("500 g to l")

print("\n--- ...and an ingredient this table does not know stays quiet too, rather than guess ---")
passthrough("200 ml melted butter to grams")
passthrough("200 ml unicorn tears to grams")
passthrough("100 ml wine to grams")

print("\n--- naming an (irrelevant) ingredient for a same-dimension conversion is harmless ---")
ok("200 grams sugar to kg", "= 0.2 kg")

print("\n--- the density grammar must not misfire on ordinary sentences about ingredients ---")
passthrough("add 2 cups sugar to the bowl")
passthrough("2 cups sugar for the cake")
passthrough("2 cups sugar in the bowl")
passthrough("please add flour to the mix")
passthrough("remember to buy sugar and flour")

print("\n--- number formatting: no float noise, no scientific notation in an everyday range ---")
check("integers keep no decimal point", C.format_number(84.0) == "84", C.format_number(84.0))
check("large integers get thousands separators",
      C.format_number(1000000) == "1,000,000", C.format_number(1000000))
check("float noise from 0.1 + 0.2 is rounded away",
      C.format_number(0.1 + 0.2) == "0.3", C.format_number(0.1 + 0.2))
check("small fractions keep their significant digits",
      C.format_number(0.001234) == "0.001234", C.format_number(0.001234))
check("negative zero reads as plain zero", C.format_number(-0.0) == "0", C.format_number(-0.0))
check("genuinely huge numbers fall back to scientific notation",
      C.format_number(1e20) == "1e+20", C.format_number(1e20))

print("\n--- Behavior.calculated: noticed, but not treated as a prompt ---")
_cfg, b = fresh("noticed")
b._neglect_ms = 5000.0
b._neglect_stage = 2
b.calculated(True)
check("still resets the being-ignored clock, like every real interaction must",
      b._neglect_ms == 0.0 and b._neglect_stage == 0)

_cfg, b = fresh("success")
check("a clean answer plays the reused 'counting' animation",
      b._sheet.has_state(B.CALCULATED_ANIM), "sheet has no 'counting' state")
b.calculated(True)
check("...specifically that one", b._antic == B.CALCULATED_ANIM, b._antic)
check("...held for a real beat, not an instant flash", b._antic_ms > 0, b._antic_ms)

_cfg, b = fresh("error")
b.calculated(False)
check("a broken answer (divide by zero, say) earns no celebration antic",
      b._antic != B.CALCULATED_ANIM, b._antic)

_cfg, b = fresh("untouched")
b._pokes.append(0.0)
b._residual = 3.0
b.mood.nudge("petted")  # give it something a prompt_sent-style nudge would otherwise touch
spirits_before = b.mood.spirits
b.calculated(True)
check("does not clear annoyance the way a real prompt does", len(b._pokes) == 1, len(b._pokes))
check("...or decay the residual either", b._residual == 3.0, b._residual)
check("does not touch the mood nudge a real prompt would give", b.mood.spirits == spirits_before,
      (b.mood.spirits, spirits_before))
check("never enters WORKING - there was nothing to wait on", b._state != State.WORKING, b._state)

print("\n--- the bubble intercepts before it ever emits 'submitted' ---")
bubble = AskBubble(1.0)
sent = []
calced = []
bubble.submitted.connect(lambda mode, text: sent.append((mode, text)))
bubble.calculated.connect(lambda expr, result, ok: calced.append((expr, result, ok)))


def press_enter():
    bubble._input.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Return, Qt.NoModifier, "\r"))


bubble.show_for(bubble.rect(), bubble.rect(), "chat", calculator_enabled=True)
bubble._input.setPlainText("12 * 7")
app.processEvents()
check("the live preview shows the answer before Enter is even pressed",
      bubble._preview.isVisible() and bubble._preview.text() == "= 84", bubble._preview.text())
check("the hint swaps to 'calculate' while a preview is live",
      bubble._hint.text() == "Enter to calculate", bubble._hint.text())
press_enter()
check("a calculation emits 'calculated', never 'submitted'", calced == [("12 * 7", "= 84", True)], calced)
check("...and 'submitted' really did stay silent", sent == [], sent)
check("closing after a clean calculation is not treated as a poke (no 'dismissed')",
      bubble._submitting, "still armed to report a dismissal")

bubble.show_for(bubble.rect(), bubble.rect(), "chat", calculator_enabled=True)
bubble._input.setPlainText("explain recursion to me")
app.processEvents()
check("ordinary prompt text shows no preview", not bubble._preview.isVisible())
check("...and the hint reverts to the ordinary one", "send" in bubble._hint.text())
press_enter()
check("an ordinary prompt still reaches 'submitted' normally",
      sent == [("chat", "explain recursion to me")], sent)

bubble.show_for(bubble.rect(), bubble.rect(), "chat", calculator_enabled=False)
bubble._input.setPlainText("12 * 7")
app.processEvents()
check("calculator_enabled=False shows no preview", not bubble._preview.isVisible())
sent.clear()
press_enter()
check("...and Enter falls through to a normal send instead",
      sent == [("chat", "12 * 7")], sent)
bubble.close()

print("\n--- main wires it up: intercepted before dispatch, and reacts through Behavior ---")
import inspect  # noqa: E402

import main as M  # noqa: E402

src_submitted = inspect.getsource(M.NibApp._on_submitted)
src_open = inspect.getsource(M.NibApp._open_bubble_at)
check("the calculator setting is read live and handed to the bubble each time it opens",
      "calculator_enabled" in src_open)

src_calculated = inspect.getsource(M.NibApp._on_calculated)
check("a calculation reacts through Behavior.calculated, never behavior.prompt_sent",
      "self.behavior.calculated" in src_calculated and "prompt_sent" not in src_calculated)
check("the result is shown on the toast, not the billboard sign (that means the pocket)",
      "self.toast.show_message" in src_calculated)
check("copying the answer is only offered for a clean result",
      'if ok else None' in src_calculated or "if ok else" in src_calculated)

print("\n" + "=" * 62)
print("FAILURES: " + ", ".join(fails) if fails else "the calculator behaves")
sys.exit(1 if fails else 0)
