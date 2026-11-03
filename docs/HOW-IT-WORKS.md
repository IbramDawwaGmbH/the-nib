# How it works

The parts that are not obvious, and why they are the way they are.

[← back to the README](../README.md)

---

## How it works

### The feet do not slide

The sheet declares `walk_step_px` — how far one walk frame of leg travel covers — and the cycle is
drawn so a planted leg's offset steps back by exactly that much per frame. So he must advance
exactly one stride per animation frame.

Rather than integrate a velocity separately from the animation (the classic moonwalk bug),
`behavior._walk` derives the distance from the animation's own progress: one scaled delta drives
both. Easing then works by slowing the *animation*, which is how a real gait slows — less time per
step, same stride length.

This is verified to hold to exactly `walk_step_px × sprite_scale` per stride — **6.000000px** at
scale 2 (the default at 96 DPI) and **9.000000px** at scale 3 — independent of tick rate, tested at
8, 16, 30 and 50ms.

### Qt runs in physical pixels

`QT_ENABLE_HIGHDPI_SCALING=0` is set before Qt is imported. Qt's own scaling would put widget
coordinates in device-independent pixels while Win32 reports physical ones, making the taskbar
alignment error-prone — and Qt would resample the sprite at a fractional factor and blur it.
Instead the app picks an **integer** scale from monitor DPI and scales the art itself with
nearest-neighbour. Ordinary chrome is scaled manually via `ui_scale` to compensate.

Crossing to a monitor with a different DPI re-picks the scale and rebuilds the art, so he stays the
same physical size — unless you pin `sprite_scale` yourself.

### Clicks pass through empty space

A translucent Qt window still swallows mouse events over fully transparent pixels, which would
block a 96×96 chunk of your taskbar. So each frame installs a `QRegion` mask taken from the
sprite's alpha. That mask is pixel-exact *only because* the art contract bans partial alpha.

### The clock adapts to what is about to happen

A fixed 33fps tick spends most of its budget recomputing a frame nobody can see — an idle breathing
frame lasts 450ms. `behavior.next_tick_ms` returns the time until the next frame flip or pending
timer, and the app sets its timer to that: standing, sitting or sleeping drop to the 250ms ceiling,
about **4 ticks a second instead of 33**. Movement is paced at ~20fps, which is safe because the
gait is tick-rate independent and 20fps is finer than the sprite's own 2px grid.

Measured cost: **~1.3% of one core on average, ranging 0.7%–2.8%** across 45-second samples. That
spread is noise in *him*, not in the measurement — it depends on how much of the sample he spent
walking, so short samples are useless for comparing builds and the tick-count reduction is the
number worth trusting. Being busy ~90% of the time genuinely costs more than the scheduling saves:
"always moving" and "cheap" pull against each other, and the adaptive clock claws back most of it.

While paused, snoozed, or hidden behind a fullscreen app, the tick drops to 250ms outright.

### Maximized is not fullscreen

The auto-hide check rejects maximized windows via `IsZoomed` before comparing rectangles. This
matters more than it looks: Windows reports a maximized window's rect as its *work area inflated by
the frame border* — measured at 8px on every side, so a work area of `(0,0,2560,1392)` becomes
`(-8,-8,2568,1400)`. On any monitor whose work area equals its full rect (an auto-hiding taskbar,
or a display carrying no taskbar) a merely maximized window would satisfy the covering test, and the
mascot would vanish for as long as anything was maximized.

The check errs toward staying visible, because the costs are lopsided: a false positive hides him
during ordinary work, whereas a false negative just leaves him over a game, which the tray can fix.
Screen-covering overlays (the Windows input experience, the NVIDIA overlay) are excluded by class.

### The rest of the Win32 layer

Pure `ctypes` — no pywin32. Taskbar geometry via `SHAppBarMessage`, monitors via
`EnumDisplayMonitors`, DPI via `GetDpiForMonitor`, idle time via `GetLastInputInfo`, the login entry
via `winreg`. Every call is wrapped so a failure degrades to a documented fallback and logs at debug
level rather than taking the app down.

Geometry (taskbar edge, monitor, DPI, auto-hide state) is re-derived every 700ms, so resolution,
monitor and taskbar changes are picked up without a restart. A bottom taskbar gives its top edge as
the walk line; anything else falls back to the monitor's work area, which already accounts for a
secondary monitor's own taskbar and keeps him out from behind a side-docked one.

