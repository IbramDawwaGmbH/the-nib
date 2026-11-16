> **Status:** WP0-WP5 done (WP0: measurements in 1.6, architecture decided: per-cap strip
> windows plus a flight layer that exists only while snow is airborne, realistic snow, no
> snowfall; WP1: the Qt-free `snow.py` model and `tests/smoke_snow.py`; WP2:
> `win.list_window_stack()`, `SnowField.sync` and `snow.clip`, `tests/smoke_snow_stack.py`; WP3:
> `snow_overlay.py`, the `snow` setting, the wiring in `main.py`, `tests/smoke_snow_overlay.py`,
> its idle cost accepted by the owner as the budget - 0.14 % of one core at rest after WP4,
> against 0.002 % with no snow (WP3's Done note); WP4: eased growth, `win.on_battery()`, the
> stack poll's back-off at rest, `tests/snow_rate.py`; WP5: Nib's reactions,
> `tests/smoke_snow_nib.py`, the rates in `docs/BEHAVIOUR.md`). WP4 and WP5 were saved
> unreviewed as "wip(snow): WP4+WP5 partial"; the WP3 sign-off audited both against their
> sections and found them complete (their Done notes). WP6 (play mode, `tests/smoke_snow_play.py`)
> is built and awaiting the owner's test run; WP7 (the docs, for the whole feature including play
> mode) is done. WP8 stays deferred. Run `py tests/run_all.py` and `py tools/check_docs.py` before each commit, and keep each file's line endings as they are.
>
> - [x] WP0 spike: measure the surface
> - [x] WP1 `snow.py` model
> - [x] WP2 window stack and clipping
> - [x] WP3 overlay, wiring and the setting
> - [x] WP4 growth, icicles over time, battery
> - [x] WP5 Nib's reactions
> - [ ] WP6 play mode and the snowball - built, awaiting the owner's test run
> - [x] WP7 docs and polish
> - [ ] WP8 a throwing animation for Nib - deferred

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
   window stack poll and the slow growth timer run, and only while snow is showing. (WP4: at rest
   the poll walks the stack every 2 s, with a glance at the front window and the pointer between.)
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
- **Signed off on the measured budget** (owner's decision, after WP4). The acceptance "idle CPU
  within noise of today's" is met by accepting WP4's measured cost as the budget rather than to
  the letter: the overlay alone at rest is **0.14 %** of one core with the pointer still (3 runs,
  30 full stack passes a minute) and 0.40 % with it moving (120 a minute), against 0.43-0.47 %
  before WP4's back-off and 0.002 % with no snow. The other choice, asking for less (a slower
  poll, or no full pass at all while the glance sees nothing), was not taken.

