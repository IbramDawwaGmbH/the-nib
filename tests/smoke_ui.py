"""Ghost mode, prompt history, the settings window, the sound layer and the first-run tour.

Driven through the real app object wherever possible, and against Win32 rather than against what
Qt believes: ghost mode is checked by reading the window's actual extended style back.
"""

import ctypes
import inspect
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _helpers import scratch as _scratch  # noqa: E402

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "0")
SRC = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, SRC)
os.chdir(SRC)
SCRATCH = _scratch()

from PySide6.QtCore import QPoint, QRect, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QTextCursor  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QSpinBox,
)

import config as cfg_module  # noqa: E402
import main as M  # noqa: E402
import settings as S  # noqa: E402
import win_integration as win  # noqa: E402
from bubble import AskBubble  # noqa: E402
from config import Config  # noqa: E402
from sound import EVENTS, Sounds  # noqa: E402

fails = []


def check(label, cond, detail=""):
    line = ("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail else "")
    # The bubble's hint contains an up-arrow, and this console is cp1252. Losing the test run to
    # a console encoding would be a silly way to fail.
    print(line.encode("ascii", "replace").decode("ascii"))
    if not cond:
        fails.append(label)


def pump(ms):
    """Let Qt actually run for a while. time.sleep() blocks the event loop, which means the fade
    animations never advance and a test that waits on one waits forever."""
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def ex_style(hwnd):
    """The window's real extended style word, straight from Win32."""
    getter = ctypes.windll.user32.GetWindowLongPtrW
    getter.argtypes = [wintypes.HWND, ctypes.c_int]
    getter.restype = ctypes.c_ssize_t
    return getter(wintypes.HWND(hwnd), -20)


WS_EX_TRANSPARENT = 0x20
app = QApplication(sys.argv)


def fresh_config(tag, **overrides):
    path = SCRATCH / f"ui_{tag}.json"
    path.unlink(missing_ok=True)
    cfg = Config(path)
    cfg["last_seen_date"] = "2026-07-31"
    cfg["intro_seen"] = True          # not under test unless a case asks for it
    for k, v in overrides.items():
        cfg[k] = v
    return cfg


print("\n--- prompt history: what is stored ---")
cfg = fresh_config("hist")
for text in ("first", "second", "third"):
    cfg.remember_prompt(text)
check("newest first", cfg["prompt_history"] == ["third", "second", "first"],
      str(cfg["prompt_history"]))
cfg.remember_prompt("second")
check("re-sending moves it to the front rather than duplicating",
      cfg["prompt_history"] == ["second", "third", "first"], str(cfg["prompt_history"]))
cfg.remember_prompt("   ")
check("blank prompts are not stored", len(cfg["prompt_history"]) == 3)

cfg["prompt_history_max"] = 3
cfg.remember_prompt("fourth")
check("the cap is honoured", len(cfg["prompt_history"]) == 3, str(cfg["prompt_history"]))
check("...dropping the oldest", "first" not in cfg["prompt_history"], str(cfg["prompt_history"]))

cfg["prompt_history_max"] = 0
cfg.remember_prompt("nope")
check("setting the maximum to 0 clears it and keeps it clear", cfg["prompt_history"] == [])

cfg2 = fresh_config("hist2")
cfg2.remember_prompt("kept")
cfg2.save()
check("it survives a reload", Config(cfg2.path)["prompt_history"] == ["kept"])
Config(cfg2.path).clear_prompt_history()
cfg3 = Config(cfg2.path)
cfg3.clear_prompt_history()
cfg3.save()
check("...and clearing really clears it", Config(cfg2.path)["prompt_history"] == [])

print("\n--- the wardrobe's recents ---")
cfg = fresh_config("recents")
for name in ("cap", "crown", "beret"):
    cfg.remember_costume(name)
check("newest first", cfg["costume_recents"] == ["beret", "crown", "cap"], str(cfg["costume_recents"]))
cfg.remember_costume("cap")
check("a repeat moves to the front", cfg["costume_recents"] == ["cap", "beret", "crown"],
      str(cfg["costume_recents"]))
cfg.dirty = False
cfg.remember_costume("")
check("taking it off is not a costume", cfg["costume_recents"] == ["cap", "beret", "crown"])
check("...and changes nothing", not cfg.dirty)
cfg.remember_costume("wig")
check("remembering one marks the file dirty", cfg.dirty)
for i in range(20):
    cfg.remember_costume(f"c{i}")
check("capped", len(cfg["costume_recents"]) == cfg_module._MAX_COSTUME_RECENTS,
      str(cfg["costume_recents"]))
cfg["costume_recents"] = "cap"
check("a bare string is not a list of costumes", cfg["costume_recents"] == [])
cfg["costume_recents"] = [1, "cap", "cap", " ", " crown "]
check("...junk dropped, repeats folded, names stripped", cfg["costume_recents"] == ["cap", "crown"],
      str(cfg["costume_recents"]))
check("written by the wardrobe, not shown in Settings", "costume_recents" in S.HIDDEN)

print("\n--- prompt history: walking back through it ---")
bubble = AskBubble(1.0)
edit = bubble._input


def press(key, text=""):
    edit.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, key, Qt.NoModifier, text))


