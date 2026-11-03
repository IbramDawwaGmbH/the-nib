"""Reading text out of an image you dropped on him: Windows' own on-device OCR engine first,
falling back to a vendored Tesseract binary if that fails for any reason.

Windows.Media.Ocr is tried first - it runs entirely on the device, needs no network connection,
and adds no separate model or binary to ship. The tradeoff, beyond the async WinRT plumbing
below, is that Microsoft documents it as supported only for apps with package identity (MSIX);
Nib ships as a plain exe, an unsupported-but-empirically-working scenario a future Windows update
could break outright. ``vendor/tesseract/`` - a ~29MB hand-trimmed subset of the official Windows
build (Apache-2.0, see ``vendor/tesseract/LICENSE`` and ``tools/vendor_tesseract.py``) - exists so
that breaking doesn't mean the feature just stops: any WinRT failure, not only a missing engine,
falls through to it.

Deliberately Qt-free, in ``win_integration``'s sense rather than ``calculator``'s: this isn't
platform-independent pure logic, it's a wrapped boundary to genuinely Windows-only capabilities,
with the same "a failure logs and returns a documented fallback rather than taking the app down"
contract - just async for the WinRT half, since that's the shape WinRT itself is, and a
subprocess for the Tesseract half. ``recognize``'s callback fires from the worker thread, not the
Qt one; the caller in ``main.py`` is responsible for marshaling back across (a Qt Signal, emitted
from inside the callback, is the supported way to do that and is what it actually uses).
"""

from __future__ import annotations

import asyncio
import io
import logging
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

#: What counts as "an image you might want read" for the purposes of a Shift+drop. Deliberately
#: not the full list of formats the decoder could technically open - these are what a screenshot
#: or a photo of text actually shows up as.
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff"})

#: A generous cap, not a realistic one - screenshots are typically well under 5MB. It exists so a
#: pathologically huge dropped file fails fast with a plain message instead of tying up a worker
#: thread for a long, unpredictable decode.
MAX_IMAGE_BYTES = 25 * 1024 * 1024

#: A flat multiplier, not a target size: measured against real 9-14px UI text (the range
#: screenshots actually use), scale=1 genuinely misreads it ("Notfiations", "Sinoutof") and
#: scale=2 always fixed it, with scale=3 adding nothing further. Scaling to a target *minimum*
#: edge instead - the first version of this - silently blew up a short, wide crop (a single line
#: of 40px text, 500x120) to 10x and made a clean read fail. Windows.Media.Ocr doesn't get the
#: same treatment because it isn't sensitive to scale the same way.
#:
#: Applied unconditionally rather than only below some "probably already big enough" size - a
#: full 1920x1080 screenshot with normal 14px text reads identically at 1x and 2x (measured), so
#: there was no accuracy case for skipping it there; the only real cost of always upscaling is
#: time and memory, which the pixel cap below already bounds.
_UPSCALE_FACTOR = 2.0

#: A generous cap on *decoded* pixels, not a realistic one - comfortably above even a wide
#: multi-monitor screenshot (a 3x4K span is ~25M px). Exists because MAX_IMAGE_BYTES only bounds
#: the compressed file; a small, flat-colour PNG can still decode to an enormous buffer, and
#: doubling that again in the upscale step below would make it worse, not safer.
_MAX_PIXELS = 40_000_000

#: Tesseract has no timeout of its own; this stands in for one so a pathological image can't hang
#: a worker thread forever.
_TESSERACT_TIMEOUT_S = 20