### WP4 — Growth, icicles over time, battery
**Files.** `snow.py`, `win_integration.py` (`on_battery`), `snow_overlay.py`, `tests/snow_rate.py`
(registered in `RATES`).
**Behaviour.** Caps grow slowly to their full height (about 20 minutes of showing) in small steps,
each step one cap re-render and one strip repaint, never animated and with no visible flakes;
icicles lengthen every few minutes; on battery caps stay but do not grow (1.4). No flurries, no
taskbar drift.
**Acceptance.** Measured growth and icicle rates quoted in the docs; zero animation ticks while caps
only grow (the growth timer is slow and repaints only the caps that changed); battery rule tested.
**Done.** Built together with WP5 (one run, at the owner's request). Rate script:
`tests/snow_rate.py` (in `RATES`). What was done, and what differs from the text above:
- **Growth eases out.** Even steps (WP1's placeholder, 40 x 30 s) measured 4.5 min to a 6 px
  middle and 21.5 to full: too slow to read as snow. A column's step is now
  `target / GROWTH_STEPS * 2 * sqrt(1 - fill)`, never under `GROWTH_MIN_PACE` (0.35) of the even
  pace, with `GROWTH_STEPS` = 42 at the same 30 s period. Measured (800 px window at 96 DPI,
  medians of 12 windows' seeds): the middle at 3 px after 1.3 min, 6 px after 2.8, 10 px after
  4.3, 20 px after 11, full after 19.8-20.0 (19.4-20.5 across windows), the same whether `grow`
  runs every 0.5 s, 2 s or 30 s. Icicles kept their 180 s / 2 px step: the first is 4 px long 12
  minutes in, the last at full length (8-28 px) after 39-48. `QUIET_FRACTION` went from 0.1 to
  0.2 to keep its meaning (about two and a half minutes of growth: the eased steps reach 0.1 in
  one), and `smoke_snow`'s "half grown at ten minutes" became "about two-thirds" (0.73 measured).
- **Battery**: `win.on_battery()` (`GetSystemPowerStatus`, `ACLineStatus == 0`; unknown or a
  failed call is mains), called by `main._on_battery` on the 700 ms poll; the wiring test checks
  that on battery the overlay is shown, not growing, at `FPS_BATTERY`.
- **Idle cost** (WP3's open item). Profiled first: `list_window_stack` at 380 top-level windows
  (19 visible) is ~2.2 ms median, 6.8 ms p90, and the bare `EnumWindows` callback into Python is
  ~0.5 ms of it before any filter - the cost is per window, so reordering the filters would not
  help. Instead the poll **backs off at rest**: after `STACK_SETTLE_PASSES` (4) full passes in a
  row with nothing changed (no window opened, closed or moved, the same front window, nothing in
  the air, the pointer not near a pile), a tick only glances at the foreground window, its
  `GetWindowRect` and the pointer (`win.foreground_glance()` plus `get_cursor_pos`, ~3.4 us
  together) and walks the stack only if one of them changed, or every `STACK_IDLE_EVERY` (4)
  ticks regardless (2 s). Any change snaps it back to every tick. Growth does not count as a
  change (thirty growing piles would otherwise keep it at full rate for 20 minutes); it still
  happens on the 2 s passes. A dent or a puff refreshes its strip itself rather than waiting for a
  pass. Measured, the overlay alone in its own process on the live desktop (real stack and
  monitors, bare piles so nothing showed, `QueryProcessCycleTime` / 2.995 GHz, 20 s windows):
  **before 0.43-0.47 % of one core** (40 passes / 20 s; WP3 had 0.6 % on a busier desktop),
  **after 0.14 % with the pointer still** (3 runs, 10 passes / 20 s) and **0.40 % with it
  moving** (as before: every tick sees a change), against 0.002 % with no snow. Passes per minute
  at rest (`snow_rate`): 30, was 120. A window moved by something other than the pointer and not
  at the front is seen up to 2 s late. Not measured: the whole app paused (WP3's 0.36 % -> 0.80 %
  figure), which would have meant snow on the owner's live desktop.
- **Audited** in the WP3 sign-off (this was committed unreviewed as "WP4+WP5 partial"): every
  item above is in the code and tested; the one gap was the acceptance's "rates quoted in the
  docs" - they were only in the CHANGELOG, `snow.py` and here - so `docs/BEHAVIOUR.md` now has
  "Snow on your windows" with the growth and icicle table, citing `tests/snow_rate.py`. Re-run
  at sign-off: middle 3 / 6 / 10 / 20 px after 1.3 / 2.8 / 4.3 / 10.8 min and full after 19.8
  with `grow` every 0.5 s or 2 s (20.0 every 30 s), first icicle 11.9 min, all full after
  38.9-48.0; 30 full passes a minute at rest, 120 with the pointer moving. The acceptance's
  "zero animation ticks while caps only grow" had no test of its own (only "at rest" before any
  growth): `smoke_snow_overlay` now checks that a growth step on the backed-off poll starts no
  animation, no pointer sampling and no flight layer.

### WP5 — Nib's reactions
**Files.** `behavior.py`, `main.py`, tests (`tests/smoke_snow_nib.py` or an existing suite),
`snow_rate.py`.
**Acceptance.** As 1.5 (reactions triggered by brushed or thrown snow only); `fidget_rate` / `quirk_rate` byte-identical under the seeded comparison with
snow on and off.
**Done.** Suite: `tests/smoke_snow_nib.py`; rates in `tests/snow_rate.py`. `Behavior` stays
Qt-free: `main._tick_snow` drains the overlay's `take_landed` / `take_gestures` every tick and
hands them in as plain calls, and carries the dent and the puff back out.
- **Hit**: `Behavior.snow_landed()` plays `startle` through `_moment(..., costume="snowcap")`, and
  `persona_costume` keeps the snowcap for `SNOW_HAT_MS` (60 s) after it - the moment-costume path
  only lasts as long as its antic, so the minute is a timer read through `persona_costume`, with
  `_may_dress()` asked on every read (a costume you pick takes it off at once, and
  `clear_persona_costume` drops the timer). One jump per shower (`SNOW_HIT_COOLDOWN_MS`, 8 s); the
  minute is held while he is paused or hidden. Not gated on the shared moment cooldown: it is a
  reaction to being hit, like the poke flinch, not a moment about the world.
- **Play**: `Behavior.snow_nearby(x, y)` for each brush or flick; within `SNOW_PLAY_NEAR_PX` (200)
  of his centre, the first gesture of a burst (`SNOW_BURST_GAP_MS`, 1.5 s) opens an ask for
  `SNOW_PLAY_WINDOW_MS` (6 s), and an idle mascot gets to his decision in 250 ms. `_pick_moment`
  answers it first (it lapses in seconds; everything else there waits on a latch or a window) with
  `hop` or `dance` at `SNOW_PLAY_CHANCE` (0.6), behind the shared 45 s cooldown and its own
  `SNOW_PLAY_COOLDOWN_MS` (2 min), and asks the app for a puff (`take_snow_puff` ->
  `SnowOverlay.puff` -> `SnowField.puff`: five small new lumps from his feet, booked as grown,
  flagged `misses_nib` so they never land on him and startle him mid-hop). The eye-tracking check
  stands aside while an ask is open, since brushing near him brings the pointer near him too.
  Measured: brushing beside him every 20 s for an hour gives 19-22 moments (3 seeds; hops and
  dances about even). While he is perched an ask simply lapses: a perch never reaches
  `_pick_moment`.
- **Perched**: `SnowField.set_dent(hwnd, span)` presses the columns under his feet (the middle
  40 % of his sprite) to `DENT_PX` (4 px) and pushes the snow into a 3-column rim either side
  (overflow booked `vanished`); growth does not refill it while he stands there and does after.
  Idempotent (called every tick; no version bump when nothing changed), not queued in `Changes`
  (the overlay re-renders that strip at once, `SnowOverlay.set_perch`), and a pile replaced by a
  move or resize comes back undented and is pressed again on the next tick. `perch_on` stands him
  on `GetWindowRect`'s top, the same handle and top the stack keys the pile by. A pile at least
  `SNOWY_PERCH_FULL` (0.25) full makes `set_snowy_perch(True)`; `_tick_perch` then shivers every
  `SNOW_SHIVER_EVERY_MS` (4-8 min, drawn lazily on the first snowy perch), through the shared
  cooldown, not while focusing or with antics off. Quirks are held off on a perch because an antic
  ends in IDLE and `update_perch` only follows the window in PERCH; the shiver instead ends straight
  back in PERCH (`_tick_antic`). Measured: 9.8-10.8 shivers an hour standing on a snowy window
  (3 seeds, 4 hours each; 1.5-3 min first, which measured 26 an hour, was a tic).
- None of it fires asleep or napping, waking, held, falling, hidden, cross, paused, focusing,
  while you are away, holding the sign or mid-quick-change (`_snow_unreachable`). The guard and a
  snooze hide the snow, so nothing reaches him then anyway.
- **Byte-identical**: no random number is drawn on any snow path until snow has reached him (the
  shiver clock is drawn on the first snowy perch, the play roll only with an ask open), checked in
  the suite by twenty simulated minutes with and without snow about, and by `fidget_rate`,
  `quirk_rate`, `day_rate`, `chatter_rate` and `ledge_rate` run seeded (`random.seed(12345)`,
  `PYTHONHASHSEED=0`) on `git archive HEAD` and on this tree: identical output.
- **Audited** in the WP3 sign-off against 1.5 and the acceptance: complete (the snowball is
  WP6's). The seeded comparison was re-run, since the "partial" commit said it had not been:
  `fidget_rate`, `quirk_rate`, `day_rate`, `chatter_rate` and `ledge_rate` on `git archive` of
  the WP3 commit (`d625803`, before any of WP5) and on this tree give byte-identical output.
  `snow_rate` re-run: 9.8 / 10.8 / 10.0 shivers an hour, 21 / 19 / 22 play moments an hour
  (32 hops, 30 dances). A hit while he is perched ends, like any antic there, in an idle beat
  and back onto the window (checked; not changed). The rates are now in `docs/BEHAVIOUR.md`.

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
**Done.** `docs/USING-HIM.md` has a Christmas snow section (when it shows, the four `snow` modes,
brushing and flicking, his reactions, keeping out of the way, **Snow: off for today**, turning it
off) with **Play in the snow** under it, plus a quick-table row, a wheel note and a tray entry;
`docs/BEHAVIOUR.md` a play-mode paragraph after the rates; `docs/PRIVACY.md` says the stack pass
reads rectangles and class names, never titles or contents, and what play mode does with clicks;
`docs/HOW-IT-WORKS.md` a section on the strips, the flight layer, the 2 Hz poll and its back-off
and the accepted 0.14 % idle cost, and both modules in the layout; README a short section and 36
suites (CONTRIBUTING too, counting `smoke_snow_play`); CHANGELOG one consolidated set of entries
for the whole feature. Written while WP6 was being built, from its section and the code so far:
where the wheel puts **Play in the snow** and the football after a hit are to be checked against
WP6 as built.

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
