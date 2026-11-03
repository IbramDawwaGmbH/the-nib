"""Mic and camera: reading mute state, the in-use consent store, and the event-driven watcher.

**This suite must never leave your microphone muted.** The one test that writes anything reads the
real state first, flips it, asserts the flip took, puts it back, and asserts the restore - and
skips itself entirely on a machine with no capture device. Everything else here is read-only or
runs against a scratch registry key of its own under ``HKCU\\Software``, deleted on the way out.
"""

import os
import sys
import time
import winreg
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SRC))

import devices as D  # noqa: E402

fails = []

#: Under HKCU, removed in the ``finally`` below - the real consent store is never written to.
SCRATCH_KEY = r"Software\NibMascotDeviceTest"


def check(label, cond, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else ""))
    if not cond:
        fails.append(label)


def make_app_entry(parent, name, start, stop):
    """One app's usage record, shaped exactly as Windows writes it."""
    with winreg.CreateKey(parent, name) as key:
        if start is not None:
            winreg.SetValueEx(key, D._START, 0, winreg.REG_QWORD, start)
        if stop is not None:
            winreg.SetValueEx(key, D._STOP, 0, winreg.REG_QWORD, stop)


def nuke(path):
    """Delete a key and everything under it - winreg will not do it recursively."""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_ALL_ACCESS)
    except OSError:
        return
    with key:
        while True:
            try:
                nuke(path + "\\" + winreg.EnumKey(key, 0))
            except OSError:
                break
    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)


