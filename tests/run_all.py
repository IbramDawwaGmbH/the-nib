"""Run every suite and report. This is the whole test command.

    python tests/run_all.py           # the correctness suites
    python tests/run_all.py --rates   # ...and the slow rate measurements too

There is no pytest here on purpose. These are not unit tests: nearly every one of them boots a real
``QApplication``, builds a real sprite sheet, and in several cases reads geometry back out of Win32
to check what Windows actually believes. A framework would add a layer between the assertion and the
thing being asserted, and the thing being asserted is usually "what does the operating system say".

Each suite prints its own checks and exits non-zero on failure, so it is equally useful to run one
directly while working on it.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: The correctness suites. Fast enough to run on every change.
SUITES = [
    ("smoke_world", "clock, calendar, season, CPU load, app personas"),
    ("smoke_play", "peeking over the taskbar, straining at a window, and the never-move rule"),
    ("smoke_gait", "his feet do not slide, at any tick rate or pace"),
    ("smoke_faces", "blinks, the startle, and the seven expressions"),
    ("smoke_throw", "throw physics: flick, bounce, roll, and what it must not break"),
    ("smoke_attention", "being ignored: the three stages and everything that resets them"),
    ("smoke_pet", "petting, coming when called, and the personality presets"),
    ("smoke_mood", "the persistent mood: decay, headroom, hostile clocks, disk writes"),
    ("smoke_memory", "what he remembers: counters, the 7-day window, bond, forget"),
    ("smoke_needs", "energy, affection and boredom: drift, nudges, the two moments, disk writes"),
    ("smoke_voice", "the lines he says: gap, daily cap, launch quiet, the phrase table"),
    ("smoke_ui", "ghost mode, prompt history, the settings window, sound, the first-run tour"),
    ("smoke_costumes", "every costume on every frame"),
    ("smoke_dressing", "he dresses himself, and never over a costume you picked"),
    ("smoke_pocket", "the snippet billboard - grab, accumulate, take down, standing still"),
    ("smoke_calculator", "the inline calculator: safe parsing, unit conversion, never dispatched"),
    ("smoke_ruler", "the pixel ruler: mouse-only measuring, and freezing a reading correctly"),
    ("smoke_ocr", "drop-to-OCR: Shift+drop eligibility, the recognize() contract, the antic"),
    ("smoke_focus", "focus sessions: the working hold, priority order, auto-pause, completion"),
    ("smoke_wheel", "the tool wheel: fits every corner, aims by angle, click versus hold-and-flick"),
    ("smoke_devices", "mic and camera: mute state, the in-use store, the event-driven watcher"),
    ("smoke_perch", "dropping him on a window: standing on it, following it, pinning it on top"),
    ("smoke_quirks", "costume opinions, the poke flinch, the spin, sneeze/trip/hiccups"),
    ("smoke_guard", "the away guard's rules: fog, blackout, backoff, peek, the intrusion log"),
    ("smoke_guard_scene", "the guard's show: eyes, stage director, phrase bags, peek chip"),
    ("smoke_guard_fx", "the guard's background: bots, dust, scan line, glow, frame rate, cost"),
    ("smoke_guard_wiring", "the guard's plumbing: held keys, Hello prompt state, the way back in"),
    ("smoke_update", "update and restart: git fast-forward, release zip, the helper, the wait"),
    ("shadow_mask", "the contact shadow's real input region, read back from GetWindowRgn"),
]

#: Measurements rather than assertions. Slow - each simulates hours of behaviour - so they are
#: opt-in. They are what stop a "small" tuning change quietly turning a charm into a tic.
RATES = [
    ("fidget_rate", "how much of his time the micro-fidgets take"),
    ("quirk_rate", "sneezes and trips per hour, across presets"),
    ("day_rate", "how often each world moment fires over a simulated day"),
    ("ledge_rate", "how often the window enumeration actually runs"),
    ("chatter_rate", "how many lines he says in a working day, at each chatter level"),
]


def run(name: str, blurb: str) -> tuple[str, bool, float]:
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(HERE / f"{name}.py")],
        capture_output=True, text=True,
    )
    took = time.monotonic() - started
    ok = result.returncode == 0
    print(f"  {'PASS' if ok else 'FAIL'}  {name:18s} {took:5.1f}s  {blurb}")
    if not ok:
        for line in (result.stdout or "").splitlines():
            if line.strip().startswith("FAIL"):
                print(f"          {line.strip()}")
        tail = (result.stderr or "").strip().splitlines()[-4:]
        for line in tail:
            print(f"          {line}")
    return name, ok, took


def ensure_sounds() -> None:
    """Generate the sound effects if they are not there yet.

    They are deliberately not committed - they are optional, off by default, and regenerate
    byte-identically from ``tools/gen_sounds.py``. But a fresh clone would otherwise report a
    failing suite for a feature the user has not switched on, which is a poor first impression and
    not a real defect.
    """
    root = HERE.parent
    sounds = root / "assets" / "sounds"
    if sounds.is_dir() and any(sounds.glob("*.wav")):
        return
    print("  generating assets/sounds (not committed - see tools/gen_sounds.py)")
    subprocess.run([sys.executable, str(root / "tools" / "gen_sounds.py")],
                   capture_output=True, check=False)


def main() -> int:
    ensure_sounds()
    wanted = list(SUITES)
    if "--rates" in sys.argv:
        wanted += RATES
    print(f"\nrunning {len(wanted)} suite(s)\n")
    results = [run(name, blurb) for name, blurb in wanted]
    failed = [name for name, ok, _t in results if not ok]
    total = sum(t for _n, _ok, t in results)
    print(f"\n{len(results) - len(failed)}/{len(results)} passed in {total:.0f}s")
    if failed:
        print("failed: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