edit.set_history(["newest", "middle", "oldest"])
edit.setPlainText("")
press(Qt.Key_Up)
check("Up recalls the newest", edit.toPlainText() == "newest", edit.toPlainText())
press(Qt.Key_Up)
press(Qt.Key_Up)
check("...and keeps going back", edit.toPlainText() == "oldest", edit.toPlainText())
press(Qt.Key_Up)
check("...and stops at the end rather than wrapping", edit.toPlainText() == "oldest",
      edit.toPlainText())
press(Qt.Key_Down)
press(Qt.Key_Down)
check("Down comes forward again", edit.toPlainText() == "newest", edit.toPlainText())

edit.set_history(["stored"])
edit.setPlainText("half-typed thought")
press(Qt.Key_Up)
check("browsing replaces what you were typing", edit.toPlainText() == "stored")
press(Qt.Key_Down)
check("...and coming back restores it", edit.toPlainText() == "half-typed thought",
      edit.toPlainText())

# The reason Up is conditional: a multi-line prompt has to stay editable.
edit.set_history(["stored"])
edit.setPlainText("line one\nline two")
edit.moveCursor(QTextCursor.End)
press(Qt.Key_Up)
check("Up inside a multi-line prompt moves the caret, it does not recall",
      edit.toPlainText() == "line one\nline two", edit.toPlainText()[:20])

edit.set_history([])
edit.setPlainText("solo")
press(Qt.Key_Up)
check("with no history Up does nothing at all", edit.toPlainText() == "solo")
bubble.close()

print("\n--- the sound layer ---")
cfg = fresh_config("sound", sound_enabled=True)
missing = Sounds(Path(SRC) / "assets" / "does-not-exist", cfg)
check("a missing sounds directory is not fatal", missing.available is False)
check("...and playing is a silent no-op", missing.play("send") is False)

# The WAVs are not committed - they are generated by tools/gen_sounds.py. On a fresh clone they
# will not be there, and that is a supported state rather than a failure, so the checks that need
# a real file say so instead of going red.
sounds_dir = Path(SRC) / "assets" / "sounds"
have_wavs = sounds_dir.is_dir() and any(sounds_dir.glob("*.wav"))
real = Sounds(sounds_dir, cfg)
check("an unknown event is refused", real.play("explode") is False)
if not have_wavs:
    print("  SKIP  no assets/sounds yet - run tools/gen_sounds.py to test playback")
else:
    check("the generated sounds are found", real.available, str(real.available))
    played = real.play("send")
    check("a real event plays", played, str(played))
    real.tick(10.0)
    check("...and immediately repeating it is swallowed", real.play("send") is False)
    real.tick(500.0)
    check("...but it comes back after the guard", real.play("send"))

off = Sounds(Path(SRC) / "assets" / "sounds", fresh_config("sound_off", sound_enabled=False))
check("switched off, nothing plays", off.play("send") is False)
check("every generated file matches a known event",
      {p.stem for p in sounds_dir.glob("*.wav")} <= set(EVENTS),
      str(sorted(p.stem for p in sounds_dir.glob("*.wav"))))

print("\n--- the settings window covers everything ---")
window = S.SettingsWindow(fresh_config("settings"), costumes=["cap", "crown"], skins=["default"])
shown = set(window._fields)
unreachable = set(cfg_module.DEFAULTS) - shown - S.HIDDEN
# persona_apps and prompt_history are dict/list valued and have no generic control; they are
# named here rather than silently tolerated, so a NEW unreachable key still fails this.
check("every setting is either shown or deliberately hidden",
      unreachable <= {"persona_apps"}, str(sorted(unreachable)))
