> **Status:** WP0, WP1 and WP2 done (WP0: measurements in 1.6, architecture decided: per-cap
> strip windows plus a flight layer that exists only while snow is airborne, realistic snow, no
> snowfall; WP1: the Qt-free `snow.py` model and `tests/smoke_snow.py`; WP2:
> `win.list_window_stack()`, `SnowField.sync` and `snow.clip`, `tests/smoke_snow_stack.py`). WP3
> is built (`snow_overlay.py`, the `snow` setting, the wiring in `main.py`,
> `tests/smoke_snow_overlay.py`) but **not ticked**: its acceptance "idle CPU within noise of
> today's" is not met (~0.6 % of one core at rest while snow shows, measured in WP3's Done note),
> and needs the owner's decision - accept a measured budget instead, or lower the cost and
> re-measure. Continue from **WP4** once that is settled. Run `py tests/run_all.py` and `py tools/check_docs.py` before each commit, and keep each file's line endings as they are.
>
> ### Where it stands (paused by the owner, 2026-11-15)
>
> **Done, reviewed and committed on `feat/christmas-snow`:** the plan, WP0 (measurements), WP1
> (`snow.py`), WP2 (window stack and clipping) and WP3 (`snow_overlay.py`, the `snow` setting,
> tray entry, one-time toast). 34/34 suites pass at `d726a15`. The owner live-tested WP3 and saw it
> work on a restored Notepad window: the strip appears and starts to build.
>
> **Saved but unfinished: branch `wip/christmas-snow-wp4-wp5` (`8a48052`).** The combined
> WP4+WP5 run (growth timing and `tests/snow_rate.py`, `win.on_battery()`, lowering the idle cost,
> Nib's reactions in `tests/smoke_snow_nib.py`) was stopped mid-implementation. It compiles and
> its three snow suites pass on their own; the full `run_all`, `check_docs`, the seeded rate
> comparison and the review were NOT run. Resume by checking it out, finishing and reviewing it,
> then merging it into `feat/christmas-snow`, or discard it and redo WP4+WP5.
>
> **Remaining, in this order:**
> 1. Finish WP4+WP5 from the saved branch (above), including the idle-cost reduction: snow at
>    rest raises a paused Nib from 0.36 % to 0.80 % of one core, mostly `list_window_stack` at
>    2 Hz over ~400 windows; the idea is to back off to 1 Hz or 0.5 Hz after unchanged passes and
>    snap back on a foreground or pointer change.
> 2. **Owner-approved fix A - snow on maximised windows.** Today a maximised window gets no cap,
>    and most people work maximised (the owner's two monitors each had only a maximised Chrome or
>    Claude window, so no snow showed at all). Draw a thinner pile just inside the top edge of a
>    maximised window, icicles hanging over its title bar or tab strip, still click-through.
> 3. **Owner-approved fix B - snow visible from the start.** Caps start bare and grow for ~20 min,
>    so at first it looks broken. When snow switches on, or a window first gets a cap, start it with
>    a visible layer, then keep growing.
> 4. WP6 play mode and the snowball, and WP7 docs and polish (they may run as one batch).
> 5. Final check (`run_all --rates`, `check_docs`, a live test on the owner's desktop), then a PR
>    from `feat/christmas-snow` to `main`. Nothing here is merged into `main` yet.
>
> **Live testing without touching the real settings:** copy `%APPDATA%\NibMascot` to a test
> folder, set `"snow": "always"` in its `config.json` (today is outside 1 Dec - 6 Jan), and start
> Nib from the worktree with `APPDATA` pointing at the test folder's parent. Only one Nib can run at
> a time (single-instance mutex), so quit the normal one first.
>
> - [x] WP0 spike: measure the surface
> - [x] WP1 `snow.py` model
> - [x] WP2 window stack and clipping
> - [ ] WP3 overlay, wiring and the setting (built; idle-CPU acceptance awaits a decision)
> - [ ] WP4 growth, icicles over time, battery
> - [ ] WP5 Nib's reactions
> - [ ] WP6 play mode and the snowball
> - [ ] WP7 docs and polish

# Nib "Christmas snow" — engineering spec for branch `feat/christmas-snow`

Snow piles up on the top edges of your windows, overhangs their corners and grows icicles. It looks
realistic: soft, shaded, anti-aliased snow (owner's decision). Nothing falls over the desktop: the
caps simply grow, slowly, with no visible flakes. A fast sideways swipe of the pointer through a pile
knocks it off; an upward flick through a pile throws it into the air, and it comes back down. Nib notices snow that lands on him and plays in it. A time-boxed "Play in
the snow" mode on his tool wheel lets you scoop and throw snowballs, including at him.

Designed with an advisor (Fable) from the real code; the measured numbers below are from this
machine (367 top-level windows, two monitors, one at a negative origin).

---

## 0. Ground rules for every package

The ground rules of `plans/LIVING-CREATURE-PLAN.md` section 0 apply unchanged: Qt-free logic modules,
measured rates for timed behaviour, full settings plumbing, no new sensors/network/content,
persistence only via `main._flush_config`, per-file line endings preserved (new files **LF**),
regenerated assets committed, tests as plain scripts registered in `tests/run_all.py` with the
README / CONTRIBUTING suite count updated, CHANGELOG `## [Unreleased]` bullets. In addition:

1. **The snow never takes a click it was not asked for.** Measured in WP0 (1.6): a translucent Qt
   window is a layered window whose alpha-0 pixels pass clicks through and whose painted pixels
   (any alpha >= 1) take them, so `ruler.py:14-20` is right and soft, alpha-shaded snow *would*
   swallow clicks without the flag. Therefore every snow window is **created** with Qt's
   `WindowTransparentForInput`, which makes Qt set `WS_EX_TRANSPARENT` itself and keep it across
   hide/show and native re-creation; a bit set only by hand (`win_integration.set_click_through`)
   is lost when Qt re-creates the HWND or recomputes the style (`setWindowFlags`). Belt and braces:
   `set_click_through(hwnd, True)` and the capture affinity are re-applied on every show and on
   `QEvent.WinIdChange`, and `setWindowFlags` is never called on a shown snow window. A test asserts
   `WindowFromPoint` over a painted pile never returns a snow window.
2. **Nothing is restacked.** CONTRIBUTING rule 1 and `tests/smoke_play.py:185` (exactly one
   `SetWindowPos` in `win_integration.py`, inside `set_topmost`). Occlusion is done by clipping.
   (WP3: the snow's own windows are not topmost, and the overlay lifts them back up with Qt's
   `raise_()` - its own windows only, no `SetWindowPos`; see WP3's Done note.)
3. **Idle is zero ticks.** When nothing is airborne, the animation timer is stopped; only the 2 Hz
   window stack poll and the slow growth timer run, and only while snow is showing.
4. **Below Nib.** The snow layer sits under the mascot, his bubble, the focus timer, the tool wheel
   and the toast; it never re-asserts topmost while the wheel or bubble is open.
5. **Hidden, not empty**, whenever it should not show (section 1.4): a visible full-screen layered
   window can cost games and video their direct-to-display path. The only full-monitor window (the
   flight layer, 1.2) exists only while snow is in the air, and is destroyed after it lands.

---

## 1. Shared foundations

### 1.1 Decisions (the owner's open questions, answered with the recommended defaults)

| Question | Decision |
| --- | --- |
| Default | On by default in the Christmas window, with a one-time toast saying how to turn it off |
| Dates | 1 December - 6 January (St Nicholas to Epiphany, matching the costume calendar) |
| Southern hemisphere | `"christmas"` mode goes by the date everywhere; `"winter"` mode follows `SEASON_WINTER` and `southern_seasons` |
| Look | Realistic (owner): soft, shaded, anti-aliased, alpha gradients, drawn from pre-rendered pixmaps |
| Falling snow | None (owner): no snowfall or flurries over the desktop; caps grow slowly with no visible flakes. Only brushed or thrown snow is ever in the air |
| Screen shares / screenshots | Hidden from capture (`WDA_EXCLUDEFROMCAPTURE`, as the guard does) |
| Night-sky tint | Not in this plan: it recolours the user's work |
| Accidental brushing | Any deliberate-speed pass knocks snow off (above ~0.8 px/ms sideways); a slow pointer heading for a button never does |
| Play mode | Yes, time-boxed (about 30 s), ended by timeout or right-click |

### 1.2 Architecture

- `snow.py` (new, Qt-free, clock- and rng-injectable): height-maps per window, growth, cap,
  overhang, icicles, airborne chunks (brushed and thrown snow), gravity, landing, budgets, the pointer
  gestures, and a pure clip function. `step(now_ms, pointer, stack) -> changes` (which caps changed
  shape, which caps moved, the airborne chunks and their bounding boxes), in the style of
  `guard_fx.BackgroundFx.step` (`guard_fx.py:590`). No flakes.
- `snow_overlay.py` (new, Qt), decided by the WP0 numbers (1.6), two kinds of window, all
  click-through, translucent, frameless, tool-type, topmost (WP3 made them not topmost: see its
  Done note), `WA_ShowWithoutActivating`,
  `WindowDoesNotAcceptFocus`, excluded from capture:
  - **Cap strips**: one small window per visible cap, sized to the cap plus overhang and icicles
    (about window width + 2 overhangs by ~90 px), from a pool (shown/hidden and re-used, not created
    per cap). Each paints one pre-rendered cap pixmap, clipped (`setMask` or a paint clip) by the
    pure clip function where other windows cover it. A strip repaints only when its cap's pixmap
    changes (a growth step, a brushed span, a new clip), and is moved by `setGeometry` only when the
    stack poll sees its window move. Idle cost measured at 0.006 % of one core for 15 strips.
  - **Flight layer**: one full-monitor window per monitor, **created on demand** when chunks leave a
    cap (brushed or thrown), repainted at 30 FPS with the chunks' grouped bounding box
    (`guard_fx.py:26-41`), and **destroyed** about 2 s after the last chunk lands (cold
    create+show measured at ~9-10 ms, first paint ~12-18 ms; it costs ~31 MB working set at 1440p
    while it exists, 0 once closed). The strips stay as they are underneath; landed snow goes back
    into a cap's height map.
  - Rejected: one permanent full-monitor overlay per monitor (47 MB working set for two monitors,
    4-9 ms per full repaint, and a permanent full-screen layered window, rule 5); growing a strip
    window to cover the chunks' flight (2-3x the CPU of the flight layer, see 1.6).
- `win_integration.list_window_stack()` (new): one `EnumWindows` pass, front to back, returning
  `(hwnd, rect, zoomed, topmost)` for every visible, non-iconic, non-cloaked, non-shell window that is
  not ours (the filters of `find_window_at`, `win_integration.py:566-574`, without the minimum size,
  since menus and tooltips must occlude). Top y from `GetWindowRect` (what the perch uses, so his
  feet and the snow line agree); left/right from `DWMWA_EXTENDED_FRAME_BOUNDS`. Measured 0.6-2.2 ms
  median, p90 7.4 ms at 367 windows, so polled at 2 Hz on its own timer, never per frame.
- Pointer: `win.get_cursor_pos()` (measured 2.4-4.2 us per call), read by the overlay's tick and
  passed into the model; the overlay never receives input outside play mode.

### 1.3 Look and physics

- Realistic, and cheap because nothing is drawn live: every cap is a **pre-rendered pixmap**
  (anti-aliased mound outline, vertical shading gradient, a soft highlight, a faint blue shadow
  underneath), re-rendered only when its shape changes (measured 1.5 ms median per 700x90 cap).
  Alpha gradients and soft edges are allowed: a partly transparent pixel would take a click, but
  every snow window carries `WS_EX_TRANSPARENT` (rule 1), so nothing does. No per-frame blur or
  glow (measured unusable, 1.6): softness is baked into the pixmaps. Physical pixels throughout,
  sizes scaled per monitor DPI.
- Piles: a height map at a few-px resolution, capped at about 24-30 px (at 96 DPI) in the middle and
  tapering to rounded corners, with 10-20 px of overhang past each side; growth reaches the cap in
  about 20 minutes of showing, in small steps (one re-render per step, never animated), with no
  visible flakes. Lip into the window at most a few px, zero over the right-hand caption-button zone.
- Icicles: 2-4 per overhang, lengths seeded from the hwnd, translucent tapered gradients hanging only
  outside the window's left and right edges; they grow a little every few minutes and snap off
  (into chunks) when brushed.
- Palette: the `snowcap` costume's tones (`tools/gen_sprites.py:2529`) as the base white and shade.
- Airborne chunks: soft, pre-rendered round lumps in a few sizes (6-18 px), budget 100 per monitor
  (measured: 100 chunks at 30 FPS cost ~8 % of one core), oldest fade first.
- Frame rate: 30 FPS while chunks are airborne (60 FPS measured affordable on the flight layer,
  ~13 % of one core with 100 chunks, so it is an option for WP3 if 30 looks choppy), timer stopped
  otherwise.
- Window moved/resized/closed/minimised: the pile at the old position breaks into falling chunks
  and regrows at the new one (resize: only the cut-off span falls). Maximised windows get no cap.
  (Measured alternative, if the owner prefers snow that rides along: moving one strip at 20 Hz
  during a drag costs ~3 % of one core, 1.2 ms per `setGeometry`; move-only does not repaint.)

### 1.4 When it shows

- Setting `snow`: `"off" | "christmas" | "winter" | "always"`, default `"christmas"`; a `_VALID_SNOW`
  tuple and `_coerce` branch, `settings.GROUPS` beside `daily_costumes`, HELP, a SETTINGS.md row.
  Requires `world_enabled`. Pure function `world.snow_active(date, mode, southern) -> bool`.
- Hidden entirely (window hidden, timers stopped): snoozed, the Away Guard up (its fog re-grabs the
  desktop every 3 s, `guard_overlay.py:20`), or a fullscreen app on that monitor. Because
  `is_fullscreen_on_monitor` only tests the foreground window, the stack pass also does its own test:
  any unzoomed window covering a monitor hides that monitor's snow.
- Static (caps shown, no growth, no brushing): a focus session, paused, ghost mode.
- On battery (`win.on_battery()`, new, `GetSystemPowerStatus`): caps shown, no growth; brushing
  and throwing still work, at 20 FPS instead of 30.
- Tray: "Snow: off for today".

### 1.5 Nib's part (reuses existing art)

- Snow landing on him (brushed or thrown chunks whose path crosses his rect): `startle`, then the
  `snowcap` costume for a minute via the moment-costume path (`behavior._moment(..., costume=...)`).
- Perched on a snowy window: the pile dents under his feet; an occasional `shiver`.
- Playing: when you brush or throw snow near him, a moment through `_pick_moment` (45 s shared
  cooldown, at most once per brush/throw burst): `hop` or `dance` with a puff thrown at his feet.
  There are no flurries to trigger anything.
- Snowball (play mode): a hit triggers `startle` then `football`, with a white ball drawn by the
  overlay. None of this enters `ANTICS`, so `fidget_rate` and `quirk_rate` must not move.

### 1.6 WP0 measurements

Measured on this machine (i9-13900H, 20 logical CPUs; monitors 2560x1440 at (1920, -249) and
1920x1080 primary, `QT_ENABLE_HIGHDPI_SCALING=0`), PySide6 6.11.1, Python 3.14, on the live desktop
with other apps running, so numbers vary run to run (where two runs exist the range is given).
Scratch script: `%TEMP%\nib-wp0\spike.py` (not committed; modes `full`, `strips`, `hit`, `real`,
`caps`, `chunks layer|strip`, `cold`). CPU is the process's cycle count (`QueryProcessCycleTime`)
divided by the nominal 2.995 GHz, as % of one core; `GetProcessTimes` was too coarse (15.6 ms ticks)
and DWM's own CPU could not be read (access denied), so compositor cost is **not** included.
"Sync repaint" is a synchronous `repaint()`: paint plus the flush to the layered window.

**Hit-testing** (`WindowFromPoint` from the same thread, another thread and another process, on both
monitors including the negative origin, at a painted pile pixel / a transparent pixel):

| Window | Pile pixel | Transparent pixel |
| --- | --- | --- |
| Translucent, no `WindowTransparentForInput`, no `WS_EX_TRANSPARENT` | **ours** | passes through |
| Same plus alpha-1 fill everywhere (the ruler's trick, play mode) | ours | ours |
| `set_click_through(True)` on either of the above | passes through | passes through |
| Created with `WindowTransparentForInput` (Qt sets `WS_EX_TRANSPARENT`) | passes through | passes through |

- The bit survives `hide()`/`show()`. Toggling the Qt flag with `setWindowFlags` (the HWND did not
  change) recomputes the style from Qt's flags: a hand-set bit is dropped when the Qt flag is off.
  `destroy()`+`create()` (new HWND) keeps the bit only when the Qt flag is set.
- `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` returns TRUE on every translucent overlay and
  strip; it is **lost** (reads 0) after native re-creation, so it must be re-applied on
  `WinIdChange` (as `guard_overlay.py` already does).
- Play mode on a `WindowTransparentForInput` window: `set_click_through(False)` plus an alpha-1 fill
  makes `WindowFromPoint` return the overlay everywhere (and stays so across hide/show), and
  `set_click_through(True)` restores pass-through. That proves OS routing only; whether Qt delivers
  the presses to a widget still flagged transparent-for-input is for WP6 to verify (or WP6 toggles
  the Qt flag while hidden).

**The owner's design: realistic caps, no snowfall.** 15 cap strips of 700x90 with a realistic
pre-rendered cap (AA mound, gradient shading, highlight, translucent icicles):

| Cap strips (15) | Result |
| --- | --- |
| Idle, timer stopped, 10 s | 0.006 % of one core |
| Memory (working set / private) | +11.7 MB / +4.4 MB for 15 (~0.8 MB WS each) |
| Cap change: re-render the cap pixmap | 1.5 ms median, 2.8 ms p90 |
| Cap change: sync repaint of one strip | 0.50 ms median, 1.2 ms p90 |
| Move one strip at 20 Hz (`setGeometry`, no repaint) | 2.9 % of one core; 1.2 ms median, 2.1 ms p90 per call |
| Move and resize one strip at 20 Hz (repaints) | 5.5 %; 1.4 ms median, 2.9 ms p90 |
| Move all 15 at 2 Hz | 2.6 %; 1.0 ms median, 2.4 ms p90 per call |
| Move and resize all 15 at 2 Hz | 4.8 %; 0.8 ms median, 1.9 ms p90 |

Airborne chunks (soft pre-rendered lumps 6-18 px, thrown up to ~650 px/s and falling ~720 px below
the cap, 6 s windows, on the 2560x1440 monitor):

| Chunks | (a) temporary full-monitor flight layer, dirty bounding box | (b) strip grown to cover the flight every frame |
| --- | --- | --- |
| 50 @ 30 FPS | 5.5 %; frame 1.5 ms median, 2.3 ms p90 | 14.3 %; frame 4.8 ms median, 6.1 ms p90 |
| 50 @ 60 FPS | 10.5 %; 1.6 / 2.2 ms | 32.2 %; 5.2 / 6.9 ms |
| 100 @ 30 FPS | 7.6 %; 2.1 / 3.4 ms | 16.3 %; 5.1 / 6.6 ms |
| 100 @ 60 FPS | 13.3 %; 1.9 / 2.6 ms (353 of 360 frames) | 32.1 %; 5.4 / 7.2 ms, 40 ms spikes |

Flight-layer lifecycle (2560x1440 / 1920x1080): cold construct+show+affinity 10.4 / 8.6 ms median
(14.8 / 9.8 p90), first paint done 17.8 / 11.8 ms; warm re-show 1.9 ms, hide 1.3 ms; close+delete
7.6 / 6.2 ms. While it exists (even hidden) it holds +31 MB WS / +14 MB private at 1440p; closing it
gives that back. The grown strip pays a `setGeometry` (1.1 ms median) plus a full repaint of its
whole (by then ~600 kpx) area every frame, which is why it costs 2-3x more.

**Pixel-style full-monitor overlay versus pixel strips** (the original question; 5 fake piles and
200 particles per monitor, 2 monitors; 10 strips of 600x40 with 20 particles each):

| Measurement | Full-monitor overlay (2) | Strips (10 of 600x40) |
| --- | --- | --- |
| Memory (WS / private) | +47.2 MB / +22.2 MB | +5.3 MB / +1.1 MB |
| Sync repaint, whole window | 4.1-8.0 ms median, 5.8-16.9 ms p90 | 0.13-0.46 ms median, 0.17-1.0 ms p90 |
| Sync repaint, one pile +-40 px | 0.19-0.48 ms median, 0.37-0.79 ms p90 | 0.12-0.56 ms median, 0.16-1.4 ms p90 |
| paintEvent, full / dirty | 0.9-1.6 ms / 0.21-0.26 ms median | 0.11-0.27 ms median |
| `update()` to paint end, full / dirty | 5.4-7.5 ms / 0.54-0.92 ms median (p90 10.8-12.2 / 1.1-1.6) | 1.2-3.1 ms median (p90 3.2-5.6) |
| CPU, full repaint at 20 / 8 FPS | 24.9 % / 9.4 % of one core | 9.4 % / 3.8 % (all 10 repainted) |
| CPU, dirty-rect repaint at 20 / 8 FPS | 3.1 % / 1.1 % | 10.8 % / 4.2 % |
| CPU idle, shown, timer stopped | 0.005-0.007 % | 0.005-0.008 % |

The full-window repaint of a full-monitor layered window costs 4-9 ms, almost all of it the flush
(the per-pixel-alpha update of the whole monitor; paintEvent itself is ~1-2 ms). Dirty rects avoid
it (0.2-0.5 ms). Ten strips all repainting each frame cost more than one overlay's single dirty rect,
because each window pays its own flush.

**Realistic snow, for comparison** (the abandoned "snow falling over the whole monitor" idea: 300
flakes per monitor of mixed sizes moving everywhere, so every frame is a full-window repaint of both
monitors; 10 s windows):

| Variant | CPU (one core) | paintEvent per window, median / p90 | Frame (both monitors, paint+flush), median / p90 | Full vs 400x200 dirty sync repaint |
| --- | --- | --- | --- | --- |
| (a) Pixel style, opaque blocks, 3 tones, 8 FPS | 11.4 % | 2.0 / 3.0 ms | 14.8 / 17.2 ms | 9.0 vs 0.7 ms |
| (a) Pixel style, 20 FPS | 28.4 % | 2.0 / 2.6 ms | 15.0 / 17.4 ms | (same) |
| (b) Realistic, pre-rendered soft pixmaps, 30 FPS | 44.8 % | 2.1 / 3.1 ms | 15.7 / 18.5 ms | 9.2 vs 1.4 ms |
| (b) Realistic, pre-rendered, 60 FPS | 70.7 %, only 457 of 600 frames | 2.6 / 4.6 ms | 18.1 / 30.7 ms | (same) |
| (c) Realistic, blurred every frame (offscreen `QGraphicsBlurEffect`), 30 FPS | 73.9 %, only **54 of 300** frames | 74 / 140 ms | 185 / 263 ms | meaningless: the blur redoes the whole layer whatever the clip |

A `QGraphicsBlurEffect` set on the top-level overlay itself had no measurable effect (same cost as
(b)): Qt does not apply graphics effects to top-level windows, so (c) blurs explicitly. In (a) and
(b) the whole-monitor layered-window update dominates: ~11 ms of each ~15 ms frame (two monitors,
~2 ms of painting each) is the flush, not the painting, so pre-rendered realistic flakes cost about
the same per frame as pixel blocks, the CPU difference is mostly frame rate, and a full-monitor 60
FPS animation is not attainable here.
Per-frame blur is out at any frame rate.

**Decision.** For the owner's design (realistic caps, nothing falling): **per-cap strip windows for
the resting caps, plus a full-monitor flight layer per monitor that exists only while brushed or
thrown snow is in the air** (1.2). Reasons: strips are idle-free (0.006 %), small (~0.8 MB each, so
a pool of 30 is ~25 MB against 47 MB for two permanent overlays), repaint one cap for 0.5 ms, and
leave no full-screen layered window on the desktop (rule 5); a permanent overlay would pay the 4-9
ms whole-window flush on every clip change and hold 47 MB for nothing. Airborne snow needs room to
fly, and the flight layer with dirty bounding boxes does it at 5-13 % of one core (30-60 FPS, 50-100
chunks) against 14-32 % for growing a strip, and costs ~10 ms to create and nothing once destroyed.

---

## 2. Work packages

### WP0 — Spike: measure the surface (no shipped code)
**Goal.** Decide full-monitor overlay versus per-cap strips with numbers, and settle hit-testing.
**Files.** A scratch script only (outside the repo), plus this plan's 1.2 and section 5 updated.
**Do.** Show a click-through translucent overlay on every monitor with a few painted piles; measure
strip and full repaint cost, memory, and whether `WindowFromPoint` ever returns it (with and without
`WS_EX_TRANSPARENT`); check with `WDA_EXCLUDEFROMCAPTURE` applied.
**Acceptance.** Numbers recorded in this plan; architecture decided; nothing else committed.
**Done.** See 1.6: per-cap strips plus an on-demand flight layer; hit-testing settled (rule 1).

### WP1 — `snow.py` model
**Files.** `snow.py`, `tests/smoke_snow.py` (registered).
**Behaviour.** As 1.2/1.3: height-maps, growth, cap, overhang, icicles, airborne chunks, gravity,
landing, budgets, brush and flick gestures from pointer samples, `step()` returning changed caps and
the chunks' grouped bounding boxes. No flakes.
**Tests.** Growth reaches the cap and stops; a brushed span turns into chunks with mass conserved;
a slow pointer brushes nothing; thrown snow goes up and comes back; budget never exceeded; `step`
reports nothing when nothing moves; deterministic under a seed.
**Done.** `SnowField` in `snow.py`: `add_cap` / `remove_cap` / `drop_cap` / `release_span` (the
one release primitive WP2's move, resize and close build on), `grow(now_ms)` on its own slow timer
(one step per cap per call, so a hidden spell never catches up), `pointer(x, y, t_ms)` for samples,
`step(now_ms, pointer=None) -> Changes` (changed caps, landings, gestures, budget fades, and dirty
boxes per monitor index, one per stroke and merged by `guard_fx._Dirty`'s rule rather than one box
per monitor, so two bursts at opposite ends never become a whole-monitor flush). `step` takes no
`stack`: the window stack is WP2's, which fills `Cap.covered` and calls the cap methods. Every exit
of snow is in `SnowField.ledger`; the growth constants are placeholders until WP4 measures them.
A cap grows to 26 px in the middle, up to 28 at the crest of its seeded wave (within 1.3's
24-30 px), but snow landing on it may pile up to 39 px (`CAP_MAX_PX`), so a throw visibly adds to
a full cap; `Cap.extent()` is sized for that.

### WP2 — Window stack and clipping
**Files.** `win_integration.py` (`list_window_stack`, extended frame bounds), `snow.py` (pure clip,
per-window pile lifecycle), tests.
**Tests.** Clip against synthetic stacks (covered, partly covered, maximised, popup on top); move,
resize, minimise and close turn snow into chunks; `smoke_play.py` still passes; the stack helper
filters cloaked/iconic/own windows (Win32 part may be exercised live as `smoke_play` does).
**Done.** `win.list_window_stack()`: one `EnumWindows` pass, `find_window_at`'s filters with no
minimum size, `(hwnd, rect, zoomed, topmost)` front to back; the rect's top is `GetWindowRect`'s,
its left, right *and bottom* the extended frame bounds (the bottom too, so the invisible resize
border never occludes), and `None` when the pass fails, which `sync` treats as "keep everything"
rather than "every window closed". Measured 0.49 ms median, 0.86 ms p90 on a quiet desktop (three
visible windows; WP0's 0.6-2.2 ms was on a busy one). `smoke_play` now also checks it is read-only
and never reads a title. `SnowField.sync(stack, now_ms, own=())` is the pure side, returning a
`StackSync` (added, caps, moved, removed, clipped, chunks, and `fullscreen` as state), because at
rest nothing calls `step`; it also drains every cap change pending, so the next `step` does not
report it twice. Rules, each in a constant and tested on synthetic stacks:
- A cap needs 120x60 px at 96 DPI (scaled), not maximised, not covering a monitor, its top edge on
  a monitor (the one holding most of it gives the scale: `set_monitors` takes `scales` now) and
  above that monitor's floor (not under the taskbar), and the band `COVER_BAND_PX` above it on a
  monitor too, so a window snapped to the top of the screen (unzoomed, top = the monitor's) gets
  no pile that would be all off-screen. At most `MAX_CAPS` = 30 (section 5): past that, windows are
  ranked by how much of their top edge shows (the `covered` rule: the pile is what shows), then by
  area. A pile keeps its place unless its top edge is all covered and a window whose top shows is
  waiting, so a maximised window over thirty piles cannot starve a visible one on another monitor,
  and two similar windows never swap places from pass to pass. With room for all (the usual case)
  none of this ranking runs.
- Moved = its top, both sides, or the scale changed (a top-edge resize is a move). The pile falls,
  a bare one starts at the new spot and does not grow for `RESPAWN_HOLD_MS` (one growth period).
- Resized by one side: only the snow past the new end, and above the new rounded end's profile
  (scaled to `CAP_MAX_PX`), falls, in its own stroke per end; the rest is carried by absolute x,
  overlap-weighted when the left edge moves the grid (mass exact; the straddling sliver is booked
  `vanished`); the unmoved side keeps its icicles, the moved side's snap. Bottom only: nothing.
  A hidden pile, or one on a fullscreen monitor, loses its cut-off span quietly, by the same rule
  as below: chunks would fly unclipped over the window or the game in front.
- Closed, minimised, hidden, maximised, too small, off every monitor: the whole pile falls.
- Quiet (no chunks) when it holds under `QUIET_FRACTION` = 0.1 of a full pile, which is the drag
  rule (a window dragged for a minute throws once, on the first pass), when all of it was covered,
  when its monitor is fullscreen, when `MASS_EXIT_CAPS` (3) and over half the piles go in one pass
  (a virtual-desktop switch cloaks them all), or when it lost its place to the pool limit (only an
  all-covered pile ever does).
- `Cap.covered`: a window in front covers the columns it spans if it reaches into the band
  `COVER_BAND_PX` (half a grown cap) above the window's top edge; `Cap.occluders` are the front
  windows cut to the cap's extent, and `snow.clip(extent, occluders)` turns them into the
  rectangles the strip may draw (checked pixel by pixel on 200 random cases).
- `fullscreen`: monitors an unmaximised window covers whole, with `is_fullscreen_on_monitor`'s
  1 px slack.
WP1's open concern is fixed at the source: `remove_cap` takes the hwnd out of `Changes.caps` and
`moved` and puts it in a new `Changes.removed`, so `caps` only ever names caps that exist (and
`step` intersects it with them again); `version` carries on across a move or resize. A pass of
`sync` over 100 windows and 30 caps, the ranking included, costs about 2 ms: 1.7-5.9 ms median
over 11 runs of the suite, measured while other agents' test suites ran on the same machine, so
the low end is the quiet-machine figure.
Suite: `tests/smoke_snow_stack.py`.

### WP3 — Overlay, wiring and the setting
**Files.** `snow_overlay.py`, `main.py`, `config.py`, `settings.py`, `docs/SETTINGS.md`, `world.py`
(`snow_active`), `tray.py`, tests (`tests/smoke_snow_overlay.py`).
**Behaviour.** A pool of cap strips drawing pre-rendered realistic cap pixmaps, moved only on the
2 Hz stack poll; a per-monitor flight layer created when chunks fly and destroyed ~2 s after they
land; grouped repaints, timer stopped when idle, every hide rule in 1.4, pointer brush and flick,
capture exclusion and click-through re-applied on every show and `WinIdChange`, the one-time toast,
the tray entry.
**Tests.** Offscreen/real QApplication: timer stopped when idle; hidden under guard, snooze and
fullscreen; `WindowFromPoint` never returns a snow window; date-window table for `snow_active`;
`smoke_ui.py` and `check_docs.py` pass.
**Acceptance.** No click lost on a snowy title bar; idle CPU within noise of today's.
**Done.** `snow_overlay.py`: `render_cap` / `render_lump` / `render_shard`, `CapStrip`,
`FlightLayer` and the `SnowOverlay` controller; `world.snow_active`; the `snow` setting (World tab)
and a hidden latch `snow_hint_seen` for the one-time toast (108 settings, not 107: the toast has to
be one-time across restarts); **Snow: off for today** in the tray, held in memory only (a restart
the same day brings the snow back); `main._update_snow` on the 700 ms geometry poll and at once on
a snooze or a guard. Suite: `tests/smoke_snow_overlay.py`. What differs from the text above:
- **Not topmost** (1.2 said topmost). Measured with `EnumWindows` and a child process owning two
  windows it activates in turn: a non-topmost tool window lands, on show and on `raise_()`, at the
  top of the normal band - above the foreground window, below every topmost window (Nib, his
  bubble, wheel and focus timer, the taskbar, menus) - so the snow is under Nib by construction and
  nothing of his is ever re-raised. Activating a window puts it above the strips (`N A B S`), so the
  2 Hz pass raises every shown strip again (`raise_`, our own windows only) whenever the front-most
  non-topmost window changes; the clip from the same pass hides whatever is in front of a pile. The
  flight layer is non-topmost too: chunks pass behind Nib and the taskbar. Nib's own ordinary
  windows (Settings, dialogs) share that band and `list_window_stack` leaves them out, so the
  overlay adds them to the front of the stack it hands `sync`, flagged maximised: they hide piles
  and never carry one. Which window is at the front is still read from the real stack (plus
  whether our own set changed), so the strips are raised again while Settings stays open.
- Behind a fullscreen app the piles also stop growing and the pointer is not sampled there; over
  one of Nib's topmost windows (the mascot, bubble, wheel, toast, focus timer) the pointer is not
  fed to the model and the movement is broken, so nothing hidden under him is brushed.
- **Rendering**: a pile is ~9-10 ms median (12 ms p90) for a 740x68 px extent at 96 DPI and
  ~19-25 ms for the widest (1840-2450 px), against 1.6's 1.5 ms for a much plainer cap - measured
  while other agents' suites ran, and done once per growth step. While snow flies a pile is
  re-rendered at most every `RENDER_GAP_MS` = 100 ms however often it changes; brushing a 1700 px
  pile at 125 % DPI measured 0.99 ms median per frame, 4.1 ms p90, 26 ms max (the render frames).
- **Idle CPU is not within noise** (acceptance not met): with snow showing at rest, the 2 Hz pass
  costs 2.5 ms median, 3.8 ms p90 (~3 ms of it `list_window_stack` at ~400 top-level windows), so
  the overlay alone goes from 0.007 % to ~0.6 % of one core, and a paused, otherwise idle Nib from
  0.36 % to 0.80 %. With the pointer near a pile (60 Hz sampling) it is ~2.4 %. Halving the poll,
  or skipping it while nothing in the stack changes, is the lever if WP7 wants it lower.
- On battery: `main._on_battery` asks `win.on_battery` if it exists (WP4 adds it), else mains.
- `docs/PRIVACY.md` got one sentence ahead of WP7: the snow reads the same rectangles twice a
  second while it shows.

### WP4 — Growth, icicles over time, battery
**Files.** `snow.py`, `win_integration.py` (`on_battery`), `snow_overlay.py`, `tests/snow_rate.py`
(registered in `RATES`).
**Behaviour.** Caps grow slowly to their full height (about 20 minutes of showing) in small steps,
each step one cap re-render and one strip repaint, never animated and with no visible flakes;
icicles lengthen every few minutes; on battery caps stay but do not grow (1.4). No flurries, no
taskbar drift.
**Acceptance.** Measured growth and icicle rates quoted in the docs; zero animation ticks while caps
only grow (the growth timer is slow and repaints only the caps that changed); battery rule tested.

### WP5 — Nib's reactions
**Files.** `behavior.py`, `main.py`, tests (`tests/smoke_snow_nib.py` or an existing suite),
`snow_rate.py`.
**Acceptance.** As 1.5 (reactions triggered by brushed or thrown snow only); `fidget_rate` / `quirk_rate` byte-identical under the seeded comparison with
snow on and off.

### WP6 — Play mode and the snowball
**Files.** `snow_overlay.py`, `tool_wheel.py` (icon), `main.py`, tests.
**Behaviour.** "Play in the snow" on the wheel: for ~30 s the flight layer is created on each monitor
as the input surface, drops click-through (`set_click_through(False)`) and paints an alpha-1 fill (the
ruler's trick, `ruler.py:18-21`; WP0 measured that `WindowFromPoint` then returns it everywhere);
press on a pile to scoop a snowball, drag, release to throw; it can hit Nib. Right-click or the
timeout ends it, always, restoring click-through (a native re-creation fails closed, because the
window keeps Qt's `WindowTransparentForInput`). Verify first that Qt delivers presses to a window
still flagged `WindowTransparentForInput` once the bit is cleared; if not, toggle the Qt flag while
the layer is hidden.
**Acceptance.** Mode always ends; nothing is captured outside it.

### WP7 — Docs and polish
**Files.** `docs/BEHAVIOUR.md`, `docs/USING-HIM.md`, `docs/PRIVACY.md` (the stack pass reads window
rectangles only, never titles or contents), `docs/HOW-IT-WORKS.md`, README, CHANGELOG.

### WP8 (deferred) — a throwing animation for Nib (about 6 frames, `tools/gen_sprites.py`).

---

## 4. Reviewer checklist (every WP)

- [ ] `py tests/run_all.py` and `py tools/check_docs.py` pass; new suites registered; README and
      CONTRIBUTING suite counts right.
- [ ] No `PySide6` import in `snow.py`, `world.py`, `behavior.py`.
- [ ] No click ever captured outside play mode; nothing restacked; `smoke_play.py` passes.
- [ ] Timer stopped when idle; budgets respected.
- [ ] `fidget_rate` / `quirk_rate` unchanged (seeded comparison) where behaviour was touched.
- [ ] Line endings preserved; no whole-file rewrites.

## 5. Risks

- A full-window repaint of a translucent full-monitor window costs 4-9 ms, mostly the layered
  flush (1.6): the flight layer must only ever repaint grouped bounding boxes, never the whole window.
- Topmost fights with Nib's own windows and other topmost apps. New in this design: every `show()` of
  a strip or of a freshly created flight layer lands at the top of the topmost band, above Nib, his
  bubble and the wheel, and rule 2 forbids restacking. WP3 must find an order that keeps the snow
  below him (for example re-asserting Nib's own topmost after a snow window shows, through the
  existing `set_topmost` path) and test it. WP3: settled by making the snow windows not topmost
  (its Done note), tested with `EnumWindows` in `tests/smoke_snow_overlay.py`.
- Qt re-creating the HWND (measured): `WS_EX_TRANSPARENT` survives only because the window carries
  `WindowTransparentForInput`; the capture affinity does not survive. Re-apply both on `WinIdChange`.
- Mixed DPI and a negative monitor origin: physical pixels throughout
  (`QT_ENABLE_HIGHDPI_SCALING=0`), per-monitor sizes. Hit-testing measured the same on both monitors.
- Airborne chunks briefly draw over the Start menu and context menus (topmost); they are few and
  short-lived, and the clip function can drop chunks over popups.
- A visible full-screen layered window can disable direct-to-display for games and video: the flight
  layer exists only while snow is in the air, and is never created while a fullscreen app runs.
- Pool size: many open windows means many strips (~0.8 MB each); cap the pool (about 30) and give
  caps to the largest visible windows first.
