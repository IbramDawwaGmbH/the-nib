"""Drop-to-OCR: eligibility, the recognize() contract, the Shift+drop branch, and the antic.

Both real engines get exercised against synthetic test images. WinRT is soft-skipped, not
hard-failed, if it genuinely isn't available on this machine (no language pack, package not
installed) - that varies by machine and isn't this repo's to guarantee. The vendored Tesseract
fallback is NOT soft-skipped: vendor/tesseract/ is a checked-in part of the repo, the same as
assets/nib_sheet.png, so its absence is a real failure, not an environment quirk. Everything else
here is fully deterministic and does not depend on either engine.
"""

import inspect
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import scratch as _scratch  # noqa: E402

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QDropEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication([])

import ocr as O  # noqa: E402
from animator import SpriteSheet  # noqa: E402
from behavior import Behavior, State  # noqa: E402
from config import Config  # noqa: E402
from mascot_window import MascotWindow  # noqa: E402

SCRATCH = _scratch()
fails = []


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


def fresh(tag, **overrides):
    path = SCRATCH / f"ocr_{tag}.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    for k, v in overrides.items():
        cfg[k] = v
    sheet = SpriteSheet(SRC / "assets" / "nib_sheet.png", SRC / "assets" / "frames.json", 2)
    return cfg, Behavior(cfg, sheet)


def wait_for(result_dict, timeout=15.0):
    deadline = time.monotonic() + timeout
    while "text" not in result_dict and time.monotonic() < deadline:
        time.sleep(0.02)
    return "text" in result_dict


def recognize_sync(path, timeout=15.0):
    result = {}
    O.recognize(path, lambda text, error: result.update(text=text, error=error))
    wait_for(result, timeout)
    return result.get("text"), result.get("error")


print("\n--- is_ocr_eligible: what a Shift+drop actually catches ---")
for name, expected in [
    ("screenshot.png", True), ("photo.JPG", True), ("scan.jpeg", True),
    ("icon.bmp", True), ("meme.gif", True), ("page.tiff", True),
    ("document.pdf", False), ("notes.txt", False), ("archive.zip", False), ("noextension", False),
]:
    got = O.is_ocr_eligible(name)
    check(f"{name!r} eligible={expected}", got == expected, got)

print("\n--- recognize(): the deterministic failure paths, no WinRT needed ---")
text, error = recognize_sync(str(SCRATCH / "definitely_does_not_exist_xyz.png"))
check("a missing file fails fast with a plain reason", text is None and bool(error), (text, error))

big_path = SCRATCH / "ocr_oversized.bin"
with open(big_path, "wb") as f:
    f.seek(O.MAX_IMAGE_BYTES + 1)
    f.write(b"\0")
text, error = recognize_sync(str(big_path))
check("an oversized file is rejected before any decode is attempted",
      text is None and "large" in (error or ""), (text, error))
big_path.unlink()

def _font(size):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.truetype(r"C:\Windows\Fonts\segoeui.ttf", size)


print("\n--- recognize(): a real image, via whichever engine this machine actually serves it with ---")
img_path = str(SCRATCH / "ocr_smoke_test.png")
img = Image.new("RGB", (700, 160), "white")
draw = ImageDraw.Draw(img)
draw.text((20, 55), "Nib reads this", fill="black", font=_font(40))
img.save(img_path)

text, error = recognize_sync(img_path)
winrt_available = not (text is None and error in (
    "OCR isn't installed", "no language pack with text recognition is installed"
))
if not winrt_available:
    print("  SKIP  WinRT unavailable on this machine (no language pack / package) - not a failure "
          "of this suite, and recognize() falling back below covers the same code path anyway")
words = set((text or "").lower().split())
check("a real screenshot-style image is read correctly, one engine or the other",
      {"nib", "reads", "this"} <= words, (text, error))

print("\n--- recognize(): callback fires off-thread for a real (non-fast-rejected) call ---")
caller_thread = threading.current_thread()
result = {}
start = time.monotonic()
O.recognize(img_path,
            lambda text, error: result.update(text=text, error=error, thread=threading.current_thread()))
