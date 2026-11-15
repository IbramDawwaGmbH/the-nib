> **Status:** nothing implemented yet. Continue from **WP0**. Run `py tests/run_all.py` and `py tools/check_docs.py` before each commit, and keep each file's line endings as they are.
>
> - [ ] WP0 spike: measure the surface
> - [ ] WP1 `snow.py` model
> - [ ] WP2 window stack and clipping
> - [ ] WP3 overlay, wiring and the setting
> - [ ] WP4 flurries, taskbar drift, battery
> - [ ] WP5 Nib's reactions
> - [ ] WP6 play mode and the snowball
> - [ ] WP7 docs and polish

# Nib "Christmas snow" — engineering spec for branch `feat/christmas-snow`

Snow piles up on the top edges of your windows, overhangs their corners and grows icicles, in Nib's
own blocky pixel style. Flurries fall over the desktop and land on the piles. A fast sideways swipe
of the pointer through a pile knocks it off; an upward flick through a pile throws it into the air,
and it comes back down. Nib notices snow that lands on him and plays in it. A time-boxed "Play in
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

1. **The snow never takes a click it was not asked for.** Outside play mode (WP6) every snow window
   carries `WS_EX_TRANSPARENT` (`win_integration.set_click_through`) as well as Qt's
   `WindowTransparentForInput`. A test asserts `WindowFromPoint` over a painted pile never returns a
   snow window. (The repo disagrees with itself about layered-window hit-testing - `ruler.py:14-20`
   versus `mascot_window.py:5-6` - so we rely on neither.)
2. **Nothing is restacked.** CONTRIBUTING rule 1 and `tests/smoke_play.py:185` (exactly one
   `SetWindowPos` in `win_integration.py`, inside `set_topmost`). Occlusion is done by clipping.
3. **Idle is zero ticks.** When nothing moves, the animation timer is stopped; only the 2 Hz window
   stack poll runs, and only while snow is showing.
4. **Below Nib.** The snow layer sits under the mascot, his bubble, the focus timer, the tool wheel
   and the toast; it never re-asserts topmost while the wheel or bubble is open.
5. **Hidden, not empty**, whenever it should not show (section 1.4): a visible full-screen layered
   window can cost games and video their direct-to-display path.

---

## 1. Shared foundations

### 1.1 Decisions (the owner's open questions, answered with the recommended defaults)

| Question | Decision |
| --- | --- |
| Default | On by default in the Christmas window, with a one-time toast saying how to turn it off |
| Dates | 1 December - 6 January (St Nicholas to Epiphany, matching the costume calendar) |
| Southern hemisphere | `"christmas"` mode goes by the date everywhere; `"winter"` mode follows `SEASON_WINTER` and `southern_seasons` |
| Steady snow vs flurries | Flurries (about 60-90 s every few minutes), so idle really is zero ticks |
| Screen shares / screenshots | Hidden from capture (`WDA_EXCLUDEFROMCAPTURE`, as the guard does) |
| Night-sky tint | Not in this plan: it recolours the user's work |
| Accidental brushing | Any deliberate-speed pass knocks snow off (above ~0.8 px/ms sideways); a slow pointer heading for a button never does |
| Play mode | Yes, time-boxed (about 30 s), ended by timeout or right-click |

### 1.2 Architecture

- `snow.py` (new, Qt-free, clock- and rng-injectable): height-maps per window, growth, cap,
  overhang, icicles, particles (flakes, chunks, thrown snow), gravity, landing, budgets, the pointer
  gestures, and a pure clip function. `step(now_ms, pointer, stack) -> dirty groups`, in the style of
  `guard_fx.BackgroundFx.step` (`guard_fx.py:590`).
- `snow_overlay.py` (new, Qt): one click-through, translucent, frameless, tool-type, topmost window
  per monitor, drawing the model procedurally from string-row sprites like
  `guard_fx._sprite_pixmap` (`guard_fx.py:249`), with grouped dirty-rect repaints after
  `guard_fx.py:26-41`. WP0 decides full-monitor versus per-cap strips by measurement.