check("nothing hidden leaked into the form", not (shown & S.HIDDEN), str(sorted(shown & S.HIDDEN)))
check("no key is claimed by two tabs",
      sum(len([k for k in keys if k in cfg_module.DEFAULTS]) for _t, keys in S.GROUPS)
      == len({k for _t, keys in S.GROUPS for k in keys if k in cfg_module.DEFAULTS}))
check("every help line names a real setting",
      set(S.HELP) <= set(cfg_module.DEFAULTS), str(sorted(set(S.HELP) - set(cfg_module.DEFAULTS))))
check("every restart-only key is real",
      S.NEEDS_RESTART <= set(cfg_module.DEFAULTS))

kinds = {}
for key, field in window._fields.items():
    kinds[type(field.widget).__name__] = kinds.get(type(field.widget).__name__, 0) + 1
print("    widgets:", kinds)
check("booleans became checkboxes",
      isinstance(window._fields["wander_enabled"].widget, QCheckBox))
check("bounded floats became spin boxes",
      isinstance(window._fields["antic_chance"].widget, QDoubleSpinBox))
check("bounded ints became spin boxes",
      isinstance(window._fields["cpu_busy_percent"].widget, QSpinBox))
check("choice keys became combos", isinstance(window._fields["personality"].widget, QComboBox))
check("...including the costumes actually installed",
      window._fields["costume"].widget.count() == 3,
      str(window._fields["costume"].widget.count()))
check("text keys became line edits", isinstance(window._fields["hotkey"].widget, QLineEdit))

print("\n--- and applying it writes through, clamped ---")
cfg = fresh_config("apply")
window = S.SettingsWindow(cfg, costumes=[], skins=["default"])
window._fields["antic_chance"].widget.setValue(0.9)
window._fields["wander_enabled"].widget.setChecked(False)
moved = window.apply()
check("changed keys are reported", {"antic_chance", "wander_enabled"} <= moved, str(sorted(moved)))
check("...and stored", cfg["antic_chance"] == 0.9 and cfg["wander_enabled"] is False)
check("nothing else moved", moved == {"antic_chance", "wander_enabled"}, str(sorted(moved)))
check("applying again reports no change", window.apply() == set())

# The spin box range is built from _BOUNDS, so out-of-range is refused at the widget rather than
# silently clamped later - which is what makes the displayed value trustworthy.
box = window._fields["ghost_opacity"].widget
box.setValue(99.0)
check("a wild value is clamped by the control itself", box.value() <= 1.0, str(box.value()))
box.setValue(0.0)
check("...and at the bottom end too, so he is never invisible", box.value() >= 0.08,
      str(box.value()))

for key, field in window._fields.items():
    if key in ("costume", "skin"):
        continue
    field.write(cfg_module.DEFAULTS[key])
window.apply()
check("restoring defaults puts every shown key back",
      all(cfg[k] == cfg_module.DEFAULTS[k] for k in window._fields
          if k not in ("costume", "skin")))
window.close()

print("\n--- ghost mode, read back from Win32 ---")
nib = M.NibApp(app, fresh_config("ghost"))
pump(300)
hwnd = int(nib.window.winId())
check("he starts solid", not (ex_style(hwnd) & WS_EX_TRANSPARENT), hex(ex_style(hwnd)))
check("...and fully opaque", nib.window.windowOpacity() > 0.9,
      str(nib.window.windowOpacity()))

nib._on_ghost_toggled(True)
app.processEvents()
check("ghost mode makes the window click-through", bool(ex_style(hwnd) & WS_EX_TRANSPARENT),
      hex(ex_style(hwnd)))
check("...and it is remembered", nib.config["ghost_mode"] is True)
pump(500)
check("...and faded, but never to nothing",
      0.0 < nib.window.windowOpacity() < 0.6, f"{nib.window.windowOpacity():.2f}")
check("...and still on screen", nib.window.isVisible())
# The whole point: he is out of the way of the mouse, not out of service.
nib._on_hotkey()
app.processEvents()
check("the hotkey still opens the bubble in ghost mode", nib.bubble.isVisible())
nib.bubble.close_silently()