returned_fast = (time.monotonic() - start) < 0.1
wait_for(result)
check("recognize() does not block the caller", returned_fast)
check("the callback runs on a different thread than the caller",
      result.get("thread") is not caller_thread)

print("\n--- recognize(): a relative path still works (WinRT rejects those outright - regression) ---")
cwd_before = Path.cwd()
os.chdir(SCRATCH)
try:
    text, error = recognize_sync("ocr_smoke_test.png")
finally:
    os.chdir(cwd_before)
check("a relative path is resolved before either engine ever sees it",
      {"nib", "reads", "this"} <= set((text or "").lower().split()), (text, error))

print("\n--- vendor/tesseract: the checked-in fallback engine itself, in isolation ---")
check("the vendored tesseract.exe is actually present",
      O._TESSERACT_EXE.exists(), O._TESSERACT_EXE)
check("the vendored eng.traineddata is actually present",
      (O._TESSERACT_DIR / "tessdata" / "eng.traineddata").exists())
check("the vendored LICENSE (Apache-2.0) is actually present",
      (O._TESSERACT_DIR / "LICENSE").exists())

small_text_path = str(SCRATCH / "ocr_small_text.png")
small_img = Image.new("RGB", (1200, 60), "white")
ImageDraw.Draw(small_img).text((10, 20), "Settings Help Notifications Sign out now",
                                fill="black", font=_font(10))
small_img.save(small_text_path)
tess_text = O._recognize_tesseract(small_text_path)
check("Tesseract, called directly, reads small (10px) UI-style text correctly with its upscale step",
      {"settings", "help", "sign", "out", "now"} <= set((tess_text or "").lower().split()), tess_text)

# Regression: convert("L") straight from RGBA reads through whatever colour a transparent pixel
# happens to carry, which is frequently black (a screenshot tool's "transparent" background can
# have RGB=(0,0,0) under alpha=0) - black text on what becomes a black background OCRs as nothing.
# White-composited RGB=(255,255,255) under alpha=0 masked this in an earlier, less careful test.
rgba_path = str(SCRATCH / "ocr_rgba_transparent.png")
rgba_img = Image.new("RGBA", (500, 120), (0, 0, 0, 0))
ImageDraw.Draw(rgba_img).text((10, 30), "Rgba Alpha Test", fill=(0, 0, 0, 255), font=_font(40))
rgba_img.save(rgba_path)
rgba_text = O._recognize_tesseract(rgba_path)
check("a transparent PNG with black RGB under alpha=0 is composited onto white, not read as black",
      "rgba alpha test" in (rgba_text or "").lower(), rgba_text)

# Regression: MAX_IMAGE_BYTES only bounds the *compressed* file. A small, flat-colour PNG can
# still decode to an enormous pixel buffer - Pillow's own bomb guard only hard-rejects above 2x
# its default threshold, everything under that just warns and proceeds - and doubling that again
# in the upscale step would make a hostile file worse, not safer.
bomb_path = str(SCRATCH / "ocr_decompression_bomb.png")
Image.new("L", (1500, 70000), 255).save(bomb_path)  # ~0.2MB file, 105M decoded pixels
bomb_start = time.monotonic()
try:
    O._recognize_tesseract(bomb_path)
    check("a decompression-bomb-shaped image is rejected before the upscale step", False)
except ValueError:
    check("a decompression-bomb-shaped image is rejected before the upscale step, and fast",
          time.monotonic() - bomb_start < 2.0, time.monotonic() - bomb_start)

