# Away Guard — feature plan (v4, full design)

## Goal
One click (tool wheel / tray / `ctrl+alt+g`) covers every monitor with a thick fog of the real
desktop, **giant pixel eyes** that follow the pointer, and **Nib on a little stage** doing his
guard routine with funny lines popping up. Anything suspicious turns the screens black, with
only red glowing eyes left. It stays until you authenticate, then you pick **Peek 3 min**
(auto re-guards) or **Unlock**. Background work keeps running.

## Ground rules from the research
1. **An overlay window pauses nothing.** It's only a window.
2. **Your laptop is already set to never sleep.** The guard still holds a cheap
   `ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED` request while active, as insurance
   (this machine is Modern Standby, where a screen-off means suspended apps). It's released on
   unlock. No long test is needed.
3. **Ctrl+Alt+Del can't be blocked**, and Win+L shouldn't be. A low-level keyboard hook
   swallows the Win key, Alt+Tab, Alt+F4 and Ctrl+Esc. It runs on a dedicated thread in a
   separate process, because Windows silently drops slow hooks.
4. **Lid and power button stay your settings.** Nib never changes power settings.

---

## 1. What you see

### Layout (main monitor = the one your pointer is on when you guard)
```
┌──────────────────────────────────────────────────────────┐
│ ░░░░░░░░░░░░░░░░░ fog of the real desktop ░░░░░░░░░░░░░░ │
│                                                          │
│            ████████████        ████████████              │
│          ██            ██    ██            ██            │  ← GIANT EYES
│          ██    ████    ██    ██    ████    ██            │    (~35% of screen height)
│          ██    ████    ██    ██    ████    ██            │
│            ████████████        ████████████              │
│                                                          │
│        ╭──────────────────────────────────╮              │
│        │ "Agents at work. Please do not   │              │  ← speech bubble (typewriter)
│        │  feed the agents."               │              │
│        ╰─────────────╮────────────────────╯              │
│                  ▟█▙ (Nib)                               │
│  ─────────────────────────────────────────────── stage   │
│              ┌──────────────────────────┐                │
│              │  ● ● ● ●   [ Hello 👆 ]  │                │  ← PIN field / Hello button
│              └──────────────────────────┘                │
│                    guarding 42 min · agents awake ✔       │  ← small status line
└──────────────────────────────────────────────────────────┘
```
- **Other monitors:** fog plus a smaller pair of eyes, which also look toward the pointer across
  screens. That's a nice effect: all the eyes on all the screens follow you.
- **`guard_scene` setting:** `"both"` (default), `"eyes"` (eyes only, phrases as a big banner),
  or `"nib"` (no eyes).

### The fog
- Grab each `QScreen` → **shrink it to ≤ 48 px wide straight away** → discard the full-size grab
  (never written or logged) → scale up with a heavy blur → grey haze on top.
  `guard_fog_strength` 1–5, default 5. You only see colours and movement, never text.
- It refreshes every 3 s. The overlay uses `WDA_EXCLUDEFROMCAPTURE` (0x11) so it isn't in its
  own grab.

### Blackout (anything suspicious)
- Every monitor goes solid black and grabs stop. Nib leaves the stage (he "runs for help":
  quick `walk_right` off-screen).
- Only the **eyes remain, now glowing red**, slowly tracking the pointer in the dark.
- The phrase banner goes red ("I see you."). The PIN field / Hello button is still there, so
  the owner can get back in.
- It's sticky until a successful unlock. The display stays on (showing black), so nothing
  pauses.

---

## 2. The giant eyes — behaviour spec
Drawn procedurally with `QPainter` in Nib's pixel style (2-px blocks × scale, Nib's palette:
body `#D9552E` for the lids, white sclera, dark pupils). No new sprite sheet is needed.