- `win_integration.list_window_stack()` (new): one `EnumWindows` pass, front to back, returning
  `(hwnd, rect, zoomed, topmost)` for every visible, non-iconic, non-cloaked, non-shell window that is
  not ours (the filters of `find_window_at`, `win_integration.py:566-574`, without the minimum size,
  since menus and tooltips must occlude). Top y from `GetWindowRect` (what the perch uses, so his
  feet and the snow line agree); left/right from `DWMWA_EXTENDED_FRAME_BOUNDS`. Measured 0.6-2.2 ms
  median, p90 7.4 ms at 367 windows, so polled at 2 Hz on its own timer, never per frame.
- Pointer: `win.get_cursor_pos()` (measured 2.4-4.2 us per call), read by the overlay's tick and
  passed into the model; the overlay never receives input outside play mode.

### 1.3 Look and physics

- Piles: one column per block, heights in blocks, capped at about 6 blocks in the middle and tapering
  to the corners, 2-3 blocks of overhang past each side; growth reaches the cap in about 20 minutes
  of snowfall, one block step at a time. Lip into the window at most one block, zero over the
  right-hand caption-button zone.
- Icicles: 2-4 per overhang, lengths seeded from the hwnd, hanging only outside the window's left and
  right edges; they grow one block every few minutes and snap off when brushed.
- Palette: the `snowcap` costume's tones (`tools/gen_sprites.py:2529`); every pixel fully opaque or
  absent, integer block scale (per monitor, like `guard_fx.block_for`, `guard_fx.py:268`).
- Particles: flakes ~30 per 1080p (cf. `MOTES_PER_1080P = 34`), chunks up to 60, thrown up to 80;
  hard budget 200 per monitor, oldest fade first. A flake landing on a visible cap adds to it.
- Frame rate: 20 FPS while chunks or thrown snow are airborne, 8 FPS during a flurry, timer stopped
  otherwise.
- Window moved/resized/closed/minimised: the pile at the old position breaks into falling chunks
  and regrows at the new one (resize: only the cut-off span falls). Maximised windows get no cap.

### 1.4 When it shows

- Setting `snow`: `"off" | "christmas" | "winter" | "always"`, default `"christmas"`; a `_VALID_SNOW`
  tuple and `_coerce` branch, `settings.GROUPS` beside `daily_costumes`, HELP, a SETTINGS.md row.
  Requires `world_enabled`. Pure function `world.snow_active(date, mode, southern) -> bool`.
- Hidden entirely (window hidden, timers stopped): snoozed, the Away Guard up (its fog re-grabs the
  desktop every 3 s, `guard_overlay.py:20`), or a fullscreen app on that monitor. Because
  `is_fullscreen_on_monitor` only tests the foreground window, the stack pass also does its own test:
  any unzoomed window covering a monitor hides that monitor's snow.
- Static (no new snow, no flurries): a focus session, paused, ghost mode.
- On battery (`win.on_battery()`, new, `GetSystemPowerStatus`): caps only, no flurries.
- Tray: "Snow: off for today".

### 1.5 Nib's part (reuses existing art)

- Snow landing on him: `startle`, then the `snowcap` costume for a minute via the moment-costume
  path (`behavior._moment(..., costume=...)`).
- Perched on a snowy window: the pile dents under his feet; an occasional `shiver`.
- Playing: a once-per-flurry moment through `_pick_moment` (45 s shared cooldown): `hop` or `dance`
  with a puff thrown at his feet.
- Snowball (play mode): a hit triggers `startle` then `football`, with a white ball drawn by the
  overlay. None of this enters `ANTICS`, so `fidget_rate` and `quirk_rate` must not move.

---

## 2. Work packages