# Regression test for a real bug: the first version of the upscale step scaled to a *target*
# minimum edge, which blew a short-but-wide crop (a single line of large text) up by 10x and
# broke a clean read that worked fine unscaled. A flat, capped multiplier can't do that - proven
# here against exactly the shape that broke it (500x120, the short edge is the *height*).
wide_short_path = str(SCRATCH / "ocr_wide_short.png")
wide_img = Image.new("RGB", (500, 120), "white")
ImageDraw.Draw(wide_img).text((10, 30), "End To End Check", fill="black", font=_font(40))
wide_img.save(wide_short_path)
wide_text = O._recognize_tesseract(wide_short_path)
check("a short, wide crop of large text isn't over-upscaled into a misread",
      "end to end check" in (wide_text or "").lower(), wide_text)

check("Tesseract is spawned with CREATE_NO_WINDOW (pythonw has no console for it to reuse)",
      "creationflags=subprocess.CREATE_NO_WINDOW" in inspect.getsource(O._recognize_tesseract))

print("\n--- recognize(): WinRT failing for ANY reason falls through to Tesseract, not a hard error ---")
_orig_winrt = O._recognize_winrt


async def _boom(path):
    raise RuntimeError("simulated WinRT breakage")


O._recognize_winrt = _boom
try:
    text, error = recognize_sync(img_path)
finally:
    O._recognize_winrt = _orig_winrt
check("a broken WinRT path still ends in a real result via the Tesseract fallback",
      {"nib", "reads", "this"} <= set((text or "").lower().split()), (text, error))

print("\n--- recognize(): both engines failing is still one plain, friendly message ---")
O._recognize_winrt = _boom


def _boom_tess(path):
    raise RuntimeError("simulated Tesseract breakage too")


_orig_tess = O._recognize_tesseract
O._recognize_tesseract = _boom_tess
try:
    text, error = recognize_sync(img_path)
finally:
    O._recognize_winrt = _orig_winrt
    O._recognize_tesseract = _orig_tess
check("both engines failing reports the same plain message ocr.py always used for a bad read",
      text is None and error == "couldn't read that image", (text, error))

print("\n--- Behavior.read_image: same shape as calculated, different animation ---")
_cfg, b = fresh("noticed")
b._neglect_ms = 5000.0
b._neglect_stage = 2
b.read_image(True)
check("resets the being-ignored clock like every real interaction must",
      b._neglect_ms == 0.0 and b._neglect_stage == 0)

_cfg, b = fresh("success")
check("the sheet actually has the reused 'look_around' state",
      b._sheet.has_state("look_around"), "sheet has no 'look_around' state")
b.read_image(True)
check("a successful read plays 'look_around'", b._antic == "look_around", b._antic)
check("...held for a real beat, not an instant flash", b._antic_ms > 0, b._antic_ms)

_cfg, b = fresh("failure")
b.read_image(False)
check("a failed read (no text found, OCR unavailable, ...) earns no antic",
      b._antic != "look_around", b._antic)

_cfg, b = fresh("prompt-untouched")
b._pokes.append(0.0)
b.mood.nudge("petted")
spirits_before = b.mood.spirits
b.read_image(True)
check("does not clear annoyance the way a real prompt does", len(b._pokes) == 1, len(b._pokes))
check("does not touch the mood nudge a real prompt would give", b.mood.spirits == spirits_before)

_cfg, b = fresh("states")
for state in (State.SLEEP, State.WAKE, State.DRAG, State.HIDDEN):
    b._state = state
    b._antic = None
    b.read_image(True)
    check(f"no antic while {state}", b._antic is None, b._antic)

print("\n--- mascot_window.dropEvent: the Shift+single-image branch, and everything that isn't it ---")
window = MascotWindow(96, 96)
captured = {"ocr": [], "files": []}
window.image_ocr_requested.connect(lambda p: captured["ocr"].append(p))
window.files_dropped.connect(lambda p: captured["files"].append(p))


def drop(paths, modifiers=Qt.NoModifier):
    captured["ocr"].clear()
    captured["files"].clear()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    event = QDropEvent(QPointF(10, 10), Qt.CopyAction, mime, Qt.LeftButton, modifiers)
    window.dropEvent(event)