| Situation | What the eyes do |
| --- | --- |
| Pointer moving | Pupils follow it, eased (move 15 % of the distance per frame), clamped inside the iris |
| No input, 2–60 s | Idle gaze: pick a random target every 1.5–4 s and drift to it; sometimes a slow side-eye |
| Blinking | Random every 2–6 s; 10 % double blink; 5 % "slow suspicious blink" |
| Fast pointer movement | Widen (lids open +20 %) for 1 s |
| Pointer near the PIN field | Glance down at it, then back up at the pointer |
| Typing in the PIN field | Eyes squeeze shut ("not looking!") while Nib covers his eyes too |
| No input 60 s | Sleepy half-lids, slow blinks |
| No input 5 min | Eyes closed, floating "Z z z"; every ~40 s **one eye pops open**, looks around, closes |
| Any input while sleeping | Snap open (startle) with a little shake |
| Wrong attempt 1 | Squint, brows angle down |
| Wrong attempt 2 | Narrow slits, small red veins appear |
| Blackout | Red glow, slow heavy tracking, no blinking (creepier) |
| Correct unlock | Happy arcs `^ ^` for 0.6 s, then fade out with the overlay |
| Coming back from Peek | Wide open, staring at the pointer: "I'm watching." |

---

## 3. Nib on stage — choreography
Nib reuses his existing sprite states (`frames.json`: `idle, blink, walk_right/left,
look_around, foot_tap, check_watch, coffee, sit_dangle, pushups, football, yawn, sleep, wake,
startle, angry, wave, counting, dance, task_finished, heart_eyes, typing, stretch …`). There's
**one new state, `cover_eyes`** (hands over eyes, 4 frames), added in `tools/gen_sprites.py`.
There's an optional **guard cap** costume via the existing costume pipeline.

### Idle routine director
A weighted random pick every 6–15 s, never the same routine twice in a row:

| Routine (weight) | Sequence |
| --- | --- |
| **Patrol** (30) | walk across the stage → `check_watch` → walk back → `look_around` |
| **Stand guard** (20) | `idle` with long `blink_slow`, now and then `foot_tap` |
| **Coffee break** (10) | `sit_dangle` → `coffee` → "Guarding is thirsty work." |
| **Keep fit** (10) | `pushups` ×3 → `stretch` |
| **Football** (10) | `football` keepy-uppies; the ball can bounce off the eyes' lower lid (they blink when hit) |
| **Counting** (10) | `counting` → "Guarded for {minutes} min. Not that I'm counting." |
| **Dance** (5) | short `dance` when no one's around |
| **Tired** (5, only after 20 min) | `yawn` → `stretch` |
After 5 min with no input, Nib goes to `sleep` in sync with the eyes, and both `wake`
together on input.

### Reactions (they interrupt the routine)
| Trigger | Nib | Sound |
| --- | --- | --- |
| Pointer comes within ~120 px | `startle` → `wave` ("nope") | `poke` |
| Pointer poked at him 3+ times | `angry` + "Poking me won't unlock anything. I checked." | `grump` |
| Click on him | `wave` pointing down at the PIN field | `poke` |
| Typing starts | **`cover_eyes`** held while typing: "I'm not looking. I'm not looking." | — |
| Wrong attempt | `angry` → `foot_tap` | `grump` |
| Blackout | runs off-stage | `grump` |
| Correct unlock | `task_finished` → `heart_eyes` | `cheer` |
| Return from Peek | walks back on → `check_watch`: "Back already?" | `wake` |
All sounds go through the existing `Sounds` (global mute respected), and there's an extra
`guard_sounds` setting (default on).

---

## 4. Phrases — looping funny lines
- Shown in a speech bubble above Nib with a **typewriter effect** (35 ms/char). They stay
  4–6 s, then fade. A new idle line every 9–14 s.
- They come from a **shuffle bag** per category, so nothing repeats until all have been
  shown.
- `{minutes}` is a placeholder for the minutes guarded so far.
- Stored in `assets/guard_phrases.json`. You can add your own via a `guard_phrases.json` in
  Nib's settings folder, which is merged with the built-in list.

**Starter set (all original):**
- **idle:** "Agents at work. Please do not feed the agents." · "Nothing to see here. Just
  very important fog." · "The humans are away. The bots are working. I'm supervising." ·
  "This screen is closed for a private meeting of the robots." · "Shh… the code is compiling
  its feelings." · "Security level: one small orange guy. Do not underestimate." · "Fun fact:
  staring at fog does not unfog it." · "Guarded for {minutes} min. Snacks were not provided." ·
  "Back in 5 minutes. (The bots said that 3 hours ago.)" · "You look like someone who doesn't
  know the password."