# The app re-asserts topmost every 3 seconds with SetWindowPos, and ghost mode persists across
# restarts - so a style silently cleared by that timer would be a baffling "it stopped working
# after a few seconds" bug. Fire it by hand rather than waiting.
nib._reassert_topmost()
pump(120)
check("ghost mode survives a topmost reassert", bool(ex_style(hwnd) & WS_EX_TRANSPARENT),
      hex(ex_style(hwnd)))
for _ in range(3):
    nib._reassert_topmost()
    pump(60)
check("...and several of them", bool(ex_style(hwnd) & WS_EX_TRANSPARENT), hex(ex_style(hwnd)))
# A frame change rebuilds the click mask, which is the other thing that could plausibly undo it.
nib._last_frame_key = None
nib._apply_frame(force=True)
pump(60)
check("...and a mask rebuild", bool(ex_style(hwnd) & WS_EX_TRANSPARENT), hex(ex_style(hwnd)))

nib._on_ghost_toggled(False)
pump(500)
check("switching it off restores hit-testing", not (ex_style(hwnd) & WS_EX_TRANSPARENT),
      hex(ex_style(hwnd)))
check("...and the opacity", nib.window.windowOpacity() > 0.9,
      f"{nib.window.windowOpacity():.2f}")

print("\n--- coming when called, through the app ---")
# Through the app's own geometry pass rather than a bare place_at: teleporting him across a
# multi-monitor desktop leaves the app's idea of which monitor he is on stale, and everything
# anchored to him - the bubble, the toast - is then clamped to the wrong screen.
nib.behavior.place_at(nib.behavior.x_min + 200)
nib._refresh_geometry()
before = nib.behavior.x
nib._on_come_here()
check("the tray entry sets him walking", nib.behavior.state.value in ("walk", "idle"),
      nib.behavior.state.value)

print("\n--- the bubble is offered the history ---")
nib.config.remember_prompt("something I asked earlier")
nib._open_bubble_at(QRect(100, 100, 40, 40))
app.processEvents()
check("recent prompts reach the bubble",
      nib.bubble._input._history == ["something I asked earlier"],
      str(nib.bubble._input._history))
check("...and the hint says so", "recent" in nib.bubble._hint.text(),
      nib.bubble._hint.text())
nib.bubble.close_silently()

print("\n--- the first-run tour ---")
check("it is latched off once seen", nib.config["intro_seen"] is True)
nib.show_intro()
app.processEvents()
check("the first tip appears", nib.toast.isVisible())
screen = nib._screen_rect()
check("...on the screen he is actually standing on",
      screen.left() <= nib.toast.x() <= screen.right(),
      f"toast at {nib.toast.x()}, screen {screen.left()}..{screen.right()}")
check("...and roughly above him", abs(nib.toast.x() - nib.behavior.x) < 400,
      f"toast at {nib.toast.x()}, him at {nib.behavior.x}")
seen = [nib.toast._label.text()]
for _ in range(len(M.INTRO_STEPS) + 1):
    nib._next_intro_step()
    app.processEvents()
    if nib._intro_step < 0:
        break
    seen.append(nib.toast._label.text())
check("it walks through the tips and stops", nib._intro_step == -1, str(nib._intro_step))
check("...covering the undiscoverable ones", len(seen) >= 3, f"{len(seen)} tips: {seen[:2]}")
check("...and every tip says something", all(t.strip() for t in seen))

print("\n--- right-click him: the same menu the tray icon shows, plus a couple pinned above it ---")


def menu_labels():
    return [a.text() for a in nib.tray._menu.actions() if not a.isSeparator()]


# The classic menu, explicitly: with the tool wheel on (the default) right-click opens the wheel
# instead, and the wheel has a suite of its own - see smoke_wheel.py.
nib.config["tool_wheel_enabled"] = False
before_menu = menu_labels()
nib.config["last_prompt"] = "recap the meeting notes"
nib._on_context_menu(QPoint(200, 200))
during_menu = menu_labels()
check("his quick actions are pinned above the tray's own menu",
      during_menu[0] == "Ask Claude…", during_menu[:3])
check("...including repeating the last prompt", any("recap the meeting notes" in t for t in during_menu),
      during_menu)
check("...and hiding him for a bit, which the tray icon's own menu does not offer",
      "Hide for 10 minutes" in during_menu, during_menu)
check("everything the tray icon shows is still there, in the same order",
      during_menu[-len(before_menu):] == before_menu, (during_menu, before_menu))