`WindowStaysOnTopHint` is not permanently honoured — other apps take the slot — so a 3-second timer
calls `SetWindowPos` to take it back.

The global hotkey is registered against the mascot's **own window** rather than the thread, so Qt
routes `WM_HOTKEY` to that one widget. A thread-level registration would need an application-wide
native event filter: a Python call for every Windows message the app pumps.

---

## Project layout

```
the-nib/
  main.py               entry point, timers, wiring, tray/window/bubble glue
  behavior.py           state machine: wander, gait, antics, annoyance, sleep (Qt-free)
  world.py              clock, calendar, season and CPU load (Qt-free, no network or files)
  mood.py               how the day went - the one thing that survives a restart (Qt-free)
  animator.py           sprite slicing, skins, costume compositing, frame clock
  mascot_window.py      frameless translucent always-on-top window, drops, drags, the shadow
  bubble.py             the ask bubble, prompt history recall, and the failure toast
  billboard.py          the snippet billboard he holds up when you grab the clipboard
  focus_timer.py        the floating countdown/progress readout shown during a focus session
  tray.py               tray icon and menu
  settings.py           the settings window, built from the config schema
  sound.py              plays the generated blips; silent about every possible failure
  win_integration.py    taskbar, monitors, DPI, fullscreen, idle, hotkeys, ghost mode, Run key
  launcher.py           builds the prefill link for whichever assistant you chose
  config.py             load/save/migrate config.json
  assets/
    nib_sheet.png     the sprite sheet
    frames.json         state -> frames, durations, per-frame anchors
    costumes.png        costume sprites
    costumes.json       costume anchors and attach points
    sounds/*.wav        generated by tools/gen_sounds.py; absent until you run it
    preview.png         zoomed contact sheet of every frame (review aid)
    banner.png          the costume showcase in the README
    gifs/               README demo GIFs, rendered from the sheet by gen_demo_gif.py
  tools/
    gen_sprites.py      renders the sheet, manifest and costumes from code
    gen_sounds.py       synthesises the sound effects from code
    validate_sheet.py   checks a sheet against the art contract
  nib.spec            PyInstaller build
  requirements.txt
  tests/
    run_all.py          runs every suite - this is the whole test command
    smoke_*.py          the correctness suites
    *_rate.py           slow behavioural rate measurements (--rates)
  docs/                 this documentation
  .github/workflows/    CI: sheet is in sync, art contract, suites, docs, build
```

`behavior.py`, `world.py` and `mood.py` are deliberately **free of Qt**, so the whole state machine
can be reasoned about and tested headlessly without a display. `mood.py` also takes an injectable
clock, so a week of moods can be simulated in a millisecond.

`behavior.py` never touches the audio layer either — it raises named *events* into a small queue
and something else decides whether that makes a noise.

### The state machine

`IDLE`, `WALK`, `INJURED`, `ANTIC`, `HOVER_WAVE`, `ASK`, `WORKING`, `ANGRY`, `SLEEP`, `WAKE`,
`HIDDEN`, `DRAG`, `PEEK`, `PET`.

Key transitions: `IDLE ⇄ WALK` on the wander beat and at the strip bounds; `IDLE/WALK → ANTIC` for a
weighted random antic; `→ HOVER_WAVE` when the cursor is close; `→ PET` when it comes to rest *on*
him, which the wave defers to; `→ ASK` on click; `ASK → WORKING` on send, and `WORKING → ANTIC` if
the prompt had a shape worth commenting on; pokes over the threshold → `ANGRY` → storm-off `WALK` →
`SLEEP` → `WAKE` → `IDLE`; fullscreen or snooze → `HIDDEN`; mouse-down → `DRAG`, release → tumbling
gravity fall → `INJURED` if it was a real drop, else `IDLE`.

---

## Packaging

```bash
pip install pyinstaller
pyinstaller nib.spec
```

The result lands in `dist/Nib/Nib.exe`, windowed, with `assets/` bundled. If you enable
"Launch on Windows startup" from a frozen build, the registry entry points at the executable rather
than at `pythonw`.

Run `python tools/gen_sounds.py` **before** building if you want sound in the frozen app —
`nib.spec` bundles the whole `assets/sounds` directory, and a build made without it has no sounds
and fails completely silently, since the feature is off by default anyway.