- **pointer_near:** "Hands where I can see them." · "That's a lot of wiggling for someone who
  isn't the owner." · "Nope." · "I can see that cursor, you know."
- **typing:** "I'm not looking. I'm not looking." · "La la la, can't see your PIN."
- **wrong_1:** "Close! (It wasn't close.)" · "Hmm. That's not it."
- **wrong_2:** "Strike two. I'm writing this down." · "The eyes are getting serious."
- **blackout:** "Lights out. The owner will hear about this." · "Nice try. Everything's
  logged." · "I see you."
- **unlock:** "Welcome back, boss!" · "Ah, it's you. Carry on."
- **peek_return:** "Peek's over. Back to guarding." · "Three minutes? That flew by."
- **sleeping:** "Zzz… (still guarding) …zzz"

---

## 5. States, security and auth

### State machine — `GuardController` (pure logic, injected clock)
```
IDLE ──guard──▶ FOG ──suspicious──▶ BLACKOUT
                 │                     │
                 └──── auth ok ────────┴──▶ choice: PEEK(3:00) ──timeout / Lock now──▶ FOG
                                                   UNLOCK ──▶ IDLE
```
- **PEEK:** hide overlays, remove the keyboard hook, pause tripwires, keep the power request.
  A small chip shows `m:ss` with a progress ring, a sound at 0:10, **+3 min** (no re-auth)
  and **Lock now**.
- **IDLE:** release everything. Show the **intrusion log** card if anything happened
  ("14:02 — 3 wrong PINs · 14:05 — Ctrl+Alt+Del"). It's kept in memory only and cleared on
  dismiss.

### Suspicious events (FOG → BLACKOUT)
| Event | Response |
| --- | --- |
| Wrong attempts ≥ 3 (`guard_blackout_after_attempts`) | Blackout. Backoff of 1 s, 2 s, 4 s … continues |
| A blocked escape key (Win, Alt+Tab, Alt+F4, Ctrl+Esc) | Blackout |
| Desktop switch (Ctrl+Alt+Del, or UAC) | Blackout |
| Foreground stolen within 2 s of input (a human did it) | Re-assert + blackout |
| Foreground stolen with no recent input (an agent opened a window) | Re-assert silently, log only |
| The guard core restarted | Blackout |
| Hello prompt (`CredentialUIBroker.exe`), toasts | Ignored (whitelisted) |

### Authentication
- **Windows Hello first** (`UserConsentVerifier` through
  `IUserConsentVerifierInterop.RequestVerificationForWindowAsync` with the overlay HWND). It's
  a small dialog over the fog; nothing is stored in Nib.
- **Guard PIN fallback** when Hello is unavailable (DeviceNotPresent / NotConfiguredForUser /
  DisabledByPolicy): `hashlib.scrypt(n=2**14, r=8, p=1, dklen=32)`, 16-byte salt,
  `hmac.compare_digest`, hashed off the UI thread.
- **First run:** if Hello is unavailable, set a PIN (entered twice, at least 4 digits) before
  the first guard.
- **Forgot it:** Ctrl+Alt+Del → Lock → Windows password; `windows_unlock_clears_guard`
  ends the guard.
- **`guard_lock_fallback`** (off by default): 10 wrong attempts → `LockWorkStation()`. Your
  laptop keeps running when locked, so you can turn it on.

---

## 6. Architecture & files

```
Nib (PySide6)                          guard_core (same exe, --guard-core, ctypes only)
 guard.py          GuardController ◀──JSON lines──▶  power request
 guard_overlay.py  per-monitor window, fog            LL keyboard hook (own thread)
 guard_scene.py    EyesPainter, StageDirector,        WinEvent: foreground / desktop switch
                   PhraseBag, PeekChip                 emergency black windows if Nib dies
 guard_auth.py     Hello + PIN                         waits on Nib's process handle
```
- **Rendering:** one 30 FPS `QTimer` drives the eyes, Nib and the bubble, dropping to 10 FPS
  in sleep mode. Fog grabs run on their own 3 s timer.