check("nothing is duplicated - Quit appears exactly once",
      during_menu.count("Quit") == 1, during_menu)

nib.tray._menu.hide()  # a real close fires aboutToHide, same as clicking elsewhere would
app.processEvents()
check("closing it cleans the extras back out, leaving the tray's own menu untouched",
      menu_labels() == before_menu, menu_labels())

src_ctx = inspect.getsource(M.NibApp._on_context_menu)
check("it goes through Tray.popup_for_mascot rather than building its own QMenu",
      "self.tray.popup_for_mascot" in src_ctx and "QMenu(" not in src_ctx)
nib.config["tool_wheel_enabled"] = True

print("\n--- a right-click is noticed once, whichever way it goes ---")
noticed = []
real_noticed = nib.behavior.noticed
nib.behavior.noticed = lambda: (noticed.append(1), real_noticed())[-1]
nib._on_context_menu(QPoint(200, 200))
check("opening the wheel counts as one notice, not two", len(noticed) == 1, len(noticed))
nib.tool_wheel.close()
app.processEvents()
nib.config["tool_wheel_enabled"] = False
noticed.clear()
nib._on_context_menu(QPoint(200, 200))
check("...and so does opening the menu", len(noticed) == 1, len(noticed))
nib.tray._menu.hide()
app.processEvents()
nib.config["tool_wheel_enabled"] = True
nib.behavior.noticed = real_noticed

print("\n--- his name, and the mood you can see ---")
check("a blank name falls back to Nib", cfg_module._coerce("name", "   ") == "Nib",
      repr(cfg_module._coerce("name", "   ")))
check("...a long one is cut to 24", cfg_module._coerce("name", "x" * 40) == "x" * 24,
      repr(cfg_module._coerce("name", "x" * 40)))
check("...and spaces round it are trimmed", cfg_module._coerce("name", "  Pip ") == "Pip")
check("...a JSON null or number is the default, not 'None'",
      cfg_module._coerce("name", None) == "Nib" and cfg_module._coerce("name", 42) == "Nib",
      repr((cfg_module._coerce("name", None), cfg_module._coerce("name", 42))))
check("...a newline in it becomes one space, so the tooltip stays one line",
      cfg_module._coerce("name", "Sir\n\tPip") == "Sir Pip",
      repr(cfg_module._coerce("name", "Sir\n\tPip")))
check("...and the cut never leaves a trailing space",
      cfg_module._coerce("name", "a" * 23 + " b") == "a" * 23,
      repr(cfg_module._coerce("name", "a" * 23 + " b")))
check("the focus-complete notification uses his name",
      'notify(str(self.config["name"]), "Focus session complete'
      in inspect.getsource(M.NibApp._tick))
check("the tooltip is composed from the mood label",
      "label_for" in inspect.getsource(M.NibApp._update_presence))
check("...with no hard-coded 'Nib - ' left in it",
      '"Nib - ' not in inspect.getsource(M.NibApp._update_presence))

# Live, on the real tray. Device indicators off so a microphone that happens to be live on the
# machine running the tests cannot claim the tooltip first.
nib.config["mic_indicator_enabled"] = False
nib.config["camera_indicator_enabled"] = False
if nib.behavior.focus_active:
    nib.behavior.set_focus(False)
nib.mood.reset()
nib._update_presence()
check("a fresh mood reads as fine in the tray", nib.tray._tray.toolTip() == "Nib — fine",
      nib.tray._tray.toolTip())
nib.mood.nudge("petted")
nib._update_presence()
check("...and a pet shows there", nib.tray._tray.toolTip() == "Nib — in good spirits",
      nib.tray._tray.toolTip())
check("the status line says the same", nib._status_text()["line"] == "Nib is in good spirits.",
      str(nib._status_text()))

about = S.SettingsWindow(fresh_config("about"), costumes=[], skins=["default"],
                         status=lambda: {"line": "x"})
titles = [about._tabs.tabText(i) for i in range(about._tabs.count())]
check("settings has an About him tab", "About him" in titles, str(titles))
check("...showing the status line", about._now_label.text() == "x", about._now_label.text())
check("...and it holds no setting of its own", set(about._fields) == set(window._fields))
check("the name is the first thing on the Personality tab",
      dict(S.GROUPS)["Personality"][0] == "name")
about.close()

