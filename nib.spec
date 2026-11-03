# PyInstaller spec for Nib.  Build with:  pyinstaller nib.spec
# Produces a windowed (console-free) build in dist/Nib/.

from PyInstaller.utils.hooks import collect_submodules

# winrt (the OCR engine's own package - see ocr.py) is a namespace package with no __init__.py
# of its own, and several of the concrete Windows.* namespaces underneath it (windows.media,
# windows.graphics) are ALSO bare namespace directories rather than real submodules -
# collect_submodules only returns real, discoverable leaf modules, not the namespace parents
# Python still has to import on the way to them. Verified empirically: a build with only the
# collect_submodules() entries below imported winrt.windows.media.ocr's own leaf module fine but
# failed on "No module named 'winrt.windows.media'" - the three explicit namespace entries are
# what actually fixed it, not a defensive guess.
_winrt_hidden = ["winrt", "winrt.system", "winrt.runtime", "winrt.windows",
                 "winrt.windows.media", "winrt.windows.graphics",
                 # The Away Guard's Windows Hello check (guard_auth.py) - the same namespace-parent
                 # trap as above, one level deeper.
                 "winrt.windows.security", "winrt.windows.security.credentials"]
for _root in (
    "winrt.windows.media.ocr",
    "winrt.windows.graphics.imaging",
    "winrt.windows.storage",
    "winrt.windows.storage.streams",
    "winrt.windows.foundation",
    "winrt.windows.globalization",
    "winrt.windows.security.credentials.ui",
):
    _winrt_hidden += collect_submodules(_root)

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("assets/nib_sheet.png", "assets"),
        ("assets/frames.json", "assets"),
        # Costumes are optional at runtime, but a build without them silently has no hats.
        ("assets/costumes.png", "assets"),
        ("assets/costumes.json", "assets"),
        # The Away Guard's speech-bubble lines. guard_scene has a one-line fallback per category,
        # so a build without it still works - it would just say the same thing all day.
        ("assets/guard_phrases.json", "assets"),
        # Sounds are optional too, and off by default - so a build that omitted them would fail
        # completely silently, which is why the whole directory is listed rather than each file.
        # Generate them first with:  python tools/gen_sounds.py
        ("assets/sounds", "assets/sounds"),
        # ocr.py's Tesseract fallback - a real binary (tesseract.exe + its DLL closure +
        # eng.traineddata), not a Python package, so it goes here rather than in hiddenimports.
        # Listed as opaque data, not binaries=[], so PyInstaller doesn't re-trace an already
        # hand-resolved DLL closure (see tools/vendor_tesseract.py) looking for "missing" system
        # DLLs it was never meant to vendor.
        ("vendor/tesseract", "vendor/tesseract"),
    ],
    # guard_core is imported only on the --guard-core path at the top of main.py, and
    # guard_scene / guard_overlay lazily, so name them rather than trust the import scan to follow.
    hiddenimports=["asyncio", "guard_core", "guard_scene", "guard_overlay"] + _winrt_hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "unittest"],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Nib",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # no console window
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Nib",
)