- **Protocol:** Nib → core: `guard / peek / unlock / ping`; core → Nib:
  `ready / pong / event{kind, detail, t}`.
- **If Nib dies,** the core instantly shows plain black windows on every monitor. When Nib
  restarts, it resumes in BLACKOUT.

| File | Change |
| --- | --- |
| `guard.py`, `guard_overlay.py`, `guard_scene.py`, `guard_auth.py`, `guard_core.py` | new |
| `assets/guard_phrases.json` | new |
| `tools/gen_sprites.py` → `assets/nib_sheet.png`, `frames.json` | add `cover_eyes` |
| `main.py` | `--guard-core` entry, hotkey (`HOTKEY_ID` + `_install_hotkey` pattern), `WM_WTSSESSION_CHANGE` |
| `tool_wheel.py`, `tray.py` | "Guard" item (shield icon) |
| `config.py` | keys in `DEFAULTS`, numeric ones in `_BOUNDS`, `guard_hotkey` in `_TEXT_KEYS` |
| `docs/SETTINGS.md`, `USING-HIM.md`, `PRIVACY.md` | document it (`check_docs.py` enforces the settings) |
| `nib.spec` | include `guard_phrases.json` and the new sprite |
| `tests/smoke_guard.py`, `tests/smoke_guard_scene.py` | new, added to `run_all.py` |

## 7. Settings
`guard_hotkey` ("ctrl+alt+g"), `guard_peek_minutes` (3), `guard_auth` ("hello" | "pin"),
`guard_pin_hash`, `guard_scene` ("both" | "eyes" | "nib"), `guard_fog_strength` (1–5, 5),
`guard_blackout_after_attempts` (3), `guard_sounds` (true), `guard_auto_after_idle_min`
(0 = off), `guard_lock_fallback` (false), `windows_unlock_clears_guard` (true).

## 8. Phases
0. **Quick checks (~1 hour, no long runs).** A throwaway script in `tools/` confirms:
   - the LL hook stays alive on its own thread while the CPU is busy
   - the fog grab doesn't include the overlay (`WDA_EXCLUDEFROMCAPTURE`)
   - the Hello dialog appears over the overlay without a desktop switch
   - the overlay covers every monitor and survives switching virtual desktops

   **Done when** all four behave. Any surprise gets written into this file before Phase 1.
1. **Core guard:** `guard_core`, FOG with a static fog snapshot, BLACKOUT, Hello + PIN with
   first-run setup, Peek/Unlock, intrusion log, hotkey, tray and wheel entries.
   - **Done when:** `smoke_guard.py` passes, and a manual run of every row of the
     suspicious-events table behaves as written.
2. **The show:** giant eyes (full behaviour table), stage director + reactions, the
   `cover_eyes` sprite, phrase bags with typewriter, sounds, red-eyes blackout, live fog
   refresh.
   - **Done when:** `smoke_guard_scene.py` (seeded RNG: director never repeats a routine,
     phrase bag never repeats before it's exhausted, eye state per situation) passes, and the
     scene's CPU use stays low (< 5 % of one core at 30 FPS).
3. **Polish:** peek chip ring + sound, status line, battery warning, auto-guard after N idle
   minutes (`get_idle_seconds()` exists), guard cap costume, custom phrases file, docs.

## 9. Before Phase 1 starts
- [ ] The other agent has finished editing `main.py` / `tool_wheel.py` / `tray.py`.
- [ ] `git init` + a first commit, for a clean rollback point.
- [ ] Phase 0 checks are done.

## 10. Honest limits
- It's a strong deterrent for coworkers, kids and cats, not a vault. Ctrl+Alt+Del can't be
  blocked; it triggers a blackout.
- A touchpad Task View gesture or another topmost window can show fog-free content for up to
  about 0.5 s before the overlay re-asserts itself.
- Toasts can appear above the overlay, and there's no supported way to stop that. Use Do Not
  Disturb if it matters.