# The line is asked for again when the tab is opened, so it can't go stale while the window
# sits open and he is petted behind it.
said = ["first"]
about = S.SettingsWindow(fresh_config("about-live"), costumes=[], skins=["default"],
                         status=lambda: {"line": said[0]})
said[0] = "second"
about._tabs.setCurrentIndex(about._tabs.count() - 1)
check("...and asks again when the tab is opened", about._now_label.text() == "second",
      about._now_label.text())
about.close()
about = S.SettingsWindow(fresh_config("about-none"), costumes=[], skins=["default"])
check("with no status it says so rather than nothing",
      about._now_label.text() == "Nothing to say yet.", about._now_label.text())
about.close()


def broken_status():
    raise RuntimeError("no mood")


about = S.SettingsWindow(fresh_config("about-broken"), costumes=[], skins=["default"],
                         status=broken_status)
check("...and a status that fails doesn't take the window with it",
      about._now_label.text() == "Nothing to say yet.", about._now_label.text())
about.close()

# Renaming applies on Apply, with no restart: the settings window writes the config, and the
# app's change handler refreshes the tooltip straight away.
nib._on_settings()
nib._settings._fields["name"].write("Pip")
nib._settings.apply()
check("a rename reaches the tooltip on Apply", nib.tray._tray.toolTip().startswith("Pip — "),
      nib.tray._tray.toolTip())
check("...and the About him line", nib._settings._now_label.text().startswith("Pip is "),
      nib._settings._now_label.text())
nib._settings.close()

# What he remembers, and forgetting it, through the real app. The memory file sits next to the
# config he was given - here, the test scratch directory - and never in the user's real one.
check("his memory lives next to the config he was given",
      nib.memory.path == nib.config.path.parent / "memory.json", str(nib.memory.path))
nib.memory.forget()          # a run before this one may have left a file in scratch
nib._memory_day = ""
# Present, whatever the machine's real idle time: a CI runner nobody has touched since boot would
# otherwise read as away, and he only counts a day he has actually seen you on.
nib.config["away_after_s"] = 0
nib.behavior.set_away(False)
nib._update_presence()
check("the first poll of the day counts it as a day seen", nib.memory.days_seen == 1,
      str(nib.memory.days_seen))
nib.behavior.prompt_sent("hello")
nib._flush_config()
check("...and the flush writes memory.json", nib.memory.path.is_file())
status = nib._status_text()
check("About him gets his memory in words", any("1 prompt" in line for line in status["memory"]),
      str(status))
check("...and may forget it", status["can_forget"] is True)
nib._on_settings()
about_page = nib._settings
about_page._tabs.setCurrentIndex(about_page._about_index)
check("the panel lists it", "Pip has known you 1 day" in about_page._memory_label.text(),
      about_page._memory_label.text())
check("...with Forget everything enabled", about_page._forget_button.isEnabled())
asked = []
real_question, real_information = S.QMessageBox.question, S.QMessageBox.information
S.QMessageBox.question = staticmethod(lambda *a, **k: (asked.append(a[2]), S.QMessageBox.No)[1])
about_page._on_forget()
check("Forget asks first", len(asked) == 1 and "memory.json" in asked[0], str(asked))
check("...and No keeps everything", nib.memory.path.is_file() and nib.memory.days_seen == 1)
S.QMessageBox.question = staticmethod(lambda *a, **k: S.QMessageBox.Yes)
S.QMessageBox.information = staticmethod(lambda *a, **k: None)
nib.mood.nudge("petted")
about_page._on_forget()
S.QMessageBox.question, S.QMessageBox.information = real_question, real_information
check("Yes deletes memory.json", not nib.memory.path.exists())
check("...and empties the panel", about_page._memory_label.text().startswith("Nothing yet"),
      about_page._memory_label.text())
check("...and greys the button out", not about_page._forget_button.isEnabled())
check("...and lets his mood start fresh", nib.mood.spirits == 0.0, str(nib.mood.spirits))
nib.config["memory_enabled"] = False
nib._memory_day = ""
nib._update_presence()
nib.behavior.prompt_sent("again")
nib._flush_config()
check("with memory switched off no file is written", not nib.memory.path.exists())
check("...and the panel says it is off", "switched off" in nib._status_text()["memory"][0],
      str(nib._status_text()))
nib.config["memory_enabled"] = True
about_page.close()