### WP0 — Spike: measure the surface (no shipped code)
**Goal.** Decide full-monitor overlay versus per-cap strips with numbers, and settle hit-testing.
**Files.** A scratch script only (outside the repo), plus this plan's 1.2 and section 5 updated.
**Do.** Show a click-through translucent overlay on every monitor with a few painted piles; measure
strip and full repaint cost, memory, and whether `WindowFromPoint` ever returns it (with and without
`WS_EX_TRANSPARENT`); check with `WDA_EXCLUDEFROMCAPTURE` applied.
**Acceptance.** Numbers recorded in this plan; architecture decided; nothing else committed.

### WP1 — `snow.py` model
**Files.** `snow.py`, `tests/smoke_snow.py` (registered).
**Behaviour.** As 1.2/1.3: height-maps, growth, cap, overhang, icicles, particles, gravity, landing,
budgets, brush and flick gestures from pointer samples, `step()` returning dirty groups.
**Tests.** Growth reaches the cap and stops; a brushed span turns into chunks with mass conserved;
a slow pointer brushes nothing; thrown snow goes up and comes back; budget never exceeded; `step`
reports nothing when nothing moves; deterministic under a seed.

### WP2 — Window stack and clipping
**Files.** `win_integration.py` (`list_window_stack`, extended frame bounds), `snow.py` (pure clip,
per-window pile lifecycle), tests.
**Tests.** Clip against synthetic stacks (covered, partly covered, maximised, popup on top); move,
resize, minimise and close turn snow into chunks; `smoke_play.py` still passes; the stack helper
filters cloaked/iconic/own windows (Win32 part may be exercised live as `smoke_play` does).

### WP3 — Overlay, wiring and the setting
**Files.** `snow_overlay.py`, `main.py`, `config.py`, `settings.py`, `docs/SETTINGS.md`, `world.py`
(`snow_active`), `tray.py`, tests (`tests/smoke_snow_overlay.py`).
**Behaviour.** Per-monitor overlays, grouped repaints, timer stopped when idle, every hide rule in
1.4, pointer brush and flick, capture exclusion, the one-time toast, the tray entry.
**Tests.** Offscreen/real QApplication: timer stopped when idle; hidden under guard, snooze and
fullscreen; `WindowFromPoint` never returns a snow window; date-window table for `snow_active`;
`smoke_ui.py` and `check_docs.py` pass.
**Acceptance.** No click lost on a snowy title bar; idle CPU within noise of today's.

### WP4 — Flurries, taskbar drift, battery
**Files.** `snow.py`, `win_integration.py` (`on_battery`), `snow_overlay.py`, `tests/snow_rate.py`
(registered in `RATES`).
**Acceptance.** Measured flurry rate quoted in the docs; zero ticks between flurries; a thin drift on
the taskbar's top edge (Nib's walk line, `get_walk_strip`) so a maximised-only desktop is not bare.

### WP5 — Nib's reactions
**Files.** `behavior.py`, `main.py`, tests (`tests/smoke_snow_nib.py` or an existing suite),
`snow_rate.py`.
**Acceptance.** As 1.5; `fidget_rate` / `quirk_rate` byte-identical under the seeded comparison with
snow on and off.

### WP6 — Play mode and the snowball
**Files.** `snow_overlay.py`, `tool_wheel.py` (icon), `main.py`, tests.
**Behaviour.** "Play in the snow" on the wheel: for ~30 s the overlay drops click-through and paints
an alpha-1 fill (the ruler's trick, `ruler.py:18-21`); press on a pile to scoop a snowball, drag,
release to throw; it can hit Nib. Right-click or the timeout ends it, always.
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

- Transparent full-monitor repaint cost is unmeasured until WP0 (guard_fx's figures are for an
  opaque window).
- Topmost fights with Nib's own windows and other topmost apps.
- Qt may recreate the HWND, dropping `WS_EX_TRANSPARENT`: re-apply on `WinIdChange`, and test it.
- Mixed DPI and a negative monitor origin: physical pixels throughout
  (`QT_ENABLE_HIGHDPI_SCALING=0`), per-monitor block size.
- Flakes draw over the Start menu and context menus (topmost); keep them sparse, clip if needed.
- A visible full-screen layered window can disable direct-to-display for games and video: hide it.