def _base_dir() -> Path:
    """Same convention as main.py's ``_assets_dir``: next to the source, or inside the bundle."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        return Path(base) if base else Path(sys.executable).parent
    return Path(__file__).parent


_TESSERACT_DIR = _base_dir() / "vendor" / "tesseract"
_TESSERACT_EXE = _TESSERACT_DIR / "tesseract.exe"
_TESSDATA_DIR = _TESSERACT_DIR / "tessdata"


def is_ocr_eligible(path: str) -> bool:
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


class OcrUnavailable(Exception):
    """Neither OCR engine could be used - this image is going to stay unread."""


async def _recognize_winrt(path: str) -> str | None:
    # Imported here, not at module level: these packages are a normal requirements.txt entry,
    # but importing lazily means the rest of this module - is_ocr_eligible in particular - stays
    # usable even where they aren't installed, and lets a missing/broken install fail as an
    # ordinary "try the fallback" rather than an import error taking the whole app down.
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage import StorageFile

    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise OcrUnavailable("no language pack with text recognition installed")

    storage_file = await StorageFile.get_file_from_path_async(path)
    stream = await storage_file.open_read_async()
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    result = await engine.recognize_async(bitmap)
    return result.text


def _recognize_tesseract(path: str) -> str | None:
    if not _TESSERACT_EXE.exists():
        raise OcrUnavailable("no vendored Tesseract binary")

    # Imported here, not at module level, for the same reason as the WinRT imports above -
    # Pillow is a real runtime dependency now, but a broken install should degrade to "OCR isn't
    # available" rather than take the whole app down on import.
    from PIL import Image

    with Image.open(path) as img:
        # MAX_IMAGE_BYTES caps the *compressed* file; it says nothing about decompressed size, and
        # a small, heavily-compressible file (a huge flat-colour PNG) can decode to an enormous
        # pixel buffer - Pillow's own bomb guard only hard-rejects above 2x its default threshold,
        # everything under that just warns and proceeds. Rejecting here, before the upscale step
        # below, is what stops such a file from being doubled again on top of that.
        if img.width * img.height > _MAX_PIXELS:
            raise ValueError(f"decoded image is {img.width}x{img.height}, too large to OCR")
        # convert("L") straight from RGBA/LA/P-with-transparency reads through whatever colour a
        # transparent pixel happens to carry, which is frequently black - a screenshot tool's
        # "transparent" background can turn into a black one, and black text on a black background
        # OCRs as nothing at all. Compositing onto white first (real alpha blending, not just
        # dropping the channel) is what a plain .convert("RGB") on such an image does not do.
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            img = img.convert("RGBA")
            background = Image.new("RGBA", img.size, (255, 255, 255, 255))
            img = Image.alpha_composite(background, img)
        img = img.convert("L")
        # Skipped only when upscaling would breach the pixel cap above, not below some
        # "probably fine already" size guess - see _UPSCALE_FACTOR for why there's no such guess.
        if img.width * img.height * _UPSCALE_FACTOR**2 <= _MAX_PIXELS:
            size = (round(img.width * _UPSCALE_FACTOR), round(img.height * _UPSCALE_FACTOR))
            img = img.resize(size, Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")

    # Piped in over stdin and read back over stdout - no temp file ever hits disk, so there is
    # nothing left over to clean up (or to forget to).
    result = subprocess.run(
        [str(_TESSERACT_EXE), "-", "stdout", "--tessdata-dir", str(_TESSDATA_DIR)],
        input=buf.getvalue(),
        capture_output=True,
        timeout=_TESSERACT_TIMEOUT_S,
        # Nib runs under pythonw, which has no console of its own - without this flag, spawning
        # a console app like tesseract.exe makes Windows create one, which flashes visibly on
        # screen for the call's duration.
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(f"tesseract exited {result.returncode}")
    return result.stdout.decode("utf-8", "replace")


def _worker(path: str, on_done: Callable[[str | None, str | None], None]) -> None:
    try:
        text = asyncio.run(_recognize_winrt(path))
    except Exception as exc:
        log.info("Windows OCR unavailable (%s) - trying the Tesseract fallback", type(exc).__name__)
        try:
            text = _recognize_tesseract(path)
        except Exception as exc2:
            # No exc_info anywhere in this function: PRIVACY.md promises nothing about a read is
            # logged at any level, and an exception's own message can embed the path it choked
            # on (a bad file, a locked one) - the same reason _on_files_dropped logs only a
            # count, never a path.
            log.warning("Tesseract fallback also failed (%s)", type(exc2).__name__)
            on_done(None, "couldn't read that image")
            return

    if text and text.strip():
        on_done(text, None)
    else:
        on_done(None, "didn't find any text in that image")


def recognize(path: str, on_done: Callable[[str | None, str | None], None]) -> None:
    """Read text out of the image at ``path``; ``on_done(text, error)`` fires exactly once.

    Tries Windows' own on-device OCR engine first; if that's unavailable or fails for any reason
    (no language pack, an unsupported-app restriction, anything at all), falls back to the
    vendored Tesseract binary rather than reporting a hard failure.

    Exactly one of ``text``/``error`` is set. Runs on a background thread and returns
    immediately - never call this from anywhere that can't handle the result arriving later,
    on a different thread.
    """
    try:
        resolved = Path(path).resolve(strict=True)
    except OSError:
        on_done(None, "couldn't read that file")
        return
    # WinRT's StorageFile.get_file_from_path_async rejects a relative path outright (a real
    # drag-and-drop always hands over an absolute one, but nothing else here guarantees it).
    if resolved.stat().st_size > MAX_IMAGE_BYTES:
        on_done(None, "that image is too large")
        return
    threading.Thread(target=_worker, args=(str(resolved), on_done), daemon=True).start()