# The minute counters and the arrival tally, driven poll by poll. Each call is handed a fake gap
# since the last poll by winding _memory_polled back; the WorldState is built by hand so the CPU
# can be "busy" and midnight can be crossed without waiting for either.
import world as W  # noqa: E402


def poll(date_key, hour, *, gap_s, busy=False, away=False):
    nib.behavior.set_away(away)
    nib._memory_polled = time.monotonic() - gap_s
    nib._update_memory(W.WorldState(
        hour=hour, weekday=2, date_key=date_key, week_key="2026-W45", part_of_day=W.PART_DAY,
        season=W.SEASON_AUTUMN, cpu_percent=90.0 if busy else 5.0, cpu_busy=busy))


nib.memory.forget()
nib._memory_day, nib._memory_was_present, nib._arrival_pending = "", None, ""
nib._present_acc = nib._busy_acc = 0.0
poll("2026-11-04", 9.25, gap_s=3600.0, busy=True)
check("a poll after a long gap (the machine slept) adds at most a few seconds",
      nib._present_acc == M.MEMORY_MAX_POLL_S and nib._busy_acc == M.MEMORY_MAX_POLL_S,
      f"{nib._present_acc} {nib._busy_acc}")
check("the first poll of a session is an arrival, tallied at its hour",
      nib.memory.days_seen == 1 and nib.memory._arrival_hours[9] == 1,
      str(nib.memory._arrival_hours))
for _ in range(11):
    poll("2026-11-04", 9.3, gap_s=5.0, busy=True)
check("a minute of polls is one present minute and one busy minute",
      nib.memory.day("2026-11-04").present_min == 1 and nib.memory.day("2026-11-04").busy_min == 1,
      str(nib.memory.day("2026-11-04")))
check("...recorded once, with the remainder carried", 0.0 <= nib._present_acc < 60.0,
      str(nib._present_acc))
for _ in range(12):
    poll("2026-11-04", 9.4, gap_s=5.0, away=True)
check("away, no present minutes", nib.memory.day("2026-11-04").present_min == 1,
      str(nib.memory.day("2026-11-04")))
check("...and no busy ones while the machine is idle", nib.memory.day("2026-11-04").busy_min == 1)
poll("2026-11-04", 23.9, gap_s=0.7)
poll("2026-11-05", 0.1, gap_s=0.7)
check("midnight at the desk counts the new day", nib.memory.days_seen == 2,
      str(nib.memory.days_seen))
check("...but not as an arrival at midnight", nib.memory._arrival_hours[0] == 0,
      str(nib.memory._arrival_hours))
poll("2026-11-05", 1.0, gap_s=0.7, away=True)
poll("2026-11-05", 8.6, gap_s=0.7)
check("...the first time you come back that day is", nib.memory._arrival_hours[8] == 1,
      str(nib.memory._arrival_hours))
poll("2026-11-05", 12.0, gap_s=0.7, away=True)
poll("2026-11-05", 13.0, gap_s=0.7)
check("...and only that first time", sum(nib.memory._arrival_hours) == 2,
      str(nib.memory._arrival_hours))
nib._forget_memory()
poll("2026-11-05", 15.0, gap_s=0.7)
check("after Forget, today counts again as the day you met",
      nib.memory.days_seen == 1 and nib.memory.first_met == "2026-11-05")
check("...but forgetting at three is not arriving at three", not any(nib.memory._arrival_hours),
      str(nib.memory._arrival_hours))
nib.memory.forget()
nib.config["away_after_s"] = cfg_module.DEFAULTS["away_after_s"]
nib.behavior.set_away(False)
window_no_forget = S.SettingsWindow(fresh_config("about-noforget"), costumes=[], skins=["default"],
                                    status=lambda: {"line": "x", "memory": ["a"],
                                                    "can_forget": True})
check("with no forget callback the button stays greyed",
      not window_no_forget._forget_button.isEnabled())
window_no_forget.close()

# The busier tooltips carry the new name too, not just the mood one.
nib.behavior.set_focus(True)
nib._update_presence()
check("...and the focus countdown", nib.tray._tray.toolTip().startswith("Pip - focusing"),
      nib.tray._tray.toolTip())
nib.behavior.set_focus(False)

nib._on_quit()
print("\n" + "=" * 62)
print("FAILURES: " + ", ".join(fails) if fails else "ghost, history, settings, sound, tour OK")
sys.exit(1 if fails else 0)
