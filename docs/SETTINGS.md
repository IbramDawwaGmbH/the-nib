# Settings

Every setting, its default, and what it does.

[← back to the README](../README.md)

---

## Settings

**Tray → Settings…** opens all of it as a form. Seven tabs, a one-line explanation under the settings
that need one, and a **Clear prompt history** button.

The form is **schema-driven**: it is built from `DEFAULTS` and `_BOUNDS` in `config.py` plus a
grouping table, and the control is chosen from the *type of the default value* — a bool becomes a
checkbox, a bounded number becomes a spin box clamped to its own bounds, a key with a fixed set of
values becomes a combo, text becomes a line edit. Nothing in the window knows what any individual
setting means. A hand-built form would drift the moment anyone added a key, and anything added to
`DEFAULTS` but not to a tab lands on an **Other** tab rather than becoming quietly unreachable.

Everything applies the moment you click Apply, because `Behavior` reads the config live on every
tick rather than caching it. The exceptions are marked with a `*`: `sprite_scale`, the hotkeys
(`guard_hotkey` included) and `log_level` take effect next launch. That is more honest — and an order of magnitude less code
— than rebuilding the sprite sheet and re-registering shortcuts underneath a running mascot.

You can still edit the file directly. `%APPDATA%\NibMascot\config.json`, written on first save.
Unknown keys are preserved, and a corrupt file falls back to defaults rather than preventing
startup. Values are coerced to the right type, clamped to sane ranges, and a reversed min/max pair
is normalised — so a hand-edited file cannot break him.

### Behaviour

| Key | Default | Meaning |
| --- | --- | --- |
| `wander_enabled` | `true` | Whether he strolls |
| `wander_min_interval_s` | `0.3` | Shortest beat *between* activities — raise it to calm him down |
| `wander_max_interval_s` | `1.2` | Longest beat between activities |
| `walk_min_px` | `120` | Shortest stroll |
| `walk_max_px` | `700` | Longest stroll |
| `walk_speed_px_s` | `0` | `0` derives speed from the sprite's stride (recommended — see the gait note) |
| `home_fraction` | `0.78` | Where he stands with no remembered position: `0.0` far left, `1.0` far right |
| `roam_all_monitors` | `true` | Let him walk between screens |

### Antics and reactions

| Key | Default | Meaning |
| --- | --- | --- |
| `antics_enabled` | `true` | Random idle antics, cursor-tracking and app reactions (not the away sit — that is `away_after_s`) |
| `antic_chance` | `0.5` | Chance of an antic rather than a walk after each beat |
| `fidgets_enabled` | `true` | The very short beats — a scratch, a foot tap, a glance at a watch he does not own |
| `quirks_enabled` | `true` | Involuntary things: a sneeze, a stumble, hiccups after a hard landing |
| `costume_reactions` | `true` | He hops into a costume you pick and has an opinion about it, or about a new colour; `false` and it just appears |
| `scroll_to_spin` | `true` | Scroll the wheel over him and he spins |
| `eye_track_distance_px` | `420` | How close your pointer must be before he starts following it |
| `hover_wave_distance_px` | `120` | How close your pointer must be for a wave |
| `petting_enabled` | `true` | Hold the pointer still on him and he leans into it |
| `pet_hold_ms` | `550` | How long the pointer must sit still on him first |
| `prompt_reactions` | `true` | React to the shape of what you just asked |
| `calculator_enabled` | `true` | A safe local parser answers a plain sum or unit conversion inline, never sent anywhere |
| `react_to_apps` | `true` | React when you switch apps (executable name only) |
| `away_after_s` | `150` | Idle seconds before he sits down to wait; `0` disables (also disables the greeting hop) — also the auto-pause threshold for a running focus session, below |
| `focus_duration_min` | `25` | Length of a focus session in minutes, started from the tray |
| `mic_indicator_enabled` | `true` | Show when your microphone is live, or live but muted. State only - never audio, never which app |
| `camera_indicator_enabled` | `true` | Show when your camera is live. An indicator only: Windows exposes no global camera mute |
| `talking_while_muted_after_s` | `2.0` | Seconds of talking into a muted mic before he says so. He then holds his tongue for a minute |
| `attention_enabled` | `true` | Notice when you are here but ignoring him |
| `attention_after_s` | `240` | Seconds to the first stage; the wave is at 2x this and giving up at 3.5x |
| `injury_enabled` | `true` | Land hurt after a real drop |
| `injury_duration_s` | `3.0` | How long he stays bandaged and dazed |
| `hide_on_fullscreen` | `true` | Fade out for fullscreen apps |

### World awareness