drop(["C:/x/photo.png"], Qt.ShiftModifier)
check("Shift + a single image -> image_ocr_requested, not files_dropped",
      captured["ocr"] == ["C:\\x\\photo.png"] and not captured["files"], captured)

drop(["C:/x/photo.png"])
check("no Shift -> falls through to the ordinary files_dropped path",
      captured["files"] == [["C:\\x\\photo.png"]] and not captured["ocr"], captured)

drop(["C:/x/photo.png", "C:/x/photo2.png"], Qt.ShiftModifier)
check("Shift + MULTIPLE images -> still the ordinary path, not OCR (ambiguous which to read)",
      captured["files"] and not captured["ocr"], captured)

drop(["C:/x/document.pdf"], Qt.ShiftModifier)
check("Shift + a non-image -> the ordinary path, unaffected by the modifier",
      captured["files"] and not captured["ocr"], captured)

drop(["C:/x/document.pdf"])
check("a perfectly ordinary drop is completely untouched by any of this",
      captured["files"] == [["C:\\x\\document.pdf"]] and not captured["ocr"], captured)

# Windows Explorer's IDataObject offers copy, move AND link as possible effects on every file
# drag, and proposes DROPEFFECT_MOVE - not copy - whenever Shift is held; that is the shell's own
# "move" gesture. (A QDropEvent built with only Qt.MoveAction as its possible actions, as earlier
# smoke tests in this file do, can never model that - Qt clamps setDropAction() to whatever the
# event's possibleActions() allows, so it silently no-ops. Real Explorer drags always include
# CopyAction among the possible actions; only the *proposed* one shifts with the modifier.) Nib
# never moves or copies a dropped file anywhere; it only ever reads the path (or, for OCR, the
# bytes). If dropEvent honoured a MoveAction proposal, Explorer would believe the drop moved the
# file and delete the source afterwards - silently destroying whatever the user just Shift-dropped,
# image or not. dropEvent must force Qt.CopyAction regardless of what got proposed.
_shell_actions = Qt.CopyAction | Qt.MoveAction | Qt.LinkAction
for paths, modifiers, label in (
    (["C:/x/photo.png"], Qt.ShiftModifier, "the OCR branch"),
    (["C:/x/document.pdf"], Qt.NoModifier, "an ordinary drop"),
):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    event = QDropEvent(QPointF(10, 10), _shell_actions, mime, Qt.LeftButton, modifiers)
    window.dropEvent(event)
    check(f"{label} forces CopyAction even when the shell proposed MoveAction "
          "(else the source file gets deleted)",
          event.dropAction() == Qt.CopyAction, event.dropAction())

print("\n--- main.py and tray.py wiring ---")
import main as M  # noqa: E402

check("a dedicated Qt bridge exists to cross the OCR worker thread safely",
      "class _OcrBridge" in inspect.getsource(M) and "Signal(str, str)" in inspect.getsource(M._OcrBridge))

src_connect = inspect.getsource(M.NibApp._connect)
check("main connects the window's image_ocr_requested to its own handler",
      "self.window.image_ocr_requested.connect(self._on_image_ocr_requested)" in src_connect)

src_requested = inspect.getsource(M.NibApp._on_image_ocr_requested)
check("it calls ocr.recognize rather than reimplementing anything",
      "ocr.recognize(" in src_requested)

src_finished = inspect.getsource(M.NibApp._on_ocr_finished)
check("success opens the bubble prefilled, the same destination a text drop uses",
      "_open_bubble_at" in src_finished and "prefill" in src_finished)
check("failure shows a toast instead of the bubble",
      "toast.show_message" in src_finished)
check("extracted text reacts through Behavior.read_image, never prompt_sent",
      "self.behavior.read_image" in src_finished and "prompt_sent" not in src_finished)
check("the char-length clamp used elsewhere for provider deep-links is reused here too",
      "max_chars_for" in src_finished)

print("\n" + "=" * 62)
print("FAILURES: " + ", ".join(fails) if fails else "drop-to-OCR behaves")
sys.exit(1 if fails else 0)
