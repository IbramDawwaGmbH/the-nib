# Troubleshooting

When he misbehaves.

[← back to the README](../README.md)

---

## Troubleshooting

**He isn't moving.** Check the tray: if he is paused, that item reads **Resume** and is ticked —
click it to unfreeze him. The pause state persists across restarts, so a stray click can leave him
frozen days later. (A left click on the tray icon no longer toggles it, precisely because that was
too easy to hit by accident.)

**He's gone.** Either a fullscreen app is up, or he is snoozed from the right-click menu. Both end
on their own. Check the log for `fullscreen detected` or `snoozing`.

**I can't find him.** Tray → **Come here** and he walks to your pointer. He roams every monitor by
default, so this is a normal thing to need rather than a fault.

**Clicking him does nothing.** Ghost mode is on — the tray item is ticked, and it survives a
restart. He is deliberately click-through in that state. The `Ctrl+Alt+C` shortcut still works.

**He makes no sound.** No audio ships in the repository. Run `python tools/gen_sounds.py`, then
tick **Sound enabled** in the settings window.

**"Couldn't open Claude."** The `claude://` scheme is handled by the Claude desktop app. The
toast that appears is clickable and opens the download page.

**The hotkey did nothing.** Something else has already registered Ctrl+Alt+C. The log says
`hotkey ... is already taken by another app`; pick another in `hotkey`.

**He's too hyper / too calm.** Raise or lower `wander_min_interval_s` / `wander_max_interval_s`.
`antic_chance` tunes antics-versus-walking. `antics_enabled: false` turns off the random idle
antics, the cursor-tracking and the app-switch reactions — but *not* the away sit and the greeting
hop, which are controlled by `away_after_s` (set it to `0` to disable those).

**"Couldn't read that image" / "Didn't find any text in that image."** [Drop-to-OCR](../README.md
#drop-to-ocr) tries Windows' own on-device engine first and falls back to a copy of Tesseract
bundled inside Nib, so this isn't a missing-install problem — it means both engines genuinely
couldn't get text out of that particular file: a screenshot with no text in it, a corrupted image,
or a file over 25MB. `nib.log` records which engine failed and why, at the default `INFO` level —
the image and any extracted text are never logged, only that.

**The PC slept (or the screen turned off) while the screen guard was up.** The guard holds a
"keep awake" request while it runs, which stops *idle* sleep and the display timeout — but closing
the lid or pressing the power button still does whatever Windows is set to do, by design: Nib never
changes power settings. If background work must keep running, set the lid and power-button actions
to **Do nothing** in Control Panel → Power Options → *Choose what closing the lid does*.

**I forgot the guard PIN.** Ctrl+Alt+Del → **Lock**, then sign back in to Windows: that ends the
guard (`windows_unlock_clears_guard`). Clear `guard_pin_hash` in `config.json` to choose a new PIN
next time.

**The screens stayed black after Nib closed.** Nib died mid-guard and its helper is doing its job.
Lock and unlock Windows (Win+L, then sign in), or just start Nib again — it takes over and you can
unlock as normal.

**Anything else.** Set `log_level` to `DEBUG` and read `%APPDATA%\NibMascot\nib.log`.