try:
    print("--- reading the mute state ---")
    state = D.mic_muted()
    check("mic_muted() answers True, False, or None - never raises",
          state in (True, False, None), repr(state))
    has_mic = state is not None
    if not has_mic:
        print("  (no capture device on this machine - the toggle test is skipped, not failed)")

    print("\n--- reading it is cheap enough for the tick ---")
    started = time.perf_counter()
    for _ in range(200):
        D.mic_muted()
    per_call_us = (time.perf_counter() - started) / 200 * 1_000_000
    check("200 reads stay well under a frame", per_call_us < 2000, f"{per_call_us:.0f}us each")

    print("\n--- the toggle, which puts your mic back exactly as it found it ---")
    if has_mic:
        before = D.mic_muted()
        flipped = D.toggle_mic()
        readback = D.mic_muted()
        restored_ok = D.set_mic_muted(before)
        after = D.mic_muted()
        check("toggle_mic() reports the flipped state", flipped == (not before), repr(flipped))
        check("...and the device really is in that state", readback == (not before), repr(readback))
        check("set_mic_muted() reports success putting it back", restored_ok)
        check("YOUR MIC IS BACK AS IT WAS", after == before, f"{before} -> {after}")

    print("\n--- the camera is an indicator only, and the module says so ---")
    check("no camera mute exists here", not hasattr(D, "set_camera_muted"))
    check("...and no camera toggle either", not hasattr(D, "toggle_camera"))
    check("the mic is the only writable thing in the module",
          [n for n in dir(D) if n.startswith("set_") or n.startswith("toggle_")]
          == ["set_mic_muted", "toggle_mic"])

    print("\n--- in-use: what counts as live ---")
    nuke(SCRATCH_KEY)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, SCRATCH_KEY) as root:
        make_app_entry(root, "idle.exe", 134261785822476419, 134261785917172190)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SCRATCH_KEY) as root:
        check("a start with a stop is finished, not live", not D._key_says_in_use(root))

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, SCRATCH_KEY) as root:
        make_app_entry(root, "live.exe", 134261785822476419, 0)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SCRATCH_KEY) as root:
        check("a start with a zero stop is live right now", D._key_says_in_use(root))

    nuke(SCRATCH_KEY)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, SCRATCH_KEY) as root:
        make_app_entry(root, "never.exe", None, None)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SCRATCH_KEY) as root:
        check("an app with no usage recorded at all is not live", not D._key_says_in_use(root))

    print("\n--- the NonPackaged subkey, where every desktop app actually lives ---")
    nuke(SCRATCH_KEY)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, SCRATCH_KEY) as root:
        make_app_entry(root, D._NON_PACKAGED, 1, 0)  # a decoy shaped like a live app
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SCRATCH_KEY) as root:
        check("the NonPackaged container is never mistaken for an app entry",
              not D._key_says_in_use(root))
    src = Path(SRC / "devices.py").read_text(encoding="utf-8")
    check("_read_in_use walks the capability key AND its NonPackaged subkey",
          'sub_path in (path, path + "\\\\" + _NON_PACKAGED)' in src)

    print("\n--- the watcher is event-driven, not a poll ---")
    nuke(SCRATCH_KEY)
    winreg.CreateKey(winreg.HKEY_CURRENT_USER, SCRATCH_KEY).Close()
    reads = {"n": 0}

    def counting_read(_cap):
        reads["n"] += 1
        return False

    watcher = D._Watcher("scratch", path=SCRATCH_KEY, read=counting_read)
    watcher.start()
    time.sleep(0.3)
    watcher.value()  # the one read it owes on construction
    baseline = reads["n"]
    for _ in range(50):
        watcher.value()
    check("nothing changed, so nothing is re-read", reads["n"] == baseline,
          f"{reads['n'] - baseline} extra reads")

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SCRATCH_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, "poke", 0, winreg.REG_SZ, "1")
    fired = False
    for _ in range(40):  # up to ~2s, far longer than it needs
        time.sleep(0.05)
        watcher.value()
        if reads["n"] > baseline:
            fired = True
            break
    check("a real change to the key wakes it", fired, f"{reads['n']} reads")

    # The case that actually matters. Windows never writes to the capability key itself - it
    # writes to a grandchild, microphone\NonPackaged\<app>\LastUsedTimeStop. If the subtree flag
    # were wrong the watcher would arm cleanly, report no error, and simply never fire, which
    # would silently disable the whole feature rather than break it visibly.
    deep = reads["n"]
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, SCRATCH_KEY + r"\NonPackaged\some.exe") as sub:
        winreg.SetValueEx(sub, D._STOP, 0, winreg.REG_QWORD, 0)
    deep_fired = False
    for _ in range(40):
        time.sleep(0.05)
        watcher.value()
        if reads["n"] > deep:
            deep_fired = True
            break
    check("a write two levels down - where Windows actually writes - wakes it too",
          deep_fired, f"{reads['n']} reads")

    watcher.stop()
    time.sleep(D._WAIT_TIMEOUT_MS / 1000 + 0.3)
    check("stop() ends the thread rather than leaving it behind",
          not watcher._thread.is_alive())

    print("\n--- a machine with none of this hardware is supported, not an error ---")
    check("in_use on a capability that does not exist is False, not a crash",
          D._read_in_use("no-such-capability") is False)
    check("mic_in_use() answers a bool", isinstance(D.mic_in_use(), bool))
    check("camera_in_use() answers a bool", isinstance(D.camera_in_use(), bool))

    print("\n--- the dot the tray icon carries, straight from the state that was read ---")
    import inspect  # noqa: E402
    import types  # noqa: E402

    import main as M  # noqa: E402

    def badge(muted, mic_live, camera_live):
        holder = types.SimpleNamespace(_device_state=(muted, mic_live, camera_live))
        return M.NibApp._device_badge_kind(holder)

    check("nothing live, no dot", badge(False, False, False) == "")
    check("a live unmuted mic is hot", badge(False, True, False) == "hot")
    check("a live muted mic is its own colour, not the hot one",
          badge(True, True, False) == "muted", badge(True, True, False))
    check("a live camera is hot", badge(False, False, True) == "hot")
    check("a muted mic with the camera on is still hot - you are being seen",
          badge(True, True, True) == "hot", badge(True, True, True))
    check("muted but nothing listening is not worth a dot at all",
          badge(True, False, False) == "")
    check("no reading yet means no dot",
          M.NibApp._device_badge_kind(types.SimpleNamespace(_device_state=None)) == "")

    print("\n--- how it is wired in ---")
    src_presence = inspect.getsource(M.NibApp._update_presence)
    check("read on the 700ms presence timer, not the movement tick",
          "self._tick_devices()" in src_presence)
    src_tick = inspect.getsource(M.NibApp._tick)
    check("...and NOT on the movement tick", "_tick_devices" not in src_tick)

    src_devices = inspect.getsource(M.NibApp._tick_devices)
    check("the icon is only repainted when the state actually changed",
          "if state != self._device_state:" in src_devices)
    check("both switches off means it stops reading entirely",
          "if not mic_on and not camera_on:" in src_devices)

    src_toggle = inspect.getsource(M.NibApp._on_mic_toggle)
    check("the toggle goes through devices.toggle_mic", "devices.toggle_mic()" in src_toggle)
    check("...and says so plainly when there is no microphone", "No microphone" in src_toggle)
    check("...and refreshes at once rather than lagging a presence tick",
          "self._tick_devices()" in src_toggle)

    src_muted = inspect.getsource(M.NibApp._tick_muted_talk)
    check("the nudge waits out talking_while_muted_after_s",
          "talking_while_muted_after_s" in src_muted)
    check("...and then holds its tongue for a cooldown",
          "_muted_nag_until" in src_muted and "MUTED_NAG_COOLDOWN_S" in src_muted)
    check("the toast goes up even when he cannot perform the animation",
          src_muted.index("toast.show_message") > src_muted.index("talking_while_muted()"))

    src_quit = inspect.getsource(M.NibApp._on_quit)
    check("quitting stops the watcher threads and lets go of the COM pointers",
          "devices.shutdown()" in src_quit)

    print("\n--- the mascot's own tell reuses existing art ---")
    import behavior as B  # noqa: E402

    check("the muted animation is one he already has", B.MUTED_ANIM in ("wave", "startle"))
    check("...and the costume is an existing one, not new art",
          B.MUTED_COSTUME == "earmuffs")
    src_tell = inspect.getsource(B.Behavior.talking_while_muted)
    check("it never counts as you interacting with him", "self.noticed()" not in src_tell)
    check("...and never touches the mood", "mood" not in src_tell.split('"""')[-1])

    print("\n--- shutdown ---")
    D.shutdown()
    D.shutdown()  # twice, because quitting can race with a watcher already stopping
    check("shutdown is safe to call twice", True)
    check("it answers after a shutdown rather than raising",
          D.mic_muted() in (True, False, None))

finally:
    nuke(SCRATCH_KEY)
    D.shutdown()

print("\n" + "=" * 62)
print("FAILURES: " + ", ".join(fails) if fails else "mic and camera behave")
sys.exit(1 if fails else 0)