| Key | Default | Meaning |
| --- | --- | --- |
| `world_enabled` | `true` | Master switch for the clock, calendar, season and CPU reactions |
| `workday_end` | `"16:00"` | Knocking-off time. `""` disables that moment *and* the Friday flop |
| `seasonal_enabled` | `true` | The shiver / heat / falling-leaf moments and the seasonal weighting |
| `southern_seasons` | `false` | Flip the seasons by six months for the southern hemisphere |
| `cpu_reaction_enabled` | `true` | Fan himself when the machine is pinned |
| `cpu_busy_percent` | `70` | Sustained **aggregate** load that counts as busy — see the note above |
| `task_finished_reaction_enabled` | `true` | Celebrate once a long sustained-load stretch drops back to quiet |
| `persona_costumes` | `true` | Let an app persona put temporary headwear on him |
| `auto_costumes` | `true` | Let him dress himself when there's a reason to — see [Costumes](ART.md#costumes-and-colours) |
| `daily_costumes` | `true` | A costume for every date of the year — see [The costume of the day](ART.md#the-costume-of-the-day). Off falls back to the season's |
| `persona_apps` | `{}` | `{"exe.exe": "animation"}`, merged over the built-in personas |
| `play_with_windows` | `true` | Peeking over the taskbar, and heaving at a window overhead |

`workday_end` accepts `"16:00"` or a bare `"16"`. Anything unparseable disables that one moment
rather than being silently rewritten to a time you never chose.

### Personality

| Key | Default | Meaning |
| --- | --- | --- |
| `name` | `"Nib"` | What he's called in the tray, toasts and lines he says. Trimmed, line breaks become spaces, at most 24 characters; blank goes back to `"Nib"`. Window titles still say Nib — that is the app's name, not his |
| `personality` | `"balanced"` | `"balanced"`, `"chill"`, `"hyper"` or `"sassy"` — a lens over the numbers below, not a rewrite of them |
| `annoyance_threshold` | `4` | Pokes needed to anger him |
| `annoyance_window_s` | `15` | Burst window for those pokes |
| `annoyance_decay_s` | `20` | Seconds per point of residual decay |
| `sleep_cooldown_s` | `60` | Nap length |
| `mood_enabled` | `true` | Let a good or bad stretch carry over between sessions |
| `mood_half_life_h` | `6.0` | Hours for a good or bad turn to half fade |
| `memory_enabled` | `true` | Counts the days, pets, prompts and focus sessions you share. Counts only, kept in `memory.json` next to `config.json` — see [Privacy](PRIVACY.md). Off: nothing is counted and no file is written; one already there is kept until you **Forget everything** |
| `memory_routine_enabled` | `true` | Lets him learn roughly what time you usually arrive (an hourly tally, nothing else). Off drops the tally from `memory.json` |
| `needs_enabled` | `true` | Energy, affection and boredom that colour what he does: tired in the evening after a long day, fond after a lot of petting, bored when nothing happens. They only lean on what he already does, never nag. Off: all of it is gone and the stored values are left as they are, until you **Forget everything**. See [Energy, affection and boredom](BEHAVIOUR.md#energy-affection-and-boredom) |
| `chatter_level` | `"rare"` | How often he says a line in a toast: `"rare"` is at most 6 a day, 45 min apart; `"normal"` at most 14, 20 min apart; `"off"` never. A greeting may skip the gap, never the daily cap. See [He says things, rarely](USING-HIM.md#he-says-things-rarely) |

### Appearance

| Key | Default | Meaning |
| --- | --- | --- |
| `sprite_scale` | `0` | `0` picks an integer scale from monitor DPI |
| `costume` | `""` | `""` is bare-headed; 53 to choose from — see `assets/costumes.json` |
| `skin` | `"default"` | Body colour remap; see `SKINS` in `animator.py` |
| `shadow_enabled` | `true` | The contact shadow under his feet |
| `ghost_mode` | `false` | Faded and click-through — visible but completely out of the way |
| `ghost_opacity` | `0.35` | How faded. Clamped to a floor of `0.08`, never fully invisible |
| `sound_enabled` | `false` | Small blips. Nothing plays until you run `tools/gen_sounds.py` |
| `sound_volume` | `0.35` | `0.0`–`1.0` |

### Shortcuts and launching

| Key | Default | Meaning |
| --- | --- | --- |
| `provider` | `"claude"` | Which assistant he launches: `"claude"`, `"chatgpt"` or `"gemini"` — see [Providers](PROVIDERS.md) |
| `default_mode` | `"chat"` | `"chat"` or `"cowork"`. Only Claude has more than one |
| `hotkey` | `"ctrl+alt+c"` | Global shortcut for the bubble; `""` disables it |
| `come_hotkey` | `""` | Calls him to your pointer. `""` disables it; the tray entry always works |
| `pocket_hotkey` | `"ctrl+alt+p"` | Grabs the clipboard into his pocket; a click or the main hotkey empties it into the bubble. `""` disables it |
| `tool_wheel_enabled` | `true` | Right-click him for a wheel of his tools. `false`, or Shift+right-click, gives the full menu instead |
| `mic_toggle_hotkey` | `""` | Mutes and unmutes your microphone. `""` disables it; the tray entry always works |
| `quick_prompt` | `""` | Double-click sends this; empty falls back to your last prompt |
| `last_prompt` | `""` | Remembered automatically so a double-click can repeat it |
| `prompt_history_max` | `20` | How many recent prompts Up in the bubble walks back through; `0` disables and clears |
| `launch_on_startup` | `false` | The registry Run entry. Setting it `true` by hand registers it on next start |

### Away guard

What the [screen guard](USING-HIM.md#the-screen-guard) does. None of these change Windows' own power,
lid or lock settings.

| Key | Default | Meaning |
| --- | --- | --- |
| `guard_hotkey` | `"ctrl+alt+g"` | Covers every monitor until you unlock. `""` disables it; the wheel and tray entries always work |
| `guard_peek_minutes` | `2` | How long **Peek** lifts the guard before it comes back by itself. `1`–`180` |
| `guard_auth` | `"hello"` | `"hello"` offers Windows Hello when this PC has it, with the guard PIN as backup; `"pin"` never offers Hello |
| `guard_pin_hash` | `""` | scrypt hash of your guard PIN, set by the prompt on the first guard (Hello or not). Never the PIN itself. Clear it to choose a new PIN next time. Not shown in the settings window |
| `guard_scene` | `"both"` | What stands guard on the fog: `"both"`, `"eyes"` (phrases as a banner, no Nib) or `"nib"` (no giant eyes) |
| `guard_fog_strength` | `5` | `1`–`5`, how heavily the desktop is blurred. Even `1` leaves text unreadable |
| `guard_background_fx` | `true` | Life in the fog: dim worker bots, drifting dust that dodges the pointer, a security-camera scan line with a blinking **REC** and the time guarded, and a slow glow that turns warmer in the evening and starry at night. In blackout only a faint red pulse and the REC stay. Light on the CPU (it slows down when nobody is around and stops during Peek); `false` gives plain fog |
| `guard_blackout_after_attempts` | `3` | Wrong PINs before every screen goes black. The wait between tries doubles regardless (1 s, 2 s, 4 s … 30 s) |
| `guard_sounds` | `true` | His guard noises. `sound_enabled` still has to be on |
| `guard_auto_after_idle_min` | `0` | Guard by itself after this many idle minutes; `0` never does. Needs a guard PIN to be set already |
| `guard_lock_fallback` | `false` | Ten wrong PINs also locks Windows. Locking does not stop background work |
| `windows_unlock_clears_guard` | `true` | Unlocking Windows ends the guard — the way back in if you forget the PIN. Turned off, a forgotten PIN leaves only signing out of Windows. Always on while no guard PIN is stored (cleared by hand, or a guard from before every guard had one) |

### State and diagnostics

| Key | Default | Meaning |
| --- | --- | --- |
| `last_x` / `last_monitor` | `null` | Remembered resting position |
| `paused` | `false` | Remembered pause state |
| `intro_seen` | `false` | Set once the first-run tour has run. Clear it to see the tips again |
| `prompt_history` | `[]` | Your recent prompts, newest first. Local only — see [Privacy](PRIVACY.md#privacy) |
| `costume_recents` | `[]` | The costumes you put on him last, newest first — the head of the [wardrobe](USING-HIM.md#the-wardrobe). Written, not for you to set |
| `mood_spirits` | `0.0` | `-1.0`–`+1.0`, how the day has gone. Written, not for you to set |
| `mood_saved_at` | `0.0` | Unix seconds at the last write, so the mood keeps fading while he is closed |
| `need_energy` | `0.5` | `0.0`–`1.0`, how rested he is. Written by him, not for you to set |
| `need_affection` | `0.5` | `0.0`–`1.0`, how fond he is feeling. Written by him |
| `need_boredom` | `0.0` | `0.0`–`1.0`, how bored he is. Written by him |
| `needs_saved_at` | `0.0` | Unix seconds at the last write, so he rests and gets over things while he is closed |
| `last_seen_date` | `""` | Latch for the morning stretch. Written, not for you to set |
| `last_clock_off_date` | `""` | Latch for the knocking-off dance |
| `last_week_over_week` | `""` | Latch for the Friday flop, keyed on the ISO week |
| `last_milestone_days` | `""` | Latch for the [day milestones](BEHAVIOUR.md#moments--things-he-does-once): the last day-count he celebrated (`7`, `30`, `100`, `365`). Written, not for you to set |
| `last_focus_streak_date` | `""` | Latch for the focus-streak party, keyed on the date |
| `log_level` | `"INFO"` | `DEBUG` for verbose diagnostics |
| `config_version` | `2` | Drives migrations — see below |

**Migrations.** When a key's *meaning* changes rather than just its default, the stored value is
discarded so it picks up the new one. This has happened once: `wander_min/max_interval_s` used to
mean "how long he rests before a stroll" (20–60s) and now means "the beat between activities"
(sub-second). Without the migration an existing config would silently pin him to standing still,
and the change would look as though it simply had not worked.

Logs go to `%APPDATA%\NibMascot\nib.log`, rotating at 512KB with two backups kept. Under
`pythonw` there is no console, so that file is where to look if something misbehaves.
