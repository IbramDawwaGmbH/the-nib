"""Rebuild vendor/tesseract/ from a real Tesseract-OCR install - for bumping the vendored version.

vendor/tesseract/ is a hand-picked ~25MB subset of the official Windows build (see
https://github.com/tesseract-ocr/tesseract, Apache-2.0): tesseract.exe, eng.traineddata, and only
the DLLs actually on its import-table closure. The official installer ships those DLLs alongside
~45MB of font-rendering libraries (cairo, pango, harfbuzz, fontconfig, full ICU tables) that only
the training tools (text2image and friends) need - tesseract.exe itself never touches them. Trimming
that by hand or by guessing which loader errors to chase is exactly the kind of unverified
assumption this project avoids; this walks the real PE import table instead; see ``ocr.py`` for the
fallback this feeds and CONTRIBUTING.md for why every OCR-adjacent change gets a real build+run
before release.

Usage, to refresh the vendored copy against a newly-installed version::

    winget install --id tesseract-ocr.tesseract --source winget
    python tools/vendor_tesseract.py "C:\\Users\\<you>\\AppData\\Local\\Programs\\Tesseract-OCR"

Then re-run tests/smoke_ocr.py and do a real PyInstaller build+run before shipping the bump -
a different upstream build could pull in a DLL this script's import-table walk won't warn about
until it's actually missing at runtime.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pefile

VENDOR_DIR = Path(__file__).parent.parent / "vendor" / "tesseract"

# DLLs that ship with Windows itself - never need vendoring, and pefile can't open them anyway
# (they live in the side-by-side WinSxS store or the API set redirection layer, not as plain
# files next to the app).
SYSTEM_DLL_PREFIXES = ("api-ms-win-", "ext-ms-win-")
SYSTEM_DLLS = {
    "kernel32.dll", "user32.dll", "advapi32.dll", "ole32.dll", "oleaut32.dll", "shell32.dll",
    "ws2_32.dll", "crypt32.dll", "bcrypt.dll", "ntdll.dll", "msvcrt.dll", "gdi32.dll",
    "shlwapi.dll", "version.dll", "winmm.dll", "secur32.dll", "iphlpapi.dll", "userenv.dll",
    "wldap32.dll", "normaliz.dll", "rpcrt4.dll", "imm32.dll", "setupapi.dll", "cfgmgr32.dll",
    "propsys.dll", "dwmapi.dll", "combase.dll", "sechost.dll",
}


def _is_system(name: str) -> bool:
    key = name.lower()
    return key in SYSTEM_DLLS or key.startswith(SYSTEM_DLL_PREFIXES)


def resolve(src: Path, entry_point: str) -> tuple[set[str], set[str]]:
    """Walk ``entry_point``'s real PE import table, recursively, within ``src``.

    Returns (vendorable file names, names the closure needs but ``src`` doesn't have).
    """
    visited: set[str] = set()  # everything scanned once, so nothing loops - includes system DLLs
    found: set[str] = set()  # only real, vendorable, non-system files - what gets copied
    missing: set[str] = set()
    queue = [entry_point]
    while queue:
        name = queue.pop()
        key = name.lower()
        if key in visited:
            continue
        visited.add(key)
        if _is_system(name):
            continue
        path = src / name
        if not path.exists():
            missing.add(name)
            continue
        found.add(name)
        pe = pefile.PE(str(path), fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"]])
        for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []):
            dll = entry.dll.decode("ascii", "replace")
            if dll.lower() not in visited:
                queue.append(dll)
        pe.close()
    return found, missing


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <path to a Tesseract-OCR install directory>")
        return 2
    src = Path(sys.argv[1])
    if not (src / "tesseract.exe").exists():
        print(f"no tesseract.exe in {src}")
        return 1

    found, missing = resolve(src, "tesseract.exe")
    if missing:
        print(f"tesseract.exe's import closure needs files {src} doesn't have: {sorted(missing)}")
        return 1

    if VENDOR_DIR.exists():
        shutil.rmtree(VENDOR_DIR)
    (VENDOR_DIR / "tessdata").mkdir(parents=True)

    total = 0
    for name in sorted(found):
        path = src / name
        total += path.stat().st_size
        shutil.copy2(path, VENDOR_DIR / name)
    shutil.copy2(src / "tessdata" / "eng.traineddata", VENDOR_DIR / "tessdata" / "eng.traineddata")
    total += (VENDOR_DIR / "tessdata" / "eng.traineddata").stat().st_size
    license_src = src / "doc" / "LICENSE"
    if license_src.exists():
        shutil.copy2(license_src, VENDOR_DIR / "LICENSE")

    print(f"vendored {len(found)} files + eng.traineddata + LICENSE, {total / 1024 / 1024:.1f} MB")
    print("now: python tests/run_all.py, then a real PyInstaller build+run before shipping")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
